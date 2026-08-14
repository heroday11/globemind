"""Fail-closed governance receipt for user-submitted opinion corrections.

The repository does not contain approved retention, privacy, legal, model-owner,
or human-review evidence for using these records as training data.  This module
therefore exposes only a non-training receipt and an unconditional training
gate.  A future approval workflow must replace the gate rather than accepting
self-asserted request fields as evidence.
"""

from __future__ import annotations

from typing import Any, Mapping, NoReturn


class FeedbackTrainingUseBlocked(RuntimeError):
    """Raised while an approved feedback-to-training workflow is unavailable."""


def build_feedback_governance_receipt() -> dict[str, Any]:
    """Return a fresh JSON-safe description of the enforced intake boundary."""

    return {
        "schema_version": "opinion-feedback-governance.v1",
        "purpose": "quality_correction",
        "stored_content": "structured_label_only",
        "free_text_accepted": False,
        "training_consent": False,
        "training_opt_out": True,
        "training_use_status": "prohibited_without_approval",
        "training_export_status": "not_configured",
        "deidentification_status": "not_verified",
        "retention_status": "not_approved",
        "retention_period_days": None,
        "review_state": "review_required",
        "eligible_for_training": False,
        "eligible_for_gold": False,
    }


def require_feedback_training_approval(
    _self_asserted_evidence: Mapping[str, Any] | None = None,
) -> NoReturn:
    """Block exports until externally governed approval and review exist."""

    raise FeedbackTrainingUseBlocked("FEEDBACK_TRAINING_GOVERNANCE_NOT_CONFIGURED")


__all__ = (
    "FeedbackTrainingUseBlocked",
    "build_feedback_governance_receipt",
    "require_feedback_training_approval",
)
