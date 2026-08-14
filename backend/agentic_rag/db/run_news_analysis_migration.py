#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一次性执行：
  1) news_analysis：NLP 列从 news 迁出并 DROP
  2) news_assignment：entity_hash、micro_event_id 等计算/归属列从 news 迁出并 DROP

用法（在仓库根目录、已激活 venv）：
  python -m agentic_rag.db.run_news_analysis_migration

依赖 agentic_rag/.env 或根目录 .env 中的 PG_* / PG_WRITE_*。

安全约定：本脚本**只连接名为 postgres 的数据库**（不读取 PG_DATABASE / PG_DBNAME），
避免在误配环境变量时改到同一实例上的其它库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from agentic_rag.db.ensure_news_pk import ensure_news_id_referenced_unique
from agentic_rag.db.news_assignment_schema import LEGACY_NEWS_COLUMNS

# 爬虫新库固定库名；迁移脚本仅此库生效，忽略 PG_DATABASE。
MIGRATION_DBNAME = "postgres"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent
        load_dotenv(root / "agentic_rag" / ".env", override=False)
        load_dotenv(root / ".env", override=True)
    except ImportError:
        pass


def _news_has_column(cur, table: str, col: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, col),
    )
    return cur.fetchone() is not None


def main() -> int:
    _load_env()
    try:
        import psycopg2
        from psycopg2 import sql as psql
    except ImportError:
        print("ERROR: pip install psycopg2-binary", file=sys.stderr)
        return 1

    host = os.getenv("PG_HOST", "127.0.0.1")
    port = int(os.getenv("PG_PORT", "5432"))
    user = os.getenv("PG_WRITE_USER") or os.getenv("PG_USER", "postgres")
    password = os.getenv("PG_WRITE_PASSWORD") or os.getenv("PG_PASSWORD", "")

    if not password:
        print(
            "[Migrate] WARNING: PG_WRITE_PASSWORD / PG_PASSWORD 未设置；"
            "若数据库要求密码将连接失败。请在 agentic_rag/.env 或仓库根 .env 中配置。",
            file=sys.stderr,
        )

    print(
        f"[Migrate] Connecting {user}@{host}:{port}/{MIGRATION_DBNAME} "
        f"(仅此库；忽略 PG_DATABASE) …"
    )

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=MIGRATION_DBNAME,
            user=user,
            password=password,
            connect_timeout=30,
        )
    except Exception as e:
        print(f"ERROR: cannot connect: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    try:
        conn.autocommit = True
        cur = conn.cursor()
        ensure_news_id_referenced_unique(cur, log=print)
        cur.close()
    except Exception as e:
        conn.close()
        print(f"ERROR: news.id 主键/唯一准备失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # ---------- Phase 1: news_analysis ----------
    table_na = "news_analysis"
    ddl_na = f"""
    CREATE TABLE IF NOT EXISTS {table_na} (
        news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
        is_china_related BOOLEAN,
        china_related_index DOUBLE PRECISION,
        entities JSONB,
        sentiment_analysis TEXT,
        topic_classification TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_news_analysis_china
    ON {table_na} (is_china_related)
    WHERE is_china_related IS NOT NULL;
    """

    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(ddl_na)
        cur.close()
        print(f"[Migrate] Table {table_na!r} ensured.")
    except Exception as e:
        conn.close()
        print(f"ERROR: news_analysis DDL failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    conn.autocommit = False
    try:
        cur = conn.cursor()
        has_nlp = _news_has_column(cur, "news", "sentiment_analysis")
        if has_nlp:
            print("[Migrate] Copying rows from news → news_analysis …")
            cur.execute(
                f"""
                INSERT INTO {table_na} (
                    news_id, is_china_related, china_related_index, entities,
                    sentiment_analysis, topic_classification, updated_at
                )
                SELECT
                    n.id,
                    n.is_china_related,
                    n.china_related_index,
                    CASE
                        WHEN n.entities IS NULL THEN NULL
                        ELSE n.entities::jsonb
                    END,
                    n.sentiment_analysis,
                    n.topic_classification,
                    now()
                FROM news n
                WHERE n.is_china_related IS NOT NULL
                   OR n.sentiment_analysis IS NOT NULL
                   OR n.topic_classification IS NOT NULL
                   OR n.entities IS NOT NULL
                   OR n.china_related_index IS NOT NULL
                ON CONFLICT (news_id) DO UPDATE SET
                    is_china_related = EXCLUDED.is_china_related,
                    china_related_index = EXCLUDED.china_related_index,
                    entities = EXCLUDED.entities,
                    sentiment_analysis = EXCLUDED.sentiment_analysis,
                    topic_classification = EXCLUDED.topic_classification,
                    updated_at = now()
                """
            )
            print(f"[Migrate] news_analysis insert/upsert done ({cur.rowcount} rows affected).")
            for col in (
                "is_china_related",
                "china_related_index",
                "entities",
                "sentiment_analysis",
                "topic_classification",
            ):
                cur.execute(
                    psql.SQL("ALTER TABLE news DROP COLUMN IF EXISTS {}").format(
                        psql.Identifier(col)
                    )
                )
                print(f"[Migrate] Dropped news.{col} (if existed).")
        else:
            print("[Migrate] No legacy NLP columns on news (sentiment_analysis missing). news_analysis copy/DROP skipped.")

        conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"ERROR: news_analysis migration failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # ---------- Phase 2: news_assignment ----------
    table_nas = "news_assignment"
    ddl_nas = f"""
    CREATE TABLE IF NOT EXISTS {table_nas} (
        news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
        entity_hash TEXT,
        micro_event_id BIGINT,
        assign_score DOUBLE PRECISION,
        assigned_at TIMESTAMPTZ,
        embedding_version TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_{table_nas}_micro_event
    ON {table_nas} (micro_event_id)
    WHERE micro_event_id IS NOT NULL;
    """

    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(ddl_nas)
        cur.close()
        print(f"[Migrate] Table {table_nas!r} ensured.")
    except Exception as e:
        conn.close()
        print(f"ERROR: news_assignment DDL failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    conn.autocommit = False
    try:
        cur = conn.cursor()
        present = [c for c in LEGACY_NEWS_COLUMNS if _news_has_column(cur, "news", c)]
        if not present:
            conn.commit()
            cur.close()
            print(
                "[Migrate] No legacy assignment columns on news "
                f"({', '.join(LEGACY_NEWS_COLUMNS)} all missing). news_assignment copy/DROP skipped."
            )
            print("[Migrate] SUCCESS: all phases finished.")
            return 0

        cast = {
            "entity_hash": "text",
            "micro_event_id": "bigint",
            "assign_score": "double precision",
            "assigned_at": "timestamptz",
            "embedding_version": "text",
        }
        parts = ["n.id"]
        for c in LEGACY_NEWS_COLUMNS:
            parts.append(f"n.{c}" if c in present else f"NULL::{cast[c]}")
        parts.append("now()")
        where_or = " OR ".join(f"n.{c} IS NOT NULL" for c in present)

        print("[Migrate] Copying rows from news → news_assignment …")
        cur.execute(
            f"""
            INSERT INTO {table_nas} (
                news_id, entity_hash, micro_event_id, assign_score,
                assigned_at, embedding_version, updated_at
            )
            SELECT {", ".join(parts)}
            FROM news n
            WHERE {where_or}
            ON CONFLICT (news_id) DO UPDATE SET
                entity_hash = EXCLUDED.entity_hash,
                micro_event_id = EXCLUDED.micro_event_id,
                assign_score = EXCLUDED.assign_score,
                assigned_at = EXCLUDED.assigned_at,
                embedding_version = EXCLUDED.embedding_version,
                updated_at = now()
            """
        )
        print(f"[Migrate] news_assignment insert/upsert done ({cur.rowcount} rows affected).")

        for col in present:
            cur.execute(
                psql.SQL("ALTER TABLE news DROP COLUMN IF EXISTS {}").format(psql.Identifier(col))
            )
            print(f"[Migrate] Dropped news.{col} (if existed).")

        conn.commit()
        cur.close()
        print("[Migrate] SUCCESS: news_assignment populated; legacy assignment columns removed from news.")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: news_assignment migration failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
