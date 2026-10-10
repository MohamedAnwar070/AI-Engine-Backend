from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._store: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        async with self._lock:
            bucket = self._store.setdefault(key, deque())
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True
