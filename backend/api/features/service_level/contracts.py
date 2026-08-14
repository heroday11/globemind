"""Versioned, privacy-minimal service-level measurement contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

SERVICE_LEVEL_SCHEMA_VERSION = "globemind.service-level.v1"
OBSERVATION_SCHEMA_VERSION = "globemind.service-level.observation.v1"
FAILURE_SCHEMA_VERSION = "globemind.service-level.write-failure.v1"
MEASUREMENT_METHOD_VERSION = "http-route-template-duration-nearest-rank-v1"
STORE_SCHEMA_VERSION = "globemind.service-level.store-entry.v1"
MAX_DURATION_MS = 3_600_000
MAX_WINDOW_HOURS = 24 * 30

Operation = Literal["search", "export", "report"]
Outcome = Literal["success", "error", "timeout", "cancelled"]


def _timezone_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class ObservationInput(BaseModel):
    """The complete measurement input; arbitrary request details are forbidden."""

    model_config = ConfigDict(extra="forbid")

    operation: Operation
    outcome: Outcome
    duration_ms: int = Field(ge=0, le=MAX_DURATION_MS)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _timezone_required(value)


class ObservationSubmission(BaseModel):
    """Admin/internal adapter input; the operation remains a closed path value."""

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    duration_ms: int = Field(ge=0, le=MAX_DURATION_MS)
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime | None) -> datetime | None:
        return _timezone_required(value) if value is not None else None


class StoredObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage_schema_version: Literal[STORE_SCHEMA_VERSION] = STORE_SCHEMA_VERSION
    observation_schema_version: Literal[OBSERVATION_SCHEMA_VERSION] = (
        OBSERVATION_SCHEMA_VERSION
    )
    measurement_method_version: Literal[MEASUREMENT_METHOD_VERSION] = (
        MEASUREMENT_METHOD_VERSION
    )
    sequence: int = Field(gt=0)
    observed_at: datetime
    operation: Operation
    outcome: Outcome
    duration_ms: int = Field(ge=0, le=MAX_DURATION_MS)
    previous_entry_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _timezone_required(value)


class StoredWriteFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_schema_version: Literal[FAILURE_SCHEMA_VERSION] = FAILURE_SCHEMA_VERSION
    measurement_method_version: Literal[MEASUREMENT_METHOD_VERSION] = (
        MEASUREMENT_METHOD_VERSION
    )
    sequence: int = Field(gt=0)
    failed_at: datetime
    operation: Operation
    reason_code: Literal["observation_store_unavailable"] = (
        "observation_store_unavailable"
    )
    previous_entry_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("failed_at")
    @classmethod
    def validate_failed_at(cls, value: datetime) -> datetime:
        return _timezone_required(value)


class TargetApprovalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_state: Literal["not_approved"] = "not_approved"
    compliance: Literal["not_computable"] = "not_computable"
    targets_configured: Literal[False] = False
    approver_evidence_state: Literal["absent"] = "absent"


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["overall", "search", "export", "report"]
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    error_rate_definition: Literal["all_non_success_outcomes"] = (
        "all_non_success_outcomes"
    )
    percentile_method: Literal["nearest_rank"] = "nearest_rank"
    success_rate: FiniteFloat | None = Field(default=None, ge=0, le=1)
    error_rate: FiniteFloat | None = Field(default=None, ge=0, le=1)
    p50_ms: int | None = Field(default=None, ge=0, le=MAX_DURATION_MS)
    p95_ms: int | None = Field(default=None, ge=0, le=MAX_DURATION_MS)
    p99_ms: int | None = Field(default=None, ge=0, le=MAX_DURATION_MS)


class ServiceLevelStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SERVICE_LEVEL_SCHEMA_VERSION] = SERVICE_LEVEL_SCHEMA_VERSION
    measurement_method_version: Literal[MEASUREMENT_METHOD_VERSION] = (
        MEASUREMENT_METHOD_VERSION
    )
    generated_at: datetime
    measurement_state: Literal["not_observed", "observed"]
    storage_state: Literal["not_initialized", "available"]
    integrity_state: Literal["verified"] = "verified"
    total_observation_count: int = Field(ge=0)
    instrumentation_write_failure_count: int = Field(ge=0)
    instrumentation_write_state: Literal["no_failures_observed", "failures_observed"]
    target: TargetApprovalState = Field(default_factory=TargetApprovalState)


class ServiceLevelWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    hours: int = Field(ge=1, le=MAX_WINDOW_HOURS)


class ServiceLevelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SERVICE_LEVEL_SCHEMA_VERSION] = SERVICE_LEVEL_SCHEMA_VERSION
    measurement_method_version: Literal[MEASUREMENT_METHOD_VERSION] = (
        MEASUREMENT_METHOD_VERSION
    )
    generated_at: datetime
    measurement_state: Literal["not_observed", "observed"]
    storage_state: Literal["not_initialized", "available"]
    integrity_state: Literal["verified"] = "verified"
    window: ServiceLevelWindow
    overall: AggregateMetrics
    operations: list[AggregateMetrics] = Field(min_length=3, max_length=3)
    instrumentation_write_failure_count: int = Field(ge=0)
    instrumentation_write_state: Literal["no_failures_observed", "failures_observed"]
    target: TargetApprovalState = Field(default_factory=TargetApprovalState)


class ObservationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SERVICE_LEVEL_SCHEMA_VERSION] = SERVICE_LEVEL_SCHEMA_VERSION
    measurement_method_version: Literal[MEASUREMENT_METHOD_VERSION] = (
        MEASUREMENT_METHOD_VERSION
    )
    recorded: Literal[True] = True
    operation: Operation


__all__ = (
    "AggregateMetrics",
    "FAILURE_SCHEMA_VERSION",
    "MAX_DURATION_MS",
    "MAX_WINDOW_HOURS",
    "MEASUREMENT_METHOD_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "ObservationInput",
    "ObservationReceipt",
    "ObservationSubmission",
    "Operation",
    "Outcome",
    "SERVICE_LEVEL_SCHEMA_VERSION",
    "STORE_SCHEMA_VERSION",
    "ServiceLevelStatus",
    "ServiceLevelSummary",
    "ServiceLevelWindow",
    "StoredObservation",
    "StoredWriteFailure",
    "TargetApprovalState",
)
