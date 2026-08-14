"""HTTP routes for the current L3/L2/L1 graph briefing surface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.features.graph_briefing import (
    GraphBriefingInputError,
    GraphBriefingNotFound,
    GraphBriefingService,
    MicroNewsBatchBody,
)

router = APIRouter()

_ResponseT = TypeVar("_ResponseT")


def _invoke(operation: Callable[[], _ResponseT]) -> _ResponseT:
    try:
        return operation()
    except GraphBriefingInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GraphBriefingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/macros/search", summary="Search current L3 macro events")
def search_macro_storylines(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(24, ge=1, le=80),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q must not be blank")
    return _invoke(lambda: GraphBriefingService(db).search_macros(query, limit))


@router.post("/micros/news-batch", summary="Fetch representative news for L2 chains")
def batch_news_for_micros(
    body: MicroNewsBatchBody = Body(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _invoke(
        lambda: GraphBriefingService(db).batch_news(body.event_ids, body.limit_per)
    )


@router.get("/universe", summary="Build a current hierarchy universe snapshot")
def get_universe(
    macro_limit: int = Query(120, ge=1, le=800),
    micro_per_macro: int = Query(40, ge=1, le=300),
    unclustered_limit: int = Query(5000, ge=0, le=20000),
    fill_ambient: bool = Query(True),
    news_per_micro: int = Query(14, ge=0, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _invoke(
        lambda: GraphBriefingService(db).universe(
            macro_limit=macro_limit,
            micro_per_macro=micro_per_macro,
            unclustered_limit=unclustered_limit,
            fill_ambient=fill_ambient,
            news_per_micro=news_per_micro,
        )
    )


@router.get("/macro/{storyline_id}", summary="Get one current L3 macro event")
def get_macro_storyline(
    storyline_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _invoke(lambda: GraphBriefingService(db).get_macro(storyline_id))


@router.get("/macro/{storyline_id}/briefing", summary="Get a macro briefing")
def get_macro_briefing(
    storyline_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _invoke(lambda: GraphBriefingService(db).get_briefing(storyline_id))


@router.get("/macro/{storyline_id}/micros", summary="List current L2 chains in a macro")
def list_micros_for_macro(
    storyline_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return _invoke(
        lambda: GraphBriefingService(db).list_micros(
            storyline_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/macro/{storyline_id}/tree", summary="Get a current L3/L2 tree")
def get_macro_tree(
    storyline_id: str,
    db: Session = Depends(get_db),
    micro_limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    return _invoke(
        lambda: GraphBriefingService(db).get_tree(
            storyline_id,
            micro_limit=micro_limit,
        )
    )


@router.get("/micro/{event_id}", summary="Get one current L2 chain")
def get_micro_event(
    event_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _invoke(lambda: GraphBriefingService(db).get_micro(event_id))


@router.get("/micro/{event_id}/news", summary="List news linked through L2 and L1")
def list_news_for_micro(
    event_id: str,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    brief: bool = Query(True),
) -> dict[str, Any]:
    return _invoke(
        lambda: GraphBriefingService(db).list_news(
            event_id,
            page=page,
            page_size=page_size,
            brief=brief,
        )
    )
