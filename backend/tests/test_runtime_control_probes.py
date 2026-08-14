from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import globemind_runtime as runtime
from scripts.runtime_control.dependency_probes import MAX_PROBE_RESPONSE_BYTES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "ops" / "runtime" / "services.json"


def _service(external_dependencies: list[Any] | None = None) -> dict[str, Any]:
    return {
        "id": "worker",
        "name": "worker",
        "kind": "service",
        "owner": "test",
        "criticality": "high",
        "check_interval_seconds": 60,
        "dependencies": [],
        "external_dependencies": external_dependencies or [],
        "controller": {
            "type": "shell-script",
            "path": "${PROJECT_ROOT}/control.sh",
            "interface": "status",
            "adoption": "observe-only",
        },
        "pid": {
            "kind": "single",
            "path": "${PROJECT_ROOT}/worker.pid",
            "cmdline_contains": ["worker.py"],
            "expected": "running",
        },
        "port": [{"id": "http", "host": "127.0.0.1", "number": 18089, "required": True}],
        "log": [],
        "health": [],
        "health_policy": {
            "mode": "process-only",
            "signals": [{"source": "pid", "required": True}],
        },
        "state": [],
        "output": [],
        "checkpoint": {
            "mode": "not-applicable",
            "state_refs": [],
            "takeover_ready": False,
        },
        "replay": {
            "mode": "not-applicable",
            "assurance": "not-applicable",
            "evidence": [],
        },
        "secret_policy": {
            "argv": "forbid-sensitive-values",
            "environment": "process-environment-only",
            "files": [],
            "redact_diagnostics": True,
        },
        "secret_refs": [],
        "lifecycle_authorization": {
            "state": "not-authorized",
            "authorized_operations": [],
            "change_request_required": True,
            "maintenance_window_required": True,
            "required_approvals": ["service-owner", "platform-operations"],
        },
        "runbook": {"path": "${PROJECT_ROOT}/runbook.md", "section": "worker"},
    }


def _probe(probe_type: str = "postgres-application-readiness") -> dict[str, Any]:
    paths = {
        "postgres-application-readiness": "/api/health/ready",
        "cloudflare-tunnel-ready": "/ready",
        "model-http-health": "/health",
    }
    if probe_type == "postgres-tcp":
        return {
            "id": "dependency-probe",
            "type": probe_type,
            "host": "127.0.0.1",
            "port": 5432,
            "timeout_seconds": 1,
            "evidence_ttl_seconds": 30,
        }
    return {
        "id": "dependency-probe",
        "type": probe_type,
        "host": "127.0.0.1",
        "port": 18089,
        "path": paths[probe_type],
        "timeout_seconds": 1,
        "evidence_ttl_seconds": 30,
        "bind_service": "worker",
    }


def _dependency(name: str = "postgres-news") -> dict[str, Any]:
    return {
        "name": name,
        "required": True,
        "verification": "probe",
        "via_probe": "dependency-probe",
    }


def _manifest(probe: dict[str, Any], dependency: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "inventory_version": "test",
        "project": "globemind",
        "variables": {"PROJECT_ROOT": "/ignored", "DATA_ROOT": "/ignored"},
        "control_policy": {
            "mode": "read_only",
            "destructive_commands_enabled": False,
            "allowed_commands": ["catalog", "doctor", "list", "status"],
        },
        "probes": [probe],
        "services": [_service([dependency or _dependency()])],
    }


def _load(tmp_path: Path, value: dict[str, Any]) -> runtime.Inventory:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return runtime.load_inventory(path, trusted_roots=(tmp_path, tmp_path.parent))


class _Response:
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self.body = body
        self.status = status
        self.closed = False

    def read(self, limit: int) -> bytes:
        assert limit == MAX_PROBE_RESPONSE_BYTES + 1
        return self.body

    def close(self) -> None:
        self.closed = True


def _strong_pid() -> dict[str, Any]:
    return {
        "status": "running",
        "identity_strength": "strong",
        "pid": 1234,
        "starttime_ticks": 5678,
    }


def _inspector(
    inventory: runtime.Inventory,
    tmp_path: Path,
    *,
    http_open: Any | None = None,
    tcp_connect: Any | None = None,
    listener_owned: bool = False,
) -> runtime.RuntimeInspector:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(exist_ok=True)
    kwargs: dict[str, Any] = {"proc_root": proc_root, "now": lambda: 2_000.0}
    if http_open is not None:
        kwargs["http_open"] = http_open
    if tcp_connect is not None:
        kwargs["tcp_connect"] = tcp_connect
    inspector = runtime.RuntimeInspector(inventory, **kwargs)
    inspector.process_owns_tcp_listener = lambda *_args: listener_owned  # type: ignore[method-assign]
    return inspector


def _write_process(proc_root: Path, *, pid: int, ticks: int) -> None:
    process_root = proc_root / str(pid)
    process_root.mkdir(parents=True)
    tail = ["S", *("0" for _ in range(18)), str(ticks), "0"]
    (process_root / "stat").write_text(
        f"{pid} (runtime worker) {' '.join(tail)}\n",
        encoding="utf-8",
    )
    (process_root / "comm").write_text("runtime-worker\n", encoding="utf-8")
    (process_root / "cmdline").write_bytes(b"python\0runtime-worker.py\0")
    (process_root / "fd").mkdir()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda probe: probe.update(host="169.254.169.254"), "loopback"),
        (lambda probe: probe.update(host="localhost"), "loopback"),
        (lambda probe: probe.update(path="//metadata/latest"), "fixed path"),
        (lambda probe: probe.update(path="/api/health/ready?next=http://x"), "fixed path"),
        (lambda probe: probe.update(timeout_seconds=6), "must not exceed 5"),
        (lambda probe: probe.update(url="http://127.0.0.1"), "unknown field"),
        (lambda probe: probe.update(command=["curl", "example.com"]), "unknown field"),
    ],
)
def test_probe_manifest_rejects_ssrf_paths_timeouts_and_commands(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    probe = _probe()
    mutation(probe)

    with pytest.raises(runtime.InventoryError, match=message):
        _load(tmp_path, _manifest(probe))


def test_runtime_guard_rejects_mutated_non_loopback_target(tmp_path: Path) -> None:
    called = False
    inventory = _load(tmp_path, _manifest(_probe()))
    inventory["probes"][0]["host"] = "169.254.169.254"
    service = inventory["services"][0]

    def open_http(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("unsafe target must not be opened")

    results, issues = _inspector(
        inventory, tmp_path, http_open=open_http
    )._inspect_external_dependencies(service, [], _strong_pid())

    assert called is False
    assert results[0]["observed_status"] == "unverified"
    assert issues[0]["code"] == "external-dependency-unverified"


def test_listener_ownership_requires_matching_inode_and_pid_incarnation(tmp_path: Path) -> None:
    inventory = _load(tmp_path, _manifest(_probe()))
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "stat").write_text("cpu 1 2 3 4\nbtime 1000\n", encoding="utf-8")
    _write_process(proc_root, pid=1234, ticks=5678)
    net_root = proc_root / "net"
    net_root.mkdir()
    (net_root / "tcp").write_text(
        "sl local_address rem_address st tx_queue:rx_queue tr tm->when retrnsmt uid timeout inode\n"
        "0: 0100007F:46A9 00000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000 0 0 4242 1\n",
        encoding="ascii",
    )
    (proc_root / "1234" / "fd" / "3").symlink_to("socket:[4242]")
    inspector = runtime.RuntimeInspector(inventory, proc_root=proc_root)

    assert inspector.process_owns_tcp_listener(1234, 5678, "127.0.0.1", 18089) is True
    assert inspector.process_owns_tcp_listener(1234, 9999, "127.0.0.1", 18089) is False
    assert inspector.process_owns_tcp_listener(1234, 5678, "127.0.0.1", 18090) is False

    (proc_root / "1234" / "fd" / "3").unlink()
    (proc_root / "1234" / "fd" / "3").symlink_to("socket:[9999]")
    assert inspector.process_owns_tcp_listener(1234, 5678, "127.0.0.1", 18089) is False


def test_postgres_tcp_probe_is_fresh_local_evidence_only(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, int], float]] = []

    class Connection:
        def close(self) -> None:
            return None

    def connect(target: tuple[str, int], timeout: float) -> Connection:
        calls.append((target, timeout))
        return Connection()

    inventory = _load(tmp_path, _manifest(_probe("postgres-tcp")))
    service = inventory["services"][0]
    results, issues = _inspector(
        inventory, tmp_path, tcp_connect=connect
    )._inspect_external_dependencies(service, [])

    assert calls == [(("127.0.0.1", 5432), 1.0)]
    assert results[0]["observed_status"] == "local-up"
    assert results[0]["probe"]["fresh_until"] == "1970-01-01T00:33:50Z"
    assert issues[0]["code"] == "external-dependency-unverified"


def test_application_readiness_can_verify_postgres_with_strong_identity(tmp_path: Path) -> None:
    response = _Response(
        json.dumps(
            {
                "service": "globemind-api",
                "ready": True,
                "checks": {"database": {"status": "up"}},
            }
        ).encode()
    )
    requested: list[tuple[str, float, str]] = []

    def open_http(request: Any, *, timeout: float) -> _Response:
        requested.append((request.full_url, timeout, request.method))
        return response

    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    results, issues = _inspector(
        inventory,
        tmp_path,
        http_open=open_http,
        listener_owned=True,
    )._inspect_external_dependencies(service, [], _strong_pid())

    assert requested == [("http://127.0.0.1:18089/api/health/ready", 1.0, "GET")]
    assert response.closed is True
    assert results[0]["observed_status"] == "external-verified"
    assert results[0]["probe"]["instance_binding"] == {
        "service": "worker",
        "pid": 1234,
        "starttime_ticks": 5678,
        "listener_verified": True,
    }
    assert issues == []


def test_strong_identity_is_required_for_external_http_verification(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "service": "globemind-api",
            "ready": True,
            "checks": {"database": {"status": "up"}},
        }
    ).encode()
    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    inspector = _inspector(inventory, tmp_path, http_open=lambda *_args, **_kwargs: _Response(body))

    results, issues = inspector._inspect_external_dependencies(
        service, [], {"status": "running", "identity_strength": "weak"}
    )

    assert results[0]["observed_status"] == "local-up"
    assert "instance_binding" not in results[0]["probe"]
    assert issues[0]["code"] == "external-dependency-unverified"


def test_strong_identity_without_listener_ownership_stays_local_up(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "service": "globemind-api",
            "ready": True,
            "checks": {"database": {"status": "up"}},
        }
    ).encode()
    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    inspector = _inspector(
        inventory,
        tmp_path,
        http_open=lambda *_args, **_kwargs: _Response(body),
        listener_owned=False,
    )

    results, issues = inspector._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "local-up"
    assert "listener ownership is unverified" in results[0]["probe"]["reason"]
    assert "instance_binding" not in results[0]["probe"]
    assert issues[0]["code"] == "external-dependency-unverified"


def test_reachable_application_with_database_down_is_business_stalled(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "service": "globemind-api",
            "ready": False,
            "checks": {"database": {"status": "down"}},
        }
    ).encode()
    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    inspector = _inspector(inventory, tmp_path, http_open=lambda *_args, **_kwargs: _Response(body))

    results, issues = inspector._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "business-stalled"
    assert issues[0]["severity"] == "error"
    assert issues[0]["code"] == "external-dependency-business-stalled"


def test_cloudflare_ready_is_external_only_with_connector_identity(tmp_path: Path) -> None:
    probe = _probe("cloudflare-tunnel-ready")
    dependency = _dependency("cloudflare-edge")
    inventory = _load(tmp_path, _manifest(probe, dependency))
    service = inventory["services"][0]
    inspector = _inspector(
        inventory,
        tmp_path,
        http_open=lambda *_args, **_kwargs: _Response(),
        listener_owned=True,
    )

    results, issues = inspector._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "external-verified"
    assert issues == []


def test_model_health_never_claims_external_verification(tmp_path: Path) -> None:
    probe = _probe("model-http-health")
    dependency = _dependency("qwen2.5-7b-awq-model")
    inventory = _load(tmp_path, _manifest(probe, dependency))
    service = inventory["services"][0]
    inspector = _inspector(inventory, tmp_path, http_open=lambda *_args, **_kwargs: _Response())

    results, issues = inspector._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "local-up"
    assert issues[0]["code"] == "external-dependency-unverified"


def test_timeout_fails_closed_without_raising(tmp_path: Path) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("timed out")

    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    results, issues = _inspector(
        inventory, tmp_path, http_open=timeout
    )._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "unreachable"
    assert issues[0]["code"] == "external-dependency-unhealthy"


def test_optional_external_dependency_failure_keeps_its_warning(tmp_path: Path) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("timed out")

    dependency = _dependency()
    dependency["required"] = False
    inventory = _load(tmp_path, _manifest(_probe(), dependency))
    service = inventory["services"][0]

    results, issues = _inspector(
        inventory,
        tmp_path,
        http_open=timeout,
    )._inspect_external_dependencies(service, [], _strong_pid())

    assert results[0]["observed_status"] == "unreachable"
    assert issues[0]["severity"] == "warning"
    assert issues[0]["details"]["required"] is False


def test_oversized_readiness_body_is_unverified_not_accepted(tmp_path: Path) -> None:
    inventory = _load(tmp_path, _manifest(_probe()))
    service = inventory["services"][0]
    oversized = _Response(b"x" * (MAX_PROBE_RESPONSE_BYTES + 1))
    inspector = _inspector(inventory, tmp_path, http_open=lambda *_args, **_kwargs: oversized)

    results, issues = inspector._inspect_external_dependencies(service, [], _strong_pid())

    assert oversized.closed is True
    assert results[0]["observed_status"] == "unverified"
    assert issues[0]["code"] == "external-dependency-unverified"


def test_production_manifest_keeps_proxy_pool_unverified_with_reason() -> None:
    inventory = runtime.load_inventory(MANIFEST)
    services = {service["id"]: service for service in inventory["services"]}
    dependency = services["proxy_pool"]["external_dependencies"][0]

    assert dependency["verification"] == "unverified"
    assert "start-ticks identity" in dependency["reason"]
    assert all(
        not (service["controller"].get("lifecycle") or {}).get("enabled", False)
        for service in services.values()
    )
