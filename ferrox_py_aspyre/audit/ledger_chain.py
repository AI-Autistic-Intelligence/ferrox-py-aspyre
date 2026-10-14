"""
Tamper-Proof Cryptographic Financial Audit Ledger (Hash-Chained SHA-256).
Enforces Belastingdienst Non-Alterability (Onveranderbaarheid) and Dutch Legal Audit Requirements.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from redis.asyncio import Redis
import structlog

logger = structlog.get_logger(__name__)

GENESIS_PREV_HASH = "0" * 64


class AuditTamperingException(Exception):
    """Raised when an audit chain verification discovers modified or corrupted blocks."""
    def __init__(self, block_index: int, message: str) -> None:
        super().__init__(f"Audit tampering detected at block #{block_index}: {message}")
        self.block_index = block_index


@dataclass
class AuditBlock:
    block_index: int
    prev_hash: str
    timestamp: str
    division_id: str
    entity_type: str
    entity_id: str
    action: str
    actor: str
    payload_digest: str
    block_hash: str

    @classmethod
    def calculate_hash(
        cls,
        block_index: int,
        prev_hash: str,
        timestamp: str,
        division_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        payload_digest: str,
    ) -> str:
        """Computes deterministic SHA-256 over all fields in standard canonical format."""
        raw = f"{block_index}|{prev_hash}|{timestamp}|{division_id}|{entity_type}|{entity_id}|{action}|{actor}|{payload_digest}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> AuditBlock:
        return cls(**json.loads(data))


class AuditHashChain:
    """
    Append-only tamper-proof ledger with cryptographic backward hash-chaining.
    Every financial state transition passing through ferrox-py is permanently recorded.
    """

    def __init__(self, redis: Redis, division_id: str) -> None:
        self.redis = redis
        self.division_id = division_id
        self._chain_key = f"ferrox:audit:chain:{division_id}"

    async def get_latest_block(self) -> AuditBlock | None:
        """Fetches the latest (head) block in the division's audit chain."""
        raw = await self.redis.rpop(self._chain_key)
        if not raw:
            return None
        # Put it back since rpop consumed it
        await self.redis.rpush(self._chain_key, raw)
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return AuditBlock.from_json(raw_str)

    async def get_all_blocks(self) -> list[AuditBlock]:
        """Retrieves the entire chain from Genesis to Head."""
        raw_list = await self.redis.lrange(self._chain_key, 0, -1)
        blocks: list[AuditBlock] = []
        for raw in raw_list:
            raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            blocks.append(AuditBlock.from_json(raw_str))
        return blocks

    async def record_entry(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        canonical_payload: dict[str, Any] | str,
    ) -> AuditBlock:
        """
        Appends an immutable block to the chain.
        Calculates SHA-256 payload digest and chains it to the previous block's hash.
        """
        # Calculate payload digest
        if isinstance(canonical_payload, dict):
            raw_payload = json.dumps(canonical_payload, default=str, sort_keys=True, separators=(",", ":"))
        else:
            raw_payload = str(canonical_payload)
        payload_digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        # Fetch last block to compute link
        raw_tail = await self.redis.lindex(self._chain_key, -1)
        if raw_tail:
            raw_str = raw_tail.decode("utf-8") if isinstance(raw_tail, bytes) else str(raw_tail)
            prev_block = AuditBlock.from_json(raw_str)
            block_index = prev_block.block_index + 1
            prev_hash = prev_block.block_hash
        else:
            # Genesis Block
            block_index = 0
            prev_hash = GENESIS_PREV_HASH

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        block_hash = AuditBlock.calculate_hash(
            block_index=block_index,
            prev_hash=prev_hash,
            timestamp=now_iso,
            division_id=self.division_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            payload_digest=payload_digest,
        )

        block = AuditBlock(
            block_index=block_index,
            prev_hash=prev_hash,
            timestamp=now_iso,
            division_id=self.division_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            payload_digest=payload_digest,
            block_hash=block_hash,
        )

        await self.redis.rpush(self._chain_key, block.to_json())
        logger.debug("audit_block_sealed", index=block_index, hash=block_hash[:8], action=action)
        return block

    @classmethod
    def verify_chain_integrity(cls, blocks: list[AuditBlock]) -> bool:
        """
        Mathematically verifies the integrity of the audit chain from Genesis to Head.
        Raises AuditTamperingException if any block was altered, removed, or inserted.
        """
        if not blocks:
            return True

        expected_prev_hash = GENESIS_PREV_HASH

        for idx, block in enumerate(blocks):
            if block.block_index != idx:
                raise AuditTamperingException(
                    block.block_index,
                    f"Index sequence broken: expected #{idx}, got #{block.block_index}",
                )

            if block.prev_hash != expected_prev_hash:
                raise AuditTamperingException(
                    block.block_index,
                    f"Prev_hash mismatch: expected {expected_prev_hash[:8]}..., got {block.prev_hash[:8]}...",
                )

            # Recompute block hash
            recomputed = AuditBlock.calculate_hash(
                block_index=block.block_index,
                prev_hash=block.prev_hash,
                timestamp=block.timestamp,
                division_id=block.division_id,
                entity_type=block.entity_type,
                entity_id=block.entity_id,
                action=block.action,
                actor=block.actor,
                payload_digest=block.payload_digest,
            )

            if block.block_hash != recomputed:
                raise AuditTamperingException(
                    block.block_index,
                    f"Block hash signature invalid: stored {block.block_hash[:8]}..., calculated {recomputed[:8]}...",
                )

            expected_prev_hash = block.block_hash

        return True
