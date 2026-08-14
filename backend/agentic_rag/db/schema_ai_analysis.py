"""
news_ai_analysis 表 Schema 管理。

管理涉华舆情分析结果表 ``news_ai_analysis``，支持两个数据库：
- ``globemind``（中文新闻）
- ``globemind_news``（英文新闻）

两个库的 schema 有细微差异（globemind_news 额外有 prototype_scores、
prototype_weighted 等列），通过 COMMON_COLUMNS / GLOBEMIND_NEWS_ONLY 区分。

用法:
    from agentic_rag.db.schema_ai_analysis import ensure_schema

    ensure_schema("globemind")
    ensure_schema("globemind_news")
"""
from __future__ import annotations

import os
from typing import Optional

TABLE_NAME = "news_ai_analysis"

# ── 两库共有的列 ──────────────────────────────────────────────────────
COMMON_COLUMNS: list[tuple[str, str]] = [
    ("news_id", "BIGINT NOT NULL"),
    ("cluster_id", "VARCHAR(255)"),
    ("analyzed_at", "TIMESTAMP NOT NULL DEFAULT now()"),
    ("entities", "JSONB"),
    ("is_china_related", "BOOLEAN"),
    ("category", "TEXT"),
    ("topic", "TEXT"),
    ("impact_level", "SMALLINT"),
    ("sub_tags", "JSONB"),
    ("china_relevance_score", "SMALLINT"),
    ("china_impact_sentiment", "REAL"),
    ("scoring_evidence", "TEXT"),
    ("entity_pair_sentiments", "JSONB"),
    ("exact_quotes", "TEXT"),
    ("frame_classification", "TEXT"),
]

# ── 仅 globemind_news 有的列（评分系统相关）───────────────────────────
GLOBEMIND_NEWS_ONLY: list[tuple[str, str]] = [
    ("prototype_scores", "JSONB"),
    ("prototype_weighted", "DOUBLE PRECISION"),
    ("lexicon_score", "DOUBLE PRECISION"),
    ("lexicon_matches", "JSONB"),
    ("china_index_version", "TEXT"),
]


def all_columns_for_db(dbname: str) -> list[tuple[str, str]]:
    """返回指定数据库应包含的全部列定义。"""
    if dbname == "globemind_news":
        return COMMON_COLUMNS + GLOBEMIND_NEWS_ONLY
    return COMMON_COLUMNS


def ensure_schema(
    dbname: str,
    *,
    auto: Optional[bool] = None,
    conn=None,
) -> None:
    """确保 ``news_ai_analysis`` 表及其全部列存在。

    参数:
        dbname: 目标数据库名（globemind / globemind_news）。
        auto: 若为 False 则跳过（默认读取 NEWS_AI_ANALYSIS_AUTO_SCHEMA 环境变量）。
        conn: 可选的外部连接；不提供时内部创建。
    """
    if auto is False or (
        auto is None
        and os.getenv("NEWS_AI_ANALYSIS_AUTO_SCHEMA", "1").strip().lower()
        in ("0", "false", "no")
    ):
        return
    try:
        import psycopg2
    except ImportError:
        return

    from agentic_rag.db.connection import get_conn

    own_conn = False
    if conn is None:
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        own_conn = True

    try:
        cur = conn.cursor()

        # 1. CREATE TABLE IF NOT EXISTS（仅含两库共有的核心列）
        col_defs = ",\n    ".join(
            f"{name} {dtype}" for name, dtype in COMMON_COLUMNS
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                {col_defs}
            )
            """
        )

        # 2. ADD COLUMN IF NOT EXISTS — 对 globemind_news 追加额外列
        for name, dtype in all_columns_for_db(dbname):
            try:
                cur.execute(
                    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS {name} {dtype}"
                )
            except Exception:
                pass  # 某些类型变体可能在现有列上失败，跳过

        # 3. 确保主键（PK 非空 + 唯一约束）
        try:
            cur.execute(
                f"ALTER TABLE {TABLE_NAME} ADD CONSTRAINT {TABLE_NAME}_pkey "
                f"PRIMARY KEY (news_id)"
            )
        except Exception:
            pass  # 已存在则跳过

        # 4. 确保索引
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_topic "
                f"ON {TABLE_NAME} (topic)"
            )
        except Exception:
            pass
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_frame "
                f"ON {TABLE_NAME} (frame_classification)"
            )
        except Exception:
            pass
        try:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_sentiment "
                f"ON {TABLE_NAME} (china_impact_sentiment)"
            )
        except Exception:
            pass

        cur.close()
        print(f"[Schema] {TABLE_NAME} @ {dbname}：已确保 {len(all_columns_for_db(dbname))} 列")
    except Exception as e:
        print(f"[Schema] {TABLE_NAME} @ {dbname} 初始化跳过: {type(e).__name__}: {e}")
    finally:
        if own_conn and conn is not None:
            conn.close()


def ensure_both_dbs() -> None:
    """便捷函数：确保两个数据库的 schema 都就绪。"""
    ensure_schema("globemind")
    ensure_schema("globemind_news")
