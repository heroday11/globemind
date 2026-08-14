"""多原型 BGE-M3 嵌入评分：6 维涉华语义相似度。

用法:
    from agentic_rag.china_index.prototypes import score_by_prototypes

    # 给一条新闻打分
    result = score_by_prototypes(text="新闻标题\\n摘要\\n正文前2000字")
    # -> {"scores": [0.1, 0.7, ...], "weighted": 0.52, "dominant_dim": "中美竞争"}

    # 批量打分（推荐：编码在 analysis_service 中一次完成）
    result = score_by_prototypes_batch(texts=[...], embedder=embedder)
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

# Lazy import: config.settings 可能在某些环境下不存在（如独立脚本或测试）
try:
    from config.settings import get_bge_china_anchor_texts, get_bge_china_negative_anchor
except (ImportError, ModuleNotFoundError):
    def get_bge_china_anchor_texts():  # type: ignore[misc]
        return []
    def get_bge_china_negative_anchor():  # type: ignore[misc]
        return ""

_DIM_NAMES: list[str] = [
    "中美战略竞争",
    "中国外交与全球治理",
    "中国经济社会",
    "中国军事安全",
    "中国人权与法治",
    "中国文化科技",
]

_DIM_WEIGHTS: list[float] = [0.25, 0.20, 0.15, 0.15, 0.15, 0.10]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    an = np.linalg.norm(a)
    bn = np.linalg.norm(b)
    if an == 0 or bn == 0:
        return 0.0
    return float(np.dot(a, b) / (an * bn))


def _build_news_text(row: dict) -> str:
    parts = [
        row.get("title") or "",
        row.get("abstract") or "",
        (row.get("body") or "")[:2000],
    ]
    return "\n".join(parts).strip()


# ── 模块级缓存：原型向量和负向基线只算一次 ──────────────────────────
_prototype_vecs: Optional[List[np.ndarray]] = None
_negative_vec: Optional[np.ndarray] = None


def _get_prototype_vecs(embedder) -> List[np.ndarray]:
    global _prototype_vecs
    if _prototype_vecs is not None:
        return _prototype_vecs
    anchor_texts = get_bge_china_anchor_texts()
    _prototype_vecs = [
        embedder.encode([t])[0] for t in anchor_texts
    ]
    return _prototype_vecs


def _get_negative_vec(embedder) -> np.ndarray:
    global _negative_vec
    if _negative_vec is not None:
        return _negative_vec
    _negative_vec = embedder.encode([get_bge_china_negative_anchor()])[0]
    return _negative_vec


def clear_prototype_cache() -> None:
    """在 BGE 模型卸载后调用，清除原型/负向向量缓存。"""
    global _prototype_vecs, _negative_vec
    _prototype_vecs = None
    _negative_vec = None


def score_by_prototypes(
    embedder,
    row: Optional[dict] = None,
    *,
    text: Optional[str] = None,
    vec: Optional[np.ndarray] = None,
) -> Dict[str, object]:
    """对单条新闻计算 6 维涉华语义分。

    Args:
        embedder: BGE-M3 嵌入器实例。
        row: 新闻字典（与 text/vec 三选一）。
        text: 已拼接好的新闻文本。
        vec: 已编码好的 BGE 向量。

    Returns:
        {"scores": list[float],  # 6 维分数
         "weighted": float,      # 加权综合
         "dominant_dim": str,    # 主导涉华维度名称
         "dominant_idx": int}    # 主导维度索引
    """
    if vec is None and text is None and row is not None:
        text = _build_news_text(row)

    proto_vecs = _get_prototype_vecs(embedder)

    # 取得文章向量
    if vec is None and text is not None:
        vec = embedder.encode([text])[0]

    if vec is None:
        return {"scores": [0.0] * 6, "weighted": 0.0, "dominant_dim": "", "dominant_idx": -1}

    # 计算 6 维对比式相似度：score_i = max(0, cos(pos_i) - cos(neg))
    neg_vec = _get_negative_vec(embedder)
    baseline = _cosine_similarity(vec, neg_vec)

    scores: List[float] = []
    for pv in proto_vecs:
        s = _cosine_similarity(vec, pv)
        scores.append(max(0.0, min(1.0, s - baseline)))

    weighted = sum(s * w for s, w in zip(scores, _DIM_WEIGHTS))
    dominant_idx = int(np.argmax(scores))
    dominant_dim = _DIM_NAMES[dominant_idx] if 0 <= dominant_idx < len(_DIM_NAMES) else ""

    return {
        "scores": scores,
        "weighted": weighted,
        "dominant_dim": dominant_dim,
        "dominant_idx": dominant_idx,
    }


def score_by_prototypes_batch(
    embedder,
    rows: Optional[List[dict]] = None,
    *,
    texts: Optional[List[str]] = None,
    vecs: Optional[List[np.ndarray]] = None,
) -> List[Dict[str, object]]:
    """批量计算 6 维涉华语义分（推荐方式，减少模型加载次数）。

    Args:
        embedder: BGE-M3 嵌入器实例。
        rows: 新闻字典列表。
        texts: 已拼接文本列表。
        vecs: 已编码向量列表。

    Returns:
        每元素与 ``score_by_prototypes`` 返回值一致。
    """
    n = 0
    if rows is not None:
        n = len(rows)
        if texts is None:
            texts = [_build_news_text(r) for r in rows]
    elif texts is not None:
        n = len(texts)
    elif vecs is not None:
        n = len(vecs)

    if n == 0:
        return []

    proto_vecs = _get_prototype_vecs(embedder)
    neg_vec = _get_negative_vec(embedder)

    # 如果没传 vecs，需要编码
    if vecs is None and texts is not None:
        encoded = embedder.encode(texts)
        # embedder.encode 返回可能是 numpy array
        if isinstance(encoded, list):
            vecs = [np.asarray(v, dtype=np.float32) for v in encoded]
        else:
            vecs = [np.asarray(encoded[i], dtype=np.float32) for i in range(len(texts))]

    results: List[Dict[str, object]] = []
    for i in range(n):
        v = vecs[i] if vecs is not None else None
        if v is None:
            results.append(
                {"scores": [0.0] * 6, "weighted": 0.0, "dominant_dim": "", "dominant_idx": -1}
            )
            continue

        baseline = _cosine_similarity(v, neg_vec)
        scores: List[float] = []
        for pv in proto_vecs:
            s = _cosine_similarity(v, pv)
            scores.append(max(0.0, min(1.0, s - baseline)))

        weighted = sum(s * w for s, w in zip(scores, _DIM_WEIGHTS))
        dominant_idx = int(np.argmax(scores))
        dominant_dim = _DIM_NAMES[dominant_idx] if 0 <= dominant_idx < len(_DIM_NAMES) else ""

        results.append(
            {
                "scores": scores,
                "weighted": weighted,
                "dominant_dim": dominant_dim,
                "dominant_idx": dominant_idx,
            }
        )

    return results
