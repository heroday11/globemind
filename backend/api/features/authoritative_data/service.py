"""Application service and bounded in-memory cache for authority adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from .adapters import (
    CrossrefAdapter,
    ImfAdapter,
    NormalizedPayload,
    UnSdgAdapter,
    WorldBankAdapter,
)
from .contracts import (
    AUTHORITATIVE_DATA_ADAPTER_VERSION,
    AuthoritativeCatalogResponse,
    AuthoritativeQueryResponse,
    CacheEvidence,
    ConnectorDescriptor,
    CoverageEvidence,
    CrossrefQuery,
    ImfQuery,
    SourceId,
    UnSdgQuery,
    WorldBankQuery,
)
from .country_profiles import (
    CountryProfileCatalogResponse,
    country_profile_catalog,
)
from .institution_profiles import (
    CountryInstitutionCatalogResponse,
    country_institution_catalog,
)
from .primary_documents import (
    CountryPrimaryDocumentCatalogResponse,
    country_primary_document_catalog,
)
from .sources import connector_descriptors
from .transport import BoundedJsonClient, UpstreamFailure

QueryModel = WorldBankQuery | ImfQuery | UnSdgQuery | CrossrefQuery
Adapter = WorldBankAdapter | ImfAdapter | UnSdgAdapter | CrossrefAdapter


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class _CacheEntry:
    response: AuthoritativeQueryResponse
    stored_at: datetime
    expires_at: datetime


class BoundedMemoryCache:
    """Process-local cache with a fixed entry count and no filesystem side effects."""

    def __init__(self, maximum_entries: int = 64) -> None:
        if maximum_entries <= 0 or maximum_entries > 256:
            raise ValueError("maximum_entries must be in [1, 256]")
        self._maximum_entries = maximum_entries
        self._entries: dict[str, _CacheEntry] = {}

    def lookup(self, key: str) -> _CacheEntry | None:
        return self._entries.get(key)

    def store(self, key: str, entry: _CacheEntry) -> None:
        self._entries[key] = entry
        while len(self._entries) > self._maximum_entries:
            oldest_key = min(
                self._entries,
                key=lambda candidate: self._entries[candidate].stored_at,
            )
            del self._entries[oldest_key]


class AuthoritativeDataService:
    def __init__(
        self,
        *,
        client: BoundedJsonClient | None = None,
        cache: BoundedMemoryCache | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or BoundedJsonClient()
        self._cache = cache or BoundedMemoryCache()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._fetch_lock = asyncio.Lock()
        self._adapters: dict[SourceId, Adapter] = {
            "world-bank": WorldBankAdapter(self._client),
            "imf": ImfAdapter(self._client),
            "un-sdg": UnSdgAdapter(self._client),
            "crossref": CrossrefAdapter(self._client),
        }

    def catalog(self) -> AuthoritativeCatalogResponse:
        """Return registration only; never reinterpret it as a live probe."""

        return AuthoritativeCatalogResponse(
            generated_at=_utc_now(self._now()),
            connectors=connector_descriptors(),
        )

    def country_profile_catalog(self) -> CountryProfileCatalogResponse:
        """Return schema readiness only; no country facts or live checks exist."""

        return country_profile_catalog(generated_at=_utc_now(self._now()))

    def country_institution_catalog(self) -> CountryInstitutionCatalogResponse:
        """Return institution schema readiness without facts or live checks."""

        return country_institution_catalog(generated_at=_utc_now(self._now()))

    def country_primary_document_catalog(
        self,
    ) -> CountryPrimaryDocumentCatalogResponse:
        """Return primary-document schema readiness without reading documents."""

        return country_primary_document_catalog(generated_at=_utc_now(self._now()))

    async def world_bank(
        self,
        query: WorldBankQuery,
        *,
        refresh: bool = False,
    ) -> AuthoritativeQueryResponse:
        return await self._execute("world-bank", query, refresh=refresh)

    async def imf(
        self,
        query: ImfQuery,
        *,
        refresh: bool = False,
    ) -> AuthoritativeQueryResponse:
        return await self._execute("imf", query, refresh=refresh)

    async def un_sdg(
        self,
        query: UnSdgQuery,
        *,
        refresh: bool = False,
    ) -> AuthoritativeQueryResponse:
        return await self._execute("un-sdg", query, refresh=refresh)

    async def crossref(
        self,
        query: CrossrefQuery,
        *,
        refresh: bool = False,
    ) -> AuthoritativeQueryResponse:
        return await self._execute("crossref", query, refresh=refresh)

    async def _execute(
        self,
        source_id: SourceId,
        query: QueryModel,
        *,
        refresh: bool,
    ) -> AuthoritativeQueryResponse:
        descriptor = self._adapters[source_id].descriptor
        query_payload = query.model_dump(mode="json", exclude_none=True)
        query_digest = _canonical_hash(
            {"source_id": source_id, "query": query_payload}
        )
        cache_key = f"{source_id}:{query_digest}"
        now = _utc_now(self._now())
        cached = self._cache.lookup(cache_key)
        if not refresh and cached is not None and now < cached.expires_at:
            return self._cache_hit(cached, generated_at=now)

        async with self._fetch_lock:
            now = _utc_now(self._now())
            cached = self._cache.lookup(cache_key)
            if not refresh and cached is not None and now < cached.expires_at:
                return self._cache_hit(cached, generated_at=now)
            try:
                normalized = await self._adapters[source_id].fetch(query)  # type: ignore[arg-type]
                self._validate_normalized(normalized)
            except UpstreamFailure as exc:
                return self._unavailable(
                    descriptor,
                    query_digest=query_digest,
                    query=query,
                    generated_at=now,
                    reason_code=exc.reason_code,
                    previous=cached,
                )
            except Exception:
                return self._unavailable(
                    descriptor,
                    query_digest=query_digest,
                    query=query,
                    generated_at=now,
                    reason_code="ADAPTER_INTERNAL_ERROR",
                    previous=cached,
                )

            if not normalized.records:
                return self._unavailable(
                    descriptor,
                    query_digest=query_digest,
                    query=query,
                    generated_at=now,
                    reason_code="NO_VALIDATED_RECORDS",
                    previous=cached,
                )
            if not normalized.cutoff:
                return self._unavailable(
                    descriptor,
                    query_digest=query_digest,
                    query=query,
                    generated_at=now,
                    reason_code="CUTOFF_UNAVAILABLE",
                    previous=cached,
                )

            expires_at = now + timedelta(seconds=descriptor.cache_ttl_seconds)
            payload_sha256 = _canonical_hash(
                [record.model_dump(mode="json") for record in normalized.records]
            )
            cache_evidence = CacheEvidence(
                state="refreshed",
                available=True,
                cutoff=normalized.cutoff,
                cutoff_kind=normalized.cutoff_kind,
                last_success=now,
                expires_at=expires_at,
                license=descriptor.license,
                coverage=normalized.coverage,
                source=descriptor.source,
                version=(
                    f"{descriptor.api_version};{AUTHORITATIVE_DATA_ADAPTER_VERSION}"
                ),
                payload_sha256=payload_sha256,
            )
            response = AuthoritativeQueryResponse(
                generated_at=now,
                query_id=f"{source_id}:{query_digest[:16]}",
                source_id=source_id,
                available=True,
                state="available",
                records=normalized.records,
                cache=cache_evidence,
                reason_codes=[],
            )
            self._cache.store(
                cache_key,
                _CacheEntry(
                    response=response.model_copy(deep=True),
                    stored_at=now,
                    expires_at=expires_at,
                ),
            )
            return response

    @staticmethod
    def _validate_normalized(normalized: NormalizedPayload) -> None:
        identifiers = [record.record_id for record in normalized.records]
        if len(set(identifiers)) != len(identifiers):
            raise UpstreamFailure("DUPLICATE_NORMALIZED_RECORD")
        if normalized.coverage.returned_records != len(normalized.records):
            raise UpstreamFailure("COVERAGE_CONTRACT_MISMATCH")

    @staticmethod
    def _cache_hit(
        entry: _CacheEntry,
        *,
        generated_at: datetime,
    ) -> AuthoritativeQueryResponse:
        response = entry.response.model_copy(deep=True)
        response.generated_at = generated_at
        response.state = "cached"
        response.cache.state = "hit"
        return response

    @staticmethod
    def _requested_dimensions(query: BaseModel) -> dict[str, list[str]]:
        output: dict[str, list[str]] = {}
        for key, value in query.model_dump(mode="json", exclude_none=True).items():
            if key == "limit":
                continue
            if key == "query" and isinstance(value, str):
                output["query_sha256"] = [_canonical_hash(value)]
                output["query_length"] = [str(len(value))]
                continue
            if isinstance(value, list):
                output[key] = [str(item) for item in value]
            else:
                output[key] = [str(value)]
        return output

    def _unavailable(
        self,
        descriptor: ConnectorDescriptor,
        *,
        query_digest: str,
        query: BaseModel,
        generated_at: datetime,
        reason_code: str,
        previous: _CacheEntry | None,
    ) -> AuthoritativeQueryResponse:
        if previous is None:
            coverage = CoverageEvidence(
                state="unknown",
                scope="No validated upstream response is available for this query.",
                requested_dimensions=self._requested_dimensions(query),
                returned_records=0,
                upstream_total=None,
                truncated=False,
            )
            cutoff = None
            cutoff_kind = "unknown"
            last_success = None
            expires_at = None
            payload_sha256 = None
        else:
            prior_cache = previous.response.cache
            coverage = CoverageEvidence(
                state="unknown",
                scope=(
                    "Refresh failed; evidence from the previous validated response is "
                    "retained below, but no prior records are served as current."
                ),
                requested_dimensions=self._requested_dimensions(query),
                returned_records=0,
                upstream_total=prior_cache.coverage.upstream_total,
                truncated=prior_cache.coverage.truncated,
            )
            cutoff = prior_cache.cutoff
            cutoff_kind = prior_cache.cutoff_kind
            last_success = prior_cache.last_success
            expires_at = prior_cache.expires_at
            payload_sha256 = prior_cache.payload_sha256

        return AuthoritativeQueryResponse(
            generated_at=generated_at,
            query_id=f"{descriptor.source.source_id}:{query_digest[:16]}",
            source_id=descriptor.source.source_id,
            available=False,
            state="unavailable",
            records=[],
            cache=CacheEvidence(
                state="unavailable",
                available=False,
                cutoff=cutoff,
                cutoff_kind=cutoff_kind,
                last_success=last_success,
                expires_at=expires_at,
                license=descriptor.license,
                coverage=coverage,
                source=descriptor.source,
                version=(
                    f"{descriptor.api_version};{AUTHORITATIVE_DATA_ADAPTER_VERSION}"
                ),
                payload_sha256=payload_sha256,
            ),
            reason_codes=[reason_code],
        )


__all__ = (
    "AuthoritativeDataService",
    "BoundedMemoryCache",
)
