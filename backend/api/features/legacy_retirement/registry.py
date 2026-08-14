"""Registry for retired endpoints and their supported alternatives."""

from __future__ import annotations

from dataclasses import dataclass

from api.features.legacy_retirement.contracts import RetiredEndpointResponse


@dataclass(frozen=True)
class _RetiredEndpointSpec:
    alternatives: tuple[str, ...]


_RETIRED_ENDPOINT_SPECS = {
    "/api/opinion/micro-story-sub-events": _RetiredEndpointSpec(
        alternatives=(
            "/api/opinion/macro-event-clusters",
            "/api/opinion/event-news",
        )
    ),
    "/api/opinion/event-timeseries": _RetiredEndpointSpec(
        alternatives=("/api/opinion/event-news",)
    ),
    "/api/opinion/global-attention": _RetiredEndpointSpec(
        alternatives=("/api/opinion/overview",)
    ),
    "/api/opinion/sentiment-polarity": _RetiredEndpointSpec(
        alternatives=("/api/opinion/overview",)
    ),
    "/api/opinion/influence-index": _RetiredEndpointSpec(
        alternatives=("/api/opinion/overview",)
    ),
    "/api/opinion/composite-index": _RetiredEndpointSpec(
        alternatives=("/api/opinion/overview",)
    ),
    "/api/opinion/topic-breakdown": _RetiredEndpointSpec(
        alternatives=("/api/opinion/dimensions",)
    ),
    "/api/opinion/frame-breakdown": _RetiredEndpointSpec(
        alternatives=("/api/opinion/dimensions",)
    ),
    "/api/opinion/narrative-dispersion": _RetiredEndpointSpec(
        alternatives=(
            "/api/opinion/overview",
            "/api/opinion/dimensions",
        )
    ),
}

RETIRED_OPINION_ENDPOINTS = frozenset(_RETIRED_ENDPOINT_SPECS)

_RETIREMENT_MESSAGE = (
    "This legacy opinion endpoint was retired because its data source is no longer "
    "part of the supported runtime schema. Alternatives are not drop-in replacements."
)


def retired_endpoint_contract(endpoint: str) -> RetiredEndpointResponse:
    """Return the immutable public contract for a registered retired endpoint."""
    try:
        spec = _RETIRED_ENDPOINT_SPECS[endpoint]
    except KeyError as exc:
        raise ValueError(f"endpoint is not registered as retired: {endpoint}") from exc
    return RetiredEndpointResponse(
        endpoint=endpoint,
        message=_RETIREMENT_MESSAGE,
        alternatives=list(spec.alternatives),
    )
