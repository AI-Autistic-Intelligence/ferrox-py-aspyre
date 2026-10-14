"""
Reversible Financial Saga Steps for Double-Entry Accounting.
Implements GAAP/Belastingdienst-compliant Storno (Tegenboeking) Compensations.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine
from ferrox_py.architectures.sagas import SagaStep
from ..dutch_sbr.models import JournalEntry, JournalEntryLine
import structlog

logger = structlog.get_logger(__name__)


def create_storno_reversal(entry: JournalEntry) -> JournalEntry:
    """
    Creates an exact accounting reversal (Storno / Tegenboeking) where debits and credits are swapped.
    This is the only legally compliant way to undo a journal entry in Dutch financial administrations.
    """
    reversed_lines: list[JournalEntryLine] = []
    for line in entry.lines:
        reversed_lines.append(
            JournalEntryLine(
                line_number=line.line_number,
                account_code=line.account_code,
                rgs_code=line.rgs_code,
                debit=line.credit,   # Invert: old credit becomes new debit
                credit=line.debit,   # Invert: old debit becomes new credit
                vat_category=line.vat_category,
                vat_amount=line.vat_amount,
                description=f"[STORNO] {line.description}",
            )
        )

    return JournalEntry(
        entry_id=f"storno-{entry.entry_id}",
        division_id=entry.division_id,
        journal_code=entry.journal_code,
        entry_number=f"REV-{entry.entry_number}",
        entry_date=entry.entry_date,
        period=entry.period,
        fiscal_year=entry.fiscal_year,
        counterparty_name=entry.counterparty_name,
        counterparty_kvk=entry.counterparty_kvk,
        counterparty_iban=entry.counterparty_iban,
        lines=reversed_lines,
    )
