from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.search import (
    QUERY_RECEIPT_METHOD_VERSION,
    QueryReceiptIntegrityError,
    SearchSnapshotConflict,
    SearchSnapshotLedger,
    SearchSnapshotNotFound,
    SearchSnapshotUnavailable,
    build_query_receipt,
    verify_query_receipt,
)
from api.features.search import entities as entity_module
from api.features.search import snapshots as snapshot_module
from api.features.search.entities import ENTITY_ALIAS_CATALOG, resolve_entity_alias
from api.features.search.query_contract import normalize_and_validate_time_semantics
from api.features.search.receipts import canonical_sha256
from api.models.schemas import (
    EventCorefClusterInfo,
    NewsItem,
    NewsResultTimeSemantics,
    SearchRequest,
    SearchResponse,
)
from api.routes import search as search_routes
from api.services import news_search_v2
from api.services.auth import get_current_user_required

_START = datetime(2026, 8, 9, 1, 2, 3, tzinfo=timezone.utc)


def _response(*, ids: tuple[int, ...] = (9, 4), total: int = 20) -> SearchResponse:
    return SearchResponse(
        data=[
            NewsItem(
                id=item_id,
                title=f"Result {item_id}",
                body=f"body-must-never-be-snapshotted-{item_id}",
                pub_time=_START + timedelta(hours=index),
                time_semantics=NewsResultTimeSemantics(
                    published_at=_START + timedelta(hours=index)
                ),
            )
            for index, item_id in enumerate(ids)
        ],
        total=total,
        page=2,
        page_size=2,
        total_pages=10,
        has_next=True,
        has_prev=True,
        query_time_ms=12.5,
    )


def _receipt(*, ids: tuple[int, ...] = (9, 4), total: int = 20):
    params = SearchRequest(
        keyword='(China OR Japan) AND NOT "trade war"',
        time_field="published_at",
        start_time="2026-08-01T00:00:00Z",
        page=2,
        page_size=2,
        sort_by="pub_time",
        sort_order="desc",
    )
    normalize_and_validate_time_semantics(params, "news")
    response = _response(ids=ids, total=total)
    explain = news_search_v2._build_query_explain(params, total=response.total)
    return build_query_receipt(params, response, explain)


def _record_path(ledger: SearchSnapshotLedger, actor_id: int, snapshot_id: str) -> Path:
    return ledger.root / "users" / str(actor_id) / "records" / f"{snapshot_id}.json"


def test_query_receipt_is_deterministic_verifiable_and_truthfully_bounded() -> None:
    first = _receipt()
    same = _receipt()
    payload = verify_query_receipt(first)

    assert first == same
    assert payload["method_version"] == QUERY_RECEIPT_METHOD_VERSION
    assert payload["receipt_kind"] == "execution_receipt"
    assert payload["normalized_contract"]["query_language"] == "boolean-v1"
    assert payload["normalized_contract"]["query_ast"]["type"] == "and"
    assert payload["normalized_contract"]["pagination"] == {"page": 2, "page_size": 2}
    assert payload["entity_catalog_review_status"] == "review_required"
    assert payload["time_field"]["applied"] == "public.news.published_at"
    assert payload["ordered_returned_ids"] == ["9", "4"]
    assert payload["result_coverage"] == {
        "status": "available",
        "scope": "returned_page",
        "result_time_field": "public.news.published_at",
        "cutoff": "2026-08-09T02:02:03Z",
        "coverage_start": "2026-08-09T01:02:03Z",
        "coverage_end": "2026-08-09T02:02:03Z",
        "timed_result_count": 2,
        "returned_result_count": 2,
        "note": "Coverage is computed only from time values on this returned page, not from the corpus.",
    }
    assert payload["snapshot_status"] == "not_frozen"
    assert payload["frozen_data_snapshot_id"] is None
    assert "does not freeze article bodies" in payload["receipt_note"]

    changed_total = _receipt(total=21)
    assert changed_total.receipt_id == first.receipt_id
    assert changed_total.stable_execution_key == first.stable_execution_key
    assert changed_total.receipt_sha256 != first.receipt_sha256

    reordered = _receipt(ids=(4, 9))
    assert reordered.ordered_returned_ids_sha256 != first.ordered_returned_ids_sha256
    assert reordered.receipt_id != first.receipt_id


def test_query_receipt_rejects_hash_and_cross_field_tampering() -> None:
    payload = _receipt().model_dump(mode="json")
    payload["ordered_returned_ids"] = ["4", "9"]
    with pytest.raises(QueryReceiptIntegrityError, match="integrity"):
        verify_query_receipt(payload)

    payload = _receipt().model_dump(mode="json")
    payload["normalized_contract"]["pagination"]["page"] = 7
    payload["normalized_contract_sha256"] = canonical_sha256(payload["normalized_contract"])
    payload["stable_execution_key"] = canonical_sha256(
        {
            "method_version": payload["method_version"],
            "normalized_contract_sha256": payload["normalized_contract_sha256"],
            "entity_catalog_version": payload["entity_catalog_version"],
            "ordered_returned_ids_sha256": payload["ordered_returned_ids_sha256"],
        }
    )
    payload["receipt_id"] = f"qr-{payload['stable_execution_key']}"
    payload["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "receipt_sha256"}
    )
    with pytest.raises(QueryReceiptIntegrityError, match="integrity"):
        verify_query_receipt(payload)


def test_dashboard_search_attaches_a_receipt_to_the_main_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(news_search_v2, "_news_search_rows", lambda *_args: ([], 0))
    monkeypatch.setattr(
        news_search_v2,
        "_news_items_from_rows",
        lambda *_args, **_kwargs: [],
    )
    response = news_search_v2.search_dashboard_v2(
        SearchRequest(keyword="China", time_field="published_at"),
        user=None,
        app_db=None,
    )

    assert response.query_explain is not None
    assert response.query_receipt is not None
    assert verify_query_receipt(response.query_receipt)["result_id_namespace"] == "none"


def test_event_coref_mode_receipts_hash_the_actual_cluster_ids() -> None:
    params = SearchRequest(
        keyword="China",
        mode="event_coref",
        search_type="news",
        time_field="published_at",
    )
    normalize_and_validate_time_semantics(params, "news")
    response = SearchResponse(
        data=[],
        total=1,
        page=1,
        page_size=10,
        total_pages=1,
        has_next=False,
        has_prev=False,
        query_time_ms=1,
        event_coref_clusters=[
            EventCorefClusterInfo(
                cluster_id="event-7",
                article_count=2,
                start_date="2026-08-08",
                end_date="2026-08-09",
            )
        ],
    )
    explain = news_search_v2._build_query_explain(params, total=1)
    receipt = build_query_receipt(params, response, explain)

    assert receipt.result_id_namespace == "l1_event"
    assert receipt.ordered_returned_ids == ["event-7"]
    assert verify_query_receipt(receipt)["result_coverage"]["cutoff"] == "2026-08-09"


def test_entity_validity_and_review_lifecycle_do_not_invent_dates() -> None:
    assert ENTITY_ALIAS_CATALOG["default_valid_from"] is None
    assert ENTITY_ALIAS_CATALOG["default_valid_to"] is None
    assert set(ENTITY_ALIAS_CATALOG["review_lifecycle"]["statuses"]) == {
        "approved",
        "review_required",
    }
    assert all(entity["valid_from"] is None for entity in ENTITY_ALIAS_CATALOG["entities"])
    assert all(entity["valid_to"] is None for entity in ENTITY_ALIAS_CATALOG["entities"])
    china = resolve_entity_alias("China")
    assert china is not None
    assert china.valid_from is None
    assert china.valid_to is None
    assert china.review_status == "review_required"
    assert china.reviewed_at is None
    assert china.reviewed_by is None
    assert china.review_evidence is None


@pytest.mark.parametrize("mutation", ["missing_review", "inverted_validity"])
def test_entity_catalog_rejects_invalid_review_lifecycle_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    payload = deepcopy(ENTITY_ALIAS_CATALOG)
    entity = payload["entities"][0]
    if mutation == "missing_review":
        entity["review_status"] = "approved"
    else:
        entity["valid_from"] = "2026-08-10"
        entity["valid_to"] = "2026-08-09"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(entity_module, "_CATALOG_PATH", catalog)

    with pytest.raises(RuntimeError):
        entity_module._load_catalog()


def test_snapshot_reads_do_not_write_and_capture_persists_no_bodies(tmp_path: Path) -> None:
    root = tmp_path / "search-snapshots"
    ledger = SearchSnapshotLedger(root)

    assert ledger.list(7)["items"] == []
    with pytest.raises(SearchSnapshotNotFound):
        ledger.get(7, "search-snap-20260809T010203000000Z-0000000000000000")
    assert not root.exists()

    captured = ledger.capture(
        actor_id=7,
        receipt=_receipt(),
        expected_previous_snapshot_id=None,
        captured_at=_START,
    )
    record_text = _record_path(ledger, 7, captured["snapshot_id"]).read_text(encoding="utf-8")

    assert captured["previous_snapshot_id"] is None
    assert captured["previous_integrity_sha256"] is None
    assert captured["body_persistence"] == "forbidden"
    assert "body-must-never-be-snapshotted" not in record_text
    assert '"body":' not in record_text
    assert captured["receipt"]["ordered_returned_ids"] == ["9", "4"]


def test_snapshot_chain_uses_optimistic_previous_id_and_replay_is_ids_only(
    tmp_path: Path,
) -> None:
    ledger = SearchSnapshotLedger(tmp_path / "search-snapshots")
    first = ledger.capture(
        actor_id=7,
        receipt=_receipt(),
        expected_previous_snapshot_id=None,
        captured_at=_START,
    )
    with pytest.raises(SearchSnapshotConflict, match="latest search snapshot changed"):
        ledger.capture(
            actor_id=7,
            receipt=_receipt(ids=(8, 3)),
            expected_previous_snapshot_id=None,
            captured_at=_START + timedelta(seconds=1),
        )
    with pytest.raises(SearchSnapshotConflict, match="exact query receipt"):
        ledger.capture(
            actor_id=7,
            receipt=_receipt(),
            expected_previous_snapshot_id=first["snapshot_id"],
            captured_at=_START + timedelta(seconds=1),
        )

    second = ledger.capture(
        actor_id=7,
        receipt=_receipt(ids=(8, 3)),
        expected_previous_snapshot_id=first["snapshot_id"],
        captured_at=_START + timedelta(seconds=2),
    )
    assert second["previous_integrity_sha256"] == first["integrity_sha256"]
    assert ledger.list(7, limit=1)["items"][0]["snapshot_id"] == second["snapshot_id"]
    replay = ledger.replay(7, first["snapshot_id"])
    assert replay["replay_mode"] == "frozen_ids_only"
    assert replay["frozen_ordered_result_ids"] == ["9", "4"]
    assert replay["current_query_executed"] is False
    assert replay["difference_status"] == "not_compared"
    assert "current search results" in replay["difference_hints"][0]
    assert "data" not in replay


def test_snapshot_duplicate_keys_hard_links_and_hash_chain_tampering_fail_closed(
    tmp_path: Path,
) -> None:
    duplicate_ledger = SearchSnapshotLedger(tmp_path / "duplicates")
    duplicate = duplicate_ledger.capture(
        actor_id=7,
        receipt=_receipt(),
        expected_previous_snapshot_id=None,
        captured_at=_START,
    )
    duplicate_path = _record_path(duplicate_ledger, 7, duplicate["snapshot_id"])
    original = duplicate_path.read_text(encoding="utf-8")
    duplicate_path.write_text(
        original[:-1] + ',"schema_version":"search-snapshot-v1"}',
        encoding="utf-8",
    )
    with pytest.raises(SearchSnapshotUnavailable, match="duplicate JSON key"):
        duplicate_ledger.list(7)

    linked_ledger = SearchSnapshotLedger(tmp_path / "links")
    linked = linked_ledger.capture(
        actor_id=7,
        receipt=_receipt(),
        expected_previous_snapshot_id=None,
        captured_at=_START,
    )
    linked_path = _record_path(linked_ledger, 7, linked["snapshot_id"])
    os.link(linked_path, tmp_path / "record-hard-link.json")
    with pytest.raises(SearchSnapshotUnavailable, match="unsafe file type or link count"):
        linked_ledger.get(7, linked["snapshot_id"])

    chain_ledger = SearchSnapshotLedger(tmp_path / "chain")
    first = chain_ledger.capture(
        actor_id=7,
        receipt=_receipt(),
        expected_previous_snapshot_id=None,
        captured_at=_START,
    )
    chain_ledger.capture(
        actor_id=7,
        receipt=_receipt(ids=(8, 3)),
        expected_previous_snapshot_id=first["snapshot_id"],
        captured_at=_START + timedelta(seconds=1),
    )
    first_path = _record_path(chain_ledger, 7, first["snapshot_id"])
    rewritten = json.loads(first_path.read_text(encoding="utf-8"))
    rewritten["receipt"]["receipt_note"] += " rewritten"
    rewritten["receipt"]["receipt_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in rewritten["receipt"].items()
            if key != "receipt_sha256"
        }
    )
    rewritten["receipt_sha256"] = rewritten["receipt"]["receipt_sha256"]
    rewritten["integrity_sha256"] = canonical_sha256(
        {key: value for key, value in rewritten.items() if key != "integrity_sha256"}
    )
    first_path.write_text(json.dumps(rewritten), encoding="utf-8")
    with pytest.raises(SearchSnapshotUnavailable, match="record contract is invalid"):
        chain_ledger.list(7)


def test_snapshot_symlink_and_release_roots_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SearchSnapshotUnavailable, match="symbolic link"):
        SearchSnapshotLedger(link)

    release_root = tmp_path / "release-evidence"
    monkeypatch.setattr(snapshot_module, "_FORBIDDEN_RELEASE_ROOT", release_root)
    with pytest.raises(SearchSnapshotUnavailable, match="inside release evidence"):
        SearchSnapshotLedger(release_root / "current" / "snapshots")
    assert not release_root.exists()


def _route_app(user: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(search_routes.router)
    if user is not None:
        app.dependency_overrides[get_current_user_required] = lambda: user
    return app


def test_snapshot_routes_require_auth_and_never_write_on_get(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "route-snapshots"
    monkeypatch.setenv("SEARCH_SNAPSHOT_ROOT", str(root))
    assert TestClient(_route_app()).get("/api/search-snapshots").status_code == 401

    client = TestClient(_route_app({"user_id": 7, "role": "user"}))
    empty = client.get("/api/search-snapshots")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert not root.exists()

    receipt = _receipt().model_dump(mode="json")
    created = client.post(
        "/api/search-snapshots",
        json={"receipt": receipt, "expected_previous_snapshot_id": None},
    )
    assert created.status_code == 201
    snapshot = created.json()
    snapshot_id = snapshot["snapshot_id"]
    assert client.get(f"/api/search-snapshots/{snapshot_id}").status_code == 200
    replay = client.get(f"/api/search-snapshots/{snapshot_id}/replay")
    assert replay.status_code == 200
    assert replay.json()["current_query_executed"] is False

    other_user = TestClient(_route_app({"user_id": 8, "role": "user"}))
    assert other_user.get(f"/api/search-snapshots/{snapshot_id}").status_code == 404

    tampered = deepcopy(receipt)
    tampered["ordered_returned_ids"] = ["4", "9"]
    rejected = client.post(
        "/api/search-snapshots",
        json={
            "receipt": tampered,
            "expected_previous_snapshot_id": snapshot_id,
        },
    )
    assert rejected.status_code == 422


@pytest.mark.parametrize(
    "identity",
    (
        {},
        {"id": 7, "role": "user"},
        {"user_id": True, "role": "user"},
        {"user_id": 0, "role": "user"},
    ),
)
def test_snapshot_routes_reject_noncanonical_authenticated_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: dict,
) -> None:
    root = tmp_path / "route-snapshots"
    monkeypatch.setenv("SEARCH_SNAPSHOT_ROOT", str(root))
    response = TestClient(_route_app(identity)).get("/api/search-snapshots")

    assert response.status_code == 403
    assert not root.exists()
