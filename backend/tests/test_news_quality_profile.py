from __future__ import annotations

import json
import importlib.util
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from news_ingest_quality import (  # noqa: E402
    DEFAULT_PROFILE_MAX_ROWS,
    NEAR_DUPLICATE_METHOD_VERSION,
    QUALITY_PROFILE_METHOD_VERSION,
    compare_news_quality_profiles,
    profile_news_rows,
)

CLI_SPEC = importlib.util.spec_from_file_location(
    "profile_news_quality", PROJECT_ROOT / "scripts" / "profile_news_quality.py"
)
assert CLI_SPEC is not None and CLI_SPEC.loader is not None
CLI_MODULE = importlib.util.module_from_spec(CLI_SPEC)
sys.modules[CLI_SPEC.name] = CLI_MODULE
CLI_SPEC.loader.exec_module(CLI_MODULE)

COMPARE_CLI_SPEC = importlib.util.spec_from_file_location(
    "compare_news_quality_profiles_cli",
    PROJECT_ROOT / "scripts" / "compare_news_quality_profiles.py",
)
assert COMPARE_CLI_SPEC is not None and COMPARE_CLI_SPEC.loader is not None
COMPARE_CLI_MODULE = importlib.util.module_from_spec(COMPARE_CLI_SPEC)
sys.modules[COMPARE_CLI_SPEC.name] = COMPARE_CLI_MODULE
COMPARE_CLI_SPEC.loader.exec_module(COMPARE_CLI_MODULE)


NOW = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)


def _article(*, suffix: str = "one", **overrides):
    row = {
        "title": f"Verified policy announcement {suffix}",
        "body": (
            "Officials published a detailed policy statement with dates, scope, "
            "and attributed comments for researchers to inspect. "
        )
        * 3,
        "url": f"https://example.com/news/{suffix}?tracking=removed#fragment",
        "published_at": "2026-08-08T12:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_profile_is_bounded_aggregate_and_does_not_retain_content():
    secret_marker = "PRIVATE-ARTICLE-CONTENT-MUST-NOT-LEAK"
    rows = [
        _article(body=(secret_marker + " legitimate report text ") * 8),
        _article(
            suffix="bad",
            title="Privacy Policy",
            body="Enable JavaScript",
            published_at=None,
        ),
    ]

    profile = profile_news_rows(rows, now=NOW)
    serialized = json.dumps(profile, ensure_ascii=False)

    assert profile["scope"] == {
        "evaluated_rows": 2,
        "max_rows": 100_000,
        "truncated": False,
        "article_content_retained": False,
        "row_identifiers_retained": False,
    }
    assert profile["labels"]["good_count"] == 1
    assert profile["labels"]["bad_count"] == 1
    assert {item["code"] for item in profile["reason_counts"]} >= {
        "body_too_short",
        "missing_published_at",
        "page_like_title",
        "placeholder_body",
    }
    assert secret_marker not in serialized
    assert "https://example.com" not in serialized
    assert profile["assurance"]["release_decision"] == "not_computable"
    assert profile["assurance"]["threshold_approval_state"] == "not_approved"


def test_profile_reports_exact_url_and_content_duplicates_without_fingerprints():
    first = _article()
    second = _article(url="https://EXAMPLE.com:443/news/one/?another=query")
    profile = profile_news_rows([first, second], now=NOW)

    assert profile["exact_duplicates"]["url"] == {
        "duplicate_groups": 1,
        "duplicate_rows": 2,
        "excess_rows": 1,
    }
    assert profile["exact_duplicates"]["normalized_content"] == {
        "duplicate_groups": 1,
        "duplicate_rows": 2,
        "excess_rows": 1,
    }
    assert "fingerprint" not in json.dumps(profile)


def test_profile_reports_bounded_near_duplicate_candidates_without_evidence_leakage():
    secret = "PRIVATE-NEAR-DUPLICATE-CONTENT-MUST-NOT-LEAK"
    base_body = (
        "Officials described the fiscal policy implementation schedule, regional "
        "scope, oversight process, and publication dates in a detailed briefing. "
    ) * 8
    rows = [
        _article(suffix="base", body=f"{secret} {base_body}"),
        _article(
            suffix="edited",
            body=f"{secret} {base_body.replace('fiscal', 'budgetary', 1)}",
        ),
        _article(
            suffix="exact-copy",
            title="Verified policy announcement base",
            body=f"{secret} {base_body}",
        ),
    ]

    profile = profile_news_rows(rows, now=NOW)
    observation = profile["near_duplicate_candidates"]
    serialized = json.dumps(profile, ensure_ascii=False)

    assert observation["method_version"] == NEAR_DUPLICATE_METHOD_VERSION
    assert observation["observation_state"] == "candidate_pairs_only"
    assert observation["profile_evaluated_rows"] == 3
    assert observation["evaluated_rows"] == 3
    assert observation["row_evaluation_limit"] == 20_000
    assert observation["row_evaluation_truncated"] is False
    assert observation["profile_scope_truncated"] is False
    assert observation["eligible_rows"] == 3
    assert observation["ineligible_low_information_rows"] == 0
    assert observation["candidate_pairs_compared"] <= observation[
        "candidate_pair_comparison_limit"
    ]
    assert observation["candidate_pairs_observed"] >= 1
    assert observation["exact_duplicate_pairs_excluded"] >= 1
    assert observation["comparison_overflow"] is False
    assert observation["candidate_threshold_approval_state"] == "not_approved"
    assert observation["human_review_state"] == "not_provided"
    assert observation["duplicate_fact_state"] == "not_established"
    assert observation["release_decision"] == "not_computable"
    assert observation["article_content_retained"] is False
    assert observation["urls_or_row_identifiers_retained"] is False
    assert secret not in serialized
    assert "https://example.com/news" not in serialized
    assert "signature" not in serialized


def test_near_duplicate_candidate_budget_is_bounded_and_rejects_boolean_limit():
    common = (
        "A sufficiently detailed multilingual-compatible policy report contains "
        "dates institutions geographic scope and attributed statements. "
    ) * 8
    rows = [
        _article(suffix=f"variant-{index}", body=f"{common} revision {index}")
        for index in range(8)
    ]

    profile = profile_news_rows(
        rows,
        now=NOW,
        max_candidate_pair_comparisons=1,
    )
    observation = profile["near_duplicate_candidates"]

    assert observation["candidate_pairs_compared"] == 1
    assert observation["candidate_pair_comparison_limit"] == 1
    assert observation["comparison_overflow"] is True
    assert observation["candidate_pairs_skipped_at_least"] >= 1

    with pytest.raises(ValueError, match="candidate pair.*positive integer"):
        profile_news_rows(
            rows,
            now=NOW,
            max_candidate_pair_comparisons=True,
        )
    with pytest.raises(ValueError, match="candidate pair.*no greater than"):
        profile_news_rows(
            rows,
            now=NOW,
            max_candidate_pair_comparisons=1_000_001,
        )


def test_near_duplicate_bucket_overflow_is_explicit_and_not_a_duplicate_fact():
    row = _article(
        suffix="same",
        body=(
            "A detailed public policy record describes dates, institutions, "
            "oversight, regional scope, and attributed statements. "
        )
        * 8,
    )
    profile = profile_news_rows([dict(row) for _ in range(70)], now=NOW)
    observation = profile["near_duplicate_candidates"]

    assert observation["candidate_generation_bucket_row_limit"] == 64
    assert observation["candidate_generation_bucket_overflow_events"] > 0
    assert observation["candidate_generation_overflow"] is True
    assert observation["candidate_pairs_observed"] == 0
    assert observation["exact_duplicate_pairs_excluded"] > 0
    assert observation["duplicate_fact_state"] == "not_established"


def test_near_duplicate_row_evaluation_limit_reports_truncation():
    empty_row = {"title": "", "body": "", "url": "", "published_at": None}
    profile = profile_news_rows(
        (dict(empty_row) for _ in range(20_001)),
        now=NOW,
        max_rows=30_000,
    )
    observation = profile["near_duplicate_candidates"]

    assert observation["profile_evaluated_rows"] == 20_001
    assert observation["evaluated_rows"] == 20_000
    assert observation["row_evaluation_limit"] == 20_000
    assert observation["row_evaluation_truncated"] is True
    assert observation["profile_scope_truncated"] is False
    assert observation["ineligible_low_information_rows"] == 20_000


def test_near_duplicate_low_information_and_malicious_unicode_fail_safe():
    profile = profile_news_rows(
        [
            _article(suffix="space", title=" \t\n", body=" \t\n"),
            _article(suffix="repeat", title="界" * 20_000, body="界" * 20_000),
            _article(suffix="surrogate", title="bad\ud800title", body="bad\udfffbody"),
        ],
        now=NOW,
    )

    observation = profile["near_duplicate_candidates"]
    assert observation["evaluated_rows"] == 3
    assert observation["eligible_rows"] == 0
    assert observation["ineligible_low_information_rows"] == 3
    assert observation["text_truncated_rows"] == 1
    assert observation["candidate_pairs_compared"] == 0
    assert observation["candidate_pairs_observed"] == 0
    assert observation["comparison_overflow"] is False


def test_profile_is_bounded_and_validates_row_and_limit_types():
    profile = profile_news_rows(
        [_article(suffix="one"), _article(suffix="two")],
        now=NOW,
        max_rows=1,
    )

    assert profile["scope"]["evaluated_rows"] == 1
    assert profile["scope"]["truncated"] is True

    with pytest.raises(ValueError, match="positive integer"):
        profile_news_rows([], now=NOW, max_rows=True)
    with pytest.raises(TypeError, match="must be an object"):
        profile_news_rows(["not-an-object"], now=NOW)


def test_profile_exposes_real_publication_cutoff_and_no_release_claim():
    profile = profile_news_rows(
        [
            _article(published_at="2026-08-01T12:00:00Z"),
            _article(suffix="two", published_at="2026-08-08T23:00:00Z"),
            _article(suffix="future", published_at="2027-01-01T00:00:00Z"),
        ],
        now=NOW,
    )

    assert profile["publication_time"] == {
        "valid_count": 2,
        "earliest_at": "2026-08-01T12:00:00+00:00",
        "cutoff_at": "2026-08-08T23:00:00+00:00",
    }
    assert profile["labels"]["gold_standard_state"] == "not_provided"
    assert profile["assurance"]["human_label_review"] == "not_provided"


def test_profile_exposes_bounded_source_language_and_month_slices():
    rows = [
        _article(
            suffix="english-one",
            language="EN_us",
            published_at="2026-07-31T23:00:00Z",
            url="https://news.example.com/world/english-one",
        ),
        _article(
            suffix="english-two",
            language="en-US",
            published_at="2026-08-01T01:00:00Z",
            url="https://news.example.com/world/english-two",
            body="short",
        ),
        _article(
            suffix="chinese",
            language="zh-CN",
            published_at="2026-08-02T01:00:00Z",
            url="https://cn.example.org/news/chinese",
        ),
        _article(
            suffix="private",
            language="secret user supplied value",
            url="http://localhost/private",
        ),
    ]

    profile = profile_news_rows(rows, now=NOW)

    assert profile["slices"]["source_domain"] == {
        "items": [
            {
                "value": "news.example.com",
                "evaluated_rows": 2,
                "good_count": 1,
                "bad_count": 1,
                "bad_rate": 0.5,
            },
            {
                "value": "cn.example.org",
                "evaluated_rows": 1,
                "good_count": 1,
                "bad_count": 0,
                "bad_rate": 0.0,
            },
        ],
        "distinct_values": 2,
        "overflow_values": 0,
        "overflow_rows": 0,
        "value_policy": "public_dns_hostname_only",
    }
    assert profile["slices"]["language"]["items"] == [
        {
            "value": "en-us",
            "evaluated_rows": 2,
            "good_count": 1,
            "bad_count": 1,
            "bad_rate": 0.5,
        },
        {
            "value": "invalid",
            "evaluated_rows": 1,
            "good_count": 0,
            "bad_count": 1,
            "bad_rate": 1.0,
        },
        {
            "value": "zh-cn",
            "evaluated_rows": 1,
            "good_count": 1,
            "bad_count": 0,
            "bad_rate": 0.0,
        },
    ]
    assert profile["slices"]["publication_month"]["items"] == [
        {
                "value": "2026-08",
                "evaluated_rows": 3,
                "good_count": 1,
                "bad_count": 2,
                "bad_rate": 0.666667,
        },
        {
            "value": "2026-07",
            "evaluated_rows": 1,
            "good_count": 1,
            "bad_count": 0,
            "bad_rate": 0.0,
        },
    ]
    assert "secret user supplied value" not in json.dumps(profile)
    assert "localhost" not in json.dumps(profile)


def test_profile_reports_schema_drift_without_retaining_unknown_field_names():
    secret_key = "private_customer_identifier"
    profile = profile_news_rows(
        [_article(**{secret_key: "must-not-leak"}), _article(suffix="two")],
        now=NOW,
    )

    assert profile["schema_observation"] == {
        "known_field_set_version": "news-article-fields-v2",
        "rows_with_unknown_fields": 1,
        "unknown_field_occurrences": 1,
        "distinct_unknown_fields": 1,
        "unknown_field_names_retained": False,
        "drift_assessment": "observed_unreviewed",
    }
    serialized = json.dumps(profile)
    assert secret_key not in serialized
    assert "must-not-leak" not in serialized


def test_profile_slice_cardinality_is_bounded_and_reports_overflow():
    profile = profile_news_rows(
        [
            _article(
                suffix=f"row-{index}",
                url=f"https://source-{index:03d}.example/news/row",
            )
            for index in range(70)
        ],
        now=NOW,
    )

    source_slice = profile["slices"]["source_domain"]
    assert len(source_slice["items"]) == 64
    assert source_slice["distinct_values"] == 70
    assert source_slice["overflow_values"] == 6
    assert source_slice["overflow_rows"] == 6


def test_profile_comparison_measures_deltas_but_never_invents_thresholds():
    baseline = profile_news_rows(
        [_article(), _article(suffix="bad", body="short")], now=NOW
    )
    current = profile_news_rows(
        [_article(), _article(suffix="two")], now=NOW
    )

    comparison = compare_news_quality_profiles(baseline, current)

    assert comparison["schema_version"] == "news-quality-profile-comparison-v3"
    assert comparison["method_version"] == QUALITY_PROFILE_METHOD_VERSION
    assert comparison["good_rate_delta"] == 0.5
    assert comparison["comparison_state"] == "observed_only"
    assert comparison["threshold_approval_state"] == "not_approved"
    assert comparison["release_decision"] == "not_computable"
    assert {
        item["code"]: item["delta"] for item in comparison["reason_rate_deltas"]
    }["body_too_short"] == -0.5
    assert comparison["volume_observation"] == {
        "baseline_evaluated_rows": 2,
        "current_evaluated_rows": 2,
        "evaluated_rows_delta": 0,
        "evaluated_rows_ratio": 1.0,
        "interruption_assessment": "not_computable",
        "approved_expected_volume": None,
    }
    assert comparison["publication_cutoff_observation"] == {
        "baseline_cutoff_at": "2026-08-08T12:00:00+00:00",
        "current_cutoff_at": "2026-08-08T12:00:00+00:00",
        "cutoff_delta_seconds": 0.0,
        "freshness_assessment": "not_computable",
        "approved_cadence": None,
    }
    assert comparison["near_duplicate_candidate_observation"] == {
        "method_version": NEAR_DUPLICATE_METHOD_VERSION,
        "baseline_evaluated_rows": 2,
        "current_evaluated_rows": 2,
        "baseline_candidate_pairs_observed": 0,
        "current_candidate_pairs_observed": 1,
        "candidate_pairs_observed_delta": 1,
        "comparison_state": "observed_candidates_only",
        "comparability_state": "not_established",
        "baseline_observation_overflow": False,
        "current_observation_overflow": False,
        "candidate_threshold_approval_state": "not_approved",
        "human_review_state": "not_provided",
        "duplicate_fact_state": "not_established",
        "release_decision": "not_computable",
    }


def test_empty_profile_preserves_null_rates():
    profile = profile_news_rows([], now=NOW)

    assert profile["labels"]["good_rate"] is None
    assert profile["labels"]["bad_rate"] is None
    assert profile["publication_time"]["cutoff_at"] is None
    assert profile["assurance"]["evaluation_state"] == "not_observed"


def test_comparison_rejects_unknown_contract_versions():
    baseline = profile_news_rows([_article()], now=NOW)
    current = profile_news_rows([_article()], now=NOW)
    current["method_version"] = "untrusted-method"

    with pytest.raises(ValueError, match="current profile method"):
        compare_news_quality_profiles(baseline, current)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda profile: profile["labels"].__setitem__("good_count", 2),
            "label counts",
        ),
        (
            lambda profile: profile["assurance"].__setitem__(
                "release_decision", "eligible"
            ),
            "release decision",
        ),
        (
            lambda profile: profile["reason_counts"].append(
                dict(profile["reason_counts"][0])
            ),
            "duplicate reason code",
        ),
        (
            lambda profile: profile["scope"].__setitem__(
                "max_rows", DEFAULT_PROFILE_MAX_ROWS + 1
            ),
            "scope row bound",
        ),
    ],
)
def test_comparison_rejects_tampered_profile_contracts(mutator, message):
    baseline = profile_news_rows([_article(body="short")], now=NOW)
    current = deepcopy(baseline)
    mutator(current)

    with pytest.raises(ValueError, match=message):
        compare_news_quality_profiles(baseline, current)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda profile: profile["near_duplicate_candidates"].__setitem__(
                "candidate_pairs_compared", 200_001
            ),
            "near-duplicate comparison count",
        ),
        (
            lambda profile: profile["near_duplicate_candidates"].__setitem__(
                "duplicate_fact_state", "confirmed"
            ),
            "duplicate fact state",
        ),
        (
            lambda profile: profile["near_duplicate_candidates"].__setitem__(
                "candidate_threshold_approval_state", "approved"
            ),
            "candidate threshold approval",
        ),
        (
            lambda profile: profile["exact_duplicates"].__setitem__(
                "near_duplicate_state", "confirmed_duplicates"
            ),
            "near-duplicate state",
        ),
    ],
)
def test_comparison_rejects_tampered_near_duplicate_contract(mutator, message):
    baseline = profile_news_rows([_article()], now=NOW)
    current = deepcopy(baseline)
    mutator(current)

    with pytest.raises(ValueError, match=message):
        compare_news_quality_profiles(baseline, current)


def test_jsonl_loader_rejects_duplicate_keys_and_non_finite_numbers(tmp_path: Path):
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text('{"title":"one","title":"two"}\n', encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="duplicate JSON key"):
        CLI_MODULE.load_jsonl(duplicate, max_rows=10)

    non_finite = tmp_path / "non-finite.jsonl"
    non_finite.write_text('{"score":NaN}\n', encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="non-finite"):
        CLI_MODULE.load_jsonl(non_finite, max_rows=10)

    overflow = tmp_path / "overflow.jsonl"
    overflow.write_text('{"score":1e400}\n', encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="non-finite"):
        CLI_MODULE.load_jsonl(overflow, max_rows=10)

    deeply_nested = tmp_path / "deep.jsonl"
    deeply_nested.write_text(
        '{"extra":' + "[" * 40 + "0" + "]" * 40 + "}\n",
        encoding="utf-8",
    )
    with pytest.raises(CLI_MODULE.ProfileInputError, match="nesting depth"):
        CLI_MODULE.load_jsonl(deeply_nested, max_rows=10)


def test_report_write_is_no_replace_and_content_free(tmp_path: Path):
    report = tmp_path / "report.json"
    profile = profile_news_rows([_article()], now=NOW)

    CLI_MODULE.write_json_no_replace(report, profile)

    assert report.stat().st_mode & 0o777 == 0o640
    assert json.loads(report.read_text(encoding="utf-8"))["schema_version"] == (
        "news-quality-profile-v3"
    )
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        CLI_MODULE.write_json_no_replace(report, profile)


def test_comparison_cli_loader_is_bounded_and_rejects_tampered_json(tmp_path: Path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(profile_news_rows([_article()], now=NOW)), encoding="utf-8"
    )

    loaded = COMPARE_CLI_MODULE.load_profile_json(profile_path)
    assert loaded["schema_version"] == "news-quality-profile-v3"

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"one","schema_version":"two"}', encoding="utf-8"
    )
    with pytest.raises(CLI_MODULE.ProfileInputError, match="duplicate JSON key"):
        COMPARE_CLI_MODULE.load_profile_json(duplicate)

    overflow = tmp_path / "overflow.json"
    overflow.write_text('{"extra":1e400}', encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="non-finite"):
        COMPARE_CLI_MODULE.load_profile_json(overflow)

    deeply_nested = tmp_path / "deep.json"
    deeply_nested.write_text("[" * 40 + "{}" + "]" * 40, encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="nesting depth"):
        COMPARE_CLI_MODULE.load_profile_json(deeply_nested)

    with pytest.raises(CLI_MODULE.ProfileInputError, match="byte limit"):
        COMPARE_CLI_MODULE.load_profile_json(profile_path, max_profile_bytes=1)
    with pytest.raises(CLI_MODULE.ProfileInputError, match="hard limit"):
        COMPARE_CLI_MODULE.load_profile_json(
            profile_path,
            max_profile_bytes=COMPARE_CLI_MODULE.DEFAULT_MAX_PROFILE_BYTES + 1,
        )


def test_comparison_artifact_is_content_free_and_no_replace(tmp_path: Path):
    baseline = profile_news_rows([_article(body="short")], now=NOW)
    current = profile_news_rows([_article()], now=NOW)
    output = tmp_path / "comparison.json"

    comparison = compare_news_quality_profiles(baseline, current)
    CLI_MODULE.write_json_no_replace(output, comparison)

    serialized = output.read_text(encoding="utf-8")
    written = json.loads(serialized)
    assert written["schema_version"] == "news-quality-profile-comparison-v3"
    assert written["release_decision"] == "not_computable"
    assert "Verified policy announcement" not in serialized
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        CLI_MODULE.write_json_no_replace(output, comparison)


def test_profile_cli_guards_release_artifact_paths():
    with pytest.raises(CLI_MODULE.ProfileInputError, match="release artifact"):
        CLI_MODULE._assert_safe_path(
            Path("/root/data/releases/globemind/current/evidence.jsonl"),
            must_exist=False,
        )


def test_profile_cli_rejects_invalid_candidate_pair_budget(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_news_quality.py",
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--output",
            str(tmp_path / "output.json"),
            "--max-candidate-pairs",
            "0",
        ],
    )

    with pytest.raises(
        CLI_MODULE.ProfileInputError,
        match="--max-candidate-pairs must be a positive integer",
    ):
        CLI_MODULE.main()


def test_profile_cli_help_names_the_actual_near_duplicate_method_family(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(sys, "argv", ["profile_news_quality.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        CLI_MODULE.parse_args()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    normalized_help = " ".join(help_text.split())
    assert "near-duplicate candidate-pair comparison budget" in normalized_help
    assert "SimHash" not in help_text


def test_profile_row_and_input_limits_have_non_overridable_hard_caps(tmp_path: Path):
    with pytest.raises(ValueError, match="max_rows.*no greater than"):
        profile_news_rows([], now=NOW, max_rows=DEFAULT_PROFILE_MAX_ROWS + 1)

    source = tmp_path / "one.jsonl"
    source.write_text(json.dumps(_article()) + "\n", encoding="utf-8")
    with pytest.raises(CLI_MODULE.ProfileInputError, match="max_rows.*hard limit"):
        CLI_MODULE.load_jsonl(
            source,
            max_rows=DEFAULT_PROFILE_MAX_ROWS + 1,
        )
    with pytest.raises(CLI_MODULE.ProfileInputError, match="input byte limit.*hard limit"):
        CLI_MODULE.load_jsonl(
            source,
            max_rows=1,
            max_input_bytes=CLI_MODULE.DEFAULT_MAX_INPUT_BYTES + 1,
        )
    with pytest.raises(CLI_MODULE.ProfileInputError, match="line byte limit.*hard limit"):
        CLI_MODULE.load_jsonl(
            source,
            max_rows=1,
            max_line_bytes=CLI_MODULE.DEFAULT_MAX_LINE_BYTES + 1,
        )
