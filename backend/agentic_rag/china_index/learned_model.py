"""逻辑回归分类器：在 BGE-M3 1024 维嵌入上学习"涉华"方向。

训练数据：99,228 条人工标注 (is_china_related)，ROC-AUC ≈ 0.88。
模型以 .npz 方式存储（~4KB），无需 sklearn 即可推理。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Optional

import numpy as np

_MODEL_PATH = os.getenv(
    "CHINA_LR_MODEL_PATH",
    str(Path(__file__).resolve().with_name("china_lr_model.npz")),
)

_W: Optional[np.ndarray] = None
_b: float = 0.0


def _load_model() -> None:
    global _W, _b
    if _W is not None:
        return
    data = np.load(_MODEL_PATH)
    _W = data["W"].astype(np.float32)
    b_arr = data["b"]
    _b = float(b_arr.item() if hasattr(b_arr, "item") else b_arr)


def predict_proba(vec: np.ndarray) -> float:
    """返回 P(china|article) ∈ [0, 1] 经过 sigmoid 校准。"""
    _load_model()
    z = float(np.dot(vec, _W) + _b)
    # 防止 exp 溢出
    if z > 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def predict_proba_batch(vecs: np.ndarray) -> np.ndarray:
    """批量预测，vecs: (N, 1024) → (N,) float32。"""
    _load_model()
    z = vecs @ _W + _b
    pos_mask = z > 0
    result = np.empty_like(z)
    result[pos_mask] = 1.0 / (1.0 + np.exp(-z[pos_mask]))
    result[~pos_mask] = np.exp(z[~pos_mask]) / (1.0 + np.exp(z[~pos_mask]))
    return result


def unload_model() -> None:
    global _W, _b
    _W = None
    _b = 0.0
