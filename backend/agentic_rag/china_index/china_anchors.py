"""向量相似度涉华兜底（替代固定关键词表 + 固定阈值 0.55）。

用法：
    预计算锚点嵌入：  python -m agentic_rag.china_index.china_anchors
    管线中调用：     score = max(LR, vector_boost(article_vec))

设计思路：
    LR（逻辑回归）是主分类器，但在纯中文/短文本涉华内容上偶有漏判。
    此模块用 BGE 向量与 ~12 条精选涉华锚点文本的余弦相似度做连续兜底。

    校准方式：
      boost = clip((max_cosine - COS_MIN) / (COS_MAX - COS_MIN), 0, 1)
    即余弦相似度低于 COS_MIN 不产生任何提升，高于 COS_MAX 则赋满分。
    这避免了旧版关键词表的固定 0.55 硬阈值问题。
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np

_ANCHOR_PATH = os.path.join(os.path.dirname(__file__), "china_anchors.npz")

# 校准参数：余弦相似度 → [0, 1] 兜底分数的映射
# 低于 _COS_MIN → 0（无提升），高于 _COS_MAX → 1（满分提升）
_COS_MIN = 0.50
_COS_MAX = 0.90

# 12 条精选涉华锚点文本（中英文混合），覆盖主要涉华维度
ANCHOR_TEXTS: list[str] = [
    # 一带一路
    "一带一路倡议基础设施合作",
    "Belt and Road Initiative international cooperation projects",
    # 台湾 / 两岸
    "台湾海峡两岸关系",
    # 南海
    "South China Sea territorial disputes and maritime security",
    # 新疆 / 西藏
    "新疆西藏人权问题",
    # 香港 / 澳门
    "香港特别行政区一国两制",
    # 政治 / CCP
    "中国共产党领导政治体制",
    # 经济 / 人民币
    "人民币汇率中国经济",
    # 军事 / PLA
    "中国人民解放军军事现代化",
    # 中美科技竞争
    "China US trade war technology competition",
    # 两会 / 人大 / 政协
    "中国全国人民代表大会",
    # 涉华外交
    "中国外交部发言人新闻",
]

_anchor_embs: Optional[np.ndarray] = None


def _load_anchors() -> Optional[np.ndarray]:
    global _anchor_embs
    if _anchor_embs is not None:
        return _anchor_embs
    if not os.path.exists(_ANCHOR_PATH):
        return None
    try:
        data = np.load(_ANCHOR_PATH)
        _anchor_embs = data["embeddings"].astype(np.float32)
        return _anchor_embs
    except Exception:
        return None


def _calibrate(cos: np.ndarray) -> np.ndarray:
    """余弦相似度 → [0, 1] 线性映射 + clip。"""
    return np.clip((cos - _COS_MIN) / (_COS_MAX - _COS_MIN), 0, 1)


def vector_boost_score(article_vec: np.ndarray) -> float:
    """单条向量 → [0, 1] 兜底分数。"""
    anchors = _load_anchors()
    if anchors is None:
        return 0.0
    a_norm = np.linalg.norm(article_vec)
    if a_norm < 1e-10:
        return 0.0
    cos = float(np.dot(article_vec, anchors.T) / (a_norm * np.linalg.norm(anchors, axis=1)))
    return float(np.clip((cos - _COS_MIN) / (_COS_MAX - _COS_MIN), 0, 1))


def vector_boost_batch(vecs: np.ndarray) -> np.ndarray:
    """批量向量 → (N,) float32 [0, 1] 兜底分数。"""
    anchors = _load_anchors()
    if anchors is None:
        return np.zeros(len(vecs), dtype=np.float32)
    # 归一化内积 = 余弦相似度
    v_norms = np.linalg.norm(vecs, axis=1, keepdims=True)  # (N, 1)
    a_norms = np.linalg.norm(anchors, axis=1, keepdims=True).T  # (1, M)
    cos_mat = (vecs @ anchors.T) / (v_norms * a_norms + 1e-10)  # (N, M)
    max_cos = np.max(cos_mat, axis=1)  # (N,)
    return _calibrate(max_cos).astype(np.float32)


def compute_anchor_embeddings(
    url: str = "http://127.0.0.1:8001/v1/embeddings",
) -> np.ndarray:
    """调用 BGE API 预计算并缓存锚点嵌入。"""
    import requests

    r = requests.post(url, json={"input": ANCHOR_TEXTS}, timeout=120)
    r.raise_for_status()
    embs = np.array(
        [item["embedding"] for item in r.json()["data"]], dtype=np.float32
    )
    np.savez(_ANCHOR_PATH, embeddings=embs)
    print(f"已保存 {len(embs)} 条锚点嵌入 → {_ANCHOR_PATH}")
    return embs


if __name__ == "__main__":
    url = os.getenv("BGE_EMBED_URL", "http://127.0.0.1:8001/v1/embeddings")
    compute_anchor_embeddings(url)
