from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.features.service_level import (
    MEASUREMENT_METHOD_VERSION,
    ObservationInput,
    ServiceLevelASGIMiddleware,
    ServiceLevelInstrumentationAdapter,
    ServiceLevelService,
    ServiceLevelStore,
    ServiceLevelStoreUnavailable,
)
from api.routes import service_level as service_level_routes
from api.services.auth import get_current_admin_user, get_current_user_required


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _service(root: Path) -> ServiceLevelService:
    return ServiceLevelService(ServiceLevelStore(root), now=lambda: NOW)


def _record(
    service: ServiceLevelService,
    operation: str = "search",
    outcome: str = "success",
    duration_ms: int = 25,
    *,
    observed_at: datetime = NOW,
) -> None:
    service.record(
        operation=operation,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        duration_ms=duration_ms,
        observed_at=observed_at,
    )


def _process_record(arguments: tuple[str, int]) -> None:
    root, index = arguments
    service = _service(Path(root))
    _record(
        service,
        operation=("search", "export", "report")[index % 3],
        outcome=("success", "error", "timeout", "cancelled")[index % 4],
        duration_ms=index,
    )


def test_constructor_status_and_empty_summary_are_zero_write(tmp_path: Path) -> None:
    root = tmp_path / "service-level-never-created"
    service = _service(root)

    status = service.status()
    summary = service.summary(window_hours=24)

    assert not root.exists()
    assert status.measurement_state == "not_observed"
    assert status.storage_state == "not_initialized"
    assert status.total_observation_count == 0
    assert status.instrumentation_write_failure_count == 0
    assert status.target.approval_state == "not_approved"
    assert status.target.compliance == "not_computable"
    assert status.target.approver_evidence_state == "absent"
    assert summary.measurement_state == "not_observed"
    assert summary.overall.sample_count == 0
    assert summary.overall.success_rate is None
    assert summary.overall.p99_ms is None
    assert [item.scope for item in summary.operations] == [
        "search",
        "export",
        "report",
    ]
    assert not root.exists()


def test_root_must_be_absolute_release_external_and_not_symlinked(
    tmp_path: Path,
) -> None:
    with pytest.raises(ServiceLevelStoreUnavailable, match="absolute"):
        ServiceLevelStore(Path("relative/service-level"))
    with pytest.raises(ServiceLevelStoreUnavailable, match="release"):
        ServiceLevelStore(Path("/root/data/releases/globemind/current/slo"))

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ServiceLevelStoreUnavailable, match="symbolic"):
        ServiceLevelStore(linked / "measurements")

    replaced_root = tmp_path / "replaced-root"
    store = ServiceLevelStore(replaced_root)
    replaced_root.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(ServiceLevelStoreUnavailable):
        store.snapshot()


def test_concurrent_appends_form_one_verified_no_replace_chain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service-level"
    service = _service(root)

    def append(index: int) -> None:
        _record(
            service,
            operation=("search", "export", "report")[index % 3],
            outcome=("success", "error", "timeout", "cancelled")[index % 4],
            duration_ms=index,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(append, range(1, 49)))

    observations, failures, initialized = service._store.snapshot()
    assert initialized is True
    assert failures == []
    assert len(observations) == 48
    assert [record.sequence for record in observations] == list(range(1, 49))
    assert observations[0].previous_entry_sha256 is None
    for previous, current in zip(observations, observations[1:]):
        assert current.previous_entry_sha256 == previous.entry_sha256
    assert sorted(path.name for path in (root / "observations").iterdir()) == [
        f"{sequence:08d}.json" for sequence in range(1, 49)
    ]


def test_cross_process_workers_share_the_durable_lock_and_chain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-worker"
    context = multiprocessing.get_context("fork")
    with context.Pool(processes=4) as pool:
        pool.map(
            _process_record,
            [(str(root), index) for index in range(1, 17)],
        )

    service = _service(root)
    observations, failures, initialized = service._store.snapshot()
    assert initialized is True
    assert len(observations) == 16
    assert failures == []
    assert [item.sequence for item in observations] == list(range(1, 17))


@pytest.mark.parametrize(
    "damage",
    ["duplicate", "nan", "invalid", "oversize", "tamper"],
)
def test_corrupt_duplicate_nonfinite_and_tampered_records_fail_closed(
    tmp_path: Path,
    damage: str,
) -> None:
    root = tmp_path / damage
    service = _service(root)
    _record(service)
    record_path = root / "observations" / "00000001.json"
    original = record_path.read_text(encoding="utf-8")
    if damage == "duplicate":
        record_path.write_text(
            original[:-1] + ',"sequence":1}',
            encoding="utf-8",
        )
    elif damage == "nan":
        payload = json.loads(original)
        payload["duration_ms"] = float("nan")
        record_path.write_text(
            json.dumps(payload, allow_nan=True),
            encoding="utf-8",
        )
    elif damage == "invalid":
        record_path.write_bytes(b"not-json")
    elif damage == "oversize":
        record_path.write_bytes(b"{" + b"x" * 4096 + b"}")
    else:
        payload = json.loads(original)
        payload["duration_ms"] += 1
        record_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ServiceLevelStoreUnavailable):
        service.status()


def test_symlink_hardlink_unknown_files_and_unsafe_modes_fail_closed(
    tmp_path: Path,
) -> None:
    for damage in ("symlink", "hardlink", "unknown", "mode"):
        root = tmp_path / damage
        service = _service(root)
        _record(service)
        record_path = root / "observations" / "00000001.json"
        if damage == "symlink":
            target = tmp_path / f"{damage}-target.json"
            target.write_bytes(record_path.read_bytes())
            record_path.unlink()
            record_path.symlink_to(target)
        elif damage == "hardlink":
            os.link(record_path, tmp_path / f"{damage}-copy.json")
        elif damage == "unknown":
            (root / "unexpected.txt").write_text("x", encoding="utf-8")
        else:
            record_path.chmod(0o666)
        with pytest.raises(ServiceLevelStoreUnavailable):
            service.status()


def test_observation_contract_rejects_private_or_dynamic_request_fields() -> None:
    valid = {
        "operation": "search",
        "outcome": "success",
        "duration_ms": 12,
        "observed_at": NOW,
    }
    for field in (
        "url",
        "route",
        "query",
        "request_body",
        "user_id",
        "token",
        "exception",
        "event_id",
        "actor",
    ):
        with pytest.raises(ValidationError):
            ObservationInput.model_validate({**valid, field: "private-marker"})
    for field, value in (
        ("operation", "billing"),
        ("outcome", "partial"),
        ("duration_ms", -1),
        ("duration_ms", 3_600_001),
    ):
        with pytest.raises(ValidationError):
            ObservationInput.model_validate({**valid, field: value})


def test_time_bounds_and_window_bounds_are_enforced(tmp_path: Path) -> None:
    service = _service(tmp_path / "bounds")
    with pytest.raises(ValueError, match="retention"):
        _record(service, observed_at=NOW - timedelta(days=367))
    with pytest.raises(ValueError, match="clock-skew"):
        _record(service, observed_at=NOW + timedelta(minutes=6))
    with pytest.raises(ValueError, match="window_hours"):
        service.summary(window_hours=0)
    with pytest.raises(ValueError, match="window_hours"):
        service.summary(window_hours=721)
    assert not (tmp_path / "bounds").exists()


def test_aggregate_math_is_server_computed_with_fixed_nearest_rank_method(
    tmp_path: Path,
) -> None:
    root = tmp_path / "aggregate"
    service = _service(root)
    fixtures = [
        ("search", "success", 10),
        ("search", "success", 20),
        ("search", "error", 30),
        ("export", "timeout", 40),
        ("report", "cancelled", 50),
    ]
    for operation, outcome, duration in fixtures:
        _record(service, operation, outcome, duration)

    summary = service.summary(window_hours=1)

    assert summary.measurement_method_version == MEASUREMENT_METHOD_VERSION
    assert summary.measurement_state == "observed"
    assert summary.overall.model_dump() == {
        "scope": "overall",
        "sample_count": 5,
        "success_count": 2,
        "error_count": 1,
        "timeout_count": 1,
        "cancelled_count": 1,
        "error_rate_definition": "all_non_success_outcomes",
        "percentile_method": "nearest_rank",
        "success_rate": 0.4,
        "error_rate": 0.6,
        "p50_ms": 30,
        "p95_ms": 50,
        "p99_ms": 50,
    }
    search = summary.operations[0]
    assert search.sample_count == 3
    assert search.success_rate == pytest.approx(2 / 3)
    assert search.error_rate == pytest.approx(1 / 3)
    assert search.p50_ms == 20
    assert search.p95_ms == 30
    assert summary.target.approval_state == "not_approved"
    assert summary.target.compliance == "not_computable"


def test_populated_status_and_summary_reads_do_not_modify_the_ledger(
    tmp_path: Path,
) -> None:
    root = tmp_path / "read-only"
    service = _service(root)
    _record(service, "export", "success", 22)
    before = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    service.status()
    service.summary(window_hours=12)

    after = {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_persisted_records_never_contain_request_or_identity_material(
    tmp_path: Path,
) -> None:
    root = tmp_path / "privacy"
    service = _service(root)
    _record(service, "report", "error", 77)

    encoded = b"".join(path.read_bytes() for path in root.rglob("*.json"))
    for forbidden in (
        b"http://",
        b"https://",
        b"query",
        b"request_body",
        b"user_id",
        b"token",
        b"exception",
        b"event_id",
        b"actor",
        b"private-marker",
    ):
        assert forbidden not in encoded


def test_instrumentation_failure_is_durable_or_explicitly_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounded_service = _service(tmp_path / "bounded-write-failure")
    assert bounded_service.record_instrumentation(
        operation="search",
        outcome="success",
        duration_ms=3_600_001,
        observed_at=NOW,
    ) == "failure_recorded"
    assert (
        bounded_service.status().instrumentation_write_failure_count == 1
    )

    root = tmp_path / "write-failure"
    service = _service(root)

    def reject_observation(_observation: ObservationInput) -> None:
        raise ServiceLevelStoreUnavailable("private storage detail")

    monkeypatch.setattr(service._store, "append_observation", reject_observation)
    assert service.record_instrumentation(
        operation="search",
        outcome="success",
        duration_ms=10,
        observed_at=NOW,
    ) == "failure_recorded"
    status = service.status()
    assert status.total_observation_count == 0
    assert status.instrumentation_write_failure_count == 1
    assert status.instrumentation_write_state == "failures_observed"

    def reject_failure(**_kwargs: object) -> None:
        raise ServiceLevelStoreUnavailable("private failure detail")

    monkeypatch.setattr(service._store, "append_write_failure", reject_failure)
    assert service.record_instrumentation(
        operation="report",
        outcome="timeout",
        duration_ms=12,
        observed_at=NOW,
    ) == "unavailable"
    # No process-local value is added to the durable cross-worker count.
    assert service.status().instrumentation_write_failure_count == 1


def test_asgi_middleware_measures_only_preconfigured_route_templates(
    tmp_path: Path,
) -> None:
    service = ServiceLevelService(ServiceLevelStore(tmp_path / "middleware"))
    adapter = ServiceLevelInstrumentationAdapter(service)
    app = FastAPI()

    @app.get("/search/{term}")
    def search(term: str) -> dict[str, str]:
        return {"term": term}

    @app.get("/exports/{export_id}")
    def export(export_id: str) -> None:
        raise RuntimeError(f"private-export-{export_id}")

    @app.get("/reports/{report_id}")
    def report(report_id: str) -> None:
        raise TimeoutError(f"private-report-{report_id}")

    instrumented = ServiceLevelASGIMiddleware(
        app,
        adapter=adapter,
        routes={
            ("GET", "/search/{term}"): "search",
            ("GET", "/exports/{export_id}"): "export",
            ("GET", "/reports/{report_id}"): "report",
        },
    )

    with TestClient(instrumented, raise_server_exceptions=False) as client:
        success = client.get("/search/private-query-marker?token=private-token")
        error = client.get("/exports/private-export-marker")
        timeout = client.get("/reports/private-report-marker")
        unknown = client.get("/unknown/private-path-marker")

    assert success.status_code == 200
    assert error.status_code == 500
    assert timeout.status_code == 500
    assert unknown.status_code == 404
    observations, failures, _initialized = service._store.snapshot()
    assert [(item.operation, item.outcome) for item in observations] == [
        ("search", "success"),
        ("export", "error"),
        ("report", "timeout"),
    ]
    assert failures == []
    encoded = b"".join(
        path.read_bytes() for path in (tmp_path / "middleware").rglob("*.json")
    )
    for forbidden in (
        b"private-query-marker",
        b"private-token",
        b"private-export-marker",
        b"private-report-marker",
        b"private-path-marker",
        b"/search/",
        b"/exports/",
        b"/reports/",
        b"/unknown/",
    ):
        assert forbidden not in encoded


def test_asgi_measurement_failure_never_masks_successful_business_response() -> None:
    class RaisingAdapter:
        def observe(self, **_kwargs: object) -> None:
            raise RuntimeError("private-adapter-detail")

    app = FastAPI()

    @app.get("/search")
    def search() -> dict[str, bool]:
        return {"ok": True}

    instrumented = ServiceLevelASGIMiddleware(
        app,
        adapter=RaisingAdapter(),  # type: ignore[arg-type]
        routes={("GET", "/search"): "search"},
    )
    with TestClient(instrumented) as client:
        response = client.get("/search")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_asgi_cancellation_is_recorded_then_propagated(tmp_path: Path) -> None:
    service = ServiceLevelService(ServiceLevelStore(tmp_path / "cancelled"))
    adapter = ServiceLevelInstrumentationAdapter(service)

    async def cancelled_app(scope, _receive, _send) -> None:
        scope["route"] = type("Route", (), {"path": "/reports/{report_id}"})()
        raise asyncio.CancelledError

    middleware = ServiceLevelASGIMiddleware(
        cancelled_app,
        adapter=adapter,
        routes={("GET", "/reports/{report_id}"): "report"},
    )

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, object]) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            middleware(
                {"type": "http", "method": "GET", "path": "/private"},
                receive,
                send,
            )
        )

    observations, _failures, _initialized = service._store.snapshot()
    assert [(item.operation, item.outcome) for item in observations] == [
        ("report", "cancelled")
    ]


def _route_app(service: ServiceLevelService) -> FastAPI:
    app = FastAPI()
    app.include_router(service_level_routes.router)
    app.dependency_overrides[service_level_routes.get_service_level_service] = (
        lambda: service
    )
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "role": "admin",
    }
    app.dependency_overrides[get_current_admin_user] = lambda: {
        "user_id": 7,
        "role": "admin",
    }
    return app


def test_routes_are_authenticated_admin_bounded_and_privacy_minimal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "route"
    service = _service(root)
    app = _route_app(service)

    with TestClient(app) as client:
        status_response = client.get("/api/service-level/status")
        summary_response = client.get("/api/service-level/summary?window_hours=1")
        recorded = client.post(
            "/api/service-level/observations/search",
            json={"outcome": "success", "duration_ms": 19},
        )
        invalid_operation = client.post(
            "/api/service-level/observations/billing",
            json={"outcome": "success", "duration_ms": 19},
        )
        private_field = client.post(
            "/api/service-level/observations/report",
            json={
                "outcome": "error",
                "duration_ms": 20,
                "query": "private-marker",
            },
        )

    assert status_response.status_code == 200
    assert summary_response.status_code == 200
    assert recorded.status_code == 201
    assert recorded.json() == {
        "schema_version": "globemind.service-level.v1",
        "measurement_method_version": MEASUREMENT_METHOD_VERSION,
        "recorded": True,
        "operation": "search",
    }
    assert invalid_operation.status_code == 422
    assert private_field.status_code == 422
    for response in (status_response, summary_response, recorded):
        encoded = response.text.lower()
        assert "filesystem" not in encoded
        assert str(root).lower() not in encoded
        assert "event_id" not in encoded
        assert "actor" not in encoded
        assert "user_id" not in encoded
        assert "request" not in encoded

    schema = app.openapi()
    for path, method in (
        ("/api/service-level/status", "get"),
        ("/api/service-level/summary", "get"),
        ("/api/service-level/observations/{operation}", "post"),
    ):
        assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]


def test_route_tamper_failure_is_generic_503(tmp_path: Path) -> None:
    root = tmp_path / "route-tamper"
    service = _service(root)
    _record(service)
    record_path = root / "observations" / "00000001.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["duration_ms"] = 999
    record_path.write_text(json.dumps(payload), encoding="utf-8")
    app = _route_app(service)

    with TestClient(app) as client:
        response = client.get("/api/service-level/status")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "service-level measurements are unavailable"
    }
    assert str(root) not in response.text


def test_route_admin_boundary_remains_explicit(tmp_path: Path) -> None:
    service = _service(tmp_path / "admin-boundary")
    app = _route_app(service)
    app.dependency_overrides.pop(get_current_admin_user)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 8,
        "role": "user",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/service-level/observations/search",
            json={"outcome": "success", "duration_ms": 10},
        )

    assert response.status_code == 403
    assert not (tmp_path / "admin-boundary").exists()
