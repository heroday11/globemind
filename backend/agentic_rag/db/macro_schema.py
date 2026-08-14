"""macro_storylines / micro_events 可选列自动补齐（供 Stage 2 / 5 / 6、Obsidian 同步调用）。"""
from __future__ import annotations

import os

from agentic_rag.db_runtime_config import require_database_password

_MACRO_STAGE_TABLES_DONE = False


def _macro_schema_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
        user=os.getenv("PG_WRITE_USER", "postgres"),
        password=require_database_password(),
        connect_timeout=15,
    )


def ensure_macro_stage_tables() -> None:
    """
    若不存在则创建 Stage5/6 依赖的核心表（仅 CREATE TABLE IF NOT EXISTS）。
    此前仅有 ALTER ADD COLUMN 时，在空库上会报「relation macro_storylines does not exist」。
    """
    global _MACRO_STAGE_TABLES_DONE
    if _MACRO_STAGE_TABLES_DONE:
        return
    if os.getenv("MACRO_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    conn = None
    try:
        conn = _macro_schema_connect()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_storylines (
                storyline_id BIGINT PRIMARY KEY,
                title TEXT NOT NULL,
                start_date DATE,
                end_date DATE,
                micro_event_count INTEGER NOT NULL DEFAULT 0,
                article_count INTEGER NOT NULL DEFAULT 0,
                description TEXT,
                status VARCHAR(32) DEFAULT 'active',
                representative_embedding JSONB,
                macro_centroid JSONB,
                entities_pool JSONB,
                china_index_avg DOUBLE PRECISION,
                sentiment_main TEXT,
                topic_main TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_events (
                event_id BIGINT PRIMARY KEY,
                title TEXT NOT NULL,
                start_date DATE,
                end_date DATE,
                article_count INTEGER NOT NULL DEFAULT 0,
                entities_pool JSONB NOT NULL DEFAULT '[]'::jsonb,
                macro_storyline_id BIGINT REFERENCES macro_storylines(storyline_id) ON DELETE SET NULL,
                china_index_avg DOUBLE PRECISION,
                sentiment_main TEXT,
                topic_main TEXT,
                first_seen_time TIMESTAMPTZ,
                last_seen_time TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS storyline_micro_map (
                storyline_id BIGINT NOT NULL REFERENCES macro_storylines(storyline_id) ON DELETE CASCADE,
                event_id BIGINT NOT NULL REFERENCES micro_events(event_id) ON DELETE CASCADE,
                membership_score DOUBLE PRECISION DEFAULT 1.0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (storyline_id, event_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS micro_event_members (
                event_id BIGINT NOT NULL REFERENCES micro_events(event_id) ON DELETE CASCADE,
                news_id BIGINT NOT NULL,
                PRIMARY KEY (event_id, news_id)
            )
            """
        )
        cur.close()
        _MACRO_STAGE_TABLES_DONE = True
        print(
            "[Schema] Stage5/6：已确保表 macro_storylines、micro_events、"
            "storyline_micro_map、micro_event_members（CREATE TABLE IF NOT EXISTS）",
            flush=True,
        )
    except Exception as e:
        print(
            f"[Schema] Stage5/6 建表未执行（需 PG 写权限，或设 MACRO_AUTO_SCHEMA=0）: {e}",
            flush=True,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def ensure_intel_persistence_columns() -> None:
    """
    为 macro_storylines、micro_events 添加情报聚合列（若不存在）：
    china_index_avg, sentiment_main, topic_main, entities_pool (JSONB)
    需写库账号；失败仅打印警告。关闭：MACRO_AUTO_SCHEMA=0
    """
    if os.getenv("MACRO_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    ensure_macro_stage_tables()
    try:
        import psycopg2
    except ImportError:
        return

    stmts = [
        "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS china_index_avg DOUBLE PRECISION",
        "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS sentiment_main TEXT",
        "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS topic_main TEXT",
        "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS entities_pool JSONB",
        "ALTER TABLE micro_events ADD COLUMN IF NOT EXISTS china_index_avg DOUBLE PRECISION",
        "ALTER TABLE micro_events ADD COLUMN IF NOT EXISTS sentiment_main TEXT",
        "ALTER TABLE micro_events ADD COLUMN IF NOT EXISTS topic_main TEXT",
        "ALTER TABLE micro_events ADD COLUMN IF NOT EXISTS entities_pool JSONB",
    ]
    conn = None
    try:
        conn = _macro_schema_connect()
        conn.autocommit = True
        cur = conn.cursor()
        for sql in stmts:
            cur.execute(sql)
        cur.close()
        print(
            "[Schema] macro_storylines / micro_events：已确保情报列 "
            "(china_index_avg, sentiment_main, topic_main, entities_pool)"
        )
    except Exception as e:
        print(
            f"[Schema] 情报列 ADD COLUMN 未执行（需 PG 写权限，或设 MACRO_AUTO_SCHEMA=0）: {e}"
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def ensure_macro_storylines_optional_columns() -> None:
    """
    为 macro_storylines 添加 description、status（若不存在），并补齐 micro/macro 情报列。
    需 PostgreSQL 写账号；失败仅打印警告，不中断主流程。
    关闭：环境变量 MACRO_AUTO_SCHEMA=0
    """
    if os.getenv("MACRO_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    ensure_macro_stage_tables()
    try:
        import psycopg2
    except ImportError:
        return

    conn = None
    try:
        conn = _macro_schema_connect()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS description TEXT"
        )
        cur.execute(
            "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'active'"
        )
        # Stage5 雪球 Recall：load_active_macros_for_snowball 依赖 JSONB 质心向量
        cur.execute(
            "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS macro_centroid JSONB"
        )
        cur.execute(
            "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS representative_embedding JSONB"
        )
        cur.execute(
            "ALTER TABLE macro_storylines ADD COLUMN IF NOT EXISTS opinion_trend_json JSONB"
        )
        cur.close()
        print(
            "[Schema] macro_storylines：已确保 description、status、macro_centroid、"
            "representative_embedding、opinion_trend_json 列（ADD COLUMN IF NOT EXISTS）"
        )
    except Exception as e:
        print(
            f"[Schema] macro_storylines 自动加列未执行（需 PG 写权限，或设 MACRO_AUTO_SCHEMA=0）: {e}"
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    ensure_intel_persistence_columns()


if __name__ == "__main__":
    """自检：python -m agentic_rag.db.macro_schema（需在仓库根目录、已配置 PG 写账号）。"""
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        _agentic = Path(__file__).resolve().parent.parent
        load_dotenv(_agentic / ".env", override=False)
        load_dotenv(_agentic.parent / ".env", override=False)
    except ImportError:
        pass
    ensure_macro_stage_tables()
    ensure_macro_storylines_optional_columns()
    print("[macro_schema] self-check OK", flush=True)
