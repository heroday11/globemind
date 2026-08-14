"""Application service for an auditable end-to-end research workflow."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .artifacts import (
    ArtifactFormat,
    ResearchArtifactError,
    ResearchExportArtifact,
    build_research_export_artifact,
)
from .comparisons import build_manifest_comparison
from .contracts import (
    AlternativeHypothesisCreateRequest,
    EvidenceCreateRequest,
    ExportManifestCreateRequest,
    HumanDecisionCreateRequest,
    InformationGapCreateRequest,
    JudgmentCreateRequest,
    MemberChangeRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectSummary,
    QuestionCreateRequest,
    ResearchProject,
    ReviewCreateRequest,
    SavedSearchCreateRequest,
)
from .evidence_snapshots import (
    EvidenceSnapshotReader,
    verify_evidence_snapshot_reference,
)
from .repository import ResearchProjectRepository
from .search_snapshots import (
    SearchSnapshotReader,
    verify_search_snapshot_reference,
)

SAFE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")


class ResearchWorkflowError(RuntimeError):
    """Base class for workflow errors safe to translate at the HTTP boundary."""


class ResearchAccessDenied(ResearchWorkflowError):
    pass


class ResearchContractConflict(ResearchWorkflowError):
    pass


class ResearchWorkflowNotReady(ResearchWorkflowError):
    def __init__(self, reason_codes: Iterable[str]) -> None:
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__("research workflow is not ready for a versioned export")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalized_workflow_timestamp(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ResearchContractConflict("workflow clock timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ResearchContractConflict("workflow clock timestamp requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_record(record: dict[str, Any], digest_field: str) -> dict[str, Any]:
    sealed = dict(record)
    sealed[digest_field] = _canonical_sha256(sealed)
    return sealed


def _seal_project_state(project: dict[str, Any]) -> None:
    integrity_payload = dict(project)
    integrity_payload.pop("state_integrity_sha256", None)
    project["state_integrity_sha256"] = _canonical_sha256(integrity_payload)


def _resource_by_id(
    project: dict[str, Any], collection: str, resource_id: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in project.get(collection, [])
            if str(item.get("id") or "") == str(resource_id or "")
        ),
        None,
    )


def _latest_matching(
    rows: Iterable[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    return next((row for row in reversed(list(rows)) if predicate(row)), None)


def _require_distinct_known_ids(
    project: dict[str, Any],
    *,
    collection: str,
    values: list[str],
    label: str,
) -> list[dict[str, Any]]:
    if len(set(values)) != len(values):
        raise ResearchContractConflict(f"{label} contains duplicate identifiers")
    rows: list[dict[str, Any]] = []
    for resource_id in values:
        resource = _resource_by_id(project, collection, resource_id)
        if resource is None:
            raise ResearchContractConflict(f"unknown {label} identifier")
        rows.append(resource)
    return rows


class ResearchWorkflowService:
    def __init__(
        self,
        repository: ResearchProjectRepository,
        *,
        clock: Callable[[], str] = _utc_now,
        id_factory: Callable[[], str] = _new_id,
        evidence_snapshot_reader: EvidenceSnapshotReader | None = None,
        search_snapshot_reader: SearchSnapshotReader | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.id_factory = id_factory
        self.evidence_snapshot_reader = evidence_snapshot_reader
        self.search_snapshot_reader = search_snapshot_reader

    @staticmethod
    def _actor(user: dict[str, Any]) -> str:
        username = str(user.get("username") or "").strip()
        if not SAFE_USERNAME_RE.fullmatch(username):
            raise ResearchAccessDenied("authenticated identity is not a safe actor")
        return username

    @staticmethod
    def _actor_id(user: dict[str, Any]) -> int:
        actor_id = user.get("user_id")
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise ResearchAccessDenied(
                "authenticated identity has no canonical user_id"
            )
        return actor_id

    @staticmethod
    def _role(project: dict[str, Any], actor: str) -> str | None:
        for member in project.get("members", []):
            if member.get("username") == actor and member.get("role") in {
                "owner",
                "reviewer",
                "reader",
            }:
                return str(member["role"])
        return None

    def _require_role(
        self,
        project: dict[str, Any],
        actor: str,
        allowed: set[str],
    ) -> str:
        role = self._role(project, actor)
        if role not in allowed:
            raise ResearchAccessDenied("project role does not permit this operation")
        return role

    def _preauthorize(
        self,
        project_id: str,
        actor: str,
        allowed: set[str],
    ) -> None:
        """Check access before optimistic-lock details can be disclosed."""
        project = self.repository.get(project_id)
        self._require_role(project, actor, allowed)

    def _next_timestamp(self, project: dict[str, Any]) -> str:
        timestamp = _normalized_workflow_timestamp(self.clock())
        prior = _normalized_workflow_timestamp(project["updated_at"])
        current_value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        prior_value = datetime.fromisoformat(prior.replace("Z", "+00:00"))
        if current_value < prior_value:
            raise ResearchContractConflict("workflow clock regressed")
        return timestamp

    def _record_change(
        self,
        project: dict[str, Any],
        *,
        actor: str,
        reason: str,
        action: str,
        resource_type: str,
        resource_id: str,
        changed_fields: list[str],
        timestamp: str,
    ) -> None:
        previous_version = int(project["version"])
        version = previous_version + 1
        project["version"] = version
        project["updated_at"] = timestamp
        change_id = self.id_factory()
        previous_change_sha256 = (
            project["change_history"][-1]["change_sha256"]
            if project["change_history"]
            else None
        )
        project["change_history"].append(
            _seal_record(
                {
                    "change_id": change_id,
                    "version": version,
                    "previous_version": previous_version,
                    "actor": actor,
                    "timestamp": timestamp,
                    "reason": reason,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "previous_change_sha256": previous_change_sha256,
                },
                "change_sha256",
            )
        )
        # The audit stream intentionally contains no research body, rationale,
        # query, review comment, source excerpt, or free-text reason.
        previous_event_sha256 = (
            project["audit_events"][-1]["event_sha256"]
            if project["audit_events"]
            else None
        )
        project["audit_events"].append(
            _seal_record(
                {
                    "event_id": self.id_factory(),
                    "project_id": project["id"],
                    "actor": actor,
                    "timestamp": timestamp,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "version": version,
                    "previous_version": previous_version,
                    "reason_sha256": hashlib.sha256(
                        reason.encode("utf-8")
                    ).hexdigest(),
                    "reason_length": len(reason),
                    "changed_fields": changed_fields,
                    "previous_event_sha256": previous_event_sha256,
                },
                "event_sha256",
            )
        )
        _seal_project_state(project)

    def create_project(
        self, body: ProjectCreateRequest, user: dict[str, Any]
    ) -> dict[str, Any]:
        actor = self._actor(user)
        now = _normalized_workflow_timestamp(self.clock())
        project_id = self.id_factory()
        countries = list(
            dict.fromkeys(
                value.strip().upper()
                for value in body.scope_countries
                if value.strip()
            )
        )
        change_id = self.id_factory()
        project: dict[str, Any] = {
            "schema_version": "research-project-v1",
            "id": project_id,
            "title": body.title,
            "description": body.description,
            "scope_countries": countries,
            "owner": actor,
            "members": [
                {
                    "username": actor,
                    "role": "owner",
                    "added_at": now,
                    "added_by": actor,
                }
            ],
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "research_questions": [],
            "saved_searches": [],
            "evidence_items": [],
            "information_gaps": [],
            "alternative_hypotheses": [],
            "judgments": [],
            "human_decisions": [],
            "reviews": [],
            "export_manifests": [],
            "change_history": [
                _seal_record(
                    {
                        "change_id": change_id,
                        "version": 1,
                        "previous_version": None,
                        "actor": actor,
                        "timestamp": now,
                        "reason": body.reason,
                        "action": "project.created",
                        "resource_type": "project",
                        "resource_id": project_id,
                        "previous_change_sha256": None,
                    },
                    "change_sha256",
                )
            ],
            "audit_events": [
                _seal_record(
                    {
                        "event_id": self.id_factory(),
                        "project_id": project_id,
                        "actor": actor,
                        "timestamp": now,
                        "action": "project.created",
                        "resource_type": "project",
                        "resource_id": project_id,
                        "version": 1,
                        "previous_version": None,
                        "reason_sha256": hashlib.sha256(
                            body.reason.encode("utf-8")
                        ).hexdigest(),
                        "reason_length": len(body.reason),
                        "changed_fields": [
                            "title",
                            "description",
                            "scope_countries",
                            "members",
                        ],
                        "previous_event_sha256": None,
                    },
                    "event_sha256",
                )
            ],
            "storage": {
                "status": "available",
                "backend": "filesystem:workspace-root-isolated-service-store",
                "durability": "atomic-json-fsync",
                "fallback": "none",
                "integrity_check": "sha256-sealed-state-and-history-chain",
                "audit_immutability": "unavailable",
                "state_integrity_scope": (
                    "complete-persisted-project-before-acl-redaction"
                ),
                "response_view": "complete-persisted-project",
            },
        }
        _seal_project_state(project)
        return self.repository.create(
            ResearchProject.model_validate(project).model_dump(mode="json")
        )

    def list_projects(self, user: dict[str, Any]) -> ProjectListResponse:
        actor = self._actor(user)
        summaries: list[ProjectSummary] = []
        for project in self.repository.list_projects():
            role = self._role(project, actor)
            if role is None:
                continue
            summaries.append(
                ProjectSummary(
                    id=project["id"],
                    title=project["title"],
                    scope_countries=project["scope_countries"],
                    role=role,
                    version=project["version"],
                    updated_at=project["updated_at"],
                    workflow_counts={
                        "questions": len(project["research_questions"]),
                        "saved_searches": len(project["saved_searches"]),
                        "evidence": len(project["evidence_items"]),
                        "gaps": len(project["information_gaps"]),
                        "hypotheses": len(project["alternative_hypotheses"]),
                        "judgments": len(project["judgments"]),
                        "decisions": len(project["human_decisions"]),
                        "reviews": len(project["reviews"]),
                        "exports": len(project["export_manifests"]),
                    },
                )
            )
        summaries.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return ProjectListResponse(projects=summaries)

    def get_project(
        self, project_id: str, user: dict[str, Any]
    ) -> dict[str, Any]:
        actor = self._actor(user)
        project = self.repository.get(project_id)
        role = self._require_role(project, actor, {"owner", "reviewer", "reader"})
        if role == "reader":
            project = dict(project)
            project["audit_events"] = []
            project["storage"] = {
                **project["storage"],
                "response_view": "acl-redacted",
            }
        return project

    def get_audit_events(
        self, project_id: str, user: dict[str, Any]
    ) -> dict[str, Any]:
        actor = self._actor(user)
        project = self.repository.get(project_id)
        self._require_role(project, actor, {"owner", "reviewer"})
        return {
            "schema_version": "research-audit-v1",
            "project_id": project_id,
            "events": project["audit_events"],
            "redaction": {
                "free_text": "sha256-and-length-only",
                "body_fields_included": "none",
            },
        }

    def set_member(
        self,
        project_id: str,
        username: str,
        body: MemberChangeRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        member_username = str(username or "").strip()
        if not SAFE_USERNAME_RE.fullmatch(member_username):
            raise ResearchContractConflict("member username is invalid")
        self._preauthorize(project_id, actor, {"owner"})

        def mutation(project: dict[str, Any]):
            self._require_role(project, actor, {"owner"})
            if member_username == project["owner"]:
                raise ResearchContractConflict("the project owner role is immutable")
            now = self._next_timestamp(project)
            existing = next(
                (
                    item
                    for item in project["members"]
                    if item["username"] == member_username
                ),
                None,
            )
            if existing is None:
                project["members"].append(
                    {
                        "username": member_username,
                        "role": body.role,
                        "added_at": now,
                        "added_by": actor,
                    }
                )
                action = "member.added"
            else:
                existing["role"] = body.role
                existing["added_at"] = now
                existing["added_by"] = actor
                action = "member.role_changed"
            self._record_change(
                project,
                actor=actor,
                reason=body.reason,
                action=action,
                resource_type="member",
                resource_id=member_username,
                changed_fields=["members.role"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=body.expected_version,
            mutation=mutation,
        )

    def _append_owner_resource(
        self,
        project_id: str,
        *,
        expected_version: int,
        reason: str,
        actor: str,
        collection: str,
        resource_type: str,
        action: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self._preauthorize(project_id, actor, {"owner"})

        def mutation(project: dict[str, Any]):
            self._require_role(project, actor, {"owner"})
            now = self._next_timestamp(project)
            resource_id = self.id_factory()
            project[collection].append(
                {
                    "id": resource_id,
                    **fields,
                    "created_at": now,
                    "created_by": actor,
                }
            )
            self._record_change(
                project,
                actor=actor,
                reason=reason,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                changed_fields=[f"{collection}[]"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=expected_version,
            mutation=mutation,
        )

    def add_question(
        self,
        project_id: str,
        body: QuestionCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        return self._append_owner_resource(
            project_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=self._actor(user),
            collection="research_questions",
            resource_type="research_question",
            action="research_question.added",
            fields={"question": body.question},
        )

    def add_saved_search(
        self,
        project_id: str,
        body: SavedSearchCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        self._preauthorize(project_id, actor, {"owner"})
        query_contract = {"query": body.query, "filters": body.filters}
        if body.search_snapshot_id is not None:
            snapshot = verify_search_snapshot_reference(
                self.search_snapshot_reader,
                actor_id=self._actor_id(user),
                snapshot_id=body.search_snapshot_id,
                query_receipt_sha256=str(body.query_receipt_sha256),
                normalized_contract_sha256=str(body.normalized_contract_sha256),
                ordered_returned_ids_sha256=str(
                    body.ordered_returned_ids_sha256
                ),
                declared_query=body.query,
            )
            snapshot["snapshot_id"] = None
        else:
            snapshot = {
                "snapshot_status": "unavailable",
                "snapshot_id": None,
                "search_snapshot_id": None,
                "query_receipt_sha256": None,
                "normalized_contract_sha256": None,
                "ordered_returned_ids_sha256": None,
                "snapshot_integrity_sha256": None,
                "snapshot_captured_at": None,
                "receipt_method_version": None,
                "entity_catalog_version": None,
                "entity_catalog_review_status": None,
                "result_id_namespace": None,
                "returned_result_count": None,
                "result_page": None,
                "result_total": None,
                "result_cutoff": None,
                "result_coverage_start": None,
                "result_coverage_end": None,
                "result_coverage_status": None,
                "snapshot_reason": "SEARCH_SNAPSHOT_NOT_PROVIDED",
            }
        return self._append_owner_resource(
            project_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=actor,
            collection="saved_searches",
            resource_type="saved_search",
            action="saved_search.added",
            fields={
                "name": body.name,
                **query_contract,
                "query_sha256": _canonical_sha256(query_contract),
                **snapshot,
            },
        )

    def add_evidence(
        self,
        project_id: str,
        body: EvidenceCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        self._preauthorize(project_id, actor, {"owner"})
        has_locator = bool(body.original_anchor)
        if body.evidence_snapshot_id is not None:
            snapshot = verify_evidence_snapshot_reference(
                self.evidence_snapshot_reader,
                article_id=int(body.article_id),
                snapshot_id=body.evidence_snapshot_id,
                content_sha256=str(body.content_sha256),
                captured_at=str(body.captured_at),
                parser_version=str(body.parser_version),
            )
            provenance_status = "verified"
            provenance_reason = "EVIDENCE_LEDGER_REFERENCE_VERIFIED"
        else:
            snapshot = {
                "article_id": None,
                "evidence_snapshot_id": None,
                "content_sha256": None,
                "captured_at": None,
                "parser_version": None,
                "snapshot_status": "unavailable",
                "snapshot_reason": "EVIDENCE_SNAPSHOT_NOT_PROVIDED",
            }
            provenance_status = (
                "declared" if has_locator and bool(body.source_url) else "incomplete"
            )
            provenance_reason = (
                "RESEARCHER_DECLARED_REFERENCE_NOT_SERVER_VERIFIED"
                if provenance_status == "declared"
                else "SOURCE_REFERENCE_OR_LOCATOR_MISSING"
            )
        return self._append_owner_resource(
            project_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=actor,
            collection="evidence_items",
            resource_type="evidence_item",
            action="evidence_item.added",
            fields={
                "relation": body.relation,
                "summary": body.summary,
                "source_id": body.source_id,
                "source_title": body.source_title,
                "source_url": body.source_url,
                "original_anchor": body.original_anchor,
                "source_published_at": body.source_published_at,
                **snapshot,
                "provenance_status": provenance_status,
                "provenance_reason": provenance_reason,
                "note": body.note,
            },
        )

    def add_information_gap(
        self,
        project_id: str,
        body: InformationGapCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        return self._append_owner_resource(
            project_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=self._actor(user),
            collection="information_gaps",
            resource_type="information_gap",
            action="information_gap.added",
            fields={
                "description": body.description,
                "impact": body.impact,
                "resolution_plan": body.resolution_plan,
            },
        )

    def add_alternative_hypothesis(
        self,
        project_id: str,
        body: AlternativeHypothesisCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        return self._append_owner_resource(
            project_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor=self._actor(user),
            collection="alternative_hypotheses",
            resource_type="alternative_hypothesis",
            action="alternative_hypothesis.added",
            fields={
                "statement": body.statement,
                "discriminating_evidence": body.discriminating_evidence,
            },
        )

    def add_judgment(
        self,
        project_id: str,
        body: JudgmentCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        self._preauthorize(project_id, actor, {"owner"})

        def mutation(project: dict[str, Any]):
            self._require_role(project, actor, {"owner"})
            supporting = _require_distinct_known_ids(
                project,
                collection="evidence_items",
                values=body.supporting_evidence_ids,
                label="supporting evidence",
            )
            opposing = _require_distinct_known_ids(
                project,
                collection="evidence_items",
                values=body.opposing_evidence_ids,
                label="opposing evidence",
            )
            if any(item["relation"] != "support" for item in supporting):
                raise ResearchContractConflict(
                    "supporting evidence must use relation=support"
                )
            if any(item["relation"] != "opposing" for item in opposing):
                raise ResearchContractConflict(
                    "opposing evidence must use relation=opposing"
                )
            _require_distinct_known_ids(
                project,
                collection="information_gaps",
                values=body.information_gap_ids,
                label="information gap",
            )
            _require_distinct_known_ids(
                project,
                collection="alternative_hypotheses",
                values=body.alternative_hypothesis_ids,
                label="alternative hypothesis",
            )
            now = self._next_timestamp(project)
            resource_id = self.id_factory()
            project["judgments"].append(
                {
                    "id": resource_id,
                    "statement": body.statement,
                    "supporting_evidence_ids": body.supporting_evidence_ids,
                    "opposing_evidence_ids": body.opposing_evidence_ids,
                    "information_gap_ids": body.information_gap_ids,
                    "alternative_hypothesis_ids": body.alternative_hypothesis_ids,
                    "uncertainty": body.uncertainty,
                    "created_at": now,
                    "created_by": actor,
                }
            )
            self._record_change(
                project,
                actor=actor,
                reason=body.reason,
                action="judgment.added",
                resource_type="judgment",
                resource_id=resource_id,
                changed_fields=["judgments[]"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=body.expected_version,
            mutation=mutation,
        )

    def add_human_decision(
        self,
        project_id: str,
        body: HumanDecisionCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        self._preauthorize(project_id, actor, {"owner"})

        def mutation(project: dict[str, Any]):
            self._require_role(project, actor, {"owner"})
            if _resource_by_id(project, "judgments", body.judgment_id) is None:
                raise ResearchContractConflict("unknown judgment identifier")
            now = self._next_timestamp(project)
            resource_id = self.id_factory()
            project["human_decisions"].append(
                {
                    "id": resource_id,
                    "judgment_id": body.judgment_id,
                    "decision": body.decision,
                    "rationale": body.rationale,
                    "modified_statement": body.modified_statement,
                    "created_at": now,
                    "created_by": actor,
                }
            )
            self._record_change(
                project,
                actor=actor,
                reason=body.reason,
                action=f"human_decision.{body.decision}",
                resource_type="human_decision",
                resource_id=resource_id,
                changed_fields=["human_decisions[]"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=body.expected_version,
            mutation=mutation,
        )

    def add_review(
        self,
        project_id: str,
        body: ReviewCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        allowed = {"reviewer"} if body.review_type == "peer_review" else {"owner"}
        self._preauthorize(project_id, actor, allowed)

        def mutation(project: dict[str, Any]):
            role = self._role(project, actor)
            if body.review_type == "peer_review":
                if role != "reviewer" or body.target_type != "decision":
                    raise ResearchAccessDenied(
                        "peer review requires reviewer role and a decision target"
                    )
                target = _resource_by_id(
                    project, "human_decisions", body.target_id
                )
                if target is None:
                    raise ResearchContractConflict("unknown decision identifier")
                if target["created_by"] == actor:
                    raise ResearchAccessDenied("authors cannot peer-review their own decision")
            else:
                if role != "owner" or body.target_type != "decision":
                    raise ResearchAccessDenied(
                        "approval requires owner role and a decision target"
                    )
                target = _resource_by_id(
                    project, "human_decisions", body.target_id
                )
                if target is None:
                    raise ResearchContractConflict("unknown decision identifier")
                if target["decision"] == "reject":
                    raise ResearchContractConflict("a rejected judgment cannot be approved")
                latest_peer_review = _latest_matching(
                    project["reviews"],
                    lambda review: review["review_type"] == "peer_review"
                    and review["target_type"] == "decision"
                    and review["target_id"] == target["id"],
                )
                if not latest_peer_review or latest_peer_review["outcome"] != "approved":
                    raise ResearchWorkflowNotReady(["PEER_REVIEW_APPROVAL_MISSING"])
            now = self._next_timestamp(project)
            resource_id = self.id_factory()
            project["reviews"].append(
                {
                    "id": resource_id,
                    "review_type": body.review_type,
                    "target_type": body.target_type,
                    "target_id": body.target_id,
                    "outcome": body.outcome,
                    "comment": body.comment,
                    "created_at": now,
                    "created_by": actor,
                }
            )
            self._record_change(
                project,
                actor=actor,
                reason=body.reason,
                action=f"review.{body.review_type}.{body.outcome}",
                resource_type="review",
                resource_id=resource_id,
                changed_fields=["reviews[]"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=body.expected_version,
            mutation=mutation,
        )

    @staticmethod
    def _export_readiness(project: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        for collection, reason in (
            ("research_questions", "RESEARCH_QUESTION_MISSING"),
            ("saved_searches", "SAVED_SEARCH_MISSING"),
            ("information_gaps", "INFORMATION_GAP_MISSING"),
            ("alternative_hypotheses", "ALTERNATIVE_HYPOTHESIS_MISSING"),
            ("judgments", "JUDGMENT_MISSING"),
        ):
            if not project[collection]:
                reasons.append(reason)
        relations = {item["relation"] for item in project["evidence_items"]}
        for relation, reason in (
            ("support", "SUPPORTING_EVIDENCE_MISSING"),
            ("opposing", "OPPOSING_EVIDENCE_MISSING"),
            ("background", "BACKGROUND_EVIDENCE_MISSING"),
        ):
            if relation not in relations:
                reasons.append(reason)

        judgments_by_id = {item["id"]: item for item in project["judgments"]}
        analytically_linked_judgments = {
            item["id"]
            for item in project["judgments"]
            if item["supporting_evidence_ids"]
            and item["opposing_evidence_ids"]
            and item["information_gap_ids"]
            and item["alternative_hypothesis_ids"]
        }
        if project["judgments"] and not analytically_linked_judgments:
            reasons.append("JUDGMENT_ANALYTIC_LINKS_INCOMPLETE")

        latest_decisions: dict[str, dict[str, Any]] = {}
        for decision in project["human_decisions"]:
            latest_decisions[decision["judgment_id"]] = decision
        approved_chains = 0
        for decision in latest_decisions.values():
            if (
                decision["decision"] not in {"confirm", "modify"}
                or decision["judgment_id"] not in judgments_by_id
                or decision["judgment_id"] not in analytically_linked_judgments
            ):
                continue
            peer_review = _latest_matching(
                project["reviews"],
                lambda review: review["review_type"] == "peer_review"
                and review["target_type"] == "decision"
                and review["target_id"] == decision["id"],
            )
            approval = _latest_matching(
                project["reviews"],
                lambda review: review["review_type"] == "approval"
                and review["target_type"] == "decision"
                and review["target_id"] == decision["id"],
            )
            if (
                peer_review
                and peer_review["outcome"] == "approved"
                and approval
                and approval["outcome"] == "approved"
            ):
                approved_chains += 1
        if approved_chains == 0:
            reasons.append("APPROVED_HUMAN_DECISION_CHAIN_MISSING")
        return reasons

    @staticmethod
    def _validate_cutoff(value: str) -> str:
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ResearchContractConflict("cutoff_at must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ResearchContractConflict("cutoff_at must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def create_export_manifest(
        self,
        project_id: str,
        body: ExportManifestCreateRequest,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor(user)
        self._preauthorize(project_id, actor, {"owner"})

        def mutation(project: dict[str, Any]):
            self._require_role(project, actor, {"owner"})
            reasons = self._export_readiness(project)
            cutoff_at = self._validate_cutoff(body.cutoff_at)
            cutoff_value = datetime.fromisoformat(cutoff_at.replace("Z", "+00:00"))
            now = self._validate_cutoff(self.clock())
            export_time = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if cutoff_value > export_time:
                reasons.append("REPORT_CUTOFF_AFTER_EXPORT_TIME")
            if any(
                item["captured_at"]
                and datetime.fromisoformat(
                    item["captured_at"].replace("Z", "+00:00")
                )
                > cutoff_value
                for item in project["evidence_items"]
            ):
                reasons.append("SOURCE_CAPTURE_AFTER_REPORT_CUTOFF")
            if reasons:
                raise ResearchWorkflowNotReady(reasons)
            previous_project_version = int(project["version"])
            project_version = previous_project_version + 1
            sources = [
                {
                    "evidence_id": item["id"],
                    "relation": item["relation"],
                    "summary": item["summary"],
                    "source_id": item["source_id"],
                    "source_title": item["source_title"],
                    "source_url": item["source_url"],
                    "original_anchor": item["original_anchor"],
                    "source_published_at": item["source_published_at"],
                    "article_id": item["article_id"],
                    "evidence_snapshot_id": item["evidence_snapshot_id"],
                    "content_sha256": item["content_sha256"],
                    "captured_at": item["captured_at"],
                    "parser_version": item["parser_version"],
                    "snapshot_status": item["snapshot_status"],
                    "provenance_status": item["provenance_status"],
                    "note": item["note"],
                }
                for item in project["evidence_items"]
            ]
            manifest = {
                "schema_version": "research-export-manifest-v2",
                "manifest_id": self.id_factory(),
                "export_version": len(project["export_manifests"]) + 1,
                "project_id": project["id"],
                "project_version": project_version,
                "previous_project_version": previous_project_version,
                "report_title": body.report_title,
                "created_at": now,
                "created_by": actor,
                "project_scope": {
                    "title": project["title"],
                    "description": project["description"],
                    "countries": list(project["scope_countries"]),
                    "capture_status": "captured_in_manifest",
                },
                "sources": sources,
                "cutoff": {
                    "at": cutoff_at,
                    "basis": body.cutoff_basis,
                },
                "method": body.method,
                "model": {
                    "status": "declared" if body.models else "not_used",
                    "items": [item.model_dump(mode="json") for item in body.models],
                },
                "uncertainty": body.uncertainty,
                "opposing_evidence": [
                    item for item in sources if item["relation"] == "opposing"
                ],
                "gaps": project["information_gaps"],
                "judgments": project["judgments"],
                "decisions": project["human_decisions"],
                "reviews": project["reviews"],
                "research_questions": project["research_questions"],
                "saved_searches": project["saved_searches"],
                "alternative_hypotheses": project["alternative_hypotheses"],
                "assurance": {
                    "workflow_gate": "passed",
                    "publication_status": "reviewed_draft",
                    "researcher_acceptance": "unavailable",
                    "source_verification": (
                        "evidence_ledger_verified"
                        if all(
                            source["provenance_status"] == "verified"
                            for source in sources
                        )
                        else (
                            "incomplete"
                            if any(
                                source["provenance_status"] == "incomplete"
                                for source in sources
                            )
                            else "researcher_declared_not_server_verified"
                        )
                    ),
                },
            }
            manifest["integrity_sha256"] = _canonical_sha256(manifest)
            project["export_manifests"].append(manifest)
            self._record_change(
                project,
                actor=actor,
                reason=body.reason,
                action="export_manifest.created",
                resource_type="export_manifest",
                resource_id=manifest["manifest_id"],
                changed_fields=["export_manifests[]"],
                timestamp=now,
            )
            return project, project

        return self.repository.mutate(
            project_id,
            expected_version=body.expected_version,
            mutation=mutation,
        )

    def get_export_manifest(
        self,
        project_id: str,
        export_version: int,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        project = self.get_project(project_id, user)
        manifest = next(
            (
                item
                for item in project["export_manifests"]
                if item["export_version"] == export_version
            ),
            None,
        )
        if manifest is None:
            raise ResearchContractConflict("export manifest version not found")
        return manifest

    def get_export_artifact(
        self,
        project_id: str,
        export_version: int,
        artifact_format: ArtifactFormat,
        user: dict[str, Any],
        *,
        export_fields: list[str] | tuple[str, ...] | None = None,
    ) -> ResearchExportArtifact:
        manifest = self.get_export_manifest(project_id, export_version, user)
        try:
            return build_research_export_artifact(
                manifest,
                artifact_format,
                export_fields=export_fields,
            )
        except (KeyError, ResearchArtifactError, TypeError) as exc:
            raise ResearchContractConflict(
                "persisted export manifest cannot be rendered"
            ) from exc

    def compare_export_manifests(
        self,
        project_id: str,
        *,
        from_export_version: int,
        to_export_version: int,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        # get_project enforces owner/reviewer/reader ACL before any sensitive
        # research body is selected for the comparison.
        project = self.get_project(project_id, user)
        manifests = {
            int(item["export_version"]): item
            for item in project["export_manifests"]
        }
        before = manifests.get(from_export_version)
        after = manifests.get(to_export_version)
        if before is None or after is None:
            raise ResearchContractConflict("export manifest version not found")
        return build_manifest_comparison(
            project_id=project_id,
            before=before,
            after=after,
        ).model_dump(mode="json")


__all__ = (
    "ResearchAccessDenied",
    "ResearchContractConflict",
    "ResearchWorkflowNotReady",
    "ResearchWorkflowService",
)
