#!/usr/bin/env python3
from __future__ import annotations

import ipaddress
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from heapq import nsmallest
from itertools import islice
from typing import Any
from urllib.parse import urlparse

try:  # Support both ``python scripts/...`` and package-style offline imports.
    from .news_date_cleaning import ensure_aware, parse_datetime
except ImportError:  # pragma: no cover - exercised by the standalone CLI path.
    from news_date_cleaning import ensure_aware, parse_datetime


MIN_PUBLISHED_YEAR = 2000
MIN_BODY_CHARS = 120
FUTURE_GRACE_DAYS = 1
QUALITY_PROFILE_SCHEMA_VERSION = "news-quality-profile-v3"
QUALITY_PROFILE_METHOD_VERSION = "news-ingest-quality-profile-v3"
NEAR_DUPLICATE_METHOD_VERSION = "bounded-char5-bottom32-rolling64-lsh8-v1"
MAX_PROFILE_ROWS = 100_000
DEFAULT_PROFILE_MAX_ROWS = MAX_PROFILE_ROWS
MAX_PROFILE_SLICE_VALUES = 64
DEFAULT_MAX_CANDIDATE_PAIR_COMPARISONS = 200_000
MAX_CANDIDATE_PAIR_COMPARISONS = 1_000_000
MAX_NEAR_DUPLICATE_TEXT_CHARS = 4_096
MAX_NEAR_DUPLICATE_ROWS = 20_000
NEAR_DUPLICATE_SHINGLE_CHARS = 5
MIN_NEAR_DUPLICATE_TEXT_CHARS = 80
MIN_NEAR_DUPLICATE_DISTINCT_SHINGLES = 32
NEAR_DUPLICATE_BOTTOM_K = 32
NEAR_DUPLICATE_LSH_KEYS = 8
NEAR_DUPLICATE_MINIMUM_SIMILARITY = 0.8
MAX_NEAR_DUPLICATE_BUCKET_ROWS = 64

KNOWN_ARTICLE_FIELDS = frozenset(
    {
        "_proxy_name",
        "_attempt",
        "abstract",
        "author",
        "body",
        "content_md5",
        "discovery_method",
        "domain",
        "error",
        "extraction_method",
        "failed_at",
        "fetch_status",
        "fetched_at",
        "historical_strategy",
        "id",
        "language",
        "lang",
        "lastmod",
        "layer",
        "news_id",
        "priority_tier",
        "published_at",
        "published_at_confidence",
        "published_at_raw",
        "published_at_source",
        "request_url",
        "response_url",
        "site_id",
        "sitemap_url",
        "source",
        "source_url",
        "title",
        "url",
    }
)

LANGUAGE_TAG_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}")
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

GENERIC_TITLES = {
    "news",
    "world",
    "international",
    "politics",
    "business",
    "economy",
    "markets",
    "sport",
    "sports",
    "football",
    "latest news",
    "breaking news",
    "editorial standards",
    "privacy policy",
    "terms of use",
    "terms and conditions",
    "contact us",
    "about us",
    "sitemap",
    "rss feed",
}

PAGE_TITLE_RE = re.compile(
    r"\b("
    r"editorial standards|privacy policy|terms (?:of use|and conditions)|"
    r"contact us|about us|sitemap|rss feed|newsletters?|subscribe|"
    r"fixtures?|standings?|score(?:s|board)?|results?|"
    r"weather forecast|tv schedule|programmes?"
    r")\b",
    re.IGNORECASE,
)
PAGE_URL_RE = re.compile(
    r"/("
    r"tag|tags|topic|topics|category|categories|section|sections|"
    r"author|authors|search|privacy|terms|about|contact|newsletter|"
    r"subscribe|sitemap|weather|scores|fixtures|standings|results|"
    r"programmes|schedule|live-tv"
    r")(?=/|\?|#|$)",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"(enable javascript|please enable javascript|请启用javascript|"
    r"subscribe to continue|sign in to continue|cookies? policy)",
    re.IGNORECASE,
)

QUALITY_REASON_CODES = frozenset(
    {
        "empty_title",
        "missing_url",
        "missing_body",
        "body_too_short",
        "page_like_title",
        "invalid_url",
        "page_like_url",
        "placeholder_body",
        "missing_published_at",
        "published_before_min_year",
        "published_future_too_far",
    }
)


@dataclass(frozen=True)
class QualityResult:
    is_good: bool
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return "good" if self.is_good else "bad"


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _now_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return ensure_aware(now)


def assess_news_row(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    min_body_chars: int = MIN_BODY_CHARS,
    min_published_year: int = MIN_PUBLISHED_YEAR,
    future_grace_days: int = FUTURE_GRACE_DAYS,
) -> QualityResult:
    title = normalize_space(row.get("title"))
    body = str(row.get("body") or "").strip()
    url = str(row.get("url") or row.get("response_url") or row.get("request_url") or "").strip()
    published_at = parse_datetime(row.get("published_at"))
    reasons: list[str] = []

    if not title:
        reasons.append("empty_title")
    if not url:
        reasons.append("missing_url")
    if not body:
        reasons.append("missing_body")
    elif len(body) < min_body_chars:
        reasons.append("body_too_short")

    title_lower = title.lower()
    if title_lower in GENERIC_TITLES or PAGE_TITLE_RE.search(title):
        reasons.append("page_like_title")

    canonical_url = _canonical_url(url) if url else None
    if url and canonical_url is None:
        reasons.append("invalid_url")
    parsed = urlparse(canonical_url or "")
    path = parsed.path or ""
    if PAGE_URL_RE.search(path):
        reasons.append("page_like_url")

    head = body[:1200]
    if PLACEHOLDER_RE.search(head):
        reasons.append("placeholder_body")

    if published_at is None:
        reasons.append("missing_published_at")
    else:
        published_at = ensure_aware(published_at)
        if published_at.year < min_published_year:
            reasons.append("published_before_min_year")
        if published_at > _now_utc(now) + timedelta(days=future_grace_days):
            reasons.append("published_future_too_far")

    return QualityResult(is_good=not reasons, reasons=tuple(dict.fromkeys(reasons)))


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _canonical_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or any(ord(character) <= 32 or ord(character) == 127 for character in raw):
        return None
    try:
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not _is_public_dns_hostname(hostname)
    ):
        return None
    authority = hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        authority = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return f"{scheme}://{authority}{path}"


def _is_public_dns_hostname(hostname: str) -> bool:
    value = hostname.rstrip(".").lower()
    if not value or value == "localhost" or "." not in value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        # Source slices deliberately never retain literal IP addresses.
        return False
    if value.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ascii_value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_value) > 253:
        return False
    return all(DNS_LABEL_RE.fullmatch(label) for label in ascii_value.split("."))


def _normalized_language(value: Any) -> str:
    normalized = normalize_space(value).lower().replace("_", "-")
    if not normalized:
        return "und"
    if len(normalized) <= 35 and LANGUAGE_TAG_RE.fullmatch(normalized):
        return normalized
    return "invalid"


def _record_slice(
    counter: dict[str, Counter[str]], value: str, *, is_good: bool
) -> None:
    item = counter.setdefault(value, Counter())
    item["evaluated_rows"] += 1
    item["good_count" if is_good else "bad_count"] += 1


def _slice_summary(
    counter: dict[str, Counter[str]], *, value_policy: str
) -> dict[str, Any]:
    ordered = sorted(
        counter.items(),
        key=lambda item: (-item[1]["evaluated_rows"], item[0]),
    )
    retained = ordered[:MAX_PROFILE_SLICE_VALUES]
    overflow = ordered[MAX_PROFILE_SLICE_VALUES:]
    items = []
    for value, counts in retained:
        evaluated_rows = counts["evaluated_rows"]
        items.append(
            {
                "value": value,
                "evaluated_rows": evaluated_rows,
                "good_count": counts["good_count"],
                "bad_count": counts["bad_count"],
                "bad_rate": _ratio(counts["bad_count"], evaluated_rows),
            }
        )
    return {
        "items": items,
        "distinct_values": len(ordered),
        "overflow_values": len(overflow),
        "overflow_rows": sum(
            counts["evaluated_rows"] for _value, counts in overflow
        ),
        "value_policy": value_policy,
    }


def _content_fingerprint(row: dict[str, Any]) -> str | None:
    title = normalize_space(row.get("title")).casefold()
    body = normalize_space(row.get("body")).casefold()
    if not title and not body:
        return None
    return sha256(
        f"{title}\n{body}".encode("utf-8", errors="surrogatepass")
    ).hexdigest()


def _bounded_near_duplicate_text(
    row: dict[str, Any],
) -> tuple[str | None, bool]:
    """Return bounded normalized text, rejecting unsafe/low-information input."""

    title = row.get("title")
    body = row.get("body")
    if title is not None and not isinstance(title, str):
        return None, False
    if body is not None and not isinstance(body, str):
        return None, False
    title_value = title or ""
    body_value = body or ""
    raw_length = len(title_value) + 1 + len(body_value)
    truncated = raw_length > MAX_NEAR_DUPLICATE_TEXT_CHARS
    title_part = title_value[:MAX_NEAR_DUPLICATE_TEXT_CHARS]
    remaining = MAX_NEAR_DUPLICATE_TEXT_CHARS - len(title_part)
    raw = title_part
    if remaining > 0:
        raw += "\n"
        remaining -= 1
        raw += body_value[:remaining]
    if any(
        0xD800 <= ord(character) <= 0xDFFF
        or (
            unicodedata.category(character).startswith("C")
            and character not in "\t\n\r"
        )
        for character in raw
    ):
        return None, truncated
    try:
        normalized = unicodedata.normalize("NFKC", raw).casefold()
    except (TypeError, ValueError):
        return None, truncated
    normalized = normalize_space(normalized)
    if len(normalized) > MAX_NEAR_DUPLICATE_TEXT_CHARS:
        normalized = normalized[:MAX_NEAR_DUPLICATE_TEXT_CHARS]
        truncated = True
    if (
        len(normalized) < MIN_NEAR_DUPLICATE_TEXT_CHARS
        or len(set(normalized)) < 8
    ):
        return None, truncated
    return normalized, truncated


def _near_duplicate_bottom_k(text: str) -> tuple[int, ...] | None:
    """Build a deterministic bottom-k sketch with a linear rolling hash."""

    width = NEAR_DUPLICATE_SHINGLE_CHARS
    if len(text) < width:
        return None
    mask = (1 << 64) - 1
    base = 257
    leading_power = pow(base, width - 1, 1 << 64)
    rolling = 0
    for character in text[:width]:
        rolling = ((rolling * base) + ord(character) + 1) & mask
    distinct_hashes = {rolling}
    for index in range(width, len(text)):
        outgoing = ord(text[index - width]) + 1
        incoming = ord(text[index]) + 1
        rolling = (
            ((rolling - outgoing * leading_power) * base) + incoming
        ) & mask
        distinct_hashes.add(rolling)
    if len(distinct_hashes) < MIN_NEAR_DUPLICATE_DISTINCT_SHINGLES:
        return None
    return tuple(nsmallest(NEAR_DUPLICATE_BOTTOM_K, distinct_hashes))


def _bottom_k_similarity_at_least_threshold(
    first: tuple[int, ...], second: tuple[int, ...]
) -> bool:
    first_values = set(first)
    second_values = set(second)
    intersection = len(first_values & second_values)
    union = len(first_values | second_values)
    # The published 0.8 candidate rule is evaluated exactly as 4/5.
    return union > 0 and intersection * 5 >= union * 4


def _near_duplicate_observation(
    sampled: list[dict[str, Any]],
    *,
    max_candidate_pair_comparisons: int,
    profile_truncated: bool,
) -> dict[str, Any]:
    buckets: dict[int, list[int]] = {}
    records: list[tuple[tuple[int, ...], str | None]] = []
    eligible_rows = 0
    ineligible_rows = 0
    text_truncated_rows = 0
    candidate_pairs_compared = 0
    candidate_pairs_observed = 0
    exact_duplicate_pairs_excluded = 0
    candidate_pairs_skipped_at_least = 0
    bucket_overflow_events = 0

    evaluated = sampled[:MAX_NEAR_DUPLICATE_ROWS]
    for row in evaluated:
        text, text_truncated = _bounded_near_duplicate_text(row)
        if text_truncated:
            text_truncated_rows += 1
        sketch = _near_duplicate_bottom_k(text) if text is not None else None
        if sketch is None:
            ineligible_rows += 1
            continue
        eligible_rows += 1
        exact_fingerprint = _content_fingerprint(row)
        candidate_indexes: set[int] = set()
        bucket_keys = sketch[:NEAR_DUPLICATE_LSH_KEYS]
        for key in bucket_keys:
            candidate_indexes.update(buckets.get(key, ()))

        for candidate_index in sorted(candidate_indexes):
            if candidate_pairs_compared >= max_candidate_pair_comparisons:
                candidate_pairs_skipped_at_least += 1
                continue
            candidate_pairs_compared += 1
            prior_sketch, prior_exact_fingerprint = records[candidate_index]
            if not _bottom_k_similarity_at_least_threshold(sketch, prior_sketch):
                continue
            if (
                exact_fingerprint is not None
                and exact_fingerprint == prior_exact_fingerprint
            ):
                exact_duplicate_pairs_excluded += 1
                continue
            candidate_pairs_observed += 1

        record_index = len(records)
        records.append((sketch, exact_fingerprint))
        for key in bucket_keys:
            bucket = buckets.setdefault(key, [])
            if len(bucket) >= MAX_NEAR_DUPLICATE_BUCKET_ROWS:
                bucket_overflow_events += 1
            else:
                bucket.append(record_index)

    comparison_overflow = candidate_pairs_skipped_at_least > 0
    return {
        "method_version": NEAR_DUPLICATE_METHOD_VERSION,
        "observation_state": "candidate_pairs_only",
        "profile_evaluated_rows": len(sampled),
        "evaluated_rows": len(evaluated),
        "row_evaluation_limit": MAX_NEAR_DUPLICATE_ROWS,
        "row_evaluation_truncated": len(sampled) > len(evaluated),
        "profile_scope_truncated": profile_truncated,
        "eligible_rows": eligible_rows,
        "ineligible_low_information_rows": ineligible_rows,
        "text_character_limit_per_row": MAX_NEAR_DUPLICATE_TEXT_CHARS,
        "text_truncated_rows": text_truncated_rows,
        "shingle_character_width": NEAR_DUPLICATE_SHINGLE_CHARS,
        "minimum_distinct_shingles": MIN_NEAR_DUPLICATE_DISTINCT_SHINGLES,
        "bottom_k_size": NEAR_DUPLICATE_BOTTOM_K,
        "candidate_generation_keys_per_row": NEAR_DUPLICATE_LSH_KEYS,
        "shingle_hash_method": "rolling64_polynomial_base257_v1",
        "candidate_pairs_compared": candidate_pairs_compared,
        "candidate_pair_comparison_limit": max_candidate_pair_comparisons,
        "candidate_pairs_skipped_at_least": candidate_pairs_skipped_at_least,
        "comparison_overflow": comparison_overflow,
        "candidate_generation_bucket_row_limit": MAX_NEAR_DUPLICATE_BUCKET_ROWS,
        "candidate_generation_bucket_overflow_events": bucket_overflow_events,
        "candidate_generation_overflow": bucket_overflow_events > 0,
        "candidate_pairs_observed": candidate_pairs_observed,
        "exact_duplicate_pairs_excluded": exact_duplicate_pairs_excluded,
        "similarity_metric": "bottom_k_set_jaccard",
        "candidate_minimum_similarity": NEAR_DUPLICATE_MINIMUM_SIMILARITY,
        "candidate_threshold_approval_state": "not_approved",
        "human_review_state": "not_provided",
        "duplicate_fact_state": "not_established",
        "release_decision": "not_computable",
        "article_content_retained": False,
        "urls_or_row_identifiers_retained": False,
    }


def _duplicate_summary(counts: Counter[str]) -> dict[str, int]:
    duplicates = [count for count in counts.values() if count > 1]
    return {
        "duplicate_groups": len(duplicates),
        "duplicate_rows": sum(duplicates),
        "excess_rows": sum(count - 1 for count in duplicates),
    }


def profile_news_rows(
    rows: Any,
    *,
    now: datetime | None = None,
    max_rows: int = DEFAULT_PROFILE_MAX_ROWS,
    max_candidate_pair_comparisons: int = (
        DEFAULT_MAX_CANDIDATE_PAIR_COMPARISONS
    ),
) -> dict[str, Any]:
    """Build a bounded aggregate profile without retaining article content.

    This is an automatic data-observability report, not a gold-standard quality
    judgment.  The returned contract deliberately has no pass/fail threshold:
    a release gate needs approved thresholds and human-reviewed labels first.
    """

    if (
        type(max_rows) is not int
        or max_rows <= 0
        or max_rows > MAX_PROFILE_ROWS
    ):  # bool must not pass as int
        raise ValueError(
            f"max_rows must be a positive integer no greater than {MAX_PROFILE_ROWS}"
        )
    if (
        type(max_candidate_pair_comparisons) is not int
        or max_candidate_pair_comparisons <= 0
        or max_candidate_pair_comparisons > MAX_CANDIDATE_PAIR_COMPARISONS
    ):
        raise ValueError(
            "candidate pair comparison limit must be a positive integer no "
            f"greater than {MAX_CANDIDATE_PAIR_COMPARISONS}"
        )

    evaluated_at = _now_utc(now)
    sampled = list(islice(iter(rows), max_rows + 1))
    truncated = len(sampled) > max_rows
    if truncated:
        sampled.pop()
    if any(not isinstance(row, dict) for row in sampled):
        raise TypeError("every profiled row must be an object")

    total = len(sampled)
    good_count = 0
    reason_counts: Counter[str] = Counter()
    present_counts: Counter[str] = Counter()
    url_counts: Counter[str] = Counter()
    content_counts: Counter[str] = Counter()
    source_domains: set[str] = set()
    valid_publication_times: list[datetime] = []
    source_slices: dict[str, Counter[str]] = {}
    language_slices: dict[str, Counter[str]] = {}
    publication_month_slices: dict[str, Counter[str]] = {}
    rows_with_unknown_fields = 0
    unknown_field_occurrences = 0
    distinct_unknown_fields: set[Any] = set()

    for row in sampled:
        result = assess_news_row(row, now=evaluated_at)
        if result.is_good:
            good_count += 1
        reason_counts.update(result.reasons)

        unknown_fields = set(row) - KNOWN_ARTICLE_FIELDS
        if unknown_fields:
            rows_with_unknown_fields += 1
            unknown_field_occurrences += len(unknown_fields)
            distinct_unknown_fields.update(unknown_fields)

        _record_slice(
            language_slices,
            _normalized_language(row.get("language") or row.get("lang")),
            is_good=result.is_good,
        )

        for field in ("title", "body", "published_at"):
            if normalize_space(row.get(field)):
                present_counts[field] += 1

        raw_url = row.get("url") or row.get("response_url") or row.get("request_url")
        canonical_url = _canonical_url(raw_url)
        if canonical_url:
            present_counts["valid_url"] += 1
            url_counts[canonical_url] += 1
            domain = (urlparse(canonical_url).hostname or "").lower()
            if domain:
                source_domains.add(domain)
                _record_slice(source_slices, domain, is_good=result.is_good)

        fingerprint = _content_fingerprint(row)
        if fingerprint:
            content_counts[fingerprint] += 1

        published_at = parse_datetime(row.get("published_at"))
        if published_at is not None:
            published_at = ensure_aware(published_at)
            if (
                published_at.year >= MIN_PUBLISHED_YEAR
                and published_at
                <= evaluated_at + timedelta(days=FUTURE_GRACE_DAYS)
            ):
                valid_publication_times.append(published_at)
                _record_slice(
                    publication_month_slices,
                    published_at.strftime("%Y-%m"),
                    is_good=result.is_good,
                )

    bad_count = total - good_count
    near_duplicate_candidates = _near_duplicate_observation(
        sampled,
        max_candidate_pair_comparisons=max_candidate_pair_comparisons,
        profile_truncated=truncated,
    )
    reason_rows = [
        {
            "code": code,
            "count": count,
            "rate": _ratio(count, total),
        }
        for code, count in sorted(reason_counts.items())
    ]
    completeness = {
        "title_present": {
            "count": present_counts["title"],
            "rate": _ratio(present_counts["title"], total),
        },
        "body_present": {
            "count": present_counts["body"],
            "rate": _ratio(present_counts["body"], total),
        },
        "published_at_present": {
            "count": present_counts["published_at"],
            "rate": _ratio(present_counts["published_at"], total),
        },
        "valid_http_url": {
            "count": present_counts["valid_url"],
            "rate": _ratio(present_counts["valid_url"], total),
        },
    }

    return {
        "schema_version": QUALITY_PROFILE_SCHEMA_VERSION,
        "method_version": QUALITY_PROFILE_METHOD_VERSION,
        "generated_at": evaluated_at.isoformat(),
        "scope": {
            "evaluated_rows": total,
            "max_rows": max_rows,
            "truncated": truncated,
            "article_content_retained": False,
            "row_identifiers_retained": False,
        },
        "labels": {
            "good_count": good_count,
            "bad_count": bad_count,
            "good_rate": _ratio(good_count, total),
            "bad_rate": _ratio(bad_count, total),
            "rule_set": "deterministic_heuristics",
            "gold_standard_state": "not_provided",
        },
        "reason_counts": reason_rows,
        "completeness": completeness,
        "exact_duplicates": {
            "url": _duplicate_summary(url_counts),
            "normalized_content": _duplicate_summary(content_counts),
            "method": "canonical_url_and_sha256_normalized_title_body",
            "near_duplicate_state": "candidate_observation_available",
        },
        "near_duplicate_candidates": near_duplicate_candidates,
        "source_coverage": {
            "distinct_domains": len(source_domains),
            "rows_with_valid_domain": present_counts["valid_url"],
            "domain_names_retained": True,
            "domain_name_policy": "public_dns_hostname_only",
        },
        "slices": {
            "source_domain": _slice_summary(
                source_slices, value_policy="public_dns_hostname_only"
            ),
            "language": _slice_summary(
                language_slices, value_policy="normalized_bcp47_or_und_or_invalid"
            ),
            "publication_month": _slice_summary(
                publication_month_slices,
                value_policy="valid_publication_time_utc_month",
            ),
            "max_values_per_dimension": MAX_PROFILE_SLICE_VALUES,
        },
        "schema_observation": {
            "known_field_set_version": "news-article-fields-v2",
            "rows_with_unknown_fields": rows_with_unknown_fields,
            "unknown_field_occurrences": unknown_field_occurrences,
            "distinct_unknown_fields": len(distinct_unknown_fields),
            "unknown_field_names_retained": False,
            "drift_assessment": (
                "observed_unreviewed"
                if rows_with_unknown_fields
                else "no_unknown_fields_observed"
            ),
        },
        "publication_time": {
            "valid_count": len(valid_publication_times),
            "earliest_at": (
                min(valid_publication_times).isoformat()
                if valid_publication_times
                else None
            ),
            "cutoff_at": (
                max(valid_publication_times).isoformat()
                if valid_publication_times
                else None
            ),
        },
        "assurance": {
            "evaluation_state": "observed" if total else "not_observed",
            "threshold_approval_state": "not_approved",
            "release_decision": "not_computable",
            "human_label_review": "not_provided",
            "limitations": [
                "Rules detect mechanical defects but do not establish factual "
                "accuracy or source reliability.",
                "Near-duplicate output is an unapproved bounded candidate "
                "observation, not a duplicate fact; language agreement, "
                "extraction fidelity, and cross-source contradiction are not "
                "measured.",
                "Source/language/month slices are mechanical aggregates; public "
                "source hostnames are retained, while private hosts and invalid "
                "language values are collapsed.",
                "No release threshold is approved; rates must not be interpreted "
                "as a pass decision.",
            ],
        },
    }


def compare_news_quality_profiles(
    baseline: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Compare two compatible profiles without inventing an approved threshold."""

    for label, profile in (("baseline", baseline), ("current", current)):
        if not isinstance(profile, dict):
            raise ValueError(f"{label} profile must be an object")
        if profile.get("schema_version") != QUALITY_PROFILE_SCHEMA_VERSION:
            raise ValueError(f"{label} profile schema is unsupported")
        if profile.get("method_version") != QUALITY_PROFILE_METHOD_VERSION:
            raise ValueError(f"{label} profile method is unsupported")

    def require_section(
        profile: dict[str, Any], section: str, label: str
    ) -> dict[str, Any]:
        value = profile.get(section)
        if not isinstance(value, dict):
            raise ValueError(f"{label} {section} must be an object")
        return value

    def rate(profile: dict[str, Any], section: str, key: str) -> float | None:
        value = profile.get(section, {}).get(key)
        if type(value) not in (int, float):
            return None
        numeric = float(value)
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError(f"{section}.{key} must be a finite rate")
        return numeric

    def evaluated_rows(profile: dict[str, Any], label: str) -> int:
        scope = require_section(profile, "scope", label)
        value = scope.get("evaluated_rows")
        if type(value) is not int or value < 0:
            raise ValueError(f"{label} evaluated_rows must be a non-negative integer")
        return value

    def cutoff(profile: dict[str, Any], label: str) -> datetime | None:
        raw = profile.get("publication_time", {}).get("cutoff_at")
        if raw is None:
            return None
        parsed = parse_datetime(raw)
        if parsed is None:
            raise ValueError(f"{label} publication cutoff is invalid")
        return ensure_aware(parsed)

    def validate_profile(profile: dict[str, Any], label: str) -> None:
        total = evaluated_rows(profile, label)
        scope = require_section(profile, "scope", label)
        max_rows = scope.get("max_rows")
        if (
            type(max_rows) is not int
            or max_rows <= 0
            or max_rows > MAX_PROFILE_ROWS
            or total > max_rows
        ):
            raise ValueError(f"{label} scope row bound is invalid")
        if type(scope.get("truncated")) is not bool:
            raise ValueError(f"{label} truncated state must be boolean")
        if scope.get("article_content_retained") is not False:
            raise ValueError(f"{label} article content retention must be false")
        if scope.get("row_identifiers_retained") is not False:
            raise ValueError(f"{label} row identifier retention must be false")

        labels = require_section(profile, "labels", label)
        good_count = labels.get("good_count")
        bad_count = labels.get("bad_count")
        if (
            type(good_count) is not int
            or type(bad_count) is not int
            or good_count < 0
            or bad_count < 0
            or good_count + bad_count != total
        ):
            raise ValueError(f"{label} label counts are inconsistent")
        if labels.get("good_rate") != _ratio(good_count, total):
            raise ValueError(f"{label} good rate is inconsistent")
        if labels.get("bad_rate") != _ratio(bad_count, total):
            raise ValueError(f"{label} bad rate is inconsistent")
        if labels.get("gold_standard_state") != "not_provided":
            raise ValueError(f"{label} gold standard state is unsupported")

        assurance = require_section(profile, "assurance", label)
        if assurance.get("threshold_approval_state") != "not_approved":
            raise ValueError(f"{label} threshold approval must remain not_approved")
        if assurance.get("release_decision") != "not_computable":
            raise ValueError(f"{label} release decision must remain not_computable")

        exact_duplicates = require_section(profile, "exact_duplicates", label)
        if (
            exact_duplicates.get("near_duplicate_state")
            != "candidate_observation_available"
        ):
            raise ValueError(f"{label} near-duplicate state is unsupported")

        near_duplicate = require_section(
            profile, "near_duplicate_candidates", label
        )
        if near_duplicate.get("method_version") != NEAR_DUPLICATE_METHOD_VERSION:
            raise ValueError(f"{label} near-duplicate method is unsupported")
        if near_duplicate.get("observation_state") != "candidate_pairs_only":
            raise ValueError(f"{label} near-duplicate observation state is invalid")
        near_evaluated_rows = near_duplicate.get("evaluated_rows")
        expected_near_rows = min(total, MAX_NEAR_DUPLICATE_ROWS)
        if (
            near_duplicate.get("profile_evaluated_rows") != total
            or near_evaluated_rows != expected_near_rows
            or near_duplicate.get("row_evaluation_limit")
            != MAX_NEAR_DUPLICATE_ROWS
            or near_duplicate.get("row_evaluation_truncated")
            is not (total > expected_near_rows)
        ):
            raise ValueError(f"{label} near-duplicate evaluated rows are inconsistent")
        if near_duplicate.get("profile_scope_truncated") is not scope.get(
            "truncated"
        ):
            raise ValueError(f"{label} near-duplicate truncation is inconsistent")
        eligible_rows = near_duplicate.get("eligible_rows")
        ineligible_rows = near_duplicate.get("ineligible_low_information_rows")
        if (
            type(eligible_rows) is not int
            or type(ineligible_rows) is not int
            or eligible_rows < 0
            or ineligible_rows < 0
            or eligible_rows + ineligible_rows != near_evaluated_rows
        ):
            raise ValueError(f"{label} near-duplicate eligibility is inconsistent")
        text_truncated_rows = near_duplicate.get("text_truncated_rows")
        if (
            type(text_truncated_rows) is not int
            or not 0 <= text_truncated_rows <= near_evaluated_rows
        ):
            raise ValueError(f"{label} near-duplicate text truncation is invalid")
        if (
            near_duplicate.get("text_character_limit_per_row")
            != MAX_NEAR_DUPLICATE_TEXT_CHARS
            or near_duplicate.get("shingle_character_width")
            != NEAR_DUPLICATE_SHINGLE_CHARS
            or near_duplicate.get("minimum_distinct_shingles")
            != MIN_NEAR_DUPLICATE_DISTINCT_SHINGLES
            or near_duplicate.get("bottom_k_size") != NEAR_DUPLICATE_BOTTOM_K
            or near_duplicate.get("candidate_generation_keys_per_row")
            != NEAR_DUPLICATE_LSH_KEYS
            or near_duplicate.get("shingle_hash_method")
            != "rolling64_polynomial_base257_v1"
        ):
            raise ValueError(f"{label} near-duplicate text method is invalid")
        comparison_limit = near_duplicate.get(
            "candidate_pair_comparison_limit"
        )
        compared = near_duplicate.get("candidate_pairs_compared")
        skipped = near_duplicate.get("candidate_pairs_skipped_at_least")
        pair_ceiling = eligible_rows * (eligible_rows - 1) // 2
        if (
            type(comparison_limit) is not int
            or not 0 < comparison_limit <= MAX_CANDIDATE_PAIR_COMPARISONS
            or type(compared) is not int
            or not 0 <= compared <= min(comparison_limit, pair_ceiling)
        ):
            raise ValueError(
                f"{label} near-duplicate comparison count is invalid"
            )
        if (
            type(skipped) is not int
            or not 0 <= skipped <= pair_ceiling
            or compared + skipped > pair_ceiling
        ):
            raise ValueError(f"{label} near-duplicate skipped count is invalid")
        comparison_overflow = near_duplicate.get("comparison_overflow")
        if (
            type(comparison_overflow) is not bool
            or comparison_overflow != (skipped > 0)
        ):
            raise ValueError(f"{label} near-duplicate comparison overflow is invalid")
        bucket_overflow_events = near_duplicate.get(
            "candidate_generation_bucket_overflow_events"
        )
        if (
            near_duplicate.get("candidate_generation_bucket_row_limit")
            != MAX_NEAR_DUPLICATE_BUCKET_ROWS
            or type(bucket_overflow_events) is not int
            or not (
                0
                <= bucket_overflow_events
                <= eligible_rows * NEAR_DUPLICATE_LSH_KEYS
            )
            or near_duplicate.get("candidate_generation_overflow")
            is not (bucket_overflow_events > 0)
        ):
            raise ValueError(f"{label} near-duplicate bucket bound is invalid")
        candidates = near_duplicate.get("candidate_pairs_observed")
        exact_excluded = near_duplicate.get("exact_duplicate_pairs_excluded")
        if (
            type(candidates) is not int
            or type(exact_excluded) is not int
            or candidates < 0
            or exact_excluded < 0
            or candidates + exact_excluded > compared
        ):
            raise ValueError(f"{label} near-duplicate candidate counts are invalid")
        if (
            near_duplicate.get("similarity_metric")
            != "bottom_k_set_jaccard"
            or near_duplicate.get("candidate_minimum_similarity")
            != NEAR_DUPLICATE_MINIMUM_SIMILARITY
        ):
            raise ValueError(f"{label} near-duplicate similarity method is invalid")
        if (
            near_duplicate.get("candidate_threshold_approval_state")
            != "not_approved"
        ):
            raise ValueError(
                f"{label} candidate threshold approval must remain not_approved"
            )
        if near_duplicate.get("human_review_state") != "not_provided":
            raise ValueError(f"{label} near-duplicate human review is unsupported")
        if near_duplicate.get("duplicate_fact_state") != "not_established":
            raise ValueError(f"{label} duplicate fact state is unsupported")
        if near_duplicate.get("release_decision") != "not_computable":
            raise ValueError(
                f"{label} near-duplicate release decision must remain not_computable"
            )
        if near_duplicate.get("article_content_retained") is not False:
            raise ValueError(f"{label} near-duplicate content retention must be false")
        if near_duplicate.get("urls_or_row_identifiers_retained") is not False:
            raise ValueError(
                f"{label} near-duplicate identifier retention must be false"
            )

        reason_items = profile.get("reason_counts")
        if not isinstance(reason_items, list):
            raise ValueError(f"{label} reason counts must be a list")
        seen_codes: set[str] = set()
        for item in reason_items:
            if not isinstance(item, dict):
                raise ValueError(f"{label} reason item must be an object")
            code = item.get("code")
            count = item.get("count")
            if code in seen_codes:
                raise ValueError(f"{label} duplicate reason code")
            if code not in QUALITY_REASON_CODES:
                raise ValueError(f"{label} reason code is unsupported")
            if type(count) is not int or count <= 0 or count > total:
                raise ValueError(f"{label} reason count is invalid")
            if item.get("rate") != _ratio(count, total):
                raise ValueError(f"{label} reason rate is inconsistent")
            seen_codes.add(code)

        publication = require_section(profile, "publication_time", label)
        valid_count = publication.get("valid_count")
        if type(valid_count) is not int or not 0 <= valid_count <= total:
            raise ValueError(f"{label} publication count is invalid")
        earliest_raw = publication.get("earliest_at")
        cutoff_raw = publication.get("cutoff_at")
        if valid_count == 0:
            if earliest_raw is not None or cutoff_raw is not None:
                raise ValueError(f"{label} empty publication range is inconsistent")
        else:
            earliest = parse_datetime(earliest_raw)
            latest = parse_datetime(cutoff_raw)
            if earliest is None or latest is None:
                raise ValueError(f"{label} publication range is invalid")
            if ensure_aware(earliest) > ensure_aware(latest):
                raise ValueError(f"{label} publication range is reversed")

    validate_profile(baseline, "baseline")
    validate_profile(current, "current")

    baseline_good = rate(baseline, "labels", "good_rate")
    current_good = rate(current, "labels", "good_rate")
    good_rate_delta = (
        round(current_good - baseline_good, 6)
        if baseline_good is not None and current_good is not None
        else None
    )
    baseline_reasons = {
        item["code"]: item["rate"] for item in baseline.get("reason_counts", [])
    }
    current_reasons = {
        item["code"]: item["rate"] for item in current.get("reason_counts", [])
    }
    reason_rate_deltas = []
    for code in sorted(set(baseline_reasons) | set(current_reasons)):
        before = baseline_reasons.get(code, 0.0)
        after = current_reasons.get(code, 0.0)
        if type(before) not in (int, float) or type(after) not in (int, float):
            raise ValueError("reason rates must be finite numbers")
        if (
            not math.isfinite(float(before))
            or not math.isfinite(float(after))
            or not 0.0 <= float(before) <= 1.0
            or not 0.0 <= float(after) <= 1.0
        ):
            raise ValueError("reason rates must be finite numbers between zero and one")
        reason_rate_deltas.append(
            {
                "code": code,
                "baseline_rate": before,
                "current_rate": after,
                "delta": round(after - before, 6),
            }
        )

    baseline_rows = evaluated_rows(baseline, "baseline")
    current_rows = evaluated_rows(current, "current")
    baseline_cutoff = cutoff(baseline, "baseline")
    current_cutoff = cutoff(current, "current")

    return {
        "schema_version": "news-quality-profile-comparison-v3",
        "method_version": QUALITY_PROFILE_METHOD_VERSION,
        "baseline_generated_at": baseline.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "good_rate_delta": good_rate_delta,
        "reason_rate_deltas": reason_rate_deltas,
        "volume_observation": {
            "baseline_evaluated_rows": baseline_rows,
            "current_evaluated_rows": current_rows,
            "evaluated_rows_delta": current_rows - baseline_rows,
            "evaluated_rows_ratio": (
                round(current_rows / baseline_rows, 6) if baseline_rows else None
            ),
            "interruption_assessment": "not_computable",
            "approved_expected_volume": None,
        },
        "publication_cutoff_observation": {
            "baseline_cutoff_at": (
                baseline_cutoff.isoformat() if baseline_cutoff else None
            ),
            "current_cutoff_at": (
                current_cutoff.isoformat() if current_cutoff else None
            ),
            "cutoff_delta_seconds": (
                round((current_cutoff - baseline_cutoff).total_seconds(), 6)
                if baseline_cutoff is not None and current_cutoff is not None
                else None
            ),
            "freshness_assessment": "not_computable",
            "approved_cadence": None,
        },
        "near_duplicate_candidate_observation": {
            "method_version": NEAR_DUPLICATE_METHOD_VERSION,
            "baseline_evaluated_rows": baseline[
                "near_duplicate_candidates"
            ]["evaluated_rows"],
            "current_evaluated_rows": current[
                "near_duplicate_candidates"
            ]["evaluated_rows"],
            "baseline_candidate_pairs_observed": baseline[
                "near_duplicate_candidates"
            ]["candidate_pairs_observed"],
            "current_candidate_pairs_observed": current[
                "near_duplicate_candidates"
            ]["candidate_pairs_observed"],
            "candidate_pairs_observed_delta": current[
                "near_duplicate_candidates"
            ]["candidate_pairs_observed"]
            - baseline["near_duplicate_candidates"]["candidate_pairs_observed"],
            "comparison_state": "observed_candidates_only",
            "comparability_state": "not_established",
            "baseline_observation_overflow": any(
                baseline["near_duplicate_candidates"][key]
                for key in (
                    "row_evaluation_truncated",
                    "profile_scope_truncated",
                    "comparison_overflow",
                    "candidate_generation_overflow",
                )
            ),
            "current_observation_overflow": any(
                current["near_duplicate_candidates"][key]
                for key in (
                    "row_evaluation_truncated",
                    "profile_scope_truncated",
                    "comparison_overflow",
                    "candidate_generation_overflow",
                )
            ),
            "candidate_threshold_approval_state": "not_approved",
            "human_review_state": "not_provided",
            "duplicate_fact_state": "not_established",
            "release_decision": "not_computable",
        },
        "comparison_state": "observed_only",
        "threshold_approval_state": "not_approved",
        "release_decision": "not_computable",
    }
