"""XLM-RoBERTa 涉华分类推理封装。

用法:
    from agentic_rag.china_index.xlm_model import predict_proba_batch

    probs = predict_proba_batch(["text1", "text2"])
    # -> np.ndarray of shape (N,), values in [0, 1]
"""

from __future__ import annotations

import os
import gc
from typing import Optional, List

import numpy as np

_MODEL_PATH = os.getenv(
    "XLM_ROBERTA_CHINA_PATH",
    "/root/data/globemind/score/xlm-model-v2/final",
)
_MAX_LENGTH = int(os.getenv("XLM_ROBERTA_MAX_LENGTH", "128"))

_model = None
_tokenizer = None
_device = None


def _lazy_load():
    global _model, _tokenizer, _device
    if _model is not None:
        return
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _tokenizer = AutoTokenizer.from_pretrained(_MODEL_PATH)
    _model = AutoModelForSequenceClassification.from_pretrained(
        _MODEL_PATH, torch_dtype=torch.float16 if _device.type == "cuda" else torch.float32
    ).to(_device)
    _model.eval()


def predict_proba_batch(texts: List[str]) -> np.ndarray:
    """批量预测涉华概率，返回 (N,) float32 数组，值域 [0, 1]。"""
    _lazy_load()
    import torch
    import numpy as np

    all_probs = []
    batch_size = 64
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        inputs = _tokenizer(
            batch_texts,
            truncation=True,
            padding="max_length",
            max_length=_MAX_LENGTH,
            return_tensors="pt",
        )
        inputs = {k: v.to(_device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = _model(**inputs)
        probs = torch.softmax(outputs.logits, dim=1)[:, 1].cpu().numpy()
        all_probs.append(probs)
    return np.concatenate(all_probs).astype(np.float32)


def predict_proba_single(text: str) -> float:
    """单条预测，返回 float ∈ [0, 1]。"""
    return float(predict_proba_batch([text])[0])


def unload_model() -> None:
    """卸载模型、释放 GPU 显存。"""
    global _model, _tokenizer, _device
    _model = None
    _tokenizer = None
    _device = None
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except ImportError:
        pass
