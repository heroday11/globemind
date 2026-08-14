from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deploy import browser_smoke, browser_smoke_evidence


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:18091", "http://127.0.0.1:18091"),
        ("http://127.42.0.8:8080/", "http://127.42.0.8:8080"),
        ("https://[::1]:18443", "https://[::1]:18443"),
    ],
)
def test_normalize_candidate_base_url_accepts_only_literal_loopback_origins(
    value: str,
    expected: str,
) -> None:
    assert browser_smoke.normalize_candidate_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://globemind.top:443",
        "http://localhost:18091",
        "http://0.0.0.0:18091",
        "http://127.0.0.1",
        "http://user:password@127.0.0.1:18091",
        "http://127.0.0.1:18091/candidate",
        "http://127.0.0.1:18091?token=secret",
        "http://127.0.0.1:18091#fragment",
        "file:///tmp/candidate",
    ],
)
def test_normalize_candidate_base_url_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(ValueError):
        browser_smoke.normalize_candidate_base_url(value)


def test_prepare_evidence_dir_requires_new_or_empty_non_symlink_path(
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence"
    prepared = browser_smoke.prepare_evidence_dir(output)

    assert prepared == output
    assert (output / "screenshots").is_dir()
    with pytest.raises(ValueError, match="empty"):
        browser_smoke.prepare_evidence_dir(output)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        browser_smoke.prepare_evidence_dir(nonempty)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "evidence-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        browser_smoke.prepare_evidence_dir(link)


def test_prepare_evidence_dir_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        browser_smoke.prepare_evidence_dir(Path("relative-evidence"))


def test_redaction_removes_dummy_token_credentials_and_url_queries() -> None:
    value = {
        "message": (
            f"Authorization: Bearer {browser_smoke.DUMMY_AUTH_TOKEN} "
            "password=hunter2 "
            "https://127.0.0.1:18091/login?access_token=should-not-survive"
        ),
        "access_token": "another-secret",
        "nested": ["api_key=key-material", "safe"],
        "console": "failed https://alice:private@external.example/path",
        "socket": "failed wss://bob:private@external.example/ws?session=secret",
    }

    sanitized = browser_smoke.redact_value(value)
    serialized = json.dumps(sanitized)

    for forbidden in (
        browser_smoke.DUMMY_AUTH_TOKEN,
        "hunter2",
        "should-not-survive",
        "another-secret",
        "key-material",
        "alice",
        "bob",
    ):
        assert forbidden not in serialized
    assert "https://127.0.0.1:18091/login" in serialized
    assert sanitized["access_token"] == "<redacted>"
    assert sanitized["console"] == "failed https://external.example/path"
    assert sanitized["socket"] == "failed wss://external.example/ws"


def test_safe_url_removes_userinfo_and_query_before_evidence() -> None:
    safe = browser_smoke._safe_url_without_query(
        "https://alice:private@external.example:8443/path?session=secret"
    )

    assert safe == "https://external.example:8443/path"
    assert "alice" not in safe
    assert "private" not in safe
    assert "session" not in safe


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:18091/api",
        "http://127.0.0.1:18091/api/health",
        "http://127.0.0.1:18091/api%2Fhealth",
        "http://127.0.0.1:18091/%61pi/health",
        "http://127.0.0.1:18091/api%252Fhealth",
    ],
)
def test_encoded_and_exact_api_paths_cannot_bypass_stubs(url: str) -> None:
    assert browser_smoke._is_api_path(browser_smoke._decoded_request_path(url))


def test_non_api_static_path_is_not_classified_as_api() -> None:
    url = "http://127.0.0.1:18091/assets/api-client.js"

    assert not browser_smoke._is_api_path(browser_smoke._decoded_request_path(url))


@pytest.mark.parametrize(
    ("path", "method", "resource_type"),
    [
        ("/", "GET", "document"),
        ("/data-service/data-search", "GET", "document"),
        ("/assets/index-deadbeef.js", "GET", "script"),
        ("/imgs/logo.png", "HEAD", "image"),
        ("/favicon.ico", "GET", "other"),
    ],
)
def test_candidate_request_allowlist_accepts_only_documents_and_static_assets(
    path: str,
    method: str,
    resource_type: str,
) -> None:
    assert browser_smoke._is_allowed_candidate_request(path, method, resource_type)


@pytest.mark.parametrize(
    ("path", "method", "resource_type"),
    [
        ("/cc/run", "POST", "fetch"),
        ("/graphql", "GET", "fetch"),
        ("/unknown", "GET", "document"),
        ("/assets/../cc/run", "GET", "script"),
        ("/assets\\..\\cc", "GET", "script"),
        ("/assets/unsafe\x00.js", "GET", "script"),
    ],
)
def test_candidate_request_allowlist_rejects_dynamic_or_unsafe_requests(
    path: str,
    method: str,
    resource_type: str,
) -> None:
    assert not browser_smoke._is_allowed_candidate_request(path, method, resource_type)


def test_redirect_evidence_records_only_a_bounded_status() -> None:
    expected = "/data-service/pipeline-monitor"

    assert browser_smoke._redirect_target_status([expected], expected) == "matched"
    assert browser_smoke._redirect_target_status([], expected) == "missing"
    assert (
        browser_smoke._redirect_target_status(
            ["/next?session=must-not-be-persisted"],
            expected,
        )
        == "unexpected"
    )


def test_request_router_never_forwards_dynamic_or_encoded_api_requests(
    tmp_path: Path,
) -> None:
    class FakeRequest:
        def __init__(self, url: str, method: str, resource_type: str) -> None:
            self.url = url
            self.method = method
            self.resource_type = resource_type

    class FakeRoute:
        def __init__(self) -> None:
            self.continued = False
            self.aborted = False
            self.fulfilled_status: int | None = None

        def continue_(self) -> None:
            self.continued = True

        def abort(self, _reason: str) -> None:
            self.aborted = True

        def fulfill(self, *, status: int, **_kwargs: object) -> None:
            self.fulfilled_status = status

    def activity() -> dict[str, list[object]]:
        return {
            "external_requests": [],
            "unexpected_api_requests": [],
            "blocked_same_origin_requests": [],
            "blocked_web_socket_requests": [],
            "stubbed_api_requests": [],
        }

    runner = browser_smoke.BrowserSmoke(
        base_url="http://127.0.0.1:18091",
        output_dir=tmp_path / "evidence",
    )

    dynamic_activity = activity()
    dynamic_route = FakeRoute()
    runner._route_request(
        dynamic_activity,
        dynamic_route,
        FakeRequest("http://127.0.0.1:18091/cc/run", "POST", "fetch"),
    )
    assert dynamic_route.aborted and not dynamic_route.continued
    assert dynamic_activity["blocked_same_origin_requests"]

    encoded_activity = activity()
    encoded_route = FakeRoute()
    runner._route_request(
        encoded_activity,
        encoded_route,
        FakeRequest("http://127.0.0.1:18091/api%2Fnot-declared", "GET", "fetch"),
    )
    assert encoded_route.fulfilled_status == 501
    assert not encoded_route.continued
    assert encoded_activity["unexpected_api_requests"] == [
        {"method": "GET", "path": "/api/not-declared"}
    ]

    static_activity = activity()
    static_route = FakeRoute()
    runner._route_request(
        static_activity,
        static_route,
        FakeRequest("http://127.0.0.1:18091/assets/app.js", "GET", "script"),
    )
    assert static_route.continued and not static_route.aborted


def test_web_socket_handler_blocks_without_persisting_credentials_or_query() -> None:
    class FakeWebSocket:
        url = "wss://alice:private@external.example/ws?session=secret"

        def __init__(self) -> None:
            self.closed: tuple[int | None, str | None] | None = None

        def close(self, *, code: int | None = None, reason: str | None = None) -> None:
            self.closed = (code, reason)

    activity: dict[str, list[object]] = {"blocked_web_socket_requests": []}
    web_socket = FakeWebSocket()

    browser_smoke.BrowserSmoke._block_web_socket(activity, web_socket)

    assert activity["blocked_web_socket_requests"] == [
        {"url": "wss://external.example/ws"}
    ]
    assert web_socket.closed == (1008, "browser smoke blocks WebSocket")


def _passing_observation() -> dict[str, object]:
    return {
        "authenticated": True,
        "expected_path": "/data-service/data-search",
        "final_path": "/data-service/data-search",
        "selector_visible": True,
        "dimensions": {
            "visible_text_chars": 42,
            "horizontal_overflow_px": 0,
            "obvious_overlaps": [],
        },
        "navigation_error": "",
        "http_redirects": [],
        "external_requests": [],
        "unexpected_api_requests": [],
        "blocked_same_origin_requests": [],
        "blocked_web_socket_requests": [],
        "resource_errors": [],
        "console_errors": [],
        "page_errors": [],
    }


def test_assess_page_observation_accepts_clean_visible_page() -> None:
    assert browser_smoke.assess_page_observation(_passing_observation()) == []


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("selector_visible", False, "root-selector-not-visible"),
        ("console_errors", ["fatal"], "console-error"),
        ("page_errors", ["uncaught"], "page-error"),
        ("http_redirects", [{"status": 302}], "http-redirect"),
        ("external_requests", [{"url": "https://example.test"}], "cross-origin-request"),
        ("unexpected_api_requests", [{"path": "/api/new"}], "unexpected-api-request"),
        (
            "blocked_same_origin_requests",
            [{"path": "/graphql"}],
            "unsafe-same-origin-request",
        ),
        (
            "blocked_web_socket_requests",
            [{"url": "wss://external.example/ws"}],
            "websocket-request",
        ),
        ("final_path", "/login", "authenticated-route-returned-login"),
    ],
)
def test_assess_page_observation_fails_closed(
    field: str,
    value: object,
    failure: str,
) -> None:
    observation = _passing_observation()
    observation[field] = value

    assert failure in browser_smoke.assess_page_observation(observation)


def test_assess_page_observation_rejects_blank_overflow_and_overlap() -> None:
    observation = _passing_observation()
    observation["dimensions"] = {
        "visible_text_chars": 0,
        "horizontal_overflow_px": 25,
        "obvious_overlaps": [{"left": "header", "right": "main"}],
    }

    assert browser_smoke.assess_page_observation(observation) == [
        "blank-page",
        "horizontal-overflow",
        "obvious-element-overlap",
    ]


def test_protected_redirect_requires_exact_target() -> None:
    observation = _passing_observation()
    observation.update(
        {
            "authenticated": False,
            "expected_path": "/login",
            "final_path": "/login",
            "expected_redirect": "/data-service/pipeline-monitor",
            "redirect_target_status": "unexpected",
        }
    )

    assert "invalid-auth-redirect" in browser_smoke.assess_page_observation(observation)


def test_api_stubs_cover_candidate_page_bootstrap_without_response_data() -> None:
    assert browser_smoke.api_stub_payload("/api/dashboard/news?page=1", "GET") == {
        "data": [],
        "total": 0,
        "page": 1,
        "page_size": 10,
    }
    assert browser_smoke.api_stub_payload("/api/workspaces", "GET") == {
        "ok": True,
        "data": [],
    }
    assert browser_smoke.api_stub_payload("/api/research/projects", "GET") == {
        "projects": [],
    }
    country_profiles = browser_smoke.api_stub_payload(
        "/api/authoritative-data/country-profiles/catalog",
        "GET",
    )
    assert country_profiles == {
        "schema_version": "browser-smoke-invalid-country-profile-fixture",
        "fixture_snapshot_id": browser_smoke.BROWSER_FIXTURE_SNAPSHOT_ID,
    }
    model_status = browser_smoke.api_stub_payload("/api/model-assurance/status", "GET")
    assert model_status["generated_at"] == browser_smoke.BROWSER_FIXTURE_GENERATED_AT
    assert model_status["release_status"] == "blocked"
    assert model_status["operational_state"] == "not_observed"
    assert browser_smoke.api_stub_payload(
        "/api/model-assurance/evaluations?limit=100",
        "GET",
    ) == []
    entity_status = browser_smoke.api_stub_payload(
        "/api/entity-governance/status",
        "GET",
    )
    assert entity_status["storage_status"] == "unavailable"
    assert entity_status["mutation_status"] == "blocked"
    assert entity_status["accuracy_claim"] == "not_measured"
    feature_health = browser_smoke.api_stub_payload("/api/status", "GET")
    assert feature_health["generated_at"] == browser_smoke.BROWSER_FIXTURE_GENERATED_AT
    assert feature_health["status"] == "historical"
    assert feature_health["ready"] is True
    assert set(feature_health["checks"]) == {
        "search",
        "ground-news",
        "opinion-analysis",
    }
    assert feature_health["checks"]["search"]["metrics"]["freshness_status"] == "stale"
    assert len(feature_health["objectives"]["workflows"]) == 3
    assert {
        item["compliance"] for item in feature_health["objectives"]["workflows"]
    } == {"not_computable"}
    with pytest.raises(browser_smoke.UnknownApiStub):
        browser_smoke.api_stub_payload("/api/not-declared", "GET")


def test_viewport_matrix_requires_both_viewports_and_exact_fixture_identity() -> None:
    results = []
    for spec in (
        *browser_smoke.PUBLIC_PAGE_SPECS,
        *browser_smoke.AUTHENTICATED_PAGE_SPECS,
    ):
        for viewport in ("desktop", "mobile"):
            semantic_probes = {
                selector: browser_smoke.fingerprint_semantic_probes(
                    {
                        selector: {
                            "text": "服务暂不可用，未展示伪造国家数据",
                            "role": expectation.role,
                            "aria_live": expectation.aria_live,
                            "aria_atomic": expectation.aria_atomic,
                            "aria_label_present": expectation.aria_label_required,
                        }
                    }
                )[selector]
                for selector, expectation in (
                    browser_smoke.SEMANTIC_ACCESSIBILITY_EXPECTATIONS.get(
                        spec.check_id,
                        {},
                    ).items()
                )
            }
            results.append(
                {
                    "page": spec.check_id,
                    "viewport": {"name": viewport},
                    "fixture_snapshot_id": browser_smoke.BROWSER_FIXTURE_SNAPSHOT_ID,
                    "semantic_probes": semantic_probes,
                }
            )

    assert browser_smoke.assess_viewport_matrix(results)["status"] == "passed"

    missing = browser_smoke.assess_viewport_matrix(results[:-1])
    assert missing["status"] == "failed"
    assert missing["missing"]

    drifted = [dict(item) for item in results]
    drifted[0]["fixture_snapshot_id"] = "other-fixture"
    mismatch = browser_smoke.assess_viewport_matrix(drifted)
    assert mismatch["status"] == "failed"
    assert mismatch["snapshot_mismatch_pages"] == [results[0]["page"]]

    semantic_drift = [dict(item) for item in results]
    country_mobile = next(
        item
        for item in semantic_drift
        if item["page"] == "country-profiles-unavailable"
        and item["viewport"]["name"] == "mobile"
    )
    country_mobile["semantic_probes"] = browser_smoke.fingerprint_semantic_probes(
        {".state-message--error": "移动端错误语义发生漂移"}
    )
    semantic_mismatch = browser_smoke.assess_viewport_matrix(semantic_drift)
    assert semantic_mismatch["status"] == "failed"
    assert semantic_mismatch["semantic_probe_mismatches"] == [
        "country-profiles-unavailable:.state-message--error"
    ]


def test_semantic_probe_fingerprint_never_retains_visible_text() -> None:
    phrase = "服务暂不可用，未展示伪造国家数据"
    normalized_phrase = "服务暂不可用， 未展示伪造国家数据"
    probe = browser_smoke.fingerprint_semantic_probes(
        {".state-message--error": "  服务暂不可用，  未展示伪造国家数据  "}
    )[".state-message--error"]

    assert probe["state"] == "observed"
    assert probe["normalized_text_chars"] == len(normalized_phrase)
    assert len(probe["normalized_text_sha256"]) == 64
    assert probe["role"] is None
    assert probe["aria_live"] is None
    assert probe["aria_label_present"] is False
    assert phrase not in json.dumps(probe, ensure_ascii=False)


def test_business_semantic_probe_inventory_covers_high_risk_empty_and_status_states() -> None:
    probes = {
        spec.check_id: spec.semantic_probe_selectors
        for spec in (
            *browser_smoke.PUBLIC_PAGE_SPECS,
            *browser_smoke.AUTHENTICATED_PAGE_SPECS,
        )
        if spec.semantic_probe_selectors
    }

    assert probes == {
        "country-profiles-unavailable": (".state-message--error",),
        "ground-news": (".home-hero__metrics", ".home-freshness"),
        "pipeline-monitor": (".trend-empty", ".status-ribbon"),
        "model-assurance": (
            ".status-panel .status-grid",
            ".status-panel .empty-state",
        ),
        "entity-governance": (".status-grid", ".state-message--error"),
    }
    assert {
        page: tuple(expectations)
        for page, expectations in browser_smoke.SEMANTIC_ACCESSIBILITY_EXPECTATIONS.items()
    } == probes


def test_business_probe_accessibility_semantics_fail_closed_without_retaining_labels() -> None:
    selector = ".state-message--error"
    observed = browser_smoke.fingerprint_semantic_probes(
        {
            selector: {
                "text": "服务暂不可用",
                "role": "status",
                "aria_live": "polite",
                "aria_atomic": "false",
                "aria_label_present": False,
            }
        }
    )
    observation = _passing_observation()
    observation.update(
        {
            "page": "country-profiles-unavailable",
            "semantic_probes": observed,
        }
    )

    assert "semantic-accessibility-mismatch" in (
        browser_smoke.assess_page_observation(observation)
    )
    serialized = json.dumps(observed, ensure_ascii=False)
    assert "服务暂不可用" not in serialized
    assert observed[selector]["role"] == "status"


def _write_passing_external_browser_evidence(tmp_path: Path) -> tuple[Path, str]:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    checks = []
    for viewport in browser_smoke.VIEWPORTS:
        for spec in (
            *browser_smoke.PUBLIC_PAGE_SPECS,
            *browser_smoke.AUTHENTICATED_PAGE_SPECS,
        ):
            semantic_probes = browser_smoke.fingerprint_semantic_probes(
                {
                    selector: {
                        "text": f"bounded semantic state for {selector}",
                        "role": expectation.role,
                        "aria_live": expectation.aria_live,
                        "aria_atomic": expectation.aria_atomic,
                        "aria_label_present": expectation.aria_label_required,
                    }
                    for selector, expectation in (
                        browser_smoke.SEMANTIC_ACCESSIBILITY_EXPECTATIONS.get(
                            spec.check_id,
                            {},
                        ).items()
                    )
                }
            )
            screenshot = f"screenshots/{viewport.name}-{spec.check_id}.png"
            (tmp_path / screenshot).write_bytes(
                b"\x89PNG\r\n\x1a\n" + spec.check_id.encode()
            )
            final_path = spec.expected_path or spec.path
            check = {
                "check_id": f"{viewport.name}-{spec.check_id}",
                "page": spec.check_id,
                "viewport": {
                    "name": viewport.name,
                    "width": viewport.width,
                    "height": viewport.height,
                    "mobile": viewport.is_mobile,
                },
                "requested_path": spec.path,
                "authenticated": spec.authenticated,
                "fixture_snapshot_id": browser_smoke.BROWSER_FIXTURE_SNAPSHOT_ID,
                "expected_path": final_path,
                "expected_redirect": spec.expected_redirect,
                "final_url": f"http://127.0.0.1:18091{final_path}",
                "final_path": final_path,
                "redirect_target_status": (
                    "matched" if spec.expected_redirect else "not_applicable"
                ),
                "selector": spec.selector,
                "selector_visible": True,
                "dimensions": {
                    "visible_text_chars": 20,
                    "horizontal_overflow_px": 0,
                    "obvious_overlaps": [],
                },
                "semantic_probes": semantic_probes,
                "screenshot": screenshot,
                "duration_ms": 10.0,
                "navigation_error": "",
                "screenshot_error": "",
                "console_errors": [],
                "page_errors": [],
                "http_redirects": [],
                "external_requests": [],
                "unexpected_api_requests": [],
                "blocked_same_origin_requests": [],
                "blocked_web_socket_requests": [],
                "resource_errors": [],
                "stubbed_api_paths": [],
                "failures": [],
                "outcome": "passed",
            }
            assert browser_smoke.assess_page_observation(check) == []
            checks.append(check)
    report = {
        "schema_version": browser_smoke.SCHEMA_VERSION,
        "tool": {
            "name": browser_smoke.TOOL_VERSION,
            "version": browser_smoke.SCHEMA_VERSION,
        },
        "status": "passed",
        "started_at": "2026-08-10T06:50:00Z",
        "finished_at": "2026-08-10T06:55:00Z",
        "duration_ms": 300000.0,
        "candidate": {"base_url": "http://127.0.0.1:18091"},
        "browser": {
            "engine": "chromium",
            "version": "external-test",
            "explicit_executable": True,
            "headless": True,
        },
        "policy": {
            "loopback_only": True,
            "http_redirects": "rejected",
            "cross_origin_requests": "blocked",
            "same_origin_requests": "document-and-static-allowlist",
            "web_socket_requests": "blocked",
            "api_mode": "in-memory-stubs-only",
            "api_fixture_snapshot_id": browser_smoke.BROWSER_FIXTURE_SNAPSHOT_ID,
            "viewport_fixture_identity": "exact_shared_snapshot_id",
            "semantic_accessibility_contract": (
                "explicit-role-live-region-atomic-and-label-presence"
            ),
            "response_bodies_persisted": False,
            "request_headers_persisted": False,
            "dummy_token_persisted": False,
        },
        "limits": {
            "timeout_ms": 20000,
            "settle_ms": 900,
            "horizontal_overflow_tolerance_px": 2,
        },
        "summary": {"total": len(checks), "passed": len(checks), "failed": 0},
        "viewport_matrix": browser_smoke.assess_viewport_matrix(checks),
        "operational_error": None,
        "checks": checks,
    }
    path = tmp_path / "browser-smoke.json"
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_external_browser_evidence_verifier_recomputes_scope_and_screenshot_hashes(
    tmp_path: Path,
) -> None:
    path, digest = _write_passing_external_browser_evidence(tmp_path)

    receipt = browser_smoke_evidence.verify_browser_smoke_evidence(
        path,
        expected_report_sha256=digest,
        evaluated_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
    )

    assert receipt["browser_evidence_verification"] == "passed"
    assert receipt["candidate_acceptance"] == (
        "not_established_in_memory_stubs_only"
    )
    assert receipt["page_count"] == 13
    assert receipt["viewport_count"] == 2
    assert receipt["check_count"] == 26
    assert receipt["business_semantic_page_count"] == 5
    assert receipt["semantic_selector_count"] == 9
    assert receipt["semantic_probe_observation_count"] == 18
    assert len(receipt["screenshot_artifacts"]) == 26
    assert receipt["response_bodies_retained_in_receipt"] is False
    assert receipt["candidate_api_consistency"].startswith("not_established")
    assert receipt["release_decision"] == "not_computable"


def test_external_browser_evidence_verifier_rejects_report_and_screenshot_tampering(
    tmp_path: Path,
) -> None:
    path, digest = _write_passing_external_browser_evidence(tmp_path)
    with pytest.raises(browser_smoke_evidence.BrowserSmokeEvidenceError, match="SHA"):
        browser_smoke_evidence.verify_browser_smoke_evidence(
            path,
            expected_report_sha256="f" * 64,
            evaluated_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
        )

    screenshot = next((tmp_path / "screenshots").iterdir())
    screenshot.write_bytes(b"tampered")
    with pytest.raises(
        browser_smoke_evidence.BrowserSmokeEvidenceError,
        match="not a PNG",
    ):
        browser_smoke_evidence.verify_browser_smoke_evidence(
            path,
            expected_report_sha256=digest,
            evaluated_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
        )

    with pytest.raises(
        browser_smoke_evidence.BrowserSmokeEvidenceError,
        match="production release",
    ):
        browser_smoke_evidence.verify_browser_smoke_evidence(
            Path("/root/data/releases/globemind/current/browser-smoke.json"),
            expected_report_sha256="a" * 64,
            evaluated_at=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
        )


def test_public_and_authenticated_checks_use_independent_contexts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.init_scripts: list[str] = []
            self.closed = False

        def set_default_timeout(self, _value: int) -> None:
            return

        def set_default_navigation_timeout(self, _value: int) -> None:
            return

        def add_init_script(self, script: str) -> None:
            self.init_scripts.append(script)

        def close(self) -> None:
            self.closed = True

    class FakeBrowser:
        def __init__(self) -> None:
            self.contexts: list[FakeContext] = []

        def new_context(self, **_kwargs: object) -> FakeContext:
            context = FakeContext()
            self.contexts.append(context)
            return context

    runner = browser_smoke.BrowserSmoke(
        base_url="http://127.0.0.1:18091",
        output_dir=tmp_path / "evidence",
    )
    browser = FakeBrowser()
    monkeypatch.setattr(
        runner,
        "_check_page",
        lambda _context, viewport, spec: {
            "outcome": "passed",
            "viewport": viewport.name,
            "page": spec.check_id,
        },
    )

    runner._run_viewport(browser, browser_smoke.VIEWPORTS[0])

    assert len(browser.contexts) == 2
    public_context, authenticated_context = browser.contexts
    assert len(public_context.init_scripts) == 1
    assert "globemind_new_user_guide_v3" in public_context.init_scripts[0]
    assert "access_token" not in public_context.init_scripts[0]
    assert len(authenticated_context.init_scripts) == 2
    assert browser_smoke.DUMMY_AUTH_TOKEN in "".join(authenticated_context.init_scripts)
    assert public_context.closed and authenticated_context.closed
    assert len(runner.results) == len(browser_smoke.PUBLIC_PAGE_SPECS) + len(
        browser_smoke.AUTHENTICATED_PAGE_SPECS
    )


def test_playwright_is_loaded_only_by_runtime_loader() -> None:
    source = Path(browser_smoke.__file__).read_text(encoding="utf-8")
    import_line = "from playwright.sync_api import sync_playwright"

    assert source.count(import_line) == 1
    assert source.index("def _load_playwright_factory") < source.index(import_line)
