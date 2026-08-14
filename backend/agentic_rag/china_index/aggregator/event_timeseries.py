"""
1. 单事件涉华指数时间序列 (event_china_timeseries)。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np


def _parse_date(raw: Any) -> Optional[date]:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt).date()
            except (ValueError, IndexError):
                continue
    return None


def _format_period(d: date, freq: str) -> str:
    if freq == "day":
        return d.isoformat()
    elif freq == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    else:  # month
        return f"{d.year}-{d.month:02d}"


def _judge_trend(ts: List[Dict]) -> str:
    if len(ts) < 3:
        return "数据不足" if len(ts) < 2 else "平稳"
    means = [t["mean"] for t in ts]
    x = np.arange(len(means))
    slope = np.polyfit(x, means, 1)[0]
    if slope > 0.01:
        return "上升"
    elif slope < -0.01:
        return "下降"
    return "平稳"


def _detect_anomalies(ts: List[Dict], global_std: float) -> List[str]:
    anomalies: List[str] = []
    for i in range(1, len(ts)):
        prev = ts[i - 1]["mean"]
        curr = ts[i]["mean"]
        if prev <= 0:
            continue
        change_pct = (curr - prev) / prev
        if abs(change_pct) > 2 * global_std and global_std > 0.01:
            direction = "↑" if change_pct > 0 else "↓"
            anomalies.append(
                f"{ts[i]['period']}: {direction}{abs(change_pct)*100:.0f}% "
                f"({prev:.2f}→{curr:.2f})"
            )
    return anomalies


def event_china_timeseries(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    score_field: str = "china_related_index",
    weight_field: Optional[str] = "source_credibility",
    freq: str = "month",
) -> Dict[str, Any]:
    """计算单个 macro-event 的涉华指数沿时间轴的波动。"""
    if not articles:
        return {
            "timeseries": [],
            "overall_mean": 0.0,
            "volatility": 0.0,
            "trend": "无数据",
            "anomalies": [],
        }

    buckets: Dict[str, List[float]] = defaultdict(list)
    bucket_weights: Dict[str, List[float]] = defaultdict(list)

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
        if weight_field:
            w = art.get(weight_field, 1.0)
            try:
                bucket_weights[period].append(float(w))
            except (TypeError, ValueError):
                bucket_weights[period].append(1.0)

    sorted_periods = sorted(buckets.keys())
    all_scores = [s for scores in buckets.values() for s in scores]

    ts: List[Dict[str, Any]] = []
    for p in sorted_periods:
        vals = np.asarray(buckets[p], dtype=float)
        wvals = np.asarray(bucket_weights.get(p, [1.0] * len(vals)), dtype=float)
        weighted_mean = float(np.average(vals, weights=wvals)) if wvals.sum() > 0 else float(vals.mean())
        ts.append(
            {
                "period": p,
                "mean": round(float(vals.mean()), 4),
                "std": round(float(vals.std()), 4) if len(vals) > 1 else 0.0,
                "count": int(len(vals)),
                "max": round(float(vals.max()), 4),
                "min": round(float(vals.min()), 4),
                "weighted_mean": round(weighted_mean, 4),
            }
        )

    overall_mean = float(np.mean(all_scores))
    overall_std = float(np.std(all_scores)) if len(all_scores) > 1 else 0.0
    trend = _judge_trend(ts)
    anomalies = _detect_anomalies(ts, overall_std)

    return {
        "timeseries": ts,
        "overall_mean": round(overall_mean, 4),
        "volatility": round(overall_std, 4),
        "trend": trend,
        "anomalies": anomalies,
    }
