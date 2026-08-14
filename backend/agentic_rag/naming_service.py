#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
naming_service.py  --  Phase-4 Qwen2.5 Batch Naming Service

Generates Chinese titles for micro_events and macro_storylines using
local Qwen2.5 GPU inference (vLLM server or transformers pipeline).

Config (env vars):
  QWEN_BACKEND   : "openai_api" | "vllm" | "transformers"
                  未设置时：若 LLM_BACKEND=cloud_api 或已配置 CLOUD_API_KEY / OPENAI_API_KEY，则默认识别为 openai_api（与 analysis_service 的 DashScope/OpenAI 等一致）；否则为 vllm
  openai_api     : 使用 CLOUD_API_BASE_URL + CLOUD_API_KEY + CLOUD_API_MODEL（或 OPENAI_*）
  vllm           : 使用 QWEN_BASE_URL + QWEN_MODEL_NAME（OpenAI 兼容服务，通常为 Docker 映射的 127.0.0.1:8000/v1）
  云端优先且 QWEN_LOCAL_FALLBACK_ON_SAFETY=1 时：安全拦截回退使用 QWEN_LOCAL_FALLBACK_BASE_URL / QWEN_LOCAL_FALLBACK_MODEL（推荐显式指向 Docker vLLM），未设则回退到 QWEN_BASE_URL / QWEN_MODEL_NAME
  QWEN_BASE_URL / QWEN_MODEL_NAME / QWEN_MODEL_PATH / QWEN_BATCH_SIZE：vllm / transformers；本地回退可与 QWEN_LOCAL_FALLBACK_* 共用 Docker 地址

Usage:
  python -m agentic_rag.naming_service               # name both
  python -m agentic_rag.naming_service --only micro  # only micro_events
  python -m agentic_rag.naming_service --only macro  # only macro_storylines
  python -m agentic_rag.naming_service --dry-run     # print prompts, no LLM
"""
from __future__ import annotations
import argparse, json, os, re, sys, threading, time
from pathlib import Path
from typing import Dict, List, Tuple

from agentic_rag import llm_usage
from agentic_rag.db_runtime_config import require_database_password
_FALLBACK_STATS: Dict[str, int] = {
    "safety_hits": 0,
    "local_retry_success": 0,
    "local_retry_fail": 0,
    "local_unavailable": 0,
}
_STATS_LOCK = threading.Lock()
_LOCAL_FALLBACK_ROUTE_LOGGED = False


def reset_openai_usage_counters() -> None:
    llm_usage.reset_usage_counters()


def get_openai_usage_snapshot() -> Dict[str, int]:
    return llm_usage.get_usage_snapshot()


def _inc_fallback_stat(key: str, n: int = 1) -> None:
    with _STATS_LOCK:
        _FALLBACK_STATS[key] = int(_FALLBACK_STATS.get(key, 0)) + int(n)


def get_fallback_stats_snapshot() -> Dict[str, int]:
    with _STATS_LOCK:
        return dict(_FALLBACK_STATS)


def print_fallback_stats(stage_label: str = "Stage5 命名") -> None:
    snap = get_fallback_stats_snapshot()
    print(
        f"[LLM] {stage_label} 安全拦截统计："
        f"safety_hits={snap.get('safety_hits', 0)}, "
        f"local_retry_success={snap.get('local_retry_success', 0)}, "
        f"local_retry_fail={snap.get('local_retry_fail', 0)}, "
        f"local_unavailable={snap.get('local_unavailable', 0)}",
        flush=True,
    )


def _accumulate_usage_from_response(resp) -> None:
    llm_usage.accumulate_from_chat_completion(resp)


def print_openai_usage_and_cost_estimate(stage_label: str = "Stage5 命名") -> None:
    """打印累计 token；若在 .env 配置了单价则估算费用（元，按百万 token 计价）。"""
    snap = get_openai_usage_snapshot()
    pt, ct, tt, nc = (
        snap["prompt_tokens"],
        snap["completion_tokens"],
        snap["total_tokens"],
        snap["api_calls"],
    )
    print(
        f"[LLM] {stage_label} API 累计："
        f"calls={nc}, prompt_tokens={pt:,}, completion_tokens={ct:,}, total≈{tt:,}",
        flush=True,
    )
    pin = (os.getenv("LLM_PRICE_INPUT_PER_MTOK_CNY") or "").strip()
    pout = (os.getenv("LLM_PRICE_OUTPUT_PER_MTOK_CNY") or "").strip()
    if not pin or not pout:
        print(
            "[LLM] 费用估算：在 .env 设置 LLM_PRICE_INPUT_PER_MTOK_CNY 与 "
            "LLM_PRICE_OUTPUT_PER_MTOK_CNY（人民币/百万 token，与百炼价目一致）后可显示约花费。",
            flush=True,
        )
        return
    try:
        r_in, r_out = float(pin), float(pout)
        cost = (pt / 1_000_000.0) * r_in + (ct / 1_000_000.0) * r_out
        print(
            f"[LLM] 按所配单价估算本次 {stage_label} 约 {cost:.4f} 元（输入 {r_in} 元/M + 输出 {r_out} 元/M，仅供参考）。",
            flush=True,
        )
    except ValueError:
        print(
            "[LLM] LLM_PRICE_*_PER_MTOK_CNY 解析失败，跳过费用估算。",
            flush=True,
        )
from dotenv import load_dotenv
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR.parent))
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.example", override=False)
from agentic_rag.db.executor import SafePGExecutor
from agentic_rag.db.security import PGSecurityConfig

# ---------------------------------------------------------------------------
# Configuration — edit here or override via environment variables
# ---------------------------------------------------------------------------
# 默认指向宿主机访问 Docker 映射端口（vllm 容器 8000:8000 → http://127.0.0.1:8000/v1）
# 模型 id 须与 curl http://127.0.0.1:8000/v1/models 中 data[].id 一致（常见为容器内路径如 AWQ）
QWEN_BASE_URL   = os.getenv("QWEN_BASE_URL",   "http://127.0.0.1:8000/v1")
QWEN_MODEL_NAME = os.getenv(
    "QWEN_MODEL_NAME",
    "/model/Qwen2.5-7B-Instruct-AWQ",
)
QWEN_MODEL_PATH = os.getenv("QWEN_MODEL_PATH", "/models/Qwen2.5-7B-Instruct-AWQ")
QWEN_CONCURRENCY_CAP = 24
QWEN_BATCH_SIZE = max(1, min(int(os.getenv("QWEN_BATCH_SIZE", "20")), QWEN_CONCURRENCY_CAP))

# Disable thinking for DeepSeek API calls
_EXTRA_BODY = {"thinking": {"type": "disabled"}}
QWEN_MAX_TOKENS = int(os.getenv("QWEN_MAX_TOKENS", "32"))  # title <= 15 chars


def _default_naming_backend() -> str:
    explicit = (os.getenv("QWEN_BACKEND") or "").strip().lower()
    if explicit in ("openai_api", "vllm", "transformers"):
        return explicit
    if (os.getenv("LLM_BACKEND") or "").strip().lower() == "cloud_api":
        return "openai_api"
    if (os.getenv("CLOUD_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip():
        return "openai_api"
    return "vllm"


QWEN_BACKEND = _default_naming_backend()


def _openai_client_config() -> tuple[str, str, str]:
    """返回 (base_url, api_key, model_id)。"""
    if QWEN_BACKEND == "openai_api":
        base = (
            os.getenv("CLOUD_API_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        key = (
            os.getenv("CLOUD_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        model = (
            os.getenv("CLOUD_API_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4o-mini"
        ).strip()
        if not key:
            raise RuntimeError(
                "openai_api 命名后端需要 CLOUD_API_KEY 或 OPENAI_API_KEY（与 Stage2–4 云端 LLM 相同）"
            )
        return base, key, model
    if QWEN_BACKEND == "vllm":
        base = QWEN_BASE_URL.rstrip("/")
        key = (os.getenv("QWEN_API_KEY") or "token-abc").strip()
        return base, key, QWEN_MODEL_NAME.strip()
    raise RuntimeError(f"当前 QWEN_BACKEND={QWEN_BACKEND!r} 不应走 OpenAI 兼容 HTTP")


def _llm_route_label() -> str:
    if QWEN_BACKEND == "openai_api":
        return "OpenAI兼容API(云端)"
    if QWEN_BACKEND == "vllm":
        return "OpenAI兼容网关(本地/内网)"
    return QWEN_BACKEND


def _is_safety_filter_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "data_inspection_failed" in msg
        or "inappropriate content" in msg
        or "content_filter" in msg
        or "safety" in msg and "filter" in msg
    )


def _local_vllm_retry_enabled() -> bool:
    return os.getenv("QWEN_LOCAL_FALLBACK_ON_SAFETY", "0").strip().lower() not in ("0", "false", "no")


def _local_vllm_client_config() -> tuple[str, str, str] | None:
    """
    本地 / Docker vLLM（OpenAI 兼容）连接信息。
    优先 QWEN_LOCAL_FALLBACK_*，便于「云端 API + Docker vLLM 回退」时与 QWEN_BASE_URL 解耦。
    未单独配置时与模块级 QWEN_BASE_URL / QWEN_MODEL_NAME 一致（含默认 127.0.0.1:8000/v1）。
    """
    base = (
        os.getenv("QWEN_LOCAL_FALLBACK_BASE_URL")
        or os.getenv("QWEN_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        base = QWEN_BASE_URL.strip().rstrip("/")
    model = (
        os.getenv("QWEN_LOCAL_FALLBACK_MODEL")
        or os.getenv("QWEN_MODEL_NAME")
        or ""
    ).strip()
    if not model:
        model = QWEN_MODEL_NAME.strip()
    key = (
        os.getenv("QWEN_LOCAL_FALLBACK_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or "token-abc"
    ).strip()
    if not base or not model:
        return None
    return base, key, model


def _maybe_log_local_fallback_routing() -> None:
    """openai_api 且启用安全回退时，首次调用打印将连哪台 Docker/内网 vLLM。"""
    global _LOCAL_FALLBACK_ROUTE_LOGGED
    if _LOCAL_FALLBACK_ROUTE_LOGGED:
        return
    if QWEN_BACKEND != "openai_api" or not _local_vllm_retry_enabled():
        _LOCAL_FALLBACK_ROUTE_LOGGED = True
        return
    _LOCAL_FALLBACK_ROUTE_LOGGED = True
    cfg = _local_vllm_client_config()
    explicit_fb = bool(
        (os.getenv("QWEN_LOCAL_FALLBACK_BASE_URL") or "").strip()
        or (os.getenv("QWEN_LOCAL_FALLBACK_MODEL") or "").strip()
    )
    if cfg:
        b, _, m = cfg
        src = (
            "QWEN_LOCAL_FALLBACK_BASE_URL / MODEL"
            if explicit_fb
            else "QWEN_BASE_URL / QWEN_MODEL_NAME（默认 Docker 宿主机 127.0.0.1:8000/v1）"
        )
        print(
            f"[LLM] 云端内容安全拦截时将回退到 Docker/内网 vLLM: base_url={b} model={m}（配置: {src}）",
            flush=True,
        )
    else:
        print(
            "[LLM] 已启用 QWEN_LOCAL_FALLBACK_ON_SAFETY，但无法解析本地 vLLM 地址；"
            "请设置 QWEN_LOCAL_FALLBACK_BASE_URL=http://127.0.0.1:8000/v1 与 QWEN_LOCAL_FALLBACK_MODEL，"
            "或设置 QWEN_BASE_URL / QWEN_MODEL_NAME。",
            flush=True,
        )

# ---------------------------------------------------------------------------
# PG helpers
# ---------------------------------------------------------------------------
def _pg_write():
    return SafePGExecutor(PGSecurityConfig(
        host=os.getenv("PG_HOST","127.0.0.1"),port=int(os.getenv("PG_PORT","5432")),
        dbname="postgres",user=os.getenv("PG_WRITE_USER","postgres"),
        password=require_database_password(),max_rows=100_000,force_limit=False))
def _pg_read():
    return SafePGExecutor(PGSecurityConfig(
        host=os.getenv("PG_HOST","127.0.0.1"),port=int(os.getenv("PG_PORT","5432")),
        dbname="postgres",user=os.getenv("PG_USER","news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),max_rows=100_000,force_limit=False))
# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------
def _direct_query(sql):
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST","127.0.0.1"),
        port=int(os.getenv("PG_PORT","5432")), dbname="postgres",
        user=os.getenv("PG_USER","news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"), connect_timeout=30)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql); return [dict(r) for r in cur.fetchall()]
    finally: conn.close()

def fetch_unnamed_micro_events(ex):
    sql = (
        "SELECT e.event_id, "
        "ARRAY_AGG(n.title    ORDER BY m.joint_dist ASC) FILTER (WHERE n.title    IS NOT NULL) AS news_titles, "
        "ARRAY_AGG(n.abstract ORDER BY m.joint_dist ASC) FILTER (WHERE n.abstract IS NOT NULL) AS news_summaries "
        "FROM micro_events e "
        "JOIN micro_event_members m ON m.event_id = e.event_id "
        "JOIN news n ON n.id = m.news_id "
        "WHERE e.title IS NULL AND e.status = 'active' "
        "GROUP BY e.event_id ORDER BY e.event_id ASC"
    )
    rows = []
    for r in _direct_query(sql):
        rows.append({"event_id": int(r["event_id"]),
                     "titles":    (r.get("news_titles")    or [])[:3],
                     "summaries": (r.get("news_summaries") or [])[:3]})
    print(f"[Data] {len(rows)} unnamed micro_events.")
    return rows

def fetch_unnamed_macro_storylines(ex):
    sql = (
        "SELECT s.storyline_id, "
        "ARRAY_AGG(e.title ORDER BY e.start_date ASC) FILTER (WHERE e.title IS NOT NULL) AS micro_titles "
        "FROM macro_storylines s "
        "JOIN storyline_micro_map m ON m.storyline_id = s.storyline_id "
        "JOIN micro_events e ON e.event_id = m.event_id "
        "WHERE s.title IS NULL AND s.status = 'active' "
        "GROUP BY s.storyline_id ORDER BY s.storyline_id ASC"
    )
    rows = []
    for r in _direct_query(sql):
        rows.append({"storyline_id": int(r["storyline_id"]),
                     "micro_titles": r.get("micro_titles") or []})
    print(f"[Data] {len(rows)} unnamed macro_storylines.")
    return rows

def build_micro_prompt(titles, summaries):
    from config.settings import get_llm_prompts

    prompts = get_llm_prompts()
    MICRO_SYS = prompts.get("micro_title_sys")
    if not MICRO_SYS:
        MICRO_SYS = (
            "You are a senior Chinese news editor. "
            "Given the following news articles, write ONE concise Chinese event title. "
            "Output plain text only (not JSON). "
            "Rules: max 15 Chinese characters, no punctuation, no explanation, title only."
        )
    parts = []
    for i, (t, s) in enumerate(zip(titles, summaries), 1):
        parts.append("[Article {}] {} / {}".format(i, t, str(s)[:80]))
    if not parts:
        for i, t in enumerate(titles, 1):
            parts.append("[Article {}] {}".format(i, t))
    body = chr(10).join(parts) if parts else "(no content)"
    return MICRO_SYS + chr(10)*2 + "Articles:" + chr(10) + body


def build_macro_prompt(micro_titles):
    from config.settings import get_llm_prompts

    prompts = get_llm_prompts()
    MACRO_SYS = prompts.get("macro_title_sys")
    if not MACRO_SYS:
        MACRO_SYS = (
            "You are a Chinese public opinion analyst. "
            "Given these sub-event titles of a continuous story in chronological order, "
            "write ONE high-level Chinese storyline title. "
            "Rules: max 15 Chinese characters, no punctuation, no explanation, title only."
        )
    body = chr(10).join("{0}. {1}".format(i+1, t) for i, t in enumerate(micro_titles)) or "(no content)"
    return MACRO_SYS + chr(10)*2 + "Sub-events:" + chr(10) + body

# ---------------------------------------------------------------------------
# GPU batch inference backend
# ---------------------------------------------------------------------------
_pipeline = None  # cached transformers pipeline

def _init_transformers_pipeline():
    global _pipeline
    if _pipeline is not None: return _pipeline
    print(f"[LLM] Loading Transformers AWQ pipeline from {QWEN_MODEL_PATH} ...")
    import torch
    from transformers import pipeline, AutoTokenizer, AutoModelForCausalLM
    tokenizer = AutoTokenizer.from_pretrained(QWEN_MODEL_PATH, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        QWEN_MODEL_PATH,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True
    )
    _pipeline = pipeline("text-generation", model=model, tokenizer=tokenizer)
    print("[LLM] Pipeline ready on GPU.")
    return _pipeline

def _generate_openai_compatible(
    prompts: List[str],
    max_tokens: int | None = None,
    *,
    response_format: dict | None = None,
) -> List[str]:
    """
    OpenAI 兼容 Chat Completions：openai_api=云端(DashScope/OpenAI 等)，vllm=本机网关。
    若云端因内容安全拦截（data_inspection_failed / inappropriate content）失败，
    且已配置本地 vLLM，则自动降级到本地 vLLM 重试该条。
    其它失败仍降级为空串，不抛异常中断流水线。
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("pip install openai")
    _maybe_log_local_fallback_routing()
    base, api_key, model_id = _openai_client_config()
    client = OpenAI(base_url=base, api_key=api_key)
    mtok = int(max_tokens if max_tokens is not None else QWEN_MAX_TOKENS)
    local_cfg = _local_vllm_client_config() if QWEN_BACKEND == "openai_api" and _local_vllm_retry_enabled() else None
    local_client = OpenAI(base_url=local_cfg[0], api_key=local_cfg[1]) if local_cfg else None

    import concurrent.futures

    def _request_once(client_obj, model_name: str, prompt: str, *, allow_response_format: bool) -> str:
        kw: dict = dict(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=mtok,
            temperature=0.1,
        )
        if response_format is not None and allow_response_format:
            kw["response_format"] = response_format
        if _EXTRA_BODY:
            kw.setdefault("extra_body", {}).update(_EXTRA_BODY)
        resp = client_obj.chat.completions.create(**kw)
        _accumulate_usage_from_response(resp)
        ch = resp.choices[0].message.content if resp.choices else None
        return (ch or "").strip()

    def _call(prompt: str) -> str:
        last_err = None
        for attempt in range(1, 4):
            try:
                return _request_once(client, model_id, prompt, allow_response_format=True)
            except Exception as e:
                last_err = e
                msg_l = str(e).lower()
                ename = type(e).__name__
                safety_hit = _is_safety_filter_error(e)
                transient = (
                    any(
                        x in msg_l
                        for x in (
                            "502",
                            "503",
                            "504",
                            "timeout",
                            "timed out",
                            "connection",
                            "connect",
                            "refused",
                            "unreachable",
                            "remote protocol",
                            "internal server error",
                        )
                    )
                    or ename
                    in (
                        "InternalServerError",
                        "APIConnectionError",
                        "APIStatusError",
                        "ConnectError",
                        "ReadTimeout",
                    )
                )
                if safety_hit:
                    _inc_fallback_stat("safety_hits")
                if safety_hit and local_client is not None and local_cfg is not None:
                    try:
                        print("[LLM] 云端命中内容安全，切换本地 vLLM 重试本条", flush=True)
                        out = _request_once(local_client, local_cfg[2], prompt, allow_response_format=False)
                        _inc_fallback_stat("local_retry_success")
                        return out
                    except Exception as local_e:
                        _inc_fallback_stat("local_retry_fail")
                        print(
                            f"[LLM] 本地 vLLM 重试也失败，降级为空输出: {type(local_e).__name__}: {local_e}",
                            flush=True,
                        )
                        return ""
                if safety_hit and (local_client is None or local_cfg is None):
                    _inc_fallback_stat("local_unavailable")
                if attempt < 3 and transient:
                    time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
                    continue
                print(f"[LLM] {_llm_route_label()} 本条失败，降级为空输出: {ename}: {e}")
                return ""
        print(f"[LLM] {_llm_route_label()} 本条重试耗尽: {last_err!r}")
        return ""

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(QWEN_BATCH_SIZE, QWEN_CONCURRENCY_CAP))
    ) as ex:
        results = list(ex.map(_call, prompts))
    return results


def ping_llm(message: str = "你好") -> str:
    """向当前配置的 HTTP LLM 发送一条用户消息并返回助手文本（用于连通性测试）。"""
    if QWEN_BACKEND == "transformers":
        return "[ping] transformers 为本地 GPU 路径，请改用 QWEN_BACKEND=openai_api 做 HTTP 探测"
    try:
        out = _generate_openai_compatible([message], max_tokens=256)
        return (out[0] if out else "").strip()
    except Exception as e:
        return f"[ping 失败] {type(e).__name__}: {e}"

def _generate_transformers(prompts: List[str]) -> List[str]:
    """Batch inference via local transformers pipeline."""
    pipe = _init_transformers_pipeline()
    results = []
    for i in range(0, len(prompts), QWEN_BATCH_SIZE):
        batch = prompts[i:i + QWEN_BATCH_SIZE]
        chats = [[{"role": "user", "content": p}] for p in batch]
        outputs = pipe(
            chats,
            max_new_tokens=QWEN_MAX_TOKENS,
            batch_size=len(batch),
            do_sample=False,
            pad_token_id=pipe.tokenizer.pad_token_id,
            eos_token_id=pipe.tokenizer.eos_token_id,
        )
        for out in outputs:
            generated = out[0]["generated_text"][-1]["content"]
            results.append(generated.strip())
    return results

def generate_titles_batch(
    prompts: List[str],
    *,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> List[str]:
    """Dispatch to configured backend. Returns one title per prompt.

    response_format: 例如 {\"type\": \"json_object\"}，仅建议 openai_api 使用；vLLM 可能不支持。
    """
    if not prompts:
        return []
    print(
        f"[LLM] Generating {len(prompts)} 条 → {_llm_route_label()} "
        f"(QWEN_BACKEND={QWEN_BACKEND}, batch={QWEN_BATCH_SIZE})..."
    )
    if QWEN_BACKEND in ("openai_api", "vllm"):
        return _generate_openai_compatible(
            prompts, max_tokens=max_tokens, response_format=response_format
        )
    if QWEN_BACKEND == "transformers":
        return _generate_transformers(prompts)
    raise ValueError(
        f"Unknown QWEN_BACKEND={QWEN_BACKEND!r}. Use 'openai_api', 'vllm', or 'transformers'."
    )

def clean_title(raw: str, max_len: int = 120) -> str:
    """Strip markdown, newlines, explanatory text; keep first line only.

    Args:
        raw: Raw LLM output.
        max_len: Maximum character length. Default 120 (~15 English words).
                 Use 20 for Chinese-only titles (micro/macro event naming).
    """
    t = raw.strip()
    t = re.sub(r"[\*#`_~>\[\]]", "", t)  # markdown symbols
    t = t.split("\n")[0].strip()           # first line only
    t = re.sub(r"^\d+\s*[\.\-\s]*\s*", "", t)  # leading "1. ", "1-", "1 " etc
    # Remove isolated English words (sequences of a-z that appear as standalone tokens)
    t = re.sub(r"\s*[a-zA-Z]+\s*", "", t)
    t = re.sub(r"[^\u4e00-\u9fff\u3400-\u4dbf\w\s]", "", t)  # strip non-CJK punct
    t = t.strip()[:max_len]
    return t

# ---------------------------------------------------------------------------
# Database write-back
# ---------------------------------------------------------------------------
def update_micro_titles(id_title_pairs: List[Tuple[int,str]], ex) -> int:
    if not id_title_pairs: return 0
    import psycopg2 as pg
    conn = pg.connect(host=os.getenv("PG_HOST","127.0.0.1"),
                      port=int(os.getenv("PG_PORT","5432")),dbname="postgres",
                      user=os.getenv("PG_WRITE_USER","postgres"),
                      password=require_database_password(),connect_timeout=10)
    conn.set_session(autocommit=False); cur=conn.cursor(); n=0
    try:
        for eid, title in id_title_pairs:
            t = title.replace("'","''")
            cur.execute(f"UPDATE micro_events SET title='{t}', updated_at=NOW() WHERE event_id={eid}")
            n += 1
        conn.commit()
    finally:
        try: conn.close()
        except: pass
    return n

def update_macro_titles(id_title_pairs: List[Tuple[int,str]], ex) -> int:
    if not id_title_pairs: return 0
    import psycopg2 as pg
    conn = pg.connect(host=os.getenv("PG_HOST","127.0.0.1"),
                      port=int(os.getenv("PG_PORT","5432")),dbname="postgres",
                      user=os.getenv("PG_WRITE_USER","postgres"),
                      password=require_database_password(),connect_timeout=10)
    conn.set_session(autocommit=False); cur=conn.cursor(); n=0
    try:
        for sid, title in id_title_pairs:
            t = title.replace("'","''")
            cur.execute(f"UPDATE macro_storylines SET title='{t}', updated_at=NOW() WHERE storyline_id={sid}")
            n += 1
        conn.commit()
    finally:
        try: conn.close()
        except: pass
    return n
# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def name_micro_events(ex_read, ex_write, batch_size, dry_run=False):
    rows = fetch_unnamed_micro_events(ex_read)
    if not rows: print("[Micro] Nothing to name."); return 0
    prompts, ids = [], []
    for r in rows:
        prompts.append(build_micro_prompt(r["titles"], r["summaries"]))
        ids.append(r["event_id"])
    if dry_run:
        for i, p in enumerate(prompts[:3]):
            print(f"\n--- Micro prompt sample {i+1} ---\n{p[:300]}...")
        print(f"[DryRun] Would generate {len(prompts)} micro titles.")
        return 0
    pairs = []
    for bs in range(0, len(prompts), batch_size):
        chunk_p = prompts[bs:bs+batch_size]
        chunk_i = ids[bs:bs+batch_size]
        titles = generate_titles_batch(chunk_p)
        for eid, raw in zip(chunk_i, titles):
            t = clean_title(raw, max_len=20)
            if t: pairs.append((eid, t))
            else: print(f"[Micro] Warning: empty title for event_id={eid}, raw={raw!r}")
        print(f"[Micro] {min(bs+batch_size,len(prompts))}/{len(prompts)} done")
    n = update_micro_titles(pairs, ex_write)
    print(f"[Micro] Updated {n} micro_events with titles.")
    return n

def name_macro_storylines(ex_read, ex_write, batch_size, dry_run=False):
    rows = fetch_unnamed_macro_storylines(ex_read)
    if not rows: print("[Macro] Nothing to name."); return 0
    prompts, ids = [], []
    for r in rows:
        prompts.append(build_macro_prompt(r["micro_titles"]))
        ids.append(r["storyline_id"])
    if dry_run:
        for i, p in enumerate(prompts[:3]):
            print(f"\n--- Macro prompt sample {i+1} ---\n{p[:300]}...")
        print(f"[DryRun] Would generate {len(prompts)} macro titles.")
        return 0
    pairs = []
    for bs in range(0, len(prompts), batch_size):
        chunk_p = prompts[bs:bs+batch_size]
        chunk_i = ids[bs:bs+batch_size]
        titles = generate_titles_batch(chunk_p)
        for sid, raw in zip(chunk_i, titles):
            t = clean_title(raw, max_len=20)
            if t: pairs.append((sid, t))
            else: print(f"[Macro] Warning: empty title for storyline_id={sid}, raw={raw!r}")
        print(f"[Macro] {min(bs+batch_size,len(prompts))}/{len(prompts)} done")
    n = update_macro_titles(pairs, ex_write)
    print(f"[Macro] Updated {n} macro_storylines with titles.")
    return n


# ── L1 event cluster naming ──────────────────────────────────────────────
_L1_NAME_SYS = (
    "你是一名地缘政治新闻分析师。以下是一组关于同一事件的新闻报道标题，"
    "请用一句简洁的中文概括该事件（10-25字）。\n\n"
    "要求：\n"
    "- 聚焦整个事件的核心，不要只抓某个细节\n"
    "- 包含关键主体和行为\n"
    "- 全部使用中文，不要夹杂英文\n"
    "- 直接输出标题，不要序号，不要引号，不要任何标点符号\n"
    "- 不要署名，不要分段"
)


def build_l1_name_prompt(articles: List[Dict]) -> str:
    """Build a prompt for naming an L1 event cluster from its articles.

    Each article dict must have keys: title, published_at, event_type,
    initiator, target.
    For clusters with >20 articles, a representative sample is used.
    """
    sample = articles
    if len(articles) > 20:
        import random
        rng = random.Random(42)
        sorted_arts = sorted(articles, key=lambda a: a.get("published_at", "") or "")
        first = sorted_arts[0]
        last = sorted_arts[-1]
        middle = rng.sample(sorted_arts[1:-1], min(18, len(sorted_arts) - 2))
        sample = [first] + sorted(middle, key=lambda a: a.get("published_at", "") or "") + [last]

    parts = []
    for i, a in enumerate(sample, 1):
        title = (a.get("title") or "")[:120]
        parts.append(f"- \"{title}\"")

    body = "\n".join(parts) if parts else "(no articles)"
    size_note = f"（共{len(articles)}篇报道）" if len(articles) > 1 else ""
    return _L1_NAME_SYS + f"\n\n新闻报道标题{size_note}：\n" + body


def name_event_clusters(
    clusters: Dict[str, List[int]],
    article_titles: Dict[int, str],
    article_events: Dict[int, Dict],
    *,
    batch_size: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, str]:
    """Generate LLM titles for L1 event clusters.

    Args:
        clusters: {cluster_id: [article_id, ...]}
        article_titles: {article_id: title_string}
        article_events: {article_id: {event_type, initiator, target, published_at}}
        batch_size: LLM batch size (default: QWEN_BATCH_SIZE)
        dry_run: Print sample prompts without calling LLM.

    Returns: {cluster_id: title_string}
    """
    bs = batch_size or QWEN_BATCH_SIZE

    # Build prompts for non-singleton clusters only
    prompts: List[str] = []
    prompt_ids: List[str] = []
    cluster_names: Dict[str, str] = {}

    # For singletons, use the article title directly
    for cid, aids in clusters.items():
        if len(aids) == 1:
            aid = aids[0]
            t = article_titles.get(aid, "").strip()
            cluster_names[cid] = t[:200] if t else f"Event {aid}"
        else:
            articles = []
            for aid in aids:
                ev = article_events.get(aid, {})
                articles.append({
                    "title": article_titles.get(aid, ""),
                    "published_at": ev.get("published_at", ""),
                    "event_type": ev.get("event_type", ""),
                    "initiator": ev.get("initiator", ""),
                    "target": ev.get("target", ""),
                })
            prompts.append(build_l1_name_prompt(articles))
            prompt_ids.append(cid)

    if not prompts:
        print("[L1 Naming] No non-singleton clusters to name.")
        return cluster_names

    if dry_run:
        print(f"[L1 Naming] Would generate {len(prompts)} titles "
              f"(skipping {sum(1 for v in clusters.values() if len(v)==1)} singletons).")
        for i, p in enumerate(prompts[:3]):
            print(f"\n--- Prompt sample {i+1} ---\n{p[:400]}...")
        return cluster_names

    # Batch call LLM
    n_total = len(prompts)
    for bs_start in range(0, n_total, bs):
        chunk_p = prompts[bs_start:bs_start + bs]
        chunk_i = prompt_ids[bs_start:bs_start + bs]
        titles = generate_titles_batch(chunk_p, max_tokens=48)
        for cid, raw in zip(chunk_i, titles):
            t = clean_title(raw)
            if t:
                cluster_names[cid] = t
            else:
                # Fallback: use first article's title
                first_aid = (clusters.get(cid) or [None])[0]
                fallback = article_titles.get(first_aid, "").strip() if first_aid else ""
                cluster_names[cid] = fallback[:200] or f"Cluster {cid[:12]}"
                print(f"[L1 Naming] Warning: empty LLM output for {cid}, "
                      f"using fallback: {cluster_names[cid][:60]}")
        done = min(bs_start + bs, n_total)
        print(f"[L1 Naming] {done}/{n_total} clusters named", flush=True)

    named = sum(1 for v in clusters.values() if len(v) >= 2)
    print(f"[L1 Naming] Named {named} non-singleton clusters "
          f"({len(cluster_names)} total including singleton titles).")
    return cluster_names


def main():
    global QWEN_BACKEND, QWEN_MODEL_PATH, QWEN_BASE_URL, QWEN_MODEL_NAME, QWEN_BATCH_SIZE
    parser = argparse.ArgumentParser(description="Phase-4 Qwen2.5 Batch Naming Service")
    parser.add_argument("--only",       choices=["micro","macro"], default=None,
                        help="Name only micro_events or macro_storylines (default: both)")
    parser.add_argument("--batch-size", type=int, default=QWEN_BATCH_SIZE)
    parser.add_argument(
        "--backend",
        choices=["openai_api", "vllm", "transformers"],
        default=None,
        help="覆盖 QWEN_BACKEND；默认从环境推断（有 CLOUD_API_KEY 时常为 openai_api）",
    )
    parser.add_argument("--model-path", type=str, default=QWEN_MODEL_PATH,
                        help="Local model path (transformers backend)")
    parser.add_argument("--base-url",   type=str, default=QWEN_BASE_URL,
                        help="仅 vllm：网关 Base URL")
    parser.add_argument("--model-name", type=str, default=QWEN_MODEL_NAME,
                        help="仅 vllm：模型名")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print sample prompts without calling LLM")
    parser.add_argument(
        "--ping",
        action="store_true",
        help="仅测试 LLM：发送 --message 后打印回复并退出（不访问数据库）",
    )
    parser.add_argument("--message", type=str, default="你好",
                        help="与 --ping 合用，默认「你好」")
    args = parser.parse_args()

    QWEN_MODEL_PATH = args.model_path
    QWEN_BASE_URL   = args.base_url
    QWEN_MODEL_NAME = args.model_name
    QWEN_BATCH_SIZE = max(1, min(int(args.batch_size), QWEN_CONCURRENCY_CAP))
    if args.backend is not None:
        QWEN_BACKEND = args.backend

    if args.ping:
        print(f"[Ping] QWEN_BACKEND={QWEN_BACKEND}  message={args.message!r}")
        if QWEN_BACKEND == "openai_api":
            b = (os.getenv("CLOUD_API_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
            m = (os.getenv("CLOUD_API_MODEL") or os.getenv("OPENAI_MODEL") or "").strip()
            print(f"[Ping] base_url={b or '(默认 OpenAI)'}  model={m or '(默认 gpt-4o-mini)'}")
        elif QWEN_BACKEND == "vllm":
            print(f"[Ping] base_url={QWEN_BASE_URL}  model={QWEN_MODEL_NAME}")
        reply = ping_llm(args.message)
        line = f"[Ping] 回复:\n{reply}"
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("gbk", errors="replace").decode("gbk", errors="replace"))
        return

    t0 = time.time()
    ex_read  = _pg_read()
    ex_write = _pg_write()

    print(f"[Config] backend={QWEN_BACKEND}  batch={QWEN_BATCH_SIZE}")
    if QWEN_BACKEND == "openai_api":
        print(
            f"[Config] CLOUD_API_BASE_URL={os.getenv('CLOUD_API_BASE_URL', '')} "
            f"CLOUD_API_MODEL={os.getenv('CLOUD_API_MODEL', '')}"
        )
    elif QWEN_BACKEND == "vllm":
        print(f"[Config] QWEN_BASE_URL={QWEN_BASE_URL}  QWEN_MODEL_NAME={QWEN_MODEL_NAME}")
    else:
        print(f"[Config] model_path={QWEN_MODEL_PATH}")

    n_micro = n_macro = 0
    if args.only != "macro":
        n_micro = name_micro_events(ex_read, ex_write, QWEN_BATCH_SIZE, dry_run=args.dry_run)
    if args.only != "micro":
        n_macro = name_macro_storylines(ex_read, ex_write, QWEN_BATCH_SIZE, dry_run=args.dry_run)

    print("\n========== Naming Service Report ==========")
    print(f"micro_events titled   : {n_micro}")
    print(f"macro_storylines titled: {n_macro}")
    print(f"Elapsed               : {time.time()-t0:.1f}s")
    print("==========================================\n")

if __name__ == "__main__":
    main()
