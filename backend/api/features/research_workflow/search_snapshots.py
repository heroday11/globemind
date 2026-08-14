"""Read-only validation of saved-search references against the search ledger."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from api.features.search import (
    QUERY_RECEIPT_SCHEMA_VERSION,
    SEARCH_SNAPSHOT_SCHEMA_VERSION,
    QueryReceiptIntegrityError,
    SearchSnapshotNotFound,
    SearchSnapshotUnavailable,
    verify_query_receipt,
)

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "snapshot_scope",
        "previous_snapshot_id",
        "previous_integrity_sha256",
        "captured_at",
        "actor_ref",
        "receipt",
        "receipt_sha256",
        "normalized_contract_sha256",
        "ordered_returned_ids_sha256",
        "corpus_snapshot_status",
        "body_persistence",
        "integrity_sha256",
    }
)


class SearchSnapshotReader(Protocol):
    """Research may read one actor-scoped record but can never capture it."""

    def get(self, actor_id: int, snapshot_id: str) -> dict[str, Any]: ...


class SearchSnapshotReferenceRejected(RuntimeError):
    """Submitted metadata does not identify the actor's immutable record."""


class SearchSnapshotVerificationUnavailable(RuntimeError):
    """The durable search ledger could not complete strict verification."""


def _same_digest(actual: Any, expected: str) -> bool:
    return (
        isinstance(actual, str)
        and _HEX_64.fullmatch(actual) is not None
        and hmac.compare_digest(actual, expected)
    )


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SearchSnapshotReferenceRejected(
            "SEARCH_SNAPSHOT_INTEGRITY_MATERIAL_INVALID"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _normalized_timestamp(value: Any) -> str:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SearchSnapshotReferenceRejected(
            "SEARCH_SNAPSHOT_CAPTURE_TIME_INVALID"
        ) from exc
    if parsed.tzinfo is None:
        raise SearchSnapshotReferenceRejected(
            "SEARCH_SNAPSHOT_CAPTURE_TIME_INVALID"
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_search_snapshot_reference(
    reader: SearchSnapshotReader | None,
    *,
    actor_id: int,
    snapshot_id: str,
    query_receipt_sha256: str,
    normalized_contract_sha256: str,
    ordered_returned_ids_sha256: str,
    declared_query: str,
) -> dict[str, Any]:
    """Verify one captured receipt without writing or replaying its query."""
    if reader is None:
        raise SearchSnapshotVerificationUnavailable(
            "SEARCH_SNAPSHOT_LEDGER_READER_UNAVAILABLE"
        )
    try:
        snapshot = reader.get(actor_id, snapshot_id)
    except SearchSnapshotNotFound as exc:
        # Actor-scoped not-found deliberately covers both missing and cross-user IDs.
        raise SearchSnapshotReferenceRejected("SEARCH_SNAPSHOT_NOT_FOUND") from exc
    except SearchSnapshotUnavailable as exc:
        raise SearchSnapshotVerificationUnavailable(
            "SEARCH_SNAPSHOT_LEDGER_UNAVAILABLE"
        ) from exc
    except ValueError as exc:
        raise SearchSnapshotReferenceRejected("SEARCH_SNAPSHOT_ID_INVALID") from exc
    except Exception as exc:
        raise SearchSnapshotVerificationUnavailable(
            "SEARCH_SNAPSHOT_LEDGER_READ_FAILED"
        ) from exc

    receipt_value = snapshot.get("receipt")
    try:
        receipt = verify_query_receipt(
            receipt_value if isinstance(receipt_value, dict) else {}
        )
    except QueryReceiptIntegrityError as exc:
        raise SearchSnapshotReferenceRejected(
            "SEARCH_QUERY_RECEIPT_INVALID"
        ) from exc

    contract = receipt.get("normalized_contract")
    coverage = receipt.get("result_coverage")
    if not isinstance(contract, dict) or not isinstance(coverage, dict):
        raise SearchSnapshotReferenceRejected("SEARCH_QUERY_RECEIPT_INVALID")

    integrity_material = {
        key: value for key, value in snapshot.items() if key != "integrity_sha256"
    }
    checks = (
        (
            set(snapshot) == _SNAPSHOT_RECORD_KEYS,
            "SEARCH_SNAPSHOT_CONTRACT_MISMATCH",
        ),
        (
            snapshot.get("schema_version") == SEARCH_SNAPSHOT_SCHEMA_VERSION,
            "SEARCH_SNAPSHOT_SCHEMA_MISMATCH",
        ),
        (snapshot.get("snapshot_id") == snapshot_id, "SEARCH_SNAPSHOT_ID_MISMATCH"),
        (
            snapshot.get("actor_ref") == f"user:{actor_id}",
            "SEARCH_SNAPSHOT_ACTOR_MISMATCH",
        ),
        (
            snapshot.get("snapshot_scope")
            == "query-contract-and-ordered-result-ids",
            "SEARCH_SNAPSHOT_SCOPE_MISMATCH",
        ),
        (
            snapshot.get("corpus_snapshot_status") == "not_frozen"
            and snapshot.get("body_persistence") == "forbidden",
            "SEARCH_SNAPSHOT_BOUNDARY_MISMATCH",
        ),
        (
            receipt.get("schema_version") == QUERY_RECEIPT_SCHEMA_VERSION,
            "SEARCH_QUERY_RECEIPT_SCHEMA_MISMATCH",
        ),
        (
            _same_digest(receipt.get("receipt_sha256"), query_receipt_sha256)
            and _same_digest(snapshot.get("receipt_sha256"), query_receipt_sha256),
            "SEARCH_QUERY_RECEIPT_HASH_MISMATCH",
        ),
        (
            _same_digest(
                receipt.get("normalized_contract_sha256"),
                normalized_contract_sha256,
            )
            and _same_digest(
                snapshot.get("normalized_contract_sha256"),
                normalized_contract_sha256,
            ),
            "SEARCH_NORMALIZED_CONTRACT_HASH_MISMATCH",
        ),
        (
            _same_digest(
                receipt.get("ordered_returned_ids_sha256"),
                ordered_returned_ids_sha256,
            )
            and _same_digest(
                snapshot.get("ordered_returned_ids_sha256"),
                ordered_returned_ids_sha256,
            ),
            "SEARCH_ORDERED_RESULT_IDS_HASH_MISMATCH",
        ),
        (
            str(contract.get("raw_query") or "").strip()
            == str(declared_query or "").strip(),
            "SEARCH_SNAPSHOT_QUERY_TEXT_MISMATCH",
        ),
        (
            _same_digest(
                snapshot.get("integrity_sha256"),
                _canonical_sha256(integrity_material),
            ),
            "SEARCH_SNAPSHOT_INTEGRITY_HASH_MISMATCH",
        ),
    )
    for matches, reason_code in checks:
        if not matches:
            raise SearchSnapshotReferenceRejected(reason_code)

    return {
        "snapshot_status": "verified",
        "snapshot_reason": "SEARCH_SNAPSHOT_REFERENCE_VERIFIED",
        "search_snapshot_id": snapshot_id,
        "query_receipt_sha256": query_receipt_sha256,
        "normalized_contract_sha256": normalized_contract_sha256,
        "ordered_returned_ids_sha256": ordered_returned_ids_sha256,
        "snapshot_integrity_sha256": snapshot["integrity_sha256"],
        "snapshot_captured_at": _normalized_timestamp(snapshot.get("captured_at")),
        "receipt_method_version": str(receipt.get("method_version") or ""),
        "entity_catalog_version": str(receipt.get("entity_catalog_version") or ""),
        "entity_catalog_review_status": str(
            receipt.get("entity_catalog_review_status") or "review_required"
        ),
        "result_id_namespace": str(receipt.get("result_id_namespace") or "none"),
        "returned_result_count": int(coverage.get("returned_result_count") or 0),
        "result_page": int(receipt.get("page") or 1),
        "result_total": int(receipt.get("total") or 0),
        "result_cutoff": coverage.get("cutoff"),
        "result_coverage_start": coverage.get("coverage_start"),
        "result_coverage_end": coverage.get("coverage_end"),
        "result_coverage_status": str(coverage.get("status") or "unavailable"),
    }


__all__ = (
    "SearchSnapshotReader",
    "SearchSnapshotReferenceRejected",
    "SearchSnapshotVerificationUnavailable",
    "verify_search_snapshot_reference",
)
