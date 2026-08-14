"""Authenticated, privacy-minimal service-level measurement API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, status

from api.core.environment import string_setting
from api.features.service_level import (
    MAX_WINDOW_HOURS,
    ObservationReceipt,
    ObservationSubmission,
    Operation,
    ServiceLevelService,
    ServiceLevelStatus,
    ServiceLevelStore,
    ServiceLevelStoreUnavailable,
    ServiceLevelSummary,
)
from api.services.auth import get_current_admin_user, get_current_user_required


router = APIRouter(prefix="/api/service-level", tags=["service-level"])
_DEFAULT_ROOT = Path(
    string_setting("SERVICE_LEVEL_ROOT", "/root/data/web/service-level")
)
_service = ServiceLevelService(ServiceLevelStore(_DEFAULT_ROOT))


def get_service_level_service() -> ServiceLevelService:
    return _service


def _unavailable(exc: ServiceLevelStoreUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="service-level measurements are unavailable",
    )


@router.get("/status", response_model=ServiceLevelStatus)
def service_level_status(
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ServiceLevelService = Depends(get_service_level_service),
) -> ServiceLevelStatus:
    try:
        return service.status()
    except ServiceLevelStoreUnavailable as exc:
        raise _unavailable(exc) from exc


@router.get("/summary", response_model=ServiceLevelSummary)
def service_level_summary(
    window_hours: int = Query(default=24, ge=1, le=MAX_WINDOW_HOURS),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ServiceLevelService = Depends(get_service_level_service),
) -> ServiceLevelSummary:
    try:
        return service.summary(window_hours=window_hours)
    except ServiceLevelStoreUnavailable as exc:
        raise _unavailable(exc) from exc


@router.post(
    "/observations/{operation}",
    response_model=ObservationReceipt,
    status_code=status.HTTP_201_CREATED,
)
def record_service_level_observation(
    body: ObservationSubmission,
    operation: Operation = ApiPath(),
    _admin: dict[str, Any] = Depends(get_current_admin_user),
    service: ServiceLevelService = Depends(get_service_level_service),
) -> ObservationReceipt:
    try:
        service.record(
            operation=operation,
            outcome=body.outcome,
            duration_ms=body.duration_ms,
            observed_at=body.observed_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ServiceLevelStoreUnavailable as exc:
        raise _unavailable(exc) from exc
    return ObservationReceipt(operation=operation)


__all__ = ("get_service_level_service", "router")
