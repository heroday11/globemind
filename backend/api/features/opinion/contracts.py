"""HTTP-compatible request contracts and transport-neutral trend inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class OpinionFeedbackPayload(BaseModel):
    """Minimal structured correction; this contract never grants training use."""

    model_config = ConfigDict(extra="forbid", strict=True)

    news_id: StrictInt = Field(gt=0)
    correction: Literal["irrelevant", "too_positive", "too_negative", "correct"]
    purpose: Literal["quality_correction"]
    training_consent: Literal[False]
    training_opt_out: Literal[True]


class OpinionRefreshPayload(BaseModel):
    days: int = 60
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    force: bool = False


@dataclass(frozen=True)
class OpinionTrendQuery:
    """Normalized inputs for the read-only stance trend use case."""

    days: int
    china_min_score: float
    sentiment_filter: str
    region: str | None = None
    language: str | None = None
    media_source: str | None = None
    event_family: str | None = None


__all__ = (
    "OpinionFeedbackPayload",
    "OpinionRefreshPayload",
    "OpinionTrendQuery",
)
