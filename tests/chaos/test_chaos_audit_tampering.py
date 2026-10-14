import pytest
from ferrox_py_aspyre.audit.ledger_chain import (
    AuditHashChain,
    AuditBlock,
    AuditTamperingException,
)
from ferrox_py_aspyre.audit.xaf_exporter import XafAuditExporter


@pytest.mark.asyncio
async def test_chaos_audit_tampering_detection(fake_redis):
    """
    CHAOS TEST: Verifies that any malicious or corrupted mutation in past audit blocks
    is mathematically detected and rejected by the hash-chain verification engine.
    """
    division_id = "DIV-AUDIT-CHAOS"
    chain = AuditHashChain(fake_redis, division_id)

    # 1. Build a valid chain of 10 financial events
    for i in range(10):
        await chain.record_entry(
            entity_type="JournalEntry",
            entity_id=f"JE-2026-{1000 + i}",
            action="COMMITTED",
            actor="AI_RECONCILIATION_ENGINE",
            canonical_payload={"amount": 100 + i, "account": "1300", "vat": "21%"},
        )

    blocks = await chain.get_all_blocks()
    assert len(blocks) == 10

    # 2. Verify untampered chain
    assert AuditHashChain.verify_chain_integrity(blocks) is True

    # 3. CHAOS INJECTION A: Attacker silently tampers with payload_digest of block #4
    tampered_blocks_a = [AuditBlock(**b.__dict__) for b in blocks]
    tampered_blocks_a[4].payload_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with pytest.raises(AuditTamperingException) as exc_info:
        AuditHashChain.verify_chain_integrity(tampered_blocks_a)
    assert exc_info.value.block_index == 4
    assert "Block hash signature invalid" in str(exc_info.value)

    # 4. CHAOS INJECTION B: Attacker deletes block #2
    tampered_blocks_b = blocks[:2] + blocks[3:]
    with pytest.raises(AuditTamperingException) as exc_info_b:
        AuditHashChain.verify_chain_integrity(tampered_blocks_b)
    assert exc_info_b.value.block_index == 3
    assert "Index sequence broken" in str(exc_info_b.value)

    # 5. CHAOS INJECTION C: Attacker alters block #6's timestamp
    tampered_blocks_c = [AuditBlock(**b.__dict__) for b in blocks]
    tampered_blocks_c[6].timestamp = "2020-01-01T00:00:00Z"
    with pytest.raises(AuditTamperingException) as exc_info_c:
        AuditHashChain.verify_chain_integrity(tampered_blocks_c)
    assert exc_info_c.value.block_index == 6


def test_xaf_audit_exporter_produces_compliant_structure():
    """
    Verifies that XafAuditExporter produces valid XML containing both transactions
    and the Belastingdienst-ready cryptographic audit trail.
    """
    blocks = [
        AuditBlock(
            block_index=0,
            prev_hash="0" * 64,
            timestamp="2026-04-01T10:00:00Z",
            division_id="DIV-XAF-01",
            entity_type="JournalEntry",
            entity_id="JE-001",
            action="COMMITTED",
            actor="AI_AGENT",
            payload_digest="abc123hash",
            block_hash="blockhash001",
        )
    ]

    xml_output = XafAuditExporter.export_xaf_xml(
        division_id="DIV-XAF-01",
        company_name="Aspyre Tech BV",
        kvk_number="68750110",
        fiscal_year=2026,
        entries=[],
        audit_blocks=blocks,
    )

    assert "xmlns=\"http://www.auditfiles.nl/XAF/3.2\"" in xml_output
    assert "<companyName>Aspyre Tech BV</companyName>" in xml_output
    assert "<kvkNumber>68750110</kvkNumber>" in xml_output
    assert "<cryptographicAuditTrail>" in xml_output
    assert "<blockHash>blockhash001</blockHash>" in xml_output
