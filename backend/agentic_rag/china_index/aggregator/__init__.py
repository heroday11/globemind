"""
聚合器拆分包 — 保持向后兼容的重新导出。

原 aggregator.py 已拆分为以下子模块：
  - event_timeseries: 单事件涉华时间序列
  - d1_attention:     注意力指数 + 议题级分解
  - d2_polarity:      情感效价 + 议题级分解
  - d3_uncertainty:   不确定性指数 (CUI)
  - d4_dispersion:    叙事分散度
  - composite:        四维复合指数

所有公开函数均通过 __init__.py 重新导出，
from aggregator import X 的写法不受影响。
"""
from __future__ import annotations

from agentic_rag.china_index.aggregator.event_timeseries import (
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
