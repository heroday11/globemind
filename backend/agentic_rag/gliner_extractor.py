#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GLiNER entity extractor — extracted from analysis_service.py.

Self-contained class for multilingual NER using GLiNER multi-v2.1.
"""
from __future__ import annotations

import os
from typing import Any, List


class GLiNEREntityExtractor:
    @staticmethod
    def _resolve_gliner_map_location() -> str:
        """
        gliner 默认 from_pretrained(map_location='cpu')；有 CUDA 时应显式传入 cuda。
        环境变量：
          GLINER_MAP_LOCATION=cuda | cuda:0 | cpu（显式覆盖）
          GLINER_USE_CPU=1  强制 CPU（例如与情感模型错峰占显存）
        """
        import torch

        explicit = (os.getenv("GLINER_MAP_LOCATION") or "").strip()
        if explicit:
            loc = explicit
            if loc.startswith("cuda") and not torch.cuda.is_available():
                print(
                    f"[GLiNER] GLINER_MAP_LOCATION={loc!r} 但 CUDA 不可用，回退 cpu",
                    flush=True,
                )
                return "cpu"
            return loc
        if os.getenv("GLINER_USE_CPU", "").strip().lower() in ("1", "true", "yes", "on"):
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @staticmethod
    def _local_gliner_dir() -> str | None:
        """若 local_model_root 下已有完整快照（含 gliner_config.json），优先于 yaml 中的 Hub id。"""
        try:
            from pathlib import Path

            from config.settings import get_models_config

            root = (get_models_config().get("local_model_root") or "/models").strip()
            base = Path(root.replace("/", os.sep))
            for cand in (base / "gliner_multi-v2.1", base / "urchade" / "gliner_multi-v2.1"):
                if (cand / "gliner_config.json").is_file():
                    return str(cand)
        except Exception:
            pass
        return None

    def __init__(self, model_name: str | None = None, labels: list[str] | None = None):
        from pathlib import Path

        from gliner import GLiNER

        from config.settings import get_gliner_config

        cfg = get_gliner_config()
        cfg_hub = (cfg.get("model_name") or "").strip() or "urchade/gliner_multi-v2.1"
        if model_name:
            mn = model_name
        elif (os.getenv("GLINER_MODEL_PATH") or "").strip():
            mn = os.getenv("GLINER_MODEL_PATH", "").strip()
        else:
            local_ready = self._local_gliner_dir()
            mn = local_ready or cfg_hub
        lbs = labels if labels is not None else cfg.get("labels")
        if not lbs:
            lbs = ["person", "location", "organization", "event"]

        map_loc = self._resolve_gliner_map_location()

        def _load() -> Any:
            p = Path(mn)
            if p.is_dir() and (p / "gliner_config.json").is_file():
                return GLiNER.from_pretrained(
                    str(p),
                    local_files_only=True,
                    map_location=map_loc,
                )
            return GLiNER.from_pretrained(mn, map_location=map_loc)

        try:
            self._model = _load()
        except Exception as e:
            msg = f"{type(e).__name__}: {e}".lower()
            if any(x in msg for x in ("ssl", "eof", "https", "connection", "timeout", "max retries")):
                old = os.environ.get("HF_ENDPOINT")
                try:
                    os.environ["HF_ENDPOINT"] = "https://huggingface.co"
                    print(
                        "[GLiNER] 镜像/API 异常，改用 huggingface.co 重试加载（可预下载模型并设 GLINER_MODEL_PATH 离线）…",
                        flush=True,
                    )
                    self._model = _load()
                finally:
                    if old is None:
                        os.environ.pop("HF_ENDPOINT", None)
                    else:
                        os.environ["HF_ENDPOINT"] = old
            else:
                raise
        self._labels = list(lbs)
        try:
            dev = self._model.device
        except Exception:
            dev = map_loc
        print(f"[GLiNER] 已加载 map_location={map_loc} device={dev} model={mn!r}", flush=True)

    def unload_model(self) -> None:
        """释放 GLiNER 权重引用，便于 Phase 结束后 gc / empty_cache。"""
        self._model = None

    def extract(self, text: str) -> List[str]:
        if self._model is None:
            return []
        preds = self._model.predict_entities(text, self._labels)
        entities: List[str] = []
        for p in preds:
            val = (p.get("text") or p.get("entity") or "") if isinstance(p, dict) else str(p)
            val = val.strip()
            if val:
                entities.append(val)
        return list(dict.fromkeys(entities))[:80]

    @staticmethod
    def _entities_from_preds(preds: List[Any]) -> List[str]:
        entities: List[str] = []
        for p in preds:
            val = (p.get("text") or p.get("entity") or "") if isinstance(p, dict) else str(p)
            val = val.strip()
            if val:
                entities.append(val)
        return list(dict.fromkeys(entities))[:80]

    def extract_batch(self, texts: List[str], *, infer_batch_size: int = 32) -> List[List[str]]:
        """
        批量 GLiNER：底层走 model.inference（DataLoader batch），充分利用 CUDA。
        infer_batch_size：传入 GLiNER inference 的 batch_size（DataLoader）。
        """
        if self._model is None:
            return [[] for _ in texts]
        if not texts:
            return []
        try:
            thr = float(os.getenv("GLINER_INFERENCE_THRESHOLD", "0.5"))
        except ValueError:
            thr = 0.5
        bs = max(1, int(infer_batch_size))
        raw = None
        while True:
            try:
                raw = self._model.inference(
                    texts,
                    self._labels,
                    flat_ner=True,
                    threshold=thr,
                    multi_label=False,
                    batch_size=bs,
                )
                break
            except Exception as e:
                msg = str(e).lower()
                is_oom = "outofmemory" in msg or "out of memory" in msg or "cuda oom" in msg
                if is_oom:
                    try:
                        import gc
                        import torch

                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception:
                        pass
                    if bs > 1:
                        bs2 = max(1, bs // 2)
                        print(
                            f"[GLiNER] OOM，inference batch {bs} -> {bs2} 重试",
                            flush=True,
                        )
                        bs = bs2
                        continue
                print(
                    f"[GLiNER] batch inference 失败，回退逐条: {type(e).__name__}: {e}",
                    flush=True,
                )
                out: List[List[str]] = []
                for t in texts:
                    try:
                        out.append(self.extract(t))
                    except Exception as e2:
                        print(f"[GLiNER Error] {type(e2).__name__}: {e2}", flush=True)
                        out.append([])
                return out
        if len(raw) != len(texts):
            print(
                f"[GLiNER] batch 返回条数 {len(raw)} != 输入 {len(texts)}，回退逐条",
                flush=True,
            )
            return [self.extract(t) for t in texts]
        return [self._entities_from_preds(preds) for preds in raw]
