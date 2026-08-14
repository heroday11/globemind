"""
PostgreSQL 工具 — 注册到 Agent 工具注册表。

新增工具：
  pg_query          - 执行只读 SQL（带三道防线）
  pg_list_tables    - 列出所有表
  pg_describe_table - 查看表结构
  pg_test_conn      - 测试数据库连接
"""
from __future__ import annotations
from typing import Any

from agentic_rag.db.executor import get_executor
from agentic_rag.db.security import PGSecurityConfig


def tool_pg_query(sql: str, max_rows: int = 500) -> dict:
    """
    对 PostgreSQL 执行只读 SQL。
    三道防线自动生效：
      - 防线一：只读用户 news_reader
      - 防线二：sqlglot AST 审查 + 强制 LIMIT
      - 防线三：5 秒 statement_timeout 熔断
    """
    cfg = PGSecurityConfig(max_rows=max_rows)
    return get_executor(cfg).query(sql)


def tool_pg_list_tables() -> dict:
    """列出数据库中所有用户表及其大小。"""
    return get_executor().list_tables()


def tool_pg_describe_table(table_name: str) -> dict:
    """查看指定表的列结构（列名、类型、可空性）。"""
    return get_executor().describe_table(table_name)


def tool_pg_test_conn() -> dict:
    """测试 PostgreSQL 连接是否正常。"""
    return get_executor().test_connection()


# ------------------------------------------------------------------ #
#  工具注册表（合并到 agent/tools.py 的 TOOL_REGISTRY）               #
# ------------------------------------------------------------------ #
PG_TOOL_REGISTRY = {
    "pg_query":          lambda **kw: tool_pg_query(**kw),
    "pg_list_tables":    lambda **kw: tool_pg_list_tables(),
    "pg_describe_table": lambda **kw: tool_pg_describe_table(**kw),
    "pg_test_conn":      lambda **kw: tool_pg_test_conn(),
}

# OpenAI function-calling schema
PG_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pg_query",
            "description": (
                "对 PostgreSQL 新闻数据库执行只读 SQL 查询。"
                "自动经过 AST 安全审查和超时熔断保护。"
                "只能执行 SELECT，禁止写操作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "纯 SELECT SQL 语句"},
                    "max_rows": {"type": "integer", "default": 100,
                                  "description": "最大返回行数（上限 500）"},
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pg_list_tables",
            "description": "列出 PostgreSQL 数据库中的所有表及其大小。探索数据库结构时首先调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pg_describe_table",
            "description": "查看指定表的列结构。在写 SQL 之前先调用，了解字段名和类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "表名"}
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pg_test_conn",
            "description": "测试 PostgreSQL 连接状态。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Anthropic tool schema
PG_ANTHROPIC_TOOLS = [
    {
        "name": "pg_query",
        "description": "对 PostgreSQL 新闻数据库执行只读 SQL，带 AST 审查和超时熔断。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "max_rows": {"type": "integer", "default": 100},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "pg_list_tables",
        "description": "列出所有数据库表。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "pg_describe_table",
        "description": "查看表结构。",
        "input_schema": {
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
        },
    },
    {
        "name": "pg_test_conn",
        "description": "测试 PostgreSQL 连接。",
        "input_schema": {"type": "object", "properties": {}},
    },
]
