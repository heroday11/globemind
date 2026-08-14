from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from api.features.search import entities as entity_catalog


def _catalog() -> dict[str, object]:
    return {
        "schema_version": 2,
        "catalog_version": "entity-aliases-test-v2",
        "default_review_status": "review_required",
        "default_review_note": "Synthetic fixture awaiting human review.",
        "catalog_review_status": "review_required",
        "curation_method": "ai_seed",
        "human_review_evidence": None,
        "default_valid_from": None,
        "default_valid_to": None,
        "accuracy_claim": "not_measured",
        "review_lifecycle": {
            "statuses": ["review_required", "approved"],
        },
        "entities": [
            {
                "entity_id": "urn:globemind:entity:country:CN",
                "entity_type": "country",
                "canonical_names": {"zh-Hans": "中国", "en": "China"},
                "aliases": [
                    {
                        "value": "China",
                        "language": "en",
                        "kind": "preferred_name",
                    }
                ],
            }
        ],
    }


def _load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    path = tmp_path / "entity-aliases.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(entity_catalog, "_CATALOG_PATH", path)
    return entity_catalog._load_catalog()


def test_catalog_json_rejects_duplicate_nonfinite_and_excessive_nesting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "entity-aliases.json"
    monkeypatch.setattr(entity_catalog, "_CATALOG_PATH", path)

    encoded = json.dumps(_catalog(), ensure_ascii=False)
    path.write_text(
        encoded.replace(
            '"catalog_version": "entity-aliases-test-v2"',
            '"catalog_version": "first", "catalog_version": "entity-aliases-test-v2"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="JSON"):
        entity_catalog._load_catalog()

    nonfinite = _catalog()
    nonfinite["unused_score"] = float("nan")
    path.write_text(json.dumps(nonfinite, allow_nan=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="JSON"):
        entity_catalog._load_catalog()

    path.write_text(
        '{"nested":' + ("[" * 1_100) + "0" + ("]" * 1_100) + "}",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="JSON"):
        entity_catalog._load_catalog()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["entities"][0].__setitem__(
            "entity_id", "urn:globemind:entity:country:C@"
        ),
        lambda payload: payload["entities"][0].__setitem__(
            "canonical_names", {"en": True}
        ),
        lambda payload: payload["entities"][0]["aliases"][0].__setitem__(
            "language", True
        ),
        lambda payload: payload["entities"][0]["aliases"][0].__setitem__(
            "kind", True
        ),
        lambda payload: payload["entities"][0].update(
            {
                "entity_id": "urn:globemind:entity:person:Xi-Jinping",
                "entity_type": "person",
            }
        ),
    ],
)
def test_catalog_rejects_unstable_ids_and_type_coercion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    payload = _catalog()
    mutate(payload)

    with pytest.raises(RuntimeError):
        _load(tmp_path, monkeypatch, payload)


def test_catalog_rejects_same_entity_alias_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _catalog()
    payload["entities"][0]["aliases"].append(
        {
            "value": "china",
            "language": "und",
            "kind": "alternative_name",
            "status": "context_dependent",
        }
    )

    with pytest.raises(RuntimeError, match="multiple alias records"):
        _load(tmp_path, monkeypatch, payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("accuracy_claim", "95_percent"),
        lambda payload: payload["entities"][0].__setitem__(
            "accuracy_claim", "95_percent"
        ),
        lambda payload: payload["entities"][0]["aliases"][0].__setitem__(
            "confidence", 0.95
        ),
        lambda payload: (
            payload.__setitem__("catalog_review_status", "approved"),
            payload.__setitem__("human_review_evidence", None),
        ),
        lambda payload: payload["entities"][0].update(
            {
                "review_status": "approved",
                "reviewed_at": "9999-01-01T00:00:00Z",
                "reviewed_by": "reviewer:7",
                "review_evidence": "https://example.test/review",
            }
        ),
        lambda payload: payload["entities"][0].update(
            {
                "review_status": "approved",
                "reviewed_at": "2026-08-09T00:00:00Z",
                "reviewed_by": "reviewer:7",
                "review_evidence": "https://user:secret@example.test/review?token=x#private",
            }
        ),
    ],
)
def test_catalog_rejects_unsubstantiated_or_unsafe_approval_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    payload = copy.deepcopy(_catalog())
    mutate(payload)

    with pytest.raises(RuntimeError):
        _load(tmp_path, monkeypatch, payload)


def test_alias_resolution_never_coerces_boolean_or_numeric_queries() -> None:
    assert entity_catalog.resolve_entity_alias(True) is None
    assert entity_catalog.resolve_entity_alias(1) is None
    assert entity_catalog.entity_alias_variants(False) == ()
    assert entity_catalog.entity_alias_variants(0) == ()
