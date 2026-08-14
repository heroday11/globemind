"""Deterministic, verifiable receipts for one dashboard-search execution.

A receipt proves which normalized contract and ordered result identifiers the
API returned. It is deliberately not a frozen corpus or article-body snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from api.features.search.entities import (
    ENTITY_ALIAS_CATALOG_REVIEW_STATUS,
    ENTITY_ALIAS_CATALOG_VERSION,
)
from api.models.schemas import (
    SearchQueryExplain,
    SearchQueryReceipt,
    SearchRequest,
    SearchResponse,
    SearchResultCoverage,
)

QUERY_RECEIPT_SCHEMA_VERSION = "search-query-receipt-v1"
QUERY_CONTRACT_SCHEMA_VERSION = "normalized-search-contract-v1"
QUERY_RECEIPT_METHOD_VERSION = "dashboard-search-v2+boolean-v1+receipt-v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID = re.compile(r"^qr-[0-9a-f]{64}$")


class QueryReceiptIntegrityError(ValueError):
    """A receipt cannot be verified against its own canonical material."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QueryReceiptIntegrityError("receipt material is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise QueryReceiptIntegrityError("receipt component is not an object")
    if not isinstance(payload, dict):
        raise QueryReceiptIntegrityError("receipt component is not an object")
    return payload


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    return raw or None


def _normalized_contract(
    params: SearchRequest,
    explain: SearchQueryExplain,
) -> dict[str, Any]:
    detail = _model_payload(explain)
    entity_resolution = [
        {
            "query_field": item.get("query_field"),
            "entity_id": item.get("entity_id"),
            "entity_type": item.get("entity_type"),
            "matched_alias": item.get("matched_alias"),
            "matched_alias_status": item.get("matched_alias_status"),
            "expanded_aliases": item.get("expanded_aliases") or [],
            "review_status": item.get("review_status"),
            "valid_from": item.get("valid_from"),
            "valid_to": item.get("valid_to"),
        }
        for item in detail.get("entity_expansions") or []
        if isinstance(item, dict)
    ]
    return {
        "schema_version": QUERY_CONTRACT_SCHEMA_VERSION,
        "query_language": detail.get("query_language"),
        "raw_query": detail.get("raw_query"),
        "query_ast": detail.get("query_ast"),
        "expanded_query_ast": detail.get("expanded_query_ast"),
        "execution_expression": detail.get("execution_expression"),
        "requested_mode": detail.get("requested_mode"),
        "effective_mode": detail.get("effective_mode"),
        "search_type": detail.get("search_type"),
        "requested_hit_location": detail.get("requested_hit_location"),
        "effective_search_fields": detail.get("effective_search_fields") or [],
        "time": detail.get("time") or {},
        "applied_filters": detail.get("applied_filters") or [],
        "entity_resolution": entity_resolution,
        "sort": {
            "requested_field": _optional_text(getattr(params, "sort_by", None)),
            "requested_order": _optional_text(getattr(params, "sort_order", None)) or "desc",
            "method_defined_tiebreakers": True,
        },
        "pagination": {
            "page": int(getattr(params, "page", 1) or 1),
            "page_size": int(getattr(params, "page_size", 10) or 10),
        },
        "cluster_scope": bool(getattr(params, "cluster_scope", False)),
        "alias_catalog": {
            "version": ENTITY_ALIAS_CATALOG_VERSION,
            "review_status": ENTITY_ALIAS_CATALOG_REVIEW_STATUS,
        },
    }


def _clean_result_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise QueryReceiptIntegrityError("returned result has no stable identifier")
    result = str(value).strip()
    if not result or len(result) > 512 or any(ord(char) < 32 for char in result):
        raise QueryReceiptIntegrityError("returned result identifier is invalid")
    return result


def _result_items(
    response: SearchResponse,
    search_type: str,
    mode: str,
) -> tuple[str, list[Any], str, str, str]:
    if search_type == "l1" or mode == "event_coref":
        clusters = list(response.event_coref_clusters or [])
        if clusters:
            return "l1_event", clusters, "cluster_id", "start_date", "end_date"
        micro = list(response.micro_story_items or [])
        return "l1_event", micro, "id", "", ""
    if search_type == "l2":
        return "l2_trend", list(response.macro_event_items or []), "id", "start_date", "end_date"
    if search_type == "l3":
        return "l3_macro", list(response.macro_event_items or []), "id", "start_date", "end_date"
    return (
        "news",
        list(response.data or []),
        "id",
        "time_semantics.published_at",
        "time_semantics.published_at",
    )


def _nested_attribute(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _canonical_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.isoformat()
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            try:
                return date.fromisoformat(raw).isoformat()
            except ValueError:
                return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(raw).isoformat()
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coverage(
    items: Iterable[Any],
    *,
    start_field: str,
    end_field: str,
    result_time_field: str,
) -> SearchResultCoverage:
    rows = list(items)
    starts: list[str] = []
    ends: list[str] = []
    timed = 0
    for item in rows:
        start = _canonical_time(_nested_attribute(item, start_field)) if start_field else None
        end = _canonical_time(_nested_attribute(item, end_field)) if end_field else None
        if start is not None or end is not None:
            timed += 1
            starts.append(start or end or "")
            ends.append(end or start or "")
    returned = len(rows)
    if timed == 0:
        status = "unavailable"
        note = "No returned item exposed the applicable time value; corpus coverage was not inferred."
    elif timed == returned:
        status = "available"
        note = "Coverage is computed only from time values on this returned page, not from the corpus."
    else:
        status = "partial"
        note = "Only part of this returned page exposed time values; corpus coverage was not inferred."
    return SearchResultCoverage(
        status=status,
        result_time_field=result_time_field,
        cutoff=max(ends) if ends else None,
        coverage_start=min(starts) if starts else None,
        coverage_end=max(ends) if ends else None,
        timed_result_count=timed,
        returned_result_count=returned,
        note=note,
    )


def _receipt_hash_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key != "receipt_sha256"
    }


def build_query_receipt(
    params: SearchRequest,
    response: SearchResponse,
    explain: SearchQueryExplain,
) -> SearchQueryReceipt:
    search_type = str(getattr(params, "search_type", "news") or "news").strip().lower()
    mode = str(getattr(params, "mode", "exact") or "exact").strip().lower()
    namespace, items, id_field, start_field, end_field = _result_items(
        response,
        search_type,
        mode,
    )
    ordered_ids = [_clean_result_id(getattr(item, id_field, None)) for item in items]
    if not ordered_ids:
        namespace = "none" if int(response.total or 0) == 0 else namespace
    contract = _normalized_contract(params, explain)
    contract_hash = canonical_sha256(contract)
    ids_hash = canonical_sha256({"namespace": namespace, "ids": ordered_ids})
    stable_material = {
        "method_version": QUERY_RECEIPT_METHOD_VERSION,
        "normalized_contract_sha256": contract_hash,
        "entity_catalog_version": ENTITY_ALIAS_CATALOG_VERSION,
        "ordered_returned_ids_sha256": ids_hash,
    }
    stable_key = canonical_sha256(stable_material)
    time_payload = _model_payload(explain.time)
    coverage = _coverage(
        items,
        start_field=start_field,
        end_field=end_field,
        result_time_field=str(time_payload.get("applied_field") or "unavailable"),
    )
    core = {
        "schema_version": QUERY_RECEIPT_SCHEMA_VERSION,
        "receipt_kind": "execution_receipt",
        "method_version": QUERY_RECEIPT_METHOD_VERSION,
        "receipt_id": f"qr-{stable_key}",
        "stable_execution_key": stable_key,
        "normalized_contract": contract,
        "normalized_contract_sha256": contract_hash,
        "entity_catalog_version": ENTITY_ALIAS_CATALOG_VERSION,
        "entity_catalog_review_status": ENTITY_ALIAS_CATALOG_REVIEW_STATUS,
        "time_field": {
            "requested": str(time_payload.get("requested_field") or "unavailable"),
            "applied": str(time_payload.get("applied_field") or "unavailable"),
        },
        "applied_filters": list(explain.applied_filters),
        "result_id_namespace": namespace,
        "ordered_returned_ids": ordered_ids,
        "ordered_returned_ids_sha256": ids_hash,
        "page": int(response.page),
        "page_size": int(response.page_size),
        "total": max(int(response.total or 0), 0),
        "result_coverage": coverage.model_dump(mode="json"),
        "snapshot_status": "not_frozen",
        "frozen_data_snapshot_id": None,
        "receipt_note": (
            "This is a deterministic execution receipt for the normalized query and returned IDs. "
            "It does not freeze article bodies, ranking inputs, or the underlying corpus."
        ),
    }
    core["receipt_sha256"] = canonical_sha256(core)
    return SearchQueryReceipt.model_validate(core)


def verify_query_receipt(value: SearchQueryReceipt | Mapping[str, Any]) -> dict[str, Any]:
    try:
        receipt = (
            value
            if isinstance(value, SearchQueryReceipt)
            else SearchQueryReceipt.model_validate(value)
        )
    except Exception as exc:
        raise QueryReceiptIntegrityError("query receipt contract is invalid") from exc
    payload = receipt.model_dump(mode="json")
    if payload["schema_version"] != QUERY_RECEIPT_SCHEMA_VERSION:
        raise QueryReceiptIntegrityError("query receipt schema is invalid")
    contract_hash = canonical_sha256(payload["normalized_contract"])
    ids_hash = canonical_sha256(
        {
            "namespace": payload["result_id_namespace"],
            "ids": payload["ordered_returned_ids"],
        }
    )
    stable_key = canonical_sha256(
        {
            "method_version": payload["method_version"],
            "normalized_contract_sha256": contract_hash,
            "entity_catalog_version": payload["entity_catalog_version"],
            "ordered_returned_ids_sha256": ids_hash,
        }
    )
    expected_receipt_hash = canonical_sha256(_receipt_hash_material(payload))
    contract = payload["normalized_contract"]
    pagination = contract.get("pagination") if isinstance(contract, dict) else None
    contract_time = contract.get("time") if isinstance(contract, dict) else None
    catalog = contract.get("alias_catalog") if isinstance(contract, dict) else None
    expected_namespace = {
        "news": "news",
        "l1": "l1_event",
        "l2": "l2_trend",
        "l3": "l3_macro",
    }.get(contract.get("search_type") if isinstance(contract, dict) else None)
    if isinstance(contract, dict) and contract.get("requested_mode") == "event_coref":
        expected_namespace = "l1_event"
    if payload["total"] == 0 and not payload["ordered_returned_ids"]:
        expected_namespace = "none"
    hashes = (
        payload["normalized_contract_sha256"],
        payload["ordered_returned_ids_sha256"],
        payload["stable_execution_key"],
        payload["receipt_sha256"],
    )
    if any(_HEX_64.fullmatch(str(item)) is None for item in hashes):
        raise QueryReceiptIntegrityError("query receipt hash format is invalid")
    if (
        payload["normalized_contract_sha256"] != contract_hash
        or payload["ordered_returned_ids_sha256"] != ids_hash
        or payload["stable_execution_key"] != stable_key
        or payload["receipt_id"] != f"qr-{stable_key}"
        or _RECEIPT_ID.fullmatch(payload["receipt_id"]) is None
        or payload["receipt_sha256"] != expected_receipt_hash
        or payload["method_version"] != QUERY_RECEIPT_METHOD_VERSION
        or not isinstance(contract, dict)
        or contract.get("schema_version") != QUERY_CONTRACT_SCHEMA_VERSION
        or not isinstance(pagination, dict)
        or pagination.get("page") != payload["page"]
        or pagination.get("page_size") != payload["page_size"]
        or not isinstance(contract_time, dict)
        or payload["time_field"]
        != {
            "requested": str(contract_time.get("requested_field") or "unavailable"),
            "applied": str(contract_time.get("applied_field") or "unavailable"),
        }
        or contract.get("applied_filters") != payload["applied_filters"]
        or not isinstance(catalog, dict)
        or catalog.get("version") != payload["entity_catalog_version"]
        or catalog.get("review_status") != payload["entity_catalog_review_status"]
        or expected_namespace != payload["result_id_namespace"]
        or payload["total"] < len(payload["ordered_returned_ids"])
        or payload["result_coverage"]["timed_result_count"]
        > payload["result_coverage"]["returned_result_count"]
        or payload["result_coverage"]["returned_result_count"]
        != len(payload["ordered_returned_ids"])
    ):
        raise QueryReceiptIntegrityError("query receipt integrity check failed")
    return payload


__all__ = (
    "QUERY_CONTRACT_SCHEMA_VERSION",
    "QUERY_RECEIPT_METHOD_VERSION",
    "QUERY_RECEIPT_SCHEMA_VERSION",
    "QueryReceiptIntegrityError",
    "build_query_receipt",
    "canonical_json_bytes",
    "canonical_sha256",
    "verify_query_receipt",
)
