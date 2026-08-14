from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features.evidence import (
    EvidenceLedgerConflict,
    EvidenceLedgerUnavailable,
    EvidenceSnapshotLedger,
)
from api.features.evidence import ledger as ledger_module
from api.routes import evidence_ledger
from api.services.auth import get_current_admin_user, get_current_user_required

_START = datetime(2026, 8, 9, 1, 2, 3, tzinfo=timezone.utc)


def _capture(
    ledger: EvidenceSnapshotLedger,
    *,
    body: str = "First paragraph.\n\nSecond paragraph.",
    change_type: str = "initial",
    claim_ids: tuple[str, ...] = ("article:42:claim-a", "article:42:claim-b"),
    expected_previous_event_id: str | None = None,
    source_url: str = "https://user:secret@example.test/story/42?token=private#section",
    at: datetime = _START,
):
    return ledger.capture(
        article_id=42,
        title="Evidence title",
        body=body,
        source_url=source_url,
        actor_id=7,
        reason="analyst requested an evidence capture",
        change_type=change_type,
        claim_ids=claim_ids,
        expected_previous_event_id=expected_previous_event_id,
        captured_at=at,
    )


def test_reads_do_not_create_storage_and_capture_is_content_addressed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    ledger = EvidenceSnapshotLedger(root)

    assert ledger.history(42)["items"] == []
    assert not root.exists()

    event = _capture(ledger)
    snapshot_id = event["snapshot_id"]
    metadata = ledger.snapshot(snapshot_id)
    full = ledger.snapshot(snapshot_id, include_body=True)

    assert root.is_dir()
    assert metadata["source_url"] == "https://example.test/story/42"
    assert "normalized_body" not in metadata
    assert full["normalized_body"] == "First paragraph.\n\nSecond paragraph."
    assert full["paragraph_count"] == 2
    assert event["previous_event_id"] is None
    assert event["content_changed"] is False
    assert event["impact_status"] == "none"


def test_revision_impact_is_append_only_and_human_review_is_separate(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger)
    original_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{initial['snapshot_id']}.json"
    )
    original_bytes = original_path.read_bytes()

    unchanged = _capture(
        ledger,
        change_type="update",
        expected_previous_event_id=initial["event_id"],
        at=_START + timedelta(minutes=1),
        claim_ids=("article:42:claim-b",),
    )
    assert unchanged["snapshot_id"] == initial["snapshot_id"]
    assert unchanged["content_changed"] is False
    assert unchanged["impact_status"] == "none"

    corrected = _capture(
        ledger,
        body="First paragraph corrected.\n\nSecond paragraph.",
        change_type="correction",
        expected_previous_event_id=unchanged["event_id"],
        at=_START + timedelta(minutes=2),
        claim_ids=("article:42:claim-c",),
    )
    assert corrected["content_changed"] is True
    assert corrected["impacted_claim_ids"] == ["article:42:claim-b"]
    assert corrected["impact_status"] == "review_required"

    review = ledger.review_impact(
        article_id=42,
        event_id=corrected["event_id"],
        actor_id=1,
        decision="modified",
        reason="only the surviving downstream claim requires review",
        impacted_claim_ids=("article:42:claim-b",),
        reviewed_at=_START + timedelta(minutes=3),
    )
    assert review["original_impacted_claim_ids"] == ["article:42:claim-b"]
    assert review["resolved_impacted_claim_ids"] == ["article:42:claim-b"]
    latest = ledger.history(42)["items"][0]
    assert latest["impact_review"]["status"] == "reviewed"
    assert latest["impact_review"]["latest"]["decision"] == "modified"
    assert original_path.read_bytes() == original_bytes


def test_revision_capture_uses_optimistic_identity_and_closed_change_semantics(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger)

    with pytest.raises(EvidenceLedgerConflict, match="latest evidence revision changed"):
        _capture(
            ledger,
            change_type="update",
            expected_previous_event_id="evt-20260809T010203000000Z-0000000000000000",
            at=_START + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="initial change is only valid"):
        _capture(
            ledger,
            change_type="initial",
            expected_previous_event_id=initial["event_id"],
            at=_START + timedelta(minutes=2),
        )


def test_tampered_duplicate_and_hard_linked_records_fail_closed(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    event = _capture(ledger)
    snapshot_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{event['snapshot_id']}.json"
    )
    original = snapshot_path.read_text(encoding="utf-8")

    snapshot_path.write_text(
        original[:-1] + ',"schema_version":"source-snapshot-v1"}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLedgerUnavailable, match="duplicate JSON key"):
        ledger.snapshot(event["snapshot_id"])

    snapshot_path.write_text(original, encoding="utf-8")
    linked = tmp_path / "linked-record.json"
    os.link(snapshot_path, linked)
    with pytest.raises(EvidenceLedgerUnavailable, match="unsafe link count"):
        ledger.snapshot(event["snapshot_id"])


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity", "1e400"))
def test_snapshot_json_rejects_non_finite_numbers(
    tmp_path: Path,
    constant: str,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    event = _capture(ledger)
    snapshot_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{event['snapshot_id']}.json"
    )
    original = snapshot_path.read_text(encoding="utf-8")
    snapshot_path.write_text(
        original[:-1] + f',"unsafe_number":{constant}' + "}",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceLedgerUnavailable, match="unreadable"):
        ledger.snapshot(event["snapshot_id"])


def test_snapshot_json_rejects_excessive_nesting_as_unavailable(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    event = _capture(ledger)
    snapshot_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{event['snapshot_id']}.json"
    )
    original = snapshot_path.read_text(encoding="utf-8")
    snapshot_path.write_text(
        original[:-1] + ',"deep":' + "[" * 1200 + "0" + "]" * 1200 + "}",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceLedgerUnavailable, match="unreadable"):
        ledger.snapshot(event["snapshot_id"])


def test_reads_reject_a_symlink_inserted_below_the_validated_root(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    event = _capture(ledger)
    articles = ledger.root / "articles"
    external_articles = tmp_path / "external-articles"
    articles.rename(external_articles)
    articles.symlink_to(external_articles, target_is_directory=True)

    with pytest.raises(EvidenceLedgerUnavailable, match="symbolic link"):
        ledger.history(42)
    with pytest.raises(EvidenceLedgerUnavailable, match="symbolic link"):
        ledger.snapshot(event["snapshot_id"])


def test_capture_rejects_a_hard_linked_lock_file(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    victim = tmp_path / "lock-victim"
    victim.write_text("do-not-lock", encoding="utf-8")
    os.link(victim, root / ".ledger.lock")

    with pytest.raises(EvidenceLedgerUnavailable, match="lock"):
        _capture(EvidenceSnapshotLedger(root))
    assert victim.read_text(encoding="utf-8") == "do-not-lock"


def test_revision_capture_requires_identity_and_strictly_increasing_time(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger, at=_START + timedelta(minutes=2))
    before = sorted(path.relative_to(ledger.root) for path in ledger.root.rglob("*.json"))

    with pytest.raises(ValueError, match="expected previous event"):
        _capture(
            ledger,
            body="A later body without optimistic identity.",
            change_type="update",
            at=_START + timedelta(minutes=3),
        )
    with pytest.raises(ValueError, match="later than the previous capture"):
        _capture(
            ledger,
            body="A backdated body.",
            change_type="update",
            expected_previous_event_id=initial["event_id"],
            at=_START + timedelta(minutes=1),
        )

    assert sorted(path.relative_to(ledger.root) for path in ledger.root.rglob("*.json")) == before


def test_capture_rejects_a_far_future_ledger_time_without_writing(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")

    with pytest.raises(ValueError, match="future"):
        _capture(ledger, at=datetime.now(timezone.utc) + timedelta(days=1))
    assert not ledger.root.exists()


def test_review_time_and_snapshot_binding_fail_closed(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger)
    correction = _capture(
        ledger,
        body="Corrected paragraph.",
        change_type="correction",
        expected_previous_event_id=initial["event_id"],
        at=_START + timedelta(minutes=2),
    )

    with pytest.raises(ValueError, match="review time"):
        ledger.review_impact(
            article_id=42,
            event_id=correction["event_id"],
            actor_id=1,
            decision="confirmed",
            reason="backdated review must be rejected",
            reviewed_at=_START + timedelta(minutes=1),
        )

    review = ledger.review_impact(
        article_id=42,
        event_id=correction["event_id"],
        actor_id=1,
        decision="confirmed",
        reason="confirm affected claims",
        reviewed_at=_START + timedelta(minutes=3),
    )
    review_path = (
        ledger.root
        / "articles"
        / "42"
        / "reviews"
        / correction["event_id"]
        / f"{review['review_id']}.json"
    )
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    payload["snapshot_id"] = f"article-42-{'0' * 64}"
    review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceLedgerUnavailable, match="review .*integrity|review contract"):
        ledger.history(42)


def test_record_hashes_detect_valid_looking_snapshot_event_and_review_tampering(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger)
    snapshot_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{initial['snapshot_id']}.json"
    )
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_payload["title"] = "A different but valid title"
    snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
    with pytest.raises(EvidenceLedgerUnavailable, match="integrity"):
        ledger.snapshot(initial["snapshot_id"])

    # Recreate a clean isolated chain for event and review tamper checks.
    second = EvidenceSnapshotLedger(tmp_path / "second-evidence")
    first_event = _capture(second)
    event_path = next((second.root / "articles" / "42" / "events").glob("*.json"))
    event_payload = json.loads(event_path.read_text(encoding="utf-8"))
    event_payload["reason"] = "a different valid analyst reason"
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")
    with pytest.raises(EvidenceLedgerUnavailable, match="integrity"):
        second.history(42)

    third = EvidenceSnapshotLedger(tmp_path / "third-evidence")
    original = _capture(third)
    correction = _capture(
        third,
        body="Corrected paragraph.",
        change_type="correction",
        expected_previous_event_id=original["event_id"],
        at=_START + timedelta(minutes=1),
    )
    review = third.review_impact(
        article_id=42,
        event_id=correction["event_id"],
        actor_id=1,
        decision="confirmed",
        reason="confirm affected claims",
        reviewed_at=_START + timedelta(minutes=2),
    )
    review_path = (
        third.root
        / "articles"
        / "42"
        / "reviews"
        / correction["event_id"]
        / f"{review['review_id']}.json"
    )
    review_payload = json.loads(review_path.read_text(encoding="utf-8"))
    review_payload["reason"] = "a different valid review reason"
    review_path.write_text(json.dumps(review_payload), encoding="utf-8")
    with pytest.raises(EvidenceLedgerUnavailable, match="integrity"):
        third.history(42)

    assert first_event["event_id"]


def test_corrupt_snapshot_binding_blocks_revision_append_before_any_new_record(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger)
    snapshot_path = (
        ledger.root
        / "articles"
        / "42"
        / "snapshots"
        / f"{initial['snapshot_id']}.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["title"] = "A coherently rewritten title"
    snapshot["record_sha256"] = ledger_module._record_sha256(snapshot)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    event_root = ledger.root / "articles" / "42" / "events"
    snapshot_root = ledger.root / "articles" / "42" / "snapshots"
    with pytest.raises(EvidenceLedgerUnavailable, match="snapshot binding"):
        _capture(
            ledger,
            body="A proposed later body.",
            change_type="update",
            expected_previous_event_id=initial["event_id"],
            at=_START + timedelta(minutes=1),
        )

    assert len(list(event_root.glob("*.json"))) == 1
    assert len(list(snapshot_root.glob("*.json"))) == 1


def test_same_body_cannot_silently_rebind_source_snapshot_metadata(tmp_path: Path) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    initial = _capture(ledger, source_url="https://example.test/original")

    with pytest.raises(EvidenceLedgerConflict, match="source metadata"):
        _capture(
            ledger,
            change_type="update",
            expected_previous_event_id=initial["event_id"],
            source_url="https://example.test/different",
            at=_START + timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "source_url",
    (
        "https://example.test/path with space",
        "https://example.test\\@evil.test/path",
        "https://example.test/path\tsegment",
    ),
)
def test_ambiguous_source_urls_are_rejected_before_storage(
    tmp_path: Path,
    source_url: str,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    with pytest.raises(ValueError, match="source URL"):
        _capture(ledger, source_url=source_url)
    assert not ledger.root.exists()


def test_duplicate_claim_input_cannot_bypass_the_raw_item_bound(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    with pytest.raises(ValueError, match="too many claim ids"):
        _capture(
            ledger,
            claim_ids=tuple("article:42:repeated" for _ in range(201)),
        )
    assert not ledger.root.exists()


def test_snapshot_source_url_preserves_an_unambiguous_ipv6_authority(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    event = _capture(
        ledger,
        source_url="https://[2001:db8::1]:8443/story?token=private#fragment",
    )

    snapshot = ledger.snapshot(event["snapshot_id"])
    assert snapshot["source_url"] == "https://[2001:db8::1]:8443/story"


def test_symlink_and_release_evidence_roots_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(EvidenceLedgerUnavailable, match="symbolic link"):
        EvidenceSnapshotLedger(link)

    release_root = tmp_path / "release-evidence"
    monkeypatch.setattr(ledger_module, "_FORBIDDEN_RELEASE_ROOT", release_root)
    with pytest.raises(EvidenceLedgerUnavailable, match="inside release evidence"):
        EvidenceSnapshotLedger(release_root / "current" / "snapshots")
    assert not release_root.exists()


def _route_app(*, user: dict | None = None, admin: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(evidence_ledger.router)
    if user is not None:
        app.dependency_overrides[get_current_user_required] = lambda: user
    if admin is not None:
        app.dependency_overrides[get_current_admin_user] = lambda: admin
    return app


def test_route_requires_auth_and_exposes_bounded_snapshot_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCE_SNAPSHOT_ROOT", str(tmp_path / "route-ledger"))
    article = SimpleNamespace(
        id=42,
        title="Country policy",
        body="Beijing announced the policy in parliament.",
        request_url="https://example.test/42?signature=secret",
    )
    monkeypatch.setattr(
        evidence_ledger,
        "get_news_by_id_v2",
        lambda article_id: article if article_id == 42 else None,
    )
    monkeypatch.setattr(
        evidence_ledger,
        "get_news_analysis_v2",
        lambda _article_id: {
            "event_extraction": {
                "initiator": "Beijing",
                "event_action": "announced",
                "target": "parliament",
                "processor_version": "test-event-v1",
            }
        },
    )

    anonymous = TestClient(_route_app())
    assert anonymous.get("/api/evidence-ledger/articles/42/history").status_code == 401

    app = _route_app(user={"user_id": 7, "role": "user"})
    client = TestClient(app)
    created = client.post(
        "/api/evidence-ledger/articles/42/captures",
        json={"reason": "capture paragraph evidence", "change_type": "initial"},
    )
    assert created.status_code == 201
    event = created.json()
    assert len(event["claim_ids"]) == 3

    metadata = client.get(f"/api/evidence-ledger/snapshots/{event['snapshot_id']}")
    assert metadata.status_code == 200
    assert "normalized_body" not in metadata.json()
    full = client.get(
        f"/api/evidence-ledger/snapshots/{event['snapshot_id']}?include_body=true"
    )
    assert full.status_code == 200
    assert full.json()["normalized_body"] == article.body

    missing_identity = client.post(
        "/api/evidence-ledger/articles/42/captures",
        json={"reason": "update snapshot", "change_type": "update"},
    )
    assert missing_identity.status_code == 422


def test_route_impact_review_requires_admin_and_maps_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIDENCE_SNAPSHOT_ROOT", str(tmp_path / "route-ledger"))
    ledger = EvidenceSnapshotLedger(tmp_path / "route-ledger")
    initial = _capture(ledger)
    correction = _capture(
        ledger,
        body="Corrected paragraph.",
        change_type="correction",
        expected_previous_event_id=initial["event_id"],
        at=_START + timedelta(minutes=1),
    )

    user_app = _route_app(user={"user_id": 7, "role": "user"})
    denied = TestClient(user_app).post(
        f"/api/evidence-ledger/articles/42/events/{correction['event_id']}/impact-reviews",
        json={"decision": "confirmed", "reason": "confirm affected claims"},
    )
    assert denied.status_code == 403

    admin_app = _route_app(
        user={"user_id": 1, "role": "admin"},
        admin={"user_id": 1, "role": "admin"},
    )
    reviewed = TestClient(admin_app).post(
        f"/api/evidence-ledger/articles/42/events/{correction['event_id']}/impact-reviews",
        json={"decision": "rejected", "reason": "no downstream claim remains affected"},
    )
    assert reviewed.status_code == 201
    assert reviewed.json()["resolved_impacted_claim_ids"] == []

    conflict = TestClient(admin_app).post(
        f"/api/evidence-ledger/articles/42/events/{initial['event_id']}/impact-reviews",
        json={"decision": "confirmed", "reason": "should not be reviewable"},
    )
    assert conflict.status_code == 409


def test_persisted_records_never_contain_source_credentials_or_query_secrets(
    tmp_path: Path,
) -> None:
    ledger = EvidenceSnapshotLedger(tmp_path / "evidence")
    _capture(ledger)
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ledger.root.rglob("*.json")
    )
    assert "secret" not in persisted
    assert "token" not in persisted
    assert "user:" in persisted  # actor reference is deliberately retained
    json.loads(next((ledger.root / "articles" / "42" / "events").glob("*.json")).read_text())
