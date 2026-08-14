"""Authenticated V1 engineering asset and processing inventory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from api.features.operations import (
    build_api_documentation_contract,
    build_asset_inventory,
    build_bounded_openapi_document,
)
from api.services.auth import get_current_admin_user

router = APIRouter(prefix="/api/governance", tags=["governance"])
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DOCUMENTATION_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/asset-inventory")
def asset_inventory(
    request: Request,
    _admin: dict[str, Any] = Depends(get_current_admin_user),
):
    return build_asset_inventory(request.app, repository_root=_REPOSITORY_ROOT)


@router.get("/openapi.json", include_in_schema=False)
def authenticated_openapi(
    request: Request,
    _admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Expose the exact running API schema without making production docs public."""
    try:
        content = build_bounded_openapi_document(request.app)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "API_DOCUMENTATION_UNAVAILABLE"},
            headers=_DOCUMENTATION_HEADERS,
        ) from exc
    return Response(
        content=content,
        media_type="application/json",
        headers=_DOCUMENTATION_HEADERS,
    )


@router.get("/api-contract")
def authenticated_api_contract(
    request: Request,
    _admin: dict[str, Any] = Depends(get_current_admin_user),
):
    """Describe schema access and unresolved version/rate-limit assurances."""
    try:
        contract = build_api_documentation_contract(request.app)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "API_DOCUMENTATION_UNAVAILABLE"},
            headers=_DOCUMENTATION_HEADERS,
        ) from exc
    return JSONResponse(content=contract, headers=_DOCUMENTATION_HEADERS)


__all__ = ("router",)
