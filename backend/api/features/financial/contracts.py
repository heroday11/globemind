from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)


class AlertRulePayload(BaseModel):
    id: str | None = Field(
        None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    metric: str | None = Field(None, min_length=1, max_length=160)
    unit: str = Field("", max_length=32)
    current: FiniteFloat | None = None
    threshold: FiniteFloat | None = None
    baseline: FiniteFloat | None = None
    severity: str | None = Field(None, max_length=16)
    breached: bool | None = None
    trend: str | None = Field(None, max_length=16)
    metric_id: str | None = Field(None, max_length=128)


AlertTriageAction = Literal[
    "acknowledge",
    "escalate",
    "mark_false_positive",
    "resolve",
    "postmortem",
]
FalsePositiveClassification = Literal[
    "data_quality",
    "duplicate_signal",
    "threshold_miscalibration",
    "known_activity",
    "insufficient_context",
]
EscalationTargetRole = Literal[
    "financial_duty_officer",
    "data_quality_reviewer",
    "research_lead",
    "security_duty_officer",
]
PostmortemOutcome = Literal[
    "confirmed_response",
    "process_improvement_identified",
    "no_follow_up_required",
]


class AlertTriageMutation(BaseModel):
    """Strict, optimistic-concurrency contract for one lifecycle event."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    action: AlertTriageAction
    reason: str = Field(min_length=3, max_length=1000)
    expected_previous_event_id: str | None
    expected_previous_event_sha256: str | None
    false_positive_classification: FalsePositiveClassification | None = None
    escalation_target_role: EscalationTargetRole | None = None
    postmortem_outcome: PostmortemOutcome | None = None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("reason cannot contain control characters")
        return value

    @field_validator("expected_previous_event_id")
    @classmethod
    def validate_expected_event_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("fat-") or len(value) > 160:
            raise ValueError("expected previous event id is invalid")
        allowed = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        )
        if any(character not in allowed for character in value):
            raise ValueError("expected previous event id is invalid")
        return value

    @field_validator("expected_previous_event_sha256")
    @classmethod
    def validate_expected_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("expected previous event hash is invalid")
        return value

    @model_validator(mode="after")
    def validate_action_fields(self) -> "AlertTriageMutation":
        previous_pair = (
            self.expected_previous_event_id,
            self.expected_previous_event_sha256,
        )
        if (previous_pair[0] is None) != (previous_pair[1] is None):
            raise ValueError("expected previous event id and hash must be provided together")

        expected_optional = {
            "mark_false_positive": "false_positive_classification",
            "escalate": "escalation_target_role",
            "postmortem": "postmortem_outcome",
        }
        values = {
            "false_positive_classification": self.false_positive_classification,
            "escalation_target_role": self.escalation_target_role,
            "postmortem_outcome": self.postmortem_outcome,
        }
        required = expected_optional.get(self.action)
        for field_name, value in values.items():
            if field_name == required and value is None:
                raise ValueError(f"{field_name} is required for {self.action}")
            if field_name != required and value is not None:
                raise ValueError(f"{field_name} is not allowed for {self.action}")
        return self


__all__ = (
    "AlertRulePayload",
    "AlertTriageAction",
    "AlertTriageMutation",
    "EscalationTargetRole",
    "FalsePositiveClassification",
    "PostmortemOutcome",
)
