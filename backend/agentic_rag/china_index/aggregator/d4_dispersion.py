"""
5. D4: 涉华叙事分散度指数 (Narrative / Topic Dispersion)。

Dispersion(t) = 1 - Σ(份额²)   — Herfindahl-Hirschman 逆指数
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

import numpy as np

from agentic_rag.china_index.aggregator.event_timeseries import _parse_date, _format_period


def narrative_dispersion(
    events: List[Dict[str, Any]],
    *,
    date_field: str = "start_date",
    article_count_field: str = "article_count",
    freq: str = "month",
) -> List[Dict[str, Any]]:
    """计算基于事件的叙事分散度（HHI 逆指数）。"""
    buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for ev in events:
        raw_date = ev.get(date_field)
        if raw_date is None:
            continue
        dt = _parse_date(raw_date)
        if dt is None:
            continue
        period = _format_period(dt, freq)
        eid = str(ev.get("id", ""))
        count = int(ev.get(article_count_field, 0) or 0)
        buckets[period][eid] += count

    sorted_periods = sorted(buckets.keys())
    result: List[Dict[str, Any]] = []
    for p in sorted_periods:
        counts = np.asarray(list(buckets[p].values()), dtype=float)
        shares = counts / counts.sum()
        hhi = float((shares**2).sum())
        dispersion = 1.0 - hhi
        n_events = int((shares > 0).sum())

        result.append(
            {
                "period": p,
                "dispersion": round(dispersion, 4),
                "hhi": round(hhi, 4),
                "n_events": n_events,
            }
        )
    return result


def topic_dispersion(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    topic_field: str = "topic_classification",
    index_field: str = "china_related_index",
    china_threshold: float = 0.4,
    freq: str = "month",
) -> List[Dict[str, Any]]:
    """基于 LLM 话题分类的叙事分散度。"""
    buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for art in articles:
        raw_date = art.get(date_field)
        if raw_date is None:
            continue
        ci = art.get(index_field)
        if ci is None:
            continue
        try:
            ci = float(ci)
        except (TypeError, ValueError):
            continue
        if ci < china_threshold:
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

        buckets[period][topic] += 1

    sorted_periods = sorted(buckets.keys())
    result: List[Dict[str, Any]] = []
    for p in sorted_periods:
        topic_counts = buckets[p]
        counts = np.asarray(list(topic_counts.values()), dtype=float)
        total = float(counts.sum())
        shares = counts / total if total > 0 else np.zeros_like(counts)
        hhi = float((shares**2).sum())
        dispersion = 1.0 - hhi
        n_topics = int((shares > 0).sum())

        topics_sorted = sorted(
            [{"topic": t, "share": round(c / total, 4)} for t, c in topic_counts.items()],
            key=lambda x: x["share"],
            reverse=True,
        )

        result.append(
            {
                "period": p,
                "dispersion": round(dispersion, 4),
                "hhi": round(hhi, 4),
                "n_topics": n_topics,
                "topics": topics_sorted[:15],
            }
        )
    return result
