from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Dict, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.features.assistant import (
    AssistantScheduleConflict,
    AssistantScheduleExecutionFailed,
    AssistantScheduleIdentityError,
    AssistantScheduleNotFound,
    AssistantSchedulePayload,
    build_assistant_schedule_application,
)
from api.services.auth import get_current_user_required

router = APIRouter(prefix="/api/assistant", tags=["AI"])

_ResultT = TypeVar("_ResultT")


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    try:
        return operation()
    except AssistantScheduleIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssistantScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssistantScheduleExecutionFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _invoke_async(operation: Callable[[], Awaitable[_ResultT]]) -> _ResultT:
    try:
        return await operation()
    except AssistantScheduleIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssistantScheduleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AssistantScheduleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AssistantScheduleExecutionFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/schedules")
def assistant_schedule_list(
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    items = _invoke(lambda: build_assistant_schedule_application().list(user))
    return JSONResponse({"ok": True, "data": items})


@router.post("/schedules")
def assistant_schedule_create(
    body: AssistantSchedulePayload,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    saved = _invoke(
        lambda: build_assistant_schedule_application().create(user, body)
    )
    return JSONResponse({"ok": True, "data": saved})


@router.put("/schedules/{schedule_id}")
def assistant_schedule_update(
    schedule_id: str,
    body: AssistantSchedulePayload,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    saved = _invoke(
        lambda: build_assistant_schedule_application().update(
            user,
            schedule_id,
            body,
        )
    )
    return JSONResponse({"ok": True, "data": saved})


@router.delete("/schedules/{schedule_id}")
def assistant_schedule_delete(
    schedule_id: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    _invoke(
        lambda: build_assistant_schedule_application().delete(user, schedule_id)
    )
    return JSONResponse({"ok": True, "id": schedule_id})


@router.post("/schedules/{schedule_id}/run")
async def assistant_schedule_run(
    schedule_id: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = await _invoke_async(
        lambda: build_assistant_schedule_application().run(user, schedule_id, db)
    )
    return JSONResponse({"ok": True, "data": result})
