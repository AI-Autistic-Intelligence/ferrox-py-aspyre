import asyncio
import time
import pytest
from ferrox_py_aspyre.exact.concurrency_lock import (
    ExactConcurrencyManager,
    ExactConcurrencyException,
)
from ferrox_py_aspyre.exact.rate_limiter import (
    ExactRateLimiter,
    TaskPriority,
    QuotaExhaustedException,
)
from ferrox_py_aspyre.exact.fair_queue import (
    ExactFairQueueScheduler,
    ExactSyncJob,
)


@pytest.mark.asyncio
async def test_exact_strict_concurrency_lock(fake_redis):
    mgr = ExactConcurrencyManager(fake_redis, default_ttl_ms=5000)

    # Acquire lease for division 101
    lock1 = await mgr.try_acquire("division_101")
    assert lock1 is not None

    # Immediate second acquire for division 101 must fail
    lock2 = await mgr.try_acquire("division_101")
    assert lock2 is None

    # Acquire for division 202 must succeed (divisions are isolated)
    lock_other = await mgr.try_acquire("division_202")
    assert lock_other is not None

    # Release division 101
    await lock1.release()

    # Now division 101 can be acquired again
    lock1_again = await mgr.try_acquire("division_101")
    assert lock1_again is not None
    await lock1_again.release()
    await lock_other.release()


@pytest.mark.asyncio
async def test_exact_rate_limiter_budget_reservation(fake_redis):
    limiter = ExactRateLimiter(fake_redis, default_daily_limit=1000, batch_reserve_threshold=0.80)

    # Simulate headers where 85% of daily quota is consumed (150 remaining out of 1000)
    headers = {
        "X-RateLimit-Limit": "1000",
        "X-RateLimit-Remaining": "150",
        "X-RateLimit-Reset": str(int(time.time() + 3600)),
        "X-RateLimit-Minutely-Limit": "60",
        "X-RateLimit-Minutely-Remaining": "30",
    }
    await limiter.update_from_headers("div_1", headers)

    # Batch task must be rejected to protect the 20% budget reserve
    with pytest.raises(QuotaExhaustedException):
        await limiter.acquire_permission("div_1", priority=TaskPriority.BATCH)

    # Interactive task MUST still be allowed to run!
    delay = await limiter.acquire_permission("div_1", priority=TaskPriority.INTERACTIVE)
    assert delay == 0.0


@pytest.mark.asyncio
async def test_exact_fair_queue_anti_starvation(fake_redis):
    concurrency_mgr = ExactConcurrencyManager(fake_redis)
    rate_limiter = ExactRateLimiter(fake_redis)
    scheduler = ExactFairQueueScheduler(fake_redis, concurrency_mgr, rate_limiter)

    # Division A (heavy batch): enqueue 20 jobs
    for i in range(20):
        await scheduler.enqueue(
            office_id="office_large",
            division_id="div_A",
            task_name="sync_invoice",
            payload={"idx": i},
            priority=TaskPriority.BATCH,
        )

    # Division B (small firm): enqueue 1 urgent job
    await scheduler.enqueue(
        office_id="office_small",
        division_id="div_B",
        task_name="reconcile_bank",
        payload={"urgent": True},
        priority=TaskPriority.INTERACTIVE,
    )

    # Dequeue first job: gets div_A (or div_B depending on set order)
    res1 = await scheduler.dequeue_next_fair_job()
    assert res1 is not None
    job1, lock1 = res1

    # While job1 is executing (lock1 held), dequeue next job:
    # It CANNOT pick another job for the same division because Concurrency=1 is locked!
    # Therefore, the other division MUST be served without waiting for the 20 jobs to complete!
    res2 = await scheduler.dequeue_next_fair_job()
    assert res2 is not None
    job2, lock2 = res2

    # Verify both divisions got picked concurrently without one starving the other
    divisions_served = {job1.division_id, job2.division_id}
    assert divisions_served == {"div_A", "div_B"}

    await lock1.release()
    await lock2.release()
