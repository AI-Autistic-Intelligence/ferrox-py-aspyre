"""
Adaptive Rate Limiter & Budget Partitioning for Exact Online REST API.
Enforces Minutely/Daily Quotas, dynamic backoff, and 80/20 interactive/batch reservation.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass
from typing import Mapping

from redis.asyncio import Redis
import structlog

logger = structlog.get_logger(__name__)


class TaskPriority(enum.Enum):
    INTERACTIVE = "interactive"  # User in UI waiting for immediate response
    BATCH = "batch"              # Background continuous scan / polling


class QuotaExhaustedException(Exception):
    """Raised when the daily or minutely quota cannot accommodate the request."""
    pass


@dataclass
class RateLimitStatus:
    limit_minutely: int
    remaining_minutely: int
    limit_daily: int
    remaining_daily: int
    reset_timestamp: float
    updated_at: float

    @property
    def daily_consumed_ratio(self) -> float:
        if self.limit_daily <= 0:
            return 0.0
        return 1.0 - (self.remaining_daily / self.limit_daily)


class ExactRateLimiter:
    """
    Distributed Rate Limiter specifically tuned for Exact Online REST limits.
    """

    def __init__(
        self,
        redis: Redis,
        default_minutely_limit: int = 60,
        default_daily_limit: int = 5000,
        batch_reserve_threshold: float = 0.80,  # 80% consumed = reserve remaining 20% for interactive
    ) -> None:
        self.redis = redis
        self.default_minutely_limit = default_minutely_limit
        self.default_daily_limit = default_daily_limit
        self.batch_reserve_threshold = batch_reserve_threshold

    def _state_key(self, division_id: str) -> str:
        return f"ferrox:exact:ratelimit:{division_id}"

    async def get_status(self, division_id: str) -> RateLimitStatus:
        key = self._state_key(division_id)
        raw = await self.redis.hgetall(key)
        now = time.time()

        if not raw:
            return RateLimitStatus(
                limit_minutely=self.default_minutely_limit,
                remaining_minutely=self.default_minutely_limit,
                limit_daily=self.default_daily_limit,
                remaining_daily=self.default_daily_limit,
                reset_timestamp=now + 60,
                updated_at=now,
            )

        def _val(field: str, fallback: int) -> int:
            b_field = field.encode("utf-8")
            val = raw.get(b_field) or raw.get(field)
            if val is None:
                return fallback
            return int(val.decode("utf-8") if isinstance(val, bytes) else val)

        def _fval(field: str, fallback: float) -> float:
            b_field = field.encode("utf-8")
            val = raw.get(b_field) or raw.get(field)
            if val is None:
                return fallback
            return float(val.decode("utf-8") if isinstance(val, bytes) else val)

        return RateLimitStatus(
            limit_minutely=_val("limit_min", self.default_minutely_limit),
            remaining_minutely=_val("rem_min", self.default_minutely_limit),
            limit_daily=_val("limit_day", self.default_daily_limit),
            remaining_daily=_val("rem_day", self.default_daily_limit),
            reset_timestamp=_fval("reset_ts", now + 60),
            updated_at=_fval("updated_at", now),
        )

    async def update_from_headers(self, division_id: str, headers: Mapping[str, str]) -> None:
        """
        Parses Exact Online standard HTTP rate limit headers and stores them in Redis.
        Headers:
          - X-RateLimit-Limit: Daily total
          - X-RateLimit-Remaining: Daily remaining
          - X-RateLimit-Reset: Epoch ms for daily reset
          - X-RateLimit-Minutely-Limit: Minutely limit (e.g. 60)
          - X-RateLimit-Minutely-Remaining: Minutely remaining
        """
        now = time.time()
        
        # Exact headers can vary in casing
        h = {k.lower(): v for k, v in headers.items()}
        
        limit_day = int(h.get("x-ratelimit-limit", self.default_daily_limit))
        rem_day = int(h.get("x-ratelimit-remaining", limit_day))
        
        limit_min = int(h.get("x-ratelimit-minutely-limit", self.default_minutely_limit))
        rem_min = int(h.get("x-ratelimit-minutely-remaining", limit_min))
        
        reset_val = h.get("x-ratelimit-reset")
        if reset_val:
            try:
                # Can be epoch milliseconds or seconds
                reset_num = float(reset_val)
                reset_ts = reset_num / 1000.0 if reset_num > 1e11 else reset_num
            except ValueError:
                reset_ts = now + 60
        else:
            reset_ts = now + 60

        key = self._state_key(division_id)
        await self.redis.hset(
            key,
            mapping={
                "limit_min": limit_min,
                "rem_min": rem_min,
                "limit_day": limit_day,
                "rem_day": rem_day,
                "reset_ts": reset_ts,
                "updated_at": now,
            },
        )
        # Expire tracking after 2 days of inactivity
        await self.redis.expire(key, 172800)

    async def acquire_permission(
        self,
        division_id: str,
        priority: TaskPriority = TaskPriority.INTERACTIVE,
    ) -> float:
        """
        Evaluates budget and injects pacing delay if required.
        Returns the delay in seconds that was applied.
        Raises QuotaExhaustedException if batch task is disallowed under budget cap.
        """
        status = await self.get_status(division_id)
        now = time.time()

        # 1. Budget Partitioning: Stop batch jobs if daily usage > 80%
        if priority == TaskPriority.BATCH:
            if status.daily_consumed_ratio >= self.batch_reserve_threshold:
                raise QuotaExhaustedException(
                    f"Division {division_id} daily quota {status.daily_consumed_ratio:.1%} exceeds "
                    f"batch threshold ({self.batch_reserve_threshold:.1%}). Reserved for interactive."
                )

        # 2. Minutely Exhaustion check
        if status.remaining_minutely <= 1:
            wait_time = max(0.5, status.reset_timestamp - now)
            if wait_time > 60:
                wait_time = 5.0  # Sanity bound
            logger.info("exact_pacing_minutely_exhaustion", division=division_id, wait_seconds=wait_time)
            await asyncio.sleep(wait_time)
            return wait_time

        # 3. Adaptive Proactive Pacing: If remaining minutely is low, pace calls smoothly
        if status.remaining_minutely < 10:
            # Gradually delay between 200ms and 1000ms
            delay = 0.2 + (10 - status.remaining_minutely) * 0.08
            logger.debug("exact_adaptive_pacing", division=division_id, delay=delay)
            await asyncio.sleep(delay)
            return delay

        return 0.0
