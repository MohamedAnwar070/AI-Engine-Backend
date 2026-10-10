from __future__ import annotations

from typing import Any, Dict, Optional

from app.config import Config
from app.core.errors import BadRequestError
from app.core.logging import get_logger
from app.runtime.http_executor import execute_http_tool
from app.runtime.pool import (
    ProcessRuntimePool,
    build_javascript_pool,
    build_python_pool,
)


logger = get_logger(__name__)

SUPPORTED_RUNTIMES = ("javascript", "python", "http")


class RuntimeRegistry:
    """Routes tool execution to JS (warm), Python (lazy), or HTTP (no pool)."""

    def __init__(
        self,
        javascript_pool: Optional[ProcessRuntimePool] = None,
        python_pool: Optional[ProcessRuntimePool] = None,
    ) -> None:
        self._javascript = javascript_pool or build_javascript_pool()
        self._python = python_pool or build_python_pool()

    async def start(self) -> None:
        await self._javascript.start()
        logger.info(
            "Runtime registry started",
            extra={
                "event": "runtime_registry_started",
                "javascript": self._javascript.status(),
                "python": self._python.status(),
            },
        )

    async def stop(self) -> None:
        await self._javascript.stop()
        await self._python.stop()

    def status(self) -> Dict[str, Any]:
        return {
            "javascript": self._javascript.status(),
            "python": self._python.status(),
            "http": {"runtime": "http", "active_workers": 0, "mode": "in-process"},
            "supported": list(SUPPORTED_RUNTIMES),
        }

    async def execute(
        self,
        runtime: str,
        *,
        code: str = "",
        input_data: Optional[Dict[str, Any]] = None,
        http_config: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        runtime = (runtime or "javascript").lower().strip()
        input_data = input_data or {}
        timeout_ms = timeout_ms or Config.NODE_EXECUTION_TIMEOUT_MS

        if runtime not in SUPPORTED_RUNTIMES:
            raise BadRequestError(
                f"Unsupported runtime '{runtime}'. Supported: {', '.join(SUPPORTED_RUNTIMES)}",
                code="unsupported_runtime",
            )

        if runtime == "http":
            return await execute_http_tool(
                http_config or {},
                input_data,
                timeout_ms=timeout_ms,
            )

        if runtime == "javascript":
            if not str(code).strip():
                raise BadRequestError(
                    "tool_code is required for javascript runtime",
                    code="tool_code_required",
                )
            return await self._javascript.execute(
                {"code": code, "input_data": input_data, "timeout_ms": timeout_ms},
                timeout_ms=timeout_ms,
            )

        if not str(code).strip():
            raise BadRequestError(
                "tool_code is required for python runtime",
                code="tool_code_required",
            )
        return await self._python.execute(
            {"code": code, "input_data": input_data, "timeout_ms": timeout_ms},
            timeout_ms=timeout_ms,
        )


def build_default_runtime_registry() -> RuntimeRegistry:
    return RuntimeRegistry()
