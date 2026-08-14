"""Deterministic structured diffs between persisted export manifests."""

from __future__ import annotations

import copy
from typing import Any, Iterable

from .contracts import ResearchVersionComparison, VersionDiffCategory


def _indexed(rows: Iterable[dict[str, Any]], id_field: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for row in rows:
        stable_id = str(row.get(id_field) or "")
        if not stable_id or stable_id in indexed:
            raise ValueError("manifest comparison requires unique stable identifiers")
        indexed[stable_id] = copy.deepcopy(row)
    return indexed


def _changed_fields(before: Any, after: Any) -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return sorted(
            key
            for key in set(before) | set(after)
            if before.get(key) != after.get(key)
        )
    return ["value"]


def _category(
    category_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> VersionDiffCategory:
    before_ids = set(before)
    after_ids = set(after)
    return VersionDiffCategory(
        id=category_id,
        added=[
            {"id": stable_id, "value": after[stable_id]}
            for stable_id in sorted(after_ids - before_ids)
        ],
        removed=[
            {"id": stable_id, "value": before[stable_id]}
            for stable_id in sorted(before_ids - after_ids)
        ],
        modified=[
            {
                "id": stable_id,
                "before": before[stable_id],
                "after": after[stable_id],
                "changed_fields": _changed_fields(
                    before[stable_id], after[stable_id]
                ),
            }
            for stable_id in sorted(before_ids & after_ids)
            if before[stable_id] != after[stable_id]
        ],
    )


def _sources(manifest: dict[str, Any], relation: str) -> dict[str, Any]:
    return _indexed(
        (
            source
            for source in manifest["sources"]
            if source["relation"] == relation
        ),
        "evidence_id",
    )


def _reviews(manifest: dict[str, Any], review_type: str) -> dict[str, Any]:
    return _indexed(
        (
            review
            for review in manifest["reviews"]
            if review["review_type"] == review_type
        ),
        "id",
    )


def build_manifest_comparison(
    *,
    project_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> ResearchVersionComparison:
    """Compare immutable manifests; callers must enforce project ACL first."""
    if before.get("project_id") != project_id or after.get("project_id") != project_id:
        raise ValueError("comparison manifests do not belong to the project")

    category_inputs = (
        (
            "research_questions",
            _indexed(before["research_questions"], "id"),
            _indexed(after["research_questions"], "id"),
        ),
        (
            "saved_searches",
            _indexed(before["saved_searches"], "id"),
            _indexed(after["saved_searches"], "id"),
        ),
        ("support_evidence", _sources(before, "support"), _sources(after, "support")),
        (
            "opposing_evidence",
            _sources(before, "opposing"),
            _sources(after, "opposing"),
        ),
        (
            "background_evidence",
            _sources(before, "background"),
            _sources(after, "background"),
        ),
        (
            "information_gaps",
            _indexed(before["gaps"], "id"),
            _indexed(after["gaps"], "id"),
        ),
        (
            "alternative_hypotheses",
            _indexed(before["alternative_hypotheses"], "id"),
            _indexed(after["alternative_hypotheses"], "id"),
        ),
        (
            "judgments",
            _indexed(before["judgments"], "id"),
            _indexed(after["judgments"], "id"),
        ),
        (
            "human_decisions",
            _indexed(before["decisions"], "id"),
            _indexed(after["decisions"], "id"),
        ),
        ("peer_reviews", _reviews(before, "peer_review"), _reviews(after, "peer_review")),
        ("approvals", _reviews(before, "approval"), _reviews(after, "approval")),
        ("method", {"method": before["method"]}, {"method": after["method"]}),
        ("model", {"model": before["model"]}, {"model": after["model"]}),
        ("cutoff", {"cutoff": before["cutoff"]}, {"cutoff": after["cutoff"]}),
    )
    categories = [
        _category(category_id, before_rows, after_rows)
        for category_id, before_rows, after_rows in category_inputs
    ]
    return ResearchVersionComparison(
        project_id=project_id,
        from_export={
            "manifest_id": before["manifest_id"],
            "export_version": before["export_version"],
            "project_version": before["project_version"],
            "created_at": before["created_at"],
        },
        to_export={
            "manifest_id": after["manifest_id"],
            "export_version": after["export_version"],
            "project_version": after["project_version"],
            "created_at": after["created_at"],
        },
        categories=categories,
        summary={
            "added": sum(len(category.added) for category in categories),
            "removed": sum(len(category.removed) for category in categories),
            "modified": sum(len(category.modified) for category in categories),
        },
        access={
            "content_visibility": "project-acl",
            "source_manifests_persisted": True,
            "comparison_persisted": False,
            "audit_event_created": False,
            "audit_body_fields": "none",
        },
    )


__all__ = ("build_manifest_comparison",)
