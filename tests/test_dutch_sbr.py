from decimal import Decimal
import pytest
from ferrox_py_aspyre.dutch_sbr.validators import (
    validate_kvk_number,
    validate_dutch_iban,
)
from ferrox_py_aspyre.dutch_sbr.vat_matrix import (
    DutchVatCategory,
    DutchVatCalculator,
)
from ferrox_py_aspyre.dutch_sbr.models import (
    JournalEntry,
    JournalEntryLine,
)


def test_kvk_validator():
    # Valid 8-digit strings
    assert validate_kvk_number("68750110") is True
    assert validate_kvk_number("01234567") is True
    # Invalid lengths or formats
    assert validate_kvk_number("1234567") is False   # 7 digits
    assert validate_kvk_number("123456789") is False # 9 digits
    assert validate_kvk_number("ABC12345") is False
    assert validate_kvk_number("00000000") is False


def test_dutch_iban_validator():
    # Well-known valid test IBANs (e.g. ABN AMRO test account)
    assert validate_dutch_iban("NL91ABNA0417164300") is True
    # Invalid checksum
    assert validate_dutch_iban("NL90ABNA0417164300") is False
    # Wrong country code or format
    assert validate_dutch_iban("DE91ABNA0417164300") is False
    assert validate_dutch_iban("NL91ABNA04171643") is False


def test_vat_calculator_and_rubrieken():
    net = Decimal("100.00")
    vat_high = DutchVatCalculator.calculate_vat(net, DutchVatCategory.HIGH_21)
    assert vat_high == Decimal("21.00")
    assert DutchVatCalculator.get_rubriek(DutchVatCategory.HIGH_21) == "1a"

    vat_low = DutchVatCalculator.calculate_vat(net, DutchVatCategory.LOW_9)
    assert vat_low == Decimal("9.00")
    assert DutchVatCalculator.get_rubriek(DutchVatCategory.LOW_9) == "1b"

    vat_verlegd = DutchVatCalculator.calculate_vat(net, DutchVatCategory.REVERSE_CHARGE)
    assert vat_verlegd == Decimal("0.00")
    assert DutchVatCalculator.get_rubriek(DutchVatCategory.REVERSE_CHARGE) == "2a"


def test_journal_entry_balancing_validation():
    # Valid balanced entry
    lines = [
        JournalEntryLine(
            line_number=1,
            account_code="1300",
            rgs_code="BActDeb",
            debit=Decimal("121.00"),
            credit=Decimal("0.00"),
            description="Debtor invoice",
        ),
        JournalEntryLine(
            line_number=2,
            account_code="8000",
            rgs_code="WOmzOmz",
            debit=Decimal("0.00"),
            credit=Decimal("100.00"),
            vat_category=DutchVatCategory.HIGH_21,
            vat_amount=Decimal("21.00"),
            description="Consulting Revenue",
        ),
        JournalEntryLine(
            line_number=3,
            account_code="1500",
            rgs_code="BPassBtw",
            debit=Decimal("0.00"),
            credit=Decimal("21.00"),
            description="VAT Payable",
        ),
    ]

    entry = JournalEntry(
        entry_id="ENTRY-001",
        division_id="DIV-100",
        journal_code="VRK",
        entry_number="2026001",
        entry_date="2026-04-01",
        period=4,
        fiscal_year=2026,
        counterparty_name="Aspyre BV",
        counterparty_kvk="68750110",
        counterparty_iban="NL91ABNA0417164300",
        lines=lines,
    )
    assert entry.total_debit == Decimal("121.00")
    assert entry.total_credit == Decimal("121.00")


def test_journal_entry_unbalanced_rejection():
    # Unbalanced: Debit 121.00 != Credit 100.00
    lines = [
        JournalEntryLine(
            line_number=1,
            account_code="1300",
            debit=Decimal("121.00"),
            credit=Decimal("0.00"),
            description="Debtor",
        ),
        JournalEntryLine(
            line_number=2,
            account_code="8000",
            debit=Decimal("0.00"),
            credit=Decimal("100.00"),
            description="Revenue",
        ),
    ]

    with pytest.raises(ValueError, match="Unbalanced journal entry"):
        JournalEntry(
            entry_id="ENTRY-UNBALANCED",
            division_id="DIV-100",
            journal_code="VRK",
            entry_number="2026002",
            entry_date="2026-04-01",
            period=4,
            fiscal_year=2026,
            lines=lines,
        )
