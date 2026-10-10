#!/usr/bin/env python3
"""Python tool worker — same JSON-line protocol as node_worker.js."""

from __future__ import annotations

import ast
import json
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List


ALLOWED_IMPORT_ROOTS = {
    "json",
    "re",
    "math",
    "datetime",
    "time",
    "uuid",
    "hashlib",
    "base64",
    "urllib",
    "http",
    "typing",
    "collections",
    "itertools",
    "functools",
    "decimal",
    "statistics",
    "copy",
    "string",
    "html",
    "xml",
    "email",
    "asyncio",
}


class RestrictedImportError(Exception):
    pass


def _validate_imports(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    raise RestrictedImportError(
                        f"Import of '{alias.name}' is not allowed in tool sandbox"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = node.module.split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                raise RestrictedImportError(
                    f"Import of '{node.module}' is not allowed in tool sandbox"
                )


class LogCapture:
    def __init__(self) -> None:
        self.logs: List[Dict[str, Any]] = []

    def _append(self, level: str, *args: Any) -> None:
        self.logs.append(
            {
                "level": level,
                "message": " ".join(
                    a if isinstance(a, str) else json.dumps(a, default=str) for a in args
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def log(self, *args: Any) -> None:
        self._append("log", *args)

    def info(self, *args: Any) -> None:
        self._append("info", *args)

    def warn(self, *args: Any) -> None:
        self._append("warn", *args)

    def error(self, *args: Any) -> None:
        self._append("error", *args)


def _sanitize_error(exc: BaseException) -> Dict[str, Any]:
    return {
        "message": str(exc),
        "name": type(exc).__name__,
        "stack": traceback.format_exc(),
    }


def _write(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def execute_payload(payload: Dict[str, Any]) -> None:
    request_id = payload.get("id")
    code = payload.get("code") or ""
    input_data = payload.get("input_data") or {}
    capture = LogCapture()

    try:
        _validate_imports(code)
        namespace: Dict[str, Any] = {
            "__builtins__": __builtins__,
            "inputs": input_data,
            "console": capture,
        }
        exec(code, namespace, namespace)  # intentional tool runner

        fn = namespace.get("execute_node") or namespace.get("executeNode")
        if not callable(fn):
            raise RuntimeError(
                "Python tool must define execute_node(inputs) or executeNode(inputs)"
            )

        result = fn(input_data)
        if hasattr(result, "__await__"):
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(result)
            finally:
                loop.close()

        _write({"id": request_id, "ok": True, "result": result, "logs": capture.logs})
    except Exception as exc:
        _write(
            {
                "id": request_id,
                "ok": False,
                "error": _sanitize_error(exc),
                "logs": capture.logs,
            }
        )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(
                {
                    "id": None,
                    "ok": False,
                    "error": _sanitize_error(exc),
                    "logs": [],
                }
            )
            continue
        execute_payload(payload)


if __name__ == "__main__":
    main()
