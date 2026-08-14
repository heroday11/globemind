from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import globemind_runtime as runtime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "ops" / "runtime" / "services.json"


def _write_fake_process(
    proc_root: Path,
    pid: int,
    *,
    starttime_ticks: int,
    argv: list[str],
    state: str = "S",
) -> None:
    process_root = proc_root / str(pid)
    process_root.mkdir(parents=True)
    # /proc/<pid>/stat fields after comm start at field 3; starttime is field 22.
    tail = [state, *("0" for _ in range(18)), str(starttime_ticks), "0"]
    (process_root / "stat").write_text(
        f"{pid} (python worker) {' '.join(tail)}\n",
        encoding="utf-8",
    )
    (process_root / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")


def _fake_inspector(tmp_path: Path, *, boot_time: int = 1_000) -> runtime.RuntimeInspector:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(exist_ok=True)
    (proc_root / "stat").write_text(f"cpu 1 2 3 4\nbtime {boot_time}\n", encoding="utf-8")
    return runtime.RuntimeInspector({}, proc_root=proc_root, now=lambda: 2_000.0)


def test_inventory_covers_declared_runtime_families_and_is_read_only() -> None:
    inventory = runtime.load_inventory(MANIFEST)
    identifiers = {service["id"] for service in inventory["services"]}

    assert identifiers == {
        "daily_ingest",
        "ground_images",
        "ground_refresh",
        "l1_extract",
        "l1_prep",
        "proxy_pool",
        "quality_labels",
        "tunnel",
        "vllm",
        "wave1_extractor",
        "wave1_loader",
        "web",
    }
    assert inventory["control_policy"]["destructive_commands_enabled"] is False
    assert set(inventory["control_policy"]["allowed_commands"]) == runtime.SAFE_COMMANDS
    for service in inventory["services"]:
        assert service["owner"]
        assert service["criticality"] in {"critical", "high", "medium"}
        assert service["controller"]["adoption"] == "observe-only"
        assert service["secret_policy"]["argv"] == "forbid-sensitive-values"
        assert service["lifecycle_authorization"]["state"] == "not-authorized"
        assert service["checkpoint"]["takeover_ready"] is False


def test_pid_identity_uses_cmdline_starttime_and_metadata(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 123
    ticks = inspector.clock_ticks * 10
    _write_fake_process(
        inspector.proc_root,
        pid,
        starttime_ticks=ticks,
        argv=["python", "backend/serve_prod.py"],
    )
    pid_file = tmp_path / "web.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_011, 1_011))
    meta_file = tmp_path / "web.pid.meta"
    meta_file.write_text(f"{pid} {ticks} 18089 production\n", encoding="utf-8")
    spec = {
        "path": str(pid_file),
        "meta_path": str(meta_file),
        "meta": {"pid_index": 0, "starttime_ticks_index": 1},
        "cmdline_contains": ["backend/serve_prod.py"],
    }

    result = inspector._inspect_pid_file(spec, pid_file)

    assert result["status"] == "running"
    assert result["identity_verified"] is True
    assert result["starttime_ticks"] == ticks
    assert "argv" not in result
    assert "cmdline" not in result
    assert "command" not in result
    assert result["issues"] == []


def test_five_token_pid_metadata_strictly_binds_pid_and_starttime(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 126
    ticks = inspector.clock_ticks * 13
    _write_fake_process(
        inspector.proc_root,
        pid,
        starttime_ticks=ticks,
        argv=["bash", "deploy/news_quality_labels_loop.sh"],
    )
    pid_file = tmp_path / "quality-labels.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_014, 1_014))
    meta_file = tmp_path / "quality-labels.pid.meta"
    spec = {
        "path": str(pid_file),
        "meta_path": str(meta_file),
        "meta": {"pid_index": 0, "starttime_ticks_index": 1},
        "cmdline_contains": ["deploy/news_quality_labels_loop.sh"],
    }

    meta_file.write_text(
        f"{pid} {ticks} {pid} {pid} news_quality_labels\n",
        encoding="utf-8",
    )
    result = inspector._inspect_pid_file(spec, pid_file)

    assert result["status"] == "running"
    assert result["identity_strength"] == "strong"
    assert result["recorded_starttime_ticks"] == ticks
    assert result["issues"] == []

    for mismatched_metadata in (
        f"{pid + 1} {ticks} {pid} {pid} news_quality_labels\n",
        f"{pid} {ticks + 1} {pid} {pid} news_quality_labels\n",
    ):
        meta_file.write_text(mismatched_metadata, encoding="utf-8")
        result = inspector._inspect_pid_file(spec, pid_file)

        assert result["status"] == "stale"
        assert result["identity_strength"] == "none"
        assert result["control_eligible"] is False
        assert {issue["code"] for issue in result["issues"]} == {"pid-meta-mismatch"}


def test_pid_identity_accepts_schema_v2_json_metadata(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 124
    ticks = inspector.clock_ticks * 11
    _write_fake_process(
        inspector.proc_root,
        pid,
        starttime_ticks=ticks,
        argv=["python", "scripts/stream_load_news_to_postgres.py"],
    )
    pid_file = tmp_path / "loader.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_012, 1_012))
    meta_file = tmp_path / "loader.pid.meta"
    meta_file.write_text(
        json.dumps({"schema_version": 2, "identity": {"pid": pid, "start_ticks": ticks}}),
        encoding="utf-8",
    )
    spec = {
        "path": str(pid_file),
        "meta_path": str(meta_file),
        "meta": {
            "format": "json",
            "schema_version": 2,
            "pid_path": "identity.pid",
            "starttime_ticks_path": "identity.start_ticks",
        },
        "cmdline_contains": ["scripts/stream_load_news_to_postgres.py"],
    }

    result = inspector._inspect_pid_file(spec, pid_file)

    assert result["status"] == "running"
    assert result["identity_strength"] == "strong"
    assert result["control_eligible"] is True
    assert result["recorded_starttime_ticks"] == ticks
    assert result["issues"] == []


def test_json_pid_metadata_mismatch_fails_closed(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 125
    ticks = inspector.clock_ticks * 12
    _write_fake_process(
        inspector.proc_root,
        pid,
        starttime_ticks=ticks,
        argv=["python", "scripts/stream_load_news_to_postgres.py"],
    )
    pid_file = tmp_path / "loader.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_013, 1_013))
    meta_file = tmp_path / "loader.pid.meta"
    meta_file.write_text(
        json.dumps({"schema_version": 2, "identity": {"pid": pid, "start_ticks": ticks + 1}}),
        encoding="utf-8",
    )
    spec = {
        "path": str(pid_file),
        "meta_path": str(meta_file),
        "meta": {
            "format": "json",
            "schema_version": 2,
            "pid_path": "identity.pid",
            "starttime_ticks_path": "identity.start_ticks",
        },
        "cmdline_contains": ["scripts/stream_load_news_to_postgres.py"],
    }

    result = inspector._inspect_pid_file(spec, pid_file)

    assert result["status"] == "stale"
    assert result["identity_strength"] == "none"
    assert result["control_eligible"] is False
    assert {issue["code"] for issue in result["issues"]} == {"pid-meta-mismatch"}


def test_pidfile_that_predates_reused_pid_is_stale(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 321
    ticks = inspector.clock_ticks * 100
    _write_fake_process(
        inspector.proc_root, pid, starttime_ticks=ticks, argv=["python", "worker.py"]
    )
    pid_file = tmp_path / "worker.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_010, 1_010))

    result = inspector._inspect_pid_file({"cmdline_contains": ["worker.py"]}, pid_file)

    assert result["status"] == "stale"
    assert result["identity_verified"] is False
    assert "pid-starttime-mismatch" in {issue["code"] for issue in result["issues"]}


def test_cmdline_mismatch_marks_live_pid_as_stale(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    pid = 456
    ticks = inspector.clock_ticks * 5
    _write_fake_process(
        inspector.proc_root, pid, starttime_ticks=ticks, argv=["python", "other.py"]
    )
    pid_file = tmp_path / "expected.pid"
    pid_file.write_text(f"{pid}\n", encoding="utf-8")
    os.utime(pid_file, (1_006, 1_006))

    result = inspector._inspect_pid_file({"cmdline_contains": ["expected.py"]}, pid_file)

    assert result["status"] == "stale"
    assert "pid-cmdline-mismatch" in {issue["code"] for issue in result["issues"]}
    assert "argv" not in result
    assert "cmdline" not in result
    assert "command" not in result


def test_sensitive_argv_is_strictly_redacted_without_false_token_match() -> None:
    secret_values = ["plain-secret", "url-secret", "bearer-secret", "key-secret"]
    database_scheme = "postgresql" + "://"
    argv = [
        "worker",
        "--password",
        secret_values[0],
        f"{database_scheme}user:{secret_values[1]}@db/news",
        f"authorization=Bearer-{secret_values[2]}",
        f"--api-key={secret_values[3]}",
        "--max-num-batched-tokens",
        "8192",
    ]

    safe, findings = runtime.redact_argv(argv)
    serialized = json.dumps(safe)

    assert all(secret not in serialized for secret in secret_values)
    assert safe[safe.index("--password") + 1] == runtime.REDACTED
    assert "--api-key=[REDACTED]" in safe
    assert safe[-2:] == ["--max-num-batched-tokens", "8192"]
    assert {finding["option"] for finding in findings} >= {"--password", "--api-key"}


def test_state_freshness_prefers_json_timestamp_and_sanitizes_summary(tmp_path: Path) -> None:
    inspector = _fake_inspector(tmp_path)
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"updated_at": 1_900, "status": "running", "token": "must-not-leak"}),
        encoding="utf-8",
    )
    spec = {
        "path": str(state_file),
        "format": "json",
        "timestamp_field": "updated_at",
        "max_age_seconds": 50,
        "stale_severity": "error",
        "summary_fields": ["updated_at", "status", "token"],
    }

    result = inspector._inspect_file(spec, "state")

    assert result["status"] == "stale"
    assert result["freshness_source"] == "updated_at"
    assert result["summary"]["token"] == runtime.REDACTED
    assert "state-stale" in {issue["code"] for issue in result["issues"]}


def test_destructive_command_is_refused_as_json(capsys) -> None:
    exit_code = runtime.main(["stop", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 64
    assert payload["read_only"] is True
    assert payload["error"] == "destructive-command-disabled"
    assert payload["operation"] == "stop"
    assert "command" not in payload


def test_list_json_is_machine_readable_and_contains_no_runtime_actions(capsys) -> None:
    exit_code = runtime.main(["list", "--json", "web"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["operation"] == "list"
    assert payload["read_only"] is True
    assert {service["id"] for service in payload["services"]} == {"web", "vllm"}
    assert payload["control_policy"] == {
        "mode": "read_only",
        "read_only": True,
        "allowed_operations": ["catalog", "list", "status", "doctor"],
    }
    assert "command" not in json.dumps(payload)
