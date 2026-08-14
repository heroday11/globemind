#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Obsidian 三层与 PostgreSQL 的一致性检查。

- db_global：全库 SQL 指标（非抽样）。
- db_row_samples：在 PG 内对随机抽中的 macro/micro/news 行做关系校验（不读磁盘）。
- samples（Obsidian）：对随机抽中的行核对 Markdown（需已跑 Stage 6）。

--repair：仅适合「旧库修补」。若已改代码并计划清空后全量重跑，不必 repair，以干净库为准。

用法：
  python -m agentic_rag.audit_vault_consistency
  python -m agentic_rag.audit_vault_consistency --skip-vault          # 只检 PostgreSQL
  python -m agentic_rag.audit_vault_consistency --seed 42 --macros 5 --micros 8 --articles 10

环境变量：PG_HOST、PG_PORT、PG_USER、PG_PASSWORD、OBSIDIAN_MICRO_* 等同 sync_obsidian_v4。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from config.settings import obsidian_vault_path
from agentic_rag.db_runtime_config import require_database_password

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_VAULT = obsidian_vault_path()


def _pg_connect_read():
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_USER", "news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),
        connect_timeout=15,
    )


def _safe_filename(text) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", str(text or "")).strip()[:50]


def _parse_frontmatter(md: str) -> tuple[dict[str, Any], str]:
    """解析首段 YAML frontmatter；失败返回 ({}, 全文)。"""
    md = md.lstrip("\ufeff")
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    raw = parts[1].strip()
    body = parts[2]
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            out[k] = v.strip('"').strip("'") if v else ""
    return out, body


def _macro_frag_filter(cur) -> tuple[str, set[str]]:
    """与 sync_obsidian_v4.fetch_all_data 一致的宏观导出 WHERE 片段（仅附加条件）。"""
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'macro_storylines'
        """
    )
    macro_cols = {row["column_name"] for row in cur.fetchall()}
    has_macro_status = "status" in macro_cols
    has_description = "description" in macro_cols
    if has_macro_status:
        frag = " AND COALESCE(status, 'active') != 'fragment' "
    elif has_description:
        frag = " AND (description IS NULL OR description NOT LIKE '【零碎线索】%') "
    else:
        frag = ""
    return frag, macro_cols


def _micro_cols(cur) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'micro_events'
        """
    )
    return {row["column_name"] for row in cur.fetchall()}


@dataclass
class CheckResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


def _add(r: CheckResult, msg: str, *, severity: str = "error") -> None:
    if severity == "error":
        r.ok = False
        r.errors.append(msg)
    elif severity == "warn":
        r.warnings.append(msg)
    else:
        r.hints.append(msg)


def db_global_audit(cur) -> dict[str, Any]:
    """全库轻量统计：悬空映射、重复 event_id、map 与 macro_storyline_id 不一致等。"""
    out: dict[str, Any] = {}
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM storyline_micro_map sm
        WHERE NOT EXISTS (
            SELECT 1 FROM macro_storylines m WHERE m.storyline_id = sm.storyline_id
        )
        OR NOT EXISTS (
            SELECT 1 FROM micro_events e WHERE e.event_id = sm.event_id
        )
        """
    )
    out["map_orphan_rows"] = int(cur.fetchone()["n"])

    cur.execute(
        """
        SELECT event_id, COUNT(*) AS c
        FROM storyline_micro_map
        GROUP BY event_id
        HAVING COUNT(*) > 1
        LIMIT 50
        """
    )
    dup = cur.fetchall()
    out["map_duplicate_event_ids_sample"] = [dict(x) for x in dup]
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM (
            SELECT event_id FROM storyline_micro_map GROUP BY event_id HAVING COUNT(*) > 1
        ) t
        """
    )
    out["map_duplicate_event_id_count"] = int(cur.fetchone()["n"])

    mcols = _micro_cols(cur)
    if "macro_storyline_id" in mcols:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM micro_events me
            JOIN (
                SELECT DISTINCT ON (event_id) event_id, storyline_id
                FROM storyline_micro_map
                ORDER BY event_id, storyline_id
            ) sm ON sm.event_id = me.event_id
            WHERE me.macro_storyline_id IS DISTINCT FROM sm.storyline_id
            """
        )
        out["micro_column_vs_map_mismatch"] = int(cur.fetchone()["n"])

        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM micro_events me
            WHERE me.macro_storyline_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM storyline_micro_map sm WHERE sm.event_id = me.event_id
              )
            """
        )
        out["micro_column_without_map_row"] = int(cur.fetchone()["n"])
    else:
        out["micro_column_vs_map_mismatch"] = None
        out["micro_column_without_map_row"] = None

    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM micro_event_members mem
        WHERE NOT EXISTS (SELECT 1 FROM micro_events e WHERE e.event_id = mem.event_id)
        """
    )
    out["members_orphan_event_id"] = int(cur.fetchone()["n"])

    cur.execute(
        """
        SELECT m.title, COUNT(*) AS c
        FROM macro_storylines m
        WHERE m.title IS NOT NULL
        GROUP BY m.title
        HAVING COUNT(*) > 1
        LIMIT 20
        """
    )
    out["macro_duplicate_title_sample"] = [dict(x) for x in cur.fetchall()]

    return out


def _sql_stage6_exported_news_pool() -> str:
    """
    与 sync_obsidian_v4.fetch_all_data 中拉取「将写入 Articles」的新闻来源一致（三段 UNION ALL）。
    审计时只应对这些 news_id 期望存在 Articles/{id}.md。
    """
    return """
    SELECT n.id AS news_id, n.title, m.event_id AS export_event_id
    FROM micro_event_members m
    JOIN news n ON n.id = m.news_id

    UNION ALL

    SELECT n.id AS news_id, n.title, nas.micro_event_id AS export_event_id
    FROM news n
    LEFT JOIN news_assignment nas ON nas.news_id = n.id
    WHERE nas.micro_event_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM micro_event_members mm WHERE mm.news_id = n.id
      )

    UNION ALL

    SELECT n.id AS news_id, n.title, me.event_id AS export_event_id
    FROM micro_events me
    JOIN news n
      ON n.title = me.title
     AND DATE(n.pub_time) = DATE(me.start_date)
    LEFT JOIN news_assignment nas ON nas.news_id = n.id
    WHERE me.article_count >= 2
      AND nas.micro_event_id IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM micro_event_members mm
          WHERE mm.event_id = me.event_id AND mm.news_id = n.id
      )
    """


def _has_table(cur, name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (name,),
    )
    return cur.fetchone() is not None


def db_row_sample_audit(
    cur,
    *,
    frag: str,
    n_macro: int,
    n_micro: int,
    n_news: int,
) -> dict[str, Any]:
    """
    在库内对随机抽中的行做关系校验（不读 Obsidian）。
    用于验证「根因是否在流水线」：若此处失败，说明问题在 PG 数据本身，而非导出笔记。
    """
    mcols = _micro_cols(cur)
    has_ms_col = "macro_storyline_id" in mcols
    has_mem = _has_table(cur, "micro_event_members")

    out: dict[str, Any] = {"macros": [], "micros": [], "articles": []}

    cur.execute(
        f"""
        SELECT storyline_id, title, micro_event_count, article_count
        FROM macro_storylines
        WHERE title IS NOT NULL {frag}
        ORDER BY RANDOM() LIMIT %s
        """,
        (max(n_macro, 1),),
    )
    for row in cur.fetchall():
        sid = int(row["storyline_id"])
        item: dict[str, Any] = {"storyline_id": sid, "ok": True, "errors": [], "warnings": [], "facts": {}}
        cur.execute(
            "SELECT COUNT(*) AS n FROM storyline_micro_map WHERE storyline_id = %s",
            (sid,),
        )
        n_map = int(cur.fetchone()["n"])
        item["facts"]["map_child_rows"] = n_map
        me_stored = int(row.get("micro_event_count") or 0)
        if me_stored != n_map:
            item["warnings"].append(
                f"micro_event_count={me_stored} 与 storyline_micro_map 中该宏观子行数={n_map} 不一致"
            )
        cur.execute(
            """
            SELECT sm.event_id
            FROM storyline_micro_map sm
            WHERE sm.storyline_id = %s
            ORDER BY sm.event_id
            LIMIT 20
            """,
            (sid,),
        )
        eids = [int(x["event_id"]) for x in cur.fetchall()]
        for eid in eids[:5]:
            cur.execute(
                "SELECT 1 FROM micro_events WHERE event_id = %s",
                (eid,),
            )
            if cur.fetchone() is None:
                item["ok"] = False
                item["errors"].append(f"map 中 event_id={eid} 在 micro_events 中不存在")
        out["macros"].append(item)

    micro_sel = "event_id, title, article_count"
    if has_ms_col:
        micro_sel += ", macro_storyline_id"
    cur.execute(
        f"""
        SELECT {micro_sel}
        FROM micro_events
        WHERE title IS NOT NULL
        ORDER BY RANDOM() LIMIT %s
        """,
        (max(n_micro, 1),),
    )
    for row in cur.fetchall():
        eid = int(row["event_id"])
        item = {"event_id": eid, "ok": True, "errors": [], "warnings": [], "facts": {}}
        cur.execute(
            "SELECT storyline_id FROM storyline_micro_map WHERE event_id = %s",
            (eid,),
        )
        map_rows = cur.fetchall()
        map_sids = [int(x["storyline_id"]) for x in map_rows]
        item["facts"]["map_storyline_ids"] = map_sids
        if len(map_sids) > 1:
            item["ok"] = False
            item["errors"].append(f"storyline_micro_map 中同一 event_id 对应多条宏观: {map_sids}")
        if has_ms_col:
            col = row.get("macro_storyline_id")
            if col is not None and map_sids:
                cs = int(col)
                if cs not in map_sids:
                    item["ok"] = False
                    item["errors"].append(
                        f"micro_events.macro_storyline_id={cs} 不在 map 的 {map_sids} 中"
                    )
            if col is None and map_sids:
                item["warnings"].append("macro_storyline_id 为空但 map 中有归属")
        ac = int(row.get("article_count") or 0)
        if has_mem:
            cur.execute(
                "SELECT COUNT(*) AS n FROM micro_event_members WHERE event_id = %s",
                (eid,),
            )
            mc = int(cur.fetchone()["n"])
            item["facts"]["member_rows"] = mc
            if mc > ac:
                item["ok"] = False
                item["errors"].append(f"micro_event_members 条数={mc} > article_count={ac}")
            elif ac > mc and mc > 0:
                item["warnings"].append(
                    f"article_count={ac} 大于成员表行数={mc}（可能仍有新闻未写入 members）"
                )
        out["micros"].append(item)

    cur.execute(
        """
        SELECT n.id AS news_id, n.title, nas.micro_event_id
        FROM news n
        LEFT JOIN news_assignment nas ON nas.news_id = n.id
        WHERE n.title IS NOT NULL
        ORDER BY RANDOM() LIMIT %s
        """,
        (max(n_news, 1),),
    )
    for row in cur.fetchall():
        nid = int(row["news_id"])
        item = {"news_id": nid, "ok": True, "errors": [], "warnings": [], "facts": {}}
        meid = row.get("micro_event_id")
        if meid is not None:
            eid = int(meid)
            cur.execute(
                "SELECT 1 FROM micro_events WHERE event_id = %s",
                (eid,),
            )
            if cur.fetchone() is None:
                item["ok"] = False
                item["errors"].append(f"news_assignment.micro_event_id={eid} 在 micro_events 中不存在")
            if has_mem:
                cur.execute(
                    """
                    SELECT 1 FROM micro_event_members
                    WHERE event_id = %s AND news_id = %s
                    """,
                    (eid, nid),
                )
                item["facts"]["in_micro_event_members"] = cur.fetchone() is not None
        out["articles"].append(item)

    return out


def _vault_micro_prefix(vault: Path) -> tuple[Path, str]:
    from agentic_rag.sync_obsidian_v4 import _micro_dir_and_prefix

    return _micro_dir_and_prefix(vault)


def audit_sample_macro(
    vault: Path,
    row: dict,
) -> CheckResult:
    r = CheckResult()
    sid = int(row["storyline_id"])
    title = (row.get("title") or "").strip()
    path = vault / "MacroEvents" / f"{_safe_filename(title)}.md"
    if not path.is_file():
        _add(
            r,
            f"宏观 storyline_id={sid} 期望文件不存在: {path}（若该条被 fragment 过滤则属预期）",
            severity="warn",
        )
        return r
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, _ = _parse_frontmatter(text)
    if str(fm.get("storyline_id", "")).strip() != str(sid):
        _add(r, f"frontmatter storyline_id 与 DB 不一致: 文件={fm.get('storyline_id')} DB={sid}")
    tfile = (fm.get("title") or "").strip()
    if title and tfile and tfile != title.replace('"', "'"):
        _add(r, f"frontmatter title 与 DB 不完全一致（可能仅引号差异） DB={title[:40]!r} file={tfile[:40]!r}", severity="warn")
    return r


def audit_sample_micro(
    vault: Path,
    row: dict,
    micro_to_macro: dict[int, int],
    macro_titles: dict[int, str],
    min_art: int,
    stub_enabled: bool,
) -> CheckResult:
    r = CheckResult()
    eid = int(row["event_id"])
    title = (row.get("title") or "").strip()
    ac = int(row.get("article_count") or 0)
    micro_dir, _ = _vault_micro_prefix(vault)
    path = micro_dir / f"E{eid}_{_safe_filename(title)}.md"

    sid = micro_to_macro.get(eid)
    # 与 Stage 6 一致：article_count < 阈值时不写 Micro 笔记（除非开占位且已归属某条导出中的宏观）
    expect_file = ac >= min_art or (stub_enabled and sid is not None and sid in macro_titles)
    if not expect_file:
        _add(
            r,
            f"微 event_id={eid} article_count={ac} < {min_art}：不期望独立 Micro 文件（与 Stage 6 一致；"
            f"有宏观归属时在宏观页为纯文本子项）",
            severity="hint",
        )
        return r

    if not path.is_file():
        _add(r, f"微 event_id={eid} 期望文件缺失: {path}")
        return r
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    if str(fm.get("event_id", "")).strip() != str(eid):
        _add(r, f"frontmatter event_id 不一致: 文件={fm.get('event_id')} DB={eid}")
    if sid and sid in macro_titles:
        mt = _safe_filename(macro_titles[sid])
        if f"MacroEvents/{mt}" not in text and "所属大事件" not in body[:800]:
            _add(r, f"微 event_id={eid} 正文中缺少指向宏观 MacroEvents/{mt} 的链接", severity="warn")
    return r


def audit_sample_article(
    vault: Path,
    news_id: int,
    title_db: Optional[str],
    event_id: Optional[int],
    micro_title: str,
    *,
    micro_article_count: Optional[int] = None,
    min_micro_articles: int = 2,
    micro_stub_links: bool = False,
    micro_has_macro_parent: bool = False,
) -> CheckResult:
    r = CheckResult()
    path = vault / "Articles" / f"{news_id}.md"
    if not path.is_file():
        _add(r, f"文章 news_id={news_id} 文件不存在: {path}")
        return r
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, _ = _parse_frontmatter(text)
    if title_db and fm.get("title") and title_db.replace('"', "'")[:80] not in str(fm.get("title", "")):
        if _safe_filename(title_db)[:20] not in str(fm.get("title", "")):
            _add(r, f"文章 {news_id} frontmatter title 与 DB 差异较大", severity="warn")
    if event_id is None:
        return r

    _, rel = _vault_micro_prefix(vault)
    stub = f"[[{rel}/E{event_id}_"

    # 未达 Micro 独立笔记阈值时，Stage 6 写「无独立 Micro 页」说明，不一定含 E{id}_ 链接或数字 id（标题作说明时）
    below_threshold = (
        micro_article_count is not None and micro_article_count < min_micro_articles
    )
    stub_note_expected = (
        below_threshold
        and micro_stub_links
        and micro_has_macro_parent
    )
    if below_threshold and not stub_note_expected:
        if (
            stub in text
            or "无独立 Micro" in text
            or "单篇·无聚类" in text
            or str(event_id) in text
        ):
            return r
        _add(
            r,
            f"文章 news_id={news_id}：微 event_id={event_id} 未达独立笔记阈值，"
            f"正文中未找到预期说明（无独立 Micro / 无聚类 / 微簇 id）",
            severity="warn",
        )
        return r

    # 应有独立 Micro 页或占位笔记：检查 Wikilink
    if stub not in text and str(event_id) not in text:
        _add(
            r,
            f"文章 news_id={news_id} 未检测到指向微簇 [[{rel}/E{event_id}_…]] 或正文中 event_id",
            severity="warn",
        )
    return r


def repair_storyline_consistency(*, dry_run: bool) -> dict[str, Any]:
    """以 storyline_micro_map 为权威同步 macro_storyline_id；再为「仅有列无 map 行」补 INSERT。"""
    from datetime import datetime, timezone

    from agentic_rag.db.macro_shared import _pg_write_executor, _table_columns

    ex = _pg_write_executor()
    conn = ex.get_write_conn()
    stats: dict[str, Any] = {"dry_run": dry_run}
    try:
        cur = conn.cursor()
        mcols = _micro_cols(cur)
        if "macro_storyline_id" not in mcols:
            stats["skipped"] = "micro_events 无 macro_storyline_id 列"
            conn.rollback()
            conn.close()
            return stats

        cur.execute(
            """
            UPDATE micro_events me
            SET macro_storyline_id = s.storyline_id
            FROM (
                SELECT DISTINCT ON (event_id) event_id, storyline_id
                FROM storyline_micro_map
                ORDER BY event_id, storyline_id
            ) s
            WHERE me.event_id = s.event_id
              AND (me.macro_storyline_id IS DISTINCT FROM s.storyline_id)
            """
        )
        stats["update_micro_from_map"] = cur.rowcount

        c_map = _table_columns(cur, "storyline_micro_map")
        insert_cols = ["storyline_id", "event_id"]
        placeholders = ["%s", "%s"]
        if "membership_score" in c_map:
            insert_cols.append("membership_score")
            placeholders.append("%s")
        now = datetime.now(timezone.utc)
        if "created_at" in c_map:
            insert_cols.append("created_at")
            placeholders.append("%s")
        if "updated_at" in c_map:
            insert_cols.append("updated_at")
            placeholders.append("%s")

        cur.execute(
            """
            SELECT me.macro_storyline_id, me.event_id
            FROM micro_events me
            WHERE me.macro_storyline_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM storyline_micro_map sm WHERE sm.event_id = me.event_id
              )
            """
        )
        rows = cur.fetchall()
        ins = 0
        sql = (
            f"INSERT INTO storyline_micro_map ({', '.join(insert_cols)}) "
            f"VALUES ({', '.join(placeholders)})"
        )
        for storyline_id, event_id in rows:
            vals: list[Any] = [storyline_id, event_id]
            if "membership_score" in c_map:
                vals.append(1.0)
            if "created_at" in c_map:
                vals.append(now)
            if "updated_at" in c_map:
                vals.append(now)
            try:
                cur.execute(sql, tuple(vals))
                ins += cur.rowcount
            except Exception as e:
                stats.setdefault("insert_errors", []).append(
                    f"event_id={event_id} storyline_id={storyline_id}: {e}"
                )
        stats["insert_map_from_micro"] = ins

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Obsidian + PG 一致性抽检")
    ap.add_argument("--vault", type=str, default=str(DEFAULT_VAULT), help="Obsidian 库根目录")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--macros", type=int, default=5, help="抽检宏观笔记数")
    ap.add_argument("--micros", type=int, default=8, help="抽检微簇笔记数")
    ap.add_argument("--articles", type=int, default=10, help="抽检文章笔记数")
    ap.add_argument("--json-out", type=str, default="", help="将完整报告写入 JSON 文件")
    ap.add_argument(
        "--repair",
        action="store_true",
        help="尝试修复 map 与 micro_events.macro_storyline_id 不一致（需写库账号）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="与 --repair 联用：只统计将影响的行数，不提交",
    )
    ap.add_argument(
        "--skip-vault",
        action="store_true",
        help="跳过 Obsidian 文件抽检，仅执行 PostgreSQL 全局 + 行级抽检",
    )
    args = ap.parse_args()
    vault = Path(args.vault).expanduser().resolve()

    from agentic_rag.sync_obsidian_v4 import (
        _obsidian_micro_min_articles,
        _obsidian_micro_stub_links,
    )

    min_art = _obsidian_micro_min_articles()
    stub_on = _obsidian_micro_stub_links()

    if args.seed is not None:
        random.seed(args.seed)

    report: dict[str, Any] = {
        "vault": str(vault),
        "seed": args.seed,
        "min_micro_articles": min_art,
        "micro_stub_links": stub_on,
        "skip_vault": args.skip_vault,
    }

    conn = _pg_connect_read()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        frag, _ = _macro_frag_filter(cur)
        micro_cols_main = _micro_cols(cur)
        report["db_global"] = db_global_audit(cur)
        report["db_row_samples"] = db_row_sample_audit(
            cur,
            frag=frag,
            n_macro=args.macros,
            n_micro=args.micros,
            n_news=args.articles,
        )

        samples: dict[str, Any] = {"macros": [], "micros": [], "articles": []}

        if not args.skip_vault:
            cur.execute(
                f"""
                SELECT storyline_id, title FROM macro_storylines
                WHERE title IS NOT NULL {frag}
                ORDER BY RANDOM() LIMIT %s
                """,
                (max(args.macros, 1),),
            )
            sample_macros = cur.fetchall()

            _msel = "event_id, title, article_count"
            if "macro_storyline_id" in micro_cols_main:
                _msel += ", macro_storyline_id"
            cur.execute(
                f"""
                SELECT {_msel}
                FROM micro_events
                WHERE title IS NOT NULL
                ORDER BY RANDOM() LIMIT %s
                """,
                (max(args.micros, 1),),
            )
            sample_micros = cur.fetchall()

            cur.execute(
                f"""
                WITH pool AS (
                    {_sql_stage6_exported_news_pool()}
                ),
                dedup AS (
                    SELECT DISTINCT ON (news_id) news_id, title, export_event_id
                    FROM pool
                    WHERE title IS NOT NULL
                    ORDER BY news_id, RANDOM()
                )
                SELECT news_id, title, export_event_id AS event_id
                FROM dedup
                ORDER BY RANDOM()
                LIMIT %s
                """,
                (max(args.articles, 1),),
            )
            sample_news = cur.fetchall()

            cur.execute(
                f"""
                SELECT storyline_id, title FROM macro_storylines
                WHERE title IS NOT NULL {frag}
                """
            )
            export_macros = cur.fetchall()
            macro_ids_keep = {int(m["storyline_id"]) for m in export_macros}
            macro_titles = {int(m["storyline_id"]): (m.get("title") or "") for m in export_macros}

            cur.execute("SELECT storyline_id, event_id FROM storyline_micro_map")
            mapping = cur.fetchall()
            micro_to_macro: dict[int, int] = {}
            for m in mapping:
                eid, sid = int(m["event_id"]), int(m["storyline_id"])
                if sid not in macro_ids_keep:
                    continue
                if eid in micro_to_macro and micro_to_macro[eid] != sid:
                    continue
                if eid not in micro_to_macro:
                    micro_to_macro[eid] = sid
            for mu in sample_micros:
                eid = int(mu["event_id"])
                raw = mu.get("macro_storyline_id")
                if raw is None:
                    continue
                sid = int(raw)
                if sid not in macro_ids_keep:
                    continue
                if eid not in micro_to_macro:
                    micro_to_macro[eid] = sid

            for row in sample_macros:
                sid = int(row["storyline_id"])
                ar = audit_sample_macro(vault, row)
                samples["macros"].append(
                    {
                        "storyline_id": sid,
                        "title": row.get("title"),
                        "ok": ar.ok,
                        "errors": ar.errors,
                        "warnings": ar.warnings,
                        "hints": ar.hints,
                    }
                )

            for row in sample_micros:
                eid = int(row["event_id"])
                ar = audit_sample_micro(
                    vault, row, micro_to_macro, macro_titles, min_art, stub_on
                )
                samples["micros"].append(
                    {
                        "event_id": eid,
                        "title": row.get("title"),
                        "article_count": row.get("article_count"),
                        "ok": ar.ok,
                        "errors": ar.errors,
                        "warnings": ar.warnings,
                        "hints": ar.hints,
                    }
                )

            for row in sample_news:
                nid = int(row["news_id"])
                eid = row.get("event_id")
                mt = ""
                micro_ac: Optional[int] = None
                if eid is not None:
                    cur.execute(
                        "SELECT title, article_count FROM micro_events WHERE event_id = %s",
                        (int(eid),),
                    )
                    one = cur.fetchone()
                    mt = (one or {}).get("title") or ""
                    if one and one.get("article_count") is not None:
                        micro_ac = int(one["article_count"])
                has_macro = (
                    int(eid) in micro_to_macro if eid is not None else False
                )
                ar = audit_sample_article(
                    vault,
                    nid,
                    row.get("title"),
                    int(eid) if eid is not None else None,
                    mt,
                    micro_article_count=micro_ac,
                    min_micro_articles=min_art,
                    micro_stub_links=stub_on,
                    micro_has_macro_parent=has_macro,
                )
                samples["articles"].append(
                    {
                        "news_id": nid,
                        "ok": ar.ok,
                        "errors": ar.errors,
                        "warnings": ar.warnings,
                        "hints": ar.hints,
                    }
                )

        report["samples"] = samples

        # 终端摘要
        g = report["db_global"]
        print("=== PostgreSQL 全局一致性（轻量）===")
        print(f"  storyline_micro_map 悬空行: {g['map_orphan_rows']}")
        print(f"  storyline_micro_map 重复 event_id 种类: {g['map_duplicate_event_id_count']}")
        if g.get("micro_column_vs_map_mismatch") is not None:
            print(f"  micro_events.macro_storyline_id 与 map 不一致行数: {g['micro_column_vs_map_mismatch']}")
            print(f"  micro 有 macro_storyline_id 但 map 无 event 行: {g['micro_column_without_map_row']}")
        print(f"  micro_event_members 悬空 event_id: {g['members_orphan_event_id']}")
        if g["macro_duplicate_title_sample"]:
            print(f"  警告: macro_storylines 标题碰撞样例: {g['macro_duplicate_title_sample'][:3]}")

        dbs = report.get("db_row_samples") or {}
        print("\n=== PostgreSQL 随机行级抽检（仅数据库，不读 Obsidian）===")
        for name, key in [("宏观行", "macros"), ("微簇行", "micros"), ("新闻行", "articles")]:
            items = dbs.get(key) or []
            bad = sum(1 for x in items if not x.get("ok", True))
            print(f"  {name}: 抽检 {len(items)} 条，不通过 {bad}")
            for x in items:
                if x.get("errors") or x.get("warnings"):
                    print(f"    — {x}")

        if not args.skip_vault:
            print("\n=== 随机抽检（Obsidian 文件，需已跑 Stage 6）===")
            for name, key in [("宏观", "macros"), ("微簇", "micros"), ("文章", "articles")]:
                items = samples[key]
                bad = sum(1 for x in items if x.get("errors"))
                print(f"  {name}: 抽检 {len(items)} 条，硬错误 {bad}")
                for x in items:
                    if x.get("errors") or x.get("warnings"):
                        print(f"    — {x}")
            hints = [x for x in samples["micros"] if x.get("hints")]
            if hints:
                print("  提示（非错误）: 未生成微笔记可能因 article_count 低于阈值且无占位策略")
        else:
            print("\n（已 --skip-vault，跳过磁盘上的 Obsidian 校验）")

    finally:
        conn.close()

    if args.repair:
        print("\n=== 修复 map ↔ macro_storyline_id（以 map 为准补列，再补 map 行）===")
        st = repair_storyline_consistency(dry_run=args.dry_run)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        report["repair"] = st

    if args.json_out:
        outp = Path(args.json_out).expanduser().resolve()
        outp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[audit] 完整报告已写入 {outp}")

    # 退出码：PG 行级抽检或 Obsidian 抽检存在失败时 1
    dbr = report.get("db_row_samples") or {}
    any_err = any(
        not x.get("ok", True)
        for part in dbr.values()
        for x in (part if isinstance(part, list) else [])
    )
    if not any_err:
        any_err = any(
            not x.get("ok", True)
            for part in report.get("samples", {}).values()
            for x in (part if isinstance(part, list) else [])
        )
    if any_err:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
