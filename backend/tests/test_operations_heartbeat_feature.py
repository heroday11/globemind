from __future__ import annotations

import json
import os

from api.features.operations import (
    HeartbeatPayload,
    HeartbeatPolicy,
    HeartbeatRegistry,
    MonitoringHistoryPolicy,
    MonitoringHistoryStore,
    normalize_heartbeat_path,
)


def test_registry_prunes_caps_and_publishes_atomically(tmp_path) -> None:
    now = [1000.0]
    registry = HeartbeatRegistry(
        data_path=tmp_path / "heartbeats.json",
        lock_path=tmp_path / "heartbeats.lock",
        policy=HeartbeatPolicy(ttl_seconds=10, max_clients=2),
        clock=lambda: now[0],
    )

    registry.update(HeartbeatPayload(client_id="client-old", path="/old"))
    now[0] = 1005.0
    registry.update(HeartbeatPayload(client_id="client-new", path="/new"))
    now[0] = 1006.0
    summary = registry.update(HeartbeatPayload(client_id="client-last", path="/new"))

    stored = json.loads((tmp_path / "heartbeats.json").read_text(encoding="utf-8"))
    assert set(stored) == {"client-new", "client-last"}
    assert summary["active"] == 2
    assert summary["paths"] == [{"path": "/new", "count": 2}]
    assert (tmp_path / "heartbeats.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "heartbeats.lock").stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".heartbeats.json.*.tmp")) == []

    now[0] = 1020.0
    assert registry.summary() == {
        "measurement_state": "available",
        "active": 0,
        "ttl_sec": 10,
        "paths": [],
        "clients": [],
    }


def test_registry_does_not_persist_user_agent_fingerprints(tmp_path) -> None:
    registry = HeartbeatRegistry(
        data_path=tmp_path / "heartbeats.json",
        lock_path=tmp_path / "heartbeats.lock",
        policy=HeartbeatPolicy(),
        clock=lambda: 1000.0,
    )

    summary = registry.update(
        HeartbeatPayload(client_id="client-safe", path="/path", visibility="visible"),
        user_agent="secret-agent-value",
    )

    stored = json.loads((tmp_path / "heartbeats.json").read_text(encoding="utf-8"))
    assert set(stored) == {"client-safe"}
    assert "user_agent" not in stored["client-safe"]
    assert "secret-agent-value" not in json.dumps(summary)
    assert summary["clients"] == [
        {
            "path": "/path",
            "visibility": "visible",
            "updated_at": "1970-01-01T00:16:40+00:00",
        }
    ]


def test_heartbeat_contract_rejects_colliding_ids_unknown_visibility_and_extras() -> None:
    invalid_payloads = (
        {"client_id": "unsafe/@client"},
        {"client_id": "client-safe", "visibility": "secret"},
        {"client_id": "client-safe", "secret": "must-not-be-accepted"},
    )
    for payload in invalid_payloads:
        try:
            HeartbeatPayload.model_validate(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid heartbeat payload was accepted: {payload}")


def test_registry_never_persists_query_parameters_or_fragments(tmp_path) -> None:
    registry = HeartbeatRegistry(
        data_path=tmp_path / "heartbeats.json",
        lock_path=tmp_path / "heartbeats.lock",
        policy=HeartbeatPolicy(),
        clock=lambda: 1000.0,
    )

    summary = registry.update(
        HeartbeatPayload(
            client_id="client-sensitive",
            path="/reset-password?token=reset-secret#fragment-secret",
        )
    )
    stored = (tmp_path / "heartbeats.json").read_text(encoding="utf-8")

    assert normalize_heartbeat_path("https://globemind.top/path?token=x") == "/path"
    assert normalize_heartbeat_path("not-a-route?token=x") == "/"
    assert "reset-secret" not in stored
    assert "fragment-secret" not in stored
    assert summary["paths"] == [{"path": "/reset-password", "count": 1}]


def test_summary_is_zero_write_and_clock_rollback_does_not_keep_clients_online(
    tmp_path,
) -> None:
    root = tmp_path / "runtime"
    now = [1000.0]
    registry = HeartbeatRegistry(
        data_path=root / "heartbeats.json",
        lock_path=root / "heartbeats.lock",
        policy=HeartbeatPolicy(ttl_seconds=10),
        clock=lambda: now[0],
    )

    assert registry.summary()["active"] == 0
    assert not root.exists(), "a heartbeat summary read must not create a lock or directory"

    registry.update(HeartbeatPayload(client_id="client-clock", path="/status"))
    now[0] = 999.0

    summary = registry.summary()
    assert summary["measurement_state"] == "available"
    assert summary["active"] == 0


def test_summary_fails_closed_on_duplicate_json_and_hardlinked_data(tmp_path) -> None:
    def registry_at(root):
        return HeartbeatRegistry(
            data_path=root / "heartbeats.json",
            lock_path=root / "heartbeats.lock",
            policy=HeartbeatPolicy(),
            clock=lambda: 1000.0,
        )

    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir()
    (duplicate_root / "heartbeats.lock").write_text("", encoding="utf-8")
    (duplicate_root / "heartbeats.json").write_text(
        '{"client-safe":{"last_seen":1000,"path":"/safe"},'
        '"client-safe":{"last_seen":1000,"path":"/forged"}}',
        encoding="utf-8",
    )
    os.chmod(duplicate_root / "heartbeats.lock", 0o600)
    os.chmod(duplicate_root / "heartbeats.json", 0o600)

    duplicate = registry_at(duplicate_root).summary()
    assert duplicate["measurement_state"] == "unavailable"
    assert duplicate["active"] is None
    assert duplicate["paths"] == []
    assert duplicate["clients"] == []

    hardlink_root = tmp_path / "hardlink"
    hardlink_root.mkdir()
    source = tmp_path / "shared-heartbeats.json"
    source.write_text("{}", encoding="utf-8")
    os.chmod(source, 0o600)
    os.link(source, hardlink_root / "heartbeats.json")
    (hardlink_root / "heartbeats.lock").write_text("", encoding="utf-8")
    os.chmod(hardlink_root / "heartbeats.lock", 0o600)

    hardlinked = registry_at(hardlink_root).summary()
    assert hardlinked["measurement_state"] == "unavailable"
    assert hardlinked["active"] is None

    confused_root = tmp_path / "type-confused"
    confused_root.mkdir()
    (confused_root / "heartbeats.lock").write_text("", encoding="utf-8")
    (confused_root / "heartbeats.json").write_text(
        json.dumps(
            {
                "client-safe": {
                    "client_id": "client-safe",
                    "path": "/safe",
                    "visibility": "visible",
                    "last_seen": "1000.0",
                    "updated_at": "1970-01-01T00:16:40+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    os.chmod(confused_root / "heartbeats.lock", 0o600)
    os.chmod(confused_root / "heartbeats.json", 0o600)

    type_confused = registry_at(confused_root).summary()
    assert type_confused["measurement_state"] == "unavailable"
    assert type_confused["active"] is None


def test_policy_rejects_non_positive_limits() -> None:
    for kwargs in (
        {"ttl_seconds": 0},
        {"ttl_seconds": True},
        {"ttl_seconds": 1.5},
        {"max_clients": 0},
        {"max_clients": "10"},
    ):
        try:
            HeartbeatPolicy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid policy to fail: {kwargs}")

    for kwargs in (
        {"max_points": True},
        {"minimum_interval_seconds": False},
        {"minimum_interval_seconds": float("nan")},
    ):
        try:
            MonitoringHistoryPolicy(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid history policy to fail: {kwargs}")


def test_history_store_applies_interval_cap_and_atomic_permissions(tmp_path) -> None:
    store = MonitoringHistoryStore(
        data_path=tmp_path / "history.json",
        lock_path=tmp_path / "history.lock",
        policy=MonitoringHistoryPolicy(max_points=2, minimum_interval_seconds=0.5),
    )

    assert store.append({"ts": 1.0, "value": 1}) == [{"ts": 1.0, "value": 1}]
    assert store.append({"ts": 1.25, "value": 2}) == [{"ts": 1.0, "value": 1}]
    store.append({"ts": 1.5, "value": 3})
    history = store.append({"ts": 2.0, "value": 4})

    assert history == [{"ts": 1.5, "value": 3}, {"ts": 2.0, "value": 4}]
    assert store.payload() == {
        "measurement_state": "observed",
        "collection_state": "not_configured",
        "sample_count": 2,
        "max_points": 2,
        "min_interval_sec": 0.5,
        "samples": history,
    }
    assert (tmp_path / "history.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "history.lock").stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".history.json.*.tmp")) == []


def test_history_read_is_zero_write_when_storage_is_not_initialized(tmp_path) -> None:
    root = tmp_path / "history-runtime"
    store = MonitoringHistoryStore(
        data_path=root / "history.json",
        lock_path=root / "history.lock",
        policy=MonitoringHistoryPolicy(),
    )

    assert store.read() == []
    assert store.payload()["measurement_state"] == "not_observed"
    assert store.payload()["collection_state"] == "not_configured"
    assert not root.exists(), "a history GET/read must not create a lock or directory"


def test_history_store_fails_safe_on_corrupt_data_and_rejects_missing_timestamp(
    tmp_path,
) -> None:
    data_path = tmp_path / "history.json"
    data_path.write_text("not-json", encoding="utf-8")
    store = MonitoringHistoryStore(
        data_path=data_path,
        lock_path=tmp_path / "history.lock",
        policy=MonitoringHistoryPolicy(),
    )

    assert store.read() == []
    try:
        store.append({"value": 1})
    except ValueError as error:
        assert "numeric ts" in str(error)
    else:
        raise AssertionError("missing history timestamp must fail")


def test_history_store_rejects_type_confusion_and_clock_rollback(tmp_path) -> None:
    data_path = tmp_path / "history.json"
    lock_path = tmp_path / "history.lock"
    lock_path.write_text("", encoding="utf-8")
    data_path.write_text('[{"ts":"1.0","value":99}]', encoding="utf-8")
    os.chmod(lock_path, 0o600)
    os.chmod(data_path, 0o600)
    store = MonitoringHistoryStore(
        data_path=data_path,
        lock_path=lock_path,
        policy=MonitoringHistoryPolicy(),
    )

    assert store.payload()["measurement_state"] == "unavailable"
    assert store.payload()["samples"] == []

    data_path.write_text('[{"ts":2.0},{"ts":1.0}]', encoding="utf-8")
    os.chmod(data_path, 0o600)
    assert store.payload()["measurement_state"] == "unavailable"

    data_path.write_text('[{"ts":2.0,"value":2}]', encoding="utf-8")
    os.chmod(data_path, 0o600)
    try:
        store.append({"ts": 1.0, "value": 1})
    except ValueError as error:
        assert "monotonic" in str(error)
    else:
        raise AssertionError("clock rollback must not be accepted as an append")
