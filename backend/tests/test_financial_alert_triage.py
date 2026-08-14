from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.financial import (
    AlertHistoryEventNotFound,
    AlertTriageConflict,
    AlertTriageMutation,
    AlertTriageUnavailable,
    FinancialAlertTriageLedger,
    FinancialAlertTriageService,
    JsonListStore,
)
from api.features.financial import triage as triage_module
from api.routes import financial
from api.services.auth import get_current_admin_user, get_current_user_required


ALERT_ID = "fin-alert-risk-20260809080000"


def _concurrent_ack_worker(
    ledger_root: str,
    history_path: str,
    start: object,
    result_queue: object,
) -> None:
    service = FinancialAlertTriageService(
        ledger_root=Path(ledger_root),
        alert_history_path=Path(history_path),
        clock=lambda: datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
        nonce_factory=lambda: f"{os.getpid():016x}",
    )
    try:
        if not start.wait(timeout=30):  # type: ignore[attr-defined]
            raise TimeoutError("start signal not received")
        service.mutate(
            ALERT_ID,
            AlertTriageMutation(
                action="acknowledge",
                reason="Concurrent acknowledgement attempt.",
                expected_previous_event_id=None,
                expected_previous_event_sha256=None,
            ),
            actor_user_id=17,
        )
    except AlertTriageConflict as exc:
        result_queue.put(exc.code)  # type: ignore[attr-defined]
    except BaseException as exc:
        result_queue.put(f"unexpected:{type(exc).__name__}:{exc}")  # type: ignore[attr-defined]
    else:
        result_queue.put("ok")  # type: ignore[attr-defined]


def _history_row(alert_id: str = ALERT_ID) -> dict[str, object]:
    return {
        "id": alert_id,
        "rule_id": "risk",
        "metric": "Risk",
        "current": 12.0,
        "threshold": 10.0,
        "severity": "high",
        "triggered_at": "2026-08-09T08:00:00Z",
        "message": "Threshold exceeded.",
        "eventTags": ["Risk", "system"],
    }


def _service(tmp_path: Path) -> FinancialAlertTriageService:
    history = tmp_path / "history.json"
    JsonListStore(history).write([_history_row()])
    nonces = iter(f"{index:016x}" for index in range(1, 20))
    times = iter(
        datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
        + timedelta(seconds=index)
        for index in range(20)
    )
    return FinancialAlertTriageService(
        ledger_root=tmp_path / "triage",
        alert_history_path=history,
        clock=lambda: next(times),
        nonce_factory=lambda: next(nonces),
    )


def _mutation(
    action: str,
    *,
    previous: dict[str, object] | None = None,
    reason: str = "Reviewed against the persisted alert evidence.",
    **fields: object,
) -> AlertTriageMutation:
    return AlertTriageMutation(
        action=action,
        reason=reason,
        expected_previous_event_id=(previous or {}).get("last_event_id"),
        expected_previous_event_sha256=(previous or {}).get("last_event_sha256"),
        **fields,
    )


def _append(
    service: FinancialAlertTriageService,
    action: str,
    previous: dict[str, object] | None = None,
    **fields: object,
) -> dict[str, object]:
    return service.mutate(
        ALERT_ID,
        _mutation(action, previous=previous, **fields),
        actor_user_id=17,
    )


def _event_files(service: FinancialAlertTriageService) -> list[Path]:
    return sorted(service.ledger.events_root.glob("*.json"))


def test_constructor_and_gets_are_zero_write_and_report_open_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    root = service.ledger.root

    assert not root.exists()
    assert service.ledger.list_events() == []
    detail = service.detail(ALERT_ID, include_sensitive=False)

    assert detail["status"] == "open"
    assert detail["has_audit"] is False
    assert detail["audit"] == []
    assert detail["operational_limitations"] == {
        "sla": "unavailable",
        "notification_delivery": "not_configured",
        "institutional_incident_system": "not_configured",
    }
    assert not root.exists()


def test_state_machine_escalation_resolution_and_postmortem_are_append_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    acknowledged = _append(service, "acknowledge")
    escalated = _append(
        service,
        "escalate",
        acknowledged,
        escalation_target_role="research_lead",
    )
    resolved = _append(service, "resolve", escalated)
    reviewed = _append(
        service,
        "postmortem",
        resolved,
        postmortem_outcome="process_improvement_identified",
    )

    assert [row["to_status"] for row in reviewed["audit"]] == [
        "acknowledged",
        "escalated",
        "resolved",
        "resolved",
    ]
    assert reviewed["status"] == "resolved"
    assert reviewed["transition_count"] == 3
    assert reviewed["last_transition_at"] == "2026-08-09T08:00:02Z"
    assert reviewed["last_event_id"] == reviewed["audit"][-1]["event_id"]
    assert reviewed["audit"][-1]["occurred_at"] == "2026-08-09T08:00:03Z"
    assert reviewed["reviewed"] is True
    assert len(_event_files(service)) == 4
    first_bytes = _event_files(service)[0].read_bytes()

    with pytest.raises(AlertTriageConflict, match="TRIAGE_POSTMORTEM_ALREADY_RECORDED"):
        _append(
            service,
            "postmortem",
            reviewed,
            postmortem_outcome="no_follow_up_required",
        )
    with pytest.raises(AlertTriageConflict, match="TRIAGE_STATE_TRANSITION_REJECTED"):
        _append(service, "acknowledge", reviewed)

    assert len(_event_files(service)) == 4
    assert _event_files(service)[0].read_bytes() == first_bytes


def test_false_positive_requires_closed_classification_and_no_partial_fields(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    acknowledged = _append(service, "acknowledge")
    completed = _append(
        service,
        "mark_false_positive",
        acknowledged,
        false_positive_classification="threshold_miscalibration",
    )

    assert completed["status"] == "false_positive"
    assert completed["audit"][-1]["false_positive_classification"] == (
        "threshold_miscalibration"
    )
    with pytest.raises(ValidationError):
        _mutation("mark_false_positive", previous=acknowledged)
    with pytest.raises(ValidationError):
        _mutation(
            "resolve",
            previous=acknowledged,
            escalation_target_role="research_lead",
        )
    with pytest.raises(ValidationError):
        _mutation(
            "escalate",
            previous=acknowledged,
            escalation_target_role="named-person",
        )


def test_optimistic_concurrency_rejects_stale_or_partial_previous_pointer(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    acknowledged = _append(service, "acknowledge")
    before = [path.read_bytes() for path in _event_files(service)]

    with pytest.raises(AlertTriageConflict, match="TRIAGE_OPTIMISTIC_CONCURRENCY_CONFLICT"):
        service.mutate(
            ALERT_ID,
            AlertTriageMutation(
                action="resolve",
                reason="Concurrent stale request is rejected.",
                expected_previous_event_id=acknowledged["last_event_id"],
                expected_previous_event_sha256="0" * 64,
            ),
            actor_user_id=17,
        )
    with pytest.raises(ValidationError):
        AlertTriageMutation(
            action="resolve",
            reason="A partial pointer is invalid.",
            expected_previous_event_id=acknowledged["last_event_id"],
            expected_previous_event_sha256=None,
        )

    assert [path.read_bytes() for path in _event_files(service)] == before


def test_multiprocess_first_acknowledgement_has_one_winner(tmp_path: Path) -> None:
    history = tmp_path / "concurrent-history.json"
    JsonListStore(history).write([_history_row()])
    ledger_root = tmp_path / "concurrent-triage"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_ack_worker,
            args=(str(ledger_root), str(history), start, result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            pytest.fail("triage test child did not finish")
        assert process.exitcode == 0

    results = sorted(result_queue.get(timeout=5) for _ in processes)
    assert results == ["TRIAGE_OPTIMISTIC_CONCURRENCY_CONFLICT", "ok"]
    assert len(FinancialAlertTriageLedger(ledger_root).list_events()) == 1


def test_first_operation_requires_real_unchanged_history_event(tmp_path: Path) -> None:
    service = _service(tmp_path)
    missing_root = service.ledger.root

    with pytest.raises(AlertHistoryEventNotFound):
        service.mutate(
            "fin-alert-missing-20260809080000",
            _mutation("acknowledge"),
            actor_user_id=17,
        )
    assert not missing_root.exists()

    acknowledged = _append(service, "acknowledge")
    JsonListStore(service.alert_history_path).write(
        [{**_history_row(), "threshold": 99.0}]
    )
    with pytest.raises(AlertTriageConflict, match="TRIAGE_ALERT_HISTORY_CHANGED"):
        _append(service, "resolve", acknowledged)


def test_redacted_detail_never_exposes_actor_or_reason_but_admin_audit_does(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    secret_reason = "Contains an internal case reference 12345."
    service.mutate(
        ALERT_ID,
        _mutation("acknowledge", reason=secret_reason),
        actor_user_id=42,
    )

    redacted = service.detail(ALERT_ID, include_sensitive=False)
    admin = service.detail(ALERT_ID, include_sensitive=True)
    encoded_redacted = json.dumps(redacted, ensure_ascii=False)

    assert secret_reason not in encoded_redacted
    assert "actor_user_id" not in encoded_redacted
    assert redacted["audit"][0]["reason"]["length"] == len(secret_reason)
    assert len(redacted["audit"][0]["reason"]["sha256"]) == 64
    assert admin["audit"][0]["reason"] == secret_reason
    assert admin["audit"][0]["actor_user_id"] == 42


@pytest.mark.parametrize("damage", ["hash", "duplicate-key", "nan", "hardlink"])
def test_tampered_duplicate_nonfinite_and_hardlinked_entries_fail_closed(
    tmp_path: Path,
    damage: str,
) -> None:
    service = _service(tmp_path)
    _append(service, "acknowledge")
    event_file = _event_files(service)[0]

    if damage == "hash":
        payload = json.loads(event_file.read_text(encoding="utf-8"))
        payload["reason"] = "tampered"
        event_file.write_text(json.dumps(payload), encoding="utf-8")
    elif damage == "duplicate-key":
        event_file.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    elif damage == "nan":
        event_file.write_text('{"sequence":NaN}', encoding="utf-8")
    else:
        os.link(event_file, tmp_path / "second-link.json")

    with pytest.raises(AlertTriageUnavailable):
        service.ledger.list_events()


def test_history_and_root_storage_reject_unsafe_filesystem_or_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "linked"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_ROOT_SYMLINK_REJECTED"):
        FinancialAlertTriageLedger(symlink_root)

    swapped_root = tmp_path / "swapped"
    swapped = FinancialAlertTriageLedger(swapped_root)
    swapped_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_ROOT_SYMLINK_REJECTED"):
        swapped.list_events()

    release_root = tmp_path / "releases"
    monkeypatch.setattr(triage_module, "_FORBIDDEN_RELEASE_ROOT", release_root)
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_ROOT_INSIDE_RELEASE"):
        FinancialAlertTriageLedger(release_root / "current" / "triage")
    assert not release_root.exists()

    release_history = release_root / "current" / "history.json"
    JsonListStore(release_history).write([_history_row()])
    release_history_service = FinancialAlertTriageService(
        ledger_root=tmp_path / "safe-triage",
        alert_history_path=release_history,
    )
    with pytest.raises(
        AlertTriageUnavailable,
        match="ALERT_HISTORY_PATH_INSIDE_RELEASE",
    ):
        release_history_service.detail(ALERT_ID, include_sensitive=False)
    assert not release_history_service.ledger.root.exists()

    history = tmp_path / "duplicate-history.json"
    history.write_text(
        '[{"id":"' + ALERT_ID + '","id":"' + ALERT_ID + '"}]',
        encoding="utf-8",
    )
    service = FinancialAlertTriageService(
        ledger_root=tmp_path / "triage-duplicate",
        alert_history_path=history,
    )
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_DUPLICATE_JSON_KEY"):
        service.detail(ALERT_ID, include_sensitive=False)
    assert not service.ledger.root.exists()


def test_lock_hardlink_and_unknown_root_entries_fail_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _append(service, "acknowledge")
    os.link(service.ledger.lock_path, tmp_path / "second-lock-link")
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_LOCK_UNSAFE"):
        service.ledger.list_events()

    os.unlink(tmp_path / "second-lock-link")
    (service.ledger.root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_ROOT_UNKNOWN_ENTRY"):
        service.ledger.list_events()


def test_missing_lock_is_not_recreated_over_existing_events(tmp_path: Path) -> None:
    service = _service(tmp_path)
    acknowledged = _append(service, "acknowledge")
    service.ledger.lock_path.unlink()

    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_LOCK_MISSING"):
        _append(service, "resolve", acknowledged)

    assert not service.ledger.lock_path.exists()
    assert len(_event_files(service)) == 1


def test_failed_no_replace_commit_preserves_chain_and_allows_safe_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    acknowledged = _append(service, "acknowledge")
    original = [path.read_bytes() for path in _event_files(service)]
    original_link = triage_module.os.link

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected link failure")

    monkeypatch.setattr(triage_module.os, "link", fail_link)
    with pytest.raises(AlertTriageUnavailable, match="TRIAGE_EVENT_APPEND_FAILED"):
        _append(service, "resolve", acknowledged)
    assert [path.read_bytes() for path in _event_files(service)] == original
    assert list(service.ledger.events_root.glob(".financial-triage-*")) == []

    monkeypatch.setattr(triage_module.os, "link", original_link)
    resolved = _append(service, "resolve", acknowledged)
    assert resolved["status"] == "resolved"
    assert len(_event_files(service)) == 2


def test_route_authentication_trust_gate_and_privacy_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history = tmp_path / "route-history.json"
    JsonListStore(history).write([_history_row()])
    root = tmp_path / "route-triage"
    monkeypatch.setattr(financial, "ALERT_HISTORY_STORE", history)
    monkeypatch.setattr(financial, "FINANCIAL_ALERT_TRIAGE_ROOT", root)

    async def unavailable_dashboard(*, refresh: bool = False):
        return {}

    monkeypatch.setattr(financial, "get_dashboard", unavailable_dashboard)
    app = FastAPI()
    app.include_router(financial.router)

    with TestClient(app) as client:
        assert client.get(f"/api/financial/alert/triage/{ALERT_ID}").status_code == 401
        assert (
            client.post(
                f"/api/financial/alert/triage/{ALERT_ID}/events",
                json={
                    "action": "acknowledge",
                    "reason": "Administrative acknowledgement.",
                    "expected_previous_event_id": None,
                    "expected_previous_event_sha256": None,
                },
            ).status_code
            == 401
        )

    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 3,
        "username": "reader",
        "role": "user",
    }
    with TestClient(app) as client:
        assert client.get(f"/api/financial/alert/triage/{ALERT_ID}").status_code == 200
        assert (
            client.get(f"/api/financial/alert/triage/{ALERT_ID}/audit").status_code
            == 403
        )

    admin = {"user_id": 9, "username": "admin", "role": "admin"}
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    with TestClient(app) as client:
        blocked = client.post(
            f"/api/financial/alert/triage/{ALERT_ID}/events",
            json={
                "action": "acknowledge",
                "reason": "Administrative acknowledgement.",
                "expected_previous_event_id": None,
                "expected_previous_event_sha256": None,
            },
        )
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "FINANCIAL_INDEX_NOT_COMPUTABLE"
    assert not root.exists()

    monkeypatch.setattr(financial, "dashboard_is_computable", lambda _dashboard: True)
    with TestClient(app) as client:
        created = client.post(
            f"/api/financial/alert/triage/{ALERT_ID}/events",
            json={
                "action": "acknowledge",
                "reason": "Administrative acknowledgement.",
                "expected_previous_event_id": None,
                "expected_previous_event_sha256": None,
            },
        )
        safe = client.get(f"/api/financial/alert/triage/{ALERT_ID}")
        full = client.get(f"/api/financial/alert/triage/{ALERT_ID}/audit")

    assert created.status_code == 200
    assert safe.status_code == full.status_code == 200
    assert "actor_user_id" not in json.dumps(safe.json())
    assert "Administrative acknowledgement." not in json.dumps(safe.json())
    assert full.json()["audit"][0]["actor_user_id"] == 9
    assert full.json()["audit"][0]["reason"] == "Administrative acknowledgement."

    before = [path.read_bytes() for path in sorted((root / "events").glob("*.json"))]
    monkeypatch.setattr(financial, "dashboard_is_computable", lambda _dashboard: False)
    with TestClient(app) as client:
        escalation = client.post(
            f"/api/financial/alert/triage/{ALERT_ID}/events",
            json={
                "action": "escalate",
                "reason": "Escalation must remain blocked while trust is unavailable.",
                "expected_previous_event_id": created.json()["last_event_id"],
                "expected_previous_event_sha256": created.json()["last_event_sha256"],
                "escalation_target_role": "research_lead",
            },
        )
    assert escalation.status_code == 503
    assert [path.read_bytes() for path in sorted((root / "events").glob("*.json"))] == before


def test_public_alert_data_exposes_only_aggregate_historical_triage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history = tmp_path / "public-history.json"
    JsonListStore(history).write([_history_row()])
    root = tmp_path / "public-triage"
    service = FinancialAlertTriageService(
        ledger_root=root,
        alert_history_path=history,
        clock=lambda: datetime(2026, 8, 9, 8, 1, tzinfo=timezone.utc),
        nonce_factory=lambda: "0000000000000001",
    )
    service.mutate(
        ALERT_ID,
        _mutation("acknowledge", reason="Sensitive incident case 7788."),
        actor_user_id=55,
    )
    monkeypatch.setattr(financial, "ALERT_HISTORY_STORE", history)
    monkeypatch.setattr(financial, "FINANCIAL_ALERT_TRIAGE_ROOT", root)

    async def dashboard(*, refresh: bool = False):
        return {}

    monkeypatch.setattr(financial, "get_dashboard", dashboard)
    result = asyncio.run(financial.financial_alert_data(refresh=False))
    encoded = json.dumps(result["history"], ensure_ascii=False)

    assert result["history"][0]["triage"]["status"] == "acknowledged"
    assert result["history"][0]["triage"]["historical"] is True
    assert result["history"][0]["triage"]["mutations_enabled"] is False
    assert "actor_user_id" not in encoded
    assert "Sensitive incident case 7788." not in encoded


def test_route_rejects_noncanonical_admin_user_id_before_any_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history = tmp_path / "route-history.json"
    JsonListStore(history).write([_history_row()])
    root = tmp_path / "route-triage"
    monkeypatch.setattr(financial, "ALERT_HISTORY_STORE", history)
    monkeypatch.setattr(financial, "FINANCIAL_ALERT_TRIAGE_ROOT", root)
    monkeypatch.setattr(financial, "dashboard_is_computable", lambda _dashboard: True)

    async def dashboard(*, refresh: bool = False):
        return {}

    monkeypatch.setattr(financial, "get_dashboard", dashboard)
    app = FastAPI()
    app.include_router(financial.router)
    app.dependency_overrides[get_current_admin_user] = lambda: {
        "user_id": "9",
        "username": "admin",
        "role": "admin",
    }

    with TestClient(app) as client:
        response = client.post(
            f"/api/financial/alert/triage/{ALERT_ID}/events",
            json={
                "action": "acknowledge",
                "reason": "Administrative acknowledgement.",
                "expected_previous_event_id": None,
                "expected_previous_event_sha256": None,
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TRIAGE_ACTOR_USER_ID_INVALID"
    assert not root.exists()
