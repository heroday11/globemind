"""Authenticated model-assurance ledger routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    Response,
    status,
)

from api.core.environment import string_setting
from api.features.model_assurance import (
    AssuranceConflict,
    AssuranceNotFound,
    AssuranceStoreUnavailable,
    EvaluationManifest,
    EvaluationSummary,
    GenerativeEvaluationSurfaceInventory,
    ManifestRejected,
    ModelAssuranceCatalog,
    ModelAssuranceService,
    ModelAssuranceStatus,
    ModelAssuranceStore,
    ModelOutputSurfaceInventory,
    StoredEvaluation,
    build_generative_evaluation_surface_inventory,
    build_model_output_surface_inventory,
)
from api.services.auth import get_current_admin_user, get_current_user_required


router = APIRouter(prefix="/api/model-assurance", tags=["model-assurance"])
_DEFAULT_ROOT = Path(
    string_setting("MODEL_ASSURANCE_ROOT", "/root/data/web/model_assurance")
)
_service = ModelAssuranceService(ModelAssuranceStore(_DEFAULT_ROOT))
_MAX_MANIFEST_BODY_BYTES = 4 * 1024 * 1024


def get_model_assurance_service() -> ModelAssuranceService:
    return _service


def _actor_ref(user: dict[str, Any]) -> str:
    raw = user.get("user_id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise HTTPException(status_code=403, detail="管理员身份缺少稳定用户 ID")
    return f"user:{raw}"


def _store_failure(exc: AssuranceStoreUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail="模型保障账本当前不可安全读取")


def _reject_duplicate_manifest_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _reject_non_finite_manifest_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


async def _require_unambiguous_manifest_json(request: Request) -> None:
    body = await request.body()
    if not body or len(body) > _MAX_MANIFEST_BODY_BYTES:
        raise HTTPException(status_code=422, detail="评测清单 JSON 大小无效")
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_duplicate_manifest_keys,
            parse_constant=_reject_non_finite_manifest_number,
        )
    except (TypeError, UnicodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="评测清单必须是无重复键的有限 JSON",
        ) from exc


@router.get("/catalog", response_model=ModelAssuranceCatalog)
def model_assurance_catalog(
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ModelAssuranceService = Depends(get_model_assurance_service),
):
    try:
        return service.catalog()
    except AssuranceStoreUnavailable as exc:
        raise _store_failure(exc) from exc


@router.get("/status", response_model=ModelAssuranceStatus)
def model_assurance_status(
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ModelAssuranceService = Depends(get_model_assurance_service),
):
    try:
        return service.status()
    except AssuranceStoreUnavailable as exc:
        raise _store_failure(exc) from exc


@router.get("/surfaces", response_model=ModelOutputSurfaceInventory)
def model_output_surface_inventory(
    response: Response,
    _user: dict[str, Any] = Depends(get_current_user_required),
):
    response.headers["Cache-Control"] = "private, no-store"
    return build_model_output_surface_inventory()


@router.get(
    "/generative-evaluation/surfaces",
    response_model=GenerativeEvaluationSurfaceInventory,
)
def generative_evaluation_surface_inventory(
    response: Response,
    _user: dict[str, Any] = Depends(get_current_user_required),
):
    response.headers["Cache-Control"] = "private, no-store"
    return build_generative_evaluation_surface_inventory()


@router.get("/evaluations", response_model=list[EvaluationSummary])
def list_model_evaluations(
    limit: int = Query(default=100, ge=1, le=500),
    model_id: str | None = Query(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
    ),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ModelAssuranceService = Depends(get_model_assurance_service),
):
    try:
        return service.list_evaluations(limit=limit, model_id=model_id)
    except AssuranceStoreUnavailable as exc:
        raise _store_failure(exc) from exc


@router.get("/evaluations/{evaluation_id}", response_model=StoredEvaluation)
def get_model_evaluation(
    evaluation_id: str = ApiPath(
        pattern=r"^eval\.[a-z0-9][a-z0-9_.-]{1,119}$"
    ),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ModelAssuranceService = Depends(get_model_assurance_service),
):
    try:
        return service.get_evaluation(evaluation_id)
    except AssuranceNotFound as exc:
        raise HTTPException(status_code=404, detail="未找到模型评测记录") from exc
    except AssuranceStoreUnavailable as exc:
        raise _store_failure(exc) from exc


@router.post(
    "/evaluations",
    response_model=StoredEvaluation,
    status_code=status.HTTP_201_CREATED,
)
def submit_model_evaluation(
    manifest: EvaluationManifest,
    _strict_json: None = Depends(_require_unambiguous_manifest_json),
    admin: dict[str, Any] = Depends(get_current_admin_user),
    service: ModelAssuranceService = Depends(get_model_assurance_service),
):
    try:
        return service.submit(manifest, submitted_by=_actor_ref(admin))
    except ManifestRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssuranceConflict as exc:
        raise HTTPException(status_code=409, detail="评测 ID 已存在且不可覆盖") from exc
    except AssuranceStoreUnavailable as exc:
        raise _store_failure(exc) from exc


__all__ = ("get_model_assurance_service", "router")
