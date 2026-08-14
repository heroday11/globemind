"""story_hierarchy 表自动创建 — 存储故事线的层级树结构。"""
from __future__ import annotations

import os

from agentic_rag.db_runtime_config import require_database_password

_STORY_HIERARCHY_TABLES_DONE = False


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="globemind_news",
        user=os.getenv("PG_WRITE_USER", "postgres"),
        password=require_database_password(),
        connect_timeout=15,
    )


def ensure_story_hierarchy_tables() -> None:
    """创建 story_hierarchy 表（CREATE TABLE IF NOT EXISTS）。"""
    global _STORY_HIERARCHY_TABLES_DONE
    if _STORY_HIERARCHY_TABLES_DONE:
        return
    if os.getenv("STORY_HIERARCHY_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    conn = None
    try:
        conn = _connect()
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS story_hierarchy (
                id              SERIAL PRIMARY KEY,
                story_id        INTEGER NOT NULL REFERENCES micro_story_coref(id) ON DELETE CASCADE,
                parent_id       INTEGER REFERENCES story_hierarchy(id) ON DELETE CASCADE,
                level           INTEGER NOT NULL DEFAULT 0,
                title           TEXT,
                cluster_ids     TEXT[] NOT NULL DEFAULT '{}',
                article_count   INTEGER NOT NULL DEFAULT 0,
                cluster_count   INTEGER NOT NULL DEFAULT 0,
                start_date      DATE,
                end_date        DATE,
                event_type      TEXT,
                entity_set      TEXT,
                turning_score   REAL DEFAULT 0.0,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_story_hierarchy_story
            ON story_hierarchy (story_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_story_hierarchy_parent
            ON story_hierarchy (parent_id)
        """)
        cur.close()
        _STORY_HIERARCHY_TABLES_DONE = True
        print("[Schema] story_hierarchy：已确保表 story_hierarchy", flush=True)
    except Exception as e:
        print(f"[Schema] story_hierarchy 建表未执行: {e}", flush=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
