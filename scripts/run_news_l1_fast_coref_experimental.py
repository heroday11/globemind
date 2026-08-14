#!/usr/bin/env python3
"""Experimental L1 clustering with conservative recall improvements.

Default run_id is ``fast_l1_exp``. This script does not overwrite the saved
mainline ``fast_l1_v1`` result unless the caller explicitly asks for that run id.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any

from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core_pipeline.union_find import UnionFind
from scripts import run_news_l1_fast_coref as base
from scripts.ensure_news_l1_infra import connect, ensure_news_l1_infra

LOGGER = logging.getLogger("run_news_l1_fast_coref_experimental")
RUN_ID = "fast_l1_exp"

SYMMETRIC_ACTIONS = {
    "meeting_visit",
    "negotiation_talks",
    "agreement_signed",
    "ceasefire_peace_talks",
}

LEADER_ALIASES = {
    "trump": "us",
    "donald trump": "us",
    "president trump": "us",
    "biden": "us",
    "joe biden": "us",
    "vance": "us",
    "jd vance": "us",
    "j d vance": "us",
    "marco rubio": "us",
    "rubio": "us",
    "putin": "russia",
    "vladimir putin": "russia",
    "president putin": "russia",
    "kremlin": "russia",
    "lavrov": "russia",
    "xi": "china",
    "xi jinping": "china",
    "president xi": "china",
    "beijing": "china",
    "zelensky": "ukraine",
    "zelenskyy": "ukraine",
    "volodymyr zelensky": "ukraine",
    "volodymyr zelenskyy": "ukraine",
    "netanyahu": "israel",
    "benjamin netanyahu": "israel",
    "modi": "india",
    "narendra modi": "india",
    "kim": "north korea",
    "kim jong un": "north korea",
    "kim jong-un": "north korea",
    "erdogan": "turkey",
    "recep tayyip erdogan": "turkey",
    "macron": "france",
    "emmanuel macron": "france",
    "starmer": "uk",
    "keir starmer": "uk",
    "meloni": "italy",
    "giorgia meloni": "italy",
    "hamas": "hamas",
    "hezbollah": "hezbollah",
}

COUNTRY_ALIASES = {
    "united states": "us",
    "usa": "us",
    "u s": "us",
    "u.s.": "us",
    "u.s": "us",
    "america": "us",
    "american": "us",
    "russian federation": "russia",
    "ukrainian": "ukraine",
    "chinese": "china",
    "prc": "china",
    "people s republic of china": "china",
    "united kingdom": "uk",
    "britain": "uk",
    "british": "uk",
    "north korea": "north korea",
    "dprk": "north korea",
    "south korea": "south korea",
    "republic of korea": "south korea",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Experimental fast L1 coreference with alias-aware buckets."
    )
    base.add_db_args(parser)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--target-start")
    parser.add_argument("--target-end")
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--body-chars", type=int, default=300)
    parser.add_argument("--include-general-news", action="store_true")
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--min-report-cluster-size", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=650)
    parser.add_argument(
        "--disable-exact-title-union",
        action="store_true",
        help="Disable global exact-title duplicate union for filtered non-generic titles.",
    )
    return parser.parse_args()


def clean_actor(value: str | None) -> str:
    text = base.normalize_key(value)
    text = re.sub(r"\b(president|prime minister|secretary|minister|leader|official|government)\b", " ", text)
    return " ".join(text.split())


def alias_actor(value: str | None) -> str:
    text = clean_actor(value)
    if not text:
        return ""
    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]
    if text in LEADER_ALIASES:
        return LEADER_ALIASES[text]
    words = text.split()
    for width in (3, 2, 1):
        for idx in range(0, max(0, len(words) - width + 1)):
            phrase = " ".join(words[idx : idx + width])
            if phrase in LEADER_ALIASES:
                return LEADER_ALIASES[phrase]
            if phrase in COUNTRY_ALIASES:
                return COUNTRY_ALIASES[phrase]
    return text


def alias_side(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for raw in re.split(r"&|,| and ", str(value), flags=re.IGNORECASE):
        actor = alias_actor(raw)
        if actor:
            parts.append(actor)
    return "&".join(sorted(set(parts)))


def pair_sides(pair_key: str | None) -> tuple[str, str]:
    if not pair_key or "→" not in pair_key:
        return "", ""
    left, right = pair_key.split("→", 1)
    return left.strip(), right.strip()


def experimental_actor_bucket(row: dict[str, Any]) -> str:
    left, right = pair_sides(base.clean_text(row.get("entity_pair_key")))
    left_alias = alias_side(left or row.get("canonical_initiator") or row.get("initiator"))
    right_alias = alias_side(right or row.get("canonical_target") or row.get("target"))
    action = base.clean_text(row.get("event_action"))

    if left_alias or right_alias:
        if action in SYMMETRIC_ACTIONS:
            actors = sorted(actor for actor in (left_alias, right_alias) if actor)
            return f"alias_pair:{'↔'.join(actors)}"
        return f"alias_pair:{left_alias}→{right_alias}"

    location = base.normalize_key(row.get("location"))
    if location:
        return f"loc:{location}"
    return "none"


def generic_or_roundup_title(title: str) -> bool:
    title_clean = base.clean_text(title)
    title_l = title_clean.lower()
    if len(title_clean) < 35:
        return True
    if base.is_roundup_title(title_clean):
        return True
    return bool(
        re.fullmatch(
            r"(video|speech|remarks|opening remarks|press conference|joint press conference|delete)",
            title_l,
        )
    )


EVENT_SIGNAL_RE = re.compile(
    r"\b("
    r"accuses?|agrees?|announces?|appoints?|approves?|arrests?|attacks?|backs?|"
    r"bans?|blocks?|ceasefire|condemns?|cuts?|deploys?|elections?|extends?|"
    r"calls?|hits?|imposes?|kills?|launches?|meets?|meeting|negotiat(?:e|es|ions?)|"
    r"passes?|peace|policy|protests?|rejects?|resigns?|sanctions?|signs?|"
    r"strikes?|summit|supports?|suspends?|tariffs?|talks?|threatens?|urges?|"
    r"visits?|votes?|warns?"
    r")\b",
    re.IGNORECASE,
)
WEAK_LINK_TITLE_RE = re.compile(
    r"^\s*(video|видео)\b|"
    r"^\s*(israel|iran|iraq|russia|ukraine|china|syria|france|germany|لبنان|إسرائيل|以色列)\s*$",
    re.IGNORECASE,
)
GENERIC_WEAK_TITLE_RE = re.compile(
    r"^\s*(speech|remarks|opening remarks|press conference|joint press conference|delete)\s*$",
    re.IGNORECASE,
)
RECURRING_SERIES_TITLE_RE = re.compile(
    r"\b(over past day|past 24 hours|daily update|"
    r"general staff:\s*russia has lost|has lost [0-9,]+ troops since)\b",
    re.IGNORECASE,
)
SOURCE_TEMPLATE_TITLE_RE = re.compile(
    r"(france diplomatie$|audition de .*commission des affaires|propos de .*barrot)",
    re.IGNORECASE,
)
INSTITUTIONAL_TEMPLATE_TITLE_RE = re.compile(
    r"\b("
    r"meeting of the north atlantic council|"
    r"ministerial meeting of the north atlantic council|"
    r"north atlantic council meeting|"
    r"secretary general'?s press conference|"
    r"doorstep statement by the nato secretary general|"
    r"pre-ministerial press conference of the nato secretary general"
    r")\b",
    re.IGNORECASE,
)


def title_has_event_signal(title: str) -> bool:
    return bool(EVENT_SIGNAL_RE.search(title or ""))


def weak_link_title(title: str) -> bool:
    title_clean = base.clean_text(title)
    title_norm = base.normalize_key(title_clean)
    if len(title_norm.replace(" ", "")) <= 4:
        return True
    if GENERIC_WEAK_TITLE_RE.search(title_clean):
        return True
    if WEAK_LINK_TITLE_RE.search(title_clean):
        return True
    if RECURRING_SERIES_TITLE_RE.search(title_clean):
        return True
    if INSTITUTIONAL_TEMPLATE_TITLE_RE.search(title_clean):
        return True
    if SOURCE_TEMPLATE_TITLE_RE.search(title_clean) and not title_has_event_signal(title_clean):
        return True
    return False


def exact_title_group_allowed(group: list[base.Record]) -> bool:
    if not group:
        return False
    title = group[0].title
    if generic_or_roundup_title(title):
        return False
    if weak_link_title(title):
        return False
    if title_has_event_signal(title):
        return True

    field_count = len({(row.event_family, row.event_action) for row in group})
    bucket_count = len({row.bucket_key for row in group})
    if len(group) >= 4 and (field_count > 2 or bucket_count > 3):
        return False
    return True


def fetch_records(conn: Any, args: argparse.Namespace) -> list[base.Record]:
    filters = ["e.parse_success IS TRUE"]
    params: list[Any] = [args.body_chars]
    if not args.include_general_news:
        filters.append("e.event_domain = 'political'")
    if args.target_start:
        filters.append("COALESCE(n.published_at, p.published_at_clean) >= %s")
        params.append(args.target_start)
    if args.target_end:
        filters.append("COALESCE(n.published_at, p.published_at_clean) <= %s")
        params.append(args.target_end)
    limit_sql = ""
    if args.max_rows:
        limit_sql = "LIMIT %s"
        params.append(args.max_rows)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.news_id,
                   COALESCE(n.title, '') AS title,
                   LEFT(COALESCE(n.body, ''), %s) AS body_short,
                   COALESCE(n.published_at, p.published_at_clean) AS published_at,
                   e.event_domain,
                   e.event_family,
                   e.event_action,
                   e.initiator,
                   e.target,
                   e.canonical_initiator,
                   e.canonical_target,
                   e.entity_pair_key,
                   e.location,
                   e.tone
	            FROM public.news_l1_event_extractions AS e
	            JOIN public.news_l1_prep AS p ON p.news_id = e.news_id
	            JOIN public.news AS n ON n.id = e.news_id
	            JOIN public.news_quality_labels AS q
	              ON q.news_id = n.id
	             AND q.is_good IS TRUE
	             AND q.label_version = 'quality_v1_20260629'
	            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(n.published_at, p.published_at_clean), e.news_id
            {limit_sql}
            """,
            params,
        )
        rows = [dict(row) for row in cur.fetchall()]

    records: list[base.Record] = []
    for row in rows:
        published_at = base.parse_dt(row.get("published_at"))
        title = base.clean_text(row.get("title"))
        body_short = base.clean_text(row.get("body_short"))
        short_text = f"{title} {body_short}".strip()
        event_family = base.clean_text(row.get("event_family")) or "other"
        event_action = base.clean_text(row.get("event_action")) or "other"
        event_domain = base.clean_text(row.get("event_domain")) or "political"
        title_key = base.normalize_key(title)
        row["event_action"] = event_action
        records.append(
            base.Record(
                news_id=int(row["news_id"]),
                published_at=published_at,
                published_date=published_at.date() if published_at else None,
                title=title,
                short_text=short_text,
                event_domain=event_domain,
                event_family=event_family,
                event_action=event_action,
                initiator=row.get("initiator"),
                target=row.get("target"),
                canonical_initiator=row.get("canonical_initiator"),
                canonical_target=row.get("canonical_target"),
                entity_pair_key=base.clean_text(row.get("entity_pair_key")) or None,
                location=row.get("location"),
                tone=base.clean_text(row.get("tone")) or None,
                title_key=title_key,
                text_tokens=base.token_set(short_text),
                title_grams=base.char_grams(title),
                bucket_key=(event_family, event_action, experimental_actor_bucket(row)),
            )
        )
    return records


def alias_bucket(record: base.Record) -> str:
    return record.bucket_key[2]


def experimental_link_threshold(a: base.Record, b: base.Record) -> float | None:
    if weak_link_title(a.title) or weak_link_title(b.title):
        return None

    if base.is_roundup_title(a.title) or base.is_roundup_title(b.title):
        if a.title_key and a.title_key == b.title_key and len(a.title_key) >= 16:
            return 0.0
        return None

    threshold = base.link_threshold(a, b)
    if threshold is not None:
        return threshold

    gap = base.day_gap(a, b)
    max_gap = base.window_days(a.event_family, a.event_action)
    if gap > max_gap:
        return None
    if a.tone and b.tone and {a.tone, b.tone} == {"positive", "negative"}:
        return None
    if alias_bucket(a) != alias_bucket(b):
        return None
    if a.event_family != b.event_family or a.event_action != b.event_action:
        return None
    if a.event_action in {"military_attack", "terror_attack", "protest", "crackdown_arrest"}:
        return 0.62
    if a.event_action in SYMMETRIC_ACTIONS:
        return 0.46
    return 0.58


def experimental_can_link(a: base.Record, b: base.Record) -> tuple[bool, float]:
    threshold = experimental_link_threshold(a, b)
    if threshold is None:
        return False, 0.0
    score = base.text_similarity(a, b)
    return score >= threshold, score


def experimental_split_overwide_cluster(
    members: list[int],
    by_id: dict[int, base.Record],
) -> list[list[int]]:
    if len(members) <= 1:
        return [sorted(members)]
    rows = sorted(
        (by_id[mid] for mid in members if mid in by_id),
        key=lambda r: (r.published_date or date.min, r.news_id),
    )
    if not rows:
        return [sorted(members)]
    max_window = base.window_days(rows[0].event_family, rows[0].event_action)
    start = rows[0].published_date
    if start is None:
        return [sorted(members)]

    groups: list[list[int]] = []
    current: list[int] = []
    current_start = start
    for row in rows:
        row_date = row.published_date
        if row_date is None:
            current.append(row.news_id)
            continue
        if (row_date - current_start).days <= max_window:
            current.append(row.news_id)
            continue
        if current:
            groups.append(sorted(current))
        current = [row.news_id]
        current_start = row_date
    if current:
        groups.append(sorted(current))
    return groups


def build_clusters(records: list[base.Record], *, max_candidates: int, exact_title_union: bool) -> dict[str, list[int]]:
    if not records:
        return {}

    by_id = {r.news_id: r for r in records}
    uf = UnionFind([str(r.news_id) for r in records])

    exact_edges = 0
    if exact_title_union:
        by_title: dict[str, list[base.Record]] = defaultdict(list)
        for record in records:
            if record.title_key and not generic_or_roundup_title(record.title):
                by_title[record.title_key].append(record)
        for group in by_title.values():
            if len(group) < 2:
                continue
            if not exact_title_group_allowed(group):
                continue
            group.sort(key=lambda r: r.news_id)
            root = group[0]
            for record in group[1:]:
                uf.union(str(root.news_id), str(record.news_id))
                exact_edges += 1

    buckets: dict[tuple[str, str, str], list[base.Record]] = defaultdict(list)
    for record in records:
        buckets[record.bucket_key].append(record)

    candidate_edges = 0
    for bucket_key, group in buckets.items():
        group.sort(key=lambda r: (r.published_date or date.min, r.news_id))
        active: deque[base.Record] = deque()
        max_window = base.window_days(bucket_key[0], bucket_key[1])
        for record in group:
            while active and record.published_date and active[0].published_date:
                if (record.published_date - active[0].published_date).days <= max_window:
                    break
                active.popleft()

            scored_edges: list[tuple[float, base.Record]] = []
            for candidate in list(active)[-max_candidates:]:
                ok, score = experimental_can_link(record, candidate)
                if ok:
                    scored_edges.append((score, candidate))
            if scored_edges:
                scored_edges.sort(key=lambda item: (-item[0], item[1].news_id))
                max_edges = 1 if record.event_action in {
                    "military_attack",
                    "terror_attack",
                    "protest",
                    "crackdown_arrest",
                    "disaster_response",
                } else 2
                for _score, candidate in scored_edges[:max_edges]:
                    uf.union(str(record.news_id), str(candidate.news_id))
                    candidate_edges += 1
            active.append(record)

    grouped: dict[str, list[int]] = defaultdict(list)
    for record in records:
        grouped[uf.find(str(record.news_id))].append(record.news_id)

    split_clusters: list[list[int]] = []
    for members in grouped.values():
        split_clusters.extend(experimental_split_overwide_cluster(members, by_id))

    LOGGER.info(
        "experimental coref records=%d buckets=%d exact_edges=%d candidate_edges=%d clusters=%d",
        len(records),
        len(buckets),
        exact_edges,
        candidate_edges,
        len(split_clusters),
    )
    return {str(i): sorted(members) for i, members in enumerate(split_clusters)}


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    t0 = time.time()
    conn = connect(args)
    ensure_news_l1_infra(conn)
    try:
        records = fetch_records(conn, args)
        LOGGER.info("loaded records=%d in %.1fs", len(records), time.time() - t0)
        clusters = build_clusters(
            records,
            max_candidates=args.max_candidates,
            exact_title_union=not args.disable_exact_title_union,
        )
        base.print_report(
            clusters,
            records,
            sample_limit=args.sample_limit,
            min_cluster_size=args.min_report_cluster_size,
        )
        if not args.dry_run:
            base.write_clusters(
                conn,
                clusters,
                records,
                run_id=args.run_id,
                clear_existing=args.clear_existing,
            )
            LOGGER.info("wrote clusters run_id=%s in %.1fs", args.run_id, time.time() - t0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
