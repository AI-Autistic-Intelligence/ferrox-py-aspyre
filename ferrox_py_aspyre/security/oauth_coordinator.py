"""
Zero-Race OAuth2 Token Lifecycle Coordinator for Exact Online Single-Use Refresh Tokens.
Prevents refresh-token burns and auth chain destruction under high concurrency.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from redis.asyncio import Redis
import structlog

from .crypto_vault import CryptoVault

logger = structlog.get_logger(__name__)

# Lua script for atomic lease acquire with TTL
OAUTH_LOCK_ACQUIRE = """
local key = KEYS[1]
local owner = ARGV[1]
local ttl_ms = tonumber(ARGV[2])

if redis.call("SET", key, owner, "PX", ttl_ms, "NX") then
    return 1
end
return 0
"""

# Lua script for safe release
OAUTH_LOCK_RELEASE = """
local key = KEYS[1]
local owner = ARGV[1]

if redis.call("GET", key) == owner then
    return redis.call("DEL", key)
end
return 0
"""


@dataclass
class OAuthTokenPair:
    division_id: str
    access_token: str
    refresh_token: str
    expires_at: float
    generation_id: int
    updated_at: float

    @property
    def is_expired(self) -> bool:
        # Buffer of 60 seconds before actual expiry
        return time.time() >= (self.expires_at - 60)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthTokenPair:
        return cls(**data)


class OAuthRefreshClient(Protocol):
    async def refresh_tokens(self, division_id: str, current_refresh_token: str) -> tuple[str, str, int]:
        """
        Calls Exact Online OAuth2 token endpoint with grant_type=refresh_token.
        Returns (new_access_token, new_refresh_token, expires_in_seconds).
        """
        ...


class MockExactOAuthClient:
    """Mock OAuth client for tests and evaluation."""
    def __init__(self) -> None:
        self.refresh_call_count = 0
        self.used_refresh_tokens: set[str] = set()

    async def refresh_tokens(self, division_id: str, current_refresh_token: str) -> tuple[str, str, int]:
        self.refresh_call_count += 1
        # Exact Online single-use validation: if a refresh token is used twice, fail fatally!
        if current_refresh_token in self.used_refresh_tokens:
            raise ValueError(
                f"FATAL: Exact Online single-use refresh token {current_refresh_token} was reused! "
                "OAuth authorization chain has been destroyed (HTTP 400 invalid_grant)."
            )
        self.used_refresh_tokens.add(current_refresh_token)

        new_access = f"EXACT-ACCESS-{uuid.uuid4()}"
        new_refresh = f"EXACT-REFRESH-{uuid.uuid4()}"
        expires_in = 600  # 10 minutes
        return new_access, new_refresh, expires_in


class ExactOAuthTokenCoordinator:
    """
    Coordinates access token retrieval and single-use refresh token rotation across all workers.
    Ensures that concurrent workers never attempt simultaneous refreshes of the single-use token.
    """

    def __init__(
        self,
        redis: Redis,
        crypto_vault: CryptoVault | None = None,
        oauth_client: OAuthRefreshClient | None = None,
    ) -> None:
        self.redis = redis
        self.vault = crypto_vault or CryptoVault()
        self.client = oauth_client or MockExactOAuthClient()
        self._acquire_script = self.redis.register_script(OAUTH_LOCK_ACQUIRE)
        self._release_script = self.redis.register_script(OAUTH_LOCK_RELEASE)

    def _token_key(self, division_id: str) -> str:
        return f"ferrox:exact:oauth:tokens:{division_id}"

    def _lock_key(self, division_id: str) -> str:
        return f"ferrox:exact:oauth:lock:{division_id}"

    async def initialize_tokens(
        self,
        division_id: str,
        access_token: str,
        refresh_token: str,
        expires_in_seconds: int = 600,
    ) -> OAuthTokenPair:
        """Saves initial encrypted OAuth credentials for a division."""
        now = time.time()
        token = OAuthTokenPair(
            division_id=division_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=now + expires_in_seconds,
            generation_id=1,
            updated_at=now,
        )
        await self._save_tokens(token)
        return token

    async def _save_tokens(self, token: OAuthTokenPair) -> None:
        key = self._token_key(token.division_id)
        # Encrypt with division_id as associated authenticated data (AEAD)
        encrypted_blob = self.vault.encrypt_json(token.to_dict(), associated_data=token.division_id)
        # 30 days retention in Redis
        await self.redis.set(key, encrypted_blob, ex=2592000)

    async def _load_tokens(self, division_id: str) -> OAuthTokenPair | None:
        key = self._token_key(division_id)
        blob = await self.redis.get(key)
        if not blob:
            return None
        blob_str = blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)
        data = self.vault.decrypt_json(blob_str, associated_data=division_id)
        return OAuthTokenPair.from_dict(data)

    async def get_valid_access_token(self, division_id: str) -> str:
        """
        Retrieves a valid unexpired access token for the given division.
        If expired, coordinates a single-flight atomic refresh with zero race conditions.
        """
        token = await self._load_tokens(division_id)
        if not token:
            raise RuntimeError(f"Division {division_id} has no registered OAuth2 credentials in vault.")

        if not token.is_expired:
            return token.access_token

        # Token is expired or about to expire: coordinate single refresh
        lock_key = self._lock_key(division_id)
        owner_id = str(uuid.uuid4())
        initial_gen = token.generation_id

        # Try to acquire lock
        acquired = await self._acquire_script(keys=[lock_key], args=[owner_id, "15000"])

        if acquired == 1:
            # We are the designated refresher
            try:
                # Double check if someone refreshed just before we took the lock
                fresh = await self._load_tokens(division_id)
                if fresh and not fresh.is_expired:
                    return fresh.access_token

                logger.info("rotating_exact_single_use_refresh_token", division=division_id, current_gen=initial_gen)
                
                # Perform the network call to Exact Online
                current_refresh = fresh.refresh_token if fresh else token.refresh_token
                new_acc, new_ref, expires_in = await self.client.refresh_tokens(division_id, current_refresh)

                now = time.time()
                updated_token = OAuthTokenPair(
                    division_id=division_id,
                    access_token=new_acc,
                    refresh_token=new_ref,
                    expires_at=now + expires_in,
                    generation_id=initial_gen + 1,
                    updated_at=now,
                )
                await self._save_tokens(updated_token)
                logger.info("exact_token_rotated_success", division=division_id, new_gen=updated_token.generation_id)
                return updated_token.access_token
            finally:
                await self._release_script(keys=[lock_key], args=[owner_id])

        else:
            # Another worker is refreshing: wait and poll for the generation_id to advance
            logger.debug("awaiting_concurrent_token_refresh", division=division_id)
            for _ in range(30):  # Wait up to 6 seconds (30 * 200ms)
                await asyncio.sleep(0.2)
                fresh = await self._load_tokens(division_id)
                if fresh and (fresh.generation_id > initial_gen or not fresh.is_expired):
                    return fresh.access_token

            raise TimeoutError(f"Timed out waiting for concurrent OAuth refresh on division {division_id}")
