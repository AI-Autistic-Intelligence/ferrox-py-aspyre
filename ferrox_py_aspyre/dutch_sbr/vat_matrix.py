"""
Dutch VAT (Btw-aangifte) Tax Matrix & Validation Engine.
Compliant with Dutch Belastingdienst SBR Taxonomy and VAT Boxes (Rubrieken).
"""

from __future__ import annotations

import enum
from decimal import Decimal, ROUND_HALF_UP


class DutchVatCategory(str, enum.Enum):
    HIGH_21 = "HIGH_21"               # Rubriek 1a: 21%
    LOW_9 = "LOW_9"                   # Rubriek 1b: 9%
    ZERO_0 = "ZERO_0"                 # Rubriek 1e: 0%
    REVERSE_CHARGE = "REVERSE_CHARGE" # Rubriek 2a: Btw verlegd
    ICP_DELIVERY = "ICP_DELIVERY"     # Rubriek 3b: Leveringen naar andere EU-landen
    EU_ACQUISITION = "EU_ACQUISITION" # Rubriek 4b: Verwervingen uit EU-landen
    EXEMPT = "EXEMPT"                 # Vrijgesteld


VAT_RATES: dict[DutchVatCategory, Decimal] = {
    DutchVatCategory.HIGH_21: Decimal("0.21"),
    DutchVatCategory.LOW_9: Decimal("0.09"),
    DutchVatCategory.ZERO_0: Decimal("0.00"),
    DutchVatCategory.REVERSE_CHARGE: Decimal("0.00"),
    DutchVatCategory.ICP_DELIVERY: Decimal("0.00"),
    DutchVatCategory.EU_ACQUISITION: Decimal("0.21"),  # Normally 21% calculated on Rubriek 4b
    DutchVatCategory.EXEMPT: Decimal("0.00"),
}

VAT_RUBRIEKEN_MAP: dict[DutchVatCategory, str] = {
    DutchVatCategory.HIGH_21: "1a",
    DutchVatCategory.LOW_9: "1b",
    DutchVatCategory.ZERO_0: "1e",
    DutchVatCategory.REVERSE_CHARGE: "2a",
    DutchVatCategory.ICP_DELIVERY: "3b",
    DutchVatCategory.EU_ACQUISITION: "4b",
    DutchVatCategory.EXEMPT: "none",
}


class DutchVatCalculator:
    """
    Computes and verifies Dutch BTW line items with penny-rounding tolerance.
    """

    @staticmethod
    def calculate_vat(net_amount: Decimal, category: DutchVatCategory) -> Decimal:
        rate = VAT_RATES.get(category, Decimal("0.00"))
        vat = (net_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return vat

    @staticmethod
    def verify_vat_amount(
        net_amount: Decimal,
        reported_vat: Decimal,
        category: DutchVatCategory,
        tolerance: Decimal = Decimal("0.05"),
    ) -> bool:
        """
        Verifies if reported VAT matches computed VAT within commercial tolerance (rounding issues).
        """
        expected = DutchVatCalculator.calculate_vat(net_amount, category)
        diff = abs(reported_vat - expected)
        return diff <= tolerance

    @staticmethod
    def get_rubriek(category: DutchVatCategory) -> str:
        return VAT_RUBRIEKEN_MAP.get(category, "unknown")
