"""Smoke tests for multi-runtime registry (JS warm, Python lazy, HTTP)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import pytest

from app.runtime.http_executor import execute_http_tool
from app.runtime.registry import RuntimeRegistry, SUPPORTED_RUNTIMES
from app.runtime.pool import build_javascript_pool, build_python_pool


@pytest.mark.asyncio
async def test_javascript_runtime_executes():
    registry = RuntimeRegistry(
        javascript_pool=build_javascript_pool(),
        python_pool=build_python_pool(),
    )
    await registry.start()
    try:
        result = await registry.execute(
            "javascript",
            code="""
async function executeNode(inputs) {
  return { success: true, value: inputs.n * 2 };
}
""",
            input_data={"n": 21},
            timeout_ms=15000,
        )
        assert result.get("ok") is True
        assert result["result"]["value"] == 42
        assert result["result"]["success"] is True
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_python_runtime_lazy_executes():
    registry = RuntimeRegistry(
        javascript_pool=build_javascript_pool(),
        python_pool=build_python_pool(),
    )
    # Python pool starts at zero workers
    assert registry.status()["python"]["active_workers"] == 0
    await registry.start()
    try:
        assert registry.status()["python"]["active_workers"] == 0
        result = await registry.execute(
            "python",
            code="""
def execute_node(inputs):
    return {"success": True, "value": inputs["n"] + 1}
""",
            input_data={"n": 41},
            timeout_ms=15000,
        )
        assert result.get("ok") is True
        assert result["result"]["value"] == 42
        assert registry.status()["python"]["active_workers"] >= 1
    finally:
        await registry.stop()
        assert registry.status()["python"]["active_workers"] == 0


@pytest.mark.asyncio
async def test_http_runtime_mock(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        @property
        def is_success(self):
            return True

        def json(self):
            return {"success": True, "jobs": 3}

        text = '{"success": true}'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, headers=None, json=None, params=None):
            assert method == "POST"
            assert "example.com" in url
            return FakeResponse()

    import app.runtime.http_executor as http_mod

    monkeypatch.setattr(http_mod.httpx, "AsyncClient", FakeClient)

    result = await execute_http_tool(
        {
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body_template": {"q": "{{query}}"},
        },
        {"query": "python"},
        timeout_ms=5000,
    )
    assert result["ok"] is True
    assert result["result"]["jobs"] == 3


def test_supported_runtimes():
    assert "javascript" in SUPPORTED_RUNTIMES
    assert "python" in SUPPORTED_RUNTIMES
    assert "http" in SUPPORTED_RUNTIMES
