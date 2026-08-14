#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stage 1b sentiment analysis pipeline — extracted from analysis_service.py.

Supports ParlaSent (XLM-R regression) and standard HF sentiment-analysis models.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

# Stage1b 情感：未配置本地目录时使用该 Hub 模型（首次运行自动下载到 HF 缓存）
# 默认使用 FinBERT（金融领域专用，3 类），比通用 distilbert SST-2 更准确识别涉华贸易新闻情感方向。
STAGE1B_SENTIMENT_HF_DEFAULT = "classla/xlm-r-parlasent"
STAGE1B_SENTIMENT_LOCAL_LEGACY = "/models/sentiment_model"


def resolve_stage1b_sentiment_model_ref() -> str:
    """
    解析 STAGE1B_SENTIMENT_MODEL_PATH：
    - 若指向已存在的本地目录 → 使用该目录（离线）
    - 若未设置且 /models/sentiment_model 存在 → 使用该目录
    - 否则使用 Hub 模型 ID（默认 classla/xlm-r-parlasent，自动下载）
    - 若显式写了不存在的盘符路径 → 回退到 Hub 并打印说明
    """
    from pathlib import Path

    env = (os.getenv("STAGE1B_SENTIMENT_MODEL_PATH") or "").strip()
    hub = STAGE1B_SENTIMENT_HF_DEFAULT
    if env:
        p = Path(env)
        if p.is_dir() and p.exists():
            return str(p.resolve())
        # Windows/Linux 绝对路径但目录不存在
        if p.is_absolute() or (len(env) >= 2 and env[1] == ":"):
            print(
                f"[阶段1b] 本地情感路径不存在或非目录: {env!r} → 改用 HuggingFace Hub: {hub}",
                flush=True,
            )
            return hub
        # 视为 Hub 上的 model id（如 distilbert-... 或 org/name）
        return env
    legacy = Path(STAGE1B_SENTIMENT_LOCAL_LEGACY)
    if legacy.is_dir():
        return str(legacy.resolve())
    print(
        f"[阶段1b] 未检测到本地目录 {legacy}，使用 Hub 模型 {hub}（首次运行下载到 transformers 缓存）",
        flush=True,
    )
    return hub


class _ParlaSentWrapper:
    """ParlaSent (xlm-r-parlasent) regression model wrapper.

    Output format compatible with HF pipeline: list of {"label", "score"} dicts.
    Maps 0-5 regression output → POSITIVE/NEGATIVE/NEUTRAL with confidence.
    """

    def __init__(self, model_name: str = "classla/xlm-r-parlasent"):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if self.device.type == "cuda" else torch.float32,
        ).to(self.device)
        self.model.eval()
        self._batch_size = int(os.getenv("STAGE1B_SENTIMENT_BATCH_SIZE", "64"))

    def __call__(self, texts, **kwargs) -> list[dict[str, str | float]]:
        """Run inference; accepts HF pipeline kwargs (batch_size, truncation, max_length) for compat."""
        if isinstance(texts, str):
            texts = [texts]
        bs = kwargs.get("batch_size", self._batch_size)
        import torch

        all_scores: list[float] = []
        for i in range(0, len(texts), int(bs)):
            batch = texts[i : i + int(bs)]
            inputs = self.tokenizer(
                batch, truncation=True, padding=True, max_length=128, return_tensors="pt",
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.no_grad():
                scores = self.model(**inputs).logits.squeeze(-1).cpu().numpy().tolist()
            if isinstance(scores, float):
                scores = [scores]
            all_scores.extend(scores)

        # 0-5 regression → -1..1 → label + confidence
        results: list[dict[str, str | float]] = []
        for s in all_scores:
            normalized = max(-1.0, min(1.0, (s - 2.5) / 2.5))
            if normalized > 0.1:
                results.append({"label": "POSITIVE", "score": normalized})
            elif normalized < -0.1:
                results.append({"label": "NEGATIVE", "score": -normalized})
            else:
                results.append({"label": "NEUTRAL", "score": 0.5})
        return results


def _is_parlasent_model(model_ref: str) -> bool:
    return "parlasent" in model_ref.lower()


def load_stage1b_sentiment_pipeline() -> Any:
    """HuggingFace sentiment-analysis / ParlaSent；模型由 resolve_stage1b_sentiment_model_ref() 决定。

    - ParlaSent (classla/xlm-r-parlasent): XLM-R-large regression 0-5，议会政治语料微调，
      替代 FinBERT 解决"关税=利好股市"的金融偏见。用 _ParlaSentWrapper 加载。
    - 其他模型：使用标准 HF pipeline("sentiment-analysis")。
    """
    import torch
    from transformers import pipeline as hf_pipeline

    raw = resolve_stage1b_sentiment_model_ref()
    if _is_parlasent_model(raw):
        print(
            f"[阶段1b] 加载 ParlaSent 情感模型: {raw!r} "
            f"(XLM-R-large regression, device={'cuda' if torch.cuda.is_available() else 'cpu'})",
            flush=True,
        )
        return _ParlaSentWrapper(raw)

    device = 0 if torch.cuda.is_available() else -1
    print(
        f"[阶段1b] 加载情感 pipeline: model={raw!r} device={device} "
        f"(torch.cuda.is_available()={torch.cuda.is_available()})",
        flush=True,
    )
    return hf_pipeline("sentiment-analysis", model=raw, device=device)


def _hf_sentiment_label_to_score(label: str, confidence: float) -> float:
    """HF sentiment-analysis：用标签符号 × 置信度得到 [-1,1] 连续分。"""
    lab = (label or "").strip().upper()
    try:
        conf = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        conf = 0.0
    if not lab:
        return 0.0
    if "NEG" in lab:
        return -conf
    if "POS" in lab:
        return conf
    return 0.0


def run_stage1b_local_gpu_pipeline(
    records: List[Dict[str, Any]],
    extractor: Any,
    sentiment_pipe: Any,
    *,
    chunk_size: int,
) -> None:
    """
    Stage 1b：凡进入本批的涉华候选（SQL 已与 CHINA_GATE_THRESHOLD / is_china_related 对齐）
    一律跑 GLiNER + HF 情感；不再使用第二道 STAGE1B_CHINA_GATE_THRESHOLD。
    """
    from agentic_rag.analysis_service import (
        _stage1b_cuda_free_ratio,
        _stage1b_gliner_infer_batch_size,
        _stage1b_safe_ratio,
        _stage1b_sentiment_hf_batch_size,
    )

    if not records:
        return

    gliner_bs = _stage1b_gliner_infer_batch_size()
    sent_bs = _stage1b_sentiment_hf_batch_size()
    safe_ratio = _stage1b_safe_ratio()
    reserve_ratio = max(0.0, 1.0 - safe_ratio)
    try:
        max_chars = int(os.getenv("STAGE1B_TEXT_MAX_CHARS", "512"))
    except ValueError:
        max_chars = 512
    max_chars = max(256, min(max_chars, 8000))
    cs = max(1, int(chunk_size))
    for i in range(0, len(records), cs):
        chunk = records[i : i + cs]
        texts = [str(rec.get("text") or "")[:max_chars] for rec in chunk]
        free_ratio = _stage1b_cuda_free_ratio()
        gliner_bs_eff = gliner_bs
        sent_bs_eff = sent_bs
        if safe_ratio > 0 and free_ratio <= reserve_ratio:
            factor = max(0.2, free_ratio / max(reserve_ratio, 1e-6))
            gliner_bs_eff = max(1, int(gliner_bs * factor))
            sent_bs_eff = max(1, int(sent_bs * factor))
            if i == 0 or gliner_bs_eff < gliner_bs or sent_bs_eff < sent_bs:
                print(
                    f"[阶段1b] 显存空闲比 {free_ratio:.2%}，启用安全缩批 "
                    f"GLiNER {gliner_bs}->{gliner_bs_eff}, HF {sent_bs}->{sent_bs_eff}",
                    flush=True,
                )

        ent_lists = extractor.extract_batch(texts, infer_batch_size=gliner_bs_eff)
        for rec, ents in zip(chunk, ent_lists):
            rec["entities"] = ents
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        try:
            try_bs = max(1, int(sent_bs_eff))
            while True:
                try:
                    outs = sentiment_pipe(
                        texts,
                        batch_size=try_bs,
                        truncation=True,
                        max_length=512,
                    )
                    break
                except Exception as e:
                    msg = str(e).lower()
                    is_oom = "outofmemory" in msg or "out of memory" in msg or "cuda oom" in msg
                    if is_oom and try_bs > 1:
                        try:
                            import gc
                            import torch

                            gc.collect()
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception:
                            pass
                        bs2 = max(1, try_bs // 2)
                        print(
                            f"[阶段1b] 情感 OOM，batch {try_bs} -> {bs2} 重试",
                            flush=True,
                        )
                        try_bs = bs2
                        continue
                    raise
        except TypeError:
            outs = sentiment_pipe([t[:4000] for t in texts])
        except Exception as e:
            print(f"[阶段1b] 情感 batch 失败: {type(e).__name__}: {e}", flush=True)
            for rec in chunk:
                rec["sentiment"] = "PARSE_FAILED"
                rec["topic"] = "PARSE_FAILED"
                rec["frame"] = ""
                rec["sentiment_score"] = None
            continue
        if not isinstance(outs, list):
            outs = [outs]
        for rec, o in zip(chunk, outs):
            if isinstance(o, dict):
                label = str(o.get("label", ""))
                sc = float(o.get("score", 0.0))
                rec["sentiment"] = f"{label} ({sc:.3f})"
                rec["sentiment_score"] = _hf_sentiment_label_to_score(label, sc)
            else:
                rec["sentiment"] = str(o)
                rec["sentiment_score"] = None
            rec["topic"] = ""
            rec["frame"] = ""
