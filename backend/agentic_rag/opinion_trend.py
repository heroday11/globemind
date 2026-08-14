"""
宏观故事线舆情时间衰减：日度 Public Opinion Impact 聚合（NumPy 向量化）。
Impact_i(t) = sentiment × source_credibility × china_related_index × exp(-λ·(t - pub_i))，t < pub_i 时为 0。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional, Sequence

import numpy as np

OPINION_DECAY_LAMBDA = 0.1  # 约 7 天减半：exp(-0.1*7) ≈ 0.5


def coerce_news_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    if "T" in s and len(s) >= 10:
        s = s.split("T", 1)[0]
    elif " " in s:
        s = s.split()[0]
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def resolve_macro_date_range(
    start_any: Any,
    end_any: Any,
    pub_dates: Sequence[date],
) -> tuple[Optional[date], Optional[date]]:
    """用宏观 start/end 与成员发稿日对齐；缺省时用稿件最小/最大日。"""
    sd = coerce_news_date(start_any)
    ed = coerce_news_date(end_any)
    if pub_dates:
        ords = sorted(d.toordinal() for d in pub_dates)
        mn = date.fromordinal(ords[0])
        mx = date.fromordinal(ords[-1])
        if sd is None:
            sd = mn
        if ed is None:
            ed = mx
    if sd is None or ed is None:
        return None, None
    if sd > ed:
        sd, ed = ed, sd
    return sd, ed


def compute_opinion_trend_json(
    start_d: date,
    end_d: date,
    article_rows: Sequence[dict],
) -> List[dict]:
    """
    返回 [{"date": "YYYY-MM-DD", "impact": float}, ...] 覆盖 [start_d, end_d] 每日一行。
    article_rows 每项可含 pub_date（date/datetime/str）、sentiment_score、source_credibility、china_related_index。
    """
    if start_d > end_d:
        return []
    start_ord = start_d.toordinal()
    end_ord = end_d.toordinal()
    n_days = end_ord - start_ord + 1

    pub_ords: List[int] = []
    bases: List[float] = []
    for r in article_rows:
        pd = coerce_news_date(r.get("pub_date"))
        if pd is None:
            continue
        p_ord = pd.toordinal()
        if p_ord > end_ord:
            continue
        try:
            ss = float(r["sentiment_score"]) if r.get("sentiment_score") is not None else 0.0
            sc = float(r["source_credibility"]) if r.get("source_credibility") is not None else 0.0
            ci = float(r["china_related_index"]) if r.get("china_related_index") is not None else 0.0
        except (TypeError, ValueError):
            continue
        base = ss * sc * ci
        if base == 0.0:
            continue
        pub_ords.append(p_ord)
        bases.append(base)

    out: List[dict] = []
    if not pub_ords:
        for i in range(n_days):
            d = date.fromordinal(start_ord + i)
            out.append({"date": d.isoformat(), "impact": 0.0})
        return out

    po = np.asarray(pub_ords, dtype=np.int64)
    ba = np.asarray(bases, dtype=np.float64)
    day_ords = np.arange(start_ord, end_ord + 1, dtype=np.int64)
    diff = day_ords[:, np.newaxis] - po[np.newaxis, :]
    mask = diff >= 0
    contrib = np.where(
        mask,
        ba[np.newaxis, :] * np.exp(-OPINION_DECAY_LAMBDA * diff.astype(np.float64)),
        0.0,
    )
    impacts = contrib.sum(axis=1)

    for i in range(n_days):
        d = date.fromordinal(start_ord + i)
        out.append({"date": d.isoformat(), "impact": round(float(impacts[i]), 6)})
    return out
