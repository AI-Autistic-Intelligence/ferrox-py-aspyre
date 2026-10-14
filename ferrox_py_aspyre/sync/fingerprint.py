"""
Semantic Content Fingerprinting Engine.
Prevents Ping-Pong Sync loops by hashing canonical business data and ignoring system timestamps.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
import structlog

logger = structlog.get_logger(__name__)

# Keys that frequently mutate without semantic business meaning
VOLATILE_FIELDS = {
    "updated_at",
    "updatedat",
    "modified_at",
    "modifiedat",
    "created_at",
    "createdat",
    "timestamp",
    "version",
    "_version",
    "etag",
    "_etag",
    "sync_run_id",
    "system_hash",
}


def _is_volatile(k: str) -> bool:
    kl = k.lower()
    if kl in VOLATILE_FIELDS:
        return True
    if kl.endswith("_at") or kl.endswith("_timestamp") or kl.endswith("timestamp"):
        return True
    if "etag" in kl or "version" in kl or "sync_run" in kl:
        return True
    return False


def _normalize_object(obj: Any) -> Any:
    """Recursively cleans volatile fields and standardizes decimals/numbers."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if _is_volatile(k):
                continue
            cleaned[k] = _normalize_object(v)
        return cleaned
    elif isinstance(obj, list):
        return [_normalize_object(item) for item in obj]
    elif isinstance(obj, Decimal):
        # Format decimal to 2 decimal places string
        return f"{obj:.2f}"
    elif isinstance(obj, float):
        return f"{obj:.2f}"
    return obj


class SemanticFingerprinter:
    """
    Computes deterministic SHA256 hashes of accounting payloads.
    Stores and verifies outgoing hashes in Redis to suppress inbound webhook echoes.
    """

    def __init__(self, redis: Redis, default_ttl_seconds: int = 604800) -> None:  # 7 days TTL
        self.redis = redis
        self.default_ttl_seconds = default_ttl_seconds

    @staticmethod
    def compute_hash(payload: dict[str, Any]) -> str:
        normalized = _normalize_object(payload)
        canonical_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _redis_key(self, entity_type: str, entity_id: str) -> str:
        return f"ferrox:sync:fingerprint:{entity_type}:{entity_id}"

    async def record_outbound_mutation(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        actor: str = "ferrox_aspyre_sync",
    ) -> str:
        """
        Records the semantic hash of a payload sent to an external system.
        """
        digest = self.compute_hash(payload)
        key = self._redis_key(entity_type, entity_id)

        data = {
            "digest": digest,
            "actor": actor,
        }
        await self.redis.set(key, json.dumps(data), ex=self.default_ttl_seconds)
        logger.debug("outbound_fingerprint_saved", entity=entity_type, id=entity_id, hash=digest[:8])
        return digest

    async def should_suppress_inbound_echo(
        self,
        entity_type: str,
        entity_id: str,
        inbound_payload: dict[str, Any],
    ) -> bool:
        """
        Checks whether an incoming webhook/event is simply an echo of our own previous mutation.
        Returns True if payload hash matches, indicating a ping-pong echo that must be suppressed.
        """
        key = self._redis_key(entity_type, entity_id)
        cached = await self.redis.get(key)
        if not cached:
            return False

        cached_str = cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        data = json.loads(cached_str)
        last_outbound_digest = data.get("digest")

        inbound_digest = self.compute_hash(inbound_payload)
        if inbound_digest == last_outbound_digest:
            logger.info(
                "ping_pong_echo_suppressed",
                entity=entity_type,
                id=entity_id,
                hash=inbound_digest[:8],
            )
            return True

        return False
