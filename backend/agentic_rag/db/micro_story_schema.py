"""micro_story_coref / micro_story_coref_members 表自动创建（供阶段⑧使用）。"""
from __future__ import annotations

import os

from agentic_rag.db_runtime_config import require_database_password

_MICRO_STORY_TABLES_DONE = False


def _micro_story_schema_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        # NOTE: Always use globemind_news (not the .env PG_DATABASE which points to "postgres")
        dbname="globemind_news",
        user=os.getenv("PG_WRITE_USER", "postgres"),
        password=require_database_password(),
        connect_timeout=15,
    )


def ensure_micro_story_tables() -> None:
    """创建 micro_story_coref 和 micro_story_coref_members 表（CREATE TABLE IF NOT EXISTS）。"""
    global _MICRO_STORY_TABLES_DONE
    if _MICRO_STORY_TABLES_DONE:
        return
    if os.getenv("MICRO_STORY_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    conn = None
    try:
        conn = _micro_story_schema_connect()
        conn.autocommit = True
        cur = conn.cursor()

        # Enable pg_vector extension for embedding column
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_story_coref (
                id              SERIAL PRIMARY KEY,
                title           TEXT,
                event_type      TEXT,
                initiator       TEXT,
                target          TEXT,
                start_date      DATE,
                end_date        DATE,
                cluster_ids     TEXT[],
                article_count   INTEGER NOT NULL DEFAULT 0,
                cluster_count   INTEGER NOT NULL DEFAULT 0,
                quality         TEXT DEFAULT 'valid',
                embedding       vector(1024),
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_story_coref_members (
                micro_story_id  INTEGER NOT NULL REFERENCES micro_story_coref(id) ON DELETE CASCADE,
                cluster_id      TEXT NOT NULL,
                news_id         BIGINT NOT NULL,
                PRIMARY KEY (micro_story_id, cluster_id, news_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_story_coref_members_news "
            "ON micro_story_coref_members (news_id)"
        )
        cur.close()
        _MICRO_STORY_TABLES_DONE = True
        print(
            "[Schema] micro_story_coref：已确保表 micro_story_coref、micro_story_coref_members",
            flush=True,
        )
    except Exception as e:
        print(
            f"[Schema] micro_story_coref 建表未执行（需 PG 写权限，或设 MICRO_STORY_AUTO_SCHEMA=0）: {e}",
            flush=True,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        from pathlib import Path
        from dotenv import load_dotenv

        _agentic = Path(__file__).resolve().parent.parent
        load_dotenv(_agentic / ".env", override=False)
        load_dotenv(_agentic.parent / ".env", override=False)
    except ImportError:
        pass
    ensure_micro_story_tables()
    print("[micro_story_schema] self-check OK", flush=True)
