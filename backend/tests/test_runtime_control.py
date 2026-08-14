from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import globemind_runtime as runtime
from scripts.runtime_control import cli as runtime_cli
from scripts.runtime_control.lifecycle import LifecycleDispatcher, LifecycleError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "ops/runtime/services.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_payload(tmp_path: Path, *, dependency: bool = False) -> tuple[dict, dict[str, Path]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    controller = tmp_path / "controller.sh"
    controller.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'printf "%s\\n" "$1" >> "$GLOBEMIND_HOME/controller.calls"\n'
        "printf '%s\\n' 'password=fixture-secret'\n",
        encoding="utf-8",
    )
    controller.chmod(0o700)
    checkpoint = tmp_path / "checkpoint.md"
    checkpoint.write_text("checkpoint procedure\n", encoding="utf-8")
    rollback = tmp_path / "rollback.md"
    rollback.write_text("rollback procedure\n", encoding="utf-8")
    audit = tmp_path / "audit"
    audit.mkdir(mode=0o700)

    worker = {
        "id": "worker",
        "name": "Fixture worker",
        "kind": "service",
        "owner": "test",
        "criticality": "medium",
        "check_interval_seconds": 60,
        "dependencies": ([{"service": "database", "required": True}] if dependency else []),
        "external_dependencies": [],
        "controller": {
            "type": "shell-script",
            "path": "${PROJECT_ROOT}/controller.sh",
            "interface": "status|start|stop|restart",
            "adoption": "managed",
            "lifecycle": {
                "enabled": True,
                "argv": {
                    operation: ["${PROJECT_ROOT}/controller.sh", operation]
                    for operation in ("status", "start", "stop", "restart")
                },
                "controller_artifacts": [
                    {
                        "path": "${PROJECT_ROOT}/controller.sh",
                        "sha256": _digest(controller),
                    }
                ],
                "checkpoint": {
                    "path": "${PROJECT_ROOT}/checkpoint.md",
                    "sha256": _digest(checkpoint),
                },
                "rollback": {
                    "path": "${PROJECT_ROOT}/rollback.md",
                    "sha256": _digest(rollback),
                },
                "audit_directory": "${PROJECT_ROOT}/audit",
                "timeout_seconds": 5,
            },
        },
        "pid": {
            "kind": "single",
            "path": "${PROJECT_ROOT}/worker.pid",
            "meta_path": "${PROJECT_ROOT}/worker.pid.meta",
            "meta": {"pid_index": 0, "starttime_ticks_index": 1},
            "cmdline_contains": ["fixture-worker"],
            "expected": "running",
        },
        "port": [
            {"id": "http", "host": "127.0.0.1", "number": 18001, "required": True}
        ],
        "log": [],
        "health": [
            {
                "type": "http",
                "port_ref": "http",
                "path": "/health",
                "expect_status": [200],
                "timeout_seconds": 1,
                "required": True,
            }
        ],
        "health_policy": {
            "mode": "active-probe",
            "signals": [
                {"source": "pid", "required": True},
                {"source": "health", "index": 0, "required": True},
            ],
        },
        "state": [],
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
            "environment": "secret-file-or-process-environment-only",
            "files": [],
            "redact_diagnostics": True,
        },
        "secret_refs": [],
        "lifecycle_authorization": {
            "state": "authorized",
            "authorized_operations": ["status", "start", "stop", "restart"],
            "change_request_required": True,
            "maintenance_window_required": True,
            "required_approvals": ["service-owner", "platform-operations"],
        },
        "runbook": {"path": "${PROJECT_ROOT}/checkpoint.md", "section": "worker"},
    }
    services = [worker]
    if dependency:
        services.insert(
            0,
            {
                **worker,
                "id": "database",
                "name": "Fixture dependency",
                "dependencies": [],
                "controller": {
                    "type": "shell-script",
                    "path": "${PROJECT_ROOT}/controller.sh",
                    "interface": "status|start|stop|restart",
                    "adoption": "observe-only",
                },
                "pid": {
                    "kind": "single",
                    "path": "${PROJECT_ROOT}/database.pid",
                    "cmdline_contains": ["fixture-database"],
                    "expected": "running",
                },
                "port": [],
                "health": [],
                "health_policy": {
                    "mode": "process-only",
                    "signals": [{"source": "pid", "required": True}],
                },
                "lifecycle_authorization": {
                    "state": "not-authorized",
                    "authorized_operations": [],
                    "change_request_required": True,
                    "maintenance_window_required": True,
                    "required_approvals": ["service-owner", "platform-operations"],
                },
                "runbook": {
                    "path": "${PROJECT_ROOT}/checkpoint.md",
                    "section": "database",
                },
            },
        )
    payload = {
        "schema_version": 2,
        "inventory_version": "test",
        "project": "fixture",
        "variables": {"PROJECT_ROOT": str(tmp_path), "DATA_ROOT": str(tmp_path)},
        "control_policy": {
            "mode": "read_only",
            "destructive_commands_enabled": False,
            "allowed_commands": ["catalog", "list", "status", "doctor"],
        },
        "services": services,
    }
    return payload, {
        "audit": audit,
        "checkpoint": checkpoint,
        "controller": controller,
        "rollback": rollback,
    }


def _load(tmp_path: Path, payload: dict) -> runtime.Inventory:
    manifest = tmp_path / "services.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o600)
    return runtime.load_inventory(manifest, trusted_roots=(tmp_path,))


def _observed_worker(
    *,
    pid_status: str,
    identity_strength: str = "none",
    control_eligible: bool = False,
    health: str = "passing",
    secret_compliant: bool = True,
) -> dict[str, Any]:
    pid = {
        "status": pid_status,
        "identity_strength": identity_strength,
        "control_eligible": control_eligible,
    }
    if pid_status == "running":
        pid.update({"pid": 4321, "starttime_ticks": 9876})
    return {
        "id": "worker",
        "status": "healthy",
        "pid": pid,
        "health": [{"status": health}],
        "external_dependencies": [],
        "secret_policy": {"compliant": secret_compliant},
    }


def _inspection(*services: dict[str, Any]) -> dict[str, Any]:
    return {"services": list(services)}


class SequenceInspector:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[str], bool]] = []

    def inspect(self, service_ids: list[str], *, doctor: bool) -> dict[str, Any]:
        self.calls.append((list(service_ids), doctor))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]

    def identity_is_gone(self, _pid: int, _starttime_ticks: int) -> bool:
        return True


def _start_inspector() -> SequenceInspector:
    return SequenceInspector(
        _inspection(_observed_worker(pid_status="missing")),
        _inspection(
            _observed_worker(
                pid_status="running", identity_strength="strong", control_eligible=True
            )
        ),
    )


def test_real_inventory_keeps_every_production_service_observe_only() -> None:
    inventory = runtime.load_inventory(MANIFEST)

    assert inventory["inventory_version"] == "1.0.0"
    for service in inventory["services"]:
        assert service["controller"]["adoption"] == "observe-only"
        assert "lifecycle" not in service["controller"]


def test_cli_dispatches_only_fixed_fixture_controller_argv_and_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload, paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    dispatcher = LifecycleDispatcher(inventory, inspector=_start_inspector())
    monkeypatch.setattr(runtime_cli, "load_inventory", lambda: inventory)
    monkeypatch.setattr(runtime_cli, "LifecycleDispatcher", lambda _inventory: dispatcher)

    exit_code = runtime.main(
        ["start", "worker", "--apply", "--request-id", "change-0001", "--json"]
    )
    response_text = capsys.readouterr().out
    response = json.loads(response_text)

    assert exit_code == 0
    assert response["outcome"] == "succeeded"
    assert response["read_only"] is False
    assert (tmp_path / "controller.calls").read_text(encoding="utf-8") == "start\n"
    assert "fixture-secret" not in response_text
    audit_files = sorted(paths["audit"].glob("*.json"))
    assert len(audit_files) == 2
    events = [json.loads(path.read_text(encoding="utf-8")) for path in audit_files]
    assert {event["outcome"] for event in events} == {"dispatch-started", "succeeded"}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in audit_files)
    assert not list(paths["audit"].glob("*.tmp"))
    assert "fixture-secret" not in "".join(path.read_text() for path in audit_files)
    assert "argv" not in "".join(path.read_text() for path in audit_files)


def test_dispatch_uses_minimal_environment_shell_false_and_exact_two_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    captured: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("MANAGED_LOOP_PROC_ROOT", str(tmp_path / "forged-proc"))
    monkeypatch.setenv("PYTHON_BIN", "/tmp/attacker-python")
    dispatcher = LifecycleDispatcher(inventory, inspector=_start_inspector(), runner=runner)

    result = dispatcher.execute(
        "worker", "start", dry_run=False, request_id="change-0002"
    )

    assert result["outcome"] == "succeeded"
    assert captured["argv"] == [str(paths["controller"]), "start"]
    assert captured["shell"] is False
    assert captured["check"] is False
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["env"] == {
        "GLOBEMIND_HOME": str(tmp_path),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert result["output_policy"] == "discarded"


def test_cli_defaults_to_audited_plan_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload, paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    dispatcher = LifecycleDispatcher(
        inventory,
        inspector=SequenceInspector(_inspection(_observed_worker(pid_status="missing"))),
    )
    monkeypatch.setattr(runtime_cli, "load_inventory", lambda: inventory)
    monkeypatch.setattr(runtime_cli, "LifecycleDispatcher", lambda _inventory: dispatcher)

    exit_code = runtime.main(["start", "worker", "--json"])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["outcome"] == "planned"
    assert result["read_only"] is True
    assert not (tmp_path / "controller.calls").exists()
    events = list(paths["audit"].glob("*.json"))
    assert len(events) == 1
    assert json.loads(events[0].read_text())["outcome"] == "planned"


def test_unknown_and_unadopted_services_fail_before_inspection(tmp_path: Path) -> None:
    payload, _paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    inspector = SequenceInspector(_inspection())
    dispatcher = LifecycleDispatcher(inventory, inspector=inspector)

    with pytest.raises(LifecycleError, match="unknown service") as unknown:
        dispatcher.execute("missing", "start", dry_run=True)
    assert unknown.value.code == "unknown-service"

    payload["services"][0]["controller"].pop("lifecycle")
    payload["services"][0]["controller"]["adoption"] = "observe-only"
    payload["services"][0]["lifecycle_authorization"].update(
        state="not-authorized", authorized_operations=[]
    )
    inventory = _load(tmp_path, payload)
    dispatcher = LifecycleDispatcher(inventory, inspector=inspector)
    with pytest.raises(LifecycleError, match="has not adopted") as disabled:
        dispatcher.execute("worker", "start", dry_run=True)
    assert disabled.value.code == "lifecycle-not-enabled"
    assert inspector.calls == []


def test_required_dependency_and_weak_identity_fail_closed(tmp_path: Path) -> None:
    payload, paths = _fixture_payload(tmp_path, dependency=True)
    inventory = _load(tmp_path, payload)
    dependency = {
        "id": "database",
        "status": "unhealthy",
        "pid": {"status": "missing"},
        "health": [],
        "external_dependencies": [],
        "secret_policy": {"compliant": True},
    }
    inspector = SequenceInspector(
        _inspection(dependency, _observed_worker(pid_status="missing"))
    )
    dispatcher = LifecycleDispatcher(inventory, inspector=inspector)

    with pytest.raises(LifecycleError) as dependency_error:
        dispatcher.execute("worker", "start", dry_run=True)
    assert dependency_error.value.code == "lifecycle-preflight-failed"
    assert "required dependency database" in " ".join(dependency_error.value.details)

    payload, paths = _fixture_payload(tmp_path / "weak")
    inventory = _load(tmp_path / "weak", payload)
    weak = _observed_worker(
        pid_status="running", identity_strength="weak", control_eligible=False, health="failing"
    )
    dispatcher = LifecycleDispatcher(inventory, inspector=SequenceInspector(_inspection(weak)))
    with pytest.raises(LifecycleError) as identity_error:
        dispatcher.execute("worker", "stop", dry_run=True)
    assert "strong PID" in " ".join(identity_error.value.details)
    assert json.loads(next(paths["audit"].glob("*.json")).read_text())["outcome"] == (
        "preflight-denied"
    )


def test_secret_and_evidence_failures_are_denied_and_audited(tmp_path: Path) -> None:
    payload, paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    paths["checkpoint"].write_text("tampered\n", encoding="utf-8")
    dispatcher = LifecycleDispatcher(inventory, inspector=_start_inspector())

    with pytest.raises(LifecycleError) as evidence_error:
        dispatcher.execute("worker", "start", dry_run=True)
    assert evidence_error.value.code == "evidence-invalid"
    assert json.loads(next(paths["audit"].glob("*.json")).read_text())["error_code"] == (
        "evidence-invalid"
    )

    payload, paths = _fixture_payload(tmp_path / "secret")
    inventory = _load(tmp_path / "secret", payload)
    inspector = SequenceInspector(
        _inspection(_observed_worker(pid_status="missing", secret_compliant=False))
    )
    dispatcher = LifecycleDispatcher(inventory, inspector=inspector)
    with pytest.raises(LifecycleError) as secret_error:
        dispatcher.execute("worker", "start", dry_run=True)
    assert "secret policy" in " ".join(secret_error.value.details)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["services"][0]["controller"]["lifecycle"].update(
            audit_directory="/etc/globemind-audit"
        ),
        lambda value: value["services"][0]["controller"].update(
            interface="status|start|stop|restart|stop;touch"
        ),
        lambda value: value["services"][0].update(health=[]),
    ],
)
def test_enabled_manifest_rejects_path_operation_and_health_gaps(
    tmp_path: Path, mutation: Any
) -> None:
    payload, _paths = _fixture_payload(tmp_path)
    mutation(payload)

    with pytest.raises(runtime.InventoryError):
        _load(tmp_path, payload)


def test_manifest_and_controller_must_not_be_writable_by_other_principals(tmp_path: Path) -> None:
    payload, paths = _fixture_payload(tmp_path)
    paths["controller"].chmod(0o722)
    inventory = _load(tmp_path, payload)
    dispatcher = LifecycleDispatcher(inventory, inspector=_start_inspector())
    with pytest.raises(LifecycleError, match="group/world writable"):
        dispatcher.execute("worker", "start", dry_run=True)

    manifest = tmp_path / "writable.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    manifest.chmod(0o666)
    with pytest.raises(runtime.InventoryError, match="group/world writable"):
        runtime.load_inventory(manifest, trusted_roots=(tmp_path,))


def test_parameter_injection_and_multi_service_fanout_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload, _paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    dispatcher = LifecycleDispatcher(inventory, inspector=_start_inspector())

    with pytest.raises(LifecycleError) as operation_error:
        dispatcher.execute("worker", "start;touch", dry_run=True)
    assert operation_error.value.code == "operation-forbidden"

    exit_code = runtime.main(["restart", "worker", "database", "--json"])
    response = json.loads(capsys.readouterr().out)
    assert exit_code == 64
    assert response["error"] == "service-selection-invalid"


def test_zero_exit_with_failed_postflight_is_not_reported_as_success(tmp_path: Path) -> None:
    payload, _paths = _fixture_payload(tmp_path)
    inventory = _load(tmp_path, payload)
    inspector = SequenceInspector(
        _inspection(_observed_worker(pid_status="missing")),
        _inspection(
            _observed_worker(
                pid_status="running", identity_strength="weak", control_eligible=False
            )
        ),
    )
    dispatcher = LifecycleDispatcher(inventory, inspector=inspector)

    result = dispatcher.execute(
        "worker", "start", dry_run=False, request_id="change-0003"
    )

    assert result["exit_code"] == 0
    assert result["outcome"] == "failed"
    assert "postflight PID identity is not strong" in result["postflight"]
