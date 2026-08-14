#!/usr/bin/env python3
"""Build L3 macro event graphs from L2 micro chains."""
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
from typing import Any

from psycopg2.extras import Json, RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ensure_news_l1_infra import add_db_args, connect

LOGGER = logging.getLogger("run_news_l3_macro_events")
RUN_ID = "fast_l3_v1"
DEFAULT_L2_RUN_ID = "fast_l2_v1"


@dataclass(slots=True)
class L2Chain:
    chain_id: str
    run_id: str
    segment_count: int
    article_count: int
    family_group: str | None
    event_family: str | None
    event_action: str | None
    pair_key: str | None
    initiator: str | None
    target: str | None
    start_date: date | None
    end_date: date | None
    title: str | None
    chain_quality: str | None
    quality_score: float
    risk_flags: Any
    l1_cluster_ids: list[str]


MACRO_SPECS: dict[str, dict[str, Any]] = {
    "us_iran_regional_conflict": {
        "title": "US-Iran confrontation and regional war",
        "family_group": "security",
        "tokens": {"iran"},
        "context": {
            "us", "trump", "israel", "nato", "gulf", "hormuz", "oil", "nuclear",
            "tehran", "missile", "hezbollah", "imf", "war", "strike", "sanction",
        },
    },
    "russia_ukraine_war": {
        "title": "Russia-Ukraine war and international response",
        "family_group": "security",
        "tokens": {"russia", "ukraine"},
        "context": {"nato", "us", "eu", "missile", "drone", "sanction", "ceasefire", "peace"},
    },
    "israel_gaza_war": {
        "title": "Israel-Gaza war and hostage negotiations",
        "family_group": "security",
        "tokens": {"israel"},
        "context": {"gaza", "hamas", "hostage", "palestinian", "rafah"},
    },
    "china_us_strategic_competition": {
        "title": "China-US strategic competition",
        "family_group": "economic_security",
        "tokens": {"china", "us"},
        "context": {"tariff", "trade", "chip", "taiwan", "export", "sanction", "tech", "xi", "trump"},
    },
    "china_taiwan_cross_strait": {
        "title": "China-Taiwan cross-strait pressure",
        "family_group": "political_diplomacy",
        "tokens": {"china", "taiwan"},
        "context": {"lai", "pla", "strait", "beijing", "diplomatic", "eswatini"},
    },
    "cambodia_thailand_border_conflict": {
        "title": "Cambodia-Thailand border conflict",
        "family_group": "security",
        "tokens": {"cambodia", "thailand"},
        "context": {"border", "ceasefire", "jbc", "siem", "reap"},
    },
    "us_venezuela_pressure": {
        "title": "US pressure campaign on Venezuela",
        "family_group": "security",
        "tokens": {"us", "venezuela"},
        "context": {"maduro", "caribbean", "drug", "military", "sanction"},
    },
}

COUNTRY_ALIASES = {
    "united states": "us",
    "u s": "us",
    "usa": "us",
    "america": "us",
    "american": "us",
    "washington": "us",
    "trump": "us",
    "biden": "us",
    "russian": "russia",
    "russian federation": "russia",
    "moscow": "russia",
    "kremlin": "russia",
    "ukrainian": "ukraine",
    "kyiv": "ukraine",
    "kiev": "ukraine",
    "prc": "china",
    "chinese": "china",
    "beijing": "china",
    "xi": "china",
    "iranian": "iran",
    "tehran": "iran",
    "israeli": "israel",
    "jerusalem": "israel",
    "south korea": "south korea",
    "north korea": "north korea",
    "uk": "uk",
    "britain": "uk",
    "british": "uk",
    "european union": "eu",
}

KNOWN_ACTORS = {
    "us", "china", "russia", "ukraine", "iran", "israel", "gaza", "hamas",
    "hezbollah", "taiwan", "eu", "nato", "uk", "south korea", "north korea",
    "india", "pakistan", "venezuela", "cambodia", "thailand", "mexico",
    "canada", "japan", "australia", "turkey", "saudi arabia", "qatar",
    "iraq", "syria", "lebanon", "gulf", "imf", "oil",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "says", "said", "after",
    "before", "over", "under", "into", "amid", "about", "near", "will", "could",
    "would", "should", "latest", "world", "news", "watch", "video", "live",
    "update", "updates", "analysis", "opinion", "report", "reports", "more",
    "daily", "edition", "brief", "briefing", "statement", "official", "minister",
    "president", "foreign", "media", "press", "mfa", "new", "headlines",
}

NOISE_TITLE_RE = re.compile(
    r"(latest news & headlines|^delete$|:\s*delete$|france diplomatie|"
    r"q&a\s*-|q&r\s*-|balance of power:|daybreak|closing bell)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build L3 macro event graphs from L2 chains.")
    add_db_args(parser)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--l2-run-id", default=DEFAULT_L2_RUN_ID)
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-l2-chains", type=int, default=8)
    parser.add_argument("--min-total-segments", type=int, default=18)
    parser.add_argument("--min-chain-segments", type=int, default=2)
    parser.add_argument("--max-context-edges", type=int, default=180)
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_text(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff]+", " ", text)
    return " ".join(text.split())


def canonical_actor(value: str) -> str:
    value = normalize_text(value)
    if not value:
        return ""
    if value in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[value]
    return value


def text_tokens(value: str | None) -> set[str]:
    text = normalize_text(value)
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text):
        if token in STOPWORDS:
            continue
        if len(token) >= 3:
            tokens.add(COUNTRY_ALIASES.get(token, token))
    return tokens


def chain_corpus(chain: L2Chain) -> str:
    return " ".join(
        clean_text(item)
        for item in [
            chain.title,
            chain.pair_key,
            chain.initiator,
            chain.target,
            chain.family_group,
            chain.event_family,
            chain.event_action,
        ]
        if item
    )


def chain_tokens(chain: L2Chain) -> set[str]:
    return text_tokens(chain_corpus(chain))


def chain_actors(chain: L2Chain) -> set[str]:
    raw_parts = [
        chain.initiator,
        chain.target,
        *(re.split(r"↔|->|→|,", chain.pair_key or "")),
    ]
    actors: set[str] = set()
    corpus = normalize_text(chain_corpus(chain))
    for part in raw_parts:
        actor = canonical_actor(clean_text(part))
        if actor in COUNTRY_ALIASES:
            actor = COUNTRY_ALIASES[actor]
        if actor in KNOWN_ACTORS:
            actors.add(actor)
    for alias, actor in COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", corpus):
            actors.add(actor)
    for actor in KNOWN_ACTORS:
        if re.search(rf"\b{re.escape(actor)}\b", corpus):
            actors.add(actor)
    return actors


def macro_spec_matches(chain: L2Chain, spec: dict[str, Any]) -> bool:
    tokens = chain_tokens(chain) | chain_actors(chain)
    required = set(spec.get("tokens") or set())
    context = set(spec.get("context") or set())
    if not required <= tokens:
        return False
    return not context or bool(tokens & context)


def family_group(chain: L2Chain) -> str:
    return clean_text(chain.family_group or chain.event_family or "mixed") or "mixed"


def macro_keys_for(chain: L2Chain) -> set[str]:
    if is_noise_chain(chain):
        return set()
    keys: set[str] = set()
    for key, spec in MACRO_SPECS.items():
        if macro_spec_matches(chain, spec):
            keys.add(key)
    actors = sorted(actor for actor in chain_actors(chain) if actor not in {"oil", "imf"})
    if len(actors) >= 2 and (chain.segment_count >= 3 or chain.article_count >= 3):
        keys.add(f"{family_group(chain)}:{actors[0]}_{actors[1]}")
    return keys


def is_noise_chain(chain: L2Chain) -> bool:
    title = chain.title or ""
    if NOISE_TITLE_RE.search(title):
        return True
    if chain.chain_quality == "watchlist" and chain.segment_count <= 2:
        return True
    return False


def stable_id(run_id: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{run_id}_{digest}"


def title_similarity(left: L2Chain, right: L2Chain) -> float:
    a = text_tokens(left.title)
    b = text_tokens(right.title)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def gap_days(left: L2Chain, right: L2Chain) -> int:
    if left.end_date is None or right.start_date is None:
        return 999
    return (right.start_date - left.end_date).days


def date_span_days(chains: list[L2Chain]) -> int:
    dates = [item.start_date for item in chains if item.start_date] + [item.end_date for item in chains if item.end_date]
    if not dates:
        return 0
    return (max(dates) - min(dates)).days


def chain_importance(chain: L2Chain, macro_key: str) -> float:
    quality = float(chain.quality_score or 0.0)
    base = chain.segment_count * 1.8 + min(18, chain.article_count) * 0.72 + quality * 5.0
    if macro_key in MACRO_SPECS:
        base += 2.4
    if chain.chain_quality == "strong":
        base += 1.2
    if NOISE_TITLE_RE.search(chain.title or ""):
        base -= 6.0
    return round(max(0.1, base), 4)


def classify_lane(chain: L2Chain) -> str:
    text = normalize_text(chain_corpus(chain))
    family = family_group(chain)
    action = clean_text(chain.event_action)
    if family == "security" or any(token in text for token in ["attack", "strike", "missile", "war", "military", "ceasefire"]):
        return "conflict"
    if any(token in text for token in ["talk", "meeting", "visit", "deal", "negotiation", "summit"]) or family == "political_diplomacy":
        return "diplomacy"
    if family == "economic_security" or any(token in text for token in ["oil", "market", "tariff", "trade", "sanction", "imf"]):
        return "economic"
    if action in {"statement_condemnation", "court_ruling", "leadership_change"}:
        return "political"
    return "context"


def classify_edge(left: L2Chain, right: L2Chain, *, backbone: bool) -> tuple[str, str]:
    text = normalize_text(f"{left.title or ''} {right.title or ''} {right.event_action or ''}")
    gap = gap_days(left, right)
    if gap <= 0:
        return ("parallel", "时间重叠")
    if any(token in text for token in ["attack", "strike", "missile", "war", "military", "escalat", "sanction", "tariff"]):
        return ("escalation", "冲突或制裁升级")
    if any(token in text for token in ["ceasefire", "peace", "talk", "meeting", "visit", "deal", "negotiation"]):
        return ("diplomacy", "外交谈判或协议推进")
    if any(token in text for token in ["oil", "market", "trade", "imf", "tariff"]):
        return ("market_reaction", "经济或市场反应")
    if backbone:
        return ("macro_sequence", "宏观时间推进")
    return ("context", "共享主体和议题的派生关联")


def relation_score(left: L2Chain, right: L2Chain) -> tuple[float, dict[str, Any]]:
    actors_left = chain_actors(left)
    actors_right = chain_actors(right)
    shared_actors = actors_left & actors_right
    tokens_left = chain_tokens(left)
    tokens_right = chain_tokens(right)
    shared_topics = (tokens_left & tokens_right) - shared_actors
    gap = max(0, gap_days(left, right))
    sim = title_similarity(left, right)
    time_score = max(0.0, 1.0 - min(gap, 45) / 45.0)
    score = (
        min(0.32, len(shared_actors) * 0.12)
        + min(0.22, len(shared_topics) * 0.045)
        + min(0.28, sim)
        + (0.10 if family_group(left) == family_group(right) else 0.0)
        + time_score * 0.18
    )
    return round(max(0.05, min(1.0, score)), 4), {
        "shared_actors": sorted(shared_actors),
        "shared_actor_count": len(shared_actors),
        "shared_topic_count": len(shared_topics),
        "title_similarity": round(sim, 4),
        "gap_days": gap,
    }


def build_edges(chains: list[L2Chain], *, max_context_edges: int) -> list[dict[str, Any]]:
    ordered = sorted(chains, key=lambda item: (item.start_date or date.min, item.end_date or date.min, item.chain_id))
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for left, right in zip(ordered, ordered[1:]):
        score, metrics = relation_score(left, right)
        edge_type, reason = classify_edge(left, right, backbone=True)
        key = (left.chain_id, right.chain_id, "backbone")
        seen.add(key)
        edges.append({
            "from_chain_id": left.chain_id,
            "to_chain_id": right.chain_id,
            "edge_type": edge_type,
            "layer": "story",
            "edge_weight": max(0.35, score),
            "relation_reason": reason,
            **metrics,
        })

    candidates: list[tuple[float, dict[str, Any]]] = []
    for idx, left in enumerate(ordered):
        for right in ordered[idx + 2 : idx + 18]:
            score, metrics = relation_score(left, right)
            if score < 0.48:
                continue
            edge_type, reason = classify_edge(left, right, backbone=False)
            candidates.append((score, {
                "from_chain_id": left.chain_id,
                "to_chain_id": right.chain_id,
                "edge_type": edge_type,
                "layer": "context",
                "edge_weight": score,
                "relation_reason": reason,
                **metrics,
            }))

    for _, edge in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_context_edges]:
        key = (edge["from_chain_id"], edge["to_chain_id"], "context")
        if key in seen:
            continue
        seen.add(key)
        edges.append(edge)
    return edges


def fetch_l2_chains(conn: Any, run_id: str, min_chain_segments: int) -> list[L2Chain]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT chain_id, run_id, segment_count, article_count, family_group,
                   event_family, event_action, pair_key, initiator, target,
                   start_date, end_date, title, chain_quality, quality_score, risk_flags,
                   COALESCE(l1_stats.l1_cluster_ids, ARRAY[]::text[]) AS l1_cluster_ids
            FROM public.event_l2_chains AS c
            LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT l1_cluster_id ORDER BY l1_cluster_id) AS l1_cluster_ids
                FROM public.event_l2_chain_segments
                WHERE run_id = c.run_id
                  AND chain_id = c.chain_id
            ) AS l1_stats ON TRUE
            WHERE c.run_id = %s
              AND c.segment_count >= %s
              AND c.chain_quality IN ('strong', 'usable')
            ORDER BY c.start_date NULLS LAST, c.end_date NULLS LAST, c.chain_id
            """,
            (run_id, min_chain_segments),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return [
        L2Chain(
            chain_id=str(row["chain_id"]),
            run_id=str(row["run_id"]),
            segment_count=int(row["segment_count"] or 0),
            article_count=int(row["article_count"] or 0),
            family_group=clean_text(row.get("family_group")) or None,
            event_family=clean_text(row.get("event_family")) or None,
            event_action=clean_text(row.get("event_action")) or None,
            pair_key=clean_text(row.get("pair_key")) or None,
            initiator=clean_text(row.get("initiator")) or None,
            target=clean_text(row.get("target")) or None,
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            title=clean_text(row.get("title")) or None,
            chain_quality=clean_text(row.get("chain_quality")) or None,
            quality_score=float(row.get("quality_score") or 0.0),
            risk_flags=row.get("risk_flags"),
            l1_cluster_ids=[str(item) for item in row.get("l1_cluster_ids") or []],
        )
        for row in rows
    ]


def ensure_l3_infra(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l3_macro_events (
                macro_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                l2_run_id TEXT NOT NULL,
                macro_key TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                family_group TEXT,
                l2_chain_count INTEGER NOT NULL DEFAULT 0,
                l1_cluster_count INTEGER NOT NULL DEFAULT 0,
                segment_count INTEGER NOT NULL DEFAULT 0,
                article_count INTEGER NOT NULL DEFAULT 0,
                start_date DATE,
                end_date DATE,
                actor_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                topic_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                quality_score DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l3_macro_members (
                macro_id TEXT NOT NULL REFERENCES public.event_l3_macro_events(macro_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                l2_run_id TEXT NOT NULL,
                l2_chain_id TEXT NOT NULL,
                node_order INTEGER NOT NULL,
                role TEXT,
                lane TEXT,
                family_group TEXT,
                pair_key TEXT,
                title TEXT,
                segment_count INTEGER NOT NULL DEFAULT 0,
                article_count INTEGER NOT NULL DEFAULT 0,
                start_date DATE,
                end_date DATE,
                importance_score DOUBLE PRECISION,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (macro_id, l2_chain_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l3_macro_edges (
                macro_id TEXT NOT NULL REFERENCES public.event_l3_macro_events(macro_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                from_chain_id TEXT NOT NULL,
                to_chain_id TEXT NOT NULL,
                edge_type TEXT,
                layer TEXT,
                edge_weight DOUBLE PRECISION,
                relation_reason TEXT,
                gap_days INTEGER,
                shared_actor_count INTEGER,
                shared_topic_count INTEGER,
                title_similarity DOUBLE PRECISION,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (macro_id, from_chain_id, to_chain_id, layer)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l3_macro_events_run ON public.event_l3_macro_events (run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l3_macro_events_score ON public.event_l3_macro_events (run_id, quality_score DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l3_macro_members_run_macro ON public.event_l3_macro_members (run_id, macro_id, node_order)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l3_macro_edges_run_macro ON public.event_l3_macro_edges (run_id, macro_id)")
    conn.commit()


def build_macro_events(
    chains: list[L2Chain],
    *,
    run_id: str,
    min_l2_chains: int,
    min_total_segments: int,
    max_context_edges: int,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[L2Chain]] = defaultdict(list)
    for chain in chains:
        for key in macro_keys_for(chain):
            buckets[key].append(chain)

    macros: dict[str, dict[str, Any]] = {}
    for key, members in buckets.items():
        unique = {chain.chain_id: chain for chain in members}
        members = sorted(unique.values(), key=lambda item: (item.start_date or date.min, item.end_date or date.min, item.chain_id))
        total_segments = sum(item.segment_count for item in members)
        if len(members) < min_l2_chains or total_segments < min_total_segments:
            continue
        macro_id = stable_id(run_id, key)
        spec = MACRO_SPECS.get(key, {})
        actors = Counter(actor for chain in members for actor in chain_actors(chain))
        topics = Counter(token for chain in members for token in chain_tokens(chain) if token not in actors and token not in STOPWORDS)
        dates = [item.start_date for item in members if item.start_date] + [item.end_date for item in members if item.end_date]
        quality = macro_quality_score(members, key)
        family = clean_text(spec.get("family_group")) or Counter(family_group(item) for item in members).most_common(1)[0][0]
        title = clean_text(spec.get("title")) or macro_title(key, actors, family)
        macros[key] = {
            "macro_id": macro_id,
            "macro_key": key,
            "title": title,
            "summary": macro_summary(title, members),
            "family_group": family,
            "chains": members,
            "edges": build_edges(members, max_context_edges=max_context_edges),
            "l2_chain_count": len(members),
            "segment_count": total_segments,
            "article_count": sum(item.article_count for item in members),
            "l1_cluster_count": len({cluster_id for item in members for cluster_id in item.l1_cluster_ids}),
            "start_date": min(dates) if dates else None,
            "end_date": max(dates) if dates else None,
            "actor_counts": dict(actors.most_common(16)),
            "topic_counts": dict(topics.most_common(18)),
            "quality_score": quality,
        }
    return macros


def macro_quality_score(chains: list[L2Chain], key: str) -> float:
    if not chains:
        return 0.0
    avg_quality = sum(float(item.quality_score or 0.0) for item in chains) / len(chains)
    span = date_span_days(chains)
    scale = min(1.0, len(chains) / 80.0) * 0.26 + min(1.0, sum(item.segment_count for item in chains) / 180.0) * 0.24
    span_score = min(1.0, span / 120.0) * 0.18
    spec_bonus = 0.08 if key in MACRO_SPECS else 0.0
    score = avg_quality * 0.24 + scale + span_score + spec_bonus
    return round(max(0.05, min(1.0, score)), 4)


def macro_title(key: str, actors: Counter[str], family: str) -> str:
    if ":" in key:
        _, pair = key.split(":", 1)
        return f"{pair.replace('_', ' / ')}: {family.replace('_', ' ')} macro event"
    top = [actor for actor, _ in actors.most_common(2)]
    return f"{' / '.join(top) if top else key}: macro event"


def macro_summary(title: str, chains: list[L2Chain]) -> str:
    span = date_span_days(chains)
    return (
        f"{title} aggregates {len(chains)} L2 micro chains, "
        f"{sum(item.segment_count for item in chains)} L1.5 segments, "
        f"and {sum(item.article_count for item in chains)} articles across {span} days."
    )


def write_macros(conn: Any, macros: dict[str, dict[str, Any]], *, run_id: str, l2_run_id: str, clear_existing: bool) -> tuple[int, int, int]:
    macro_values: list[tuple[Any, ...]] = []
    member_values: list[tuple[Any, ...]] = []
    edge_values: list[tuple[Any, ...]] = []

    for macro in sorted(macros.values(), key=lambda item: (-item["quality_score"], -item["l2_chain_count"], item["macro_key"])):
        chains = macro["chains"]
        macro_values.append((
            macro["macro_id"],
            run_id,
            l2_run_id,
            macro["macro_key"],
            macro["title"],
            macro["summary"],
            macro["family_group"],
            macro["l2_chain_count"],
            macro["l1_cluster_count"],
            macro["segment_count"],
            macro["article_count"],
            macro["start_date"],
            macro["end_date"],
            Json(macro["actor_counts"]),
            Json(macro["topic_counts"]),
            macro["quality_score"],
        ))
        for idx, chain in enumerate(chains, start=1):
            member_values.append((
                macro["macro_id"],
                run_id,
                l2_run_id,
                chain.chain_id,
                idx,
                "backbone" if chain.segment_count >= 4 or chain_importance(chain, macro["macro_key"]) >= 10 else "support",
                classify_lane(chain),
                family_group(chain),
                chain.pair_key,
                chain.title,
                chain.segment_count,
                chain.article_count,
                chain.start_date,
                chain.end_date,
                chain_importance(chain, macro["macro_key"]),
                Json({
                    "chain_quality": chain.chain_quality,
                    "quality_score": chain.quality_score,
                    "event_family": chain.event_family,
                    "event_action": chain.event_action,
                    "initiator": chain.initiator,
                    "target": chain.target,
                    "actors": sorted(chain_actors(chain)),
                }),
            ))
        for edge in macro["edges"]:
            edge_values.append((
                macro["macro_id"],
                run_id,
                edge["from_chain_id"],
                edge["to_chain_id"],
                edge["edge_type"],
                edge["layer"],
                edge["edge_weight"],
                edge["relation_reason"],
                edge["gap_days"],
                edge["shared_actor_count"],
                edge["shared_topic_count"],
                edge["title_similarity"],
                Json({
                    "shared_actors": edge.get("shared_actors", []),
                }),
            ))

    with conn.cursor() as cur:
        if clear_existing:
            cur.execute("DELETE FROM public.event_l3_macro_events WHERE run_id = %s", (run_id,))
        if macro_values:
            execute_values(
                cur,
                """
                INSERT INTO public.event_l3_macro_events (
                    macro_id, run_id, l2_run_id, macro_key, title, summary, family_group,
                    l2_chain_count, l1_cluster_count, segment_count, article_count,
                    start_date, end_date, actor_counts, topic_counts, quality_score
                ) VALUES %s
                ON CONFLICT (macro_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    family_group = EXCLUDED.family_group,
                    l2_chain_count = EXCLUDED.l2_chain_count,
                    segment_count = EXCLUDED.segment_count,
                    article_count = EXCLUDED.article_count,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    actor_counts = EXCLUDED.actor_counts,
                    topic_counts = EXCLUDED.topic_counts,
                    quality_score = EXCLUDED.quality_score,
                    updated_at = now()
                """,
                macro_values,
                page_size=500,
            )
        if member_values:
            execute_values(
                cur,
                """
                INSERT INTO public.event_l3_macro_members (
                    macro_id, run_id, l2_run_id, l2_chain_id, node_order, role, lane,
                    family_group, pair_key, title, segment_count, article_count,
                    start_date, end_date, importance_score, metadata
                ) VALUES %s
                ON CONFLICT (macro_id, l2_chain_id) DO UPDATE SET
                    node_order = EXCLUDED.node_order,
                    role = EXCLUDED.role,
                    lane = EXCLUDED.lane,
                    family_group = EXCLUDED.family_group,
                    pair_key = EXCLUDED.pair_key,
                    title = EXCLUDED.title,
                    segment_count = EXCLUDED.segment_count,
                    article_count = EXCLUDED.article_count,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    importance_score = EXCLUDED.importance_score,
                    metadata = EXCLUDED.metadata
                """,
                member_values,
                page_size=1000,
            )
        if edge_values:
            execute_values(
                cur,
                """
                INSERT INTO public.event_l3_macro_edges (
                    macro_id, run_id, from_chain_id, to_chain_id, edge_type, layer,
                    edge_weight, relation_reason, gap_days, shared_actor_count,
                    shared_topic_count, title_similarity, metadata
                ) VALUES %s
                ON CONFLICT (macro_id, from_chain_id, to_chain_id, layer) DO UPDATE SET
                    edge_type = EXCLUDED.edge_type,
                    edge_weight = EXCLUDED.edge_weight,
                    relation_reason = EXCLUDED.relation_reason,
                    gap_days = EXCLUDED.gap_days,
                    shared_actor_count = EXCLUDED.shared_actor_count,
                    shared_topic_count = EXCLUDED.shared_topic_count,
                    title_similarity = EXCLUDED.title_similarity,
                    metadata = EXCLUDED.metadata
                """,
                edge_values,
                page_size=1000,
            )
    conn.commit()
    return len(macro_values), len(member_values), len(edge_values)


def print_report(macros: dict[str, dict[str, Any]], *, sample_limit: int) -> None:
    print("summary")
    print(f"macro_events={len(macros)}")
    print(f"macro_members={sum(len(item['chains']) for item in macros.values())}")
    print(f"macro_edges={sum(len(item['edges']) for item in macros.values())}")
    print("top_macro_events")
    for macro in sorted(macros.values(), key=lambda item: (-item["quality_score"], -item["l2_chain_count"], item["macro_key"]))[:sample_limit]:
        print("-" * 80)
        print(
            f"{macro['macro_id']} key={macro['macro_key']} score={macro['quality_score']} "
            f"chains={macro['l2_chain_count']} segments={macro['segment_count']} "
            f"articles={macro['article_count']} dates={macro['start_date']}..{macro['end_date']}"
        )
        print(f"  {macro['title']}")
        for chain in macro["chains"][:5]:
            print(f"  - {chain.start_date} {classify_lane(chain)} n={chain.segment_count}/{chain.article_count} | {(chain.title or '')[:120]}")


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    t0 = time.time()
    conn = connect(args)
    ensure_l3_infra(conn)
    try:
        chains = fetch_l2_chains(conn, args.l2_run_id, args.min_chain_segments)
        LOGGER.info("loaded L2 chains=%d in %.1fs", len(chains), time.time() - t0)
        macros = build_macro_events(
            chains,
            run_id=args.run_id,
            min_l2_chains=args.min_l2_chains,
            min_total_segments=args.min_total_segments,
            max_context_edges=args.max_context_edges,
        )
        print_report(macros, sample_limit=args.sample_limit)
        if not args.dry_run:
            written = write_macros(
                conn,
                macros,
                run_id=args.run_id,
                l2_run_id=args.l2_run_id,
                clear_existing=args.clear_existing,
            )
            LOGGER.info("wrote L3 macros=%d members=%d edges=%d", *written)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
