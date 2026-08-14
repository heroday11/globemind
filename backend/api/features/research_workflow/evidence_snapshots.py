"""Read-only validation of research evidence against the evidence ledger."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Any, Protocol

from api.features.evidence import (
    SNAPSHOT_SCHEMA_VERSION,
    EvidenceLedgerNotFound,
    EvidenceLedgerUnavailable,
)


class EvidenceSnapshotReader(Protocol):
    """The workflow may read a ledger snapshot but can never capture one."""

    def snapshot(
        self, snapshot_id: str, *, include_body: bool = False
    ) -> dict[str, Any]: ...


class EvidenceSnapshotReferenceRejected(RuntimeError):
    """Supplied snapshot metadata does not identify one immutable ledger record."""


class EvidenceSnapshotVerificationUnavailable(RuntimeError):
    """The durable evidence ledger could not complete a read-only verification."""


def _normalized_timestamp(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceSnapshotReferenceRejected(
            "SNAPSHOT_CAPTURE_TIME_INVALID"
        ) from exc
    if parsed.tzinfo is None:
        raise EvidenceSnapshotReferenceRejected("SNAPSHOT_CAPTURE_TIME_INVALID")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_evidence_snapshot_reference(
    reader: EvidenceSnapshotReader | None,
    *,
    article_id: int,
    snapshot_id: str,
    content_sha256: str,
    captured_at: str,
    parser_version: str,
) -> dict[str, Any]:
    """Verify exact immutable metadata without reading the snapshot body."""
    if reader is None:
        raise EvidenceSnapshotVerificationUnavailable(
            "EVIDENCE_LEDGER_READER_UNAVAILABLE"
        )
    try:
        snapshot = reader.snapshot(snapshot_id, include_body=False)
    except EvidenceLedgerNotFound as exc:
        raise EvidenceSnapshotReferenceRejected("SNAPSHOT_NOT_FOUND") from exc
    except EvidenceLedgerUnavailable as exc:
        raise EvidenceSnapshotVerificationUnavailable(
            "EVIDENCE_LEDGER_UNAVAILABLE"
        ) from exc
    except ValueError as exc:
        raise EvidenceSnapshotReferenceRejected("SNAPSHOT_ID_INVALID") from exc
    except Exception as exc:
        raise EvidenceSnapshotVerificationUnavailable(
            "EVIDENCE_LEDGER_READ_FAILED"
        ) from exc

    checks = (
        (snapshot.get("schema_version") == SNAPSHOT_SCHEMA_VERSION, "SNAPSHOT_SCHEMA_MISMATCH"),
        (snapshot.get("snapshot_id") == snapshot_id, "SNAPSHOT_ID_MISMATCH"),
        (snapshot.get("article_id") == article_id, "SNAPSHOT_ARTICLE_MISMATCH"),
        (
            isinstance(snapshot.get("content_sha256"), str)
            and hmac.compare_digest(snapshot["content_sha256"], content_sha256),
            "SNAPSHOT_HASH_MISMATCH",
        ),
        (
            snapshot.get("parser_version") == parser_version,
            "SNAPSHOT_PARSER_VERSION_MISMATCH",
        ),
        (
            _normalized_timestamp(snapshot.get("first_captured_at"))
            == _normalized_timestamp(captured_at),
            "SNAPSHOT_CAPTURE_TIME_MISMATCH",
        ),
    )
    for matches, reason_code in checks:
        if not matches:
            raise EvidenceSnapshotReferenceRejected(reason_code)
    return {
        "article_id": article_id,
        "evidence_snapshot_id": snapshot_id,
        "content_sha256": content_sha256,
        "captured_at": _normalized_timestamp(captured_at),
        "parser_version": parser_version,
        "snapshot_status": "verified",
        "snapshot_reason": "EVIDENCE_LEDGER_REFERENCE_VERIFIED",
    }


__all__ = (
    "EvidenceSnapshotReader",
    "EvidenceSnapshotReferenceRejected",
    "EvidenceSnapshotVerificationUnavailable",
    "verify_evidence_snapshot_reference",
)
