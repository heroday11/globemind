"""Validation helpers for user-configured HTTP provider endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


class ProviderBaseUrlError(ValueError):
    """A provider endpoint is unsafe or cannot be parsed unambiguously."""


def normalize_provider_base_url(value: str) -> str:
    """Return a trimmed absolute HTTP(S) base URL without embedded secrets.

    Paths are intentionally allowed because OpenAI-compatible gateways commonly
    expose ``/v1`` below an origin. Query strings, fragments, user information,
    backslashes, controls, and ambiguous/non-HTTP schemes are rejected.
    """

    if type(value) is not str:
        raise ProviderBaseUrlError("provider base URL must be a string")
    normalized = value.strip()
    if not normalized:
        return ""
    if len(normalized) > 2048:
        raise ProviderBaseUrlError("provider base URL is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ProviderBaseUrlError("provider base URL contains control characters")
    if any(char.isspace() for char in normalized):
        raise ProviderBaseUrlError("provider base URL contains whitespace")
    if "\\" in normalized:
        raise ProviderBaseUrlError("provider base URL contains a backslash")
    if "?" in normalized or "#" in normalized:
        raise ProviderBaseUrlError("provider base URL cannot contain query or fragment data")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ProviderBaseUrlError("provider base URL is malformed") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ProviderBaseUrlError("provider base URL must use HTTP or HTTPS")
    if not parsed.netloc or parsed.hostname is None:
        raise ProviderBaseUrlError("provider base URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderBaseUrlError("provider base URL cannot contain credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ProviderBaseUrlError("provider base URL port is invalid")
    return normalized


def provider_base_url_or_none(value: Any) -> str | None:
    """Sanitize a legacy stored value for safe public serialization."""

    if value is None:
        return None
    try:
        return normalize_provider_base_url(value)
    except ProviderBaseUrlError:
        return None


__all__ = (
    "ProviderBaseUrlError",
    "normalize_provider_base_url",
    "provider_base_url_or_none",
)
