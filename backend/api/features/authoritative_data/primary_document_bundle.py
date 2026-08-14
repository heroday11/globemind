"""Strict offline intake for reviewed country primary-document bundles.

The loader validates external artifacts without networking, database access, or
retaining document bodies in its result. A valid bundle is still only intake
evidence; it is not automatically published by the schema-only public catalog.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COUNTRY_PRIMARY_DOCUMENT_BUNDLE_SCHEMA_VERSION = (
    "globemind.country-primary-document-bundle.v1"
)
MAX_PRIMARY_DOCUMENT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PRIMARY_DOCUMENT_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_PRIMARY_DOCUMENT_BUNDLE_BYTES = 64 * 1024 * 1024
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")
_ACTOR_RE = re.compile(r"^(?:owner|reviewer):[a-z0-9][a-z0-9._-]{7,63}$")
_ANCHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class CountryPrimaryDocumentBundleError(RuntimeError):
    """The bundle cannot enter the reviewed country-data intake boundary."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise CountryPrimaryDocumentBundleError(f"duplicate JSON key: {key!r}")
        output[key] = value
    return output


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _safe_relative_locator(value: str) -> str:
    if value != value.strip() or _CONTROL_RE.search(value) or "\\" in value:
        raise ValueError("artifact locator must be a trimmed POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise ValueError("artifact locator must be normalized and relative")
    if any(part in {"releases", "current", "previous", "rejected"} for part in path.parts):
        raise ValueError("artifact locator crosses the release boundary")
    return path.as_posix()


def _safe_official_locator(value: str) -> str:
    if value != value.strip() or _CONTROL_RE.search(value) or "\\" in value:
        raise ValueError("official locator is malformed")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("official locator is malformed") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("official locator must be a stable credential-free HTTPS URL")
    hostname = parsed.hostname.casefold().rstrip(".")
    if not hostname:
        raise ValueError("official locator hostname is empty")
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


class CountryDocumentSectionAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_id: str = Field(pattern=_ANCHOR_RE.pattern)
    label: str = Field(min_length=1, max_length=300)
    byte_start: int = Field(ge=0, strict=True)
    byte_end: int = Field(gt=0, strict=True)
    content_sha256: str = Field(pattern=_SHA256_RE.pattern)

    @model_validator(mode="after")
    def range_is_nonempty(self) -> "CountryDocumentSectionAnchor":
        if self.byte_end <= self.byte_start:
            raise ValueError("section anchor byte range must be non-empty")
        return self


class CountryDocumentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    country_code: str = Field(pattern=_COUNTRY_RE.pattern)
    issuing_authority: str = Field(min_length=1, max_length=300)
    official_identifier: str = Field(min_length=1, max_length=240)
    document_kind: Literal[
        "constitution",
        "statute",
        "regulation",
        "official_gazette",
        "judicial_decision",
        "policy_document",
        "treaty",
    ]
    original_title: str = Field(min_length=1, max_length=500)


class CountryDocumentTextEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_language: str = Field(pattern=_LANGUAGE_RE.pattern)
    official_locator: str = Field(min_length=1, max_length=2_048)
    content_locator: str = Field(min_length=1, max_length=500)
    content_sha256: str = Field(pattern=_SHA256_RE.pattern)
    section_anchors: tuple[CountryDocumentSectionAnchor, ...] = Field(
        min_length=1,
        max_length=2_000,
    )

    @field_validator("official_locator")
    @classmethod
    def official_locator_is_safe(cls, value: str) -> str:
        return _safe_official_locator(value)

    @field_validator("content_locator")
    @classmethod
    def content_locator_is_safe(cls, value: str) -> str:
        return _safe_relative_locator(value)

    @model_validator(mode="after")
    def anchors_are_unique_and_ordered(self) -> "CountryDocumentTextEvidence":
        ids = [anchor.anchor_id for anchor in self.section_anchors]
        if len(ids) != len(set(ids)):
            raise ValueError("section anchor IDs must be unique")
        ranges = [(anchor.byte_start, anchor.byte_end) for anchor in self.section_anchors]
        if ranges != sorted(ranges) or any(
            right[0] < left[1] for left, right in zip(ranges, ranges[1:])
        ):
            raise ValueError("section anchor ranges must be ordered and non-overlapping")
        return self


class CountryDocumentTemporalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issued_at: datetime
    effective_from: datetime
    effective_until: datetime | None = None
    status_as_of: datetime

    @field_validator("issued_at", "effective_from", "effective_until", "status_as_of")
    @classmethod
    def times_are_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        return None if value is None else _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def legal_period_is_consistent(self) -> "CountryDocumentTemporalEvidence":
        if self.effective_from < self.issued_at:
            raise ValueError("effective_from cannot precede issuance")
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must follow effective_from")
        if self.status_as_of < self.issued_at:
            raise ValueError("status_as_of cannot precede issuance")
        return self


class CountryDocumentVersionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_identifier: str = Field(min_length=1, max_length=240)
    amends: tuple[str, ...] = Field(default=(), max_length=100)
    amended_by: tuple[str, ...] = Field(default=(), max_length=100)
    supersedes: tuple[str, ...] = Field(default=(), max_length=100)
    superseded_by: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def relationships_are_unique(self) -> "CountryDocumentVersionEvidence":
        all_values = (*self.amends, *self.amended_by, *self.supersedes, *self.superseded_by)
        if len(all_values) != len(set(all_values)):
            raise ValueError("version relationships must be unique across relation kinds")
        return self


class CountryDocumentGovernanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieved_at: datetime
    source_cutoff: datetime
    license_state: Literal["verified", "restricted"]
    license_artifact_locator: str = Field(min_length=1, max_length=500)
    license_artifact_sha256: str = Field(pattern=_SHA256_RE.pattern)
    owner_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    reviewer_identifier: str = Field(pattern=_ACTOR_RE.pattern)
    reviewed_at: datetime
    review_expires_at: datetime
    review_state: Literal["approved"] = "approved"

    @field_validator("license_artifact_locator")
    @classmethod
    def license_locator_is_safe(cls, value: str) -> str:
        return _safe_relative_locator(value)

    @field_validator("retrieved_at", "source_cutoff", "reviewed_at", "review_expires_at")
    @classmethod
    def times_are_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware_utc(value, info.field_name)

    @model_validator(mode="after")
    def review_chain_is_consistent(self) -> "CountryDocumentGovernanceEvidence":
        if self.owner_identifier == self.reviewer_identifier:
            raise ValueError("document owner and reviewer must be distinct")
        if self.retrieved_at > self.source_cutoff:
            raise ValueError("retrieved_at cannot follow source_cutoff")
        if self.reviewed_at < self.retrieved_at:
            raise ValueError("review cannot precede retrieval")
        if self.review_expires_at <= self.reviewed_at:
            raise ValueError("review expiry must follow review")
        return self


class CountryPrimaryDocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=200)
    identity: CountryDocumentIdentity
    text: CountryDocumentTextEvidence
    temporal: CountryDocumentTemporalEvidence
    version: CountryDocumentVersionEvidence
    governance: CountryDocumentGovernanceEvidence
    publication_state: Literal["intake_verified_not_published"] = (
        "intake_verified_not_published"
    )

    @model_validator(mode="after")
    def identifier_binds_country_and_content(self) -> "CountryPrimaryDocumentRecord":
        expected = (
            "urn:globemind:country-document:"
            f"{self.identity.country_code.casefold()}:{self.text.content_sha256}"
        )
        if self.document_id != expected:
            raise ValueError("document_id does not bind country and content SHA-256")
        return self


class CountryPrimaryDocumentBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.country-primary-document-bundle.v1"
    ] = COUNTRY_PRIMARY_DOCUMENT_BUNDLE_SCHEMA_VERSION
    bundle_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
    bundle_version: str = Field(min_length=1, max_length=120)
    pilot_country_codes: tuple[str, ...] = Field(min_length=1, max_length=3)
    documents: tuple[CountryPrimaryDocumentRecord, ...] = Field(
        min_length=1,
        max_length=500,
    )
    source_truth_review: Literal["human_reviewed_primary_source"] = (
        "human_reviewed_primary_source"
    )
    public_catalog_promotion: Literal["separate_explicit_step_required"] = (
        "separate_explicit_step_required"
    )

    @field_validator("pilot_country_codes")
    @classmethod
    def countries_are_canonical(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(_COUNTRY_RE.fullmatch(value) is None for value in values):
            raise ValueError("pilot country codes must be unique ISO alpha-2 values")
        return values

    @model_validator(mode="after")
    def document_graph_is_closed(self) -> "CountryPrimaryDocumentBundle":
        identifiers = [document.document_id for document in self.documents]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("document IDs must be unique")
        countries = {document.identity.country_code for document in self.documents}
        if countries != set(self.pilot_country_codes):
            raise ValueError("pilot country scope must exactly match document countries")
        known = set(identifiers)
        for document in self.documents:
            relationships = (
                *document.version.amends,
                *document.version.amended_by,
                *document.version.supersedes,
                *document.version.superseded_by,
            )
            if document.document_id in relationships:
                raise ValueError("document cannot relate to itself")
            if set(relationships) - known:
                raise ValueError("version relationship points outside the bundle")
        return self


@dataclass(frozen=True)
class LoadedCountryPrimaryDocumentBundle:
    bundle: CountryPrimaryDocumentBundle
    manifest_sha256: str
    manifest_bytes: int
    verified_artifact_count: int
    verified_artifact_bytes: int
    document_bodies_retained: bool = False


def _assert_not_release(path: Path, field: str) -> None:
    candidate = Path(os.path.abspath(os.path.normpath(path)))
    if candidate == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in candidate.parents:
        raise CountryPrimaryDocumentBundleError(f"{field} cannot use a production release")


def _assert_no_symlink_components(path: Path, field: str) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe /= part
        if probe.is_symlink():
            raise CountryPrimaryDocumentBundleError(f"{field} cannot contain symlinks")


def _read_single_link_file(path: Path, *, maximum: int, field: str) -> bytes:
    _assert_not_release(path, field)
    _assert_no_symlink_components(path, field)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CountryPrimaryDocumentBundleError(f"{field} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise CountryPrimaryDocumentBundleError(f"{field} must be a single-link file")
        if before.st_size <= 0 or before.st_size > maximum:
            raise CountryPrimaryDocumentBundleError(f"{field} exceeds its byte boundary")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise CountryPrimaryDocumentBundleError(f"{field} changed while being read")
        body = b"".join(chunks)
        if len(body) != before.st_size or len(body) > maximum:
            raise CountryPrimaryDocumentBundleError(f"{field} exceeds its byte boundary")
        return body
    finally:
        os.close(descriptor)


def _resolve_bundle_artifact(root: Path, locator: str, field: str) -> Path:
    try:
        normalized = _safe_relative_locator(locator)
    except ValueError as exc:
        raise CountryPrimaryDocumentBundleError(f"{field} is invalid") from exc
    candidate = root / PurePosixPath(normalized)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CountryPrimaryDocumentBundleError(f"{field} escapes the bundle") from exc
    return resolved


def load_country_primary_document_bundle(
    manifest_path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> LoadedCountryPrimaryDocumentBundle:
    """Validate a reviewed external bundle without publishing or retaining bodies."""

    if _SHA256_RE.fullmatch(expected_sha256) is None:
        raise CountryPrimaryDocumentBundleError("expected SHA-256 is invalid")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise CountryPrimaryDocumentBundleError("evaluated_at must include a timezone")
    if not manifest_path.is_absolute():
        raise CountryPrimaryDocumentBundleError("manifest path must be absolute")
    manifest = Path(os.path.abspath(os.path.normpath(manifest_path)))
    raw = _read_single_link_file(
        manifest,
        maximum=MAX_PRIMARY_DOCUMENT_MANIFEST_BYTES,
        field="manifest",
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise CountryPrimaryDocumentBundleError("manifest SHA-256 mismatch")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                CountryPrimaryDocumentBundleError(f"non-finite JSON number: {value}")
            ),
        )
        bundle = CountryPrimaryDocumentBundle.model_validate(payload)
    except CountryPrimaryDocumentBundleError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CountryPrimaryDocumentBundleError("manifest failed strict validation") from exc

    root = manifest.parent.resolve(strict=True)
    evaluated = evaluated_at.astimezone(timezone.utc)
    verified_bytes = 0
    verified_paths: set[Path] = set()
    for index, document in enumerate(bundle.documents):
        if document.governance.reviewed_at > evaluated:
            raise CountryPrimaryDocumentBundleError("document review is in the future")
        if document.governance.review_expires_at <= evaluated:
            raise CountryPrimaryDocumentBundleError("document review is expired")
        if document.temporal.status_as_of > document.governance.source_cutoff:
            raise CountryPrimaryDocumentBundleError("legal status exceeds source cutoff")

        content_path = _resolve_bundle_artifact(
            root,
            document.text.content_locator,
            f"documents[{index}].content",
        )
        content = _read_single_link_file(
            content_path,
            maximum=MAX_PRIMARY_DOCUMENT_ARTIFACT_BYTES,
            field=f"documents[{index}].content",
        )
        if hashlib.sha256(content).hexdigest() != document.text.content_sha256:
            raise CountryPrimaryDocumentBundleError("document content SHA-256 mismatch")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CountryPrimaryDocumentBundleError("document content must be UTF-8") from exc
        for anchor in document.text.section_anchors:
            if anchor.byte_end > len(content):
                raise CountryPrimaryDocumentBundleError("section anchor exceeds document bytes")
            segment = content[anchor.byte_start : anchor.byte_end]
            try:
                segment.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CountryPrimaryDocumentBundleError("section anchor splits UTF-8 text") from exc
            if hashlib.sha256(segment).hexdigest() != anchor.content_sha256:
                raise CountryPrimaryDocumentBundleError("section anchor SHA-256 mismatch")

        license_path = _resolve_bundle_artifact(
            root,
            document.governance.license_artifact_locator,
            f"documents[{index}].license",
        )
        license_body = _read_single_link_file(
            license_path,
            maximum=MAX_PRIMARY_DOCUMENT_ARTIFACT_BYTES,
            field=f"documents[{index}].license",
        )
        if hashlib.sha256(license_body).hexdigest() != document.governance.license_artifact_sha256:
            raise CountryPrimaryDocumentBundleError("license artifact SHA-256 mismatch")
        for artifact_path, body in ((content_path, content), (license_path, license_body)):
            if artifact_path not in verified_paths:
                verified_paths.add(artifact_path)
                verified_bytes += len(body)
                if verified_bytes > MAX_PRIMARY_DOCUMENT_BUNDLE_BYTES:
                    raise CountryPrimaryDocumentBundleError("bundle exceeds total byte boundary")

    return LoadedCountryPrimaryDocumentBundle(
        bundle=bundle,
        manifest_sha256=digest,
        manifest_bytes=len(raw),
        verified_artifact_count=len(verified_paths),
        verified_artifact_bytes=verified_bytes,
    )


__all__ = (
    "COUNTRY_PRIMARY_DOCUMENT_BUNDLE_SCHEMA_VERSION",
    "CountryDocumentGovernanceEvidence",
    "CountryDocumentIdentity",
    "CountryDocumentSectionAnchor",
    "CountryDocumentTemporalEvidence",
    "CountryDocumentTextEvidence",
    "CountryDocumentVersionEvidence",
    "CountryPrimaryDocumentBundle",
    "CountryPrimaryDocumentBundleError",
    "CountryPrimaryDocumentRecord",
    "LoadedCountryPrimaryDocumentBundle",
    "load_country_primary_document_bundle",
)
