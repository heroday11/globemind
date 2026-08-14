"""Bounded request correlation helpers that never consume request secrets."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_ROUTE_TEMPLATE = re.compile(r"^/[A-Za-z0-9_{}:./-]*$")
_METHOD = re.compile(r"^[A-Z]{3,16}$")


def resolve_request_id(
    candidate: Any,
    *,
    factory: Callable[[], Any] = uuid.uuid4,
) -> str:
    """Accept a bounded opaque correlation ID or generate one locally."""
    value = str(candidate or "").strip()
    if _REQUEST_ID.fullmatch(value):
        return value
    generated = getattr(factory(), "hex", "")
    if not isinstance(generated, str) or not re.fullmatch(r"[a-f0-9]{32}", generated):
        raise RuntimeError("request id factory returned an invalid identifier")
    return generated


def safe_route_template(scope: Mapping[str, Any]) -> str:
    """Return a static route template, never the user-controlled URL path."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if (
        not isinstance(template, str)
        or len(template) > 256
        or _ROUTE_TEMPLATE.fullmatch(template) is None
    ):
        return "unmatched"
    return template


def safe_request_method(value: Any) -> str:
    method = str(value or "").upper()
    return method if _METHOD.fullmatch(method) else "UNKNOWN"


__all__ = (
    "REQUEST_ID_HEADER",
    "resolve_request_id",
    "safe_request_method",
    "safe_route_template",
)
