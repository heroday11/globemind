#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
垂直分区：将 ``news_analysis.bge_embedding`` 迁至 ``news_embeddings`` 并 DROP 热表大列。

用法（仓库根、已配置 PG）::

  python -m agentic_rag.db.migrations.migrate_vertical_partition_embeddings --dry-run
  python -m agentic_rag.db.migrations.migrate_vertical_partition_embeddings --execute

步骤：
  1. CREATE TABLE IF NOT EXISTS news_embeddings (...)
  2. INSERT ... SELECT FROM news_analysis WHERE bge_embedding IS NOT NULL
  3. ALTER TABLE news_analysis DROP COLUMN IF EXISTS bge_embedding
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO / "agentic_rag" / ".env", override=False)
        load_dotenv(_REPO / ".env", override=True)
    except ImportError:
        pass


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
        user=os.getenv("PG_WRITE_USER") or os.getenv("PG_USER", "postgres"),
        password=os.getenv("PG_WRITE_PASSWORD") or os.getenv("PG_PASSWORD", ""),
        connect_timeout=30,
    )


def _column_exists(cur, table: str, col: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, col),
    )
    return cur.fetchone() is not None


def run_migration(*, execute: bool) -> int:
    _load_env()
    from agentic_rag.db.news_analysis_schema import EMBEDDINGS_TABLE_NAME, TABLE_NAME

    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (EMBEDDINGS_TABLE_NAME,),
        )
        emb_exists = cur.fetchone() is not None
        has_old_col = _column_exists(cur, TABLE_NAME, "bge_embedding")

        print(f"[migrate_vp] table {EMBEDDINGS_TABLE_NAME} exists={emb_exists}")
        print(f"[migrate_vp] {TABLE_NAME}.bge_embedding column exists={has_old_col}")

        stmts: list[str] = [
            f"""
            CREATE TABLE IF NOT EXISTS {EMBEDDINGS_TABLE_NAME} (
                news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
                bge_embedding JSONB
            )
            """,
        ]
        if has_old_col:
            stmts.append(
                f"""
                INSERT INTO {EMBEDDINGS_TABLE_NAME} (news_id, bge_embedding)
                SELECT news_id, bge_embedding FROM {TABLE_NAME}
                WHERE bge_embedding IS NOT NULL
                ON CONFLICT (news_id) DO NOTHING
                """
            )
            stmts.append(
                f"ALTER TABLE {TABLE_NAME} DROP COLUMN IF EXISTS bge_embedding"
            )

        for s in stmts:
            print("[migrate_vp] ---")
            print(s.strip()[:200] + ("..." if len(s) > 200 else ""))

        if not execute:
            print("[migrate_vp] dry-run：未执行")
            return 0

        # 必须先结束上面 SELECT 开启的隐式事务，否则切换 autocommit 会触发
        # ProgrammingError: set_session cannot be used inside a transaction
        conn.rollback()
        conn.autocommit = True
        for s in stmts:
            cur.execute(s)
        print("[migrate_vp] 已执行（autocommit 逐条提交）")
        return 0
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[migrate_vp] 失败: {type(e).__name__}: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="垂直分区：bge_embedding → news_embeddings")
    ap.add_argument("--execute", action="store_true", help="执行；省略则 dry-run")
    args = ap.parse_args()
    return run_migration(execute=bool(args.execute))


if __name__ == "__main__":
    raise SystemExit(main())
