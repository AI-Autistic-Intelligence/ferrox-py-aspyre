from decimal import Decimal
import pytest
from ferrox_py_aspyre.ai_bridge.pii_scrubber import GdprPiiScrubber
from ferrox_py_aspyre.ai_bridge.prompt_context import AccountingPromptContextBuilder
from ferrox_py_aspyre.dutch_sbr.models import JournalEntry, JournalEntryLine
from ferrox_py_aspyre.dutch_sbr.vat_matrix import DutchVatCategory
from ferrox_py_aspyre.dutch_sbr.vraagposten import VraagpostItem, VraagpostReason


def test_pii_scrubber_masks_iban_and_kvk():
    scrubber = GdprPiiScrubber(hmac_secret="TEST_SALT_123")

    raw_iban = "NL91ABNA0417164300"
    masked_iban = scrubber.mask_dutch_iban(raw_iban)
    assert masked_iban == "NL**ABNA******4300"
    assert "041716" not in masked_iban

    raw_kvk = "68750110"
    masked_kvk = scrubber.mask_kvk(raw_kvk)
    assert masked_kvk.startswith("KVK_")
    assert raw_kvk not in masked_kvk


def test_pii_scrubber_redacts_text_with_bsn_email_phone():
    scrubber = GdprPiiScrubber()
    text = (
        "Factuur betaling van Jan Jansen (BSN: 123456789), email: jan.jansen@example.nl, "
        "tel: 0612345678 naar IBAN NL91ABNA0417164300 inzake advies."
    )
    scrubbed = scrubber.scrub_text(text)

    assert "123456789" not in scrubbed
    assert "[BSN_REDACTED]" in scrubbed
    assert "jan.jansen@example.nl" not in scrubbed
    assert "[EMAIL_REDACTED]" in scrubbed
    assert "0612345678" not in scrubbed
    assert "[PHONE_REDACTED]" in scrubbed
    assert "NL**ABNA******4300" in scrubbed


def test_pii_scrubber_full_journal_entry():
    scrubber = GdprPiiScrubber()
    entry = JournalEntry(
        entry_id="ENTRY-GDPR-01",
        division_id="DIV-12345",
        journal_code="INK",
        entry_number="20260099",
        entry_date="2026-04-01",
        period=4,
        fiscal_year=2026,
        counterparty_name="Pieter Post Timmerwerken",
        counterparty_kvk="68750110",
        counterparty_iban="NL91ABNA0417164300",
        lines=[
            JournalEntryLine(
                line_number=1,
                account_code="4500",
                rgs_code="WKosAlg",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
                vat_category=DutchVatCategory.HIGH_21,
                vat_amount=Decimal("21.00"),
                description="Houtwerk geleverd aan dhr. Jansen te Utrecht",
            ),
            JournalEntryLine(
                line_number=2,
                account_code="1510",
                rgs_code="BPassBtw",
                debit=Decimal("21.00"),
                credit=Decimal("0.00"),
                description="Voorbelasting 21%",
            ),
            JournalEntryLine(
                line_number=3,
                account_code="1600",
                rgs_code="BPassCre",
                debit=Decimal("0.00"),
                credit=Decimal("121.00"),
                description="Crediteuren Pieter Post",
            ),
        ],
    )

    scrubbed = scrubber.scrub_journal_entry(entry)

    assert scrubbed["gdpr_scrubbed"] is True
    assert scrubbed["counterparty_pseudonym"].startswith("VENDOR_")
    assert "Pieter Post" not in scrubbed["counterparty_pseudonym"]
    assert scrubbed["counterparty_iban_masked"] == "NL**ABNA******4300"
    assert scrubbed["counterparty_kvk_masked"].startswith("KVK_")
    # Accounting integrity preserved
    assert scrubbed["total_debit"] == "121.00"
    assert scrubbed["total_credit"] == "121.00"


def test_prompt_context_builder():
    builder = AccountingPromptContextBuilder()
    vp = VraagpostItem(
        vraagpost_id="VP-001",
        division_id="DIV-99",
        office_id="OFFICE-1",
        source_system="ExactOnline",
        external_entity_id="EXT-100",
        amount=Decimal("250.00"),
        reason=VraagpostReason.AMBIGUOUS_VAT_TREATMENT,
        original_payload={"description": "Betaling tankstation Shell NL91ABNA0417164300"},
    )

    context = builder.build_vraagpost_triage_prompt(vp)
    assert "user_prompt" in context
    assert "system_instruction" in context
    assert "response_schema" in context
    assert "NL**ABNA******4300" in context["user_prompt"]
    assert "NL91ABNA0417164300" not in context["user_prompt"]
