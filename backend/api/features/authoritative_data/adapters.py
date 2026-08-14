"""Bounded normalizers for four official, read-only data APIs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .contracts import (
    AuthorityRecord,
    ConnectorDescriptor,
    CoverageEvidence,
    CrossrefQuery,
    ImfQuery,
    RecordValue,
    UnSdgQuery,
    WorldBankQuery,
)
from .sources import connector_descriptor
from .transport import BoundedJsonClient, UpstreamFailure

CutoffKind = Literal[
    "observation_period",
    "data_period",
    "source_update_time",
    "publication_time",
    "unknown",
]


@dataclass(frozen=True)
class NormalizedPayload:
    records: list[AuthorityRecord]
    cutoff: str | None
    cutoff_kind: CutoffKind
    coverage: CoverageEvidence


def _text(value: Any, *, maximum: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:maximum] if normalized else None


def _record_value(value: Any) -> RecordValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _text(value, maximum=500)
    return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _period(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return str(int(value)) if value.is_integer() else str(value)[:64]
    return _text(value, maximum=64)


def _period_key(value: str) -> tuple[int, str]:
    match = re.match(r"^(\d{4})", value)
    return (int(match.group(1)) if match else -1, value)


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_metadata(
    raw: Any,
    *,
    prefix: str = "",
    maximum_items: int = 24,
) -> dict[str, RecordValue]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, RecordValue] = {}
    for key, value in raw.items():
        safe_key = _text(key, maximum=48)
        if not safe_key or not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_key):
            continue
        normalized = _record_value(value)
        if normalized is None:
            continue
        output[f"{prefix}{safe_key}"] = normalized
        if len(output) >= maximum_items:
            break
    return output


def _stable_suffix(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class WorldBankAdapter:
    """World Bank Indicators API v2 adapter.

    Official endpoint contract:
    https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
    """

    descriptor: ConnectorDescriptor = connector_descriptor("world-bank")

    def __init__(self, client: BoundedJsonClient) -> None:
        self._client = client

    async def fetch(self, query: WorldBankQuery) -> NormalizedPayload:
        url = (
            f"{self.descriptor.source.endpoint}/country/{query.country}/indicator/"
            f"{query.indicator}"
        )
        params: dict[str, str | int] = {
            "format": "json",
            "page": 1,
            "per_page": query.limit,
            "source": 2,
        }
        if query.start_year is not None and query.end_year is not None:
            params["date"] = f"{query.start_year}:{query.end_year}"
        else:
            params["mrnev"] = query.limit
        payload = await self._client.get_json(url, params=params)
        if (
            not isinstance(payload, list)
            or len(payload) < 2
            or not isinstance(payload[0], dict)
            or not isinstance(payload[1], list)
        ):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")

        metadata = payload[0]
        records: list[AuthorityRecord] = []
        for row in payload[1]:
            if not isinstance(row, dict):
                continue
            indicator = row.get("indicator")
            country = row.get("country")
            indicator_id = (
                _text(indicator.get("id"), maximum=64)
                if isinstance(indicator, dict)
                else None
            )
            if indicator_id != query.indicator:
                continue
            entity_id = _text(row.get("countryiso3code"), maximum=12)
            if entity_id and entity_id != query.country:
                continue
            record_period = _period(row.get("date"))
            value = _record_value(row.get("value"))
            if record_period is None or value is None:
                continue
            if query.start_year is not None and record_period.isdigit():
                year = int(record_period)
                if year < query.start_year or year > (query.end_year or year):
                    continue
            entity_name = (
                _text(country.get("value"), maximum=300)
                if isinstance(country, dict)
                else None
            )
            series_name = (
                _text(indicator.get("value"), maximum=500)
                if isinstance(indicator, dict)
                else None
            )
            metadata_fields: dict[str, RecordValue] = {
                "world_bank_source_id": _record_value(row.get("source")),
                "decimal_places": _record_value(row.get("decimal")),
                "observation_status": _record_value(row.get("obs_status")),
            }
            metadata_fields = {
                key: value
                for key, value in metadata_fields.items()
                if value is not None
            }
            records.append(
                AuthorityRecord(
                    record_id=(
                        f"world-bank:{entity_id or query.country}:"
                        f"{query.indicator}:{record_period}"
                    ),
                    series_id=query.indicator,
                    series_name=series_name,
                    entity_id=entity_id or query.country,
                    entity_name=entity_name,
                    period=record_period,
                    value=value,
                    metadata=metadata_fields,
                )
            )
            if len(records) >= query.limit:
                break

        cutoff = max(
            (record.period for record in records if record.period),
            key=_period_key,
            default=None,
        )
        upstream_total = _non_negative_int(metadata.get("total"))
        coverage = CoverageEvidence(
            state="partial",
            scope=(
                "World Development Indicators source 2; one validated country and "
                "indicator, bounded to the requested page."
            ),
            requested_dimensions={
                "country": [query.country],
                "indicator": [query.indicator],
                "years": (
                    [f"{query.start_year}:{query.end_year}"]
                    if query.start_year is not None
                    else [f"latest-non-empty:{query.limit}"]
                ),
            },
            returned_records=len(records),
            upstream_total=upstream_total,
            truncated=(upstream_total or 0) > len(records),
        )
        return NormalizedPayload(
            records=records,
            cutoff=cutoff,
            cutoff_kind="observation_period",
            coverage=coverage,
        )


class ImfAdapter:
    """IMF DataMapper API v2 adapter.

    Official endpoint contract: https://www.imf.org/external/datamapper/api/
    The response is filtered again locally because availability is not proof that
    the upstream honored every requested dimension.
    """

    descriptor: ConnectorDescriptor = connector_descriptor("imf")

    def __init__(self, client: BoundedJsonClient) -> None:
        self._client = client

    async def fetch(self, query: ImfQuery) -> NormalizedPayload:
        entity_path = "/".join(query.entities)
        url = f"{self.descriptor.source.endpoint}/{query.indicator}/{entity_path}"
        params: dict[str, str] = {}
        if query.periods:
            params["periods"] = ",".join(str(period) for period in query.periods)
        payload = await self._client.get_json(url, params=params)
        if not isinstance(payload, dict):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")
        api_metadata = payload.get("api")
        values = payload.get("values")
        if (
            not isinstance(api_metadata, dict)
            or str(api_metadata.get("version")) != "2"
            or not isinstance(values, dict)
        ):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")
        series_values = values.get(query.indicator)
        if not isinstance(series_values, dict):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")

        indicator_metadata = payload.get("indicators")
        raw_indicator = (
            indicator_metadata.get(query.indicator)
            if isinstance(indicator_metadata, dict)
            else None
        )
        series_name = None
        unit = None
        if isinstance(raw_indicator, dict):
            series_name = _text(
                raw_indicator.get("label") or raw_indicator.get("name"),
                maximum=500,
            )
            unit = _text(raw_indicator.get("unit"), maximum=120)

        all_records: list[AuthorityRecord] = []
        period_filter = {str(period) for period in query.periods}
        entity_order = {entity: index for index, entity in enumerate(query.entities)}
        for entity in query.entities:
            entity_values = series_values.get(entity)
            if not isinstance(entity_values, dict):
                continue
            for raw_period, raw_value in entity_values.items():
                record_period = _period(raw_period)
                value = _record_value(raw_value)
                if record_period is None or value is None:
                    continue
                if period_filter and record_period not in period_filter:
                    continue
                all_records.append(
                    AuthorityRecord(
                        record_id=(
                            f"imf:{query.indicator}:{entity}:{record_period}"
                        ),
                        series_id=query.indicator,
                        series_name=series_name,
                        entity_id=entity,
                        period=record_period,
                        value=value,
                        unit=unit,
                        metadata={"estimate_status": "unknown"},
                    )
                )
        all_records.sort(
            key=lambda record: (
                entity_order.get(record.entity_id or "", len(entity_order)),
                -_period_key(record.period or "")[0],
                record.period or "",
            )
        )
        records = all_records[: query.limit]
        cutoff = max(
            (record.period for record in records if record.period),
            key=_period_key,
            default=None,
        )
        coverage = CoverageEvidence(
            state="partial",
            scope=(
                "Only explicitly requested DataMapper entities and periods are "
                "retained, with a hard normalized-record cap."
            ),
            requested_dimensions={
                "indicator": [query.indicator],
                "entities": list(query.entities),
                "periods": [str(period) for period in query.periods] or ["unspecified"],
            },
            returned_records=len(records),
            upstream_total=len(all_records),
            truncated=len(all_records) > len(records),
        )
        return NormalizedPayload(
            records=records,
            cutoff=cutoff,
            cutoff_kind="data_period",
            coverage=coverage,
        )


class UnSdgAdapter:
    """UNSD SDG API v1 paginated observation adapter.

    Official Swagger endpoint: https://unstats.un.org/SDGAPI/swagger/
    The operation used is GET /v1/sdg/Series/Data with page and pageSize.
    """

    descriptor: ConnectorDescriptor = connector_descriptor("un-sdg")

    def __init__(self, client: BoundedJsonClient) -> None:
        self._client = client

    async def fetch(self, query: UnSdgQuery) -> NormalizedPayload:
        url = f"{self.descriptor.source.endpoint}/Series/Data"
        params: list[tuple[str, str | int | float]] = [
            ("seriesCode", query.series_code),
            ("areaCode", query.area_code),
            ("page", 1),
            ("pageSize", query.limit),
        ]
        if query.start_year is not None and query.end_year is not None:
            params.extend(
                [
                    ("timePeriodStart", query.start_year),
                    ("timePeriodEnd", query.end_year),
                ]
            )
        payload = await self._client.get_json(url, params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")

        records: list[AuthorityRecord] = []
        requested_area = str(query.area_code)
        for row in payload["data"]:
            if not isinstance(row, dict):
                continue
            series_id = _text(row.get("series"), maximum=80)
            entity_id = _text(row.get("geoAreaCode"), maximum=12)
            if series_id != query.series_code or entity_id != requested_area:
                continue
            record_period = _period(row.get("timePeriodStart"))
            value = _record_value(row.get("value"))
            if record_period is None or value is None:
                continue
            if query.start_year is not None:
                year = _period_key(record_period)[0]
                if year < query.start_year or year > (query.end_year or year):
                    continue
            metadata_fields: dict[str, RecordValue] = {
                "source_label": _record_value(row.get("source")),
                "value_type": _record_value(row.get("valueType")),
                "time_coverage": _record_value(row.get("timeCoverage")),
                "lower_bound": _record_value(row.get("lowerBound")),
                "upper_bound": _record_value(row.get("upperBound")),
                "base_period": _record_value(row.get("basePeriod")),
            }
            metadata_fields = {
                key: value
                for key, value in metadata_fields.items()
                if value is not None
            }
            metadata_fields.update(
                _safe_metadata(
                    row.get("dimensions"),
                    prefix="dimension.",
                    maximum_items=12,
                )
            )
            remaining = max(0, 24 - len(metadata_fields))
            metadata_fields.update(
                _safe_metadata(
                    row.get("attributes"),
                    prefix="attribute.",
                    maximum_items=remaining,
                )
            )
            suffix = _stable_suffix(
                {
                    "series": series_id,
                    "area": entity_id,
                    "period": record_period,
                    "dimensions": row.get("dimensions"),
                    "attributes": row.get("attributes"),
                }
            )
            records.append(
                AuthorityRecord(
                    record_id=f"un-sdg:{series_id}:{entity_id}:{record_period}:{suffix}",
                    series_id=series_id,
                    series_name=_text(row.get("seriesDescription"), maximum=500),
                    entity_id=entity_id,
                    entity_name=_text(row.get("geoAreaName"), maximum=300),
                    period=record_period,
                    value=value,
                    metadata=metadata_fields,
                )
            )
            if len(records) >= query.limit:
                break

        cutoff = max(
            (record.period for record in records if record.period),
            key=_period_key,
            default=None,
        )
        upstream_total = _non_negative_int(payload.get("totalElements"))
        coverage = CoverageEvidence(
            state="partial",
            scope=(
                "One UNSD SDG series and one M49 area, first page only, with an "
                "explicit page-size cap."
            ),
            requested_dimensions={
                "series": [query.series_code],
                "area_m49": [requested_area],
                "years": (
                    [f"{query.start_year}:{query.end_year}"]
                    if query.start_year is not None
                    else ["unspecified"]
                ),
            },
            returned_records=len(records),
            upstream_total=upstream_total,
            truncated=(upstream_total or 0) > len(records),
        )
        return NormalizedPayload(
            records=records,
            cutoff=cutoff,
            cutoff_kind="observation_period",
            coverage=coverage,
        )


def _crossref_publication_period(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    date_parts = raw.get("date-parts")
    if (
        not isinstance(date_parts, list)
        or not date_parts
        or not isinstance(date_parts[0], list)
        or not date_parts[0]
    ):
        return None
    parts: list[int] = []
    for value in date_parts[0][:3]:
        parsed = _non_negative_int(value)
        if parsed is None:
            return None
        parts.append(parsed)
    if not parts or not 1000 <= parts[0] <= 9999:
        return None
    if len(parts) == 1:
        return f"{parts[0]:04d}"
    if not 1 <= parts[1] <= 12:
        return None
    if len(parts) == 2:
        return f"{parts[0]:04d}-{parts[1]:02d}"
    if not 1 <= parts[2] <= 31:
        return None
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"


class CrossrefAdapter:
    """Crossref REST API v1 works adapter.

    Official docs: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
    Abstracts, references, resource links, and full-text links are never normalized.
    """

    descriptor: ConnectorDescriptor = connector_descriptor("crossref")

    def __init__(self, client: BoundedJsonClient) -> None:
        self._client = client

    async def fetch(self, query: CrossrefQuery) -> NormalizedPayload:
        url = f"{self.descriptor.source.endpoint}/works"
        params: dict[str, str | int] = {
            "query.title": query.query,
            "rows": query.limit,
            "select": "DOI,title,type,publisher,published,indexed,created",
        }
        if query.from_index_date and query.until_index_date:
            params["filter"] = (
                f"from-index-date:{query.from_index_date.isoformat()},"
                f"until-index-date:{query.until_index_date.isoformat()}"
            )
        payload = await self._client.get_json(url, params=params)
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "ok"
            or not isinstance(payload.get("message"), dict)
        ):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")
        message = payload["message"]
        items = message.get("items")
        if not isinstance(items, list):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")

        records: list[AuthorityRecord] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            doi = _text(row.get("DOI"), maximum=200)
            if not doi:
                continue
            raw_title = row.get("title")
            title = (
                _text(raw_title[0], maximum=500)
                if isinstance(raw_title, list) and raw_title
                else _text(raw_title, maximum=500)
            )
            if not title:
                continue
            indexed = row.get("indexed")
            updated_at = (
                _datetime(indexed.get("date-time"))
                if isinstance(indexed, dict)
                else None
            )
            record_period = _crossref_publication_period(row.get("published"))
            records.append(
                AuthorityRecord(
                    record_id=f"crossref:{doi.lower()}",
                    series_id="crossref.works",
                    series_name=_text(row.get("type"), maximum=120),
                    entity_id=doi.lower(),
                    entity_name=_text(row.get("publisher"), maximum=300),
                    period=record_period,
                    value=title,
                    updated_at=updated_at,
                    metadata={
                        "abstract_included": False,
                        "references_included": False,
                        "full_text_links_included": False,
                    },
                )
            )
            if len(records) >= query.limit:
                break

        updated_values = [
            record.updated_at
            for record in records
            if record.updated_at is not None
        ]
        if updated_values:
            cutoff = max(updated_values).isoformat().replace("+00:00", "Z")
            cutoff_kind: CutoffKind = "source_update_time"
        else:
            cutoff = max(
                (record.period for record in records if record.period),
                key=_period_key,
                default=None,
            )
            cutoff_kind = "publication_time" if cutoff else "unknown"
        upstream_total = _non_negative_int(message.get("total-results"))
        requested_dimensions = {
            "title_query_sha256": [
                hashlib.sha256(query.query.encode("utf-8")).hexdigest()
            ],
            "title_query_length": [str(len(query.query))],
        }
        if query.from_index_date and query.until_index_date:
            requested_dimensions["index_date"] = [
                f"{query.from_index_date.isoformat()}:{query.until_index_date.isoformat()}"
            ]
        coverage = CoverageEvidence(
            state="partial",
            scope=(
                "Crossref /works title query, first bounded page; only DOI, title, "
                "type, publisher, publication date, and index timestamps are retained."
            ),
            requested_dimensions=requested_dimensions,
            returned_records=len(records),
            upstream_total=upstream_total,
            truncated=(upstream_total or 0) > len(records),
        )
        return NormalizedPayload(
            records=records,
            cutoff=cutoff,
            cutoff_kind=cutoff_kind,
            coverage=coverage,
        )


__all__ = (
    "CrossrefAdapter",
    "ImfAdapter",
    "NormalizedPayload",
    "UnSdgAdapter",
    "WorldBankAdapter",
)
