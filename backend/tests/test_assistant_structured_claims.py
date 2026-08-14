from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.features.assistant import (
    StructuredClaimError,
    bind_interactive_tool_result,
    finalize_interactive_output,
    finalize_structured_claim_output,
    interactive_source_records,
    verify_external_structured_claim_observation,
)

NOW = datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc)


def _tool_result() -> dict:
    return bind_interactive_tool_result(
        {
            "ok": True,
            "tool": "news_search",
            "news": [
                {
                    "id": "news-42",
                    "title": "Bounded current-turn result",
                    "abstract": "A bounded excerpt returned by this turn's tool.",
                }
            ],
        }
    )


def test_interactive_structured_claims_get_server_ids_and_safe_rendering() -> None:
    tool_result = _tool_result()
    source_id = interactive_source_records([tool_result])[0].source_id
    output = json.dumps(
        {
            "schema_version": "globemind.generated-claims.v1",
            "claims": [
                {
                    "statement": "The current-turn record supports this bounded statement.",
                    "disposition": "supported",
                    "citation_source_ids": [source_id],
                    "unknown_reason_code": None,
                },
                {
                    "statement": "Independent corroboration is not available.",
                    "disposition": "unknown",
                    "citation_source_ids": [],
                    "unknown_reason_code": "INDEPENDENT_SOURCE_NOT_AVAILABLE",
                },
            ],
        }
    )

    bounded = finalize_interactive_output(
        output,
        [tool_result],
        evidence_required=True,
        generation_complete=True,
    )

    assert bounded.assurance["status"] == "review_required"
    assert bounded.assurance["structured_claim_records"] == "available"
    assert bounded.assurance["claim_count"] == 2
    assert len(bounded.assurance["claim_ids"]) == 2
    assert all(value.startswith("GM-C-") for value in bounded.assurance["claim_ids"])
    assert bounded.assurance["claim_partition_state"] == (
        "model_declared_not_semantically_verified"
    )
    assert bounded.assurance["claims"][0]["statement_sha256"]
    assert "statement" not in bounded.assurance["claims"][0]
    source_record = interactive_source_records([tool_result])[0]
    assert bounded.assurance["claims"][0]["citation_source_bindings"] == [
        {
            "source_id": source_record.source_id,
            "binding_sha256": source_record.binding_sha256,
        }
    ]
    assert len(bounded.assurance["source_inventory_binding_sha256"]) == 64
    assert bounded.assurance["claim_id_binding_scope"] == (
        "ordinal_statement_sha256_and_exact_source_artifact_sha256"
    )
    assert bounded.assurance["claims"][0]["source_truth"] == "not_verified"
    assert "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM" not in (
        bounded.assurance["reason_codes"]
    )
    assert f"[{source_id}]" in bounded.content
    assert "[GM-UNKNOWN]" in bounded.content


def test_structured_claims_fail_closed_on_scope_and_disposition_drift() -> None:
    tool_result = _tool_result()
    for claim in (
        {
            "statement": "Out of scope source.",
            "disposition": "supported",
            "citation_source_ids": ["GM-T-FFFFFFFFFFFFFFFF"],
            "unknown_reason_code": None,
        },
        {
            "statement": "Unknown but improperly cited.",
            "disposition": "unknown",
            "citation_source_ids": [
                interactive_source_records([tool_result])[0].source_id
            ],
            "unknown_reason_code": "SOURCE_TRUTH_UNKNOWN",
        },
    ):
        bounded = finalize_interactive_output(
            json.dumps(
                {
                    "schema_version": "globemind.generated-claims.v1",
                    "claims": [claim],
                }
            ),
            [tool_result],
            evidence_required=True,
            generation_complete=True,
        )
        assert bounded.assurance["status"] == "blocked_replaced_unknown"
        assert bounded.assurance["claim_ids"] == []
        assert bounded.content.endswith("[GM-UNKNOWN]")


def test_structured_claims_reject_duplicate_keys_and_raw_markup() -> None:
    tool_result = _tool_result()
    duplicate = (
        '{"schema_version":"globemind.generated-claims.v1",'
        '"schema_version":"globemind.generated-claims.v1","claims":[]}'
    )
    raw_html = json.dumps(
        {
            "schema_version": "globemind.generated-claims.v1",
            "claims": [
                {
                    "statement": "<img src=x> unsafe",
                    "disposition": "unknown",
                    "citation_source_ids": [],
                    "unknown_reason_code": "SOURCE_TRUTH_UNKNOWN",
                }
            ],
        }
    )
    for output in (duplicate, raw_html):
        bounded = finalize_interactive_output(
            output,
            [tool_result],
            evidence_required=True,
            generation_complete=True,
        )
        assert bounded.assurance["status"] == "blocked_replaced_unknown"
        assert bounded.assurance["claim_ids"] == []


def test_legacy_markdown_remains_explicitly_unstructured() -> None:
    tool_result = _tool_result()
    source_id = interactive_source_records([tool_result])[0].source_id
    bounded = finalize_interactive_output(
        f"Legacy bounded block. [{source_id}]",
        [tool_result],
        evidence_required=True,
        generation_complete=True,
    )

    assert bounded.assurance["structured_claim_records"] == "not_available"
    assert bounded.assurance["claim_ids"] == []
    assert "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM" in bounded.assurance["reason_codes"]


def test_product_routes_require_structured_claim_output_and_legacy_fails_closed() -> None:
    route_source = Path("backend/api/routes/assistant.py").read_text(encoding="utf-8")

    assert route_source.count("require_structured_claims=True") == 4
    bounded = finalize_interactive_output(
        "Legacy text that must not cross the product route boundary.",
        (),
        evidence_required=False,
        generation_complete=True,
        require_structured_claims=True,
    )
    assert bounded.assurance["status"] == "blocked_replaced_unknown"
    assert bounded.assurance["reason_codes"] == ["STRUCTURED_CLAIM_OUTPUT_REQUIRED"]


def test_non_factual_structured_content_needs_no_fabricated_source() -> None:
    bounded = finalize_interactive_output(
        json.dumps(
            {
                "schema_version": "globemind.generated-claims.v1",
                "claims": [
                    {
                        "statement": "你好，我可以帮助你整理研究问题。",
                        "disposition": "non_factual",
                        "citation_source_ids": [],
                        "unknown_reason_code": None,
                    }
                ],
            }
        ),
        (),
        evidence_required=False,
        generation_complete=True,
        require_structured_claims=True,
    )

    assert bounded.assurance["structured_claim_records"] == "available"
    assert bounded.assurance["claims"][0]["disposition"] == "non_factual"
    assert bounded.assurance["claims"][0]["source_truth"] == "not_applicable"
    assert "[GM-UNKNOWN]" not in bounded.content


def test_claim_ids_bind_exact_source_artifact_hash_not_only_display_id() -> None:
    source_id = "GM-T-AAAAAAAAAAAAAAAA"
    output = json.dumps(
        {
            "schema_version": "globemind.generated-claims.v1",
            "claims": [
                {
                    "statement": "The exact source artifact supports this statement.",
                    "disposition": "supported",
                    "citation_source_ids": [source_id],
                    "unknown_reason_code": None,
                }
            ],
        }
    )

    first = finalize_structured_claim_output(
        output,
        source_bindings={source_id: "a" * 64},
    )
    second = finalize_structured_claim_output(
        output,
        source_bindings={source_id: "b" * 64},
    )

    assert first.metadata["claim_ids"] != second.metadata["claim_ids"]
    assert (
        first.metadata["source_inventory_binding_sha256"]
        != second.metadata["source_inventory_binding_sha256"]
    )
    assert first.metadata["claims"][0]["citation_source_bindings"] == [
        {"source_id": source_id, "binding_sha256": "a" * 64}
    ]
    with pytest.raises(StructuredClaimError, match="SOURCE_BINDINGS_INVALID"):
        finalize_structured_claim_output(
            output,
            source_bindings={source_id: "not-a-hash"},
        )


def _write_external_claim_observation(
    tmp_path: Path,
    *,
    claim_id_override: str | None = None,
) -> tuple[Path, str, Path]:
    source_id = "GM-T-AAAAAAAAAAAAAAAA"
    source_path = tmp_path / "source.json"
    source_body = b'{"bounded":"external source artifact"}'
    source_path.write_bytes(source_body)
    source_sha = hashlib.sha256(source_body).hexdigest()
    model_output = json.dumps(
        {
            "schema_version": "globemind.generated-claims.v1",
            "claims": [
                {
                    "statement": "The external source supports this bounded statement.",
                    "disposition": "supported",
                    "citation_source_ids": [source_id],
                    "unknown_reason_code": None,
                }
            ],
        }
    )
    structured = finalize_structured_claim_output(
        model_output,
        source_bindings={source_id: source_sha},
    )
    claim = structured.metadata["claims"][0]
    payload = {
        "schema_version": "globemind.external-structured-claim-observation.v1",
        "candidate_id": "isolated-candidate-001",
        "observed_at": NOW.isoformat(),
        "generation_artifact_sha256": hashlib.sha256(
            model_output.encode()
        ).hexdigest(),
        "source_inventory_binding_sha256": structured.metadata[
            "source_inventory_binding_sha256"
        ],
        "sources": [
            {
                "source_id": source_id,
                "artifact_locator": source_path.name,
                "artifact_sha256": source_sha,
            }
        ],
        "claims": [
            {
                "claim_id": claim_id_override or claim["claim_id"],
                "ordinal": claim["ordinal"],
                "statement_sha256": claim["statement_sha256"],
                "disposition": claim["disposition"],
                "citation_source_ids": claim["citation_source_ids"],
                "unknown_reason_code": claim["unknown_reason_code"],
            }
        ],
        "statement_bodies_retained": False,
        "source_bodies_retained": False,
    }
    path = tmp_path / "external-claims.json"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest(), source_path


def test_external_structured_claim_evidence_rehashes_sources_and_claim_ids(
    tmp_path: Path,
) -> None:
    path, digest, source_path = _write_external_claim_observation(tmp_path)

    receipt = verify_external_structured_claim_observation(
        path,
        expected_sha256=digest,
        evaluated_at=NOW + timedelta(minutes=1),
    )

    assert receipt.structure_verification == "passed"
    assert receipt.exact_source_artifact_hashes_verified is True
    assert receipt.claim_id_bindings_recomputed is True
    assert receipt.claim_count == 1
    assert receipt.statement_bodies_retained is False
    assert receipt.source_bodies_retained is False
    assert receipt.source_truth == "not_verified"
    assert receipt.semantic_entailment == "not_verified"
    assert receipt.release_decision == "not_computable"
    serialized = receipt.model_dump_json()
    assert source_path.read_text(encoding="utf-8") not in serialized
    assert "The external source supports" not in serialized


def test_external_structured_claim_evidence_rejects_source_and_binding_tampering(
    tmp_path: Path,
) -> None:
    path, digest, source_path = _write_external_claim_observation(tmp_path)
    source_path.write_bytes(b'{"bounded":"tampered"}')
    with pytest.raises(StructuredClaimError, match="SOURCE_SHA256_MISMATCH"):
        verify_external_structured_claim_observation(
            path,
            expected_sha256=digest,
            evaluated_at=NOW + timedelta(minutes=1),
        )

    with pytest.raises(StructuredClaimError, match="RELEASE_PATH_REJECTED"):
        verify_external_structured_claim_observation(
            Path("/root/data/releases/globemind/current/external-claims.json"),
            expected_sha256="a" * 64,
            evaluated_at=NOW + timedelta(minutes=1),
        )

    other = tmp_path / "binding"
    other.mkdir()
    path, digest, _ = _write_external_claim_observation(
        other,
        claim_id_override="GM-C-FFFFFFFFFFFFFFFFFFFF",
    )
    with pytest.raises(StructuredClaimError, match="ID_BINDING_MISMATCH"):
        verify_external_structured_claim_observation(
            path,
            expected_sha256=digest,
            evaluated_at=NOW + timedelta(minutes=1),
        )
