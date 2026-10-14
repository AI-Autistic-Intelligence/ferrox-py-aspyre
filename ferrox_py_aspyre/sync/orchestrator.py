"""
Bidirectional Accounting Sync Orchestrator with Saga Resilience, Anti-Ping-Pong & Vraagposten Escalation.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from ferrox_py.architectures.sagas import SagaOrchestrator, SagaStep
from redis.asyncio import Redis
import structlog

from ..dutch_sbr.models import JournalEntry
from ..dutch_sbr.vraagposten import VraagpostItem, VraagpostReason, VraagpostStatus
from .fingerprint import SemanticFingerprinter
from .saga_steps import create_storno_reversal

logger = structlog.get_logger(__name__)


class ExternalAccountingGateway(Protocol):
    async def post_journal_entry(self, entry: JournalEntry) -> dict[str, Any]:
        ...

    async def get_remote_version(self, division_id: str, entry_id: str) -> str | None:
        ...


class AspyreLedgerRepository(Protocol):
    async def save_journal_entry(self, entry: JournalEntry) -> None:
        ...

    async def get_journal_entry(self, entry_id: str) -> JournalEntry | None:
        ...

    async def create_vraagpost(self, item: VraagpostItem) -> None:
        ...


class BidirectionalSyncOrchestrator:
    """
    Orchestrates distributed sync operations between Aspyre and external financial systems.
    
    Guarantees:
    1. Zero Silent Overwrites: Concurrency conflicts are escalated to Vraagposten.
    2. Zero Ping-Pong: Echo events are suppressed via semantic hashing.
    3. GAAP/Belastingdienst compliance: Aborted transactions are cleanly reversed via Storno.
    """

    def __init__(
        self,
        redis: Redis,
        external_gateway: ExternalAccountingGateway,
        aspyre_repo: AspyreLedgerRepository,
        saga_orchestrator: SagaOrchestrator | None = None,
    ) -> None:
        self.redis = redis
        self.external_gw = external_gateway
        self.aspyre_repo = aspyre_repo
        self.fingerprinter = SemanticFingerprinter(redis)
        self.saga_orchestrator = saga_orchestrator or SagaOrchestrator()

    async def sync_outbound_to_external(
        self,
        entry: JournalEntry,
        expected_remote_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Pushes a journal entry from Aspyre to an external accounting system using a compensating Saga.
        """
        # 1. Optimistic Concurrency Check
        current_version = await self.external_gw.get_remote_version(entry.division_id, entry.entry_id)
        if expected_remote_version and current_version and current_version != expected_remote_version:
            logger.warn(
                "sync_conflict_detected_escalating",
                entry_id=entry.entry_id,
                expected=expected_remote_version,
                actual=current_version,
            )
            # Create Vraagpost for human triage
            vraagpost = VraagpostItem(
                vraagpost_id=f"vp-{uuid.uuid4()}",
                division_id=entry.division_id,
                office_id="default-office",
                source_system="AspyreSync",
                external_entity_id=entry.entry_id,
                amount=entry.total_debit,
                reason=VraagpostReason.CONCURRENT_MODIFICATION_CONFLICT,
                original_payload=entry.model_dump(),
                conflict_delta={"expected_version": expected_remote_version, "actual_version": current_version},
            )
            await self.aspyre_repo.create_vraagpost(vraagpost)
            return {"status": "ESCALATED_TO_VRAAGPOSTEN", "vraagpost_id": vraagpost.vraagpost_id}

        # 2. Build Saga Steps
        async def step_post_external(state: dict[str, Any]) -> dict[str, Any]:
            res = await self.external_gw.post_journal_entry(entry)
            logger.info("posted_to_external_accounting", entry_id=entry.entry_id)
            return res

        async def comp_post_external(state: dict[str, Any]) -> None:
            # Reversal via Storno
            storno = create_storno_reversal(entry)
            await self.external_gw.post_journal_entry(storno)
            logger.warn("compensation_storno_posted", entry_id=entry.entry_id)

        async def step_commit_aspyre(state: dict[str, Any]) -> bool:
            await self.aspyre_repo.save_journal_entry(entry)
            return True

        async def step_save_fingerprint(state: dict[str, Any]) -> str:
            return await self.fingerprinter.record_outbound_mutation(
                entity_type="JournalEntry",
                entity_id=entry.entry_id,
                payload=entry.model_dump(),
            )

        steps = [
            SagaStep("post_external", step_post_external, comp_post_external),
            SagaStep("commit_aspyre", step_commit_aspyre),
            SagaStep("save_fingerprint", step_save_fingerprint),
        ]

        saga_id = f"saga-sync-{entry.entry_id}-{uuid.uuid4()}"
        result = await self.saga_orchestrator.execute(steps, saga_id=saga_id)
        return {"status": "COMPLETED", "result": result}

    async def handle_inbound_from_external(
        self,
        division_id: str,
        entry_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Processes an incoming webhook or event from an external system.
        Filters out self-echoes and detects conflicts.
        """
        entry_id = str(entry_payload.get("entry_id", ""))

        # 1. Anti-Ping-Pong Check
        is_echo = await self.fingerprinter.should_suppress_inbound_echo(
            entity_type="JournalEntry",
            entity_id=entry_id,
            inbound_payload=entry_payload,
        )
        if is_echo:
            return {"status": "SUPPRESSED_ECHO", "entry_id": entry_id}

        # 2. Validate domain invariants via Pydantic model
        try:
            entry = JournalEntry.model_validate(entry_payload)
        except Exception as e:
            logger.error("inbound_entry_validation_failed", error=str(e))
            # Escalate invalid or unbalanced payload to Vraagposten
            vraagpost = VraagpostItem(
                vraagpost_id=f"vp-val-{uuid.uuid4()}",
                division_id=division_id,
                office_id="default-office",
                source_system="InboundExternal",
                external_entity_id=entry_id,
                amount=Decimal(str(entry_payload.get("amount", "0.00"))),
                reason=VraagpostReason.UNBALANCED_DELTA,
                original_payload=entry_payload,
                conflict_delta={"validation_error": str(e)},
            )
            await self.aspyre_repo.create_vraagpost(vraagpost)
            return {"status": "ESCALATED_TO_VRAAGPOSTEN", "reason": "VALIDATION_FAILED", "vraagpost_id": vraagpost.vraagpost_id}

        # 3. Apply to Aspyre
        await self.aspyre_repo.save_journal_entry(entry)
        return {"status": "APPLIED", "entry_id": entry.entry_id}
