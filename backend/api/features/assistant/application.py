"""Transport-independent application facade for assistant schedules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from api.features.assistant.contracts import (
    AssistantScheduleConflict,
    AssistantScheduleExecutionFailed,
    AssistantScheduleIdentity,
    AssistantScheduleNotFound,
    AssistantSchedulePayload,
)

Schedule = dict[str, Any]


@dataclass(frozen=True)
class AssistantScheduleOperations:
    list_schedules: Callable[[str, int], list[Schedule]]
    upsert_schedule: Callable[..., Schedule]
    delete_schedule: Callable[[str, str], bool]
    run_schedule: Callable[..., Awaitable[Schedule]]


class AssistantScheduleApplication:
    """Own identity parsing and the list/create/update/delete/run use cases."""

    def __init__(self, operations: AssistantScheduleOperations):
        self._operations = operations

    def list(self, claims: Mapping[str, Any]) -> list[Schedule]:
        identity = AssistantScheduleIdentity.from_claims(claims)
        return self._operations.list_schedules(identity.username, identity.user_id)

    def create(
        self,
        claims: Mapping[str, Any],
        body: AssistantSchedulePayload,
    ) -> Schedule:
        identity = AssistantScheduleIdentity.from_claims(claims)
        return self._operations.upsert_schedule(
            identity.username,
            identity.user_id,
            body.to_schedule_payload(),
        )

    def update(
        self,
        claims: Mapping[str, Any],
        schedule_id: str,
        body: AssistantSchedulePayload,
    ) -> Schedule:
        identity = AssistantScheduleIdentity.from_claims(claims)
        return self._operations.upsert_schedule(
            identity.username,
            identity.user_id,
            body.to_schedule_payload(),
            schedule_id=schedule_id,
        )

    def delete(self, claims: Mapping[str, Any], schedule_id: str) -> None:
        identity = AssistantScheduleIdentity.from_claims(claims)
        if not self._operations.delete_schedule(identity.username, schedule_id):
            raise AssistantScheduleNotFound("定时任务不存在")

    async def run(
        self,
        claims: Mapping[str, Any],
        schedule_id: str,
        db: Any,
    ) -> Schedule:
        identity = AssistantScheduleIdentity.from_claims(claims)
        try:
            return await self._operations.run_schedule(
                identity.username,
                identity.user_id,
                schedule_id,
                db,
                manual=True,
            )
        except KeyError as exc:
            raise AssistantScheduleNotFound("定时任务不存在") from exc
        except RuntimeError as exc:
            detail = str(exc)
            if detail in {
                "正在运行",
                "该定时任务正在运行",
                "该定时任务正在其他进程运行",
            }:
                raise AssistantScheduleConflict(detail) from exc
            raise AssistantScheduleExecutionFailed("定时任务执行失败") from exc
        except Exception as exc:
            raise AssistantScheduleExecutionFailed("定时任务执行失败") from exc


def build_assistant_schedule_application() -> AssistantScheduleApplication:
    """Bind the feature facade to the current file-backed scheduler adapter."""
    from api.services import assistant_schedule

    return AssistantScheduleApplication(
        AssistantScheduleOperations(
            list_schedules=assistant_schedule.list_schedules,
            upsert_schedule=assistant_schedule.upsert_schedule,
            delete_schedule=assistant_schedule.delete_schedule,
            run_schedule=assistant_schedule.run_schedule,
        )
    )


__all__ = (
    "AssistantScheduleApplication",
    "AssistantScheduleOperations",
    "build_assistant_schedule_application",
)
