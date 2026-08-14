"""Append-only source snapshots and revision-impact records.

The ledger is deliberately separate from immutable releases and ordinary
article reads. Captures are explicit authenticated actions; a GET never writes
evidence. Snapshot bodies are content-addressed and immutable, while capture
events and impact reviews are separate append-only records.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal
from urllib.parse import urlsplit, urlunsplit

from .application import split_article_paragraphs

LEDGER_SCHEMA_VERSION = "evidence-ledger-v1"
SNAPSHOT_SCHEMA_VERSION = "source-snapshot-v1"
REVISION_EVENT_SCHEMA_VERSION = "source-revision-event-v1"
IMPACT_REVIEW_SCHEMA_VERSION = "source-impact-review-v1"
SNAPSHOT_PARSER_VERSION = "article-display-v1"

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_EVENTS_PER_ARTICLE = 1000
MAX_CLAIMS_PER_EVENT = 200
MAX_CLOCK_SKEW = timedelta(minutes=5)
_SNAPSHOT_ID = re.compile(r"^article-(?P<article_id>[1-9][0-9]*)-(?P<digest>[0-9a-f]{64})$")
_EVENT_ID = re.compile(
    r"^evt-(?P<stamp>[0-9]{8}T[0-9]{12}Z)-[0-9a-f]{16}$"
)
_REVIEW_ID = re.compile(
    r"^review-(?P<stamp>[0-9]{8}T[0-9]{12}Z)-[0-9a-f]{16}$"
)
_CLAIM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CHANGE_TYPES = frozenset({"initial", "update", "correction", "withdrawal"})
_REVIEW_DECISIONS = frozenset({"confirmed", "modified", "rejected"})
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")


class EvidenceLedgerError(RuntimeError):
    """Base class for bounded, user-safe ledger failures."""


class EvidenceLedgerUnavailable(EvidenceLedgerError):
    """The durable ledger cannot be used safely."""


class EvidenceLedgerConflict(EvidenceLedgerError):
    """Optimistic revision identity no longer matches."""


class EvidenceLedgerNotFound(EvidenceLedgerError):
    """A requested snapshot or revision does not exist."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("ledger timestamps require timezone information")
    return current.astimezone(timezone.utc)


def _record_id(prefix: str, at: datetime) -> str:
    stamp = at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}-{secrets.token_hex(8)}"


def _record_time(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceLedgerUnavailable(f"ledger {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise EvidenceLedgerUnavailable(f"ledger {field} is invalid")
    return parsed.astimezone(timezone.utc)


def _reject_future_time(value: datetime, *, field: str) -> None:
    if value > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
        raise ValueError(f"{field} cannot be in the future")


def _clean_text(value: Any, *, field: str, minimum: int, maximum: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) < minimum or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ValueError(f"{field} has an invalid length or control character")
    return text


def _claim_ids(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for index, raw in enumerate(values):
        if index >= MAX_CLAIMS_PER_EVENT:
            raise ValueError("too many claim ids")
        value = str(raw or "").strip()
        if _CLAIM_ID.fullmatch(value) is None:
            raise ValueError("claim id has an invalid format")
        if value not in result:
            result.append(value)
    return result


def _safe_source_url(value: Any) -> str | None:
    raw_value = str(value or "")
    if "\\" in raw_value or any(
        ord(character) <= 32 or ord(character) == 127 for character in raw_value
    ):
        raise ValueError("source URL contains ambiguous syntax")
    raw = raw_value.strip()
    if not raw:
        return None
    if len(raw) > 4000:
        raise ValueError("source URL is too long")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source URL must be HTTP(S)")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except (UnicodeError, ValueError) as exc:
        raise ValueError("source URL authority is invalid") from exc
    # Credentials, query parameters and fragments are not persisted because
    # signed URLs and crawler tokens frequently live there.
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", "", ""))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceLedgerUnavailable("ledger record has a duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _record_sha256(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "record_sha256"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _ensure_directory(path: Path, *, mode: int = 0o750) -> None:
    if _path_has_symlink(path):
        raise EvidenceLedgerUnavailable("ledger path contains a symbolic link")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        if path.is_symlink() or not path.is_dir():
            raise EvidenceLedgerUnavailable("ledger directory is unavailable")
        os.chmod(path, mode)
    except EvidenceLedgerUnavailable:
        raise
    except OSError as exc:
        raise EvidenceLedgerUnavailable("ledger directory is unavailable") from exc


def _read_json(path: Path, *, maximum: int = MAX_METADATA_BYTES) -> dict[str, Any]:
    descriptor = -1
    try:
        if _path_has_symlink(path):
            raise EvidenceLedgerUnavailable("ledger path contains a symbolic link")
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceLedgerUnavailable("ledger record is unavailable")
        if before.st_nlink != 1:
            raise EvidenceLedgerUnavailable("ledger record has an unsafe link count")
        if before.st_size > maximum:
            raise EvidenceLedgerUnavailable("ledger record exceeds its size bound")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            encoded = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
        if len(encoded) > maximum:
            raise EvidenceLedgerUnavailable("ledger record exceeds its size bound")
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise EvidenceLedgerUnavailable("ledger record changed while it was read")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
            parse_float=_finite_json_float,
        )
    except EvidenceLedgerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise EvidenceLedgerUnavailable("ledger record is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise EvidenceLedgerUnavailable("ledger record root is invalid")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any], *, mode: int = 0o640) -> None:
    _ensure_directory(path.parent)
    if path.exists() and path.is_symlink():
        raise EvidenceLedgerUnavailable("ledger target is a symbolic link")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_BODY_BYTES + MAX_METADATA_BYTES:
        raise EvidenceLedgerUnavailable("ledger record exceeds its size bound")
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".ledger-", dir=path.parent)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # All ledger records are immutable. Creating the destination with a
        # hard link gives us O_EXCL-like no-replace semantics, after which the
        # temporary name is removed and the committed record has link count 1.
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, TypeError, ValueError) as exc:
        raise EvidenceLedgerUnavailable("ledger record could not be committed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class EvidenceSnapshotLedger:
    """Filesystem-backed, append-only evidence revision ledger."""

    def __init__(self, root: Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise EvidenceLedgerUnavailable("ledger root must be absolute")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        if _path_has_symlink(self.root):
            raise EvidenceLedgerUnavailable("ledger root contains a symbolic link")
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise EvidenceLedgerUnavailable("ledger root cannot be inside release evidence")
        # Construction is intentionally read-only. Only an explicit capture
        # or review creates the durable root; history and snapshot GETs never
        # mutate storage.

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_directory(self.root)
        lock_path = self.root / ".ledger.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise EvidenceLedgerUnavailable("ledger lock has an unsafe link count")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_nlink,
            ):
                raise EvidenceLedgerUnavailable("ledger lock changed while being acquired")
            yield
        except EvidenceLedgerUnavailable:
            raise
        except OSError as exc:
            raise EvidenceLedgerUnavailable("ledger lock is unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _article_root(self, article_id: int) -> Path:
        if isinstance(article_id, bool) or not isinstance(article_id, int) or article_id <= 0:
            raise ValueError("article id must be a positive integer")
        return self.root / "articles" / str(article_id)

    def _event_files(self, article_id: int) -> list[Path]:
        event_root = self._article_root(article_id) / "events"
        if not event_root.exists():
            return []
        if event_root.is_symlink() or not event_root.is_dir():
            raise EvidenceLedgerUnavailable("ledger event directory is invalid")
        entries = list(event_root.iterdir())
        if any(
            entry.is_symlink()
            or not entry.is_file()
            or _EVENT_ID.fullmatch(entry.stem) is None
            or entry.suffix != ".json"
            for entry in entries
        ):
            raise EvidenceLedgerUnavailable("ledger event directory contains an invalid entry")
        paths = sorted(entries)
        if len(paths) > MAX_EVENTS_PER_ARTICLE:
            raise EvidenceLedgerUnavailable("ledger event bound was exceeded")
        return paths

    def _load_events(self, article_id: int) -> list[dict[str, Any]]:
        paths = self._event_files(article_id)
        events = [_read_json(path) for path in paths]
        previous: dict[str, Any] | None = None
        for path, event in zip(paths, events, strict=True):
            event_id = str(event.get("event_id") or "")
            event_match = _EVENT_ID.fullmatch(event_id)
            snapshot_id = str(event.get("snapshot_id") or "")
            snapshot_match = _SNAPSHOT_ID.fullmatch(snapshot_id)
            captured_at = _record_time(event.get("captured_at"), field="capture time")
            try:
                _reject_future_time(captured_at, field="ledger capture time")
            except ValueError as exc:
                raise EvidenceLedgerUnavailable("ledger capture time is invalid") from exc
            try:
                claims = _claim_ids(event.get("claim_ids", ()))
                impacted = _claim_ids(event.get("impacted_claim_ids", ()))
            except ValueError as exc:
                raise EvidenceLedgerUnavailable("ledger event claim contract is invalid") from exc
            claimed_record_sha256 = event.get("record_sha256")
            if (
                not isinstance(claimed_record_sha256, str)
                or _HEX_64.fullmatch(claimed_record_sha256) is None
                or claimed_record_sha256 != _record_sha256(event)
            ):
                raise EvidenceLedgerUnavailable(
                    "ledger event record integrity check failed"
                )
            if (
                event.get("schema_version") != REVISION_EVENT_SCHEMA_VERSION
                or event.get("article_id") != article_id
                or event_match is None
                or path.name != f"{event_id}.json"
                or snapshot_match is None
                or int(snapshot_match.group("article_id")) != article_id
                or captured_at.strftime("%Y%m%dT%H%M%S%fZ")
                != event_match.group("stamp")
                or event.get("declared_change_type") not in _CHANGE_TYPES
                or event.get("declaration_evidence_status") != "analyst_declared"
                or re.fullmatch(r"user:[1-9][0-9]*", str(event.get("actor_ref") or ""))
                is None
                or not isinstance(event.get("reason"), str)
                or _HEX_64.fullmatch(
                    str(event.get("snapshot_record_sha256") or "")
                )
                is None
                or event.get("claim_ids") != claims
                or event.get("impacted_claim_ids") != impacted
            ):
                raise EvidenceLedgerUnavailable("ledger event contract is invalid")

            try:
                snapshot = self.snapshot(snapshot_id, include_body=False)
            except EvidenceLedgerNotFound as exc:
                raise EvidenceLedgerUnavailable(
                    "ledger snapshot binding is invalid"
                ) from exc
            if (
                event.get("snapshot_record_sha256")
                != snapshot.get("record_sha256")
                or _record_time(
                    snapshot.get("first_captured_at"),
                    field="snapshot capture time",
                )
                > captured_at
            ):
                raise EvidenceLedgerUnavailable("ledger snapshot binding is invalid")

            if previous is None:
                expected_previous_event = None
                expected_previous_snapshot = None
                expected_previous_record = None
                expected_changed = False
                expected_impacted: list[str] = []
                expected_impact_status = "none"
                if event.get("declared_change_type") != "initial":
                    raise EvidenceLedgerUnavailable("ledger event chain is invalid")
            else:
                expected_previous_event = previous["event_id"]
                expected_previous_snapshot = previous["snapshot_id"]
                expected_previous_record = previous["record_sha256"]
                expected_changed = snapshot_id != expected_previous_snapshot
                prior_claims = list(previous["claim_ids"])
                requires_review = bool(prior_claims) and (
                    expected_changed
                    or event.get("declared_change_type") in {"correction", "withdrawal"}
                )
                expected_impacted = prior_claims if requires_review else []
                expected_impact_status = "review_required" if requires_review else "none"
                if event.get("declared_change_type") == "initial":
                    raise EvidenceLedgerUnavailable("ledger event chain is invalid")
            if (
                event.get("previous_event_id") != expected_previous_event
                or event.get("previous_snapshot_id") != expected_previous_snapshot
                or event.get("previous_event_record_sha256")
                != expected_previous_record
                or event.get("content_changed") is not expected_changed
                or impacted != expected_impacted
                or event.get("impact_status") != expected_impact_status
            ):
                raise EvidenceLedgerUnavailable("ledger event chain is invalid")
            previous = event
        return events

    def _review_files(self, article_id: int, event_id: str) -> list[Path]:
        if _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError("event id has an invalid format")
        root = self._article_root(article_id) / "reviews" / event_id
        if not root.exists():
            return []
        if root.is_symlink() or not root.is_dir():
            raise EvidenceLedgerUnavailable("ledger review directory is invalid")
        entries = list(root.iterdir())
        if any(
            entry.is_symlink()
            or not entry.is_file()
            or _REVIEW_ID.fullmatch(entry.stem) is None
            or entry.suffix != ".json"
            for entry in entries
        ):
            raise EvidenceLedgerUnavailable("ledger review directory contains an invalid entry")
        paths = sorted(entries)
        if len(paths) > MAX_EVENTS_PER_ARTICLE:
            raise EvidenceLedgerUnavailable("ledger review bound was exceeded")
        return paths

    def _reviews(
        self,
        article_id: int,
        event_id: str,
        *,
        expected_snapshot_id: str,
        expected_event_record_sha256: str,
        event_captured_at: datetime,
    ) -> list[dict[str, Any]]:
        paths = self._review_files(article_id, event_id)
        reviews = [_read_json(path) for path in paths]
        previous_reviewed_at = event_captured_at
        previous_review_sha256: str | None = None
        for path, review in zip(paths, reviews, strict=True):
            review_id = str(review.get("review_id") or "")
            review_match = _REVIEW_ID.fullmatch(review_id)
            reviewed_at = _record_time(review.get("reviewed_at"), field="review time")
            try:
                _reject_future_time(reviewed_at, field="ledger review time")
            except ValueError as exc:
                raise EvidenceLedgerUnavailable("ledger review time is invalid") from exc
            try:
                original = _claim_ids(review.get("original_impacted_claim_ids", ()))
                resolved = _claim_ids(review.get("resolved_impacted_claim_ids", ()))
            except ValueError as exc:
                raise EvidenceLedgerUnavailable("ledger review claim contract is invalid") from exc
            decision = review.get("decision")
            claimed_record_sha256 = review.get("record_sha256")
            if (
                not isinstance(claimed_record_sha256, str)
                or _HEX_64.fullmatch(claimed_record_sha256) is None
                or claimed_record_sha256 != _record_sha256(review)
            ):
                raise EvidenceLedgerUnavailable(
                    "ledger review record integrity check failed"
                )
            if (
                review.get("schema_version") != IMPACT_REVIEW_SCHEMA_VERSION
                or review.get("article_id") != article_id
                or review.get("event_id") != event_id
                or review.get("snapshot_id") != expected_snapshot_id
                or review.get("event_record_sha256")
                != expected_event_record_sha256
                or review.get("previous_review_record_sha256")
                != previous_review_sha256
                or review_match is None
                or path.name != f"{review_id}.json"
                or reviewed_at.strftime("%Y%m%dT%H%M%S%fZ")
                != review_match.group("stamp")
                or decision not in _REVIEW_DECISIONS
                or re.fullmatch(r"user:[1-9][0-9]*", str(review.get("actor_ref") or ""))
                is None
                or not isinstance(review.get("reason"), str)
                or review.get("original_impacted_claim_ids") != original
                or review.get("resolved_impacted_claim_ids") != resolved
                or (decision == "confirmed" and resolved != original)
                or (decision == "rejected" and resolved != [])
                or (
                    decision == "modified"
                    and (not resolved or not set(resolved).issubset(original))
                )
                or reviewed_at <= previous_reviewed_at
            ):
                raise EvidenceLedgerUnavailable("ledger review contract is invalid")
            previous_reviewed_at = reviewed_at
            previous_review_sha256 = claimed_record_sha256
        return sorted(reviews, key=lambda item: (str(item.get("reviewed_at")), item["review_id"]))

    def _public_event(self, event: dict[str, Any]) -> dict[str, Any]:
        # Recheck the referenced immutable snapshot before publishing revision
        # metadata. A tampered or missing body therefore fails closed.
        reviews = self._reviews(
            int(event["article_id"]),
            str(event["event_id"]),
            expected_snapshot_id=str(event["snapshot_id"]),
            expected_event_record_sha256=str(event["record_sha256"]),
            event_captured_at=_record_time(event["captured_at"], field="capture time"),
        )
        snapshot = self.snapshot(str(event["snapshot_id"]), include_body=False)
        if (
            event.get("snapshot_record_sha256") != snapshot.get("record_sha256")
            or _record_time(
                snapshot.get("first_captured_at"),
                field="snapshot capture time",
            )
            > _record_time(event.get("captured_at"), field="capture time")
        ):
            raise EvidenceLedgerUnavailable("ledger snapshot binding is invalid")
        result = dict(event)
        result["impact_review"] = {
            "status": "reviewed" if reviews else event["impact_status"],
            "review_count": len(reviews),
            "latest": reviews[-1] if reviews else None,
        }
        return result

    def capture(
        self,
        *,
        article_id: int,
        title: Any,
        body: Any,
        source_url: Any,
        actor_id: int,
        reason: Any,
        change_type: Literal["initial", "update", "correction", "withdrawal"],
        claim_ids: Iterable[Any] = (),
        expected_previous_event_id: str | None = None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        article_root = self._article_root(article_id)
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise ValueError("actor id must be a positive integer")
        if change_type not in _CHANGE_TYPES:
            raise ValueError("change type is invalid")
        capture_reason = _clean_text(reason, field="reason", minimum=3, maximum=500)
        clean_title = _clean_text(title, field="title", minimum=1, maximum=1000)
        safe_url = _safe_source_url(source_url)
        claims = _claim_ids(claim_ids)
        paragraphs = split_article_paragraphs(body)
        normalized_body = "\n\n".join(paragraphs)
        if not normalized_body:
            raise ValueError("article body is unavailable")
        body_bytes = normalized_body.encode("utf-8")
        if len(body_bytes) > MAX_BODY_BYTES:
            raise ValueError("article body exceeds the snapshot bound")
        now = _utc(captured_at)
        _reject_future_time(now, field="ledger capture time")
        digest = hashlib.sha256(body_bytes).hexdigest()
        snapshot_id = f"article-{article_id}-{digest}"

        with self._locked():
            _ensure_directory(article_root / "snapshots")
            _ensure_directory(article_root / "events")
            events = self._load_events(article_id)
            previous = events[-1] if events else None
            if previous is None and change_type != "initial":
                raise ValueError("the first capture must declare an initial change")
            if previous is not None and change_type == "initial":
                raise ValueError("initial change is only valid for the first capture")
            previous_event_id = str(previous["event_id"]) if previous else None
            if previous is not None and expected_previous_event_id is None:
                raise ValueError("expected previous event id is required for a revision")
            if (
                expected_previous_event_id is not None
                and expected_previous_event_id != previous_event_id
            ):
                raise EvidenceLedgerConflict("latest evidence revision changed")
            if previous is not None and now <= _record_time(
                previous["captured_at"],
                field="capture time",
            ):
                raise ValueError("capture time must be later than the previous capture")
            if len(events) >= MAX_EVENTS_PER_ARTICLE:
                raise EvidenceLedgerUnavailable("ledger event bound was exceeded")

            snapshot_path = article_root / "snapshots" / f"{snapshot_id}.json"
            if snapshot_path.exists():
                existing = self.snapshot(snapshot_id, include_body=True)
                if (
                    existing.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
                    or existing.get("snapshot_id") != snapshot_id
                    or existing.get("content_sha256") != digest
                    or existing.get("normalized_body") != normalized_body
                ):
                    raise EvidenceLedgerUnavailable("content-addressed snapshot is inconsistent")
                if (
                    existing.get("title") != clean_title
                    or existing.get("source_url") != safe_url
                ):
                    raise EvidenceLedgerConflict(
                        "content snapshot cannot be rebound to different source metadata"
                    )
                snapshot_record_sha256 = str(existing["record_sha256"])
            else:
                snapshot = {
                    "schema_version": SNAPSHOT_SCHEMA_VERSION,
                    "snapshot_id": snapshot_id,
                    "article_id": article_id,
                    "title": clean_title,
                    "source_url": safe_url,
                    "normalized_body": normalized_body,
                    "paragraph_count": len(paragraphs),
                    "content_sha256": digest,
                    "hash_scope": "normalized-display-body",
                    "parser_version": SNAPSHOT_PARSER_VERSION,
                    "first_captured_at": now.isoformat(),
                }
                snapshot["record_sha256"] = _record_sha256(snapshot)
                snapshot_record_sha256 = str(snapshot["record_sha256"])
                _atomic_json(snapshot_path, snapshot)

            previous_snapshot_id = str(previous["snapshot_id"]) if previous else None
            changed = previous_snapshot_id is not None and previous_snapshot_id != snapshot_id
            previous_claims = _claim_ids(previous.get("claim_ids", ())) if previous else []
            requires_review = bool(previous_claims) and (
                changed or change_type in {"correction", "withdrawal"}
            )
            event_id = _record_id("evt", now)
            event = {
                "schema_version": REVISION_EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "article_id": article_id,
                "snapshot_id": snapshot_id,
                "previous_event_id": previous_event_id,
                "previous_snapshot_id": previous_snapshot_id,
                "previous_event_record_sha256": (
                    previous["record_sha256"] if previous else None
                ),
                "snapshot_record_sha256": snapshot_record_sha256,
                "content_changed": changed,
                "declared_change_type": change_type,
                "declaration_evidence_status": "analyst_declared",
                "captured_at": now.isoformat(),
                "actor_ref": f"user:{actor_id}",
                "reason": capture_reason,
                "claim_ids": claims,
                "impacted_claim_ids": previous_claims if requires_review else [],
                "impact_status": "review_required" if requires_review else "none",
            }
            event["record_sha256"] = _record_sha256(event)
            _atomic_json(article_root / "events" / f"{event_id}.json", event)
            return self._public_event(event)

    def history(self, article_id: int, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("history limit must be between 1 and 100")
        events = self._load_events(article_id)
        selected = [self._public_event(event) for event in reversed(events[-limit:])]
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "article_id": article_id,
            "event_count": len(events),
            "items": selected,
        }

    def snapshot(self, snapshot_id: str, *, include_body: bool = False) -> dict[str, Any]:
        match = _SNAPSHOT_ID.fullmatch(snapshot_id)
        if match is None:
            raise ValueError("snapshot id has an invalid format")
        article_id = int(match.group("article_id"))
        path = self._article_root(article_id) / "snapshots" / f"{snapshot_id}.json"
        if not path.exists():
            raise EvidenceLedgerNotFound("snapshot was not found")
        payload = _read_json(path, maximum=MAX_BODY_BYTES + MAX_METADATA_BYTES)
        claimed_record_sha256 = payload.get("record_sha256")
        if (
            not isinstance(claimed_record_sha256, str)
            or _HEX_64.fullmatch(claimed_record_sha256) is None
            or claimed_record_sha256 != _record_sha256(payload)
        ):
            raise EvidenceLedgerUnavailable("snapshot record integrity check failed")
        body = payload.get("normalized_body")
        try:
            first_captured_at = _record_time(
                payload.get("first_captured_at"),
                field="snapshot capture time",
            )
            _reject_future_time(first_captured_at, field="snapshot capture time")
            clean_title = _clean_text(
                payload.get("title"),
                field="snapshot title",
                minimum=1,
                maximum=1000,
            )
            safe_source_url = _safe_source_url(payload.get("source_url"))
        except ValueError as exc:
            raise EvidenceLedgerUnavailable("snapshot integrity check failed") from exc
        canonical_body = (
            "\n\n".join(split_article_paragraphs(body)) if isinstance(body, str) else None
        )
        if (
            payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
            or payload.get("snapshot_id") != snapshot_id
            or payload.get("article_id") != article_id
            or payload.get("content_sha256") != match.group("digest")
            or payload.get("hash_scope") != "normalized-display-body"
            or payload.get("parser_version") != SNAPSHOT_PARSER_VERSION
            or not isinstance(body, str)
            or body != canonical_body
            or payload.get("paragraph_count") != len(split_article_paragraphs(body))
            or payload.get("title") != clean_title
            or payload.get("source_url") != safe_source_url
            or hashlib.sha256(body.encode("utf-8")).hexdigest() != match.group("digest")
        ):
            raise EvidenceLedgerUnavailable("snapshot integrity check failed")
        result = dict(payload)
        if not include_body:
            result.pop("normalized_body", None)
        return result

    def review_impact(
        self,
        *,
        article_id: int,
        event_id: str,
        actor_id: int,
        decision: Literal["confirmed", "modified", "rejected"],
        reason: Any,
        impacted_claim_ids: Iterable[Any] | None = None,
        reviewed_at: datetime | None = None,
    ) -> dict[str, Any]:
        if _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError("event id has an invalid format")
        if decision not in _REVIEW_DECISIONS:
            raise ValueError("review decision is invalid")
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise ValueError("actor id must be a positive integer")
        review_reason = _clean_text(reason, field="reason", minimum=3, maximum=500)
        proposed_claims = (
            None if impacted_claim_ids is None else _claim_ids(impacted_claim_ids)
        )
        now = _utc(reviewed_at)
        _reject_future_time(now, field="ledger review time")
        with self._locked():
            event = next(
                (item for item in self._load_events(article_id) if item["event_id"] == event_id),
                None,
            )
            if event is None:
                raise EvidenceLedgerNotFound("revision event was not found")
            if event.get("impact_status") != "review_required":
                raise EvidenceLedgerConflict("revision event has no reviewable impact")
            event_captured_at = _record_time(event["captured_at"], field="capture time")
            reviews = self._reviews(
                article_id,
                event_id,
                expected_snapshot_id=str(event["snapshot_id"]),
                expected_event_record_sha256=str(event["record_sha256"]),
                event_captured_at=event_captured_at,
            )
            if now <= event_captured_at or (
                reviews
                and now
                <= _record_time(reviews[-1]["reviewed_at"], field="review time")
            ):
                raise ValueError("review time must be later than the revision and prior reviews")
            original_claims = _claim_ids(event.get("impacted_claim_ids", ()))
            if decision == "modified":
                if not proposed_claims:
                    raise ValueError("modified review requires impacted claim ids")
                unknown = sorted(set(proposed_claims) - set(original_claims))
                if unknown:
                    raise ValueError("modified review includes an unknown impacted claim")
                resolved_claims = proposed_claims
            elif proposed_claims is not None:
                raise ValueError("impacted claim ids are only valid for a modified review")
            elif decision == "rejected":
                resolved_claims = []
            else:
                resolved_claims = original_claims
            review_id = _record_id("review", now)
            review = {
                "schema_version": IMPACT_REVIEW_SCHEMA_VERSION,
                "review_id": review_id,
                "article_id": article_id,
                "event_id": event_id,
                "snapshot_id": event["snapshot_id"],
                "event_record_sha256": event["record_sha256"],
                "previous_review_record_sha256": (
                    reviews[-1]["record_sha256"] if reviews else None
                ),
                "decision": decision,
                "reason": review_reason,
                "reviewed_at": now.isoformat(),
                "actor_ref": f"user:{actor_id}",
                "original_impacted_claim_ids": original_claims,
                "resolved_impacted_claim_ids": resolved_claims,
            }
            review["record_sha256"] = _record_sha256(review)
            path = self._article_root(article_id) / "reviews" / event_id / f"{review_id}.json"
            _atomic_json(path, review)
            return review


__all__ = (
    "EvidenceLedgerConflict",
    "EvidenceLedgerError",
    "EvidenceLedgerNotFound",
    "EvidenceLedgerUnavailable",
    "EvidenceSnapshotLedger",
    "IMPACT_REVIEW_SCHEMA_VERSION",
    "LEDGER_SCHEMA_VERSION",
    "REVISION_EVENT_SCHEMA_VERSION",
    "SNAPSHOT_PARSER_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
)
