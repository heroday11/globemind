"""Authenticated HTTP boundary for temporal entity governance."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    status,
)
from fastapi.routing import APIRoute

from api.core.environment import raw_setting, string_setting
from api.features.entity_governance import (
    AliasReviewRequest,
    EntityDecisionRequest,
    EntityGovernanceAccessDenied,
    EntityGovernanceConflict,
    EntityGovernanceLedger,
    EntityGovernanceNotFound,
    EntityGovernanceService,
    EntityGovernanceUnavailable,
    MergeDecisionRequest,
    RelationAddRequest,
    RelationRetractRequest,
    SeedCatalogUnavailable,
    SplitDecisionRequest,
    load_search_seed_entities,
)
from api.features.evidence import EvidenceLedgerUnavailable, EvidenceSnapshotLedger
from api.services.auth import get_current_user_required


_Result = TypeVar("_Result")
_MAX_MUTATION_JSON_BYTES = 64 * 1024
_ENTITY_URN_PATTERN = (
    r"^urn:globemind:entity:(country|person|organization|location):"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_RELATION_URN_PATTERN = r"^urn:globemind:relation:[0-9a-f]{32}$"


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite_json_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_non_finite_json_number(value)
    return parsed


async def _require_unambiguous_mutation_json(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ENTITY_GOVERNANCE_JSON_AMBIGUOUS",
                "message": "实体治理写入正文必须是有界 JSON 对象",
            },
        )
    body = await request.body()
    if not body or len(body) > _MAX_MUTATION_JSON_BYTES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ENTITY_GOVERNANCE_JSON_AMBIGUOUS",
                "message": "实体治理写入正文为空或超出边界",
            },
        )
    try:
        payload = json.loads(
            body,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_finite_json_float,
        )
        if not isinstance(payload, dict):
            raise ValueError("JSON root must be an object")
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ENTITY_GOVERNANCE_JSON_AMBIGUOUS",
                "message": "实体治理正文必须是无重复键的有限 JSON 对象",
            },
        ) from exc


class _StrictEntityGovernanceRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def strict_route_handler(request: Request):
            await _require_unambiguous_mutation_json(request)
            return await route_handler(request)

        return strict_route_handler


router = APIRouter(prefix="/api/entity-governance", tags=["entity-governance"])
router.route_class = _StrictEntityGovernanceRoute


def get_entity_governance_service() -> EntityGovernanceService:
    try:
        seeds = load_search_seed_entities()
    except (SeedCatalogUnavailable, RuntimeError):
        return EntityGovernanceService(
            None,
            {},
            unavailable_reason="ENTITY_GOVERNANCE_SEED_CATALOG_UNAVAILABLE",
        )

    root = Path(
        string_setting(
            "ENTITY_GOVERNANCE_ROOT",
            "/root/data/web/entity-governance",
        )
    )
    raw_hmac_key = raw_setting("ENTITY_GOVERNANCE_HMAC_KEY")
    try:
        ledger = EntityGovernanceLedger(root, raw_hmac_key.encode("utf-8"))
    except (EntityGovernanceUnavailable, UnicodeError):
        return EntityGovernanceService(
            None,
            seeds,
            unavailable_reason="ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE",
        )

    evidence_root = Path(
        string_setting(
            "EVIDENCE_SNAPSHOT_ROOT",
            "/root/data/web/evidence-snapshots",
        )
    )
    try:
        evidence_reader = EvidenceSnapshotLedger(evidence_root)
    except EvidenceLedgerUnavailable:
        evidence_reader = None
    return EntityGovernanceService(
        ledger,
        seeds,
        evidence_reader=evidence_reader,
    )


def _execute(operation: Callable[[], _Result]) -> _Result:
    try:
        return operation()
    except EntityGovernanceAccessDenied as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": str(exc)},
        ) from exc
    except EntityGovernanceNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": str(exc)},
        ) from exc
    except EntityGovernanceConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc)},
        ) from exc
    except EntityGovernanceUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ENTITY_GOVERNANCE_UNAVAILABLE",
                "reason_code": str(exc),
                "fallback": "none",
            },
        ) from exc


@router.get("/status")
def get_entity_governance_status(
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.status(user))


@router.get("/catalog")
def get_entity_governance_catalog(
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.catalog(user))


@router.get("/entities/{entity_id}")
def get_governed_entity(
    entity_id: str = ApiPath(pattern=_ENTITY_URN_PATTERN),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.entity(entity_id, user))


@router.get("/relations")
def get_governed_relations(
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.relations(user))


@router.get("/history")
def get_entity_governance_history(
    limit: int = Query(default=100, ge=1, le=100),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.history(user, limit=limit))


@router.post(
    "/entities/{entity_id}/decisions",
    status_code=status.HTTP_201_CREATED,
)
def decide_governed_entity(
    body: EntityDecisionRequest,
    entity_id: str = ApiPath(pattern=_ENTITY_URN_PATTERN),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.decide_entity(entity_id, body, user))


@router.post("/aliases/reviews", status_code=status.HTTP_201_CREATED)
def review_governed_alias(
    body: AliasReviewRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.review_alias(body, user))


@router.post("/relations", status_code=status.HTTP_201_CREATED)
def add_governed_relation(
    body: RelationAddRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.add_relation(body, user))


@router.post(
    "/relations/{relation_id}/retractions",
    status_code=status.HTTP_201_CREATED,
)
def retract_governed_relation(
    body: RelationRetractRequest,
    relation_id: str = ApiPath(pattern=_RELATION_URN_PATTERN),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.retract_relation(relation_id, body, user))


@router.post("/merges", status_code=status.HTTP_201_CREATED)
def decide_governed_merge(
    body: MergeDecisionRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.decide_merge(body, user))


@router.post("/splits", status_code=status.HTTP_201_CREATED)
def decide_governed_split(
    body: SplitDecisionRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: EntityGovernanceService = Depends(get_entity_governance_service),
):
    return _execute(lambda: service.decide_split(body, user))


__all__ = ("get_entity_governance_service", "router")
