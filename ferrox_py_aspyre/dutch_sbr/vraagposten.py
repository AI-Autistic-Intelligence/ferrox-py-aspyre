"""
Vraagposten (Suspense Accounts / Triage Queue) Domain Model.
Directs ambiguous transactions, unmapped ledgers, and conflict deltas to Aspyre's review inbox.
"""

from __future__ import annotations

import enum
import time
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field


class VraagpostReason(str, enum.Enum):
    CONCURRENT_MODIFICATION_CONFLICT = "CONCURRENT_MODIFICATION_CONFLICT"
    AMBIGUOUS_VAT_TREATMENT = "AMBIGUOUS_VAT_TREATMENT"
    UNKNOWN_COUNTERPARTY_IBAN = "UNKNOWN_COUNTERPARTY_IBAN"
    MISSING_RGS_MAPPING = "MISSING_RGS_MAPPING"
    UNBALANCED_DELTA = "UNBALANCED_DELTA"


class VraagpostStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    AI_SUGGESTED = "AI_SUGGESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VraagpostItem(BaseModel):
    vraagpost_id: str
    division_id: str
    office_id: str
    source_system: str  # e.g. "ExactOnline", "Twinfield", "Moneybird", "Aspyre"
    external_entity_id: str
    amount: Decimal
    reason: VraagpostReason
    status: VraagpostStatus = VraagpostStatus.PENDING_REVIEW
    original_payload: dict[str, Any]
    conflict_delta: dict[str, Any] | None = None
    ai_confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    ai_suggested_account: str | None = None
    ai_suggested_vat_category: str | None = None
    suspense_account_code: str = "9999"  # Standard Dutch Grootboek Vraagposten
    created_at: float = Field(default_factory=time.time)
    resolved_at: float | None = None
    resolved_by: str | None = None
