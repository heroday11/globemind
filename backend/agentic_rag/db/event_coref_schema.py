"""event_coref_clusters / event_coref_members 表自动创建（供阶段⑦使用）。"""
from __future__ import annotations

import os

from agentic_rag.db_runtime_config import require_database_password

_EVENT_COREF_TABLES_DONE = False


def _event_coref_schema_connect():
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


def ensure_event_coref_tables() -> None:
    """创建 event_coref_clusters 和 event_coref_members 表（CREATE TABLE IF NOT EXISTS）。"""
    global _EVENT_COREF_TABLES_DONE
    if _EVENT_COREF_TABLES_DONE:
        return
    if os.getenv("EVENT_COREF_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    conn = None
    try:
        conn = _event_coref_schema_connect()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS event_coref_clusters (
                cluster_id      TEXT PRIMARY KEY,
                article_count   INTEGER NOT NULL DEFAULT 0,
                event_type      TEXT,
                initiator       TEXT,
                target          TEXT,
                dominant_trigger TEXT,
                start_date      DATE,
                end_date        DATE,
                cluster_quality TEXT,
                title           TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        # Add title column if missing (for existing tables created before schema update)
        cur.execute(
            "ALTER TABLE event_coref_clusters ADD COLUMN IF NOT EXISTS title TEXT"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS event_coref_members (
                cluster_id   TEXT NOT NULL REFERENCES event_coref_clusters(cluster_id) ON DELETE CASCADE,
                news_id      BIGINT NOT NULL,
                event_type   TEXT,
                initiator    TEXT,
                target       TEXT,
                trigger      TEXT,
                published_at TIMESTAMPTZ,
                PRIMARY KEY (cluster_id, news_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_members_news_id "
            "ON event_coref_members (news_id)"
        )
        cur.close()
        _EVENT_COREF_TABLES_DONE = True
        print(
            "[Schema] event_coref：已确保表 event_coref_clusters、event_coref_members",
            flush=True,
        )
    except Exception as e:
        print(
            f"[Schema] event_coref 建表未执行（需 PG 写权限，或设 EVENT_COREF_AUTO_SCHEMA=0）: {e}",
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
    ensure_event_coref_tables()
    print("[event_coref_schema] self-check OK", flush=True)
