"""
6. 四维复合涉华指数 (Composite China Index, CCI)。

CCI(t) = w1 × Attention_norm(t) + w2 × |Polarity_norm(t)|
       + w3 × Uncertainty_norm(t) + w4 × Dispersion_norm(t)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def composite_china_index(
    attention_series: List[Dict[str, Any]],
    polarity_series: List[Dict[str, Any]],
    uncertainty_series: List[Dict[str, Any]],
    dispersion_series: List[Dict[str, Any]],
    *,
    weights: Optional[Tuple[float, float, float, float]] = None,
    normalize: bool = True,
    baseline_periods: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """计算四维实用复合指数 CCI(t)。

    Args:
        attention_series: global_attention_index 的输出。
        polarity_series: global_sentiment_polarity 的输出。
        uncertainty_series: china_uncertainty_index 的输出。
        dispersion_series: narrative_dispersion 或 topic_dispersion 的输出。
        weights: 四维权重 (w_att, w_pol, w_unc, w_dis)，默认等权重 (0.25, 0.25, 0.25, 0.25)。
        normalize: 是否对每维 min-max 归一化到 [0,1]。

    Returns:
        [{"period": "...", "cci": 0.52, "attention_norm": 0.31, ...}, ...]
    """
    if weights is None:
        weights = (0.25, 0.25, 0.25, 0.25)
    w_att, w_pol, w_unc, w_dis = weights

    period_map: Dict[str, dict] = {}
    for entry in attention_series:
        p = entry["period"]
        period_map.setdefault(p, {})["attention"] = entry.get("attention", 0.0)
    for entry in polarity_series:
        p = entry["period"]
        period_map.setdefault(p, {})["polarity"] = abs(entry.get("polarity", 0.0))
    for entry in uncertainty_series:
        p = entry["period"]
        period_map.setdefault(p, {})["uncertainty"] = entry.get("uncertainty", 0.0)
    for entry in dispersion_series:
        p = entry["period"]
        period_map.setdefault(p, {})["dispersion"] = entry.get("dispersion", 0.0)

    sorted_periods = sorted(period_map.keys())
    if not sorted_periods:
        return []

    raw_att = np.asarray([period_map[p].get("attention", 0.0) for p in sorted_periods], dtype=float)
    raw_pol = np.asarray([period_map[p].get("polarity", 0.0) for p in sorted_periods], dtype=float)
    raw_unc = np.asarray([period_map[p].get("uncertainty", 0.0) for p in sorted_periods], dtype=float)
    raw_dis = np.asarray([period_map[p].get("dispersion", 0.0) for p in sorted_periods], dtype=float)

    def _minmax(arr: np.ndarray) -> np.ndarray:
        mn, mx = float(arr.min()), float(arr.max())
        if mx - mn < 1e-12:
            return np.zeros_like(arr)
        return (arr - mn) / (mx - mn)

    if normalize:
        att_norm = _minmax(raw_att)
        pol_norm = _minmax(raw_pol)
        unc_norm = _minmax(raw_unc)
        dis_norm = _minmax(raw_dis)
    else:
        att_norm = raw_att
        pol_norm = raw_pol
        unc_norm = raw_unc
        dis_norm = raw_dis

    result: List[Dict[str, Any]] = []
    for i, p in enumerate(sorted_periods):
        cci = (
            w_att * att_norm[i]
            + w_pol * pol_norm[i]
            + w_unc * unc_norm[i]
            + w_dis * dis_norm[i]
        )
        result.append(
            {
                "period": p,
                "cci": round(float(cci), 4),
                "attention_norm": round(float(att_norm[i]), 4),
                "polarity_norm": round(float(pol_norm[i]), 4),
                "uncertainty_norm": round(float(unc_norm[i]), 4),
                "dispersion_norm": round(float(dis_norm[i]), 4),
                "raw": {
                    "attention": round(float(raw_att[i]), 4),
                    "polarity": round(float(raw_pol[i]), 4),
                    "uncertainty": round(float(raw_unc[i]), 4),
                    "dispersion": round(float(raw_dis[i]), 4),
                },
            }
        )
    return result
