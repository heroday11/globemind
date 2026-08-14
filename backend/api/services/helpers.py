import os
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, exists, or_, func, and_, not_, text, case, literal
from sqlalchemy import inspect
from api.orm import models
from api.core.db import get_db
from urllib.parse import urlparse

from fastapi import HTTPException
from api.models.schemas import SearchRequest


# ================== 辅助函数 ==================

def parse_time_range(publish_time: Optional[str]) -> tuple:
    if not publish_time: return None, None
    now = datetime.now()
    ranges = {
        '近一天': timedelta(days=1),
        '近一周': timedelta(weeks=1),
        '近一月': timedelta(days=30),
        '近三月': timedelta(days=90),
        '近一年': timedelta(days=365),
    }
    if publish_time in ranges:
        start_time = now - ranges[publish_time]
        return start_time, now
    return None, None


def extract_source_from_url(url: str) -> str:
    if not url: return ""
    try:
        domain = urlparse(url).netloc
        domain = re.sub(r'^www\.', '', domain)
        parts = domain.split('.')
        if len(parts) >= 2: return parts[-2].capitalize()
        return domain
    except:
        return ""


def _extract_domain_source(domain: str) -> str:
    """从域名提取可读的数据源名称。"""
    if not domain:
        return ""
    domain = domain.lower()
    # 去掉 www 前缀
    domain = re.sub(r'^www\.', '', domain)
    parts = domain.split('.')
    # 取主域名部分（如 xxxx.com -> xxxx, news.xxxx.com -> xxxx）
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return parts[0].capitalize() if parts else ""


_LANG_CODE_MAP = {
    "US": "英语", "CN": "中文", "RU": "俄语", "ZA": "英语", "NZ": "英语",
    "AU": "英语", "GB": "英语", "CA": "英语", "TL": "外语",
    "IN": "印地语", "BR": "葡萄牙语", "JP": "日语", "KR": "韩语",
    "FR": "法语", "DE": "德语", "IT": "意大利语", "ES": "西班牙语",
    "PT": "葡萄牙语", "NL": "荷兰语", "SE": "瑞典语", "NO": "挪威语",
    "DK": "丹麦语", "FI": "芬兰语", "PL": "波兰语", "TR": "土耳其语",
    "SA": "阿拉伯语", "AE": "阿拉伯语", "EG": "阿拉伯语", "IL": "希伯来语",
    "TW": "中文", "HK": "中文", "SG": "英语",
}

def get_language_name(language: Optional[str]) -> str:
    if not language:
        return "未知"
    return _LANG_CODE_MAP.get(str(language).strip().upper(), str(language))


def get_language_name_map(languages: List[Optional[str]]) -> Dict[str, str]:
    return {lang: get_language_name(lang) for lang in languages if lang}


def get_user_favorite_sets_for_scope(
    db: Session,
    user_id: Optional[int],
    news_ids: List[int],
    favorite_scope_topic: Optional[str] = None,
) -> tuple[set[int], set[int]]:
    """
    返回 (收藏星标 news_id 集合, 预警 news_id 集合)。
    favorite_scope_topic 为 None 时不按主题过滤；否则只统计该主题下记录。
    """
    if not user_id or user_id <= 0 or not news_ids:
        return set(), set()
    q = db.query(models.UserFavorite.news_id, models.UserFavorite.item_kind).filter(
        models.UserFavorite.user_id == user_id,
        models.UserFavorite.news_id.in_(news_ids),
    )
    if favorite_scope_topic is not None:
        q = q.filter(models.UserFavorite.topic == favorite_scope_topic)
    fav: set[int] = set()
    warn: set[int] = set()
    for r in q.all():
        nid = int(r.news_id)
        kind = (r.item_kind or "favorite").strip().lower()
        if kind == "warning":
            warn.add(nid)
        else:
            fav.add(nid)
    return fav, warn


def _normalize_favorite_topic(raw: Optional[str]) -> str:
    return (raw or "").strip()


def _normalize_favorite_kind(raw: Optional[str]) -> str:
    k = (raw or "favorite").strip().lower()
    if k not in ("favorite", "warning"):
        raise HTTPException(status_code=400, detail="kind 必须是 favorite 或 warning")
    return k


def resolve_country_to_language(db: Session, language: Optional[str]) -> Optional[str]:
    """
    将显式语言筛选解析为 news.language 列的值。

    函数名为兼容旧调用保留；调用方不得传入国家或地点并据此猜测语言。
    如果未提供或 `language` 表无匹配则返回 None，表示不过滤。
    """
    if not language or not str(language).strip():
        return None
    raw = str(language).strip()
    if raw.isdigit():
        # 老数据可能传 language_id 数字，忽略
        return None
    # 查询 language 表（若有映射），否则直接返回 raw 值作为过滤条件
    row = db.query(models.Language.name).filter(models.Language.name == raw).first()
    if row:
        return row.name
    return raw


# 前端占位「站点」选项，不构成真实 URL 子串；若参与过滤会导致 0 条结果
_SITE_FILTER_PLACEHOLDERS = frozenset(
    {"请选择", "新闻网站", "博客", "社交媒体", "论坛", "全部", ""},
)


def _effective_site_filter(site: Optional[str]) -> Optional[str]:
    if site is None:
        return None
    s = str(site).strip()
    if not s or s in _SITE_FILTER_PLACEHOLDERS:
        return None
    return s


SAFE_SORT_FIELDS = {
    "pub_time": models.News.pub_time,
    "published_at": models.News.pub_time,
    "id": models.News.id,
    "title": models.News.title,
    "language_id": models.News.language_id,
}


def get_safe_sort_field(sort_by: Optional[str]):
    return SAFE_SORT_FIELDS.get((sort_by or "").strip(), models.News.pub_time)


# 中英同义词映射（用于检索扩展，支持模糊搜索同义词/多语种）
_ZH_EN_SYNONYM_MAP = {
    # 中国 / China
    '中国': ['china', 'chinese', 'beijing'],
    'china': ['中国', 'chinese', 'beijing'],
    'chinese': ['中国', 'china'],
    # 美国 / US
    '美国': ['usa', 'united states', 'america', 'american', 'washington dc', 'us'],
    'usa': ['美国', 'united states', 'america', 'american', 'us'],
    'america': ['美国', 'united states', 'american'],
    'american': ['美国', 'usa', 'america'],
    'united states': ['美国', 'usa', 'america', 'american', 'us'],
    # 台湾 / Taiwan
    '台湾': ['taiwan', 'taipei'],
    'taiwan': ['台湾', 'taipei'],
    # 香港 / Hong Kong
    '香港': ['hong kong', 'hongkong'],
    'hong kong': ['香港', 'hongkong'],
    'hongkong': ['香港'],
    # 日本 / Japan
    '日本': ['japan', 'japanese', 'tokyo'],
    'japan': ['日本', 'japanese'],
    'japanese': ['日本', 'japan'],
    # 俄罗斯 / Russia
    '俄罗斯': ['russia', 'russian', 'moscow'],
    'russia': ['俄罗斯', 'russian'],
    'russian': ['俄罗斯', 'russia'],
    # 英国 / UK
    '英国': ['uk', 'united kingdom', 'britain', 'british', 'london'],
    'uk': ['英国', 'united kingdom', 'britain', 'british'],
    'britain': ['英国', 'uk', 'united kingdom'],
    # 欧洲 / Europe
    '欧洲': ['europe', 'european', 'eu'],
    'europe': ['欧洲', 'european', 'eu'],
    'eu': ['欧洲', 'european union', '欧洲联盟'],
    # 南海 / South China Sea
    '南海': ['south china sea'],
    'south china sea': ['南海'],
    # 贸易 / trade
    '贸易': ['trade', 'tariff', 'trading'],
    'trade': ['贸易', 'tariff'],
    'tariff': ['关税', '贸易', 'trade'],
    '关税': ['tariff', 'trade', '贸易'],
    # 军事 / military
    '军事': ['military', 'armed forces', 'defense', 'army'],
    'military': ['军事', 'armed forces', 'army'],
    # 经济 / economy
    '经济': ['economy', 'economic', 'economics'],
    'economy': ['经济', 'economic'],
    # 科技 / technology
    '科技': ['technology', 'tech', 'technological'],
    'technology': ['科技', 'tech'],
    # 疫情 / pandemic
    '疫情': ['pandemic', 'epidemic', 'covid', 'coronavirus', 'outbreak'],
    'pandemic': ['疫情', 'epidemic', 'covid'],
    'covid': ['疫情', 'coronavirus', '新冠'],
    # 华为
    '华为': ['huawei'],
    'huawei': ['华为'],
    # 台湾问题 / cross-strait
    '台海': ['taiwan strait', 'cross-strait'],
    'cross-strait': ['台海', '两岸'],
    '两岸': ['cross-strait', '台海'],
}


def _expand_synonyms(terms: List[str]) -> List[str]:
    """对关键词列表进行同义词扩展，保留原词同时加入对应语种的同义词"""
    expanded = list(terms)
    for t in terms:
        key = t.lower().strip()
        if key in _ZH_EN_SYNONYM_MAP:
            for syn in _ZH_EN_SYNONYM_MAP[key]:
                if syn not in expanded and syn not in terms:
                    expanded.append(syn)
    return expanded


def tokenize_terms(raw: Optional[str]) -> List[str]:
    """
    支持:
    - 普通空格分词: a b c
    - 引号短语: "new york" asia
    """
    if not raw:
        return []
    text_value = str(raw).strip()
    if not text_value:
        return []
    terms: List[str] = []
    for quoted, plain in re.findall(r'"([^"]+)"|(\S+)', text_value):
        token = (quoted or plain or "").strip()
        if token:
            terms.append(token)
    return terms


def to_like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains_in_column(column, term: str):
    return column.ilike(to_like_pattern(term), escape="\\")


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def build_title_abstract(
    title: Optional[str],
    abstract: Optional[str],
    body: Optional[str],
    request_url: Optional[str],
    trans_title: Optional[str] = None,
    trans_abstract: Optional[str] = None,
    trans_body: Optional[str] = None,
) -> Tuple[str, str]:
    title_candidates = [
        _clean_text(title),
        _clean_text(trans_title),
    ]
    title_value = next((v for v in title_candidates if v), "")
    if not title_value:
        source = extract_source_from_url(request_url or "")
        title_value = f"{source} - 无标题" if source else "无标题"

    abstract_candidates = [
        _clean_text(abstract),
        _clean_text(trans_abstract),
        _clean_text(body)[:200],
        _clean_text(trans_body)[:200],
    ]
    abstract_value = next((v for v in abstract_candidates if v), "")
    if abstract_value and len(abstract_value) == 200:
        abstract_value = abstract_value + "..."
    return title_value, abstract_value


def _columns_for_hit_location(hit_location: str, translated_alias=None):
    """按 hit_location 返回参与检索的列。标题=title，摘要=abstract，正文=body，全文=title+abstract。"""
    title_cols = [models.News.title]
    abstract_cols = [models.News.abstract]
    body_cols = [models.News.body]
    if translated_alias is not None:
        title_cols.append(translated_alias.trans_title)
        abstract_cols.append(translated_alias.trans_abstract)
        body_cols.append(translated_alias.trans_body)
    if hit_location == "标题":
        return title_cols
    if hit_location == "摘要":
        return abstract_cols
    if hit_location == "正文":
        return body_cols
    # 全文：标题+摘要（不含 body，body ILIKE 扫描 1.7M 行导致搜索超时）
    return title_cols + abstract_cols


def _term_in_columns(columns: list, term: str):
    """单个词在给定列中任意一处包含即命中（OR）"""
    return or_(*[_contains_in_column(c, term) for c in columns if c is not None])


def build_filter_conditions(
    db: Session,
    search_params: SearchRequest,
    language_id: Optional[int],
    translated_alias=None,
) -> List[Any]:
    """
    构建筛选条件：keyword/topic 主检索；must_include 必须全部包含（AND）；
    any_include 不参与 WHERE，仅用于排序；need_exclude 任一词出现则排除。
    hit_location 统一控制检索范围：标题/摘要/正文/全文。
    """
    final_conditions = []
    loc = search_params.hit_location or "全文"
    columns = _columns_for_hit_location(loc, translated_alias)

    # 1. 主检索：keyword 或 topic 至少命中一处（OR  across columns）
    keyword = (search_params.keyword or search_params.topic or "").strip()
    if keyword:
        keyword_terms = tokenize_terms(keyword)
        if keyword_terms:
            expanded = _expand_synonyms(keyword_terms)
            final_conditions.append(or_(*[_term_in_columns(columns, t) for t in expanded]))

    # 2. must_include：每个词都必须出现（AND）
    if search_params.must_include:
        for k in tokenize_terms(search_params.must_include):
            k = k.strip()
            if k:
                final_conditions.append(_term_in_columns(columns, k))

    # 3. need_exclude：任一词在范围内出现则排除（AND NOT 每个词）
    if search_params.need_exclude:
        for k in tokenize_terms(search_params.need_exclude):
            k = k.strip()
            if k:
                final_conditions.append(not_(_term_in_columns(columns, k)))

    # 4. 时间
    start_dt, end_dt = None, None
    if search_params.publish_time:
        start_dt, end_dt = parse_time_range(search_params.publish_time)
    if search_params.start_time:
        try:
            start_dt = datetime.fromisoformat(search_params.start_time)
        except Exception:
            pass
    if search_params.end_time:
        try:
            end_dt = datetime.fromisoformat(search_params.end_time)
        except Exception:
            pass
    if start_dt:
        final_conditions.append(models.News.pub_time >= start_dt)
    if end_dt:
        final_conditions.append(models.News.pub_time <= end_dt)

    # 5. 数据源
    if search_params.data_source:
        ds = search_params.data_source.strip()
        # 查找 v3_media 表中匹配的域名
        media = db.query(models.V3Media.domain).filter(
            models.V3Media.name == ds
        ).first()
        if media and media.domain:
            final_conditions.append(_contains_in_column(models.News.request_url, media.domain))
        else:
            # 回退：直接按字符串匹配 request_url
            final_conditions.append(_contains_in_column(models.News.request_url, ds))

    eff_site = _effective_site_filter(search_params.site)
    if eff_site:
        final_conditions.append(_contains_in_column(models.News.request_url, eff_site))

    # 6. 国家/语言（标准列 news.language_id）
    if language_id is not None:
        final_conditions.append(models.News.language_id == language_id)

    return final_conditions


def build_any_include_order_expression(search_params: SearchRequest, translated_alias=None):
    """
    构建「是否命中 any_include」的表达式，用于 ORDER BY 优先展示命中结果。
    未传 any_include 时返回 None。
    """
    if not search_params.any_include:
        return None
    terms = tokenize_terms(search_params.any_include)
    if not terms:
        return None
    loc = search_params.hit_location or "全文"
    columns = _columns_for_hit_location(loc, translated_alias)
    weighted_columns = []
    for c in columns:
        if c in (models.News.title,):
            weighted_columns.append((c, 5))
        elif c in (models.News.abstract,):
            weighted_columns.append((c, 3))
        else:
            weighted_columns.append((c, 1))
    score_expr = 0
    for term in terms:
        for col, weight in weighted_columns:
            score_expr = score_expr + case((_contains_in_column(col, term), weight), else_=0)
    return score_expr


def build_relevance_order_expression(search_params: SearchRequest, translated_alias=None):
    """
    综合相关性排序:
    - title > abstract > body
    - translated_* 参与召回与排序，提升缺失标题/摘要场景
    - keyword/topic、must_include、any_include 均参与打分
    """
    all_terms: List[str] = []
    all_terms.extend(tokenize_terms(search_params.keyword or search_params.topic))
    all_terms.extend(tokenize_terms(search_params.must_include))
    all_terms.extend(tokenize_terms(search_params.any_include))
    if not all_terms:
        return None

    score_expr = 0
    hit_location = search_params.hit_location or "全文"
    active_columns = _columns_for_hit_location(hit_location, translated_alias)
    weighted_columns = []
    for col in active_columns:
        col_name = getattr(col, "key", "")
        if col_name in ("title", "trans_title"):
            weighted_columns.append((col, 6))
        elif col_name in ("abstract", "trans_abstract"):
            weighted_columns.append((col, 4))
        else:
            weighted_columns.append((col, 2))
    for term in all_terms:
        for col, weight in weighted_columns:
            score_expr = score_expr + case((_contains_in_column(col, term), weight), else_=0)
    return score_expr


# 通用分页查询函数
def get_paginated_field(db: Session, field, page: int, size: int):
    offset = (page - 1) * size
    query = db.query(models.News.id, field.label('value'))
    total = db.query(func.count(models.News.id)).scalar()
    items = query.order_by(desc(models.News.id)).offset(offset).limit(size).all()

    formatted = []
    for item in items:
        val = item.value
        if isinstance(val, datetime): val = val.strftime('%Y-%m-%d %H:%M:%S')
        formatted.append({"id": item.id, "value": str(val) if val else "—"})

    return {
        "data": formatted,
        "total": total,
        "page": page,
        "size": size,
        "pages": (total + size - 1) // size,
        "has_next": page * size < total,
        "has_prev": page > 1
    }


def has_lxy_translated_table(bind) -> bool:
    try:
        return bool(inspect(bind).has_table("lxy_translated", schema="public"))
    except Exception:
        return False


def news_query_with_optional_translation(db: Session):
    """返回 (query, translated_alias | None)。无翻译表时不 join。"""
    if not has_lxy_translated_table(db.get_bind()):
        return db.query(models.News), None
    latest_translated_subquery = (
        db.query(
            models.LxyTranslated.news_id.label("news_id"),
            func.max(models.LxyTranslated.id).label("max_id"),
        )
        .group_by(models.LxyTranslated.news_id)
        .subquery()
    )
    translated = aliased(models.LxyTranslated)
    q = (
        db.query(models.News)
        .outerjoin(latest_translated_subquery, latest_translated_subquery.c.news_id == models.News.id)
        .outerjoin(translated, translated.id == latest_translated_subquery.c.max_id)
    )
    return q, translated


def news_row_entity_columns(translated):
    """与列表/搜索一致的 with_entities 列；无翻译表时用 NULL 占位。"""
    base = [
        models.News.id,
        models.News.title,
        models.News.abstract,
        models.News.body,
        models.News.pub_time,
        models.News.request_url,
        models.News.language_id,
    ]
    if translated is None:
        return base + [
            literal(None).label("trans_title"),
            literal(None).label("trans_abstract"),
            literal(None).label("trans_body"),
            literal(None).label("is_translated"),
        ]
    return base + [
        translated.trans_title,
        translated.trans_abstract,
        translated.trans_body,
        translated.is_translated,
    ]


def run_startup_schema_check(db: Session):
    """启动只读自检：关键表与字段是否齐全。"""
    required_columns = {
        "news": {"id", "title", "body", "published_at", "url", "language"},
        "app_user": {
            "id",
            "username",
            "password_hash",
            "full_name",
            "email",
            "phone",
            "updated_at",
            "created_at",
            "is_active",
            "last_login_at",
            "role",
            "avatar_url",
            "api_keys",
        },
        "language": {"id", "name"},
        "user_favorite": {"id", "user_id", "news_id", "topic", "item_kind", "created_at"},
        "user_search_history": {"id", "user_id", "keyword", "created_at"},
        "password_reset_token": {"id", "user_id", "token_hash", "expires_at", "used_at", "created_at"},
    }
    optional_tables = {
        "lxy_translated": {"id", "news_id", "website_id", "trans_title", "trans_abstract", "trans_body", "is_translated", "created_at", "updated_at"},
    }
    pipeline_tables = (
        "news_analysis",
        "news_embeddings",
        "macro_storylines",
        "micro_events",
        "storyline_micro_map",
    )
    rows = db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                'news', 'lxy_translated', 'app_user', 'language', 'user_favorite',
                'user_search_history', 'password_reset_token'
              )
            """
        )
    ).fetchall()
    inspector = inspect(db.get_bind())
    existing_tables = inspector.get_table_names()
    errors = {}

    for table, required_cols in required_columns.items():
        if table not in existing_tables:
            errors[table] = "Table missing"
            continue

        # 获取表中的实际列名
        actual_cols = {col['name'] for col in inspector.get_columns(table)}
        # 找出缺失的列
        missing_cols = required_cols - actual_cols
        if missing_cols:
            errors[table] = list(missing_cols)

    opt_warn = {}
    for table, required_cols in optional_tables.items():
        if table not in existing_tables:
            opt_warn[table] = "optional table missing"
            continue
        actual_cols = {col['name'] for col in inspector.get_columns(table)}
        missing_cols = required_cols - actual_cols
        if missing_cols:
            opt_warn[table] = list(missing_cols)

    if opt_warn:
        print(f"[INFO] 可选表/翻译列: {opt_warn}")

    have_pipe = [t for t in pipeline_tables if t in existing_tables]
    missing_pipe = [t for t in pipeline_tables if t not in existing_tables]
    print(
        f"[INFO] 流水线相关表（PG 侧）: 已存在 {have_pipe or '无'}；未建表 {missing_pipe or '无'}",
        flush=True,
    )
    if errors:
        print(f"[WARN] schema 自检告警: {errors}")
    else:
        print("[OK] 数据库 Schema 自检通过（必选表）")
    return {
        "ready": not errors,
        "errors": errors,
        "optional_warnings": opt_warn,
        "pipeline_tables_missing": missing_pipe,
    }
