"""涉华指数模块（精简版）。

管线涉华评分：
    BGE-M3 编码 → 逻辑回归（99K 标注样本）→ 向量相似度兜底（连续分数，非固定阈值）。
写库字段：news_ai_analysis.china_relevance_score。

外部调用：
    from agentic_rag.china_index.learned_model import predict_proba_batch
"""
from __future__ import annotations

__all__: list[str] = []

