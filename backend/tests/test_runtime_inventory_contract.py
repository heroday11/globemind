from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "ops" / "runtime" / "services.json"
CLOUDFLARED_SCRIPT = PROJECT_ROOT / "deploy" / "start_cloudflared.sh"
WEB_SCRIPT = PROJECT_ROOT / "deploy" / "start_web_prod.sh"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _services() -> dict[str, dict]:
    return {item["id"]: item for item in _manifest()["services"]}


def test_manifest_v2_has_ownership_intervals_and_dependency_closure() -> None:
    manifest = _manifest()
    assert manifest["schema_version"] == 2
    assert manifest["inventory_version"] == "1.0.0"
    services = _services()
    assert services

    limits = {"critical": 60, "high": 300, "medium": 900}
    graph: dict[str, set[str]] = {}
    for service_id, service in services.items():
        assert service["owner"]
        assert service["criticality"] in limits
        interval = service["check_interval_seconds"]
        assert isinstance(interval, int) and 0 < interval <= limits[service["criticality"]]
        assert service["controller"]["adoption"] == "observe-only"
        assert service["lifecycle_authorization"] == {
            "state": "not-authorized",
            "authorized_operations": [],
            "change_request_required": True,
            "maintenance_window_required": True,
            "required_approvals": ["service-owner", "platform-operations"],
        }
        assert service["checkpoint"]["takeover_ready"] is False
        assert service["health_policy"]["signals"][0] == {
            "source": "pid",
            "required": True,
        }
        assert service["runbook"]["section"] == service_id
        dependencies = {item["service"] for item in service["dependencies"]}
        assert dependencies <= services.keys()
        assert service_id not in dependencies
        graph[service_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        assert service_id not in visiting, f"dependency cycle at {service_id}"
        if service_id in visited:
            return
        visiting.add(service_id)
        for dependency in graph[service_id]:
            visit(dependency)
        visiting.remove(service_id)
        visited.add(service_id)

    for service_id in graph:
        visit(service_id)


def test_pipeline_checkpoint_and_replay_assurance_is_evidence_scoped() -> None:
    services = _services()

    assert services["wave1_loader"]["checkpoint"] == {
        "mode": "durable",
        "state_refs": [0, 1],
        "takeover_ready": False,
    }
    assert services["wave1_loader"]["replay"]["assurance"] == "verified"
    assert services["wave1_extractor"]["checkpoint"]["mode"] == "progress-only"
    assert services["wave1_extractor"]["replay"]["assurance"] == "verified"

    not_evidenced = {
        "daily_ingest",
        "ground_images",
        "ground_refresh",
        "l1_extract",
        "l1_prep",
        "quality_labels",
    }
    for service_id in not_evidenced:
        service = services[service_id]
        assert service["replay"] == {
            "mode": "unknown",
            "assurance": "not-evidenced",
            "evidence": [],
        }
    assert services["daily_ingest"]["checkpoint"]["mode"] == "progress-only"
    for service_id in not_evidenced - {"daily_ingest"}:
        assert services[service_id]["checkpoint"]["mode"] == "not-evidenced"


def test_secret_references_cover_policy_files_without_copying_secret_material() -> None:
    for service in _services().values():
        refs = service["secret_refs"]
        assert {item["file_index"] for item in refs} == set(
            range(len(service["secret_policy"]["files"]))
        )
        assert len({item["name"] for item in refs}) == len(refs)
        assert all(set(item) == {"name", "file_index"} for item in refs)


def test_manifest_uses_authoritative_wave1_state_and_success_only_completion() -> None:
    services = _services()
    extractor = services["wave1_extractor"]
    extractor_log = extractor["log"][0]
    assert "max_age_seconds" not in extractor_log

    supervisor, progress = extractor["state"]
    assert "max_age_seconds" not in supervisor
    assert supervisor["complete_values"] == ["completed"]
    assert supervisor["authoritative"] is False
    assert progress["authoritative"] is True
    assert progress["timestamp_field"] == "updated_at"
    assert progress["max_age_seconds"] == 600

    loader_state = services["wave1_loader"]["state"]
    assert len(loader_state) == 2
    assert all(item["authoritative"] is True for item in loader_state)
    for service in services.values():
        for state in service["state"]:
            assert not ({"stopped", "failed"} & set(state.get("complete_values", [])))


def test_wave1_loader_contract_uses_managed_runtime_evidence() -> None:
    loader = _services()["wave1_loader"]

    assert loader["pid"] == {
        "kind": "single",
        "path": "${DATA_ROOT}/runtime/globemind/wave1_loader/wave1_loader.pid",
        "meta_path": "${DATA_ROOT}/runtime/globemind/wave1_loader/wave1_loader.pid.meta",
        "meta": {
            "format": "json",
            "schema_version": 2,
            "pid_path": "identity.pid",
            "starttime_ticks_path": "identity.start_ticks",
        },
        "cmdline_contains": [
            "scripts/stream_load_news_to_postgres.py",
            "wave1_articles_merged.jsonl",
        ],
        "expected": "running",
    }
    assert loader["health"] == [
        {
            "type": "unix-control-status",
            "path": "${DATA_ROOT}/runtime/globemind/wave1_loader/wave1_loader.pid.sock",
            "expect_status": ["running"],
            "timeout_seconds": 2,
            "required": True,
        }
    ]
    heartbeat = loader["state"][0]
    assert heartbeat["path"].endswith("/wave1_loader.pid.heartbeat")
    assert heartbeat["timestamp_field"] == "heartbeat_at"
    assert heartbeat["max_age_seconds"] == 180
    assert heartbeat["stale_severity"] == "error"
    assert loader["log"] == [
        {"path": "${DATA_ROOT}/runtime/globemind/logs/wave1_loader.log", "required": True}
    ]


def test_managed_loop_inventory_declares_only_evidenced_identity_contracts() -> None:
    services = _services()
    daily_pid = services["daily_ingest"]["pid"]
    quality_pid = services["quality_labels"]["pid"]
    assert "meta_path" not in daily_pid
    assert "meta" not in daily_pid
    assert quality_pid["meta_path"] == ("${PROJECT_ROOT}/logs/news_quality_labels_loop.pid.meta")
    assert quality_pid["meta"] == {"pid_index": 0, "starttime_ticks_index": 1}
    assert daily_pid["expected"] == quality_pid["expected"] == "running"

    expected_interface = "start|stop|restart|status|logs|follow"
    assert services["daily_ingest"]["controller"]["interface"] == expected_interface
    assert services["quality_labels"]["controller"]["interface"] == expected_interface


def test_tunnel_contract_matches_active_v092_runtime_paths() -> None:
    tunnel = _services()["tunnel"]
    assert tunnel["pid"]["path"].endswith("/cloudflared-v092.pid")
    assert tunnel["pid"]["meta_path"].endswith("/cloudflared-v092.pid.meta")
    assert tunnel["pid"]["meta"] == {"pid_index": 0, "starttime_ticks_index": 1}
    assert tunnel["port"] == [
        {"id": "metrics", "host": "127.0.0.1", "number": 20242, "required": True}
    ]
    assert tunnel["log"] == [{"path": "${DATA_ROOT}/cloudflared/tunnel-v092.log", "required": True}]

    script = CLOUDFLARED_SCRIPT.read_text(encoding="utf-8")
    assert "CLOUDFLARED_METRICS:-127.0.0.1:20242" in script
    assert "CLOUDFLARED_PID_FILE:-/root/data/cloudflared/cloudflared-v092.pid" in script
    assert "CLOUDFLARED_LOG:-/root/data/cloudflared/tunnel-v092.log" in script
    assert '--metrics "$CLOUDFLARED_METRICS"' in script
    assert "flock -w 10 9" in script
    assert (
        'write_identity_file "$CLOUDFLARED_PID_META_FILE" "$$ $start_ticks cloudflared"' in script
    )
    assert script.index("write_identity_file", script.index('start_ticks="')) < script.index(
        'exec "$CLOUDFLARED_BIN"'
    )


def test_ground_image_output_is_non_authoritative_warning() -> None:
    ground_images = _services()["ground_images"]
    assert "max_age_seconds" not in ground_images["log"][0]
    assert ground_images["output"] == [
        {
            "path": "${PROJECT_ROOT}/logs/ground_news_image_backfill_loop.log",
            "required": False,
            "max_age_seconds": 5400,
            "stale_severity": "warning",
            "authoritative": False,
        }
    ]


def test_web_log_normalization_runs_only_after_instance_and_port_guards() -> None:
    script = WEB_SCRIPT.read_text(encoding="utf-8")
    function_start = script.index("start_service()")
    function_end = script.index("stop_service()", function_start)
    start_service = script[function_start:function_end]
    assert start_service.index("process_is_instance") < start_service.index(
        "normalize_canonical_log"
    )
    assert start_service.index('ss -ltnH "sport = :${PORT}"') < start_service.index(
        "normalize_canonical_log"
    )
    assert start_service.index("normalize_canonical_log") < start_service.index(': > "$LOG_FILE"')
    assert 'mv -fT "$tmp" "$LOG_FILE"' in script
    assert "The historical target is retained" in script


def test_web_log_normalization_replaces_only_the_symlink(tmp_path: Path) -> None:
    script = WEB_SCRIPT.read_text(encoding="utf-8")
    start = script.index("normalize_canonical_log()")
    end = script.index("\n}\n\nprocess_start_ticks()", start) + 3
    function = script[start:end]

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    historical = log_dir / "historical.log"
    historical.write_text("historical evidence\n", encoding="utf-8")
    canonical = log_dir / "globemind_web_prod.log"
    canonical.symlink_to(historical)

    command = "\n".join(
        (
            "set -euo pipefail",
            f"LOG_DIR={log_dir!s}",
            f"LOG_FILE={canonical!s}",
            "INSTANCE=production",
            function,
            "normalize_canonical_log",
        )
    )
    subprocess.run(["bash", "-c", command], check=True, cwd=PROJECT_ROOT)

    assert canonical.is_file()
    assert not canonical.is_symlink()
    assert canonical.read_text(encoding="utf-8") == ""
    assert canonical.stat().st_mode & 0o777 == 0o640
    assert historical.read_text(encoding="utf-8") == "historical evidence\n"


def test_runtime_start_scripts_are_valid_bash() -> None:
    subprocess.run(
        ["bash", "-n", str(CLOUDFLARED_SCRIPT), str(WEB_SCRIPT)],
        check=True,
        cwd=PROJECT_ROOT,
    )
