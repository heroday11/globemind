"""Read-only evidence snapshot verification through the evidence facade."""

from __future__ import annotations

import hmac
import re
from typing import Any, Mapping, Protocol

from api.features.evidence import (
    SNAPSHOT_PARSER_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    EvidenceLedgerNotFound,
    EvidenceLedgerUnavailable,
)

from .contracts import EvidenceReference
from .errors import (
    EntityEvidenceReferenceRejected,
    EntityEvidenceVerificationBlocked,
    EntityGovernanceUnavailable,
)

VERIFIED_EVIDENCE_KEYS = frozenset(
    {
        "verification_status",
        "schema_version",
        "snapshot_id",
        "article_id",
        "content_sha256",
        "parser_version",
        "verification_scope",
        "source_metadata_verification",
        "body_persistence",
    }
)
_SNAPSHOT_ID = re.compile(
    r"^article-(?P<article_id>[1-9][0-9]*)-(?P<digest>[0-9a-f]{64})$"
)


class EvidenceSnapshotReader(Protocol):
    def snapshot(
        self,
        snapshot_id: str,
        *,
        include_body: bool = False,
    ) -> dict[str, Any]: ...


def validate_verified_evidence_metadata(evidence: Any) -> None:
    if not isinstance(evidence, Mapping) or set(evidence) != VERIFIED_EVIDENCE_KEYS:
        raise EntityGovernanceUnavailable(
            "ENTITY_GOVERNANCE_EVENT_EVIDENCE_INVALID"
        )
    article_id = evidence.get("article_id")
    snapshot_id = evidence.get("snapshot_id")
    digest = evidence.get("content_sha256")
    match = _SNAPSHOT_ID.fullmatch(snapshot_id) if isinstance(snapshot_id, str) else None
    if (
        evidence.get("verification_status") != "verified"
        or evidence.get("body_persistence") != "forbidden"
        or evidence.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or evidence.get("parser_version") != SNAPSHOT_PARSER_VERSION
        or evidence.get("verification_scope")
        != "normalized-body-content-address-and-reference-fields"
        or evidence.get("source_metadata_verification") != "not_measured"
        or isinstance(article_id, bool)
        or not isinstance(article_id, int)
        or article_id <= 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or match is None
        or int(match.group("article_id")) != article_id
        or match.group("digest") != digest
    ):
        raise EntityGovernanceUnavailable(
            "ENTITY_GOVERNANCE_EVENT_EVIDENCE_INVALID"
        )


def verify_evidence_reference(
    reader: EvidenceSnapshotReader | None,
    reference: EvidenceReference,
) -> dict[str, Any]:
    if reader is None:
        raise EntityEvidenceVerificationBlocked(
            "ENTITY_EVIDENCE_LEDGER_READER_UNAVAILABLE"
        )
    try:
        snapshot = reader.snapshot(reference.snapshot_id, include_body=False)
    except EvidenceLedgerNotFound as exc:
        raise EntityEvidenceReferenceRejected(
            "ENTITY_EVIDENCE_SNAPSHOT_NOT_FOUND"
        ) from exc
    except EvidenceLedgerUnavailable as exc:
        raise EntityEvidenceVerificationBlocked(
            "ENTITY_EVIDENCE_LEDGER_UNAVAILABLE"
        ) from exc
    except ValueError as exc:
        raise EntityEvidenceReferenceRejected(
            "ENTITY_EVIDENCE_SNAPSHOT_ID_INVALID"
        ) from exc
    except Exception as exc:
        raise EntityEvidenceVerificationBlocked(
            "ENTITY_EVIDENCE_LEDGER_READ_FAILED"
        ) from exc

    digest = snapshot.get("content_sha256")
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or snapshot.get("snapshot_id") != reference.snapshot_id
        or snapshot.get("article_id") != reference.article_id
        or not isinstance(digest, str)
        or not hmac.compare_digest(digest, reference.content_sha256)
        or snapshot.get("parser_version") != reference.parser_version
        or reference.parser_version != SNAPSHOT_PARSER_VERSION
        or snapshot.get("hash_scope") != "normalized-display-body"
        or "normalized_body" in snapshot
    ):
        raise EntityEvidenceReferenceRejected(
            "ENTITY_EVIDENCE_SNAPSHOT_REFERENCE_MISMATCH"
        )
    return {
        "verification_status": "verified",
        "schema_version": str(snapshot["schema_version"]),
        "snapshot_id": reference.snapshot_id,
        "article_id": reference.article_id,
        "content_sha256": reference.content_sha256,
        "parser_version": reference.parser_version,
        "verification_scope": "normalized-body-content-address-and-reference-fields",
        "source_metadata_verification": "not_measured",
        "body_persistence": "forbidden",
    }


__all__ = (
    "EvidenceSnapshotReader",
    "VERIFIED_EVIDENCE_KEYS",
    "validate_verified_evidence_metadata",
    "verify_evidence_reference",
)
