"""
PostgreSQL 只读执行器工厂（薄封装，便于统一导入点）。
"""
from __future__ import annotations

from agentic_rag.db.executor import SafePGExecutor
from agentic_rag.db.security import PGSecurityConfig


def get_read_executor(max_rows: int = 500, force_limit: bool = True) -> SafePGExecutor:
    return SafePGExecutor(PGSecurityConfig(max_rows=max_rows, force_limit=force_limit))
