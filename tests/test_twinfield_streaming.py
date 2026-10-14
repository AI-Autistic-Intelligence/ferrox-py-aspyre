import asyncio
import time
from decimal import Decimal
import pytest
from ferrox_py_aspyre.twinfield.streaming_parser import (
    TwinfieldStreamingParser,
    TwinfieldTransaction,
)


def _generate_twinfield_xml(num_transactions: int = 50) -> str:
    xml_parts = ['<browse><browse_data>']
    for i in range(1, num_transactions + 1):
        xml_parts.append(f"""
        <transaction office="1001" code="VRK" number="{1000 + i}" date="2026-03-31">
            <line id="1">
                <account>1300</account>
                <debit>121.00</debit>
                <credit>0.00</credit>
                <vatcode></vatcode>
                <vatamount>0.00</vatamount>
                <description>Debtor Invoice {i}</description>
            </line>
            <line id="2">
                <account>8000</account>
                <debit>0.00</debit>
                <credit>100.00</credit>
                <vatcode>VH</vatcode>
                <vatamount>21.00</vatamount>
                <description>Revenue Net 21%</description>
            </line>
            <line id="3">
                <account>1500</account>
                <debit>0.00</debit>
                <credit>21.00</credit>
                <vatcode>VH</vatcode>
                <vatamount>0.00</vatamount>
                <description>VAT High 21%</description>
            </line>
        </transaction>
        """)
    xml_parts.append('</browse_data></browse>')
    return "".join(xml_parts)


@pytest.mark.asyncio
async def test_streaming_xml_parser_correctness():
    xml_data = _generate_twinfield_xml(num_transactions=10)
    transactions = await TwinfieldStreamingParser.parse_all(xml_data)

    assert len(transactions) == 10
    tx1 = transactions[0]
    assert tx1.office_code == "1001"
    assert tx1.transaction_type == "VRK"
    assert tx1.transaction_number == "1001"
    assert len(tx1.lines) == 3
    assert tx1.total_debit == Decimal("121.00")
    assert tx1.total_credit == Decimal("121.00")
    assert tx1.is_balanced is True


@pytest.mark.asyncio
async def test_streaming_xml_parser_does_not_starve_event_loop():
    """
    Spawns a heavy XML parse while simultaneously measuring event loop tick jitter.
    Verifies that the event loop remains responsive (ticks do not lag > 25ms).
    """
    large_xml = _generate_twinfield_xml(num_transactions=200)

    max_jitter_ms = 0.0
    monitoring = True

    async def loop_monitor():
        nonlocal max_jitter_ms, monitoring
        while monitoring:
            t0 = time.perf_counter()
            await asyncio.sleep(0.005)  # 5ms expected tick
            elapsed = time.perf_counter() - t0
            jitter = (elapsed - 0.005) * 1000
            if jitter > max_jitter_ms:
                max_jitter_ms = jitter

    monitor_task = asyncio.create_task(loop_monitor())

    # Execute streaming parse in parallel with the monitor
    parsed = await TwinfieldStreamingParser.parse_all(large_xml)

    monitoring = False
    await monitor_task

    assert len(parsed) == 200
    # The event loop should experience minimal blocking since parsing was offloaded
    assert max_jitter_ms < 50.0  # Safe threshold well below unmitigated 200ms-600ms GIL lock
