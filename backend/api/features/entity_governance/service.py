"""Temporal entity governance application service and approved read projection."""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Mapping

from .contracts import (
    AliasReviewRequest,
    EntityDecisionRequest,
    MergeDecisionRequest,
    RelationAddRequest,
    RelationRetractRequest,
    SplitDecisionRequest,
)
from .errors import (
    EntityGovernanceAccessDenied,
    EntityGovernanceConflict,
    EntityGovernanceNotFound,
    EntityGovernanceUnavailable,
)
from .evidence import (
    EvidenceSnapshotReader,
    validate_verified_evidence_metadata,
    verify_evidence_reference,
)
from .ledger import HISTORY_SCHEMA_VERSION, EntityGovernanceLedger

STATUS_SCHEMA_VERSION = "entity-governance-status-v2"
CATALOG_SCHEMA_VERSION = "entity-governance-catalog-v2"
ENTITY_SCHEMA_VERSION = "entity-governance-entity-v2"
RELATION_SCHEMA_VERSION = "entity-governance-relations-v2"
MUTATION_SCHEMA_VERSION = "entity-governance-mutation-result-v1"
_PREDICATE = re.compile(r"^urn:globemind:predicate:[a-z0-9][a-z0-9._-]{0,95}$")
_RELATION_ID = re.compile(r"^urn:globemind:relation:[0-9a-f]{32}$")


@dataclass
class _Projection:
    entities: dict[str, dict[str, Any]]
    alias_reviews: dict[tuple[str, str, str], dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    retractions: dict[str, dict[str, Any]]
    merges: dict[str, dict[str, Any]]
    splits: dict[str, dict[str, Any]]
    event_count: int
    latest_event_id: str | None


def _new_id() -> str:
    return uuid.uuid4().hex


def _require_keys(payload: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(payload) != expected:
        raise EntityGovernanceUnavailable(code)


def _valid_interval(valid_from: Any, valid_to: Any, code: str) -> None:
    parsed: list[date | None] = []
    for value in (valid_from, valid_to):
        if value is None:
            parsed.append(None)
            continue
        if not isinstance(value, str):
            raise EntityGovernanceUnavailable(code)
        try:
            normalized = date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise EntityGovernanceUnavailable(code) from exc
        if normalized != value:
            raise EntityGovernanceUnavailable(code)
        parsed.append(date.fromisoformat(value))
    if parsed[0] is not None and parsed[1] is not None and parsed[0] > parsed[1]:
        raise EntityGovernanceUnavailable(code)


def _validate_evidence(evidence: Any) -> None:
    validate_verified_evidence_metadata(evidence)


def _seed_alias(
    seed: Mapping[str, Any],
    alias: str,
    language: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in seed.get("aliases", [])
            if isinstance(item, dict)
            and str(item.get("value") or "").casefold() == alias.casefold()
            and item.get("language") == language
        ),
        None,
    )


def _canonical_merge_target(source: str, merges: Mapping[str, dict[str, Any]]) -> str:
    seen: set[str] = set()
    current = source
    while current in merges:
        if current in seen:
            raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_MERGE_CYCLE")
        seen.add(current)
        current = str(merges[current]["target_entity_id"])
    return current


def _active_merges(projection: _Projection) -> dict[str, dict[str, Any]]:
    approved = {
        entity_id
        for entity_id, state in projection.entities.items()
        if state["review_status"] == "approved"
    }
    return {
        source: decision
        for source, decision in projection.merges.items()
        if source in approved and decision["target_entity_id"] in approved
    }


def _active_splits(projection: _Projection) -> dict[str, dict[str, Any]]:
    approved = {
        entity_id
        for entity_id, state in projection.entities.items()
        if state["review_status"] == "approved"
    }
    return {
        source: decision
        for source, decision in projection.splits.items()
        if source in approved
        and all(item in approved for item in decision["resulting_entity_ids"])
    }


def _merge_participants(merges: Mapping[str, Mapping[str, Any]]) -> set[str]:
    participants = set(merges)
    participants.update(
        str(decision["target_entity_id"]) for decision in merges.values()
    )
    return participants


def _split_participants(splits: Mapping[str, Mapping[str, Any]]) -> set[str]:
    participants = set(splits)
    for decision in splits.values():
        participants.update(str(item) for item in decision["resulting_entity_ids"])
    return participants


def build_governance_projection(
    seeds: Mapping[str, Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
) -> _Projection:
    entities = {
        entity_id: {
            "seed": copy.deepcopy(dict(seed)),
            "review_status": "review_required",
            "valid_from": seed.get("source_valid_from"),
            "valid_to": seed.get("source_valid_to"),
            "decision_event": None,
        }
        for entity_id, seed in seeds.items()
    }
    alias_reviews: dict[tuple[str, str, str], dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    retractions: dict[str, dict[str, Any]] = {}
    merges: dict[str, dict[str, Any]] = {}
    splits: dict[str, dict[str, Any]] = {}
    latest_event_id: str | None = None
    count = 0

    for raw_event in events:
        event = copy.deepcopy(dict(raw_event))
        count += 1
        latest_event_id = str(event.get("event_id") or "")
        payload = event.get("payload")
        _validate_evidence(event.get("evidence"))
        if not isinstance(payload, dict):
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_PAYLOAD_INVALID"
            )
        event_type = event.get("event_type")

        if event_type == "entity.decision":
            _require_keys(
                payload,
                {"entity_id", "decision", "valid_from", "valid_to"},
                "ENTITY_GOVERNANCE_ENTITY_EVENT_INVALID",
            )
            entity_id = str(payload["entity_id"])
            if entity_id not in entities or payload["decision"] not in {"approve", "reject"}:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_ENTITY_EVENT_INVALID"
                )
            _valid_interval(
                payload["valid_from"],
                payload["valid_to"],
                "ENTITY_GOVERNANCE_ENTITY_TIME_INVALID",
            )
            target_status = (
                "approved" if payload["decision"] == "approve" else "rejected"
            )
            if (
                entities[entity_id]["review_status"] == target_status
                and entities[entity_id]["valid_from"] == payload["valid_from"]
                and entities[entity_id]["valid_to"] == payload["valid_to"]
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_DUPLICATE_DECISION"
                )
            entities[entity_id].update(
                {
                    "review_status": target_status,
                    "valid_from": payload["valid_from"],
                    "valid_to": payload["valid_to"],
                    "decision_event": event,
                }
            )
            continue

        if event_type == "alias.review":
            _require_keys(
                payload,
                {
                    "entity_id",
                    "alias",
                    "language",
                    "kind",
                    "decision",
                    "context_dependent",
                    "valid_from",
                    "valid_to",
                },
                "ENTITY_GOVERNANCE_ALIAS_EVENT_INVALID",
            )
            entity_id = str(payload["entity_id"])
            language = str(payload["language"])
            alias = str(payload["alias"])
            if entity_id not in entities:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_ALIAS_EVENT_INVALID"
                )
            seed_alias = _seed_alias(entities[entity_id]["seed"], alias, language)
            if (
                seed_alias is None
                or payload["decision"] not in {"approve", "reject"}
                or payload["kind"] != seed_alias.get("kind")
                or not isinstance(payload["context_dependent"], bool)
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_ALIAS_EVENT_INVALID"
                )
            _valid_interval(
                payload["valid_from"],
                payload["valid_to"],
                "ENTITY_GOVERNANCE_ALIAS_TIME_INVALID",
            )
            key = (entity_id, alias.casefold(), language)
            existing_alias_review = alias_reviews.get(key)
            if existing_alias_review is not None and all(
                existing_alias_review[field] == payload[field]
                for field in (
                    "decision",
                    "context_dependent",
                    "valid_from",
                    "valid_to",
                )
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_DUPLICATE_ALIAS_REVIEW"
                )
            alias_reviews[key] = {**payload, "review_event": event}
            continue

        if event_type == "relation.added":
            _require_keys(
                payload,
                {
                    "relation_id",
                    "subject_id",
                    "predicate",
                    "object_id",
                    "valid_from",
                    "valid_to",
                },
                "ENTITY_GOVERNANCE_RELATION_EVENT_INVALID",
            )
            relation_id = str(payload["relation_id"])
            subject_id = str(payload["subject_id"])
            object_id = str(payload["object_id"])
            if (
                _RELATION_ID.fullmatch(relation_id) is None
                or relation_id in relations
                or subject_id == object_id
                or subject_id not in entities
                or object_id not in entities
                or entities[subject_id]["review_status"] != "approved"
                or entities[object_id]["review_status"] != "approved"
                or _PREDICATE.fullmatch(str(payload["predicate"])) is None
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_RELATION_EVENT_INVALID"
                )
            _valid_interval(
                payload["valid_from"],
                payload["valid_to"],
                "ENTITY_GOVERNANCE_RELATION_TIME_INVALID",
            )
            if any(
                prior_id not in retractions
                and prior["subject_id"] == subject_id
                and prior["predicate"] == payload["predicate"]
                and prior["object_id"] == object_id
                and prior["valid_from"] == payload["valid_from"]
                and prior["valid_to"] == payload["valid_to"]
                for prior_id, prior in relations.items()
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_DUPLICATE_ACTIVE_RELATION"
                )
            relations[relation_id] = {**payload, "add_event": event}
            continue

        if event_type == "relation.retracted":
            _require_keys(
                payload,
                {"relation_id", "added_record_sha256"},
                "ENTITY_GOVERNANCE_RETRACTION_EVENT_INVALID",
            )
            relation_id = str(payload["relation_id"])
            if (
                relation_id not in relations
                or relation_id in retractions
                or payload["added_record_sha256"]
                != relations[relation_id]["add_event"]["record_sha256"]
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_RETRACTION_EVENT_INVALID"
                )
            retractions[relation_id] = event
            continue

        if event_type == "merge.decision":
            _require_keys(
                payload,
                {"source_entity_id", "target_entity_id"},
                "ENTITY_GOVERNANCE_MERGE_EVENT_INVALID",
            )
            source = str(payload["source_entity_id"])
            target = str(payload["target_entity_id"])
            if (
                source == target
                or source not in entities
                or target not in entities
                or entities[source]["review_status"] != "approved"
                or entities[target]["review_status"] != "approved"
                or source in merges
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_MERGE_EVENT_INVALID"
                )
            current = target
            while current in merges:
                if current == source:
                    raise EntityGovernanceUnavailable(
                        "ENTITY_GOVERNANCE_MERGE_CYCLE"
                    )
                current = str(merges[current]["target_entity_id"])
            if current == source:
                raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_MERGE_CYCLE")
            if {source, target} & _split_participants(splits):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_IDENTITY_DECISION_CONFLICT"
                )
            merges[source] = {**payload, "decision_event": event}
            continue

        if event_type == "split.decision":
            _require_keys(
                payload,
                {"source_entity_id", "resulting_entity_ids"},
                "ENTITY_GOVERNANCE_SPLIT_EVENT_INVALID",
            )
            source = str(payload["source_entity_id"])
            results = payload["resulting_entity_ids"]
            if (
                source not in entities
                or entities[source]["review_status"] != "approved"
                or not isinstance(results, list)
                or any(not isinstance(result, str) for result in results)
                or len(results) < 2
                or len(results) != len(set(results))
                or source in results
                or any(
                    result not in entities
                    or entities[result]["review_status"] != "approved"
                    for result in results
                )
                or (
                    {source, *results}
                    & (_merge_participants(merges) | _split_participants(splits))
                )
            ):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_SPLIT_EVENT_INVALID"
                )
            splits[source] = {**payload, "decision_event": event}
            continue

        raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_TYPE_INVALID")

    for source in merges:
        _canonical_merge_target(source, merges)
    return _Projection(
        entities=entities,
        alias_reviews=alias_reviews,
        relations=relations,
        retractions=retractions,
        merges=merges,
        splits=splits,
        event_count=count,
        latest_event_id=latest_event_id,
    )


class EntityGovernanceService:
    def __init__(
        self,
        ledger: EntityGovernanceLedger | None,
        seed_entities: Mapping[str, Mapping[str, Any]],
        *,
        evidence_reader: EvidenceSnapshotReader | None = None,
        unavailable_reason: str | None = None,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self.ledger = ledger
        self.seed_entities = {
            key: copy.deepcopy(dict(value)) for key, value in seed_entities.items()
        }
        self.evidence_reader = evidence_reader
        self.unavailable_reason = unavailable_reason
        self.id_factory = id_factory

    @staticmethod
    def _identity(user: Mapping[str, Any], *, admin: bool = False) -> int:
        actor_id = user.get("user_id")
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise EntityGovernanceAccessDenied(
                "ENTITY_GOVERNANCE_CANONICAL_USER_ID_REQUIRED"
            )
        if admin and user.get("role") != "admin":
            raise EntityGovernanceAccessDenied("ENTITY_GOVERNANCE_ADMIN_REQUIRED")
        return actor_id

    def _required_ledger(self) -> EntityGovernanceLedger:
        if self.ledger is None:
            raise EntityGovernanceUnavailable(
                self.unavailable_reason or "ENTITY_GOVERNANCE_LEDGER_UNAVAILABLE"
            )
        return self.ledger

    def _projection(self) -> _Projection:
        ledger = self._required_ledger()
        return build_governance_projection(self.seed_entities, ledger.records())

    @staticmethod
    def _latest_matches(projection: _Projection, expected: str | None) -> None:
        if projection.latest_event_id != expected:
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED"
            )

    def status(self, user: Mapping[str, Any]) -> dict[str, Any]:
        self._identity(user)
        if self.ledger is None:
            storage_status = "unavailable"
            integrity_status = "unavailable"
            event_count: int | None = None
            latest_event_id = None
            root_initialized = False
            reason = self.unavailable_reason or "ENTITY_GOVERNANCE_LEDGER_UNAVAILABLE"
        else:
            try:
                projection = self._projection()
            except EntityGovernanceUnavailable as exc:
                storage_status = "unavailable"
                integrity_status = "failed_closed"
                event_count = None
                latest_event_id = None
                root_initialized = self.ledger.root.exists()
                reason = str(exc)
            else:
                storage_status = "available"
                integrity_status = "verified"
                event_count = projection.event_count
                latest_event_id = projection.latest_event_id
                root_initialized = self.ledger.root.exists()
                reason = None
        if storage_status != "available":
            mutation_status = "blocked"
            mutation_blocker = reason
        elif self.evidence_reader is None:
            mutation_status = "blocked"
            mutation_blocker = "ENTITY_EVIDENCE_LEDGER_READER_UNAVAILABLE"
        else:
            mutation_status = "ready"
            mutation_blocker = None
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "storage_status": storage_status,
            "reason": reason,
            "root_initialized": root_initialized,
            "event_count": event_count,
            "latest_event_id": latest_event_id,
            "integrity_status": integrity_status,
            "mutation_status": mutation_status,
            "mutation_blocker": mutation_blocker,
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

    def _public_entity(
        self,
        entity_id: str,
        projection: _Projection,
    ) -> dict[str, Any] | None:
        state = projection.entities[entity_id]
        if state["review_status"] != "approved":
            return None
        seed = state["seed"]
        approved_aliases: list[dict[str, Any]] = []
        for review in projection.alias_reviews.values():
            if review["entity_id"] == entity_id and review["decision"] == "approve":
                approved_aliases.append(
                    {
                        "value": review["alias"],
                        "language": review["language"],
                        "kind": review["kind"],
                        "context_dependent": review["context_dependent"],
                        "valid_from": review["valid_from"],
                        "valid_to": review["valid_to"],
                        "review_event_id": review["review_event"]["event_id"],
                        "evidence": copy.deepcopy(review["review_event"]["evidence"]),
                    }
                )
        active_merges = _active_merges(projection)
        merge = active_merges.get(entity_id)
        split = _active_splits(projection).get(entity_id)
        decision_event = state["decision_event"]
        return {
            "entity_id": entity_id,
            "entity_type": seed["entity_type"],
            "canonical_names": copy.deepcopy(seed["canonical_names"]),
            "review_status": "approved",
            "valid_from": state["valid_from"],
            "valid_to": state["valid_to"],
            "approved_aliases": approved_aliases,
            "decision_event_id": decision_event["event_id"],
            "decision_evidence": copy.deepcopy(decision_event["evidence"]),
            "merge_target_id": merge["target_entity_id"] if merge else None,
            "canonical_entity_id": _canonical_merge_target(entity_id, active_merges),
            "split_into_entity_ids": (
                list(split["resulting_entity_ids"]) if split else []
            ),
        }

    def catalog(self, user: Mapping[str, Any]) -> dict[str, Any]:
        self._identity(user)
        projection = self._projection()
        approved = [
            public
            for entity_id in sorted(projection.entities)
            if (public := self._public_entity(entity_id, projection)) is not None
        ]
        review_queue = [
            {
                "entity_id": entity_id,
                "entity_type": state["seed"]["entity_type"],
                "canonical_names": copy.deepcopy(state["seed"]["canonical_names"]),
                "review_status": "review_required",
                "source_catalog_version": state["seed"]["source_catalog_version"],
                "source_catalog_review_status": state["seed"][
                    "source_catalog_review_status"
                ],
                "accuracy_claim": "not_measured",
            }
            for entity_id, state in sorted(projection.entities.items())
            if state["review_status"] == "review_required"
        ]
        rejected = [
            entity_id
            for entity_id, state in sorted(projection.entities.items())
            if state["review_status"] == "rejected"
        ]
        active_merges = _active_merges(projection)
        active_splits = _active_splits(projection)
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "accuracy_claim": "not_measured",
            "projection_policy": "approved-and-not-retracted-only",
            "review_expiry_policy": "not_configured",
            "seed_inventory_scope": "bounded-public-search-facade-probes",
            "approved_entities": approved,
            "review_required_entities": review_queue,
            "rejected_entity_ids": rejected,
            "merge_decisions": [
                {
                    "source_entity_id": source,
                    "target_entity_id": decision["target_entity_id"],
                    "decision_event_id": decision["decision_event"]["event_id"],
                }
                for source, decision in sorted(active_merges.items())
            ],
            "split_decisions": [
                {
                    "source_entity_id": source,
                    "resulting_entity_ids": list(decision["resulting_entity_ids"]),
                    "decision_event_id": decision["decision_event"]["event_id"],
                }
                for source, decision in sorted(active_splits.items())
            ],
            "event_count": projection.event_count,
            "latest_event_id": projection.latest_event_id,
            "assurance": {
                "worm": "unavailable",
                "digital_signature": "unavailable",
                "institutional_directory": "unavailable",
            },
        }

    def entity(self, entity_id: str, user: Mapping[str, Any]) -> dict[str, Any]:
        self._identity(user)
        projection = self._projection()
        if entity_id not in projection.entities:
            raise EntityGovernanceNotFound("ENTITY_GOVERNANCE_ENTITY_NOT_FOUND")
        state = projection.entities[entity_id]
        seed = state["seed"]
        decision_event = state["decision_event"]
        alias_states = []
        for alias in seed["aliases"]:
            key = (
                entity_id,
                str(alias["value"]).casefold(),
                str(alias["language"]),
            )
            reviewed = projection.alias_reviews.get(key)
            alias_states.append(
                {
                    **copy.deepcopy(alias),
                    "review_status": (
                        "review_required"
                        if reviewed is None
                        else (
                            "approved"
                            if reviewed["decision"] == "approve"
                            else "rejected"
                        )
                    ),
                    "context_dependent": (
                        alias.get("status") == "context_dependent"
                        if reviewed is None
                        else reviewed["context_dependent"]
                    ),
                    "valid_from": reviewed["valid_from"] if reviewed else None,
                    "valid_to": reviewed["valid_to"] if reviewed else None,
                    "review_event_id": (
                        reviewed["review_event"]["event_id"] if reviewed else None
                    ),
                    "review_evidence": (
                        copy.deepcopy(reviewed["review_event"]["evidence"])
                        if reviewed
                        else None
                    ),
                }
            )
        active_relations = self._active_relations(projection)
        return {
            "schema_version": ENTITY_SCHEMA_VERSION,
            "review_expiry_policy": "not_configured",
            "entity_id": entity_id,
            "governance_review_status": state["review_status"],
            "seed": copy.deepcopy(seed),
            "governance_decision": (
                {
                    "decision": decision_event["payload"]["decision"],
                    "valid_from": decision_event["payload"]["valid_from"],
                    "valid_to": decision_event["payload"]["valid_to"],
                    "event_id": decision_event["event_id"],
                    "occurred_at": decision_event["occurred_at"],
                    "actor_ref": decision_event["actor_ref"],
                    "reason": decision_event["reason"],
                    "evidence": copy.deepcopy(decision_event["evidence"]),
                }
                if decision_event is not None
                else None
            ),
            "alias_reviews": alias_states,
            "active_projection": self._public_entity(entity_id, projection),
            "active_relation_count": sum(
                1
                for relation in active_relations
                if entity_id in {relation["subject_id"], relation["object_id"]}
            ),
            "accuracy_claim": "not_measured",
        }

    @staticmethod
    def _active_relations(projection: _Projection) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for relation_id, relation in sorted(projection.relations.items()):
            if relation_id in projection.retractions:
                continue
            if (
                projection.entities[relation["subject_id"]]["review_status"]
                != "approved"
                or projection.entities[relation["object_id"]]["review_status"]
                != "approved"
            ):
                continue
            active.append(
                {
                    "relation_id": relation_id,
                    "subject_id": relation["subject_id"],
                    "predicate": relation["predicate"],
                    "object_id": relation["object_id"],
                    "valid_from": relation["valid_from"],
                    "valid_to": relation["valid_to"],
                    "review_status": "approved",
                    "added_event_id": relation["add_event"]["event_id"],
                    "evidence": copy.deepcopy(relation["add_event"]["evidence"]),
                }
            )
        return active

    def relations(self, user: Mapping[str, Any]) -> dict[str, Any]:
        self._identity(user)
        projection = self._projection()
        items = self._active_relations(projection)
        return {
            "schema_version": RELATION_SCHEMA_VERSION,
            "projection_policy": "approved-and-not-retracted-only",
            "accuracy_claim": "not_measured",
            "review_expiry_policy": "not_configured",
            "relation_count": len(items),
            "items": items,
        }

    def history(self, user: Mapping[str, Any], *, limit: int = 100) -> dict[str, Any]:
        self._identity(user)
        ledger = self._required_ledger()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_HISTORY_LIMIT_INVALID")
        records = ledger.records()
        projection = build_governance_projection(self.seed_entities, records)
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "event_count": len(records),
            "items": list(reversed(records[-limit:])),
            "semantic_projection_verified": True,
            "latest_event_id": projection.latest_event_id,
            "worm_status": "unavailable",
            "digital_signature_status": "unavailable",
            "visibility": "authenticated-users",
            "actor_reference_semantics": "local-canonical-user-id-not-directory-resolved",
            "reason_visibility": "authenticated-users",
        }

    def _append(
        self,
        *,
        user: Mapping[str, Any],
        event_type: str,
        reason: str,
        evidence_reference: Any,
        payload: Mapping[str, Any],
        expected_previous_event_id: str | None,
    ) -> dict[str, Any]:
        actor_id = self._identity(user, admin=True)
        ledger = self._required_ledger()
        evidence = verify_evidence_reference(self.evidence_reader, evidence_reference)
        records = ledger.records()
        actual_previous = records[-1]["event_id"] if records else None
        if actual_previous != expected_previous_event_id:
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED"
            )
        # Validate the exact candidate semantics before making the append-only
        # write durable. The ledger repeats the optimistic check under its
        # exclusive lock, so a concurrent writer still fails closed.
        build_governance_projection(self.seed_entities, records)
        build_governance_projection(
            self.seed_entities,
            [
                *records,
                {
                    "event_id": "synthetic-precommit-validation",
                    "event_type": event_type,
                    "evidence": evidence,
                    "payload": dict(payload),
                },
            ],
        )
        event = ledger.append(
            actor_id=actor_id,
            event_type=event_type,
            reason=reason,
            evidence=evidence,
            payload=payload,
            expected_previous_event_id=expected_previous_event_id,
        )
        return {
            "schema_version": MUTATION_SCHEMA_VERSION,
            "event": event,
            "projection_status": "accepted",
        }

    def decide_entity(
        self,
        entity_id: str,
        body: EntityDecisionRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        if entity_id not in projection.entities:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_UNKNOWN_ENTITY")
        state = projection.entities[entity_id]
        target_status = "approved" if body.decision == "approve" else "rejected"
        if (
            state["review_status"] == target_status
            and state["valid_from"] == body.valid_from
            and state["valid_to"] == body.valid_to
        ):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_DUPLICATE_DECISION")
        return self._append(
            user=user,
            event_type="entity.decision",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "entity_id": entity_id,
                "decision": body.decision,
                "valid_from": body.valid_from,
                "valid_to": body.valid_to,
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )

    def review_alias(
        self,
        body: AliasReviewRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        if body.entity_id not in projection.entities:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_UNKNOWN_ENTITY")
        alias = _seed_alias(
            projection.entities[body.entity_id]["seed"],
            body.alias,
            body.language,
        )
        if alias is None:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_UNKNOWN_ALIAS")
        key = (body.entity_id, body.alias.casefold(), body.language)
        existing = projection.alias_reviews.get(key)
        if existing is not None and all(
            existing[key] == value
            for key, value in {
                "decision": body.decision,
                "context_dependent": body.context_dependent,
                "valid_from": body.valid_from,
                "valid_to": body.valid_to,
            }.items()
        ):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_DUPLICATE_ALIAS_REVIEW")
        return self._append(
            user=user,
            event_type="alias.review",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "entity_id": body.entity_id,
                "alias": str(alias["value"]),
                "language": body.language,
                "kind": str(alias["kind"]),
                "decision": body.decision,
                "context_dependent": body.context_dependent,
                "valid_from": body.valid_from,
                "valid_to": body.valid_to,
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )

    def add_relation(
        self,
        body: RelationAddRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        referenced = {body.subject_id, body.object_id}
        if not referenced.issubset(projection.entities):
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_RELATION_HAS_UNKNOWN_ENTITY"
            )
        if any(
            projection.entities[entity_id]["review_status"] != "approved"
            for entity_id in referenced
        ):
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_RELATION_ENTITY_NOT_APPROVED"
            )
        if any(
            relation_id not in projection.retractions
            and relation["subject_id"] == body.subject_id
            and relation["predicate"] == body.predicate
            and relation["object_id"] == body.object_id
            and relation["valid_from"] == body.valid_from
            and relation["valid_to"] == body.valid_to
            for relation_id, relation in projection.relations.items()
        ):
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_DUPLICATE_ACTIVE_RELATION"
            )
        identifier = str(self.id_factory())
        if re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_RELATION_ID_INVALID")
        return self._append(
            user=user,
            event_type="relation.added",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "relation_id": f"urn:globemind:relation:{identifier}",
                "subject_id": body.subject_id,
                "predicate": body.predicate,
                "object_id": body.object_id,
                "valid_from": body.valid_from,
                "valid_to": body.valid_to,
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )

    def retract_relation(
        self,
        relation_id: str,
        body: RelationRetractRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        if relation_id not in projection.relations:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_UNKNOWN_RELATION")
        if relation_id in projection.retractions:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_RELATION_ALREADY_RETRACTED")
        relation = projection.relations[relation_id]
        return self._append(
            user=user,
            event_type="relation.retracted",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "relation_id": relation_id,
                "added_record_sha256": relation["add_event"]["record_sha256"],
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )

    def decide_merge(
        self,
        body: MergeDecisionRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        referenced = {body.source_entity_id, body.target_entity_id}
        if not referenced.issubset(projection.entities):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_MERGE_UNKNOWN_ENTITY")
        if any(
            projection.entities[entity_id]["review_status"] != "approved"
            for entity_id in referenced
        ):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_MERGE_ENTITY_NOT_APPROVED")
        if body.source_entity_id in projection.merges:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_MERGE_SOURCE_ALREADY_DECIDED")
        if body.source_entity_id in projection.splits or body.target_entity_id in projection.splits:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_IDENTITY_DECISION_CONFLICT")
        current = body.target_entity_id
        while current in projection.merges:
            if current == body.source_entity_id:
                raise EntityGovernanceConflict("ENTITY_GOVERNANCE_MERGE_CYCLE")
            current = str(projection.merges[current]["target_entity_id"])
        if current == body.source_entity_id:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_MERGE_CYCLE")
        if referenced & _split_participants(projection.splits):
            raise EntityGovernanceConflict(
                "ENTITY_GOVERNANCE_IDENTITY_DECISION_CONFLICT"
            )
        return self._append(
            user=user,
            event_type="merge.decision",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "source_entity_id": body.source_entity_id,
                "target_entity_id": body.target_entity_id,
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )

    def decide_split(
        self,
        body: SplitDecisionRequest,
        user: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._identity(user, admin=True)
        projection = self._projection()
        self._latest_matches(projection, body.expected_previous_event_id)
        referenced = {body.source_entity_id, *body.resulting_entity_ids}
        if not referenced.issubset(projection.entities):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_SPLIT_UNKNOWN_ENTITY")
        if any(
            projection.entities[entity_id]["review_status"] != "approved"
            for entity_id in referenced
        ):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_SPLIT_ENTITY_NOT_APPROVED")
        if referenced & (
            _merge_participants(projection.merges)
            | _split_participants(projection.splits)
        ):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_IDENTITY_DECISION_CONFLICT")
        return self._append(
            user=user,
            event_type="split.decision",
            reason=body.reason,
            evidence_reference=body.evidence,
            payload={
                "source_entity_id": body.source_entity_id,
                "resulting_entity_ids": list(body.resulting_entity_ids),
            },
            expected_previous_event_id=body.expected_previous_event_id,
        )


__all__ = (
    "CATALOG_SCHEMA_VERSION",
    "ENTITY_SCHEMA_VERSION",
    "MUTATION_SCHEMA_VERSION",
    "RELATION_SCHEMA_VERSION",
    "STATUS_SCHEMA_VERSION",
    "EntityGovernanceService",
    "build_governance_projection",
)
