"""
事件级与全局涉华指数聚合 + 时间序列生成。

此文件已拆分为 ``agentic_rag.china_index.aggregator/`` 包，
此处仅为向后兼容的重新导出。

用法:
    from agentic_rag.china_index.aggregator import (
        event_china_timeseries,
        global_attention_index,
        composite_china_index,
    )
"""
from __future__ import annotations

# ruff: noqa: F401, F403

from agentic_rag.china_index.aggregator.event_timeseries import (
    _parse_date,
    _format_period,
    _judge_trend,
    _detect_anomalies,
    event_china_timeseries,
)
from agentic_rag.china_index.aggregator.d1_attention import (
    global_attention_index,
    attention_by_topic,
    attention_by_frame,
)
from agentic_rag.china_index.aggregator.d2_polarity import (
    global_sentiment_polarity,
    polarity_by_topic,
    polarity_by_frame,
)
from agentic_rag.china_index.aggregator.d3_uncertainty import (
    china_uncertainty_index,
)
from agentic_rag.china_index.aggregator.d4_dispersion import (
    narrative_dispersion,
    topic_dispersion,
)
from agentic_rag.china_index.aggregator.composite import (
    composite_china_index,
)

__all__ = [
    "event_china_timeseries",
    "global_attention_index",
    "attention_by_topic",
    "attention_by_frame",
    "global_sentiment_polarity",
    "polarity_by_topic",
    "polarity_by_frame",
    "china_uncertainty_index",
    "narrative_dispersion",
    "topic_dispersion",
    "composite_china_index",
]
