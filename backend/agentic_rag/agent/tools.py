"""
Tool definitions for the Agentic RAG system.
Wraps RAG pipeline and PostgreSQL calls for direct in-process use by agents.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Any, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from agentic_rag import VAULT_DIR


# ------------------------------------------------------------------ #
#  RAG pipeline (lazy singleton)                                       #
# ------------------------------------------------------------------ #
_pipeline = None

def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from agentic_rag.ingestion.pipeline import IngestionPipeline
        db = os.getenv("DB_PATH", "")
        if not db:
            test_db = Path(__file__).parent.parent / "data" / "validate_test.db"
            prod_db = Path(__file__).parent.parent / "data" / "rag.db"
            db = str(test_db) if test_db.exists() else str(prod_db)
        _pipeline = IngestionPipeline(db_path=db)
    return _pipeline


def tool_search(query: str, top_k: int = 5, mode: str = "hybrid",
                category: str | None = None) -> List[dict]:
    """Hybrid RAG search. Returns top-k ranked chunks."""
    return _get_pipeline().search(query, top_k=top_k, mode=mode,
                                   category_filter=category)


def tool_ingest(text: str, title: str = "Untitled",
                source: str = "agent", category: str = "general") -> dict:
    """Ingest a new document into the knowledge base."""
    return _get_pipeline().ingest_text(text=text, title=title,
                                       source=source, category=category)


def tool_stats() -> dict:
    """Return DB statistics."""
    return _get_pipeline().stats()


def tool_list_categories() -> dict:
    """List all document categories."""
    db = _get_pipeline().store._conn
    rows = db.execute("SELECT DISTINCT category FROM documents ORDER BY category").fetchall()
    return {"categories": [r[0] for r in rows]}


def tool_read_obsidian(note_name: str | None = None) -> dict:
    """Read summaries from the Obsidian vault."""
    vault = Path(os.getenv("OBSIDIAN_VAULT_PATH", str(VAULT_DIR)))
    if note_name:
        p = vault / note_name
        if not p.exists():
            p = vault / (note_name + ".md")
        if p.exists():
            return {"note": p.name, "content": p.read_text(encoding="utf-8")}
        return {"error": f"Note '{note_name}' not found"}
    notes = [{"name": f.stem, "size": f.stat().st_size} for f in vault.glob("*.md")]
    return {"vault_path": str(vault), "notes": notes}


# ------------------------------------------------------------------ #
#  PostgreSQL 安全工具（三道防线）                                      #
# ------------------------------------------------------------------ #
def tool_pg_query(sql: str, max_rows: int = 100) -> dict:
    """对 PostgreSQL 执行只读 SQL，带 AST 审查 + 超时熔断。"""
    from agentic_rag.db.pg_tools import tool_pg_query as _f
    return _f(sql=sql, max_rows=max_rows)


def tool_pg_list_tables() -> dict:
    """列出 PostgreSQL 所有表及大小。"""
    from agentic_rag.db.pg_tools import tool_pg_list_tables as _f
    return _f()


def tool_pg_describe_table(table_name: str) -> dict:
    """查看 PostgreSQL 表结构（列名、类型）。"""
    from agentic_rag.db.pg_tools import tool_pg_describe_table as _f
    return _f(table_name=table_name)


def tool_pg_test_conn() -> dict:
    """测试 PostgreSQL 连接。"""
    from agentic_rag.db.pg_tools import tool_pg_test_conn as _f
    return _f()


# ------------------------------------------------------------------ #
#  Unified tool dispatch table                                         #
# ------------------------------------------------------------------ #
TOOL_REGISTRY = {
    # RAG 检索工具
    "search":             tool_search,
    "ingest_text":        tool_ingest,
    "get_stats":          tool_stats,
    "list_categories":    tool_list_categories,
    "read_obsidian":      tool_read_obsidian,
    # PostgreSQL 安全查询工具（防线一+二+三）
    "pg_query":           tool_pg_query,
    "pg_list_tables":     tool_pg_list_tables,
    "pg_describe_table":  tool_pg_describe_table,
    "pg_test_conn":       tool_pg_test_conn,
}


def dispatch(tool_name: str, tool_args: dict) -> Any:
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    return fn(**tool_args)
