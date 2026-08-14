#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
开发/换库时「清空流水线产物」：PostgreSQL 的 news_analysis / news_assignment，
以及 Milvus 的 news_vectors、cluster_centroids（通常为 Docker 映射端口 19530）。

⚠ 会删除微观分析结果与向量侧全量数据；news 正文不会被删。
⚠ 全量一键重置（PG 分析字段 + 宏观表 + Milvus + 文件侧产物）请用仓库根：
   python tools/reset_system.py --execute

用法（仓库根、已激活 venv）：
  python -m agentic_rag.db.reset_pipeline_storage --help
  python -m agentic_rag.db.reset_pipeline_storage --execute --pg --milvus

仅清 PG：
  python -m agentic_rag.db.reset_pipeline_storage --execute --pg

仅删 Milvus 集合并由下次任务自动重建空表：
  python -m agentic_rag.db.reset_pipeline_storage --execute --milvus
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent
        load_dotenv(root / "agentic_rag" / ".env", override=False)
        load_dotenv(root / ".env", override=True)
    except ImportError:
        pass


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
        user=os.getenv("PG_WRITE_USER") or os.getenv("PG_USER", "postgres"),
        password=os.getenv("PG_WRITE_PASSWORD") or os.getenv("PG_PASSWORD", ""),
        connect_timeout=30,
    )


def reset_postgres_tables(*, truncate_assignment: bool) -> None:
    import psycopg2

    conn = _pg_connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name='news_embeddings'
            """
        )
        if cur.fetchone():
            cur.execute("TRUNCATE TABLE news_embeddings")
            print("[PG] TRUNCATE news_embeddings OK")
        cur.execute("TRUNCATE TABLE news_analysis RESTART IDENTITY CASCADE")
        print("[PG] TRUNCATE news_analysis OK")
        if truncate_assignment:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='news_assignment'
                """
            )
            if cur.fetchone():
                cur.execute("TRUNCATE TABLE news_assignment RESTART IDENTITY CASCADE")
                print("[PG] TRUNCATE news_assignment OK")
            else:
                print("[PG] news_assignment 不存在，跳过")
        cur.close()
    finally:
        conn.close()


def drop_milvus_collections() -> None:
    """删除 news_vectors、cluster_centroids（供 reset / reset_system 复用）。

    复用 ``milvus_store.get_milvus_store`` 的连接逻辑（支持 URI / host:port），
    避免维护两套 Milvus 连接参数。
    """
    from pymilvus import utility

    from agentic_rag.db.milvus_store import get_milvus_store

    store = get_milvus_store()
    try:
        for name in ("news_vectors", "cluster_centroids"):
            if utility.has_collection(name):
                utility.drop_collection(name)
                print(f"[Milvus] dropped collection {name!r}")
            else:
                print(f"[Milvus] collection {name!r} 不存在，跳过")
    finally:
        try:
            from pymilvus import connections

            connections.disconnect("default")
        except Exception:
            pass


def main() -> int:
    _load_env()
    p = argparse.ArgumentParser(description="清空 news_analysis / Milvus 向量集合（需 --execute）")
    p.add_argument(
        "--execute",
        action="store_true",
        help="缺少此开关时只打印计划，不执行",
    )
    p.add_argument("--pg", action="store_true", help="TRUNCATE news_analysis（及可选 news_assignment）")
    p.add_argument(
        "--also-assignment",
        action="store_true",
        help="与 --pg 同时 TRUNCATE news_assignment",
    )
    p.add_argument(
        "--milvus",
        action="store_true",
        help="drop Milvus 集合 news_vectors、cluster_centroids",
    )
    args = p.parse_args()

    if not args.pg and not args.milvus:
        print("请至少指定 --pg 和/或 --milvus。示例：--execute --pg --milvus", file=sys.stderr)
        return 1

    if not args.execute:
        print(
            "【预览】将执行：\n"
            + ("  - PostgreSQL: TRUNCATE news_analysis" + (" + news_assignment" if args.also_assignment else "") + "\n" if args.pg else "")
            + ("  - Milvus: drop news_vectors, cluster_centroids\n" if args.milvus else "")
            + "\n加 --execute 才真正执行。"
        )
        return 0

    if args.pg:
        reset_postgres_tables(truncate_assignment=args.also_assignment)
    if args.milvus:
        drop_milvus_collections()

    print(
        "\n[Done] 清空完成。\n"
        "  · 本脚本不会启动任何流水线阶段（不会跑 44 / 999）。若你看到 44，是另一条命令触发的。\n"
        "  · 建议顺序：先跑微观分析（如 python -m agentic_rag.run_pipeline_stages --stage 999），"
        "写回 PG 且 MILVUS_SYNC=1 时会逐步灌回 Milvus；待 news_analysis 里又有涉华等结果后，"
        "如需单独补灌向量再考虑 --stage 44。\n"
        "  · 若刚 TRUNCATE 完就跑 44：涉华模式下候选可能为 0（分析表为空）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
