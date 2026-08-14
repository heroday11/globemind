from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.features import FeatureHealthCheck, build_feature_health_report
from api.features.operations import maintenance_history
from api.routes import dashboard


NOW = datetime(2026, 8, 9, 19, 30, tzinfo=timezone.utc)


def _event(
    event_id: str = "maintenance-20260809",
    *,
    started_at: str = "2026-08-09T17:00:00Z",
    ended_at: str = "2026-08-09T17:30:00Z",
) -> dict[str, object]:
    return {
        "id": event_id,
        "type": "maintenance",
        "status": "completed",
        "title": "公开检索索引维护",
        "summary": "维护期间当前资料查询不可用，完成后恢复。",
        "started_at": started_at,
        "ended_at": ended_at,
        "affected_features": ["search"],
    }


def _ledger(events: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "globemind.maintenance-events.v1",
        "generated_at": "2026-08-09T19:00:00Z",
        "events": [_event()] if events is None else events,
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_maintenance_history_read_is_zero_write_bounded_and_honest(tmp_path: Path) -> None:
    path = tmp_path / "maintenance-events.json"
    _write(path, _ledger())
    before = path.stat()
    directory_before = sorted(item.name for item in tmp_path.iterdir())

    result = maintenance_history.load_public_maintenance_history(
        str(path),
        evaluated_at=NOW,
    )

    after = path.stat()
    assert result["status"] == "available"
    assert result["freshness"] == "current"
    assert result["events"][0] == {
        **_event(),
        "started_at": "2026-08-09T17:00:00+00:00",
        "ended_at": "2026-08-09T17:30:00+00:00",
    }
    assert result["retention"] == {
        "status": "not_approved",
        "published_event_limit": maintenance_history.MAX_PUBLIC_EVENTS,
    }
    assert result["subscription"] == {"status": "not_configured"}
    assert result["owner"] == {"status": "not_configured"}
    assert result["bounds"]["max_source_bytes"] == maintenance_history.MAX_SOURCE_BYTES
    assert directory_before == sorted(item.name for item in tmp_path.iterdir())
    assert (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


@pytest.mark.parametrize(
    ("configured_path", "expected_status"),
    [("", "not_configured"), ("  ", "unavailable"), ("relative.json", "unavailable")],
)
def test_maintenance_history_distinguishes_not_configured_from_unavailable(
    configured_path: str,
    expected_status: str,
) -> None:
    result = maintenance_history.load_public_maintenance_history(
        configured_path,
        evaluated_at=NOW,
    )

    assert result["status"] == expected_status
    assert result["events"] == []
    assert result["retention"]["status"] == "not_approved"
    assert result["subscription"]["status"] == "not_configured"
    assert result["owner"]["status"] == "not_configured"
    assert "不能据此推断" in result["reason"]


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":"globemind.maintenance-events.v1","generated_at":"2026-08-09T19:00:00Z","events":[],"events":[]}',
        '{"schema_version":"globemind.maintenance-events.v1","generated_at":"2026-08-09T19:00:00Z","events":[],"n":NaN}',
        "[1,2,3]",
    ],
)
def test_maintenance_history_rejects_non_strict_json(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "maintenance-events.json"
    path.write_text(raw, encoding="utf-8")

    result = maintenance_history.load_public_maintenance_history(str(path), evaluated_at=NOW)

    assert result["status"] == "unavailable"
    assert result["events"] == []


def test_maintenance_history_rejects_oversize_source_and_event_count(tmp_path: Path) -> None:
    path = tmp_path / "maintenance-events.json"
    path.write_bytes(b" " * (maintenance_history.MAX_SOURCE_BYTES + 1))
    assert maintenance_history.load_public_maintenance_history(
        str(path), evaluated_at=NOW
    )["status"] == "unavailable"

    _write(
        path,
        _ledger(
            [
                _event(f"maintenance-{index:03d}")
                for index in range(maintenance_history.MAX_PUBLIC_EVENTS + 1)
            ]
        ),
    )
    assert maintenance_history.load_public_maintenance_history(
        str(path), evaluated_at=NOW
    )["status"] == "unavailable"


def test_maintenance_history_rejects_symbolic_and_hard_links(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    _write(source, _ledger())
    symbolic = tmp_path / "symbolic.json"
    symbolic.symlink_to(source)
    hard = tmp_path / "hard.json"
    os.link(source, hard)

    assert maintenance_history.load_public_maintenance_history(
        str(symbolic), evaluated_at=NOW
    )["status"] == "unavailable"
    assert maintenance_history.load_public_maintenance_history(
        str(hard), evaluated_at=NOW
    )["status"] == "unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "globemind.maintenance-events.v1",
            "generated_at": "2026-08-09T19:31:00Z",
            "events": [],
        },
        _ledger([_event(started_at="2026-08-09T19:30:01Z", ended_at="2026-08-09T19:31:00Z")]),
        _ledger(
            [
                _event("maintenance-new", started_at="2026-08-09T16:00:00Z", ended_at="2026-08-09T16:30:00Z"),
                _event("maintenance-old", started_at="2026-08-09T18:00:00Z", ended_at="2026-08-09T18:30:00Z"),
            ]
        ),
    ],
)
def test_maintenance_history_fails_closed_on_clock_rollback_future_or_non_monotonic_events(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "maintenance-events.json"
    _write(path, payload)

    result = maintenance_history.load_public_maintenance_history(str(path), evaluated_at=NOW)

    assert result["status"] == "unavailable"
    assert result["events"] == []


def test_maintenance_history_rejects_ambiguous_event_shapes(tmp_path: Path) -> None:
    path = tmp_path / "maintenance-events.json"
    invalid = _event()
    invalid["unexpected"] = "self-claimed approval"
    _write(path, _ledger([invalid]))

    result = maintenance_history.load_public_maintenance_history(str(path), evaluated_at=NOW)

    assert result["status"] == "unavailable"
    assert result["events"] == []


def test_maintenance_history_rejects_group_writable_or_excessively_deep_sources(
    tmp_path: Path,
) -> None:
    path = tmp_path / "maintenance-events.json"
    _write(path, _ledger())
    path.chmod(0o664)
    assert maintenance_history.load_public_maintenance_history(
        str(path), evaluated_at=NOW
    )["status"] == "unavailable"

    path.chmod(0o644)
    nested = "null"
    for _ in range(2_000):
        nested = f'{{"nested":{nested}}}'
    path.write_text(nested, encoding="utf-8")
    assert maintenance_history.load_public_maintenance_history(
        str(path), evaluated_at=NOW
    )["status"] == "unavailable"


def test_public_status_get_projects_ledger_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "maintenance-events.json"
    _write(path, _ledger())
    before = path.stat()

    class _Summary:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {}

    monkeypatch.setenv("MAINTENANCE_EVENT_LEDGER_PATH", str(path))
    monkeypatch.setattr(
        dashboard,
        "_build_public_feature_health_report",
        lambda _db: build_feature_health_report(
            (
                FeatureHealthCheck(
                    feature_id="search",
                    status="down",
                    latency_ms=0,
                        dependencies=["test"],
                    metrics={"freshness_status": "offline"},
                ),
            )
        ),
    )
    monkeypatch.setattr(dashboard._public_service_level, "summary", lambda: _Summary())

    response = dashboard.public_status(db=object())
    payload = json.loads(response.body)
    after = path.stat()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert payload["incident_history"]["status"] == "available"
    assert payload["incident_history"]["events"][0]["id"] == "maintenance-20260809"
    assert (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
