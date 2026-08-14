"""
涉华舆情指数 API：聚合全部涉华新闻的 china_impact_sentiment，
经时间衰减（λ=0.1）叠加生成日度舆情指数走势。

GET /api/opinion/china-trend?days=365&china_min_score=0.4

返回 { dates: ["YYYY-MM-DD", ...], values: [float, ...], meta: {...} }
供前端 ECharts 折线图直接消费。
"""
from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Sequence

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.features.legacy_retirement import (
    RetiredEndpointResponse,
    retired_endpoint_contract,
)

# ── 轻量内存 TTL 缓存 ────────────────────────────────────────────────
# key: md5(func_name + query_params), value: (expires_at, content_dict)
_RESP_CACHE: dict[str, tuple[float, dict]] = {}


def _cache_key(func_name: str, **params: Any) -> str:
    raw = func_name + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[dict]:
    now = time.monotonic()
    entry = _RESP_CACHE.get(key)
    if entry and now < entry[0]:
        return entry[1]
    return None


def _cache_set(key: str, content: dict, ttl: float) -> None:
    _RESP_CACHE[key] = (time.monotonic() + ttl, content)

router = APIRouter()

DEFAULT_SOURCE_CREDIBILITY = 0.5

# ── 注意力衰减模型（shifted power-law）──
# 每篇文章的影响力随时间衰减：influence(t) = sentiment × CRI × decay(t - pub_date)
# 每日总值 = Σ 所有文章当日的衰减后影响力
DECAY_TAU_BASE = 1.0    # 基准半衰期（天），CRI=0 的文章
DECAY_TAU_SCALE = 4.0   # CRI 每增加 1，半衰期增加的天数
DECAY_ALPHA = 1.5       # 幂律指数，越大衰减越快
DECAY_MAX_LAG = 60      # 最大影响追踪天数（超过后截断）


def _coerce_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[0]
    elif " " in s:
        s = s.split()[0]
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _article_decay_weight(delta_days: float, importance: float) -> float:
    """
    Shifted power-law 注意力衰减模型。

    decay(0) = 1.0，在 δ = τ 时 decay = 0.5。
    符合真实新闻注意力衰减规律（幂律尾部）。

    参数：
        delta_days: 距发布的天数（0 = 发布当天）
        importance: 重要性 (0-1)，使用 CRI，越高衰减越慢
    返回：
        0-1 的衰减权重
    """
    if delta_days <= 0:
        return 1.0
    τ = DECAY_TAU_BASE + DECAY_TAU_SCALE * importance
    return 1.0 / (1 + (delta_days / τ) ** DECAY_ALPHA)


def _compute_decay_trend(
    start_d: date,
    end_d: date,
    article_rows: Sequence[dict],
) -> List[dict]:
    """
    基于注意力衰减模型的舆情指数计算。

    每篇文章在日期 t 的贡献 = sentiment × CRI × decay(t - pub_date)
    每日总值 = Σ 所有文章当日的衰减后贡献

    严格可加：总值的日差 = 当天新增贡献 - 衰减损失。
    无需额外平滑。
    """
    if start_d > end_d:
        return []
    n_days = (end_d - start_d).days + 1
    daily = [0.0] * n_days

    for r in article_rows:
        pd = _coerce_date(r.get("pub_date"))
        if pd is None:
            continue
        try:
            ss = float(r.get("sentiment_score", 0) or 0)
            ci = float(r.get("china_index", 0) or 0)
        except (TypeError, ValueError):
            continue

        base = ss * ci
        if base == 0:
            continue

        pub_offset = (pd - start_d).days
        if pub_offset >= n_days:
            continue  # 文章在窗口后发布，不贡献

        i_start = max(0, pub_offset)
        i_end = min(n_days, pub_offset + DECAY_MAX_LAG)
        for i in range(i_start, i_end):
            delta = i - pub_offset
            w = _article_decay_weight(delta, ci)
            daily[i] += base * w

    return [
        {"date": (start_d + timedelta(days=i)).isoformat(), "impact": round(daily[i], 6)}
        for i in range(n_days)
    ]


# Retained as an unregistered legacy implementation; opinion_v2 owns this path.
def get_china_opinion_trend(
    days: int = Query(365, ge=7, le=3650, description="回溯天数"),
    china_min_score: float = Query(0.4, ge=0.0, le=1.0, description="最低涉华指数阈值（china_related_index, 0-1）"),
    sentiment_filter: str = Query("all", description="情感过滤：all 全量叠加，positive 仅正面，negative 仅负面"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    聚合全部涉华新闻的 china_impact_sentiment，
    经日聚合 + 高斯平滑生成日度舆情指数走势。

    - 涉华筛选：news_ai_analysis.china_related_index >= china_min_score
    - 情感值：china_impact_sentiment（-1.0 ~ +1.0，已含涉华方向）
    - 算法：
        1) 每天 Σ(sentiment × china_index) —— 线性可加
        2) 可选高斯平滑（σ=1.0天，仅可视化平滑，不参与指数计算）
    - sentiment_filter：all 全量 / positive 仅正面 / negative 仅负面
    - china_min_score：0-1 浮点数，默认 0.4
    """
    try:
        ck = _cache_key("china_trend", days=days, china_min_score=china_min_score, sentiment_filter=sentiment_filter)
        cached = _cache_get(ck)
        if cached is not None:
            return JSONResponse(content=cached, media_type="application/json; charset=utf-8")
    except Exception:
        pass
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    fetch_start = start_date - timedelta(days=DECAY_MAX_LAG)

    rows = db.execute(
        text("""
            SELECT
                n.id,
                (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                na.china_impact_sentiment,
                COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
            FROM news_ai_analysis na
            JOIN news n ON n.id = na.news_id
            WHERE COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= :min_score
              AND na.china_impact_sentiment IS NOT NULL
              AND n.published_at IS NOT NULL
              AND n.published_at >= :fetch_start
              AND EXISTS (
                  SELECT 1 FROM event_coref_members ecm WHERE ecm.news_id = n.id
              )
            ORDER BY n.published_at ASC
        """),
        {
            "min_score": china_min_score,
            "fetch_start": fetch_start,
        },
    ).mappings().fetchall()

    article_rows = [
        {
            "pub_date": r["pub_date"],
            "sentiment_score": float(r["china_impact_sentiment"]),
            "source_credibility": DEFAULT_SOURCE_CREDIBILITY,
            "china_index": float(r["china_index"]),
        }
        for r in rows
        if r["pub_date"] is not None
    ]

    # 情感方向过滤
    if sentiment_filter == "positive":
        article_rows = [a for a in article_rows if a["sentiment_score"] > 0]
    elif sentiment_filter == "negative":
        article_rows = [a for a in article_rows if a["sentiment_score"] < 0]

    # 注意力衰减模型计算（无额外平滑）
    decay_trend = _compute_decay_trend(start_date, end_date, article_rows)

    # 以最后一条新闻的衰减截止日截断
    if article_rows:
        last_article_date = max(r["pub_date"] for r in article_rows)
        # 衰减后的影响持续 DECAY_MAX_LAG 天，截断多余的空窗
        last_str = last_article_date.isoformat()
        last_idx = None
        for i, t in enumerate(decay_trend):
            if t["date"] == last_str:
                last_idx = i
        if last_idx is not None:
            truncate_at = min(len(decay_trend), last_idx + 1 + DECAY_MAX_LAG)
            decay_trend = decay_trend[:truncate_at]
    else:
        last_article_date = None

    dates = [t["date"] for t in decay_trend]
    values = [t["impact"] for t in decay_trend]

    # 统计信息
    non_zero = [v for v in values if v != 0]
    meta = {
        "total_articles": len(article_rows),
        "last_article_date": last_article_date.isoformat() if last_article_date else None,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": len(decay_trend),
        "avg_impact": round(sum(non_zero) / len(non_zero), 4) if non_zero else 0.0,
        "max_impact": round(max(values), 4) if values else 0.0,
        "min_impact": round(min(values), 4) if values else 0.0,
        "china_min_score": china_min_score,
    }

    content = {
        "dates": dates,
        "values": values,             # 注意力衰减后日总值 = Σ sentiment × CRI × decay
        "meta": meta,
    }
    try:
        _cache_set(ck, content, ttl=300)
    except Exception:
        pass
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


def get_v3_aggregate_stats(
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    事件涉华分析综合统计（原 V3 聚类统计已废弃，改用 event-coref v2 数据）。
    返回事件聚类分布、覆盖率、涉华数据量等。
    """
    # 事件聚类分布
    sizes = db.execute(
        text("SELECT article_count FROM event_coref_clusters ORDER BY article_count")
    ).fetchall()
    sz = [s[0] for s in sizes]
    n_clusters = len(sz)

    cluster_dist = {}
    if sz:
        cluster_dist = {
            "total": n_clusters,
            "min": min(sz),
            "max": max(sz),
            "avg": round(sum(sz) / n_clusters, 1),
            "median": sorted(sz)[n_clusters // 2],
            "singletons": sum(1 for s in sz if s == 1),
            "tiny_lt5": sum(1 for s in sz if 1 < s < 5),
            "small_lt10": sum(1 for s in sz if 5 <= s < 10),
            "large_ge50": sum(1 for s in sz if s >= 50),
        }

    # Macro 事件 & Micro 故事
    n_macro = db.execute(text("SELECT COUNT(*) FROM macro_event_coref")).scalar() or 0
    n_micro = db.execute(text("SELECT COUNT(*) FROM micro_story_coref")).scalar() or 0

    # 覆盖率
    total_news = db.execute(text("SELECT COUNT(*) FROM news")).scalar() or 0
    clustered = db.execute(
        text("SELECT COUNT(DISTINCT news_id) FROM event_coref_members")
    ).scalar() or 0

    try:
        ck = _cache_key("v3_stats")
        cached = _cache_get(ck)
        if cached is not None:
            return JSONResponse(content=cached, media_type="application/json; charset=utf-8")
    except Exception:
        pass

    # 涉华数据
    china_relevant = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE COALESCE(china_relevance_score, prototype_weighted, 0) >= 0.4"),
    ).scalar() or 0
    china_with_sentiment = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE COALESCE(china_relevance_score, prototype_weighted, 0) >= 0.4 AND china_impact_sentiment IS NOT NULL"),
    ).scalar() or 0

    # 翻译覆盖
    translated = db.execute(
        text("SELECT COUNT(*) FROM news_translation WHERE translation_quality IS NOT NULL"),
    ).scalar() or 0

    # 媒体源
    media_count = db.execute(text("SELECT COUNT(*) FROM media_sources")).scalar() or 0

    content = {
        "ok": True,
        "event_coref": {
            "clusters": cluster_dist,
            "macro_events": n_macro,
            "micro_stories": n_micro,
        },
        "coverage": {
            "total_news": total_news,
            "clustered": clustered,
            "unclustered": total_news - clustered,
            "coverage_pct": round(clustered * 100 / total_news, 1) if total_news else 0,
        },
        "china_data": {
            "relevant_articles": china_relevant,
            "with_sentiment": china_with_sentiment,
        },
        "data_volume": {
            "translated_articles": translated,
            "media_sources": media_count,
        },
    }
    try:
        _cache_set(ck, content, ttl=600)
    except Exception:
        pass
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


def get_opinion_health(
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    舆情系统健康检查。返回各子维度的数据新鲜度和覆盖情况。
    """
    from datetime import timezone

    now = datetime.now(timezone.utc)

    # 各表最新数据时间
    latest_news = db.execute(
        text("SELECT MAX(n.published_at) FROM news_ai_analysis na JOIN news n ON n.id = na.news_id")
    ).scalar()
    latest_sentiment = db.execute(
        text("SELECT MAX(analyzed_at) FROM news_ai_analysis")
    ).scalar()

    # 延迟（小时）
    lag_hours = None
    if latest_news:
        lag = now - latest_news.replace(tzinfo=timezone.utc) if latest_news.tzinfo is None else now - latest_news
        lag_hours = round(lag.total_seconds() / 3600, 1)

    # 今日文章数
    today_start = now.date()
    today_articles = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis na JOIN news n ON n.id = na.news_id WHERE (n.published_at AT TIME ZONE 'UTC')::date = :today"),
        {"today": today_start},
    ).scalar() or 0

    # 今日涉华文章数
    today_china = db.execute(
        text("""
            SELECT COUNT(*) FROM news_ai_analysis na
            JOIN news n ON n.id = na.news_id
            WHERE (n.published_at AT TIME ZONE 'UTC')::date = :today
              AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
        """),
        {"today": today_start},
    ).scalar() or 0

    # 情感缺失比例
    total_rows = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis")
    ).scalar() or 1
    missing_sentiment = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE china_impact_sentiment IS NULL")
    ).scalar() or 0

    # 话题缺失比例
    missing_topic = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE topic IS NULL OR topic = ''")
    ).scalar() or 0

    # 框架缺失比例
    missing_frame = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE frame_classification IS NULL OR frame_classification = ''")
    ).scalar() or 0

    missing_prototype = db.execute(
        text("SELECT COUNT(*) FROM news_ai_analysis WHERE china_relevance_score IS NULL AND prototype_weighted IS NULL")
    ).scalar() or 0

    status = "healthy"
    alerts = []
    if missing_sentiment / total_rows > 0.05:
        alerts.append(f"情感缺失: {missing_sentiment}/{total_rows} ({missing_sentiment/total_rows*100:.1f}%)")
        status = "degraded"
    if missing_topic / total_rows > 0.10:
        alerts.append(f"话题缺失: {missing_topic}/{total_rows} ({missing_topic/total_rows*100:.1f}%)")
        status = "degraded"
    if missing_frame / total_rows > 0.10:
        alerts.append(f"框架缺失: {missing_frame}/{total_rows} ({missing_frame/total_rows*100:.1f}%)")
        status = "degraded"
    if missing_prototype / total_rows > 0.01:
        alerts.append(f"原型分缺失: {missing_prototype}/{total_rows} ({missing_prototype/total_rows*100:.1f}%)")
        status = "degraded"
    if lag_hours is not None and lag_hours > 24:
        alerts.append(f"数据延迟 {lag_hours}h > 24h")
        status = "degraded"

    return JSONResponse(content={
        "ok": True,
        "status": status,
        "alerts": alerts,
        "freshness": {
            "latest_news": latest_news.isoformat() if latest_news else None,
            "latest_analysis": latest_sentiment.isoformat() if latest_sentiment else None,
            "lag_hours": lag_hours,
            "today_articles": today_articles,
            "today_china_articles": today_china,
        },
        "coverage": {
            "total_rows": total_rows,
            "sentiment_pct": round((total_rows - missing_sentiment) / total_rows * 100, 1),
            "topic_pct": round((total_rows - missing_topic) / total_rows * 100, 1),
            "frame_pct": round((total_rows - missing_frame) / total_rows * 100, 1),
            "prototype_pct": round((total_rows - missing_prototype) / total_rows * 100, 1),
        },
        "missing": {
            "sentiment": missing_sentiment,
            "topic": missing_topic,
            "frame": missing_frame,
            "prototype_weighted": missing_prototype,
        },
    })


# ──────────────────────────────────────────────
# 事件下钻 API（点击折线图节点 -> 事件卡片 -> 新闻列表）
# ──────────────────────────────────────────────

def get_events_by_date(
    date_str: str = Query(..., description="日期 YYYY-MM-DD"),
    sentiment_filter: str = Query("all", description="情感过滤：all 全量，positive 仅正面，negative 仅负面"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    点击舆情指数图上某日期节点后，返回覆盖该日的 L2 宏观事件（macro_event_coref）。
    每个事件附带影响指数和近 30 天舆情走势，按涉华重要性排序。
    点击宏观事件可下钻查看其内部的 L1 子事件聚类（event_coref_clusters）。
    """
    import math
    target_date = date.fromisoformat(date_str)

    # 查找在该日期有 L1 聚类涉华新闻的 L2 宏观事件
    macro_rows = db.execute(
        text("""
            SELECT DISTINCT me.id, me.title, me.event_type_family AS event_type,
                   me.initiator, me.target, me.start_date, me.end_date,
                   me.article_count, me.cluster_count, me.story_count
            FROM macro_event_coref me
            WHERE EXISTS (
                SELECT 1 FROM macro_event_coref_members mcm
                JOIN news n ON n.id = mcm.news_id
                JOIN news_ai_analysis na ON na.news_id = n.id
                JOIN event_coref_members ecm ON ecm.news_id = n.id
                WHERE mcm.macro_event_id = me.id
                  AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                  AND na.china_impact_sentiment IS NOT NULL
                  AND (n.published_at AT TIME ZONE 'UTC')::date = :target_date
            )
            ORDER BY me.article_count DESC
            LIMIT 50
        """),
        {"target_date": target_date},
    ).mappings().fetchall()

    # ── 计算当日全局衰减后冲击力（仅 L1 聚类新闻） ──
    window_start_dt = target_date - timedelta(days=DECAY_MAX_LAG)
    all_day_data = db.execute(
        text("""
            SELECT (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                   na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
            FROM news_ai_analysis na
            JOIN news n ON n.id = na.news_id
            JOIN event_coref_members ecm ON ecm.news_id = n.id
            WHERE na.china_impact_sentiment IS NOT NULL
              AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
              AND n.published_at >= :window_start
              AND n.published_at < :window_end
        """),
        {"window_start": window_start_dt, "window_end": target_date + timedelta(days=1)},
    ).mappings().fetchall()

    all_rows_decay = [
        {
            "pub_date": r["pub_date"],
            "sentiment_score": float(r["china_impact_sentiment"]),
            "china_index": float(r["china_index"]),
        }
        for r in all_day_data
        if r["pub_date"] is not None
    ]
    if sentiment_filter == "positive":
        all_rows_decay = [a for a in all_rows_decay if a["sentiment_score"] > 0]
    elif sentiment_filter == "negative":
        all_rows_decay = [a for a in all_rows_decay if a["sentiment_score"] < 0]

    total_raw_daily = round(
        sum(
            a["sentiment_score"] * a["china_index"]
            * _article_decay_weight((target_date - a["pub_date"]).days, a["china_index"])
            for a in all_rows_decay
            if (target_date - a["pub_date"]).days >= 0
        ),
        4,
    )

    events = []
    for mr in macro_rows:
        me_id = mr["id"]
        query_start = target_date - timedelta(days=DECAY_MAX_LAG)

        # 统计该宏观事件的总涉华 L1 聚类文章数
        china_total = db.execute(
            text("""
                SELECT COUNT(*) FROM macro_event_coref_members mcm
                JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                JOIN event_coref_members ecm ON ecm.news_id = mcm.news_id
                WHERE mcm.macro_event_id = :me_id
                  AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
            """),
            {"me_id": me_id},
        ).scalar() or 0

        # 获取该宏观事件的涉华 L1 聚类文章用于趋势计算
        article_rows = db.execute(
            text("""
                SELECT (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                       na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                FROM macro_event_coref_members mcm
                JOIN news n ON n.id = mcm.news_id
                JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                JOIN event_coref_members ecm ON ecm.news_id = n.id
                WHERE mcm.macro_event_id = :me_id
                  AND na.china_impact_sentiment IS NOT NULL
                  AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                  AND n.published_at >= :start_date
                  AND n.published_at < :end_date
                ORDER BY n.published_at ASC
            """),
            {
                "me_id": me_id,
                "start_date": query_start,
                "end_date": target_date + timedelta(days=1),
            },
        ).mappings().fetchall()

        articles = [
            {
                "pub_date": a["pub_date"],
                "sentiment_score": float(a["china_impact_sentiment"]),
                "china_index": float(a["china_index"]),
            }
            for a in article_rows
            if a["pub_date"] is not None
        ]

        if sentiment_filter == "positive":
            articles = [a for a in articles if a["sentiment_score"] > 0]
        elif sentiment_filter == "negative":
            articles = [a for a in articles if a["sentiment_score"] < 0]

        daily_impact = round(
            sum(
                a["sentiment_score"] * a["china_index"]
                * _article_decay_weight((target_date - a["pub_date"]).days, a["china_index"])
                for a in articles
                if (target_date - a["pub_date"]).days >= 0
            ),
            2,
        )

        trend = _compute_decay_trend(query_start, target_date, articles)

        if len(trend) > 1:
            vals = [t["impact"] for t in trend]
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            volatility = round(math.sqrt(variance), 2)
        else:
            volatility = 0.0

        china_news = int(china_total)
        importance = round(
            abs(daily_impact) * math.log(2 + china_news) * (1 + volatility / 10),
            2,
        )

        label = mr["title"] or f"宏观事件 #{me_id}"

        events.append({
            "macro_id": str(me_id),
            "macro_event_id": me_id,
            "label": label,
            "event_type": mr["event_type"],
            "initiator": mr["initiator"],
            "target": mr["target"],
            "start_date": mr["start_date"].isoformat() if mr["start_date"] else None,
            "end_date": mr["end_date"].isoformat() if mr["end_date"] else None,
            "member_count": int(mr["article_count"]),
            "cluster_count": int(mr["cluster_count"]),
            "china_news_count": china_news,
            "impact_index": daily_impact,
            "daily_impact": daily_impact,
            "trend_avg": round(sum(t["impact"] for t in trend) / len(trend), 1) if trend else 0.0,
            "volatility": volatility,
            "china_importance": importance,
            "trend_dates": [t["date"] for t in trend],
            "trend_values": [t["impact"] for t in trend],
            "level": "l2",
        })

    events.sort(key=lambda e: e["china_importance"], reverse=True)

    return JSONResponse(
        content={
            "ok": True,
            "events": events,
            "total_raw_daily": round(total_raw_daily, 2),
        },
        media_type="application/json; charset=utf-8",
    )


def get_macro_event_clusters(
    macro_event_id: int = Query(..., description="宏观事件 ID（macro_event_coref.id）"),
    date_str: str = Query(..., description="原点击日期 YYYY-MM-DD"),
    sentiment_filter: str = Query("all", description="情感过滤：all 全量，positive 仅正面，negative 仅负面"),
    page: int = Query(1, description="页码，从1开始"),
    page_size: int = Query(30, description="每页条数"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    点击 L2 宏观事件后，返回其内部的 L1 子事件聚类（event_coref_clusters），
    每个子事件附带影响指数和近 30 天舆情走势，按涉华重要性排序。
    点击 L1 子事件可继续下钻查看新闻列表（通过 event-news 接口）。
    """
    import math
    target_date = date.fromisoformat(date_str)

    # 统计该宏观事件内的总聚类数（排除单文章簇）
    total_clusters = db.execute(
        text("""
            SELECT COUNT(DISTINCT mcm.cluster_id) FROM macro_event_coref_members mcm
            JOIN event_coref_clusters ec ON ec.cluster_id = mcm.cluster_id
            WHERE mcm.macro_event_id = :me_id
              AND ec.article_count >= 2
        """),
        {"me_id": macro_event_id},
    ).scalar() or 0
    china_clusters = db.execute(
        text("""
            SELECT COUNT(DISTINCT mcm.cluster_id) FROM macro_event_coref_members mcm
            JOIN event_coref_clusters ec ON ec.cluster_id = mcm.cluster_id
            WHERE mcm.macro_event_id = :me_id
              AND ec.article_count >= 2
              AND EXISTS (
                  SELECT 1 FROM news_ai_analysis na
                  JOIN event_coref_members ecm ON ecm.news_id = na.news_id
                  WHERE ecm.cluster_id = ec.cluster_id
                    AND na.china_impact_sentiment IS NOT NULL
                    AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
              )
        """),
        {"me_id": macro_event_id},
    ).scalar() or 0

    # 获取该宏观事件内的 L1 聚类（top 30），仅保留有涉华新闻的
    cluster_rows = db.execute(
        text("""
            SELECT DISTINCT ec.cluster_id, ec.title, ec.dominant_trigger, ec.article_count,
                   ec.event_type, ec.initiator, ec.target,
                   (
                       SELECT COUNT(*) FROM macro_event_coref_members mcm2
                       JOIN event_coref_members ecm2 ON ecm2.news_id = mcm2.news_id
                       JOIN news_ai_analysis na2 ON na2.news_id = mcm2.news_id
                       WHERE mcm2.macro_event_id = :me_id
                         AND mcm2.cluster_id = ec.cluster_id
                         AND COALESCE(na2.china_relevance_score, na2.prototype_weighted, 0) >= 0.4
                   ) AS china_news_count
            FROM macro_event_coref_members mcm
            JOIN event_coref_clusters ec ON ec.cluster_id = mcm.cluster_id
            WHERE mcm.macro_event_id = :me_id
              AND mcm.cluster_id IS NOT NULL
              AND ec.article_count >= 2
              AND EXISTS (
                  SELECT 1 FROM news_ai_analysis na
                  JOIN event_coref_members ecm ON ecm.news_id = na.news_id
                  WHERE ecm.cluster_id = ec.cluster_id
                    AND na.china_impact_sentiment IS NOT NULL
                    AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
              )
            ORDER BY ec.article_count DESC
            LIMIT 500
        """),
        {"me_id": macro_event_id},
    ).mappings().fetchall()

    sub_events = []
    for cr in cluster_rows:
        cluster_id = cr["cluster_id"]
        query_start = target_date - timedelta(days=DECAY_MAX_LAG)

        article_rows = db.execute(
            text("""
                SELECT (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                       na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                FROM event_coref_members ecm
                JOIN news n ON n.id = ecm.news_id
                JOIN news_ai_analysis na ON na.news_id = ecm.news_id
                WHERE ecm.cluster_id = :cluster_id
                  AND na.china_impact_sentiment IS NOT NULL
                  AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                  AND n.published_at >= :start_date
                  AND n.published_at < :end_date
                ORDER BY n.published_at ASC
            """),
            {
                "cluster_id": cluster_id,
                "start_date": query_start,
                "end_date": target_date + timedelta(days=1),
            },
        ).mappings().fetchall()

        articles = [
            {
                "pub_date": a["pub_date"],
                "sentiment_score": float(a["china_impact_sentiment"]),
                "china_index": float(a["china_index"]),
            }
            for a in article_rows
            if a["pub_date"] is not None
        ]

        if sentiment_filter == "positive":
            articles = [a for a in articles if a["sentiment_score"] > 0]
        elif sentiment_filter == "negative":
            articles = [a for a in articles if a["sentiment_score"] < 0]

        daily_impact = round(
            sum(
                a["sentiment_score"] * a["china_index"]
                * _article_decay_weight((target_date - a["pub_date"]).days, a["china_index"])
                for a in articles
                if (target_date - a["pub_date"]).days >= 0
            ),
            2,
        )

        trend = _compute_decay_trend(query_start, target_date, articles)

        if len(trend) > 1:
            vals = [t["impact"] for t in trend]
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            volatility = round(math.sqrt(variance), 2)
        else:
            volatility = 0.0

        china_news = int(cr["china_news_count"] or 0)
        importance = round(
            abs(daily_impact) * math.log(2 + china_news) * (1 + volatility / 10),
            2,
        )

        # 构造标签：优先用 LLM 生成的 title，次选 dominant_trigger + initiator/target
        if cr["title"]:
            label = cr["title"]
        else:
            label_parts = [cr["dominant_trigger"] or ""] if cr["dominant_trigger"] else []
            if cr["initiator"] and cr["target"]:
                label_parts = [f"{cr['initiator']} - {cr['target']}"]
            label = " / ".join(label_parts) if label_parts else f"事件 {cluster_id[-12:]}"
            if cr["event_type"]:
                label = f"[{cr['event_type']}] {label}"

        sub_events.append({
            "cluster_id": cluster_id,
            "macro_id": cluster_id,
            "label": label,
            "event_type": cr["event_type"],
            "initiator": cr["initiator"],
            "target": cr["target"],
            "member_count": int(cr["article_count"]),
            "china_news_count": china_news,
            "impact_index": daily_impact,
            "daily_impact": daily_impact,
            "volatility": volatility,
            "china_importance": importance,
            "trend_dates": [t["date"] for t in trend],
            "trend_values": [t["impact"] for t in trend],
            "level": "l1",
        })

    # 排序：非零 impact 在前（按 |impact| 降序），零 impact 在后
    sub_events.sort(key=lambda e: (
        e["daily_impact"] == 0,
        -abs(e["daily_impact"]),
    ))

    # 分页（total 用 china_clusters 实际总数）
    total = china_clusters
    offset = (page - 1) * page_size
    paged = sub_events[offset:offset + page_size]

    total_visible_impact = round(sum(e["daily_impact"] for e in paged), 2)

    return JSONResponse(
        content={
            "ok": True,
            "sub_events": paged,
            "macro_total_clusters": total_clusters,
            "china_clusters": china_clusters,
            "l1_total_impact": total_visible_impact,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": (offset + page_size) < total,
        },
        media_type="application/json; charset=utf-8",
    )


@router.get(
    "/opinion/micro-story-sub-events",
    tags=["舆情"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_micro_story_sub_events() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/micro-story-sub-events")


def get_event_news(
    cluster_id: Optional[str] = Query(None, description="事件聚类 ID（event_coref_clusters.cluster_id）"),
    macro_id: Optional[str] = Query(None, description="兼容旧前端，同 cluster_id"),
    micro_story_id: Optional[str] = Query(None, description="微故事 ID（剩余未聚类时使用）"),
    date: Optional[str] = Query(None, description="目标日期（剩余未聚类时使用，YYYY-MM-DD）"),
    remaining: bool = Query(False, description="是否为剩余未聚类文章"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """
    返回指定事件聚类中包含的涉华新闻列表（分页）。
    使用 event_coref_members + event_coref_clusters (v2)。
    当 remaining=True 时，返回微故事中未归入任何聚类的文章。
    """
    # 自动检测"剩余未聚类"模式：macro_id="remaining:YYYY-MM-DD"
    if macro_id and isinstance(macro_id, str) and macro_id.startswith("remaining:"):
        remaining = True
        parsed_date = macro_id.split(":", 1)[1]
        if not date and parsed_date:
            date = parsed_date
    if remaining:
        # 未聚类文章页数少，首屏多加载一些
        page_size = max(page_size, 200)
        if not date:
            return JSONResponse(
                content={"ok": False, "error": "remaining 模式需要 date"},
                status_code=400,
            )
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start_window = target_date - timedelta(days=30)

        if micro_story_id:
            # 子事件级未聚类：微故事内有文章但未归入任何聚类
            covered_ids = db.execute(
                text("""
                    SELECT DISTINCT ecm.cluster_id
                    FROM event_coref_members ecm
                    JOIN micro_story_coref_members mcm ON mcm.news_id = ecm.news_id
                    WHERE mcm.micro_story_id = :ms_id
                      AND ecm.cluster_id IS NOT NULL
                """),
                {"ms_id": micro_story_id},
            ).scalars().all()

            if covered_ids:
                ph = ", ".join(f":cid_{i}" for i in range(len(covered_ids)))
                params = {f"cid_{i}": cid for i, cid in enumerate(covered_ids)}
                params["ms_id"] = micro_story_id
                params["start_date"] = start_window
                params["end_date"] = target_date
                params["limit"] = page_size
                params["offset"] = (page - 1) * page_size

                total = db.execute(
                    text(f"""
                        SELECT COUNT(*)
                        FROM micro_story_coref_members mcm
                        JOIN news n ON n.id = mcm.news_id
                        JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                        WHERE mcm.micro_story_id = :ms_id
                          AND na.china_impact_sentiment IS NOT NULL
                          AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                          AND n.published_at >= :start_date
                          AND n.published_at <= :end_date
                          AND (mcm.cluster_id IS NULL OR mcm.cluster_id NOT IN ({ph}))
                    """),
                    params,
                ).scalar() or 0

                news_rows = db.execute(
                    text(f"""
                        SELECT n.id, n.title,
                               (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                               na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                        FROM micro_story_coref_members mcm
                        JOIN news n ON n.id = mcm.news_id
                        JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                        WHERE mcm.micro_story_id = :ms_id
                          AND na.china_impact_sentiment IS NOT NULL
                          AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                          AND n.published_at >= :start_date
                          AND n.published_at <= :end_date
                          AND (mcm.cluster_id IS NULL OR mcm.cluster_id NOT IN ({ph}))
                        ORDER BY n.published_at DESC
                        LIMIT :limit OFFSET :offset
                    """),
                    params,
                ).mappings().fetchall()
            else:
                params = {"ms_id": micro_story_id, "start_date": start_window, "end_date": target_date,
                          "limit": page_size, "offset": (page - 1) * page_size}
                total = db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM micro_story_coref_members mcm
                        JOIN news n ON n.id = mcm.news_id
                        JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                        WHERE mcm.micro_story_id = :ms_id
                          AND na.china_impact_sentiment IS NOT NULL
                          AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                          AND n.published_at >= :start_date
                          AND n.published_at <= :end_date
                    """),
                    params,
                ).scalar() or 0
                news_rows = db.execute(
                    text("""
                        SELECT n.id, n.title,
                               (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                               na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                        FROM micro_story_coref_members mcm
                        JOIN news n ON n.id = mcm.news_id
                        JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                        WHERE mcm.micro_story_id = :ms_id
                          AND na.china_impact_sentiment IS NOT NULL
                          AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                          AND n.published_at >= :start_date
                          AND n.published_at <= :end_date
                        ORDER BY n.published_at DESC
                        LIMIT :limit OFFSET :offset
                    """),
                    params,
                ).mappings().fetchall()
        else:
            # 事件级未聚类：该日期不属任何微故事的文章（仅当日，按重要性排序）
            params = {"target_date": target_date,
                      "limit": page_size, "offset": (page - 1) * page_size}
            total = db.execute(
                text("""
                    SELECT COUNT(*)
                    FROM news_ai_analysis na
                    JOIN news n ON n.id = na.news_id
                    WHERE na.china_impact_sentiment IS NOT NULL
                      AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                      AND (n.published_at AT TIME ZONE 'UTC')::date = :target_date
                      AND NOT EXISTS (
                          SELECT 1 FROM micro_story_coref_members mcm
                          JOIN micro_story_coref ms ON ms.id = mcm.micro_story_id
                          WHERE mcm.news_id = n.id
                            AND ms.start_date <= :target_date
                            AND ms.end_date >= :target_date
                      )
                """),
                params,
            ).scalar() or 0
            news_rows = db.execute(
                text("""
                    SELECT n.id, n.title,
                           (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                           na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                    FROM news_ai_analysis na
                    JOIN news n ON n.id = na.news_id
                    WHERE na.china_impact_sentiment IS NOT NULL
                      AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                      AND (n.published_at AT TIME ZONE 'UTC')::date = :target_date
                      AND NOT EXISTS (
                          SELECT 1 FROM micro_story_coref_members mcm
                          JOIN micro_story_coref ms ON ms.id = mcm.micro_story_id
                          WHERE mcm.news_id = n.id
                            AND ms.start_date <= :target_date
                            AND ms.end_date >= :target_date
                      )
                    ORDER BY na.china_impact_sentiment * COALESCE(na.china_relevance_score, na.prototype_weighted, 0) DESC
                    LIMIT :limit OFFSET :offset
                """),
                params,
            ).mappings().fetchall()

        news_list = [
            {
                "id": n["id"],
                "title": n["title"] or "无标题",
                "pub_date": n["pub_date"].isoformat() if n["pub_date"] else None,
                "sentiment": round(float(n["china_impact_sentiment"]), 3) if n["china_impact_sentiment"] is not None else 0.0,
                "china_index": round(float(n["china_index"]), 4) if n["china_index"] is not None else 0.0,
            }
            for n in news_rows
        ]

        return JSONResponse(
            content={
                "ok": True,
                "total": total,
                "page": page,
                "page_size": page_size,
                "news": news_list,
            },
            media_type="application/json; charset=utf-8",
        )

    cid = cluster_id or macro_id
    if not cid:
        return JSONResponse(
            content={"ok": False, "error": "必须指定 cluster_id 或 macro_id"},
            status_code=400,
        )

    # 优先查 event_coref_members（cluster_id 模式）；失败时回退 micro_story_coref_members
    total = db.execute(
        text("""
            SELECT COUNT(*)
            FROM event_coref_members ecm
            JOIN news_ai_analysis na ON na.news_id = ecm.news_id
            WHERE ecm.cluster_id = :cid
              AND na.china_impact_sentiment IS NOT NULL
              AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
        """),
        {"cid": cid},
    ).scalar() or 0

    offset = (page - 1) * page_size
    news_rows = []

    if total > 0:
        news_rows = db.execute(
            text("""
                SELECT n.id, n.title,
                       (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                       na.china_impact_sentiment,
                       COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                FROM event_coref_members ecm
                JOIN news n ON n.id = ecm.news_id
                JOIN news_ai_analysis na ON na.news_id = ecm.news_id
                WHERE ecm.cluster_id = :cid
                  AND na.china_impact_sentiment IS NOT NULL
                  AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                ORDER BY n.published_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"cid": cid, "limit": page_size, "offset": offset},
        ).mappings().fetchall()
    else:
        # 可能是 micro_story_id，尝试 micro_story_coref_members
        try:
            msid = int(cid)
        except (ValueError, TypeError):
            msid = None
        if msid is not None:
            total = db.execute(
                text("""
                    SELECT COUNT(*)
                    FROM micro_story_coref_members mcm
                    JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                    WHERE mcm.micro_story_id = :msid
                      AND na.china_impact_sentiment IS NOT NULL
                      AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                """),
                {"msid": msid},
            ).scalar() or 0

            if total > 0:
                news_rows = db.execute(
                    text("""
                        SELECT n.id, n.title,
                               (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                               na.china_impact_sentiment,
                               COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index
                        FROM micro_story_coref_members mcm
                        JOIN news n ON n.id = mcm.news_id
                        JOIN news_ai_analysis na ON na.news_id = mcm.news_id
                        WHERE mcm.micro_story_id = :msid
                          AND na.china_impact_sentiment IS NOT NULL
                          AND COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4
                        ORDER BY na.china_impact_sentiment * COALESCE(na.china_relevance_score, na.prototype_weighted, 0) DESC
                        LIMIT :limit OFFSET :offset
                    """),
                    {"msid": msid, "limit": page_size, "offset": offset},
                ).mappings().fetchall()

    news_list = [
        {
            "id": n["id"],
            "title": n["title"] or "无标题",
            "pub_date": n["pub_date"].isoformat() if n["pub_date"] else None,
            "sentiment": round(float(n["china_impact_sentiment"]), 3) if n["china_impact_sentiment"] is not None else 0.0,
            "china_index": round(float(n["china_index"]), 4) if n["china_index"] is not None else 0.0,
        }
        for n in news_rows
    ]

    return JSONResponse(
        content={
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "news": news_list,
        },
        media_type="application/json; charset=utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
# 阶段 2：事件级涉华分析
# ═══════════════════════════════════════════════════════════════════


@router.get(
    "/opinion/event-timeseries",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_event_china_timeseries() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/event-timeseries")


@router.get(
    "/opinion/global-attention",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_global_attention_index() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/global-attention")


@router.get(
    "/opinion/sentiment-polarity",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_global_sentiment_polarity() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/sentiment-polarity")


@router.get(
    "/opinion/influence-index",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_china_uncertainty() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/influence-index")


@router.get(
    "/opinion/composite-index",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_composite_china_index() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/composite-index")


@router.get(
    "/opinion/topic-breakdown",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_topic_breakdown() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/topic-breakdown")


@router.get(
    "/opinion/frame-breakdown",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_frame_breakdown() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/frame-breakdown")


@router.get(
    "/opinion/narrative-dispersion",
    tags=["舆情-事件分析"],
    status_code=410,
    response_model=RetiredEndpointResponse,
    deprecated=True,
)
def get_narrative_dispersion() -> RetiredEndpointResponse:
    """Return the stable retirement contract without resolving a database session."""
    return retired_endpoint_contract("/api/opinion/narrative-dispersion")
