"""
Search execution functions extracted from application.py.
Provides all search-related query logic: exact, fuzzy, cluster, event_coref,
micro_stories, macro_events, V11 cluster search, and children expansion.
"""

import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from sqlalchemy import and_, desc, or_, text
from sqlalchemy.orm import Session

from api.core.environment import int_setting, string_setting
from api.features.search import (
    expand_v11_cluster_children as _expand_current_v11_children,
)
from api.features.search import (
    search_v11_clusters as _search_current_v11_clusters,
)
from api.models.schemas import (
    EventCorefClusterInfo,
    MacroEventItem,
    MicroStoryItem,
    NewsItem,
    NewsResultTimeSemantics,
    SearchRequest,
    SearchResponse,
    V11ClusterSearchRequest,
    V11ClusterSearchResponse,
)
from api.orm import models
from api.services.helpers import (
    _columns_for_hit_location,
    _effective_site_filter,
    build_filter_conditions,
    build_title_abstract,
    extract_source_from_url,
    get_safe_sort_field,
    get_user_favorite_sets_for_scope,
    news_query_with_optional_translation,
    news_row_entity_columns,
    parse_time_range,
    resolve_country_to_language,
    tokenize_terms,
)

# ==================== module-level constants ====================

# ==================== internal helpers ====================

def _expand_semantic_query_cn(q: str) -> str:
    """
    对中文公共卫生/疫情类查询做轻量扩展，缓解 Milvus 向量与语料措辞不一致导致的召回过低。
    不改变 SQL 精确检索条件，仅作用于 fuzzy/cluster 的向量编码输入。
    """
    text_value = (q or "").strip()
    if not text_value:
        return text_value
    cn_markers = (
        "疫情",
        "新冠",
        "新冠肺炎",
        "冠状病毒",
        "奥密克戎",
        "防疫",
        "确诊",
        "核酸",
        "隔离",
        "感染病例",
    )
    tl = text_value.lower()
    hit = "covid" in tl or any(m in text_value for m in cn_markers)
    if hit:
        return f"{text_value} 新冠肺炎 COVID-19 冠状病毒 公共卫生 流行病"
    return text_value


def _build_semantic_query_text(params: SearchRequest) -> str:
    """BGE-M3 语义检索用查询文本（UTF-8 字符串，支持中文）。"""
    parts = [
        (params.keyword or params.topic or "").strip(),
        (params.must_include or "").strip(),
        (params.any_include or "").strip(),
    ]
    return _expand_semantic_query_cn(" ".join(p for p in parts if p))


def _combined_text_for_vector_filter(row) -> str:
    t = [
        row.title or "",
        row.abstract or "",
        row.body or "",
        getattr(row, "trans_title", None) or "",
        getattr(row, "trans_abstract", None) or "",
        getattr(row, "trans_body", None) or "",
    ]
    return " ".join(t)


def _passes_vector_mode_filters(row, params: SearchRequest, language_id: Optional[int], extra_must_terms: str = "") -> bool:
    """fuzzy/cluster 模式在 Milvus 召回后，用 PG 元数据做时间与站点等过滤。

    extra_must_terms: 额外的 must_include 词（用于将 keyword 隐含加入过滤）。
    """
    start_dt, end_dt = None, None
    if params.publish_time:
        start_dt, end_dt = parse_time_range(params.publish_time)
    if params.start_time:
        try:
            start_dt = datetime.fromisoformat(params.start_time)
        except Exception:
            pass
    if params.end_time:
        try:
            end_dt = datetime.fromisoformat(params.end_time)
        except Exception:
            pass
    pt = row.pub_time
    if start_dt and pt and pt < start_dt:
        return False
    if end_dt and pt and pt > end_dt:
        return False
    if language_id is not None and row.language_id != language_id:
        return False
    url = (row.request_url or "").lower()
    if params.data_source and str(params.data_source).lower() not in url:
        return False
    eff_site = _effective_site_filter(params.site)
    if eff_site and str(eff_site).lower() not in url:
        return False
    blob = _combined_text_for_vector_filter(row).lower()
    combined_must = ((params.must_include or "") + " " + extra_must_terms).strip()
    for term in tokenize_terms(combined_must):
        if term and term.lower() not in blob:
            return False
    for term in tokenize_terms(params.need_exclude):
        if term and term.lower() in blob:
            return False
    return True


def _fetch_news_rows_by_ids_ordered(db: Session, ids: List[int]):
    """按 ids 顺序返回 news + 可选翻译列（与 exact 搜索列一致）。"""
    if not ids:
        return []
    query, translated = news_query_with_optional_translation(db)
    q = query.filter(models.News.id.in_(ids)).with_entities(*news_row_entity_columns(translated))
    rows = q.all()
    by_id = {int(r.id): r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def _rows_to_news_items(
    db: Session,
    user: Optional[Dict[str, Any]],
    rows: List[Any],
    favorite_scope_topic: Optional[str] = None,
) -> List[NewsItem]:
    if not rows:
        return []
    user_id = int(user["user_id"]) if user and user.get("user_id") else 0
    favorite_ids, warned_ids = get_user_favorite_sets_for_scope(
        db, user_id, [r.id for r in rows], favorite_scope_topic
    )
    data_list: List[NewsItem] = []
    for row in rows:
        tt = getattr(row, "trans_title", None)
        ta = getattr(row, "trans_abstract", None)
        tb = getattr(row, "trans_body", None)
        it = getattr(row, "is_translated", None)
        display_title, display_abstract = build_title_abstract(
            title=row.title,
            abstract=row.abstract,
            body=row.body,
            request_url=row.request_url,
            trans_title=tt,
            trans_abstract=ta,
            trans_body=tb,
        )
        data_list.append(
            NewsItem(
                id=row.id,
                title=display_title,
                abstract=display_abstract,
                body=row.body,
                pub_time=row.pub_time,
                request_url=row.request_url,
                language_id=row.language_id,
                created_at=None,
                time_semantics=NewsResultTimeSemantics(published_at=row.pub_time),
                source=extract_source_from_url(row.request_url),
                location=None,
                is_first_release=False,
                is_favorited=row.id in favorite_ids,
                is_warned=row.id in warned_ids,
                has_translation=bool(tt or ta or tb),
                trans_title=tt,
                trans_abstract=ta,
                trans_body=tb,
                is_translated=it,
            )
        )
    return data_list


def _maybe_vector_fallback_to_exact(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
    total: int,
    reason: str,
) -> Optional[SearchResponse]:
    """Milvus 无结果、全被过滤或服务异常时，回退为 SQL 精确检索，避免「疫情」等词 0 条。"""
    if total > 0 or not vector_fallback_exact_enabled():
        return None
    print(f"[search] vector 模式无可用结果（{reason}），回退 SQL exact", flush=True)
    return execute_search_exact(db, params, user, start_ts)


def _build_v11_cluster_tree(
    db: Session,
    news_ids: List[int],
    max_cluster_news: int = 10,
) -> Optional[List[dict]]:
    """Build the compatibility tree from current L2, L1, and news relations."""
    if not news_ids:
        return None

    rows = db.execute(
        text(
            """
            SELECT
                member.news_id,
                cluster.cluster_id,
                cluster.title AS cluster_title,
                cluster.event_type,
                cluster.initiator,
                cluster.target,
                cluster.dominant_trigger,
                cluster.cluster_quality,
                segment.chain_id,
                chain.title AS chain_title,
                article.title AS news_title,
                article.published_at AS pub_time
            FROM public.event_coref_members member
            JOIN public.event_coref_clusters cluster
              ON cluster.cluster_id = member.cluster_id
            JOIN public.news article ON article.id = member.news_id
            LEFT JOIN public.event_l2_chain_segments segment
              ON segment.l1_cluster_id = cluster.cluster_id
            LEFT JOIN public.event_l2_chains chain ON chain.chain_id = segment.chain_id
            WHERE member.news_id = ANY(:news_ids)
            ORDER BY chain.article_count DESC NULLS LAST,
                     cluster.article_count DESC NULLS LAST,
                     article.published_at DESC NULLS LAST
            """
        ),
        {"news_ids": news_ids},
    ).mappings().all()
    if not rows:
        return None

    cluster_meta: Dict[str, dict] = {}
    cluster_news: Dict[str, list[dict]] = {}
    cluster_news_ids: Dict[str, set[int]] = {}
    chain_meta: Dict[str, dict] = {}
    chain_clusters: Dict[str, list[str]] = {}
    assigned_clusters: set[str] = set()

    for row in rows:
        cluster_id = str(row["cluster_id"])
        cluster_meta.setdefault(
            cluster_id,
            {
                "title": row.get("cluster_title") or "",
                "event_type": row.get("event_type") or "",
                "initiator": row.get("initiator") or "",
                "target": row.get("target") or "",
                "dominant_trigger": row.get("dominant_trigger") or "",
                "cluster_quality": row.get("cluster_quality") or "",
            },
        )
        news = cluster_news.setdefault(cluster_id, [])
        seen_news_ids = cluster_news_ids.setdefault(cluster_id, set())
        news_id = int(row["news_id"])
        if news_id not in seen_news_ids:
            seen_news_ids.add(news_id)
            news.append(
                {
                    "id": news_id,
                    "title": row.get("news_title") or "",
                    "pub_time": row.get("pub_time"),
                    "time_semantics": NewsResultTimeSemantics(
                        published_at=row.get("pub_time")
                    ).model_dump(),
                }
            )

        chain_id_value = row.get("chain_id")
        if chain_id_value is None:
            continue
        chain_id = str(chain_id_value)
        chain_meta.setdefault(
            chain_id,
            {"title": row.get("chain_title") or chain_id},
        )
        clusters = chain_clusters.setdefault(chain_id, [])
        if cluster_id not in clusters:
            clusters.append(cluster_id)
        assigned_clusters.add(cluster_id)

    def build_clusters(cluster_ids: List[str]) -> list[dict]:
        output = []
        for cluster_id in cluster_ids:
            meta = cluster_meta[cluster_id]
            news = cluster_news.get(cluster_id, [])[:max_cluster_news]
            output.append(
                {
                    "cluster_id": cluster_id,
                    **meta,
                    "news_count": len(news),
                    "news": news,
                }
            )
        return output

    tree = []
    for chain_id, cluster_ids in chain_clusters.items():
        clusters = build_clusters(cluster_ids)
        tree.append(
            {
                "story_id": chain_id,
                "title": chain_meta[chain_id]["title"],
                "cluster_count": len(clusters),
                "news_count": sum(item["news_count"] for item in clusters),
                "clusters": clusters,
            }
        )

    orphan_ids = [
        cluster_id for cluster_id in cluster_meta if cluster_id not in assigned_clusters
    ]
    if orphan_ids:
        clusters = build_clusters(orphan_ids)
        tree.append(
            {
                "story_id": "unassigned",
                "title": "未分配走势链",
                "cluster_count": len(clusters),
                "news_count": sum(item["news_count"] for item in clusters),
                "clusters": clusters,
            }
        )
    return tree or None


# ==================== exported / public functions ====================


def execute_search_exact(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
) -> SearchResponse:
    _t0 = time.time()
    language_id = resolve_country_to_language(db, params.language)
    query, translated = news_query_with_optional_translation(db)
    filters = build_filter_conditions(db, params, language_id, translated)
    _t1 = time.time()
    print(f"[search_profile]  exact build={(_t1-_t0)*1000:.0f}ms", end=" ", flush=True)
    if filters:
        query = query.filter(and_(*filters))
    if params.cluster_scope:
        query = query.filter(
            text("EXISTS (SELECT 1 FROM news_ai_analysis WHERE news_ai_analysis.news_id = news.id AND news_ai_analysis.prototype_weighted >= 0.4)")
        )
        # 排除 GDELT 来源（无正文数据）
        query = query.filter(text("news.source_dataset_id NOT IN (1, 7, 11)"))
    sort_field = get_safe_sort_field(params.sort_by)
    order_direction = desc(sort_field) if (params.sort_order or "desc") == "desc" else sort_field.asc()
    # EXACT 模式：只按 sort_field/time 排序。
    # build_relevance_order_expression 会生成大量 CASE WHEN ILIKE（同义词扩充后可达
    # 8-16 个 ILIKE），对 ~20k 命中行逐行求值使排序耗时 ~4.7s。精确检索已用 WHERE
    # 过滤出相关结果，再按 ILIKE 排序得不偿失。
    query = query.order_by(order_direction)
    offset = (params.page - 1) * params.page_size
    query = query.with_entities(*news_row_entity_columns(translated))
    # 取 page_size+1 条以判断是否有下一页，避免昂贵 COUNT(*)
    _t2 = time.time()
    print(f"[search_profile]  exact db stage={(_t2-_t1)*1000:.0f}ms", flush=True)
    rows = query.offset(offset).limit(params.page_size + 1).all()
    _t3 = time.time()
    print(f"[search_profile]  exact db query={(_t3-_t2)*1000:.0f}ms rows={len(rows)}", flush=True)
    has_next = len(rows) > params.page_size
    results = rows[:params.page_size]
    total = (params.page - 1) * params.page_size + len(rows) if not has_next else 99999
    data_list = _rows_to_news_items(db, user, results, params.favorite_scope_topic)
    query_time = (time.time() - start_ts) * 1000
    return SearchResponse(
        data=data_list,
        total=total,
        page=params.page,
        page_size=params.page_size,
        total_pages=(total + params.page_size - 1) // params.page_size if params.page_size else 0,
        has_next=has_next,
        has_prev=params.page > 1,
        query_time_ms=query_time,
    )


def vector_fallback_exact_enabled() -> bool:
    v = string_setting("SEARCH_VECTOR_FALLBACK_EXACT", "1").lower()
    return v not in ("0", "false", "no", "off")


def execute_search_fuzzy(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
) -> SearchResponse:
    q = _build_semantic_query_text(params)
    if not q.strip():
        # 对前端更友好：语义检索缺少查询词时，回退到 exact（可按时间/语种/站点等筛选）
        return execute_search_exact(db, params, user, start_ts)
    try:
        from agentic_rag.db.milvus_store import get_milvus_store
        vec = encode_query_bge_m3(q)
        store = get_milvus_store()
        top_k = int_setting("MILVUS_FUZZY_TOP_K", 16000, minimum=1)
        top_k = min(16000, max(top_k, params.page * params.page_size + 50))
        hits = store.search_similar_news(vec, top_k=top_k)
    except HTTPException:
        raise
    except Exception as e:
        if vector_fallback_exact_enabled():
            print(f"[search] fuzzy Milvus/BGE 异常，回退 exact: {e!s}", flush=True)
            return execute_search_exact(db, params, user, start_ts)
        raise HTTPException(status_code=503, detail=f"Milvus 或 BGE-M3 不可用: {e!s}") from e

    ordered_ids: List[int] = []
    seen: set[int] = set()
    for h in hits:
        nid = int(h.news_id)
        if nid in seen:
            continue
        seen.add(nid)
        ordered_ids.append(nid)

    language_id = resolve_country_to_language(db, params.language)
    kw_ids: List[int] = []
    semantic_ids: List[int] = []
    chunk_size = 400
    keyword = (params.keyword or params.topic or "").strip()
    for i in range(0, len(ordered_ids), chunk_size):
        chunk = ordered_ids[i : i + chunk_size]
        rows = _fetch_news_rows_by_ids_ordered(db, chunk)
        for row in rows:
            if _passes_vector_mode_filters(row, params, language_id):
                # 含关键词的排前面，纯语义的排后面
                if keyword and keyword.lower() in _combined_text_for_vector_filter(row).lower():
                    kw_ids.append(int(row.id))
                else:
                    semantic_ids.append(int(row.id))

    # ── 补充：Milvus 未覆盖的精确关键词命中 ────────────────────
    supplement_ids: List[int] = []
    if keyword:
        q_exact, trans_alias = news_query_with_optional_translation(db)
        exact_filters = build_filter_conditions(db, params, language_id, trans_alias)
        if exact_filters:
            from sqlalchemy import and_
            q_exact = q_exact.filter(and_(*exact_filters))
        q_exact = q_exact.with_entities(models.News.id)
        all_exact_ids = [r.id for r in q_exact.all()]

        milvus_ids = set(kw_ids) | set(semantic_ids)
        supplement_ids = [nid for nid in all_exact_ids if nid not in milvus_ids]
        if supplement_ids:
            print(f"[search] fuzzy 补充 {len(supplement_ids)} 条精确命中（Milvus 未覆盖）", flush=True)

    # ── 组装最终排序 ──────────────────────────────────────────
    is_pub_time_sort = params.sort_by in {"pub_time", "published_at"}

    def _sort_by_pubtime(ids, rd=None):
        if not ids:
            return ids
        if rd is None:
            rows_all = _fetch_news_rows_by_ids_ordered(db, ids)
            rd = {int(r.id): r for r in rows_all}
        desc_order = (params.sort_order or "desc") == "desc"
        return sorted(ids, key=lambda nid: -(rd[nid].pub_time.timestamp() if rd.get(nid) and rd[nid].pub_time else 0.0) if desc_order else (rd[nid].pub_time.timestamp() if rd.get(nid) and rd[nid].pub_time else 0.0))

    if is_pub_time_sort:
        kw_ids = _sort_by_pubtime(kw_ids)
        semantic_ids = _sort_by_pubtime(semantic_ids)
        supplement_ids = _sort_by_pubtime(supplement_ids)

    filtered_ids = kw_ids + semantic_ids  # 含关键词的在前

    total = len(filtered_ids) + len(supplement_ids)
    fb = _maybe_vector_fallback_to_exact(
        db,
        params,
        user,
        start_ts,
        total,
        "fuzzy: Milvus 无命中或全部被时间与站点等条件过滤",
    )
    if fb is not None:
        return fb

    offset = (params.page - 1) * params.page_size
    page_ids: List[int] = []
    if offset < len(filtered_ids):
        page_ids = filtered_ids[offset : offset + params.page_size]
    if len(page_ids) < params.page_size:
        sup_offset = max(0, offset - len(filtered_ids))
        sup_needed = params.page_size - len(page_ids)
        page_ids.extend(supplement_ids[sup_offset : sup_offset + sup_needed])

    page_rows = _fetch_news_rows_by_ids_ordered(db, page_ids)
    data_list = _rows_to_news_items(db, user, page_rows, params.favorite_scope_topic)
    query_time = (time.time() - start_ts) * 1000
    return SearchResponse(
        data=data_list,
        total=total,
        page=params.page,
        page_size=params.page_size,
        total_pages=(total + params.page_size - 1) // params.page_size if params.page_size else 0,
        has_next=params.page * params.page_size < total,
        has_prev=params.page > 1,
        query_time_ms=query_time,
    )


def execute_search_cluster(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
) -> SearchResponse:
    """
    按关键词搜索新闻，在当前 L2 走势链 → L1 事件簇 → 新闻层级中
    构建层级树返回。通过 news 原文关键词命中映射到簇，
    保证即使簇表无直接文本匹配也能找到结果。
    """
    q = _build_semantic_query_text(params)
    if not q.strip():
        return execute_search_exact(db, params, user, start_ts)

    from sqlalchemy import text as sa_text
    kw = f"%{q}%"

    # ── Step 1: 在 news 原文中搜索关键词，获取命中 ID ──
    # 同时搜索 title 和 abstract（正文 body 过大忽略）
    all_rows = db.execute(sa_text("""
        SELECT DISTINCT n.id
        FROM news n
        WHERE n.title ILIKE :kw OR n.abstract ILIKE :kw2
        ORDER BY n.id
        LIMIT 3000
    """), {"kw": kw, "kw2": kw}).fetchall()
    filtered_ids = [int(r[0]) for r in all_rows]

    # 若新闻无命中，通过簇表尝试搜索 cluster.event_type 的中文映射
    if not filtered_ids:
        # 构建中文 → event_type 反向映射
        event_type_cn = {
            "军事": "military", "抗议": "protest_repression",
            "外交": "diplomacy", "贸易": "trade_conflict",
            "人权": "human_rights_migration", "移民": "human_rights_migration",
            "任命": "appointment_leadership", "领导层": "appointment_leadership",
            "恐怖": "terrorism_espionage", "间谍": "terrorism_espionage",
            "政策": "policy_legal", "法律": "policy_legal",
            "援助": "aid_disaster", "灾难": "aid_disaster",
            "灾害": "aid_disaster",
        }
        et_kw = None
        for cn, en in event_type_cn.items():
            if cn in q:
                et_kw = en
                break
        if et_kw:
            et_rows = db.execute(sa_text("""
                SELECT DISTINCT ecm.news_id
                FROM event_coref_clusters ec
                JOIN event_coref_members ecm ON ecm.cluster_id = ec.cluster_id
                WHERE ec.event_type = :et
                ORDER BY ecm.news_id DESC
                LIMIT 3000
            """), {"et": et_kw}).fetchall()
            filtered_ids = [int(r[0]) for r in et_rows]

    # ── Step 2: 构建 V11 层级簇树 ──
    cluster_tree = _build_v11_cluster_tree(db, filtered_ids)

    # ── Step 3: 平面新闻列表（应用过滤 + 分页） ──
    language_id = resolve_country_to_language(db, params.language)
    news_rows = _fetch_news_rows_by_ids_ordered(db, filtered_ids)
    final_ids: list = []
    for row in news_rows:
        if _passes_vector_mode_filters(row, params, language_id):
            final_ids.append(int(row.id))

    total = len(final_ids)
    fb_resp = _maybe_vector_fallback_to_exact(
        db, params, user, start_ts, total,
        "cluster: 无匹配新闻",
    )
    if fb_resp is not None:
        return fb_resp

    offset = (params.page - 1) * params.page_size
    page_ids = final_ids[offset: offset + params.page_size]
    page_rows = _fetch_news_rows_by_ids_ordered(db, page_ids)
    data_list = _rows_to_news_items(db, user, page_rows, params.favorite_scope_topic)
    query_time = (time.time() - start_ts) * 1000

    return SearchResponse(
        data=data_list,
        total=total,
        page=params.page,
        page_size=params.page_size,
        total_pages=(total + params.page_size - 1) // params.page_size if params.page_size else 0,
        has_next=params.page * params.page_size < total,
        has_prev=params.page > 1,
        query_time_ms=query_time,
        cluster_tree=cluster_tree,
    )


def execute_search_event_coref(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
) -> SearchResponse:
    """Event coref 模式：搜索文章 → 查找所属事件共核簇 → 返回簇信息 + 簇内文章。"""
    from sqlalchemy import text as sa_text

    q = _build_semantic_query_text(params)
    if not q.strip():
        return execute_search_exact(db, params, user, start_ts)

    # ── Step 1: SQL fuzzy search on news to find matching articles ──
    language_id = resolve_country_to_language(db, params.language)
    query, translated = news_query_with_optional_translation(db)
    filters = build_filter_conditions(db, params, language_id, translated)
    if filters:
        query = query.filter(and_(*filters))
    # Build keyword matching condition respecting hit_location
    kw = f"%{q}%"
    loc = params.hit_location or "全文"
    search_columns = _columns_for_hit_location(loc, translated)
    keyword_cond = or_(col.ilike(kw) for col in search_columns)
    query = query.filter(keyword_cond)

    # Get enough matching news IDs for clustering.
    max_fetch = int_setting("EVENT_COREF_MAX_NEWS", 2000, minimum=1)
    query = query.with_entities(models.News.id).limit(max_fetch)
    matched_ids = [int(r.id) for r in query.all()]
    if not matched_ids:
        return SearchResponse(
            data=[], total=0, page=params.page, page_size=params.page_size,
            total_pages=0, has_next=False, has_prev=False, query_time_ms=0,
        )

    # ── Step 2: Look up event coref clusters for matched news ──
    member_rows = db.execute(sa_text("""
        SELECT DISTINCT cm.cluster_id, cm.news_id,
               cm.event_type, cm.initiator, cm.target, cm.trigger
        FROM event_coref_members cm
        WHERE cm.news_id = ANY(:nids)
    """), {"nids": matched_ids}).fetchall()

    if not member_rows:
        # No event coref clusters found, return exact results without cluster grouping
        return execute_search_exact(db, params, user, start_ts)

    # Build map: cluster_id -> set of matched news_ids + member event data
    matched_clusters: dict[str, dict] = {}
    for row in member_rows:
        cid = str(row.cluster_id)
        if cid not in matched_clusters:
            matched_clusters[cid] = {"matched_news": set(), "event_type": str(row.event_type) if row.event_type else None}
        matched_clusters[cid]["matched_news"].add(int(row.news_id))

    # ── Step 3: Get cluster metadata ──
    cids = list(matched_clusters.keys())
    cluster_meta_rows = db.execute(sa_text("""
        SELECT cluster_id, article_count, event_type, initiator, target,
               dominant_trigger, cluster_quality, start_date, end_date
        FROM event_coref_clusters
        WHERE cluster_id = ANY(:cids)
    """), {"cids": cids}).fetchall()

    cluster_meta = {str(r.cluster_id): r for r in cluster_meta_rows}

    # ── Step 3b: Use the current L2 chain title as the parent display label. ──
    cluster_story_titles: dict[str, str] = {}
    if cids:
        chain_rows = db.execute(sa_text("""
            SELECT DISTINCT ON (segment.l1_cluster_id)
                   segment.l1_cluster_id, chain.title
            FROM public.event_l2_chain_segments segment
            JOIN public.event_l2_chains chain ON chain.chain_id = segment.chain_id
            WHERE segment.l1_cluster_id = ANY(:cids)
            ORDER BY segment.l1_cluster_id,
                     chain.quality_score DESC NULLS LAST,
                     chain.article_count DESC
        """), {"cids": cids}).fetchall()
        cluster_story_titles = {str(row[0]): row[1] or "" for row in chain_rows}

    # ── Step 4: Get ALL articles in the matched clusters ──
    all_member_rows = db.execute(sa_text("""
        SELECT cm.cluster_id, cm.news_id
        FROM event_coref_members cm
        WHERE cm.cluster_id = ANY(:cids)
        ORDER BY cm.news_id
    """), {"cids": cids}).fetchall()

    # Group all news_ids by cluster
    cluster_all_news: dict[str, list[int]] = {}
    for row in all_member_rows:
        cid = str(row.cluster_id)
        cluster_all_news.setdefault(cid, []).append(int(row.news_id))

    # Fetch news rows for all articles in clusters
    all_news_ids = list(set(
        nid for ids in cluster_all_news.values() for nid in ids
    ))
    news_rows = _fetch_news_rows_by_ids_ordered(db, all_news_ids)
    news_by_id = {int(r.id): r for r in news_rows}

    # Fetch event_coref_cluster_id for NewsItem enrichment
    news_to_cluster: dict[int, str] = {}
    for cid, nids in cluster_all_news.items():
        for nid in nids:
            news_to_cluster[nid] = cid

    # ── Step 5: Build response ──
    # Sort clusters by matched article count descending
    sorted_clusters = sorted(
        matched_clusters.items(),
        key=lambda x: len(x[1]["matched_news"]),
        reverse=True,
    )

    # Paginate: how many clusters to return
    per_page = params.page_size
    offset = (params.page - 1) * per_page
    page_clusters = sorted_clusters[offset: offset + per_page]
    total_pages = (len(sorted_clusters) + per_page - 1) // per_page if per_page else 0

    # Build flat data list (all articles in paginated clusters)
    flat_data: list[NewsItem] = []
    event_coref_clusters_out: list[EventCorefClusterInfo] = []

    for cid, info in page_clusters:
        meta = cluster_meta.get(cid)
        all_nids = cluster_all_news.get(cid, [])
        cluster_items = []
        for nid in all_nids:
            row = news_by_id.get(nid)
            if row:
                items = _rows_to_news_items(db, user, [row], params.favorite_scope_topic)
                if items:
                    items[0].event_coref_cluster_id = cid
                    cluster_items.append(items[0])
        flat_data.extend(cluster_items)

        display_event_type = cluster_story_titles.get(cid) or (
            str(meta.event_type)
            if meta and meta.event_type
            else (info.get("event_type") or "")
        )

        event_coref_clusters_out.append(EventCorefClusterInfo(
            cluster_id=cid,
            article_count=int(meta.article_count) if meta else len(all_nids),
            event_type=display_event_type,
            initiator=str(meta.initiator) if meta and meta.initiator else "",
            target=str(meta.target) if meta and meta.target else "",
            dominant_trigger=str(meta.dominant_trigger) if meta and meta.dominant_trigger else "",
            cluster_quality=str(meta.cluster_quality) if meta and meta.cluster_quality else "",
            start_date=str(meta.start_date) if meta and meta.start_date else None,
            end_date=str(meta.end_date) if meta and meta.end_date else None,
            articles=cluster_items,
        ))

    query_time = (time.time() - start_ts) * 1000

    return SearchResponse(
        data=flat_data,
        total=len(sorted_clusters),
        page=params.page,
        page_size=params.page_size,
        total_pages=total_pages,
        has_next=params.page * params.page_size < len(sorted_clusters),
        has_prev=params.page > 1,
        query_time_ms=query_time,
        event_coref_clusters=event_coref_clusters_out,
    )


def _compatibility_search_keyword(params: SearchRequest) -> str:
    for value in (
        params.keyword,
        params.topic,
        params.must_include,
        params.any_include,
    ):
        if value and value.strip():
            return value.strip()
    return ""


def search_micro_stories(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
    mode: str,
) -> SearchResponse:
    """Compatibility projection of current L1 clusters into the old item model."""
    result = _search_current_v11_clusters(
        db,
        V11ClusterSearchRequest(
            keyword=_compatibility_search_keyword(params),
            level="cluster",
            page=params.page,
            page_size=params.page_size,
        ),
    )
    items = [
        MicroStoryItem(
            id=item.id,
            title=item.title,
            event_type=item.event_type,
            initiator=item.initiator,
            target=item.target,
            article_count=item.article_count,
            cluster_count=item.children_count,
        )
        for item in result.items
    ]
    return SearchResponse(
        data=[],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
        has_next=result.has_next,
        has_prev=result.has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        micro_story_items=items,
    )


def search_macro_events(
    db: Session,
    params: SearchRequest,
    user: Optional[Dict[str, Any]],
    start_ts: float,
    mode: str,
) -> SearchResponse:
    """Compatibility projection of current L2 chains into the old item model."""
    result = _search_current_v11_clusters(
        db,
        V11ClusterSearchRequest(
            keyword=_compatibility_search_keyword(params),
            level="micro",
            page=params.page,
            page_size=params.page_size,
        ),
    )
    items = [
        MacroEventItem(
            id=item.id,
            title=item.title,
            initiator=item.initiator,
            target=item.target,
            article_count=item.article_count,
            story_count=item.children_count,
            start_date=item.start_date,
            end_date=item.end_date,
            level="l2",
        )
        for item in result.items
    ]
    return SearchResponse(
        data=[],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        total_pages=result.total_pages,
        has_next=result.has_next,
        has_prev=result.has_prev,
        query_time_ms=(time.time() - start_ts) * 1000,
        macro_event_items=items,
    )


def execute_search_v11_clusters(
    db: Session,
    req: V11ClusterSearchRequest,
) -> V11ClusterSearchResponse:
    """Compatibility alias for the current hierarchy search adapter."""
    return _search_current_v11_clusters(db, req)


def expand_v11_cluster_children(
    db: Session,
    parent_id: Union[int, str],
    parent_level: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Compatibility alias for the current hierarchy children adapter."""
    return _expand_current_v11_children(
        db,
        parent_id,
        parent_level,
        page,
        page_size,
    )


def encode_query_bge_m3(q: str):
    """Encode through the BGE HTTP service, with a configurable local fallback."""
    import numpy as np
    text = (q or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="查询文本不能为空")

    # Prefer the configured BGE HTTP service. The historical :8001 default may
    # be absent, so V0.9.3 retains a local compatibility fallback by default.
    remote_error: Exception | None = None
    try:
        import json as _json

        base = string_setting("BGE_M3_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
        url = f"{base}/v1/embed"
        body = _json.dumps({"input": [text]}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1) as resp:
            raw = resp.read().decode("utf-8")
        data = _json.loads(raw) if raw else {}
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            emb = items[0].get("embedding") if isinstance(items[0], dict) else None
            if isinstance(emb, list) and emb:
                vec = np.asarray([float(x) for x in emb], dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                return vec / (norm + 1e-9)
    except Exception as exc:
        remote_error = exc

    local_fallback = string_setting("BGE_LOCAL_FALLBACK_ENABLED", "1").strip().lower()
    if local_fallback not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=503,
            detail="BGE HTTP embedding service unavailable; local model fallback is disabled",
        ) from remote_error

    try:
        from agentic_rag.ingestion.embedder import get_embedder
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail="BGE 依赖未就绪: pip install sentence-transformers PyYAML pyyaml; 错误: %s" % (e,),
        ) from e
    emb = get_embedder()
    vec = emb.encode([text])[0]
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / (norm + 1e-9)
