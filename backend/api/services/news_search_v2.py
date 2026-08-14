import json
import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from api.core.db import SQLALCHEMY_DATABASE_URL, engine
from api.features.search import (
    ENTITY_ALIAS_CATALOG_VERSION,
    QUERY_LANGUAGE_VERSION,
    QUERY_LIMITS,
    QueryNode,
    build_query_receipt,
    build_search_hit_disclosure,
    entity_alias_variants,
    iter_query_leaves,
    parse_supported_query,
    primary_query_text,
    render_query_ast,
    resolve_entity_alias,
)
from api.models.schemas import (
    ClusterTreeMacro,
    ClusterTreeMicro,
    ClusterTreeNews,
    EventCorefClusterInfo,
    MacroEventItem,
    NewsItem,
    NewsListResponse,
    NewsResultTimeSemantics,
    SearchEntityExpansion,
    SearchExplainStage,
    SearchHitDisclosure,
    SearchQueryExplain,
    SearchResponse,
    SearchTimeSemantics,
)
from api.services.helpers import get_user_favorite_sets_for_scope

NEWS_DATABASE_URL = SQLALCHEMY_DATABASE_URL
QUALITY_LABEL_VERSION = "quality_v1_20260629"
SEARCH_DEADLINE_SECONDS = float(QUERY_LIMITS["statement_timeout_seconds"])
FUZZY_TITLE_CANDIDATES_PER_TERM = 500
BOOLEAN_TITLE_CANDIDATES_PER_TERM = int(
    QUERY_LIMITS["max_title_candidates_per_positive_leaf"]
)
FUZZY_LITERAL_TITLE_QUERIES = frozenset({"control", "taiwan"})


class SearchDeadlineExceeded(RuntimeError):
    """Raised when a dashboard search exhausts its database time budget."""


_SEARCH_DEADLINE: ContextVar[Optional[float]] = ContextVar(
    "globemind_search_deadline",
    default=None,
)

NEWS_ENGINE = engine


@contextmanager
def _search_budget(started_at: float):
    token = _SEARCH_DEADLINE.set(started_at + SEARCH_DEADLINE_SECONDS)
    try:
        yield
    finally:
        _SEARCH_DEADLINE.reset(token)


@contextmanager
def _search_connection():
    deadline = _SEARCH_DEADLINE.get() or (time.time() + SEARCH_DEADLINE_SECONDS)
    remaining_ms = int((deadline - time.time()) * 1000)
    if remaining_ms <= 50:
        raise SearchDeadlineExceeded("搜索已达到 6 秒上限")
    try:
        with NEWS_ENGINE.connect() as conn:
            conn.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{remaining_ms}ms"},
            )
            yield conn
    except DBAPIError as exc:
        if getattr(exc.orig, "pgcode", None) == "57014":
            raise SearchDeadlineExceeded("搜索已达到 6 秒上限") from exc
        raise


LANG_LABELS = {
    "ar": "阿拉伯语",
    "bn": "孟加拉语",
    "cs": "捷克语",
    "da": "丹麦语",
    "de": "德语",
    "el": "希腊语",
    "en": "英语",
    "es": "西班牙语",
    "fa": "波斯语",
    "fi": "芬兰语",
    "fr": "法语",
    "gu": "古吉拉特语",
    "he": "希伯来语",
    "hi": "印地语",
    "hu": "匈牙利语",
    "id": "印尼语",
    "it": "意大利语",
    "ja": "日语",
    "ko": "韩语",
    "ms": "马来语",
    "nl": "荷兰语",
    "no": "挪威语",
    "pl": "波兰语",
    "pt": "葡萄牙语",
    "ro": "罗马尼亚语",
    "ru": "俄语",
    "sv": "瑞典语",
    "sw": "斯瓦希里语",
    "ta": "泰米尔语",
    "th": "泰语",
    "tr": "土耳其语",
    "uk": "乌克兰语",
    "ur": "乌尔都语",
    "vi": "越南语",
    "zh": "中文",
}

LANG_ALIASES = {
    "中文": "zh",
    "汉语": "zh",
    "英语": "en",
    "英文": "en",
    "俄语": "ru",
    "阿拉伯语": "ar",
    "法语": "fr",
    "德语": "de",
    "西班牙语": "es",
    "葡萄牙语": "pt",
    "日语": "ja",
    "韩语": "ko",
    "土耳其语": "tr",
    "波斯语": "fa",
    "孟加拉语": "bn",
    "古吉拉特语": "gu",
    "泰米尔语": "ta",
    "泰语": "th",
    "乌尔都语": "ur",
}


QUERY_ALIAS_MAP = {
    "中国": ("中国", "中國", "china", "chinese", "beijing", "prc"),
    "中國": ("中国", "中國", "china", "chinese", "beijing", "prc"),
    "china": ("china", "chinese", "中国", "中國", "beijing", "prc"),
    "芯片": (
        "芯片",
        "晶片",
        "半导体",
        "半導體",
        "集成电路",
        "積體電路",
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "microchip",
        "microchips",
        "integrated circuit",
        "wafer",
        "wafers",
        "foundry",
        "lithography",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "晶片": (
        "晶片",
        "芯片",
        "半导体",
        "半導體",
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "wafer",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "半导体": (
        "半导体",
        "半導體",
        "芯片",
        "晶片",
        "集成电路",
        "積體電路",
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "wafer",
        "foundry",
        "lithography",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "半導體": (
        "半导体",
        "半導體",
        "芯片",
        "晶片",
        "集成电路",
        "積體電路",
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "wafer",
        "foundry",
        "lithography",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "semiconductor": (
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "microchip",
        "integrated circuit",
        "wafer",
        "foundry",
        "lithography",
        "芯片",
        "晶片",
        "半导体",
        "半導體",
        "集成电路",
        "積體電路",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "semiconductors": (
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "microchip",
        "integrated circuit",
        "wafer",
        "foundry",
        "lithography",
        "芯片",
        "晶片",
        "半导体",
        "半導體",
        "集成电路",
        "積體電路",
        "tsmc",
        "nvidia",
        "gpu",
    ),
    "chip": (
        "chip",
        "chips",
        "microchip",
        "microchips",
        "semiconductor",
        "semiconductors",
        "integrated circuit",
        "wafer",
        "foundry",
        "芯片",
        "晶片",
        "半导体",
        "半導體",
    ),
    "chips": (
        "chip",
        "chips",
        "microchip",
        "microchips",
        "semiconductor",
        "semiconductors",
        "integrated circuit",
        "wafer",
        "foundry",
        "芯片",
        "晶片",
        "半导体",
        "半導體",
    ),
    "美国": ("美国", "美國", "united states", "u.s.", "us", "usa", "america", "american", "washington"),
    "美國": ("美国", "美國", "united states", "u.s.", "us", "usa", "america", "american", "washington"),
    "united states": ("united states", "u.s.", "us", "usa", "america", "american", "美国", "美國"),
    "usa": ("usa", "united states", "u.s.", "us", "america", "american", "美国", "美國"),
    "俄罗斯": ("俄罗斯", "俄羅斯", "russia", "russian", "moscow"),
    "俄羅斯": ("俄罗斯", "俄羅斯", "russia", "russian", "moscow"),
    "russia": ("russia", "russian", "俄罗斯", "俄羅斯", "moscow"),
    "乌克兰": ("乌克兰", "烏克蘭", "ukraine", "ukrainian", "kyiv", "kiev"),
    "烏克蘭": ("乌克兰", "烏克蘭", "ukraine", "ukrainian", "kyiv", "kiev"),
    "ukraine": ("ukraine", "ukrainian", "乌克兰", "烏克蘭", "kyiv", "kiev"),
    "日本": ("日本", "japan", "japanese", "tokyo"),
    "japan": ("japan", "japanese", "日本", "tokyo"),
    "韩国": ("韩国", "韓國", "南韩", "南韓", "south korea", "korea", "korean", "seoul"),
    "韓國": ("韩国", "韓國", "南韩", "南韓", "south korea", "korea", "korean", "seoul"),
    "south korea": ("south korea", "korea", "korean", "韩国", "韓國", "南韩", "南韓", "seoul"),
    "朝鲜": ("朝鲜", "朝鮮", "北韩", "北韓", "north korea", "pyongyang"),
    "朝鮮": ("朝鲜", "朝鮮", "北韩", "北韓", "north korea", "pyongyang"),
    "north korea": ("north korea", "朝鲜", "朝鮮", "北韩", "北韓", "pyongyang"),
    "台湾": ("台湾", "台灣", "taiwan", "taiwanese", "taipei"),
    "台灣": ("台湾", "台灣", "taiwan", "taiwanese", "taipei"),
    "taiwan": ("taiwan", "taiwanese", "台湾", "台灣", "taipei"),
    "南海": ("南海", "south china sea", "spratly", "paracel", "scarborough shoal", "west philippine sea", "太平岛", "仁爱礁", "黄岩岛"),
    "south china sea": ("south china sea", "南海", "spratly", "paracel", "scarborough shoal", "west philippine sea", "太平岛", "仁爱礁", "黄岩岛"),
    "印度": ("印度", "india", "indian", "new delhi"),
    "india": ("india", "indian", "印度", "new delhi"),
    "伊朗": ("伊朗", "iran", "iranian", "tehran"),
    "iran": ("iran", "iranian", "伊朗", "tehran"),
    "以色列": ("以色列", "israel", "israeli"),
    "israel": ("israel", "israeli", "以色列"),
    "巴勒斯坦": ("巴勒斯坦", "palestine", "palestinian", "gaza"),
    "palestine": ("palestine", "palestinian", "巴勒斯坦", "gaza"),
    "加沙": ("加沙", "gaza"),
    "gaza": ("gaza", "加沙"),
    "缅甸": ("缅甸", "緬甸", "myanmar", "burma"),
    "緬甸": ("缅甸", "緬甸", "myanmar", "burma"),
    "myanmar": ("myanmar", "burma", "缅甸", "緬甸"),
    "泰国": ("泰国", "泰國", "thailand", "thai", "bangkok"),
    "泰國": ("泰国", "泰國", "thailand", "thai", "bangkok"),
    "thailand": ("thailand", "thai", "泰国", "泰國", "bangkok"),
    "加拿大": ("加拿大", "canada", "canadian", "ottawa"),
    "canada": ("canada", "canadian", "加拿大", "ottawa"),
    "德国": ("德国", "德國", "germany", "german", "berlin"),
    "德國": ("德国", "德國", "germany", "german", "berlin"),
    "germany": ("germany", "german", "德国", "德國", "berlin"),
    "法国": ("法国", "法國", "france", "french", "paris"),
    "法國": ("法国", "法國", "france", "french", "paris"),
    "france": ("france", "french", "法国", "法國", "paris"),
    "英国": ("英国", "英國", "united kingdom", "uk", "britain", "british", "london"),
    "英國": ("英国", "英國", "united kingdom", "uk", "britain", "british", "london"),
    "united kingdom": ("united kingdom", "uk", "britain", "british", "英国", "英國", "london"),
    "意大利": ("意大利", "義大利", "italy", "italian", "rome"),
    "義大利": ("意大利", "義大利", "italy", "italian", "rome"),
    "italy": ("italy", "italian", "意大利", "義大利", "rome"),
    "菲律宾": ("菲律宾", "菲律賓", "philippines", "philippine", "manila"),
    "菲律賓": ("菲律宾", "菲律賓", "philippines", "philippine", "manila"),
    "philippines": ("philippines", "philippine", "菲律宾", "菲律賓", "manila"),
    "越南": ("越南", "vietnam", "vietnamese", "hanoi"),
    "vietnam": ("vietnam", "vietnamese", "越南", "hanoi"),
    "柬埔寨": ("柬埔寨", "cambodia", "cambodian", "phnom penh"),
    "cambodia": ("cambodia", "cambodian", "柬埔寨", "phnom penh"),
    "特朗普": ("特朗普", "川普", "trump", "donald trump"),
    "川普": ("特朗普", "川普", "trump", "donald trump"),
    "trump": ("trump", "donald trump", "特朗普", "川普"),
    "拜登": ("拜登", "biden", "joe biden"),
    "biden": ("biden", "joe biden", "拜登"),
    "普京": ("普京", "普丁", "putin", "vladimir putin"),
    "普丁": ("普京", "普丁", "putin", "vladimir putin"),
    "putin": ("putin", "vladimir putin", "普京", "普丁"),
    "泽连斯基": ("泽连斯基", "澤連斯基", "zelensky", "zelenskyy", "volodymyr zelensky"),
    "澤連斯基": ("泽连斯基", "澤連斯基", "zelensky", "zelenskyy", "volodymyr zelensky"),
    "zelensky": ("zelensky", "zelenskyy", "volodymyr zelensky", "泽连斯基", "澤連斯基"),
}

BOILERPLATE_MARKERS = (
    "广告",
    "廣告",
    "跳转至下一栏",
    "跳轉至下一欄",
    "头条新闻",
    "頭條新聞",
    "每日新闻",
    "每日新聞",
    "深度报道",
    "深度報導",
    "显示更多",
    "顯示更多",
    "latest news",
    "top stories",
    "subscribe",
    "newsletter",
    "enable javascript",
    "cookie",
)

CHINA_REGIONAL_FALSE_POSITIVE_RE = re.compile(r"中国地方|中国地銀|中国新聞|中国銀行")
LATIN_WORD_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$", re.I)
L1_ALIAS_EXPAND_QUERIES = {
    "芯片",
    "晶片",
    "半导体",
    "半導體",
    "集成电路",
    "積體電路",
    "semiconductor",
    "semiconductors",
    "chip",
    "chips",
    "南海",
    "south china sea",
}
L1_DIRECT_CLUSTER_QUERIES = frozenset({"south china sea"})


def _article_quality_where() -> str:
    return """
        NOT (
            (COALESCE(ms.domain, '') ILIKE '%dw.com%' OR COALESCE(n.url, '') ILIKE '%://%dw.com/%')
            AND COALESCE(n.url, '') NOT LIKE '%/a-%'
        )
        AND NOT (COALESCE(n.title, '') ILIKE '%最新ニュース・特集%')
        AND NOT (COALESCE(n.title, '') ILIKE '%最新新闻%专题%')
        AND NOT (COALESCE(n.title, '') ILIKE '%跳转至下一栏%')
        AND NOT (COALESCE(n.title, '') ILIKE '%跳轉至下一欄%')
        AND NOT (COALESCE(n.title, '') ILIKE '%广告%')
        AND NOT (COALESCE(n.title, '') ILIKE '%廣告%')
        AND NOT (COALESCE(n.title, '') ILIKE '%Latest News & Headlines%')
        AND NOT (COALESCE(n.title, '') ILIKE '%potato chips%')
        AND NOT (COALESCE(n.title, '') ILIKE '%Kartoffel-Chips%')
        AND NOT (COALESCE(n.title, '') ILIKE '%chips im test%')
        AND NOT (COALESCE(n.title, '') ILIKE '%tortilla chips%')
    """


def _clean_article_title(title: Optional[str]) -> str:
    title_text = _clean(title)
    title_text = re.sub(r"\s*[-–—]\s*(中国|China|Cina)\s*[-–—]\s*Ansa\.it\s*$", "", title_text, flags=re.I)
    return title_text.strip()


def _infer_title_from_body(body: Optional[str]) -> str:
    text = _clean(body)
    if not text:
        return ""
    head = text[:260]
    for size in range(8, min(90, len(head) // 2) + 1):
        prefix = head[:size].strip()
        if not prefix or prefix[-1].isdigit():
            continue
        rest = head[size:].lstrip()
        if rest.startswith(prefix):
            return prefix.strip(" -–—|")
    return ""


def _is_generic_title(title: str) -> bool:
    value = _clean(title)
    if not value:
        return True
    if value.lower() in {
        "news",
        "world",
        "politics",
        "business",
        "economy",
        "opinion",
        "china",
        "中国",
        "中國",
        "international",
        "breaking news",
    }:
        return True
    if len(value) <= 5:
        return True
    generic_markers = (
        "新闻",
        "新聞",
        "每日新闻",
        "每日新聞",
        "头条新闻",
        "頭條新聞",
        "深度报道",
        "深度報導",
        "最新ニュース",
        "最新情報",
        "ニュース 経済",
    )
    return any(marker in value for marker in generic_markers)


def _query_term_variants(
    term: Optional[str],
    *,
    expand_topics: bool = True,
) -> List[str]:
    raw = _clean(term)
    if not raw:
        return []
    entity_match = resolve_entity_alias(raw)
    if entity_match is not None:
        return list(entity_alias_variants(raw))[:10]

    variants = [raw]
    if expand_topics:
        for alias in QUERY_ALIAS_MAP.get(raw.lower(), ()):
            if alias not in variants:
                variants.append(alias)
        for alias in QUERY_ALIAS_MAP.get(raw, ()):
            if alias not in variants:
                variants.append(alias)
    return variants[:10]


def _needs_word_boundary_match(variant: str) -> bool:
    return bool(LATIN_WORD_RE.fullmatch(_clean(variant)))


def _variant_in_text(variant: str, haystack: str) -> bool:
    value = _clean(variant).lower()
    if not value:
        return False
    if _needs_word_boundary_match(value):
        return bool(re.search(rf"(^|[^a-z0-9]){re.escape(value)}([^a-z0-9]|$)", haystack))
    return value in haystack


def _query_match_terms(query: str) -> List[str]:
    raw = _clean(query)
    if not raw:
        return []
    parsed = parse_supported_query(raw)
    if parsed is not None and (
        parsed.explicit_boolean
        or parsed.root.kind != "and"
        or any(node.kind == "phrase" for node, _negated in iter_query_leaves(parsed.root))
    ):
        return [node.value for node, _negated in iter_query_leaves(parsed.root)]
    if resolve_entity_alias(raw) is not None:
        return [raw]
    parts = _split_terms(query)
    if len(parts) <= 1:
        return [raw]

    # Preserve versioned multi-word aliases as one logical term.  A plain
    # whitespace split would turn "United States policy" into three required
    # terms and make the alias expansion/explain output disagree with the SQL.
    terms: List[str] = []
    index = 0
    while index < len(parts):
        match_value: Optional[str] = None
        match_end = index + 1
        for end in range(min(len(parts), index + 8), index, -1):
            candidate = " ".join(parts[index:end])
            if resolve_entity_alias(candidate) is not None:
                match_value = candidate
                match_end = end
                break
        terms.append(match_value or parts[index])
        index = match_end if match_value is not None else index + 1
    return terms


def _uses_boolean_ast(value: Optional[str]) -> bool:
    parsed = parse_supported_query(value)
    if parsed is None:
        return False
    leaves = list(iter_query_leaves(parsed.root))
    return (
        parsed.explicit_boolean
        or any(node.kind == "phrase" for node, _negated in leaves)
        and not (parsed.root.kind == "phrase" and len(leaves) == 1)
    )


def _query_leaf_variants(node: QueryNode, *, expand_aliases: bool) -> List[str]:
    if node.kind == "phrase":
        return [node.value]
    return _query_term_variants(
        node.value,
        expand_topics=expand_aliases,
    ) or [node.value]


def _compile_query_ast_sql(
    node: QueryNode,
    columns: Sequence[str],
    bind: Dict[str, Any],
    key_prefix: str,
    *,
    expand_aliases: bool,
    counter: Optional[List[int]] = None,
) -> str:
    """Compile a validated Boolean AST to parameterized SQL only."""

    position = counter if counter is not None else [0]
    if node.kind in {"term", "phrase"}:
        leaf_index = position[0]
        position[0] += 1
        predicates: List[str] = []
        for variant_index, variant in enumerate(
            _query_leaf_variants(node, expand_aliases=expand_aliases)[: int(QUERY_LIMITS["max_aliases_per_term"])]
        ):
            key = f"{key_prefix}_{leaf_index}_{variant_index}"
            if _needs_word_boundary_match(variant):
                bind[key] = rf"(^|[^[:alnum:]]){re.escape(_clean(variant))}([^[:alnum:]]|$)"
                predicates.extend(f"{column} ~* :{key}" for column in columns)
            else:
                bind[key] = f"%{_clean(variant)}%"
                predicates.extend(f"{column} ILIKE :{key}" for column in columns)
        if not predicates:
            return "FALSE"
        return "(" + " OR ".join(predicates) + ")"
    if node.kind == "not":
        child = _compile_query_ast_sql(
            node.children[0],
            columns,
            bind,
            key_prefix,
            expand_aliases=expand_aliases,
            counter=position,
        )
        return f"NOT ({child})"
    operator = " AND " if node.kind == "and" else " OR "
    children = [
        _compile_query_ast_sql(
            child,
            columns,
            bind,
            key_prefix,
            expand_aliases=expand_aliases,
            counter=position,
        )
        for child in node.children
    ]
    return "(" + operator.join(children) + ")"


def _explicit_phrase_value(value: Optional[str]) -> Optional[str]:
    """Return an explicitly quoted phrase, otherwise ``None``.

    A normal multi-term research topic must not silently become one literal
    phrase.  Quoting is the public, reproducible way to request that stricter
    behavior.
    """

    raw = _clean(value)
    quote_pairs = (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"))
    for opening, closing in quote_pairs:
        if raw.startswith(opening) and raw.endswith(closing) and len(raw) > 2:
            phrase = _clean(raw[len(opening) : -len(closing)])
            return phrase or None
    return None


def _text_match_mode(value: Optional[str], mode: Optional[str]) -> Tuple[str, Optional[str]]:
    """Map the public mode to SQL matching without implicit phrase search."""

    phrase = _explicit_phrase_value(value)
    if phrase is not None:
        return "phrase", phrase
    if _clean(mode).lower() == "fuzzy":
        return "or", value
    return "and", value


def _strip_known_false_positive_contexts(text: str, keyword: Optional[str]) -> str:
    query = _clean(keyword).lower()
    variants = {v.lower() for term in _query_match_terms(_clean(keyword)) for v in _query_term_variants(term)}
    if query in ("中国", "中國", "china") or {"china", "chinese", "中国", "中國"} & variants:
        return CHINA_REGIONAL_FALSE_POSITIVE_RE.sub("", text)
    return text


def _has_boilerplate_overload(text: str) -> bool:
    head = _clean(text)[:900]
    if not head:
        return False
    lowered = head.lower()
    score = sum(lowered.count(marker.lower()) for marker in BOILERPLATE_MARKERS)
    if score >= 3:
        return True
    return bool(re.search(r"(广告|廣告).{0,240}(头条新闻|頭條新聞|每日新闻|每日新聞|跳转至下一栏|跳轉至下一欄)", head))


def _is_false_positive_article_context(title: str, body: str, keyword: Optional[str]) -> bool:
    query = _clean(keyword).lower()
    variants = {v.lower() for term in _query_match_terms(_clean(keyword)) for v in _query_term_variants(term)}
    if query in ("中国", "中國", "china") or {"china", "chinese", "中国", "中國"} & variants:
        return bool(CHINA_REGIONAL_FALSE_POSITIVE_RE.search(f"{title} {body[:220]}"))
    return False


def _query_matches_clean_text(title: str, body: str, keyword: Optional[str]) -> bool:
    query = _clean(keyword)
    if not query:
        return True
    haystack = _strip_known_false_positive_contexts(f"{title} {body}", query).lower()
    for term in _query_match_terms(query):
        variants = [variant.lower() for variant in _query_term_variants(term)]
        if variants and not any(_variant_in_text(variant, haystack) for variant in variants):
            return False
    return True


def _has_usable_text(title: str, body: str) -> bool:
    if len(body) >= 80:
        return True
    return bool(title and not _is_generic_title(title) and len(title) >= 12)


def _is_low_quality_article_row(row: Dict[str, Any], keyword: Optional[str] = None) -> bool:
    title = _clean_article_title(row.get("title"))
    body = _clean_article_display_text(row.get("body"), title)
    inferred_title = _infer_title_from_body(body)
    if _is_generic_title(title) and inferred_title:
        title = inferred_title
    if not _has_usable_text(title, body):
        return True
    body_lower = body.lower()
    if body_lower.startswith("to view this video please enable javascript"):
        return True
    if "読売新聞の購読者" in body and "限定" in body:
        return True
    if "请启用javascript" in body_lower or "enable javascript" in body_lower:
        return True
    if _has_boilerplate_overload(f"{title} {body[:900]}"):
        return True
    if _is_false_positive_article_context(title, body, keyword):
        return True
    if not _query_matches_clean_text(title, body, keyword):
        return True
    return False


def _clean(value: Any) -> str:
    text_value = "" if value is None else str(value)
    text_value = re.sub(r"\s+", " ", text_value).strip()
    return text_value


def _source_from_url(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        host = re.sub(r"^www\.", "", host)
        return host
    except Exception:
        return ""


def _clean_article_display_text(body: Optional[str], title: Optional[str] = None) -> str:
    body_text = _clean(body)
    if not body_text:
        return ""

    title_text = _clean(title)
    if title_text and len(title_text) >= 6:
        matches = list(re.finditer(re.escape(title_text), body_text))
        if len(matches) >= 2 and matches[1].end() <= 700:
            body_text = body_text[matches[1].end() :].strip()
        elif matches and matches[0].start() <= 24:
            body_text = body_text[matches[0].end() :].strip()

    if "跳转至下一栏" in body_text or "跳轉至下一欄" in body_text:
        for marker in ("深度报道 深度报道", "深度報導 深度報導", "更多新闻 更多新闻", "更多新聞 更多新聞"):
            marker_idx = body_text.find(marker)
            if 0 <= marker_idx <= 1200:
                body_text = body_text[marker_idx + len(marker) :].strip()
                break
        for _ in range(4):
            cleaned = re.sub(r"^((广告|廣告)\s*)?.{0,220}?(跳转至下一栏|跳轉至下一欄)\s*", "", body_text).strip()
            if cleaned == body_text:
                break
            body_text = cleaned

    body_text = re.sub(r"^((广告|廣告)\s*)+", "", body_text).strip()
    body_text = re.sub(r"^(头条新闻|頭條新聞|每日新闻|每日新聞|深度报道|深度報導)(\s+\1)?\s*", "", body_text).strip()
    body_text = re.sub(r"^((显示更多|顯示更多)\s*)+", "", body_text).strip()
    body_text = re.sub(r"^(\d{4}年\d{1,2}月\d{1,2}日\s+){1,2}", "", body_text).strip()
    return _clean(body_text)


def _snippet(body: Optional[str], limit: int = 260, title: Optional[str] = None) -> str:
    body_text = _clean_article_display_text(body, title)
    if not body_text:
        return ""
    sentence = re.split(r"(?<=[。！？!?])\s+|\n+", body_text, maxsplit=1)[0]
    if len(sentence) < 45:
        sentence = body_text
    return sentence[:limit].rstrip()


def _date_to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


CORE_CHINA_DETAIL_RE = re.compile(
    r"(\b(china|chinese|beijing|prc|mainland china)\b|中国|中國|北京|中方|大陆|大陸)",
    re.I,
)
PERIPHERY_CHINA_DETAIL_RE = re.compile(
    r"(\b(taiwan|taiwanese|hong kong|hongkong|xinjiang|tibet|south china sea)\b|台湾|台灣|香港|新疆|西藏|南海)",
    re.I,
)
POSITIVE_CHINA_DETAIL_RE = re.compile(
    r"(praise|welcome|benefit|boost|cooperat|support|resilien|breakthrough|领先|合作|支持|提振|突破|赞扬|欢迎)",
    re.I,
)
NEGATIVE_CHINA_DETAIL_RE = re.compile(
    r"(critic|condemn|sanction|restriction|ban|risk|threat|pressure|tension|dispute|probe|tariff|制裁|限制|禁止|风险|威胁|压力|紧张|争端|调查|关税|批评|谴责)",
    re.I,
)


def _china_detail_role_and_score(news_item: NewsItem, extraction: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    title = _clean(news_item.title)
    body = _clean((news_item.abstract or "") + " " + (news_item.body or ""))[:2600]
    text = f"{title} {body}"
    actor_text = " ".join(
        _clean((extraction or {}).get(key))
        for key in ("initiator", "target", "canonical_initiator", "canonical_target", "location")
    )
    title_core = bool(CORE_CHINA_DETAIL_RE.search(title))
    text_core = bool(CORE_CHINA_DETAIL_RE.search(text))
    actor_core = bool(CORE_CHINA_DETAIL_RE.search(actor_text))
    title_periphery = bool(PERIPHERY_CHINA_DETAIL_RE.search(title))
    text_periphery = bool(PERIPHERY_CHINA_DETAIL_RE.search(text))
    actor_periphery = bool(PERIPHERY_CHINA_DETAIL_RE.search(actor_text))
    tone = _clean((extraction or {}).get("tone")).lower() or "neutral"
    has_pos = bool(POSITIVE_CHINA_DETAIL_RE.search(text))
    has_neg = bool(NEGATIVE_CHINA_DETAIL_RE.search(text))

    if actor_core:
        role, directness, directness_score, confidence = "china_as_actor_or_target", "direct_evaluation", 0.96, 0.78
    elif title_core and (has_pos or has_neg or tone != "neutral"):
        role, directness, directness_score, confidence = "china_in_title", "direct_evaluation", 0.78, 0.72
    elif actor_periphery:
        role, directness, directness_score, confidence = "china_periphery_related", "indirect_related", 0.56, 0.68
    elif title_periphery or text_periphery:
        role, directness, directness_score, confidence = "china_periphery_related", "mention_or_context", 0.44, 0.58
    elif text_core:
        role, directness, directness_score, confidence = "china_mention", "mention_only", 0.42, 0.54
    else:
        role, directness, directness_score, confidence = "not_china_related", "not_related", 0.0, 0.42

    if has_neg and not has_pos:
        stance = -0.35 if directness_score >= 0.55 else -0.18
    elif has_pos and not has_neg:
        stance = 0.32 if directness_score >= 0.55 else 0.16
    elif tone == "negative":
        stance = -0.22 if directness_score >= 0.55 else -0.10
    elif tone == "positive":
        stance = 0.22 if directness_score >= 0.55 else 0.10
    else:
        stance = 0.0

    relevance = round(max(0.0, min(1.0, directness_score * confidence)), 3)
    article_weight = round(max(0.0, min(1.0, relevance * max(0.35, confidence))), 3)
    impact_index = round(stance * article_weight * 100.0, 1)
    evidence = []
    if actor_core or actor_periphery:
        evidence.append("L1 抽取的发起方/目标/地点命中涉华实体")
    if title_core or title_periphery:
        evidence.append("标题命中涉华或中国周边实体")
    if has_neg:
        evidence.append("文本含限制/风险/制裁/压力等负向评价词")
    if has_pos:
        evidence.append("文本含合作/支持/突破等正向评价词")
    if not evidence:
        evidence.append("未发现明确涉华实体或评价证据")
    return {
        "source": "detail_realtime_v1",
        "is_china_related": relevance >= 0.35,
        "relevance_score": relevance,
        "china_index": relevance,
        "china_role": role,
        "directness": directness,
        "directness_score": round(directness_score, 3),
        "stance_score": round(stance, 3),
        "confidence": round(confidence, 3),
        "article_weight": article_weight,
        "impact_index": impact_index,
        "polarity": "positive" if impact_index > 0 else "negative" if impact_index < 0 else "neutral",
        "tone": tone,
        "target_scope": (extraction or {}).get("event_family") or "general",
        "evidence": "；".join(evidence),
    }


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("T", " ")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _normalize_language(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return None
    return LANG_ALIASES.get(raw, raw.lower())


def _split_terms(value: Optional[str]) -> List[str]:
    raw = _clean(value)
    if not raw:
        return []
    parts = re.split(r"[\s,，;；|]+", raw)
    return [p for p in parts if p]


def _page_bounds(page: int, page_size: int) -> Tuple[int, int]:
    p = max(int(page or 1), 1)
    size = max(min(int(page_size or 10), 100), 1)
    return p, size


def _favorite_sets(
    app_db: Optional[Session],
    user: Optional[Dict[str, Any]],
    ids: Sequence[int],
    scope_topic: Optional[str],
) -> Tuple[set, set]:
    if not app_db or not user or not ids:
        return set(), set()
    try:
        user_id = int(user.get("user_id") or 0)
    except Exception:
        user_id = 0
    if not user_id:
        return set(), set()
    try:
        return get_user_favorite_sets_for_scope(app_db, user_id, list(ids), scope_topic)
    except Exception:
        return set(), set()


def _news_select_sql(body_expr: str = "LEFT(COALESCE(n.body, ''), 1200)", extra_select: str = "") -> str:
    return f"""
        SELECT
            n.id,
            COALESCE(NULLIF(n.title, ''), '') AS title,
            {body_expr} AS body,
            n.url AS request_url,
            n.published_at AS pub_time,
            n.language AS language_id,
            n.region AS news_region,
            n.author,
            ms.domain,
            COALESCE(NULLIF(msp.source_name, ''), NULLIF(ms.domain, ''), '') AS source_name,
            msp.country AS source_country,
            msp.region AS source_region,
            msp.source_type,
            msp.political_leaning,
            msp.credibility_tier,
            ecm.cluster_id AS event_coref_cluster_id,
            c.title AS cluster_title,
            c.article_count AS cluster_article_count
            {extra_select}
        FROM public.news n
        LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
        LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
        {_quality_news_join_sql()}
        LEFT JOIN LATERAL (
            SELECT cluster_id
            FROM public.event_coref_members ecm2
            WHERE ecm2.news_id = n.id
            ORDER BY ecm2.membership_score DESC NULLS LAST, ecm2.created_at DESC NULLS LAST
            LIMIT 1
        ) ecm ON TRUE
        LEFT JOIN public.event_coref_clusters c ON c.cluster_id = ecm.cluster_id
    """


def _news_items_from_rows(
    rows: Sequence[Dict[str, Any]],
    app_db: Optional[Session] = None,
    user: Optional[Dict[str, Any]] = None,
    scope_topic: Optional[str] = None,
    include_body: bool = False,
    positive_literal_terms: Optional[Sequence[str]] = None,
    effective_search_fields: Optional[Sequence[str]] = None,
) -> List[NewsItem]:
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    fav_ids, warn_ids = _favorite_sets(app_db, user, ids, scope_topic)
    out: List[NewsItem] = []
    for r in rows:
        nid = int(r.get("id"))
        source = _clean(r.get("source_name")) or _clean(r.get("domain")) or _source_from_url(r.get("request_url"))
        title = _clean_article_title(r.get("title")) or (f"{source} - 无标题" if source else "无标题")
        display_body = _clean_article_display_text(r.get("body"), title)
        inferred_title = _infer_title_from_body(display_body)
        if _is_generic_title(title) and inferred_title:
            title = inferred_title
            display_body = _clean_article_display_text(display_body, title)
        abstract = _snippet(display_body, title=title)
        language = _clean(r.get("language_id"))
        source_country = _clean(r.get("source_country"))
        source_region = _clean(r.get("source_region"))
        news_region = _clean(r.get("news_region"))
        location = _clean(r.get("location"))
        cluster_article_count = r.get("cluster_article_count")
        cluster_article_count = int(cluster_article_count) if cluster_article_count is not None else None
        if cluster_article_count is None:
            value_tag = "常规"
        elif cluster_article_count >= 1000:
            value_tag = "超高关注"
        elif cluster_article_count >= 200:
            value_tag = "高关注"
        elif cluster_article_count >= 50:
            value_tag = "中关注"
        else:
            value_tag = "低关注"
        out.append(
            NewsItem(
                id=nid,
                title=title,
                abstract=abstract,
                body=display_body if include_body else None,
                pub_time=r.get("pub_time"),
                request_url=r.get("request_url"),
                language_id=language or None,
                created_at=None,
                time_semantics=NewsResultTimeSemantics(
                    published_at=r.get("pub_time")
                ),
                source=source or None,
                source_country=source_country or None,
                source_region=source_region or None,
                news_region=news_region or None,
                location=location or None,
                cluster_title=_clean(r.get("cluster_title")) or None,
                cluster_article_count=cluster_article_count,
                value_tag=value_tag,
                is_first_release=False,
                is_favorited=nid in fav_ids,
                is_warned=nid in warn_ids,
                search_hit=(
                    build_search_hit_disclosure(
                        title=title,
                        abstract=abstract,
                        positive_literal_terms=positive_literal_terms,
                        effective_search_fields=effective_search_fields or [],
                    )
                    if positive_literal_terms is not None
                    else SearchHitDisclosure()
                ),
            )
        )
    return out


def _add_time_filters(
    clauses: List[str],
    bind: Dict[str, Any],
    params: Any,
    column: str,
    start_column: Optional[str] = None,
    end_column: Optional[str] = None,
) -> None:
    start_column = start_column or column
    end_column = end_column or column
    publish_time = _clean(getattr(params, "publish_time", None))
    days = {
        "近一天": 1,
        "近一周": 7,
        "近一月": 30,
        "近三月": 90,
        "近一年": 365,
    }.get(publish_time)
    if days:
        bind["auto_start_time"] = datetime.now() - timedelta(days=days)
        clauses.append(f"{end_column} >= :auto_start_time")

    start = _parse_dt(getattr(params, "start_time", None))
    end = _parse_dt(getattr(params, "end_time", None))
    if start:
        bind["start_time"] = start
        clauses.append(f"{end_column} >= :start_time")
    if end:
        bind["end_time"] = end
        clauses.append(f"{start_column} <= :end_time")


def _text_columns_for_news(hit_location: Optional[str]) -> List[str]:
    hit = _clean(hit_location)
    if hit == "标题":
        return ["COALESCE(n.title, '')"]
    if hit == "正文":
        return ["COALESCE(n.body, '')"]
    # 新新闻库没有全文索引，默认用标题优先保证首屏速度。
    return ["COALESCE(n.title, '')"]


def _quality_news_join_sql(alias: str = "nq") -> str:
    return (
        f"JOIN public.news_quality_labels {alias} "
        f"ON {alias}.news_id = n.id "
        f"AND {alias}.is_good = TRUE "
        f"AND {alias}.label_version = '{QUALITY_LABEL_VERSION}'"
    )


def _add_text_clause(
    clauses: List[str],
    bind: Dict[str, Any],
    columns: Sequence[str],
    value: Optional[str],
    key_prefix: str,
    op: str = "and",
    expand_aliases: bool = False,
) -> None:
    raw = _clean(value)
    if not raw:
        return
    parsed = parse_supported_query(raw)
    if parsed is not None and _uses_boolean_ast(raw):
        clauses.append(
            _compile_query_ast_sql(
                parsed.root,
                columns,
                bind,
                key_prefix,
                expand_aliases=expand_aliases,
            )
        )
        return
    explicit_phrase = _explicit_phrase_value(raw)
    literal_phrase = op == "phrase" or explicit_phrase is not None
    terms = [explicit_phrase or raw] if literal_phrase else _query_match_terms(raw)
    if not terms:
        return
    groups = []
    for idx, term in enumerate(terms[:12]):
        variants = (
            [term]
            if literal_phrase
            else _query_term_variants(term, expand_topics=expand_aliases)
        ) or [term]
        variant_groups = []
        for variant_idx, variant in enumerate(variants[:10]):
            key = f"{key_prefix}_{idx}_{variant_idx}"
            if _needs_word_boundary_match(variant):
                bind[key] = rf"(^|[^[:alnum:]]){re.escape(_clean(variant))}([^[:alnum:]]|$)"
                variant_groups.append("(" + " OR ".join(f"{col} ~* :{key}" for col in columns) + ")")
            else:
                bind[key] = f"%{variant}%"
                variant_groups.append("(" + " OR ".join(f"{col} ILIKE :{key}" for col in columns) + ")")
        if variant_groups:
            groups.append("(" + " OR ".join(variant_groups) + ")")
    if not groups:
        return
    joiner = " OR " if op == "or" else " AND "
    clauses.append("(" + joiner.join(groups) + ")")


def _news_keyword_filter_sql(
    keyword: Optional[str],
    key_prefix: str = "article_kw",
    *,
    expand_topics: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    clauses: List[str] = []
    bind: Dict[str, Any] = {}
    operator, _normalized_keyword = _text_match_mode(keyword, "exact")
    _add_text_clause(
        clauses,
        bind,
        ["COALESCE(n.title, '')", "COALESCE(n.body, '')"],
        keyword,
        key_prefix,
        operator,
        expand_aliases=expand_topics,
    )
    return (" AND ".join(clauses) if clauses else "1=1"), bind


def _query_specific_news_where(keyword: Optional[str]) -> str:
    query = _clean(keyword).lower()
    variants = {v.lower() for term in _query_match_terms(_clean(keyword)) for v in _query_term_variants(term)}
    if query in ("南海", "south china sea") or {"南海", "south china sea"} & variants:
        return """
            AND NOT (COALESCE(n.title, '') ILIKE '%中南海%')
            AND NOT (COALESCE(n.title, '') ILIKE '%南海トラフ%')
            AND NOT (COALESCE(n.title, '') ILIKE '%南海地震%')
            AND NOT (COALESCE(n.title, '') ILIKE '%Nankai Trough%')
        """
    return ""


def _query_specific_news_order(keyword: Optional[str]) -> str:
    if _clean(keyword).lower() == "control":
        return """
            CASE WHEN (
                COALESCE(n.title, '') ~* '(^|[^a-z])(chip|semiconductor|technology|tech|export)([^a-z]|$)'
                OR COALESCE(n.title, '') ~ '(芯片|晶片|半导体|科技)'
            ) THEN 0 ELSE 1 END
        """
    return "0"


def _title_match_groups(
    keyword: Optional[str],
    *,
    expand_aliases: bool,
) -> List[List[str]]:
    query = _clean(keyword)
    if not query:
        return []
    if _uses_boolean_ast(query):
        return []
    explicit_phrase = _explicit_phrase_value(query)
    # Unquoted topics are tokenized for both modes.  Exact mode intersects the
    # resulting candidate sets below, while fuzzy mode unions alias-expanded
    # candidates.  Only explicit quotes preserve a literal phrase.
    source_terms = [explicit_phrase] if explicit_phrase else _query_match_terms(query)
    groups: List[List[str]] = []
    for term in source_terms:
        variants = (
            [term]
            if explicit_phrase
            else _query_term_variants(term, expand_topics=expand_aliases)
        )
        group: List[str] = []
        for variant in variants:
            value = _clean(variant)
            if len(value) < 2:
                continue
            low = value.lower()
            if low not in {x.lower() for x in group}:
                group.append(value)
            if len(group) >= 10:
                break
        if group:
            groups.append(group)
        if len(groups) >= 12:
            break
    return groups


def _title_match_terms(keyword: Optional[str], *, expand_aliases: bool) -> List[str]:
    return [
        variant
        for group in _title_match_groups(keyword, expand_aliases=expand_aliases)
        for variant in group
    ]


def _title_match_cte_sql(
    terms: Sequence[Any],
    key_prefix: str,
    per_term: int = 1200,
    *,
    match_all: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    bind: Dict[str, Any] = {"per_term": per_term}
    selects: List[str] = []
    for idx, raw_group in enumerate(terms):
        group = [raw_group] if isinstance(raw_group, str) else list(raw_group)
        predicates: List[str] = []
        for variant_idx, term in enumerate(group):
            key = f"{key_prefix}_{idx}_{variant_idx}"
            if _needs_word_boundary_match(term):
                bind[key] = rf"(^|[^a-z0-9]){re.escape(_clean(term))}([^a-z0-9]|$)"
                predicates.append(f"n.title ~* :{key}")
            else:
                bind[key] = f"%{term}%"
                predicates.append(f"n.title ILIKE :{key}")
        if not predicates:
            continue
        predicate = "(" + " OR ".join(predicates) + ")"
        selects.append(
            f"""(
            SELECT n.id AS news_id
            FROM public.news n
            WHERE {predicate}
            ORDER BY n.published_at DESC NULLS LAST, n.id DESC
            LIMIT :per_term
            )"""
        )
    if not selects:
        return "SELECT NULL::BIGINT AS news_id WHERE FALSE", bind
    joiner = "\nINTERSECT\n" if match_all and len(selects) > 1 else "\nUNION ALL\n"
    return joiner.join(selects), bind


def _has_advanced_search_filters(params: Any) -> bool:
    promoted_must_include = (
        not _clean(getattr(params, "keyword", None) or getattr(params, "topic", None))
        and bool(_clean(getattr(params, "must_include", None)))
    )
    attrs = ("any_include", "need_exclude", "data_source", "language", "country", "start_time", "end_time", "publish_time")
    if not promoted_must_include:
        attrs = ("must_include", *attrs)
    return any(
        _clean(getattr(params, attr, None))
        for attr in attrs
    )


def _has_title_candidate_incompatible_filters(params: Any) -> bool:
    """Filters that cannot safely be applied after the bounded title lookup.

    Date filters are intentionally supported by the fast path. Previously a
    common preset such as "近三月" forced a scan of the complete news table,
    making zero-result searches the slowest requests on the page.
    """

    promoted_must_include = (
        not _clean(getattr(params, "keyword", None) or getattr(params, "topic", None))
        and bool(_clean(getattr(params, "must_include", None)))
    )
    attrs = ("any_include", "need_exclude", "data_source", "language", "country", "site")
    if not promoted_must_include:
        attrs = ("must_include", *attrs)
    if any(_clean(getattr(params, attr, None)) for attr in attrs):
        return True
    # The bounded title candidate CTE is built newest-first. Route ascending
    # publication-time requests through the complete filtered path so old
    # matches are not silently excluded from the candidate window.
    return _clean(getattr(params, "sort_order", "desc")).lower() == "asc"


def _boolean_title_candidate_plan(
    keyword: str,
    *,
    expand_aliases: bool,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    parsed = parse_supported_query(keyword)
    if parsed is None or not _uses_boolean_ast(keyword):
        return None
    positive_groups: List[List[str]] = []
    for node, negated in iter_query_leaves(parsed.root):
        if negated:
            continue
        variants: List[str] = []
        for variant in _query_leaf_variants(node, expand_aliases=expand_aliases):
            value = _clean(variant)
            if value and value.casefold() not in {item.casefold() for item in variants}:
                variants.append(value)
            if len(variants) >= int(QUERY_LIMITS["max_aliases_per_term"]):
                break
        if variants:
            positive_groups.append(variants)
    if not positive_groups:
        # parse_supported_query rejects this condition. Keep a defensive
        # fail-closed return in case an internal caller bypasses validation.
        return None
    candidate_sql, bind = _title_match_cte_sql(
        positive_groups,
        "boolean_title_candidate",
        per_term=BOOLEAN_TITLE_CANDIDATES_PER_TERM,
        match_all=False,
    )
    predicate = _compile_query_ast_sql(
        parsed.root,
        ["COALESCE(n.title, '')"],
        bind,
        "boolean_title_filter",
        expand_aliases=expand_aliases,
    )
    return candidate_sql, bind, predicate


def _news_rows_from_title_matches(params: Any, page: int, page_size: int) -> Optional[Tuple[List[Dict[str, Any]], int]]:
    keyword = _search_keyword(params)
    mode = _clean(getattr(params, "mode", "exact"))
    # Literal Latin substrings such as "Taiwan" and "control" already cover
    # their common inflections. Avoid redundant aliases and retain a wider
    # single candidate window for relevance reranking.
    expand_aliases = mode == "fuzzy" and keyword.lower() not in FUZZY_LITERAL_TITLE_QUERIES
    if _clean(getattr(params, "hit_location", None)) == "正文":
        return None
    if _has_title_candidate_incompatible_filters(params):
        return None
    boolean_plan = _boolean_title_candidate_plan(
        keyword,
        expand_aliases=expand_aliases,
    )
    term_groups = _title_match_groups(keyword, expand_aliases=expand_aliases)
    if boolean_plan is None and not term_groups:
        return None
    offset = (page - 1) * page_size
    if boolean_plan is None:
        matched_sql, bind = _title_match_cte_sql(
            term_groups,
            "news_title_match",
            per_term=FUZZY_TITLE_CANDIDATES_PER_TERM if expand_aliases else 3000,
            match_all=mode != "fuzzy",
        )
        boolean_where = ""
    else:
        matched_sql, bind, boolean_predicate = boolean_plan
        boolean_where = f"AND {boolean_predicate}"
    time_clauses: List[str] = []
    _add_time_filters(time_clauses, bind, params, "n.published_at")
    time_where = "".join(f" AND {clause}" for clause in time_clauses)
    sort_by = _clean(getattr(params, "sort_by", None)).lower()
    use_title_priority = sort_by not in {"pub_time", "published_at"}
    relevance_rank = (
        "0"
        if boolean_plan is not None or not use_title_priority
        else _query_specific_news_order(keyword)
    )
    query_specific_where = (
        "" if boolean_plan is not None else _query_specific_news_where(keyword)
    )
    stmt = text(
        f"""
        WITH matched_news AS (
            SELECT DISTINCT news_id
            FROM ({matched_sql}) raw
            WHERE news_id IS NOT NULL
        ),
        ranked AS (
            SELECT
                n.id,
                n.published_at,
                {relevance_rank} AS relevance_rank,
                COUNT(*) OVER() AS total_count
            FROM matched_news mn
            JOIN public.news_quality_labels q
              ON q.news_id = mn.news_id
             AND q.is_good = TRUE
             AND q.label_version = '{QUALITY_LABEL_VERSION}'
            JOIN public.news n ON n.id = mn.news_id
            LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
            LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
            WHERE {_article_quality_where()}
              {query_specific_where}
              {boolean_where}
              {time_where}
            ORDER BY relevance_rank, n.published_at DESC NULLS LAST, n.id DESC
            LIMIT :limit OFFSET :offset
        )
        {_news_select_sql(extra_select=", ranked.total_count")}
        JOIN ranked ON ranked.id = n.id
        ORDER BY ranked.relevance_rank, ranked.published_at DESC NULLS LAST, ranked.id DESC
        """
    )
    with _search_connection() as conn:
        rows = conn.execute(stmt, {**bind, "limit": page_size, "offset": offset}).mappings().all()
    total = int(rows[0].get("total_count") or 0) if rows else 0
    return [dict(r) for r in rows], total


def _macro_events_from_title_matches(params: Any, page_size: int, offset: int, start_ts: float) -> Optional[SearchResponse]:
    keyword = _search_keyword(params)
    mode = _clean(getattr(params, "mode", "exact"))
    term_groups = _title_match_groups(keyword, expand_aliases=mode == "fuzzy")
    if not term_groups:
        return None
    if _has_advanced_search_filters(params):
        return None
    matched_sql, bind = _title_match_cte_sql(
        term_groups,
        "l3_title_match",
        per_term=1400,
        match_all=mode != "fuzzy",
    )
    stmt = text(
        f"""
        WITH matched_news AS (
            SELECT DISTINCT news_id
            FROM ({matched_sql}) raw
            WHERE news_id IS NOT NULL
        ),
        macro_hits AS (
            SELECT
                m.macro_id AS id,
                m.title,
                m.summary,
                m.family_group,
                m.article_count,
                m.l2_chain_count AS story_count,
                m.start_date,
                m.end_date,
                m.quality_score,
                COUNT(DISTINCT mn.news_id) AS match_count,
                MAX(n.published_at) AS latest_match_at
            FROM matched_news mn
            JOIN public.news_quality_labels q
              ON q.news_id = mn.news_id
             AND q.is_good = TRUE
             AND q.label_version = '{QUALITY_LABEL_VERSION}'
            JOIN public.news n ON n.id = mn.news_id
            JOIN public.event_coref_members ecm ON ecm.news_id = mn.news_id
            JOIN public.event_l2_chain_segments s ON s.l1_cluster_id = ecm.cluster_id
            JOIN public.event_l3_macro_members mm
              ON mm.l2_chain_id = s.chain_id
             AND mm.l2_run_id = s.run_id
            JOIN public.event_l3_macro_events m
              ON m.macro_id = mm.macro_id
             AND m.run_id = mm.run_id
            LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
            LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
            WHERE {_article_quality_where()}
              {_query_specific_news_where(keyword)}
            GROUP BY
                m.macro_id,
                m.title,
                m.summary,
                m.family_group,
                m.article_count,
                m.l2_chain_count,
                m.start_date,
                m.end_date,
                m.quality_score
            HAVING COUNT(DISTINCT mn.news_id) >= 2
        ),
        ranked AS (
            SELECT *, COUNT(*) OVER() AS total_count
            FROM macro_hits
            ORDER BY
                (match_count::float / GREATEST(SQRT(NULLIF(article_count, 0)), 1)) DESC,
                match_count DESC,
                quality_score DESC NULLS LAST,
                article_count DESC NULLS LAST,
                latest_match_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        )
        SELECT * FROM ranked
        """
    )
    page = int(getattr(params, "page", 1) or 1)
    with _search_connection() as conn:
        rows = conn.execute(stmt, {**bind, "limit": min(page_size * 3, 60), "offset": offset}).mappings().all()
    if not rows:
        return None
    total = int(rows[0].get("total_count") or 0)
    row_dicts = [dict(r) for r in rows][:page_size]
    macro_items = [
        MacroEventItem(
            id=str(r["id"]),
            title=_clean(r.get("title")) or str(r["id"]),
            # L3 has no audited initiator column. ``family_group`` is a
            # classification label and must not be presented as an actor.
            initiator=None,
            target=None,
            article_count=int(r.get("article_count") or 0),
            story_count=int(r.get("story_count") or 0),
            start_date=_date_to_str(r.get("start_date")),
            end_date=_date_to_str(r.get("end_date")),
            quality_score=float(r["quality_score"]) if r.get("quality_score") is not None else None,
            summary=_snippet(r.get("summary"), 360),
            level="l3",
        )
        for r in row_dicts
    ]
    total_pages, has_next, has_prev = _pagination_response(total, page, page_size)
    return SearchResponse(
        data=[],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=[],
        micro_story_items=[],
        macro_event_items=macro_items,
    )


def _l1_clusters_from_title_matches(
    params: Any,
    page_size: int,
    offset: int,
    user: Optional[Dict[str, Any]],
    app_db: Optional[Session],
) -> Optional[SearchResponse]:
    start_ts = time.time()
    keyword = _search_keyword(params)
    mode = _clean(getattr(params, "mode", "exact"))
    term_groups = _title_match_groups(keyword, expand_aliases=mode == "fuzzy")
    if not term_groups:
        return None
    matched_sql, bind = _title_match_cte_sql(
        term_groups,
        "l1_title_match",
        per_term=1400,
        match_all=mode != "fuzzy",
    )
    stmt = text(
        f"""
        WITH matched_news AS (
            SELECT DISTINCT news_id
            FROM ({matched_sql}) raw
            WHERE news_id IS NOT NULL
        ),
        cluster_hits AS (
            SELECT
                ecm.cluster_id,
                COUNT(*) AS match_count,
                MAX(n.published_at) AS latest_match_at
            FROM matched_news mn
            JOIN public.news_quality_labels q
              ON q.news_id = mn.news_id
             AND q.is_good = TRUE
             AND q.label_version = '{QUALITY_LABEL_VERSION}'
            JOIN public.event_coref_members ecm ON ecm.news_id = mn.news_id
            JOIN public.news n ON n.id = mn.news_id
            LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
            LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
            WHERE {_article_quality_where()}
              {_query_specific_news_where(keyword)}
            GROUP BY ecm.cluster_id
        ),
        ranked_clusters AS (
            SELECT
                c.cluster_id,
                c.title,
                c.article_count,
                c.event_type,
                c.event_family,
                c.initiator,
                c.target,
                c.dominant_trigger,
                c.cluster_quality,
                c.start_date,
                c.end_date,
                h.match_count,
                h.latest_match_at,
                COUNT(*) OVER() AS total_count
            FROM cluster_hits h
            JOIN public.event_coref_clusters c ON c.cluster_id = h.cluster_id
            ORDER BY h.match_count DESC, c.article_count DESC NULLS LAST, h.latest_match_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        )
        SELECT * FROM ranked_clusters
        """
    )
    with _search_connection() as conn:
        raw_clusters = conn.execute(
            stmt,
            {**bind, "limit": min(page_size * 8, 120), "offset": offset},
        ).mappings().all()
    if not raw_clusters:
        return None
    total = int(raw_clusters[0].get("total_count") or 0)
    clusters = _dedupe_l1_clusters([dict(r) for r in raw_clusters])[: page_size * 4]
    cluster_ids = [str(r["cluster_id"]) for r in clusters]
    articles_by_cluster: Dict[str, List[NewsItem]] = {cid: [] for cid in cluster_ids}
    if cluster_ids:
        article_keyword_sql, article_keyword_bind = _news_keyword_filter_sql(
            keyword,
            "l1_title_article_kw",
            expand_topics=mode == "fuzzy",
        )
        article_stmt = text(
            """
            SELECT *
            FROM (
                SELECT
                    ecm.cluster_id,
                    n.id,
                    COALESCE(NULLIF(n.title, ''), '') AS title,
                    LEFT(COALESCE(n.body, ''), 1200) AS body,
                    n.url AS request_url,
                    n.published_at AS pub_time,
                    n.language AS language_id,
                    n.region AS news_region,
                    n.author,
                    ms.domain,
                    COALESCE(NULLIF(msp.source_name, ''), NULLIF(ms.domain, ''), '') AS source_name,
                    msp.country AS source_country,
                    msp.region AS source_region,
                    msp.source_type,
                    msp.political_leaning,
                    msp.credibility_tier,
                    ROW_NUMBER() OVER (
                        PARTITION BY ecm.cluster_id
                        ORDER BY n.published_at DESC NULLS LAST, ecm.membership_score DESC NULLS LAST
                    ) AS rn
                FROM public.event_coref_members ecm
                JOIN public.news n ON n.id = ecm.news_id
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                """ + _quality_news_join_sql() + """
                WHERE ecm.cluster_id IN :cluster_ids
                  AND """ + _article_quality_where() + """
                  AND """ + article_keyword_sql + """
            ) ranked
            WHERE rn <= 8
            ORDER BY cluster_id, pub_time DESC NULLS LAST
            """
        ).bindparams(bindparam("cluster_ids", expanding=True))
        with _search_connection() as conn:
            article_rows = [
                dict(r)
                for r in conn.execute(
                    article_stmt,
                    {"cluster_ids": cluster_ids, **article_keyword_bind},
                ).mappings().all()
            ]
        for cid in cluster_ids:
            rows = [r for r in article_rows if str(r.get("cluster_id")) == cid]
            articles_by_cluster[cid] = _news_items_from_rows(rows, app_db, user, getattr(params, "favorite_scope_topic", None))
    items: List[EventCorefClusterInfo] = []
    for r in clusters:
        cid = str(r["cluster_id"])
        articles = articles_by_cluster.get(cid, [])
        if not articles:
            continue
        title = _clean(r.get("title"))
        items.append(
            EventCorefClusterInfo(
                cluster_id=cid,
                article_count=int(r.get("article_count") or 0),
                event_type=title or _clean(r.get("event_type") or r.get("event_family")),
                initiator=_clean(r.get("initiator")),
                target=_clean(r.get("target")),
                dominant_trigger=_clean(r.get("dominant_trigger")),
                cluster_quality=_clean(r.get("cluster_quality")),
                start_date=_date_to_str(r.get("start_date")),
                end_date=_date_to_str(r.get("end_date")),
                articles=articles,
            )
        )
        if len(items) >= page_size:
            break
    total_pages, has_next, has_prev = _pagination_response(total, int(getattr(params, "page", 1) or 1), page_size)
    return SearchResponse(
        data=[],
        total=total,
        page=int(getattr(params, "page", 1) or 1),
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=offset > 0,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=items,
        micro_story_items=[],
        macro_event_items=[],
    )


def _add_exclude_clause(
    clauses: List[str],
    bind: Dict[str, Any],
    columns: Sequence[str],
    value: Optional[str],
) -> None:
    raw = _clean(value)
    if not raw:
        return
    parsed = parse_supported_query(raw)
    if parsed is not None and _uses_boolean_ast(raw):
        predicate = _compile_query_ast_sql(
            parsed.root,
            columns,
            bind,
            "exclude_boolean",
            expand_aliases=False,
        )
        clauses.append(f"NOT ({predicate})")
        return
    explicit_phrase = _explicit_phrase_value(raw)
    terms = [explicit_phrase] if explicit_phrase else _query_match_terms(raw)
    for idx, term in enumerate(terms[:12]):
        variants = (
            [term]
            if explicit_phrase
            else _query_term_variants(term, expand_topics=False) or [term]
        )
        predicates: List[str] = []
        for variant_idx, variant in enumerate(variants[:10]):
            key = f"exclude_{idx}_{variant_idx}"
            if _needs_word_boundary_match(variant):
                bind[key] = rf"(^|[^[:alnum:]]){re.escape(_clean(variant))}([^[:alnum:]]|$)"
                predicates.extend(f"{col} ~* :{key}" for col in columns)
            else:
                bind[key] = f"%{variant}%"
                predicates.extend(f"{col} ILIKE :{key}" for col in columns)
        if predicates:
            clauses.append("NOT (" + " OR ".join(predicates) + ")")


def _add_news_filters(clauses: List[str], bind: Dict[str, Any], params: Any) -> None:
    clauses.append(_article_quality_where())
    columns = _text_columns_for_news(getattr(params, "hit_location", None))
    keyword = getattr(params, "keyword", None) or getattr(params, "topic", None)
    mode = _clean(getattr(params, "mode", "exact"))
    operator, _normalized_keyword = _text_match_mode(keyword, mode)
    expand_topics = mode == "fuzzy" and _clean(keyword).lower() not in FUZZY_LITERAL_TITLE_QUERIES
    _add_text_clause(
        clauses,
        bind,
        columns,
        keyword,
        "kw",
        operator,
        expand_aliases=expand_topics,
    )
    query_specific_where = "" if _uses_boolean_ast(keyword) else _query_specific_news_where(keyword)
    if query_specific_where:
        clauses.append(re.sub(r"^\s*AND\s+", "", query_specific_where.strip(), flags=re.I))
    _add_text_clause(clauses, bind, columns, getattr(params, "must_include", None), "must", "and")
    _add_text_clause(clauses, bind, columns, getattr(params, "any_include", None), "any", "or")
    _add_exclude_clause(clauses, bind, columns, getattr(params, "need_exclude", None))
    _add_time_filters(clauses, bind, params, "n.published_at")

    source = _clean(getattr(params, "data_source", None))
    if source:
        bind["data_source"] = f"%{source}%"
        clauses.append(
            "(COALESCE(msp.source_name, '') ILIKE :data_source "
            "OR COALESCE(ms.domain, '') ILIKE :data_source "
            "OR COALESCE(n.url, '') ILIKE :data_source)"
        )
    language = _normalize_language(getattr(params, "language", None))
    if language:
        bind["language"] = language
        clauses.append("LOWER(COALESCE(n.language, '')) = :language")


def _pagination_response(total: int, page: int, page_size: int) -> Tuple[int, bool, bool]:
    total_pages = math.ceil(total / page_size) if total and page_size else 0
    return total_pages, page < total_pages, page > 1


def _search_keyword(params: Any) -> str:
    return _clean(getattr(params, "keyword", None) or getattr(params, "topic", None) or getattr(params, "must_include", None))


def _should_expand_l1_aliases(keyword: Optional[str], mode: Optional[str]) -> bool:
    if _clean(mode).lower() != "fuzzy":
        return False
    query = _clean(keyword).lower()
    if not query:
        return False
    if query in L1_DIRECT_CLUSTER_QUERIES:
        return False
    terms = {query, *_split_terms(query)}
    return any(term in L1_ALIAS_EXPAND_QUERIES for term in terms)


def _l1_cluster_key(cluster_id: Any) -> str:
    value = str(cluster_id or "")
    match = re.search(r"([0-9a-f]{12,})$", value)
    return match.group(1) if match else value


def _l1_cluster_preference(cluster_id: Any) -> Tuple[int, str]:
    value = str(cluster_id or "")
    if "_v2_" in value:
        rank = 0
    elif "_v1_" in value:
        rank = 1
    elif "eval" in value:
        rank = 3
    elif "exp" in value:
        rank = 4
    else:
        rank = 2
    return rank, value


def _dedupe_l1_clusters(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chosen: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for idx, row in enumerate(rows):
        title_key = _clean(row.get("title") or row.get("event_type") or row.get("event_family")).lower()
        key = f"title:{title_key}" if len(title_key) >= 16 else _l1_cluster_key(row.get("cluster_id"))
        current = chosen.get(key)
        if current is None:
            chosen[key] = (idx, row)
            continue
        _, current_row = current
        if _l1_cluster_preference(row.get("cluster_id")) < _l1_cluster_preference(current_row.get("cluster_id")):
            chosen[key] = (idx, row)
    return [row for _, row in sorted(chosen.values(), key=lambda item: item[0])]


def _chain_ids_with_clean_articles(chain_ids: Sequence[str], keyword: Optional[str]) -> set:
    ids = [str(item) for item in chain_ids if item]
    if not ids:
        return set()
    keyword_sql, keyword_bind = _news_keyword_filter_sql(keyword, "chain_article_kw")
    stmt = text(
        f"""
        SELECT DISTINCT s.chain_id
        FROM public.event_l2_chain_segments s
        JOIN public.event_coref_members ecm ON ecm.cluster_id = s.l1_cluster_id
        JOIN public.news n ON n.id = ecm.news_id
        LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
        LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
        {_quality_news_join_sql()}
        WHERE s.chain_id IN :chain_ids
          AND {_article_quality_where()}
          AND {keyword_sql}
        LIMIT :limit
        """
    ).bindparams(bindparam("chain_ids", expanding=True))
    with _search_connection() as conn:
        rows = conn.execute(
            stmt,
            {"chain_ids": ids, "limit": min(len(ids) * 6, 600), **keyword_bind},
        ).mappings().all()
    return {str(row["chain_id"]) for row in rows}


def _cluster_ids_with_clean_articles(cluster_ids: Sequence[str], keyword: Optional[str] = None) -> set:
    ids = [str(item) for item in cluster_ids if item]
    if not ids:
        return set()
    keyword_sql, keyword_bind = _news_keyword_filter_sql(keyword, "cluster_article_kw")
    stmt = text(
        f"""
        SELECT DISTINCT ecm.cluster_id
        FROM public.event_coref_members ecm
        JOIN public.news n ON n.id = ecm.news_id
        LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
        LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
        {_quality_news_join_sql()}
        WHERE ecm.cluster_id IN :cluster_ids
          AND {_article_quality_where()}
          AND {keyword_sql}
        LIMIT :limit
        """
    ).bindparams(bindparam("cluster_ids", expanding=True))
    with _search_connection() as conn:
        rows = conn.execute(
            stmt,
            {"cluster_ids": ids, "limit": min(len(ids) * 5, 500), **keyword_bind},
        ).mappings().all()
    return {str(row["cluster_id"]) for row in rows}


def _macro_ids_with_clean_articles(macro_ids: Sequence[str], keyword: Optional[str]) -> set:
    ids = [str(item) for item in macro_ids if item]
    if not ids:
        return set()
    keyword_sql, keyword_bind = _news_keyword_filter_sql(keyword, "macro_article_kw")
    stmt = text(
        f"""
        SELECT DISTINCT mm.macro_id
        FROM public.event_l3_macro_members mm
        JOIN public.event_l2_chain_segments s ON s.chain_id = mm.l2_chain_id
        JOIN public.event_coref_members ecm ON ecm.cluster_id = s.l1_cluster_id
        JOIN public.news n ON n.id = ecm.news_id
        LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
        LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
        {_quality_news_join_sql()}
        WHERE mm.macro_id IN :macro_ids
          AND {_article_quality_where()}
          AND {keyword_sql}
        LIMIT :limit
        """
    ).bindparams(bindparam("macro_ids", expanding=True))
    with _search_connection() as conn:
        rows = conn.execute(
            stmt,
            {"macro_ids": ids, "limit": min(len(ids) * 8, 800), **keyword_bind},
        ).mappings().all()
    return {str(row["macro_id"]) for row in rows}


def get_news_stats_v2() -> Dict[str, Any]:
    with NEWS_ENGINE.connect() as conn:
        total = int(conn.execute(text("SELECT COUNT(*) FROM public.news")).scalar() or 0)
        lang_rows = conn.execute(
            text(
                """
                SELECT COALESCE(NULLIF(language, ''), 'unknown') AS language, COUNT(*) AS count
                FROM public.news
                GROUP BY COALESCE(NULLIF(language, ''), 'unknown')
                ORDER BY count DESC
                """
            )
        ).mappings().all()
    language_stats = [
        {
            "id": r["language"],
            "count": int(r["count"] or 0),
            "name": LANG_LABELS.get(r["language"], r["language"] or "未知"),
        }
        for r in lang_rows
    ]
    return {
        "total_news": total,
        "total_languages": len(language_stats),
        "language_stats": language_stats,
    }


def get_search_options_v2() -> Dict[str, Any]:
    with NEWS_ENGINE.connect() as conn:
        lang_rows = conn.execute(
            text(
                """
                SELECT COALESCE(NULLIF(language, ''), 'unknown') AS id, COUNT(*) AS count
                FROM public.news
                GROUP BY COALESCE(NULLIF(language, ''), 'unknown')
                ORDER BY count DESC
                LIMIT 60
                """
            )
        ).mappings().all()
        media_rows = conn.execute(
            text(
                """
                SELECT
                    COALESCE(NULLIF(msp.source_name, ''), NULLIF(ms.domain, ''), '未知来源') AS name,
                    COALESCE(NULLIF(ms.domain, ''), COALESCE(NULLIF(msp.source_name, ''), 'unknown')) AS domain,
                    COUNT(n.id) AS count
                FROM public.news n
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                GROUP BY 1, 2
                ORDER BY count DESC
                LIMIT 350
                """
            )
        ).mappings().all()
    language_options = [
        {"id": r["id"], "name": LANG_LABELS.get(r["id"], r["id"] or "未知")}
        for r in lang_rows
        if r["id"] and r["id"] != "unknown"
    ]
    media_sources = [
        {"name": _clean(r["name"]), "domain": _clean(r["domain"])}
        for r in media_rows
        if _clean(r["name"])
    ]
    data_sources = []
    seen = set()
    for row in media_sources:
        for value in (row["name"], row["domain"]):
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                data_sources.append(value)
    return {
        "language_options": language_options,
        "data_sources": data_sources,
        "media_sources": media_sources,
        "sites": ["新闻网站"],
    }


def list_news_v2(
    page: int,
    size: int,
    sort_by: Optional[str],
    sort_order: Optional[str],
    user: Optional[Dict[str, Any]],
    app_db: Optional[Session],
    favorite_scope_topic: Optional[str],
) -> NewsListResponse:
    page, size = _page_bounds(page, size)
    offset = (page - 1) * size
    direction = "ASC" if (sort_order or "desc").lower() == "asc" else "DESC"
    inner_order = f"n_sort.published_at {direction} NULLS LAST, q.news_id {direction}"
    outer_order = f"selected.published_at {direction} NULLS LAST, selected.news_id {direction}"
    if sort_by not in ("pub_time", "published_at"):
        inner_order = "n_sort.published_at DESC NULLS LAST, q.news_id DESC"
        outer_order = "selected.published_at DESC NULLS LAST, selected.news_id DESC"
    query_limit = size
    with NEWS_ENGINE.connect() as conn:
        total = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM public.news_quality_labels
                    WHERE is_good = TRUE
                      AND label_version = :label_version
                    """
                )
                ,
                {"label_version": QUALITY_LABEL_VERSION},
            ).scalar()
            or 0
        )
        rows = conn.execute(
            text(
                f"""
                WITH selected AS (
                    SELECT q.news_id, n_sort.published_at AS published_at
                    FROM public.news_quality_labels AS q
                    JOIN public.news AS n_sort
                      ON n_sort.id = q.news_id
                    WHERE q.is_good = TRUE
                      AND q.label_version = :label_version
		                    ORDER BY {inner_order}
                    LIMIT :limit OFFSET :offset
                )
	                """
	                + _news_select_sql()
	                + """
	                JOIN selected ON selected.news_id = n.id
	                ORDER BY """
	                + outer_order
	                + """
	                """
            ),
            {"label_version": QUALITY_LABEL_VERSION, "limit": query_limit, "offset": offset},
        ).mappings().all()
    row_dicts = [dict(r) for r in rows][:size]
    items = _news_items_from_rows(row_dicts, app_db, user, favorite_scope_topic)
    total_pages, has_next, has_prev = _pagination_response(total, page, size)
    return NewsListResponse(
        data=items,
        total=total,
        page=page,
        page_size=size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
    )


def get_news_bulk_by_ids_v2(
    ids: Sequence[int],
    user: Optional[Dict[str, Any]],
    app_db: Optional[Session],
    favorite_scope_topic: Optional[str],
) -> List[NewsItem]:
    id_list = [int(x) for x in ids if x is not None]
    if not id_list:
        return []
    stmt = text(
        _news_select_sql()
        + """
        WHERE n.id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    with NEWS_ENGINE.connect() as conn:
        rows = conn.execute(stmt, {"ids": id_list}).mappings().all()
    by_id = {int(r["id"]): dict(r) for r in rows}
    ordered = [by_id[nid] for nid in id_list if nid in by_id]
    return _news_items_from_rows(ordered, app_db, user, favorite_scope_topic)


def get_news_by_id_v2(news_id: int) -> Optional[NewsItem]:
    with NEWS_ENGINE.connect() as conn:
        row = conn.execute(
            text(
                _news_select_sql("n.body")
                + """
                WHERE n.id = :news_id
                LIMIT 1
                """
            ),
            {"news_id": news_id},
        ).mappings().first()
    if not row:
        return None
    return _news_items_from_rows([dict(row)], include_body=True)[0]


def _news_search_rows(params: Any, page: int, page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    title_match_result = _news_rows_from_title_matches(params, page, page_size)
    if title_match_result is not None:
        return title_match_result

    clauses = ["1=1"]
    bind: Dict[str, Any] = {}
    _add_news_filters(clauses, bind, params)
    where_sql = " AND ".join(clauses)
    offset = (page - 1) * page_size
    direction = "ASC" if (getattr(params, "sort_order", "desc") or "desc").lower() == "asc" else "DESC"
    order = f"n.published_at {direction} NULLS LAST, n.id {direction}"
    keyword = _clean(getattr(params, "keyword", None) or getattr(params, "topic", None))
    if (getattr(params, "sort_by", None) in (None, "", "similarity")) and keyword:
        bind["rank_kw"] = f"%{keyword}%"
        order = (
            "CASE WHEN COALESCE(n.title, '') ILIKE :rank_kw THEN 0 ELSE 1 END, "
            "n.published_at DESC NULLS LAST, n.id DESC"
    )
    query_limit = min(page_size * 5, 100)
    with _search_connection() as conn:
        rows = conn.execute(
            text(
                _news_select_sql(extra_select=", COUNT(*) OVER() AS total_count")
                + f"""
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT :limit OFFSET :offset
                """
            ),
            {**bind, "limit": query_limit, "offset": offset},
        ).mappings().all()
    total = int(rows[0].get("total_count") or 0) if rows else 0
    row_dicts = [dict(r) for r in rows][:page_size]
    return row_dicts, total


def _cluster_tree_for_news_rows(rows: Sequence[Dict[str, Any]]) -> List[ClusterTreeMacro]:
    news_ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    if not news_ids:
        return []
    news_by_id = {
        int(r["id"]): ClusterTreeNews(
            id=int(r["id"]),
            title=_clean(r.get("title")) or "无标题",
            pub_time=r.get("pub_time"),
            time_semantics=NewsResultTimeSemantics(
                published_at=r.get("pub_time")
            ),
        )
        for r in rows
    }
    stmt_members = text(
        """
        SELECT
            ecm.cluster_id,
            ecm.news_id,
            c.title,
            c.article_count,
            c.event_type,
            c.event_family,
            c.initiator,
            c.target,
            c.dominant_trigger,
            c.cluster_quality
        FROM public.event_coref_members ecm
        JOIN public.event_coref_clusters c ON c.cluster_id = ecm.cluster_id
        WHERE ecm.news_id IN :news_ids
        ORDER BY ecm.membership_score DESC NULLS LAST, ecm.published_at DESC NULLS LAST
        """
    ).bindparams(bindparam("news_ids", expanding=True))
    with _search_connection() as conn:
        member_rows = conn.execute(stmt_members, {"news_ids": news_ids}).mappings().all()
    if not member_rows:
        return []
    clusters: Dict[str, ClusterTreeMicro] = {}
    for r in member_rows:
        cid = str(r["cluster_id"])
        cluster = clusters.get(cid)
        if not cluster:
            cluster = ClusterTreeMicro(
                cluster_id=cid,
                title=_clean(r.get("title")),
                event_type=_clean(r.get("event_type") or r.get("event_family")),
                initiator=_clean(r.get("initiator")),
                target=_clean(r.get("target")),
                dominant_trigger=_clean(r.get("dominant_trigger")),
                cluster_quality=_clean(r.get("cluster_quality")),
                news_count=int(r.get("article_count") or 0),
                news=[],
            )
            clusters[cid] = cluster
        news_item = news_by_id.get(int(r["news_id"]))
        if news_item and all(existing.id != news_item.id for existing in cluster.news):
            cluster.news.append(news_item)

    cluster_ids = list(clusters.keys())
    stmt_chains = text(
        """
        SELECT DISTINCT
            s.l1_cluster_id AS cluster_id,
            ch.chain_id,
            ch.title,
            ch.segment_count,
            ch.article_count,
            ch.quality_score
        FROM public.event_l2_chain_segments s
        JOIN public.event_l2_chains ch ON ch.chain_id = s.chain_id
        WHERE s.l1_cluster_id IN :cluster_ids
        ORDER BY ch.quality_score DESC NULLS LAST, ch.article_count DESC NULLS LAST
        """
    ).bindparams(bindparam("cluster_ids", expanding=True))
    with _search_connection() as conn:
        chain_rows = conn.execute(stmt_chains, {"cluster_ids": cluster_ids}).mappings().all()

    story_map: Dict[str, ClusterTreeMacro] = {}
    assigned_clusters = set()
    for r in chain_rows:
        cid = str(r["cluster_id"])
        cluster = clusters.get(cid)
        if not cluster:
            continue
        sid = str(r["chain_id"])
        story = story_map.get(sid)
        if not story:
            story = ClusterTreeMacro(
                story_id=sid,
                title=_clean(r.get("title")) or sid,
                cluster_count=0,
                news_count=int(r.get("article_count") or 0),
                clusters=[],
            )
            story_map[sid] = story
        if cid not in assigned_clusters:
            story.clusters.append(cluster)
            story.cluster_count = len(story.clusters)
            assigned_clusters.add(cid)

    for cid, cluster in clusters.items():
        if cid in assigned_clusters:
            continue
        sid = f"unlinked-{cid}"
        story_map[sid] = ClusterTreeMacro(
            story_id=sid,
            title=cluster.title or cluster.event_type or cid,
            cluster_count=1,
            news_count=cluster.news_count,
            clusters=[cluster],
        )
    return list(story_map.values())


def _empty_search_response(params: Any, start_ts: float, page: int, page_size: int) -> SearchResponse:
    return SearchResponse(
        data=[],
        total=0,
        page=page,
        page_size=page_size,
        total_pages=0,
        has_next=False,
        has_prev=page > 1,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=[],
        micro_story_items=[],
        macro_event_items=[],
    )


def _cluster_text_filters(params: Any, columns: Sequence[str], expand_aliases: bool = False) -> Tuple[str, Dict[str, Any]]:
    clauses = ["1=1"]
    bind: Dict[str, Any] = {}
    keyword = getattr(params, "keyword", None) or getattr(params, "topic", None)
    mode = _clean(getattr(params, "mode", "exact"))
    operator, _normalized_keyword = _text_match_mode(keyword, mode)
    _add_text_clause(
        clauses,
        bind,
        columns,
        keyword,
        "kw",
        operator,
        expand_aliases=expand_aliases,
    )
    _add_text_clause(clauses, bind, columns, getattr(params, "must_include", None), "must", "and", expand_aliases=expand_aliases)
    _add_text_clause(clauses, bind, columns, getattr(params, "any_include", None), "any", "or", expand_aliases=expand_aliases)
    _add_exclude_clause(clauses, bind, columns, getattr(params, "need_exclude", None))
    return " AND ".join(clauses), bind


def _search_l1(params: Any, start_ts: float, user: Optional[Dict[str, Any]], app_db: Optional[Session]) -> SearchResponse:
    page, page_size = _page_bounds(params.page, params.page_size)
    offset = (page - 1) * page_size
    keyword = _search_keyword(params)
    mode = _clean(getattr(params, "mode", "exact"))
    expand_topic_aliases = _should_expand_l1_aliases(keyword, mode)
    if expand_topic_aliases and not _has_advanced_search_filters(params):
        title_match_response = _l1_clusters_from_title_matches(params, page_size, offset, user, app_db)
        if title_match_response is not None:
            return title_match_response

    if keyword.lower() in L1_DIRECT_CLUSTER_QUERIES:
        columns = ["COALESCE(c.title, '')"]
    else:
        columns = [
            "(COALESCE(c.title, '') || ' ' || COALESCE(c.event_domain, '') || ' ' || COALESCE(c.event_type, '') || ' ' || COALESCE(c.event_family, '') || ' ' || COALESCE(c.event_action, '') || ' ' || COALESCE(c.initiator, '') || ' ' || COALESCE(c.target, '') || ' ' || COALESCE(c.location, ''))",
        ]
    where_sql, bind = _cluster_text_filters(params, columns, expand_aliases=expand_topic_aliases)
    clauses = [where_sql]
    _add_time_filters(clauses, bind, params, "c.start_date", "c.start_date", "c.end_date")
    where_sql = " AND ".join(clauses)
    with _search_connection() as conn:
        raw_clusters = conn.execute(
            text(
                f"""
                SELECT
                    c.cluster_id,
                    c.title,
                    c.article_count,
                    c.event_type,
                    c.event_family,
                    c.initiator,
                    c.target,
                    c.dominant_trigger,
                    c.cluster_quality,
                    c.start_date,
                    c.end_date,
                    COUNT(*) OVER() AS total_count
                FROM public.event_coref_clusters c
                WHERE {where_sql}
                ORDER BY c.article_count DESC NULLS LAST, c.end_date DESC NULLS LAST, c.start_date DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {**bind, "limit": min(page_size * 25, 300), "offset": offset},
        ).mappings().all()
    total = int(raw_clusters[0].get("total_count") or 0) if raw_clusters else 0
    clusters = _dedupe_l1_clusters([dict(r) for r in raw_clusters])[: page_size * 8]
    cluster_ids = [str(r["cluster_id"]) for r in clusters]
    articles_by_cluster: Dict[str, List[NewsItem]] = {cid: [] for cid in cluster_ids}
    if cluster_ids:
        article_keyword = None if keyword.lower() in L1_DIRECT_CLUSTER_QUERIES else keyword
        article_keyword_sql, article_keyword_bind = _news_keyword_filter_sql(
            article_keyword,
            "l1_article_kw",
            expand_topics=expand_topic_aliases,
        )
        stmt = text(
            """
            SELECT *
            FROM (
                SELECT
                    ecm.cluster_id,
                    n.id,
                    COALESCE(NULLIF(n.title, ''), '') AS title,
                    LEFT(COALESCE(n.body, ''), 1200) AS body,
                    n.url AS request_url,
                    n.published_at AS pub_time,
                    n.language AS language_id,
                    n.region AS news_region,
                    n.author,
                    ms.domain,
                    COALESCE(NULLIF(msp.source_name, ''), NULLIF(ms.domain, ''), '') AS source_name,
                    msp.country AS source_country,
                    msp.region AS source_region,
                    msp.source_type,
                    msp.political_leaning,
                    msp.credibility_tier,
                    ROW_NUMBER() OVER (
                        PARTITION BY ecm.cluster_id
                        ORDER BY n.published_at DESC NULLS LAST, ecm.membership_score DESC NULLS LAST
                    ) AS rn
                FROM public.event_coref_members ecm
                JOIN public.news n ON n.id = ecm.news_id
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                """ + _quality_news_join_sql() + """
                WHERE ecm.cluster_id IN :cluster_ids
                  AND """ + _article_quality_where() + """
                  AND """ + article_keyword_sql + """
            ) ranked
            WHERE rn <= 8
            ORDER BY cluster_id, pub_time DESC NULLS LAST
            """
        ).bindparams(bindparam("cluster_ids", expanding=True))
        with _search_connection() as conn:
            article_rows = [
                dict(r)
                for r in conn.execute(
                    stmt,
                    {"cluster_ids": cluster_ids, **article_keyword_bind},
                ).mappings().all()
            ]
        for cid in cluster_ids:
            rows = [r for r in article_rows if str(r.get("cluster_id")) == cid]
            articles_by_cluster[cid] = _news_items_from_rows(rows, app_db, user, getattr(params, "favorite_scope_topic", None))

    items: List[EventCorefClusterInfo] = []
    for r in clusters:
        cid = str(r["cluster_id"])
        articles = articles_by_cluster.get(cid, [])
        if not articles:
            continue
        title = _clean(r.get("title"))
        items.append(
            EventCorefClusterInfo(
                cluster_id=cid,
                article_count=int(r.get("article_count") or 0),
                event_type=title or _clean(r.get("event_type") or r.get("event_family")),
                initiator=_clean(r.get("initiator")),
                target=_clean(r.get("target")),
                dominant_trigger=_clean(r.get("dominant_trigger")),
                cluster_quality=_clean(r.get("cluster_quality")),
                start_date=_date_to_str(r.get("start_date")),
                end_date=_date_to_str(r.get("end_date")),
                articles=articles,
            )
        )
        if len(items) >= page_size:
            break
    total_pages, has_next, has_prev = _pagination_response(total, page, page_size)
    return SearchResponse(
        data=[],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=items,
        micro_story_items=[],
        macro_event_items=[],
    )


def _search_l2(params: Any, start_ts: float) -> SearchResponse:
    page, page_size = _page_bounds(params.page, params.page_size)
    offset = (page - 1) * page_size
    mode = _clean(getattr(params, "mode", "exact"))
    columns = [
        "(COALESCE(ch.title, '') || ' ' || COALESCE(ch.family_group, '') || ' ' || COALESCE(ch.event_family, '') || ' ' || COALESCE(ch.event_action, '') || ' ' || COALESCE(ch.initiator, '') || ' ' || COALESCE(ch.target, '') || ' ' || COALESCE(ch.pair_key, ''))",
    ]
    where_sql, bind = _cluster_text_filters(params, columns, expand_aliases=mode == "fuzzy")
    clauses = [where_sql]
    _add_time_filters(clauses, bind, params, "ch.start_date", "ch.start_date", "ch.end_date")
    where_sql = " AND ".join(clauses)
    with _search_connection() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    ch.chain_id AS id,
                    ch.title,
                    ch.initiator,
                    ch.target,
                    ch.article_count,
                    ch.segment_count AS story_count,
                    ch.start_date,
                    ch.end_date,
                    ch.chain_quality,
                    ch.quality_score,
                    ch.family_group,
                    ch.event_family,
                    COUNT(*) OVER() AS total_count
                FROM public.event_l2_chains ch
                WHERE {where_sql}
                ORDER BY ch.quality_score DESC NULLS LAST, ch.article_count DESC NULLS LAST, ch.end_date DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {**bind, "limit": min(page_size * 5, 100), "offset": offset},
        ).mappings().all()
    total = int(rows[0].get("total_count") or 0) if rows else 0
    row_dicts = [dict(r) for r in rows]
    allowed_chain_ids = _chain_ids_with_clean_articles([str(r["id"]) for r in row_dicts], None)
    row_dicts = [r for r in row_dicts if str(r["id"]) in allowed_chain_ids][:page_size]
    macro_items = [
        MacroEventItem(
            id=str(r["id"]),
            title=_clean(r.get("title")) or str(r["id"]),
            initiator=_clean(r.get("initiator")) or None,
            target=_clean(r.get("target")) or None,
            article_count=int(r.get("article_count") or 0),
            story_count=int(r.get("story_count") or 0),
            start_date=_date_to_str(r.get("start_date")),
            end_date=_date_to_str(r.get("end_date")),
            chain_quality=_clean(r.get("chain_quality")),
            quality_score=float(r["quality_score"]) if r.get("quality_score") is not None else None,
            level="l2",
        )
        for r in row_dicts
    ]
    total_pages, has_next, has_prev = _pagination_response(total, page, page_size)
    return SearchResponse(
        data=[],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=[],
        micro_story_items=[],
        macro_event_items=macro_items,
    )


def _search_l3(params: Any, start_ts: float) -> SearchResponse:
    page, page_size = _page_bounds(params.page, params.page_size)
    offset = (page - 1) * page_size
    keyword = _search_keyword(params)
    mode = _clean(getattr(params, "mode", "exact"))
    if _should_expand_l1_aliases(keyword, mode):
        title_match_response = _macro_events_from_title_matches(params, page_size, offset, start_ts)
        if title_match_response is not None:
            return title_match_response

    columns = [
        "(COALESCE(m.title, '') || ' ' || COALESCE(m.summary, '') || ' ' || COALESCE(m.family_group, '') || ' ' || COALESCE(m.macro_key, ''))",
    ]
    where_sql, bind = _cluster_text_filters(params, columns, expand_aliases=mode == "fuzzy")
    clauses = [where_sql]
    _add_time_filters(clauses, bind, params, "m.start_date", "m.start_date", "m.end_date")
    where_sql = " AND ".join(clauses)
    with _search_connection() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    m.macro_id AS id,
                    m.title,
                    m.summary,
                    m.family_group,
                    m.article_count,
                    m.l2_chain_count AS story_count,
                    m.start_date,
                    m.end_date,
                    m.quality_score,
                    COUNT(*) OVER() AS total_count
                FROM public.event_l3_macro_events m
                WHERE {where_sql}
                ORDER BY m.quality_score DESC NULLS LAST, m.article_count DESC NULLS LAST, m.end_date DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {**bind, "limit": min(page_size * 5, 100), "offset": offset},
        ).mappings().all()
    total = int(rows[0].get("total_count") or 0) if rows else 0
    row_dicts = [dict(r) for r in rows]
    allowed_macro_ids = _macro_ids_with_clean_articles([str(r["id"]) for r in row_dicts], None)
    row_dicts = [r for r in row_dicts if str(r["id"]) in allowed_macro_ids][:page_size]
    macro_items = [
        MacroEventItem(
            id=str(r["id"]),
            title=_clean(r.get("title")) or str(r["id"]),
            # L3 has no audited initiator column. ``family_group`` is a
            # classification label and must not be presented as an actor.
            initiator=None,
            target=None,
            article_count=int(r.get("article_count") or 0),
            story_count=int(r.get("story_count") or 0),
            start_date=_date_to_str(r.get("start_date")),
            end_date=_date_to_str(r.get("end_date")),
            quality_score=float(r["quality_score"]) if r.get("quality_score") is not None else None,
            summary=_snippet(r.get("summary"), 360),
            level="l3",
        )
        for r in row_dicts
    ]
    total_pages, has_next, has_prev = _pagination_response(total, page, page_size)
    return SearchResponse(
        data=[],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        cluster_tree=[],
        event_coref_clusters=[],
        micro_story_items=[],
        macro_event_items=macro_items,
    )


def _effective_search_fields(params: Any) -> Tuple[List[str], str]:
    search_type = _clean(getattr(params, "search_type", "news")).lower() or "news"
    hit_location = _clean(getattr(params, "hit_location", "全文")) or "全文"
    if search_type == "news":
        if hit_location == "正文":
            return ["news.body"], "正文只匹配新闻正文。"
        if hit_location == "标题":
            return ["news.title"], "标题只匹配新闻标题。"
        if hit_location == "摘要":
            return ["news.title"], "当前新闻库没有独立摘要索引；兼容值“摘要”实际按标题匹配。"
        return ["news.title"], "兼容值“全文”当前采用标题优先快路径，不代表独立全文索引。"
    if search_type == "l1":
        return (
            ["event.title", "event.type", "event.family", "event.actors", "event.location"],
            "L1 查询匹配事件聚类的标题、类型、家族、参与方与地点组合字段；命中位置选项不用于层级检索。",
        )
    if search_type == "l2":
        return (
            ["trend.title", "trend.family", "trend.action", "trend.actors", "trend.pair_key"],
            "L2 查询匹配走势标题、事件家族/动作、参与方和 pair_key；命中位置选项不用于层级检索。",
        )
    return (
        ["macro.title", "macro.summary", "macro.family", "macro.key"],
        "L3 查询匹配大事件标题、摘要、家族和 macro_key；命中位置选项不用于层级检索。",
    )


def _time_explain(params: Any) -> SearchTimeSemantics:
    search_type = _clean(getattr(params, "search_type", "news")).lower() or "news"
    requested = _clean(
        getattr(params, "_requested_time_field", None)
        or getattr(params, "time_field", "auto")
    ) or "auto"
    relative = _clean(getattr(params, "publish_time", None))
    start = _clean(getattr(params, "start_time", None))
    end = _clean(getattr(params, "end_time", None))
    has_filter = bool((relative and relative != "不限") or start or end)
    if search_type == "news":
        predicate = (
            "published_at 按所选相对窗口及 start_time/end_time 的闭区间筛选。"
            if has_filter
            else "当前未应用时间限制；可筛选字段为 published_at。"
        )
        return SearchTimeSemantics(
            requested_field=requested,
            applied_field="public.news.published_at",
            label="新闻发布日期",
            predicate=predicate,
            timezone_note="未带偏移的 datetime-local 按数据库会话时间解释；相对范围以请求执行时刻计算。",
            unavailable_fields=["collected_at（采集时间）", "updated_at（更新时间）", "event_time（事件发生时间）"],
        )
    predicate = (
        "事件区间按 event.end_date >= start_time 且 event.start_date <= end_time 判断重叠；相对窗口约束 event.end_date。"
        if has_filter
        else "当前未应用时间限制；可筛选字段为事件 start_date/end_date 区间。"
    )
    return SearchTimeSemantics(
        requested_field=requested,
        applied_field="event.start_date/event.end_date",
        label="事件发生时间区间",
        predicate=predicate,
        timezone_note="未带偏移的 datetime-local 按数据库会话时间解释；相对范围以请求执行时刻计算。",
        unavailable_fields=["published_at（新闻发布日期）", "collected_at（采集时间）", "updated_at（更新时间）"],
    )


def _applied_filter_explain(params: Any) -> List[Dict[str, Any]]:
    filters: List[Dict[str, Any]] = []
    mappings = (
        ("must_include", "must_include", "all_terms"),
        ("any_include", "any_include", "any_term"),
        ("need_exclude", "need_exclude", "exclude_each_term"),
        ("publish_time", "relative_time", "relative_window"),
        ("start_time", "start_time", "inclusive_lower_bound"),
        ("end_time", "end_time", "inclusive_upper_bound"),
        ("data_source", "data_source", "contains"),
        ("language", "language", "equals_normalized_code"),
    )
    for attribute, field, operator in mappings:
        value = _clean(getattr(params, attribute, None))
        if value and value != "不限":
            if attribute in {"must_include", "any_include", "need_exclude"} and _uses_boolean_ast(value):
                operator = "boolean_ast_exclusion" if attribute == "need_exclude" else "boolean_ast"
            filters.append({"field": field, "operator": operator, "value": value})
    sort_by = _clean(getattr(params, "sort_by", None))
    if sort_by in {"published_at", "pub_time"}:
        direction = _clean(getattr(params, "sort_order", "desc")).lower() or "desc"
        operator = f"sort_{direction}"
        if sort_by == "pub_time":
            operator = f"legacy_alias_{operator}"
        filters.append(
            {
                "field": "published_at",
                "operator": operator,
                "value": sort_by,
            }
        )
    return filters


def _relaxation_suggestions(params: Any, total: int, explicit_phrase: bool) -> List[str]:
    suggestions: List[str] = []
    if total > 0:
        if any(resolve_entity_alias(term) for term in _query_match_terms(primary_query_text(params))):
            suggestions.append("可使用语言筛选查看别名展开后的各语言子集；当前结果总数包含列出的实体别名。")
        return suggestions
    mode = _clean(getattr(params, "mode", "exact")) or "exact"
    parsed = parse_supported_query(primary_query_text(params))
    if explicit_phrase:
        suggestions.append("可移除整句外层引号，改为“全部词”匹配；系统本次未自动移除引号。")
    elif parsed is not None and parsed.explicit_boolean:
        suggestions.append("可手动减少 AND/NOT 条件或扩大 OR 分支；系统本次未改写 Boolean AST。")
    elif mode == "exact" and len(_query_match_terms(primary_query_text(params))) > 1:
        suggestions.append("可切换到“主题扩展”执行 OR/主题别名召回；系统本次未自动切换模式。")
    if _clean(getattr(params, "language", None)):
        suggestions.append("可移除语言限制，检查其他语言子集；系统本次未自动放宽语言。")
    if any(
        _clean(getattr(params, field, None))
        for field in ("publish_time", "start_time", "end_time")
    ):
        suggestions.append("可扩大当前明确显示的时间字段范围；系统本次未改动时间条件。")
    if _clean(getattr(params, "data_source", None)):
        suggestions.append("可移除数据源限制，检查其他来源；系统本次未自动扩大来源。")
    if not suggestions:
        suggestions.append("请检查拼写或改用目录中已列出的实体别名；系统没有生成猜测性别名。")
    return suggestions


def _expanded_query_ast(node: QueryNode, *, expand_aliases: bool) -> Dict[str, Any]:
    if node.kind in {"term", "phrase"}:
        variants = _query_leaf_variants(node, expand_aliases=expand_aliases)
        payload: Dict[str, Any] = {
            "type": node.kind,
            "value": node.value,
            "match": "literal_phrase" if node.kind == "phrase" else "any_expanded_alias",
            "expanded_values": variants,
        }
        match = resolve_entity_alias(node.value) if node.kind == "term" else None
        if match is not None:
            payload.update(
                {
                    "entity_id": match.entity_id,
                    "entity_type": match.entity_type,
                    "review_status": match.review_status,
                }
            )
        return payload
    if node.kind == "not":
        return {
            "type": "not",
            "operand": _expanded_query_ast(
                node.children[0],
                expand_aliases=expand_aliases,
            ),
        }
    return {
        "type": node.kind,
        "children": [
            _expanded_query_ast(child, expand_aliases=expand_aliases)
            for child in node.children
        ],
    }


def _execution_expression(node: QueryNode, *, expand_aliases: bool) -> str:
    if node.kind in {"term", "phrase"}:
        variants = _query_leaf_variants(node, expand_aliases=expand_aliases)
        values = ", ".join(json.dumps(value, ensure_ascii=False) for value in variants)
        function = "PHRASE" if node.kind == "phrase" else "ANY"
        return f"{function}({values})"
    if node.kind == "not":
        return f"NOT ({_execution_expression(node.children[0], expand_aliases=expand_aliases)})"
    operator = f" {node.kind.upper()} "
    return "(" + operator.join(
        _execution_expression(child, expand_aliases=expand_aliases)
        for child in node.children
    ) + ")"


def _query_terms_for_entity_resolution(value: Any) -> List[str]:
    parsed = parse_supported_query(value)
    if parsed is None:
        return []
    return [
        node.value
        for node, _negated in iter_query_leaves(parsed.root)
        if node.kind == "term"
    ]


def _positive_literal_terms_for_hit_display(params: Any) -> List[str]:
    """Return only submitted positive leaves; aliases remain undisclosed spans."""

    values = [
        primary_query_text(params),
        getattr(params, "must_include", None),
        getattr(params, "any_include", None),
    ]
    terms: List[str] = []
    seen: set[str] = set()
    for value in values:
        parsed = parse_supported_query(value)
        if parsed is None:
            continue
        for node, negated in iter_query_leaves(parsed.root):
            term = _clean(node.value)
            folded = term.casefold()
            if negated or not term or folded in seen:
                continue
            seen.add(folded)
            terms.append(term)
            if len(terms) >= int(QUERY_LIMITS["max_terms"]):
                return terms
    return terms


def _effective_explain_root(parsed: Any, *, mode: str) -> QueryNode:
    root = parsed.root
    if (
        mode == "fuzzy"
        and not _uses_boolean_ast(parsed.raw)
        and root.kind == "and"
    ):
        # This is the established fuzzy contract: a plain, unstructured list
        # of terms is an OR topic expansion. An explicitly structured Boolean
        # expression always retains its submitted operators.
        return QueryNode("or", children=root.children)
    return root


def _build_query_explain(params: Any, total: int) -> SearchQueryExplain:
    raw_query = primary_query_text(params)
    parsed = parse_supported_query(raw_query)
    explicit_phrase = bool(parsed is not None and parsed.root.kind == "phrase")
    mode = _clean(getattr(params, "mode", "exact")) or "exact"
    search_type = _clean(getattr(params, "search_type", "news")).lower() or "news"
    if search_type == "news":
        expand_topics = mode == "fuzzy" and raw_query.lower() not in FUZZY_LITERAL_TITLE_QUERIES
    elif search_type == "l1" or mode == "event_coref":
        expand_topics = _should_expand_l1_aliases(raw_query, mode)
    else:
        expand_topics = mode == "fuzzy"
    if parsed is None:
        query_ast: Dict[str, Any] = {"type": "empty"}
        expanded_query_ast: Dict[str, Any] = {"type": "empty"}
        execution_expression = ""
        normalized_terms: List[str] = []
        expanded_terms: List[str] = []
        limits = {
            **QUERY_LIMITS,
            "observed_query_chars": 0,
            "observed_tokens": 0,
            "observed_ast_nodes": 0,
            "observed_terms": 0,
            "observed_nesting_depth": 0,
        }
    else:
        effective_root = _effective_explain_root(parsed, mode=mode)
        query_ast = effective_root.as_dict()
        expanded_query_ast = _expanded_query_ast(
            effective_root,
            expand_aliases=expand_topics,
        )
        execution_expression = _execution_expression(
            effective_root,
            expand_aliases=expand_topics,
        )
        normalized_terms = [
            node.value for node, _negated in iter_query_leaves(parsed.root)
        ]
        expanded_terms = []
        for node, _negated in iter_query_leaves(parsed.root):
            for variant in _query_leaf_variants(node, expand_aliases=expand_topics):
                if variant.casefold() not in {item.casefold() for item in expanded_terms}:
                    expanded_terms.append(variant)
        limits = parsed.limits_dict()

    expansions: List[SearchEntityExpansion] = []
    seen_entities: set[Tuple[str, str]] = set()
    expansion_inputs = [
        ("primary_query", _query_terms_for_entity_resolution(raw_query)),
        ("must_include", _query_terms_for_entity_resolution(getattr(params, "must_include", None))),
        ("any_include", _query_terms_for_entity_resolution(getattr(params, "any_include", None))),
        ("need_exclude", _query_terms_for_entity_resolution(getattr(params, "need_exclude", None))),
    ]
    for query_field, terms in expansion_inputs:
        for term in terms:
            match = resolve_entity_alias(term)
            identity = (query_field, match.entity_id) if match is not None else None
            if match is None or identity in seen_entities:
                continue
            seen_entities.add(identity)
            expansions.append(
                SearchEntityExpansion(
                    query_field=query_field,
                    **match.as_explain_dict(),
                )
            )

    mode_semantics = {
        "exact": "按 boolean-v1 AST 执行；隐式空格与 AND 等价，优先级为 NOT、AND、OR；实体别名仅在叶节点内按 OR。",
        "fuzzy": "显式 boolean-v1 AST 保留运算符；未写运算符的多个主题词沿用 OR，并在叶节点内展开已列出的主题/实体别名；不是向量相似度。",
        "cluster": "先按全部词规则检索新闻，再把当前页已关联新闻组织为簇树。",
        "event_coref": "按全部词规则匹配 L1 事件共核字段。",
    }.get(mode, "按请求模式执行")
    if explicit_phrase:
        mode_semantics = "仅匹配外层引号中的完整短语；实体和主题别名均未展开。"

    fields, field_note = _effective_search_fields(params)
    stages = [
        SearchExplainStage(
            stage="parse",
            status="executed",
            matched_count=len(normalized_terms),
            count_semantics="parsed_term_count",
            detail=(
                f"{QUERY_LANGUAGE_VERSION} 已执行；AST {limits['observed_ast_nodes']}/"
                f"{limits['max_ast_nodes']} 节点，括号深度 {limits['observed_nesting_depth']}/"
                f"{limits['max_nesting_depth']}；不是文档命中数。"
            ),
        ),
        SearchExplainStage(
            stage="entity_resolution",
            status="executed",
            matched_count=len(expansions),
            count_semantics="resolved_entity_count",
            detail=f"仅使用版本化目录 {ENTITY_ALIAS_CATALOG_VERSION}；未命中的文本不会猜测实体。",
        ),
        SearchExplainStage(
            stage="retrieval",
            status="executed",
            matched_count=max(int(total or 0), 0),
            count_semantics="api_response_total",
            detail="这是 API 返回的总数；候选生成等中间阶段尚未埋点，因此不伪造中间命中数。",
        ),
        SearchExplainStage(
            stage="relaxation",
            status="not_run",
            matched_count=None,
            count_semantics=None,
            detail="系统没有自动放宽查询；建议仅供用户手动选择后重新检索。",
        ),
    ]
    return SearchQueryExplain(
        query_language=QUERY_LANGUAGE_VERSION,
        raw_query=raw_query,
        query_ast=query_ast,
        expanded_query_ast=expanded_query_ast,
        execution_expression=execution_expression or render_query_ast(parsed.root) if parsed is not None else "",
        limits=limits,
        normalized_terms=[_clean(term) for term in normalized_terms if _clean(term)],
        expanded_terms=expanded_terms,
        explicit_phrase=explicit_phrase,
        requested_mode=mode,
        effective_mode=mode,
        mode_semantics=mode_semantics,
        search_type=search_type,
        requested_hit_location=_clean(getattr(params, "hit_location", "全文")) or "全文",
        effective_search_fields=fields,
        hit_location_note=field_note,
        alias_catalog_version=ENTITY_ALIAS_CATALOG_VERSION,
        entity_expansions=expansions,
        applied_filters=_applied_filter_explain(params),
        time=_time_explain(params),
        stages=stages,
        relaxation_suggestions=_relaxation_suggestions(params, total, explicit_phrase),
        automatic_relaxation=False,
        unsupported_syntax=[],
    )


def search_dashboard_v2(
    params: Any,
    user: Optional[Dict[str, Any]],
    app_db: Optional[Session],
    start_ts: Optional[float] = None,
) -> SearchResponse:
    started = start_ts or time.time()
    with _search_budget(started):
        page, page_size = _page_bounds(params.page, params.page_size)
        st = _clean(getattr(params, "search_type", "news")).lower()
        query_explain: Optional[SearchQueryExplain] = None
        if st == "l1" or _clean(getattr(params, "mode", "")) == "event_coref":
            response = _search_l1(params, started, user, app_db)
        elif st == "l2":
            response = _search_l2(params, started)
        elif st == "l3":
            response = _search_l3(params, started)
        else:
            rows, total = _news_search_rows(params, page, page_size)
            query_explain = _build_query_explain(params, total)
            items = _news_items_from_rows(
                rows,
                app_db,
                user,
                getattr(params, "favorite_scope_topic", None),
                positive_literal_terms=_positive_literal_terms_for_hit_display(params),
                effective_search_fields=query_explain.effective_search_fields,
            )
            total_pages, has_next, has_prev = _pagination_response(total, page, page_size)
            cluster_tree = _cluster_tree_for_news_rows(rows) if _clean(getattr(params, "mode", "")) == "cluster" else []
            response = SearchResponse(
                data=items,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                has_next=has_next,
                has_prev=has_prev,
                query_time_ms=(time.time() - started) * 1000,
                cluster_tree=cluster_tree,
                event_coref_clusters=[],
                micro_story_items=[],
                macro_event_items=[],
            )
        response.query_explain = query_explain or _build_query_explain(
            params,
            response.total,
        )
        response.query_receipt = build_query_receipt(
            params,
            response,
            response.query_explain,
        )
        return response


def _news_analysis_metadata(news_item: NewsItem) -> List[Dict[str, str]]:
    times = news_item.time_semantics
    event_values = [
        value.strftime("%Y-%m-%d %H:%M:%S")
        for value in (times.event_time_start, times.event_time_end)
        if value is not None
    ]
    items: List[Dict[str, str]] = [
        {"key": "news_id", "label": "新闻 ID", "value": str(news_item.id)},
        {
            "key": "published_at",
            "label": "新闻发布日期",
            "value": times.published_at.strftime("%Y-%m-%d %H:%M:%S") if times.published_at else "—",
        },
        {
            "key": "event_time",
            "label": "事件时间",
            "value": " 至 ".join(event_values) if event_values else "—",
        },
        {
            "key": "collected_at",
            "label": "采集时间",
            "value": times.collected_at.strftime("%Y-%m-%d %H:%M:%S") if times.collected_at else "—",
        },
        {
            "key": "updated_at",
            "label": "更新时间",
            "value": times.updated_at.strftime("%Y-%m-%d %H:%M:%S") if times.updated_at else "—",
        },
        {"key": "source", "label": "来源", "value": news_item.source or "—"},
        {
            "key": "language",
            "label": "语言代码",
            "value": _clean(news_item.language_id) or "—",
        },
    ]
    geographic_fields = (
        ("source_country", "来源国（未权威核验）"),
        ("source_region", "来源地区（未权威核验）"),
        ("news_region", "新闻地区（语义未映射）"),
        ("location", "位置（记录值，未核验）"),
    )
    for key, label in geographic_fields:
        value = _clean(getattr(news_item, key, None))
        if value:
            items.append({"key": key, "label": label, "value": value})
    return items


def get_news_analysis_v2(news_id: int) -> Dict[str, Any]:
    news_item = get_news_by_id_v2(news_id)
    if not news_item:
        return {"items": [], "l1_clusters": [], "china_analysis": None, "event_extraction": None, "trend": []}

    items = _news_analysis_metadata(news_item)
    event_extraction = None
    china_analysis = None
    with NEWS_ENGINE.connect() as conn:
        extraction_row = conn.execute(
            text(
                """
                SELECT *
                FROM public.news_l1_event_extractions
                WHERE news_id = :news_id
                ORDER BY updated_at DESC NULLS LAST, extracted_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"news_id": news_id},
        ).mappings().first()
        if extraction_row:
            raw_response = extraction_row.get("raw_response")
            parsed_raw = None
            if raw_response:
                try:
                    parsed_raw = json.loads(raw_response)
                except Exception:
                    parsed_raw = raw_response
            event_extraction = {
                "news_id": int(extraction_row["news_id"]),
                "language": _clean(extraction_row.get("language")),
                "region": _clean(extraction_row.get("region")),
                "event_domain": _clean(extraction_row.get("event_domain")),
                "event_family": _clean(extraction_row.get("event_family")),
                "event_action": _clean(extraction_row.get("event_action")),
                "initiator": _clean(extraction_row.get("initiator")),
                "target": _clean(extraction_row.get("target")),
                "location": _clean(extraction_row.get("location")),
                "tone": _clean(extraction_row.get("tone")),
                "canonical_initiator": _clean(extraction_row.get("canonical_initiator")),
                "canonical_target": _clean(extraction_row.get("canonical_target")),
                "entity_pair_key": _clean(extraction_row.get("entity_pair_key")),
                "parse_success": bool(extraction_row.get("parse_success")),
                "extraction_error": _clean(extraction_row.get("extraction_error")),
                "processor_version": _clean(extraction_row.get("processor_version")),
                "extracted_at": _date_to_str(extraction_row.get("extracted_at")),
                "updated_at": _date_to_str(extraction_row.get("updated_at")),
                "raw_response": parsed_raw,
            }
            items.extend(
                [
                    {"key": "event_family", "label": "L1 事件族", "value": event_extraction["event_family"] or "—"},
                    {"key": "event_action", "label": "L1 行动", "value": event_extraction["event_action"] or "—"},
                    {"key": "entity_pair_key", "label": "实体对", "value": event_extraction["entity_pair_key"] or "—"},
                    {"key": "tone", "label": "事件语气", "value": event_extraction["tone"] or "—"},
                ]
            )

        score_row = conn.execute(
            text(
                """
                SELECT *
                FROM public.china_opinion_article_scores
                WHERE news_id = :news_id
                ORDER BY updated_at DESC NULLS LAST, scored_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"news_id": news_id},
        ).mappings().first()
        if score_row:
            impact_index = round(
                float(score_row.get("stance_score") or 0.0)
                * float(score_row.get("article_weight") or 0.0)
                * 100.0,
                1,
            )
            china_analysis = {
                "source": "china_opinion_article_scores",
                "is_china_related": float(score_row.get("relevance_score") or 0.0) >= 0.35,
                "relevance_score": round(float(score_row.get("relevance_score") or 0.0), 3),
                "china_index": round(float(score_row.get("relevance_score") or 0.0), 3),
                "china_role": _clean(score_row.get("china_role")),
                "directness": _clean(score_row.get("directness")),
                "directness_score": round(float(score_row.get("directness_score") or 0.0), 3),
                "stance_score": round(float(score_row.get("stance_score") or 0.0), 3),
                "confidence": round(float(score_row.get("confidence") or 0.0), 3),
                "article_weight": round(float(score_row.get("article_weight") or 0.0), 3),
                "impact_index": impact_index,
                "polarity": "positive" if impact_index > 0 else "negative" if impact_index < 0 else "neutral",
                "tone": _clean(score_row.get("tone")),
                "target_scope": _clean(score_row.get("target_scope")),
                "evidence": _clean(score_row.get("evidence")),
                "method_version": _clean(score_row.get("method_version")),
                "updated_at": _date_to_str(score_row.get("updated_at")),
            }
        else:
            china_analysis = _china_detail_role_and_score(news_item, event_extraction)

        l1_rows = conn.execute(
            text(
                """
                SELECT
                    c.cluster_id,
                    c.title,
                    c.article_count,
                    c.event_type,
                    c.event_family,
                    c.initiator,
                    c.target,
                    c.start_date,
                    c.end_date
                FROM public.event_coref_members ecm
                JOIN public.event_coref_clusters c ON c.cluster_id = ecm.cluster_id
                WHERE ecm.news_id = :news_id
                ORDER BY ecm.membership_score DESC NULLS LAST, c.article_count DESC NULLS LAST
                LIMIT 8
                """
            ),
            {"news_id": news_id},
        ).mappings().all()

    l1_clusters = [
        {
            "id": str(r["cluster_id"]),
            "title": _clean(r.get("title") or r.get("event_type") or r.get("event_family") or r["cluster_id"]),
            "article_count": int(r.get("article_count") or 0),
            "cluster_count": 0,
            "start_date": _date_to_str(r.get("start_date")),
            "end_date": _date_to_str(r.get("end_date")),
            "initiator": _clean(r.get("initiator")),
            "target": _clean(r.get("target")),
        }
        for r in l1_rows
    ]
    if l1_clusters:
        items.append(
            {
                "key": "l1_clusters",
                "label": "所属 L1 事件",
                "value": "、".join(c["title"] for c in l1_clusters[:3]),
            }
        )
        items.append({"key": "has_event_cluster", "label": "是否有事件归属", "value": "是"})
    else:
        items.append({"key": "has_event_cluster", "label": "是否有事件归属", "value": "否"})

    if china_analysis:
        items.extend(
            [
                {"key": "is_china_related", "label": "是否涉华", "value": "是" if china_analysis.get("is_china_related") else "否"},
                {"key": "china_relevance_score", "label": "涉华指数", "value": china_analysis.get("relevance_score")},
                {"key": "china_impact_sentiment", "label": "涉华影响", "value": china_analysis.get("impact_index")},
                {"key": "china_directness", "label": "涉华直接性", "value": china_analysis.get("directness") or "—"},
            ]
        )

    l2_chains = []
    if l1_clusters:
        stmt = text(
            """
            SELECT DISTINCT
                ch.chain_id,
                ch.title,
                ch.article_count,
                ch.segment_count,
                ch.start_date,
                ch.end_date
            FROM public.event_l2_chain_segments s
            JOIN public.event_l2_chains ch ON ch.chain_id = s.chain_id
            WHERE s.l1_cluster_id IN :cluster_ids
            ORDER BY ch.article_count DESC NULLS LAST
            LIMIT 6
            """
        ).bindparams(bindparam("cluster_ids", expanding=True))
        with NEWS_ENGINE.connect() as conn:
            chain_rows = conn.execute(stmt, {"cluster_ids": [c["id"] for c in l1_clusters]}).mappings().all()
        l2_chains = [
            {
                "id": str(r["chain_id"]),
                "title": _clean(r.get("title") or r["chain_id"]),
                "article_count": int(r.get("article_count") or 0),
                "segment_count": int(r.get("segment_count") or 0),
                "start_date": _date_to_str(r.get("start_date")),
                "end_date": _date_to_str(r.get("end_date")),
            }
            for r in chain_rows
        ]
        if l2_chains:
            items.append(
                {
                    "key": "l2_chains",
                    "label": "所属 L2 走势",
                    "value": "、".join(c["title"] for c in l2_chains[:3]),
                }
            )

    l3_macros = []
    if l2_chains:
        stmt = text(
            """
            SELECT DISTINCT
                m.macro_id,
                m.title,
                m.article_count,
                m.l2_chain_count,
                m.start_date,
                m.end_date
            FROM public.event_l3_macro_members mm
            JOIN public.event_l3_macro_events m ON m.macro_id = mm.macro_id
            WHERE mm.l2_chain_id IN :chain_ids
            ORDER BY m.article_count DESC NULLS LAST
            LIMIT 5
            """
        ).bindparams(bindparam("chain_ids", expanding=True))
        with NEWS_ENGINE.connect() as conn:
            macro_rows = conn.execute(stmt, {"chain_ids": [c["id"] for c in l2_chains]}).mappings().all()
        l3_macros = [
            {
                "id": str(r["macro_id"]),
                "title": _clean(r.get("title") or r["macro_id"]),
                "article_count": int(r.get("article_count") or 0),
                "l2_chain_count": int(r.get("l2_chain_count") or 0),
                "start_date": _date_to_str(r.get("start_date")),
                "end_date": _date_to_str(r.get("end_date")),
            }
            for r in macro_rows
        ]
        if l3_macros:
            items.append(
                {
                    "key": "l3_macros",
                    "label": "所属 L3 大事件",
                    "value": "、".join(c["title"] for c in l3_macros[:3]),
                }
            )

    trend = []
    if l1_clusters:
        with NEWS_ENGINE.connect() as conn:
            trend_rows = conn.execute(
                text(
                    """
	                    SELECT DATE(n.published_at) AS day, COUNT(*) AS article_count
	                    FROM public.event_coref_members ecm
	                    JOIN public.news n ON n.id = ecm.news_id
	                    """ + _quality_news_join_sql() + """
	                    WHERE ecm.cluster_id = :cluster_id
	                      AND n.published_at IS NOT NULL
                    GROUP BY DATE(n.published_at)
                    ORDER BY day
                    """
                ),
                {"cluster_id": l1_clusters[0]["id"]},
            ).mappings().all()
        trend = [
            {
                "date": str(r["day"]),
                "article_count": int(r["article_count"] or 0),
                "avg_sentiment": None,
                "avg_china_index": None,
            }
            for r in trend_rows
        ]
    return {
        "items": items,
        "l1_clusters": l1_clusters,
        "l2_chains": l2_chains,
        "l3_macros": l3_macros,
        "china_analysis": china_analysis,
        "event_extraction": event_extraction,
        "trend": trend,
    }


def expand_cluster_children_v2(
    item_id: str,
    level: str,
    page: int,
    page_size: int,
    user: Optional[Dict[str, Any]] = None,
    app_db: Optional[Session] = None,
) -> Dict[str, Any]:
    page, page_size = _page_bounds(page, page_size)
    offset = (page - 1) * page_size
    parent_level = _clean(level).lower()
    if parent_level in ("macro", "l2"):
        with NEWS_ENGINE.connect() as conn:
            total = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM public.event_l2_chain_segments WHERE chain_id = :item_id"),
                    {"item_id": item_id},
                ).scalar()
                or 0
            )
            rows = conn.execute(
                text(
                    """
                    SELECT
                        COALESCE(s.l1_cluster_id, s.segment_id) AS id,
                        COALESCE(c.title, s.story_angle, s.segment_id) AS title,
                        COALESCE(c.article_count, s.article_count, 0) AS article_count,
                        c.event_type,
                        COALESCE(c.event_family, s.event_family) AS event_family,
                        COALESCE(c.initiator, '') AS initiator,
                        COALESCE(c.target, '') AS target,
                        COALESCE(c.start_date, s.start_date) AS start_date,
                        COALESCE(c.end_date, s.end_date) AS end_date
                    FROM public.event_l2_chain_segments s
                    LEFT JOIN public.event_coref_clusters c ON c.cluster_id = s.l1_cluster_id
                    WHERE s.chain_id = :item_id
                    ORDER BY s.segment_order ASC NULLS LAST, s.start_date ASC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"item_id": item_id, "limit": page_size, "offset": offset},
            ).mappings().all()
        row_dicts = _dedupe_l1_clusters([dict(r) for r in rows])
        allowed_cluster_ids = _cluster_ids_with_clean_articles([str(r["id"]) for r in row_dicts])
        row_dicts = [r for r in row_dicts if str(r["id"]) in allowed_cluster_ids][:page_size]
        return {
            "items": [
                {
                    "id": str(r["id"]),
                    "title": _clean(r.get("title") or r["id"]),
                    "level": "cluster",
                    "article_count": int(r.get("article_count") or 0),
                    "children_count": int(r.get("article_count") or 0),
                    "children": [],
                    "initiator": _clean(r.get("initiator")),
                    "target": _clean(r.get("target")),
                    "start_date": _date_to_str(r.get("start_date")),
                    "end_date": _date_to_str(r.get("end_date")),
                    "event_type": _clean(r.get("event_type") or r.get("event_family")),
                }
                for r in row_dicts
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "parent_level": parent_level,
            "child_level": "cluster",
        }

    if parent_level == "l3":
        with NEWS_ENGINE.connect() as conn:
            total = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM public.event_l3_macro_members WHERE macro_id = :item_id"),
                    {"item_id": item_id},
                ).scalar()
                or 0
            )
            rows = conn.execute(
                text(
                    """
                    SELECT
                        mm.l2_chain_id AS id,
                        COALESCE(ch.title, mm.title, mm.l2_chain_id) AS title,
                        COALESCE(ch.article_count, mm.article_count, 0) AS article_count,
                        COALESCE(ch.segment_count, mm.segment_count, 0) AS children_count,
                        COALESCE(ch.start_date, mm.start_date) AS start_date,
                        COALESCE(ch.end_date, mm.end_date) AS end_date,
                        ch.initiator,
                        ch.target,
                        mm.role,
                        mm.lane
                    FROM public.event_l3_macro_members mm
                    LEFT JOIN public.event_l2_chains ch ON ch.chain_id = mm.l2_chain_id
                    WHERE mm.macro_id = :item_id
                    ORDER BY mm.node_order ASC NULLS LAST, mm.importance_score DESC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"item_id": item_id, "limit": page_size, "offset": offset},
            ).mappings().all()
        row_dicts = [dict(r) for r in rows]
        allowed_chain_ids = _chain_ids_with_clean_articles([str(r["id"]) for r in row_dicts], None)
        row_dicts = [r for r in row_dicts if str(r["id"]) in allowed_chain_ids][:page_size]
        return {
            "items": [
                {
                    "id": str(r["id"]),
                    "title": _clean(r.get("title") or r["id"]),
                    "level": "l2",
                    "article_count": int(r.get("article_count") or 0),
                    "children_count": int(r.get("children_count") or 0),
                    "children": [],
                    "initiator": _clean(r.get("initiator")) or None,
                    "target": _clean(r.get("target")) or None,
                    "start_date": _date_to_str(r.get("start_date")),
                    "end_date": _date_to_str(r.get("end_date")),
                    "event_type": _clean(r.get("role") or r.get("lane")),
                }
                for r in row_dicts
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "parent_level": parent_level,
            "child_level": "l2",
        }

    if parent_level in ("cluster", "micro", "l1"):
        with NEWS_ENGINE.connect() as conn:
            total = int(
                conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM public.event_coref_members ecm
                        JOIN public.news n ON n.id = ecm.news_id
                        LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                        {_quality_news_join_sql()}
                        WHERE ecm.cluster_id = :item_id
                          AND {_article_quality_where()}
                        """
                    ),
                    {"item_id": item_id},
                ).scalar()
                or 0
            )
            rows = conn.execute(
                text(
                    _news_select_sql()
                    + """
                    JOIN public.event_coref_members ecm_filter ON ecm_filter.news_id = n.id
                    WHERE ecm_filter.cluster_id = :item_id
                      AND """
                    + _article_quality_where()
                    + """
                    ORDER BY n.published_at DESC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"item_id": item_id, "limit": min(page_size * 4, 100), "offset": offset},
            ).mappings().all()
        clean_rows = [dict(r) for r in rows][:page_size]
        news_items = _news_items_from_rows(
            clean_rows,
            app_db,
            user,
            None,
        )
        return {
            "items": [item.model_dump() for item in news_items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "parent_level": parent_level,
            "child_level": "news",
        }

    return {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "parent_level": parent_level,
        "child_level": "",
    }
