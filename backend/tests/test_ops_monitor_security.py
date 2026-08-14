from __future__ import annotations

import json

from api.core.redaction import REDACTED, sanitize_diagnostic
from api.routes import ops_monitor


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_pid_diagnostics_never_return_command_line(monkeypatch) -> None:
    seen_args: list[str] = []

    def fake_run(args: list[str], **_kwargs):
        seen_args.extend(args)
        return 0, "123 1 4.2 0.1 01:02:03 S python", ""

    monkeypatch.setattr(ops_monitor, "_run_cmd", fake_run)

    result = ops_monitor._ps_info(123)

    assert result["name"] == "python"
    assert not {"argv", "cmd", "cmdline", "command"}.intersection(result)
    assert not any("cmd=" in value or "args=" in value for value in seen_args)


def test_process_matching_uses_args_only_internally(monkeypatch) -> None:
    secret = "postgres-secret-value"
    output = (
        "321 1 12.0 1.0 00:10:00 Sl python "
        f"python stream_load_news_to_postgres.py --password {secret}"
    )
    monkeypatch.setattr(
        ops_monitor,
        "_run_cmd",
        lambda *_args, **_kwargs: (0, output, ""),
    )

    result = ops_monitor._processes_matching(["stream_load_news_to_postgres.py"])
    payload = _serialized(result)

    assert result == [
        {
            "pid": 321,
            "ppid": 1,
            "cpu_pct": 12.0,
            "mem_pct": 1.0,
            "etime": "00:10:00",
            "stat": "Sl",
            "name": "python",
            "label": "stream_load_news_to_postgres.py",
            "evidence_quality": "heuristic",
            "evidence_source": "process-name-match",
            "authoritative_for_management": False,
        }
    ]
    assert secret not in payload
    assert not any(key in payload for key in ('"argv"', '"cmd"', '"cmdline"', '"command"'))


def test_diagnostic_boundary_removes_commands_and_redacts_common_secret_forms() -> None:
    secrets = {
        "driver": "driver-password",
        "mongo": "mongo-password",
        "pg": "pg-password",
        "aws": "aws-secret-value",
        "bearer": "bearer-value",
    }
    postgres_scheme = "postgresql+psycopg" + "://"
    mongodb_scheme = "mongodb" + "://"
    raw = {
        "cmd": f"worker --password {secrets['driver']}",
        "nested": {
            "argv": ["worker", "--token", "token-value"],
            "dsn": f"{postgres_scheme}user:{secrets['driver']}@db/app",
            "mongo": f"{mongodb_scheme}user:{secrets['mongo']}@db/app",
            "PGPASSWORD": secrets["pg"],
            "AWS_SECRET_ACCESS_KEY": secrets["aws"],
            "header": f"Bearer {secrets['bearer']}",
        },
    }

    safe = sanitize_diagnostic(raw)
    payload = _serialized(safe)

    assert "cmd" not in safe
    assert "argv" not in safe["nested"]
    assert safe["nested"]["PGPASSWORD"] == REDACTED
    assert safe["nested"]["AWS_SECRET_ACCESS_KEY"] == REDACTED
    assert all(secret not in payload for secret in secrets.values())


def test_pipeline_monitor_sanitizes_snapshot_before_caching(monkeypatch) -> None:
    secret = "snapshot-password"
    monkeypatch.setattr(
        ops_monitor,
        "_snapshot",
        lambda: {
            "ok": True,
            "system": {
                "processes": [
                    {"pid": 7, "cmd": f"loader --password {secret}", "name": "python"}
                ]
            },
        },
    )
    ops_monitor._SNAPSHOT_CACHE.update({"ts": 0.0, "data": None})

    response = ops_monitor.pipeline_monitor(fresh=True, _user={"user_id": 1})
    payload = _serialized(response)

    assert secret not in payload
    assert '"cmd"' not in payload
    assert ops_monitor._SNAPSHOT_CACHE["data"] == response


def test_snapshot_builders_do_not_record_history_as_a_get_side_effect(monkeypatch) -> None:
    def fail_if_appended(_sample):
        raise AssertionError("GET snapshot construction must not append monitoring history")

    monkeypatch.setattr(ops_monitor, "_append_history_sample", fail_if_appended)
    monkeypatch.setattr(ops_monitor, "_history_payload", lambda _samples=None: {"samples": []})
    monkeypatch.setattr(ops_monitor, "_db_snapshot", lambda: {})
    monkeypatch.setattr(ops_monitor, "_system_snapshot", lambda: {})
    monkeypatch.setattr(ops_monitor, "_online_summary", lambda: {"active": 0})
    monkeypatch.setattr(ops_monitor, "_runtime_catalog_snapshot", lambda: {})
    monkeypatch.setattr(ops_monitor, "_build_pipelines", lambda _db: [])
    monkeypatch.setattr(
        ops_monitor,
        "attach_catalog_management",
        lambda pipelines, _catalog: pipelines,
    )

    assert ops_monitor._snapshot()["series"] == {"samples": []}

    monkeypatch.setattr(ops_monitor, "_read_json", lambda _path: {})
    monkeypatch.setattr(
        ops_monitor,
        "_daily_pipeline_context",
        lambda: (None, {}, None, None, None),
    )
    monkeypatch.setattr(ops_monitor, "_fast_system_snapshot", lambda: {"gpus": []})
    monkeypatch.setattr(ops_monitor, "_cached_overview", lambda: {})

    assert ops_monitor._fast_snapshot()["series"] == {"samples": []}


def test_monitor_unknown_counts_remain_unavailable_instead_of_becoming_zero() -> None:
    assert ops_monitor._to_float(True) is None
    assert ops_monitor._pct(False) is None
    assert ops_monitor._bounded_number(float("nan")) is None
    update = ops_monitor._fast_progress_update("daily_ingest", {})
    assert update["successes"] is None
    assert update["failures"] is None
    assert update["remaining"] is None
    assert update["active_tasks"] is None

    overview = ops_monitor._overview(
        {},
        [],
        {},
        {"measurement_state": "unavailable", "active": None},
    )
    assert overview["news_total"] is None
    assert overview["good_last_24h"] is None
    assert overview["online_active"] is None

    history = ops_monitor._history_sample(
        {"overview": {}, "pipeline_updates": []}
    )
    assert history["news_total"] is None
    assert history["online_active"] is None
    assert history["wave_remaining"] is None
    assert history["daily_remaining"] is None


def test_system_probe_failures_do_not_become_healthy_zeroes(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise OSError("probe unavailable")

    monkeypatch.setattr(ops_monitor.os, "cpu_count", lambda: None)
    monkeypatch.setattr(ops_monitor.os, "getloadavg", unavailable)
    monkeypatch.setattr(ops_monitor.Path, "read_text", unavailable)
    monkeypatch.setattr(ops_monitor, "_gpu_snapshot", lambda: [])

    system = ops_monitor._fast_system_snapshot()

    assert system["cpu"]["count"] is None
    assert system["cpu"]["load1"] is None
    assert system["cpu"]["pressure_pct"] is None
    assert system["memory"]["total_bytes"] is None
    assert system["memory"]["used_bytes"] is None
    assert system["memory"]["used_pct"] is None
