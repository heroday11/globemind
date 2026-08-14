from __future__ import annotations

import json
import os
import socket
import stat
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts import globemind_runtime as runtime
from scripts.runtime_control.manifest import Inventory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "ops" / "runtime" / "services.json"


def _service(identifier: str, dependencies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": identifier,
        "kind": "service",
        "owner": "test",
        "criticality": "high",
        "check_interval_seconds": 60,
        "dependencies": dependencies or [],
        "external_dependencies": [],
        "controller": {
            "type": "shell-script",
            "path": "${PROJECT_ROOT}/control.sh",
            "interface": "status",
            "adoption": "observe-only",
        },
        "pid": {
            "kind": "single",
            "path": f"${{PROJECT_ROOT}}/{identifier}.pid",
            "cmdline_contains": [f"{identifier}.py"],
            "expected": "running",
        },
        "port": [],
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


def _manifest(*services: dict[str, Any]) -> dict[str, Any]:
    for service in services:
        required_health = [
            {"source": "health", "index": index, "required": True}
            for index, item in enumerate(service["health"])
            if item.get("required") is True
        ]
        service["health_policy"] = {
            "mode": "active-probe" if required_health else "process-only",
            "signals": [{"source": "pid", "required": True}, *required_health],
        }
        service["runbook"]["section"] = service["id"]
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
        "services": list(services),
    }


def _load(tmp_path: Path, value: dict[str, Any]) -> Inventory:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return runtime.load_inventory(path, trusted_roots=(tmp_path, tmp_path.parent))


def _fake_proc(tmp_path: Path, boot_time: int = 1_000) -> Path:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(exist_ok=True)
    (proc_root / "stat").write_text(f"cpu 1 2 3 4\nbtime {boot_time}\n", encoding="utf-8")
    return proc_root


def _write_process(proc_root: Path, pid: int, ticks: int, argv: list[str]) -> None:
    root = proc_root / str(pid)
    root.mkdir(parents=True)
    tail = ["S", *("0" for _ in range(18)), str(ticks), "0"]
    (root / "stat").write_text(f"{pid} (python worker) {' '.join(tail)}\n", encoding="utf-8")
    (root / "comm").write_text("python\n", encoding="utf-8")
    (root / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for item in value.values():
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def test_real_schema_v2_manifest_passes_strict_validation() -> None:
    inventory = runtime.load_inventory(MANIFEST)

    assert inventory["schema_version"] == 2
    assert len(inventory["services"]) == 12
    assert all("check_interval_seconds" in service for service in inventory["services"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["services"][0].pop("health_policy"),
            "missing required field 'health_policy'",
        ),
        (
            lambda value: value["services"][4]["checkpoint"].update(state_refs=[99]),
            "outside the declared collection",
        ),
        (
            lambda value: value["services"][0].update(secret_refs=[]),
            "reference every declared secret policy file",
        ),
        (
            lambda value: value["services"][5]["replay"].update(evidence=[]),
            "verified replay assurance requires evidence",
        ),
        (
            lambda value: value["services"][0]["lifecycle_authorization"].update(
                state="authorized",
                authorized_operations=["status", "start", "stop", "restart"],
            ),
            "authorized lifecycle requires enabled managed adoption",
        ),
        (
            lambda value: value["services"][5]["checkpoint"].update(takeover_ready=True),
            "requires explicit lifecycle authorization",
        ),
        (
            lambda value: value["services"][0]["runbook"].update(section="invalid section"),
            "invalid value",
        ),
    ],
)
def test_management_catalog_schema_and_semantics_fail_closed(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutation(value)

    with pytest.raises(runtime.InventoryError, match=message):
        _load(tmp_path, value)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schema_version=1), "schema_version"),
        (lambda value: value["services"][0].update(unknown_nested=True), "unknown field"),
        (
            lambda value: value["services"][0]["controller"].update(adoption="managed"),
            "observe-only",
        ),
        (
            lambda value: value["services"][0]["state"].append(
                {
                    "path": "${PROJECT_ROOT}/state.json",
                    "format": "json",
                    "status_field": "status",
                    "complete_values": ["stopped"],
                }
            ),
            "unsafe terminal state",
        ),
    ],
)
def test_schema_v2_is_deep_and_fail_closed(tmp_path: Path, mutation: Any, message: str) -> None:
    value = _manifest(_service("worker"))
    mutation(value)

    with pytest.raises(runtime.InventoryError, match=message):
        _load(tmp_path, value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["services"][0]["controller"].update(path="/etc/passwd"),
        lambda value: value["services"][0]["pid"].update(path="${PROJECT_ROOT}/../escape.pid"),
        lambda value: value["services"][0].update(
            output=[{"glob": "/etc/*.conf", "required": False}]
        ),
    ],
)
def test_manifest_paths_and_globs_cannot_escape_trust_roots(tmp_path: Path, mutation: Any) -> None:
    value = _manifest(_service("worker"))
    mutation(value)

    with pytest.raises(runtime.InventoryError, match="trusted roots|may not contain"):
        _load(tmp_path, value)


def test_symlinked_path_cannot_escape_trust_roots(tmp_path: Path) -> None:
    (tmp_path / "outside").symlink_to("/etc/passwd")
    value = _manifest(_service("worker"))
    value["services"][0]["controller"]["path"] = "${PROJECT_ROOT}/outside"

    with pytest.raises(runtime.InventoryError, match="trusted roots"):
        _load(tmp_path, value)


@pytest.mark.parametrize("host", ["169.254.169.254", "example.com", "0.0.0.0", "::"])
def test_health_targets_are_literal_loopback_only(tmp_path: Path, host: str) -> None:
    service = _service("worker")
    service["port"] = [{"id": "http", "host": host, "number": 8080, "required": True}]
    service["health"] = [
        {
            "type": "http",
            "port_ref": "http",
            "path": "/health",
            "expect_status": [200],
            "timeout_seconds": 1,
            "required": True,
        }
    ]

    with pytest.raises(runtime.InventoryError, match="loopback"):
        _load(tmp_path, _manifest(service))


def test_unix_control_health_requires_schema_v2_json_pid_metadata(tmp_path: Path) -> None:
    service = _service("worker")
    service["pid"].update(
        meta_path="${PROJECT_ROOT}/worker.pid.meta",
        meta={"pid_index": 0, "starttime_ticks_index": 1},
    )
    service["health"] = [
        {
            "type": "unix-control-status",
            "path": "${PROJECT_ROOT}/worker.sock",
            "expect_status": ["running"],
            "timeout_seconds": 1,
            "required": True,
        }
    ]

    with pytest.raises(runtime.InventoryError, match="schema-v2 JSON PID identity metadata"):
        _load(tmp_path, _manifest(service))


def test_json_pid_metadata_rejects_non_v2_schema(tmp_path: Path) -> None:
    service = _service("worker")
    service["pid"].update(
        meta_path="${PROJECT_ROOT}/worker.pid.meta",
        meta={
            "format": "json",
            "schema_version": 1,
            "pid_path": "identity.pid",
            "starttime_ticks_path": "identity.start_ticks",
        },
    )

    with pytest.raises(runtime.InventoryError, match=r"meta\.schema_version: must be 2"):
        _load(tmp_path, _manifest(service))


def test_authenticated_unix_control_probe_sends_status_only(tmp_path: Path) -> None:
    service = _service("worker")
    service["pid"].update(
        meta_path="${PROJECT_ROOT}/worker.pid.meta",
        meta={
            "format": "json",
            "schema_version": 2,
            "pid_path": "identity.pid",
            "starttime_ticks_path": "identity.start_ticks",
        },
    )
    service["health"] = [
        {
            "type": "unix-control-status",
            "path": "${PROJECT_ROOT}/worker.sock",
            "expect_status": ["running"],
            "timeout_seconds": 1,
            "required": True,
        }
    ]
    inventory = _load(tmp_path, _manifest(service))
    proc_root = _fake_proc(tmp_path)
    pid = os.getpid()
    ticks = int(os.sysconf("SC_CLK_TCK")) * 10
    _write_process(proc_root, pid, ticks, ["python", "worker.py"])
    pid_path = tmp_path / "worker.pid"
    pid_path.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_path, (1_011, 1_011))

    socket_path = tmp_path / "worker.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(1)
    socket_metadata = socket_path.lstat()
    instance_id = "worker-instance"
    boot_id = "test-boot-id"
    (tmp_path / "worker.pid.meta").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "instance_id": instance_id,
                "identity": {"pid": pid, "start_ticks": ticks, "boot_id": boot_id},
                "control_socket": {
                    "path": str(socket_path.resolve()),
                    "device": socket_metadata.st_dev,
                    "inode": socket_metadata.st_ino,
                    "owner": socket_metadata.st_uid,
                    "mode": stat.S_IMODE(socket_metadata.st_mode),
                },
            }
        ),
        encoding="utf-8",
    )
    requests: list[dict[str, Any]] = []

    class FakeControlClient:
        def __enter__(self) -> FakeControlClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, path: str) -> None:
            assert path == str(socket_path)

        def getsockopt(self, _level: int, _option: int, _size: int) -> bytes:
            return struct.pack("3i", pid, os.geteuid(), os.getegid())

        def sendall(self, payload: bytes) -> None:
            requests.append(json.loads(payload.rstrip(b"\n")))

        def recv(self, _size: int) -> bytes:
            return (
                json.dumps(
                    {
                        "ok": True,
                        "status": "running",
                        "instance_id": instance_id,
                        "pid": pid,
                    }
                ).encode("utf-8")
                + b"\n"
            )

    inspector = runtime.RuntimeInspector(
        inventory,
        proc_root=proc_root,
        now=lambda: 2_000.0,
        unix_socket_factory=FakeControlClient,
    )
    try:
        result = inspector.inspect_service(inventory["services"][0], doctor=False)
    finally:
        server.close()

    assert requests == [
        {
            "schema_version": 1,
            "command": "status",
            "instance_id": instance_id,
            "boot_id": boot_id,
        }
    ]
    assert result["health"] == [
        {
            "type": "unix-control-status",
            "path": str(socket_path),
            "status": "passing",
            "issues": [],
            "observed_status": "running",
            "peer_identity_verified": True,
            "latency_ms": result["health"][0]["latency_ms"],
        }
    ]
    assert result["status"] == "healthy"


def test_selection_includes_transitive_dependencies_without_invocation_fields(
    tmp_path: Path,
) -> None:
    database = _service("database")
    api = _service("api", [{"service": "database", "required": True}])
    tunnel = _service("tunnel", [{"service": "api", "required": True}])
    inventory = _load(tmp_path, _manifest(tunnel, database, api))

    payload = runtime._list_payload(inventory, ["tunnel"])

    assert {service["id"] for service in payload["services"]} == {"tunnel", "api", "database"}
    assert payload["dependency_closure"] == ["database", "api", "tunnel"]
    assert not ({"argv", "cmdline", "command"} & _all_keys(payload))


def test_identity_and_secret_detection_never_return_process_arguments(tmp_path: Path) -> None:
    secrets = ["pg-secret", "aws-secret", "bearer-secret", "flag-secret", "dsn-secret"]
    service = _service("worker")
    service["pid"].update(
        meta_path="${PROJECT_ROOT}/worker.pid.meta",
        meta={"pid_index": 0, "starttime_ticks_index": 1},
    )
    inventory = _load(tmp_path, _manifest(service))
    proc_root = _fake_proc(tmp_path)
    inspector = runtime.RuntimeInspector(inventory, proc_root=proc_root, now=lambda: 2_000.0)
    pid = 123
    ticks = inspector.clock_ticks * 10
    _write_process(
        proc_root,
        pid,
        ticks,
        [
            "python",
            "worker.py",
            f"PGPASSWORD={secrets[0]}",
            f"AWS_SECRET_ACCESS_KEY={secrets[1]}",
            "Authorization=Bearer " + secrets[2],
            "--token",
            secrets[3],
            "postgresql://user:" + secrets[4] + "@db/news",
        ],
    )
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_011, 1_011))
    (tmp_path / "worker.pid.meta").write_text(f"{pid} {ticks}\n", encoding="utf-8")

    result = inspector.inspect_service(inventory["services"][0], doctor=False)
    serialized = json.dumps(result)

    assert result["pid"]["identity_strength"] == "strong"
    assert result["pid"]["control_eligible"] is True
    assert result["secret_policy"]["compliant"] is False
    assert all(secret not in serialized for secret in secrets)
    assert not ({"argv", "cmdline", "command"} & _all_keys(result))


def test_missing_start_ticks_metadata_is_weak_and_not_control_eligible(tmp_path: Path) -> None:
    inventory = _load(tmp_path, _manifest(_service("worker")))
    proc_root = _fake_proc(tmp_path)
    inspector = runtime.RuntimeInspector(inventory, proc_root=proc_root, now=lambda: 2_000.0)
    ticks = inspector.clock_ticks * 10
    _write_process(proc_root, 321, ticks, ["python", "worker.py"])
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("321\n", encoding="utf-8")
    os.utime(pid_file, (1_011, 1_011))

    result = inspector._inspect_pid_file(inventory["services"][0]["pid"], pid_file)

    assert result["status"] == "running"
    assert result["identity_strength"] == "weak"
    assert result["control_eligible"] is False
    assert "argv" not in result


def test_process_marker_requires_exact_token_not_substring(tmp_path: Path) -> None:
    service = _service("worker")
    service["pid"].update(
        meta_path=str(tmp_path / "worker.pid.meta"),
        meta={"pid_index": 0, "starttime_ticks_index": 1},
    )
    inventory = _load(tmp_path, _manifest(service))
    proc_root = _fake_proc(tmp_path)
    inspector = runtime.RuntimeInspector(inventory, proc_root=proc_root, now=lambda: 2_000.0)
    ticks = inspector.clock_ticks * 10
    _write_process(proc_root, 444, ticks, ["python", "/tmp/notworker.py.backup"])
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("444\n", encoding="utf-8")
    os.utime(pid_file, (1_011, 1_011))
    (tmp_path / "worker.pid.meta").write_text(f"444 {ticks}\n", encoding="utf-8")

    result = inspector._inspect_pid_file(inventory["services"][0]["pid"], pid_file)

    assert result["status"] == "stale"
    assert result["identity_strength"] == "none"
    assert result["control_eligible"] is False
    assert "pid-cmdline-mismatch" in {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("terminal", ["stopped", "failed", "cancelled", "error"])
def test_non_success_terminal_state_never_marks_pipeline_complete(
    tmp_path: Path, terminal: str
) -> None:
    service = _service("worker")
    service["pid"]["path"] = str(tmp_path / "worker.pid")
    service["pid"]["expected"] = "running-or-complete"
    service["state"] = [
        {
            "path": str(tmp_path / "state.json"),
            "format": "json",
            "status_field": "status",
            "complete_values": [terminal],
            "authoritative": True,
        }
    ]
    inventory = Inventory(
        {"schema_version": 2, "inventory_version": "test", "services": [service]},
        (tmp_path,),
    )
    (tmp_path / "state.json").write_text(json.dumps({"status": terminal}), encoding="utf-8")

    result = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path)).inspect_service(
        service, doctor=False
    )

    assert result["pid"]["status"] == "missing"
    assert result["state"][0]["complete"] is False
    assert result["status"] == "unhealthy"


@pytest.mark.parametrize(
    ("authoritative", "expected_status"),
    [(False, "missing"), (True, "complete")],
)
def test_only_authoritative_success_state_can_replace_missing_pid(
    tmp_path: Path, authoritative: bool, expected_status: str
) -> None:
    service = _service("worker")
    service["pid"]["path"] = str(tmp_path / "worker.pid")
    service["pid"]["expected"] = "running-or-complete"
    service["state"] = [
        {
            "path": str(tmp_path / "state.json"),
            "format": "json",
            "status_field": "status",
            "complete_values": ["completed"],
            "authoritative": authoritative,
        }
    ]
    inventory = Inventory(
        {"schema_version": 2, "inventory_version": "test", "services": [service]},
        (tmp_path,),
    )
    (tmp_path / "state.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    result = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path)).inspect_service(
        service, doctor=False
    )

    assert result["pid"]["status"] == expected_status


def test_summary_fields_cannot_reintroduce_invocation_or_secrets(tmp_path: Path) -> None:
    inventory = _load(tmp_path, _manifest(_service("worker")))
    state = tmp_path / "state.json"
    secret = "not-for-output"
    state.write_text(
        json.dumps(
            {"argv": ["--password", secret], "PGPASSWORD": secret, "note": f"Bearer {secret}"}
        ),
        encoding="utf-8",
    )
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    result = inspector._inspect_file(
        {"path": str(state), "format": "json", "summary_fields": ["argv", "PGPASSWORD", "note"]},
        "state",
    )
    serialized = json.dumps(result)

    assert "argv" not in result["summary"]
    assert result["summary"]["PGPASSWORD"] == runtime.REDACTED
    assert secret not in serialized


def test_generic_secret_suffixes_and_authorization_schemes_are_redacted() -> None:
    secrets = [
        "openai-value",
        "database-value",
        "custom-token-value",
        "custom-secret-value",
        "basic-value",
        "bearer-value",
    ]
    value = {
        "OPENAI_API_KEY": secrets[0],
        "DATABASE_PASSWORD": secrets[1],
        "CUSTOM_TOKEN": secrets[2],
        "CUSTOM_SECRET": secrets[3],
        "basic": f"Authorization: Basic {secrets[4]}",
        "bearer": f"Authorization=Bearer {secrets[5]}",
    }

    serialized = json.dumps(runtime.sanitize(value))

    assert all(secret not in serialized for secret in secrets)
    assert serialized.count(runtime.REDACTED) >= len(secrets)

    safe, findings = runtime.redact_argv(
        ["client", "Authorization:", "Basic", "split-basic-secret"]
    )
    assert "split-basic-secret" not in json.dumps(safe)
    assert findings


def test_recursive_invocation_aliases_are_removed_from_output() -> None:
    value = {
        "safe": {
            "commands": ["run"],
            "process_args": ["--token", "secret"],
            "argv_raw": ["secret"],
            "raw_command": "secret",
            "nested": [{"command_line": "secret"}, {"value": "kept"}],
        }
    }

    result = runtime.sanitize(value)
    keys = _all_keys(result)

    assert not ({"commands", "process_args", "argv_raw", "raw_command", "command_line"} & keys)
    assert result["safe"]["nested"][1]["value"] == "kept"


@pytest.mark.parametrize(
    "content",
    [
        b'{"updated_at": NaN}',
        ("[" * 40 + "0" + "]" * 40).encode(),
        b'{"updated_at":"Infinity"}',
        b"\xff\xfe",
    ],
)
def test_state_json_is_bounded_unicode_safe_and_finite(tmp_path: Path, content: bytes) -> None:
    inventory = _load(tmp_path, _manifest(_service("worker")))
    state = tmp_path / "state.json"
    state.write_bytes(content)
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    result = inspector._inspect_file(
        {"path": str(state), "format": "json", "timestamp_field": "updated_at"},
        "state",
    )

    assert result["status"] == "invalid" or any(
        issue["code"] == "state-timestamp-invalid" for issue in result["issues"]
    )


def test_state_json_size_limit_prevents_large_read(tmp_path: Path) -> None:
    inventory = _load(tmp_path, _manifest(_service("worker")))
    state = tmp_path / "state.json"
    with state.open("wb") as handle:
        handle.truncate(8 * 1024 * 1024 + 1)
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    result = inspector._inspect_file({"path": str(state), "format": "json"}, "state")

    assert result["status"] == "invalid"
    assert {issue["code"] for issue in result["issues"]} == {"state-json-too-large"}


def test_dependency_health_propagates_in_topological_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = _service("leaf")
    middle = _service("middle", [{"service": "leaf", "required": True}])
    root = _service("root", [{"service": "middle", "required": True}])
    inventory = _load(tmp_path, _manifest(root, middle, leaf))
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    def fake_inspect(service: dict[str, Any], *, doctor: bool) -> dict[str, Any]:
        del doctor
        unhealthy = service["id"] == "leaf"
        return {
            "id": service["id"],
            "dependencies": service["dependencies"],
            "issues": (
                [{"severity": "error", "code": "failed", "message": "failed"}] if unhealthy else []
            ),
            "status": "unhealthy" if unhealthy else "healthy",
        }

    monkeypatch.setattr(inspector, "inspect_service", fake_inspect)

    payload = inspector.inspect(["root"])

    assert payload["dependency_closure"] == ["leaf", "middle", "root"]
    assert [service["status"] for service in payload["services"]] == [
        "unhealthy",
        "unhealthy",
        "unhealthy",
    ]


@pytest.mark.parametrize(
    ("dependency_status", "required", "expected_severity", "expected_consumer_status"),
    (
        ("degraded", False, "info", "healthy"),
        ("unhealthy", False, "info", "healthy"),
        ("degraded", True, "warning", "degraded"),
        ("unhealthy", True, "error", "unhealthy"),
    ),
)
def test_internal_dependency_status_propagation_respects_required_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_status: str,
    required: bool,
    expected_severity: str,
    expected_consumer_status: str,
) -> None:
    dependency = _service("dependency")
    consumer = _service(
        "consumer",
        [{"service": "dependency", "required": required}],
    )
    root = _service("root", [{"service": "consumer", "required": True}])
    inventory = _load(tmp_path, _manifest(root, consumer, dependency))
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))
    dependency_severity = "error" if dependency_status == "unhealthy" else "warning"

    def fake_inspect(service: dict[str, Any], *, doctor: bool) -> dict[str, Any]:
        del doctor
        is_dependency = service["id"] == "dependency"
        return {
            "id": service["id"],
            "dependencies": service["dependencies"],
            "issues": (
                [
                    {
                        "severity": dependency_severity,
                        "code": "dependency-self-check",
                        "message": "dependency self check is not healthy",
                    }
                ]
                if is_dependency
                else []
            ),
            "status": dependency_status if is_dependency else "healthy",
        }

    monkeypatch.setattr(inspector, "inspect_service", fake_inspect)

    payload = inspector.inspect(["root"])
    by_id = {service["id"]: service for service in payload["services"]}
    propagated = next(
        issue for issue in by_id["consumer"]["issues"] if issue["code"] == "dependency-unhealthy"
    )

    assert by_id["dependency"]["status"] == dependency_status
    assert by_id["consumer"]["status"] == expected_consumer_status
    assert by_id["root"]["status"] == expected_consumer_status
    assert propagated["severity"] == expected_severity
    assert propagated["details"] == {
        "dependency": "dependency",
        "dependency_status": dependency_status,
        "required": required,
    }


def test_unverified_external_dependency_is_explicit_and_degrading(tmp_path: Path) -> None:
    service = _service("worker")
    service["external_dependencies"] = ["postgres-news"]
    inventory = _load(tmp_path, _manifest(service))
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    results, issues = inspector._inspect_external_dependencies(service, [])

    assert results == [
        {
            "name": "postgres-news",
            "required": True,
            "verification": "unverified",
            "via_health": None,
            "observed_status": "unverified",
        }
    ]
    assert issues[0]["severity"] == "warning"
    assert issues[0]["code"] == "external-dependency-unverified"


def test_unknown_secret_policy_is_never_reported_compliant(tmp_path: Path) -> None:
    service = _service("worker")
    service["secret_policy"]["environment"] = "unknown-policy"
    inventory = Inventory(
        {"schema_version": 2, "inventory_version": "test", "services": [service]},
        (tmp_path,),
    )
    inspector = runtime.RuntimeInspector(inventory, proc_root=_fake_proc(tmp_path))

    result = inspector._inspect_secret_policy(service, {"secret_findings": []}, doctor=False)

    assert result["compliant"] is False
    assert {issue["code"] for issue in result["issues"]} == {"secret-policy-unknown"}


def test_destructive_cli_is_refused_without_command_field(capsys: Any) -> None:
    exit_code = runtime.main(["stop", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 64
    assert payload["error"] == "destructive-command-disabled"
    assert payload["operation"] == "stop"
    assert not ({"argv", "cmdline", "command"} & _all_keys(payload))


@pytest.mark.parametrize(
    "arguments",
    [
        ["--manifest", "/tmp/manifest.json", "list", "--json"],
        ["--root=/", "list", "--json"],
    ],
)
def test_cli_rejects_manifest_and_trust_root_overrides(capsys: Any, arguments: list[str]) -> None:
    exit_code = runtime.main(arguments)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 64
    assert payload["error"] == "configuration-override-disabled"
    assert payload["operation"] == "configuration"
    assert "/tmp/manifest.json" not in json.dumps(payload)


def test_service_named_stop_is_not_treated_as_destructive_subcommand(capsys: Any) -> None:
    exit_code = runtime.main(["status", "stop", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"] == "inventory-error"
    assert "operation" not in payload
