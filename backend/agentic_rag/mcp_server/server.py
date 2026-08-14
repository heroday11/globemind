"""
MCP Server (JSON-RPC 2.0 over HTTP) — exposes RAG tools to Claude Code.

Tools:
  search          - hybrid semantic + keyword search
  ingest_text     - add a document at runtime
  get_stats       - DB statistics
  list_categories - available categories
  read_obsidian   - read Obsidian vault summaries

Integration: add to .mcp.json -> { "url": "http://127.0.0.1:8765/mcp" }
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from agentic_rag import VAULT_DIR
from agentic_rag.ingestion.pipeline import IngestionPipeline

app = FastAPI(title="Agentic RAG MCP Server", version="0.1.0")
_pipeline: IngestionPipeline | None = None


def get_pipeline() -> IngestionPipeline:
    global _pipeline
    if _pipeline is None:
        db = os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "rag.db"))
        _pipeline = IngestionPipeline(db_path=db)
    return _pipeline


TOOLS = [
    {
        "name": "search",
        "description": (
            "Hybrid semantic + keyword search over the document corpus. "
            "Returns ranked text chunks with source metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
                "mode": {"type": "string", "enum": ["hybrid", "vector", "fulltext"], "default": "hybrid"},
                "category": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ingest_text",
        "description": "Add a new document into the RAG knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text":     {"type": "string"},
                "title":    {"type": "string"},
                "source":   {"type": "string"},
                "category": {"type": "string", "default": "general"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_stats",
        "description": "Return database statistics: document count, chunk count, vector count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_categories",
        "description": "List all document categories currently stored in the knowledge base.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_obsidian",
        "description": "Read knowledge graph summaries from the Obsidian vault.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_name": {"type": "string", "description": "Specific note filename (optional)"},
            },
        },
    },
]


# ------------------------------------------------------------------ #
#  Tool execution                                                      #
# ------------------------------------------------------------------ #
def execute_tool(name: str, args: dict) -> Any:
    pipeline = get_pipeline()

    if name == "search":
        results = pipeline.search(
            query=args["query"],
            top_k=args.get("top_k", 5),
            mode=args.get("mode", "hybrid"),
            category_filter=args.get("category"),
        )
        return results

    elif name == "ingest_text":
        r = pipeline.ingest_text(
            text=args["text"],
            title=args.get("title", "Untitled"),
            source=args.get("source", "mcp"),
            category=args.get("category", "general"),
        )
        return r

    elif name == "get_stats":
        return pipeline.stats()

    elif name == "list_categories":
        db = pipeline.store._conn
        rows = db.execute("SELECT DISTINCT category FROM documents ORDER BY category").fetchall()
        return {"categories": [r[0] for r in rows]}

    elif name == "read_obsidian":
        vault = Path(os.getenv("OBSIDIAN_VAULT_PATH", str(VAULT_DIR)))
        note_name = args.get("note_name")
        if note_name:
            p = vault / note_name
            if not p.exists():
                p = vault / (note_name + ".md")
            if p.exists():
                return {"note": p.name, "content": p.read_text(encoding="utf-8")}
            return {"error": f"Note '{note_name}' not found in vault"}
        # Return index of all notes
        notes = []
        for f in vault.glob("*.md"):
            notes.append({"name": f.stem, "size": f.stat().st_size})
        return {"vault_path": str(vault), "notes": notes}

    else:
        raise ValueError(f"Unknown tool: {name}")


# ------------------------------------------------------------------ #
#  JSON-RPC 2.0 endpoints                                             #
# ------------------------------------------------------------------ #
@app.get("/health")
async def health():
    return {"status": "ok", "service": "agentic-rag-mcp"}


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    try:
        if method == "tools/list":
            result = {"tools": TOOLS}

        elif method == "tools/call":
            tool_name = params.get("name") or params.get("tool_name")
            tool_args = params.get("arguments") or params.get("args") or {}
            output = execute_tool(tool_name, tool_args)
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(output, ensure_ascii=False, indent=2)}
                ]
            }

        elif method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentic-rag", "version": "0.1.0"},
            }

        else:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                                  "error": {"code": -32601, "message": f"Method not found: {method}"}})

        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})

    except Exception as exc:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": req_id,
             "error": {"code": -32603, "message": str(exc)}},
            status_code=500,
        )


def main():
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8765"))
    print(f"[MCP] Starting server at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
