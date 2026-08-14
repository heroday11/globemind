"""
2. D1: 注意力指数 (Global Attention Index) + 按议题/框架分解。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import numpy as np

from agentic_rag.china_index.aggregator.event_timeseries import _parse_date, _format_period


CHINA_THRESHOLD = 0.4


def global_attention_index(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    score_field: str = "china_related_index",
    freq: str = "month",
) -> List[Dict[str, Any]]:
    """D1: 涉华注意力指数（Agenda-Setting Theory）。

    Attention(t) = [Σ china_index_i / N_total(t)] × 1000
    """
    buckets: Dict[str, List[float]] = defaultdict(list)

    for art in articles:
        raw_date = art.get(date_field)
        if raw_date is None:
            continue
        score = art.get(score_field)
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue

        dt = _parse_date(raw_date)
        if dt is None:
            continue
        period = _format_period(dt, freq)
        buckets[period].append(score)

    sorted_periods = sorted(buckets.keys())
    result: List[Dict[str, Any]] = []
    for p in sorted_periods:
        scores = np.asarray(buckets[p], dtype=float)
        n_total = len(scores)
        attention = float(scores.sum()) / n_total * 1000.0 if n_total > 0 else 0.0
        is_china = scores >= CHINA_THRESHOLD

        result.append(
            {
                "period": p,
                "attention": round(attention, 4),
                "article_count": n_total,
                "china_count": int(is_china.sum()),
                "china_ratio": round(float(is_china.mean()), 4) if n_total > 0 else 0.0,
                "avg_index": round(float(scores.mean()), 4) if n_total > 0 else 0.0,
            }
        )
    return result


def attention_by_topic(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    score_field: str = "china_related_index",
    topic_field: str = "topic_classification",
    freq: str = "month",
    top_n: int = 10,
    min_topic_share: float = 0.01,
) -> List[Dict[str, Any]]:
    """D1 议题级分解：按话题展示注意力分布。"""
    period_data: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

    for art in articles:
        raw_date = art.get(date_field)
        if raw_date is None:
            continue
        score = art.get(score_field)
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue

        dt = _parse_date(raw_date)
        if dt is None:
            continue
        period = _format_period(dt, freq)

        topic = art.get(topic_field)
        if not topic or not isinstance(topic, str) or topic.strip() == "":
            topic = "_未分类"
        else:
            topic = topic.strip()

        period_data[period][topic].append(score)
        period_data[period]["_total"].append(score)

    sorted_periods = sorted(period_data.keys())
    result: List[Dict[str, Any]] = []

    for p in sorted_periods:
        topics = period_data[p]
        total_scores = np.asarray(topics.pop("_total", [0.0]), dtype=float)
        n_total = len(total_scores)
        total_attention = float(total_scores.sum()) / n_total * 1000.0 if n_total > 0 else 0.0

        topic_list: List[Dict[str, Any]] = []
        other_sum = 0.0
        other_count = 0

        for topic_name, scores in topics.items():
            s = np.asarray(scores, dtype=float)
            t_att = float(s.sum()) / n_total * 1000.0 if n_total > 0 else 0.0
            t_pct = float(s.sum()) / float(total_scores.sum()) if float(total_scores.sum()) > 0 else 0.0

            if t_pct >= min_topic_share:
                topic_list.append({
                    "topic": topic_name,
                    "attention": round(t_att, 4),
                    "pct": round(t_pct, 4),
                    "article_count": int(len(s)),
                    "avg_index": round(float(s.mean()), 4),
                })
            else:
                other_sum += t_att
                other_count += int(len(s))

        topic_list.sort(key=lambda x: x["attention"], reverse=True)
        topic_list = topic_list[:top_n]

        if other_count > 0:
            topic_list.append({
                "topic": "_其他",
                "attention": round(other_sum, 4),
                "pct": round(other_sum / total_attention, 4) if total_attention > 0 else 0.0,
                "article_count": other_count,
                "avg_index": 0.0,
            })

        result.append({
            "period": p,
            "total_attention": round(total_attention, 4),
            "article_count": n_total,
            "china_count": int((total_scores >= CHINA_THRESHOLD).sum()),
            "topics": topic_list,
        })

    return result


def attention_by_frame(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    score_field: str = "china_related_index",
    frame_field: str = "frame_classification",
    freq: str = "month",
    top_n: int = 10,
    min_frame_share: float = 0.01,
) -> List[Dict[str, Any]]:
    """D1 框架级分解：按新闻框架展示注意力分布。"""
    return attention_by_topic(
        articles,
        date_field=date_field,
        score_field=score_field,
        topic_field=frame_field,
        freq=freq,
        top_n=top_n,
        min_topic_share=min_frame_share,
    )
