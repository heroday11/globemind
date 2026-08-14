#!/usr/bin/env python3
"""
Copy application user tables from the legacy globemind_news database to news.

The script is idempotent: rows are upserted by primary key and sequences are
advanced after copying. Connection settings are read from backend/api/.env,
with LEGACY_DB_NAME and TARGET_DB_NAME overrides available.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
for path in (REPO_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

load_dotenv(REPO_ROOT / "backend" / "api" / ".env", override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from api.core.db import create_tables  # noqa: E402


USER_TABLES = [
    "app_user",
    "user_search_history",
    "user_favorite",
    "password_reset_token",
    "assistant_chat_session",
    "assistant_chat_message",
]


def _conn(db_name: str):
    return psycopg2.connect(
        host=os.getenv("DB_HOST") or os.getenv("PG_HOST") or "127.0.0.1",
        port=int(os.getenv("DB_PORT") or os.getenv("PG_PORT") or "5432"),
        user=os.getenv("DB_USER") or os.getenv("PG_USER") or "postgres",
        password=os.getenv("DB_PASSWORD") or os.getenv("PG_PASSWORD") or "",
        dbname=db_name,
        connect_timeout=10,
    )


def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def _columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def _primary_key(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """,
            (f"public.{table}",),
        )
        return [r[0] for r in cur.fetchall()]


def _copy_table(src, dst, table: str) -> int:
    if not _table_exists(src, table) or not _table_exists(dst, table):
        return 0

    src_cols = set(_columns(src, table))
    dst_cols = _columns(dst, table)
    cols = [c for c in dst_cols if c in src_cols]
    pk_cols = _primary_key(dst, table)
    if not cols or not pk_cols:
        return 0

    col_sql = ", ".join(f'"{c}"' for c in cols)
    with src.cursor() as cur:
        cur.execute(f'SELECT {col_sql} FROM public."{table}" ORDER BY 1')
        rows = cur.fetchall()
    if not rows:
        return 0

    conflict_sql = ", ".join(f'"{c}"' for c in pk_cols)
    update_cols = [c for c in cols if c not in pk_cols]
    if update_cols:
        update_sql = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
        suffix = f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
    else:
        suffix = f"ON CONFLICT ({conflict_sql}) DO NOTHING"

    sql = f'INSERT INTO public."{table}" ({col_sql}) VALUES %s {suffix}'
    with dst.cursor() as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def _advance_sequences(conn, tables: Iterable[str]) -> None:
    with conn.cursor() as cur:
        for table in tables:
            if not _table_exists(conn, table):
                continue
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_default LIKE 'nextval%%'
                """,
                (table,),
            )
            for (column,) in cur.fetchall():
                cur.execute(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, %s),
                        GREATEST((SELECT COALESCE(MAX(id), 1) FROM public.""" + f'"{table}"' + """), 1)
                    )
                    """,
                    (f"public.{table}", column),
                )


def main() -> int:
    legacy_db = os.getenv("LEGACY_DB_NAME", "globemind_news")
    target_db = os.getenv("TARGET_DB_NAME", os.getenv("DB_NAME", "news"))
    if legacy_db == target_db:
        print(f"legacy and target database are both {target_db}; nothing to copy")
        return 0

    create_tables()

    src = _conn(legacy_db)
    dst = _conn(target_db)
    try:
        dst.autocommit = False
        copied: dict[str, int] = {}
        for table in USER_TABLES:
            copied[table] = _copy_table(src, dst, table)
        _advance_sequences(dst, USER_TABLES)
        dst.commit()
        for table, count in copied.items():
            print(f"{table}: upserted {count}")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
