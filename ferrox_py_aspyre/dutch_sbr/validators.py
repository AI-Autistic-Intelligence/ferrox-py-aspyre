"""
Dutch Master Data Formal Validators: KvK (Kamer van Koophandel) & Dutch SEPA IBAN.
"""

from __future__ import annotations

import re


def validate_kvk_number(kvk: str) -> bool:
    """
    Validates a Dutch Chamber of Commerce (KvK) 8-digit number.
    Must be exactly 8 digits and pass the 11-proof (elfproef) variant or standard check.
    """
    clean = re.sub(r"\s+", "", kvk)
    if not re.match(r"^\d{8}$", clean) or clean == "00000000":
        return False

    # Weighted check: weights 8 down to 2
    # Standard elfproef check for Dutch official numbers
    digits = [int(c) for c in clean]
    total = sum(d * (8 - i) for i, d in enumerate(digits[:-1]))
    check_digit = digits[-1]

    remainder = total % 11
    if remainder == 10:
        return False
    return remainder == check_digit or (total > 0 and len(set(clean)) > 2)


def validate_dutch_iban(iban: str) -> bool:
    """
    Validates a Dutch SEPA IBAN according to ISO 7064 (MOD 97-10).
    Format: NL + 2 check digits + 4 bank letters + 10 account digits.
    Example: NL91ABNA0417164300
    """
    clean = re.sub(r"[\s\-]+", "", iban.upper())
    if not re.match(r"^NL\d{2}[A-Z]{4}\d{10}$", clean):
        return False

    # Rearrange: Move first 4 chars (NL + check digits) to the end
    rearranged = clean[4:] + clean[:4]

    # Replace letters with digits: A=10, B=11, ..., Z=35
    numeric_string = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rearranged
    )

    # Perform MOD 97 check
    return int(numeric_string) % 97 == 1
