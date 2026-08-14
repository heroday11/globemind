"""Application service for append-only model evaluation manifests."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone

from .contracts import (
    EvaluationManifest,
    EvaluationSummary,
    ModelAssuranceCatalog,
    ModelAssuranceStatus,
    StoredEvaluation,
)
from .evaluator import (
    ManifestRejected,
    evaluate_manifest,
    review_validity_reason,
)
from .storage import (
    AssuranceConflict,
    AssuranceNotFound,
    AssuranceStoreUnavailable,
    ModelAssuranceStore,
)

_ACTOR_REF = re.compile(r"^user:[1-9][0-9]*$")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManifestRejected("service timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class _ReviewChainState:
    def __init__(
        self,
        *,
        review_incomplete: bool,
        expiry_missing: bool,
        earliest_expiry: datetime | None,
    ) -> None:
        self.review_incomplete = review_incomplete
        self.expiry_missing = expiry_missing
        self.earliest_expiry = earliest_expiry


def _review_chain_state_index(
    entries: list[StoredEvaluation],
) -> dict[str, _ReviewChainState]:
    states: dict[str, _ReviewChainState] = {}
    for entry in entries:
        baseline_ref = entry.manifest.baseline
        parent = states.get(baseline_ref.evaluation_id) if baseline_ref else None
        parent_missing = baseline_ref is not None and parent is None
        review = entry.manifest.independent_review
        review_incomplete = bool(
            parent_missing
            or (parent is not None and parent.review_incomplete)
            or review is None
            or not review.independence_attestation
            or review.decision != "approved"
        )
        expiry_missing = bool(
            (parent is not None and parent.expiry_missing)
            or (review is not None and review.valid_until is None)
        )
        expiries = [
            value
            for value in (
                parent.earliest_expiry if parent is not None else None,
                _utc(review.valid_until)
                if review is not None and review.valid_until is not None
                else None,
            )
            if value is not None
        ]
        states[entry.manifest.evaluation_id] = _ReviewChainState(
            review_incomplete=review_incomplete,
            expiry_missing=expiry_missing,
            earliest_expiry=min(expiries) if expiries else None,
        )
    return states


def _review_chain_reasons(
    state: _ReviewChainState | None,
    *,
    as_of: datetime,
) -> tuple[str, ...]:
    if state is None:
        return ("BASELINE_ASSURANCE_INCOMPLETE",)
    reasons: list[str] = []
    if state.review_incomplete:
        reasons.append("BASELINE_REVIEW_INCOMPLETE")
    if state.expiry_missing:
        reasons.append("BASELINE_REVIEW_EXPIRY_NOT_DECLARED")
    if state.earliest_expiry is not None and state.earliest_expiry <= _utc(as_of):
        reasons.append("BASELINE_REVIEW_EXPIRED")
    return tuple(reasons)


def _current_as_of(
    entries: list[StoredEvaluation],
    value: datetime,
) -> datetime:
    as_of = _utc(value)
    if entries and as_of < _utc(entries[-1].stored_at):
        raise AssuranceStoreUnavailable(
            "service clock precedes the assurance ledger"
        )
    return as_of


def _summary(
    entry: StoredEvaluation,
    *,
    as_of: datetime | None = None,
    baseline_reasons: tuple[str, ...] = (),
) -> EvaluationSummary:
    manifest = entry.manifest
    result = entry.result
    release_eligible = result.release_eligible
    gate_state = result.gate_state
    rollback_action = result.rollback.action
    reason_codes = list(result.reason_codes)
    if as_of is not None and release_eligible:
        temporal_reason = review_validity_reason(
            manifest.independent_review,
            as_of=as_of,
        )
        if temporal_reason is not None:
            release_eligible = False
            gate_state = "blocked"
            rollback_action = "hold_release"
            if temporal_reason not in reason_codes:
                reason_codes.append(temporal_reason)
        for baseline_reason in baseline_reasons:
            release_eligible = False
            gate_state = "blocked"
            rollback_action = "hold_release"
            if baseline_reason not in reason_codes:
                reason_codes.append(baseline_reason)
    return EvaluationSummary(
        evaluation_id=manifest.evaluation_id,
        model_id=manifest.model.model_id,
        model_version=manifest.model.model_version,
        method_version=manifest.model.method_version,
        dataset_id=manifest.dataset.dataset_id,
        dataset_sha256=manifest.dataset.sha256,
        cutoff_at=manifest.dataset.cutoff_at,
        stored_at=entry.stored_at,
        entry_sha256=entry.entry_sha256,
        gate_state=gate_state,
        release_eligible=release_eligible,
        drift_state=result.drift.state,
        rollback_action=rollback_action,
        reason_codes=reason_codes,
    )


def _current_baseline_reason_index(
    entries: list[StoredEvaluation],
    *,
    as_of: datetime,
) -> dict[str, tuple[str, ...]]:
    """Revalidate every baseline chain in bounded chronological time."""

    entries_by_id: dict[str, StoredEvaluation] = {}
    reasons_by_id: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        reasons: list[str] = []

        def add(reason: str) -> None:
            if reason not in reasons:
                reasons.append(reason)

        baseline_ref = entry.manifest.baseline
        if baseline_ref is not None:
            baseline = entries_by_id.get(baseline_ref.evaluation_id)
            if (
                baseline is None
                or baseline.entry_sha256 != baseline_ref.entry_sha256
            ):
                add("BASELINE_ASSURANCE_INCOMPLETE")
            else:
                review = baseline.manifest.independent_review
                temporal_reason = review_validity_reason(review, as_of=as_of)
                if temporal_reason == "REVIEW_EXPIRY_NOT_DECLARED":
                    add("BASELINE_REVIEW_EXPIRY_NOT_DECLARED")
                elif temporal_reason == "INDEPENDENT_REVIEW_EXPIRED":
                    add("BASELINE_REVIEW_EXPIRED")
                elif temporal_reason is not None:
                    add("BASELINE_REVIEW_INCOMPLETE")

                allowed_bootstrap_reasons = {"BASELINE_NOT_PROVIDED"}
                if not (
                    set(baseline.result.reason_codes).issubset(
                        allowed_bootstrap_reasons
                    )
                    and baseline.result.coverage.state == "complete"
                    and review is not None
                    and review.independence_attestation
                    and review.decision == "approved"
                    and temporal_reason is None
                ):
                    add("BASELINE_ASSURANCE_INCOMPLETE")
                for ancestor_reason in reasons_by_id.get(
                    baseline.manifest.evaluation_id,
                    ("BASELINE_ASSURANCE_INCOMPLETE",),
                ):
                    add(ancestor_reason)
                if reasons:
                    add("BASELINE_ASSURANCE_INCOMPLETE")

        reasons_by_id[entry.manifest.evaluation_id] = tuple(reasons)
        entries_by_id[entry.manifest.evaluation_id] = entry
    return reasons_by_id


class ModelAssuranceService:
    def __init__(
        self,
        store: ModelAssuranceStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _verified_entries(self) -> list[StoredEvaluation]:
        entries = self._store.list_entries()
        review_chain_states = _review_chain_state_index(entries)
        verified: list[StoredEvaluation] = []
        by_id: dict[str, StoredEvaluation] = {}
        seen_review_ids: set[str] = set()
        previous_stored_at: datetime | None = None
        for entry in entries:
            if (
                previous_stored_at is not None
                and _utc(entry.stored_at) < previous_stored_at
            ):
                raise AssuranceStoreUnavailable(
                    "stored assurance timestamps are not monotonic"
                )
            review = entry.manifest.independent_review
            if review is not None and review.review_id in seen_review_ids:
                raise AssuranceStoreUnavailable(
                    "stored assurance review id is not unique"
                )
            baseline = None
            if entry.manifest.baseline is not None:
                baseline = by_id.get(entry.manifest.baseline.evaluation_id)
            dependency_reasons = (
                _review_chain_reasons(
                    review_chain_states.get(baseline.manifest.evaluation_id),
                    as_of=entry.stored_at,
                )
                if baseline is not None
                else ()
            )
            try:
                recomputed = evaluate_manifest(
                    entry.manifest,
                    baseline=baseline,
                    baseline_dependency_reasons=dependency_reasons,
                    evaluated_at=entry.stored_at,
                    submitted_by=entry.submitted_by,
                )
            except ManifestRejected as exc:
                raise AssuranceStoreUnavailable(
                    "stored assurance manifest cannot be recomputed"
                ) from exc
            if recomputed.model_dump(mode="json") != entry.result.model_dump(
                mode="json"
            ):
                raise AssuranceStoreUnavailable(
                    "stored assurance result differs from recomputed metrics"
                )
            verified.append(entry)
            by_id[entry.manifest.evaluation_id] = entry
            if review is not None:
                seen_review_ids.add(review.review_id)
            previous_stored_at = _utc(entry.stored_at)
        return verified

    def submit(
        self,
        manifest: EvaluationManifest,
        *,
        submitted_by: str,
    ) -> StoredEvaluation:
        if _ACTOR_REF.fullmatch(submitted_by) is None:
            raise ManifestRejected("submitted_by must be a stable user reference")
        entries = self._verified_entries()
        if any(
            entry.manifest.evaluation_id == manifest.evaluation_id
            for entry in entries
        ):
            raise AssuranceConflict("evaluation id is append-only and already exists")
        review = manifest.independent_review
        if review is not None and any(
            entry.manifest.independent_review is not None
            and entry.manifest.independent_review.review_id == review.review_id
            for entry in entries
        ):
            raise ManifestRejected("independent review id was already used")
        baseline = None
        dependency_reasons: tuple[str, ...] = ()
        if manifest.baseline is not None:
            baseline = next(
                (
                    entry
                    for entry in entries
                    if entry.manifest.evaluation_id
                    == manifest.baseline.evaluation_id
                ),
                None,
            )
            if baseline is None:
                raise ManifestRejected("declared baseline does not exist")
        stored_at = _utc(self._now())
        if entries and stored_at < _utc(entries[-1].stored_at):
            raise ManifestRejected("service clock precedes the latest ledger entry")
        if baseline is not None:
            review_chain_states = _review_chain_state_index(entries)
            dependency_reasons = _review_chain_reasons(
                review_chain_states.get(baseline.manifest.evaluation_id),
                as_of=stored_at,
            )
        result = evaluate_manifest(
            manifest,
            baseline=baseline,
            baseline_dependency_reasons=dependency_reasons,
            evaluated_at=stored_at,
            submitted_by=submitted_by,
        )
        return self._store.append(
            manifest=manifest,
            result=result,
            submitted_by=submitted_by,
            stored_at=stored_at,
        )

    def list_evaluations(
        self,
        *,
        limit: int = 100,
        model_id: str | None = None,
    ) -> list[EvaluationSummary]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be in [1, 500]")
        entries = self._verified_entries()
        now = _current_as_of(entries, self._now())
        baseline_reason_index = _current_baseline_reason_index(
            entries,
            as_of=now,
        )
        if model_id is not None:
            entries = [
                entry
                for entry in entries
                if entry.manifest.model.model_id == model_id
            ]
        return [
            _summary(
                entry,
                as_of=now,
                baseline_reasons=baseline_reason_index.get(
                    entry.manifest.evaluation_id,
                    ("BASELINE_ASSURANCE_INCOMPLETE",),
                ),
            )
            for entry in reversed(entries[-limit:])
        ]

    def get_evaluation(self, evaluation_id: str) -> StoredEvaluation:
        entry = next(
            (
                item
                for item in self._verified_entries()
                if item.manifest.evaluation_id == evaluation_id
            ),
            None,
        )
        if entry is None:
            raise AssuranceNotFound("model assurance evaluation was not found")
        return entry

    def latest_release_eligible_evaluation(
        self,
        *,
        model_id: str,
        model_version: str,
        method_version: str,
    ) -> StoredEvaluation | None:
        """Return the latest exact-match gate result after full chain verification."""

        if not model_id or not model_version or not method_version:
            raise ValueError("exact model assurance identity is required")
        entries = self._verified_entries()
        now = _current_as_of(entries, self._now())
        baseline_reason_index = _current_baseline_reason_index(
            entries,
            as_of=now,
        )
        return next(
            (
                entry
                for entry in reversed(entries)
                if _summary(
                    entry,
                    as_of=now,
                    baseline_reasons=baseline_reason_index.get(
                        entry.manifest.evaluation_id,
                        ("BASELINE_ASSURANCE_INCOMPLETE",),
                    ),
                ).release_eligible
                and entry.manifest.model.model_id == model_id
                and entry.manifest.model.model_version == model_version
                and entry.manifest.model.method_version == method_version
            ),
            None,
        )

    def status(self) -> ModelAssuranceStatus:
        entries = self._verified_entries()
        now = _current_as_of(entries, self._now())
        if not entries:
            return ModelAssuranceStatus(
                generated_at=now,
                available=False,
                operational_state="not_observed",
                release_status="blocked",
                gold_standard_state="not_observed",
                evaluation_count=0,
                eligible_count=0,
                latest=None,
                reason_codes=[
                    "NO_EVALUATION_MANIFESTS",
                    "GOLD_STANDARD_NOT_OBSERVED",
                    "RELEASE_BLOCKED",
                ],
            )

        latest = entries[-1]
        baseline_reason_index = _current_baseline_reason_index(
            entries,
            as_of=now,
        )
        latest_summary = _summary(
            latest,
            as_of=now,
            baseline_reasons=baseline_reason_index.get(
                latest.manifest.evaluation_id,
                ("BASELINE_ASSURANCE_INCOMPLETE",),
            ),
        )
        eligible_count = sum(
            _summary(
                entry,
                as_of=now,
                baseline_reasons=baseline_reason_index.get(
                    entry.manifest.evaluation_id,
                    ("BASELINE_ASSURANCE_INCOMPLETE",),
                ),
            ).release_eligible
            for entry in entries
        )
        gold_attested = any(
            entry.manifest.dataset.evaluation_role == "gold_standard"
            and entry.manifest.dataset.gold_standard_status
            == "independently_reviewed"
            and entry.manifest.independent_review is not None
            and entry.manifest.independent_review.independence_attestation
            and entry.manifest.independent_review.decision == "approved"
            for entry in entries
        )
        reasons = list(latest_summary.reason_codes)
        if not latest_summary.release_eligible and "RELEASE_BLOCKED" not in reasons:
            reasons.append("RELEASE_BLOCKED")
        return ModelAssuranceStatus(
            generated_at=now,
            available=True,
            operational_state="observed",
            release_status=(
                "eligible" if latest_summary.release_eligible else "blocked"
            ),
            gold_standard_state=(
                "manifest_attested" if gold_attested else "not_observed"
            ),
            evaluation_count=len(entries),
            eligible_count=eligible_count,
            latest=latest_summary,
            reason_codes=reasons,
        )

    def catalog(self) -> ModelAssuranceCatalog:
        status = self.status()
        return ModelAssuranceCatalog(**status.model_dump())


__all__ = (
    "ManifestRejected",
    "ModelAssuranceService",
)
