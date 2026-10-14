"""
AI Bridge: Prompt Context Builder for Autonomous Accounting & VAT Reconciliation.
Produces sanitized, token-optimized contexts for LLM agents.
"""

from __future__ import annotations

import json
from typing import Any, Sequence
from ..dutch_sbr.models import JournalEntry
from ..dutch_sbr.vraagposten import VraagpostItem
from .pii_scrubber import GdprPiiScrubber


class AccountingPromptContextBuilder:
    """
    Constructs high-density, GDPR-compliant context representations for Aspyre AI agents.
    """

    def __init__(self, scrubber: GdprPiiScrubber | None = None) -> None:
        self.scrubber = scrubber or GdprPiiScrubber()

    def build_vraagpost_triage_prompt(
        self,
        vraagpost: VraagpostItem,
        candidate_rgs_accounts: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Builds the prompt context for resolving a suspense account item (Vraagpost).
        """
        # Scrub original payload if it represents a journal entry or transaction
        scrubbed_payload = self.scrubber.scrub_text(json.dumps(vraagpost.original_payload))

        default_accounts = candidate_rgs_accounts or [
            {"code": "4500", "rgs": "WKosAlg", "name": "Algemene exploitatiekosten"},
            {"code": "4000", "rgs": "WKosAut", "name": "Autokosten en transport"},
            {"code": "4300", "rgs": "WKosHui", "name": "Huisvestingskosten"},
            {"code": "8000", "rgs": "WOmzOmz", "name": "Omzet hoog tarief 21%"},
            {"code": "9999", "rgs": "BPassVra", "name": "Vraagposten (Suspense)"},
        ]

        system_instruction = (
            "You are Aspyre's Autonomous Dutch Tax & Accounting AI. "
            "Analyze the following scrubbed financial query (Vraagpost), determine the exact RGS account code, "
            "and verify the Dutch VAT (Btw) category according to Belastingdienst guidelines. "
            "All GDPR identifiers have been pseudonymized."
        )

        user_content = {
            "vraagpost_id": vraagpost.vraagpost_id,
            "amount_eur": str(vraagpost.amount),
            "reason": vraagpost.reason.value,
            "suspense_account": vraagpost.suspense_account_code,
            "sanitized_evidence": scrubbed_payload,
            "candidate_rgs_accounts": default_accounts,
            "dutch_vat_rules": {
                "HIGH_21": "Rubriek 1a: 21% standard tariff",
                "LOW_9": "Rubriek 1b: 9% food, medicine, books",
                "REVERSE_CHARGE": "Rubriek 2a: Btw verlegd",
                "ZERO_0": "Rubriek 1e: 0% or exempt",
            },
        }

        return {
            "system_instruction": system_instruction,
            "user_prompt": json.dumps(user_content, indent=2),
            "response_schema": {
                "type": "object",
                "properties": {
                    "suggested_account_code": {"type": "string"},
                    "suggested_vat_category": {"type": "string"},
                    "confidence_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reasoning_dutch": {"type": "string"},
                },
                "required": ["suggested_account_code", "suggested_vat_category", "confidence_score"],
            },
        }
