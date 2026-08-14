"""
新闻侧「计算/归属」字段表 `news_assignment`：与 `news` 一行对一行，通过 news_id 关联。

从 `news` 迁出的列：entity_hash、micro_event_id、assign_score、assigned_at、embedding_version。

环境变量：
  NEWS_ASSIGNMENT_AUTO_SCHEMA=0  — 跳过建表
  NEWS_ASSIGNMENT_MIGRATE_LEGACY=0 — 跳过从 news 旧列的一次性拷贝（默认 1）
"""
from __future__ import annotations

import os

TABLE_NAME = "news_assignment"

# 若仍在 news 上则一次性 INSERT → DROP
LEGACY_NEWS_COLUMNS = (
    "entity_hash",
    "micro_event_id",
    "assign_score",
    "assigned_at",
    "embedding_version",
)


def ensure_news_assignment_table() -> None:
    """创建 news_assignment（若不存在）。需写库账号。"""
    if os.getenv("NEWS_ASSIGNMENT_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    stmts = [
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
            entity_hash TEXT,
            micro_event_id BIGINT,
            assign_score DOUBLE PRECISION,
            assigned_at TIMESTAMPTZ,
            embedding_version TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_micro_event
        ON {TABLE_NAME} (micro_event_id)
        WHERE micro_event_id IS NOT NULL
        """,
    ]
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "127.0.0.1"),
            port=int(os.getenv("PG_PORT", "5432")),
            dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
            user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
            password=os.getenv("PG_WRITE_PASSWORD", os.getenv("PG_PASSWORD", "")),
            connect_timeout=15,
        )
        conn.autocommit = True
        cur = conn.cursor()
        for s in stmts:
            cur.execute(s)
        cur.close()
    except Exception as e:
        print(f"[Schema] news_assignment 创建跳过: {type(e).__name__}: {e}")
        return
    finally:
        if conn is not None:
            conn.close()

    if os.getenv("NEWS_ASSIGNMENT_MIGRATE_LEGACY", "1").strip().lower() in ("0", "false", "no"):
        return
    _migrate_legacy_news_columns_if_present()


def _news_has_column(cur, col: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'news' AND column_name = %s
        """,
        (col,),
    )
    return cur.fetchone() is not None


def _migrate_legacy_news_columns_if_present() -> None:
    """若 news 上仍存在 micro_event_id 等列，则拷贝到 news_assignment 后删除旧列。"""
    try:
        import psycopg2
        from psycopg2 import sql as psql
    except ImportError:
        return
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST", "127.0.0.1"),
            port=int(os.getenv("PG_PORT", "5432")),
            dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
            user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
            password=os.getenv("PG_WRITE_PASSWORD", os.getenv("PG_PASSWORD", "")),
            connect_timeout=15,
        )
        cur = conn.cursor()
        present = [c for c in LEGACY_NEWS_COLUMNS if _news_has_column(cur, c)]
        if not present:
            cur.close()
            conn.close()
            return
        print("[Schema] 检测到 news 上仍存在归属/计算列，正在迁移至 news_assignment …")
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
        cur.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
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
        for col in present:
            cur.execute(
                psql.SQL("ALTER TABLE news DROP COLUMN IF EXISTS {}").format(psql.Identifier(col))
            )
        conn.commit()
        cur.close()
        print("[Schema] news 上旧归属列已删除，数据已写入 news_assignment。")
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(
            f"[Schema] news_assignment 自动迁移失败（可手工执行 SQL）: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        if conn is not None:
            conn.close()


def sql_join_news_assignment(
    news_alias: str = "n",
    nas_alias: str = "nas",
    join_type: str = "LEFT",
) -> str:
    return f"{join_type} JOIN {TABLE_NAME} {nas_alias} ON {nas_alias}.news_id = {news_alias}.id"
