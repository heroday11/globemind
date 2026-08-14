from __future__ import annotations

import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "check_claim_output_coverage.py"
INVENTORY = ROOT / "config" / "claim-output-inventory.json"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "globemind_claim_output_coverage", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


coverage = _load_script()


def _write(root: Path, locator: str, content: str) -> Path:
    path = root / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _fixture_payload(tmp_path: Path) -> dict:
    _write(
        tmp_path,
        "backend/api/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api/example')\n"
        "@router.get('/result')\n"
        "def get_result():\n"
        "    return {'claim_id': 'x', 'citation_locator': 'record:x', "
        "'reason_code': 'UNKNOWN', 'evidence_state': 'explicit_unknown'}\n",
    )
    _write(tmp_path, "frontend/src/Example.vue", "<template><main /></template>\n")
    return {
        "schema_version": "globemind.claim-output-inventory.v1",
        "automation": {
            "state": "not_configured",
            "reason_code": "READ_ONLY_MANUAL_GATE",
        },
        "scope": {
            "allowed_roots": ["backend/api", "frontend/src"],
            "allowed_suffixes": [".py", ".vue"],
            "max_entries": 8,
            "max_locators": 24,
            "max_file_bytes": 32768,
            "max_total_bytes": 131072,
        },
        "required_capabilities": [
            "claim_id",
            "citation_locator",
            "reason_code",
            "unknown_gate",
        ],
        "entries": [
            {
                "id": "example-output",
                "output_kind": "derived_api",
                "public_routes": [
                    {
                        "method": "GET",
                        "public_path": "/api/example/result",
                        "locator": "backend/api/routes/example.py",
                        "function": "get_result",
                    }
                ],
                "public_pages": ["frontend/src/Example.vue"],
                "capabilities": {
                    name: {
                        "state": "present",
                        "probes": [
                            {
                                "kind": "python_function_literals",
                                "locator": "backend/api/routes/example.py",
                                "symbol": "get_result",
                                "literals": [
                                    {
                                        "claim_id": "claim_id",
                                        "citation_locator": "citation_locator",
                                        "reason_code": "reason_code",
                                        "unknown_gate": "explicit_unknown",
                                    }[name]
                                ],
                            }
                        ],
                    }
                    for name in (
                        "claim_id",
                        "citation_locator",
                        "reason_code",
                        "unknown_gate",
                    )
                },
            }
        ],
    }


def test_checked_in_inventory_passes_static_probes_without_fact_attestation() -> None:
    inventory = coverage.load_inventory(INVENTORY, ROOT)
    report = coverage.audit_inventory(ROOT, inventory)

    assert inventory.automation_state == "configured"
    assert inventory.max_entries <= 32
    assert inventory.max_locators <= 128
    assert report.entry_total >= 7
    assert report.findings == ()
    assert report.coverage_state == "inventory_probes_passed_not_fact_verified"

    entries = {entry.id: entry for entry in inventory.entries}
    for surface_id in ("assistant-interactive", "assistant-scheduled-report"):
        capabilities = {
            capability.name: capability
            for capability in entries[surface_id].capabilities
        }
        claim_id = capabilities["claim_id"]
        assert claim_id.state == "present"
        assert claim_id.reason_code is None
        assert claim_id.probes


def test_exact_route_and_symbol_scoped_ast_probes_pass(tmp_path: Path) -> None:
    inventory = coverage.validate_inventory(_fixture_payload(tmp_path), tmp_path)

    assert coverage.audit_inventory(tmp_path, inventory).findings == ()


def test_missing_capability_is_a_finding_not_a_coverage_claim(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    payload["entries"][0]["capabilities"]["claim_id"] = {
        "state": "missing",
        "reason_code": "CLAIM_ID_NOT_EMITTED",
    }
    report = coverage.audit_inventory(
        tmp_path, coverage.validate_inventory(payload, tmp_path)
    )

    assert report.coverage_state == "partial"
    assert report.findings == (
        coverage.Finding(
            surface_id="example-output",
            capability="claim_id",
            rule_code="COV_CLAIM_ID_MISSING",
            locator="backend/api/routes/example.py#get_result",
        ),
    )
    assert set(report.findings[0].public_payload()) == {
        "surface_id",
        "capability",
        "rule_code",
        "locator",
    }


def test_keyword_outside_exact_symbol_cannot_self_assert_coverage(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "backend/api/routes/example.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api/example')\n"
        "CLAIM_ID = 'claim_id'\n"
        "@router.get('/result')\n"
        "def get_result():\n"
        "    return {'citation_locator': 'record:x', 'reason_code': 'UNKNOWN', "
        "'evidence_state': 'explicit_unknown'}\n",
    )

    report = coverage.audit_inventory(
        tmp_path, coverage.validate_inventory(payload, tmp_path)
    )

    assert any(item.rule_code == "COV_CLAIM_ID_PROBE_FAILED" for item in report.findings)


@pytest.mark.parametrize(
    "raw, message",
    [
        ('{"schema_version":"x","schema_version":"y"}', "duplicate JSON key"),
        ('{"schema_version":NaN}', "non-finite JSON number"),
    ],
)
def test_ambiguous_json_fails_closed(
    tmp_path: Path, raw: str, message: str
) -> None:
    path = _write(tmp_path, "inventory.json", raw)

    with pytest.raises(coverage.ClaimCoverageError, match=message):
        coverage.load_inventory(path, tmp_path)


def test_excessive_json_depth_fails_closed(tmp_path: Path) -> None:
    raw = '{"x":' * 40 + "null" + "}" * 40
    path = _write(tmp_path, "inventory.json", raw)

    with pytest.raises(coverage.ClaimCoverageError, match="nesting depth"):
        coverage.load_inventory(path, tmp_path)


def test_inventory_file_itself_cannot_escape_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    external = _write(tmp_path, "external.json", "{}")

    with pytest.raises(coverage.ClaimCoverageError, match="escapes the repository"):
        coverage.load_inventory(external, repository)


def test_source_escape_symlink_and_hardlink_fail_closed(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    payload["entries"][0]["public_pages"] = ["../outside.vue"]
    with pytest.raises(coverage.ClaimCoverageError, match="normalized repository-relative"):
        coverage.validate_inventory(payload, tmp_path)

    payload = _fixture_payload(tmp_path)
    outside = _write(tmp_path, "outside.vue", "<template />\n")
    link = tmp_path / "frontend" / "src" / "Linked.vue"
    link.symlink_to(outside)
    payload["entries"][0]["public_pages"] = ["frontend/src/Linked.vue"]
    with pytest.raises(coverage.ClaimCoverageError, match="symlink"):
        coverage.validate_inventory(payload, tmp_path)

    link.unlink()
    os.link(outside, link)
    with pytest.raises(coverage.ClaimCoverageError, match="hard-linked"):
        coverage.validate_inventory(payload, tmp_path)


def test_source_count_and_size_limits_fail_closed(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    payload["scope"]["max_entries"] = 1
    payload["entries"].append(deepcopy(payload["entries"][0]))
    payload["entries"][1]["id"] = "second-output"
    with pytest.raises(coverage.ClaimCoverageError, match="entry limit"):
        coverage.validate_inventory(payload, tmp_path)

    payload = _fixture_payload(tmp_path)
    payload["scope"]["max_file_bytes"] = 16
    with pytest.raises(coverage.ClaimCoverageError, match="file byte limit"):
        coverage.validate_inventory(payload, tmp_path)


def test_route_drift_is_a_finding_and_report_never_contains_body(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    payload["entries"][0]["public_routes"][0]["public_path"] = "/api/example/secret"
    report = coverage.audit_inventory(
        tmp_path, coverage.validate_inventory(payload, tmp_path)
    )
    serialized = json.dumps(report.public_payload(), ensure_ascii=False)

    assert any(item.rule_code == "COV_ROUTE_PROBE_FAILED" for item in report.findings)
    assert "record:x" not in serialized
    assert "explicit_unknown" not in serialized


def test_cli_returns_one_for_partial_and_two_for_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _fixture_payload(tmp_path)
    payload["entries"][0]["capabilities"]["claim_id"] = {
        "state": "missing",
        "reason_code": "CLAIM_ID_NOT_EMITTED",
    }
    path = _write(tmp_path, "inventory.json", json.dumps(payload))

    assert coverage.main(["--repository-root", str(tmp_path), "--inventory", str(path)]) == 1
    output = capsys.readouterr().out
    assert "coverage_state=partial" in output
    assert "CLAIM_ID_NOT_EMITTED" not in output

    assert coverage.main(["--repository-root", str(tmp_path), "--inventory", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().out == "claim-output-coverage:config:COV_CONFIG_MISSING\n"
