"""Build article claims that only cite verifiable body paragraphs."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from .contracts import (
    ArticleEvidenceChain,
    ClaimType,
    EvidenceClaim,
    EvidenceProvenance,
    ParagraphCitation,
)

EVIDENCE_SCHEMA_VERSION = "article-evidence-v1"
_CHINA_BODY_TERMS = (
    "China",
    "Chinese",
    "Beijing",
    "PRC",
    "中国",
    "中國",
    "北京",
    "中方",
    "大陆",
    "大陸",
)


def normalize_claim_type(value: Any) -> ClaimType:
    try:
        return ClaimType(str(value or "").strip().lower())
    except ValueError:
        return ClaimType.UNKNOWN


def split_article_paragraphs(body: Any) -> list[str]:
    """Match the article reader's deterministic paragraph segmentation."""
    text = str(body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    lines = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    paragraphs: list[str] = []
    for line in lines:
        if len(line) <= 280:
            paragraphs.append(line)
            continue
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[。！？!?.])\s+", line)
            if part.strip()
        ]
        if len(sentences) <= 1:
            paragraphs.append(line)
            continue
        buffer = ""
        for sentence in sentences:
            if not buffer:
                buffer = sentence
            elif len(f"{buffer} {sentence}") > 240:
                paragraphs.append(buffer)
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}"
        if buffer:
            paragraphs.append(buffer)
    return paragraphs


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _excerpt(paragraph: str, start: int, end: int, limit: int = 220) -> str:
    if len(paragraph) <= limit:
        return paragraph
    padding = max(0, (limit - (end - start)) // 2)
    left = max(0, start - padding)
    right = min(len(paragraph), max(end + padding, left + limit))
    left = max(0, right - limit)
    prefix = "…" if left else ""
    suffix = "…" if right < len(paragraph) else ""
    return f"{prefix}{paragraph[left:right]}{suffix}"


def _candidate_fragments(values: Iterable[Any]) -> list[str]:
    fragments: list[str] = []
    for value in values:
        fragments.extend(re.split(r"[；;\n]+", _normalized(value)))
    return list(dict.fromkeys(fragment for fragment in fragments if fragment))


def _safe_citation_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 4000
        or "\\" in raw
        or any(ord(character) <= 32 or ord(character) == 127 for character in raw)
    ):
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", "", ""))


def _find_candidate(paragraph: str, candidate: str) -> tuple[int, int] | None:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/-]*", candidate):
        if len(candidate) < 4:
            return None
        match = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])",
            paragraph,
            re.IGNORECASE,
        )
        return (match.start(), match.end()) if match else None
    start = paragraph.casefold().find(candidate.casefold())
    return (start, start + len(candidate)) if start >= 0 else None


def locate_paragraph_citations(
    *,
    article_id: int,
    body: Any,
    title: Any = "",
    evidence_fragments: Sequence[Any] = (),
    anchor_terms: Sequence[Any] = (),
    source_url: str | None = None,
    limit: int = 3,
) -> tuple[list[ParagraphCitation], bool]:
    """Locate exact body evidence; never fall back to the article title."""
    source_url = _safe_citation_url(source_url)
    paragraphs = split_article_paragraphs(body)
    normalized_title = _normalized(title).casefold()
    fragments = _candidate_fragments(evidence_fragments)
    title_only_rejected = any(
        fragment.casefold() == normalized_title for fragment in fragments if normalized_title
    )
    candidates = [
        fragment
        for fragment in fragments
        if len(fragment) >= 8 and fragment.casefold() != normalized_title
    ]
    candidates.extend(
        term
        for term in _candidate_fragments(anchor_terms)
        if len(term) >= 2 and term.casefold() != normalized_title
    )
    candidates = list(dict.fromkeys(candidates))
    citations: list[ParagraphCitation] = []
    seen_paragraphs: set[int] = set()
    for paragraph_number, paragraph in enumerate(paragraphs, start=1):
        for candidate in candidates:
            located = _find_candidate(paragraph, candidate)
            if located is None or paragraph_number in seen_paragraphs:
                continue
            start, end = located
            citations.append(
                ParagraphCitation(
                    article_id=article_id,
                    paragraph_number=paragraph_number,
                    anchor_id=f"article-{article_id}-paragraph-{paragraph_number}",
                    matched_text=paragraph[start:end],
                    excerpt=_excerpt(paragraph, start, end),
                    source_url=source_url or None,
                )
            )
            seen_paragraphs.add(paragraph_number)
            break
        if len(citations) >= limit:
            break
    return citations, title_only_rejected


def _unavailable_reason(
    paragraphs: Sequence[str],
    title_only_rejected: bool,
) -> str:
    if not paragraphs:
        return "BODY_UNAVAILABLE"
    if title_only_rejected:
        return "TITLE_ONLY_EVIDENCE_REJECTED"
    return "PARAGRAPH_ANCHOR_NOT_FOUND"


def _claim(
    *,
    claim_id: str,
    claim_type: ClaimType,
    text: str,
    source: str,
    citations: list[ParagraphCitation],
    paragraphs: Sequence[str],
    title_only_rejected: bool,
) -> EvidenceClaim:
    return EvidenceClaim(
        id=claim_id,
        claim_type=claim_type,
        text=text,
        source=source,
        evidence_status="available" if citations else "unavailable",
        citations=citations,
        unavailable_reason=(
            None
            if citations
            else _unavailable_reason(paragraphs, title_only_rejected)
        ),
    )


def build_article_evidence_chain(
    article: Any,
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the V1 article evidence slice without inventing snapshots or sources."""
    article_id = int(getattr(article, "id", 0) or 0)
    title = getattr(article, "title", "") or ""
    body = getattr(article, "body", "") or ""
    source_url = getattr(article, "request_url", None)
    paragraphs = split_article_paragraphs(body)
    analysis = analysis if isinstance(analysis, dict) else {}
    claims: list[EvidenceClaim] = []

    china = analysis.get("china_analysis")
    if isinstance(china, dict):
        citations, rejected = locate_paragraph_citations(
            article_id=article_id,
            body=body,
            title=title,
            evidence_fragments=(china.get("evidence"),),
            anchor_terms=_CHINA_BODY_TERMS,
            source_url=source_url,
        )
        related = "涉华" if china.get("is_china_related") else "非涉华"
        claims.append(
            _claim(
                claim_id=f"article:{article_id}:china-judgment",
                claim_type=ClaimType.JUDGMENT,
                text=f"系统将该文章判断为{related}。",
                source=str(china.get("source") or "article-analysis"),
                citations=citations,
                paragraphs=paragraphs,
                title_only_rejected=rejected,
            )
        )
        indicator_text = (
            "涉华相关度、影响和置信指标分别为 "
            f"{china.get('relevance_score', '—')}、"
            f"{china.get('impact_index', '—')}、"
            f"{china.get('confidence', '—')}。"
        )
        claims.append(
            _claim(
                claim_id=f"article:{article_id}:china-indicators",
                claim_type=ClaimType.INDICATOR,
                text=indicator_text,
                source=str(china.get("source") or "article-analysis"),
                citations=list(citations),
                paragraphs=paragraphs,
                title_only_rejected=rejected,
            )
        )

    extraction = analysis.get("event_extraction")
    if isinstance(extraction, dict):
        for field, label in (
            ("initiator", "事件发起方"),
            ("target", "事件目标"),
            ("event_action", "事件行动"),
        ):
            value = _normalized(extraction.get(field))
            if not value:
                continue
            citations, rejected = locate_paragraph_citations(
                article_id=article_id,
                body=body,
                title=title,
                anchor_terms=(value,),
                source_url=source_url,
            )
            claims.append(
                _claim(
                    claim_id=f"article:{article_id}:event-{field}",
                    claim_type=ClaimType.HYPOTHESIS,
                    text=f"模型抽取的{label}为 {value}。",
                    source=str(
                        extraction.get("processor_version") or "event-extraction"
                    ),
                    citations=citations,
                    paragraphs=paragraphs,
                    title_only_rejected=rejected,
                )
            )

    if not claims:
        claims.append(
            EvidenceClaim(
                id=f"article:{article_id}:analysis-unknown",
                claim_type=ClaimType.UNKNOWN,
                text="当前没有可分类且可引用的文章分析主张。",
                source="article-analysis",
                evidence_status="unavailable",
                unavailable_reason=(
                    "BODY_UNAVAILABLE" if not paragraphs else "ANALYSIS_UNAVAILABLE"
                ),
            )
        )

    normalized_body = "\n\n".join(paragraphs)
    provenance = EvidenceProvenance(
        body_status="available" if paragraphs else "unavailable",
        response_body_sha256=(
            hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
            if normalized_body
            else None
        ),
        hash_scope="normalized-display-body" if normalized_body else None,
    )
    contract = ArticleEvidenceChain(
        article_id=article_id,
        paragraph_count=len(paragraphs),
        claims=claims,
        provenance=provenance,
    )
    return contract.model_dump(mode="json")


__all__ = (
    "EVIDENCE_SCHEMA_VERSION",
    "build_article_evidence_chain",
    "locate_paragraph_citations",
    "normalize_claim_type",
    "split_article_paragraphs",
)
