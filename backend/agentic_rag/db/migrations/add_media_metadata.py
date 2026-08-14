#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建 media_metadata 表（域名级媒体元数据：信任分、偏见标签、权威度等）。

用法（仓库根目录，已配置 agentic_rag/.env 写库账号）：
  python -m agentic_rag.db.migrations.add_media_metadata

安全约定：与 run_news_analysis_migration 一致，仅连接库名 postgres。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MIGRATION_DBNAME = "postgres"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent.parent
        load_dotenv(root / "agentic_rag" / ".env", override=False)
        load_dotenv(root / ".env", override=True)
    except ImportError:
        pass


def main() -> int:
    _load_env()
    try:
        import psycopg2
    except ImportError:
        print("ERROR: pip install psycopg2-binary", file=sys.stderr)
        return 1

    host = os.getenv("PG_HOST", "127.0.0.1")
    port = int(os.getenv("PG_PORT", "5432"))
    user = os.getenv("PG_WRITE_USER") or os.getenv("PG_USER", "postgres")
    password = os.getenv("PG_WRITE_PASSWORD") or os.getenv("PG_PASSWORD", "")

    print(
        f"[media_metadata] Connecting {user}@{host}:{port}/{MIGRATION_DBNAME} …",
        flush=True,
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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS media_metadata (
                domain VARCHAR(512) PRIMARY KEY,
                trust_score DOUBLE PRECISION,
                bias_label VARCHAR(256),
                authority_rank DOUBLE PRECISION,
                last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_media_metadata_trust
            ON media_metadata (trust_score)
            WHERE trust_score IS NOT NULL
            """
        )
        cur.close()
        print("[media_metadata] CREATE TABLE IF NOT EXISTS … OK", flush=True)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
