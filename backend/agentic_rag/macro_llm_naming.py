#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Stage 5 内嵌：微簇/故事线 LLM 命名与综述（可选调用 naming_service / vLLM）。"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Tuple

from agentic_rag.naming_service import (
    QWEN_BACKEND,
    QWEN_BATCH_SIZE,
    build_micro_prompt,
    clean_title,
    generate_titles_batch,
)
from agentic_rag.db_runtime_config import require_database_password

# 历史模拟器 SKIP_MACRO_LLM_NAMING=1 时写入库内占位，供事后批量补全（见 macro_llm_fill_pending）
PLACEHOLDER_SUMMARY = "Waiting for LLM generation..."
PLACEHOLDER_TOPIC_MAIN = "Topic_Pending"


def skip_macro_llm_naming() -> bool:
    return os.getenv("SKIP_MACRO_LLM_NAMING", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def apply_macro_llm_skip_placeholders(
    macro_events: List[dict],
    macro_summaries: Dict[int, str],
) -> None:
    """SKIP 模式下为宏观行写入占位 summary / topic_main（结构聚类仍落库）。"""
    if not skip_macro_llm_naming():
        return
    for macro in macro_events:
        if macro.get("is_fragment"):
            continue
        sid = int(macro["macro_id"])
        macro["topic_main"] = PLACEHOLDER_TOPIC_MAIN
        macro_summaries[sid] = PLACEHOLDER_SUMMARY


def _cloud_json_mode_enabled() -> bool:
    """与 analysis_service 一致：云端 OpenAI 兼容接口是否启用 json_object。"""
    return os.getenv("CLOUD_API_JSON_MODE", "1").strip().lower() not in ("0", "false", "no")


def _macro_intel_response_format():
    if QWEN_BACKEND == "openai_api" and _cloud_json_mode_enabled():
        return {"type": "json_object"}
    return None


def _pg_news_rows(news_ids: List[int]) -> Dict[int, dict[str, str]]:
    if not news_ids:
        return {}
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_USER", "news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),
        connect_timeout=15,
    )
    out: Dict[int, dict[str, str]] = {}
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        for i in range(0, len(news_ids), 3000):
            chunk = news_ids[i : i + 3000]
            cur.execute(
                "SELECT id, title, abstract FROM news WHERE id = ANY(%s)",
                (chunk,),
            )
            for r in cur.fetchall():
                out[int(r["id"])] = {
                    "title": (r.get("title") or "").strip(),
                    "abstract": (r.get("abstract") or "").strip(),
                }
    finally:
        conn.close()
    return out


def _heuristic_micro_title(news_ids: List[int], news_map: Dict[int, dict[str, str]]) -> str:
    for nid in news_ids[:8]:
        row = news_map.get(nid)
        if not row:
            continue
        t = row.get("title") or ""
        if len(t) >= 4:
            t = re.sub(r'[\r\n]+', ' ', t)
            return t[:24]
    return ""


def build_macro_intel_prompt(micro_titles: List[str]) -> str:
    from config.settings import get_llm_prompts

    body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(micro_titles)) or "(无子事件)"
    prompts = get_llm_prompts()
    head = prompts.get("macro_intel_sys")
    if not head:
        head = (
            "You are a Chief Intelligence Officer. Analyze the following micro-events. "
            "Provide a valid JSON with 'title' (max 24 chars) and 'summary' (max 200 chars). "
            "Focus on geopolitical impact, alliances, and sanctions."
        )
    return (
        f"{head.strip()}\n\n"
        "Response must be a valid JSON object. 字段："
        '{"title":"不超过24字的中文故事线名","summary":"不超过200字的中文背景综述"}。\n'
        "不要 Markdown 围栏，不要其它解释。\n\n"
        f"子事件标题：\n{body}"
    )


def _parse_macro_intel(raw: str) -> Tuple[str, str]:
    """配合云端 JSON 模式：整段 json.loads，失败则从首个 { 起 raw_decode。"""
    text = (raw or "").strip()
    title, summary = "", ""
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
    if isinstance(data, dict):
        title = str(data.get("title") or "").strip()
        summary = str(data.get("summary") or "").strip()
    title = clean_title(title)[:24] if title else ""
    if len(summary) > 200:
        summary = summary[:200]
    return title, summary


def resolve_micro_titles(
    meta: Dict[int, dict],
    *,
    use_llm: bool,
    news_cache: Dict[int, dict[str, str]] | None = None,
) -> Dict[int, str]:
    """为每个微簇生成展示标题：LLM（Top5 标题+摘要）或启发式截断。"""
    if skip_macro_llm_naming():
        use_llm = False
    all_ids: List[int] = []
    for m in meta.values():
        all_ids.extend(m.get("news_ids") or [])
    cache = news_cache if news_cache is not None else _pg_news_rows(list(dict.fromkeys(all_ids)))

    cids = sorted(meta.keys())
    result: Dict[int, str] = {}
    prompts: List[str] = []
    prompt_cids: List[int] = []

    for cid in cids:
        nids = list(meta[cid].get("news_ids") or [])[:5]
        titles, sums = [], []
        for nid in nids:
            row = cache.get(nid)
            if not row:
                continue
            if row.get("title"):
                titles.append(row["title"])
                sums.append((row.get("abstract") or "")[:120])
        if not titles:
            result[cid] = _heuristic_micro_title(meta[cid].get("news_ids") or [], cache) or f"微簇{cid}"
            continue
        if use_llm and QWEN_BACKEND in ("vllm", "openai_api"):
            prompts.append(build_micro_prompt(titles[:5], sums[:5] if sums else titles[:5]))
            prompt_cids.append(cid)
        else:
            result[cid] = _heuristic_micro_title(meta[cid].get("news_ids") or [], cache) or titles[0][:24]

    if prompts and use_llm:
        for bs in range(0, len(prompts), QWEN_BATCH_SIZE):
            chunk_p = prompts[bs : bs + QWEN_BATCH_SIZE]
            chunk_i = prompt_cids[bs : bs + QWEN_BATCH_SIZE]
            try:
                raw_list = generate_titles_batch(chunk_p, max_tokens=48)
            except Exception as e:
                print(f"[MacroLLM] 微标题 LLM API 失败，本批改用启发式: {type(e).__name__}: {e}")
                for cidx in chunk_i:
                    nids0 = meta[cidx].get("news_ids") or []
                    result[cidx] = (
                        _heuristic_micro_title(nids0, cache) or f"微簇{cidx}"
                    )
                continue
            for cidx, raw in zip(chunk_i, raw_list):
                tt = clean_title(raw)
                nids0 = meta[cidx].get("news_ids") or []
                result[cidx] = tt or _heuristic_micro_title(nids0, cache) or f"微簇{cidx}"

    for cid in cids:
        if cid not in result:
            result[cid] = _heuristic_micro_title(meta[cid].get("news_ids") or [], cache) or f"微簇{cid}"
    return result


def resolve_macro_intel(
    macro_events: List[dict],
    micro_titles: Dict[int, str],
    *,
    use_llm: bool,
) -> Tuple[Dict[int, str], Dict[int, str]]:
    """非零碎故事线：LLM 生成标题+综述；失败则用子事件标题拼接占位。"""
    if skip_macro_llm_naming():
        use_llm = False
    titles_map: Dict[int, str] = {}
    summ_map: Dict[int, str] = {}

    prompts: List[str] = []
    pids: List[int] = []

    for macro in macro_events:
        sid = int(macro["macro_id"])
        members = sorted(int(x) for x in macro["fine_cluster_ids"])
        mt = [micro_titles.get(c, f"微{c}") for c in members]
        if macro.get("is_fragment"):
            head = micro_titles.get(members[0], "未命名") if members else "未命名"
            titles_map[sid] = f"线索·{head}"[:26]
            summ_map[sid] = "【零碎线索】下属微事件过少，仅供检索归档。"
            continue

        if use_llm and QWEN_BACKEND in ("vllm", "openai_api"):
            prompts.append(build_macro_intel_prompt(mt))
            pids.append(sid)
        else:
            head = mt[0] if mt else "聚合故事线"
            titles_map[sid] = clean_title(head)[:24] if head else f"故事线{sid}"
            summ_map[sid] = "；".join(mt[:5])[:200]

    if prompts and use_llm:
        step = max(1, QWEN_BATCH_SIZE // 2)
        for bs in range(0, len(prompts), step):
            chunk_p = prompts[bs : bs + step]
            chunk_i = pids[bs : bs + step]
            try:
                raw_list = generate_titles_batch(
                    chunk_p,
                    max_tokens=400,
                    response_format=_macro_intel_response_format(),
                )
            except Exception as e:
                print(f"[MacroLLM] 宏观综述 LLM API 失败，本批改用拼接占位: {type(e).__name__}: {e}")
                for sid in chunk_i:
                    members = sorted(
                        int(x) for x in next(
                            m["fine_cluster_ids"]
                            for m in macro_events
                            if int(m["macro_id"]) == sid
                        )
                    )
                    mt = [micro_titles.get(c, "") for c in members]
                    titles_map[sid] = clean_title(mt[0])[:24] if mt and mt[0] else f"故事线{sid}"
                    summ_map[sid] = "；".join(x for x in mt if x)[:200] or "自动聚合的多起相关事件。"
                continue
            for sid, raw in zip(chunk_i, raw_list):
                ti, su = _parse_macro_intel(raw)
                members = sorted(
                    int(x) for x in next(
                        m["fine_cluster_ids"]
                        for m in macro_events
                        if int(m["macro_id"]) == sid
                    )
                )
                mt = [micro_titles.get(int(c), "") for c in members]
                if not ti:
                    ti = clean_title(mt[0])[:24] if mt else f"故事线{sid}"
                if not su:
                    su = "；".join(x for x in mt if x)[:200] or "自动聚合的多起相关事件。"
                titles_map[sid] = ti
                summ_map[sid] = su

    for macro in macro_events:
        sid = int(macro["macro_id"])
        if sid in titles_map:
            continue
        members = sorted(int(x) for x in macro["fine_cluster_ids"])
        mt = [micro_titles.get(c, "") for c in members]
        titles_map[sid] = clean_title(mt[0])[:24] if mt and mt[0] else f"故事线{sid}"
        summ_map[sid] = "；".join(x for x in mt if x)[:200] or "自动聚合的多起相关事件。"

    return titles_map, summ_map


def build_rolling_macro_intel_prompt(old_summary: str, new_micro_titles: List[str]) -> str:
    """雪球增量：在旧综述基础上合并新微簇标题所代表的事实。"""
    from config.settings import get_llm_prompts

    old_s = (old_summary or "").strip() or "(无)"
    body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(new_micro_titles)) or "(无新子事件)"
    prompts = get_llm_prompts()
    head = prompts.get("macro_intel_rolling_sys")
    if not head:
        head = (
            "You are a Chief Intelligence Officer. Given an existing storyline summary and NEW micro-event titles, "
            "produce an updated summary (max 200 Chinese chars) that merges old context with new facts. "
            "Output valid JSON only."
        )
    return (
        f"{head.strip()}\n\n"
        "Response must be a valid JSON object: "
        '{"summary":"不超过200字的中文更新综述（保留旧要点并纳入新事实）"}。\n'
        "不要 Markdown 围栏，不要其它解释。\n\n"
        f"旧综述：\n{old_s}\n\n"
        f"本批新增子事件标题：\n{body}"
    )


def _parse_rolling_summary(raw: str) -> str:
    text = (raw or "").strip()
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
    if isinstance(data, dict):
        s = str(data.get("summary") or "").strip()
        if len(s) > 200:
            s = s[:200]
        return s
    return ""


def resolve_macro_intel_rolling(
    old_summary: str,
    new_micro_titles: List[str],
    *,
    use_llm: bool,
) -> str:
    """滚动综述：旧摘要 + 新微簇标题 → 新摘要。"""
    if skip_macro_llm_naming():
        return PLACEHOLDER_SUMMARY
    if not new_micro_titles:
        return (old_summary or "").strip()
    if not use_llm or QWEN_BACKEND not in ("vllm", "openai_api"):
        merged = ((old_summary or "").strip() + "；" + "；".join(new_micro_titles))[:200]
        return merged or (old_summary or "").strip()

    try:
        raw_list = generate_titles_batch(
            [build_rolling_macro_intel_prompt(old_summary, new_micro_titles)],
            max_tokens=400,
            response_format=_macro_intel_response_format(),
        )
        su = _parse_rolling_summary(raw_list[0] if raw_list else "")
        if su:
            return su
    except Exception as e:
        print(f"[MacroLLM] 滚动综述失败，改用拼接: {type(e).__name__}: {e}")

    merged = ((old_summary or "").strip() + "；" + "；".join(new_micro_titles))[:200]
    return merged or (old_summary or "").strip()
