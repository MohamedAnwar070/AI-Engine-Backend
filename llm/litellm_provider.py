"""Cloud LLM gateway (LiteLLM naming, OpenAI-compatible transport).

Design: providers are swapped by config only — {provider, model, base_url,
api_key} stored in MongoDB (llm_providers) and resolved at chat time. Model
ids use LiteLLM naming (gemini/..., groq/..., openrouter/...:free,
github/..., gpt-...) and are translated to each vendor's OpenAI-compatible
endpoint here, so the base install needs only openai+httpx (wheels, no Rust).

If the optional ``litellm`` package is installed, native transports are
preferred automatically; otherwise the built-in OpenAI-compatible client is
used. No code change needed either way.

Endpoint map (OpenAI-compatible):
  gemini     https://generativelanguage.googleapis.com/v1beta/openai/
  groq       https://api.groq.com/openai/v1
  openrouter https://openrouter.ai/api/v1
  github     https://models.github.ai/inference
  openai     https://api.openai.com/v1 (SDK default)
  custom     operator-supplied base_url (required)

Wire formats (api_format, selectable per provider):
  chat-completions   POST {base}/chat/completions (OpenAI-compatible)
  anthropic-messages POST {base}/v1/messages (Anthropic Messages API)
  responses          POST {base}/responses (OpenAI Responses API)
Full chat paths pasted as base_url are accepted as-is for every format.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.integration import LLMProvider

PROVIDER_BASE_URLS: Dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "github": "https://models.github.ai/inference",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

ANTHROPIC_VERSION = "2023-06-01"

_LITELLM_PREFIX = re.compile(r"^(gemini|groq|openrouter|github|openai|anthropic|deepseek|mistral)/", re.I)


def to_openai_compatible(
    provider: str, model: str, base_url: str
) -> Tuple[str, Optional[str]]:
    """Translate LiteLLM (provider, model) -> (openai_model_id, base_url|None).

    Strips the litellm vendor prefix (gemini/, groq/, openrouter/, github/)
    because OpenAI-compatible endpoints expect the bare model id. Custom and
    unknown providers pass the model through unchanged.
    """
    provider = (provider or "").lower().strip()
    model = (model or "").strip()
    base_url = (base_url or "").strip()

    short = _LITELLM_PREFIX.sub("", model) if provider != "custom" else model
    if provider in PROVIDER_BASE_URLS and not base_url:
        base_url = PROVIDER_BASE_URLS[provider]
    if provider == "openai":
        base_url = base_url or ""
    return short or model, (base_url or None)


def to_native_model(provider: str, model: str) -> str:
    """Bare model id for native Anthropic/Responses endpoints.

    Strips the LiteLLM vendor prefix (anthropic/, openai/, ...) while custom
    provider ids pass through verbatim.
    """
    provider = (provider or "").lower().strip()
    model = (model or "").strip()
    if provider == "custom":
        return model
    return _LITELLM_PREFIX.sub("", model) or model


def join_endpoint(base_url: str, api_format: str) -> str:
    """Append the wire path unless base_url already ends with it.

    Accepts full chat paths pasted as base_url (e.g. .../v1/chat/completions).
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url is required to build the request endpoint.")
    low = base.lower()
    if api_format == "anthropic-messages":
        if low.endswith(("/v1/messages", "/messages")):
            return base
        return base + "/v1/messages"
    if api_format == "responses":
        if low.endswith("/responses"):
            return base
        return base + "/responses"
    if low.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def effective_base_url(api_format: str, provider: str, base_url: str) -> str:
    """Vendor default base when the doc leaves base_url blank."""
    base_url = (base_url or "").strip()
    if base_url:
        return base_url
    return PROVIDER_BASE_URLS.get((provider or "").lower(), "")


def build_auth_headers(
    *, auth_type: str, api_key: str, auth_header: str = ""
) -> Dict[str, str]:
    """Headers for non-SDK (raw httpx) calls to custom gateways.

    - bearer:    Authorization: Bearer <key> (OpenAI-compatible default)
    - x-api-key: <auth_header or x-api-key>: <key> (Anthropic-style gateways)
    """
    auth_type = (auth_type or "bearer").lower().strip()
    if auth_type == "x-api-key":
        name = (auth_header or "x-api-key").strip()
        return {name: api_key}
    return {"Authorization": f"Bearer {api_key}"}


def nodes_to_openai_tools(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for node in nodes or []:
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        schema = node.get("node_schema") or {}
        inputs = schema.get("inputs") or {}
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for input_name, input_type in inputs.items():
            properties[input_name] = {
                "type": "string" if input_type == "string" else "number",
                "description": f"{input_name} parameter",
            }
            required.append(input_name)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(node.get("description") or name)[:1000],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return tools


def parse_call_tool_text(output: str) -> Optional[Dict[str, Any]]:
    if not output or "CALL_TOOL:" not in output:
        return None
    match = re.search(r"CALL_TOOL:\s*([A-Za-z0-9_]+)\((.*?)\)", output, re.DOTALL)
    if not match:
        return None
    inputs: Dict[str, Any] = {}
    params_str = match.group(2) or ""
    for m in re.finditer(r'(\w+)\s*=\s*["\'](.*?)["\']', params_str):
        inputs[m.group(1)] = m.group(2)
    for m in re.finditer(r"(\w+)\s*=\s*([^,\"'\s)]+)", params_str):
        inputs.setdefault(m.group(1), m.group(2))
    return {"name": match.group(1), "inputs": inputs}


def clean_text_output(output: str) -> str:
    text = (output or "").strip()
    if "Output:" in text:
        text = text.split("Output:")[-1].strip()
    text = re.sub(r"^Reasoning:.*?(?=\n\n|\Z)", "", text, flags=re.I | re.S).strip()
    text = re.sub(r"CALL_TOOL:.*", "", text).strip()
    return text


def build_prompt_messages(
    project: Dict[str, Any],
    user_message: str,
    nodes: List[Dict[str, Any]],
    conversation_history: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    nodes_context = "Available tools:\n"
    for node in nodes or []:
        schema = node.get("node_schema") or {}
        inputs = schema.get("inputs") or {}
        nodes_context += (
            f"- {node.get('name')}({', '.join(inputs.keys())}): "
            f"{node.get('description')}\n"
        )
    system_content = (
        f"{project.get('llm_prompt', 'You are a helpful assistant.')}\n\n"
        f"{nodes_context}\n"
        "Engine rules:\n"
        "1. Prefer native function calling when tools are provided.\n"
        "2. If tools are NOT provided, output exactly: "
        'CALL_TOOL: ToolName(param="value") to call a tool.\n'
        "3. Only call a tool when required parameters are known; otherwise ask.\n"
        "4. Never invent tool results."
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    for msg in (conversation_history or [])[-20:]:
        role = msg.get("role") or "user"
        content = str(msg.get("content") or "")
        if not content:
            continue
        if role not in ("user", "assistant", "system"):
            role = "user"
        if role == "system" and len(content) > 4000:
            content = content[:4000] + "..."
        messages.append({"role": role, "content": content})
    if not messages or messages[-1].get("content") != user_message:
        messages.append({"role": "user", "content": user_message})
    return messages


def _has_litellm_package() -> bool:
    try:
        import litellm  # noqa: F401

        return True
    except ImportError:
        return False


class LiteLLMProvider(LLMProvider):
    """One resolved cloud endpoint, swappable by DB config.

    Resolved config keys: provider, model (LiteLLM id), api_key, base_url,
    extra_params, temperature, max_tokens, top_p, timeout_seconds.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config or {})
        self.model = str(self.config.get("model") or "").strip()
        if not self.model:
            raise ValueError("LiteLLM provider config is missing 'model'")
        self.provider_key = str(self.config.get("provider") or "custom").lower()
        self.provider_label = str(
            self.config.get("provider_id") or self.provider_key or self.model
        )

    def _openai_client(self):
        from openai import AsyncOpenAI

        api_key = (self.config.get("api_key") or "").strip()
        if not api_key:
            raise ValueError(
                f"Provider '{self.provider_label}' has no API key: store api_key "
                "via POST /api/llm-providers or set its api_key_env var."
            )
        auth_type = str(self.config.get("auth_type") or "bearer").lower()
        if auth_type not in {"bearer", ""}:
            raise ValueError(
                f"Provider '{self.provider_label}' uses auth_type={auth_type!r}, "
                "which the OpenAI SDK cannot send — the raw-HTTP path is used "
                "automatically. Do not construct the SDK client directly."
            )
        _, base_url = to_openai_compatible(
            self.provider_key, self.model, str(self.config.get("base_url") or "")
        )
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "timeout": float(self.config.get("timeout_seconds") or 60),
        }
        if base_url:
            kwargs["base_url"] = base_url
        return AsyncOpenAI(**kwargs)

    def _uses_raw_http(self) -> bool:
        return str(self.config.get("auth_type") or "bearer").lower() not in {
            "bearer",
            "",
        }

    async def _raw_http_chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        """Raw httpx chat for custom auth (x-api-key / query-param gateways)."""
        import httpx

        api_key = (self.config.get("api_key") or "").strip()
        if not api_key:
            raise ValueError(f"Provider '{self.provider_label}' has no API key.")
        model_id, base_url = to_openai_compatible(
            self.provider_key, self.model, str(self.config.get("base_url") or "")
        )
        if not base_url:
            raise ValueError(
                f"Provider '{self.provider_label}' needs base_url for raw-HTTP chat."
            )
        auth_type = str(self.config.get("auth_type") or "bearer").lower()
        headers = build_auth_headers(
            auth_type=auth_type,
            api_key=api_key,
            auth_header=str(self.config.get("auth_header") or ""),
        )
        headers["Content-Type"] = "application/json"
        extra = dict(self.config.get("extra_params") or {})
        extra_headers = extra.pop("extra_headers", None)
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        payload: Dict[str, Any] = {
            "model": (self.config.get("model_override") or model_id),
            "messages": messages,
            "temperature": float(
                temperature if temperature is not None else self.config.get("temperature") or 0.4
            ),
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 1024),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(extra)
        url = base_url.rstrip("/") + "/chat/completions"
        params = None
        if auth_type == "query":
            params = {(self.config.get("auth_query_param") or "key"): api_key}
        tag = (
            f"gateway=raw_http provider={self.provider_label} "
            f"model={self.model} auth={auth_type}"
        )
        async with httpx.AsyncClient(
            timeout=float(self.config.get("timeout_seconds") or 60)
        ) as client:
            resp = await client.post(url, headers=headers, params=params, json=payload)
            resp.raise_for_status()
            data = resp.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Unexpected chat response shape: {exc}") from exc
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            fn = tool_calls[0].get("function") or {}
            try:
                inputs = json.loads(fn.get("arguments") or "{}")
            except Exception:
                inputs = {}
            return {
                "action": "call_node",
                "node_name": fn.get("name"),
                "inputs": inputs if isinstance(inputs, dict) else {},
                "text_response": message.get("content")
                or f"Calling {fn.get('name')}...",
                "debug_info": tag + " native_tools=1",
            }
        text = (message.get("content") or "").strip()
        return {"text": text, "tag": tag}

    def _raw_auth(self) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
        """(headers, query params) for raw httpx calls from stored auth config."""
        api_key = (self.config.get("api_key") or "").strip()
        auth_type = str(self.config.get("auth_type") or "bearer").lower()
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        params: Optional[Dict[str, str]] = None
        if api_key and auth_type != "none":
            if auth_type == "query":
                params = {(self.config.get("auth_query_param") or "key"): api_key}
            else:
                headers.update(
                    build_auth_headers(
                        auth_type=auth_type,
                        api_key=api_key,
                        auth_header=str(self.config.get("auth_header") or ""),
                    )
                )
        extra = dict(self.config.get("extra_params") or {})
        extra_headers = extra.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        return headers, params

    def _native_timeout(self) -> float:
        return float(self.config.get("timeout_seconds") or 60)

    # ---------- Anthropic Messages API ----------

    def _anthropic_payload(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        system_parts: List[str] = []
        converted: List[Dict[str, str]] = []
        for msg in messages:
            role = msg.get("role") or "user"
            content = str(msg.get("content") or "")
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": content})
            else:
                converted.append({"role": "user", "content": content})
        payload: Dict[str, Any] = {
            "model": to_native_model(self.provider_key, self.model),
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 1024),
            "temperature": float(
                temperature if temperature is not None else self.config.get("temperature") or 0.4
            ),
            "messages": converted,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object"}),
                }
                for t in tools
            ]
            payload["tool_choice"] = {"type": "auto"}
        return payload

    @staticmethod
    def _parse_anthropic_response(data: Dict[str, Any]) -> Dict[str, Any]:
        blocks = data.get("content") or []
        if not isinstance(blocks, list):
            raise ValueError("Unexpected Anthropic response shape.")
        texts: List[str] = []
        tool_use: Optional[Dict[str, Any]] = None
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]))
            elif block.get("type") == "tool_use" and tool_use is None:
                tool_use = block
        text = "\n".join(texts).strip()
        if tool_use:
            inputs = tool_use.get("input") or {}
            return {
                "action": "call_node",
                "node_name": tool_use.get("name"),
                "inputs": inputs if isinstance(inputs, dict) else {},
                "text_response": text or f"Calling {tool_use.get('name')}...",
            }
        return {"action": "chat", "text_response": text}

    async def _generate_via_anthropic(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        import httpx

        base = effective_base_url(
            "anthropic-messages", self.provider_key, str(self.config.get("base_url") or "")
        )
        url = join_endpoint(base, "anthropic-messages")
        headers, params = self._raw_auth()
        headers["anthropic-version"] = ANTHROPIC_VERSION
        messages = build_prompt_messages(project, user_message, nodes, conversation_history)
        tools = nodes_to_openai_tools(nodes) if nodes else []
        tag = f"gateway=anthropic provider={self.provider_label} model={self.model}"
        payload = self._anthropic_payload(messages, tools or None, temperature, None)
        async with httpx.AsyncClient(timeout=self._native_timeout()) as client:
            try:
                resp = await client.post(url, headers=headers, params=params, json=payload)
                resp.raise_for_status()
                result = self._parse_anthropic_response(resp.json())
            except Exception as exc:
                err = str(exc).lower()
                if tools and any(m in err for m in ("tool", "function", "unsupported", "unrecognized")):
                    payload = self._anthropic_payload(messages, None, temperature, None)
                    resp = await client.post(url, headers=headers, params=params, json=payload)
                    resp.raise_for_status()
                    result = self._parse_anthropic_response(resp.json())
                else:
                    raise
        if result.get("action") == "call_node":
            result["debug_info"] = tag + " native_tools=1"
            return result
        text = result.get("text_response", "")
        parsed = parse_call_tool_text(text) if nodes else None
        if parsed:
            return {
                "action": "call_node",
                "node_name": parsed["name"],
                "inputs": parsed["inputs"],
                "text_response": clean_text_output(text) or f"Calling {parsed['name']}...",
                "debug_info": tag + " text_fallback=1",
            }
        return {
            "action": "chat",
            "text_response": clean_text_output(text) or text,
            "debug_info": tag,
        }

    # ---------- OpenAI Responses API ----------

    def _responses_payload(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": to_native_model(self.provider_key, self.model),
            "input": [
                {"role": m.get("role") or "user", "content": str(m.get("content") or "")}
                for m in messages
                if str(m.get("content") or "")
            ],
            "temperature": float(
                temperature if temperature is not None else self.config.get("temperature") or 0.4
            ),
            "max_output_tokens": int(max_tokens or self.config.get("max_tokens") or 1024),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {"type": "object"}),
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        return payload

    @staticmethod
    def _parse_responses_response(data: Dict[str, Any]) -> Dict[str, Any]:
        output = data.get("output") or []
        if not isinstance(output, list):
            raise ValueError("Unexpected Responses API shape.")
        texts: List[str] = []
        call: Optional[Dict[str, Any]] = None
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call" and call is None:
                call = item
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                        texts.append(str(part["text"]))
        text = "\n".join(texts).strip()
        if call:
            try:
                inputs = json.loads(call.get("arguments") or "{}")
            except Exception:
                inputs = {}
            return {
                "action": "call_node",
                "node_name": call.get("name"),
                "inputs": inputs if isinstance(inputs, dict) else {},
                "text_response": text or f"Calling {call.get('name')}...",
            }
        return {"action": "chat", "text_response": text}

    async def _generate_via_responses(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        import httpx

        base = effective_base_url(
            "responses", self.provider_key, str(self.config.get("base_url") or "")
        )
        url = join_endpoint(base, "responses")
        headers, params = self._raw_auth()
        messages = build_prompt_messages(project, user_message, nodes, conversation_history)
        tools = nodes_to_openai_tools(nodes) if nodes else []
        tag = f"gateway=responses provider={self.provider_label} model={self.model}"
        payload = self._responses_payload(messages, tools or None, temperature, None)
        async with httpx.AsyncClient(timeout=self._native_timeout()) as client:
            try:
                resp = await client.post(url, headers=headers, params=params, json=payload)
                resp.raise_for_status()
                result = self._parse_responses_response(resp.json())
            except Exception as exc:
                err = str(exc).lower()
                if tools and any(m in err for m in ("tool", "function", "unsupported", "unrecognized")):
                    payload = self._responses_payload(messages, None, temperature, None)
                    resp = await client.post(url, headers=headers, params=params, json=payload)
                    resp.raise_for_status()
                    result = self._parse_responses_response(resp.json())
                else:
                    raise
        result["debug_info"] = tag + (" native_tools=1" if result.get("action") == "call_node" else "")
        if result.get("action") == "chat":
            text = result.get("text_response", "")
            result["text_response"] = clean_text_output(text) or text
        return result

    async def list_models(self) -> List[str]:
        """List model ids via GET {base_url}/models (OpenAI-compatible)."""
        import httpx

        api_key = (self.config.get("api_key") or "").strip()
        _, base_url = to_openai_compatible(
            self.provider_key, self.model, str(self.config.get("base_url") or "")
        )
        if not base_url:
            raise ValueError(
                f"Provider '{self.provider_label}' has no base_url; "
                "model listing needs an OpenAI-compatible endpoint."
            )
        headers = (
            build_auth_headers(
                auth_type=str(self.config.get("auth_type") or "bearer"),
                api_key=api_key,
                auth_header=str(self.config.get("auth_header") or ""),
            )
            if api_key
            else {}
        )
        params = None
        if str(self.config.get("auth_type") or "").lower() == "query" and api_key:
            params = {(self.config.get("auth_query_param") or "key"): api_key}
        url = base_url.rstrip("/") + "/models"
        async with httpx.AsyncClient(
            timeout=float(self.config.get("timeout_seconds") or 60)
        ) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("Unexpected /models response shape.")
        return sorted(
            str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")
        )

    def _chat_kwargs(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        model_id, _ = to_openai_compatible(
            self.provider_key, self.model, str(self.config.get("base_url") or "")
        )
        kwargs: Dict[str, Any] = {
            "model": (self.config.get("model_override") or model_id),
            "messages": messages,
            "temperature": float(
                temperature
                if temperature is not None
                else self.config.get("temperature") or 0.4
            ),
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 1024),
        }
        extra = dict(self.config.get("extra_params") or {})
        if isinstance(extra, dict):
            # OpenRouter attribution headers etc. pass through as extra_headers.
            headers = extra.pop("extra_headers", None)
            kwargs.update(extra)
            if headers:
                kwargs["extra_headers"] = headers
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    async def _generate_via_openai_compatible(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        if self._uses_raw_http():
            return await self._generate_via_raw_http(
                project, user_message, nodes, conversation_history, temperature
            )
        client = self._openai_client()
        messages = build_prompt_messages(project, user_message, nodes, conversation_history)
        tools = nodes_to_openai_tools(nodes) if nodes else []
        tag = f"gateway=openai_compatible provider={self.provider_label} model={self.model}"

        if tools:
            try:
                response = await client.chat.completions.create(
                    **self._chat_kwargs(messages, tools, temperature, None)
                )
            except Exception as exc:
                err = str(exc).lower()
                if not any(m in err for m in ("tool", "function", "unsupported", "unrecognized")):
                    raise
                tools = []
                response = await client.chat.completions.create(
                    **self._chat_kwargs(messages, None, temperature, None)
                )
            else:
                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", None) or []
                if tool_calls:
                    fn = tool_calls[0].function
                    try:
                        inputs = json.loads(fn.arguments or "{}")
                    except Exception:
                        inputs = {}
                    return {
                        "action": "call_node",
                        "node_name": fn.name,
                        "inputs": inputs if isinstance(inputs, dict) else {},
                        "text_response": getattr(message, "content", None)
                        or f"Calling {fn.name}...",
                        "debug_info": tag + " native_tools=1",
                    }
                text = (getattr(message, "content", None) or "").strip()
                parsed = parse_call_tool_text(text)
                if parsed:
                    return {
                        "action": "call_node",
                        "node_name": parsed["name"],
                        "inputs": parsed["inputs"],
                        "text_response": clean_text_output(text) or f"Calling {parsed['name']}...",
                        "debug_info": tag + " text_fallback=1",
                    }
                return {
                    "action": "chat",
                    "text_response": clean_text_output(text) or text,
                    "debug_info": tag,
                }

        response = await client.chat.completions.create(
            **self._chat_kwargs(messages, None, temperature, None)
        )
        text = (response.choices[0].message.content or "").strip()
        parsed = parse_call_tool_text(text) if nodes else None
        if parsed:
            return {
                "action": "call_node",
                "node_name": parsed["name"],
                "inputs": parsed["inputs"],
                "text_response": clean_text_output(text) or f"Calling {parsed['name']}...",
                "debug_info": tag + " prompt_tools=1",
            }
        return {
            "action": "chat",
            "text_response": clean_text_output(text) or text,
            "debug_info": tag,
        }

    async def _generate_via_raw_http(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        messages = build_prompt_messages(project, user_message, nodes, conversation_history)
        tools = nodes_to_openai_tools(nodes) if nodes else []
        try:
            result = await self._raw_http_chat(messages, tools or None, temperature, None)
        except Exception as exc:
            err = str(exc).lower()
            if tools and any(m in err for m in ("tool", "function", "unsupported", "unrecognized")):
                result = await self._raw_http_chat(messages, None, temperature, None)
            else:
                raise
        if isinstance(result, dict) and "action" in result:
            return result
        text, tag = result["text"], result["tag"]
        parsed = parse_call_tool_text(text) if nodes else None
        if parsed:
            return {
                "action": "call_node",
                "node_name": parsed["name"],
                "inputs": parsed["inputs"],
                "text_response": clean_text_output(text) or f"Calling {parsed['name']}...",
                "debug_info": tag + (" prompt_tools=1" if tools else ""),
            }
        return {
            "action": "chat",
            "text_response": clean_text_output(text) or text,
            "debug_info": tag,
        }

    async def _generate_via_litellm_package(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float],
    ) -> Dict[str, Any]:
        import litellm

        messages = build_prompt_messages(project, user_message, nodes, conversation_history)
        tools = nodes_to_openai_tools(nodes) if nodes else []
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": float(self.config.get("timeout_seconds") or 60),
            "num_retries": 0,
            "temperature": float(
                temperature if temperature is not None else self.config.get("temperature") or 0.4
            ),
            "max_tokens": int(self.config.get("max_tokens") or 1024),
        }
        if (self.config.get("api_key") or "").strip():
            kwargs["api_key"] = self.config["api_key"].strip()
        if (self.config.get("base_url") or "").strip():
            kwargs["base_url"] = self.config["base_url"].strip()
        extra = self.config.get("extra_params") or {}
        if isinstance(extra, dict):
            kwargs.update(extra)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        tag = f"gateway=litellm provider={self.provider_label} model={self.model}"
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            err = str(exc).lower()
            if tools and any(m in err for m in ("tool", "function", "unsupported")):
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                response = await litellm.acompletion(**kwargs)
            else:
                raise
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            fn = tool_calls[0].function
            try:
                inputs = json.loads(fn.arguments or "{}")
            except Exception:
                inputs = {}
            return {
                "action": "call_node",
                "node_name": fn.name,
                "inputs": inputs if isinstance(inputs, dict) else {},
                "text_response": getattr(message, "content", None) or f"Calling {fn.name}...",
                "debug_info": tag + " native_tools=1",
            }
        text = (getattr(message, "content", None) or "").strip()
        parsed = parse_call_tool_text(text) if nodes else None
        if parsed:
            return {
                "action": "call_node",
                "node_name": parsed["name"],
                "inputs": parsed["inputs"],
                "text_response": clean_text_output(text) or f"Calling {parsed['name']}...",
                "debug_info": tag + " text_fallback=1",
            }
        return {
            "action": "chat",
            "text_response": clean_text_output(text) or text,
            "debug_info": tag,
        }

    async def generate(
        self,
        project: Dict,
        user_message: str,
        nodes: List[Dict],
        conversation_history: List[Dict],
        temperature: Optional[float] = None,
        stop_sequences: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        api_format = str(self.config.get("api_format") or "chat-completions").lower()
        if api_format == "anthropic-messages":
            return await self._generate_via_anthropic(
                project, user_message, nodes, conversation_history, temperature
            )
        if api_format == "responses":
            return await self._generate_via_responses(
                project, user_message, nodes, conversation_history, temperature
            )
        if _has_litellm_package():
            return await self._generate_via_litellm_package(
                project, user_message, nodes, conversation_history, temperature
            )
        return await self._generate_via_openai_compatible(
            project, user_message, nodes, conversation_history, temperature
        )

    async def test_connection(self, prompt: str = "Reply with: ok") -> Dict[str, Any]:
        started = time.perf_counter()
        api_format = str(self.config.get("api_format") or "chat-completions").lower()
        if api_format in {"anthropic-messages", "responses"}:
            project = {"llm_prompt": "You are a helpful assistant.", "llm_config": {}}
            if api_format == "anthropic-messages":
                result = await self._generate_via_anthropic(
                    project, prompt, [], [], 0.0
                )
            else:
                result = await self._generate_via_responses(
                    project, prompt, [], [], 0.0
                )
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "sample_text": str(result.get("text_response") or "")[:500],
            }
        if self._uses_raw_http():
            result = await self._raw_http_chat(
                [{"role": "user", "content": prompt}], None, 0.0, 64
            )
            if isinstance(result, dict) and "action" in result:
                text = result.get("text_response", "")
            else:
                text = result["text"]
            return {
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "sample_text": text[:500],
            }
        if _has_litellm_package():
            import litellm

            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "timeout": float(self.config.get("timeout_seconds") or 60),
                "num_retries": 0,
                "temperature": 0.0,
                "max_tokens": 64,
            }
            if (self.config.get("api_key") or "").strip():
                kwargs["api_key"] = self.config["api_key"].strip()
            if (self.config.get("base_url") or "").strip():
                kwargs["base_url"] = self.config["base_url"].strip()
            response = await litellm.acompletion(**kwargs)
            text = (response.choices[0].message.content or "").strip()
        else:
            client = self._openai_client()
            response = await client.chat.completions.create(
                **self._chat_kwargs(
                    [{"role": "user", "content": prompt}], None, 0.0, 64
                )
            )
            text = (response.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "sample_text": text[:500],
        }
