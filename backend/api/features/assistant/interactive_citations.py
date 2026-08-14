"""Fail-closed citation boundary for interactive model generations.

Only successful tool results from the current turn can receive server-created
``citation_source_id`` values.  Model output is buffered until this module has
checked its complete text; invalid or interrupted output is replaced by a
server-authored unknown response and is never copied into assurance metadata.

This is a syntactic/source-identity boundary, not a fact checker.  It does not
verify source truth or that a cited source semantically entails a claim.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .structured_claims import StructuredClaimError, finalize_structured_claim_output

INTERACTIVE_CITATION_SCHEMA_VERSION = "globemind.interactive-citation.v1"
INTERACTIVE_SOURCE_SCHEMA_VERSION = "globemind.interactive-source.v1"
UNKNOWN_CITATION_MARKER = "[GM-UNKNOWN]"
UNSTRUCTURED_CLAIM_REASON_CODE = "UNSTRUCTURED_MODEL_OUTPUT_NOT_PER_CLAIM"
MAX_INTERACTIVE_OUTPUT_LENGTH = 64_000
MAX_INTERACTIVE_SOURCES = 64

_SOURCE_ID_RE = re.compile(r"^GM-T-[0-9A-F]{16}$")
_GM_MARKER_RE = re.compile(r"\[(GM-[^\]\r\n]{1,96})\]")
_NUMERIC_CITATION_RE = re.compile(
    r"\[(?:\d{1,4})(?:\s*(?:,|;|\u2013|-)\s*\d{1,4})*\]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RAW_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")
_ACTIVE_MARKUP_RE = re.compile(
    r"<(?:script|iframe|object|embed|link|meta|img|video|audio|source|"
    r"picture|svg|style|base|form|input)\b|"
    r"\bon[a-z]+\s*=|"
    r"\b(?:href|src)\s*=\s*['\"]?\s*(?:javascript|data)\s*:|"
    r"\]\(\s*(?:javascript|data)\s*:",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]\r\n]*\]")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_MARKDOWN_SEPARATOR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


class InteractiveCitationError(RuntimeError):
    """Complete interactive output cannot cross the citation boundary."""

    def __init__(self, *reason_codes: str):
        codes = tuple(dict.fromkeys(str(code) for code in reason_codes if code))
        self.reason_codes = codes or ("INTERACTIVE_CITATION_BOUNDARY_FAILED",)
        super().__init__(",".join(self.reason_codes))


@dataclass(frozen=True)
class InteractiveSourceRecord:
    source_id: str
    source_kind: str
    tool_name: str
    binding_sha256: str

    def public_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "tool_name": self.tool_name,
            "binding_sha256": self.binding_sha256,
        }


@dataclass(frozen=True)
class InteractiveOutput:
    content: str
    assurance: dict[str, Any]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _bounded_identity(value: Any, maximum: int = 512) -> str:
    text = _CONTROL_RE.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def _safe_https_locator(value: Any) -> str:
    raw = _bounded_identity(value, 2_048)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    hostname = parsed.hostname.casefold().rstrip(".")
    if not hostname:
        return ""
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))[:2_048]


def _source_record(*, tool_name: str, source_kind: str, identity: str) -> InteractiveSourceRecord:
    binding = {
        "schema_version": INTERACTIVE_SOURCE_SCHEMA_VERSION,
        "tool_name": _bounded_identity(tool_name, 80),
        "source_kind": _bounded_identity(source_kind, 80),
        "identity": _bounded_identity(identity),
    }
    binding_sha256 = _sha256(binding)
    return InteractiveSourceRecord(
        source_id=f"GM-T-{binding_sha256[:16].upper()}",
        source_kind=binding["source_kind"],
        tool_name=binding["tool_name"],
        binding_sha256=binding_sha256,
    )


def _scrub_untrusted_binding_fields(value: Any, seen: set[int] | None = None) -> None:
    """Remove model/tool-provided fields that could impersonate server bindings."""

    visited = seen if seen is not None else set()
    identity = id(value)
    if identity in visited:
        return
    visited.add(identity)
    if isinstance(value, MutableMapping):
        value.pop("citation_source_id", None)
        value.pop("citation_sources", None)
        for child in list(value.values()):
            _scrub_untrusted_binding_fields(child, visited)
    elif isinstance(value, list):
        for child in value:
            _scrub_untrusted_binding_fields(child, visited)


def _bind_item(
    item: MutableMapping[str, Any],
    *,
    tool_name: str,
    source_kind: str,
    identity: str,
    records: list[InteractiveSourceRecord],
) -> None:
    if not identity or len(records) >= MAX_INTERACTIVE_SOURCES:
        return
    record = _source_record(
        tool_name=tool_name,
        source_kind=source_kind,
        identity=identity,
    )
    item["citation_source_id"] = record.source_id
    records.append(record)


def bind_interactive_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a tool result and add deterministic current-turn source IDs.

    List/catalog calls, failed calls, Skill instructions, database cards, and
    generated images intentionally receive no source ID.  Their presence is not
    evidence for factual claims.
    """

    if not isinstance(result, Mapping):
        return {"ok": False, "error": "invalid tool result"}
    bound: dict[str, Any] = copy.deepcopy(dict(result))
    _scrub_untrusted_binding_fields(bound)
    if bound.get("ok") is not True:
        bound["citation_sources"] = []
        return bound

    tool_name = _bounded_identity(bound.get("tool"), 80) or "tool"
    records: list[InteractiveSourceRecord] = []

    news = bound.get("news")
    if isinstance(news, list):
        for item in news[:32]:
            if not isinstance(item, MutableMapping):
                continue
            source_identity = _bounded_identity(item.get("id"))
            if source_identity and (item.get("title") or item.get("abstract")):
                _bind_item(
                    item,
                    tool_name=tool_name,
                    source_kind="news_record",
                    identity=f"news:{source_identity}",
                    records=records,
                )

    clusters = bound.get("clusters")
    if isinstance(clusters, list):
        for item in clusters[:32]:
            if not isinstance(item, MutableMapping):
                continue
            source_identity = _bounded_identity(item.get("id"))
            if source_identity and (item.get("title") or item.get("summary")):
                _bind_item(
                    item,
                    tool_name=tool_name,
                    source_kind="event_record",
                    identity=f"event:{source_identity}",
                    records=records,
                )

    if tool_name == "web_search":
        web_results = bound.get("results")
        if isinstance(web_results, list):
            for item in web_results[:24]:
                if not isinstance(item, MutableMapping):
                    continue
                locator = _safe_https_locator(item.get("url"))
                if locator and (item.get("title") or item.get("snippet")):
                    _bind_item(
                        item,
                        tool_name=tool_name,
                        source_kind="web_result",
                        identity=f"web:{locator}",
                        records=records,
                    )

    if tool_name == "selected_favorite_read":
        item = bound.get("item")
        if isinstance(item, MutableMapping):
            source_identity = _bounded_identity(item.get("id") or bound.get("index"))
            if source_identity and (item.get("title") or item.get("abstract") or item.get("desc")):
                _bind_item(
                    item,
                    tool_name=tool_name,
                    source_kind="selected_favorite",
                    identity=f"favorite:{source_identity}",
                    records=records,
                )

    if tool_name in {"workspace_read_file", "knowledge_read_file"}:
        content = bound.get("content")
        file_identity = _bounded_identity(
            bound.get("path") or bound.get("filename") or bound.get("name")
        )
        if isinstance(content, str) and content.strip() and file_identity:
            record = _source_record(
                tool_name=tool_name,
                source_kind="user_file",
                identity=f"file:{file_identity}",
            )
            bound["citation_source_id"] = record.source_id
            records.append(record)

    deduplicated: dict[str, InteractiveSourceRecord] = {}
    for record in records:
        deduplicated.setdefault(record.source_id, record)
    bound["citation_sources"] = [
        record.public_dict()
        for record in list(deduplicated.values())[:MAX_INTERACTIVE_SOURCES]
    ]
    return bound


def interactive_source_records(
    tool_results: Sequence[Mapping[str, Any]],
) -> tuple[InteractiveSourceRecord, ...]:
    records: dict[str, InteractiveSourceRecord] = {}
    for result in tool_results:
        raw_sources = result.get("citation_sources")
        if not isinstance(raw_sources, list):
            continue
        for raw in raw_sources:
            if not isinstance(raw, Mapping):
                continue
            source_id = str(raw.get("source_id") or "")
            binding_sha256 = str(raw.get("binding_sha256") or "")
            source_kind = _bounded_identity(raw.get("source_kind"), 80)
            tool_name = _bounded_identity(raw.get("tool_name"), 80)
            if (
                not _SOURCE_ID_RE.fullmatch(source_id)
                or not re.fullmatch(r"[0-9a-f]{64}", binding_sha256)
                or source_id != f"GM-T-{binding_sha256[:16].upper()}"
                or not source_kind
                or not tool_name
            ):
                continue
            records.setdefault(
                source_id,
                InteractiveSourceRecord(
                    source_id=source_id,
                    source_kind=source_kind,
                    tool_name=tool_name,
                    binding_sha256=binding_sha256,
                ),
            )
            if len(records) >= MAX_INTERACTIVE_SOURCES:
                return tuple(sorted(records.values(), key=lambda item: item.source_id))
    return tuple(sorted(records.values(), key=lambda item: item.source_id))


def interactive_source_set_sha256(sources: Sequence[InteractiveSourceRecord]) -> str:
    return _sha256(
        {
            "schema_version": INTERACTIVE_SOURCE_SCHEMA_VERSION,
            "sources": [record.public_dict() for record in sources],
        }
    )


def _unstructured_claim_metadata() -> dict[str, Any]:
    """Describe the intentionally open per-claim contract without body parsing."""

    return {
        "claim_ids": [],
        "claim_partition_state": "not_established",
        "structured_claim_records": "not_available",
        "per_claim_unknown_state": "not_available",
        "claim_id_reason_code": UNSTRUCTURED_CLAIM_REASON_CODE,
    }


def _has_meaningful_text(value: str) -> bool:
    without_markers = _GM_MARKER_RE.sub("", value)
    return re.search(r"[A-Za-z0-9\u3400-\u9fff]", without_markers) is not None


def _substantive_blocks(markdown: str) -> tuple[str, ...]:
    blocks: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if paragraph:
            value = " ".join(paragraph).strip()
            paragraph.clear()
            if _has_meaningful_text(value):
                blocks.append(value)

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            flush()
            continue
        if (
            line.startswith("#")
            or _MARKDOWN_SEPARATOR_RE.fullmatch(line)
            or _TABLE_SEPARATOR_RE.fullmatch(line)
        ):
            flush()
            continue
        if re.match(r"^(?:[-+*]|\d+[.)])\s+", line) or line.startswith("|"):
            flush()
            if _has_meaningful_text(line):
                blocks.append(line)
            continue
        paragraph.append(line)
    flush()
    return tuple(blocks)


def assure_interactive_output(
    content: str,
    sources: Sequence[InteractiveSourceRecord],
    *,
    evidence_required: bool,
) -> dict[str, Any]:
    """Validate a complete model output without retaining its body in metadata."""

    if not isinstance(content, str) or not content.strip():
        raise InteractiveCitationError("MODEL_OUTPUT_EMPTY")
    if len(content) > MAX_INTERACTIVE_OUTPUT_LENGTH:
        raise InteractiveCitationError("MODEL_OUTPUT_LIMIT_EXCEEDED")
    if _CONTROL_RE.search(content):
        raise InteractiveCitationError("MODEL_OUTPUT_CONTROL_CHARACTER")
    if _ACTIVE_MARKUP_RE.search(content):
        raise InteractiveCitationError("MODEL_OUTPUT_ACTIVE_MARKUP")
    if _RAW_HTML_RE.search(content):
        raise InteractiveCitationError("MODEL_OUTPUT_RAW_HTML")
    if _MARKDOWN_IMAGE_RE.search(content):
        raise InteractiveCitationError("MODEL_OUTPUT_IMAGE")
    if _NUMERIC_CITATION_RE.search(content):
        raise InteractiveCitationError("IMPLICIT_NUMERIC_CITATION_FORBIDDEN")

    allowed = {source.source_id for source in sources}
    used_markers = set(_GM_MARKER_RE.findall(content))
    used_sources = sorted(marker for marker in used_markers if marker != "GM-UNKNOWN")
    out_of_scope = sorted(set(used_sources) - allowed)
    if out_of_scope:
        raise InteractiveCitationError("CITATION_SOURCE_ID_OUT_OF_SCOPE")

    blocks = _substantive_blocks(content)
    if not blocks:
        raise InteractiveCitationError("SUBSTANTIVE_BLOCKS_EMPTY")
    cited = 0
    explicit_unknown = 0
    uncited = 0
    for block in blocks:
        block_markers = set(_GM_MARKER_RE.findall(block))
        block_sources = {marker for marker in block_markers if marker in allowed}
        if block_sources:
            cited += 1
        elif "GM-UNKNOWN" in block_markers:
            explicit_unknown += 1
        else:
            uncited += 1
    if evidence_required and uncited:
        raise InteractiveCitationError(
            "SUBSTANTIVE_BLOCK_WITHOUT_CURRENT_SOURCE_OR_UNKNOWN"
        )

    source_set_hash = interactive_source_set_sha256(sources)
    return {
        "schema_version": INTERACTIVE_CITATION_SCHEMA_VERSION,
        "source_schema_version": INTERACTIVE_SOURCE_SCHEMA_VERSION,
        "status": "review_required",
        "publication_eligibility": "blocked_pending_human_review",
        "evidence_required": evidence_required,
        "evidence_state": (
            "bounded_current_turn_sources"
            if cited
            else "explicit_unknown"
            if explicit_unknown
            else "not_required"
        ),
        **_unstructured_claim_metadata(),
        "source_count": len(sources),
        "source_set_sha256": source_set_hash,
        "hash_assurance": {
            "scope": "current_turn_source_identifier_set_fingerprint_only",
            "read_time_integrity_verification": "not_performed",
            "worm_or_signature_assurance": "unavailable",
        },
        "citations_used": used_sources,
        "substantive_blocks_total": len(blocks),
        "substantive_blocks_cited": cited,
        "substantive_blocks_explicit_unknown": explicit_unknown,
        "substantive_blocks_uncited": uncited,
        "checks": {
            "generation_complete": "passed",
            "source_identifier_boundary": "passed",
            "substantive_block_disposition": (
                "passed" if evidence_required else "not_required"
            ),
            "implicit_numeric_citation": "absent",
            "active_markup_or_image": "absent",
            "source_truth": "not_verified",
            "semantic_entailment": "not_verified",
            "fact_check": "not_performed",
            "human_review": "required_for_formal_use",
        },
        "metadata_retention": {
            "user_body": "not_recorded",
            "model_output_body": "not_recorded",
            "secret": "not_recorded",
            "base_url": "not_recorded",
        },
        "reason_codes": [
            # Markdown blocks are citation-disposition units only.  A block can
            # contain multiple assertions, so it must never be hashed or exposed
            # as though it were one structured claim.
            UNSTRUCTURED_CLAIM_REASON_CODE,
            "SOURCE_TRUTH_NOT_VERIFIED",
            "SEMANTIC_ENTAILMENT_NOT_VERIFIED",
            "FACT_CHECK_NOT_PERFORMED",
            "HUMAN_REVIEW_REQUIRED_FOR_FORMAL_USE",
        ],
    }


def _safe_unknown_output(
    sources: Sequence[InteractiveSourceRecord],
    reason_codes: Iterable[str],
    *,
    evidence_required: bool,
) -> InteractiveOutput:
    safe_reasons = tuple(
        dict.fromkeys(
            code
            for code in (str(reason) for reason in reason_codes)
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code)
        )
    ) or ("INTERACTIVE_CITATION_BOUNDARY_FAILED",)
    content = (
        "本轮生成结果未通过完整性或证据引用边界，无法安全展示；"
        f"相关事实状态为未知。{UNKNOWN_CITATION_MARKER}"
    )
    return InteractiveOutput(
        content=content,
        assurance={
            "schema_version": INTERACTIVE_CITATION_SCHEMA_VERSION,
            "source_schema_version": INTERACTIVE_SOURCE_SCHEMA_VERSION,
            "status": "blocked_replaced_unknown",
            "publication_eligibility": "blocked",
            "evidence_required": evidence_required,
            "evidence_state": "explicit_unknown",
            **_unstructured_claim_metadata(),
            "source_count": len(sources),
            "source_set_sha256": interactive_source_set_sha256(sources),
            "hash_assurance": {
                "scope": "current_turn_source_identifier_set_fingerprint_only",
                "read_time_integrity_verification": "not_performed",
                "worm_or_signature_assurance": "unavailable",
            },
            "citations_used": [],
            "checks": {
                "generation_complete": (
                    "failed"
                    if "MODEL_GENERATION_INCOMPLETE" in safe_reasons
                    else "unverified"
                ),
                "source_identifier_boundary": "failed_or_unavailable",
                "substantive_block_disposition": "server_replaced_unknown",
                "source_truth": "not_verified",
                "semantic_entailment": "not_verified",
                "fact_check": "not_performed",
                "human_review": "required_for_formal_use",
            },
            "metadata_retention": {
                "user_body": "not_recorded",
                "model_output_body": "not_recorded",
                "secret": "not_recorded",
                "base_url": "not_recorded",
            },
            "reason_codes": list(safe_reasons),
        },
    )


def finalize_interactive_output(
    model_output: str,
    tool_results: Sequence[Mapping[str, Any]],
    *,
    evidence_required: bool,
    generation_complete: bool,
    require_structured_claims: bool = False,
) -> InteractiveOutput:
    """Return only validated output or a server-authored explicit unknown."""

    sources = interactive_source_records(tool_results)
    if not generation_complete:
        return _safe_unknown_output(
            sources,
            ("MODEL_GENERATION_INCOMPLETE",),
            evidence_required=evidence_required,
        )
    if model_output.lstrip().startswith("{"):
        try:
            structured = finalize_structured_claim_output(
                model_output,
                source_bindings={
                    source.source_id: source.binding_sha256 for source in sources
                },
            )
            assurance = assure_interactive_output(
                structured.content,
                sources,
                evidence_required=evidence_required,
            )
        except StructuredClaimError as exc:
            return _safe_unknown_output(
                sources,
                (exc.reason_code,),
                evidence_required=evidence_required,
            )
        assurance.update(structured.metadata)
        assurance["reason_codes"] = [
            reason
            for reason in assurance["reason_codes"]
            if reason != UNSTRUCTURED_CLAIM_REASON_CODE
        ]
        assurance["reason_codes"].insert(
            0, "STRUCTURED_CLAIM_PARTITION_MODEL_DECLARED_NOT_VERIFIED"
        )
        return InteractiveOutput(content=structured.content, assurance=assurance)
    if require_structured_claims:
        return _safe_unknown_output(
            sources,
            ("STRUCTURED_CLAIM_OUTPUT_REQUIRED",),
            evidence_required=evidence_required,
        )
    try:
        assurance = assure_interactive_output(
            model_output,
            sources,
            evidence_required=evidence_required,
        )
    except InteractiveCitationError as exc:
        return _safe_unknown_output(
            sources,
            exc.reason_codes,
            evidence_required=evidence_required,
        )
    return InteractiveOutput(content=model_output.strip(), assurance=assurance)


__all__ = (
    "INTERACTIVE_CITATION_SCHEMA_VERSION",
    "INTERACTIVE_SOURCE_SCHEMA_VERSION",
    "InteractiveCitationError",
    "InteractiveOutput",
    "InteractiveSourceRecord",
    "MAX_INTERACTIVE_OUTPUT_LENGTH",
    "UNSTRUCTURED_CLAIM_REASON_CODE",
    "UNKNOWN_CITATION_MARKER",
    "assure_interactive_output",
    "bind_interactive_tool_result",
    "finalize_interactive_output",
    "interactive_source_records",
    "interactive_source_set_sha256",
)
