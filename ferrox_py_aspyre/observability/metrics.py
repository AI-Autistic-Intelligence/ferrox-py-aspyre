"""
Observability & SLA Instrumentation for Aspyre Financial Systems Integration.
Tracks Event-Loop Lag, Upstream 429 Errors, Queue Latencies, and Idempotency Violations.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SlaMetricSnapshot:
    upstream_429_count: int = 0
    duplicate_ledger_entry_count: int = 0
    total_interactive_jobs: int = 0
    total_batch_jobs: int = 0
    interactive_latencies_ms: list[float] = field(default_factory=list)
    batch_latencies_ms: list[float] = field(default_factory=list)
    event_loop_lag_samples_ms: list[float] = field(default_factory=list)
    successful_sagas: int = 0
    failed_sagas: int = 0
    compensated_sagas: int = 0
    session_renewals: int = 0

    @property
    def interactive_p99_ms(self) -> float:
        if not self.interactive_latencies_ms:
            return 0.0
        sorted_lat = sorted(self.interactive_latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def event_loop_p99_lag_ms(self) -> float:
        if not self.event_loop_lag_samples_ms:
            return 0.0
        sorted_lag = sorted(self.event_loop_lag_samples_ms)
        idx = int(len(sorted_lag) * 0.99)
        return sorted_lag[min(idx, len(sorted_lag) - 1)]

    @property
    def saga_compensation_success_ratio(self) -> float:
        if self.failed_sagas == 0:
            return 1.0
        return self.compensated_sagas / self.failed_sagas


class FinancialIntegrationMetricsCollector:
    """
    In-memory and Prometheus-compatible metrics aggregator.
    """

    def __init__(self) -> None:
        self.snapshot = SlaMetricSnapshot()
        self._monitoring = False
        self._lag_monitor_task: asyncio.Task[None] | None = None

    def record_upstream_429(self, upstream_system: str, division_id: str) -> None:
        self.snapshot.upstream_429_count += 1
        logger.error("sla_breach_upstream_429", system=upstream_system, division=division_id)

    def record_duplicate_detected(self, entity_id: str) -> None:
        self.snapshot.duplicate_ledger_entry_count += 1
        logger.critical("sla_breach_duplicate_entry", entity_id=entity_id)

    def record_job_latency(self, priority_is_interactive: bool, latency_ms: float) -> None:
        if priority_is_interactive:
            self.snapshot.total_interactive_jobs += 1
            self.snapshot.interactive_latencies_ms.append(latency_ms)
        else:
            self.snapshot.total_batch_jobs += 1
            self.snapshot.batch_latencies_ms.append(latency_ms)

    def record_saga_outcome(self, succeeded: bool, compensated: bool = False) -> None:
        if succeeded:
            self.snapshot.successful_sagas += 1
        else:
            self.snapshot.failed_sagas += 1
            if compensated:
                self.snapshot.compensated_sagas += 1

    async def _measure_loop_lag_loop(self) -> None:
        """Background coroutine that detects event-loop freezing."""
        interval = 0.05  # 50ms expected interval
        while self._monitoring:
            before = time.perf_counter()
            await asyncio.sleep(interval)
            after = time.perf_counter()
            lag_ms = max(0.0, (after - before - interval) * 1000)
            self.snapshot.event_loop_lag_samples_ms.append(lag_ms)
            if lag_ms > 50.0:
                logger.warn("event_loop_lag_spike", lag_ms=round(lag_ms, 2))

    def start_event_loop_lag_monitor(self) -> None:
        if not self._monitoring:
            self._monitoring = True
            self._lag_monitor_task = asyncio.create_task(self._measure_loop_lag_loop())

    def stop_event_loop_lag_monitor(self) -> None:
        self._monitoring = False
        if self._lag_monitor_task:
            self._lag_monitor_task.cancel()
