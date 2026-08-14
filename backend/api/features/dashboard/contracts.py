"""Stable request contracts for dashboard routes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NewsTranslateParagraphRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=False,
    )

    text: str = Field(..., min_length=1, max_length=6000)
    target_language: Literal["zh-Hans"] = "zh-Hans"
    source_language: str = Field(
        default="und",
        min_length=2,
        max_length=48,
        pattern=r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|und)$",
    )

    @field_validator("text")
    @classmethod
    def reject_unsafe_controls(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("translation text must contain non-whitespace content")
        if any(
            (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ):
            raise ValueError("translation text contains unsafe Unicode characters")
        return value


__all__ = ("NewsTranslateParagraphRequest",)
