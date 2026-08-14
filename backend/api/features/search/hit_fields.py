"""Bounded display-only hit offsets for search result presentation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from api.models.schemas import SearchHitDisclosure, SearchHitSpan

SEARCH_HIT_SCHEMA_VERSION = "search-hit-display-v1"
_MAX_TERMS = 64
_MAX_TERM_CODE_POINTS = 160
_MAX_SPANS = 64
_ALLOWED_FIELDS = frozenset({"news.title", "news.body"})


def _literal_terms(values: Iterable[object]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        term = value.strip()
        folded = term.casefold()
        if not term or len(term) > _MAX_TERM_CODE_POINTS or folded in seen:
            continue
        seen.add(folded)
        terms.append(term)
        if len(terms) >= _MAX_TERMS:
            break
    return sorted(terms, key=lambda item: (-len(item), item.casefold()))


def _field_spans(
    field: str,
    text_value: str,
    terms: Sequence[str],
) -> list[SearchHitSpan]:
    candidates: list[tuple[int, int]] = []
    for term in terms:
        candidates.extend(
            (match.start(), match.end())
            for match in re.finditer(
                re.escape(term),
                text_value,
                flags=re.IGNORECASE,
            )
        )
    selected: list[tuple[int, int]] = []
    for start, end in sorted(
        candidates,
        key=lambda item: (item[0], -(item[1] - item[0]), item[1]),
    ):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end))
        if len(selected) >= _MAX_SPANS:
            break
    return [
        SearchHitSpan(field=field, start=start, end=end)
        for start, end in selected
    ]


def build_search_hit_disclosure(
    *,
    title: object,
    abstract: object,
    positive_literal_terms: Iterable[object],
    effective_search_fields: Sequence[object],
) -> SearchHitDisclosure:
    """Return code-point offsets without echoing query terms or emitting markup."""

    fields = [
        value
        for value in effective_search_fields
        if isinstance(value, str) and value in _ALLOWED_FIELDS
    ]
    fields = list(dict.fromkeys(fields))[:2]
    terms = _literal_terms(positive_literal_terms)
    if not fields or not terms:
        return SearchHitDisclosure(
            status="unavailable",
            effective_search_fields=fields,
            reason_code="SEARCH_TERMS_NOT_AVAILABLE",
        )

    spans: list[SearchHitSpan] = []
    if "news.title" in fields:
        spans.extend(
            _field_spans(
                "title",
                title if isinstance(title, str) else "",
                terms,
            )
        )
    if "news.body" in fields and len(spans) < _MAX_SPANS:
        spans.extend(
            _field_spans(
                "abstract",
                abstract if isinstance(abstract, str) else "",
                terms,
            )[: _MAX_SPANS - len(spans)]
        )
    return SearchHitDisclosure(
        status="available" if spans else "no_display_span",
        effective_search_fields=fields,
        reason_code=(
            "DISPLAY_LITERAL_MATCHES_FOUND"
            if spans
            else "NO_LITERAL_SPAN_IN_RETURNED_DISPLAY_TEXT"
        ),
        spans=spans,
    )
