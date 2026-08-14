#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""China Index v2 数据库迁移：为 news_ai_analysis 增列 + 创建时间序列表。

用法：
    python -m agentic_rag.db.run_china_index_v2_migration
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from agentic_rag.db_runtime_config import require_database_password

_AI_TABLE = "news_ai_analysis"
_TS_TABLE = "china_index_timeseries"


def _get_conn():
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", "globemind_news"),
        user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
        password=require_database_password(),
        connect_timeout=15,
    )
    conn.autocommit = True
    return conn


def _add_column(cur, table: str, col: str, col_type: str, comment: Optional[str] = None) -> bool:
    """ADD COLUMN IF NOT EXISTS。返回 True 表示成功。"""
    try:
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
        )
        if comment:
            try:
                cur.execute(f"COMMENT ON COLUMN {table}.{col} IS {__import__('psycopg2').extensions.AsIs(__import__('json').dumps(comment))}")
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"  [跳过] {table}.{col}: {type(e).__name__}: {e}")
        return False


def run_migration(dry_run: bool = False) -> None:
    conn = _get_conn()
    cur = conn.cursor()

    print(f"[迁移] {_AI_TABLE} 增列 + 创建 {_TS_TABLE} …")
    if dry_run:
        print("[dry-run] 以上为预览，未实际执行。")
        conn.close()
        return

    # 1. 增列
    _add_column(cur, _AI_TABLE, "prototype_scores", "JSONB",
                "6维涉华原型分")
    _add_column(cur, _AI_TABLE, "prototype_weighted", "DOUBLE PRECISION",
                "6维加权综合涉华指数 [0,1]")
    _add_column(cur, _AI_TABLE, "lexicon_score", "DOUBLE PRECISION",
                "层次化词典实体级涉华分数 [0,1]")
    _add_column(cur, _AI_TABLE, "lexicon_matches", "JSONB",
                "词典命中明细")
    _add_column(cur, _AI_TABLE, "china_index_version", "TEXT DEFAULT 'v2'",
                "涉华指数版本标记")

    # 2. 索引
    for idx_name, col, condition in [
        ("idx_ai_china_index_weighted", "prototype_weighted",
         "WHERE prototype_weighted IS NOT NULL"),
        ("idx_ai_china_index_version", "china_index_version", ""),
    ]:
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {_AI_TABLE} ({col}) {condition}"
            )
        except Exception as e:
            print(f"  [跳过] 索引 {idx_name}: {e}")

    # 3. 时间序列表
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TS_TABLE} (
            id          SERIAL PRIMARY KEY,
            event_id    INTEGER REFERENCES macro_event_coref(id) ON DELETE CASCADE,
            period      DATE NOT NULL,
            metric      TEXT NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            sample_size INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (event_id, period, metric)
        )
        """
    )
    for idx_name, cols in [
        ("idx_china_ts_event", "(event_id, period)"),
        ("idx_china_ts_metric", "(metric, period)"),
    ]:
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {_TS_TABLE} {cols}"
            )
        except Exception as e:
            print(f"  [跳过] 索引 {idx_name}: {e}")

    # 4. 历史数据打 v1 标签
    cur.execute(
        f"""
        UPDATE {_AI_TABLE}
        SET china_index_version = 'v1'
        WHERE china_index_version IS NULL
          AND is_china_related IS NOT NULL
          AND china_relevance_score IS NOT NULL
        """
    )
    updated = cur.rowcount
    print(f"  [版本标记] {updated} 条历史数据标记为 v1")

    conn.close()
    print("[完成] China Index v2 迁移结束。")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    run_migration(dry_run=dry_run)


if __name__ == "__main__":
    main()
