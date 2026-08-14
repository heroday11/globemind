"""Stable, fail-closed contracts for bounded authoritative-data queries."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    model_validator,
)

AUTHORITATIVE_DATA_SCHEMA_VERSION = "globemind.authoritative-data.v1"
AUTHORITATIVE_DATA_CONTRACT_VERSION = "1.0.0"
AUTHORITATIVE_DATA_ADAPTER_VERSION = "authoritative-adapters-1.0.0"

SourceId = Literal["world-bank", "imf", "un-sdg", "crossref"]
RecordValue = str | int | FiniteFloat | bool | None


class LicenseEvidence(BaseModel):
    """Legal evidence as observed, never inferred from API availability."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["verified", "restricted", "unknown"]
    identifier: str | None = Field(default=None, max_length=160)
    terms_url: str | None = Field(default=None, pattern=r"^https://", max_length=500)
    scope: str = Field(min_length=1, max_length=600)
    caveats: list[str] = Field(default_factory=list, max_length=12)


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: SourceId
    authority: str = Field(min_length=1, max_length=160)
    endpoint: str = Field(pattern=r"^https://", max_length=500)
    documentation_url: str = Field(pattern=r"^https://", max_length=500)


class CoverageEvidence(BaseModel):
    """Coverage of this bounded response, not the authority's whole corpus."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["verified", "partial", "unknown"]
    scope: str = Field(min_length=1, max_length=600)
    requested_dimensions: dict[str, list[str]] = Field(default_factory=dict)
    returned_records: int = Field(ge=0)
    upstream_total: int | None = Field(default=None, ge=0)
    truncated: bool


class CacheEvidence(BaseModel):
    """Every cache decision carries the minimum provenance needed for trust."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["refreshed", "hit", "unavailable"]
    available: bool
    cutoff: str | None = Field(default=None, max_length=64)
    cutoff_kind: Literal[
        "observation_period",
        "data_period",
        "source_update_time",
        "publication_time",
        "unknown",
    ] = "unknown"
    last_success: datetime | None = None
    expires_at: datetime | None = None
    license: LicenseEvidence
    coverage: CoverageEvidence
    source: SourceEvidence
    version: str = Field(min_length=1, max_length=160)
    payload_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class AuthorityRecord(BaseModel):
    """Small normalized record shared by all four source adapters."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=240)
    series_id: str = Field(min_length=1, max_length=120)
    series_name: str | None = Field(default=None, max_length=500)
    entity_id: str | None = Field(default=None, max_length=120)
    entity_name: str | None = Field(default=None, max_length=300)
    period: str | None = Field(default=None, max_length=64)
    value: RecordValue = None
    unit: str | None = Field(default=None, max_length=120)
    updated_at: datetime | None = None
    metadata: dict[str, RecordValue] = Field(default_factory=dict)


class AuthoritativeQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["globemind.authoritative-data.v1"] = (
        AUTHORITATIVE_DATA_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = AUTHORITATIVE_DATA_CONTRACT_VERSION
    generated_at: datetime
    query_id: str = Field(pattern=r"^[a-z-]+:[0-9a-f]{16}$")
    source_id: SourceId
    available: bool
    state: Literal["available", "cached", "unavailable"]
    records: list[AuthorityRecord]
    cache: CacheEvidence
    reason_codes: list[str] = Field(default_factory=list)


class ConnectorDescriptor(BaseModel):
    """Static connector registration; it is not a live health assertion."""

    model_config = ConfigDict(extra="forbid")

    source: SourceEvidence
    api_version: str = Field(min_length=1, max_length=80)
    adapter_version: Literal["authoritative-adapters-1.0.0"] = (
        AUTHORITATIVE_DATA_ADAPTER_VERSION
    )
    license: LicenseEvidence
    maximum_records_per_request: int = Field(gt=0, le=100)
    cache_ttl_seconds: int = Field(gt=0, le=86400)
    request_timeout_seconds: FiniteFloat = Field(gt=0, le=30)
    available: Literal[False] = False
    operational_state: Literal["not_observed"] = "not_observed"
    live_checked: Literal[False] = False


class AuthoritativeCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["globemind.authoritative-data.v1"] = (
        AUTHORITATIVE_DATA_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = AUTHORITATIVE_DATA_CONTRACT_VERSION
    generated_at: datetime
    available: Literal[False] = False
    operational_state: Literal["not_observed"] = "not_observed"
    connectors: list[ConnectorDescriptor]
    reason_codes: list[str] = Field(
        default_factory=lambda: ["LIVE_STATUS_NOT_OBSERVED"]
    )


class WorldBankQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str = Field(pattern=r"^[A-Z0-9]{2,3}$")
    indicator: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,63}$")
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    limit: int = Field(default=24, ge=1, le=100)

    @model_validator(mode="after")
    def validate_year_window(self) -> "WorldBankQuery":
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("start_year and end_year must be supplied together")
        if self.start_year is not None and self.end_year is not None:
            if self.end_year < self.start_year:
                raise ValueError("end_year must be on or after start_year")
            if self.end_year - self.start_year > 50:
                raise ValueError("year range may not exceed 50 years")
        return self


class ImfQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,63}$")
    entities: list[str] = Field(min_length=1, max_length=5)
    periods: list[int] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "ImfQuery":
        if len(set(self.entities)) != len(self.entities):
            raise ValueError("entities must be unique")
        if any(
            re.fullmatch(r"[A-Z0-9][A-Z0-9_]{1,11}", value) is None
            for value in self.entities
        ):
            raise ValueError("invalid IMF entity identifier")
        if any(value < 1900 or value > 2100 for value in self.periods):
            raise ValueError("period must be between 1900 and 2100")
        if len(set(self.periods)) != len(self.periods):
            raise ValueError("periods must be unique")
        return self


class UnSdgQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.~-]{1,79}$")
    area_code: int = Field(ge=0, le=999)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)
    limit: int = Field(default=25, ge=1, le=50)

    @model_validator(mode="after")
    def validate_year_window(self) -> "UnSdgQuery":
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("start_year and end_year must be supplied together")
        if self.start_year is not None and self.end_year is not None:
            if self.end_year < self.start_year:
                raise ValueError("end_year must be on or after start_year")
            if self.end_year - self.start_year > 50:
                raise ValueError("year range may not exceed 50 years")
        return self


class CrossrefQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    from_index_date: date | None = None
    until_index_date: date | None = None
    limit: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate_date_window(self) -> "CrossrefQuery":
        if (self.from_index_date is None) != (self.until_index_date is None):
            raise ValueError(
                "from_index_date and until_index_date must be supplied together"
            )
        if self.from_index_date and self.until_index_date:
            if self.until_index_date < self.from_index_date:
                raise ValueError("until_index_date must be on or after from_index_date")
            if (self.until_index_date - self.from_index_date).days > 3660:
                raise ValueError("index-date range may not exceed ten years")
        return self


__all__ = (
    "AUTHORITATIVE_DATA_ADAPTER_VERSION",
    "AUTHORITATIVE_DATA_CONTRACT_VERSION",
    "AUTHORITATIVE_DATA_SCHEMA_VERSION",
    "AuthorityRecord",
    "AuthoritativeCatalogResponse",
    "AuthoritativeQueryResponse",
    "CacheEvidence",
    "ConnectorDescriptor",
    "CoverageEvidence",
    "CrossrefQuery",
    "ImfQuery",
    "LicenseEvidence",
    "SourceEvidence",
    "SourceId",
    "UnSdgQuery",
    "WorldBankQuery",
)
