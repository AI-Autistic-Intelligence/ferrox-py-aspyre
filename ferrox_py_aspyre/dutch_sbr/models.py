"""
Pydantic V2 Domain Models for Dutch Standard Business Reporting (SBR) & RGS Accounting.
Enforces balanced double-entry (Debit == Credit), KvK, IBAN, and VAT matrix validation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, Field, model_validator

from .validators import validate_kvk_number, validate_dutch_iban
from .vat_matrix import DutchVatCategory, DutchVatCalculator


class JournalEntryLine(BaseModel):
    line_number: int = Field(..., ge=1)
    account_code: str = Field(..., min_length=1, max_length=20, description="Internal General Ledger account")
    rgs_code: str | None = Field(default=None, description="Referentie GrootboekSchema code, e.g., WOmzOmz")
    debit: Decimal = Field(default=Decimal("0.00"), ge=0)
    credit: Decimal = Field(default=Decimal("0.00"), ge=0)
    vat_category: DutchVatCategory | None = None
    vat_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    description: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_line_amount(self) -> JournalEntryLine:
        if self.debit > 0 and self.credit > 0:
            raise ValueError(f"Line {self.line_number}: Cannot specify both debit ({self.debit}) and credit ({self.credit}) on same line.")
        if self.debit == 0 and self.credit == 0:
            raise ValueError(f"Line {self.line_number}: Line must have either debit > 0 or credit > 0.")
        return self


class JournalEntry(BaseModel):
    entry_id: str
    division_id: str
    journal_code: str = Field(..., description="e.g. VRK (Sales), INK (Purchases), BNK (Bank), MEM (General)")
    entry_number: str
    entry_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    period: int = Field(..., ge=1, le=12)
    fiscal_year: int = Field(..., ge=2000, le=2100)
    counterparty_name: str | None = None
    counterparty_kvk: str | None = None
    counterparty_iban: str | None = None
    lines: list[JournalEntryLine] = Field(..., min_length=2)

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0.00"))

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0.00"))

    @model_validator(mode="after")
    def validate_balanced_and_compliant(self) -> JournalEntry:
        # 1. Check double entry balancing equation (Debit == Credit)
        diff = abs(self.total_debit - self.total_credit)
        if diff != Decimal("0.00"):
            raise ValueError(
                f"Unbalanced journal entry {self.entry_number}: Total Debit ({self.total_debit}) "
                f"does not match Total Credit ({self.total_credit}). Imbalance = {diff}"
            )

        # 2. Check KvK if present
        if self.counterparty_kvk and not validate_kvk_number(self.counterparty_kvk):
            raise ValueError(f"Invalid Dutch KvK registration number: {self.counterparty_kvk}")

        # 3. Check IBAN if present
        if self.counterparty_iban and not validate_dutch_iban(self.counterparty_iban):
            raise ValueError(f"Invalid Dutch SEPA IBAN: {self.counterparty_iban}")

        # 4. Check VAT accuracy on revenue / expense lines
        for line in self.lines:
            if line.vat_category and line.vat_amount > 0:
                base_amount = line.debit if line.debit > 0 else line.credit
                # If the line represents gross with VAT embedded or net
                is_valid = DutchVatCalculator.verify_vat_amount(
                    net_amount=base_amount,
                    reported_vat=line.vat_amount,
                    category=line.vat_category,
                )
                if not is_valid:
                    expected = DutchVatCalculator.calculate_vat(base_amount, line.vat_category)
                    raise ValueError(
                        f"VAT Mismatch on line {line.line_number}: Reported VAT {line.vat_amount} "
                        f"does not match expected {expected} for category {line.vat_category.value}"
                    )

        return self
