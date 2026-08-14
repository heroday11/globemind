"""HTTP-compatible contracts and application errors for assistant schedules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

_SAFE_SCHEDULE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")


class AssistantSchedulePayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    topic: str = Field(..., min_length=1, max_length=300)
    prompt: str = Field(default="", max_length=6000)
    cadence: Literal[
        "manual",
        "hourly",
        "every_6_hours",
        "every_12_hours",
        "daily",
        "weekly",
        "custom_hours",
    ] = "daily"
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    time_of_day: str = Field(
        default="08:30",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
    )
    day_of_week: int = Field(default=0, ge=0, le=6)
    interval_hours: int = Field(default=24, ge=1, le=720)
    enabled: bool = True
    report_type: str = Field(default="brief", max_length=64)
    time_range: str = Field(default="24h", max_length=64)
    perspective: str = Field(default="综合研判", max_length=100)
    include_sources: bool = True
    include_charts: bool = False
    pinned_workspace: str = Field(default="", max_length=100)
    favorite_context: dict[str, Any] | None = None
    knowledge_context: dict[str, Any] | None = None

    def to_schedule_payload(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()  # pragma: no cover - Pydantic v1 compatibility


class AssistantScheduleApplicationError(Exception):
    """Base class for schedule errors that the HTTP adapter understands."""


class AssistantScheduleIdentityError(AssistantScheduleApplicationError, ValueError):
    """The authenticated claims cannot identify a schedule owner."""


class AssistantScheduleNotFound(AssistantScheduleApplicationError, LookupError):
    """The requested schedule does not exist for this owner."""


class AssistantScheduleConflict(AssistantScheduleApplicationError, RuntimeError):
    """The requested schedule operation conflicts with an active run."""


class AssistantScheduleExecutionFailed(AssistantScheduleApplicationError, RuntimeError):
    """The schedule backend failed while executing a manual run."""


@dataclass(frozen=True)
class AssistantScheduleIdentity:
    username: str
    user_id: int

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> "AssistantScheduleIdentity":
        username = str(claims.get("username") or "").strip()
        if (
            username in {"", ".", ".."}
            or not _SAFE_SCHEDULE_USERNAME_RE.fullmatch(username)
        ):
            raise AssistantScheduleIdentityError("当前用户缺少 username")
        raw_user_id = claims.get("user_id")
        if type(raw_user_id) is int:
            user_id = raw_user_id
        elif isinstance(raw_user_id, str) and re.fullmatch(
            r"[1-9][0-9]{0,18}",
            raw_user_id.strip(),
        ):
            user_id = int(raw_user_id)
        else:
            user_id = 0
        if user_id <= 0:
            raise AssistantScheduleIdentityError("当前用户缺少有效 user_id")
        return cls(username=username, user_id=user_id)


__all__ = (
    "AssistantScheduleApplicationError",
    "AssistantScheduleConflict",
    "AssistantScheduleExecutionFailed",
    "AssistantScheduleIdentity",
    "AssistantScheduleIdentityError",
    "AssistantScheduleNotFound",
    "AssistantSchedulePayload",
)
