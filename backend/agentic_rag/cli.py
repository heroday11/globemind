#!/usr/bin/env python
"""
Interactive CLI for the Agentic RAG system.

Usage:
  cd agentic_rag
  python cli.py chat                     # interactive chat with agent
  python cli.py ingest path/to/file.txt  # ingest a file
  python cli.py search "query text"      # quick search
  python cli.py sync                     # sync knowledge to Obsidian
  python cli.py stats                    # show DB stats
  python cli.py serve                    # start MCP server
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def cmd_chat(args):
    from agentic_rag.agent.router import get_agent
    agent = get_agent()
    print("Agentic RAG Chat (type 'quit' to exit)\n")
    history = []
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not user or user.lower() in ("quit", "exit", "q"):
            break
        print("Agent: ", end="", flush=True)
        answer = agent.chat(user, history=history)
        print(answer)
        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": answer})
        print()


def cmd_ingest(args):
    from agentic_rag.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    result = pipeline.ingest_file(args.path, category=args.category)
    print(json.dumps(result, indent=2))


def cmd_search(args):
    from agentic_rag.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    results = pipeline.search(args.query, top_k=args.top_k, mode=args.mode)
    for i, r in enumerate(results, 1):
        score = r.get("rrf_score") or r.get("score", 0)
        print(f"\n[{i}] {r['title']} ({r['category']}) score={score:.4f}")
        print(f"    Source: {r['source']}")
        print(f"    {r['text'][:300]}...")


def cmd_sync(args):
    from agentic_rag.knowledge.obsidian_sync import ObsidianSyncManager
    mgr = ObsidianSyncManager()
    result = mgr.sync()
    print(json.dumps(result, indent=2))


def cmd_stats(args):
    from agentic_rag.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    stats = pipeline.stats()
    print(json.dumps(stats, indent=2))


def cmd_serve(args):
    import uvicorn
    from agentic_rag.mcp_server.server import app
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8765"))
    print(f"Starting MCP server at http://{host}:{port}")
    print(f"  Health : http://{host}:{port}/health")
    print(f"  MCP    : http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description="Agentic RAG CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("chat", help="Interactive agent chat")

    p_ingest = sub.add_parser("ingest", help="Ingest a file")
    p_ingest.add_argument("path")
    p_ingest.add_argument("--category", default="file")

    p_search = sub.add_parser("search", help="Search the knowledge base")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", dest="top_k", type=int, default=5)
    p_search.add_argument("--mode", choices=["hybrid", "vector", "fulltext"], default="hybrid")

    sub.add_parser("sync", help="Sync knowledge to Obsidian vault")
    sub.add_parser("stats", help="Show DB statistics")
    sub.add_parser("serve", help="Start MCP server")

    args = parser.parse_args()
    dispatch = {
        "chat":   cmd_chat,
        "ingest": cmd_ingest,
        "search": cmd_search,
        "sync":   cmd_sync,
        "stats":  cmd_stats,
        "serve":  cmd_serve,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
    else:
        fn(args)


if __name__ == "__main__":
    main()
