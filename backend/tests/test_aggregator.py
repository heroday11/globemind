"""涉华指数聚合器单元测试。

直接在 Python 中构造已知序列输入，验证 D1-D4 及复合指数计算是否正确。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_rag.china_index.aggregator import (
    global_attention_index,
    global_sentiment_polarity,
    china_uncertainty_index,
    narrative_dispersion,
    topic_dispersion,
    composite_china_index,
    attention_by_topic,
    polarity_by_topic,
)


# ── D1: Attention ──────────────────────────────────────────────────────


class TestGlobalAttentionIndex:
    def test_basic_monthly(self):
        """每月固定 1 篇涉华文章，attention 应基本恒定。"""
        articles = [
            {"published_at": date(2024, 1, 5), "china_related_index": 0.8},
            {"published_at": date(2024, 1, 15), "china_related_index": 0.6},
            {"published_at": date(2024, 2, 10), "china_related_index": 0.9},
            {"published_at": date(2024, 2, 20), "china_related_index": 0.5},
        ]
        result = global_attention_index(articles, freq="month")
        assert len(result) == 2
        assert result[0]["period"] == "2024-01"
        assert result[0]["attention"] > 0
        assert result[1]["period"] == "2024-02"

    def test_no_articles_returns_empty(self):
        assert global_attention_index([]) == []

    def test_zero_index_filtered(self):
        """china_related_index=0 不应进入 attention 计数。"""
        articles = [
            {"published_at": date(2024, 1, 5), "china_related_index": 0.0},
            {"published_at": date(2024, 1, 5), "china_related_index": 0.8},
        ]
        result = global_attention_index(articles, freq="month")
        assert len(result) == 1


# ── D2: Polarity ───────────────────────────────────────────────────────


class TestGlobalSentimentPolarity:
    def test_positive_sentiment(self):
        articles = [
            {"published_at": date(2024, 1, 5), "sentiment_score": 0.5, "china_related_index": 0.8},
            {"published_at": date(2024, 1, 15), "sentiment_score": 0.3, "china_related_index": 0.6},
        ]
        result = global_sentiment_polarity(articles, freq="month")
        assert len(result) == 1
        assert result[0]["polarity"] > 0  # 整体正面

    def test_negative_sentiment(self):
        articles = [
            {"published_at": date(2024, 1, 5), "sentiment_score": -0.8, "china_related_index": 0.9},
            {"published_at": date(2024, 1, 15), "sentiment_score": -0.5, "china_related_index": 0.7},
        ]
        result = global_sentiment_polarity(articles, freq="month")
        assert len(result) == 1
        assert result[0]["polarity"] < 0  # 整体负面


# ── D3: Uncertainty ────────────────────────────────────────────────────


class TestChinaUncertaintyIndex:
    def test_high_volatility(self):
        """涉华占比波动大 → uncertainty 高。"""
        articles = [
            {"published_at": date(2024, 1, d), "china_related_index": v}
            for d, v in [
                (1, 0.9), (2, 0.1), (3, 0.9), (4, 0.1), (5, 0.9),
                (6, 0.1), (7, 0.9), (8, 0.1), (9, 0.9), (10, 0.1),
            ]
        ]
        result = china_uncertainty_index(articles, china_threshold=0.4, rolling_window=2, freq="month")
        assert len(result) == 1

    def test_stable_ratio(self):
        """涉华占比稳定 → uncertainty 低。"""
        articles = [
            {"published_at": date(2024, 1, d), "china_related_index": 0.5}
            for d in range(1, 29)
        ]
        result = china_uncertainty_index(articles, china_threshold=0.4, rolling_window=2, freq="month")
        assert len(result) == 1

    def test_no_china_articles(self):
        articles = [{"published_at": date(2024, 1, 5), "china_related_index": 0.0}]
        result = china_uncertainty_index(articles, china_threshold=0.5, freq="month")
        assert len(result) == 1


# ── D4: Dispersion ─────────────────────────────────────────────────────


class TestNarrativeDispersion:
    def test_single_event(self):
        events = [
            {"id": "e1", "start_date": date(2024, 1, 5), "article_count": 10},
        ]
        result = narrative_dispersion(events, freq="month")
        assert len(result) == 1
        assert result[0]["dispersion"] == 0.0  # 单个事件 → 无分散度


class TestTopicDispersion:
    def test_multiple_topics(self):
        articles = [
            {"published_at": date(2024, 1, 5), "topic_classification": "中美贸易", "china_related_index": 0.8},
            {"published_at": date(2024, 1, 10), "topic_classification": "南海问题", "china_related_index": 0.7},
            {"published_at": date(2024, 1, 15), "topic_classification": "中美贸易", "china_related_index": 0.6},
        ]
        result = topic_dispersion(articles, freq="month")
        assert len(result) == 1
        assert result[0]["dispersion"] > 0  # 多个话题 → 有分散度


# ── Composite Index ────────────────────────────────────────────────────


class TestCompositeChinaIndex:
    def test_equal_weight(self):
        att = [{"period": "2024-01", "attention": 10.0}]
        pol = [{"period": "2024-01", "polarity": 0.5}]
        unc = [{"period": "2024-01", "uncertainty": 0.3, "china_ratio": 0.5}]
        dis = [{"period": "2024-01", "dispersion": 0.7}]

        result = composite_china_index(att, pol, unc, dis, normalize=False)
        assert len(result) == 1
        assert "cci" in result[0]
        assert "raw" in result[0]
        # 等权重: 每个维度 0.25
        expected = 0.25 * 10.0 + 0.25 * abs(0.5) + 0.25 * 0.3 + 0.25 * 0.7
        assert abs(result[0]["cci"] - expected) < 1e-6

    def test_period_mismatch(self):
        """各维度周期不一致时缺失维度用 0 填充。"""
        att = [{"period": "2024-01", "attention": 10.0}]
        pol = [{"period": "2024-01", "polarity": 0.5}]
        unc = [{"period": "2024-02", "uncertainty": 0.3, "china_ratio": 0.5}]  # 不同月
        dis = [{"period": "2024-01", "dispersion": 0.7}]

        result = composite_china_index(att, pol, unc, dis, normalize=False)
        # 两个 period 都出现
        periods = {r["period"] for r in result}
        assert periods == {"2024-01", "2024-02"}


# ── Topic Decomposition ────────────────────────────────────────────────


class TestAttentionByTopic:
    def test_topic_split(self):
        articles = [
            {"published_at": date(2024, 1, 5), "topic_classification": "中美贸易", "china_related_index": 0.8},
            {"published_at": date(2024, 1, 10), "topic_classification": "南海问题", "china_related_index": 0.6},
            {"published_at": date(2024, 1, 15), "topic_classification": "中美贸易", "china_related_index": 0.7},
        ]
        result = attention_by_topic(articles, freq="month", top_n=5)
        assert len(result) == 1
        topics = result[0]["topics"]
        assert len(topics) >= 2
        china_trade = [t for t in topics if t["topic"] == "中美贸易"]
        assert len(china_trade) == 1
        assert china_trade[0]["attention"] > 0


# ── Backtest helpers ───────────────────────────────────────────────────


def _compute_baseline(
    series: list[dict], event_idx: int, lookback: int
) -> float:
    """事件前 lookback 天的中位 impact（剔除 0）。"""
    vals = [
        s["impact"]
        for s in series[max(0, event_idx - lookback) : event_idx]
        if s["impact"] != 0.0
    ]
    if not vals:
        return 0.0
    vals.sort()
    return vals[len(vals) // 2]


def _find_peak(series: list[dict], event_idx: int, lookahead: int) -> float:
    """事件后 lookahead 天内的最大 |impact|。"""
    end = min(len(series), event_idx + lookahead + 1)
    vals = [abs(s["impact"]) for s in series[event_idx:end]]
    return max(vals) if vals else 0.0


class TestBacktestHelpers:
    def test_z_score(self):
        series = [
            {"date": "2024-01-01", "impact": 0.1},
            {"date": "2024-01-02", "impact": 0.2},
            {"date": "2024-01-03", "impact": 0.0},
            {"date": "2024-01-04", "impact": 1.5},  # 事件日
        ]
        baseline = _compute_baseline(series, 3, lookback=3)
        assert baseline > 0
        peak = _find_peak(series, 3, lookahead=1)
        assert peak == 1.5
