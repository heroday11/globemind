"""
4. D3: 涉华不确定性指数 (China Uncertainty Index, CUI)。

CUI 基于 Caldara & Iacoviello (2022) GPR 方法论：
  不确定性 = 涉华报道比例的波动性（滚动标准差）。

公式：
  ratio(t) = china_article_count(t) / total_article_count(t)
  CUI(t) = sigma_window(ratio)
  CUI_norm(t) = CUI(t) / mu_window(ratio)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np

from agentic_rag.china_index.aggregator.event_timeseries import _parse_date, _format_period


def china_uncertainty_index(
    articles: List[Dict[str, Any]],
    *,
    date_field: str = "published_at",
    index_field: str = "china_related_index",
    china_threshold: float = 0.4,
    freq: str = "month",
    rolling_window: int = 3,
) -> List[Dict[str, Any]]:
    """D3: 涉华不确定性指数（China Uncertainty Index）。

    基于涉华报道占比的滚动波动率。
    """
    buckets: Dict[str, List[float]] = defaultdict(list)

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

        dt = _parse_date(raw_date)
        if dt is None:
            continue
        period = _format_period(dt, freq)
        buckets[period].append(ci)

    sorted_periods = sorted(buckets.keys())
    if not sorted_periods:
        return []

    ratios: List[float] = []
    totals: List[int] = []
    chinas: List[int] = []
    for p in sorted_periods:
        scores = np.asarray(buckets[p], dtype=float)
        n_total = len(scores)
        n_china = int((scores >= china_threshold).sum())
        ratio = n_china / n_total if n_total > 0 else 0.0
        ratios.append(ratio)
        totals.append(n_total)
        chinas.append(n_china)

    ratios_arr = np.asarray(ratios, dtype=float)
    global_mean = float(ratios_arr.mean())
    global_std = float(ratios_arr.std()) if len(ratios_arr) > 1 else 0.0

    result: List[Dict[str, Any]] = []
    for i, p in enumerate(sorted_periods):
        start = max(0, i - rolling_window + 1)
        window_ratios = ratios_arr[start : i + 1]
        if len(window_ratios) >= 2:
            uncertainty = float(window_ratios.std())
            window_mean = float(window_ratios.mean())
        else:
            uncertainty = 0.0
            window_mean = ratios[i]

        cv = uncertainty / window_mean if window_mean > 0 else 0.0
        z = (ratios[i] - global_mean) / global_std if global_std > 0 else 0.0

        result.append({
            "period": p,
            "uncertainty": round(uncertainty, 6),
            "cv": round(cv, 4),
            "china_ratio": round(ratios[i], 6),
            "total_articles": totals[i],
            "china_articles": chinas[i],
            "z_score": round(z, 4),
        })

    return result
