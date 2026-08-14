"""Authenticated source snapshot and downstream revision-impact API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.core.environment import string_setting
from api.features.evidence import (
    EvidenceLedgerConflict,
    EvidenceLedgerNotFound,
    EvidenceLedgerUnavailable,
    EvidenceSnapshotLedger,
    build_article_evidence_chain,
)
from api.services.auth import get_current_admin_user, get_current_user_required
from api.services.news_search_v2 import get_news_analysis_v2, get_news_by_id_v2

router = APIRouter(prefix="/api/evidence-ledger", tags=["evidence-ledger"])


class CaptureArticleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)
    change_type: Literal["initial", "update", "correction", "withdrawal"]
    expected_previous_event_id: str | None = Field(default=None, max_length=80)


class ReviewImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirmed", "modified", "rejected"]
    reason: str = Field(min_length=3, max_length=500)
    impacted_claim_ids: list[str] | None = Field(default=None, max_length=200)


def _ledger() -> EvidenceSnapshotLedger:
    root = Path(
        string_setting(
            "EVIDENCE_SNAPSHOT_ROOT",
            "/root/data/web/evidence-snapshots",
        )
    )
    return EvidenceSnapshotLedger(root)


def _actor_id(user: dict[str, Any]) -> int:
    raw = user.get("user_id", user.get("id"))
    if isinstance(raw, bool):
        raise HTTPException(status_code=401, detail="active user identity is invalid")
    try:
        actor_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail="active user identity is invalid",
        ) from exc
    if actor_id <= 0:
        raise HTTPException(status_code=401, detail="active user identity is invalid")
    return actor_id


def _raise_ledger_error(exc: Exception) -> None:
    if isinstance(exc, EvidenceLedgerNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, EvidenceLedgerConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, EvidenceLedgerUnavailable):
        raise HTTPException(
            status_code=503,
            detail="evidence ledger is unavailable",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.post("/articles/{article_id}/captures", status_code=201)
def capture_article(
    article_id: int,
    body: CaptureArticleRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
):
    if body.change_type == "initial" and body.expected_previous_event_id is not None:
        raise HTTPException(
            status_code=422,
            detail="initial capture cannot name a previous event",
        )
    if body.change_type != "initial" and not body.expected_previous_event_id:
        raise HTTPException(
            status_code=422,
            detail="revision capture requires expected_previous_event_id",
        )

    article = get_news_by_id_v2(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="article was not found")
    try:
        analysis = get_news_analysis_v2(article_id)
    except Exception:
        # The immutable source body remains capturable even when derived
        # analysis is unavailable. No unavailable claim is treated as impact.
        analysis = None
    chain = build_article_evidence_chain(
        article,
        analysis if isinstance(analysis, dict) else None,
    )
    claim_ids = [
        str(claim["id"])
        for claim in chain.get("claims", [])
        if isinstance(claim, dict) and claim.get("evidence_status") == "available"
    ]

    try:
        return _ledger().capture(
            article_id=article_id,
            title=getattr(article, "title", ""),
            body=getattr(article, "body", ""),
            source_url=getattr(article, "request_url", None),
            actor_id=_actor_id(user),
            reason=body.reason,
            change_type=body.change_type,
            claim_ids=claim_ids,
            expected_previous_event_id=body.expected_previous_event_id,
        )
    except Exception as exc:
        _raise_ledger_error(exc)


@router.get("/articles/{article_id}/history")
def article_history(
    article_id: int,
    limit: int = Query(default=100, ge=1, le=100),
    _user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _ledger().history(article_id, limit=limit)
    except Exception as exc:
        _raise_ledger_error(exc)


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: str,
    include_body: bool = Query(default=False),
    _user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _ledger().snapshot(snapshot_id, include_body=include_body)
    except Exception as exc:
        _raise_ledger_error(exc)


@router.post("/articles/{article_id}/events/{event_id}/impact-reviews", status_code=201)
def review_revision_impact(
    article_id: int,
    event_id: str,
    body: ReviewImpactRequest,
    admin: dict[str, Any] = Depends(get_current_admin_user),
):
    try:
        return _ledger().review_impact(
            article_id=article_id,
            event_id=event_id,
            actor_id=_actor_id(admin),
            decision=body.decision,
            reason=body.reason,
            impacted_claim_ids=body.impacted_claim_ids,
        )
    except Exception as exc:
        _raise_ledger_error(exc)


__all__ = ("router",)
