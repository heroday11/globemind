"""Stable response models for endpoint retirement."""

from typing import Literal

from pydantic import BaseModel, Field


class RetiredEndpointResponse(BaseModel):
    """HTTP 410 response shared by retired endpoint mounts."""

    ok: Literal[False] = False
    code: Literal["endpoint_retired"] = "endpoint_retired"
    status: Literal[410] = 410
    endpoint: str
    message: str
    retired_in: Literal["v0.10"] = "v0.10"
    alternatives: list[str] = Field(default_factory=list)
