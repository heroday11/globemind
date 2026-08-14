"""
Event coreference clustering — two-tier architecture.

Tier 1 (coreferent-pair clustering):
  - event_type partition
  - (initiator, target) entity-pair indexing
  - trigger similarity (legacy) or BGE-M3 embedding cosine similarity (primary)
  - time delta windows
  Connected components via UnionFind.

Tier 2 (singleton rescue):
  - FAISS nearest-neighbor index across all articles
  - Singletons that failed Tier 1 try to attach to non-singleton clusters
    from different entity pairs at high similarity threshold (0.90)
  - Mutual nearest neighbor + time window guardrails
  Ensures zero transitive chaining via UnionFind (singleton edges feed back
  into the same UnionFind graph, already validated in production).
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from core_pipeline.event_extract_v11 import Event, ExtractionResult
from core_pipeline.entity_normalizer import entity_pair_key
from core_pipeline.union_find import UnionFind

try:
    import numpy as np
    from sklearn.neighbors import NearestNeighbors
    _HAS_SKLEARN = True
except ImportError:
    np = None
    NearestNeighbors = None
    _HAS_SKLEARN = False

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    faiss = None
    _HAS_FAISS = False

logger = logging.getLogger("event_coref_cluster")

# ── Entity alias map (loaded from pre-computed embedding analysis) ──
# Maps raw entity names → merged canonical forms for fuzzy-matched entities
# e.g., "王毅" ↔ "Wang Yi", "唐纳德·特朗普" ↔ "特朗普"
_ENTITY_ALIAS_MAP: Dict[str, str] = {}


def load_entity_aliases(path: str) -> None:
    """Load pre-computed entity alias map from JSON."""
    import json
    with open(path, encoding="utf-8") as f:
        _ENTITY_ALIAS_MAP.clear()
        _ENTITY_ALIAS_MAP.update(json.load(f))
    logger.info("Loaded %d entity aliases from %s", len(_ENTITY_ALIAS_MAP), path)

# ── Trigger polarity keywords ──────────────────────────────
# Prevent auto-merging of opposite-polarity events (e.g., "breaks ties" vs "celebrates ties")
_POSITIVE_TRIGGER_WORDS = {
    "celebrate", "strengthen", "deepen", "cooperate", "aid",
    "support", "partner", "agree", "peace", "alliance",
    "friendship", "establish", "invest", "promote", "welcome",
    "praise", "launch", "boost", "enhance", "improve",
    "restore", "normalize", "facilitate", "expand", "renew",
    "sign", "reach", "pledge", "commit",
}
_NEGATIVE_TRIGGER_WORDS = {
    "break", "sanction", "conflict", "war", "collapse",
    "dispute", "crisis", "crackdown", "protest", "riot",
    "attack", "assassinate", "assassination", "repression",
    "condemn", "threaten", "tariff", "clash", "fight",
    "destroy", "kill", "violate", "denounce", "accuse",
    "withdraw", "cancel", "ban", "restrict", "block",
    "oppose", "halt", "stop", "cut", "sue", "lawsuit",
    "sever",
}


def _trigger_polarity(trigger: str) -> int:
    """Classify trigger polarity: 1 = positive, -1 = negative, 0 = neutral/mixed.
    Uses prefix matching so 'celebrates' matches keyword 'celebrate', 'sanctions' matches 'sanction'."""
    cleaned = re.sub(r'[^\w\s]', ' ', trigger.lower())
    words = set(cleaned.split())

    def _match(word_set, words):
        for w in words:
            for kw in word_set:
                if w.startswith(kw):
                    return True
        return False

    pos = _match(_POSITIVE_TRIGGER_WORDS, words)
    neg = _match(_NEGATIVE_TRIGGER_WORDS, words)
    if pos and not neg:
        return 1
    if neg and not pos:
        return -1
    return 0  # neutral or mixed


def _polarity_compatible(t1: str, t2: str) -> bool:
    """Two triggers are polarity-compatible (not one positive & one negative)."""
    p1 = _trigger_polarity(t1)
    p2 = _trigger_polarity(t2)
    if p1 * p2 < 0:  # opposite polarity
        return False
    return True


# ── Article body quality check ──────────────────────────────
# Filter out articles with no real content (CSS scraped, newsfeed list pages)
_CSS_SYNTAX_PATTERN = re.compile(r'[\{\};#]')
_NEWSLINE_PATTERN = re.compile(r'https?://\S+')


def _is_low_quality_body(body: Optional[str], min_chars: int = 100) -> bool:
    """Check if article body is too low-quality for event extraction.

    Two-tier approach prevents full-body dilution of garbage signals:
      - Tier 1 (head[:500]): CSS syntax ratio, URL lines, template nav lines.
        Concentrated garbage signals in the first 500 chars, before real content
        dilutes them in longer bodies. If head has no period structure, checks
        garbage signals and falls back to content rescue from the rest of body.
      - Tier 2 (full body): sentence structure check (periods, sentence endings).
        Full body used here because real content may start after site chrome.
    """
    if not body or not isinstance(body, str):
        return True
    body = body.strip()
    if len(body) < min_chars:
        return True

    head = body[:500]

    # ── Fast path: head has real sentence content → definitely real article ──
    _has_sent = lambda t: bool(re.search(r'(?<!\d)\.\s*[A-ZА-Я]', t))
    if _has_sent(head) or '。' in head:
        return False

    # ── Tier 1: Head-based garbage detection ──
    # Only runs when head has no period structure. If a check fires, content
    # rescue checks whether real article text exists later in the body (after
    # embedded CSS/scripts at the top of the scraped page).

    # CSS-heavy → scraping artifact
    css_chars = len(_CSS_SYNTAX_PATTERN.findall(head))
    if css_chars > len(head) * 0.02:
        # Content rescue: check limited window (500-3500) so articles with
        # CSS at the top are rescued, but spam pages with CSS throughout
        # (exbulletin, first sentence at offset 7000) are still filtered.
        rescue = body[500:3500]
        if _has_sent(rescue) or '。' in rescue:
            return False
        return True

    # Newsfeed list page: many URLs as standalone lines
    url_lines = sum(1 for line in head.split('\n') if _NEWSLINE_PATTERN.search(line))
    if url_lines >= 4:
        return True

    # Template/navigation detection: >50% short lines without periods
    head_lines = [l.strip() for l in head.split('\n') if l.strip()]
    if len(head_lines) >= 4:
        nav_lines = sum(1 for l in head_lines
                        if len(l) < 35 and not l.rstrip().endswith(('.', '。')))
        if nav_lines / len(head_lines) > 0.5:
            return True

    # Link-list page detection: if >70% of head lines are < 80 chars with no
    # sentence-ending punctuation, the body is a newsfeed/article-listing page
    # rather than an actual article (e.g., TRT World category pages).
    link_like = sum(1 for l in head_lines
                    if len(l) < 80
                    and not any(l.rstrip().endswith(p) for p in ('.', '!', '?')))
    if len(head_lines) >= 5 and link_like / len(head_lines) > 0.7:
        return True

    # ── Tier 2: Full-body sentence content check ──
    # Bodies with 100+ characters but NO sentence-ending period
    # are site chrome (schedules, nav menus, article lists) rather
    # than real content. Uses lookbehind for digits to avoid matching
    # radio frequencies ("89.5") and requires an uppercase letter after
    # whitespace to avoid matching abbreviations ("Aug. 11").
    if _has_sent(body) or '。' in body or body.rstrip().endswith(('.', '。')):
        return False
    return True

def _entity_word_in_body(ent: str, body_lower: str) -> bool:
    """Check if entity name appears in body using word-boundary matching.

    For short names (< 4 chars), uses word boundaries to prevent false
    positives like 'US' matching inside 'August' or 'focus'. Also handles
    period-separated variants like 'U.S.' for entity 'US'.
    """
    ent_lower = ent.lower().strip('., ')
    if not ent_lower:
        return False

    # Generate variants: raw and without periods (catches U.S. ↔ US)
    variants = {ent_lower, ent_lower.replace('.', '')}

    for v in variants:
        if not v:
            continue
        if len(v) <= 3:
            if re.search(r'\b' + re.escape(v) + r'\b', body_lower):
                return True
        else:
            if v in body_lower:
                return True
    return False


def _extraction_matches_body(event: Event, body: str) -> bool:
    """Verify extracted event is actually discussed in the article body.

    Two-tier check: EITHER the entity names (initiator/target) appear in the
    body, OR the trigger content words appear (via prefix matching). This
    catches sidebar contamination where neither entities nor trigger keywords
    from the extraction are present in the actual article text.

    Checks both raw entity strings and canonical forms (after title stripping)
    to handle cases like 'Caretaker PM of Pakistan' → 'pakistan' in body.
    """
    if not body or not event:
        return True
    body_lower = body.lower()

    # Check entity names (both raw and canonical forms)
    # Also try variants: strip parentheticals, split comma-separated multi-entity
    for ent in (event.initiator, event.target):
        if ent and ent.lower() not in ('null', 'none', 'unknown', ''):
            if _entity_word_in_body(ent, body_lower):
                return True
            # Try stripped form (remove parenthetical annotations like "王毅(中国外交部长)")
            stripped = re.sub(r'[（(][^)）]*[)）]', '', ent).strip()
            if stripped != ent and _entity_word_in_body(stripped, body_lower):
                return True
            # Try canonical form (title-stripped)
            canon = _canonical_entity(ent)
            if canon and len(canon) > 2 and canon in body_lower:
                return True
            # Try each part of comma-separated entities ("Trump, Netanyahu")
            for sep in [', ', '，', ',']:
                if sep in ent:
                    for part in ent.split(sep):
                        part = part.strip()
                        if part and _entity_word_in_body(part, body_lower):
                            return True
                    break  # Only try one separator

    # Check trigger content words via prefix matching.
    # v11: trigger is template → skip body-match (entity matching unreliable
    # for multilingual CC-NEWS).
    # v12: trigger_verb is real extracted value → entity check already done
    # above; if entity names didn't match the 400-char body preview, it's
    # likely a text coverage issue (entity appears later in the article),
    # not sidebar contamination. Accept the extraction.
    from core_pipeline.event_extract_v11 import _DEFAULT_TRIGGERS
    if event.trigger in _DEFAULT_TRIGGERS.values() or getattr(event, 'trigger_verb', None):
        return True  # v11 template or v12 real trigger_verb → accept

    # Uses 4-char common prefix to handle affix variations:
    #   "meets" ↔ "meeting" (both share prefix "meet")
    #   "congratulates" ↔ "congratulatory" (share "congratul")
    #   "sanctions" ↔ "sanction" (one is prefix of the other)
    trigger_words = re.sub(r'[^\w\s]', ' ', event.trigger.lower()).split()
    stopwords = {"the", "a", "an", "in", "on", "at", "to", "for", "of",
                 "with", "and", "or", "by", "from", "its", "his", "her",
                 "their", "is", "are", "was", "were", "be", "been", "being",
                 "has", "have", "had", "do", "does", "did", "will", "would",
                 "could", "should", "may", "might", "shall", "can",
                 "not", "no", "nor", "but", "if", "so", "as", "up", "down"}
    content_words = [w for w in trigger_words if w not in stopwords and len(w) > 2]
    if content_words:
        body_words = set(re.findall(r'[a-z]+', body_lower))
        matches = 0
        for w in content_words:
            for bw in body_words:
                # Direct prefix: "sanctions" matches "sanction" body text.
                # For the reverse (trigger starts with body word), require
                # body word >= 3 chars to prevent "meets" matching "me".
                if bw.startswith(w) or (len(bw) >= 3 and w.startswith(bw)):
                    matches += 1
                    break
                # Same root, different affixes: "meets" ↔ "meeting"
                min_len = min(len(w), len(bw))
                if min_len >= 4 and w[:4] == bw[:4]:
                    matches += 1
                    break
        if matches >= max(1, len(content_words) // 3):
            return True

    # Neither entity nor trigger matched → likely sidebar contamination
    return False


# ── Time windows (days) per event_type ──────────────────────
TIME_WINDOWS: Dict[str, int] = {
    "trade_conflict": 90,         # Tariffs, sanctions, negotiations unfold over months
    "diplomacy": 90,              # Wide window for multi-month engagement cycles
    "military": 90,               # Conflicts, ceasefire violations, military posturing (same as diplomacy/trade)
    "policy_legal": 60,           # Legislation, elections, court cases
    "protest_repression": 90,     # Protests/repression cycles span seasons
    "terrorism_espionage": 90,    # Investigations and campaigns cross months
    "human_rights_migration": 90, # Migration trends and rights campaigns evolve slowly
    "aid_disaster": 60,           # Relief efforts span weeks; isolated disasters stay singleton
    "appointment_leadership": 60, # Appointments are one-shot events; window helps fringe

    # Fine-grained base types (pre-alias, used by non-aliased types)
    "ceasefire": 7, "military_conflict": 7, "border_clash": 7,
    "attack": 7, "assassination": 7, "protest": 7, "riot": 7,
    "accident": 7, "disaster": 7, "appointment": 7, "resignation": 7,
    "leadership_change": 7,
    "diplomatic_meeting": 14, "diplomatic_visit": 14,
    "diplomatic_break": 14, "negotiation": 21, "peace_talk": 21,
    "election": 14, "legislation_policy": 21,
    "legal_action": 21, "judiciary": 21,
    "territorial_dispute": 21, "tariff_trade_war": 30, "sanction": 30,
    "military_cooperation": 21, "cyber_attack": 21, "espionage": 21,
    "repression": 21,
    "treaty": 30, "agreement": 30, "alliance": 30,
    "economic_cooperation": 30, "investment": 30, "aid": 30,
    "technology": 30, "energy_dispute": 30,
    "human_rights": 60, "migration": 60, "refugee": 60,
    "disease": 60, "space": 60, "science": 60,
    "other": 14,
}

# ── Similarity thresholds ───────────────────────────────────
DIPLO_CLUSTER_WINDOW = 3       # days — diplomatic events within window auto-cluster


# ── Event types where trigger variants describe the same event ──
# For these, same (initiator, target) + tight time = same event
# regardless of trigger phrasing ("holds talks with" vs "diplomatic visit",
# "appoints" vs "installs" vs "establishes")
AFFINITY_TYPES = {"diplomatic_meeting", "diplomatic_visit", "diplomatic_break", "appointment"}

# ── Location / entity aliases ────────────────────────────────
LOCATION_ALIASES = {
    "us": "united states", "usa": "united states", "america": "united states",
    "u.s.": "united states", "u.s": "united states", "u.s.a.": "united states",
    "the u.s.": "united states", "the u.s": "united states",
    "the us": "united states", "the usa": "united states",
    "the united states": "united states",
    "uk": "united kingdom", "britain": "united kingdom", "u.k.": "united kingdom",
    "uae": "united arab emirates", "u.a.e.": "united arab emirates",
    "turkiye": "turkey", "türkiye": "turkey",
    "lao": "laos",
    "dprk": "north korea", "north korea": "dprk",
    "ro korea": "south korea", "south korea": "republic of korea",
    "russia": "russian federation",
    "china": "people's republic of china", "prc": "people's republic of china",
    "iran": "iran",
    "syria": "syrian arab republic",
    "venezuela": "bolivarian republic of venezuela",
    "myanmar": "burma", "burma": "myanmar",
    "côte d'ivoire": "cote d'ivoire", "ivory coast": "cote d'ivoire",
    "czech republic": "czechia", "czechia": "czech republic",
    "palestine": "state of palestine",
    "vatican": "holy see", "holy see": "vatican",
    # Leader aliases — map heads of state to their country so articles
    # using different references to the same leader get clustered together.
    "trump": "united states", "donald trump": "united states",
    "president trump": "united states", "trump administration": "united states",
    "biden": "united states", "president biden": "united states",
    "joe biden": "united states", "biden administration": "united states",
    "xi": "china", "xi jinping": "china", "president xi": "china",
    "putin": "russia", "vladimir putin": "russia", "president putin": "russia",
    "kim": "north korea", "kim jong un": "north korea",
    "modi": "india", "narendra modi": "india", "prime minister modi": "india",
    "trudeau": "canada", "justin trudeau": "canada",
    "scholz": "germany", "olaf scholz": "germany",
    "macron": "france", "emmanuel macron": "france",
    "sunak": "uk", "rishi sunak": "uk",
    "starmer": "uk", "keir starmer": "uk",
    "lai": "taiwan", "lai ching te": "taiwan", "tsai": "taiwan",
    "netanyahu": "israel", "benjamin netanyahu": "israel",
    "marco rubio": "united states", "rubio": "united states",
    "vladimir zelensky": "ukraine", "zelensky": "ukraine", "zelenskyy": "ukraine",
    "mohammed bin salman": "saudi arabia", "mbs": "saudi arabia",
    "sheikh mohamed": "uae", "mbz": "uae",
    "mohamed bin zayed": "uae",
    "erdogan": "turkey", "recep tayyip erdogan": "turkey",
    "nawaf salam": "kuwait",
    "mohammad reza": "iran", "khamenei": "iran",
    "bashar al assad": "syria", "assad": "syria",
    # Government bodies and institutions
    "pentagon": "united states", "the pentagon": "united states",
    "white house": "united states", "the white house": "united states",
    "state department": "united states", "us state department": "united states",
    "congress": "united states", "us congress": "united states",
    "senate": "united states", "house of representatives": "united states",
    "kremlin": "russia", "the kremlin": "russia",
    "duma": "russia", "state duma": "russia",
    "zhongnanhai": "china", "chinese government": "china",
    "cpc": "china", "ccp": "china", "chinese communist party": "china",
    "pla": "china", "chinese military": "china", "people's liberation army": "china",
    "national people's congress": "china", "npc": "china",
    "iranian government": "iran", "irgc": "iran",
    "islamic revolutionary guard corps": "iran",
    "idf": "israel", "israel defense forces": "israel",
    "israeli government": "israel",
    "hamas": "palestine", "hezbollah": "lebanon",
    "houthi": "yemen", "houthis": "yemen", "ansar allah": "yemen",
    "taliban": "afghanistan",
    "uk government": "uk", "british government": "uk",
    "westminster": "uk", "downing street": "uk",
    "elysee": "france", "elysee palace": "france",
    "chancellery": "germany",
    "kantei": "japan", "japanese government": "japan",
    "blue house": "south korea",
    "united nations": "un", "un": "un", "the un": "un",
    "nato": "nato", "the nato": "nato",
    "european union": "eu", "eu": "eu",
    "european commission": "eu", "european council": "eu",
    "world bank": "world bank",
    "imf": "imf", "international monetary fund": "imf",
    "who": "who", "world health organization": "who",
    "world trade organization": "wto", "wto": "wto",
    # Adjective forms → country
    "american": "united states", "chinese": "china",
    "russian": "russia", "british": "uk",
    "iranian": "iran", "israeli": "israel",
    "french": "france", "german": "germany",
    "japanese": "japan", "south korean": "south korea",
    "north korean": "north korea", "indian": "india",
    "australian": "australia", "canadian": "canada",
    "saudi": "saudi arabia", "turkish": "turkey",
    "ukrainian": "ukraine", "syrian": "syria",
    "iraqi": "iraq", "afghan": "afghanistan",
    "pakistani": "pakistan", "palestinian": "palestine",
    "yemeni": "yemen", "egyptian": "egypt",
    "european": "eu", "soviet": "russia",
    "arab": "united arab emirates",
    # Cities → country
    "beijing": "china", "shanghai": "china", "hong kong": "china",
    "washington": "united states", "washington dc": "united states",
    "moscow": "russia", "london": "uk", "paris": "france",
    "berlin": "germany", "tokyo": "japan", "seoul": "south korea",
    "new delhi": "india", "ottawa": "canada", "canberra": "australia",
    "brasilia": "brazil", "mexico city": "mexico",
    "riyadh": "saudi arabia", "abu dhabi": "uae", "dubai": "uae",
    "doha": "qatar", "ankara": "turkey", "tel aviv": "israel",
    "jerusalem": "israel", "cairo": "egypt", "bangkok": "thailand",
    "hanoi": "vietnam", "jakarta": "indonesia", "islamabad": "pakistan",
    "kabul": "afghanistan", "baghdad": "iraq", "damascus": "syria",
    "kuala lumpur": "malaysia", "singapore": "singapore",
    "manila": "philippines", "santiago": "chile",
    "cape town": "south africa", "pretoria": "south africa",
    "tehran": "iran", "pyongyang": "north korea",
    "rome": "italy", "madrid": "spain", "stockholm": "sweden",
    "oslo": "norway", "helsinki": "finland", "copenhagen": "denmark",
    "warsaw": "poland", "kyiv": "ukraine", "vienna": "austria",
    "brussels": "belgium", "amsterdam": "netherlands",
    "dublin": "ireland", "athens": "greece", "lisbon": "portugal",
    "bern": "switzerland", "budapest": "hungary",
    "prague": "czech republic", "bucharest": "romania",
    "addis ababa": "ethiopia", "nairobi": "kenya",
    "lagos": "nigeria", "accra": "ghana",
    # Additional world leaders (by country)
    "shinzo abe": "japan", "abe": "japan", "fumio kishida": "japan", "kishida": "japan",
    "yoon suk yeol": "south korea", "yoon": "south korea",
    "moon jae in": "south korea", "moon": "south korea",
    "lee jae myung": "south korea",
    "anthony albanese": "australia", "albanese": "australia",
    "scott morrison": "australia", "morrison": "australia",
    "luiz inacio lula da silva": "brazil", "lula": "brazil",
    "jair bolsonaro": "brazil", "bolsonaro": "brazil",
    "claudia sheinbaum": "mexico", "sheinbaum": "mexico",
    "manuel lopez obrador": "mexico", "amlo": "mexico",
    "javier milei": "argentina", "milei": "argentina",
    "shehbaz sharif": "pakistan", "sharif": "pakistan",
    "imran khan": "pakistan",
    "narendra modi": "india", "modi": "india",
    "zoran milanovic": "croatia", "milanovic": "croatia",
    "aleksandar vucic": "serbia", "vucic": "serbia",
    "volodymyr zelensky": "ukraine", "zelensky": "ukraine", "zelenskyy": "ukraine",
    "petro poroshenko": "ukraine", "poroshenko": "ukraine",
    "kyrsten sinema": "united states", "sinema": "united states",
    "jake sullivan": "united states",
    "antony blinken": "united states", "blinken": "united states",
    "lloyd austin": "united states", "austin": "united states",
    "kristi noem": "united states",
    "el salvador": "el salvador",
    "nayib bukele": "el salvador", "bukele": "el salvador",
    "mohamed bin salman": "saudi arabia", "mbs": "saudi arabia",
    "salman bin abdulaziz": "saudi arabia",
    "sheikh mohamed": "uae", "sheikh mohammed": "uae", "mbz": "uae",
    "mohamed bin zayed": "uae", "mohammed bin zayed": "uae",
    "recep tayyip erdogan": "turkey", "erdogan": "turkey",
    "abdullah ii": "jordan", "king abdullah": "jordan",
    "abdul fattah el sisi": "egypt", "el sisi": "egypt", "sisi": "egypt",
    "abdel fattah el sisi": "egypt",
    "mohammed bin salman al saud": "saudi arabia",
    "tamim bin hamad": "qatar", "sheikh tamim": "qatar",
    "hamad bin khalifa": "qatar",
    "nawaf salam": "kuwait",
    "mishal al ahmad": "kuwait",
    "haitham bin tariq": "oman",
    "isa bin salman": "bahrain",
    "hamad bin isa": "bahrain",
    "narendra modi": "india", "modi": "india",
    "droupadi murmu": "india",
    "subrahmanyam jaishankar": "india", "jaishankar": "india",
    "anwaar ul haq kakar": "pakistan",
    "asif ali zardari": "pakistan", "zardari": "pakistan",
    "bilawal bhutto": "pakistan", "bhutto": "pakistan",
    "joko widodo": "indonesia", "jokowi": "indonesia",
    "prabowo subianto": "indonesia", "prabowo": "indonesia",
    "bongbong marcos": "philippines", "marcos": "philippines",
    "rodrigo duterte": "philippines", "duterte": "philippines",
    "nguyen xuan phuc": "vietnam",
    "pham minh chinh": "vietnam",
    "to lam": "vietnam",
    "prayut chan ocha": "thailand", "prayut": "thailand",
    "srettha thavisin": "thailand",
    "lee hsien loong": "singapore", "lee hsien loong": "singapore",
    "lawrence wong": "singapore",
    "anwar ibrahim": "malaysia", "anwar": "malaysia",
    "mohamed muizzu": "maldives",
    "ranil wickremesinghe": "sri lanka", "wickremesinghe": "sri lanka",
    "gotabaya rajapaksa": "sri lanka", "rajapaksa": "sri lanka",
    "justin trudeau": "canada", "trudeau": "canada",
    "pierre poilievre": "canada", "poilievre": "canada",
    "chrystia freeland": "canada",
    "jimmy carter": "united states", "carter": "united states",
    "barack obama": "united states", "obama": "united states",
    "hillary clinton": "united states", "clinton": "united states",
    "kamala harris": "united states", "harris": "united states",
    "bernie sanders": "united states", "sanders": "united states",
    "mike pence": "united states", "pence": "united states",
    "tim walz": "united states",
    "jd vance": "united states", "vance": "united states",
    "pete hegseth": "united states", "hegseth": "united states",
    "tulsi gabbard": "united states",
    "mike pompeo": "united states", "pompeo": "united states",
    "john bolton": "united states", "bolton": "united states",
    "john kerry": "united states", "kerry": "united states",
    "nikki haley": "united states", "haley": "united states",
    "mitt romney": "united states", "romney": "united states",
    "charles michel": "eu",
    "ursula von der leyen": "eu", "von der leyen": "eu",
    "joe biden": "united states", "biden": "united states",
    "emmanuel macron": "france", "macron": "france",
    "olaf scholz": "germany", "scholz": "germany",
    "friedrich merz": "germany", "merz": "germany",
    "angela merkel": "germany", "merkel": "germany",
    "giorgia meloni": "italy", "meloni": "italy",
    "mateo salvini": "italy",
    "enrico letta": "italy",
    "pedro sanchez": "spain", "sanchez": "spain",
    "donald tusk": "poland", "tusk": "poland",
    "mateusz morawiecki": "poland",
    "kyriakos mitsotakis": "greece", "mitsotakis": "greece",
    "victor orban": "hungary", "orban": "hungary",
    "petr fiala": "czech republic", "fiala": "czech republic",
    "robert fico": "slovakia", "fico": "slovakia",
    "milo vucevic": "serbia",
    "edgars rinkevics": "latvia",
    "gitanas nauseda": "lithuania",
    "kaja kallas": "eu", "kallas": "eu",
    "mark rutte": "netherlands", "rutte": "netherlands",
    "alexander de croo": "belgium",
    "simon harris": "ireland",
    "ulrich ulf kristersson": "sweden", "kristersson": "sweden",
    "jonas gahr store": "norway", "store": "norway",
    "petteri orpo": "finland", "orpo": "finland",
    "mette frederiksen": "denmark", "frederiksen": "denmark",
    "william ruto": "kenya", "ruto": "kenya",
    "uhuru kenyatta": "kenya", "kenyatta": "kenya",
    "abiy ahmed": "ethiopia", "abiy": "ethiopia",
    "cyril ramaphosa": "south africa", "ramaphosa": "south africa",
    "bola tinubu": "nigeria", "tinubu": "nigeria",
    "nana akufo addo": "ghana",
    "azali assoumani": "comoros",
    "daniel chapo": "mozambique",
    "hakainde hichilema": "zambia",
    "emmanuel ramon": "mexico",
    "jose pimentel": "cabo verde",
    "luis lacalle pou": "uruguay",
    "santiago pena": "paraguay",
    "gustavo petro": "colombia", "petro": "colombia",
    "ivan duque": "colombia", "duque": "colombia",
    "nicolas maduro": "venezuela", "maduro": "venezuela",
    "juan guaido": "venezuela", "guaido": "venezuela",
    "daniel ortega": "nicaragua", "ortega": "nicaragua",
    "xiomara castro": "honduras",
    "rodrigo chaves": "costa rica",
    "laurentino cortizo": "panama",
    "jose raul mulino": "panama",
    "luis arce": "bolivia",
    "dina boluarte": "peru",
    "pedro castillo": "peru",
    "gabe newell": "new zealand",
    "jacinda ardern": "new zealand", "ardern": "new zealand",
    "christopher luxon": "new zealand", "luxon": "new zealand",
    "frank bainimarama": "fiji",
    "srettha thavisin": "thailand",
    "hun sen": "cambodia", "hun manet": "cambodia",
    "thongloun sisoulith": "laos",
    "min aung hlaing": "myanmar",
    "suharto": "indonesia",
    "mahathir mohamad": "malaysia", "mahathir": "malaysia",
    "mohamed muizzu": "maldives",
    "tshering tobgay": "bhutan",
    "xanana gusmao": "timor leste",
    "lucas papademos": "cook islands",
    # Country self-references and "of X" recoverable names
    "japan": "japan", "canada": "canada", "australia": "australia",
    "brazil": "brazil", "mexico": "mexico", "egypt": "egypt",
    "turkey": "turkey", "indonesia": "indonesia", "thailand": "thailand",
    "vietnam": "vietnam", "pakistan": "pakistan", "bangladesh": "bangladesh",
    "nigeria": "nigeria", "argentina": "argentina", "italy": "italy",
    "spain": "spain", "netherlands": "netherlands", "sweden": "sweden",
    "norway": "norway", "poland": "poland", "ukraine": "ukraine",
    "iraq": "iraq", "afghanistan": "afghanistan", "yemen": "yemen",
    "libya": "libya", "algeria": "algeria", "morocco": "morocco",
    "sudan": "sudan", "ethiopia": "ethiopia", "kenya": "kenya",
    "tanzania": "tanzania", "ghana": "ghana", "south africa": "south africa",
    "chile": "chile", "colombia": "colombia", "peru": "peru",
    "cuba": "cuba", "greece": "greece", "portugal": "portugal",
    "romania": "romania", "hungary": "hungary", "austria": "austria",
    "czech": "czech republic", "slovakia": "slovakia",
    "slovenia": "slovenia", "croatia": "croatia", "serbia": "serbia",
    "bulgaria": "bulgaria", "moldova": "moldova",
    "new zealand": "new zealand",
    "philippines": "philippines", "malaysia": "malaysia",
    "nepal": "nepal", "sri lanka": "sri lanka", "mongolia": "mongolia",
    # Cross-language name variants that appear in the 384 uncovered clusters
    "ucrania": "ukraine",  # Ukrainian/Portuguese
    "rusia": "russia",     # Spanish/Romanian
    "俄罗斯": "russia",     # Chinese
    "乌克兰": "ukraine",    # Chinese
    "習近平": "china",     # Chinese (traditional)
    "习近平": "china",     # Chinese (simplified)
    "Ucrania": "ukraine",
    "Ungaria": "hungary",  # Romanian for Hungary
    "Rusia": "russia",
    "Liên bang Nga": "russia",  # Vietnamese for "Russian Federation"
    "Thủ tướng Phạm Minh Chính": "vietnam",  # Vietnamese PM
    "Formatul București 9": "romania",  # Bucharest 9 format
    "Sanae Takaichi": "japan",  # Japanese politician
    "Bobi Wine": "uganda",  # Ugandan politician
    "Hakan Fidan": "turkey",  # Turkish foreign minister
    "Pedro Sánchez": "spain",
    "Pope Francis": "vatican",
    "Ursula von der Leyen": "eu",
    "Delcy Rodríguez": "venezuela",
    "Donald Tusk": "poland",
    "Viktor Orbán": "hungary",
    "Nicolás Maduro": "venezuela",
    "România": "romania",
    "Pete Hegseth": "united states",  # US Defense Secretary
    "Marco Rubio": "united states",
    "Mary Louise Kelly": "united states",
    "Bridget Brink": "united states",  # US ambassador
    "Judge James Boasberg": "united states",
    "President Trump": "united states",
    "JD Vance": "united states",
    "Pete Hegseth (Defense Secretary)": "united states",
    "Joe Kent": "united states",
    "John Gill (UK Minister)": "uk",
    "King Charles III": "uk",
    "Keir Starmer": "uk",
    "Vladímir Putin": "russia",
    "Vladimir Putin": "russia",
    "Kremlin": "russia",
    "foreign correspondent": "russia",  # Russian state media
    "Recep Tayyip Erdoğan": "turkey",
    "Israel and Iran": "israel, iran",
    "Democrats": "united states",
    "Homeland Security": "united states",
    "second Trump administration": "united states",
    "Americans in Iran": "iran",
    "Iranian protesters": "iran",
    "Iranian regime": "iran",
    # Parenthetical variants that appear in extracted data
    "iran (islamic republic of)": "iran",
    "usa (donald trump)": "united states",
    "united states (trump)": "united states",
    "china (xi jinping)": "china",
    "russia (putin)": "russia",
    "north korea (kim jong un)": "north korea",
    "venezuela (maduro)": "venezuela",
    "islamic republic of iran": "iran",
    "russian federation": "russia",
    "people's republic of china": "china",
    "republic of korea": "south korea",
    "state of palestine": "palestine",
    "syrian arab republic": "syria",
    "bolivarian republic of venezuela": "venezuela",
    "kingdom of saudi arabia": "saudi arabia",
}

# ── Titles to strip from entity names ──────────────────────
_TITLE_PATTERN = re.compile(
    r'^(president|prime minister|deputy prime minister|foreign minister|'
    r'defence minister|defense minister|finance minister|interior minister|'
    r'minister|deputy|secretary of state|secretary|senator|congressman|congresswoman|'
    r'ambassador|governor|mayor|chairman|chairperson|spokesperson|spokesman|spokeswoman|'
    r'general|admiral|colonel|h\.?e\.?|h\.?m\.?|h\.?r\.?h\.?|'
    r'mr\.?|mrs\.?|ms\.?|dr\.?|prof\.?|sir|lord|king|queen|prince|princess|'
    r'emperor|empress|sultan|sheikh|imam|ayatollah|ayatullah|'
    r'cardinal|archbishop|bishop|pastor|pope|'
    r'honorable|excellency|excellence|'
    r'fm|pm|vp|ceo|pres\.?|gov\.?|sen\.?|rep\.?|amb\.?|gen\.?|col\.?|lt\.?|capt\.?|'
    r'präsident|presidente|prezident|premier)\s+',
    re.IGNORECASE,
)


def _parse_dt(val: Any) -> Optional[datetime]:
    """Parse datetime from various formats."""
    if isinstance(val, datetime):
        return val
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    val = re.sub(r'\.\d+(\+|\-)', r'\1', val)
    val = re.sub(r'(\+|-)(\d{2}):(\d{2})', r'\1\2\3', val)
    # Handle bare +HH/-HH offset (PostgreSQL ::text yields +00 not +0000)
    # Only match if preceded by seconds digits (i.e. HH:MM:SS+00, not a bare date like 2024-01-10)
    val = re.sub(r'(?<=:\d{2})([+-]\d{2})$', r'\g<0>00', val)
    for fmt in ['%Y-%m-%d %H:%M:%S%z', '%Y-%m-%dT%H:%M:%S%z',
                 '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _normalize_trigger(trigger: str) -> Set[str]:
    """Lowercase, tokenize, remove stopwords."""
    t = trigger.lower().strip().rstrip('.')
    t = re.sub(r'[^\w\s]', ' ', t)
    tokens = set(t.split())
    stopwords = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "with",
                 "and", "or", "by", "from", "its", "his", "her", "their"}
    return tokens - stopwords


# ── Canonical name cache for fast reverse-lookup ──
_CANONICAL_NAME_CACHE: Optional[Set[str]] = None

def _get_canonical_names() -> Set[str]:
    """Build a cached set of all canonical country names (LOCATION_ALIASES values + common countries)."""
    global _CANONICAL_NAME_CACHE
    if _CANONICAL_NAME_CACHE is None:
        _CANONICAL_NAME_CACHE = set(LOCATION_ALIASES.values())
        _CANONICAL_NAME_CACHE.update({
            "japan", "china", "iran", "russia", "france", "germany",
            "india", "canada", "australia", "brazil", "mexico",
            "egypt", "turkey", "indonesia", "thailand", "vietnam",
            "pakistan", "bangladesh", "nigeria", "argentina",
            "italy", "spain", "netherlands", "sweden", "norway",
            "poland", "ukraine", "iraq", "afghanistan", "yemen",
            "libya", "algeria", "morocco", "sudan", "angola",
            "ethiopia", "kenya", "tanzania", "ghana",
            "south africa", "chile", "colombia", "peru",
            "cuba", "greece", "portugal", "romania",
            "hungary", "austria", "czech", "slovakia",
            "denmark", "finland", "belgium", "ireland",
            "new zealand", "philippines", "malaysia",
            "nepal", "sri lanka", "mongolia",
        })
    return _CANONICAL_NAME_CACHE


def _canonical_entity(name: Optional[str], _depth: int = 0) -> Optional[str]:
    """Normalize entity to canonical form for pairing.

    Strips titles + leading 'the', resolves aliases, extracts country
    from 'of X' patterns, splits comma-separated multi-entities.
    Returns lowercased core name.
    Returns None if no meaningful entity remains.

    Also checks the pre-computed _ENTITY_ALIAS_MAP (from embedding-based
    fuzzy matching) before falling through to the string-based logic.
    """
    if not name:
        return None
    if _depth > 5:
        return None

    # Pre-computed alias from embedding fuzzy matching (cross-language,
    # transliteration variants, abbreviation normalization)
    if _ENTITY_ALIAS_MAP and name in _ENTITY_ALIAS_MAP:
        return _ENTITY_ALIAS_MAP[name]

    n = name.strip().rstrip('., ').strip()
    if not n:
        return None

    # ── Strip parenthetical annotations ──
    # "USA (Donald Trump)" → "USA", "United States (Trump)" → "United States"
    # "US (Trump)" → "US", "Iran's government (IRGC)" → "Iran's government"
    n = re.sub(r'\s*[（(][^)）]*[)）]', '', n).strip()
    if not n:
        return None

    # ── "Last, First" format check BEFORE comma splitting ──
    # "Trump, Donald" is Last, First (not multi-entity).
    # Check if reversing the parts yields a known alias.
    for sep in (', ', '，', ','):
        if sep in n:
            parts = [p.strip() for p in n.split(sep) if p.strip()]
            if len(parts) == 2:
                rejoined = f"{parts[1]} {parts[0]}"
                rejoined_lower = rejoined.lower()
                # Check if the reversed form matches an alias or a known entity
                if rejoined_lower in LOCATION_ALIASES:
                    n = rejoined  # Replace and re-process
                    break
                # Also try with title stripped
                stripped = _TITLE_PATTERN.sub('', rejoined_lower).strip()
                if stripped in LOCATION_ALIASES or stripped.rstrip('.').strip() in LOCATION_ALIASES:
                    n = rejoined
                    break

    # ── Split on " and " / " & " ──
    # "US and Israel" → [US, Israel] → each canonicalized separately
    for sep in (' and ', ' & '):
        if sep in n:
            parts = [p.strip() for p in n.split(sep) if p.strip()]
            if len(parts) >= 2:
                canon_parts = []
                for p in parts:
                    cp = _canonical_entity(p, _depth=_depth + 1)
                    if cp:
                        canon_parts.append(cp)
                if canon_parts:
                    return ", ".join(sorted(set(canon_parts)))
            break

    # ── Split comma-separated multi-entities ──
    # The model sometimes outputs "United States, Israel" as initiator.
    # Normalize each part and return sorted, joined string so
    # "United States, Israel" and "US, Israel" map to the same key.
    for sep in (', ', '，', ','):
        if sep in n:
            parts = [p.strip() for p in n.split(sep) if p.strip()]
            if len(parts) >= 2:
                canon_parts = []
                for p in parts:
                    cp = _canonical_entity(p, _depth=_depth + 1)
                    if cp:
                        canon_parts.append(cp)
                if canon_parts:
                    return ", ".join(sorted(set(canon_parts)))
            break

    n_lower = n.lower()

    # ── "administration"/"regime" suffix → strip before alias lookup ──
    # "Trump administration" → "Trump" → alias-matched to "united states"
    admin_match = re.search(r'^(.+?)\s+(administration|regime|government|party)$', n_lower)
    if admin_match:
        core = admin_match.group(1).strip()
        if core in LOCATION_ALIASES:
            return LOCATION_ALIASES[core]
        # Try recursive canonical on core (depth-limited)
        return _canonical_entity(core, _depth=_depth + 1)

    # Strip trailing period for alias matching ("u.s." → "us")
    n_alias = n_lower.rstrip('.').strip()
    if n_alias in LOCATION_ALIASES:
        return LOCATION_ALIASES[n_alias]

    # Direct alias match
    if n_lower in LOCATION_ALIASES:
        return LOCATION_ALIASES[n_lower]

    # Strip titles
    n_stripped = _TITLE_PATTERN.sub('', n_lower).strip()

    if not n_stripped or len(n_stripped) < 2:
        return None

    # Strip genitive "'s" / "’s" suffix so "Trump's proposal" → alias-matchable to "trump"
    n_stripped = re.sub(r"[’']s\s*$", "", n_stripped).strip()
    if len(n_stripped) < 2:
        return None

    # Check alias again after stripping
    if n_stripped in LOCATION_ALIASES:
        return LOCATION_ALIASES[n_stripped]

    # Also check dot-stripped version after title stripping
    n_stripped_dotless = n_stripped.rstrip('.').strip()
    if n_stripped_dotless in LOCATION_ALIASES:
        return LOCATION_ALIASES[n_stripped_dotless]

    # ── Suffix match: find longest known entity suffix in remaining string ──
    # "us donald trump" → no direct match, but suffix "donald trump" is an alias.
    # "US President Donald Trump" → title strip → "us donald trump" → "donald trump" matches.
    words = n_stripped.split()
    for i in range(len(words)):
        suffix = " ".join(words[i:])
        if suffix in LOCATION_ALIASES:
            return LOCATION_ALIASES[suffix]

    # Extract country from "of X" / "of the X" / "of the republic of X" patterns
    # Handles: "Emperor of Japan"→"japan", "President of the Republic of Tajikistan"→"tajikistan"
    of_match = re.search(r'\bof\s+(?:the\s+)?(?:republic\s+of\s+)?(.+)$', n_stripped)
    if of_match:
        of_target = of_match.group(1).strip()
        if of_target in LOCATION_ALIASES:
            return LOCATION_ALIASES[of_target]
        return of_target

    # Strip leading "the " (only after title removal, not for pure "the X" names)
    if n_stripped.startswith("the "):
        n_stripped = n_stripped[4:].strip()
        if n_stripped in LOCATION_ALIASES:
            return LOCATION_ALIASES[n_stripped]

    # ── Word-by-word alias resolution (fallback) ──
    # When the full entity name doesn't match any alias pattern,
    # check each word individually. With the expanded LOCATION_ALIASES
    # this catches:
    #   "Chinese military" -> "chinese" -> "china"
    #   "American forces" -> "american" -> "united states"
    for w in reversed(n_stripped.split()):
        w_clean = w.rstrip('.').strip()
        if len(w_clean) >= 3 and w_clean in LOCATION_ALIASES:
            return LOCATION_ALIASES[w_clean]

    # ── Entity core word fallback ──
    # Extract the last meaningful word. For entity names where
    # individual words aren't aliased but the core word is a known
    # canonical name, this provides one more matching attempt.
    core = _entity_core_word(n_stripped)
    if core and len(core) >= 3:
        if core in LOCATION_ALIASES:
            return LOCATION_ALIASES[core]
        # Check reverse: is core a canonical name that appears in alias VALUES?
        if core in _get_canonical_names():
            return core

    return n_stripped


def _trigram_jaccard(s1: str, s2: str) -> float:
    """Character trigram Jaccard similarity — captures word shape."""
    t1 = set(s1[i:i+3] for i in range(len(s1)-2))
    t2 = set(s2[i:i+3] for i in range(len(s2)-2))
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _entity_similarity(name1: Optional[str], name2: Optional[str]) -> float:
    """Soft entity name similarity 0~1.

    Three-tier strategy:
    1. Canonical normalization via _canonical_entity() — handles known
       aliases (leader→country via LOCATION_ALIASES, title stripping).
       If both resolve to the same form → 1.0.
    2. Last-token comparison on canonical forms — the last word (usually
       the country core name, e.g. 'iran' from 'islamic republic of iran')
       is the most distinguishing part. If last-tokens match → 1.0.
    3. Trigram Jaccard on last-tokens only — avoids noise from long
       canonical forms sharing common words ('republic', 'of', etc).
       Short names (< 8 chars) get a penalty since trigram overlap
       at that length is inflated (e.g. 'iran' vs 'iraq' = 0.33).

    Returns 0.5 (neutral) when either name is missing.
    """
    if not name1 or not name2:
        return 0.5

    n1 = name1.lower().strip().rstrip('.')
    n2 = name2.lower().strip().rstrip('.')
    if n1 == n2:
        return 1.0

    # Canonical resolution (aliases, title stripping, country extraction)
    c1 = _canonical_entity(name1) or n1
    c2 = _canonical_entity(name2) or n2
    if c1 == c2:
        return 1.0

    # Last-token comparison — the most distinguishing part
    last1 = c1.split()[-1] if c1.split() else c1
    last2 = c2.split()[-1] if c2.split() else c2
    if last1 == last2:
        return 1.0

    # Trigram on last-tokens only (avoids 'republic of' noise)
    tri = _trigram_jaccard(last1, last2)

    # Short-name penalty: trigram is inflated for names < 8 chars
    if len(last1) < 8 and len(last2) < 8:
        tri *= 0.5

    return tri


def _time_proximity(
    dt1: Optional[datetime],
    dt2: Optional[datetime],
    time_window_days: int,
) -> float:
    """Exponential decay time proximity score 0~1.

    score = exp(-days_apart / time_window)
    - Same day → 1.0
    - 1 day apart in a 7-day window → exp(-1/7) ≈ 0.87
    - 3 days apart in a 3-day window → exp(-3/3) ≈ 0.37
    - 7 days apart in a 3-day window → exp(-7/3) ≈ 0.10

    Smooth decay replaces the hard cutoff, preserving soft signal
    even for articles just beyond the nominal window.
    """
    if dt1 is None or dt2 is None:
        return 0.5
    days = abs((dt1 - dt2).total_seconds()) / 86400
    if time_window_days <= 0:
        time_window_days = 7
    return math.exp(-days / time_window_days)


def _trigger_similarity(t1: str, t2: str) -> float:
    """Token Jaccard + character trigram Jaccard.

    Two-tier: returns the HIGHER of token-level overlap (handles
    tense/inflection variants like 'holds talks' vs 'held talks')
    and trigram-level shape similarity (handles word variants
    like 'sanctions' vs 'sanction').
    """
    tok1 = _normalize_trigger(t1)
    tok2 = _normalize_trigger(t2)
    if not tok1 or not tok2:
        return 0.0

    # Token Jaccard
    jac = len(tok1 & tok2) / len(tok1 | tok2) if tok1 | tok2 else 0.0

    # Trigram Jaccard on sorted token strings
    s1 = " ".join(sorted(tok1))
    s2 = " ".join(sorted(tok2))
    tri = _trigram_jaccard(s1, s2)

    return max(jac, tri)


def _time_delta_days(dt1: Optional[datetime], dt2: Optional[datetime]) -> Optional[int]:
    """Absolute days between two datetimes."""
    if dt1 is None or dt2 is None:
        return None
    return abs((dt1 - dt2).days)


def _time_window_days(event_type: str) -> int:
    return TIME_WINDOWS.get(event_type, 30)
# ═══════════════════════════════════════════════════════════════
# Embedding-enhanced event coreference clustering
#
# Architecture:
#   1. Filter valid events + low-quality body detection
#   2. Partition by event_type (strict — no type aliasing)
#   3. FAISS 10-NN neighbor index from BGE-M3 embeddings
#   4. Within each type: each article linked to its mutual NN neighbors
#      that pass: BGE-M3 cosine >= 0.75 + strict time window (7-14d)
#      + polarity check (affinity types only)
#   5. UnionFind connected components
#   6. split_overlong_clusters for transitive time-window enforcement
# ═══════════════════════════════════════════════════════════════
DEFAULT_EMBEDDING_SIM_THRESHOLD = 0.75
DEFAULT_EMBEDDING_TOP_K = 50

# ── Multi-signal fusion weights (Route B) ─────────────────────
# Three independent signals fused into one continuous score:
#   score = w_bge * bge_cosine + w_entity * entity_sim + w_time * time_prox
# Entity similarity uses canonical normalization + trigram fallback.
# Time proximity uses exponential decay (not hard cutoff).
# Threshold calibrated to be slightly more permissive than 0.85 hard gate
# for same-entity pairs, while keeping different-entity pairs safe.
FUSION_WEIGHTS = {
    "bge": 0.40,       # BGE-M3 embedding cosine (was 0.45)
    "entity": 0.30,    # Entity name similarity (was 0.35)
    "time": 0.15,      # Time proximity (was 0.20)
    "trigger": 0.10,   # Trigger verb similarity (new in v12)
    "location": 0.05,  # Location match (new in v12)
}


def load_embeddings_from_db(cur) -> Dict[int, np.ndarray]:
    """Load BGE-M3 embeddings from news_embeddings table."""
    if not _HAS_SKLEARN:
        raise ImportError("numpy/sklearn required for embedding-enhanced clustering")
    cur.execute("""
        SELECT ne.news_id, ne.embedding
        FROM news_embeddings ne
        JOIN event_coref_members ecm ON ne.news_id = ecm.news_id
    """)
    rows = cur.fetchall()
    embeddings: Dict[int, np.ndarray] = {}
    for news_id, emb_raw in rows:
        if isinstance(emb_raw, memoryview):
            emb_raw = bytes(emb_raw)
        if isinstance(emb_raw, bytes):
            emb_raw = json.loads(emb_raw.decode())
        embeddings[int(news_id)] = np.array(emb_raw, dtype=np.float32)
    logger.info("Loaded %d embeddings", len(embeddings))
    return embeddings


def build_semantic_index(
    embeddings: Dict[int, np.ndarray],
    n_neighbors: int = DEFAULT_EMBEDDING_TOP_K,
) -> Tuple[NearestNeighbors, List[int], np.ndarray]:
    """Build cosine-similarity nearest-neighbor index from all embeddings."""
    ids = list(embeddings.keys())
    matrix = np.stack([embeddings[nid] for nid in ids])
    logger.info("Building NN index: %d points x %d dims", matrix.shape[0], matrix.shape[1])
    t0 = time.time()
    nn = NearestNeighbors(
        n_neighbors=min(n_neighbors, len(ids)),
        metric="cosine", algorithm="brute", n_jobs=-1,
    )
    nn.fit(matrix)
    logger.info("NN index built in %.1fs", time.time() - t0)
    return nn, ids, matrix


def compute_all_neighbors(
    nn: NearestNeighbors,
    ids: List[int],
    matrix: np.ndarray,
    threshold: float = DEFAULT_EMBEDDING_SIM_THRESHOLD,
) -> Dict[int, Set[int]]:
    """Pre-compute ALL semantic neighbors for ALL articles at once.

    Returns {article_id: {neighbor_id, ...}} — one kneighbors call for the
    entire matrix instead of N individual calls.
    """
    logger.info("Computing all-neighbors matrix (N=%d, k=%d)...",
                len(ids), nn.n_neighbors)
    t0 = time.time()
    distances, indices = nn.kneighbors(matrix, return_distance=True)

    neighbor_map: Dict[int, Set[int]] = {}
    for i, nid in enumerate(ids):
        nbr_set: Set[int] = set()
        for dist, nbr_idx in zip(distances[i], indices[i]):
            sim = 1.0 - dist
            if sim >= threshold:
                nbr_id = ids[nbr_idx]
                if nbr_id != nid:
                    nbr_set.add(nbr_id)
        neighbor_map[nid] = nbr_set

    elapsed = time.time() - t0
    logger.info("All-neighbors computed in %.1fs (avg %.1f neighbors/article)",
                elapsed, sum(len(v) for v in neighbor_map.values()) / max(len(neighbor_map), 1))
    return neighbor_map


_ENTITY_STOP_WORDS = frozenset((
    "the", "and", "for", "its", "not", "all", "but", "are", "has",
    "president", "prime", "minister", "secretary", "general",
    "chancellor", "ambassador", "chairman", "spokesperson",
    "governor", "senator", "mayor", "admiral", "colonel",
))
# Cyrillic lowercase range (а–я + ё)
_CYRILLIC = "а-яё"
# CJK Unified Ideographs + Extension A + Compatibility Ideographs
_CJK = "一-鿿㐀-䶿豈-﫿"


def _entity_core_word(name: str | None) -> str | None:
    """Extract the core identifying word from an entity name.

    'Donald Trump' → 'trump', 'President Xi Jinping' → 'jinping',
    'Xi' → 'xi', 'US' → 'us', 'China' → 'china',
    '习近平' → '近平', '俄罗斯' → '罗斯', 'Совет ЕС' → 'ес'.
    Returns None if no meaningful word can be extracted.
    """
    if not name:
        return None
    text = name.lower().rstrip('., ')

    # Latin words (2+ chars)
    latin = re.findall(r"[a-z]{2,}", text)
    for w in reversed(latin):
        if w not in _ENTITY_STOP_WORDS:
            return w
    if latin:
        return latin[-1]

    # Cyrillic words (2+ chars)
    cyr = re.findall(f"[{_CYRILLIC}]{{2,}}", text)
    if cyr:
        return cyr[-1]

    # CJK — take last 2 characters of the last CJK sequence
    # (single CJK chars like 中/美/伊 are too common to discriminate well)
    cjk_seqs = re.findall(f"[{_CJK}]+", text)
    if cjk_seqs:
        last = cjk_seqs[-1]
        return last[-2:] if len(last) >= 2 else last

    return None


def _entity_last_word(name1: str | None, name2: str | None) -> bool:
    """Check if two entity names share the same core word (last non-stop word).

    'Donald Trump' and 'President Trump' → both end with 'trump' → True.
    'China' and 'Xi Jinping' → 'china' vs 'jinping' → False.
    """
    c1 = _entity_core_word(name1)
    c2 = _entity_core_word(name2)
    return c1 is not None and c2 is not None and c1 == c2


def _embedding_time_window(event_type: str) -> int:
    """Edge-level time window tuned for event-level clustering."""
    if event_type in (
        "military",
        "protest_repression",
        "terrorism_espionage",
        "military_security",
        "civil_unrest",
        "security_crime",
    ):
        return 1
    if event_type in (
        "diplomacy",
        "trade_conflict",
        "policy_legal",
        "appointment_leadership",
        "economic_trade",
        "technology_industry",
        "domestic_politics",
        "law_policy",
        "public_development",
    ):
        return 2
    return 3


def _cluster_time_window(event_type: str) -> int:
    """Cluster-level span window; slightly stricter than the edge window."""
    if event_type in (
        "military",
        "protest_repression",
        "terrorism_espionage",
        "military_security",
        "civil_unrest",
        "security_crime",
    ):
        return 1
    if event_type in (
        "diplomacy",
        "trade_conflict",
        "policy_legal",
        "appointment_leadership",
        "economic_trade",
        "technology_industry",
        "domestic_politics",
        "law_policy",
        "public_development",
    ):
        return 2
    return 3


def _refinement_time_window(event_type: str) -> int:
    if event_type in (
        "military",
        "protest_repression",
        "terrorism_espionage",
        "military_security",
        "civil_unrest",
        "security_crime",
    ):
        return 1
    if event_type in (
        "diplomacy",
        "trade_conflict",
        "policy_legal",
        "appointment_leadership",
        "economic_trade",
        "technology_industry",
        "domestic_politics",
        "law_policy",
        "public_development",
    ):
        return 2
    return 2


def _refinement_similarity_threshold(event_type: str) -> float:
    if event_type in (
        "military",
        "trade_conflict",
        "protest_repression",
        "terrorism_espionage",
        "military_security",
        "economic_trade",
        "civil_unrest",
        "security_crime",
    ):
        return 0.93
    if event_type in (
        "diplomacy",
        "policy_legal",
        "appointment_leadership",
        "technology_industry",
        "domestic_politics",
        "law_policy",
        "public_development",
    ):
        return 0.92
    return 0.915


def _headline_text(
    article_id: int,
    article_titles: Optional[Dict[int, str]] = None,
    article_bodies: Optional[Dict[int, str]] = None,
) -> str:
    title = (article_titles or {}).get(article_id, "").strip()
    if title:
        return title
    body = (article_bodies or {}).get(article_id, "").strip()
    if not body:
        return ""
    head = re.split(r"(?<=[.!?。！？])\s+|\n+", body, maxsplit=1)[0].strip()
    return head[:180]


def _normalize_headline(text: str) -> str:
    return " ".join(re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower()).split())


def _headline_tokens(text: str) -> Set[str]:
    normalized = _normalize_headline(text)
    if not normalized:
        return set()

    tokens: Set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.add(part)
            else:
                tokens.update(part[i:i + 2] for i in range(len(part) - 1))
        elif len(part) >= 3:
            tokens.add(part)
    return tokens


def _headline_ngrams(text: str, n: int = 4) -> Set[str]:
    compact = _normalize_headline(text).replace(" ", "")
    if not compact:
        return set()
    if len(compact) <= n:
        return {compact}
    return {compact[i:i + n] for i in range(len(compact) - n + 1)}


def _set_jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _headline_similarity(text1: str, text2: str) -> float:
    norm1 = _normalize_headline(text1)
    norm2 = _normalize_headline(text2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0
    return max(
        _set_jaccard(_headline_tokens(text1), _headline_tokens(text2)),
        _set_jaccard(_headline_ngrams(text1), _headline_ngrams(text2)),
    )


def split_overlong_clusters(
    clusters: Dict[str, List[int]],
    results_lookup: Dict[int, ExtractionResult],
) -> Dict[str, List[int]]:
    """Post-process: split clusters whose date range exceeds event type's time window.

    UnionFind enforces time constraints only at the edge level (each adjacent
    pair is within the window), but transitive chaining can produce a cluster
    spanning far longer (A↔B 2d, B↔C 2d → A↔C 4d beyond a 3d window).

    This enforces the time window at the *cluster level*: if the span from
    earliest to latest article exceeds the event type's window, the cluster
    is greedily split into sub-clusters each within the window.
    """
    new_clusters: Dict[str, List[int]] = {}
    split_count = 0

    for root, article_ids in clusters.items():
        if len(article_ids) <= 1:
            new_clusters[root] = article_ids
            continue

        # Get articles with dates
        dated = []
        for aid in article_ids:
            r = results_lookup.get(aid)
            if r and r.published_at:
                dt = _parse_dt(r.published_at)
                if dt:
                    dated.append((aid, dt))

        if len(dated) <= 1:
            new_clusters[root] = article_ids
            continue

        # Sort by date
        dated.sort(key=lambda x: x[1])

        # Determine dominant event type's time window
        types = Counter()
        for aid in article_ids:
            r = results_lookup.get(aid)
            if r and r.event:
                types[r.event.event_type] += 1
        dominant_type = types.most_common(1)[0][0] if types else "other"
        time_window = _cluster_time_window(dominant_type)

        # Check if cluster already fits in time window
        total_span = (dated[-1][1] - dated[0][1]).total_seconds() / 86400
        if total_span <= time_window:
            new_clusters[root] = article_ids
            continue

        # Greedy split: start a group from earliest article, add subsequent
        # articles while they fall within time_window of the group's start.
        groups = []
        current_group = [dated[0]]
        group_start_dt = dated[0][1]

        for aid, dt in dated[1:]:
            days_from_start = (dt - group_start_dt).total_seconds() / 86400
            if days_from_start <= time_window:
                current_group.append((aid, dt))
            else:
                groups.append([a for a, _ in current_group])
                current_group = [(aid, dt)]
                group_start_dt = dt

        if current_group:
            groups.append([a for a, _ in current_group])

        for i, group in enumerate(groups):
            if i == 0:
                new_clusters[root] = sorted(group)
            else:
                new_key = f"{root}_s{i}"
                new_clusters[new_key] = sorted(group)

        split_count += 1

    if split_count:
        logger.info("Split %d overlong clusters (time-window enforcement at cluster level)", split_count)
    return new_clusters


def split_impure_clusters(
    clusters: Dict[str, List[int]],
    results_lookup: Dict[int, ExtractionResult],
    max_entity_pairs: int = 4,
) -> Dict[str, List[int]]:
    """Post-process: split clusters with too many distinct entity pairs.

    Large clusters often become garbage bins containing many unrelated
    entity pairs (e.g., all diplomatic meetings in a 2-week window bridged
    through one-side entity matches).  This splits them by entity core
    pair so each sub-cluster represents events sharing the same
    (initiator, target) pair.  Small clusters with few entity pairs are
    left untouched.
    """
    new_clusters: Dict[str, List[int]] = {}
    split_count = 0

    for root, article_ids in clusters.items():
        if len(article_ids) <= max_entity_pairs:
            new_clusters[root] = article_ids
            continue

        pair_groups: Dict[str, List[int]] = defaultdict(list)
        for aid in article_ids:
            r = results_lookup.get(aid)
            if r and r.event:
                pair_key = entity_pair_key(r.event.initiator or "", r.event.target or "") or "→"
                pair_groups[pair_key].append(aid)
            else:
                pair_groups["→"].append(aid)

        dominant_size = max((len(members) for members in pair_groups.values()), default=0)
        dominant_share = dominant_size / max(len(article_ids), 1)
        pair_count = len(pair_groups)
        should_split = False
        if len(article_ids) >= 12:
            should_split = dominant_share < 0.65 or pair_count >= max_entity_pairs + 1
        elif len(article_ids) >= 6:
            should_split = dominant_share < 0.75 or pair_count >= max_entity_pairs

        if should_split:
            for i, (_, members) in enumerate(pair_groups.items()):
                key = f"{root}_e{i}" if i > 0 else root
                new_clusters[key] = sorted(members)
            split_count += 1
        else:
            new_clusters[root] = article_ids

    if split_count:
        logger.info("Split %d impure clusters (entity-pair diversity > %d)",
                    split_count, max_entity_pairs)
    return new_clusters


def split_broad_semantic_clusters(
    clusters: Dict[str, List[int]],
    results_lookup: Dict[int, ExtractionResult],
    embeddings: Optional[Dict[int, np.ndarray]],
    *,
    article_titles: Optional[Dict[int, str]] = None,
    article_bodies: Optional[Dict[int, str]] = None,
    min_cluster_size: int = 6,
) -> Dict[str, List[int]]:
    """Split oversized storyline-like clusters back into event-level components."""
    if not embeddings:
        return clusters

    new_clusters: Dict[str, List[int]] = {}
    split_count = 0

    for root, article_ids in clusters.items():
        if len(article_ids) < min_cluster_size:
            new_clusters[root] = article_ids
            continue

        rows = []
        event_types = Counter()
        entity_pairs = Counter()
        for aid in article_ids:
            emb = embeddings.get(aid)
            row = results_lookup.get(aid)
            if emb is None or row is None or row.event is None:
                continue
            dt = _parse_dt(row.published_at)
            if dt is None:
                continue
            event_types[row.event.event_type] += 1
            entity_pairs[entity_pair_key(row.event.initiator or "", row.event.target or "")] += 1
            rows.append((aid, row, emb, dt))

        if len(rows) < min_cluster_size:
            new_clusters[root] = article_ids
            continue

        dominant_type = event_types.most_common(1)[0][0] if event_types else "other"
        span_days = (max(dt for _, _, _, dt in rows) - min(dt for _, _, _, dt in rows)).days
        if span_days <= _cluster_time_window(dominant_type) and len(entity_pairs) <= 2:
            new_clusters[root] = article_ids
            continue

        rows.sort(key=lambda item: (item[3], item[0]))
        pair_window = _refinement_time_window(dominant_type)
        min_cosine = _refinement_similarity_threshold(dominant_type)
        local_uf = UnionFind([str(aid) for aid, _, _, _ in rows])

        for i, (aid1, row1, emb1, dt1) in enumerate(rows):
            title1 = _headline_text(aid1, article_titles, article_bodies)
            pair1 = entity_pair_key(row1.event.initiator or "", row1.event.target or "")
            trig1 = getattr(row1.event, "trigger_verb", None) or row1.event.trigger
            loc1 = getattr(row1.event, "location", None)
            for aid2, row2, emb2, dt2 in rows[i + 1:]:
                day_gap = abs((dt1 - dt2).days)
                if day_gap > pair_window:
                    if dt2 > dt1:
                        break
                    continue

                cosine = float(np.dot(emb1, emb2))
                if cosine < min_cosine:
                    continue

                title2 = _headline_text(aid2, article_titles, article_bodies)
                pair2 = entity_pair_key(row2.event.initiator or "", row2.event.target or "")
                trig2 = getattr(row2.event, "trigger_verb", None) or row2.event.trigger

                title_sim = _headline_similarity(title1, title2)
                trigger_sim = _trigger_similarity(trig1, trig2)
                same_pair = pair1 == pair2 and pair1 != "→"
                soft_entity = (
                    _entity_similarity(row1.event.initiator, row2.event.initiator) >= 0.95
                    and _entity_similarity(row1.event.target, row2.event.target) >= 0.95
                )
                exact_duplicate_like = title_sim >= 0.24 or cosine >= 0.965

                loc2 = getattr(row2.event, "location", None)
                if loc1 and loc2:
                    loc1_c = _canonical_entity(loc1) or str(loc1).lower()
                    loc2_c = _canonical_entity(loc2) or str(loc2).lower()
                    loc_sim = 1.0 if loc1_c == loc2_c else _trigram_jaccard(loc1_c, loc2_c)
                    if loc_sim < 0.25 and title_sim < 0.24 and trigger_sim < 0.45:
                        continue

                if not same_pair and not soft_entity:
                    if title_sim < 0.20 and trigger_sim < 0.50:
                        continue
                else:
                    if not exact_duplicate_like and title_sim < 0.10 and trigger_sim < 0.38:
                        continue

                if not _polarity_compatible(trig1, trig2):
                    continue

                local_uf.union(str(aid1), str(aid2))

        grouped: Dict[str, List[int]] = defaultdict(list)
        for aid, _, _, _ in rows:
            grouped[local_uf.find(str(aid))].append(aid)

        if len(grouped) <= 1:
            new_clusters[root] = sorted(article_ids)
            continue

        ordered_groups = sorted(
            (sorted(members) for members in grouped.values()),
            key=lambda members: (-len(members), members[0]),
        )
        for i, members in enumerate(ordered_groups):
            key = root if i == 0 else f"{root}_r{i}"
            new_clusters[key] = members
        split_count += 1

    if split_count:
        logger.info("Split %d broad clusters via semantic refinement", split_count)
    return new_clusters


def build_event_coreference_with_embeddings(
    results: List[ExtractionResult],
    article_bodies: Optional[Dict[int, str]] = None,
    article_titles: Optional[Dict[int, str]] = None,
    neighbor_map: Optional[Dict[int, Set[int]]] = None,
    embeddings: Optional[Dict[int, np.ndarray]] = None,
) -> Dict[str, List[int]]:
    """Build event coreference clusters with BGE-M3 embedding similarity.

    Replaces trigger similarity (always 1.0 for v11 template strings)
    with BGE-M3 embedding cosine similarity, constrained within
    entity-pair groups and time windows to avoid language bias.

    Path 1 — embeddings provided: BGE-M3 cosine similarity replaces
    trigger similarity entirely. FAISS neighbor index is built to
    enable cross-entity-pair singleton rescue.

    Path 2 — neighbor_map provided: embedding pre-filter via mutual NN
    before trigger similarity check (hybrid mode).

    Args:
        results: Extracted events.
        article_bodies: Optional body text for quality filtering.
        neighbor_map: Optional pre-computed {article_id: {neighbor_id, ...}}
                      for fast-path pre-filtering.
        embeddings: Optional {article_id: np.ndarray} of L2-normalized
                    BGE-M3 embeddings.

    Returns: {cluster_id: [article_id, ...]}
    """
    if not _HAS_SKLEARN and neighbor_map is None and embeddings is None:
        raise RuntimeError(
            "build_event_coreference_with_embeddings requires either "
            "sklearn, neighbor_map, or embeddings. The trigger-based "
            "fallback was removed."
        )

    t0 = time.time()

    # ── Filter valid events ──
    valid: List[ExtractionResult] = [r for r in results if r.event and r.parse_success]
    n_total = len(results)

    # ── Filter low-quality article bodies ──
    if article_bodies is not None:
        before = len(valid)
        valid = [r for r in valid if not _is_low_quality_body(article_bodies.get(r.article_id))]
        n_removed = before - len(valid)
        if n_removed:
            logger.info("Low-quality body filter: removed %d/%d articles (%.1f%%)",
                        n_removed, before, 100 * n_removed / max(before, 1))

        before2 = len(valid)
        valid = [r for r in valid if _extraction_matches_body(r.event, article_bodies.get(r.article_id, ""))]
        n_sidebar = before2 - len(valid)
        if n_sidebar:
            logger.info("Extraction-body mismatch: removed %d/%d articles (%.1f%%)",
                        n_sidebar, before2, 100 * n_sidebar / max(before2, 1))

    n_valid = len(valid)
    logger.info("Event coreference (embedding): %d/%d valid events", n_valid, n_total)
    if n_valid == 0:
        return {}

    # ── Build FAISS neighbor index ──
    # Pre-compute nearest neighbors for all articles. Used as the primary
    # candidate source for coreference linking (replaces hard entity-pair grouping).
    _neighbor_list: Optional[Dict[int, List[Tuple[int, float]]]] = None
    if embeddings and (_HAS_FAISS or _HAS_SKLEARN):
        _all_ids_list = list(embeddings.keys())
        _matrix = np.stack([embeddings[nid] for nid in _all_ids_list]).astype(np.float32)
        N_NEIGHBORS = min(50, len(_all_ids_list))
        t_nn = time.time()

        if _HAS_FAISS:
            logger.info("Building FAISS neighbor index (%d x %d)... (this takes ~2 min)",
                        len(_all_ids_list), N_NEIGHBORS)
            index = faiss.IndexFlatIP(int(_matrix.shape[1]))
            index.add(_matrix)
            _distances, _indices = index.search(_matrix, N_NEIGHBORS)
            logger.info("FAISS neighbor index: %d x %d in %.1fs",
                        len(_all_ids_list), N_NEIGHBORS, time.time() - t_nn)
            # IndexFlatIP on L2-normalized vectors → inner product = cosine
            _neighbor_list = {
                nid: [(_all_ids_list[idx], float(_distances[i][k]))
                      for k, idx in enumerate(_indices[i])
                      if _all_ids_list[idx] != nid]
                for i, nid in enumerate(_all_ids_list)
            }
        else:
            _nn = NearestNeighbors(
                n_neighbors=N_NEIGHBORS,
                metric="cosine", algorithm="brute", n_jobs=-1,
            )
            _nn.fit(_matrix)
            _distances, _indices = _nn.kneighbors(_matrix)
            logger.info("Sklearn neighbor index: %d x %d in %.2fs",
                        len(_all_ids_list), N_NEIGHBORS, time.time() - t_nn)
            # sklearn metric="cosine" returns cosine distance = 1 - cosine
            _neighbor_list = {
                nid: [(_all_ids_list[idx], float(1.0 - _distances[i][k]))
                      for k, idx in enumerate(_indices[i])
                      if _all_ids_list[idx] != nid]
                for i, nid in enumerate(_all_ids_list)
            }

    # ── Partition by event_type (keep original strict types, NO aliasing) ──
    by_type: Dict[str, List[ExtractionResult]] = defaultdict(list)
    for r in valid:
        by_type[r.event.event_type].append(r)

    logger.info("Event type partitions (original types): %s",
                {et: len(v) for et, v in sorted(by_type.items(), key=lambda x: -len(x[1]))[:10]})

    all_edges: List[Tuple[int, int, float]] = []
    edge_stats_by_type: Dict[str, int] = Counter()

    # ── Coreference via FAISS neighbor similarity ──
    # Each article uses its top-N BGE-M3 neighbors as candidate links,
    # filtered by same event_type, time window, and polarity.
    # No hard entity-pair grouping — embedding similarity itself
    # handles soft entity matching (e.g. "US → Iran" ~ "USA → Iran").
    for event_type, group in by_type.items():
        time_window = _embedding_time_window(event_type)
        group_ids: Set[int] = {r.article_id for r in group}
        group_lookup: Dict[int, ExtractionResult] = {r.article_id: r for r in group}
        group_edge_count = 0

        for r1 in group:
            n1 = r1.article_id
            nbrs = _neighbor_list.get(n1, []) if _neighbor_list is not None else []
            source_edges: List[Tuple[int, float]] = []
            source_pair_buckets: Set[str] = set()

            for nbr_id, cos in nbrs:
                # Dedup: only process each pair once (lower id → higher id)
                if nbr_id <= n1 or nbr_id not in group_ids:
                    continue

                r2 = group_lookup[nbr_id]

                # Time window (strict: 7-14 days)
                dt1 = _parse_dt(r1.published_at)
                dt2 = _parse_dt(r2.published_at)
                if dt1 and dt2:
                    if abs((dt1 - dt2).total_seconds()) / 86400 > time_window:
                        continue

                # Mutual NN: prevent transitive chaining (A↔B, B↔C ≠ A↔C)
                if _neighbor_list is not None:
                    nbr_nbrs = _neighbor_list.get(nbr_id, [])
                    if not any(nn_id == n1 for nn_id, _ in nbr_nbrs):
                        continue

                # Tone-based polarity check (v12)
                _t1 = getattr(r1.event, 'tone', 'neutral')
                _t2 = getattr(r2.event, 'tone', 'neutral')
                if _t1 != _t2 and _t1 in ('positive', 'negative') and _t2 in ('positive', 'negative'):
                    continue

                # ── Multi-signal weighted fusion (Route B+v12) ──────────
                _init_sim = _entity_similarity(r1.event.initiator, r2.event.initiator)
                _tgt_sim = _entity_similarity(r1.event.target, r2.event.target)
                _entity_score = (_init_sim + _tgt_sim) / 2.0

                # Swapped-direction boost
                _init_tgt_sim = _entity_similarity(r1.event.initiator, r2.event.target)
                _tgt_init_sim = _entity_similarity(r1.event.target, r2.event.initiator)
                if _init_tgt_sim > 0.8 and _tgt_init_sim > 0.8:
                    _entity_score = max(_entity_score, 0.9)

                _time_score = _time_proximity(dt1, dt2, time_window)

                # Trigger verb similarity (v12)
                _tv1 = getattr(r1.event, 'trigger_verb', None) or r1.event.trigger
                _tv2 = getattr(r2.event, 'trigger_verb', None) or r2.event.trigger
                from core_pipeline.event_extract_v11 import _DEFAULT_TRIGGERS as _DT
                _is_template1 = _tv1 in set(_DT.values())
                _is_template2 = _tv2 in set(_DT.values())
                if _is_template1 or _is_template2:
                    _trigger_score = 0.5
                else:
                    _trigger_score = _trigger_similarity(_tv1, _tv2)

                # Location similarity (v12)
                _loc1 = getattr(r1.event, 'location', None)
                _loc2 = getattr(r2.event, 'location', None)
                if _loc1 and _loc2:
                    _loc1_c = _canonical_entity(_loc1) or _loc1.lower()
                    _loc2_c = _canonical_entity(_loc2) or _loc2.lower()
                    _location_score = 1.0 if _loc1_c == _loc2_c else _trigram_jaccard(_loc1_c, _loc2_c)
                else:
                    _location_score = 0.5

                _fusion_score = (
                    FUSION_WEIGHTS["bge"] * cos
                    + FUSION_WEIGHTS["entity"] * _entity_score
                    + FUSION_WEIGHTS["time"] * _time_score
                    + FUSION_WEIGHTS.get("trigger", 0.10) * _trigger_score
                    + FUSION_WEIGHTS.get("location", 0.05) * _location_score
                )

                # Adaptive threshold based on entity+trigger alignment
                _alignment = (_entity_score + _trigger_score) / 2.0
                if _alignment >= 0.8:
                    _adaptive_thresh = 0.70
                elif _alignment >= 0.3:
                    _adaptive_thresh = 0.75
                else:
                    _adaptive_thresh = 0.80

                pair1 = entity_pair_key(r1.event.initiator or "", r1.event.target or "")
                pair2 = entity_pair_key(r2.event.initiator or "", r2.event.target or "")
                exact_pair_match = pair1 == pair2 and pair1 != "→"
                if not exact_pair_match:
                    _adaptive_thresh = max(_adaptive_thresh, 0.80)
                    if _trigger_score < 0.25 and cos < 0.90:
                        continue

                if _fusion_score < _adaptive_thresh:
                    continue

                edge_bucket = pair2 or "→"
                if edge_bucket in source_pair_buckets and len(source_edges) >= 2:
                    continue
                source_edges.append((nbr_id, _fusion_score))
                source_pair_buckets.add(edge_bucket)

            if source_edges:
                source_edges.sort(key=lambda item: (-item[1], item[0]))
                for nbr_id, score in source_edges[:3]:
                    all_edges.append((n1, nbr_id, score))
                    group_edge_count += 1

        edge_stats_by_type[event_type] += group_edge_count

    # ── UnionFind ──
    all_id_ints: Set[int] = set()
    for r in valid:
        all_id_ints.add(r.article_id)
    all_id_strs = [str(a) for a in all_id_ints]
    uf = UnionFind(all_id_strs)
    id_to_int = {str(a): a for a in all_id_ints}

    for n1, n2, _sim in all_edges:
        s1, s2 = str(n1), str(n2)
        if uf.find(s1) != uf.find(s2):
            uf.union(s1, s2)

    elapsed = time.time() - t0
    logger.info("Coreference graph: %d edges across %d types in %.1fs",
                len(all_edges), len(by_type), elapsed)

    # ── Build cluster dict ──
    cluster_map: Dict[str, List[int]] = defaultdict(list)
    for nid_s in all_id_strs:
        root = uf.find(nid_s)
        cluster_map[root].append(id_to_int[nid_s])

    result: Dict[str, List[int]] = {}
    for root_s, members in cluster_map.items():
        result[root_s] = sorted(members)

    # ── Post-process: enforce time window at cluster level ──
    result = split_overlong_clusters(result, {r.article_id: r for r in valid})

    # ── Post-process: split clusters with too many distinct entity pairs ──
    result = split_impure_clusters(result, {r.article_id: r for r in valid}, max_entity_pairs=4)

    # ── Post-process: split broad storyline-like clusters into event shards ──
    result = split_broad_semantic_clusters(
        result,
        {r.article_id: r for r in valid},
        embeddings,
        article_titles=article_titles,
        article_bodies=article_bodies,
    )

    n_singletons = sum(1 for v in result.values() if len(v) == 1)
    logger.info("Event clusters (embedding): %d (%d non-singleton, %.1f%%)",
                len(result), len(result) - n_singletons,
                100 * (len(result) - n_singletons) / max(len(result), 1))

    return result


def print_cluster_report(
    clusters: Dict[str, List[int]],
    results: List[ExtractionResult],
):
    """Print clustering quality report."""
    result_map = {r.article_id: r for r in results if r.event}

    print(f"\n{'='*60}")
    print("EVENT COREFERENCE CLUSTERING REPORT (v2)")
    print('=' * 60)

    n_clusters = len(clusters)
    n_articles = sum(len(v) for v in clusters.values())
    n_singletons = sum(1 for v in clusters.values() if len(v) == 1)
    n_no_event = sum(1 for r in results if not r.parse_success)

    print(f"\nArticles with event: {n_articles}")
    print(f"Articles no event:  {n_no_event}")
    print(f"Total clusters:     {n_clusters}")
    print(f"Singletons:         {n_singletons} ({100*n_singletons//max(n_clusters,1)}%)")

    # Size distribution
    size_dist = Counter()
    for aids in clusters.values():
        sz = len(aids)
        bucket = 1 if sz == 1 else 2 if sz <= 2 else 3 if sz <= 3 else 5 if sz <= 5 else 10 if sz <= 10 else 20 if sz <= 20 else 50 if sz <= 50 else 100 if sz <= 100 else 999
        size_dist[bucket] += 1

    print(f"\nCluster size distribution:")
    for b in [1, 2, 3, 5, 10, 20, 50, 100, 999]:
        label = {1: "1", 2: "2", 3: "3", 5: "4-5", 10: "6-10", 20: "11-20", 50: "21-50", 100: "51-100", 999: "100+"}
        if b in size_dist:
            print(f"  {label[b]:>8}: {size_dist[b]}")

    # Per event-type stats
    type_stats = Counter()
    for aids in clusters.values():
        for aid in aids:
            r = result_map.get(aid)
            if r and r.event:
                type_stats[r.event.event_type] += 1

    print(f"\nTop event types by article count:")
    for et, count in type_stats.most_common(10):
        print(f"  {et:30s}: {count:>4}")

    # Sample largest clusters with quality check
    sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))
    print(f"\n── Quality check: Top 15 clusters ──")
    for cid, aids in sorted_clusters[:15]:
        if len(aids) < 2:
            continue
        initiators = set()
        targets = set()
        triggers = set()
        times = []
        for aid in aids:
            r = result_map.get(aid)
            if r and r.event:
                initiators.add(str(r.event.initiator or '')[:30])
                targets.add(str(r.event.target or '')[:30])
                triggers.add(r.event.trigger)
                dt = _parse_dt(r.published_at)
                if dt:
                    times.append(str(dt.date()))

        consistent = len(initiators) == 1 and len(targets) == 1
        date_range = f"{times[0]}~{times[-1]}" if len(times) > 1 else (times[0] if times else "?")
        main_trigger = Counter(triggers).most_common(1)[0][0] if triggers else "?"

        marker = "✓" if consistent else "⚠"
        print(f"\n  [{marker}] Cluster {cid} ({len(aids):>3} arts, {date_range})")
        if consistent:
            print(f"    trigger: {main_trigger[:60]}")
        else:
            print(f"    ⚠ initiators: {list(initiators)[:3]}")
            print(f"    ⚠ targets:    {list(targets)[:3]}")
            print(f"    trigger: {main_trigger[:60]}")

        for aid in aids[:5]:
            r = result_map.get(aid)
            dt = _parse_dt(getattr(r, 'published_at', None))
            dts = str(dt.date()) if dt else "?"
            e = r.event if r and r.event else None
            if e:
                init = (e.initiator or '?')[:25]
                targ = (e.target or '?')[:25]
                print(f"    [{dts}] {init:26s} → {targ:26s} | {e.trigger[:40]}")
            else:
                print(f"    [{dts}] <no event>")

    print()
