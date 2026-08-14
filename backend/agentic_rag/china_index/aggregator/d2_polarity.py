"""
3. D2: 情感效价 (Sentiment Polarity) + 按议题/框架分解。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np

from agentic_rag.china_index.aggregator.event_timeseries import _parse_date, _format_period


def polarity_by_topic(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    sentiment_field: str = "sentiment_score",
    index_field: str = "china_related_index",
    topic_field: str = "topic_classification",
    freq: str = "month",
    top_n: int = 10,
    min_topic_share: float = 0.01,
) -> List[Dict[str, Any]]:
    """D2 议题级分解：按话题展示情感效价分布。"""
    period_topics: Dict[str, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))

    for art in articles:
        raw_date = art.get(date_field)
        if raw_date is None:
            continue
        sentiment = art.get(sentiment_field)
        ci = art.get(index_field)
        if sentiment is None or ci is None:
            continue
        try:
            sentiment = float(sentiment)
            ci = float(ci)
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

        period_topics[period][topic].append((sentiment, ci))

    sorted_periods = sorted(period_topics.keys())
    result: List[Dict[str, Any]] = []

    for p in sorted_periods:
        topic_data = period_topics[p]
        all_c = np.asarray([c for vals in topic_data.values() for _, c in vals], dtype=float)
        total_weight = float(all_c.sum())

        topic_list: List[Dict[str, Any]] = []
        other_weight = 0.0
        other_count = 0
        other_pol_num = 0.0

        for topic_name, pairs in topic_data.items():
            sentiments = np.asarray([s for s, _ in pairs], dtype=float)
            indices = np.asarray([c for _, c in pairs], dtype=float)
            w = float(indices.sum())
            w_pct = w / total_weight if total_weight > 0 else 0.0
            pol = float((sentiments * indices).sum()) / w if w > 0 else 0.0

            if w_pct >= min_topic_share:
                topic_list.append({
                    "topic": topic_name,
                    "polarity": round(pol, 4),
                    "article_count": int(len(sentiments)),
                    "weight": round(w, 4),
                    "weight_pct": round(w_pct, 4),
                })
            else:
                other_weight += w
                other_count += int(len(sentiments))
                other_pol_num += float((sentiments * indices).sum())

        topic_list.sort(key=lambda x: x["weight"], reverse=True)
        topic_list = topic_list[:top_n]

        if other_count > 0:
            topic_list.append({
                "topic": "_其他",
                "polarity": round(other_pol_num / other_weight, 4) if other_weight > 0 else 0.0,
                "article_count": other_count,
                "weight": round(other_weight, 4),
                "weight_pct": round(other_weight / total_weight, 4) if total_weight > 0 else 0.0,
            })

        result.append({
            "period": p,
            "topics": topic_list,
        })

    return result


def polarity_by_frame(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    sentiment_field: str = "sentiment_score",
    index_field: str = "china_related_index",
    frame_field: str = "frame_classification",
    freq: str = "month",
    top_n: int = 10,
    min_frame_share: float = 0.01,
) -> List[Dict[str, Any]]:
    """D2 框架级分解：按新闻框架展示情感效价。"""
    return polarity_by_topic(
        articles,
        date_field=date_field,
        sentiment_field=sentiment_field,
        index_field=index_field,
        topic_field=frame_field,
        freq=freq,
        top_n=top_n,
        min_topic_share=min_frame_share,
    )


def global_sentiment_polarity(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    sentiment_field: str = "sentiment_score",
    index_field: str = "china_related_index",
    freq: str = "month",
) -> List[Dict[str, Any]]:
    """D2: 涉华加权情感效价（Sentiment Valence）。

    Polarity(t) = Σ(sentiment_i × china_index_i) / Σ(china_index_i)
    """
    buckets: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

    for art in articles:
        raw_date = art.get(date_field)
        if raw_date is None:
            continue
        sentiment = art.get(sentiment_field)
        china_idx = art.get(index_field)
        if sentiment is None or china_idx is None:
            continue
        try:
            sentiment = float(sentiment)
            china_idx = float(china_idx)
        except (TypeError, ValueError):
            continue

        dt = _parse_date(raw_date)
        if dt is None:
            continue
        period = _format_period(dt, freq)
        buckets[period].append((sentiment, china_idx))

    sorted_periods = sorted(buckets.keys())
    result: List[Dict[str, Any]] = []
    for p in sorted_periods:
        pairs = buckets[p]
        sentiments = np.asarray([s for s, _ in pairs], dtype=float)
        indices = np.asarray([c for _, c in pairs], dtype=float)

        total_weight = float(indices.sum())
        if total_weight > 0:
            polarity = float((sentiments * indices).sum()) / total_weight
        else:
            polarity = 0.0

        positive = int((sentiments > 0.1).sum())
        negative = int((sentiments < -0.1).sum())
        neutral = int(len(sentiments) - positive - negative)

        result.append(
            {
                "period": p,
                "polarity": round(float(polarity), 4),
                "positive": positive,
                "negative": negative,
                "neutral": neutral,
                "total": int(len(sentiments)),
                "mean_sentiment": round(float(sentiments.mean()), 4) if len(sentiments) > 0 else 0.0,
            }
        )
    return result
