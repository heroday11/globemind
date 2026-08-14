"""
Agentic Router — Multi-turn ReAct agent with tool use.

Architecture:
  RootAgent  : understands user intent, decides which tools to call
  Sub-agents : search_agent, db_agent, obsidian_agent (simulated via
               tool routing — plug in LangGraph/AutoGen for full graph)

For MVP we implement a single-loop ReAct pattern compatible with
both OpenAI and Anthropic APIs, with no external framework dependency.
Swap to LangGraph for full multi-agent graph when scaling.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, List

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from agentic_rag.agent.tools import TOOL_REGISTRY, dispatch

# ------------------------------------------------------------------ #
#  Shared system prompt                                               #
# ------------------------------------------------------------------ #
SYSTEM_PROMPT = """\
You are an intelligent research assistant backed by a hybrid RAG system.
You have access to a large knowledge base (TB-scale in production) and
an Obsidian vault with curated knowledge graph summaries.

Strategy:
1. For strategic/overview questions: read_obsidian first to get the big picture.
2. For specific factual questions: search the knowledge base directly.
3. For data questions: use list_categories to understand available data, then search.
4. Always cite the source title and category from search results.
5. If results are insufficient, try a different query or mode (vector/fulltext/hybrid).
6. Be concise and structured in your final answer.
"""

# ------------------------------------------------------------------ #
#  OpenAI tool schemas                                                #
# ------------------------------------------------------------------ #
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Hybrid semantic+keyword search over the knowledge base. Use this first for any factual question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":    {"type": "string"},
                    "top_k":   {"type": "integer", "default": 5},
                    "mode":    {"type": "string", "enum": ["hybrid", "vector", "fulltext"], "default": "hybrid"},
                    "category":{"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_obsidian",
            "description": "Read high-level knowledge graph summaries from Obsidian vault.",
            "parameters": {
                "type": "object",
                "properties": {"note_name": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "Get knowledge base statistics (document/chunk/vector counts).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "List available document categories in the knowledge base.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ------------------------------------------------------------------ #
#  Anthropic tool schemas                                             #
# ------------------------------------------------------------------ #
ANTHROPIC_TOOLS = [
    {
        "name": "search",
        "description": "Hybrid semantic+keyword search over the knowledge base.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string"},
                "top_k":   {"type": "integer", "default": 5},
                "mode":    {"type": "string", "enum": ["hybrid", "vector", "fulltext"]},
                "category":{"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_obsidian",
        "description": "Read high-level knowledge summaries from Obsidian vault.",
        "input_schema": {
            "type": "object",
            "properties": {"note_name": {"type": "string"}},
        },
    },
    {
        "name": "get_stats",
        "description": "Get knowledge base statistics.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_categories",
        "description": "List available document categories.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ------------------------------------------------------------------ #
#  OpenAI ReAct agent                                                 #
# ------------------------------------------------------------------ #
class OpenAIAgent:
    def __init__(self, model: str | None = None):
        import openai
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.max_iters = 8

    def chat(self, user_message: str, history: List[dict] | None = None) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        for _ in range(self.max_iters):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    print(f"  [Agent] Tool: {tc.function.name}({json.dumps(args)[:100]})")
                    result = dispatch(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False)[:4000],
                    })
            else:
                return msg.content or ""

        return "[Max iterations reached]"


# ------------------------------------------------------------------ #
#  Anthropic ReAct agent                                              #
# ------------------------------------------------------------------ #
class AnthropicAgent:
    def __init__(self, model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model or os.getenv("LLM_MODEL", "claude-3-5-haiku-20241022")
        self.max_iters = 8

    def chat(self, user_message: str, history: list | None = None) -> str:
        messages: list = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        for _ in range(self.max_iters):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=ANTHROPIC_TOOLS,
            )

            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        print(f"  [Agent] Tool: {block.name}({json.dumps(block.input)[:100]})")
                        result = dispatch(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False)[:4000],
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                for block in resp.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

        return "[Max iterations reached]"


# ------------------------------------------------------------------ #
#  Factory                                                            #
# ------------------------------------------------------------------ #
def get_agent():
    """Return the configured LLM agent based on LLM_PROVIDER env var."""
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "anthropic":
        return AnthropicAgent()
    return OpenAIAgent()
