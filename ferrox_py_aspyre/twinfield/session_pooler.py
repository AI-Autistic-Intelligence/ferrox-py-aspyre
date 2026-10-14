"""
Twinfield Stateful Session Pooler with Cluster-Aware Routing & Keep-Alive.
Mitigates authentication limits and cluster redirections (e.g., c1/c3.twinfield.com).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from redis.asyncio import Redis
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TwinfieldSession:
    office_code: str
    user_code: str
    session_id: str
    cluster_url: str
    created_at: float
    expires_at: float
    last_activity: float

    @property
    def is_valid(self) -> bool:
        # Buffer of 60 seconds before actual expiration
        return time.time() < (self.expires_at - 60)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> TwinfieldSession:
        payload = json.loads(data)
        return cls(**payload)


class TwinfieldAuthenticator(Protocol):
    async def logon(self, office_code: str, user_code: str, password: str) -> TwinfieldSession:
        """Authenticate against Twinfield Logon SOAP service and return session + cluster."""
        ...

    async def keep_alive(self, cluster_url: str, session_id: str) -> bool:
        """Send keep-alive SOAP ping to extend session sliding expiration."""
        ...


class MockTwinfieldAuthenticator:
    """Default high-fidelity mock authenticator for testing and isolated evaluation."""

    def __init__(self, default_cluster: str = "https://c1.twinfield.com") -> None:
        self.default_cluster = default_cluster
        self.call_count = 0

    async def logon(self, office_code: str, user_code: str, password: str) -> TwinfieldSession:
        self.call_count += 1
        now = time.time()
        # Twinfield default session expiration is 20 minutes (1200 seconds)
        return TwinfieldSession(
            office_code=office_code,
            user_code=user_code,
            session_id=f"TWIN-SESS-{office_code}-{int(now)}-{self.call_count}",
            cluster_url=self.default_cluster,
            created_at=now,
            expires_at=now + 1200,
            last_activity=now,
        )

    async def keep_alive(self, cluster_url: str, session_id: str) -> bool:
        return True


class TwinfieldSessionPooler:
    """
    Manages and pools stateful Twinfield sessions in Redis.
    
    Key Features:
    - Eliminates login-thundering-herd with distributed lock per (office, user).
    - Stores cluster endpoint for routing without repeated discovery.
    - Sliding TTL update on lease.
    - Background keep-alive heartbeat support.
    """

    def __init__(
        self,
        redis: Redis,
        authenticator: TwinfieldAuthenticator | None = None,
        session_ttl_seconds: int = 1200,
    ) -> None:
        self.redis = redis
        self.authenticator = authenticator or MockTwinfieldAuthenticator()
        self.session_ttl_seconds = session_ttl_seconds

    def _session_key(self, office_code: str, user_code: str) -> str:
        return f"ferrox:twinfield:session:{office_code}:{user_code}"

    def _lock_key(self, office_code: str, user_code: str) -> str:
        return f"ferrox:twinfield:lock:{office_code}:{user_code}"

    async def get_valid_session(
        self,
        office_code: str,
        user_code: str,
        password: str,
        force_refresh: bool = False,
    ) -> TwinfieldSession:
        """
        Retrieves an active cached session from Redis or performs a safe locked login.
        """
        key = self._session_key(office_code, user_code)

        if not force_refresh:
            cached = await self.redis.get(key)
            if cached:
                session_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
                session = TwinfieldSession.from_json(session_str)
                if session.is_valid:
                    # Slide the TTL in Redis
                    await self.touch_session(session)
                    return session

        # Session invalid or missing: Acquire distributed lock to authenticate once
        lock_key = self._lock_key(office_code, user_code)
        # Try to acquire lock with 10s TTL
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=10)
        
        if not acquired:
            # Another worker is authenticating; wait and re-check cache
            for _ in range(20):
                await asyncio.sleep(0.2)
                cached = await self.redis.get(key)
                if cached:
                    session_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
                    session = TwinfieldSession.from_json(session_str)
                    if session.is_valid:
                        return session
            # Fallthrough if lock was abandoned

        try:
            logger.info("authenticating_twinfield_session", office=office_code, user=user_code)
            session = await self.authenticator.logon(office_code, user_code, password)
            await self._save_session(session)
            return session
        finally:
            await self.redis.delete(lock_key)

    async def _save_session(self, session: TwinfieldSession) -> None:
        key = self._session_key(session.office_code, session.user_code)
        remaining_ttl = max(60, int(session.expires_at - time.time()))
        await self.redis.set(key, session.to_json(), ex=remaining_ttl)

    async def touch_session(self, session: TwinfieldSession) -> None:
        """Refreshes last activity and extends Redis TTL."""
        session.last_activity = time.time()
        key = self._session_key(session.office_code, session.user_code)
        remaining_ttl = max(60, int(session.expires_at - time.time()))
        await self.redis.set(key, session.to_json(), ex=remaining_ttl)

    async def invalidate_session(self, office_code: str, user_code: str) -> None:
        """Immediately purges an invalidated or rejected session."""
        key = self._session_key(office_code, user_code)
        await self.redis.delete(key)
        logger.warn("twinfield_session_invalidated", office=office_code, user=user_code)

    async def run_keep_alive(self, session: TwinfieldSession) -> bool:
        """Performs a remote keep-alive heartbeat to prevent server-side timeout."""
        success = await self.authenticator.keep_alive(session.cluster_url, session.session_id)
        if success:
            session.expires_at = time.time() + self.session_ttl_seconds
            await self._save_session(session)
            return True
        await self.invalidate_session(session.office_code, session.user_code)
        return False
