"""
Exact Online REST Client with Adaptive Rate Limiting & Concurrency Guard.
"""

from __future__ import annotations

import httpx
from typing import Any, Mapping
from .concurrency_lock import ExactConcurrencyManager, ExactDivisionLock
from .rate_limiter import ExactRateLimiter, TaskPriority
import structlog

logger = structlog.get_logger(__name__)


class ExactOnlineClient:
    """
    Client for interacting with Exact Online REST endpoints.
    Protects every single call with the strict concurrency lease and rate limiter.
    """

    def __init__(
        self,
        concurrency_mgr: ExactConcurrencyManager,
        rate_limiter: ExactRateLimiter,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://start.exactonline.nl/api/v1",
    ) -> None:
        self.concurrency_mgr = concurrency_mgr
        self.rate_limiter = rate_limiter
        self.http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self.base_url = base_url

    async def execute_request(
        self,
        division_id: str,
        method: str,
        path: str,
        token: str,
        priority: TaskPriority = TaskPriority.INTERACTIVE,
        json_data: dict[str, Any] | None = None,
        existing_lock: ExactDivisionLock | None = None,
    ) -> httpx.Response:
        """
        Executes a REST request to Exact Online while respecting concurrency=1 and rate limits.
        """
        # If caller already holds the lease (e.g. from fair scheduler), reuse it; otherwise acquire one
        lock_to_release: ExactDivisionLock | None = None
        if existing_lock is None:
            lock_to_release = await self.concurrency_mgr.acquire_with_timeout(division_id, timeout_seconds=10.0)

        try:
            # Check budget and apply adaptive pacing
            await self.rate_limiter.acquire_permission(division_id, priority=priority)

            url = f"{self.base_url}/{division_id}/{path.lstrip('/')}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            response = await self.http_client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
            )

            # Ingest headers into distributed rate limit tracker
            await self.rate_limiter.update_from_headers(division_id, response.headers)

            if response.status_code == 429:
                logger.error("exact_upstream_429_received", division=division_id, path=path)

            return response
        finally:
            if lock_to_release:
                await lock_to_release.release()
