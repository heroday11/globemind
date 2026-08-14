#!/usr/bin/env python3
"""Build L2 storyline chains from L1.5 segments.

L2 links nearby L1.5 segments into readable evolving stories. It operates on
segments, not raw articles, so broad L1 story cards do not become oversized
atomic nodes.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import Json, RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ensure_news_l1_infra import add_db_args, connect

LOGGER = logging.getLogger("run_news_l2_storylines")
RUN_ID = "fast_l2_v1"
DEFAULT_L15_RUN_ID = "fast_l15_v1"


@dataclass(slots=True)
class Segment:
    segment_id: str
    l1_cluster_id: str
    article_count: int
    event_domain: str | None
    event_family: str | None
    event_action: str | None
    story_angle: str | None
    initiator: str | None
    target: str | None
    location: str | None
    tone: str | None
    start_date: date | None
    end_date: date | None
    title: str | None


SYMMETRIC_ACTIONS = {
    "meeting_visit",
    "negotiation_talks",
    "agreement_signed",
    "ceasefire_peace_talks",
}
GLOBAL_ONLY_ACTORS = {"us", "united states", "china", "russia", "eu", "nato", "un", "uk"}
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "says", "say", "after",
    "before", "over", "under", "into", "amid", "about", "near", "will", "could",
    "would", "should", "latest", "world", "news", "watch", "video", "live",
    "update", "updates", "analysis", "opinion", "report", "reports",
    "more", "daily", "edition", "brief", "briefing", "statement", "statements",
    "official", "officials", "government", "minister", "president", "foreign",
    "media", "press", "mfa", "says", "said",
}
WEAK_TOPIC_TOKENS = {
    "trump", "putin", "zelensky", "zelenskyy", "biden", "kremlin", "white",
    "house", "russia", "russian", "ukraine", "ukrainian", "china", "chinese",
    "iran", "iranian", "israel", "israeli", "europe", "european", "america",
    "american", "united", "states", "state", "nato", "asean", "deal", "talks",
    "talk", "meeting", "summit", "warns", "threatens",
}
BROAD_ACTIONS = {
    "statement_condemnation",
    "sanction_export_control",
    "tariff_trade_dispute",
    "leadership_change",
    "other",
}
L2_NOISE_TITLE_RE = re.compile(
    r"(general staff:\s*russia has lost|has lost [0-9,]+ troops since|"
    r"france diplomatie$|q&r\s*-|q&a\s*-|quai d[’']orsay|"
    r"extract from the press briefing|extrait du point de presse|"
    r"over [0-9]+% of .* trust .*poll|trust .*latest poll shows|"
    r"more than [0-9]+% of .* trust .*poll|almost [0-9]+% of .* trust .*poll)",
    re.IGNORECASE,
)
L2_ROUNDUP_TITLE_RE = re.compile(
    r"(,\s*more\s*-\s*bloomberg$|"
    r"balance of power:|daybreak[^:]*:|closing bell[^:]*:|open interest[^:]*:)",
    re.IGNORECASE,
)
L2_NOISE_ACTOR_RE = re.compile(
    r"^(general staff|general staff of the armed forces of ukraine)$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build L2 storyline chains from L1.5 segments.")
    add_db_args(parser)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--l15-run-id", default=DEFAULT_L15_RUN_ID)
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--min-chain-segments", type=int, default=2)
    return parser.parse_args()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_actor(value: str | None) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff]+", " ", text)
    text = " ".join(text.split())
    aliases = {
        "united states": "us",
        "u s": "us",
        "usa": "us",
        "america": "us",
        "american": "us",
        "russian federation": "russia",
        "prc": "china",
        "people s republic of china": "china",
        "united kingdom": "uk",
        "britain": "uk",
        "british": "uk",
        "north korea": "north korea",
        "dprk": "north korea",
        "south korea": "south korea",
    }
    return aliases.get(text, text)


def mode(values: Iterable[str | None], default: str | None = None) -> str | None:
    clean = [value for value in values if value]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def stable_id(run_id: str, parts: Iterable[Any]) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{run_id}_{digest}"


def family_group(segment: Segment) -> str:
    family = segment.event_family or "other"
    if family in {"military_security", "civil_unrest", "security_crime", "terrorism_espionage"}:
        return "security"
    if family in {"economic_trade", "technology_industry"}:
        return "economic_security"
    if family in {"diplomacy", "law_policy", "domestic_politics"}:
        return "political_diplomacy"
    if family in {"human_rights_migration", "public_development", "disaster_environment"}:
        return "humanitarian_public"
    return family


def actor_pair_key(segment: Segment) -> str:
    left = normalize_actor(segment.initiator)
    right = normalize_actor(segment.target)
    if not left or not right:
        return "none"
    if segment.event_action in SYMMETRIC_ACTIONS:
        actors = sorted(actor for actor in (left, right) if actor)
        if actors:
            return "↔".join(actors)
    return f"{left}->{right}"


def max_gap_days(segment: Segment) -> int:
    family = segment.event_family or ""
    action = segment.event_action or ""
    if action in {"military_attack", "terror_attack", "protest", "crackdown_arrest"}:
        return 5
    if family in {"military_security", "civil_unrest", "security_crime"}:
        return 5
    if family in {"economic_trade", "technology_industry", "law_policy"}:
        return 14
    if family in {"diplomacy", "domestic_politics"}:
        return 14
    return 14


def max_chain_span_days(segment: Segment) -> int:
    family = segment.event_family or ""
    if family in {"military_security", "civil_unrest", "security_crime"}:
        return 14
    if family in {"economic_trade", "technology_industry", "law_policy"}:
        return 35
    if family in {"diplomacy", "domestic_politics"}:
        return 35
    return 30


def day_gap(a: Segment, b: Segment) -> int:
    if a.end_date is None or b.start_date is None:
        return 9999
    return (b.start_date - a.end_date).days


def chain_span(chain: list[Segment], candidate: Segment) -> int:
    dates = [seg.start_date for seg in chain if seg.start_date] + [candidate.start_date]
    ends = [seg.end_date for seg in chain if seg.end_date] + [candidate.end_date]
    clean_dates = [d for d in dates + ends if d]
    if not clean_dates:
        return 0
    return (max(clean_dates) - min(clean_dates)).days


def title_tokens(title: str | None) -> set[str]:
    text = clean_text(title).lower()
    tokens = set()
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text):
        if token in STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 1:
                tokens.add(token)
            else:
                tokens.update(token[i : i + 2] for i in range(len(token) - 1))
        elif len(token) >= 4:
            tokens.add(token)
    return tokens


def title_similarity(a: Segment, b: Segment) -> float:
    left = title_tokens(a.title)
    right = title_tokens(b.title)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def strong_topic_tokens(segment: Segment) -> set[str]:
    return title_tokens(segment.title) - WEAK_TOPIC_TOKENS


def shared_strong_topics(a: Segment, b: Segment) -> int:
    return len(strong_topic_tokens(a) & strong_topic_tokens(b))


def is_noise_segment(segment: Segment) -> bool:
    if L2_NOISE_TITLE_RE.search(segment.title or ""):
        return True
    if L2_NOISE_ACTOR_RE.search(segment.initiator or ""):
        return True
    if L2_ROUNDUP_TITLE_RE.search(segment.title or ""):
        return True
    if segment.story_angle == "official_update" and "france diplomatie" in (segment.title or "").lower():
        return True
    return False


def edge_metrics(previous: Segment | None, current: Segment) -> dict[str, Any]:
    if previous is None:
        return {
            "edge_weight": 1.0,
            "relation_reason": "chain_start",
            "title_similarity": 1.0,
            "shared_topic_count": 0,
            "gap_days": 0,
        }
    sim = title_similarity(previous, current)
    shared_topics = shared_strong_topics(previous, current)
    gap = max(0, day_gap(previous, current))
    same_l1 = previous.l1_cluster_id == current.l1_cluster_id
    same_action = previous.event_action == current.event_action
    same_family = previous.event_family == current.event_family
    angle_bonus = current.story_angle in {"market_reaction", "outcome_reaction", "analysis_context"}
    gap_score = max(0.0, 1.0 - min(gap, 14) / 14.0)
    weight = (
        (0.30 if same_l1 else 0.0)
        + min(0.30, sim)
        + min(0.20, shared_topics * 0.08)
        + (0.10 if same_action else 0.0)
        + (0.05 if same_family else 0.0)
        + (0.05 if angle_bonus else 0.0)
        + gap_score * 0.20
    )
    if sim < 0.12 and shared_topics == 0 and not same_l1:
        weight *= 0.55
    reason_parts = [
        f"sim={sim:.3f}",
        f"topics={shared_topics}",
        f"gap={gap}",
    ]
    if same_l1:
        reason_parts.append("same_l1")
    if same_action:
        reason_parts.append("same_action")
    if angle_bonus:
        reason_parts.append(f"angle={current.story_angle}")
    return {
        "edge_weight": round(max(0.05, min(1.0, weight)), 4),
        "relation_reason": "|".join(reason_parts),
        "title_similarity": round(sim, 4),
        "shared_topic_count": shared_topics,
        "gap_days": gap,
    }


def cross_l1_link_ok(previous: Segment, candidate: Segment) -> bool:
    if previous.l1_cluster_id == candidate.l1_cluster_id:
        return True
    sim = title_similarity(previous, candidate)
    shared_topics = shared_strong_topics(previous, candidate)
    gap = day_gap(previous, candidate)
    broad = previous.event_action in BROAD_ACTIONS or candidate.event_action in BROAD_ACTIONS
    if sim >= 0.25:
        return True
    if sim >= 0.18 and (shared_topics >= 1 or gap <= 2):
        return True
    if shared_topics >= 2 and sim >= 0.10:
        return True
    if shared_topics >= 1 and sim >= 0.12 and not broad:
        return True
    if previous.event_action == candidate.event_action and gap <= 2 and sim >= 0.12 and shared_topics >= 1:
        return True
    if candidate.story_angle in {"market_reaction", "outcome_reaction", "analysis_context"} and gap <= 2 and sim >= 0.12:
        return True
    return False


def edge_type(previous: Segment | None, current: Segment) -> str:
    if previous is None:
        return "start"
    if current.story_angle == "market_reaction":
        return "market_reaction"
    if previous.story_angle == "preview_planning" and current.story_angle in {"main_event", "official_update"}:
        return "preview_to_event"
    if current.story_angle == "outcome_reaction":
        return "event_to_outcome"
    if current.story_angle == "analysis_context":
        return "analysis_context"
    if current.event_action == previous.event_action:
        return "continuation"
    if current.event_action in {"agreement_signed", "ceasefire_peace_talks"}:
        return "resolution"
    if current.event_action in {"military_attack", "sanction_export_control", "tariff_trade_dispute"}:
        return "escalation"
    if current.event_action in {"meeting_visit", "negotiation_talks"}:
        return "de_escalation"
    return "progression"


def chain_quality_metrics(chain: list[Segment]) -> dict[str, Any]:
    if len(chain) <= 1:
        return {"label": "single", "score": 1.0, "flags": []}

    edge_rows = [edge_metrics(chain[idx - 1], chain[idx]) for idx in range(1, len(chain))]
    weights = [float(row["edge_weight"]) for row in edge_rows]
    similarities = [float(row["title_similarity"]) for row in edge_rows]
    gaps = [int(row["gap_days"]) for row in edge_rows]
    shared_topics = [int(row["shared_topic_count"]) for row in edge_rows]
    dates = [seg.start_date for seg in chain if seg.start_date] + [seg.end_date for seg in chain if seg.end_date]
    span_days = (max(dates) - min(dates)).days if dates else 0

    avg_weight = sum(weights) / len(weights)
    min_weight = min(weights)
    avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
    flags: list[str] = []
    if min_weight < 0.35:
        flags.append("weak_edge")
    if avg_weight < 0.52:
        flags.append("low_average_edge_weight")
    if avg_similarity < 0.16 and sum(shared_topics) < len(shared_topics):
        flags.append("low_title_overlap")
    if span_days > 28 and avg_weight < 0.62:
        flags.append("long_span")
    if any(gap >= 10 and topics == 0 and sim < 0.18 for gap, topics, sim in zip(gaps, shared_topics, similarities)):
        flags.append("long_gap_low_topic")

    penalty = min(0.32, 0.08 * len(flags))
    score = max(0.05, min(1.0, avg_weight - penalty))
    if score >= 0.72 and not flags:
        label = "strong"
    elif score < 0.50 or flags:
        label = "watchlist"
    else:
        label = "usable"
    return {"label": label, "score": round(score, 4), "flags": flags}


def fetch_segments(conn: Any, run_id: str) -> list[Segment]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT segment_id, l1_cluster_id, article_count, event_domain,
                   event_family, event_action, story_angle, initiator, target,
                   location, tone, start_date, end_date, title
            FROM public.event_l15_segments
            WHERE run_id = %s
            ORDER BY start_date NULLS LAST, end_date NULLS LAST, segment_id
            """,
            (run_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    return [
        Segment(
            segment_id=str(row["segment_id"]),
            l1_cluster_id=str(row["l1_cluster_id"]),
            article_count=int(row["article_count"] or 0),
            event_domain=clean_text(row.get("event_domain")) or None,
            event_family=clean_text(row.get("event_family")) or None,
            event_action=clean_text(row.get("event_action")) or None,
            story_angle=clean_text(row.get("story_angle")) or None,
            initiator=clean_text(row.get("initiator")) or None,
            target=clean_text(row.get("target")) or None,
            location=clean_text(row.get("location")) or None,
            tone=clean_text(row.get("tone")) or None,
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            title=clean_text(row.get("title")) or None,
        )
        for row in rows
    ]


def should_start_new_chain(chain: list[Segment], candidate: Segment) -> bool:
    if not chain:
        return False
    previous = chain[-1]
    gap = day_gap(previous, candidate)
    max_gap = min(max_gap_days(previous), max_gap_days(candidate))
    if gap > max_gap:
        return True
    if chain_span(chain, candidate) > max_chain_span_days(candidate):
        return True
    if len(chain) >= 12:
        return True
    if not cross_l1_link_ok(previous, candidate):
        return True
    return False


def build_chains(segments: list[Segment], *, run_id: str, min_chain_segments: int) -> dict[str, list[Segment]]:
    buckets: dict[tuple[str, str], list[Segment]] = defaultdict(list)
    for segment in segments:
        if is_noise_segment(segment):
            continue
        pair = actor_pair_key(segment)
        if pair == "none":
            continue
        buckets[(family_group(segment), pair)].append(segment)

    chains: dict[str, list[Segment]] = {}
    for bucket_key, group in buckets.items():
        group.sort(key=lambda seg: (seg.start_date or date.min, seg.end_date or date.min, seg.segment_id))
        current: list[Segment] = []
        for segment in group:
            if should_start_new_chain(current, segment):
                if len(current) >= min_chain_segments:
                    chains[stable_id(run_id, [bucket_key[0], bucket_key[1], current[0].segment_id, current[-1].segment_id])] = current
                current = [segment]
            else:
                current.append(segment)
        if len(current) >= min_chain_segments:
            chains[stable_id(run_id, [bucket_key[0], bucket_key[1], current[0].segment_id, current[-1].segment_id])] = current
    return chains


def ensure_l2_infra(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l2_chains (
                chain_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                l15_run_id TEXT NOT NULL,
                segment_count INTEGER NOT NULL DEFAULT 0,
                article_count INTEGER NOT NULL DEFAULT 0,
                family_group TEXT,
                event_family TEXT,
                event_action TEXT,
                pair_key TEXT,
                initiator TEXT,
                target TEXT,
                start_date DATE,
                end_date DATE,
                title TEXT,
                chain_quality TEXT,
                quality_score DOUBLE PRECISION,
                risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l2_chain_segments (
                chain_id TEXT NOT NULL REFERENCES public.event_l2_chains(chain_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                l15_run_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                l1_cluster_id TEXT NOT NULL,
                segment_order INTEGER NOT NULL,
                edge_type TEXT,
                event_family TEXT,
                event_action TEXT,
                story_angle TEXT,
                start_date DATE,
                end_date DATE,
                article_count INTEGER NOT NULL DEFAULT 0,
                edge_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                relation_reason TEXT,
                title_similarity DOUBLE PRECISION,
                shared_topic_count INTEGER,
                gap_days INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chain_id, segment_id)
            )
            """
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chain_segments "
            "ADD COLUMN IF NOT EXISTS edge_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chain_segments "
            "ADD COLUMN IF NOT EXISTS relation_reason TEXT"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chain_segments "
            "ADD COLUMN IF NOT EXISTS title_similarity DOUBLE PRECISION"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chain_segments "
            "ADD COLUMN IF NOT EXISTS shared_topic_count INTEGER"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chain_segments "
            "ADD COLUMN IF NOT EXISTS gap_days INTEGER"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chains "
            "ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION"
        )
        cur.execute(
            "ALTER TABLE public.event_l2_chains "
            "ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chains_run ON public.event_l2_chains (run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chains_pair ON public.event_l2_chains (pair_key, start_date, end_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chains_date ON public.event_l2_chains (start_date, end_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chains_quality ON public.event_l2_chains (run_id, chain_quality, quality_score DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chain_segments_run ON public.event_l2_chain_segments (run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l2_chain_segments_segment ON public.event_l2_chain_segments (segment_id)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_l2_chain_segments_run_l1_chain "
            "ON public.event_l2_chain_segments (run_id, l1_cluster_id, chain_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_l2_chain_segments_run_chain_order "
            "ON public.event_l2_chain_segments (run_id, chain_id, segment_order)"
        )
    conn.commit()


def chain_title(chain: list[Segment]) -> str:
    pair = actor_pair_key(chain[0])
    action = mode([seg.event_action for seg in chain], "story")
    family = mode([seg.event_family for seg in chain], "event")
    first_title = chain[0].title or ""
    if first_title:
        return f"{pair}: {first_title[:160]}"
    return f"{pair}: {family}/{action}"


def write_chains(
    conn: Any,
    chains: dict[str, list[Segment]],
    *,
    run_id: str,
    l15_run_id: str,
    clear_existing: bool,
) -> None:
    with conn.cursor() as cur:
        if clear_existing:
            cur.execute("TRUNCATE public.event_l2_chain_segments, public.event_l2_chains CASCADE")
        else:
            cur.execute("DELETE FROM public.event_l2_chains WHERE run_id = %s", (run_id,))
    conn.commit()

    chain_values = []
    member_values = []
    for chain_id, chain in sorted(chains.items(), key=lambda item: (-len(item[1]), item[0])):
        dates = [seg.start_date for seg in chain if seg.start_date] + [seg.end_date for seg in chain if seg.end_date]
        quality = chain_quality_metrics(chain)
        chain_values.append(
            (
                chain_id,
                run_id,
                l15_run_id,
                len(chain),
                sum(seg.article_count for seg in chain),
                family_group(chain[0]),
                mode([seg.event_family for seg in chain], None),
                mode([seg.event_action for seg in chain], None),
                actor_pair_key(chain[0]),
                mode([seg.initiator for seg in chain], None),
                mode([seg.target for seg in chain], None),
                min(dates) if dates else None,
                max(dates) if dates else None,
                chain_title(chain)[:240],
                quality["label"],
                quality["score"],
                Json(quality["flags"]),
            )
        )
        previous = None
        for idx, segment in enumerate(chain, start=1):
            metrics = edge_metrics(previous, segment)
            member_values.append(
                (
                    chain_id,
                    run_id,
                    l15_run_id,
                    segment.segment_id,
                    segment.l1_cluster_id,
                    idx,
                    edge_type(previous, segment),
                    segment.event_family,
                    segment.event_action,
                    segment.story_angle,
                    segment.start_date,
                    segment.end_date,
                    segment.article_count,
                    metrics["edge_weight"],
                    metrics["relation_reason"],
                    metrics["title_similarity"],
                    metrics["shared_topic_count"],
                    metrics["gap_days"],
                )
            )
            previous = segment

    if not chain_values:
        return

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.event_l2_chains (
                chain_id, run_id, l15_run_id, segment_count, article_count,
                family_group, event_family, event_action, pair_key, initiator, target,
                start_date, end_date, title, chain_quality, quality_score, risk_flags
            )
            VALUES %s
            ON CONFLICT (chain_id) DO UPDATE SET
                segment_count = EXCLUDED.segment_count,
                article_count = EXCLUDED.article_count,
                family_group = EXCLUDED.family_group,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                pair_key = EXCLUDED.pair_key,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                title = EXCLUDED.title,
                chain_quality = EXCLUDED.chain_quality,
                quality_score = EXCLUDED.quality_score,
                risk_flags = EXCLUDED.risk_flags,
                updated_at = now()
            """,
            chain_values,
            page_size=1000,
        )
        execute_values(
            cur,
            """
            INSERT INTO public.event_l2_chain_segments (
                chain_id, run_id, l15_run_id, segment_id, l1_cluster_id, segment_order,
                edge_type, event_family, event_action, story_angle, start_date, end_date, article_count
                , edge_weight, relation_reason, title_similarity, shared_topic_count, gap_days
            )
            VALUES %s
            ON CONFLICT (chain_id, segment_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                l15_run_id = EXCLUDED.l15_run_id,
                l1_cluster_id = EXCLUDED.l1_cluster_id,
                segment_order = EXCLUDED.segment_order,
                edge_type = EXCLUDED.edge_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                story_angle = EXCLUDED.story_angle,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                article_count = EXCLUDED.article_count,
                edge_weight = EXCLUDED.edge_weight,
                relation_reason = EXCLUDED.relation_reason,
                title_similarity = EXCLUDED.title_similarity,
                shared_topic_count = EXCLUDED.shared_topic_count,
                gap_days = EXCLUDED.gap_days
            """,
            member_values,
            page_size=3000,
        )
    conn.commit()


def print_report(chains: dict[str, list[Segment]], *, sample_limit: int) -> None:
    sizes = [len(chain) for chain in chains.values()]
    article_counts = [sum(seg.article_count for seg in chain) for chain in chains.values()]
    quality_rows = [chain_quality_metrics(chain) for chain in chains.values()]
    print("summary")
    print(f"chains={len(chains)}")
    print(f"segments_in_chains={sum(sizes)}")
    print(f"max_chain_segments={max(sizes) if sizes else 0}")
    print(f"max_chain_articles={max(article_counts) if article_counts else 0}")
    print("family_groups", Counter(family_group(chain[0]) for chain in chains.values() if chain).most_common(12))
    print("quality", Counter(row["label"] for row in quality_rows).most_common())
    print("sample_chains")
    for chain_id, chain in sorted(chains.items(), key=lambda item: (-len(item[1]), item[0]))[:sample_limit]:
        dates = [seg.start_date for seg in chain if seg.start_date] + [seg.end_date for seg in chain if seg.end_date]
        print("-" * 80)
        print(
            f"{chain_id} segments={len(chain)} articles={sum(seg.article_count for seg in chain)} "
            f"pair={actor_pair_key(chain[0])} dates={min(dates) if dates else None}..{max(dates) if dates else None}"
        )
        previous = None
        for segment in chain[:10]:
            print(
                f"  {edge_type(previous, segment)} {segment.start_date} "
                f"{segment.event_family}/{segment.event_action}/{segment.story_angle} "
                f"n={segment.article_count} | {(segment.title or '')[:140]}"
            )
            previous = segment


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    t0 = time.time()
    conn = connect(args)
    ensure_l2_infra(conn)
    try:
        segments = fetch_segments(conn, args.l15_run_id)
        LOGGER.info("loaded segments=%d in %.1fs", len(segments), time.time() - t0)
        chains = build_chains(segments, run_id=args.run_id, min_chain_segments=args.min_chain_segments)
        print_report(chains, sample_limit=args.sample_limit)
        if not args.dry_run:
            write_chains(
                conn,
                chains,
                run_id=args.run_id,
                l15_run_id=args.l15_run_id,
                clear_existing=args.clear_existing,
            )
            LOGGER.info("wrote L2 chains run_id=%s in %.1fs", args.run_id, time.time() - t0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
