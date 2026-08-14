"""
阶段 1c：非涉华新闻轻量实体标注（FlashText 词典匹配）。

- 词表：从涉华新闻已写入的 entities JSON 抽取去重字符串（整次运行只建一次 KeywordProcessor）。
- 目标：(entities IS NULL OR entities = '[]') AND is_china_related = FALSE AND duplicate_of IS NULL
- 写回：JSON 字符串数组，与 GLiNER 路径一致，兼容 Stage 5/6。

不并入 --stage 1 / 999；需显式 --stage 1c。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, List, Optional, Set

from agentic_rag import analysis_service as svc
from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA_TABLE
from config.settings import get_stop_words_set

try:
    from flashtext import KeywordProcessor
except ImportError:
    KeywordProcessor = None  # type: ignore[misc, assignment]


def _max_vocab_kw_len() -> int:
    try:
        return max(8, int(os.getenv("FAST_ENTITY_MAX_KEYWORD_LEN", "256")))
    except ValueError:
        return 256


def _max_entities_per_news() -> int:
    try:
        return max(1, int(os.getenv("FAST_ENTITY_MAX_PER_NEWS", "80")))
    except ValueError:
        return 80


def _min_token_len() -> int:
    try:
        return max(1, int(os.getenv("FAST_ENTITY_MIN_TOKEN_LEN", "2")))
    except ValueError:
        return 2


def _text_for_match(row: dict) -> str:
    t = (row.get("title") or "").strip()
    a = (row.get("abstract") or "").strip()
    return f"{t}\n{a}".strip()


def parse_entity_strings_from_json(val: Any) -> List[str]:
    """从 news_analysis.entities 解析出字符串列表（兼容 JSON 字符串或 list）。"""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        try:
            val = json.loads(s)
        except json.JSONDecodeError:
            return []
    if not isinstance(val, list):
        return []
    out: List[str] = []
    for item in val:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            raw = item.get("name") or item.get("text") or item.get("entity")
            if isinstance(raw, str) and raw.strip():
                out.append(raw.strip())
    return out


def collect_distinct_keywords_from_db(ex_read, *, page_size: int = 5000) -> Set[str]:
    """分页拉取涉华非空 entities，返回去重后的词典字符串集合。"""
    mx = _max_vocab_kw_len()
    mn = _min_token_len()
    seen: Set[str] = set()
    offset = 0
    pages = 0
    while True:
        sql = (
            f"SELECT na.entities AS entities FROM {_NA_TABLE} na "
            f"WHERE na.is_china_related IS TRUE "
            f"AND na.entities IS NOT NULL "
            f"AND na.entities != '[]'::jsonb "
            f"ORDER BY na.news_id ASC "
            f"LIMIT {int(page_size)} OFFSET {int(offset)}"
        )
        res = ex_read.query(sql)
        if not res.get("ok"):
            raise RuntimeError(f"[1c] 读取词表失败: {res.get('error')}")
        rows = res.get("rows") or []
        if not rows:
            break
        pages += 1
        for row in rows:
            for s in parse_entity_strings_from_json(row.get("entities")):
                if len(s) > mx:
                    continue
                if len(s) < mn:
                    continue
                seen.add(s)
        if len(rows) < page_size:
            break
        offset += page_size
    print(
        f"[阶段1c] 词表扫描完成：分页 {pages}，distinct 关键词 {len(seen):,} 条",
        flush=True,
    )
    return seen


def build_keyword_processor(keywords: Set[str]) -> "KeywordProcessor":
    if KeywordProcessor is None:
        raise RuntimeError("未安装 flashtext，请执行: pip install flashtext")
    case_sensitive = os.getenv("FAST_ENTITY_CASE_SENSITIVE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    kp = KeywordProcessor(case_sensitive=case_sensitive)
    for kw in sorted(keywords):
        if not kw:
            continue
        try:
            kp.add_keyword(kw)
        except Exception:
            continue
    return kp


def fetch_pending_non_china_batch(
    ex_read, batch_size: int, *, after_id: int
) -> List[dict]:
    """按 id 游标分页，避免「无匹配未 UPDATE」时重复拉取同一批。"""
    lim = max(1, int(batch_size))
    aid = max(0, int(after_id))
    sql = (
        "SELECT n.id, n.title, n.abstract FROM news n "
        f"INNER JOIN {_NA_TABLE} na ON na.news_id = n.id "
        "WHERE na.is_china_related IS FALSE "
        "AND na.duplicate_of IS NULL "
        "AND (na.entities IS NULL OR na.entities = '[]'::jsonb) "
        f"AND n.id > {aid} "
        f"ORDER BY n.id ASC LIMIT {lim}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"[1c] 拉取待标注行失败: {res.get('error')}")
    return res.get("rows") or []


def count_pending_non_china(ex_read) -> int:
    sql = (
        "SELECT COUNT(*) AS cnt FROM news n "
        f"INNER JOIN {_NA_TABLE} na ON na.news_id = n.id "
        "WHERE na.is_china_related IS FALSE "
        "AND na.duplicate_of IS NULL "
        "AND (na.entities IS NULL OR na.entities = '[]'::jsonb)"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"[1c] COUNT 失败: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        return 0
    v = rows[0].get("cnt")
    return int(v) if v is not None else 0


def _update_entities_batch(ex_write, pairs: List[tuple]) -> None:
    """pairs: list of (news_id, list[str])"""
    if not pairs:
        return
    import psycopg2.extras
    from psycopg2.extras import Json

    conn = ex_write.get_write_conn()
    try:
        cur = conn.cursor()
        sql = (
            f"UPDATE {_NA_TABLE} SET entities = %s::jsonb, updated_at = now() "
            "WHERE news_id = %s"
        )
        params = [(Json(ents), int(nid)) for nid, ents in pairs]
        psycopg2.extras.execute_batch(cur, sql, params, page_size=500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_stage1c(
    *,
    batch_size: int = 500,
    max_rows: Optional[int] = None,
    max_batches: Optional[int] = None,
) -> None:
    """
    非涉华实体快速补全：FlashText 全词表仅构建一次，批处理分页更新。
    """
    svc.ensure_dotenv_loaded()
    try:
        from agentic_rag.db.news_analysis_schema import (
            ensure_news_analysis_dedupe_columns,
            ensure_news_analysis_table,
        )

        ensure_news_analysis_table()
        ensure_news_analysis_dedupe_columns()
    except Exception as e:
        print(f"[Schema] ensure news_analysis: {type(e).__name__}: {e}")

    ex_read = svc._pg_read()
    ex_write = svc._pg_write()

    pending = count_pending_non_china(ex_read)
    print(
        f"[阶段1c/FlashText] 待标注非涉华（entities 空且非影子）: {pending:,} 条；"
        f"batch_size={batch_size}, max_rows={max_rows}, max_batches={max_batches}",
        flush=True,
    )

    t_vocab0 = time.perf_counter()
    vocab = collect_distinct_keywords_from_db(ex_read)
    if not vocab:
        print(
            "[阶段1c] 词表为空（无涉华实体可复用）。请先有 is_china_related=TRUE 且 entities 非空的分析结果。",
            flush=True,
        )
        return

    kp = build_keyword_processor(vocab)
    stop_set = get_stop_words_set()
    print(
        f"[阶段1c] KeywordProcessor 已构建（关键词 {len(vocab):,}），停用词 {len(stop_set):,} 条，耗时 "
        f"{time.perf_counter() - t_vocab0:.1f}s；开始扫描正文…",
        flush=True,
    )

    total = 0
    batches = 0
    after_id = 0
    t0 = time.perf_counter()
    max_ent = _max_entities_per_news()

    try:
        while True:
            if max_batches is not None and batches >= max_batches:
                print(f"[阶段1c] stop: max_batches={max_batches}", flush=True)
                break
            if max_rows is not None and total >= max_rows:
                print(f"[阶段1c] stop: max_rows={max_rows}", flush=True)
                break

            remaining = None if max_rows is None else max_rows - total
            bs = batch_size if remaining is None else max(1, min(batch_size, remaining))
            rows = fetch_pending_non_china_batch(ex_read, bs, after_id=after_id)
            if not rows:
                break

            updates: List[tuple] = []
            for row in rows:
                text = _text_for_match(row)
                if not text:
                    continue
                found = kp.extract_keywords(text)
                if not found:
                    continue
                uniq: List[str] = []
                seen_line: Set[str] = set()
                for w in found:
                    ws = w.strip() if isinstance(w, str) else str(w).strip()
                    if not ws or ws.lower() in seen_line:
                        continue
                    if ws.lower() in stop_set:
                        continue
                    seen_line.add(ws.lower())
                    uniq.append(ws)
                    if len(uniq) >= max_ent:
                        break
                if not uniq:
                    continue
                updates.append((int(row["id"]), uniq))

            if updates:
                _update_entities_batch(ex_write, updates)

            n = len(rows)
            after_id = max(int(r["id"]) for r in rows)
            total += n
            batches += 1
            print(
                f"[阶段1c] 本批拉取 {n} 条（id≤{after_id}），写入实体 {len(updates)} 条，"
                f"累计扫描 {total}，批 {batches}，用时 {time.perf_counter() - t0:.1f}s",
                flush=True,
            )
    finally:
        pass

    print(f"[阶段1c] 完成，总扫描 {total} 行，用时 {time.perf_counter() - t0:.1f}s", flush=True)
