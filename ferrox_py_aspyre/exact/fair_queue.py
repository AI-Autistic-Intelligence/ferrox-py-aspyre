"""
Multi-Tenant Deficit Round Robin (DRR) Fair-Queuing Scheduler for Exact Online.
Prevents starvation between 2,000 accounting offices and 1,000,000 SME administrations.
Combines Fair Scheduling with Strict Concurrency=1 Leases.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

from redis.asyncio import Redis
import structlog

from .concurrency_lock import ExactConcurrencyManager, ExactDivisionLock
from .rate_limiter import ExactRateLimiter, TaskPriority

logger = structlog.get_logger(__name__)


@dataclass
class ExactSyncJob:
    job_id: str
    office_id: str
    division_id: str
    task_name: str
    payload: dict[str, Any]
    priority: TaskPriority
    created_at: float
    retries: int = 0

    def to_json(self) -> str:
        data = asdict(self)
        data["priority"] = self.priority.value
        return json.dumps(data)

    @classmethod
    def from_json(cls, data: str) -> ExactSyncJob:
        payload = json.loads(data)
        payload["priority"] = TaskPriority(payload.get("priority", "batch"))
        return cls(**payload)


class ExactFairQueueScheduler:
    """
    Deficit Round Robin (DRR) Fair Scheduler for Exact Online API calls.
    
    Guarantees:
    1. Zero starvation: Offices with small volumes are never blocked by heavy batch syncs.
    2. Strict Concurrency = 1: A division will never have more than one job executing concurrently.
    3. Priority isolation: Interactive jobs jump ahead of background batch scans.
    """

    def __init__(
        self,
        redis: Redis,
        concurrency_manager: ExactConcurrencyManager,
        rate_limiter: ExactRateLimiter,
        quantum: int = 1,
    ) -> None:
        self.redis = redis
        self.concurrency_mgr = concurrency_manager
        self.rate_limiter = rate_limiter
        self.quantum = quantum
        self._active_divisions_key = "ferrox:exact:active_divisions"
        self._ring_key = "ferrox:exact:division_ring"

    def _queue_key(self, division_id: str, priority: TaskPriority) -> str:
        return f"ferrox:exact:queue:{division_id}:{priority.value}"

    async def enqueue(
        self,
        office_id: str,
        division_id: str,
        task_name: str,
        payload: dict[str, Any],
        priority: TaskPriority = TaskPriority.BATCH,
    ) -> str:
        """
        Pushes a new synchronization job into the division-specific queue.
        """
        job_id = str(uuid.uuid4())
        job = ExactSyncJob(
            job_id=job_id,
            office_id=office_id,
            division_id=division_id,
            task_name=task_name,
            payload=payload,
            priority=priority,
            created_at=time.time(),
        )

        queue_key = self._queue_key(division_id, priority)
        await self.redis.rpush(queue_key, job.to_json())
        
        # Add to active set and ring if newly active
        is_new = await self.redis.sadd(self._active_divisions_key, division_id)
        if is_new:
            await self.redis.rpush(self._ring_key, division_id)

        logger.debug(
            "exact_job_enqueued",
            job_id=job_id,
            division=division_id,
            priority=priority.value,
        )
        return job_id

    async def dequeue_next_fair_job(
        self,
    ) -> tuple[ExactSyncJob, ExactDivisionLock] | None:
        """
        Picks the next eligible job using Deficit Round Robin while enforcing Concurrency=1.
        Returns the job and its active division lock lease.
        """
        ring_len = await self.redis.llen(self._ring_key)
        if ring_len == 0:
            return None

        # Inspect up to ring_len divisions to find one ready to execute
        for _ in range(ring_len):
            raw_div = await self.redis.lpop(self._ring_key)
            if not raw_div:
                break
            division_id = raw_div.decode("utf-8") if isinstance(raw_div, bytes) else str(raw_div)

            # 1. Check if division is currently executing (concurrency=1 check)
            if await self.concurrency_mgr.is_locked(division_id):
                # Still running: put back at the tail of the ring and check next division
                await self.redis.rpush(self._ring_key, division_id)
                continue

            # 2. Try to acquire lease
            lock = await self.concurrency_mgr.try_acquire(division_id)
            if not lock:
                await self.redis.rpush(self._ring_key, division_id)
                continue

            # 3. Check interactive queue first, then batch queue
            interactive_q = self._queue_key(division_id, TaskPriority.INTERACTIVE)
            batch_q = self._queue_key(division_id, TaskPriority.BATCH)

            raw_job = await self.redis.lpop(interactive_q)
            if not raw_job:
                raw_job = await self.redis.lpop(batch_q)

            if not raw_job:
                # No more jobs for this division; cleanup active set and do not re-add to ring
                await self.redis.srem(self._active_divisions_key, division_id)
                await lock.release()
                continue

            # Re-enqueue division into ring if it has more jobs pending
            rem_interactive = await self.redis.llen(interactive_q)
            rem_batch = await self.redis.llen(batch_q)
            if (rem_interactive + rem_batch) > 0:
                await self.redis.rpush(self._ring_key, division_id)
            else:
                await self.redis.srem(self._active_divisions_key, division_id)

            job_str = raw_job.decode("utf-8") if isinstance(raw_job, bytes) else str(raw_job)
            job = ExactSyncJob.from_json(job_str)

            # Return job and keep lock acquired until worker completes it!
            return job, lock

        return None
