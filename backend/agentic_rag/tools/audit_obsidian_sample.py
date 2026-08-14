#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抽样对比 news / news_analysis 与 obsidian_vault 三层笔记。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _safe(s: str, n: int = 120) -> str:
    t = (s or "")[:n]
    return t.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent
    os.chdir(root)
    try:
        from dotenv import load_dotenv

        load_dotenv(root / "agentic_rag" / ".env", override=False)
        load_dotenv(root / ".env", override=True)
    except ImportError:
        pass

    from config.settings import obsidian_vault_path

    vault = obsidian_vault_path()
    art_dir = vault / "Articles"
    micro_dir = vault / "MicroEvents"
    macro_dir = vault / "MacroEvents"

    arts = sorted(
        art_dir.glob("*.md"),
        key=lambda p: -int(p.stem) if p.stem.isdigit() else 0,
    )[:3]
    if not arts:
        print("Articles 目录无 .md")
        return 1
    ids = [int(p.stem) for p in arts]
    print("抽样 Article 文件:", [p.name for p in arts])
    print("news_id:", ids)

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "127.0.0.1"),
            port=int(os.getenv("PG_PORT", "5432")),
            dbname=os.getenv("PG_DATABASE", "postgres"),
            user=os.getenv("PG_USER", "postgres"),
            password=(os.getenv("PG_PASSWORD") or "").strip().strip('"'),
            connect_timeout=10,
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
    except Exception as e:
        print("数据库连接失败:", type(e).__name__, e)
        return 1

    for nid in ids:
        cur.execute(
            """
            SELECT n.id, n.title,
                   na.is_china_related, na.china_related_index,
                   na.sentiment_analysis, na.topic_classification
            FROM news n
            LEFT JOIN news_analysis na ON na.news_id = n.id
            WHERE n.id = %s
            """,
            (nid,),
        )
        row = cur.fetchone()
        print("\n=== PostgreSQL news_id", nid, "===")
        if not row:
            print("  news 表中无此 id")
            continue
        print("  DB title[:80]:", _safe(row["title"] or "", 80))
        print(
            "  analysis:",
            "index=" + str(row["china_related_index"]),
            "is_china=" + str(row["is_china_related"]),
        )

    mf = sorted(micro_dir.glob("E*.md"))[:1]
    if mf:
        text = mf[0].read_text(encoding="utf-8", errors="replace")
        m = re.search(r"event_id:\s*(\d+)", text)
        eid = int(m.group(1)) if m else None
        print("\n=== MicroEvents 文件", mf[0].name, "event_id=", eid)
        if eid:
            cur.execute(
                "SELECT event_id, title, article_count FROM micro_events WHERE event_id = %s",
                (eid,),
            )
            mr = cur.fetchone()
            print("  micro_events:", dict(mr) if mr else "不存在")

    maf = sorted(macro_dir.glob("*.md"))[:1]
    if maf:
        mt = maf[0].read_text(encoding="utf-8", errors="replace")
        m2 = re.search(r"storyline_id:\s*(\d+)", mt)
        sid = int(m2.group(1)) if m2 else None
        print("\n=== MacroEvents 文件", maf[0].name)
        print("  storyline_id:", sid)
        if sid:
            cur.execute(
                "SELECT storyline_id, title, article_count FROM macro_storylines WHERE storyline_id = %s",
                (sid,),
            )
            xr = cur.fetchone()
            print("  macro_storylines:", dict(xr) if xr else "不存在")

    # 首条 Article frontmatter vs DB
    p = arts[0]
    nid = int(p.stem)
    body = p.read_text(encoding="utf-8", errors="replace")
    fm = {}
    if yaml and body.startswith("---"):
        end = body.find("---", 3)
        if end > 0:
            try:
                fm = yaml.safe_load(body[3:end]) or {}
            except Exception:
                pass
    cur.execute(
        """
        SELECT na.china_related_index, na.is_china_related,
               na.sentiment_analysis, na.topic_classification
        FROM news_analysis na WHERE na.news_id = %s
        """,
        (nid,),
    )
    dba = cur.fetchone()
    print("\n=== 一致性（首条） news_id", nid)
    if dba and fm:
        ci_fm = fm.get("china_index")
        ci_db = dba["china_related_index"]
        if ci_fm is not None and ci_db is not None:
            ok = abs(float(ci_fm) - float(ci_db)) < 0.001
            print("  china_index 笔记 vs DB:", ci_fm, ci_db, "→", "一致" if ok else "不一致")
        print("  is_china 笔记 vs DB:", fm.get("is_china"), dba["is_china_related"])
    elif not dba:
        print("  DB 无 news_analysis 行，但存在 Article 文件 → 可能为旧导出或已删库行")

    cur.close()
    conn.close()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
