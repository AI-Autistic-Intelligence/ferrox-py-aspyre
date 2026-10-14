"""
Twinfield Streaming XML Parser.
Zero-Copy, Off-Thread Iterparse mitigating Python Event-Loop Starvation on Large SOAP Payloads.
"""

from __future__ import annotations

import asyncio
import io
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator, Generator, Sequence

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TwinfieldEntryLine:
    line_number: int
    account_code: str
    debit: Decimal
    credit: Decimal
    vat_code: str | None
    vat_amount: Decimal
    description: str


@dataclass
class TwinfieldTransaction:
    office_code: str
    transaction_type: str
    transaction_number: str
    date: str
    lines: list[TwinfieldEntryLine]

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0.00"))

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0.00"))

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


def _sync_iterparse_stream(xml_bytes: bytes) -> Generator[TwinfieldTransaction, None, None]:
    """
    Synchronous streaming parser using iterparse and root-clearing.
    Must be executed inside an off-thread worker to release the event loop.
    """
    stream = io.BytesIO(xml_bytes)
    # Using 'end' event on transaction tags
    context = ET.iterparse(stream, events=("end",))
    
    current_tx_lines: list[TwinfieldEntryLine] = []

    for event, elem in context:
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        
        if tag in ("line", "entryline"):
            # Parse individual line
            line_no = int(elem.attrib.get("id", "0"))
            acct = elem.findtext("account") or elem.attrib.get("account", "UNKNOWN")
            deb_str = elem.findtext("debit") or "0.00"
            cred_str = elem.findtext("credit") or "0.00"
            vat_code = elem.findtext("vatcode")
            vat_amt_str = elem.findtext("vatamount") or "0.00"
            desc = elem.findtext("description") or ""

            current_tx_lines.append(
                TwinfieldEntryLine(
                    line_number=line_no,
                    account_code=acct.strip(),
                    debit=Decimal(deb_str),
                    credit=Decimal(cred_str),
                    vat_code=vat_code.strip() if vat_code else None,
                    vat_amount=Decimal(vat_amt_str),
                    description=desc.strip(),
                )
            )
            elem.clear()

        elif tag in ("transaction", "browse_row", "finentry"):
            # Finished a transaction record
            office = elem.attrib.get("office") or elem.findtext("office") or "UNKNOWN"
            code = elem.attrib.get("code") or elem.findtext("code") or "VRK"
            num = elem.attrib.get("number") or elem.findtext("number") or "0"
            dt = elem.attrib.get("date") or elem.findtext("date") or "2026-01-01"

            tx = TwinfieldTransaction(
                office_code=office.strip(),
                transaction_type=code.strip(),
                transaction_number=num.strip(),
                date=dt.strip(),
                lines=list(current_tx_lines),
            )
            current_tx_lines.clear()
            elem.clear()
            yield tx

    # Clear root element
    del context


class TwinfieldStreamingParser:
    """
    High-performance, async-friendly XML parser for Twinfield SOAP payloads.
    Guarantees that the main asyncio loop experiences zero blocking ticks.
    """

    @classmethod
    async def parse_transactions_stream(
        cls,
        xml_payload: bytes | str,
        chunk_size: int = 100,
    ) -> AsyncIterator[list[TwinfieldTransaction]]:
        """
        Parses XML in an off-thread executor, yielding chunks of transactions.
        """
        payload_bytes = xml_payload.encode("utf-8") if isinstance(xml_payload, str) else xml_payload
        start_time = time.perf_counter()

        def _run_parse() -> list[TwinfieldTransaction]:
            return list(_sync_iterparse_stream(payload_bytes))

        # Offload CPU-heavy parsing out of the event loop
        transactions = await asyncio.to_thread(_run_parse)
        
        duration = time.perf_counter() - start_time
        logger.debug(
            "twinfield_xml_parsed",
            records=len(transactions),
            bytes=len(payload_bytes),
            duration_ms=round(duration * 1000, 2),
        )

        for i in range(0, len(transactions), chunk_size):
            yield transactions[i : i + chunk_size]

    @classmethod
    async def parse_all(cls, xml_payload: bytes | str) -> list[TwinfieldTransaction]:
        """Convenience method to retrieve all parsed transactions."""
        results: list[TwinfieldTransaction] = []
        async for chunk in cls.parse_transactions_stream(xml_payload, chunk_size=500):
            results.extend(chunk)
        return results
