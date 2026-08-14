"""HTTP-compatible input contracts for graph briefing."""

from typing import Any

from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator


class MicroNewsBatchBody(BaseModel):
    """Batch request for representative news under current L2 chains."""

    event_ids: list[StrictStr | StrictInt] = Field(default_factory=list, max_length=800)
    limit_per: int = Field(25, ge=1, le=100)

    @field_validator("event_ids", mode="before")
    @classmethod
    def reject_boolean_ids(cls, value: Any) -> Any:
        if isinstance(value, list) and any(isinstance(item, bool) for item in value):
            raise ValueError("boolean graph ids are not allowed")
        return value
