"""
Strict Concurrency=1 Distributed Semaphore for Exact Online.
Ensures no division ever executes more than one concurrent REST request.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import TracebackType
from typing import Any

from redis.asyncio import Redis
import structlog

logger = structlog.get_logger(__name__)

# Lua script to atomically acquire lock if key does not exist or matches current owner
ACQUIRE_LUA = """
local key = KEYS[1]
local owner = ARGV[1]
local ttl_ms = tonumber(ARGV[2])

if redis.call("EXISTS", key) == 0 then
    redis.call("SET", key, owner, "PX", ttl_ms)
    return 1
end

if redis.call("GET", key) == owner then
    redis.call("PEXPIRE", key, ttl_ms)
    return 1
end

return 0
"""

# Lua script to safely release lock only if owned
RELEASE_LUA = """
local key = KEYS[1]
local owner = ARGV[1]

if redis.call("GET", key) == owner then
    return redis.call("DEL", key)
end

return 0
"""


class ExactConcurrencyException(Exception):
    """Raised when an operation cannot acquire the strict concurrency=1 lease."""
    pass


class ExactDivisionLock:
    """
    Context manager representing a single acquired lease for a division.
    """

    def __init__(
        self,
        redis: Redis,
        division_id: str,
        owner_id: str,
        ttl_ms: int = 15000,
    ) -> None:
        self.redis = redis
        self.division_id = division_id
        self.owner_id = owner_id
        self.ttl_ms = ttl_ms
        self.key = f"ferrox:exact:concurrency:{division_id}"
        self._release_script = self.redis.register_script(RELEASE_LUA)

    async def release(self) -> None:
        try:
            await self._release_script(keys=[self.key], args=[self.owner_id])
            logger.debug("exact_lock_released", division=self.division_id, owner=self.owner_id)
        except Exception as e:
            logger.warn("exact_lock_release_failed", division=self.division_id, error=str(e))

    async def __aenter__(self) -> ExactDivisionLock:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.release()


class ExactConcurrencyManager:
    """
    Manages strict Concurrency=1 locks across all Exact Online divisions.
    """

    def __init__(self, redis: Redis, default_ttl_ms: int = 15000) -> None:
        self.redis = redis
        self.default_ttl_ms = default_ttl_ms
        self._acquire_script = self.redis.register_script(ACQUIRE_LUA)

    def _lock_key(self, division_id: str) -> str:
        return f"ferrox:exact:concurrency:{division_id}"

    async def try_acquire(
        self,
        division_id: str,
        owner_id: str | None = None,
        ttl_ms: int | None = None,
    ) -> ExactDivisionLock | None:
        """
        Attempts to acquire the lease immediately. Returns None if already locked.
        """
        owner = owner_id or str(uuid.uuid4())
        ttl = ttl_ms or self.default_ttl_ms
        key = self._lock_key(division_id)

        result = await self._acquire_script(keys=[key], args=[owner, str(ttl)])
        if result == 1:
            logger.debug("exact_lock_acquired", division=division_id, owner=owner)
            return ExactDivisionLock(self.redis, division_id, owner, ttl)
        return None

    async def acquire_with_timeout(
        self,
        division_id: str,
        timeout_seconds: float = 5.0,
        retry_interval_seconds: float = 0.05,
    ) -> ExactDivisionLock:
        """
        Polls until lease is acquired or timeout occurs.
        """
        owner = str(uuid.uuid4())
        deadline = time.perf_counter() + timeout_seconds

        while time.perf_counter() < deadline:
            lock = await self.try_acquire(division_id, owner_id=owner)
            if lock:
                return lock
            await asyncio.sleep(retry_interval_seconds)

        raise ExactConcurrencyException(
            f"Timeout waiting for Exact Online concurrency=1 lease for division {division_id}"
        )

    async def is_locked(self, division_id: str) -> bool:
        """Checks if a division is currently processing a call."""
        key = self._lock_key(division_id)
        val = await self.redis.get(key)
        return val is not None
