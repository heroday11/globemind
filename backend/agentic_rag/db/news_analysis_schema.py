"""
新闻 NLP / 微观分析结果表 `news_analysis`：与 `news` 一行对一行，通过 news_id 关联。

原存储在 `news` 上的 is_china_related、china_related_index、entities、
sentiment_analysis、topic_classification 迁移到此表，避免污染原始新闻宽表。

大块 BGE 句向量存于独立表 ``news_embeddings``（垂直分区，减轻热表顺序扫描与缓存压力）。

环境变量：
  NEWS_ANALYSIS_AUTO_SCHEMA=0  — 跳过建表
  NEWS_ANALYSIS_MIGRATE_LEGACY=0 — 跳过从 news 旧列的一次性拷贝（默认 1：若检测到旧列则迁移并 DROP）
"""
from __future__ import annotations

import os
from typing import Optional

TABLE_NAME = "news_analysis"  # 热表：标量分析字段（无大块向量）
EMBEDDINGS_TABLE_NAME = "news_embeddings"  # 冷表：BGE 向量 JSONB


def ensure_news_analysis_dedupe_columns() -> None:
    """为 MinHash 去重增加 duplicate_of / dedupe_method（ADD COLUMN IF NOT EXISTS）。"""
    if os.getenv("NEWS_ANALYSIS_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return
    conn = None
    try:
        from agentic_rag.db.connection import get_conn

        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        cur = conn.cursor()
        cur.execute(
            f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS duplicate_of BIGINT NULL"
        )
        cur.execute(
            f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS dedupe_method TEXT NULL"
        )
        try:
            cur.execute(
                f"""
                ALTER TABLE {TABLE_NAME}
                ADD CONSTRAINT news_analysis_duplicate_of_fkey
                FOREIGN KEY (duplicate_of) REFERENCES news(id) ON DELETE SET NULL
                """
            )
        except Exception:
            pass
        cur.close()
        print(
            f"[Schema] {TABLE_NAME}：已确保 duplicate_of、dedupe_method 列（及外键，若可添加）"
        )
    except Exception as e:
        print(f"[Schema] news_analysis 去重列补齐跳过: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()


def ensure_news_analysis_opinion_columns() -> None:
    """舆情扩展：来源可信度、连续情感分（ADD COLUMN IF NOT EXISTS）。"""
    if os.getenv("NEWS_ANALYSIS_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return
    conn = None
    try:
        from agentic_rag.db.connection import get_conn

        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        cur = conn.cursor()
        cur.execute(
            f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS "
            f"source_credibility DOUBLE PRECISION NOT NULL DEFAULT 0.5"
        )
        cur.execute(
            f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS "
            f"sentiment_score DOUBLE PRECISION"
        )
        cur.close()
        print(
            f"[Schema] {TABLE_NAME}：已确保 source_credibility、sentiment_score 列"
        )
    except Exception as e:
        print(f"[Schema] opinion 列补齐跳过: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()


def ensure_news_embeddings_table() -> None:
    """创建 ``news_embeddings``（垂直分区，大块 JSONB 向量与 news_analysis 分离）。"""
    if os.getenv("NEWS_ANALYSIS_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return
    from agentic_rag.db.ensure_news_pk import ensure_news_id_referenced_unique
    from agentic_rag.db.connection import get_conn

    conn = None
    try:
        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        cur = conn.cursor()
        ensure_news_id_referenced_unique(cur, log=print)
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {EMBEDDINGS_TABLE_NAME} (
                news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
                bge_embedding JSONB
            )
            """
        )
        cur.close()
        print(
            f"[Schema] {EMBEDDINGS_TABLE_NAME}：已确保（BGE 向量冷存储，与 {TABLE_NAME} 垂直分区）"
        )
    except Exception as e:
        print(f"[Schema] {EMBEDDINGS_TABLE_NAME} 创建跳过: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()


def ensure_news_analysis_frame_column() -> None:
    """为框架分类增加 frame_classification 列（ADD COLUMN IF NOT EXISTS）。"""
    if os.getenv("NEWS_ANALYSIS_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return
    conn = None
    try:
        from agentic_rag.db.connection import get_conn

        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        cur = conn.cursor()
        cur.execute(
            f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS "
            f"frame_classification TEXT"
        )
        cur.close()
        print(f"[Schema] {TABLE_NAME}：已确保 frame_classification 列")
    except Exception as e:
        print(f"[Schema] frame_classification 列补齐跳过: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()


def ensure_news_analysis_bge_embedding_column() -> None:
    """兼容旧名：向量已迁至 ``news_embeddings``，不再向 news_analysis 添加 bge 列。"""
    ensure_news_embeddings_table()


def ensure_news_analysis_table() -> None:
    """创建 news_analysis（若不存在）。需写库账号。"""
    if os.getenv("NEWS_ANALYSIS_AUTO_SCHEMA", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        import psycopg2
    except ImportError:
        return

    from agentic_rag.db.ensure_news_pk import ensure_news_id_referenced_unique
    from agentic_rag.db.connection import get_conn

    stmts = [
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
            is_china_related BOOLEAN,
            china_related_index DOUBLE PRECISION,
            entities JSONB,
            sentiment_analysis TEXT,
            topic_classification TEXT,
            frame_classification TEXT,
            source_credibility DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            sentiment_score DOUBLE PRECISION,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_china
        ON {TABLE_NAME} (is_china_related)
        WHERE is_china_related IS NOT NULL
        """,
    ]
    conn = None
    try:
        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=True, connect_timeout=15)
        cur = conn.cursor()
        ensure_news_id_referenced_unique(cur, log=print)
        cur.close()
        cur = conn.cursor()
        for s in stmts:
            cur.execute(s)
        cur.close()
    except Exception as e:
        print(f"[Schema] news_analysis 创建跳过: {type(e).__name__}: {e}")
        return
    finally:
        if conn is not None:
            conn.close()

    ensure_news_analysis_dedupe_columns()
    ensure_news_embeddings_table()
    ensure_news_analysis_opinion_columns()
    ensure_news_analysis_frame_column()

    if os.getenv("NEWS_ANALYSIS_MIGRATE_LEGACY", "1").strip().lower() in ("0", "false", "no"):
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
    """若 news 上仍存在 sentiment_analysis 等列，则拷贝到 news_analysis 后删除旧列。"""
    try:
        import psycopg2
        from psycopg2 import sql as psql
    except ImportError:
        return
    conn = None
    try:
        from agentic_rag.db.connection import get_conn

        dbname = os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres"))
        conn = get_conn(dbname, autocommit=False, connect_timeout=15)
        cur = conn.cursor()
        if not _news_has_column(cur, "sentiment_analysis"):
            cur.close()
            conn.close()
            return
        print("[Schema] 检测到 news 上仍存在微观分析列，正在迁移至 news_analysis …")
        # entities：兼容 text / json / jsonb
        cur.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
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
        for col in (
            "is_china_related",
            "china_related_index",
            "entities",
            "sentiment_analysis",
            "topic_classification",
        ):
            cur.execute(
                psql.SQL("ALTER TABLE news DROP COLUMN IF EXISTS {}").format(psql.Identifier(col))
            )
        conn.commit()
        cur.close()
        print("[Schema] news 上旧微观分析列已删除，数据已写入 news_analysis。")
    except Exception as e:
        if conn is not None:
            conn.rollback()
        print(
            f"[Schema] 自动迁移失败（可手工执行 SQL 或修正 entities 类型后重试）: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        if conn is not None:
            conn.close()


def sql_unprocessed_where(news_alias: str = "n", na_alias: str = "na") -> str:
    """待微观分析行：无 analysis 行，或三项字段仍有 NULL。"""
    return (
        f"({na_alias}.news_id IS NULL OR {na_alias}.is_china_related IS NULL OR "
        f"{na_alias}.sentiment_analysis IS NULL OR "
        f"{na_alias}.topic_classification IS NULL OR "
        f"{na_alias}.frame_classification IS NULL)"
    )


def sql_join_news_analysis(
    news_alias: str = "n",
    na_alias: str = "na",
    join_type: str = "LEFT",
) -> str:
    return f"{join_type} JOIN {TABLE_NAME} {na_alias} ON {na_alias}.news_id = {news_alias}.id"


def sql_join_news_embeddings(
    news_alias: str = "n",
    ne_alias: str = "ne",
    join_type: str = "LEFT",
) -> str:
    """联接向量冷表（用于 SELECT ``ne.bge_embedding``）。"""
    return (
        f"{join_type} JOIN {EMBEDDINGS_TABLE_NAME} {ne_alias} "
        f"ON {ne_alias}.news_id = {news_alias}.id"
    )
