from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple

from app.core.logging import get_logger
from app.schemas import NodeExecutionResponse, ProjectTestRequest


logger = get_logger(__name__)


class OrchestrationBackend(Protocol):
    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def get_cached_execution(
        self, cache_key: str
    ) -> Optional[NodeExecutionResponse]: ...
    async def set_cached_execution(
        self, cache_key: str, response: NodeExecutionResponse, ttl_seconds: int
    ) -> None: ...
    async def enqueue_test_run(
        self, test_run_id: str, project_api_id: str, request: ProjectTestRequest
    ) -> None: ...
    async def dequeue_test_run(
        self, timeout_seconds: int = 1
    ) -> Optional[Tuple[str, str, ProjectTestRequest]]: ...
    async def acquire_test_run_lock(
        self, test_run_id: str, worker_id: str, ttl_seconds: int = 300
    ) -> bool: ...
    async def release_test_run_lock(self, test_run_id: str, worker_id: str) -> None: ...


@dataclass
class MemoryCacheEntry:
    response: NodeExecutionResponse
    expires_at: float


class InMemoryOrchestrationBackend:
    def __init__(self) -> None:
        self._cache: Dict[str, MemoryCacheEntry] = {}
        self._queue: asyncio.Queue[Tuple[str, str, ProjectTestRequest]] = (
            asyncio.Queue()
        )
        self._cache_lock = asyncio.Lock()
        self._locks: Dict[str, str] = {}

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_cached_execution(
        self, cache_key: str
    ) -> Optional[NodeExecutionResponse]:
        now = asyncio.get_running_loop().time()
        async with self._cache_lock:
            entry = self._cache.get(cache_key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(cache_key, None)
                return None
            return entry.response

    async def set_cached_execution(
        self, cache_key: str, response: NodeExecutionResponse, ttl_seconds: int
    ) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = asyncio.get_running_loop().time() + ttl_seconds
        async with self._cache_lock:
            self._cache[cache_key] = MemoryCacheEntry(
                response=response, expires_at=expires_at
            )

    async def enqueue_test_run(
        self, test_run_id: str, project_api_id: str, request: ProjectTestRequest
    ) -> None:
        await self._queue.put((test_run_id, project_api_id, request))

    async def dequeue_test_run(
        self, timeout_seconds: int = 1
    ) -> Optional[Tuple[str, str, ProjectTestRequest]]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None

    async def acquire_test_run_lock(
        self, test_run_id: str, worker_id: str, ttl_seconds: int = 300
    ) -> bool:
        owner = self._locks.get(test_run_id)
        if owner and owner != worker_id:
            return False
        self._locks[test_run_id] = worker_id
        return True

    async def release_test_run_lock(self, test_run_id: str, worker_id: str) -> None:
        if self._locks.get(test_run_id) == worker_id:
            self._locks.pop(test_run_id, None)


class RedisOrchestrationBackend:
    CACHE_KEY_PREFIX = "arffy:execution-cache:"
    TEST_QUEUE_KEY = "arffy:test-runs:queue"
    TEST_LOCK_PREFIX = "arffy:test-runs:lock:"

    def __init__(self, redis_url: str) -> None:
        from redis.asyncio import Redis

        self.redis = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)

    async def startup(self) -> None:
        await self.redis.ping()
        logger.info(
            "Redis orchestration backend connected", extra={"event": "redis_connected"}
        )

    async def shutdown(self) -> None:
        await self.redis.aclose()

    async def get_cached_execution(
        self, cache_key: str
    ) -> Optional[NodeExecutionResponse]:
        payload = await self.redis.get(f"{self.CACHE_KEY_PREFIX}{cache_key}")
        if not payload:
            return None
        return NodeExecutionResponse.model_validate_json(payload)

    async def set_cached_execution(
        self, cache_key: str, response: NodeExecutionResponse, ttl_seconds: int
    ) -> None:
        if ttl_seconds <= 0:
            return
        await self.redis.set(
            f"{self.CACHE_KEY_PREFIX}{cache_key}",
            response.model_dump_json(),
            ex=ttl_seconds,
        )

    async def enqueue_test_run(
        self, test_run_id: str, project_api_id: str, request: ProjectTestRequest
    ) -> None:
        payload = json.dumps(
            {
                "test_run_id": test_run_id,
                "project_api_id": project_api_id,
                "request": request.model_dump(mode="json"),
            },
            ensure_ascii=True,
        )
        await self.redis.lpush(self.TEST_QUEUE_KEY, payload)

    async def dequeue_test_run(
        self, timeout_seconds: int = 1
    ) -> Optional[Tuple[str, str, ProjectTestRequest]]:
        result = await self.redis.brpop(self.TEST_QUEUE_KEY, timeout=timeout_seconds)
        if not result:
            return None
        _, payload = result
        parsed = json.loads(payload)
        return (
            parsed["test_run_id"],
            parsed["project_api_id"],
            ProjectTestRequest.model_validate(parsed["request"]),
        )

    async def acquire_test_run_lock(
        self, test_run_id: str, worker_id: str, ttl_seconds: int = 300
    ) -> bool:
        return bool(
            await self.redis.set(
                f"{self.TEST_LOCK_PREFIX}{test_run_id}",
                worker_id,
                ex=ttl_seconds,
                nx=True,
            )
        )

    async def release_test_run_lock(self, test_run_id: str, worker_id: str) -> None:
        key = f"{self.TEST_LOCK_PREFIX}{test_run_id}"
        owner = await self.redis.get(key)
        if owner == worker_id:
            await self.redis.delete(key)


def build_orchestration_backend(redis_url: Optional[str]) -> OrchestrationBackend:
    if not redis_url:
        logger.warning(
            "REDIS_URL not configured; using in-memory orchestration backend",
            extra={"event": "orchestration_fallback_memory"},
        )
        return InMemoryOrchestrationBackend()

    try:
        return RedisOrchestrationBackend(redis_url)
    except Exception:
        logger.exception(
            "Failed to initialize Redis orchestration backend; using in-memory fallback",
            extra={"event": "orchestration_redis_init_failed"},
        )
        return InMemoryOrchestrationBackend()
