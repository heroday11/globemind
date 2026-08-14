from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from api.features.entity_governance import (
    EntityGovernanceConflict,
    EntityGovernanceLedger,
    EntityGovernanceUnavailable,
    EntityGovernanceService,
    load_search_seed_entities,
)


KEY = b"entity-governance-test-hmac-key-0001"


class AdvancingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(microseconds=1)
        return value


def _ledger(root: Path, *, key: bytes = KEY) -> EntityGovernanceLedger:
    return EntityGovernanceLedger(
        root,
        key,
        clock=AdvancingClock(),
        nonce_factory=lambda: "b" * 16,
    )


def _evidence() -> dict[str, object]:
    digest = "c" * 64
    return {
        "verification_status": "verified",
        "schema_version": "source-snapshot-v1",
        "snapshot_id": f"article-3-{digest}",
        "article_id": 3,
        "content_sha256": digest,
        "parser_version": "article-display-v1",
        "verification_scope": "normalized-body-content-address-and-reference-fields",
        "source_metadata_verification": "not_measured",
        "body_persistence": "forbidden",
    }


def _append(
    ledger: EntityGovernanceLedger,
    previous: str | None = None,
    *,
    entity_id: str = "urn:globemind:entity:country:CN",
) -> dict[str, object]:
    return ledger.append(
        actor_id=9,
        event_type="entity.decision",
        reason="Human reviewer approves a bounded seed entity",
        evidence=_evidence(),
        payload={
            "entity_id": entity_id,
            "decision": "approve",
            "valid_from": None,
            "valid_to": None,
        },
        expected_previous_event_id=previous,
    )


def _only_event_path(root: Path) -> Path:
    return next((root / "events").glob("*.json"))


def test_constructor_records_and_history_on_absent_root_are_zero_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "absent"
    ledger = _ledger(root)

    assert ledger.records() == []
    assert ledger.history() == {
        "schema_version": "entity-governance-history-v1",
        "event_count": 0,
        "items": [],
    }
    assert not root.exists()


def test_constructor_rejects_relative_short_key_and_symbolic_roots(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_ROOT_MUST_BE_ABSOLUTE",
    ):
        EntityGovernanceLedger(Path("relative/governance"), KEY)
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_HMAC_KEY_UNAVAILABLE",
    ):
        EntityGovernanceLedger(tmp_path / "short-key", b"short")

    actual = tmp_path / "actual"
    actual.mkdir()
    symbolic = tmp_path / "symbolic"
    symbolic.symlink_to(actual, target_is_directory=True)
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_ROOT_SYMLINK_REJECTED",
    ):
        EntityGovernanceLedger(symbolic, KEY)

    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "does-not-exist", target_is_directory=True)
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_ROOT_SYMLINK_REJECTED",
    ):
        EntityGovernanceLedger(broken, KEY)


def test_append_fsync_chain_and_optimistic_concurrency(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    ledger = _ledger(root)
    first = _append(ledger)
    second = _append(
        ledger,
        str(first["event_id"]),
        entity_id="urn:globemind:entity:country:US",
    )

    records = ledger.records()
    assert records == [first, second]
    assert second["previous_event_id"] == first["event_id"]
    assert second["previous_record_sha256"] == first["record_sha256"]
    assert second["previous_chain_hmac_sha256"] == first["chain_hmac_sha256"]
    assert all(len(str(item["record_sha256"])) == 64 for item in records)
    assert all(len(str(item["chain_hmac_sha256"])) == 64 for item in records)
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert stat.S_IMODE((root / "events").stat().st_mode) == 0o750
    assert stat.S_IMODE((root / ".entity-governance.lock").stat().st_mode) == 0o600
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o640 for path in (root / "events").iterdir())

    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED",
    ):
        _append(ledger, str(first["event_id"]))
    assert len(ledger.records()) == 2


def test_flock_allows_only_one_writer_for_the_same_empty_head(tmp_path: Path) -> None:
    root = tmp_path / "concurrent-governance"
    ledger = _ledger(root)
    barrier = Barrier(2)

    def append_after_barrier(entity_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            return str(_append(ledger, entity_id=entity_id)["event_id"])
        except EntityGovernanceConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                append_after_barrier,
                (
                    "urn:globemind:entity:country:CN",
                    "urn:globemind:entity:country:US",
                ),
            )
        )

    assert sum(item.startswith("egv-") for item in outcomes) == 1
    assert outcomes.count("ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED") == 1
    assert len(ledger.records()) == 1


def test_modified_record_fails_full_hash_and_hmac_validation(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    ledger = _ledger(root)
    _append(ledger)
    path = _only_event_path(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reason"] = "An attacker changed the recorded human reason"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID",
    ):
        ledger.records()


def test_wrong_hmac_key_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    ledger = _ledger(root)
    _append(ledger)

    wrong_key = _ledger(root, key=b"different-governance-hmac-key-000001")
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID",
    ):
        wrong_key.records()


def test_duplicate_json_key_is_rejected_before_record_use(tmp_path: Path) -> None:
    root = tmp_path / "governance"
    ledger = _ledger(root)
    event = _append(ledger)
    path = _only_event_path(root)
    path.write_text(
        "{" + f'"event_id":"{event["event_id"]}",' + '"event_id":"duplicate"}',
        encoding="utf-8",
    )

    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_DUPLICATE_JSON_KEY",
    ):
        ledger.records()


def test_event_and_lock_hardlinks_are_rejected(tmp_path: Path) -> None:
    event_root = tmp_path / "event-ledger"
    event_ledger = _ledger(event_root)
    _append(event_ledger)
    os.link(_only_event_path(event_root), tmp_path / "external-event-hardlink.json")
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_FILE_UNSAFE",
    ):
        event_ledger.records()

    lock_root = tmp_path / "lock-ledger"
    lock_ledger = _ledger(lock_root)
    _append(lock_ledger)
    os.link(
        lock_root / ".entity-governance.lock",
        tmp_path / "external-lock-hardlink",
    )
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_LOCK_FILE_UNSAFE",
    ):
        lock_ledger.records()


def test_event_symlink_and_symlinked_event_directory_are_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "governance"
    ledger = _ledger(root)
    _append(ledger)
    path = _only_event_path(root)
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_DIRECTORY_INVALID",
    ):
        ledger.records()

    root_with_symbolic_events = tmp_path / "symbolic-events-root"
    root_with_symbolic_events.mkdir()
    (root_with_symbolic_events / "events").symlink_to(
        tmp_path,
        target_is_directory=True,
    )
    symbolic_ledger = _ledger(root_with_symbolic_events)
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_DIRECTORY_UNSAFE",
    ):
        symbolic_ledger.records()


def test_parent_component_symlink_swap_is_rejected_before_read(
    tmp_path: Path,
) -> None:
    container = tmp_path / "container"
    container.mkdir()
    ledger = _ledger(container / "governance")

    outside_container = tmp_path / "outside-container"
    (outside_container / "governance").mkdir(parents=True)

    container.rmdir()
    container.symlink_to(outside_container, target_is_directory=True)

    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_PATH_SYMLINK_REJECTED",
    ):
        ledger.records()


def test_excessively_nested_event_json_fails_closed_as_ledger_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nested-event"
    ledger = _ledger(root)
    _append(ledger)
    path = _only_event_path(root)
    path.write_text(
        '{"nested":' + ("[" * 1_100) + "0" + ("]" * 1_100) + "}",
        encoding="utf-8",
    )

    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_UNREADABLE",
    ):
        ledger.records()


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_event_numbers_are_rejected_during_json_decode(
    tmp_path: Path,
    literal: str,
) -> None:
    root = tmp_path / f"non-finite-{literal.replace('-', 'negative-')}"
    ledger = _ledger(root)
    _append(ledger)
    path = _only_event_path(root)
    encoded = path.read_text(encoding="utf-8")
    path.write_text(
        encoded.replace('"sequence":1', f'"sequence":{literal}'),
        encoding="utf-8",
    )

    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_NON_FINITE_JSON_NUMBER",
    ):
        ledger.records()


def test_deleted_chain_prefix_and_unexpected_entries_fail_closed(tmp_path: Path) -> None:
    deleted_root = tmp_path / "deleted-prefix"
    deleted = _ledger(deleted_root)
    first = _append(deleted)
    _append(
        deleted,
        str(first["event_id"]),
        entity_id="urn:globemind:entity:country:US",
    )
    sorted((deleted_root / "events").glob("*.json"))[0].unlink()
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID",
    ):
        deleted.records()

    unexpected_root = tmp_path / "unexpected"
    unexpected = _ledger(unexpected_root)
    _append(unexpected)
    (unexpected_root / "mutable-index.json").write_text("{}", encoding="utf-8")
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_ROOT_HAS_UNEXPECTED_ENTRY",
    ):
        unexpected.records()


def test_hmac_valid_but_semantically_invalid_evidence_fails_projection_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invalid-semantic-event"
    ledger = _ledger(root)
    evidence = _evidence()
    evidence["schema_version"] = "invented-snapshot-schema"
    ledger.append(
        actor_id=9,
        event_type="entity.decision",
        reason="This internally signed record has invalid evidence metadata",
        evidence=evidence,
        payload={
            "entity_id": "urn:globemind:entity:country:CN",
            "decision": "approve",
            "valid_from": None,
            "valid_to": None,
        },
        expected_previous_event_id=None,
    )
    service = EntityGovernanceService(ledger, load_search_seed_entities())

    status = service.status({"user_id": 1, "role": "user"})
    assert status["storage_status"] == "unavailable"
    assert status["integrity_status"] == "failed_closed"
    assert status["reason"] == "ENTITY_GOVERNANCE_EVENT_EVIDENCE_INVALID"
    with pytest.raises(
        EntityGovernanceUnavailable,
        match="ENTITY_GOVERNANCE_EVENT_EVIDENCE_INVALID",
    ):
        service.catalog({"user_id": 1, "role": "user"})


def test_ledger_rejects_control_characters_in_reason_before_write(tmp_path: Path) -> None:
    root = tmp_path / "control-reason"
    ledger = _ledger(root)
    with pytest.raises(
        EntityGovernanceConflict,
        match="ENTITY_GOVERNANCE_REASON_INVALID",
    ):
        ledger.append(
            actor_id=9,
            event_type="entity.decision",
            reason="human\nreview",
            evidence=_evidence(),
            payload={
                "entity_id": "urn:globemind:entity:country:CN",
                "decision": "approve",
                "valid_from": None,
                "valid_to": None,
            },
            expected_previous_event_id=None,
        )
    assert not root.exists()
