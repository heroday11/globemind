"""
Event Evolution Chain — build causal storylines from L1 event clusters.

Architecture
------------
Groups L1 clusters by (initiator, target) entity pair, sorts by time,
and classifies edges between consecutive events using tone + event_type
transitions. Produces directed acyclic story graphs representing the
evolution of each geopolitical interaction.

Edge Types
----------
  continuation   Same tone + same event_type (ongoing action)
  progression    Changed event_type (narrative progression)
  escalation     Negative escalation (conflict intensifies)
  de-escalation  Tone shift negative→neutral or neutral→positive (cooling down)
  resolution     Tone shift to positive (agreement/peace)
  response       Trigger keyword indicates direct response
  gap            Time gap > max_gap_days between related events

v2.1 improvements:
  - Entity-pair boundary detection: different (initiator, target) = new story
  - Unified edge types (no more 'continued' duplicates)
  - Fixed indentation bug (lines were nested under for-pair loop)
  - Differentiated max_gap_days per event type for better coverage

Usage
-----
  from core_pipeline.event_evolution_chain import build_storylines
  build_storylines()

  python -m core_pipeline.event_evolution_chain
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from datetime import time as dt_time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from config.db_runtime_config import require_database_password

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - exercised only in minimal test envs
    psycopg2 = None

logger = logging.getLogger("event_evolution_chain")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── DB connection ────────────────────────────────────────────
DB_CONFIG = {
    "host": os.getenv("PG_HOST", "192.168.207.171"),
    "port": int(os.getenv("PG_PORT", "54333")),
    "dbname": os.getenv("PG_DB", "globemind_news"),
    "user": os.getenv("PG_USER", "postgres"),
}


def get_conn():
    if psycopg2 is None:
        raise ImportError("psycopg2 is required to build L2 storylines")
    return psycopg2.connect(
        **DB_CONFIG,
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),
    )


# ── Canonical entity resolution ──────────────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from core_pipeline.event_coref_cluster import _canonical_entity
except ImportError:

    def _canonical_entity(value):
        return value


def _canonical_pair(init: Optional[str], target: Optional[str]) -> Tuple[str, str]:
    """Normalize entity pair to canonical form."""
    i = (_canonical_entity(init) or init or "?").lower().strip()
    t = (_canonical_entity(target) or target or "?").lower().strip()
    return (i, t)


# ── Trigger-verb severity scores (0-1) ──
# Maps trigger verbs to conflict intensity severity.
# Used as a continuous feature for edge classification.
TRIGGER_SEVERITY: Dict[str, float] = {
    "kill": 0.95, "destroy": 0.95, "massacre": 1.0, "slaughter": 1.0,
    "annihilate": 1.0, "bomb": 0.9, "bombard": 0.9, "shell": 0.85,
    "missile": 0.9, "strike": 0.8, "attack": 0.8, "raid": 0.8,
    "invade": 0.9, "invasion": 0.9, "assault": 0.85, "offensive": 0.85,
    "drone": 0.75, "airstrike": 0.85, "air strike": 0.85,
    "war": 0.9, "conflict": 0.7, "escalate": 0.8, "intensify": 0.7,
    "shoot": 0.8, "fire": 0.7, "artillery": 0.8,
    "sanction": 0.6, "embargo": 0.6, "tariff": 0.5,
    "threaten": 0.6, "threat": 0.5, "warn": 0.4,
    "condemn": 0.4, "denounce": 0.4, "accuse": 0.4, "blame": 0.3,
    "protest": 0.4, "repress": 0.6, "crackdown": 0.7,
    "arrest": 0.5, "detain": 0.5, "sentence": 0.4,
    "negotiate": 0.2, "talk": 0.15, "discuss": 0.15, "dialogue": 0.15,
    "meet": 0.1, "visit": 0.1, "summit": 0.15,
    "agree": 0.1, "accord": 0.1, "treaty": 0.1, "ceasefire": 0.05,
    "peace": 0.0, "reconcile": 0.05, "truce": 0.05,
    "aid": 0.2, "assist": 0.15, "support": 0.15,
    "appoint": 0.1, "elect": 0.1, "resign": 0.2,
    "announce": 0.2, "declare": 0.25, "state": 0.1,
    "report": 0.1, "say": 0.05, "claim": 0.1,
}

# ── Trigger-verb keyword sets ──
ESCALATION_KEYWORDS = {
    "sanction", "strike", "attack", "missile", "bomb", "threat", "threaten",
    "condemn", "denounce", "accuse", "retaliate", "retaliation",
    "war", "invade", "invasion", "shoot", "kill", "destroy",
    "制裁", "打击", "攻击", "导弹",
}

# ── Causal / narrative-arc keyword sets (v2.3) ──
# These detect explicit causal language and narrative progression patterns.
CAUSAL_KEYWORDS = {
    "lead to", "led to", "trigger", "triggered", "cause", "caused",
    "result in", "resulted in", "spark", "sparked", "fuel", "fueled",
    "prompt", "prompted", "provoke", "provoked", "precipitate",
    "引发", "导致", "引起", "触发",
}
# Sub-categories for narrative arc matching
ESCALATE_VERBS = {
    "threaten", "threat", "warn", "escalate", "intensify", "heighten",
    "动员", "升级", "威胁",
}
ATTACK_VERBS = {
    "strike", "attack", "bomb", "missile", "invade", "assault", "raid",
    "offensive", "bombard", "shell", "launch strike", "launch attack",
    "打击", "攻击", "轰炸", "入侵",
}
CONDEMN_VERBS = {
    "condemn", "denounce", "accuse", "blame", "criticize",
    "谴责", "指责", "批评",
}
SANCTION_VERBS = {
    "sanction", "penalty", "tariff", "embargo", "boycott", "restrict",
    "制裁", "禁运", "限制",
}
NEGOTIATE_VERBS = {
    "negotiate", "talks", "dialogue", "mediation", "discuss",
    "会谈", "谈判", "对话",
}
AGREE_VERBS = {
    "agree", "accord", "treaty", "deal", "settle", "ceasefire",
    "truce", "armistice", "sign", "pact",
    "协议", "和", "停火", "签署",
}
MEET_VERBS = {
    "meet", "visit", "summit", "conference", "hold talks",
    "会见", "访问", "峰会",
}
# Narrative arc patterns: (category_a, category_b) → edge_type,
# SECOND entry is the boost to apply (0.0-1.0)
NARRATIVE_ARCS: List[Tuple[Set[str], Set[str], str, float]] = [
    (ESCALATE_VERBS, ATTACK_VERBS, "escalation", 0.9),       # "threaten → attack": clear escalation
    (CONDEMN_VERBS, SANCTION_VERBS, "escalation", 0.8),      # "condemn → sanction": escalation
    (ESCALATE_VERBS, CONDEMN_VERBS, "escalation", 0.7),      # "threaten → condemn": escalation building
    (ATTACK_VERBS, ATTACK_VERBS, "continuation", 0.6),       # "attack → attack": ongoing fighting
    (ATTACK_VERBS, NEGOTIATE_VERBS, "de-escalation", 0.8),   # "attack → negotiate": cooling down
    (SANCTION_VERBS, NEGOTIATE_VERBS, "de-escalation", 0.7), # "sanction → talk": de-escalation
    (ESCALATE_VERBS, MEET_VERBS, "de-escalation", 0.7),      # "threaten → meet": de-escalation
    (ATTACK_VERBS, AGREE_VERBS, "resolution", 0.9),          # "attack → ceasefire": resolution
    (NEGOTIATE_VERBS, AGREE_VERBS, "resolution", 0.8),       # "negotiate → agree": resolution
    (MEET_VERBS, AGREE_VERBS, "resolution", 0.7),            # "meet → agree": resolution
    (CONDEMN_VERBS, AGREE_VERBS, "resolution", 0.6),         # "condemn → peace": resolution
]

RESPONSE_KEYWORDS = {
    "respond", "response", "react", "reaction", "reply",
    "回应", "反应", "答复",
}

DEESCALATION_KEYWORDS = {
    "negotiate", "negotiation", "talk", "discuss", "discussion",
    "meet", "meeting", "dialogue", "mediation",
    "会谈", "讨论", "对话",
}

RESOLUTION_KEYWORDS = {
    "ceasefire", "peace", "accord", "treaty", "sign", "agree", "agreement",
    "deal", "settle", "settlement", "reconcile", "reconciliation",
    "停火", "和平", "协议", "和解",
}

# ── Per-event-type max_gap_days for differentiated coverage ──
# Types with inherently slow evolution (sanctions, human rights, legal)
# get wider windows. Fast-moving types (military, protest) get tighter ones.
EVENT_TYPE_MAX_GAP: Dict[str, int] = {
    "trade_conflict": 45,              # Tariff wars, sanctions → unfold over months
    "diplomacy": 45,                   # Diplomatic engagements, negotiations
    "military": 21,                    # Military ops are fast-paced
    "policy_legal": 60,                # Legislation, court cases → slow
    "protest_repression": 21,          # Protests are event-driven, short bursts
    "terrorism_espionage": 45,         # Investigations span longer
    "human_rights_migration": 60,      # Rights campaigns evolve slowly
    "aid_disaster": 45,                # Relief efforts can span months
    "appointment_leadership": 60,      # Appointments → one-shot but waiting for reply
}
DEFAULT_EVENT_TYPE_GAP = 30

# ── Max cumulative span for a single sub-graph (in days) ─────
# Prevents single entity-pair chains from growing into 200+ node
# monsters spanning 5 months. When events cover > this span,
# they're split into separate story sub-graphs.
MAX_STORY_SPAN_DAYS = 30

# ── Per-type max cumulative span for a sub-graph ───────────────
# Different event types get different span caps before splitting
# into separate sub-graphs. Follows the same pattern as EVENT_TYPE_MAX_GAP:
#   - Fast-moving (military, protest): tighter span
#   - Slow-moving (policy, diplomacy): wider span
EVENT_TYPE_MAX_SPAN: Dict[str, int] = {
    "trade_conflict": 45,
    "diplomacy": 45,
    "military": 21,
    "policy_legal": 60,
    "protest_repression": 21,
    "terrorism_espionage": 45,
    "human_rights_migration": 60,
    "aid_disaster": 45,
    "appointment_leadership": 60,
}
DEFAULT_MAX_SPAN = 30

# ── Global powers that shouldn't be the sole bridge between conflicts ──
# When two stories share ONLY a global power entity (e.g., both involve "US"),
# they're different conflicts and shouldn't be merged.  Non-power entities
# (Iran, Ukraine, Venezuela, Israel...) ARE valid bridges between stories.
GLOBAL_POWERS: Set[str] = {
    "united states", "china", "russia", "eu", "un", "nato", "uk",
}

MAX_CHAPTER_NODES = 12
MAX_SAME_DAY_EVENTS_PER_CHAPTER = 4
MAX_REFERENCE_NEIGHBORS = 3
MAX_ANALYSIS_RELATIONS_PER_STORY = 12
MAX_AMBIGUOUS_MICRO_CHAPTER_NODES = 4
AMBIGUOUS_MICRO_CHAPTER_MAX_SPAN_DAYS = 2
AMBIGUOUS_MICRO_CHAPTER_MAX_PURITY = 0.5
CHAPTER_STRONG_LINK_WINDOW_DAYS = 14
CHAPTER_CONTEXT_WINDOW_DAYS = 5
CHAPTER_CONTEXT_MIN_SIM = 0.6
PAIR_SEQUENCE_MAX_GAP_DAYS = 14
PAIR_SEQUENCE_RELAXED_MAX_GAP_DAYS = 21
PAIR_SEQUENCE_RELAXED_MIN_VERB_SIM = 0.45
PAIR_FAMILY_REFERENCE_MAX_GAP_DAYS = 10
MAX_MACRO_CHAPTERS = 12
MAX_MACRO_SPAN_DAYS = 35
MAX_MACRO_GAP_DAYS = 7

TYPE_FAMILY: Dict[str, str] = {
    "military": "conflict",
    "terrorism_espionage": "conflict",
    "protest_repression": "conflict",
    "trade_conflict": "economic",
    "policy_legal": "institutional",
    "diplomacy": "negotiation",
    "appointment_leadership": "negotiation",
    "human_rights_migration": "humanitarian",
    "aid_disaster": "humanitarian",
}


def _max_span_for(event_type: str) -> int:
    return EVENT_TYPE_MAX_SPAN.get(event_type, DEFAULT_MAX_SPAN)


def _max_gap_for(event_type: str) -> int:
    return EVENT_TYPE_MAX_GAP.get(event_type, DEFAULT_EVENT_TYPE_GAP)


def _min_gap_for(a_type: str, b_type: str) -> int:
    """Use the more restrictive gap of the two event types."""
    return min(_max_gap_for(a_type), _max_gap_for(b_type))


def _stable_story_int(value: str) -> int:
    return int(hashlib.md5(value.encode("utf-8")).hexdigest(), 16) & 0x7FFFFFFF


def _coerce_date(value: object) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _coerce_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return datetime.combine(value, dt_time(12, 0), tzinfo=timezone.utc)
    return None


def _median_datetime(values: Iterable[datetime]) -> Optional[datetime]:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    ts = (ordered[mid - 1].timestamp() + ordered[mid].timestamp()) / 2.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _midpoint_datetime(start: Optional[date], end: Optional[date]) -> Optional[datetime]:
    start_dt = _coerce_datetime(start)
    end_dt = _coerce_datetime(end)
    if start_dt and end_dt:
        ts = (start_dt.timestamp() + end_dt.timestamp()) / 2.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return start_dt or end_dt


def _cosine_similarity(vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = sum(x * x for x in vec_a) ** 0.5
    norm_b = sum(y * y for y in vec_b) ** 0.5
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _mean_embedding(vectors: Iterable[Optional[List[float]]]) -> Optional[List[float]]:
    valid = [vec for vec in vectors if vec]
    if not valid:
        return None
    return [sum(dim) / len(valid) for dim in zip(*valid)]


def _normalize_trigger_phrase(verb: str) -> str:
    tokens = [tok.rstrip(",._;!?").lower() for tok in (verb or "").split()]
    tokens = [tok for tok in tokens if tok]
    return " ".join(tokens)


def _trigger_severity(verb: str) -> float:
    if not verb:
        return 0.0
    best = 0.0
    for token in _normalize_trigger_phrase(verb).split():
        if token in TRIGGER_SEVERITY:
            best = max(best, TRIGGER_SEVERITY[token])
        for key, val in TRIGGER_SEVERITY.items():
            if token.startswith(key) or key.startswith(token):
                best = max(best, val)
    return best


def _pick_dominant_trigger(verbs: Iterable[str]) -> str:
    counts: Counter[str] = Counter()
    severities: Dict[str, float] = {}
    for raw in verbs:
        norm = _normalize_trigger_phrase(raw)
        if not norm:
            continue
        counts[norm] += 1
        severities[norm] = max(severities.get(norm, 0.0), _trigger_severity(norm))
    if not counts:
        return ""
    return max(counts, key=lambda verb: (counts[verb], severities.get(verb, 0.0), len(verb)))


def _event_time_key(event: Dict[str, Any]) -> datetime:
    display_time = event.get("display_time")
    if isinstance(display_time, datetime):
        return display_time
    if isinstance(display_time, date):
        return datetime.combine(display_time, dt_time(12, 0), tzinfo=timezone.utc)
    midpoint = _midpoint_datetime(
        _coerce_date(event.get("start_date")),
        _coerce_date(event.get("end_date")),
    )
    if midpoint is not None:
        return midpoint
    return datetime(1900, 1, 1, tzinfo=timezone.utc)


def _chapter_span_cap_for(event_type: str) -> int:
    return max(7, min(_max_span_for(event_type), 24))


def _current_majority_type(events: List[Dict[str, Any]]) -> str:
    counts = Counter((event.get("event_type") or "unknown") for event in events)
    return counts.most_common(1)[0][0] if counts else "unknown"


def _same_day_count(events: List[Dict[str, Any]], target: datetime) -> int:
    target_day = target.date()
    return sum(1 for event in events if _event_time_key(event).date() == target_day)


def _type_family(event_type: str) -> str:
    return TYPE_FAMILY.get((event_type or "").lower(), "other")


def _chapter_type_counts(events: List[Dict[str, Any]]) -> Counter[str]:
    return Counter((event.get("event_type") or "unknown") for event in events)


def _dominant_type_share(events: List[Dict[str, Any]]) -> float:
    if not events:
        return 0.0
    counts = _chapter_type_counts(events)
    return max(counts.values()) / max(len(events), 1)


def _chapter_embedding(events: List[Dict[str, Any]], cluster_embedding: Dict[str, List[float]]) -> Optional[List[float]]:
    return _mean_embedding(cluster_embedding.get(event["cluster_id"]) for event in events)


def _should_split_on_type_intrusion(
    current: List[Dict[str, Any]],
    candidate: Dict[str, Any],
    cluster_embedding: Dict[str, List[float]],
) -> bool:
    if len(current) < 3:
        return False

    current_counts = _chapter_type_counts(current)
    current_types = set(current_counts)
    candidate_type = candidate.get("event_type") or "unknown"
    majority_type, majority_count = current_counts.most_common(1)[0]
    majority_share = majority_count / max(len(current), 1)
    prev = current[-1]
    prev_type = prev.get("event_type") or "unknown"
    candidate_time = _event_time_key(candidate)
    prev_time = _event_time_key(prev)
    gap_days = max(0, (candidate_time.date() - prev_time.date()).days)

    if candidate_type not in current_types and len(current_types) >= 2 and gap_days <= 5:
        return True

    if candidate_type != majority_type and majority_share >= 0.6:
        chapter_centroid = _chapter_embedding(current, cluster_embedding)
        cand_emb = cluster_embedding.get(candidate["cluster_id"])
        cand_cos = _cosine_similarity(chapter_centroid, cand_emb)
        trigger_overlap = _verb_similarity(
            prev.get("trigger_verb", ""),
            candidate.get("trigger_verb", ""),
        )
        family_changed = _type_family(prev_type) != _type_family(candidate_type)
        if gap_days <= 4 and cand_cos < 0.65 and trigger_overlap < 0.35:
            return True
        if family_changed and gap_days <= 3 and cand_cos < 0.72:
            return True

    return False


def _best_chapter_split_index(
    chapter: List[Dict[str, Any]],
    cluster_embedding: Dict[str, List[float]],
) -> Tuple[Optional[int], float]:
    if len(chapter) < 4:
        return None, 0.0

    dominant_share = _dominant_type_share(chapter)
    type_count = len(_chapter_type_counts(chapter))
    best_idx: Optional[int] = None
    best_score = 0.0

    for idx in range(2, len(chapter) - 1):
        left = chapter[:idx]
        right = chapter[idx:]
        left_share = _dominant_type_share(left)
        right_share = _dominant_type_share(right)
        purity_gain = ((left_share + right_share) / 2.0) - dominant_share
        if purity_gain <= 0:
            continue

        prev = chapter[idx - 1]
        curr = chapter[idx]
        type_change = 1.0 if prev.get("event_type") != curr.get("event_type") else 0.0
        family_change = 1.0 if _type_family(prev.get("event_type", "")) != _type_family(curr.get("event_type", "")) else 0.0
        emb_shift = 1.0 - max(0.0, _cosine_similarity(
            cluster_embedding.get(prev["cluster_id"]),
            cluster_embedding.get(curr["cluster_id"]),
        ))
        trigger_shift = 1.0 - _verb_similarity(
            prev.get("trigger_verb", ""),
            curr.get("trigger_verb", ""),
        )
        gap_days = max(0, (_event_time_key(curr).date() - _event_time_key(prev).date()).days)
        score = (
            0.45 * purity_gain
            + 0.18 * type_change
            + 0.15 * family_change
            + 0.12 * emb_shift
            + 0.10 * trigger_shift
            + 0.05 * min(gap_days / 3.0, 1.0)
        )
        if type_count >= 3:
            score += 0.05
        if score > best_score:
            best_score = score
            best_idx = idx

    threshold = 0.18 if type_count >= 3 else 0.24
    if best_score < threshold:
        return None, best_score
    return best_idx, best_score


def _split_impure_chapter(
    chapter: List[Dict[str, Any]],
    cluster_embedding: Dict[str, List[float]],
) -> List[List[Dict[str, Any]]]:
    if len(chapter) < 4:
        return [chapter]
    type_counts = _chapter_type_counts(chapter)
    dominant_share = _dominant_type_share(chapter)
    if dominant_share >= 0.8 and len(type_counts) <= 2:
        return [chapter]

    split_idx, _ = _best_chapter_split_index(chapter, cluster_embedding)
    if split_idx is None:
        return [chapter]

    left = chapter[:split_idx]
    right = chapter[split_idx:]
    pieces: List[List[Dict[str, Any]]] = []
    for part in (left, right):
        if len(part) >= 2:
            pieces.extend(_split_impure_chapter(part, cluster_embedding))
        else:
            pieces.append(part)
    return pieces


def _salvage_same_type_runs(
    chapter: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    runs: List[List[Dict[str, Any]]] = []
    current_run = [chapter[0]]
    current_type = chapter[0].get("event_type") or "unknown"
    for event in chapter[1:]:
        event_type = event.get("event_type") or "unknown"
        if event_type == current_type:
            current_run.append(event)
            continue
        if len(current_run) >= 2:
            runs.append(current_run)
        current_run = [event]
        current_type = event_type
    if len(current_run) >= 2:
        runs.append(current_run)
    return runs


def _filter_ambiguous_micro_chapters(
    chapters: List[List[Dict[str, Any]]],
) -> List[List[Dict[str, Any]]]:
    filtered: List[List[Dict[str, Any]]] = []
    for chapter in chapters:
        if len(chapter) < 2:
            continue
        if len(chapter) > MAX_AMBIGUOUS_MICRO_CHAPTER_NODES:
            filtered.append(chapter)
            continue

        dominant_share = _dominant_type_share(chapter)
        if dominant_share > AMBIGUOUS_MICRO_CHAPTER_MAX_PURITY:
            filtered.append(chapter)
            continue

        type_counts = _chapter_type_counts(chapter)
        if any(event.get("time_precision") == "member_median" for event in chapter):
            filtered.append(chapter)
            continue

        if len(chapter) <= 3 and len(type_counts) == len(chapter):
            filtered.extend(_salvage_same_type_runs(chapter))
            continue

        chapter_dates = [_event_time_key(event).date() for event in chapter]
        span_days = (max(chapter_dates) - min(chapter_dates)).days if chapter_dates else 999
        if span_days > AMBIGUOUS_MICRO_CHAPTER_MAX_SPAN_DAYS:
            filtered.append(chapter)
            continue

        same_type_runs = _salvage_same_type_runs(chapter)
        filtered.extend(same_type_runs)
    return filtered


def _split_large_macro_group(
    story_ids: List[int],
    chapter_by_id: Dict[int, Dict[str, Any]],
) -> List[List[int]]:
    ordered_ids = sorted(story_ids, key=lambda story_id: (chapter_by_id[story_id]["start_time"], story_id))
    if not ordered_ids:
        return []

    groups: List[List[int]] = []
    current = [ordered_ids[0]]
    current_start = chapter_by_id[ordered_ids[0]]["start_time"]
    current_end = chapter_by_id[ordered_ids[0]]["end_time"]
    for story_id in ordered_ids[1:]:
        chapter = chapter_by_id[story_id]
        previous = chapter_by_id[current[-1]]
        gap_days = _date_range_gap_days(
            current_start,
            current_end,
            chapter["start_time"],
            chapter["end_time"],
        )
        span_days = max(0, (chapter["end_time"].date() - current_start.date()).days)
        shared_entities = previous["entities"] & chapter["entities"]
        sim = _cosine_similarity(previous.get("embedding"), chapter.get("embedding"))
        same_pair_family = previous["entity_pair_set"] == chapter["entity_pair_set"] and bool(previous["entity_pair_set"])
        should_break = (
            len(current) >= MAX_MACRO_CHAPTERS
            or span_days > MAX_MACRO_SPAN_DAYS
            or gap_days > MAX_MACRO_GAP_DAYS
            or (not shared_entities and sim < 0.75)
            or (
                same_pair_family
                and previous.get("dominant_type") != chapter.get("dominant_type")
                and sim < 0.72
            )
        )
        if should_break:
            groups.append(current)
            current = [story_id]
            current_start = chapter["start_time"]
        else:
            current.append(story_id)
        current_end = chapter["end_time"]
    if current:
        groups.append(current)
    return groups


def _should_start_new_chapter(
    current: List[Dict[str, Any]],
    candidate: Dict[str, Any],
    cluster_embedding: Dict[str, List[float]],
    max_gap_days: int,
) -> bool:
    if not current:
        return False

    previous = current[-1]
    if _detect_story_boundary(previous, candidate, max_gap_days):
        return True

    current_start = _event_time_key(current[0])
    candidate_time = _event_time_key(candidate)
    span_days = max(0, (candidate_time.date() - current_start.date()).days)
    majority_type = _current_majority_type(current)
    if span_days > _chapter_span_cap_for(majority_type):
        return True

    if len(current) >= MAX_CHAPTER_NODES:
        return True

    if _same_day_count(current, candidate_time) >= MAX_SAME_DAY_EVENTS_PER_CHAPTER:
        return True

    if _should_split_on_type_intrusion(current, candidate, cluster_embedding):
        return True

    recent_gap_days = max(0, (candidate_time.date() - _event_time_key(previous).date()).days)
    if recent_gap_days <= 4:
        prev_emb = cluster_embedding.get(previous["cluster_id"])
        cand_emb = cluster_embedding.get(candidate["cluster_id"])
        cos = _cosine_similarity(prev_emb, cand_emb)
        chapter_cos = _cosine_similarity(
            _chapter_embedding(current, cluster_embedding),
            cand_emb,
        )
        trigger_overlap = _verb_similarity(
            previous.get("trigger_verb", ""),
            candidate.get("trigger_verb", ""),
        )
        if len(current) >= 3 and previous.get("event_type") != candidate.get("event_type"):
            if cos < 0.55 and trigger_overlap < 0.35:
                return True
        majority_type = _current_majority_type(current)
        if len(current) >= 4 and candidate.get("event_type") != majority_type:
            if chapter_cos < 0.62 and trigger_overlap < 0.4:
                return True

    return False


def _chapters_are_mergeable(
    left: List[Dict[str, Any]],
    right: List[Dict[str, Any]],
    cluster_embedding: Dict[str, List[float]],
) -> bool:
    if not left or not right:
        return False
    if _current_majority_type(left) != _current_majority_type(right):
        return False
    left_emb = _chapter_embedding(left, cluster_embedding)
    right_emb = _chapter_embedding(right, cluster_embedding)
    emb_sim = _cosine_similarity(left_emb, right_emb)
    trigger_sim = _verb_similarity(
        left[-1].get("trigger_verb", ""),
        right[0].get("trigger_verb", ""),
    )
    if not (emb_sim >= 0.72 or trigger_sim >= 0.5):
        return False
    merged = left + right
    if (
        len(merged) <= MAX_AMBIGUOUS_MICRO_CHAPTER_NODES
        and _dominant_type_share(merged) < 0.8
    ):
        return False
    return True


def _repair_small_chapters(
    chapters: List[List[Dict[str, Any]]],
    cluster_embedding: Dict[str, List[float]],
) -> List[List[Dict[str, Any]]]:
    repaired: List[List[Dict[str, Any]]] = []
    for idx, chapter in enumerate(chapters):
        if len(chapter) >= 2:
            repaired.append(chapter)
            continue
        if (
            repaired
            and len(repaired[-1]) < MAX_CHAPTER_NODES
            and _chapters_are_mergeable(repaired[-1], chapter, cluster_embedding)
        ):
            repaired[-1].extend(chapter)
        elif (
            idx + 1 < len(chapters)
            and _chapters_are_mergeable(chapter, chapters[idx + 1], cluster_embedding)
        ):
            chapters[idx + 1] = chapter + chapters[idx + 1]
        else:
            repaired.append(chapter)
    return [chapter for chapter in repaired if len(chapter) >= 2]


def _split_pair_into_chapters(
    events: List[Dict[str, Any]],
    cluster_embedding: Dict[str, List[float]],
    max_gap_days: int,
) -> List[List[Dict[str, Any]]]:
    if len(events) < 2:
        return []

    chapters: List[List[Dict[str, Any]]] = []
    current = [events[0]]
    for event in events[1:]:
        if _should_start_new_chapter(current, event, cluster_embedding, max_gap_days):
            chapters.append(current)
            current = [event]
        else:
            current.append(event)
    chapters.append(current)
    repaired = _repair_small_chapters(chapters, cluster_embedding)
    refined: List[List[Dict[str, Any]]] = []
    for chapter in repaired:
        refined.extend(_split_impure_chapter(chapter, cluster_embedding))
    stabilized = _repair_small_chapters(refined, cluster_embedding)
    return _filter_ambiguous_micro_chapters(stabilized)


def _date_range_gap_days(
    start_a: Optional[datetime],
    end_a: Optional[datetime],
    start_b: Optional[datetime],
    end_b: Optional[datetime],
) -> int:
    if not start_a or not end_a or not start_b or not end_b:
        return 999
    if start_b <= end_a and start_a <= end_b:
        return 0
    if end_a < start_b:
        return (start_b.date() - end_a.date()).days
    return (start_a.date() - end_b.date()).days


def _ensure_story_relations_table(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS story_relations (
            id SERIAL PRIMARY KEY,
            story_id INTEGER NOT NULL,
            neighbor_story_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            layer TEXT NOT NULL DEFAULT 'context',
            score REAL NOT NULL DEFAULT 0.0,
            reason TEXT,
            rank INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (story_id, neighbor_story_id, relation_type, layer)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_story_relations_story
        ON story_relations (story_id, layer, rank)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_story_relations_neighbor
        ON story_relations (neighbor_story_id)
        """
    )


def _build_story_relations(
    chapter_by_id: Dict[int, Dict[str, Any]],
    reference_candidates: Dict[int, List[Dict[str, Any]]],
    macro_groups: Dict[int, List[int]],
    pair_story_groups: Dict[Tuple[str, str], List[int]],
) -> List[Dict[str, Any]]:
    relation_map: Dict[Tuple[int, int, str, str], Dict[str, Any]] = {}

    def _upsert_relation(
        story_id: int,
        neighbor_story_id: int,
        relation_type: str,
        layer: str,
        score: float,
        reason: str,
    ) -> None:
        if story_id == neighbor_story_id:
            return
        key = (story_id, neighbor_story_id, relation_type, layer)
        row = {
            "story_id": story_id,
            "neighbor_story_id": neighbor_story_id,
            "relation_type": relation_type,
            "layer": layer,
            "score": round(score, 4),
            "reason": reason,
        }
        existing = relation_map.get(key)
        if existing is None or row["score"] > existing["score"]:
            relation_map[key] = row

    for story_id, refs in reference_candidates.items():
        ordered_refs = sorted(
            refs,
            key=lambda item: (-item["score"], item["neighbor_story_id"], item["reason"]),
        )
        for item in ordered_refs:
            reason = item["reason"]
            if reason == "weak_context":
                continue
            relation_type = "pair_family" if reason.startswith("pair_family") else "context"
            _upsert_relation(
                story_id,
                item["neighbor_story_id"],
                relation_type,
                "context",
                item["score"],
                reason,
            )

    for member_story_ids in macro_groups.values():
        ordered_ids = sorted(
            member_story_ids,
            key=lambda story_id: (chapter_by_id[story_id]["start_time"], story_id),
        )
        for left_story_id, right_story_id in zip(ordered_ids, ordered_ids[1:]):
            same_pair = chapter_by_id[left_story_id]["pair"] == chapter_by_id[right_story_id]["pair"]
            score = 0.97 if same_pair else 0.9
            for source, target in ((left_story_id, right_story_id), (right_story_id, left_story_id)):
                _upsert_relation(
                    source,
                    target,
                    "macro_sequence",
                    "backbone",
                    score,
                    "adjacent_macro_chapter",
                )

    for pair_story_ids in pair_story_groups.values():
        ordered_ids = sorted(
            pair_story_ids,
            key=lambda story_id: (chapter_by_id[story_id]["start_time"], story_id),
        )
        for left_story_id, right_story_id in zip(ordered_ids, ordered_ids[1:]):
            left_story = chapter_by_id[left_story_id]
            right_story = chapter_by_id[right_story_id]
            gap_days = _date_range_gap_days(
                left_story["start_time"],
                left_story.get("end_time", left_story["start_time"]),
                right_story["start_time"],
                right_story.get("end_time", right_story["start_time"]),
            )
            same_type = left_story.get("dominant_type") == right_story.get("dominant_type")
            trigger_similarity = _verb_similarity(
                left_story.get("dominant_trigger", ""),
                right_story.get("dominant_trigger", ""),
            )
            allow_pair_sequence = gap_days <= PAIR_SEQUENCE_MAX_GAP_DAYS or (
                same_type
                and gap_days <= PAIR_SEQUENCE_RELAXED_MAX_GAP_DAYS
                and trigger_similarity >= PAIR_SEQUENCE_RELAXED_MIN_VERB_SIM
            )
            if not allow_pair_sequence:
                continue

            score = 0.95 if gap_days <= PAIR_SEQUENCE_MAX_GAP_DAYS else 0.88
            for source, target in ((left_story_id, right_story_id), (right_story_id, left_story_id)):
                _upsert_relation(
                    source,
                    target,
                    "pair_sequence",
                    "backbone",
                    score,
                    "adjacent_pair_chapter",
                )

    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in relation_map.values():
        grouped[row["story_id"]].append(row)

    ranked_rows: List[Dict[str, Any]] = []
    for story_id, rows in grouped.items():
        ordered_rows = sorted(
            rows,
            key=lambda row: (
                0 if row["layer"] == "backbone" else 1,
                -row["score"],
                row["neighbor_story_id"],
                row["relation_type"],
            ),
        )[:MAX_ANALYSIS_RELATIONS_PER_STORY]
        for rank, row in enumerate(ordered_rows, start=1):
            ranked = dict(row)
            ranked["rank"] = rank
            ranked_rows.append(ranked)
    return ranked_rows


# ── Story boundary detection ─────────────────────────────────
def _detect_story_boundary(
    a: Dict, b: Dict,
    max_gap_days: int = 30,
) -> bool:
    """Check if two consecutive events have a story boundary between them.

    A boundary starts a new sub-graph. Rules (any triggers a boundary):
    1. Different (initiator, target) entity pair → new story (overarching rule)
    2. Time gap > 14 days between consecutive events
    3. Event_type conflict→cooperation transition WITH gap > 7 days
    4. Opposite tone polarity shift WITH gap > 3 days
    5. Entity pair changes regardless of gap (e.g., US↔Iran → Russia↔Ukraine)
    """
    # Rule 1: Entity pair changed → definitely a new story
    # (Unless it's a simple direction reversal: US→Iran vs Iran→US)
    a_pair = (_canonical_entity(a.get("initiator")), _canonical_entity(a.get("target")))
    b_pair = (_canonical_entity(b.get("initiator")), _canonical_entity(b.get("target")))
    a_set = set(a_pair)
    b_set = set(b_pair)
    if a_set != b_set and not (a_set == b_set and a_pair != b_pair):
        # Completely different entity pair → story boundary
        if not (a_pair[0] and a_pair[1] and b_pair[0] and b_pair[1]):
            pass  # Missing entity data → rely on other rules
        elif a_pair[0] != b_pair[0] and a_pair[1] != b_pair[1]:
            return True  # Neither initiator nor target overlaps
        # If one side overlaps (e.g., US→Iran and US→Venezuela share initiator),
        # check gap: if > 7 days with different target → split
        a_date = a.get("start_date") or a.get("end_date")
        b_date = b.get("start_date") or b.get("end_date")
        a_date_obj = a_date.date() if isinstance(a_date, datetime) else a_date
        b_date_obj = b_date.date() if isinstance(b_date, datetime) else b_date
        gap = 0
        if isinstance(a_date_obj, date) and isinstance(b_date_obj, date):
            gap = (b_date_obj - a_date_obj).days
        if a_pair != b_pair and gap > 7:
            return True

    a_date = a.get("start_date") or a.get("end_date")
    b_date = b.get("start_date") or b.get("end_date")
    a_date_obj = a_date.date() if isinstance(a_date, datetime) else a_date
    b_date_obj = b_date.date() if isinstance(b_date, datetime) else b_date
    gap = 0
    if isinstance(a_date_obj, date) and isinstance(b_date_obj, date):
        gap = (b_date_obj - a_date_obj).days

    a_type = (a.get("event_type") or "").lower()
    b_type = (b.get("event_type") or "").lower()
    if a_set == b_set and a_pair != b_pair:
        if gap > 2:
            return True
        if _type_family(a_type) != "negotiation" or _type_family(b_type) != "negotiation":
            return True
        if _verb_similarity(a.get("trigger_verb", ""), b.get("trigger_verb", "")) < 0.25 and gap > 0:
            return True

    # Rule 2: Time gap > type-specific threshold
    # Uses half of the per-type max_gap (floor 10, ceiling 21) so fast-moving
    # types like military break sooner than slow-moving types like diplomacy.
    story_break_gap = max(_min_gap_for(a_type, b_type) // 2, 10)
    story_break_gap = min(story_break_gap, 21)
    if gap > story_break_gap:
        return True

    # Rule 3: Event_type conflict↔cooperation transition WITH gap > 3 days
    conflict_types = {"military", "trade_conflict", "protest_repression", "terrorism_espionage"}
    cooperation_types = {"diplomacy", "aid_disaster", "appointment_leadership", "human_rights_migration"}
    a_type = (a.get("event_type") or "").lower()
    b_type = (b.get("event_type") or "").lower()
    if a_type in conflict_types and b_type in cooperation_types and gap > 3:
        return True
    if a_type in cooperation_types and b_type in conflict_types and gap > 3:
        return True

    # Rule 4: Opposite polarity shift WITH gap > 3 days
    a_tone = a.get("tone", "neutral")
    b_tone = b.get("tone", "neutral")
    if ((a_tone == "positive" and b_tone == "negative")
            or (a_tone == "negative" and b_tone == "positive")) and gap > 3:
        return True

    return False


# ── Narrative arc matching ──────────────────────────────
def _narrative_arc_match(
    trigger_a: str, trigger_b: str,
) -> Tuple[Optional[str], float]:
    """Check if two trigger verbs form a known narrative arc pattern.

    Returns (edge_type, confidence) if matched, (None, 0.0) otherwise.
    This runs BEFORE tone-based classification and can override it.
    """
    words_a = set((trigger_a or "").lower().split())
    words_b = set((trigger_b or "").lower().split())
    if not words_a or not words_b:
        return None, 0.0

    def _match_cat(words: Set[str], cat: Set[str]) -> bool:
        """Check if any word in `words` matches any keyword in `cat`.
        Uses prefix matching so 'warned' matches 'warn', 'attacked' matches 'attack'."""
        for w in words:
            w_clean = w.rstrip(',._;!?').lower()
            for kw in cat:
                if w_clean == kw or w_clean.startswith(kw) or kw.startswith(w_clean):
                    return True
        return False

    best_type, best_conf = None, 0.0
    for cat_a, cat_b, etype, conf in NARRATIVE_ARCS:
        # Check forward: trigger_a ∈ cat_a AND trigger_b ∈ cat_b
        if _match_cat(words_a, cat_a) and _match_cat(words_b, cat_b):
            if conf > best_conf:
                best_type, best_conf = etype, conf
        # Check reverse (weaker signal)
        if _match_cat(words_a, cat_b) and _match_cat(words_b, cat_a):
            reverse_conf = conf * 0.6
            if reverse_conf > best_conf:
                best_type, best_conf = etype, reverse_conf

    return best_type, best_conf


def _has_causal_relation(trigger_a: str, trigger_b: str) -> Tuple[bool, float]:
    """Check if trigger verbs indicate a causal relation between events."""
    causal_words = CAUSAL_KEYWORDS
    words = set((trigger_a or "").lower().split())
    words.update((trigger_b or "").lower().split())
    matches = words & causal_words
    if matches:
        return True, min(1.0, len(matches) * 0.5)
    return False, 0.0


# ── Edge classification ──────────────────────────────────────
def _verb_similarity(verb_a: str, verb_b: str) -> float:
    """Compute semantic similarity between two trigger verb phrases.
    Uses word overlap Jaccard to estimate how similar the actions are.
    """
    words_a = set((verb_a or "").lower().split())
    words_b = set((verb_b or "").lower().split())
    if not words_a or not words_b:
        return 0.0
    # Clean punctuation
    words_a = {w.rstrip(',._;!?') for w in words_a}
    words_b = {w.rstrip(',._;!?') for w in words_b}
    intersection = words_a & words_b
    union = words_a | words_b
    if not union:
        return 0.0

    # Weighted: content words (verbs, nouns) matter more than function words
    content_words = {"the", "a", "an", "in", "on", "at", "to", "for", "of",
                     "with", "and", "or", "by", "from", "its", "his", "her"}
    content_overlap = [w for w in intersection if w not in content_words]
    content_union = [w for w in union if w not in content_words]

    if content_union:
        return len(content_overlap) / max(len(content_union), 1)
    return len(intersection) / max(len(union), 1)

def _entity_overlap(init_a: str, tgt_a: str, init_b: str, tgt_b: str) -> float:
    """Score how much entity overlap exists between two event clusters.
    Returns 0.0 (no overlap) to 1.0 (full overlap).
    """
    entities_a = {(init_a or "").lower().strip(), (tgt_a or "").lower().strip()}
    entities_b = {(init_b or "").lower().strip(), (tgt_b or "").lower().strip()}
    # Remove unknowns
    entities_a.discard("?")
    entities_a.discard("")
    entities_b.discard("?")
    entities_b.discard("")
    if not entities_a or not entities_b:
        return 0.0
    intersection = entities_a & entities_b
    if not intersection:
        return 0.0
    # Jaccard-like: shared entities / total unique entities
    union = entities_a | entities_b
    return len(intersection) / max(len(union), 1)

def _keyword_boost(trigger_a: str, trigger_b: str, edge_type: str) -> float:
    """Return a boost factor [0,1] if keywords support the edge type."""
    words = (trigger_a or "").lower().split() + (trigger_b or "").lower().split()
    if not words:
        return 0.0

    kw_map = {
        "escalation": ESCALATION_KEYWORDS,
        "de-escalation": DEESCALATION_KEYWORDS,
        "resolution": RESOLUTION_KEYWORDS,
        "response": RESPONSE_KEYWORDS,
    }
    kws = kw_map.get(edge_type)
    if not kws:
        return 0.0

    matches = sum(1 for w in words if w in kws)
    if matches >= 2:
        return 1.0
    if matches == 1:
        return 0.5
    return 0.0


def _tone_consistency(tone_a: str, tone_b: str) -> float:
    """Score tone consistency: 1.0 same, 0.5 one neutral, 0.0 opposite."""
    if tone_a == tone_b:
        return 1.0
    if tone_a == "neutral" or tone_b == "neutral":
        return 0.5
    return 0.0


# ── Story depth computation ────────────────────────────
def _compute_story_depth(story_id: int, edges: List[Dict]) -> int:
    """Compute the longest path length (number of edges) in this story graph.

    Uses topological sort (Kahn's algorithm) + DP to avoid recursion limits.
    """
    adj = defaultdict(list)
    all_nodes: Set[str] = set()
    for e in edges:
        sid = _stable_story_int(e["story_id"])
        if sid != story_id:
            continue
        src, tgt = e["source_cluster"], e["target_cluster"]
        adj[src].append(tgt)
        all_nodes.add(src)
        all_nodes.add(tgt)

    if not all_nodes:
        return 0

    # Compute in-degree for topological sort
    in_deg = {n: 0 for n in all_nodes}
    for src in adj:
        for tgt in adj[src]:
            in_deg[tgt] = in_deg.get(tgt, 0) + 1

    # Kahn's algorithm: topological order
    queue = [n for n in all_nodes if in_deg.get(n, 0) == 0]
    topo_order = []
    while queue:
        n = queue.pop(0)
        topo_order.append(n)
        for tgt in adj[n]:
            in_deg[tgt] -= 1
            if in_deg[tgt] == 0:
                queue.append(tgt)

    # DP: process in REVERSE topological order (terminals first)
    dist = {n: 0 for n in all_nodes}
    for n in reversed(topo_order):
        for tgt in adj[n]:
            dist[n] = max(dist[n], 1 + dist[tgt])

    return max(dist.values()) if dist else 0


# ── Story title generation ─────────────────────────────
def _generate_story_title(story_edges: List[Dict], cluster_info: Dict[str, Dict]) -> str:
    """Generate a descriptive title for a story graph from cluster metadata."""
    from collections import Counter as _Counter
    initiators: _Counter = _Counter()
    targets: _Counter = _Counter()
    types: _Counter = _Counter()

    for e in story_edges:
        for cid_key in ("source_cluster", "target_cluster"):
            info = cluster_info.get(e.get(cid_key, ""), {})
            init_val = info.get("initiator", "") or ""
            tgt_val = info.get("target", "") or ""
            if init_val and init_val not in ("?", "?"):
                initiators[init_val] += 1
            if tgt_val and tgt_val not in ("?", "?"):
                targets[tgt_val] += 1
            if info.get("event_type"):
                types[info["event_type"]] += 1

    main_init = initiators.most_common(1)[0][0] if initiators else ""
    main_tgt = targets.most_common(1)[0][0] if targets else ""
    main_type = types.most_common(1)[0][0] if types else ""
    main_type_cn = {
        "military": "军事", "diplomacy": "外交", "trade_conflict": "贸易冲突",
        "protest_repression": "抗议镇压", "human_rights_migration": "人权移民",
        "policy_legal": "政策法律", "appointment_leadership": "人事任命",
        "aid_disaster": "援助救灾", "terrorism_espionage": "恐怖主义间谍",
    }.get(main_type, main_type)

    if main_init and main_tgt:
        return f"{main_init}对{main_tgt}的{main_type_cn}事件"
    if main_init:
        return f"{main_init}参与的{main_type_cn}事件"
    if main_type_cn:
        return f"{main_type_cn}系列事件"
    return ""


# ── Core algorithm ───────────────────────────────────────────
def build_storylines(
    min_article_count: int = 2,
    clear_existing: bool = True,
    max_gap_days: int = 30,
) -> Dict[str, Any]:
    """Build event evolution chains from L1 clusters.

    Parameters
    ----------
    min_article_count : int
        Minimum articles per cluster to include (2 = skip singletons).
    clear_existing : bool
        Clear existing story tables before writing.
    max_gap_days : int
        Maximum gap between consecutive events in the same storyline.

    Returns
    -------
    Summary dict with chain counts and edge statistics.
    """
    conn = get_conn()
    cur = conn.cursor()
    t0 = time.time()

    cur.execute("""
        SELECT cluster_id, article_count, event_type, initiator, target,
               start_date, end_date
        FROM event_coref_clusters
        WHERE article_count >= %s
        ORDER BY start_date NULLS LAST, cluster_id
    """, (min_article_count,))
    rows = cur.fetchall()
    logger.info("Loaded %d L1 clusters (min_articles=%d)", len(rows), min_article_count)

    if not rows:
        conn.close()
        return {"graphs": 0, "nodes": 0, "edges": 0, "macro_groups": 0}

    tone_by_cluster: Dict[str, str] = {}
    ev_type_by_cluster: Dict[str, str] = {}
    init_by_cluster: Dict[str, str] = {}
    target_by_cluster: Dict[str, str] = {}
    verb_by_cluster: Dict[str, str] = {}
    display_time_by_cluster: Dict[str, datetime] = {}
    time_precision_by_cluster: Dict[str, str] = {}
    cluster_dates: Dict[str, date] = {}

    checkpoint_path = os.path.join(os.path.dirname(__file__), "..", "data", "checkpoint_v13_all.jsonl")
    if not os.path.exists(checkpoint_path):
        checkpoint_path = os.path.join(os.path.dirname(__file__), "..", "data", "checkpoint_v12_geopolitical.jsonl")

    article_tone: Dict[int, str] = {}
    article_verb: Dict[int, str] = {}
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path) as f:
            for line in f:
                record = json.loads(line)
                event = record.get("event")
                if not event:
                    continue
                if event.get("tone"):
                    article_tone[record["article_id"]] = event["tone"]
                if event.get("trigger_verb"):
                    article_verb[record["article_id"]] = event["trigger_verb"]

    cluster_ids = [row[0] for row in rows]
    member_rows_by_cluster: Dict[str, List[Tuple[int, Optional[datetime]]]] = defaultdict(list)
    if cluster_ids:
        cur.execute(
            """
            SELECT cluster_id, news_id, published_at
            FROM event_coref_members
            WHERE cluster_id = ANY(%s)
            """,
            (cluster_ids,),
        )
        for cid, news_id, published_at in cur.fetchall():
            member_rows_by_cluster[cid].append((news_id, _coerce_datetime(published_at)))

    for cid, article_count, event_type, initiator, target, start_date, end_date in rows:
        ev_type_by_cluster[cid] = event_type or "unknown"
        init_by_cluster[cid] = initiator or ""
        target_by_cluster[cid] = target or ""
        member_rows = member_rows_by_cluster.get(cid, [])
        member_ids = [news_id for news_id, _ in member_rows]
        member_times = [ts for _, ts in member_rows if ts is not None]

        tones = [article_tone.get(mid, "neutral") for mid in member_ids if mid in article_tone]
        pos = sum(1 for tone in tones if tone == "positive")
        neg = sum(1 for tone in tones if tone == "negative")
        if pos > neg:
            tone_by_cluster[cid] = "positive"
        elif neg > pos:
            tone_by_cluster[cid] = "negative"
        else:
            tone_by_cluster[cid] = "neutral"

        verb_by_cluster[cid] = _pick_dominant_trigger(article_verb.get(mid, "") for mid in member_ids)

        median_time = _median_datetime(member_times)
        if median_time is not None:
            display_time_by_cluster[cid] = median_time
            time_precision_by_cluster[cid] = "member_median"
        else:
            display_time_by_cluster[cid] = _midpoint_datetime(
                _coerce_date(start_date),
                _coerce_date(end_date),
            ) or datetime(1900, 1, 1, tzinfo=timezone.utc)
            time_precision_by_cluster[cid] = "date_midpoint"

        cluster_date = _coerce_date(start_date) or _coerce_date(end_date)
        if cluster_date is not None:
            cluster_dates[cid] = cluster_date

    logger.info("Tone/trigger data loaded for %d clusters", len(cluster_ids))

    logger.info("Loading cluster embeddings and sentiment...")
    cluster_embedding: Dict[str, List[float]] = {}
    cluster_sentiment: Dict[str, float] = {}

    if cluster_ids:
        cur.execute("""
            SELECT m.cluster_id, e.embedding
            FROM news_embeddings e
            JOIN event_coref_members m ON e.news_id = m.news_id
            WHERE m.cluster_id = ANY(%s) AND e.embedding IS NOT NULL
        """, (cluster_ids,))
        emb_raw: Dict[str, List[List[float]]] = defaultdict(list)
        for cid, emb_json in cur.fetchall():
            emb = json.loads(emb_json) if isinstance(emb_json, str) else emb_json
            if emb and len(emb) == 1024:
                emb_raw[cid].append(emb)
        for cid, vectors in emb_raw.items():
            cluster_embedding[cid] = _mean_embedding(vectors) or []

        cur.execute("""
            SELECT m.cluster_id, a.deepseek_sentiment
            FROM news_ai_analysis a
            JOIN event_coref_members m ON a.news_id = m.news_id
            WHERE m.cluster_id = ANY(%s) AND a.deepseek_sentiment IS NOT NULL
        """, (cluster_ids,))
        sent_raw: Dict[str, List[float]] = defaultdict(list)
        for cid, sentiment in cur.fetchall():
            sent_raw[cid].append(sentiment)
        for cid, sentiments in sent_raw.items():
            cluster_sentiment[cid] = sum(sentiments) / len(sentiments)

    logger.info(
        "Cluster embeddings: %d, Cluster sentiment: %d",
        len(cluster_embedding),
        len(cluster_sentiment),
    )

    def _compute_edge_features(
        a_event: Dict[str, Any],
        b_event: Dict[str, Any],
        a_cid: str,
        b_cid: str,
        gap_days: int,
        max_gap: int,
    ) -> Dict[str, float]:
        sent_a = cluster_sentiment.get(a_cid, 0.0)
        sent_b = cluster_sentiment.get(b_cid, 0.0)
        sent_delta = max(-1.0, min(1.0, (sent_b - sent_a) / 10.0))
        same_type = 1.0 if a_event.get("event_type") == b_event.get("event_type") else 0.0
        ac_a = float(a_event.get("article_count", 1))
        ac_b = float(b_event.get("article_count", 1))
        growth_ratio = min(3.0, ac_b / max(ac_a, 1.0)) / 3.0
        pair_a = _canonical_pair(a_event.get("initiator", ""), a_event.get("target", ""))
        pair_b = _canonical_pair(b_event.get("initiator", ""), b_event.get("target", ""))
        return {
            "cosine_sim": _cosine_similarity(cluster_embedding.get(a_cid), cluster_embedding.get(b_cid)),
            "sent_delta": sent_delta,
            "sev_delta": _trigger_severity(b_event.get("trigger_verb", "")) - _trigger_severity(a_event.get("trigger_verb", "")),
            "same_type": same_type,
            "time_prox": max(0.0, 1.0 - gap_days / max(max_gap, 1)),
            "growth_ratio": growth_ratio,
            "same_pair": 1.0 if pair_a == pair_b else 0.0,
        }

    def _classify_edge(
        features: Dict[str, float],
        ev_type_a: str,
        tone_a: str,
        ev_type_b: str,
        tone_b: str,
        trigger_a: str = "",
        trigger_b: str = "",
    ) -> Tuple[str, float]:
        cos = features["cosine_sim"]
        sd = features["sent_delta"]
        svd = features["sev_delta"]
        st = features["same_type"]
        tp = features["time_prox"]
        gr = features["growth_ratio"]
        sp = features["same_pair"]

        esc_score = (
            0.20 * max(0, 1.0 - cos)
            + 0.20 * max(0, -sd)
            + 0.25 * max(0, svd)
            + 0.20 * (1.0 - st)
            + 0.15 * gr
        )
        des_score = (
            0.30 * max(0, sd)
            + 0.25 * max(0, -svd)
            + 0.20 * (1.0 - st)
            + 0.15 * tp
            + 0.10 * (1.0 - gr)
        )
        con_score = (
            0.25 * cos
            + 0.25 * st
            + 0.20 * tp
            + 0.10 * (1.0 - abs(sd))
            + 0.20 * sp
        )
        pro_score = 0.45 * (1.0 - st) + 0.20 * (1.0 - cos) + 0.15 * tp

        arc_type, arc_conf = _narrative_arc_match(trigger_a, trigger_b)
        if arc_type and arc_conf >= 0.85:
            return arc_type, arc_conf

        if con_score >= max(esc_score, des_score, pro_score) + 0.08:
            edge_type, confidence = "continuation", con_score
        elif pro_score >= max(esc_score, des_score) + 0.08 and con_score < 0.45:
            edge_type, confidence = "progression", pro_score * 0.7
        elif esc_score >= des_score:
            edge_type, confidence = "escalation", esc_score
        else:
            if tone_a == "negative" and tone_b == "positive":
                edge_type = "resolution"
            elif tone_a == "negative" and tone_b == "neutral":
                edge_type = "de-escalation"
            elif sd > 0.2:
                edge_type = "resolution"
            else:
                edge_type = "de-escalation"
            confidence = des_score

        if arc_type and arc_conf >= 0.55 and confidence < arc_conf:
            edge_type = arc_type
            confidence = arc_conf

        if sp and edge_type == "continuation":
            confidence = min(1.0, confidence + 0.10)

        return edge_type, max(0.1, min(1.0, confidence))

    cluster_info_for_title: Dict[str, Dict[str, Any]] = {}
    pair_to_events: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for cid, article_count, event_type, initiator, target, start_date, end_date in rows:
        cluster_info_for_title[cid] = {
            "initiator": initiator or "",
            "target": target or "",
            "event_type": event_type or "",
            "tone": tone_by_cluster.get(cid, "neutral"),
            "trigger_verb": verb_by_cluster.get(cid, ""),
            "display_time": display_time_by_cluster.get(cid),
        }
        pair = _canonical_pair(initiator, target)
        if pair == ("?", "?"):
            continue
        pair_to_events[pair].append({
            "cluster_id": cid,
            "article_count": article_count,
            "event_type": ev_type_by_cluster.get(cid, event_type or "unknown"),
            "tone": tone_by_cluster.get(cid, "neutral"),
            "trigger_verb": verb_by_cluster.get(cid, ""),
            "initiator": initiator or "",
            "target": target or "",
            "start_date": start_date,
            "end_date": end_date,
            "display_time": display_time_by_cluster.get(cid),
            "time_precision": time_precision_by_cluster.get(cid, "date_midpoint"),
        })

    chapter_records: List[Dict[str, Any]] = []
    all_edges: List[Dict[str, Any]] = []
    for pair, events in pair_to_events.items():
        if len(events) < 2:
            continue
        events.sort(key=_event_time_key)
        chapters = _split_pair_into_chapters(events, cluster_embedding, max_gap_days)
        for chapter_index, chapter in enumerate(chapters, start=1):
            if len(chapter) < 2:
                continue
            story_key = "chapter|%s|%s|%s" % (
                pair[0],
                pair[1],
                "|".join(event["cluster_id"] for event in chapter),
            )
            story_int = _stable_story_int(story_key)
            story_dates = [_event_time_key(event) for event in chapter]
            chapter_day_density = Counter(dt.date() for dt in story_dates)
            chapter_embedding = _mean_embedding(cluster_embedding.get(event["cluster_id"]) for event in chapter)
            entity_set = {pair[0], pair[1]} - {"?", ""}
            dominant_type = Counter(event.get("event_type", "unknown") for event in chapter).most_common(1)[0][0]
            chapter_records.append({
                "story_key": story_key,
                "story_id": story_int,
                "pair": pair,
                "entity_pair_set": frozenset(entity_set),
                "entities": entity_set,
                "cluster_ids": [event["cluster_id"] for event in chapter],
                "start_time": min(story_dates),
                "end_time": max(story_dates),
                "dominant_type": dominant_type,
                "dominant_trigger": _pick_dominant_trigger(event.get("trigger_verb", "") for event in chapter),
                "embedding": chapter_embedding,
                "node_count": len(chapter),
                "max_day_density": max(chapter_day_density.values()) if chapter_day_density else 0,
                "time_precision": "member_median"
                if any(time_precision_by_cluster.get(event["cluster_id"]) == "member_median" for event in chapter)
                else "date_midpoint",
            })

            for left, right in zip(chapter, chapter[1:]):
                gap_days = max(0, (_event_time_key(right).date() - _event_time_key(left).date()).days)
                effective_max_gap = _min_gap_for(
                    (left.get("event_type") or "").lower(),
                    (right.get("event_type") or "").lower(),
                )
                if gap_days > min(effective_max_gap, 14):
                    edge_type = "gap"
                    confidence = 0.0
                else:
                    features = _compute_edge_features(
                        left,
                        right,
                        left["cluster_id"],
                        right["cluster_id"],
                        gap_days,
                        effective_max_gap,
                    )
                    edge_type, confidence = _classify_edge(
                        features,
                        left.get("event_type", "unknown"),
                        left.get("tone", "neutral"),
                        right.get("event_type", "unknown"),
                        right.get("tone", "neutral"),
                        left.get("trigger_verb", ""),
                        right.get("trigger_verb", ""),
                    )
                all_edges.append({
                    "story_id": story_key,
                    "source_cluster": left["cluster_id"],
                    "target_cluster": right["cluster_id"],
                    "edge_type": edge_type,
                    "weight": round(max(0.1, confidence), 4),
                    "gap_days": gap_days,
                    "source_tone": left.get("tone", "neutral"),
                    "target_tone": right.get("tone", "neutral"),
                })

    chapter_records.sort(key=lambda item: (item["start_time"], item["story_id"]))
    chapter_by_id = {record["story_id"]: record for record in chapter_records}
    reference_candidates: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    parent = {record["story_id"]: record["story_id"] for record in chapter_records}

    def _find_parent(story_id: int) -> int:
        root = parent[story_id]
        if root != story_id:
            parent[story_id] = _find_parent(root)
        return parent[story_id]

    def _union_story(a_id: int, b_id: int) -> None:
        root_a = _find_parent(a_id)
        root_b = _find_parent(b_id)
        if root_a == root_b:
            return
        if root_a < root_b:
            parent[root_b] = root_a
        else:
            parent[root_a] = root_b

    for idx, chapter_a in enumerate(chapter_records):
        for chapter_b in chapter_records[idx + 1:]:
            same_pair_family = chapter_a["entity_pair_set"] == chapter_b["entity_pair_set"] and bool(chapter_a["entity_pair_set"])
            gap_days = _date_range_gap_days(
                chapter_a["start_time"],
                chapter_a["end_time"],
                chapter_b["start_time"],
                chapter_b["end_time"],
            )
            if not same_pair_family and gap_days > CHAPTER_STRONG_LINK_WINDOW_DAYS:
                continue

            shared_entities = chapter_a["entities"] & chapter_b["entities"]
            non_power_shared = shared_entities - GLOBAL_POWERS
            sim = _cosine_similarity(chapter_a["embedding"], chapter_b["embedding"])
            reason = None
            score = 0.0
            strong_link = False

            if (
                same_pair_family
                and gap_days <= 5
                and chapter_a["dominant_type"] == chapter_b["dominant_type"]
                and sim >= 0.72
            ):
                reason = "pair_family"
                score = 0.92 if gap_days <= 3 else 0.86
                strong_link = True
            elif non_power_shared and gap_days <= CHAPTER_CONTEXT_WINDOW_DAYS and sim >= max(CHAPTER_CONTEXT_MIN_SIM, 0.72):
                reason = "shared_entity_context"
                score = 0.72 + min(0.18, sim * 0.2)
                strong_link = True
            elif len(non_power_shared) >= 2 and gap_days <= 10 and sim >= 0.5:
                reason = "multi_entity_overlap"
                score = 0.70 + min(0.1, sim * 0.1)
                strong_link = True
            elif same_pair_family and gap_days <= PAIR_FAMILY_REFERENCE_MAX_GAP_DAYS:
                reason = "pair_family_reference"
                score = 0.70 if chapter_a["dominant_type"] == chapter_b["dominant_type"] else 0.60

            if reason is None:
                continue

            reference_candidates[chapter_a["story_id"]].append({
                "neighbor_story_id": chapter_b["story_id"],
                "reason": reason,
                "score": round(score, 4),
            })
            reference_candidates[chapter_b["story_id"]].append({
                "neighbor_story_id": chapter_a["story_id"],
                "reason": reason,
                "score": round(score, 4),
            })
            if strong_link:
                _union_story(chapter_a["story_id"], chapter_b["story_id"])

    macro_groups: Dict[int, List[int]] = defaultdict(list)
    for record in chapter_records:
        macro_groups[_find_parent(record["story_id"])].append(record["story_id"])
    raw_macro_groups = list(macro_groups.values())
    macro_groups = defaultdict(list)
    macro_group_index = 0
    for group_story_ids in raw_macro_groups:
        split_groups = _split_large_macro_group(group_story_ids, chapter_by_id)
        for subgroup in split_groups:
            if not subgroup:
                continue
            macro_groups[macro_group_index] = subgroup
            macro_group_index += 1

    pair_story_groups: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for record in chapter_records:
        pair_story_groups[record["pair"]].append(record["story_id"])

    pair_meta_by_story: Dict[int, Dict[str, int]] = {}
    for pair_story_ids in pair_story_groups.values():
        ordered_ids = sorted(pair_story_ids, key=lambda story_id: (chapter_by_id[story_id]["start_time"], story_id))
        for pair_order, story_id in enumerate(ordered_ids, start=1):
            pair_meta_by_story[story_id] = {
                "pair_chapter_index": pair_order,
                "pair_chapter_count": len(ordered_ids),
            }

    story_meta_by_id: Dict[int, Dict[str, Any]] = {}
    for member_story_ids in macro_groups.values():
        ordered_ids = sorted(member_story_ids, key=lambda story_id: (chapter_by_id[story_id]["start_time"], story_id))
        macro_story_id = _stable_story_int("macro|" + "|".join(str(story_id) for story_id in ordered_ids))
        for chapter_order, story_id in enumerate(ordered_ids, start=1):
            refs = sorted(
                reference_candidates.get(story_id, []),
                key=lambda item: (-item["score"], item["neighbor_story_id"]),
            )[:MAX_REFERENCE_NEIGHBORS]
            pair_refs = [item for item in refs if item["reason"].startswith("pair_family")]
            context_refs = [item for item in refs if not item["reason"].startswith("pair_family")]
            story_meta_by_id[story_id] = {
                "story_kind": "chapter",
                "macro_story_id": macro_story_id,
                "macro_chapter_index": chapter_order,
                "macro_chapter_count": len(ordered_ids),
                "pair_key": list(chapter_by_id[story_id]["pair"]),
                "dominant_type": chapter_by_id[story_id]["dominant_type"],
                "dominant_trigger": chapter_by_id[story_id]["dominant_trigger"],
                "max_day_density": chapter_by_id[story_id]["max_day_density"],
                "time_precision": chapter_by_id[story_id]["time_precision"],
                "reference_neighbors": refs,
                "pair_family_neighbors": pair_refs,
                "context_neighbors": context_refs,
            }
            story_meta_by_id[story_id].update(pair_meta_by_story.get(story_id, {}))

    story_relations = _build_story_relations(
        chapter_by_id,
        reference_candidates,
        macro_groups,
        pair_story_groups,
    )

    elapsed = time.time() - t0
    logger.info(
        "Chapterized %d pair streams into %d display stories and %d macro groups in %.1fs",
        len(pair_to_events),
        len(chapter_records),
        len(macro_groups),
        elapsed,
    )

    _ensure_story_relations_table(cur)
    if clear_existing:
        cur.execute("DELETE FROM story_edges")
        cur.execute("DELETE FROM story_relations")
        cur.execute("DELETE FROM story_hierarchy")
        cur.execute("DELETE FROM story_trees")
        conn.commit()
        logger.info("Cleared existing story tables (incl story_trees)")

    edge_count = 0
    if all_edges:
        rows_to_insert = [
            (
                _stable_story_int(edge["story_id"]),
                edge["source_cluster"],
                edge["target_cluster"],
                edge["edge_type"],
                edge["weight"],
                edge["source_cluster"],
                edge["target_cluster"],
            )
            for edge in all_edges
        ]
        try:
            psycopg2.extras.execute_values(
                cur,
                """
                    INSERT INTO story_edges
                        (story_id, from_cluster_id, to_cluster_id, edge_type, weight,
                         source_event_id, target_event_id)
                    VALUES %s
                    ON CONFLICT DO NOTHING
                """,
                rows_to_insert,
                template=None,
            )
            edge_count = len(rows_to_insert)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Batch edge insert failed (%d rows): %s", len(rows_to_insert), exc)

    relation_count = 0
    if story_relations:
        relation_rows = [
            (
                row["story_id"],
                row["neighbor_story_id"],
                row["relation_type"],
                row["layer"],
                row["score"],
                row["reason"],
                row["rank"],
            )
            for row in story_relations
        ]
        try:
            psycopg2.extras.execute_values(
                cur,
                """
                    INSERT INTO story_relations
                        (story_id, neighbor_story_id, relation_type, layer, score, reason, rank)
                    VALUES %s
                    ON CONFLICT (story_id, neighbor_story_id, relation_type, layer) DO UPDATE SET
                        score = EXCLUDED.score,
                        reason = EXCLUDED.reason,
                        rank = EXCLUDED.rank
                """,
                relation_rows,
                template=None,
            )
            relation_count = len(relation_rows)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Batch relation insert failed (%d rows): %s", len(relation_rows), exc)

    story_nodes: Dict[int, Set[str]] = defaultdict(set)
    story_edges_by_gid: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for edge in all_edges:
        gid = _stable_story_int(edge["story_id"])
        story_nodes[gid].add(edge["source_cluster"])
        story_nodes[gid].add(edge["target_cluster"])
        story_edges_by_gid[gid].append(edge)

    try:
        for chapter in chapter_records:
            gid = chapter["story_id"]
            nodes = story_nodes.get(gid, set(chapter["cluster_ids"]))
            nc = len(nodes)
            depth = _compute_story_depth(gid, all_edges)
            node_dates = [cluster_dates[nid] for nid in nodes if nid in cluster_dates]
            start_date = min(node_dates) if node_dates else None
            end_date = max(node_dates) if node_dates else None
            edges_for_story = story_edges_by_gid.get(gid, [])
            title = _generate_story_title(edges_for_story, cluster_info_for_title)
            if not title:
                pair = chapter["pair"]
                title = f"{pair[0]}对{pair[1]}的{chapter['dominant_type']}事件"
            meta_val = story_meta_by_id.get(gid, {})
            if 1 < meta_val.get("pair_chapter_count", 1) <= 12:
                title = f"{title} 第{meta_val['pair_chapter_index']}阶段"
            cur.execute(
                """
                INSERT INTO story_trees (id, title, node_count, depth, start_date, end_date, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    node_count = EXCLUDED.node_count,
                    depth = EXCLUDED.depth,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    meta = EXCLUDED.meta
                """,
                (gid, title, nc, depth, start_date, end_date, json.dumps(meta_val)),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.warning("story_trees insert/update failed: %s", exc)

    all_nodes: Set[str] = set()
    edge_types = defaultdict(int)
    for edge in all_edges:
        all_nodes.add(edge["source_cluster"])
        all_nodes.add(edge["target_cluster"])
        edge_types[edge["edge_type"]] += 1

    max_story_nodes = max((record["node_count"] for record in chapter_records), default=0)
    max_story_day_density = max((record["max_day_density"] for record in chapter_records), default=0)

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Event Evolution Chain — Build Complete")
    logger.info("=" * 60)
    logger.info("  Display Stories: %d", len(chapter_records))
    logger.info("  Macro Groups:    %d", len(macro_groups))
    logger.info("  Nodes:           %d", len(all_nodes))
    logger.info("  Edges:           %d", edge_count)
    logger.info("  Relations:       %d", relation_count)
    logger.info("  Max story size:  %d nodes", max_story_nodes)
    logger.info("  Max day density: %d nodes/day", max_story_day_density)
    for edge_type, count in sorted(edge_types.items(), key=lambda item: (-item[1], item[0])):
        logger.info("    %-20s %d", edge_type, count)
    logger.info("  Time:            %.1fs", time.time() - t0)

    conn.close()
    return {
        "graphs": len(chapter_records),
        "nodes": len(all_nodes),
        "edges": edge_count,
        "edge_types": dict(edge_types),
        "macro_groups": len(macro_groups),
        "relations": relation_count,
        "max_story_nodes": max_story_nodes,
        "max_story_day_density": max_story_day_density,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Event Evolution Chain builder")
    parser.add_argument("--min-articles", type=int, default=2,
                        help="Minimum articles per L1 cluster")
    parser.add_argument("--max-gap-days", type=int, default=30,
                        help="Max days between events in same chain")
    parser.add_argument("--no-clear", action="store_true",
                        help="Don't clear existing story tables")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    build_storylines(
        min_article_count=args.min_articles,
        clear_existing=not args.no_clear,
        max_gap_days=args.max_gap_days,
    )


if __name__ == "__main__":
    main()
