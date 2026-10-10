from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.core.errors import BadRequestError


_ENV_PATTERN = re.compile(r"\{\{\s*env:([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_UNRESOLVED_PATTERN = re.compile(r"^\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}$")


def _render_template(value: Any, inputs: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        rendered = value
        for key, item in inputs.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(item))
        return rendered
    if isinstance(value, list):
        return [_render_template(item, inputs) for item in value]
    if isinstance(value, dict):
        rendered_dict = {}
        for key, item in value.items():
            rendered_item = _render_template(item, inputs)
            if isinstance(rendered_item, str) and _UNRESOLVED_PATTERN.match(rendered_item):
                continue
            rendered_dict[key] = rendered_item
        return rendered_dict
    return value


def _render_path(url: str, inputs: Dict[str, Any]) -> str:
    encoded = {key: quote(str(item), safe="") for key, item in inputs.items()}
    return _render_template(url, encoded)


def _render_env_placeholders(value: Any) -> Tuple[Any, List[str]]:
    """Resolve {{env:VAR}} from server env (secrets never live in node config).

    Returns (rendered, missing_names). Callers fail closed on missing names.
    """
    missing: List[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        resolved = os.getenv(name, "")
        if not resolved:
            if name not in missing:
                missing.append(name)
            return ""
        return resolved

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _ENV_PATTERN.sub(_replace, node)
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, dict):
            return {key: _walk(item) for key, item in node.items()}
        return node

    return _walk(value), missing


async def execute_http_tool(
    http_config: Dict[str, Any],
    input_data: Dict[str, Any],
    *,
    timeout_ms: int,
) -> Dict[str, Any]:
    if not http_config:
        raise BadRequestError(
            "http_config is required for http runtime nodes",
            code="http_config_required",
        )

    method = str(http_config.get("method", "POST")).upper()
    url = _render_path(str(http_config.get("url", "")).strip(), input_data)
    if not url:
        raise BadRequestError("http_config.url is required", code="http_url_required")

    headers = _render_template(http_config.get("headers") or {}, input_data)
    if not isinstance(headers, dict):
        headers = {}

    body_template = http_config.get("body_template", input_data)
    if body_template is None:
        body = input_data
    else:
        body = _render_template(body_template, input_data)

    # Secrets via {{env:VAR}} — resolved server-side, never stored in node docs.
    url, missing_url = _render_env_placeholders(url)
    headers, missing_headers = _render_env_placeholders(headers)
    body, missing_body = _render_env_placeholders(body)
    missing = sorted(set(missing_url + missing_headers + missing_body))
    if missing:
        raise BadRequestError(
            f"Business connector env var(s) not set on the server: {', '.join(missing)}",
            code="http_env_missing",
        )
    if not url:
        raise BadRequestError("http_config.url is required", code="http_url_required")

    timeout = max(timeout_ms / 1000.0, 0.1)
    worker_id = f"http-{uuid4().hex[:8]}"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if method in {"GET", "DELETE", "HEAD"}:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=body if isinstance(body, dict) else None,
                )
            else:
                response = await client.request(
                    method, url, headers=headers, json=body
                )
    except httpx.TimeoutException as exc:
        return {
            "ok": False,
            "worker_id": worker_id,
            "error": {
                "message": f"HTTP request timed out: {exc}",
                "name": "TimeoutError",
            },
            "logs": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "worker_id": worker_id,
            "error": {"message": str(exc), "name": type(exc).__name__},
            "logs": [],
        }

    content_type = response.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            parsed: Any = response.json()
        else:
            parsed = response.text
    except Exception:
        parsed = response.text

    if response.is_success:
        if isinstance(parsed, dict):
            result = {
                **parsed,
                "success": parsed.get("success", True),
                "status_code": response.status_code,
            }
        else:
            result = {
                "success": True,
                "status_code": response.status_code,
                "body": parsed,
            }
        return {"ok": True, "worker_id": worker_id, "result": result, "logs": []}

    body_preview = (
        parsed if isinstance(parsed, str) else json.dumps(parsed, default=str)[:500]
    )
    return {
        "ok": False,
        "worker_id": worker_id,
        "error": {
            "message": f"HTTP {response.status_code}: {body_preview}",
            "name": "HttpError",
        },
        "logs": [],
        "result": {
            "success": False,
            "status_code": response.status_code,
            "body": parsed,
            "error": f"HTTP {response.status_code}",
        },
    }
