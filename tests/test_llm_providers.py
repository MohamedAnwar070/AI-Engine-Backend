"""Unit tests for DB-driven LiteLLM layer (no network, no MongoDB)."""

from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest


def test_catalog_has_free_and_paid_presets_with_docs():
    from app.core.llm_catalog import get_catalog

    catalog = get_catalog()
    keys = {entry["key"] for entry in catalog}
    assert {"gemini-flash-free", "groq-llama-free", "openrouter-free",
            "github-models-free", "openai-paid", "custom-openai-compatible"} <= keys
    for entry in catalog:
        assert entry["provider"] and entry["model"] and entry["label"]
        # Free tiers must link to verification docs (limits change frequently).
        if entry["tier"] == "free":
            assert entry["docs_url"].startswith("https://"), entry["key"]
            assert "verify" in entry["limits_note"].lower(), entry["key"]


def test_secrets_roundtrip_and_mask():
    from app.core import secrets

    enc = secrets.encrypt_secret("sk-test-12345678")
    assert enc and enc != "sk-test-12345678"
    assert secrets.decrypt_secret(enc) == "sk-test-12345678"
    assert secrets.decrypt_secret("") == ""
    assert secrets.mask_key("sk-test-12345678") == "****5678"
    assert secrets.mask_key("") == ""


def test_provider_model_validation():
    from app.services_llm_providers import _validate_model_for_provider

    _validate_model_for_provider("gemini", "gemini/gemini-2.0-flash")
    _validate_model_for_provider("custom", "openai/my-model", "https://x.example/v1")
    with pytest.raises(ValueError):
        _validate_model_for_provider("groq", "llama-3.3-70b-versatile")  # missing prefix


def test_nodes_to_tools_and_call_tool_parse():
    from llm.litellm_provider import nodes_to_openai_tools, parse_call_tool_text

    nodes = [{
        "name": "search_jobs",
        "description": "Search jobs",
        "node_schema": {"inputs": {"query": "string", "limit": "number"}},
    }]
    tools = nodes_to_openai_tools(nodes)
    assert tools[0]["function"]["name"] == "search_jobs"
    assert set(tools[0]["function"]["parameters"]["properties"]) == {"query", "limit"}

    parsed = parse_call_tool_text('Reasoning: x\nCALL_TOOL: search_jobs(query="python")')
    assert parsed == {"name": "search_jobs", "inputs": {"query": "python"}}
    assert parse_call_tool_text("no tool here") is None


def test_openai_compatible_translation():
    from llm.litellm_provider import to_openai_compatible

    model, base = to_openai_compatible("gemini", "gemini/gemini-2.0-flash", "")
    assert model == "gemini-2.0-flash"
    assert base == "https://generativelanguage.googleapis.com/v1beta/openai/"

    model, base = to_openai_compatible(
        "openrouter", "openrouter/meta-llama/llama-3.3-70b-instruct:free", "")
    assert model == "meta-llama/llama-3.3-70b-instruct:free"
    assert base == "https://openrouter.ai/api/v1"

    model, base = to_openai_compatible("github", "github/gpt-4o-mini", "")
    assert model == "gpt-4o-mini"
    assert base == "https://models.github.ai/inference"

    model, base = to_openai_compatible("custom", "openai/my-model", "https://x.example/v1")
    assert model == "openai/my-model"
    assert base == "https://x.example/v1"


def _install_fake_openai(response_text=None, tool_call=None):
    import llm.litellm_provider as prov

    class FakeFn:
        def __init__(self):
            self.name = tool_call["name"]
            self.arguments = tool_call["arguments"]

    class FakeMsg:
        def __init__(self):
            self.content = response_text
            self.tool_calls = (
                [types.SimpleNamespace(function=FakeFn())] if tool_call else []
            )

    class FakeChoices:
        message = FakeMsg()

        def __init__(self):
            self.message = FakeMsg()

    class FakeResp:
        choices = [FakeChoices().message and FakeChoices()]

        def __init__(self):
            self.choices = [FakeChoices()]

    class FakeCompletions:
        async def create(self, **kwargs):
            self.last_kwargs = kwargs
            return FakeResp()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.chat = FakeChat()

    prov_module = prov
    original = prov_module.LiteLLMProvider._openai_client

    def _fake_openai_client(self):
        api_key = (self.config.get("api_key") or "").strip()
        if not api_key:
            raise ValueError("no API key")
        return FakeClient(api_key=api_key)

    prov_module.LiteLLMProvider._openai_client = _fake_openai_client
    return original


def test_litellm_native_tool_call(monkeypatch):
    import llm.litellm_provider as prov

    monkeypatch.setattr(prov, "_has_litellm_package", lambda: False)
    original = _install_fake_openai(
        response_text="Calling search_jobs...",
        tool_call={"name": "search_jobs", "arguments": '{"query": "python"}'},
    )
    try:
        provider = prov.LiteLLMProvider({"provider": "groq",
                                         "model": "groq/llama-3.3-70b-versatile",
                                         "api_key": "x", "timeout_seconds": 5})
        project = {"llm_prompt": "help", "llm_config": {}}
        nodes = [{"name": "search_jobs", "description": "Search",
                  "node_schema": {"inputs": {"query": "string"}}}]
        out = asyncio.run(provider.generate(project, "find python jobs", nodes, []))
        assert out["action"] == "call_node"
        assert out["node_name"] == "search_jobs"
        assert out["inputs"] == {"query": "python"}
    finally:
        prov.LiteLLMProvider._openai_client = original


def test_litellm_chat_without_tools(monkeypatch):
    import llm.litellm_provider as prov

    monkeypatch.setattr(prov, "_has_litellm_package", lambda: False)
    original = _install_fake_openai(response_text="Hello, how can I help?")
    try:
        provider = prov.LiteLLMProvider({"provider": "gemini",
                                         "model": "gemini/gemini-2.0-flash",
                                         "api_key": "x", "timeout_seconds": 5})
        out = asyncio.run(provider.generate(
            {"llm_prompt": "help", "llm_config": {}}, "hi", [], []))
        assert out["action"] == "chat"
        assert "Hello" in out["text_response"]
    finally:
        prov.LiteLLMProvider._openai_client = original


def test_runtime_config_prefers_db_key_over_env(monkeypatch):
    from app.services_llm_providers import resolve_api_key, to_runtime_config
    from app.core.secrets import encrypt_secret

    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    doc_env = {"provider": "gemini", "provider_id": "a", "model": "gemini/gemini-2.0-flash",
               "api_key_encrypted": "", "api_key_env": "GEMINI_API_KEY",
               "base_url": "", "extra_params": {}, "default_temperature": 0.4,
               "default_max_tokens": 1024, "default_top_p": 0.9}
    assert resolve_api_key(doc_env) == "env-key"

    doc_db = dict(doc_env, api_key_encrypted=encrypt_secret("db-key"))
    assert resolve_api_key(doc_db) == "db-key"
    cfg = to_runtime_config(doc_db, model_override="gemini/gemini-2.5-flash")
    assert cfg["model"] == "gemini/gemini-2.5-flash"
    assert cfg["api_key"] == "db-key"


def test_llm_config_accepts_provider_binding():
    from app.schemas import LLMConfig

    cfg = LLMConfig(provider_id="llmprov_abc", model_override="groq/llama-3.1-8b-instant")
    assert cfg.provider_id == "llmprov_abc"
    assert LLMConfig().provider_id is None


def test_custom_provider_requires_base_url():
    from app.schemas import LLMProviderCreate

    ok = LLMProviderCreate(name="Local Ollama", provider="custom",
                           model="llama3.1:8b",
                           base_url="http://localhost:11434/v1",
                           auth_type="none")
    assert ok.base_url.startswith("http")

    import pydantic

    with pytest.raises(pydantic.ValidationError):
        LLMProviderCreate(name="Bad custom", provider="custom",
                          model="llama3.1:8b", base_url="")


def test_custom_validation_rejects_bad_url():
    from app.services_llm_providers import _validate_model_for_provider

    _validate_model_for_provider("custom", "llama3.1:8b", "http://localhost:11434/v1")
    with pytest.raises(ValueError):
        _validate_model_for_provider("custom", "llama3.1:8b", "")
    with pytest.raises(ValueError):
        _validate_model_for_provider("custom", "llama3.1:8b", "not-a-url")


def test_build_auth_headers_variants():
    from llm.litellm_provider import build_auth_headers

    assert build_auth_headers(auth_type="bearer", api_key="k") == {
        "Authorization": "Bearer k"}
    assert build_auth_headers(auth_type="x-api-key", api_key="k") == {"x-api-key": "k"}
    assert build_auth_headers(auth_type="x-api-key", api_key="k",
                              auth_header="X-Custom-Key") == {"X-Custom-Key": "k"}


def test_custom_passthrough_keeps_bare_model_id():
    from llm.litellm_provider import to_openai_compatible

    model, base = to_openai_compatible("custom", "llama3.1:8b", "http://localhost:11434/v1")
    assert model == "llama3.1:8b"
    assert base == "http://localhost:11434/v1"


def test_raw_http_chat_tool_call(monkeypatch):
    import llm.litellm_provider as prov

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "Calling get_time...",
                "tool_calls": [{"function": {
                    "name": "get_time",
                    "arguments": '{"tz": "UTC"}'}}]}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            calls["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, params=None, json=None):
            calls.update(url=url, headers=headers, json=json)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(prov, "_has_litellm_package", lambda: False)

    provider = prov.LiteLLMProvider({
        "provider": "custom", "provider_id": "c1", "model": "llama3.1:8b",
        "api_key": "secret", "base_url": "http://localhost:11434/v1",
        "auth_type": "x-api-key", "auth_header": "X-Local-Key",
        "temperature": 0.2, "max_tokens": 256, "timeout_seconds": 5})
    out = asyncio.run(provider.generate(
        {"llm_prompt": "help", "llm_config": {}},
        "what time is it",
        [{"name": "get_time", "description": "time",
          "node_schema": {"inputs": {"tz": "string"}}}], []))
    assert out["action"] == "call_node"
    assert out["node_name"] == "get_time"
    assert out["inputs"] == {"tz": "UTC"}
    assert calls["headers"]["X-Local-Key"] == "secret"
    assert calls["url"] == "http://localhost:11434/v1/chat/completions"


def test_list_models_parses_openai_shape(monkeypatch):
    import llm.litellm_provider as prov

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "b-model"}, {"id": "a-model"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None, params=None):
            assert url.endswith("/models")
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = prov.LiteLLMProvider({
        "provider": "custom", "model": "anything",
        "api_key": "", "base_url": "http://localhost:11434/v1",
        "auth_type": "none", "timeout_seconds": 5})
    assert asyncio.run(provider.list_models()) == ["a-model", "b-model"]


def test_join_endpoint_accepts_full_paths():
    from llm.litellm_provider import join_endpoint

    assert join_endpoint("https://api.example.com/v1", "chat-completions") == \
        "https://api.example.com/v1/chat/completions"
    assert join_endpoint("https://tokenharbor.ai/v1/chat/completions", "chat-completions") == \
        "https://tokenharbor.ai/v1/chat/completions"
    assert join_endpoint("https://api.example.com/v1", "anthropic-messages") == \
        "https://api.example.com/v1/v1/messages"
    assert join_endpoint("https://api.anthropic.com", "anthropic-messages") == \
        "https://api.anthropic.com/v1/messages"
    assert join_endpoint("https://api.example.com/v1", "responses") == \
        "https://api.example.com/v1/responses"
    with pytest.raises(ValueError):
        join_endpoint("", "chat-completions")


def test_anthropic_native_tool_call(monkeypatch):
    import llm.litellm_provider as prov

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [
                {"type": "text", "text": "I'll check the time."},
                {"type": "tool_use", "id": "t1", "name": "get_time",
                 "input": {"tz": "UTC"}},
            ]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, params=None, json=None):
            calls.update(url=url, headers=headers, json=json)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = prov.LiteLLMProvider({
        "provider": "custom", "model": "claude-haiku-4-5",
        "api_key": "k", "base_url": "https://api.example.com/v1",
        "auth_type": "x-api-key", "auth_header": "",
        "api_format": "anthropic-messages", "timeout_seconds": 5})
    out = asyncio.run(provider.generate(
        {"llm_prompt": "help", "llm_config": {}},
        "what time is it",
        [{"name": "get_time", "description": "time",
          "node_schema": {"inputs": {"tz": "string"}}}], []))
    assert out["action"] == "call_node"
    assert out["node_name"] == "get_time"
    assert out["inputs"] == {"tz": "UTC"}
    assert calls["url"] == "https://api.example.com/v1/v1/messages"
    assert calls["headers"]["x-api-key"] == "k"
    assert calls["headers"]["anthropic-version"] == "2023-06-01"
    sent_tool = calls["json"]["tools"][0]
    assert sent_tool["name"] == "get_time"
    assert "input_schema" in sent_tool


def test_responses_native_function_call(monkeypatch):
    import llm.litellm_provider as prov

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": [
                {"type": "function_call", "name": "search_jobs",
                 "arguments": '{"query": "python"}', "call_id": "c1"},
            ]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, params=None, json=None):
            calls.update(url=url, headers=headers, json=json)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    provider = prov.LiteLLMProvider({
        "provider": "openai", "model": "gpt-4o-mini",
        "api_key": "k", "base_url": "",
        "api_format": "responses", "timeout_seconds": 5})
    out = asyncio.run(provider.generate(
        {"llm_prompt": "help", "llm_config": {}},
        "find python jobs",
        [{"name": "search_jobs", "description": "Search",
          "node_schema": {"inputs": {"query": "string"}}}], []))
    assert out["action"] == "call_node"
    assert out["node_name"] == "search_jobs"
    assert out["inputs"] == {"query": "python"}
    assert calls["url"] == "https://api.openai.com/v1/responses"
    assert calls["json"]["tools"][0]["type"] == "function"


def test_api_format_flows_into_runtime_config():
    from app.services_llm_providers import to_runtime_config

    doc = {"provider": "custom", "provider_id": "c", "model": "m",
           "api_key_encrypted": "", "api_key_env": "", "base_url": "https://x/v1",
           "extra_params": {}, "auth_type": "bearer", "auth_header": "",
           "auth_query_param": "", "api_format": "anthropic-messages",
           "default_temperature": 0.4, "default_max_tokens": 1024, "default_top_p": 0.9}
    assert to_runtime_config(doc)["api_format"] == "anthropic-messages"


def test_pinned_provider_id_helper():
    from app.services import _pinned_provider_id

    assert _pinned_provider_id({"llm_config": {"provider_id": "llmprov_x"}}) == "llmprov_x"
    assert _pinned_provider_id({"llm_config": {"provider_id": "  "}}) == ""
    assert _pinned_provider_id({"llm_config": {}}) == ""
    assert _pinned_provider_id({}) == ""


def test_pin_wins_over_legacy_global_mode(monkeypatch):
    """A project pin must route to the DB chain even when global mode is legacy."""
    import app.services as services

    monkeypatch.setattr(services.Config, "LLM_PROVIDER", "openai")
    calls = []

    async def fake_chain(project, user_message, nodes, conversation_history,
                         temperature=None, stop_sequences=None):
        calls.append("chain")
        return {"action": "chat", "text_response": "via chain"}

    class FakeLegacy:
        async def generate(self, **kwargs):
            calls.append("legacy")
            return {"action": "chat", "text_response": "via legacy"}

    monkeypatch.setattr(services, "_generate_via_litellm_chain", fake_chain)
    monkeypatch.setattr(services, "get_llm_provider", lambda: FakeLegacy())

    pinned = {"name": "P", "llm_config": {"provider_id": "llmprov_x"}}
    out = asyncio.run(services.generate_llm_response(pinned, "hi", [], []))
    assert out["text_response"] == "via chain"
    assert calls == ["chain"]

    calls.clear()
    unpinned = {"name": "P", "llm_config": {}}
    out = asyncio.run(services.generate_llm_response(unpinned, "hi", [], []))
    assert out["text_response"] == "via legacy"
    assert calls == ["legacy"]
