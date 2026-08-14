"""Application service for honest, target-free service-level measurement."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Literal

from .contracts import (
    MAX_WINDOW_HOURS,
    AggregateMetrics,
    ObservationInput,
    Operation,
    Outcome,
    ServiceLevelStatus,
    ServiceLevelSummary,
    ServiceLevelWindow,
    StoredObservation,
)
from .ledger import ServiceLevelStore, ServiceLevelStoreUnavailable

MIN_OBSERVED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)
MAX_OBSERVATION_AGE = timedelta(days=366)
MAX_CLOCK_SKEW = timedelta(minutes=5)
InstrumentationWriteResult = Literal[
    "recorded",
    "failure_recorded",
    "unavailable",
]
_OPERATIONS: tuple[Operation, ...] = ("search", "export", "report")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _nearest_rank(values: list[int], probability: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _metrics(
    scope: Literal["overall", "search", "export", "report"],
    records: Iterable[StoredObservation],
) -> AggregateMetrics:
    selected = list(records)
    sample_count = len(selected)
    success_count = sum(record.outcome == "success" for record in selected)
    error_count = sum(record.outcome == "error" for record in selected)
    timeout_count = sum(record.outcome == "timeout" for record in selected)
    cancelled_count = sum(record.outcome == "cancelled" for record in selected)
    durations = [record.duration_ms for record in selected]
    return AggregateMetrics(
        scope=scope,
        sample_count=sample_count,
        success_count=success_count,
        error_count=error_count,
        timeout_count=timeout_count,
        cancelled_count=cancelled_count,
        success_rate=(success_count / sample_count if sample_count else None),
        # Error rate is intentionally all non-success outcomes. The individual
        # counters preserve the error/timeout/cancelled split.
        error_rate=(
            (sample_count - success_count) / sample_count
            if sample_count
            else None
        ),
        p50_ms=_nearest_rank(durations, 0.50),
        p95_ms=_nearest_rank(durations, 0.95),
        p99_ms=_nearest_rank(durations, 0.99),
    )


class ServiceLevelService:
    def __init__(
        self,
        store: ServiceLevelStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _current_time(self) -> datetime:
        return _utc(self._now())

    def _validate_observed_at(self, observed_at: datetime) -> datetime:
        value = _utc(observed_at)
        now = self._current_time()
        if value < MIN_OBSERVED_AT or value < now - MAX_OBSERVATION_AGE:
            raise ValueError("observation timestamp is outside the retention bound")
        if value > now + MAX_CLOCK_SKEW:
            raise ValueError("observation timestamp exceeds the clock-skew bound")
        return value

    def record(
        self,
        *,
        operation: Operation,
        outcome: Outcome,
        duration_ms: int,
        observed_at: datetime | None = None,
    ) -> None:
        if isinstance(duration_ms, bool):
            raise ValueError("duration must be an integer number of milliseconds")
        timestamp = self._validate_observed_at(
            observed_at if observed_at is not None else self._current_time()
        )
        observation = ObservationInput.model_validate(
            {
                "operation": operation,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "observed_at": timestamp,
            }
        )
        self._store.append_observation(observation)

    def record_instrumentation(
        self,
        *,
        operation: Operation,
        outcome: Outcome,
        duration_ms: int,
        observed_at: datetime | None = None,
    ) -> InstrumentationWriteResult:
        """Best-effort write that never asks callers to replace business output.

        A failed observation write is recorded in a second bounded durable
        chain. If even that cannot be persisted, the explicit return state is
        ``unavailable``; no process-local counter pretends otherwise.
        """

        timestamp = observed_at if observed_at is not None else self._current_time()
        try:
            self.record(
                operation=operation,
                outcome=outcome,
                duration_ms=duration_ms,
                observed_at=timestamp,
            )
            return "recorded"
        except (ServiceLevelStoreUnavailable, OSError, ValueError):
            try:
                self._store.append_write_failure(
                    operation=operation,
                    failed_at=self._current_time(),
                )
            except (ServiceLevelStoreUnavailable, OSError, ValueError):
                return "unavailable"
            return "failure_recorded"

    def status(self) -> ServiceLevelStatus:
        observations, failures, initialized = self._store.snapshot()
        now = self._current_time()
        return ServiceLevelStatus(
            generated_at=now,
            measurement_state=("observed" if observations else "not_observed"),
            storage_state=("available" if initialized else "not_initialized"),
            total_observation_count=len(observations),
            instrumentation_write_failure_count=len(failures),
            instrumentation_write_state=(
                "failures_observed" if failures else "no_failures_observed"
            ),
        )

    def summary(self, *, window_hours: int = 24) -> ServiceLevelSummary:
        if (
            isinstance(window_hours, bool)
            or not isinstance(window_hours, int)
            or window_hours < 1
            or window_hours > MAX_WINDOW_HOURS
        ):
            raise ValueError(f"window_hours must be in [1, {MAX_WINDOW_HOURS}]")
        observations, failures, initialized = self._store.snapshot()
        now = self._current_time()
        starts_at = now - timedelta(hours=window_hours)
        selected = [
            observation
            for observation in observations
            if starts_at <= observation.observed_at <= now
        ]
        return ServiceLevelSummary(
            generated_at=now,
            measurement_state=("observed" if selected else "not_observed"),
            storage_state=("available" if initialized else "not_initialized"),
            window=ServiceLevelWindow(
                starts_at=starts_at,
                ends_at=now,
                hours=window_hours,
            ),
            overall=_metrics("overall", selected),
            operations=[
                _metrics(
                    operation,
                    (
                        record
                        for record in selected
                        if record.operation == operation
                    ),
                )
                for operation in _OPERATIONS
            ],
            instrumentation_write_failure_count=len(failures),
            instrumentation_write_state=(
                "failures_observed" if failures else "no_failures_observed"
            ),
        )


__all__ = (
    "InstrumentationWriteResult",
    "MAX_CLOCK_SKEW",
    "MAX_OBSERVATION_AGE",
    "MIN_OBSERVED_AT",
    "ServiceLevelService",
)
