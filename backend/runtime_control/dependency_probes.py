"""Bounded, read-only probes for declared loopback dependencies."""

from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from .json_safety import JSONSafetyError, loads_bounded
from .process_identity import utc_iso
from .redaction import redact_text

MAX_PROBE_RESPONSE_BYTES = 64 * 1024
_FIXED_HTTP_PATHS = {
    "postgres-application-readiness": "/api/health/ready",
    "cloudflare-tunnel-ready": "/ready",
    "model-http-health": "/health",
}
_SUPPORTED_TYPES = {"postgres-tcp", *_FIXED_HTTP_PATHS}


def _expires_at(timestamp: float, ttl_seconds: float) -> str:
    return datetime.fromtimestamp(timestamp + ttl_seconds, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


class DependencyProbeRunner:
    """Execute only the fixed probe forms accepted by the manifest validator."""

    def __init__(
        self,
        *,
        now: Callable[[], float],
        http_open: Callable[..., Any],
        tcp_connect: Callable[[tuple[str, int], float], Any],
        listener_ownership_check: Callable[[int, int, str, int], bool],
    ) -> None:
        self.now = now
        self.http_open = http_open
        self.tcp_connect = tcp_connect
        self.listener_ownership_check = listener_ownership_check

    @staticmethod
    def _strong_instance(pid_result: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(pid_result, Mapping):
            return None
        pid = pid_result.get("pid")
        start_ticks = pid_result.get("starttime_ticks")
        if (
            pid_result.get("status") != "running"
            or pid_result.get("identity_strength") != "strong"
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or isinstance(start_ticks, bool)
            or not isinstance(start_ticks, int)
        ):
            return None
        return {"pid": pid, "starttime_ticks": start_ticks}

    def _base_result(self, probe: Mapping[str, Any]) -> dict[str, Any]:
        target: dict[str, Any] = {
            "host": probe["host"],
            "port": probe["port"],
        }
        if "path" in probe:
            target["path"] = probe["path"]
        return {
            "id": probe["id"],
            "type": probe["type"],
            "status": "unverified",
            "target": target,
            "evidence_ttl_seconds": probe["evidence_ttl_seconds"],
            "read_only": True,
        }

    def _stamp_freshness(
        self, result: dict[str, Any], probe: Mapping[str, Any]
    ) -> dict[str, Any]:
        checked_at = self.now()
        result["checked_at"] = utc_iso(checked_at)
        result["fresh_until"] = _expires_at(
            checked_at, float(probe["evidence_ttl_seconds"])
        )
        return result

    @staticmethod
    def _runtime_target_is_safe(probe: Mapping[str, Any]) -> bool:
        probe_type = probe.get("type")
        host = probe.get("host")
        port = probe.get("port")
        timeout = probe.get("timeout_seconds")
        ttl = probe.get("evidence_ttl_seconds")
        if probe_type not in _SUPPORTED_TYPES or not isinstance(host, str):
            return False
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return False
        except ValueError:
            return False
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            return False
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 5
        ):
            return False
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl <= 300:
            return False
        if probe_type == "postgres-tcp":
            return "path" not in probe and "bind_service" not in probe
        return (
            probe.get("path") == _FIXED_HTTP_PATHS[probe_type]
            and isinstance(probe.get("bind_service"), str)
            and bool(probe["bind_service"])
        )

    def _run_tcp(self, probe: Mapping[str, Any]) -> dict[str, Any]:
        result = self._base_result(probe)
        started = time.monotonic()
        try:
            connection = self.tcp_connect(
                (str(probe["host"]), int(probe["port"])),
                float(probe["timeout_seconds"]),
            )
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        except (OSError, TimeoutError) as exc:
            result.update(
                {
                    "status": "unreachable",
                    "reason": "declared loopback TCP endpoint is unavailable",
                    "error": redact_text(str(exc)),
                }
            )
        else:
            result.update(
                {
                    "status": "local-up",
                    "reason": "fresh TCP reachability does not prove application or external health",
                }
            )
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        return result

    @staticmethod
    def _read_response(response: Any) -> bytes:
        body = response.read(MAX_PROBE_RESPONSE_BYTES + 1)
        if len(body) > MAX_PROBE_RESPONSE_BYTES:
            raise JSONSafetyError("probe response exceeds the bounded read limit")
        return body

    def _http_exchange(self, probe: Mapping[str, Any]) -> tuple[int, bytes, str | None, float]:
        host = str(probe["host"])
        url_host = f"[{host}]" if ":" in host else host
        request = urllib.request.Request(
            f"http://{url_host}:{int(probe['port'])}{probe['path']}",
            headers={"Accept": "application/json", "User-Agent": "globemind-runtime/0.11"},
            method="GET",
        )
        started = time.monotonic()
        response: Any | None = None
        try:
            response = self.http_open(request, timeout=float(probe["timeout_seconds"]))
            raw_status = getattr(response, "status", None)
            status = int(raw_status if raw_status is not None else response.getcode())
            body = (
                self._read_response(response)
                if probe["type"] == "postgres-application-readiness"
                else b""
            )
            error = None
        except urllib.error.HTTPError as exc:
            response = exc
            status = int(exc.code)
            try:
                body = (
                    self._read_response(exc)
                    if probe["type"] == "postgres-application-readiness"
                    else b""
                )
            except (OSError, JSONSafetyError):
                body = b""
            error = redact_text(str(exc))
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        return status, body, error, (time.monotonic() - started) * 1000

    @staticmethod
    def _database_ready(body: bytes) -> tuple[bool, str | None]:
        try:
            payload = loads_bounded(body.decode("utf-8"), max_depth=12, max_nodes=512)
        except (JSONSafetyError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return False, f"application readiness evidence is invalid: {redact_text(str(exc))}"
        if not isinstance(payload, Mapping):
            return False, "application readiness evidence is not a JSON object"
        checks = payload.get("checks")
        database = checks.get("database") if isinstance(checks, Mapping) else None
        valid_identity = payload.get("service") == "globemind-api"
        ready = payload.get("ready") is True
        database_up = isinstance(database, Mapping) and database.get("status") == "up"
        if valid_identity and ready and database_up:
            return True, None
        return False, "application reports PostgreSQL readiness is not up"

    def _run_http(
        self,
        probe: Mapping[str, Any],
        pid_result: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        result = self._base_result(probe)
        instance = self._strong_instance(pid_result)
        listener_owned_before = bool(
            instance is not None
            and self.listener_ownership_check(
                instance["pid"],
                instance["starttime_ticks"],
                str(probe["host"]),
                int(probe["port"]),
            )
        )
        if listener_owned_before:
            result["instance_binding"] = {
                "service": probe["bind_service"],
                **instance,
                "listener_verified": True,
            }
        try:
            status_code, body, error, latency = self._http_exchange(probe)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            result.update(
                {
                    "status": "unreachable",
                    "status_code": 0,
                    "reason": "declared loopback HTTP endpoint is unavailable",
                    "error": redact_text(str(exc)),
                    "latency_ms": 0.0,
                }
            )
            return result
        except JSONSafetyError as exc:
            result.update(
                {
                    "status": "unverified",
                    "status_code": 200,
                    "reason": redact_text(str(exc)),
                    "latency_ms": 0.0,
                }
            )
            return result

        result.update({"status_code": status_code, "latency_ms": round(latency, 2)})
        if error:
            result["error"] = error
        if status_code != 200:
            result.update(
                {
                    "status": "business-stalled",
                    "reason": "local endpoint responded but did not report readiness",
                }
            )
            return result

        listener_owned_after = bool(
            listener_owned_before
            and instance is not None
            and self.listener_ownership_check(
                instance["pid"],
                instance["starttime_ticks"],
                str(probe["host"]),
                int(probe["port"]),
            )
        )
        endpoint_bound = listener_owned_before and listener_owned_after
        if not endpoint_bound:
            result.pop("instance_binding", None)

        probe_type = probe["type"]
        if probe_type == "postgres-application-readiness":
            database_ready, reason = self._database_ready(body)
            if not database_ready:
                result.update({"status": "business-stalled", "reason": reason})
                return result
            if not endpoint_bound:
                result.update(
                    {
                        "status": "local-up",
                        "reason": (
                            "database readiness passed but strong web identity is unavailable"
                            if instance is None
                            else "database readiness passed but listener ownership is unverified"
                        ),
                    }
                )
                return result
            result.update(
                {
                    "status": "external-verified",
                    "reason": "identity-bound application readiness reports PostgreSQL up",
                }
            )
            return result

        if probe_type == "cloudflare-tunnel-ready":
            if not endpoint_bound:
                result.update(
                    {
                        "status": "local-up",
                        "reason": (
                            "tunnel readiness passed but strong connector identity is unavailable"
                            if instance is None
                            else "tunnel readiness passed but listener ownership is unverified"
                        ),
                    }
                )
                return result
            result.update(
                {
                    "status": "external-verified",
                    "reason": "identity-bound tunnel readiness reports an active edge connection",
                }
            )
            return result

        result.update(
            {
                "status": "local-up",
                "reason": "fresh model health proves only local serving health",
            }
        )
        return result

    def run(
        self,
        probe: Mapping[str, Any],
        *,
        pid_result: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not self._runtime_target_is_safe(probe):
            return {
                "id": str(probe.get("id") or "invalid-probe")[:128],
                "type": str(probe.get("type") or "invalid")[:64],
                "status": "unverified",
                "reason": "probe target was rejected by the runtime safety guard",
                "read_only": True,
            }
        if probe["type"] == "postgres-tcp":
            result = self._run_tcp(probe)
        else:
            result = self._run_http(probe, pid_result)
        return self._stamp_freshness(result, probe)
