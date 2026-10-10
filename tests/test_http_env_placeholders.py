"""{{env:VAR}} placeholder resolution for HTTP business connectors (no network)."""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.http_executor import (
    _render_env_placeholders,
    execute_http_tool,
)


def test_env_placeholders_resolve_nested(monkeypatch):
    monkeypatch.setenv("HOSPITAL_API_BASE_URL", "http://localhost:8001")
    monkeypatch.setenv("HOSPITAL_API_KEY", "secret-123")
    rendered, missing = _render_env_placeholders({
        "url": "{{env:HOSPITAL_API_BASE_URL}}/doctors",
        "headers": {"X-API-Key": "{{env:HOSPITAL_API_KEY}}"},
        "body": [{"slot": "{{env:MISSING_ENV_VAR}}"}],
    })
    assert rendered["url"] == "http://localhost:8001/doctors"
    assert rendered["headers"] == {"X-API-Key": "secret-123"}
    assert missing == ["MISSING_ENV_VAR"]


def test_missing_env_fails_closed(monkeypatch):
    monkeypatch.delenv("HOSPITAL_API_KEY", raising=False)
    with pytest.raises(Exception) as exc:
        asyncio.run(execute_http_tool(
            {"method": "GET",
             "url": "http://localhost:8001/meta/services",
             "headers": {"X-API-Key": "{{env:HOSPITAL_API_KEY}}"},
             "body_template": {}},
            {},
            timeout_ms=5000,
        ))
    assert "HOSPITAL_API_KEY" in str(exc.value)


def test_execute_sends_resolved_values(monkeypatch):
    import httpx

    monkeypatch.setenv("HOSPITAL_API_BASE_URL", "http://biz.local")
    monkeypatch.setenv("HOSPITAL_API_KEY", "k-1")
    calls = {}

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        is_success = True

        def json(self):
            return {"success": True, "doctors": [{"name": "Dr. X"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, headers=None, json=None, params=None):
            calls.update(method=method, url=url, headers=headers,
                         json=json, params=params)
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = asyncio.run(execute_http_tool(
        {"method": "GET",
         "url": "{{env:HOSPITAL_API_BASE_URL}}/doctors",
         "headers": {"X-API-Key": "{{env:HOSPITAL_API_KEY}}"},
         "body_template": {"specialty": "{{specialty}}"}},
        {"specialty": "Cardiology"},
        timeout_ms=5000,
    ))
    assert out["ok"] is True
    assert calls["url"] == "http://biz.local/doctors"
    assert calls["headers"]["X-API-Key"] == "k-1"
    assert calls["params"] == {"specialty": "Cardiology"}
    assert out["result"]["doctors"] == [{"name": "Dr. X"}]
