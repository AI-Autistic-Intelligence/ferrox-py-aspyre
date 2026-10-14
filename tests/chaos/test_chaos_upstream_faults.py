import asyncio
import time
import pytest
from ferrox_py_aspyre.exact.concurrency_lock import ExactConcurrencyManager
from ferrox_py_aspyre.exact.rate_limiter import ExactRateLimiter, TaskPriority
from ferrox_py_aspyre.exact.fair_queue import ExactFairQueueScheduler


@pytest.mark.asyncio
async def test_chaos_adaptive_backoff_on_minutely_throttle(fake_redis):
    """
    CHAOS TEST: Exact Online reports that remaining minutely calls dropped to 0.
    The rate limiter must automatically inject a pacing sleep before allowing calls through,
    preventing an upstream 429 breach.
    """
    limiter = ExactRateLimiter(fake_redis)

    # Simulate headers where minutely calls are down to 0, reset in 0.5s
    now = time.time()
    headers = {
        "x-ratelimit-minutely-limit": "60",
        "x-ratelimit-minutely-remaining": "0",
        "x-ratelimit-reset": str(int((now + 0.5) * 1000)),
    }
    await limiter.update_from_headers("div_chaos", headers)

    t0 = time.perf_counter()
    delay = await limiter.acquire_permission("div_chaos", priority=TaskPriority.INTERACTIVE)
    elapsed = time.perf_counter() - t0

    assert delay > 0.0
    assert elapsed >= 0.4  # Pacing delay was enforced


@pytest.mark.asyncio
async def test_chaos_fair_queue_unaffected_by_faulty_tenant(fake_redis):
    """
    CHAOS TEST: Tenant A has an ongoing long lock (simulating connection hang/slow upstream call).
    Tenant B pushes tasks. Tenant B MUST be dequeued immediately without waiting for Tenant A's lease to expire.
    """
    concurrency_mgr = ExactConcurrencyManager(fake_redis, default_ttl_ms=10000)
    rate_limiter = ExactRateLimiter(fake_redis)
    scheduler = ExactFairQueueScheduler(fake_redis, concurrency_mgr, rate_limiter)

    # 1. Tenant A has 10 jobs enqueued
    for i in range(10):
        await scheduler.enqueue("Office_A", "div_A", "batch_task", {"i": i})

    # 2. Tenant B has 1 job enqueued
    await scheduler.enqueue("Office_B", "div_B", "urgent_task", {"i": 999}, priority=TaskPriority.INTERACTIVE)

    # 3. Simulate Tenant A being locked by a hung process
    lock_a = await concurrency_mgr.try_acquire("div_A", owner_id="hung_worker", ttl_ms=10000)
    assert lock_a is not None

    # 4. Dequeue: Scheduler sees div_A is locked, skips it in O(1), and serves div_B!
    res = await scheduler.dequeue_next_fair_job()
    assert res is not None
    job, lock_b = res

    assert job.division_id == "div_B"
    assert job.office_id == "Office_B"

    await lock_a.release()
    await lock_b.release()
