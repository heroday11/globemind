#!/usr/bin/env python3
"""Mainline fast L1 coreference pipeline.

This is the stable production baseline saved as ``fast_l1_v1``.
Do not tune experimental clustering behavior in this file directly; use
``scripts/run_news_l1_fast_coref_experimental.py`` or a new experiment module
so the mainline run remains reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core_pipeline.union_find import UnionFind
from scripts.ensure_news_l1_infra import add_db_args, connect, ensure_news_l1_infra

LOGGER = logging.getLogger("run_news_l1_fast_coref")
PIPELINE_VERSION = "fast_l1_v1"


@dataclass(slots=True)
class Record:
    news_id: int
    published_at: datetime | None
    published_date: date | None
    title: str
    short_text: str
    event_domain: str
    event_family: str
    event_action: str
    initiator: str | None
    target: str | None
    canonical_initiator: str | None
    canonical_target: str | None
    entity_pair_key: str | None
    location: str | None
    tone: str | None
    title_key: str
    text_tokens: frozenset[str]
    title_grams: frozenset[str]
    bucket_key: tuple[str, str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stable fast L1 event coreference without full-text BGE embeddings."
    )
    add_db_args(parser)
    parser.add_argument("--run-id", default=PIPELINE_VERSION)
    parser.add_argument("--target-start")
    parser.add_argument("--target-end")
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--body-chars", type=int, default=300)
    parser.add_argument("--include-general-news", action="store_true")
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--min-report-cluster-size", type=int, default=2)
    parser.add_argument("--max-candidates", type=int, default=450)
    return parser.parse_args()


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_key(value: str | None) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff]+", " ", text)
    return " ".join(text.split())


def token_set(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+|[\u0600-\u06ff]+|[\u0400-\u04ff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.add(part)
            else:
                tokens.update(part[i : i + 2] for i in range(len(part) - 1))
        elif len(part) >= 3:
            tokens.add(part)
    return frozenset(tokens)


def char_grams(text: str, n: int = 4) -> frozenset[str]:
    compact = normalize_key(text).replace(" ", "")
    if not compact:
        return frozenset()
    if len(compact) <= n:
        return frozenset({compact})
    return frozenset(compact[i : i + n] for i in range(len(compact) - n + 1))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    lset = left if isinstance(left, (set, frozenset)) else set(left)
    rset = right if isinstance(right, (set, frozenset)) else set(right)
    if not lset or not rset:
        return 0.0
    return len(lset & rset) / len(lset | rset)


def text_similarity(a: Record, b: Record) -> float:
    title_sim = max(
        jaccard(a.title_grams, b.title_grams),
        jaccard(token_set(a.title), token_set(b.title)),
    )
    body_sim = jaccard(a.text_tokens, b.text_tokens)
    # Short body only nudges the score; titles carry the event identity.
    return max(title_sim, 0.85 * title_sim + 0.15 * body_sim)


ROUNDUP_TITLE_RE = re.compile(
    r"\b("
    r"meet the press|live updates?|morning bid|evening brief|daily brief|"
    r"news roundup|what to know|things to know|more\s*-\s*bloomberg|"
    r"market today|stocks to watch|the latest"
    r")\b",
    re.IGNORECASE,
)


def is_roundup_title(title: str) -> bool:
    return bool(ROUNDUP_TITLE_RE.search(title or ""))


def day_gap(a: Record, b: Record) -> int:
    if a.published_date is None or b.published_date is None:
        return 9999
    return abs((a.published_date - b.published_date).days)


def window_days(family: str, action: str) -> int:
    if action in {
        "military_attack",
        "terror_attack",
        "protest",
        "crackdown_arrest",
        "disaster_response",
    }:
        return 1
    if action in {
        "military_deployment",
        "ceasefire_peace_talks",
        "election_vote",
        "leadership_change",
        "court_ruling",
    }:
        return 2
    if family in {"military_security", "civil_unrest", "security_crime", "disaster_environment"}:
        return 2
    if action in {
        "meeting_visit",
        "statement_condemnation",
        "agreement_signed",
        "negotiation_talks",
        "law_policy_change",
        "sanction_export_control",
        "tariff_trade_dispute",
        "technology_policy",
        "industrial_policy",
        "infrastructure_development",
        "public_welfare_policy",
        "environment_policy",
    }:
        return 3
    return 2


def pair_sides(pair_key: str | None) -> tuple[str, str]:
    if not pair_key or "→" not in pair_key:
        return "", ""
    left, right = pair_key.split("→", 1)
    return left.strip(), right.strip()


def meaningful_pair(pair_key: str | None) -> bool:
    left, right = pair_sides(pair_key)
    return bool(left or right)


def full_pair(pair_key: str | None) -> bool:
    left, right = pair_sides(pair_key)
    return bool(left and right)


def actor_bucket(row: dict[str, Any]) -> str:
    pair_key = clean_text(row.get("entity_pair_key"))
    if meaningful_pair(pair_key):
        return f"pair:{pair_key}"
    location = normalize_key(row.get("location"))
    if location:
        return f"loc:{location}"
    init = normalize_key(row.get("canonical_initiator") or row.get("initiator"))
    target = normalize_key(row.get("canonical_target") or row.get("target"))
    if init or target:
        return f"actor:{init}->{target}"
    return "none"


def same_or_missing_location(a: Record, b: Record) -> bool:
    la = normalize_key(a.location)
    lb = normalize_key(b.location)
    return not la or not lb or la == lb


def link_threshold(a: Record, b: Record) -> float | None:
    gap = day_gap(a, b)
    max_gap = window_days(a.event_family, a.event_action)
    if gap > max_gap:
        return None
    if a.tone and b.tone and {a.tone, b.tone} == {"positive", "negative"}:
        return None

    if is_roundup_title(a.title) or is_roundup_title(b.title):
        if a.title_key and a.title_key == b.title_key and len(a.title_key) >= 16:
            return 0.0
        return None

    if a.title_key and a.title_key == b.title_key and len(a.title_key) >= 16:
        return 0.0

    pair_equal = (
        meaningful_pair(a.entity_pair_key)
        and a.entity_pair_key == b.entity_pair_key
    )
    if pair_equal and full_pair(a.entity_pair_key):
        if a.event_action in {
            "military_attack",
            "terror_attack",
            "protest",
            "crackdown_arrest",
            "disaster_response",
        }:
            return 0.44
        if a.event_action in {"meeting_visit", "agreement_signed", "negotiation_talks", "ceasefire_peace_talks"}:
            return 0.30
        if a.event_action == "statement_condemnation":
            return 0.36
        return 0.34
    if pair_equal:
        return 0.50
    if same_or_missing_location(a, b):
        return 0.62
    return 0.72


def can_link(a: Record, b: Record) -> tuple[bool, float]:
    threshold = link_threshold(a, b)
    if threshold is None:
        return False, 0.0
    score = text_similarity(a, b)
    return score >= threshold, score


def fetch_records(conn: Any, args: argparse.Namespace) -> list[Record]:
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

    records: list[Record] = []
    for row in rows:
        published_at = parse_dt(row.get("published_at"))
        title = clean_text(row.get("title"))
        body_short = clean_text(row.get("body_short"))
        short_text = f"{title} {body_short}".strip()
        event_family = clean_text(row.get("event_family")) or "other"
        event_action = clean_text(row.get("event_action")) or "other"
        event_domain = clean_text(row.get("event_domain")) or "political"
        title_key = normalize_key(title)
        records.append(
            Record(
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
                entity_pair_key=clean_text(row.get("entity_pair_key")) or None,
                location=row.get("location"),
                tone=clean_text(row.get("tone")) or None,
                title_key=title_key,
                text_tokens=token_set(short_text),
                title_grams=char_grams(title),
                bucket_key=(event_family, event_action, actor_bucket(row)),
            )
        )
    return records


def build_clusters(records: list[Record], *, max_candidates: int) -> dict[str, list[int]]:
    if not records:
        return {}

    by_id = {r.news_id: r for r in records}
    uf = UnionFind([str(r.news_id) for r in records])
    buckets: dict[tuple[str, str, str], list[Record]] = defaultdict(list)
    for record in records:
        buckets[record.bucket_key].append(record)

    total_edges = 0
    for bucket_key, group in buckets.items():
        group.sort(key=lambda r: (r.published_date or date.min, r.news_id))
        active: deque[Record] = deque()
        max_window = window_days(bucket_key[0], bucket_key[1])
        for record in group:
            while active and record.published_date and active[0].published_date:
                if (record.published_date - active[0].published_date).days <= max_window:
                    break
                active.popleft()

            candidates = list(active)[-max_candidates:]
            scored_edges: list[tuple[float, Record]] = []
            for candidate in candidates:
                ok, score = can_link(record, candidate)
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
                    total_edges += 1
            active.append(record)

    grouped: dict[str, list[int]] = defaultdict(list)
    for record in records:
        grouped[uf.find(str(record.news_id))].append(record.news_id)

    split_clusters: list[list[int]] = []
    for members in grouped.values():
        split_clusters.extend(split_overwide_cluster(members, by_id))
    LOGGER.info(
        "fast coref records=%d buckets=%d edges=%d clusters=%d",
        len(records),
        len(buckets),
        total_edges,
        len(split_clusters),
    )
    return {str(i): sorted(members) for i, members in enumerate(split_clusters)}


def split_overwide_cluster(members: list[int], by_id: dict[int, Record]) -> list[list[int]]:
    if len(members) <= 2:
        return [sorted(members)]
    rows = sorted(
        (by_id[mid] for mid in members if mid in by_id),
        key=lambda r: (r.published_date or date.min, r.news_id),
    )
    if not rows:
        return [sorted(members)]
    max_window = window_days(rows[0].event_family, rows[0].event_action)
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


def stable_cluster_id(run_id: str, article_ids: list[int]) -> str:
    payload = ",".join(str(x) for x in sorted(article_ids))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{run_id}_{digest}"


def mode(values: Iterable[str | None], default: str | None = None) -> str | None:
    clean = [value for value in values if value]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def write_clusters(
    conn: Any,
    clusters: dict[str, list[int]],
    records: list[Record],
    *,
    run_id: str,
    clear_existing: bool,
) -> None:
    lookup = {r.news_id: r for r in records}
    with conn.cursor() as cur:
        if clear_existing:
            cur.execute("TRUNCATE public.event_coref_members, public.event_coref_clusters CASCADE")
        else:
            cur.execute("DELETE FROM public.event_coref_clusters WHERE run_id = %s", (run_id,))
    conn.commit()

    cluster_values = []
    member_values = []
    for members in sorted(clusters.values(), key=lambda m: (-len(m), min(m))):
        rows = [lookup[mid] for mid in sorted(members) if mid in lookup]
        if not rows:
            continue
        dates = [row.published_date for row in rows if row.published_date]
        cluster_id = stable_cluster_id(run_id, [row.news_id for row in rows])
        event_family = mode([row.event_family for row in rows], "other")
        event_action = mode([row.event_action for row in rows], "other")
        event_domain = mode([row.event_domain for row in rows], "political")
        initiator = mode([row.initiator for row in rows], None)
        target = mode([row.target for row in rows], None)
        location = mode([row.location for row in rows], None)
        tone = mode([row.tone for row in rows], "neutral")
        quality = "singleton" if len(rows) == 1 else "fast_rule_candidate"
        title = rows[0].title[:200]
        cluster_values.append(
            (
                cluster_id,
                run_id,
                len(rows),
                event_domain,
                event_family,
                event_family,
                event_action,
                initiator,
                target,
                location,
                tone,
                event_action,
                min(dates) if dates else None,
                max(dates) if dates else None,
                quality,
                title,
            )
        )
        for row in rows:
            member_values.append(
                (
                    cluster_id,
                    run_id,
                    row.news_id,
                    row.event_domain,
                    row.event_family,
                    row.event_family,
                    row.event_action,
                    row.initiator,
                    row.target,
                    row.event_action,
                    row.published_at,
                    1.0,
                )
            )

    if not cluster_values:
        return

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_clusters (
                cluster_id, run_id, article_count, event_domain, event_type,
                event_family, event_action, initiator, target, location, tone,
                dominant_trigger, start_date, end_date, cluster_quality, title
            )
            VALUES %s
            ON CONFLICT (cluster_id) DO UPDATE SET
                article_count = EXCLUDED.article_count,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                location = EXCLUDED.location,
                tone = EXCLUDED.tone,
                dominant_trigger = EXCLUDED.dominant_trigger,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                cluster_quality = EXCLUDED.cluster_quality,
                title = EXCLUDED.title,
                updated_at = now()
            """,
            cluster_values,
            page_size=1000,
        )
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_members (
                cluster_id, run_id, news_id, event_domain, event_type,
                event_family, event_action, initiator, target, trigger,
                published_at, membership_score
            )
            VALUES %s
            ON CONFLICT (cluster_id, news_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                trigger = EXCLUDED.trigger,
                published_at = EXCLUDED.published_at,
                membership_score = EXCLUDED.membership_score
            """,
            member_values,
            page_size=3000,
        )
    conn.commit()


def print_report(
    clusters: dict[str, list[int]],
    records: list[Record],
    *,
    sample_limit: int,
    min_cluster_size: int,
) -> None:
    lookup = {r.news_id: r for r in records}
    sizes = [len(members) for members in clusters.values()]
    non_singleton = [members for members in clusters.values() if len(members) >= 2]
    print("summary")
    print(f"records={len(records)}")
    print(f"clusters={len(clusters)}")
    print(f"non_singleton={len(non_singleton)}")
    print(f"singleton={sum(1 for size in sizes if size == 1)}")
    print(f"max_cluster={max(sizes) if sizes else 0}")
    print("size_top", sorted(sizes, reverse=True)[:20])

    family_counts = Counter()
    for members in non_singleton:
        rows = [lookup[mid] for mid in members if mid in lookup]
        family_counts[(mode([r.event_family for r in rows]), mode([r.event_action for r in rows]))] += 1
    print("top_family_action_clusters")
    for (family, action), count in family_counts.most_common(15):
        print(f"{family}/{action}: {count}")

    print("sample_clusters")
    shown = 0
    for members in sorted(non_singleton, key=lambda m: (-len(m), min(m))):
        if len(members) < min_cluster_size:
            continue
        rows = [lookup[mid] for mid in sorted(members) if mid in lookup]
        if not rows:
            continue
        print("-" * 80)
        print(
            f"size={len(rows)} family={mode([r.event_family for r in rows])} "
            f"action={mode([r.event_action for r in rows])} "
            f"pair={mode([r.entity_pair_key for r in rows])} "
            f"dates={rows[0].published_date}..{rows[-1].published_date}"
        )
        for row in rows[:8]:
            print(f"  {row.news_id} {row.published_date} {row.entity_pair_key or '→'} | {row.title[:140]}")
        shown += 1
        if shown >= sample_limit:
            break


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
        clusters = build_clusters(records, max_candidates=args.max_candidates)
        print_report(
            clusters,
            records,
            sample_limit=args.sample_limit,
            min_cluster_size=args.min_report_cluster_size,
        )
        if not args.dry_run:
            write_clusters(
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
