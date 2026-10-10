from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from app.core.errors import InternalServerError
from app.core.logging import get_logger


logger = get_logger(__name__)


class ProcessRuntimeWorker:
    """Language-agnostic stdin/stdout JSON-line worker process."""

    def __init__(
        self,
        worker_id: str,
        command: Sequence[str],
        workdir: Path,
        runtime_name: str,
    ) -> None:
        self.worker_id = worker_id
        self.command = list(command)
        self.workdir = workdir
        self.runtime_name = runtime_name
        self.process: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.last_used_at = time.monotonic()

    async def start(self) -> None:
        async with self._lock:
            if self.process and self.process.returncode is None:
                return

            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.workdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stdout_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            self.last_used_at = time.monotonic()
            logger.info(
                "Runtime worker started",
                extra={
                    "event": "runtime_worker_started",
                    "worker_id": self.worker_id,
                    "runtime": self.runtime_name,
                },
            )

    async def stop(self) -> None:
        process = self.process
        self.process = None

        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        for task in (self._stdout_task, self._stderr_task):
            if task:
                task.cancel()

        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(
                    InternalServerError(
                        f"{self.runtime_name} runtime worker stopped unexpectedly",
                        code="runtime_worker_stopped",
                    )
                )

    async def restart(self) -> None:
        logger.warning(
            "Restarting runtime worker",
            extra={
                "event": "runtime_worker_restarting",
                "worker_id": self.worker_id,
                "runtime": self.runtime_name,
            },
        )
        await self.stop()
        await self.start()

    async def execute(self, payload: Dict[str, Any], timeout_ms: int) -> Dict[str, Any]:
        await self.start()
        if not self.process or not self.process.stdin:
            raise InternalServerError(
                f"{self.runtime_name} runtime process is unavailable",
                code="runtime_unavailable",
            )

        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        self.last_used_at = time.monotonic()

        message = json.dumps({"id": request_id, **payload}, ensure_ascii=True) + "\n"
        try:
            self.process.stdin.write(message.encode("utf-8"))
            await self.process.stdin.drain()
        except Exception as exc:
            self._pending.pop(request_id, None)
            await self.restart()
            raise InternalServerError(
                f"Failed to dispatch execution to {self.runtime_name} runtime: {exc}",
                code="runtime_dispatch_failed",
            ) from exc

        try:
            response = await asyncio.wait_for(future, timeout=timeout_ms / 1000 + 2)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            await self.restart()
            raise InternalServerError(
                f"{self.runtime_name} runtime response timeout",
                code="runtime_response_timeout",
            ) from exc

        response["worker_id"] = self.worker_id
        self.last_used_at = time.monotonic()
        return response

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                payload = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse runtime worker stdout line",
                    extra={
                        "event": "runtime_worker_stdout_parse_failed",
                        "worker_id": self.worker_id,
                        "runtime": self.runtime_name,
                    },
                )
                continue

            response_id = payload.get("id")
            future = self._pending.pop(response_id, None)
            if future and not future.done():
                future.set_result(payload)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            logger.warning(
                line.decode("utf-8", errors="replace").rstrip(),
                extra={
                    "event": "runtime_worker_stderr",
                    "worker_id": self.worker_id,
                    "runtime": self.runtime_name,
                },
            )


class ProcessRuntimePool:
    """
    Process pool for a single language runtime.

    min_workers=0 → lazy (no processes until first execute)
    min_workers>=1 → warm pool started on start()
    idle_ttl_seconds → scale-to-zero when idle (for lazy runtimes)
    """

    def __init__(
        self,
        *,
        runtime_name: str,
        command: Sequence[str],
        workdir: Path,
        min_workers: int = 1,
        max_workers: int = 2,
        idle_ttl_seconds: int = 300,
    ) -> None:
        self.runtime_name = runtime_name
        self.command = list(command)
        self.workdir = workdir
        self.min_workers = max(0, min_workers)
        self.max_workers = max(self.min_workers or 1, max_workers)
        self.idle_ttl_seconds = max(0, idle_ttl_seconds)
        self._workers: List[ProcessRuntimeWorker] = []
        self._next_index = 0
        self._started = False
        self._startup_lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None
        self._last_execute_at = time.monotonic()
        self._semaphore = asyncio.Semaphore(self.max_workers)

    def _make_worker(self, index: int) -> ProcessRuntimeWorker:
        return ProcessRuntimeWorker(
            worker_id=f"{self.runtime_name}-{index + 1}",
            command=self.command,
            workdir=self.workdir,
            runtime_name=self.runtime_name,
        )

    async def start(self) -> None:
        async with self._startup_lock:
            if self._started:
                return
            if self.min_workers > 0:
                self._workers = [
                    self._make_worker(index) for index in range(self.min_workers)
                ]
                for worker in self._workers:
                    await worker.start()
            self._started = True
            if self.idle_ttl_seconds > 0 and self.min_workers == 0:
                self._reaper_task = asyncio.create_task(self._idle_reaper())

    async def stop(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        for worker in self._workers:
            await worker.stop()
        self._workers.clear()
        self._started = False

    async def _ensure_workers(self) -> None:
        async with self._startup_lock:
            if not self._workers:
                count = max(self.min_workers, 1)
                self._workers = [self._make_worker(index) for index in range(count)]
                for worker in self._workers:
                    await worker.start()
                logger.info(
                    "Lazy runtime pool scaled up",
                    extra={
                        "event": "runtime_pool_scaled_up",
                        "runtime": self.runtime_name,
                        "workers": len(self._workers),
                    },
                )

    async def _idle_reaper(self) -> None:
        while True:
            await asyncio.sleep(min(30, max(5, self.idle_ttl_seconds // 2 or 30)))
            if not self._workers:
                continue
            idle_for = time.monotonic() - self._last_execute_at
            if idle_for < self.idle_ttl_seconds:
                continue
            async with self._startup_lock:
                if not self._workers:
                    continue
                if time.monotonic() - self._last_execute_at < self.idle_ttl_seconds:
                    continue
                logger.info(
                    "Lazy runtime pool scaled to zero",
                    extra={
                        "event": "runtime_pool_scaled_to_zero",
                        "runtime": self.runtime_name,
                        "idle_seconds": round(idle_for, 1),
                    },
                )
                for worker in self._workers:
                    await worker.stop()
                self._workers.clear()

    async def execute(self, payload: Dict[str, Any], timeout_ms: int) -> Dict[str, Any]:
        await self.start()
        async with self._semaphore:
            await self._ensure_workers()
            worker = self._workers[self._next_index % len(self._workers)]
            self._next_index = (self._next_index + 1) % len(self._workers)
            self._last_execute_at = time.monotonic()
            return await worker.execute(payload, timeout_ms=timeout_ms)

    def status(self) -> Dict[str, Any]:
        return {
            "runtime": self.runtime_name,
            "min_workers": self.min_workers,
            "max_workers": self.max_workers,
            "active_workers": len(self._workers),
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "started": self._started,
        }


# Backward-compatible aliases
NodeRuntimeWorker = ProcessRuntimeWorker
NodeRuntimePool = ProcessRuntimePool


def build_default_runtime_pool() -> ProcessRuntimePool:
    """Legacy helper — JS warm pool only. Prefer RuntimeRegistry."""
    return build_javascript_pool()


def build_javascript_pool() -> ProcessRuntimePool:
    runtime_dir = Path(__file__).resolve().parent
    script_path = runtime_dir / "node_worker.js"
    backend_root = runtime_dir.parent.parent
    pool_size = int(os.getenv("NODE_RUNTIME_POOL_SIZE", "2"))
    return ProcessRuntimePool(
        runtime_name="javascript",
        command=["node", str(script_path)],
        workdir=backend_root,
        min_workers=max(1, pool_size),
        max_workers=max(1, pool_size),
        idle_ttl_seconds=0,
    )


def build_python_pool() -> ProcessRuntimePool:
    runtime_dir = Path(__file__).resolve().parent
    script_path = runtime_dir / "python_worker.py"
    backend_root = runtime_dir.parent.parent
    max_workers = int(os.getenv("PYTHON_RUNTIME_POOL_SIZE", "2"))
    idle_ttl = int(os.getenv("RUNTIME_IDLE_TTL_SECONDS", "300"))
    return ProcessRuntimePool(
        runtime_name="python",
        command=[sys.executable, str(script_path)],
        workdir=backend_root,
        min_workers=0,
        max_workers=max(1, max_workers),
        idle_ttl_seconds=idle_ttl,
    )
