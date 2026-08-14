from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.features.entity_governance import (
    AliasReviewRequest,
    EntityDecisionRequest,
    EntityEvidenceReferenceRejected,
    EntityEvidenceVerificationBlocked,
    EntityGovernanceAccessDenied,
    EntityGovernanceConflict,
    EntityGovernanceLedger,
    EntityGovernanceService,
    EvidenceReference,
    MergeDecisionRequest,
    RelationAddRequest,
    RelationRetractRequest,
    SplitDecisionRequest,
    load_search_seed_entities,
)
from api.features.evidence import (
    SNAPSHOT_PARSER_VERSION,
    EvidenceSnapshotLedger,
)


CN = "urn:globemind:entity:country:CN"
US = "urn:globemind:entity:country:US"
RU = "urn:globemind:entity:country:RU"
JP = "urn:globemind:entity:country:JP"
KR = "urn:globemind:entity:country:KR"
KP = "urn:globemind:entity:country:KP"
ADMIN = {"user_id": 7, "role": "admin"}
USER = {"user_id": 8, "role": "user"}


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(microseconds=1)
        return value


def _evidence_reference(root: Path) -> tuple[EvidenceSnapshotLedger, EvidenceReference]:
    ledger = EvidenceSnapshotLedger(root)
    captured = ledger.capture(
        article_id=17,
        title="Governance source",
        body="A source paragraph with enough information for human review.",
        source_url="https://example.test/source?secret=removed",
        actor_id=7,
        reason="Capture the source before entity adjudication",
        change_type="initial",
        captured_at=datetime(2026, 8, 9, 7, 0, tzinfo=timezone.utc),
    )
    snapshot = ledger.snapshot(captured["snapshot_id"], include_body=False)
    return ledger, EvidenceReference(
        article_id=17,
        snapshot_id=snapshot["snapshot_id"],
        content_sha256=snapshot["content_sha256"],
        parser_version=SNAPSHOT_PARSER_VERSION,
    )


def _service(
    root: Path,
    evidence_reader: object | None,
    *,
    identifiers: list[str] | None = None,
) -> EntityGovernanceService:
    values = iter(identifiers or [f"{index:032x}" for index in range(1, 30)])
    return EntityGovernanceService(
        EntityGovernanceLedger(
            root,
            b"entity-governance-test-hmac-key-0001",
            clock=AdvancingClock(),
            nonce_factory=lambda: "a" * 16,
        ),
        load_search_seed_entities(),
        evidence_reader=evidence_reader,
        id_factory=lambda: next(values),
    )


def _entity_request(
    evidence: EvidenceReference,
    previous: str | None,
    *,
    decision: str = "approve",
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> EntityDecisionRequest:
    return EntityDecisionRequest(
        expected_previous_event_id=previous,
        reason=f"Human decision to {decision} this stable entity record",
        evidence=evidence,
        decision=decision,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _approve(
    service: EntityGovernanceService,
    entity_id: str,
    evidence: EvidenceReference,
    previous: str | None,
) -> str:
    result = service.decide_entity(
        entity_id,
        _entity_request(evidence, previous),
        ADMIN,
    )
    return result["event"]["event_id"]


def test_empty_read_model_is_honest_and_performs_zero_writes(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    service = _service(root, None)

    status = service.status(USER)
    catalog = service.catalog(USER)
    entity = service.entity(CN, USER)
    relations = service.relations(USER)
    history = service.history(USER)

    assert not root.exists()
    assert status == {
        "schema_version": "entity-governance-status-v2",
        "storage_status": "available",
        "reason": None,
        "root_initialized": False,
        "event_count": 0,
        "latest_event_id": None,
        "integrity_status": "verified",
        "mutation_status": "blocked",
        "mutation_blocker": "ENTITY_EVIDENCE_LEDGER_READER_UNAVAILABLE",
        "chain": "sha256-and-hmac-sha256",
        "append_semantics": "no-replace-local-filesystem",
        "hmac_key_id": "unavailable",
        "hmac_key_rotation": "offline-controlled-migration-not-implemented",
        "worm_status": "unavailable",
        "digital_signature_status": "unavailable",
        "institutional_directory_integration": "unavailable",
        "accuracy_claim": "not_measured",
        "seed_review_default": "review_required",
        "evidence_policy": "verified-evidence-snapshot-required-for-mutations",
        "review_expiry_policy": "not_configured",
    }
    assert catalog["approved_entities"] == []
    assert catalog["rejected_entity_ids"] == []
    assert len(catalog["review_required_entities"]) == len(
        load_search_seed_entities()
    )
    assert {item["review_status"] for item in catalog["review_required_entities"]} == {
        "review_required"
    }
    assert entity["governance_review_status"] == "review_required"
    assert entity["active_projection"] is None
    assert relations["items"] == []
    assert history["event_count"] == 0
    assert history["semantic_projection_verified"] is True


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"id": 7, "role": "admin"},
        {"user_id": True, "role": "admin"},
        {"user_id": "7", "role": "admin"},
        {"user_id": 0, "role": "admin"},
    ],
)
def test_only_canonical_positive_integer_user_id_is_accepted(
    tmp_path: Path,
    identity: dict[str, object],
) -> None:
    service = _service(tmp_path / "governance", None)

    with pytest.raises(
        EntityGovernanceAccessDenied,
        match="ENTITY_GOVERNANCE_CANONICAL_USER_ID_REQUIRED",
    ):
        service.status(identity)

    assert not (tmp_path / "governance").exists()


def test_all_mutations_require_admin_without_initializing_storage(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    _, evidence = _evidence_reference(tmp_path / "evidence")
    service = _service(root, None)

    with pytest.raises(
        EntityGovernanceAccessDenied,
        match="ENTITY_GOVERNANCE_ADMIN_REQUIRED",
    ):
        service.decide_entity(CN, _entity_request(evidence, None), USER)

    assert not root.exists()


def test_approved_entity_alias_relation_and_retraction_lifecycle(tmp_path: Path) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    service = _service(tmp_path / "governance", evidence_reader)

    assert service.status(USER)["mutation_status"] == "ready"
    assert service.status(USER)["mutation_blocker"] is None

    previous = service.decide_entity(
        CN,
        _entity_request(evidence, None, valid_from="1949-10-01"),
        ADMIN,
    )["event"]["event_id"]
    previous = service.decide_entity(
        US,
        _entity_request(evidence, previous, valid_from="1776-07-04"),
        ADMIN,
    )["event"]["event_id"]
    alias_result = service.review_alias(
        AliasReviewRequest(
            expected_previous_event_id=previous,
            reason="Human review confirms this English abbreviation",
            evidence=evidence,
            entity_id=CN,
            alias="PRC",
            language="en",
            decision="approve",
            context_dependent=False,
            valid_from="1949-10-01",
            valid_to=None,
        ),
        ADMIN,
    )
    previous = alias_result["event"]["event_id"]
    relation_result = service.add_relation(
        RelationAddRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer accepts the sourced temporal relationship",
            evidence=evidence,
            subject_id=CN,
            predicate="urn:globemind:predicate:diplomatic-peer",
            object_id=US,
            valid_from="1979-01-01",
            valid_to=None,
        ),
        ADMIN,
    )
    relation_id = relation_result["event"]["payload"]["relation_id"]
    previous = relation_result["event"]["event_id"]

    catalog = service.catalog(USER)
    entity = service.entity(CN, USER)
    relations = service.relations(USER)

    assert {item["entity_id"] for item in catalog["approved_entities"]} == {CN, US}
    assert entity["active_projection"]["valid_from"] == "1949-10-01"
    approved_alias = next(
        item
        for item in entity["active_projection"]["approved_aliases"]
        if item["value"] == "PRC"
    )
    assert approved_alias["language"] == "en"
    assert approved_alias["context_dependent"] is False
    assert approved_alias["evidence"]["body_persistence"] == "forbidden"
    assert approved_alias["evidence"]["verification_scope"] == (
        "normalized-body-content-address-and-reference-fields"
    )
    assert approved_alias["evidence"]["source_metadata_verification"] == "not_measured"
    assert relations["relation_count"] == 1
    assert relations["items"][0]["relation_id"] == relation_id
    assert relations["items"][0]["valid_from"] == "1979-01-01"
    assert catalog["review_expiry_policy"] == "not_configured"
    assert entity["review_expiry_policy"] == "not_configured"
    assert relations["review_expiry_policy"] == "not_configured"

    retracted = service.retract_relation(
        relation_id,
        RelationRetractRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer retracts the relationship from the active projection",
            evidence=evidence,
        ),
        ADMIN,
    )

    assert service.relations(USER)["items"] == []
    history = service.history(USER)
    assert history["event_count"] == 5
    assert history["latest_event_id"] == retracted["event"]["event_id"]
    assert history["items"][0]["event_type"] == "relation.retracted"
    assert history["visibility"] == "authenticated-users"
    assert history["reason_visibility"] == "authenticated-users"
    assert history["actor_reference_semantics"] == (
        "local-canonical-user-id-not-directory-resolved"
    )
    assert all(item["actor_ref"] == "user:7" for item in history["items"])
    assert all("normalized_body" not in item["evidence"] for item in history["items"])


def test_rejection_removes_entity_and_incident_relations_from_active_projection(
    tmp_path: Path,
) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    service = _service(tmp_path / "governance", evidence_reader)
    previous = _approve(service, CN, evidence, None)
    previous = _approve(service, US, evidence, previous)
    relation = service.add_relation(
        RelationAddRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer accepts a bounded relation",
            evidence=evidence,
            subject_id=CN,
            predicate="urn:globemind:predicate:related-to",
            object_id=US,
            valid_from=None,
            valid_to=None,
        ),
        ADMIN,
    )
    previous = relation["event"]["event_id"]
    service.decide_entity(
        CN,
        _entity_request(evidence, previous, decision="reject"),
        ADMIN,
    )

    catalog = service.catalog(USER)
    assert CN in catalog["rejected_entity_ids"]
    assert CN not in {item["entity_id"] for item in catalog["approved_entities"]}
    assert service.entity(CN, USER)["active_projection"] is None
    assert service.entity(CN, USER)["governance_decision"]["decision"] == "reject"
    assert service.entity(CN, USER)["governance_decision"]["actor_ref"] == "user:7"
    assert service.relations(USER)["items"] == []
    assert service.history(USER)["event_count"] == 4


def test_merge_chain_cycle_and_non_destructive_split_decisions(tmp_path: Path) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    service = _service(tmp_path / "governance", evidence_reader)
    previous = None
    for entity_id in (CN, US, JP, RU, KR, KP):
        previous = _approve(service, entity_id, evidence, previous)

    first_merge = service.decide_merge(
        MergeDecisionRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer links the source identity to a canonical target",
            evidence=evidence,
            source_entity_id=CN,
            target_entity_id=US,
        ),
        ADMIN,
    )
    previous = first_merge["event"]["event_id"]

    with pytest.raises(EntityGovernanceConflict, match="ENTITY_GOVERNANCE_MERGE_CYCLE"):
        service.decide_merge(
            MergeDecisionRequest(
                expected_previous_event_id=previous,
                reason="This reverse decision would create a cycle",
                evidence=evidence,
                source_entity_id=US,
                target_entity_id=CN,
            ),
            ADMIN,
        )

    second_merge = service.decide_merge(
        MergeDecisionRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer extends the acyclic canonical identity chain",
            evidence=evidence,
            source_entity_id=US,
            target_entity_id=JP,
        ),
        ADMIN,
    )
    previous = second_merge["event"]["event_id"]
    split = service.decide_split(
        SplitDecisionRequest(
            expected_previous_event_id=previous,
            reason="Human reviewer records a non-destructive identity split",
            evidence=evidence,
            source_entity_id=RU,
            resulting_entity_ids=[KR, KP],
        ),
        ADMIN,
    )
    previous = split["event"]["event_id"]

    assert service.entity(CN, USER)["active_projection"]["canonical_entity_id"] == JP
    assert service.entity(RU, USER)["active_projection"]["split_into_entity_ids"] == [
        KR,
        KP,
    ]
    assert {item["entity_id"] for item in service.catalog(USER)["approved_entities"]} == {
        CN,
        US,
        JP,
        RU,
        KR,
        KP,
    }

    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_IDENTITY_DECISION_CONFLICT",
    ):
        service.decide_split(
            SplitDecisionRequest(
                expected_previous_event_id=previous,
                reason="This split overlaps an existing merge participant",
                evidence=evidence,
                source_entity_id=JP,
                resulting_entity_ids=[KR, KP],
            ),
            ADMIN,
        )

    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_SPLIT_UNKNOWN_ENTITY",
    ):
        service.decide_split(
            SplitDecisionRequest(
                expected_previous_event_id=previous,
                reason="This split references an unknown stable identifier",
                evidence=evidence,
                source_entity_id="urn:globemind:entity:country:ZZ",
                resulting_entity_ids=[KR, KP],
            ),
            ADMIN,
        )

    assert service.history(USER)["event_count"] == 9


def test_unknown_unapproved_duplicate_and_stale_relations_fail_closed(
    tmp_path: Path,
) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    service = _service(tmp_path / "governance", evidence_reader)
    request = RelationAddRequest(
        expected_previous_event_id=None,
        reason="Human reviewer proposes a relationship",
        evidence=evidence,
        subject_id=CN,
        predicate="urn:globemind:predicate:related-to",
        object_id=US,
        valid_from=None,
        valid_to=None,
    )
    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_RELATION_ENTITY_NOT_APPROVED",
    ):
        service.add_relation(request, ADMIN)

    previous = _approve(service, CN, evidence, None)
    previous = _approve(service, US, evidence, previous)
    added = service.add_relation(
        request.model_copy(update={"expected_previous_event_id": previous}),
        ADMIN,
    )
    latest = added["event"]["event_id"]
    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_DUPLICATE_ACTIVE_RELATION",
    ):
        service.add_relation(
            request.model_copy(update={"expected_previous_event_id": latest}),
            ADMIN,
        )
    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED",
    ):
        service.decide_entity(JP, _entity_request(evidence, previous), ADMIN)
    assert service.history(USER)["event_count"] == 3


def test_missing_or_mismatched_evidence_never_initializes_governance_root(
    tmp_path: Path,
) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    missing_root = tmp_path / "missing-reader-governance"
    missing = _service(missing_root, None)
    with pytest.raises(
        EntityEvidenceVerificationBlocked,
        match="ENTITY_EVIDENCE_LEDGER_READER_UNAVAILABLE",
    ):
        missing.decide_entity(CN, _entity_request(evidence, None), ADMIN)
    assert not missing_root.exists()

    mismatch_root = tmp_path / "mismatch-governance"
    mismatch = _service(mismatch_root, evidence_reader)
    wrong = evidence.model_copy(update={"content_sha256": "f" * 64})
    with pytest.raises(
        EntityEvidenceReferenceRejected,
        match="ENTITY_EVIDENCE_SNAPSHOT_REFERENCE_MISMATCH",
    ):
        mismatch.decide_entity(CN, _entity_request(wrong, None), ADMIN)
    assert not mismatch_root.exists()


def test_unknown_entity_and_alias_do_not_create_records(tmp_path: Path) -> None:
    evidence_reader, evidence = _evidence_reference(tmp_path / "evidence")
    root = tmp_path / "governance"
    service = _service(root, evidence_reader)

    with pytest.raises(EntityGovernanceConflict, match="ENTITY_GOVERNANCE_UNKNOWN_ENTITY"):
        service.decide_entity(
            "urn:globemind:entity:country:ZZ",
            _entity_request(evidence, None),
            ADMIN,
        )
    with pytest.raises(EntityGovernanceConflict, match="ENTITY_GOVERNANCE_UNKNOWN_ALIAS"):
        service.review_alias(
            AliasReviewRequest(
                expected_previous_event_id=None,
                reason="Reject an alias that is not in the bounded seed inventory",
                evidence=evidence,
                entity_id=CN,
                alias="Invented alias",
                language="en",
                decision="reject",
                context_dependent=False,
                valid_from=None,
                valid_to=None,
            ),
            ADMIN,
        )
    assert not root.exists()


@pytest.mark.parametrize(
    "contract,payload",
    [
        (
            RelationAddRequest,
            {
                "expected_previous_event_id": None,
                "reason": "Reject this self loop",
                "evidence": {
                    "article_id": 1,
                    "snapshot_id": f"article-1-{'a' * 64}",
                    "content_sha256": "a" * 64,
                    "parser_version": SNAPSHOT_PARSER_VERSION,
                },
                "subject_id": CN,
                "predicate": "urn:globemind:predicate:self",
                "object_id": CN,
                "valid_from": None,
                "valid_to": None,
            },
        ),
        (
            EntityDecisionRequest,
            {
                "expected_previous_event_id": None,
                "reason": "Reject an inverted interval",
                "evidence": {
                    "article_id": 1,
                    "snapshot_id": f"article-1-{'a' * 64}",
                    "content_sha256": "a" * 64,
                    "parser_version": SNAPSHOT_PARSER_VERSION,
                },
                "decision": "approve",
                "valid_from": "2026-08-10",
                "valid_to": "2026-08-09",
            },
        ),
        (
            AliasReviewRequest,
            {
                "expected_previous_event_id": None,
                "reason": "Reject a partially referenced mutation",
                "evidence": {
                    "article_id": 1,
                    "snapshot_id": f"article-1-{'a' * 64}",
                    "parser_version": SNAPSHOT_PARSER_VERSION,
                },
                "entity_id": CN,
                "alias": "China",
                "language": "en",
                "decision": "approve",
                "context_dependent": False,
                "valid_from": None,
                "valid_to": None,
            },
        ),
        (
            AliasReviewRequest,
            {
                "expected_previous_event_id": None,
                "reason": "Reject coercion of a Boolean review field",
                "evidence": {
                    "article_id": 1,
                    "snapshot_id": f"article-1-{'a' * 64}",
                    "content_sha256": "a" * 64,
                    "parser_version": SNAPSHOT_PARSER_VERSION,
                },
                "entity_id": CN,
                "alias": "China",
                "language": "en",
                "decision": "approve",
                "context_dependent": "false",
                "valid_from": None,
                "valid_to": None,
            },
        ),
    ],
)
def test_strict_contracts_reject_unsafe_or_partial_mutations(
    contract: type[object],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        contract.model_validate(payload)
