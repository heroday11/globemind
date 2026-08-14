#!/usr/bin/env python3
from __future__ import annotations

import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from urllib.parse import urlparse

from dateutil import parser as date_parser
from dateutil.parser import UnknownTimezoneWarning


MAX_REAL_TZ_OFFSET_SECONDS = 14 * 60 * 60
MIN_REASONABLE_YEAR = 1800
FUTURE_GRACE_DAYS = 3
COMMON_TZINFOS = {
    "UTC": 0,
    "GMT": 0,
    "EST": -5 * 3600,
    "EDT": -4 * 3600,
    "CST": -6 * 3600,
    "CDT": -5 * 3600,
    "MST": -7 * 3600,
    "MDT": -6 * 3600,
    "PST": -8 * 3600,
    "PDT": -7 * 3600,
    "HKT": 8 * 3600,
    "SGT": 8 * 3600,
    "JST": 9 * 3600,
    "KST": 9 * 3600,
    "CET": 1 * 3600,
    "CEST": 2 * 3600,
    "BST": 1 * 3600,
    "IST": 5 * 3600 + 30 * 60,
}

ISO_TZ_SUFFIX_RE = re.compile(
    r"^(.+[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)([+-])(\d{2}):?(\d{2})$"
)

MONTHS = {
    # English
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    # Spanish / Portuguese / Italian / French common forms
    "enero": 1,
    "janvier": 1,
    "janeiro": 1,
    "gennaio": 1,
    "febrero": 2,
    "fevrier": 2,
    "fevereiro": 2,
    "febbraio": 2,
    "marzo": 3,
    "mars": 3,
    "marco": 3,
    "abril": 4,
    "avril": 4,
    "aprile": 4,
    "mayo": 5,
    "mai": 5,
    "maio": 5,
    "maggio": 5,
    "junio": 6,
    "juin": 6,
    "junho": 6,
    "giugno": 6,
    "julio": 7,
    "juillet": 7,
    "julho": 7,
    "luglio": 7,
    "agosto": 8,
    "aout": 8,
    "setiembre": 9,
    "septiembre": 9,
    "septembre": 9,
    "setembro": 9,
    "settembre": 9,
    "octubre": 10,
    "outubro": 10,
    "ottobre": 10,
    "noviembre": 11,
    "novembre": 11,
    "novembro": 11,
    "diciembre": 12,
    "decembre": 12,
    "dezembro": 12,
    "dicembre": 12,
    # German
    "januar": 1,
    "februar": 2,
    "maerz": 3,
    "märz": 3,
    "april": 4,
    "juni": 6,
    "juli": 7,
    "oktober": 10,
    "dezember": 12,
}

MONTH_PATTERN = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
DAY_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<day>[0-3]?\d)(?:\.|º|°)?\s+(?:de\s+|del\s+|d[eo]\s+)?"
    rf"(?P<month>{MONTH_PATTERN})"
    rf"(?:\s+de|\s+del|,)?\s+(?P<year>18\d{{2}}|19\d{{2}}|20\d{{2}})\b",
    re.IGNORECASE,
)
MONTH_DAY_YEAR_RE = re.compile(
    rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<day>[0-3]?\d)(?:st|nd|rd|th)?"
    rf",?\s+(?P<year>18\d{{2}}|19\d{{2}}|20\d{{2}})\b",
    re.IGNORECASE,
)
DATE_SIGNAL_RE = re.compile(rf"(18\d{{2}}|19\d{{2}}|20\d{{2}}|{MONTH_PATTERN})", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"\b(?P<year>18\d{2}|19\d{2}|20\d{2})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})\b")
URL_YYYYMMDD_RE = re.compile(r"(?<!\d)(?P<year>18\d{2}|19\d{2}|20\d{2})(?P<month>\d{2})(?P<day>\d{2})(?!\d)")
URL_YEAR_MMDD_RE = re.compile(r"/(?P<year>18\d{2}|19\d{2}|20\d{2})/(?P<month>\d{2})(?P<day>\d{2})(?:/|[^0-9])")

PUBLISHED_LABEL_RE = re.compile(
    r"(published|posted|updated|date|fecha|publicado|actualizado|publié|publie|mis a jour|"
    r"veröffentlicht|veroffentlicht|aggiornato|pubblicato)\s*[:|,-]\s*(?P<date>.{0,80})",
    re.IGNORECASE,
)
BYLINE_DATE_RE = re.compile(r"\|\s*(?P<date>.{0,80})")
EVENT_CONTEXT_RE = re.compile(
    r"(deadline|closing date|apply by|application|event date|fixture|result|"
    r"plazo|postulaci[oó]n|convocatoria|fechas?|evento|juegos|directo|resultado|"
    r"campanha|campana|inscri[cç][aã]o|candidatura)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DateCandidate:
    dt: datetime
    source: str
    raw: str
    score: int
    reason: str


@dataclass(frozen=True)
class DateCleanResult:
    published_at: datetime | None
    source: str
    raw: str
    confidence: int
    reason: str

    def isoformat(self) -> str:
        return self.published_at.isoformat() if self.published_at else ""


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _datetime_from_parts(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_multilingual_date(value: str) -> datetime | None:
    text = strip_accents(value.strip().lower())
    if not text:
        return None
    for regex in (ISO_DATE_RE, DAY_MONTH_YEAR_RE, MONTH_DAY_YEAR_RE):
        match = regex.search(text)
        if not match:
            continue
        month_text = match.group("month").lower()
        month = int(month_text) if month_text.isdigit() else MONTHS.get(month_text)
        if not month:
            continue
        return _datetime_from_parts(int(match.group("year")), month, int(match.group("day")))
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    multilingual = parse_multilingual_date(text)
    if multilingual:
        return multilingual

    cleaned_text = text
    match = ISO_TZ_SUFFIX_RE.match(text)
    if match:
        offset_seconds = int(match.group(3)) * 3600 + int(match.group(4)) * 60
        if offset_seconds > MAX_REAL_TZ_OFFSET_SECONDS:
            cleaned_text = match.group(1)

    try:
        return ensure_aware(datetime.fromisoformat(cleaned_text))
    except Exception:
        pass

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UnknownTimezoneWarning)
            return ensure_aware(date_parser.parse(cleaned_text, fuzzy=True, dayfirst=False, tzinfos=COMMON_TZINFOS))
    except Exception:
        return None


def date_from_url(url: str) -> datetime | None:
    path = urlparse(url or "").path
    for regex in (URL_YEAR_MMDD_RE, URL_YYYYMMDD_RE, ISO_DATE_RE):
        match = regex.search(path)
        if not match:
            continue
        dt = _datetime_from_parts(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        if dt:
            return dt
    return None


def likely_event_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 80): min(len(text), end + 80)]
    return bool(EVENT_CONTEXT_RE.search(window))


def has_date_signal(value: str) -> bool:
    return bool(DATE_SIGNAL_RE.search(strip_accents(value.lower())))


def body_lead_date(title: str | None, body: str | None) -> DateCandidate | None:
    text = f"{title or ''}\n{body or ''}".strip()
    if not text:
        return None
    head = text[:1200]
    candidates: list[DateCandidate] = []

    for label_match in PUBLISHED_LABEL_RE.finditer(head):
        raw = label_match.group("date")
        if not has_date_signal(raw):
            continue
        dt = parse_datetime(raw)
        if dt:
            candidates.append(DateCandidate(dt, "body_label", raw.strip(), 70, "published_label"))

    first_lines = "\n".join(line for line in head.splitlines()[:4])
    for pipe_match in BYLINE_DATE_RE.finditer(first_lines):
        raw = pipe_match.group("date")
        if not has_date_signal(raw):
            continue
        dt = parse_datetime(raw)
        if dt:
            candidates.append(DateCandidate(dt, "body_byline", raw.strip(), 90, "byline_pipe"))

    for regex in (DAY_MONTH_YEAR_RE, MONTH_DAY_YEAR_RE, ISO_DATE_RE):
        for match in regex.finditer(head[:450]):
            if likely_event_context(head, match.start(), match.end()):
                continue
            dt = parse_datetime(match.group(0))
            if dt:
                candidates.append(DateCandidate(dt, "body_lead", match.group(0).strip(), 55, "lead_text"))

    return max(candidates, key=lambda item: item.score, default=None)


def is_reasonable_date(dt: datetime, fetched_at: datetime | None) -> bool:
    if dt.year < MIN_REASONABLE_YEAR:
        return False
    if fetched_at and dt > fetched_at.replace(tzinfo=timezone.utc) and (dt - fetched_at).days > FUTURE_GRACE_DAYS:
        return False
    return True


def score_candidate(
    candidate: DateCandidate,
    *,
    lastmod: datetime | None,
    url_dt: datetime | None,
    fetched_at: datetime | None,
) -> DateCandidate | None:
    dt = ensure_aware(candidate.dt)
    if not is_reasonable_date(dt, fetched_at):
        return None
    score = candidate.score
    reasons = [candidate.reason]

    if lastmod:
        delta_days = abs((dt.date() - lastmod.date()).days)
        if delta_days <= 3:
            score += 22
            reasons.append("close_to_lastmod")
        elif delta_days <= 45:
            score += 10
            reasons.append("near_lastmod")
        elif candidate.source == "extracted" and delta_days > 120:
            score -= 55
            reasons.append("far_from_lastmod")

    if url_dt:
        delta_days = abs((dt.date() - url_dt.date()).days)
        if delta_days <= 1:
            score += 35
            reasons.append("matches_url_date")
        elif candidate.source == "extracted" and delta_days > 45:
            score -= 35
            reasons.append("far_from_url_date")

    return DateCandidate(dt, candidate.source, candidate.raw, score, ",".join(reasons))


def clean_published_at(row: dict[str, Any], *, now: datetime | None = None) -> DateCleanResult:
    fetched_at = parse_datetime(row.get("fetched_at")) or now
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    else:
        fetched_at = ensure_aware(fetched_at)

    lastmod = parse_datetime(row.get("lastmod"))
    url = str(row.get("response_url") or row.get("request_url") or row.get("url") or "")
    url_dt = date_from_url(url)

    candidates: list[DateCandidate] = []
    extracted_raw = str(row.get("published_at") or "").strip()
    extracted_dt = parse_datetime(extracted_raw)
    if extracted_dt:
        candidates.append(DateCandidate(extracted_dt, "extracted", extracted_raw, 58, "extracted_value"))
    if url_dt:
        candidates.append(DateCandidate(url_dt, "url", url, 52, "url_date"))
    body_candidate = body_lead_date(row.get("title"), row.get("body"))
    if body_candidate:
        candidates.append(body_candidate)
    if lastmod:
        candidates.append(DateCandidate(lastmod, "lastmod", str(row.get("lastmod") or ""), 42, "sitemap_lastmod"))

    scored = [
        item
        for candidate in candidates
        if (item := score_candidate(candidate, lastmod=lastmod, url_dt=url_dt, fetched_at=fetched_at))
    ]
    if not scored:
        return DateCleanResult(None, "", "", 0, "no_valid_candidate")

    best = max(scored, key=lambda item: (item.score, item.source != "lastmod"))
    return DateCleanResult(best.dt, best.source, best.raw, best.score, best.reason)
