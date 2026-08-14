from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(
        ...,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    path: str = Field(default="/", max_length=256)
    visibility: Literal["visible", "hidden", "prerender"] | None = None
