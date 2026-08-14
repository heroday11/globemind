#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import socket
import time
from concurrent.futures import Future, ThreadPoolExecutor
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import numpy as np


def _bootstrap_dotenv_before_embedder() -> None:
    """先于 embedder 子模块加载 .env，使 HF_HUB_OFFLINE 等对 HF_ENDPOINT 默认值生效。"""
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        here = Path(__file__).resolve().parent
        root = here.parent
        for p in (here / ".env", root / ".env"):
            if p.is_file():
                load_dotenv(p, override=False)
    except ImportError:
        pass


_bootstrap_dotenv_before_embedder()

from agentic_rag.db.executor import SafePGExecutor
from agentic_rag.db.security import PGSecurityConfig
from agentic_rag.ingestion.embedder import get_embedder
from agentic_rag.naming_service import _is_safety_filter_error, _local_vllm_retry_enabled

# BAAI/bge-m3 句向量维度；用于 Milvus 同步批在「尚未加载 SentenceTransformer」时预分配矩阵（避免 get_embedder().dim 触发整模加载）
BGE_M3_SENTENCE_DIM = int(os.getenv("BGE_M3_SENTENCE_DIM", "1024"))


def _infer_milvus_batch_embedding_dim(to_sync: List[Dict[str, Any]]) -> int:
    """优先从本批已有 bge_embedding 推断维度，否则用默认维数（不加载 PyTorch 模型）。"""
    for r in to_sync:
        be = r.get("bge_embedding")
        if be is not None:
            return int(np.asarray(be).shape[-1])
    return BGE_M3_SENTENCE_DIM


def _default_llm_workers(backend: str, llm_batch_size: int) -> int:
    """默认并发：云端走保守值避免 429；本地保留较小并发。"""
    if backend == "cloud_api":
        target = int(os.getenv("LLM_WORKERS_CLOUD", "8"))
    else:
        target = int(os.getenv("LLM_WORKERS_LOCAL", "4"))
    return max(1, min(int(llm_batch_size), target))


def _json_loads_vector(s: str) -> Any:
    """灾难恢复 / 大批量 JSONB：优先 orjson。"""
    try:
        import orjson

        return orjson.loads(s)
    except ImportError:
        return json.loads(s)
    except Exception:
        return json.loads(s)


def _deserialize_bge_embedding_from_db(val: Any) -> Optional[np.ndarray]:
    """将 PG 中 JSONB / list / str / bytes 等转为 float32 一维向量；无法解析则 None（走本地 encode）。"""
    if val is None:
        return None
    try:
        if isinstance(val, np.ndarray):
            arr = np.asarray(val, dtype=np.float32).reshape(-1)
            return arr if arr.size > 0 else None
        if isinstance(val, (list, tuple)):
            arr = np.asarray(val, dtype=np.float32).reshape(-1)
            return arr if arr.size > 0 else None
        if isinstance(val, memoryview):
            return np.frombuffer(val, dtype=np.float32).copy()
        if isinstance(val, (bytes, bytearray)):
            return np.frombuffer(bytes(val), dtype=np.float32).copy()
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            try:
                parsed = _json_loads_vector(s)
            except json.JSONDecodeError:
                try:
                    import ast

                    parsed = ast.literal_eval(s)
                except (SyntaxError, ValueError, TypeError):
                    return None
            arr = np.asarray(parsed, dtype=np.float32).reshape(-1)
            return arr if arr.size > 0 else None
    except Exception:
        return None
    return None


def _bge_embedding_to_pg_json(r: Dict[str, Any], Json: Any) -> Optional[Any]:
    """写回 ``news_embeddings.bge_embedding``（JSONB）。"""
    be = r.get("bge_embedding")
    if be is None:
        return None
    if isinstance(be, np.ndarray):
        return Json(be.astype(np.float32).tolist())
    if isinstance(be, (list, tuple)):
        return Json([float(x) for x in be])
    try:
        return Json(np.asarray(be, dtype=np.float32).tolist())
    except Exception:
        return None


def ensure_dotenv_loaded() -> None:
    """加载 agentic_rag/.env 与项目根 .env（不覆盖已在 shell 里设置的变量）。"""
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        here = Path(__file__).resolve().parent
        root = here.parent
        for path in (here / ".env", root / ".env"):
            if path.is_file():
                load_dotenv(path, override=False)
    except ImportError:
        pass


# 闸门：仅 china_related_index > 此值的记录进入 LLM/API；并与 is_china_related 布尔一致
try:
    from config.settings import FrozenDefaults

    _CHINA_GATE_DEFAULT = str(FrozenDefaults.CHINA_GATE_THRESHOLD)
except Exception:
    _CHINA_GATE_DEFAULT = "0.40"
CHINA_GATE_THRESHOLD = float(os.getenv("CHINA_GATE_THRESHOLD", _CHINA_GATE_DEFAULT))
PARSE_FAILED = "PARSE_FAILED"
# 未过闸：不写 NULL，否则 WHERE sentiment IS NULL 会导致同批新闻被反复拉取
SKIPPED_BELOW_GATE_SENTIMENT = ""
SKIPPED_BELOW_GATE_TOPIC = ""

# 历史兼容：旧版 Stage1b 曾用独立闸值写 ignored_low_relevance；现已与 CHINA_GATE_THRESHOLD 对齐，不再因闸值跳过 GLiNER/情感。
IGNORED_LOW_RELEVANCE_SENTIMENT = "ignored_low_relevance"
IGNORED_LOW_RELEVANCE_TOPIC = "ignored_low_relevance"

# sync_china_news_to_milvus：仅首次打印「复用 1a 向量」说明，避免每批刷屏
_MILVUS_REUSE_EMBEDDING_HINT_PRINTED = False

_DEFAULT_SENTIMENT_TOPIC_PROMPT = (
    "请分析以下新闻的涉华报道倾向。Response must be a valid JSON object.\n"
    "仅输出一个 JSON 对象，包含三个字符串字段：\n"
    "1) sentiment：正面、负面或中立之一\n"
    "2) topic：2到4个汉字的核心主题\n"
    "3) frame：从以下选项中选择最匹配的一个框架类别（只填类别名）：\n"
    "   中国威胁论、经济合作、军事冲突、人权批评、科技竞争、外交互动、国内治理、中立报道\n"
    "不要 Markdown 代码块，不要其它说明文字。\n"
    "新闻：{text}"
)

FRAME_CLASSES = [
    "中国威胁论", "经济合作", "军事冲突", "人权批评",
    "科技竞争", "外交互动", "国内治理", "中立报道",
]


def _llm_sentiment_topic_prompt(text: str) -> str:
    """情感/主题 JSON 提示词：优先 config/ontology_and_trust.yaml llm_prompts.sentiment_topic。"""
    try:
        from config.settings import get_llm_prompts

        tmpl = get_llm_prompts().get("sentiment_topic")
        if tmpl and "{text}" in tmpl:
            return tmpl.format(text=text)
        if tmpl:
            return f"{tmpl.rstrip()}\n新闻：{text}"
    except Exception:
        pass
    return _DEFAULT_SENTIMENT_TOPIC_PROMPT.format(text=text)

LLMBackend = Literal["vllm", "cloud_api"]


def _resolve_llm_backend() -> LLMBackend:
    raw = (os.getenv("LLM_BACKEND") or "cloud_api").strip().lower()
    if raw == "vllm":
        return "vllm"
    if raw in ("cloud", "cloud_api", "api"):
        return "cloud_api"
    return "cloud_api"


def _print_runtime_resource_hints(backend: LLMBackend) -> None:
    print(f"[Runtime] LLM_BACKEND={backend} (env LLM_BACKEND={os.getenv('LLM_BACKEND', '')!r})")
    if backend == "vllm":
        print(f"[Runtime] vLLM base_url={os.getenv('VLLM_BASE_URL', 'http://localhost:8000')}")
        print(f"[Runtime] vLLM model={_vllm_model_name()} (env VLLM_MODEL overrides ontology models.micro_analysis_model)")
    else:
        print(f"[Runtime] CLOUD_API_BASE_URL={os.getenv('CLOUD_API_BASE_URL', '')}")
        print(f"[Runtime] CLOUD_API_MODEL={os.getenv('CLOUD_API_MODEL', '')}")
        if _local_vllm_retry_enabled():
            print(
                f"[Runtime] 云端内容安全拦截时将回退本地 vLLM: "
                f"url={_cloud_fallback_vllm_endpoint()} model={_cloud_fallback_vllm_model()} "
                f"(QWEN_LOCAL_FALLBACK_* / VLLM_*)",
                flush=True,
            )


def _pg_read() -> SafePGExecutor:
    return SafePGExecutor(PGSecurityConfig(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_USER", "news_reader"),
        password=os.environ["PG_PASSWORD"],  # 必须通过环境变量设置
        max_rows=100_000,
        force_limit=False,
    ))


def _pg_write() -> SafePGExecutor:
    return SafePGExecutor(PGSecurityConfig(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_WRITE_USER", "postgres"),
        password=os.environ["PG_WRITE_PASSWORD"],  # 必须通过环境变量设置
        max_rows=100_000,
        force_limit=False,
    ))


def _unprocessed_where_clause() -> str:
    """微观分析未完成：无 news_analysis 行，或关键字段仍为 NULL（与旧 news 列语义一致）。"""
    from agentic_rag.db.news_analysis_schema import sql_unprocessed_where

    return sql_unprocessed_where("n", "na")


def _count_unprocessed_rows(ex_read: SafePGExecutor) -> int:
    from agentic_rag.db.news_analysis_schema import sql_join_news_analysis
    from agentic_rag.pipeline.sim_time_window import sim_pub_time_and

    sql = (
        "SELECT COUNT(*) AS cnt FROM news n "
        f"{sql_join_news_analysis()} "
        f"WHERE {_unprocessed_where_clause()}"
        f"{sim_pub_time_and('n')}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Count failed: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        return 0
    v = rows[0].get("cnt")
    return int(v) if v is not None else 0


def _preflight_log(
    *,
    backend: LLMBackend,
    unprocessed_db: int,
    max_rows: Optional[int],
    batch_size: int,
    gate_threshold: float,
    workers: int,
    llm_batch_size: int,
) -> None:
    plan_total = unprocessed_db if max_rows is None else min(int(max_rows), int(unprocessed_db))
    pass_rate = float(os.getenv("ESTIMATED_GATE_PASS_RATE", "0.08"))
    est_pass = max(1, int(round(plan_total * pass_rate))) if plan_total > 0 else 0

    if backend == "cloud_api":
        engine_line = f"[Pre-flight] 当前 LLM 引擎: CLOUD_API (Model: {os.getenv('CLOUD_API_MODEL', '') or '(unset CLOUD_API_MODEL)'})"
    else:
        engine_line = f"[Pre-flight] 当前 LLM 引擎: VLLM (Model: {_vllm_model_name()})"

    # 调优口径：5 万条全量约 15–20 分钟 BGE；过闸约 8% 时阶段③ 约 10–15 分钟（旧全量口径）
    ref_rows = 50_000.0
    scale = (plan_total / ref_rows) if plan_total else 0.0
    stage2_lo, stage2_hi = 15.0 * scale, 20.0 * scale
    ref_pass = 4000.0
    pass_scale = (est_pass / ref_pass) if est_pass else 0.0
    stage3_lo, stage3_hi = 10.0 * pass_scale, 15.0 * pass_scale
    # 小批量时线性比例会 <1 分钟，:0f 会变成 0；设下限并支持「秒」展示
    floor_min = 1.0 / 60.0  # 至少 1 秒量级
    stage2_lo = max(floor_min, stage2_lo)
    stage2_hi = max(floor_min * 2, stage2_hi)
    stage3_lo = max(floor_min, stage3_lo)
    stage3_hi = max(floor_min * 2, stage3_hi)
    est_total_lo = stage2_lo + stage3_lo
    est_total_hi = stage2_hi + stage3_hi

    def _fmt_est_mins(lo: float, hi: float) -> str:
        if hi < 1.0:
            return f"约 {lo * 60:.0f}–{hi * 60:.0f} 秒"
        if hi < 5.0:
            return f"约 {lo:.1f}–{hi:.1f} 分钟"
        return f"约 {lo:.0f}–{hi:.0f} 分钟"

    print(f"[Pre-flight] 准备处理新闻总数: {plan_total:,} 条（库中待处理 {unprocessed_db:,} 条，max_rows={max_rows}）")
    print(engine_line)
    print(f"[Pre-flight] 拦截阈值: china_related_index >= {gate_threshold:.2f}（过闸才做 GLiNER + LLM/API）")
    print(
        f"[Pre-flight] 预估过闸约 {est_pass:,} 条（按通过率 {pass_rate:.0%} 粗算，实际以分数分布为准）"
    )
    print(
        f"[Pre-flight] 预估阶段② 全量 BGE-M3（无 GLiNER）: {_fmt_est_mins(stage2_lo, stage2_hi)}"
        f"（按 {int(ref_rows):,} 条≈15–20 分钟线性折算；机器/GPU 不同会有偏差）"
    )
    print(
        f"[Pre-flight] 预估阶段③ 过闸 GLiNER ∥ 云端 API: {_fmt_est_mins(stage3_lo, stage3_hi)}"
        f"（按约 {int(ref_pass):,} 条过闸≈10–15 分钟线性折算）"
    )
    print(
        f"[Pre-flight] 预估合计（②+③ 核心算力）: {_fmt_est_mins(est_total_lo, est_total_hi)}"
    )
    print(
        f"[Pre-flight] 并发: batch_size={batch_size}, llm_batch_size={llm_batch_size}, workers={workers}, "
        f"GLINER_MAX_CONCURRENT={os.getenv('GLINER_MAX_CONCURRENT', '8')}"
    )
    print(
        "[Pre-flight] 阶段说明: ①拉取 → ②全量 BGE 打分（entities 置空）→ "
        "③仅过闸：GLiNER 与 API 异步并行 → ④写回"
    )


def _vllm_endpoint() -> str:
    return os.getenv("VLLM_BASE_URL", "http://localhost:8000").rstrip("/") + "/v1/chat/completions"


def _vllm_model_name() -> str:
    env = (os.getenv("VLLM_MODEL") or "").strip()
    if env:
        return env
    try:
        from config.settings import get_models_config

        p = (get_models_config().get("micro_analysis_model") or "").strip()
        if p:
            return p.replace("\\", "/")
    except Exception:
        pass
    return "/models/Qwen2.5-1.5B-Instruct"


def _cloud_fallback_vllm_endpoint() -> str:
    """与 naming_service 回退一致：优先 QWEN_LOCAL_FALLBACK_BASE_URL（可含 /v1），否则 VLLM_BASE_URL。"""
    raw = (
        (os.getenv("QWEN_LOCAL_FALLBACK_BASE_URL") or "").strip()
        or (os.getenv("VLLM_BASE_URL") or "http://127.0.0.1:8000").strip()
    ).rstrip("/")
    if not raw:
        raw = "http://127.0.0.1:8000"
    if raw.endswith("/v1"):
        return raw + "/chat/completions"
    return raw + "/v1/chat/completions"


def _cloud_fallback_vllm_model() -> str:
    """优先 QWEN_LOCAL_FALLBACK_MODEL，须与 vLLM --served-model-name 一致（Docker 常为 /model/...）。"""
    m = (os.getenv("QWEN_LOCAL_FALLBACK_MODEL") or os.getenv("VLLM_MODEL") or "").strip()
    if m:
        return m.replace("\\", "/")
    try:
        from config.settings import get_models_config

        p = (get_models_config().get("micro_analysis_model") or "").strip()
        if p:
            return p.replace("\\", "/")
    except Exception:
        pass
    return "/models/Qwen2.5-1.5B-Instruct"


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    an = np.linalg.norm(a)
    bn = np.linalg.norm(b)
    if an == 0 or bn == 0:
        return 0.0
    return float(np.dot(a, b) / (an * bn))


def _build_news_text(row: dict) -> str:
    return f"{row.get('title') or ''}\n{row.get('abstract') or ''}\n{(row.get('body') or '')[:2000]}".strip()


def _fetch_unprocessed_rows(ex_read, batch_size: int) -> List[dict]:
    from agentic_rag.db.news_analysis_schema import sql_join_news_analysis
    from agentic_rag.pipeline.sim_time_window import sim_fetch_order_by, sim_pub_time_and

    sql = (
        "SELECT n.id, n.title, n.abstract, n.body, n.url FROM news n "
        f"{sql_join_news_analysis()} "
        f"WHERE {_unprocessed_where_clause()}"
        f"{sim_pub_time_and('n')} "
        f"{sim_fetch_order_by()} "
        f"LIMIT {int(batch_size)}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Read failed: {res.get('error')}")
    return res.get("rows", [])


from agentic_rag.gliner_extractor import GLiNEREntityExtractor as _GLiNEREntityExtractor
GLiNEREntityExtractor = _GLiNEREntityExtractor


def _stage1b_gliner_infer_batch_size() -> int:
    try:
        return max(1, int(os.getenv("STAGE1B_GLINER_INFER_BATCH_SIZE", "16")))
    except ValueError:
        return 16


def _stage1b_sentiment_hf_batch_size() -> int:
    try:
        return max(1, int(os.getenv("STAGE1B_SENTIMENT_BATCH_SIZE", "16")))
    except ValueError:
        return 16


def _stage1b_cuda_free_ratio() -> float:
    """返回当前 CUDA 空闲占比（0~1）；不可用时返回 1。"""
    try:
        import torch

        if not torch.cuda.is_available():
            return 1.0
        free_b, total_b = torch.cuda.mem_get_info()
        if total_b <= 0:
            return 1.0
        return max(0.0, min(1.0, float(free_b) / float(total_b)))
    except Exception:
        return 1.0


def _stage1b_safe_ratio() -> float:
    """目标占用上限，默认 0.8（预留约 20% 显存余量）；<=0 表示关闭自动缩批。"""
    try:
        raw = os.getenv("STAGE1B_GPU_SAFE_RATIO", "").strip()
        if raw:
            v = float(raw)
        else:
            v = float(os.getenv("GPU_VRAM_SAFE_RATIO", "0.8"))
    except ValueError:
        v = 0.8
    if v <= 0:
        return 0.0
    return max(0.5, min(0.95, v))


_BGE_VRAM_GUARD_INIT = False


def _init_bge_vram_guard_once() -> None:
    """
    Windows/WDDM 下可选显存上限，尽量避免静默 swap 到系统内存。
    默认开启，优先 BGE_CUDA_MEM_FRACTION，否则使用 GPU_VRAM_SAFE_RATIO（默认 0.8）。
    """
    global _BGE_VRAM_GUARD_INIT
    if _BGE_VRAM_GUARD_INIT:
        return
    _BGE_VRAM_GUARD_INIT = True
    try:
        import torch

        if not torch.cuda.is_available():
            return
        frac_raw = os.getenv("BGE_CUDA_MEM_FRACTION", "").strip()
        if frac_raw:
            frac = float(frac_raw)
        else:
            frac = _stage1b_safe_ratio()
        if frac <= 0:
            return
        total_gb_cfg = os.getenv("GPU_VRAM_TOTAL_GB", "").strip()
        if total_gb_cfg:
            try:
                total_gb_limit = float(total_gb_cfg)
                props = torch.cuda.get_device_properties(0)
                total_gb_real = float(props.total_memory) / (1024 ** 3)
                if total_gb_limit > 0 and total_gb_real > 0:
                    frac = min(frac, (total_gb_limit * max(0.01, frac)) / total_gb_real)
            except Exception:
                pass
        frac = max(0.5, min(0.98, frac))
        torch.cuda.set_per_process_memory_fraction(frac)
        print(f"[AdaptiveBatch] set_per_process_memory_fraction={frac:.2f}", flush=True)
    except Exception:
        # 不影响主流程：部分驱动/版本不支持该接口
        pass


def init_cuda_memory_guard_once() -> None:
    """对外入口：应用 CUDA 进程显存预算（若支持）。"""
    _init_bge_vram_guard_once()


def adaptive_encode(
    model: Any,
    texts: List[str],
    *,
    batch_size: int,
    show_progress_bar: bool = False,
) -> np.ndarray:
    """
    BGE 自适应编码：OOM 时递归二分，确保尽可能以当前可承受最大批量运行。
    """
    if not texts:
        dim = int(getattr(model, "dim", 1024))
        return np.zeros((0, dim), dtype=np.float32)
    # Stage 1a 吞吐保护：编码前再做硬截断兜底（字符上限）。
    try:
        max_chars = int(os.getenv("BGE_TEXT_MAX_CHARS", "1024"))
    except ValueError:
        max_chars = 1024
    max_chars = max(128, min(max_chars, 20000))
    texts = [str(t or "")[:max_chars] for t in texts]
    bs = max(1, min(int(batch_size), len(texts)))
    try:
        vecs = model.encode(texts, batch_size=bs, show_progress_bar=show_progress_bar)
        return np.asarray(vecs, dtype=np.float32)
    except Exception as e:
        msg = str(e).lower()
        is_oom = (
            "outofmemory" in msg
            or "out of memory" in msg
            or "cuda oom" in msg
            or e.__class__.__name__ == "OutOfMemoryError"
        )
        if not is_oom:
            raise
        print(
            f"[AdaptiveBatch] OOM caught on batch of size {len(texts)}. "
            "Halving batch and retrying...",
            flush=True,
        )
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        if len(texts) <= 1:
            raise
        mid = len(texts) // 2
        left = adaptive_encode(
            model,
            texts[:mid],
            batch_size=max(1, bs // 2),
            show_progress_bar=False,
        )
        right = adaptive_encode(
            model,
            texts[mid:],
            batch_size=max(1, bs // 2),
            show_progress_bar=False,
        )
        return np.vstack([left, right])


def _bge_encode_texts_chunked(embedder: Any, texts: List[str], *, show_progress: bool = True) -> np.ndarray:
    """仅编码新闻文本列表；分块 + tqdm（与 profile_china_index_sample 思路一致）。"""
    if not texts:
        dim = int(getattr(embedder, "dim", 1024))
        return np.zeros((0, dim), dtype=np.float32)

    encode_bs = max(1, int(os.getenv("BGE_ENCODE_BATCH_SIZE", "24")))
    progress_step = max(1, int(os.getenv("BGE_PROGRESS_STEP", "8")))
    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    if tqdm is None or not show_progress:
        return adaptive_encode(
            embedder,
            texts,
            batch_size=encode_bs,
            show_progress_bar=False,
        )

    parts: List[np.ndarray] = []
    pbar = tqdm(
        total=len(texts),
        desc="② BGE-M3 打分",
        unit="条",
        dynamic_ncols=True,
        mininterval=0.3,
        leave=False,
    )
    for i in range(0, len(texts), progress_step):
        chunk = texts[i:i + progress_step]
        bs = max(1, min(encode_bs, len(chunk)))
        vecs_c = adaptive_encode(
            embedder,
            chunk,
            batch_size=bs,
            show_progress_bar=False,
        )
        parts.append(vecs_c)
        pbar.update(len(chunk))
        pbar.set_postfix(batch=f"{i // progress_step + 1}", refresh=False)
    pbar.close()
    return np.vstack(parts)


def _compute_china_index_only(
    embedder,
    rows: List[dict],
    *,
    show_bge_progress: bool = True,
) -> List[Dict[str, Any]]:
    """阶段②：BGE-M3 → 逻辑回归涉华分类 + BGE向量化（用于 Milvus）；GLiNER 推迟至阶段③。

    涉华评分管线：
      1. BGE-M3 编码文本（同时用于 Milvus 和 LR 分类）
      2. 逻辑回归（在 1024-d BGE 向量上预测，~93K 条/s）
      3. 向量相似度兜底（用 BGE 向量与涉华锚点文本的余弦相似度做连续兜底）
    """
    texts = [_build_news_text(r) for r in rows]
    vecs = None

    # BGE-M3 编码（用于 Milvus 向量库 + LR 涉华分类）
    if embedder is not None:
        _init_bge_vram_guard_once()
        if os.getenv("BGE_CHUNKED_ENCODE", "1").strip().lower() in ("1", "true", "yes"):
            vecs = _bge_encode_texts_chunked(embedder, texts, show_progress=show_bge_progress)
        else:
            vecs = adaptive_encode(
                embedder,
                texts,
                batch_size=max(1, int(os.getenv("BGE_ENCODE_BATCH_SIZE", "24"))),
                show_progress_bar=False,
            )

    # BGE 向量 → 逻辑回归涉华分类（替代旧版 XLM-RoBERTa）
    from agentic_rag.china_index.learned_model import predict_proba_batch as lr_predict

    lr_probas: Optional[np.ndarray] = None
    if vecs is not None:
        try:
            lr_probas = lr_predict(vecs)
        except Exception as e:
            print(f"[LR] prediction error: {e}")

    # BGE 向量相似度兜底（替代固定关键词表，连续分数更灵活）
    from agentic_rag.china_index.china_anchors import vector_boost_batch

    vec_boosts: Optional[np.ndarray] = None
    if vecs is not None:
        try:
            vec_boosts = vector_boost_batch(vecs)
        except Exception as e:
            print(f"[vector boost] error: {e}")

    out: List[Dict[str, Any]] = []
    for i, (row, text) in enumerate(zip(rows, texts)):
        # ── LR 涉华概率 ──────────────────────────
        if lr_probas is not None:
            score = float(lr_probas[i])
        else:
            score = 0.0

        # ── 向量相似度兜底 ──────────────────────
        if vec_boosts is not None:
            score = max(score, float(vec_boosts[i]))

        gated = score >= CHINA_GATE_THRESHOLD
        rec: Dict[str, Any] = {
            "id": int(row["id"]),
            "title": row.get("title") or "",
            "text": text,
            "url": row.get("url"),
            "is_china_related": bool(gated),
            "china_related_index": max(0.0, min(1.0, score)),
            "prototype_scores": None,
            "lexicon_matches": {},
            "entities": [],
            "sentiment": None,
            "topic": None,
            "sentiment_score": None,
        }
        if vecs is not None:
            rec["bge_embedding"] = np.asarray(vecs[i], dtype=np.float32)
        else:
            rec["bge_embedding"] = None
        out.append(rec)
    return out


def _parse_llm_json(raw: str) -> Optional[Dict[str, str]]:
    """解析情感/主题 JSON；优先整段 json.loads（配合云端 JSON 模式）。"""
    if not raw:
        return None
    text = raw.strip()
    data = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        dec = json.JSONDecoder()
        for m in re.finditer(r"\{", text):
            try:
                cand, _ = dec.raw_decode(text[m.start() :])
                if isinstance(cand, dict):
                    data = cand
                    break
            except Exception:
                continue
    if not isinstance(data, dict):
        return None
    sentiment = data.get("sentiment") or data.get("情感") or data.get("sentiment_analysis")
    topic = data.get("topic") or data.get("主题") or data.get("topic_classification")
    frame = data.get("frame") or data.get("框架") or data.get("frame_classification")
    if not isinstance(sentiment, str) or not sentiment.strip() or not isinstance(topic, str) or not topic.strip():
        return None
    if not isinstance(frame, str) or not frame.strip() or frame.strip() not in FRAME_CLASSES:
        frame = "中立报道"
    return {"sentiment": sentiment.strip(), "topic": topic.strip(), "frame": frame.strip()}


def _cloud_json_mode_enabled() -> bool:
    """云端 OpenAI 兼容接口默认启用 json_object（Qwen-Plus 等），减少 PARSE_FAILED。设 CLOUD_API_JSON_MODE=0 可关闭。"""
    return os.getenv("CLOUD_API_JSON_MODE", "1").strip().lower() not in ("0", "false", "no")


def _safe_console_text(val: Any) -> str:
    txt = "" if val is None else str(val)
    try:
        txt.encode("gbk")
        return txt
    except Exception:
        return txt.encode("gbk", errors="ignore").decode("gbk", errors="ignore")


def _call_vllm_once(prompt: str, timeout_s: float) -> str:
    payload = {
        "model": _vllm_model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 96,
    }
    req = urllib.request.Request(
        _vllm_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        parsed = json.loads(resp.read().decode("utf-8", errors="ignore"))
    try:
        from agentic_rag.llm_usage import accumulate_from_usage_dict

        accumulate_from_usage_dict(parsed.get("usage"))
    except Exception:
        pass
    return parsed.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def _call_vllm_fallback_once(prompt: str, timeout_s: float) -> str:
    """云端 DashScope 等内容安全拦截后，走本地 Docker / 内网 vLLM（与 QWEN_LOCAL_FALLBACK_* 对齐）。"""
    payload = {
        "model": _cloud_fallback_vllm_model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 96,
    }
    req = urllib.request.Request(
        _cloud_fallback_vllm_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        parsed = json.loads(resp.read().decode("utf-8", errors="ignore"))
    try:
        from agentic_rag.llm_usage import accumulate_from_usage_dict

        accumulate_from_usage_dict(parsed.get("usage"))
    except Exception:
        pass
    return parsed.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


async def _call_vllm_with_retry(prompt: str, timeout_s: float, max_retries: int = 4) -> str:
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.to_thread(_call_vllm_once, prompt, timeout_s)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt >= max_retries:
                raise RuntimeError(f"vLLM HTTPError {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, socket.timeout) as e:
            if attempt >= max_retries:
                raise RuntimeError(f"vLLM connection error: {e}") from e
        backoff = min(8.0, 0.5 * (2 ** attempt))
        print(f"[LLM Retry] attempt={attempt + 1}, sleep={backoff:.1f}s")
        await asyncio.sleep(backoff)
    return ""


def _cloud_model_name() -> str:
    return os.getenv("CLOUD_API_MODEL", "gpt-4o-mini")


def _make_async_openai_client():  # noqa: ANN201
    from openai import AsyncOpenAI
    base = (os.getenv("CLOUD_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    key = os.getenv("CLOUD_API_KEY") or ""
    return AsyncOpenAI(base_url=base, api_key=key)


async def _call_cloud_with_retry(client: Any, prompt: str, timeout_s: float, max_retries: int = 4) -> str:
    model = _cloud_model_name()
    for attempt in range(max_retries + 1):
        try:
            kwargs: Dict[str, Any] = dict(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=96,
                timeout=timeout_s,
            )
            if _cloud_json_mode_enabled():
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
            try:
                from agentic_rag.llm_usage import accumulate_from_chat_completion

                accumulate_from_chat_completion(resp)
            except Exception:
                pass
            ch = resp.choices[0].message.content if resp.choices else None
            return (ch or "").strip()
        except Exception as e:
            if _local_vllm_retry_enabled() and _is_safety_filter_error(e):
                try:
                    print(
                        f"[Cloud→vLLM] 内容安全拦截，改用本地: model={_cloud_fallback_vllm_model()}",
                        flush=True,
                    )
                    return await asyncio.to_thread(_call_vllm_fallback_once, prompt, timeout_s)
                except Exception as e_fb:
                    raise RuntimeError(
                        f"Cloud API safety filter; local vLLM fallback failed: {e_fb}"
                    ) from e
            name = type(e).__name__
            msg = str(e).lower()
            retryable = (
                "429" in msg
                or "rate" in msg
                or "timeout" in msg
                or "connection" in msg
                or "503" in msg
                or "502" in msg
                or "524" in msg
            )
            if attempt >= max_retries or not retryable:
                raise RuntimeError(f"Cloud API error: {name}: {e}") from e
            backoff = min(16.0, 0.5 * (2 ** attempt))
            print(f"[Cloud LLM Retry] attempt={attempt + 1}, sleep={backoff:.1f}s ({name})")
            await asyncio.sleep(backoff)
    return ""


def _passes_llm_gate(rec: Dict[str, Any]) -> bool:
    """与 SQL `china_related_index >= CHINA_GATE_THRESHOLD` 对齐。"""
    return float(rec.get("china_related_index") or 0.0) >= CHINA_GATE_THRESHOLD


def _apply_skip_below_gate(records: List[Dict[str, Any]]) -> None:
    for rec in records:
        if not _passes_llm_gate(rec):
            rec["sentiment"] = SKIPPED_BELOW_GATE_SENTIMENT
            rec["topic"] = SKIPPED_BELOW_GATE_TOPIC
            rec["frame"] = ""
            rec["entities"] = []


async def _run_llm_pipeline_async(
    records: List[Dict[str, Any]],
    backend: LLMBackend,
    llm_batch_size: int,
    workers: int,
    timeout_budget: Optional[float],
    extractor: Optional[GLiNEREntityExtractor] = None,
    *,
    quiet_chunk_bar: bool = False,
    chunk_bar_desc: str = "③ GLiNER+LLM",
) -> None:
    to_run = [r for r in records if _passes_llm_gate(r)]
    _apply_skip_below_gate(records)
    if not to_run:
        print(f"[LLM] skip all {len(records)} (none passed gate >= {CHINA_GATE_THRESHOLD})")
        return

    if extractor is None:
        extractor = GLiNEREntityExtractor()

    start_ts = time.time()
    sem = asyncio.Semaphore(max(1, workers))
    gliner_sem = asyncio.Semaphore(max(1, int(os.getenv("GLINER_MAX_CONCURRENT", "8"))))
    vllm_timeout_s = float(os.getenv("VLLM_REQUEST_TIMEOUT_SECONDS", "300"))
    cloud_timeout_s = float(os.getenv("CLOUD_API_TIMEOUT_SECONDS", "120"))

    cloud_client = None
    if backend == "cloud_api":
        cloud_client = _make_async_openai_client()

    async def infer_one(rec: Dict[str, Any]) -> None:
        # 全局向量 + 深度分析分流：仅对涉华新闻跑 GLiNER+情感主题 LLM（预算集中在涉华）
        if rec.get("is_china_related") is False:
            rec.setdefault("entities", [])
            rec.setdefault("sentiment", SKIPPED_BELOW_GATE_SENTIMENT)
            rec.setdefault("topic", SKIPPED_BELOW_GATE_TOPIC)
            rec.setdefault("frame", "")
            return

        prompt = _llm_sentiment_topic_prompt((rec["text"] or "")[:1200])

        async def run_gliner() -> None:
            try:
                async with gliner_sem:
                    ents = await asyncio.to_thread(extractor.extract, rec["text"] or "")
                rec["entities"] = ents
            except Exception as e:
                rec["entities"] = []
                print(f"[GLiNER Error] {_safe_console_text(rec['title'])}: {type(e).__name__}: {e}")

        async def run_llm() -> None:
            try:
                async with sem:
                    if backend == "vllm":
                        raw = await _call_vllm_with_retry(prompt, vllm_timeout_s)
                    else:
                        raw = await _call_cloud_with_retry(cloud_client, prompt, cloud_timeout_s)
                parsed = _parse_llm_json(raw)
                if parsed is None:
                    rec["sentiment"] = PARSE_FAILED
                    rec["topic"] = PARSE_FAILED
                    rec["frame"] = ""
                    print(f"[LLM Parse Error] {_safe_console_text(rec['title'])}")
                else:
                    rec["sentiment"] = parsed["sentiment"]
                    rec["topic"] = parsed["topic"]
                    rec["frame"] = parsed.get("frame", "中立报道")
            except Exception as e:
                rec["sentiment"] = PARSE_FAILED
                rec["topic"] = PARSE_FAILED
                rec["frame"] = ""
                print(f"[LLM Error] {_safe_console_text(rec['title'])}: {type(e).__name__}: {e}")

        await asyncio.gather(run_gliner(), run_llm())

    chunk_n = max(1, (len(to_run) + llm_batch_size - 1) // llm_batch_size)
    try:
        from tqdm import tqdm as _tqdm_llm
    except ImportError:
        _tqdm_llm = None

    _llm_line = (
        f"[LLM+GLiNER] {backend}: 本批新闻={len(records)}, 过闸={len(to_run)}, "
        f"子批={chunk_n}（GLiNER ∥ API） workers={workers}"
    )
    if _tqdm_llm is not None and not quiet_chunk_bar:
        _tqdm_llm.write(_llm_line)
        chunk_iter = _tqdm_llm(
            range(0, len(to_run), llm_batch_size),
            total=chunk_n,
            desc=f"{chunk_bar_desc}+云端LLM",
            unit="子批",
            file=sys.stderr,
            dynamic_ncols=True,
            leave=False,
            mininterval=0.3,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )
    elif _tqdm_llm is not None and quiet_chunk_bar:
        try:
            _tqdm_llm.write(_llm_line, file=sys.stdout)
        except TypeError:
            # 旧版 tqdm.write 可能无 file= 参数
            print(_llm_line, file=sys.stdout, flush=True)
        chunk_iter = range(0, len(to_run), llm_batch_size)
    else:
        print(
            f"[LLM+GLiNER] {backend}: batch={len(records)}, gate_pass={len(to_run)} "
            f"(GLiNER ∥ API), llm_batch_size={llm_batch_size}, workers={workers}"
        )
        chunk_iter = range(0, len(to_run), llm_batch_size)

    for i in chunk_iter:
        chunk = to_run[i : i + llm_batch_size]
        if timeout_budget is not None and (time.time() - start_ts) >= timeout_budget:
            for r in chunk:
                r["sentiment"] = PARSE_FAILED
                r["topic"] = PARSE_FAILED
            print("[LLM Error] timeout reached, remaining gate-pass rows marked as PARSE_FAILED")
            continue
        t_chunk = time.perf_counter()
        await asyncio.gather(*[infer_one(r) for r in chunk])
        if _tqdm_llm is None:
            chunk_idx = i // llm_batch_size + 1
            print(
                f"[LLM+GLiNER] 子批 {chunk_idx}/{chunk_n} 完成，本段耗时 {time.perf_counter() - t_chunk:.1f}s"
            )

    if cloud_client is not None:
        aclose = getattr(cloud_client, "close", None)
        if callable(aclose):
            ret = aclose()
            if asyncio.iscoroutine(ret):
                await ret

    if extractor is not None:
        try:
            extractor.unload_model()
        except Exception:
            pass


def _run_llm_pipeline(
    records: List[Dict[str, Any]],
    backend: LLMBackend,
    llm_batch_size: int,
    workers: int,
    timeout_budget: Optional[float],
    extractor: Optional[GLiNEREntityExtractor] = None,
    *,
    quiet_chunk_bar: bool = False,
    chunk_bar_desc: str = "③ GLiNER+LLM",
) -> None:
    asyncio.run(
        _run_llm_pipeline_async(
            records,
            backend,
            llm_batch_size,
            workers,
            timeout_budget,
            extractor,
            quiet_chunk_bar=quiet_chunk_bar,
            chunk_bar_desc=chunk_bar_desc,
        )
    )


# Stage 1b sentiment pipeline: extracted to stage1b_sentiment.py
from agentic_rag.stage1b_sentiment import (
    STAGE1B_SENTIMENT_HF_DEFAULT,
    STAGE1B_SENTIMENT_LOCAL_LEGACY,
    resolve_stage1b_sentiment_model_ref,
    load_stage1b_sentiment_pipeline,
    run_stage1b_local_gpu_pipeline,
)


def _milvus_sync_enabled() -> bool:
    return os.getenv("MILVUS_SYNC", "1").strip().lower() not in ("0", "false", "no")


def _milvus_sync_during_micro_enabled() -> bool:
    """默认关闭：微观 1a/legacy run_analysis 只写 PG，Milvus 交给 --stage 44。"""
    return os.getenv("MILVUS_SYNC_DURING_MICRO", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _milvus_sync_async_enabled() -> bool:
    """True：Milvus/Router 在后台线程执行，主线程可重叠下一批 BGE（单 worker 串行，避免与 BGE 抢 GPU）。"""
    if not _milvus_sync_enabled():
        return False
    return os.getenv("MILVUS_SYNC_ASYNC", "0").strip().lower() in ("1", "true", "yes", "on")


def _pipeline_v2_dual_write() -> bool:
    """流水线 v2：1a 写回 PG 后同批直写 Milvus（向量仍在内存）。"""
    return os.getenv("PIPELINE_V2_DUAL_WRITE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _pipeline_v2_milvus_sync_all() -> bool:
    """与 --milvus-sync-all 对齐：双写时同步非涉华向量。"""
    return os.getenv("PIPELINE_V2_MILVUS_SYNC_ALL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _milvus_sync_after_micro_write(sync_milvus: bool) -> bool:
    if not sync_milvus or not _milvus_sync_enabled():
        return False
    return _milvus_sync_during_micro_enabled() or _pipeline_v2_dual_write()


_milvus_async_executor: ThreadPoolExecutor | None = None
_milvus_async_futures: list[Future] = []
_milvus_async_notice_shown = False

# 微观写回（Dual-write / MILVUS_SYNC_DURING_MICRO）：累积到较大块再路由，避免每小批 route + flush。
_milvus_route_buffer: List[Dict[str, Any]] = []
_milvus_route_buffer_only_china: Optional[bool] = None
_milvus_micro_route_buffer_notice_shown = False
_milvus_micro_stage_flush_needed = False


def _milvus_micro_route_buffer_size() -> int:
    """与 MILVUS_SYNC_CHUNK 对齐；微观路径在内存中凑满该条数再送入 incremental_router。"""
    try:
        v = int(os.getenv("MILVUS_SYNC_CHUNK", "1000"))
    except ValueError:
        v = 1000
    return max(48, min(v, 10_000))


def _records_have_bge_for_milvus_sync(records: List[Dict[str, Any]], only_china: bool) -> bool:
    """only_china=True：仅检查涉华非影子行；False：凡将写入 Milvus 的非影子行均需向量。"""
    for r in records:
        if r.get("duplicate_of"):
            continue
        if only_china and not r.get("is_china_related"):
            continue
        if r.get("bge_embedding") is None:
            return False
    return True


def _records_have_bge_for_china_milvus(records: List[Dict[str, Any]]) -> bool:
    """后台线程不应再调 get_embedder()；涉华行需已有 bge_embedding（影子稿不参与 Milvus）。"""
    return _records_have_bge_for_milvus_sync(records, True)


def _copy_records_for_milvus_async(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in records:
        d = dict(r)
        be = r.get("bge_embedding")
        if be is not None:
            d["bge_embedding"] = np.asarray(be, dtype=np.float32).copy()
        out.append(d)
    return out


def _milvus_async_worker_job(
    records: List[Dict[str, Any]],
    *,
    only_china: bool = True,
) -> None:
    # 循环内不显式 flush；由阶段末 drain 统一 flush_all。
    sync_china_news_to_milvus(
        records,
        only_china=only_china,
        defer_store_flush=True,
    )


def _submit_milvus_async_worker_only(
    records: List[Dict[str, Any]],
    *,
    only_china: bool = True,
) -> None:
    """提交单块后台 Milvus 任务（已由上层按 MILVUS_SYNC_CHUNK 拆好）。"""
    global _milvus_async_executor, _milvus_async_futures, _milvus_async_notice_shown
    if _milvus_async_executor is None:
        _milvus_async_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="milvus_sync",
        )
    if not _milvus_async_notice_shown:
        print(
            "[MilvusSync] MILVUS_SYNC_ASYNC=1：Milvus/Router 在后台单线程执行，"
            "与下一批 BGE 重叠；阶段结束前会等待全部完成。",
            flush=True,
        )
        _milvus_async_notice_shown = True
    payload = _copy_records_for_milvus_async(records)
    fut = _milvus_async_executor.submit(
        _milvus_async_worker_job,
        payload,
        only_china=only_china,
    )
    _milvus_async_futures.append(fut)


def _dispatch_milvus_route_chunk(
    records: List[Dict[str, Any]],
    *,
    only_china: bool,
) -> None:
    """将一条已凑满的微观路由分块送往异步 worker 或同步 route（均 defer flush）。"""
    global _milvus_micro_stage_flush_needed
    if not records:
        return
    _milvus_micro_stage_flush_needed = True
    if _milvus_sync_async_enabled() and _records_have_bge_for_milvus_sync(
        records,
        only_china,
    ):
        _submit_milvus_async_worker_only(records, only_china=only_china)
    else:
        if _milvus_sync_async_enabled() and not _records_have_bge_for_milvus_sync(
            records,
            only_china,
        ):
            print(
                "[MilvusSync] MILVUS_SYNC_ASYNC=1 但本缓冲块缺 bge_embedding，本块改为同步路由",
                flush=True,
            )
        sync_china_news_to_milvus(
            records,
            only_china=only_china,
            defer_store_flush=True,
        )


def _enqueue_milvus_route_after_micro(
    records: List[Dict[str, Any]],
    *,
    only_china: bool = True,
) -> None:
    """微观写回入口：先写入内存缓冲，达到 MILVUS_SYNC_CHUNK 再路由（增大 gRPC / router 批）。"""
    global _milvus_route_buffer, _milvus_route_buffer_only_china, _milvus_micro_route_buffer_notice_shown
    if not _milvus_micro_route_buffer_notice_shown:
        print(
            f"[MilvusSync] 微观写回缓冲 MILVUS_SYNC_CHUNK={_milvus_micro_route_buffer_size()} "
            "条后再路由；阶段末 drain 时统一 flush（非每小批 flush）。",
            flush=True,
        )
        _milvus_micro_route_buffer_notice_shown = True
    copied = _copy_records_for_milvus_async(records)
    if (
        _milvus_route_buffer_only_china is not None
        and _milvus_route_buffer_only_china != only_china
    ):
        if _milvus_route_buffer:
            oc = _milvus_route_buffer_only_china
            part = list(_milvus_route_buffer)
            _milvus_route_buffer.clear()
            _dispatch_milvus_route_chunk(part, only_china=bool(oc))
    _milvus_route_buffer.extend(copied)
    _milvus_route_buffer_only_china = only_china

    chunk = _milvus_micro_route_buffer_size()
    while len(_milvus_route_buffer) >= chunk:
        part = _milvus_route_buffer[:chunk]
        del _milvus_route_buffer[:chunk]
        _dispatch_milvus_route_chunk(part, only_china=only_china)


def drain_milvus_async_workers() -> None:
    """刷出微观路由缓冲、等待异步 Milvus 任务，并在阶段末统一 flush Milvus。"""
    global _milvus_async_executor, _milvus_async_futures, _milvus_async_notice_shown
    global _milvus_route_buffer, _milvus_route_buffer_only_china, _milvus_micro_route_buffer_notice_shown
    global _milvus_micro_stage_flush_needed

    if _milvus_route_buffer and _milvus_route_buffer_only_china is not None:
        part = list(_milvus_route_buffer)
        oc = _milvus_route_buffer_only_china
        _milvus_route_buffer.clear()
        _milvus_route_buffer_only_china = None
        _dispatch_milvus_route_chunk(part, only_china=oc)

    if _milvus_async_futures:
        for fut in _milvus_async_futures:
            fut.result()
        _milvus_async_futures.clear()
    if _milvus_async_executor is not None:
        _milvus_async_executor.shutdown(wait=True)
        _milvus_async_executor = None
    _milvus_async_notice_shown = False
    _milvus_micro_route_buffer_notice_shown = False

    if _milvus_micro_stage_flush_needed and _milvus_sync_enabled():
        try:
            from agentic_rag.db.milvus_store import get_milvus_store

            get_milvus_store().flush_all()
        except Exception as e:
            print(f"[MilvusSync] 阶段末 flush_all: {type(e).__name__}: {e}", flush=True)
        _milvus_micro_stage_flush_needed = False


def _pub_time_to_ts(val: Any) -> int:
    if val is None:
        return int(time.time())
    try:
        if hasattr(val, "timestamp"):
            return int(val.timestamp())
        s = str(val).strip().replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return int(time.time())


def _fetch_pub_times_for_ids(ids: List[int]) -> Dict[int, int]:
    if not ids:
        return {}
    ex = _pg_read()
    ids_str = ",".join(str(int(i)) for i in ids)
    sql = f"SELECT id, pub_time FROM news WHERE id IN ({ids_str})"
    res = ex.query(sql)
    if not res.get("ok"):
        return {}
    out: Dict[int, int] = {}
    for row in res.get("rows") or []:
        out[int(row["id"])] = _pub_time_to_ts(row.get("pub_time"))
    return out


def sync_china_news_to_milvus(
    records: List[Dict[str, Any]],
    *,
    only_china: bool = True,
    defer_store_flush: bool = False,
) -> None:
    """阶段 4.5：将本批新闻向量化并写入 Milvus（幂等跳过已存在 news_id）。

    only_china=True（默认）：仅处理 `is_china_related` 为真的记录（与微观分析写回一致）。
    only_china=False：处理本批全部记录（供「全库有标题新闻」同步图谱）。
    """
    if not _milvus_sync_enabled():
        return
    from agentic_rag.pipeline.incremental_router import route_news_batch

    if only_china:
        batch = [
            r
            for r in records
            if r.get("is_china_related") and not r.get("duplicate_of")
        ]
    else:
        batch = [
            r
            for r in records
            if r.get("id") is not None and not r.get("duplicate_of")
        ]
    if not batch:
        return
    ids = [int(r["id"]) for r in batch]
    try:
        from agentic_rag.db.milvus_store import get_milvus_store

        store = get_milvus_store()
        new_ids_order = store.filter_new_news_ids(ids)
    except Exception as e:
        print(f"[MilvusSync] 连接/过滤失败，跳过: {e}")
        return
    if not new_ids_order:
        print(
            f"[MilvusSync] 本批 {len(batch)} 条候选均已存在于 Milvus，无需写入。",
            flush=True,
        )
        return
    r_by_id = {int(r["id"]): r for r in batch}
    to_sync = [r_by_id[nid] for nid in new_ids_order if nid in r_by_id]
    if not to_sync:
        return

    n = len(to_sync)
    missed = [i for i, r in enumerate(to_sync) if r.get("bge_embedding") is None]
    if missed:
        print(
            f"[MilvusSync] 待写入 Milvus：{n} 条（已从候选中过滤掉已在库内的 id）→ "
            f"其中 {len(missed)} 条需 BGE 编码…",
            flush=True,
        )
    else:
        print(
            f"[MilvusSync] 待写入 Milvus：{n} 条（已从候选中过滤掉已在库内的 id）→ 复用已有向量…",
            flush=True,
        )
    dim = _infer_milvus_batch_embedding_dim(to_sync)
    embeddings = np.zeros((n, dim), dtype=np.float32)
    for i, r in enumerate(to_sync):
        be = r.get("bge_embedding")
        if be is not None:
            embeddings[i] = np.asarray(be, dtype=np.float32)
    if missed:
        embedder = get_embedder()
        texts_m = [to_sync[i].get("text") or "" for i in missed]
        _init_bge_vram_guard_once()
        enc = adaptive_encode(
            embedder,
            texts_m,
            batch_size=max(1, int(os.getenv("BGE_ENCODE_BATCH_SIZE", "24"))),
            show_progress_bar=(len(texts_m) > 32),
        )
        for j, idx in enumerate(missed):
            embeddings[idx] = enc[j]
    elif n > 0:
        global _MILVUS_REUSE_EMBEDDING_HINT_PRINTED
        if not _MILVUS_REUSE_EMBEDDING_HINT_PRINTED:
            _MILVUS_REUSE_EMBEDDING_HINT_PRINTED = True
            print(
                "[MilvusSync] 提示: 后续各批若复用阶段①a 已写入的 bge_embedding，则不再跑 BGE；"
                "路由/写 Milvus 以 CPU 与 gRPC 为主，任务管理器里 GPU 可能长期接近 0%（属正常）。",
                flush=True,
            )

    titles = [r.get("title") or "" for r in to_sync]
    ts_map = _fetch_pub_times_for_ids(new_ids_order)
    timestamps = [ts_map.get(int(r["id"]), int(time.time())) for r in to_sync]

    label = "涉华候选" if only_china else "全库候选"
    print(
        f"[MilvusSync] {label} {len(batch)} 条 / 将向量化 {n} 条 → incremental_router（大批量时路由可能需数分钟，见 [Router] 进度）",
        flush=True,
    )
    try:
        route_news_batch(
            new_ids_order,
            embeddings,
            titles=titles,
            timestamps=timestamps,
            defer_store_flush=defer_store_flush,
        )
    except Exception as e:
        print(f"[MilvusSync] route_news_batch 失败: {e}")


def _milvus_sync_china_only_from_env() -> bool:
    """仅同步涉华 vs 全库标题：环境变量优先，否则 config.yaml pipeline_defaults.milvus_sync_china_only。"""
    v = os.getenv("MILVUS_SYNC_CHINA_ONLY")
    if v is not None and str(v).strip() != "":
        return str(v).strip().lower() not in ("0", "false", "no", "off")
    try:
        from config.settings import FrozenDefaults

        return bool(getattr(FrozenDefaults, "MILVUS_SYNC_CHINA_ONLY", True))
    except Exception:
        return True


def sync_china_news_from_db(
    limit: int = 2000,
    *,
    china_only: Optional[bool] = None,
) -> int:
    """从数据库拉取新闻并同步 Milvus（无需 pkl；供 CLI 阶段 4.5 使用）。

    china_only=True：仅 `is_china_related IS TRUE`（默认，或与 MILVUS_SYNC_CHINA_ONLY=1）。
    china_only=False：`title` 非空的全部新闻（用于知识图谱包含全库向量）。
    """
    ensure_dotenv_loaded()
    try:
        from agentic_rag.db.news_analysis_schema import ensure_news_analysis_table

        ensure_news_analysis_table()
    except Exception as e:
        print(f"[MilvusSync] news_analysis / news_embeddings 表确保: {type(e).__name__}: {e}")
    if not _milvus_sync_enabled():
        print("[MilvusSync] MILVUS_SYNC=0，跳过")
        return 0
    if china_only is None:
        china_only = _milvus_sync_china_only_from_env()
    ex = _pg_read()
    lim = max(1, min(int(limit), 500_000))
    from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA
    from agentic_rag.db.news_analysis_schema import sql_join_news_embeddings

    from agentic_rag.pipeline.sim_time_window import sim_pub_time_and

    sim_sql = sim_pub_time_and("n")
    _ne = sql_join_news_embeddings("n", "ne", "LEFT")
    if china_only:
        sql = (
            f"SELECT n.id, n.title, n.abstract, n.pub_time, ne.bge_embedding FROM news n "
            f"INNER JOIN {_NA} na ON na.news_id = n.id "
            f"{_ne} "
            f"WHERE na.is_china_related IS TRUE "
            f"AND na.duplicate_of IS NULL "
            f"AND n.title IS NOT NULL AND n.title != '' "
            f"{sim_sql}"
            f"ORDER BY n.id DESC LIMIT {lim}"
        )
    else:
        sql = (
            f"SELECT n.id, n.title, n.abstract, n.pub_time, na.is_china_related, ne.bge_embedding FROM news n "
            f"LEFT JOIN {_NA} na ON na.news_id = n.id "
            f"{_ne} "
            f"WHERE n.title IS NOT NULL AND n.title != '' "
            f"AND (na.duplicate_of IS NULL) "
            f"{sim_sql}"
            f"ORDER BY n.id DESC LIMIT {lim}"
        )
    res = ex.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"MilvusSync 读库失败: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        if china_only:
            print("[MilvusSync] 无 is_china_related=TRUE 且标题非空的新闻")
        else:
            print("[MilvusSync] 无标题非空的新闻")
        return 0
    if china_only:
        print(
            f"[MilvusSync] 数据库候选 {len(rows)} 条（仅涉华 is_china_related=TRUE，按 id DESC，最多 {lim} 条）。"
            "若需图谱包含全部有标题新闻，请使用 --milvus-sync-all 或 MILVUS_SYNC_CHINA_ONLY=0。",
            flush=True,
        )
    else:
        print(
            f"[MilvusSync] 数据库候选 {len(rows)} 条（有标题即纳入，不限涉华；按 id DESC，最多 {lim} 条）。",
            flush=True,
        )
    records: List[Dict[str, Any]] = []
    for row in rows:
        t = row.get("title") or ""
        ab = row.get("abstract") or ""
        ic = row.get("is_china_related")
        if china_only:
            ic_rel = True
        else:
            ic_rel = bool(ic) if ic is not None else False
        records.append({
            "id": int(row["id"]),
            "title": t,
            "text": f"{t} {ab}".strip(),
            "is_china_related": ic_rel,
            "bge_embedding": _deserialize_bge_embedding_from_db(row.get("bge_embedding")),
        })
    chunk = max(1, min(int(os.getenv("MILVUS_SYNC_CHUNK", "1000")), 10_000))
    n_total = len(records)
    for off in range(0, n_total, chunk):
        part = records[off : off + chunk]
        last = off + chunk >= n_total
        sync_china_news_to_milvus(
            part,
            only_china=china_only,
            defer_store_flush=not last,
        )
    if n_total > chunk:
        print(
            f"[MilvusSync] 已按 MILVUS_SYNC_CHUNK={chunk} 分块路由，末块后统一 flush",
            flush=True,
        )
    return n_total


def _write_china_score_to_ai_analysis(records: List[Dict[str, Any]]) -> None:
    """将管线产出的涉华分数（BGE+LR+keyword）写入 news_ai_analysis（双库）。"""
    from agentic_rag.db.connection import get_conn

    scores = []
    for r in records:
        news_id = int(r["id"])
        xlmr = float(r.get("china_related_index") or 0.0)
        scores.append((xlmr, news_id))

    if not scores:
        return

    sql = (
        "UPDATE news_ai_analysis SET "
        "china_relevance_score = COALESCE(china_relevance_score, %s), "
        "analyzed_at = now() "
        "WHERE news_id = %s"
    )

    import psycopg2.extras

    for dbname in ("globemind", "globemind_news"):
        try:
            conn = get_conn(dbname, autocommit=False, connect_timeout=10)
            try:
                cur = conn.cursor()
                psycopg2.extras.execute_batch(cur, sql, scores, page_size=500)
                conn.commit()
                cur.close()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as e:
            print(f"  [XLM-R→{dbname}] 跳过: {e}")


def compute_llm_relevance_combined_score(
    prototype_weighted: float | None,
    llm_score: int | None,
    *,
    prototype_weight: float = 0.65,
    llm_weight: float = 0.35,
) -> float:
    """加权融合 prototype_weighted 与 LLM 评分，输出 0-1 浮点值。

    三层评分体系：lexicon(0.25) + prototype(0.40) + LLM(0.35)。
    此处给出 prototype + LLM 的二次加权（等价于 0.65/0.35 归一化）。
    当 LLM 缺失时回退 prototype；当两者均缺失时返回 0。
    """
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


def _call_vllm_china_relevance(title: str, abstract: str, timeout_s: float = 15) -> int | None:
    """调用 vLLM 获取中国相关性评分（1-10），与 backfill_llm_china_relevance.py 共享同一 prompt。"""
    prompt = (
        "On a scale of 1 to 10, how relevant is the following news article to China?\n"
        "1 = completely unrelated to China\n"
        "10 = entirely about China and Chinese interests\n\n"
        "Consider: mentions of Chinese government, companies, citizens, territories "
        "(including Taiwan, South China Sea), policies, or issues framed as affecting China.\n\n"
        'Respond ONLY with a JSON object: {{"relevance_score": <1-10>, "reasoning": "<one sentence>"}}\n\n'
        f"Title: {(title or '')[:300]}\n"
        f"Abstract: {(abstract or '')[:500]}"
    )
    try:
        raw = _call_vllm_once(prompt, timeout_s=timeout_s)
    except Exception as e:
        print(f"  [LLM] 相关性调用失败: {e}", flush=True)
        return None
    if not raw:
        return None
    import re
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
            except Exception:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    score = data.get("relevance_score") or data.get("score") or data.get("relevance")
    try:
        score = int(score)
    except (TypeError, ValueError):
        return None
    return max(1, min(10, score))


def run_llm_relevance_batch(
    records: list[dict],
    *,
    batch_size: int = 50,
    max_workers: int = 4,
) -> int:
    """对一批记录执行 LLM 涉华相关性评分并写回 china_relevance_score。

    每条记录需含 'id'、'title'、'abstract'。
    使用 compute_llm_relevance_combined_score 融合 prototype_weighted。
    返回成功更新的条数。

    此函数作为 ``--stage llm-relevance`` 的轻量内联替代，
    在已有 ``prototype_weighted`` 的场景下调用的增量评分。
    """
    if not records:
        return 0

    from agentic_rag.db.connection import get_conn
    import psycopg2.extras

    updated = 0
    batch_params = []

    for rec in records:
        news_id = int(rec["id"])
        baseline = float(rec.get("prototype_weighted") or rec.get("china_related_index") or 0.0)
        llm_score = _call_vllm_china_relevance(
            rec.get("title") or "",
            rec.get("abstract") or "",
        )
        combined = compute_llm_relevance_combined_score(baseline, llm_score)
        batch_params.append((combined, news_id))
        updated += 1

        if len(batch_params) >= batch_size:
            for dbname in ("globemind", "globemind_news"):
                try:
                    conn = get_conn(dbname, autocommit=False, connect_timeout=10)
                    try:
                        cur = conn.cursor()
                        psycopg2.extras.execute_batch(
                            cur,
                            "UPDATE news_ai_analysis SET china_relevance_score = %s, "
                            "china_index_version = 'v2_llm' WHERE news_id = %s",
                            batch_params,
                            page_size=500,
                        )
                        conn.commit()
                        cur.close()
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        conn.close()
                except Exception as e:
                    print(f"  [LLM→{dbname}] 跳过: {e}", flush=True)
            batch_params = []

    # 余批
    if batch_params:
        for dbname in ("globemind", "globemind_news"):
            try:
                conn = get_conn(dbname, autocommit=False, connect_timeout=10)
                try:
                    cur = conn.cursor()
                    psycopg2.extras.execute_batch(
                        cur,
                        "UPDATE news_ai_analysis SET china_relevance_score = %s, "
                        "china_index_version = 'v2_llm' WHERE news_id = %s",
                        batch_params,
                        page_size=500,
                    )
                    conn.commit()
                    cur.close()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as e:
                print(f"  [LLM→{dbname}] 跳过: {e}", flush=True)

    return updated


def _write_back_batch(
    ex_write,
    records: List[Dict[str, Any]],
    *,
    sync_milvus: bool = True,
) -> None:
    import psycopg2.extras
    from psycopg2.extras import Json

    from agentic_rag.db.news_analysis_schema import EMBEDDINGS_TABLE_NAME as _NE
    from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA
    from agentic_rag.source_credibility import credibility_from_url

    def _cred_for_row(r: Dict[str, Any]) -> float:
        v = r.get("source_credibility")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        return credibility_from_url(r.get("url"))

    conn = ex_write.get_write_conn()
    try:
        cur = conn.cursor()
        sql_na = (
            f"INSERT INTO {_NA} (news_id, is_china_related, china_related_index, entities, "
            f"sentiment_analysis, topic_classification, frame_classification, duplicate_of, "
            f"dedupe_method, source_credibility, sentiment_score, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            f"ON CONFLICT (news_id) DO UPDATE SET "
            f"is_china_related = EXCLUDED.is_china_related, "
            f"china_related_index = EXCLUDED.china_related_index, "
            f"entities = EXCLUDED.entities, "
            f"sentiment_analysis = EXCLUDED.sentiment_analysis, "
            f"topic_classification = EXCLUDED.topic_classification, "
            f"frame_classification = EXCLUDED.frame_classification, "
            f"duplicate_of = COALESCE(EXCLUDED.duplicate_of, {_NA}.duplicate_of), "
            f"dedupe_method = COALESCE(EXCLUDED.dedupe_method, {_NA}.dedupe_method), "
            f"source_credibility = COALESCE(EXCLUDED.source_credibility, {_NA}.source_credibility), "
            f"sentiment_score = COALESCE(EXCLUDED.sentiment_score, {_NA}.sentiment_score), "
            f"updated_at = now()"
        )
        params_na = [
            (
                int(r["id"]),
                r["is_china_related"],
                float(r["china_related_index"]),
                Json(r.get("entities") or []),
                r.get("sentiment"),
                r.get("topic"),
                r.get("frame"),
                r.get("duplicate_of"),
                r.get("dedupe_method"),
                _cred_for_row(r),
                r.get("sentiment_score"),
            )
            for r in records
        ]
        psycopg2.extras.execute_batch(cur, sql_na, params_na, page_size=500)

        emb_params = [
            (int(r["id"]), _bge_embedding_to_pg_json(r, Json))
            for r in records
            if r.get("bge_embedding") is not None
        ]
        if emb_params:
            sql_ne = (
                f"INSERT INTO {_NE} (news_id, bge_embedding) VALUES (%s, %s) "
                f"ON CONFLICT (news_id) DO UPDATE SET "
                f"bge_embedding = COALESCE(EXCLUDED.bge_embedding, {_NE}.bge_embedding)"
            )
            psycopg2.extras.execute_batch(cur, sql_ne, emb_params, page_size=500)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # 将涉华分数（BGE+LR）同步写入 news_ai_analysis（供 API 直接读取）
    try:
        _write_china_score_to_ai_analysis(records)
    except Exception as e:
        print(f"[AI Analysis] 涉华分数写入 news_ai_analysis 失败: {e}")

    if _milvus_sync_after_micro_write(sync_milvus):
        only_china = not _pipeline_v2_milvus_sync_all()
        _enqueue_milvus_route_after_micro(records, only_china=only_china)


def run_analysis(
    batch_size: int = 64,
    llm_batch_size: Optional[int] = None,
    workers: Optional[int] = None,
    max_batches: Optional[int] = None,
    max_rows: Optional[int] = None,
    max_seconds: Optional[float] = None,
    per_chunk_timeout_seconds: Optional[float] = None,
    micro_total_cap: Optional[int] = None,
):
    ensure_dotenv_loaded()
    from agentic_rag.analysis_quiet import apply_run_analysis_warning_filters

    apply_run_analysis_warning_filters()
    try:
        from agentic_rag.db.news_analysis_schema import ensure_news_analysis_table

        ensure_news_analysis_table()
    except Exception as e:
        print(f"[Schema] news_analysis 表确保跳过: {type(e).__name__}: {e}")
    try:
        from agentic_rag.db.macro_schema import ensure_intel_persistence_columns

        ensure_intel_persistence_columns()
    except Exception as e:
        print(f"[Schema] Stage2 启动时情报列补齐跳过: {type(e).__name__}: {e}")
    backend = _resolve_llm_backend()
    if llm_batch_size is None:
        llm_batch_size = 32 if backend == "cloud_api" else 32
    if workers is None:
        workers = _default_llm_workers(backend, llm_batch_size)

    _print_runtime_resource_hints(backend)

    ex_read = _pg_read()
    ex_write = _pg_write()

    unprocessed = _count_unprocessed_rows(ex_read)
    from agentic_rag.pipeline import micro_budget as _micro_budget

    run_row_limit, cap_skip = _micro_budget.effective_row_limit(max_rows, micro_total_cap)
    if run_row_limit is not None and run_row_limit <= 0:
        print(
            f"[run_analysis] {cap_skip or '本段条数上限为 0'}；跳过"
            f"（状态文件: {_micro_budget.budget_state_path()}）",
            flush=True,
        )
        return
    if micro_total_cap is not None:
        print(
            f"[run_analysis] micro_total_cap={micro_total_cap} 已累计 {_micro_budget.load_consumed():,}，"
            f"本段上限 {run_row_limit if run_row_limit is not None else '∞'} 条（与单次 max_rows 取较小值）",
            flush=True,
        )
    _preflight_log(
        backend=backend,
        unprocessed_db=unprocessed,
        max_rows=run_row_limit,
        batch_size=batch_size,
        gate_threshold=CHINA_GATE_THRESHOLD,
        workers=workers,
        llm_batch_size=llm_batch_size,
    )

    embedder = None
    try:
        embedder = get_embedder()
    except Exception as e:
        print(f"[Embedder] disabled due to load failure: {type(e).__name__}: {e}")

    from agentic_rag.pipeline_file_logging import init_detail_session_log, log_detail
    from agentic_rag.pipeline_logging import log_pipeline

    init_detail_session_log("run_analysis")
    log_pipeline(
        f"run_analysis START backend={backend} batch_size={batch_size} max_rows={max_rows} "
        f"run_row_limit={run_row_limit} micro_total_cap={micro_total_cap} "
        f"max_batches={max_batches} max_seconds={max_seconds}"
    )
    log_detail(
        f"run_analysis START backend={backend} batch_size={batch_size} max_rows={max_rows} "
        f"run_row_limit={run_row_limit} micro_total_cap={micro_total_cap} "
        f"max_batches={max_batches} max_seconds={max_seconds} unprocessed_db={unprocessed}"
    )
    total = 0
    batches = 0
    start_ts = time.time()

    try:
        from tqdm import tqdm as _tqdm_main
    except ImportError:
        _tqdm_main = None

    planned_total = (
        min(unprocessed, run_row_limit) if run_row_limit is not None else unprocessed
    )
    main_pbar = None
    if _tqdm_main is not None and planned_total > 0:
        main_pbar = _tqdm_main(
            total=planned_total,
            desc="微观分析·总进度(可断点续跑)",
            unit="条",
            file=sys.stderr,
            dynamic_ncols=True,
            mininterval=0.5,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        )

    def _log(msg: str) -> None:
        """主进度条在 stderr，阶段日志走 stdout，减轻与 Milvus 等 print 抢同一行。"""
        if main_pbar is not None:
            try:
                _tqdm_main.write(msg, file=sys.stdout)
            except TypeError:
                print(msg, file=sys.stdout, flush=True)
        else:
            print(msg, file=sys.stdout, flush=True)

    show_bge_inner = main_pbar is None
    shared_gliner_extractor: Optional[GLiNEREntityExtractor] = None

    try:
        while True:
            if max_batches is not None and batches >= max_batches:
                print(f"[Pipeline] stop by max_batches={max_batches}")
                break
            if run_row_limit is not None and total >= run_row_limit:
                print(f"[Pipeline] stop by run_row_limit={run_row_limit} (max_rows={max_rows})")
                break
            if max_seconds is not None and (time.time() - start_ts) >= max_seconds:
                print(f"[Pipeline] stop by max_seconds={max_seconds}")
                break

            remaining = None if run_row_limit is None else run_row_limit - total
            effective_batch_size = batch_size if remaining is None else max(1, min(batch_size, remaining))
            rows = _fetch_unprocessed_rows(ex_read, batch_size=effective_batch_size)
            if not rows:
                break

            row_ids = [int(r["id"]) for r in rows]
            log_pipeline(
                f"run_analysis BATCH_PULL n={len(rows)} id_min={min(row_ids)} id_max={max(row_ids)} "
                f"total_so_far={total}"
            )
            log_detail(
                f"BATCH_PULL n={len(rows)} id_min={min(row_ids)} id_max={max(row_ids)} "
                f"total_so_far={total} batch_index={batches + 1}"
            )
            _log(
                f"[阶段②] LR 涉华分类 batch={len(rows)} 条（③④ 紧随其后；总进度条含 ETA）"
            )
            records = _compute_china_index_only(
                embedder, rows, show_bge_progress=show_bge_inner
            )
            passed = sum(1 for r in records if _passes_llm_gate(r))
            _log(
                f"[阶段③→④] 过闸 {passed}/{len(records)} 条（>={CHINA_GATE_THRESHOLD}）→ GLiNER+LLM → 写库+Milvus"
            )
            if shared_gliner_extractor is None:
                shared_gliner_extractor = GLiNEREntityExtractor()
            _run_llm_pipeline(
                records,
                backend,
                llm_batch_size,
                workers,
                per_chunk_timeout_seconds,
                shared_gliner_extractor,
                quiet_chunk_bar=main_pbar is not None,
            )
            _write_back_batch(ex_write, records)

            total += len(records)
            if micro_total_cap is not None:
                _micro_budget.add_consumed_micro(len(records))
            batches += 1
            elapsed = time.time() - start_ts
            if main_pbar is not None:
                main_pbar.update(len(records))
                main_pbar.set_postfix_str(f"第{batches}批")
            _log(f"[本批结束] 累计={total} 批={batches} 用时{elapsed:.0f}s")
            if micro_total_cap is not None:
                print(
                    f"[run_analysis] micro 总预算 已累计 {_micro_budget.load_consumed():,} / {micro_total_cap}",
                    flush=True,
                )
            log_pipeline(
                f"run_analysis BATCH_COMMIT n={len(records)} accumulated={total} batches={batches} "
                f"elapsed_s={elapsed:.1f} id_min={min(row_ids)} id_max={max(row_ids)}"
            )
            log_detail(
                f"BATCH_COMMIT n={len(records)} accumulated={total} batches={batches} "
                f"elapsed_s={elapsed:.1f} gate_pass={passed}"
            )
    except Exception as e:
        log_pipeline(
            f"run_analysis ABORT after accumulated={total} batches={batches}: "
            f"{type(e).__name__}: {e}"
        )
        raise
    finally:
        if shared_gliner_extractor is not None:
            try:
                shared_gliner_extractor.unload_model()
            except Exception:
                pass
        try:
            drain_milvus_async_workers()
        except Exception as e:
            print(f"[MilvusSync] drain_milvus_async_workers: {type(e).__name__}: {e}", flush=True)
        if main_pbar is not None:
            try:
                main_pbar.close()
            except Exception:
                pass
        log_pipeline(
            f"run_analysis END accumulated={total} batches={batches} elapsed_s={time.time() - start_ts:.1f}"
        )
        try:
            from agentic_rag.llm_usage import get_usage_snapshot

            snap = get_usage_snapshot()
            _u = (
                f"[LLM Usage] run_analysis 累计 tokens: "
                f"prompt={snap['prompt_tokens']:,} completion={snap['completion_tokens']:,} "
                f"total≈{snap['total_tokens']:,} api_calls={snap['api_calls']:,}"
            )
            if main_pbar is not None:
                try:
                    _tqdm_main.write(_u, file=sys.stdout)
                except TypeError:
                    print(_u, file=sys.stdout, flush=True)
            else:
                print(_u, file=sys.stdout, flush=True)
            log_detail(f"run_analysis END usage={snap}")
        except Exception:
            pass

    print(f"[Pipeline] done. total={total}")


def count_news_is_china_analyzed() -> int:
    """已写入 is_china_related 的新闻条数（非 NULL 即视为已跑过微观分析）。"""
    ensure_dotenv_loaded()
    from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA

    res = _pg_read().query(f"SELECT COUNT(*) AS cnt FROM {_NA} WHERE is_china_related IS NOT NULL")
    if not res.get("ok"):
        raise RuntimeError(f"Count analyzed failed: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        return 0
    v = rows[0].get("cnt")
    return int(v) if v is not None else 0


def stage_count_unprocessed() -> int:
    ensure_dotenv_loaded()
    return _count_unprocessed_rows(_pg_read())


def stage_fetch_unprocessed(batch_size: int) -> List[dict]:
    ensure_dotenv_loaded()
    return _fetch_unprocessed_rows(_pg_read(), batch_size)


def make_embedder_only() -> Any:
    """阶段②：仅加载 BGE-M3，不加载 GLiNER。"""
    ensure_dotenv_loaded()
    try:
        return get_embedder()
    except Exception as e:
        print(f"[Embedder] disabled due to load failure: {type(e).__name__}: {e}")
        return None


def make_embedder_and_extractor() -> tuple[Any, None]:
    """兼容旧调用：返回 (embedder, None)；GLiNER 在阶段③ 按需延迟加载。"""
    return make_embedder_only(), None


def stage_embed_china_only(rows: List[dict], embedder: Any) -> List[Dict[str, Any]]:
    """阶段②：仅 china_related_index，entities 恒为 []（与 records_stage2.pkl 约定一致）。"""
    return _compute_china_index_only(embedder, rows)


def stage_embed_and_entities(
    rows: List[dict],
    embedder: Any,
    extractor: Any = None,
) -> List[Dict[str, Any]]:
    """兼容旧名：已忽略 extractor，等同 stage_embed_china_only。"""
    return stage_embed_china_only(rows, embedder)


def stage_llm_only(
    records: List[Dict[str, Any]],
    llm_batch_size: Optional[int] = None,
    workers: Optional[int] = None,
    timeout_budget: Optional[float] = None,
    extractor: Optional[GLiNEREntityExtractor] = None,
) -> None:
    ensure_dotenv_loaded()
    backend = _resolve_llm_backend()
    if llm_batch_size is None:
        llm_batch_size = 32 if backend == "cloud_api" else 32
    if workers is None:
        workers = _default_llm_workers(backend, llm_batch_size)
    _print_runtime_resource_hints(backend)
    _run_llm_pipeline(records, backend, llm_batch_size, workers, timeout_budget, extractor)


def stage_write_back(records: List[Dict[str, Any]]) -> None:
    ensure_dotenv_loaded()
    try:
        from agentic_rag.db.news_analysis_schema import ensure_news_analysis_table

        ensure_news_analysis_table()
    except Exception as e:
        print(f"[Schema] news_analysis 确保: {type(e).__name__}: {e}")
    _write_back_batch(_pg_write(), records)


if __name__ == "__main__":
    run_analysis(max_batches=1, max_rows=32, max_seconds=180, per_chunk_timeout_seconds=None)
