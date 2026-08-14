"""Public contracts for intentionally retired HTTP endpoints."""

from api.features.legacy_retirement.contracts import RetiredEndpointResponse
from api.features.legacy_retirement.registry import (
    RETIRED_OPINION_ENDPOINTS,
    retired_endpoint_contract,
)

__all__ = (
    "RETIRED_OPINION_ENDPOINTS",
    "RetiredEndpointResponse",
    "retired_endpoint_contract",
)
