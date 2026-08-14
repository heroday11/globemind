from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "check_public_claims.py"
POLICY = ROOT / "config" / "public-claim-policy.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("globemind_public_claim_linter", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claim_linter = _load_script()


def _write(root: Path, locator: str, content: str) -> Path:
    path = root / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _fixture_payload(tmp_path: Path) -> dict:
    payload = deepcopy(json.loads(POLICY.read_text(encoding="utf-8")))
    payload["automation"] = {
        "state": "not_configured",
        "scheduler_locator": None,
        "artifact_retention_locator": None,
        "reason_code": "FIXTURE_AUTOMATION_NOT_CONFIGURED",
    }
    payload["scope"] = {
        "include_roots": ["frontend/src"],
        "suffixes": [".vue", ".js", ".ts", ".tsx"],
        "max_files": 16,
        "max_file_bytes": 16_384,
        "max_total_bytes": 65_536,
        "exclusions": [],
    }
    payload["evidence_mappings"] = []
    (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    return payload


def test_checked_in_policy_is_bounded_automated_and_repository_is_clean() -> None:
    policy = claim_linter.load_policy(POLICY, ROOT)

    assert policy.automation_state == "configured"
    assert policy.max_files <= 256
    assert policy.max_file_bytes <= 262_144
    assert policy.max_total_bytes <= 8_388_608
    assert claim_linter.scan_repository(ROOT, policy) == ()


def test_unqualified_claims_emit_only_locator_rule_and_line(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "frontend/src/PublicPage.vue",
        "<template>\n"
        "  <p>LIVE 全球监测</p>\n"
        "  <p>连接可信数据库</p>\n"
        "  <p>覆盖 60+ 语种</p>\n"
        "</template>\n",
    )

    policy = claim_linter.validate_policy(payload, tmp_path)
    findings = claim_linter.scan_repository(tmp_path, policy)

    assert {(item.rule_code, item.line) for item in findings} == {
        ("CLM_REALTIME_UNQUALIFIED", 2),
        ("CLM_ASSURANCE_UNQUALIFIED", 3),
        ("CLM_QUANTIFIED_COVERAGE_UNQUALIFIED", 4),
        ("CLM_SCALE_UNQUALIFIED", 4),
    }
    assert all(
        set(item.public_payload()) == {"locator", "line", "rule_code"}
        for item in findings
    )
    serialized = json.dumps([item.public_payload() for item in findings], ensure_ascii=False)
    assert "全球监测" not in serialized
    assert "可信数据库" not in serialized
    assert "60+ 语种" not in serialized


def test_qualified_historical_and_ui_state_words_are_not_marketing_claims(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "frontend/src/PublicPage.vue",
        "<template>\n"
        "  <p aria-live=\"polite\">状态更新</p>\n"
        "  <p>历史材料曾记录 60+ 语种，待核验，不代表当前覆盖。</p>\n"
        "  <p>当前内容不得作为实时结论。</p>\n"
        "  <p>准确率为 not_measured，尚未形成专业结论。</p>\n"
        "</template>\n",
    )

    policy = claim_linter.validate_policy(payload, tmp_path)

    assert claim_linter.scan_repository(tmp_path, policy) == ()


def test_qualifier_in_a_different_clause_cannot_launder_a_strong_claim(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "frontend/src/PublicPage.vue",
        "<p>历史样本待核验；LIVE 全球监测</p>\n",
    )

    policy = claim_linter.validate_policy(payload, tmp_path)

    assert claim_linter.scan_repository(tmp_path, policy) == (
        claim_linter.Finding(
            locator="frontend/src/PublicPage.vue",
            line=1,
            rule_code="CLM_REALTIME_UNQUALIFIED",
        ),
    )


def test_markup_attribute_and_later_comma_clause_cannot_launder_claims(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "frontend/src/PublicPage.vue",
        '<p aria-live="polite">LIVE 全球监测</p>\n'
        "<p>LIVE 全球监测，历史样本待核验</p>\n",
    )

    policy = claim_linter.validate_policy(payload, tmp_path)

    assert claim_linter.scan_repository(tmp_path, policy) == (
        claim_linter.Finding(
            locator="frontend/src/PublicPage.vue",
            line=1,
            rule_code="CLM_REALTIME_UNQUALIFIED",
        ),
        claim_linter.Finding(
            locator="frontend/src/PublicPage.vue",
            line=2,
            rule_code="CLM_REALTIME_UNQUALIFIED",
        ),
    )


def test_english_assurance_and_scale_claims_are_covered(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    _write(
        tmp_path,
        "frontend/src/PublicPage.vue",
        "<p>Trusted database with 2 million sources and a professional report.</p>\n",
    )

    policy = claim_linter.validate_policy(payload, tmp_path)
    rule_codes = {item.rule_code for item in claim_linter.scan_repository(tmp_path, policy)}

    assert rule_codes == {
        "CLM_ASSURANCE_UNQUALIFIED",
        "CLM_QUANTIFIED_COVERAGE_UNQUALIFIED",
        "CLM_SCALE_UNQUALIFIED",
    }


def test_fixture_exclusion_is_explicit_and_does_not_suppress_production(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    payload["scope"]["exclusions"] = [
        {
            "locator": "frontend/src/fixtures",
            "classification": "fixture",
            "reason_code": "TEST_FIXTURE",
        }
    ]
    _write(tmp_path, "frontend/src/fixtures/Demo.vue", "<p>LIVE 60+ 语种</p>\n")
    _write(tmp_path, "frontend/src/PublicPage.vue", "<p>LIVE 全球监测</p>\n")

    policy = claim_linter.validate_policy(payload, tmp_path)
    findings = claim_linter.scan_repository(tmp_path, policy)

    assert findings == (
        claim_linter.Finding(
            locator="frontend/src/PublicPage.vue",
            line=1,
            rule_code="CLM_REALTIME_UNQUALIFIED",
        ),
    )


def test_exact_claim_anchor_requires_existing_catalog_method_or_status_locator(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    _write(tmp_path, "frontend/src/PublicPage.vue", "<p>Ready = 3+ 信源且有评级</p>\n")
    _write(tmp_path, "backend/method.py", "READY_SOURCE_MINIMUM = 3\n")
    payload["evidence_mappings"] = [
        {
            "source_locator": "frontend/src/PublicPage.vue",
            "rule_code": "CLM_SCALE_UNQUALIFIED",
            "claim_anchor_pattern": "Ready\\s*=\\s*3\\+\\s*信源",
            "classification": "evidence_backed",
            "evidence": [{"kind": "method", "locator": "backend/method.py"}],
        }
    ]

    policy = claim_linter.validate_policy(payload, tmp_path)
    findings = claim_linter.scan_repository(tmp_path, policy)

    assert not any(item.rule_code == "CLM_SCALE_UNQUALIFIED" for item in findings)

    payload["evidence_mappings"][0]["evidence"][0]["locator"] = "backend/missing.py"
    with pytest.raises(claim_linter.ClaimPolicyError, match="does not exist"):
        claim_linter.validate_policy(payload, tmp_path)


def test_configured_automation_requires_scheduler_and_retention_evidence(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload(tmp_path)
    payload["automation"] = {
        "state": "configured",
        "scheduler_locator": None,
        "artifact_retention_locator": None,
    }

    with pytest.raises(claim_linter.ClaimPolicyError):
        claim_linter.validate_policy(payload, tmp_path)

    _write(tmp_path, "ops/schedule.yml", "schedule: read-only\n")
    _write(tmp_path, "ops/retention.md", "bounded retention\n")
    payload["automation"] = {
        "state": "configured",
        "scheduler_locator": "ops/schedule.yml",
        "artifact_retention_locator": "ops/retention.md",
    }

    assert claim_linter.validate_policy(payload, tmp_path).automation_state == "configured"


def test_missing_policy_fails_without_an_automation_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = claim_linter.main(["--policy", str(tmp_path / "missing.json")])

    assert exit_code == 2
    assert capsys.readouterr().out == "claim-policy:0:CLM_CONFIG_MISSING\n"


def test_scope_fails_closed_on_size_limit_and_release_locator(tmp_path: Path) -> None:
    payload = _fixture_payload(tmp_path)
    payload["scope"]["max_file_bytes"] = 8
    _write(tmp_path, "frontend/src/PublicPage.vue", "<p>ordinary copy</p>\n")
    policy = claim_linter.validate_policy(payload, tmp_path)

    with pytest.raises(claim_linter.ClaimPolicyError, match="max_file_bytes"):
        claim_linter.scan_repository(tmp_path, policy)

    payload = _fixture_payload(tmp_path)
    payload["scope"]["include_roots"] = ["releases/current/frontend"]
    with pytest.raises(claim_linter.ClaimPolicyError, match="release boundary"):
        claim_linter.validate_policy(payload, tmp_path)


def test_policy_loader_rejects_outside_hardlinked_duplicate_and_non_finite_inputs(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    payload = _fixture_payload(repository)
    encoded = json.dumps(payload)

    outside = _write(tmp_path, "outside-policy.json", encoded)
    with pytest.raises(claim_linter.ClaimPolicyError, match="inside the repository"):
        claim_linter.load_policy(outside, repository)

    external = _write(tmp_path, "external-policy.json", encoded)
    hardlink = repository / "hardlinked-policy.json"
    hardlink.hardlink_to(external)
    with pytest.raises(claim_linter.ClaimPolicyError, match="hard-linked"):
        claim_linter.load_policy(hardlink, repository)

    duplicate = encoded.replace(
        '"schema_version": 1',
        '"schema_version": 1, "schema_version": 1',
        1,
    )
    duplicate_path = _write(repository, "duplicate-policy.json", duplicate)
    with pytest.raises(claim_linter.ClaimPolicyError, match="duplicate"):
        claim_linter.load_policy(duplicate_path, repository)

    non_finite_payload = deepcopy(payload)
    non_finite_payload["untrusted_probe"] = float("nan")
    non_finite_path = _write(
        repository,
        "non-finite-policy.json",
        json.dumps(non_finite_payload),
    )
    with pytest.raises(claim_linter.ClaimPolicyError, match="non-finite"):
        claim_linter.load_policy(non_finite_path, repository)


def test_source_and_evidence_hardlinks_fail_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    payload = _fixture_payload(repository)

    outside_source = _write(tmp_path, "outside-source.vue", "<p>LIVE monitor</p>\n")
    linked_source = repository / "frontend" / "src" / "PublicPage.vue"
    linked_source.hardlink_to(outside_source)
    policy = claim_linter.validate_policy(payload, repository)

    with pytest.raises(claim_linter.ClaimPolicyError, match="hard-linked"):
        claim_linter.scan_repository(repository, policy)

    linked_source.unlink()
    _write(repository, "frontend/src/PublicPage.vue", "<p>Ready = 3+ sources</p>\n")
    outside_evidence = _write(tmp_path, "outside-evidence.py", "READY_MINIMUM = 3\n")
    evidence_path = repository / "backend" / "method.py"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.hardlink_to(outside_evidence)
    payload["evidence_mappings"] = [
        {
            "source_locator": "frontend/src/PublicPage.vue",
            "rule_code": "CLM_SCALE_UNQUALIFIED",
            "claim_anchor_pattern": "Ready\\s*=\\s*3\\+",
            "classification": "evidence_backed",
            "evidence": [{"kind": "method", "locator": "backend/method.py"}],
        }
    ]

    with pytest.raises(claim_linter.ClaimPolicyError, match="hard-linked"):
        claim_linter.validate_policy(payload, repository)
