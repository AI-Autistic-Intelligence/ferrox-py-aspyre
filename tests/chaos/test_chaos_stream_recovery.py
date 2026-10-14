import asyncio
from decimal import Decimal
import pytest
from ferrox_py_aspyre.twinfield.checkpoint_recovery import (
    TwinfieldCheckpointTracker,
    TwinfieldResilientStreamer,
)
from ferrox_py_aspyre.twinfield.streaming_parser import TwinfieldTransaction


def _generate_chunkable_xml(start_num: int, count: int) -> str:
    xml_parts = ['<browse><browse_data>']
    for i in range(count):
        num = start_num + i
        xml_parts.append(f"""
        <transaction office="OFFICE_NL" code="VRK" number="{num}" date="2026-04-01">
            <line id="1">
                <account>1300</account>
                <debit>121.00</debit>
                <credit>0.00</credit>
                <vatcode></vatcode>
                <vatamount>0.00</vatamount>
                <description>Invoice {num}</description>
            </line>
            <line id="2">
                <account>8000</account>
                <debit>0.00</debit>
                <credit>121.00</credit>
                <vatcode>VH</vatcode>
                <vatamount>21.00</vatamount>
                <description>Revenue</description>
            </line>
        </transaction>
        """)
    xml_parts.append('</browse_data></browse>')
    return "".join(xml_parts)


@pytest.mark.asyncio
async def test_chaos_stream_recovery_on_socket_collapse(fake_redis):
    """
    CHAOS TEST: A 100-transaction stream collapses midway after 40 records due to simulated socket reset.
    When resumed with the full stream, the resilient streamer must resume from checkpoint #1040
    and process the remaining 60 records with ZERO duplicate ledger entries.
    """
    tracker = TwinfieldCheckpointTracker(fake_redis)
    streamer = TwinfieldResilientStreamer(tracker)

    full_xml = _generate_chunkable_xml(start_num=1001, count=100)
    committed_tx_ids: list[str] = []

    # 1. First run: Fault injected after 40 transactions
    call_count = 0
    async def flaky_consumer(batch: list[TwinfieldTransaction]) -> None:
        nonlocal call_count
        call_count += 1
        if call_count > 2:
            raise ConnectionResetError("CHAOS: Remote Twinfield cluster closed TCP socket abruptly")
        for tx in batch:
            committed_tx_ids.append(tx.transaction_number)

    # Run stream 1: Expect failure
    with pytest.raises(ConnectionResetError):
        await streamer.process_stream_with_checkpoints(
            stream_id="stream_chaos_01",
            office_code="OFFICE_NL",
            xml_source=full_xml,
            batch_consumer=flaky_consumer,
            chunk_size=20,
        )

    # Verify that exactly 40 transactions were committed before crash
    assert len(committed_tx_ids) == 40
    assert committed_tx_ids[-1] == "1040"

    # Checkpoint in Redis must show 1040 as last processed
    cp = await tracker.get_checkpoint("OFFICE_NL", "stream_chaos_01")
    assert cp is not None
    assert cp.last_processed_tx_number == "1040"
    assert cp.status == "INTERRUPTED"

    # 2. Resumed run: Network restored, full stream replayed
    resumed_consumer_calls = 0
    async def healthy_consumer(batch: list[TwinfieldTransaction]) -> None:
        nonlocal resumed_consumer_calls
        resumed_consumer_calls += 1
        for tx in batch:
            committed_tx_ids.append(tx.transaction_number)

    new_records = await streamer.process_stream_with_checkpoints(
        stream_id="stream_chaos_01",
        office_code="OFFICE_NL",
        xml_source=full_xml,
        batch_consumer=healthy_consumer,
        chunk_size=20,
    )

    # 3. Assertions:
    # Newly committed records must be exactly 60 (records 1041 to 1100)
    assert new_records == 60
    assert len(committed_tx_ids) == 100

    # ZERO duplicate transactions allowed in ledger
    assert len(set(committed_tx_ids)) == 100
    assert committed_tx_ids[0] == "1001"
    assert committed_tx_ids[-1] == "1100"

    # Checkpoint status must now be COMPLETED
    final_cp = await tracker.get_checkpoint("OFFICE_NL", "stream_chaos_01")
    assert final_cp is not None
    assert final_cp.status == "COMPLETED"
    assert final_cp.last_processed_tx_number == "1100"
