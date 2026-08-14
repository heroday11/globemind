"""
统一数据库连接管理。

所有涉华舆情系统的数据库连接均通过此模块获取，
消除各脚本中硬编码的连接参数和分散的 os.getenv 调用。

用法:
    from agentic_rag.db.connection import get_conn, get_db_url

    conn = get_conn("globemind_news")
    # or with a raw URL:
    url = get_db_url("globemind")
"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import quote_plus

from agentic_rag.db_runtime_config import require_database_password


def get_db_url(
    dbname: str,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """从环境变量构建 PostgreSQL 连接 URL（统一入口）。"""
    h = host or os.getenv("PG_HOST", "127.0.0.1")
    p = port or int(os.getenv("PG_PORT", "5432"))
    u = user or os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres"))
    pw = password or require_database_password()
    return "postgresql://" + f"{u}:{quote_plus(pw)}@{h}:{p}/{dbname}"


def get_conn(
    dbname: str,
    *,
    autocommit: bool = True,
    connect_timeout: int = 15,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
):
    """创建并返回一个 psycopg2 连接（统一入口）。"""
    import psycopg2

    conn = psycopg2.connect(
        host=host or os.getenv("PG_HOST", "127.0.0.1"),
        port=port or int(os.getenv("PG_PORT", "5432")),
        dbname=dbname,
        user=user or os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
        password=password or require_database_password(),
        connect_timeout=connect_timeout,
    )
    conn.autocommit = autocommit
    return conn
