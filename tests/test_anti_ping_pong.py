from decimal import Decimal
from typing import Any
import pytest
from ferrox_py_aspyre.sync.fingerprint import SemanticFingerprinter
from ferrox_py_aspyre.sync.saga_steps import create_storno_reversal
from ferrox_py_aspyre.sync.orchestrator import (
    BidirectionalSyncOrchestrator,
    ExternalAccountingGateway,
    AspyreLedgerRepository,
)
from ferrox_py_aspyre.dutch_sbr.models import JournalEntry, JournalEntryLine
from ferrox_py_aspyre.dutch_sbr.vraagposten import VraagpostItem, VraagpostReason


@pytest.mark.asyncio
async def test_semantic_fingerprinter_echo_suppression(fake_redis):
    fingerprinter = SemanticFingerprinter(fake_redis)

    outbound_payload = {
        "entry_id": "INV-100",
        "total": Decimal("150.00"),
        "customer": "Tech BV",
        "updated_at": "2026-04-01T10:00:00Z",
        "sync_run_id": "run-abc-123",
    }

    # Record outbound mutation
    digest = await fingerprinter.record_outbound_mutation("JournalEntry", "INV-100", outbound_payload)
    assert digest is not None

    # Inbound event arrived from webhook with modified system timestamps and etag
    inbound_echo = {
        "entry_id": "INV-100",
        "total": Decimal("150.00"),
        "customer": "Tech BV",
        "updated_at": "2026-04-01T10:00:02Z",  # 2 seconds later
        "etag": "W/12345678",                  # external etag added
        "sync_run_id": "run-different",
    }

    # Must be suppressed as self-echo!
    is_echo = await fingerprinter.should_suppress_inbound_echo("JournalEntry", "INV-100", inbound_echo)
    assert is_echo is True

    # Real human modification in ERP (amount changed)
    real_update = {
        "entry_id": "INV-100",
        "total": Decimal("180.00"),            # Modified!
        "customer": "Tech BV",
        "updated_at": "2026-04-01T10:05:00Z",
    }
    is_real_echo = await fingerprinter.should_suppress_inbound_echo("JournalEntry", "INV-100", real_update)
    assert is_real_echo is False


def test_storno_reversal_structure():
    entry = JournalEntry(
        entry_id="ENTRY-10",
        division_id="DIV-1",
        journal_code="VRK",
        entry_number="100",
        entry_date="2026-04-01",
        period=4,
        fiscal_year=2026,
        lines=[
            JournalEntryLine(line_number=1, account_code="1300", debit=Decimal("100.00"), credit=Decimal("0.00")),
            JournalEntryLine(line_number=2, account_code="8000", debit=Decimal("0.00"), credit=Decimal("100.00")),
        ],
    )

    storno = create_storno_reversal(entry)
    assert storno.entry_number == "REV-100"
    # Line 1 debit 100 becomes credit 100
    assert storno.lines[0].debit == Decimal("0.00")
    assert storno.lines[0].credit == Decimal("100.00")
    # Line 2 credit 100 becomes debit 100
    assert storno.lines[1].debit == Decimal("100.00")
    assert storno.lines[1].credit == Decimal("0.00")
    assert "[STORNO]" in storno.lines[0].description


class MockGateway:
    def __init__(self, remote_version: str = "v1") -> None:
        self.remote_version = remote_version
        self.posted_entries: list[JournalEntry] = []

    async def post_journal_entry(self, entry: JournalEntry) -> dict[str, Any]:
        self.posted_entries.append(entry)
        return {"status": "ok", "remote_id": f"REM-{entry.entry_id}"}

    async def get_remote_version(self, division_id: str, entry_id: str) -> str | None:
        return self.remote_version


class MockAspyreRepo:
    def __init__(self) -> None:
        self.entries: dict[str, JournalEntry] = {}
        self.vraagposten: list[VraagpostItem] = []

    async def save_journal_entry(self, entry: JournalEntry) -> None:
        self.entries[entry.entry_id] = entry

    async def get_journal_entry(self, entry_id: str) -> JournalEntry | None:
        return self.entries.get(entry_id)

    async def create_vraagpost(self, item: VraagpostItem) -> None:
        self.vraagposten.append(item)


@pytest.mark.asyncio
async def test_sync_conflict_escalates_to_vraagposten(fake_redis):
    # Remote version is v2, but Aspyre expected v1
    gw = MockGateway(remote_version="v2")
    repo = MockAspyreRepo()
    orchestrator = BidirectionalSyncOrchestrator(fake_redis, gw, repo)

    entry = JournalEntry(
        entry_id="ENTRY-CONFLICT",
        division_id="DIV-1",
        journal_code="VRK",
        entry_number="101",
        entry_date="2026-04-01",
        period=4,
        fiscal_year=2026,
        lines=[
            JournalEntryLine(line_number=1, account_code="1300", debit=Decimal("200.00"), credit=Decimal("0.00")),
            JournalEntryLine(line_number=2, account_code="8000", debit=Decimal("0.00"), credit=Decimal("200.00")),
        ],
    )

    # Sync outbound with expected version 'v1'
    result = await orchestrator.sync_outbound_to_external(entry, expected_remote_version="v1")

    # Must be escalated instead of overwriting remote data!
    assert result["status"] == "ESCALATED_TO_VRAAGPOSTEN"
    assert len(repo.vraagposten) == 1
    vp = repo.vraagposten[0]
    assert vp.reason == VraagpostReason.CONCURRENT_MODIFICATION_CONFLICT
    assert vp.suspense_account_code == "9999"
    # External gateway should NOT have been touched with the conflicting write
    assert len(gw.posted_entries) == 0
