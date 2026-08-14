"""ASGI instrumentation limited to predeclared route templates and operations."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from datetime import datetime, timezone
from typing import Any

from .contracts import Operation, Outcome
from .service import InstrumentationWriteResult, ServiceLevelService

_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)
_ROUTE_TEMPLATE = re.compile(r"^/[A-Za-z0-9_./{}:-]{0,199}$")
_OPERATIONS = frozenset({"search", "export", "report"})
_LOGGER = logging.getLogger("globemind.service_level")


class ServiceLevelInstrumentationAdapter:
    """Persist only a fixed operation, outcome, duration, and timestamp."""

    def __init__(self, service: ServiceLevelService) -> None:
        self._service = service

    def observe(
        self,
        *,
        operation: Operation,
        outcome: Outcome,
        duration_ms: int,
        observed_at: datetime | None = None,
    ) -> InstrumentationWriteResult:
        result = self._service.record_instrumentation(
            operation=operation,
            outcome=outcome,
            duration_ms=duration_ms,
            observed_at=observed_at,
        )
        if result == "unavailable":
            # Deliberately omit URLs, paths, request identifiers and exception
            # strings. There is no process-local counter masquerading as
            # durable cross-worker status.
            _LOGGER.warning("service_level_instrumentation_unavailable")
        return result


class ServiceLevelASGIMiddleware:
    """Measure configured HTTP route templates without reading request bodies."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        adapter: ServiceLevelInstrumentationAdapter,
        routes: Mapping[tuple[str, str], Operation],
        monotonic_ns: Callable[[], int] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.app = app
        self._adapter = adapter
        self._monotonic_ns = monotonic_ns or time.perf_counter_ns
        self._now = now or (lambda: datetime.now(timezone.utc))
        normalized: dict[tuple[str, str], Operation] = {}
        for raw_key, raw_operation in routes.items():
            if (
                not isinstance(raw_key, tuple)
                or len(raw_key) != 2
                or not all(isinstance(item, str) for item in raw_key)
            ):
                raise ValueError("route measurement keys must be method/template pairs")
            method, route_template = raw_key
            method = method.upper()
            if method not in _HTTP_METHODS:
                raise ValueError("route measurement method is not allowed")
            if (
                _ROUTE_TEMPLATE.fullmatch(route_template) is None
                or ".." in route_template
                or "//" in route_template
            ):
                raise ValueError("route measurement template is invalid")
            if raw_operation not in _OPERATIONS:
                raise ValueError("route measurement operation is not allowed")
            key = (method, route_template)
            if key in normalized:
                raise ValueError("route measurement mapping is duplicated")
            normalized[key] = raw_operation
        self._routes = normalized

    def _operation(self, scope: MutableMapping[str, Any]) -> Operation | None:
        method = scope.get("method")
        route = scope.get("route")
        template = getattr(route, "path", None)
        if not isinstance(method, str) or not isinstance(template, str):
            return None
        return self._routes.get((method.upper(), template))

    def _record(
        self,
        *,
        scope: MutableMapping[str, Any],
        outcome: Outcome,
        started_ns: int,
    ) -> None:
        operation = self._operation(scope)
        if operation is None:
            return
        elapsed_ns = max(0, self._monotonic_ns() - started_ns)
        duration_ms = (elapsed_ns + 999_999) // 1_000_000
        try:
            self._adapter.observe(
                operation=operation,
                outcome=outcome,
                duration_ms=duration_ms,
                observed_at=self._now(),
            )
        except Exception:
            # Measurement must never replace the business response. This log
            # is intentionally static because arbitrary exception text can
            # contain request data.
            _LOGGER.warning("service_level_instrumentation_adapter_failed")

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[..., Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started_ns = self._monotonic_ns()
        status_code: int | None = None

        async def observe_send(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                candidate = message.get("status")
                if isinstance(candidate, int):
                    status_code = candidate
            await send(message)

        try:
            await self.app(scope, receive, observe_send)
        except asyncio.CancelledError:
            self._record(
                scope=scope,
                outcome="cancelled",
                started_ns=started_ns,
            )
            raise
        except TimeoutError:
            self._record(
                scope=scope,
                outcome="timeout",
                started_ns=started_ns,
            )
            raise
        except Exception:
            self._record(
                scope=scope,
                outcome="error",
                started_ns=started_ns,
            )
            raise
        else:
            outcome: Outcome = (
                "success"
                if status_code is not None and status_code < 400
                else "error"
            )
            self._record(
                scope=scope,
                outcome=outcome,
                started_ns=started_ns,
            )


__all__ = (
    "ServiceLevelASGIMiddleware",
    "ServiceLevelInstrumentationAdapter",
)
