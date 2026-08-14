"""LLM 涉华相关性评分回填 — 单元测试。"""
from __future__ import annotations

import pytest


def compute_combined_score(
    prototype_weighted: float | None,
    llm_score: int | None,
    *,
    prototype_weight: float = 0.65,
    llm_weight: float = 0.35,
) -> float:
    """加权融合 prototype_weighted 与 LLM 评分，输出 0-1 浮点值。"""
    if prototype_weighted is not None and llm_score is not None:
        llm_norm = llm_score / 10.0
        total_w = prototype_weight + llm_weight
        combined = (prototype_weight * prototype_weighted + llm_weight * llm_norm) / total_w
        return max(0.0, min(1.0, combined))
    if prototype_weighted is not None:
        return max(0.0, min(1.0, prototype_weighted))
    if llm_score is not None:
        return max(0.0, min(1.0, llm_score / 10.0))
    return 0.0


class TestComputeCombinedScore:
    def test_both_sources(self):
        """prototype_weighted=0.8 + llm_score=7 → 加权融合。"""
        score = compute_combined_score(0.8, 7)
        # 0.65 * 0.8 + 0.35 * 0.7 = 0.52 + 0.245 = 0.765
        assert abs(score - 0.765) < 1e-6

    def test_only_prototype(self):
        """仅 prototype_weighted。"""
        score = compute_combined_score(0.6, None)
        assert abs(score - 0.6) < 1e-6

    def test_only_llm(self):
        """仅 LLM 评分。"""
        score = compute_combined_score(None, 8)
        assert abs(score - 0.8) < 1e-6

    def test_both_none(self):
        """两者全缺失 → 0.0。"""
        assert compute_combined_score(None, None) == 0.0

    def test_clamping(self):
        """超出 [0,1] 的输入应被截断。"""
        score = compute_combined_score(-0.5, None)
        assert score == 0.0
        score = compute_combined_score(None, 11)
        assert abs(score - 1.0) < 1e-6

    def test_high_llm_low_prototype(self):
        """prototype_weighted 低但 LLM 认为高相关。"""
        score = compute_combined_score(0.2, 9)
        # 0.65 * 0.2 + 0.35 * 0.9 = 0.13 + 0.315 = 0.445
        assert abs(score - 0.445) < 1e-6

    def test_weight_param(self):
        """自定义权重。"""
        score = compute_combined_score(0.8, 7, prototype_weight=0.5, llm_weight=0.5)
        # 0.5 * 0.8 + 0.5 * 0.7 = 0.4 + 0.35 = 0.75
        assert abs(score - 0.75) < 1e-6

    def test_combined_score_range(self):
        """验证 combined score 0-1 的取值范围。"""
        score = compute_combined_score(1.0, 10)
        assert abs(score - 1.0) < 1e-6

        score = compute_combined_score(0.0, 1)
        assert score >= 0.0

    def test_extreme_values(self):
        """极端值组合。"""
        # prototype 最高 + LLM 最高
        score = compute_combined_score(1.0, 10)
        assert abs(score - 1.0) < 1e-6

        # prototype 最低 + LLM 最低
        score = compute_combined_score(0.0, 1)
        assert abs(score - 0.035) < 1e-6
