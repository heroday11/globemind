#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地「CC 主管」HTTP 接口：Anthropic Claude + Tool Use，需要出 PPT 时调用本机 /generate-ppt。

**推荐**：只启动 `python api_server.py`，CC 路由已挂载在同一进程（默认 :8765）：
  POST /cc/chat  POST /cc/chat/stream（SSE，真流式 + 步骤/接口）
  GET /cc/config   GET /cc/health

**可选**：单独调试 CC 时 `python cc_bridge.py`（默认 :8770，仅挂载 CC 路由）。

环境变量：
  ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
                         二选一（ccswitch 常写 AUTH_TOKEN）；可与 ANTHROPIC_BASE_URL 搭配转流
  ANTHROPIC_BASE_URL     非官方/转流网关；仅设此项且没有 key 时可用 ANTHROPIC_API_KEY_PLACEHOLDER（默认 local-proxy）
  CC_BACKEND             auto | anthropic | cli（默认 auto）
  CC_CLI_CMD             本地 CLI，一条可 exec 的命令（见下方 stdin 协议）；CC_BACKEND=cli 时必填
  CC_CLI_TIMEOUT         CLI 超时秒数，默认 600
  CC_ANTHROPIC_MODEL     未设时依次尝试 ANTHROPIC_MODEL、ANTHROPIC_DEFAULT_SONNET_MODEL，最后默认 sonnet id
  CLAUDE_CONFIG_DIR      若设置，会读取其中的 settings.json 的 env（与 Claude Code 一致）
  PPT_GATEWAY_BASE       （可选）仅当自行把 PPT 工具加回工具列表时需要；当前默认不含 PPT 工具
  API_PORT               与 api_server 一致时用于自动拼网关地址（默认 8765）
  CC_ANTHROPIC_CONNECT_TIMEOUT  连接转流/API 超时秒数，默认 120（ConnectTimeout 时可加大）
  CC_ANTHROPIC_READ_TIMEOUT     单次 messages 读超时秒数，默认 600
  ANTHROPIC_HTTPX_TRUST_ENV     设为 0/false 时 httpx 不使用系统 HTTP(S)_PROXY（代理导致 TLS 超时时可关）
  CC_ANTHROPIC_MAX_RETRIES      SDK 内部失败重试次数，默认 2；设为 0 可略过重试略省等待
  CC_ANTHROPIC_TRANSPORT_RETRIES  httpx 连接层重试（瞬断/握手失败），默认 2
  CC_CHAT_MAX_CONCURRENT        同时处理的 /cc/chat（Anthropic）请求数，默认 1，避免连发压垮转流

还会在未覆盖的前提下，从本机 Claude Code 配置读取 env：
  $CLAUDE_CONFIG_DIR/settings.json → ~/.claude/settings.json → 仓库根 .claude/settings.json

CLI stdin 协议（UTF-8 JSON 一行或多行合并后解析）：
  {"message": "<用户话>", "system": "<系统提示>"}
stdout：优先解析 JSON 的 "reply" 字段；否则整段 stdout 作为回复。
注意：CLI 模式不会走 Anthropic Tool Use；数据助手推荐 anthropic 路径并设 CC_ENABLE_LOCAL_TOOLS=1。

本地读文件 / 跑命令（仅此模式，且默认关闭）：
  CC_ENABLE_LOCAL_TOOLS=1  为 Anthropic 路径增加工具 read_repo_file、run_shell_command（危险：勿把 /cc/chat 暴露公网）
  CC_LOCAL_SANDBOX_ROOT    工作目录与读文件根，默认本仓库根目录
  CC_READ_FILE_MAX_BYTES   单文件最多读取字节，默认 300000
  CC_SHELL_TIMEOUT         run_shell_command 超时秒数，默认 180
  CC_TOOL_MAX_ROUNDS       Anthropic 工具循环最多轮数，默认 16（用尽后仍会从 tool_result 再要一轮纯文本）

新闻库检索（数据助手推荐打开，避免用 shell 拼 curl）：
  ASSISTANT_SEARCH_API_BASE  后端根 URL，如 http://127.0.0.1:8088（须能访问 POST /api/dashboard/search）
  ASSISTANT_SEARCH_API_TOKEN 可选 Bearer，与登录用户无关时一般不填
  CC_ENABLE_NEWS_SEARCH_TOOL  设为 0/false/off 时关闭 search_news_corpus（即使已配 BASE）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# .env（与 api_server 一致；主进程已加载时重复调用无害）
# ---------------------------------------------------------------------------


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (REPO_ROOT / ".env", REPO_ROOT / "ppt-master" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


_load_dotenv_files()

log = logging.getLogger("cc_bridge")


def is_production() -> bool:
    """Resolve the deployment policy without importing the API application.

    The standalone bridge deliberately has no API dependency.  The web API
    injects its authenticated-user resolver through :func:`configure_cc_auth`
    when it mounts this router.
    """

    return (os.environ.get("APP_ENV") or "development").strip().lower() in {
        "prod",
        "production",
    }


CCAuthProvider = Callable[[Request], Awaitable[dict[str, Any]] | dict[str, Any]]
_cc_auth_provider: CCAuthProvider | None = None


def configure_cc_auth(provider: CCAuthProvider | None) -> None:
    """Inject the host application's auth policy into the CC adapter.

    Keeping this seam in the bridge means the CC capability can be tested or
    hosted independently while the API remains the composition root.
    """

    global _cc_auth_provider
    _cc_auth_provider = provider


async def _require_cc_chat_access(request: Request) -> dict[str, Any]:
    provider = _cc_auth_provider
    if provider is not None:
        user = provider(request)
        if hasattr(user, "__await__"):
            user = await user
        if not isinstance(user, dict):
            raise HTTPException(status_code=401, detail="未登录或 token 无效")
        return user

    # Standalone mode has no identity store.  Do not treat a bearer header as
    # proof of identity; a host must inject a complete verifier when mounting
    # the router.  This keeps the capability fail-closed when misconfigured.
    raise HTTPException(status_code=401, detail="CC 鉴权适配器未配置")

# 已从以下文件合并过 env（仅用于 /cc/config 展示，不含密钥）
_CLAUDE_SETTINGS_SOURCES: list[str] = []


def _claude_code_settings_paths() -> list[Path]:
    out: list[Path] = []
    ccd = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if ccd:
        out.append(Path(ccd).expanduser() / "settings.json")
    out.append(Path.home() / ".claude" / "settings.json")
    out.append(REPO_ROOT / ".claude" / "settings.json")
    return out


def _apply_claude_code_settings_env() -> None:
    """把 Claude Code settings.json 里 env 写入 os.environ，不覆盖已有非空变量。"""
    global _CLAUDE_SETTINGS_SOURCES
    _CLAUDE_SETTINGS_SOURCES = []
    for p in _claude_code_settings_paths():
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as e:
            log.debug("skip Claude settings %s: %s", p, e)
            continue
        env_block = data.get("env") if isinstance(data, dict) else None
        if not isinstance(env_block, dict):
            continue
        rel = str(p)
        try:
            rel = str(p.resolve().relative_to(Path.home()))
        except ValueError:
            pass
        applied_any = False
        for k, v in env_block.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if (os.environ.get(k) or "").strip():
                continue
            if v is None:
                continue
            os.environ[k] = str(v)
            applied_any = True
        if applied_any:
            _CLAUDE_SETTINGS_SOURCES.append(rel)


_apply_claude_code_settings_env()


def _effective_anthropic_model() -> str:
    for k in (
        "CC_ANTHROPIC_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# Claude 系统提示与工具
# ---------------------------------------------------------------------------

CC_SYSTEM_PROMPT = (
    "你是「数据助手」—— Globemind 全局新闻舆情分析平台的智能助手。\n\n"
    "## 核心能力\n"
    "- **舆情分析**：调用本地知识库（事件聚类、舆情指数、新闻库）进行高质量的情报分析、事件溯源、趋势研判\n"
    "- **联网搜索**：通过 `web_search` 获取互联网实时信息，补充分析维度\n"
    "- **代码执行**：通过 `run_code` 运行 Python 代码进行数据分析、统计计算、可视化\n"
    "- **新闻检索**：通过 `search_news_corpus` 检索本地新闻库（关键词/语义向量/聚类三种模式）\n\n"
    "## 工作原则\n"
    "1. 始终用中文回复\n"
    "2. 基于数据和工具返回结果进行分析，不编造数据\n"
    "3. 舆情分析时主动调用相关工具获取数据支撑，而非凭推测回答\n"
    "4. 需要最新信息时主动使用 `web_search` 联网搜索\n"
    "5. 仅使用已启用的工具，不假设存在其他隐藏能力\n"
    "6. 数据分析优先使用 `run_code`（预装 pandas/numpy/matplotlib），而非 shell 命令"
)

BASE_TOOLS: list[dict[str, Any]] = []

LOCAL_CAPABILITY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_repo_file",
        "description": (
            "读取沙箱根（datasearch 仓库根）下的文本文件。relative_path 使用正斜杠，禁止 .. 与绝对路径。"
            "需要笔记/数据时优先尝试 obsidian_vault/ 下路径；大二进制会拒绝。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": (
                        "相对沙箱根，正斜杠；数据优先 obsidian_vault/xxx.md；"
                        "源码示例 cppt/cc_bridge.py。"
                    ),
                },
            },
            "required": ["relative_path"],
        },
    },
    {
        "name": "run_shell_command",
        "description": (
            "在沙箱根目录下执行一条命令（非交互、无 shell 组合语法）。"
            "示例：python scripts/orchestrator.py --help、pytest -q。禁止 ; && || 换行与管道。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "单行可执行命令（由空格拆分为 argv，不经 cmd.exe /bin/sh 解释）",
                },
            },
            "required": ["command"],
        },
    },
]


def _local_tools_enabled() -> bool:
    return os.environ.get("CC_ENABLE_LOCAL_TOOLS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _news_search_tool_enabled() -> bool:
    if os.environ.get("CC_ENABLE_NEWS_SEARCH_TOOL", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    return bool((os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip())


SEARCH_NEWS_CORPUS_TOOL: dict[str, Any] = {
    "name": "search_news_corpus",
    "description": (
        "调用站点后台新闻检索 HTTP 接口 POST /api/dashboard/search，从新闻库返回结构化列表（标题、摘要、时间等）。"
        "用户要查报道、事实核对、列举新闻时**优先使用本工具**；不要用 run_shell_command 拼 curl 或访问数据库。"
        "mode：exact=SQL 关键词；fuzzy=语义向量；cluster=聚类簇。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "检索关键词或短语（中文或英文）",
            },
            "mode": {
                "type": "string",
                "enum": ["exact", "fuzzy", "cluster"],
                "description": "检索模式，默认 exact；向量慢时仍可用 exact",
            },
            "page": {
                "type": "integer",
                "description": "页码，从 1 开始",
                "default": 1,
            },
            "page_size": {
                "type": "integer",
                "description": "每页条数，建议 5～20",
                "default": 10,
            },
        },
        "required": ["keyword"],
    },
}


WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "通过必应（Bing）搜索引擎在互联网上搜索实时信息，返回标题、摘要、URL。"
        "适合查询新闻、事件、人物、最新动态等需要联网获取的内容。"
        "如果用户明确要求「联网搜索」「网上查」「搜索一下」，应优先使用此工具。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（中文或英文），尽量简洁精确",
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果数量上限，默认 8，最大 15",
                "default": 8,
            },
        },
        "required": ["query"],
    },
}

NAVIGATE_CLUSTERS_TOOL: dict[str, Any] = {
    "name": "navigate_clusters",
    "description": (
        "浏览 L3/L2/L1 事件层级（宏观事件 → 事件链 → 新闻），来自本平台聚类分析管线。"
        "支持：按关键词搜索簇、获取宏观簇详情、列出宏观下的微观事件、获取微观详情、获取微观下的新闻。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search_clusters",
                    "get_macro",
                    "get_micros",
                    "get_micro",
                    "get_news",
                ],
                "description": (
                    "search_clusters=按关键词搜索簇；get_macro=获取宏观簇详情；"
                    "get_micros=获取宏观下的微观列表；get_micro=获取微观详情；get_news=获取微观下的新闻"
                ),
            },
            "keyword": {
                "type": "string",
                "description": "search_clusters 时使用的搜索关键词",
            },
            "macro_id": {
                "anyOf": [{"type": "string"}, {"type": "integer"}],
                "description": "L3 宏观事件 ID（macro_id），get_macro/get_micros 时使用",
            },
            "micro_id": {
                "anyOf": [{"type": "string"}, {"type": "integer"}],
                "description": "L2 事件链 ID（chain_id），get_micro/get_news 时使用",
            },
            "page": {
                "type": "integer",
                "description": "分页页码，默认 1",
                "default": 1,
            },
            "page_size": {
                "type": "integer",
                "description": "每页条数，默认 10，最大 50",
                "default": 10,
            },
        },
        "required": ["action"],
    },
}

OPINION_INDEX_TOOL: dict[str, Any] = {
    "name": "query_opinion_index",
    "description": (
        "查询本平台舆情指数数据，包括情感趋势、影响力指数、事件分布等。"
        "适合：分析舆情走势、情感极性变化、事件影响力排名。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "china_trend",
                    "events_by_date",
                    "event_clusters",
                    "event_news",
                ],
                "description": (
                    "china_trend=涉华舆情趋势；events_by_date=按日期检索事件；"
                    "event_clusters=宏观事件簇列表；event_news=获取某事件的新闻"
                ),
            },
            "days": {
                "type": "integer",
                "description": "时间范围（天），默认 365",
                "default": 365,
            },
            "date": {
                "type": "string",
                "description": "日期（YYYY-MM-DD），用于 events_by_date 筛选",
            },
            "event_id": {
                "type": "integer",
                "description": "事件 ID，用于 event_news 获取相关新闻",
            },
            "page": {
                "type": "integer",
                "default": 1,
            },
            "page_size": {
                "type": "integer",
                "description": "每页条数，默认 10，最大 50",
                "default": 10,
            },
        },
        "required": ["action"],
    },
}

KNOWLEDGE_BASE_TOOL: dict[str, Any] = {
    "name": "access_knowledge_base",
    "description": (
        "读取本平台知识库（KB）的分类和文件内容。"
        "知识库存储了分析笔记、研究报告、参考文档等。"
        "适合：查阅本地研究笔记、知识文档、历史分析报告。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_categories",
                    "list_files",
                    "read_file",
                ],
                "description": (
                    "list_categories=列出所有分类；"
                    "list_files=列出指定分类的文件（传入 category）；"
                    "read_file=读取文件内容（传入 filename）"
                ),
            },
            "category": {
                "type": "string",
                "description": "知识库分类 ID，用于 list_files 筛选",
            },
            "filename": {
                "type": "string",
                "description": "文件名，用于 read_file 读取内容",
            },
        },
        "required": ["action"],
    },
}

RUN_CODE_TOOL: dict[str, Any] = {
    "name": "run_code",
    "description": (
        "在安全沙箱环境中执行 Python 代码，用于数据分析、统计计算、生成图表等。"
        "已预装 pandas、numpy、matplotlib、plotly 等常用库。"
        "代码执行结果会返回 stdout 和 stderr。注意：不支持交互式输入，无网络访问。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 30，最大 120",
                "default": 30,
            },
        },
        "required": ["code"],
    },
}


async def _tool_search_news_corpus(args: dict[str, Any]) -> dict[str, Any]:
    base = (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 ASSISTANT_SEARCH_API_BASE"}
    kw = str((args.get("keyword") or "")).strip()
    if not kw:
        return {"ok": False, "error": "keyword 不能为空"}
    mode = str((args.get("mode") or "exact")).strip().lower()
    if mode not in ("exact", "fuzzy", "cluster"):
        mode = "exact"
    try:
        page = max(1, int(args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        ps = int(args.get("page_size") or 10)
    except (TypeError, ValueError):
        ps = 10
    ps = max(1, min(20, ps))
    payload: dict[str, Any] = {
        "keyword": kw,
        "mode": mode,
        "page": page,
        "page_size": ps,
    }
    url = f"{base}/api/dashboard/search"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = (os.environ.get("ASSISTANT_SEARCH_API_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            r = await client.post(url, json=payload, headers=headers)
        try:
            data = r.json()
        except json.JSONDecodeError:
            data = {"_non_json": (r.text or "")[:4000]}
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}",
                "body_preview": json.dumps(data, ensure_ascii=False)[:2500],
            }
        items = data.get("data")
        if not isinstance(items, list):
            items = []
        slim: list[dict[str, Any]] = []
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            ab = str(it.get("abstract") or "")
            slim.append(
                {
                    "id": it.get("id"),
                    "title": it.get("title"),
                    "abstract": (ab[:500] + "…") if len(ab) > 500 else ab,
                    "pub_time": str(it.get("pub_time") or ""),
                    "source": it.get("source"),
                }
            )
        return {
            "ok": True,
            "endpoint": url,
            "mode": mode,
            "keyword": kw,
            "total": data.get("total"),
            "page": data.get("page", page),
            "page_size": data.get("page_size", ps),
            "query_time_ms": data.get("query_time_ms"),
            "items": slim,
            "items_returned": len(slim),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "hint": url}


async def _tool_web_search(args: dict[str, Any]) -> dict[str, Any]:
    """通过 Bing HTML 接口进行互联网搜索。"""
    query = str((args.get("query") or "")).strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    try:
        max_results = min(15, max(1, int(args.get("max_results") or 8)))
    except (TypeError, ValueError):
        max_results = 8

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=True) as client:
            r = await client.get(
                "https://www.bing.com/search",
                params={"q": query},
                headers=headers,
            )
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        results: list[dict[str, Any]] = []
        for li in soup.select("li.b_algo"):
            if len(results) >= max_results:
                break
            h2 = li.select_one("h2")
            link = h2.find("a") if h2 else None
            title = link.get_text(strip=True) if link else ""
            url = link.get("href", "") if link else ""
            snippet_el = li.select_one(".b_caption p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title:
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })
        lines = [
            f"搜索「{query}」共找到 {len(results)} 条结果：\n",
        ]
        for idx, item in enumerate(results, 1):
            lines.append(f"{idx}. {item['title']}")
            if item.get("snippet"):
                lines.append(f"   简介：{item['snippet'][:200]}")
            lines.append(f"   链接：{item['url']}")
            lines.append("")
        return {
            "ok": True,
            "query": query,
            "results_count": len(results),
            "results": results,
            "results_text": "\n".join(lines),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "hint": "web_search 使用 Bing HTML 接口"}


async def _tool_navigate_clusters(args: dict[str, Any]) -> dict[str, Any]:
    """浏览 current L3/L2/L1 事件层级。"""
    base = (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 ASSISTANT_SEARCH_API_BASE"}
    action = str((args.get("action") or "")).strip()
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            if action == "search_clusters":
                kw = str((args.get("keyword") or "")).strip()
                if not kw:
                    return {"ok": False, "error": "keyword 不能为空"}
                r = await client.get(
                    f"{base}/api/graph/macros/search",
                    params={"q": kw, "limit": 24},
                    headers=headers,
                )
            elif action == "get_macro":
                mid = str(args.get("macro_id") or "").strip()
                if not mid:
                    return {"ok": False, "error": "macro_id 不能为空"}
                r = await client.get(
                    f"{base}/api/graph/macro/{quote(mid, safe='')}",
                    headers=headers,
                )
            elif action == "get_micros":
                mid = str(args.get("macro_id") or "").strip()
                if not mid:
                    return {"ok": False, "error": "macro_id 不能为空"}
                page = max(1, int(args.get("page") or 1))
                page_size = max(1, min(50, int(args.get("page_size") or 10)))
                r = await client.get(
                    f"{base}/api/graph/macro/{quote(mid, safe='')}/micros",
                    params={"limit": page_size, "offset": (page - 1) * page_size},
                    headers=headers,
                )
            elif action == "get_micro":
                mid = str(args.get("micro_id") or "").strip()
                if not mid:
                    return {"ok": False, "error": "micro_id 不能为空"}
                r = await client.get(
                    f"{base}/api/graph/micro/{quote(mid, safe='')}",
                    headers=headers,
                )
            elif action == "get_news":
                mid = str(args.get("micro_id") or "").strip()
                if not mid:
                    return {"ok": False, "error": "micro_id 不能为空"}
                ps = max(1, min(50, int(args.get("page_size") or 10)))
                r = await client.post(
                    f"{base}/api/graph/micros/news-batch",
                    json={"event_ids": [mid], "limit_per": ps},
                    headers={**headers, "Content-Type": "application/json"},
                )
            else:
                return {"ok": False, "error": f"未知 action: {action}"}
        r.raise_for_status()
        data = r.json()
        items = data.get("items", data.get("micro_events"))
        if isinstance(items, list):
            items_returned = len(items)
        elif isinstance(data.get("by_event"), dict):
            items_returned = sum(
                len(value)
                for value in data["by_event"].values()
                if isinstance(value, list)
            )
        else:
            items_returned = 1 if isinstance(data, dict) and data else 0
        return {
            "ok": True,
            "action": action,
            "data": data,
            "items_returned": items_returned,
        }
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}", "body": (e.response.text or "")[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _tool_query_opinion_index(args: dict[str, Any]) -> dict[str, Any]:
    """查询舆情指数数据。"""
    base = (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 ASSISTANT_SEARCH_API_BASE"}
    action = str((args.get("action") or "")).strip()
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            if action == "china_trend":
                days = int(args.get("days") or 365)
                r = await client.get(
                    f"{base}/api/opinion/china-trend",
                    params={"days": max(7, min(3650, days))},
                    headers=headers,
                )
            elif action == "events_by_date":
                date_val = str((args.get("date") or "")).strip()
                if not date_val:
                    return {"ok": False, "error": "date 不能为空 (YYYY-MM-DD)"}
                r = await client.get(
                    f"{base}/api/opinion/events-by-date",
                    params={"date_str": date_val},
                    headers=headers,
                )
            elif action == "event_clusters":
                eid = args.get("event_id")
                date_val = str((args.get("date") or "")).strip()
                if not eid or not date_val:
                    return {"ok": False, "error": "event_id 和 date 不能为空"}
                page = max(1, int(args.get("page") or 1))
                ps = max(1, min(50, int(args.get("page_size") or 30)))
                r = await client.get(
                    f"{base}/api/opinion/macro-event-clusters",
                    params={"macro_event_id": int(eid), "date_str": date_val, "page": page, "page_size": ps},
                    headers=headers,
                )
            elif action == "event_news":
                cid = str((args.get("event_id") or "")).strip()
                if not cid:
                    return {"ok": False, "error": "event_id(cluster_id) 不能为空"}
                page = max(1, int(args.get("page") or 1))
                ps = max(1, min(50, int(args.get("page_size") or 20)))
                r = await client.get(
                    f"{base}/api/opinion/event-news",
                    params={"cluster_id": cid, "page": page, "page_size": ps},
                    headers=headers,
                )
            else:
                return {"ok": False, "error": f"未知 action: {action}"}
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "action": action, "data": data}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}", "body": (e.response.text or "")[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _tool_access_knowledge_base(args: dict[str, Any]) -> dict[str, Any]:
    """读取知识库分类和文件内容。"""
    base = (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 ASSISTANT_SEARCH_API_BASE"}
    action = str((args.get("action") or "")).strip()
    headers = {"Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            if action == "list_categories":
                r = await client.get(f"{base}/api/kb2/categories", headers=headers)
            elif action == "list_files":
                cat = str((args.get("category") or "")).strip()
                if not cat:
                    return {"ok": False, "error": "category 不能为空"}
                r = await client.get(
                    f"{base}/api/kb2/files",
                    params={"category": cat},
                    headers=headers,
                )
            elif action == "read_file":
                cat = str((args.get("category") or "")).strip()
                fn = str((args.get("filename") or "")).strip()
                if not cat or not fn:
                    return {"ok": False, "error": "category 和 filename 不能为空"}
                r = await client.get(
                    f"{base}/api/kb2/files/{fn}/read",
                    params={"category": cat},
                    headers=headers,
                )
            else:
                return {"ok": False, "error": f"未知 action: {action}"}
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "action": action, "data": data}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}", "body": (e.response.text or "")[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _tool_run_code(args: dict[str, Any]) -> dict[str, Any]:
    """在安全沙箱中执行 Python 代码。"""
    code = str((args.get("code") or "")).strip()
    if not code:
        return {"ok": False, "error": "code 不能为空"}
    try:
        timeout = min(120, max(1, int(args.get("timeout") or 30)))
    except (TypeError, ValueError):
        timeout = 30

    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False)
    try:
        tmp.write(code)
        tmp.close()
        p = await asyncio.create_subprocess_exec(
            sys.executable, tmp.name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp.name.rpartition("/")[0] or "/tmp",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            out_b, err_b = await asyncio.wait_for(p.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            return {"ok": False, "error": f"执行超时（>{timeout}s）", "code_preview": code[:500]}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    out = (out_b or b"").decode("utf-8", errors="replace")[:100_000]
    err = (err_b or b"").decode("utf-8", errors="replace")[:50_000]
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode or 0,
        "stdout": out,
        "stderr": err,
    }


def _sandbox_root(username: str | None = None) -> Path:
    """沙箱根目录：当 username 不为空时，限制为工作区目录。"""
    if username:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,96}", username):
            raise ValueError("invalid workspace username")
        workspace_root = Path("/root/data/workspace").resolve()
        target = (workspace_root / username).resolve()
        target.relative_to(workspace_root)
        return target
    raw = (os.environ.get("CC_LOCAL_SANDBOX_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # 默认 datasearch 根（cppt 上一级），便于 read_repo_file 访问 obsidian_vault/ 与同仓其他目录
    parent = REPO_ROOT.resolve().parent
    if parent.is_dir():
        return parent
    return REPO_ROOT.resolve()


def _read_file_path_candidates(relative_path: str) -> list[str]:
    """生成尝试读取的相对路径：纠正模型多写的 cppt/ 或与仓库目录名重复的一级前缀。"""
    rel = relative_path.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(x: str) -> None:
        if not x or x in seen:
            return
        seen.add(x)
        out.append(x)

    add(rel)
    parts = rel.split("/")
    if parts and parts[0].lower() == "cppt":
        add("/".join(parts[1:]))
    root_name = REPO_ROOT.name
    if parts and parts[0].lower() == root_name.lower() and len(parts) > 1:
        add("/".join(parts[1:]))
    return out


def _safe_resolve_under_root(rel: str, root: Path) -> Path | None:
    rel = rel.strip().replace("\\", "/")
    if not rel:
        return None
    try:
        if PurePosixPath(rel).is_absolute():
            return None
    except ValueError:
        return None
    if ".." in PurePosixPath(rel).parts:
        return None
    cand = (root / rel).resolve()
    try:
        cand.relative_to(root.resolve())
    except ValueError:
        return None
    return cand


def _tool_read_repo_file_sync(relative_path: str, username: str | None = None) -> dict[str, Any]:
    root = _sandbox_root(username)
    try:
        max_b = int((os.environ.get("CC_READ_FILE_MAX_BYTES") or "300000").strip())
    except ValueError:
        max_b = 300_000
    last: dict[str, Any] | None = None
    for cand in _read_file_path_candidates(relative_path):
        p = _safe_resolve_under_root(cand, root)
        if p is None:
            last = {
                "ok": False,
                "error": "非法路径、越界或为空",
                "tried_relative_path": cand,
            }
            continue
        if not p.is_file():
            last = {"ok": False, "error": "不是文件或不存在", "path": str(p), "tried_relative_path": cand}
            continue
        size = p.stat().st_size
        if size > max_b:
            return {
                "ok": False,
                "error": f"文件过大（>{max_b} bytes）",
                "path": str(p),
            }
        data = p.read_bytes()
        if b"\x00" in data[:8192]:
            return {
                "ok": False,
                "error": "疑似二进制，拒绝全文读取",
                "path": str(p),
            }
        text = data.decode("utf-8", errors="replace")
        out: dict[str, Any] = {"ok": True, "path": str(p), "content": text}
        if cand != relative_path.strip().replace("\\", "/").lstrip("/"):
            out["resolved_from"] = relative_path
            out["used_relative_path"] = cand
        return out
    return last or {
        "ok": False,
        "error": "无法解析路径",
        "relative_path": relative_path,
    }


def _dangerous_shell_token_outside_double_quotes(cmd: str, token: str) -> bool:
    """仅在双引号外判定危险子串，避免 `python -c "import os; print(1)"` 中的分号误伤。"""
    if token not in cmd:
        return False
    in_dq = False
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == '"':
            in_dq = not in_dq
        elif not in_dq and cmd.startswith(token, i):
            return True
        i += 1
    return False


def _decode_subprocess_output(stdout: bytes | None, stderr: bytes | None) -> tuple[str, str]:
    """
    Windows 下 cmd/Python 常输出 GBK/CP936。此前用 UTF-8 + replace 会「假成功」产生满屏乱码。
    先严格按 UTF-8 解；失败则在 Windows 上回退 GBK。
    """

    def dec(b: bytes | None) -> str:
        if not b:
            return ""
        for enc in ("utf-8-sig", "utf-8"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        if os.name == "nt":
            for enc in ("gbk", "cp936"):
                try:
                    return b.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
        return b.decode("utf-8", errors="replace")

    return dec(stdout), dec(stderr)


async def _tool_run_shell_command(args: dict[str, Any], username: str | None = None) -> dict[str, Any]:
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "error": "command 为空"}
    if any(x in cmd for x in ("\n", "\r")):
        return {"ok": False, "error": "仅允许单行命令"}
    if _dangerous_shell_token_outside_double_quotes(cmd, ";"):
        return {"ok": False, "error": "为安全起见禁止使用: ';'（请拆成单条简单命令）"}
    for bad in ("&&", "||", "|", "`", "$("):
        if _dangerous_shell_token_outside_double_quotes(cmd, bad):
            return {"ok": False, "error": f"为安全起见禁止使用: {bad!r}（请拆成单条简单命令）"}

    try:
        timeout = float((os.environ.get("CC_SHELL_TIMEOUT") or "180").strip())
    except ValueError:
        timeout = 180.0
    root = _sandbox_root(username)
    argv = _shlex_split_cli(cmd)
    if not argv:
        return {"ok": False, "error": "无法解析命令"}

    def _run() -> tuple[int, str, str]:
        # Windows：`dir` 等为 cmd 内建命令，无独立 .exe，shell=False 会 WinError 2。
        if os.name == "nt":
            p = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=False,
                timeout=timeout,
                shell=True,
            )
        else:
            p = subprocess.run(
                argv,
                cwd=str(root),
                capture_output=True,
                text=False,
                timeout=timeout,
                shell=False,
            )
        out, err = _decode_subprocess_output(p.stdout, p.stderr)
        return p.returncode, out, err

    try:
        code, out, err = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时（>{timeout}s）", "command": cmd}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "command": cmd}

    return {
        "ok": code == 0,
        "exit_code": code,
        "stdout": out[-24_000:],
        "stderr": err[-24_000:],
        "cwd": str(root),
    }


def _active_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if _local_tools_enabled() and not is_production():
        tools.extend(LOCAL_CAPABILITY_TOOLS)
    else:
        tools.extend(BASE_TOOLS)
    if _news_search_tool_enabled():
        tools.append(SEARCH_NEWS_CORPUS_TOOL)
    tools.append(WEB_SEARCH_TOOL)
    if not is_production() and os.environ.get("CC_ENABLE_RUN_CODE", "").strip() == "1":
        tools.append(RUN_CODE_TOOL)
    if _news_search_tool_enabled():
        tools.extend([
            NAVIGATE_CLUSTERS_TOOL,
            OPINION_INDEX_TOOL,
            KNOWLEDGE_BASE_TOOL,
        ])
    return tools


def _cc_system_prompt(username: str | None = None) -> str:
    s = CC_SYSTEM_PROMPT
    extra = ""

    if _news_search_tool_enabled():
        extra += (
            "\n\n## 后台数据工具（已启用）\n\n"
            "### 新闻检索\n"
            "- **`search_news_corpus`**：调用后台 `POST /api/dashboard/search` 检索新闻库，支持 exact（关键词）/ fuzzy（语义向量）/ cluster（聚类簇）三种模式。"
            "需要查具体报道、核对事实、补全新闻列表时优先使用。\n\n"
            "### 事件聚类导航\n"
            "- **`navigate_clusters`**：浏览 L1/L2 事件聚类层级。支持按关键词搜索宏观簇（search_clusters）、"
            "获取宏观簇详情（get_macro）、列出微观事件（get_micros）、获取微观下新闻列表（get_news）。\n"
            "适用于：追踪某个事件的发展脉络、查看事件聚类结构、获取特定聚类下的新闻详情。\n\n"
            "### 舆情指数\n"
            "- **`query_opinion_index`**：查询本平台舆情指数。支持：涉华舆情趋势（china_trend，含情感/影响力分析）、"
            "按日期检索事件（events_by_date，某日的宏观事件列表）、事件聚类下钻（event_clusters）、事件新闻列表（event_news）。\n"
            "适用于：分析舆情走势、情感极性变化、事件影响力排名。\n\n"
            "### 知识库\n"
            "- **`access_knowledge_base`**：读取本平台知识库的分类和文件内容。支持列出分类（list_categories）、"
            "浏览分类下文件（list_files）、读取文件内容（read_file）。\n"
            "适用于：查阅研究笔记、分析报告、参考文档等本地知识资产。\n"
        )

    extra += (
        "\n## 联网搜索\n"
        "- **`web_search`**：通过必应在互联网上搜索实时信息。"
        "当用户要求查最新消息、网上找资料时优先使用。\n"
    )

    extra += (
        "\n## 代码执行\n"
        "- **`run_code`**：在沙箱中执行 Python 代码（pandas/numpy/matplotlib 已预装）。"
        "适用于数据分析、统计计算、图表生成，比 shell 命令更安全、更可控。"
    )

    if not _local_tools_enabled():
        return s + extra

    lines = [
        s + extra,
        "\n\n## 本地文件系统（已启用 CC_ENABLE_LOCAL_TOOLS）",
    ]
    if username:
        sandbox_dir = _sandbox_root(username)
        lines.append(
            f"\n⚠ **安全限制：当前为工作区沙箱模式（用户 {username}）**\n"
            f"- **`read_repo_file`**：只能读取 `{sandbox_dir}/` 目录内的文件。\n"
            f"- **`run_shell_command`**：仅在 `{sandbox_dir}/` 目录下执行单条命令。\n"
            f"- 禁止越界访问（`..`、绝对路径、组合命令语法）。\n"
        )
    else:
        lines.append(
            "\n- **`read_repo_file`**：路径相对沙箱根（datasearch 根目录），优先在 `obsidian_vault/` 下查找数据。\n"
            "- **`run_shell_command`**：仅在沙箱根执行单条命令（无 `;`、`&&`、管道）。\n"
        )
    lines.append("- 写文件请使用 `run_code` 或输出补丁由用户保存。")
    return "\n".join(lines)


async def _run_tool(name: str, tool_input: dict[str, Any], username: str | None = None) -> dict[str, Any]:
    if name == "read_repo_file":
        if not _local_tools_enabled():
            return {"ok": False, "error": "read_repo_file 未启用（设置 CC_ENABLE_LOCAL_TOOLS=1）"}
        rel = str((tool_input or {}).get("relative_path") or "")
        return await asyncio.to_thread(_tool_read_repo_file_sync, rel, username)
    if name == "run_shell_command":
        if not _local_tools_enabled():
            return {"ok": False, "error": "run_shell_command 未启用（设置 CC_ENABLE_LOCAL_TOOLS=1）"}
        return await _tool_run_shell_command(tool_input or {}, username)
    if name == "search_news_corpus":
        if not _news_search_tool_enabled():
            return {"ok": False, "error": "search_news_corpus 未启用（配置 ASSISTANT_SEARCH_API_BASE）"}
        return await _tool_search_news_corpus(tool_input if isinstance(tool_input, dict) else {})
    if name == "web_search":
        return await _tool_web_search(tool_input if isinstance(tool_input, dict) else {})
    if name == "navigate_clusters":
        if not _news_search_tool_enabled():
            return {"ok": False, "error": "navigate_clusters 未启用（配置 ASSISTANT_SEARCH_API_BASE）"}
        return await _tool_navigate_clusters(tool_input if isinstance(tool_input, dict) else {})
    if name == "query_opinion_index":
        if not _news_search_tool_enabled():
            return {"ok": False, "error": "query_opinion_index 未启用（配置 ASSISTANT_SEARCH_API_BASE）"}
        return await _tool_query_opinion_index(tool_input if isinstance(tool_input, dict) else {})
    if name == "access_knowledge_base":
        if not _news_search_tool_enabled():
            return {"ok": False, "error": "access_knowledge_base 未启用（配置 ASSISTANT_SEARCH_API_BASE）"}
        return await _tool_access_knowledge_base(tool_input if isinstance(tool_input, dict) else {})
    if name == "run_code":
        return await _tool_run_code(tool_input if isinstance(tool_input, dict) else {})
    return {"ok": False, "error": f"未知工具: {name}"}


def _text_from_content(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts).strip()


def _format_cc_sse(obj: dict[str, Any]) -> bytes:
    """与 api_server.format_sse_data 一致：单行 JSON，便于 EventSource/fetch 解析。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _api_base() -> str:
    return (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "http://127.0.0.1:8088").strip().rstrip("/")


def _tool_external_call_info(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """工具即将执行时，说明调用的本机 HTTP 或本地资源（便于客户端展示「做到哪一步」）。"""
    if name == "read_repo_file":
        return {
            "invoke": {
                "kind": "local_read",
                "sandbox_root": str(_sandbox_root()),
                "relative_path": (tool_input or {}).get("relative_path"),
            }
        }
    if name == "run_shell_command":
        cmd = str((tool_input or {}).get("command") or "")
        return {
            "invoke": {
                "kind": "local_shell",
                "cwd": str(_sandbox_root()),
                "command_preview": cmd[:500],
            }
        }
    if name == "search_news_corpus":
        base = (os.environ.get("ASSISTANT_SEARCH_API_BASE") or "").strip().rstrip("/")
        return {
            "invoke": {
                "kind": "http_post",
                "url": f"{base}/api/dashboard/search",
                "keyword_preview": str((tool_input or {}).get("keyword") or "")[:200],
                "mode": str((tool_input or {}).get("mode") or "exact"),
            }
        }
    if name == "web_search":
        return {
            "invoke": {
                "kind": "http_get",
                "url": "https://www.bing.com/search",
                "query_preview": str((tool_input or {}).get("query") or "")[:200],
            }
        }
    if name == "navigate_clusters":
        return {
            "invoke": {
                "kind": "http_get",
                "url": f"{_api_base()}/api/graph/...",
                "action": str((tool_input or {}).get("action") or ""),
            }
        }
    if name == "query_opinion_index":
        return {
            "invoke": {
                "kind": "http_get",
                "url": f"{_api_base()}/api/opinion/...",
                "action": str((tool_input or {}).get("action") or ""),
            }
        }
    if name == "access_knowledge_base":
        return {
            "invoke": {
                "kind": "http_get",
                "url": f"{_api_base()}/api/kb2/...",
                "action": str((tool_input or {}).get("action") or ""),
            }
        }
    if name == "run_code":
        return {
            "invoke": {
                "kind": "local_sandbox",
                "language": "python",
                "timeout": int((tool_input or {}).get("timeout") or 30),
            }
        }
    return {"invoke": {"kind": "unknown", "tool": name}}


def _tool_result_for_stream(result: dict[str, Any], *, debug: bool) -> dict[str, Any]:
    if debug:
        out = dict(result)
        c = out.get("content")
        if isinstance(c, str) and len(c) > 50_000:
            out["content"] = c[:50_000] + f"\n…（已截断，原长 {len(c)}）"
        return out
    r: dict[str, Any] = {"ok": result.get("ok")}
    if "http_status" in result:
        r["http_status"] = result["http_status"]
    if "error" in result:
        r["error"] = result["error"]
    if "hint" in result:
        r["hint"] = result["hint"]
    gw = result.get("gateway_response")
    if isinstance(gw, dict):
        r["gateway_response"] = gw
    if "style_keys" in result:
        r["style_keys"] = result["style_keys"]
        r["count"] = result.get("count")
    for k in ("path", "used_relative_path", "resolved_from", "tried_relative_path"):
        if k in result:
            r[k] = result[k]
    c = result.get("content")
    if isinstance(c, str):
        r["content_length"] = len(c)
        r["content_preview"] = c[:1500]
    if "stdout" in result or "stderr" in result:
        r["stdout_preview"] = (result.get("stdout") or "")[:1500]
        r["stderr_preview"] = (result.get("stderr") or "")[:800]
        r["exit_code"] = result.get("exit_code")
    if result.get("items_returned") is not None:
        r["items_returned"] = result.get("items_returned")
    if "total" in result:
        r["total"] = result.get("total")
    if "query_time_ms" in result:
        r["query_time_ms"] = result.get("query_time_ms")
    items = result.get("items")
    if isinstance(items, list) and items:
        titles = []
        for it in items[:8]:
            if isinstance(it, dict) and it.get("title"):
                titles.append(str(it.get("title"))[:120])
        if titles:
            r["titles_preview"] = titles
    return r


def _anthropic_stream_event_to_sse(event: Any) -> dict[str, Any] | None:
    """将 anthropic AsyncMessageStream 的解析后事件映射为下游 SSE。

    依赖 SDK 便利事件（text / thinking / signature）避免重复，
    同时兜底 content_block_delta 以防某些 API 后端不发射便利事件。
    """
    t = getattr(event, "type", None)

    # --- 便利事件：SDK 将原始 delta 聚合后发射，每个 delta 只触发一次 ---
    if t == "text":
        text = getattr(event, "text", "") or ""
        return {"step": "text_delta", "text": text} if text else None
    if t == "thinking":
        text = getattr(event, "thinking", "") or ""
        return {"step": "thinking_delta", "text": text} if text else None
    if t == "signature":
        sig = getattr(event, "signature", "") or ""
        return {"step": "thinking_delta", "signature": sig} if sig else None

    # --- 原始块事件（便利事件已覆盖 text/thinking，此处只处理 API 可能缺失的类型） ---
    if t == "content_block_delta":
        delta = getattr(event, "delta", None)
        if delta is None:
            return None
        dt = getattr(delta, "type", None)
        # text_delta 由上面的 t == "text" 覆盖，不重复发射
        if dt in ("thinking_delta", "signature_delta"):
            return None  # 由 t == "thinking" / "signature" 覆盖
        return None

    if t == "message_start":
        mid = getattr(getattr(event, "message", None), "id", None)
        return {"step": "message_start", "id": mid}
    if t == "message_delta":
        usage = getattr(event, "usage", None)
        u: dict[str, Any] | None = None
        if usage is not None and hasattr(usage, "model_dump"):
            u = usage.model_dump()
        delta_obj = getattr(event, "delta", None)
        sr = getattr(delta_obj, "stop_reason", None) if delta_obj is not None else None
        if not u and not sr:
            return None
        out: dict[str, Any] = {"step": "usage"}
        if u:
            out["usage"] = u
        if sr:
            out["stop_reason"] = sr
        return out
    if t == "content_block_start":
        cb = getattr(event, "content_block", None)
        if cb is None:
            return None
        cbt = getattr(cb, "type", None)
        if cbt == "tool_use":
            return {
                "step": "tool_use_start",
                "tool": getattr(cb, "name", None),
                "tool_use_id": getattr(cb, "id", None),
            }
        return {"step": "content_block_start", "block_type": cbt}
    if t == "message_stop":
        m = getattr(event, "message", None)
        return {
            "step": "assistant_message_stop",
            "stop_reason": getattr(m, "stop_reason", None) if m else None,
            "model": getattr(m, "model", None) if m else None,
        }
    return None


def _resolve_anthropic_key_and_base() -> tuple[str | None, str | None]:
    """与 ccswitch / Claude Code 常见环境对齐：AUTH_TOKEN、BASE_URL。"""
    key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or ""
    ).strip()
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip() or None
    if not key and base:
        key = (os.environ.get("ANTHROPIC_API_KEY_PLACEHOLDER") or "local-proxy").strip()
    return key or None, base


def _float_env(key: str, default: float) -> float:
    try:
        return float((os.environ.get(key) or str(default)).strip())
    except ValueError:
        return default


def _cc_max_tool_rounds() -> int:
    try:
        return max(1, int((os.environ.get("CC_TOOL_MAX_ROUNDS") or "16").strip()))
    except ValueError:
        return 16


def _messages_end_with_tool_results_only(messages: list[dict[str, Any]]) -> bool:
    if not messages or messages[-1].get("role") != "user":
        return False
    lc = messages[-1].get("content")
    if not isinstance(lc, list) or not lc:
        return False
    return all(isinstance(x, dict) and x.get("type") == "tool_result" for x in lc)


def _anthropic_httpx_timeout() -> httpx.Timeout:
    read_s = _float_env("CC_ANTHROPIC_READ_TIMEOUT", 600.0)
    conn_s = _float_env("CC_ANTHROPIC_CONNECT_TIMEOUT", 120.0)
    return httpx.Timeout(read_s, connect=conn_s, pool=conn_s)


def _anthropic_httpx_trust_env() -> bool:
    return (os.environ.get("ANTHROPIC_HTTPX_TRUST_ENV", "1") or "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _anthropic_transport_retries() -> int:
    try:
        return max(0, int((os.environ.get("CC_ANTHROPIC_TRANSPORT_RETRIES") or "2").strip()))
    except ValueError:
        return 2


def _cc_chat_max_concurrent() -> int:
    try:
        return max(1, int((os.environ.get("CC_CHAT_MAX_CONCURRENT") or "1").strip()))
    except ValueError:
        return 1


_cc_anthropic_chat_sem: asyncio.Semaphore | None = None


def _get_cc_anthropic_chat_sem() -> asyncio.Semaphore:
    global _cc_anthropic_chat_sem
    if _cc_anthropic_chat_sem is None:
        _cc_anthropic_chat_sem = asyncio.Semaphore(_cc_chat_max_concurrent())
    return _cc_anthropic_chat_sem


# 按 (api_key, base_url) 缓存的 httpx 连接池，复用 TLS 会话避免握手损耗
_anthropic_http_client_cache: dict[tuple[str, str | None], httpx.AsyncClient] = {}


def _shlex_split_cli(cmd: str) -> list[str]:
    return shlex.split(cmd, posix=os.name != "nt")


async def _cc_chat_via_cli(body: CCChatRequest) -> CCChatResponse:
    raw = (os.environ.get("CC_CLI_CMD") or "").strip()
    if not raw:
        raise HTTPException(
            status_code=503,
            detail="CC_CLI_CMD 未配置。示例：CC_CLI_CMD=python scripts/my_cc_cli.py",
        )
    timeout = float((os.environ.get("CC_CLI_TIMEOUT") or "600").strip())
    argv = _shlex_split_cli(raw)
    if not argv:
        raise HTTPException(status_code=500, detail="CC_CLI_CMD 解析为空")

    payload = json.dumps(
        {"message": body.message, "system": CC_SYSTEM_PROMPT},
        ensure_ascii=False,
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
    )
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(input=payload.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        proc.kill()
        raise HTTPException(
            status_code=504,
            detail=f"CC_CLI_CMD 超时（{timeout}s）",
        ) from e

    err_s = (err_b or b"").decode("utf-8", errors="replace").strip()
    out_s = (out_b or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"CC_CLI_CMD 退出码 {proc.returncode}: {err_s or out_s or '无输出'}",
        )

    reply = out_s
    if out_s:
        try:
            obj = json.loads(out_s)
            if isinstance(obj, dict) and isinstance(obj.get("reply"), str):
                reply = obj["reply"]
        except json.JSONDecodeError:
            pass

    return CCChatResponse(
        reply=reply or "（CLI 无输出）",
        status="ok",
        tool_trace=[],
    )


async def _cc_chat_via_anthropic(body: CCChatRequest) -> CCChatResponse:
    key, base_url = _resolve_anthropic_key_and_base()
    if not key:
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN，且未设置 ANTHROPIC_BASE_URL。"
                "若用 ccswitch 转流，请把当前 profile 的 BASE_URL 与 token 导出到运行 api_server 的环境，"
                "或设置 CC_BACKEND=cli 与 CC_CLI_CMD 调用本地 CLI。"
            ),
        )

    try:
        import anthropic
    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail="请安装: pip install -r requirements-cc.txt（需 anthropic）",
        ) from e

    model = _effective_anthropic_model()
    try:
        max_retries = max(0, int((os.environ.get("CC_ANTHROPIC_MAX_RETRIES") or "2").strip()))
    except ValueError:
        max_retries = 2

    transport = httpx.AsyncHTTPTransport(retries=_anthropic_transport_retries())
    http_client = httpx.AsyncClient(
        transport=transport,
        timeout=_anthropic_httpx_timeout(),
        trust_env=_anthropic_httpx_trust_env(),
    )
    try:
        client = anthropic.AsyncAnthropic(
            api_key=key,
            base_url=base_url.rstrip("/") if base_url else None,
            http_client=http_client,
            max_retries=max_retries,
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": body.message}]
        trace: list[dict[str, Any]] = []
        max_rounds = _cc_max_tool_rounds()
        final_text = ""
        _username = body.username

        for _ in range(max_rounds):
            try:
                msg = await client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=_cc_system_prompt(_username),
                    messages=messages,
                    tools=_active_tools(),
                )
            except anthropic.APITimeoutError as e:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "msg": (
                            "访问 Anthropic/转流接口超时（APITimeoutError）。可尝试："
                            "① 增大 CC_ANTHROPIC_CONNECT_TIMEOUT（默认 120）与 CC_ANTHROPIC_READ_TIMEOUT（默认 600）；"
                            "② 若系统配置了 HTTP_PROXY/HTTPS_PROXY 且异常，设 ANTHROPIC_HTTPX_TRUST_ENV=0 直连；"
                            "③ 检查网络与 ANTHROPIC_BASE_URL 是否可达。"
                        ),
                        "error": str(e),
                    },
                ) from e
            except anthropic.APIConnectionError as e:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "msg": (
                            "无法连接到 Anthropic/转流服务（APIConnectionError）。"
                            "请检查 ANTHROPIC_BASE_URL、防火墙与代理；"
                            "怀疑系统代理导致 TLS 失败时可设 ANTHROPIC_HTTPX_TRUST_ENV=0。"
                            "连续两次请求时第二次易失败：/cc/chat 默认串行（CC_CHAT_MAX_CONCURRENT=1）；"
                            "瞬断可加大 CC_ANTHROPIC_TRANSPORT_RETRIES（当前 httpx 连接层重试）。"
                        ),
                        "error": str(e),
                    },
                ) from e

            if msg.stop_reason == "end_turn" or not any(
                getattr(b, "type", None) == "tool_use" for b in msg.content
            ):
                final_text = _text_from_content(msg.content)
                break

            messages.append({"role": "assistant", "content": msg.content})

            tool_results: list[dict[str, Any]] = []
            for block in msg.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                tool_input = getattr(block, "input", {}) or {}
                tid = block.id
                result = await _run_tool(name, tool_input if isinstance(tool_input, dict) else {}, _username)
                trace.append({"tool": name, "input": tool_input, "result": result})
                log.info("tool %s ok=%s", name, result.get("ok", result.get("http_status") == 200))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            if not tool_results:
                final_text = _text_from_content(msg.content)
                break

            messages.append({"role": "user", "content": tool_results})

        if not final_text and _messages_end_with_tool_results_only(messages):
            try:
                msg = await client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=_cc_system_prompt(_username),
                    messages=messages,
                )
            except anthropic.APITimeoutError as e:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "msg": (
                            "访问 Anthropic/转流接口超时（APITimeoutError）。可尝试："
                            "① 增大 CC_ANTHROPIC_CONNECT_TIMEOUT（默认 120）与 CC_ANTHROPIC_READ_TIMEOUT（默认 600）；"
                            "② 若系统配置了 HTTP_PROXY/HTTPS_PROXY 且异常，设 ANTHROPIC_HTTPX_TRUST_ENV=0 直连；"
                            "③ 检查网络与 ANTHROPIC_BASE_URL 是否可达。"
                        ),
                        "error": str(e),
                    },
                ) from e
            except anthropic.APIConnectionError as e:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "msg": (
                            "无法连接到 Anthropic/转流服务（APIConnectionError）。"
                            "请检查 ANTHROPIC_BASE_URL、防火墙与代理；"
                            "怀疑系统代理导致 TLS 失败时可设 ANTHROPIC_HTTPX_TRUST_ENV=0。"
                            "连续两次请求时第二次易失败：/cc/chat 默认串行（CC_CHAT_MAX_CONCURRENT=1）；"
                            "瞬断可加大 CC_ANTHROPIC_TRANSPORT_RETRIES（当前 httpx 连接层重试）。"
                        ),
                        "error": str(e),
                    },
                ) from e
            final_text = _text_from_content(msg.content)

        if not final_text and trace:
            last = trace[-1].get("result")
            final_text = json.dumps(last, ensure_ascii=False)[:8000]

        if not final_text:
            final_text = "（已达到最大工具轮次，请简化需求后重试。）"

        return CCChatResponse(
            reply=final_text or "（无文本回复）",
            status="ok",
            tool_trace=trace if body.debug else [],
        )
    finally:
        await http_client.aclose()


async def _cc_chat_stream_via_anthropic(
    body: CCChatRequest,
    _cred_override: tuple[str | None, str | None] | None = None,
) -> AsyncIterator[bytes]:
    """Anthropic messages.stream：逐 token 推送 + 工具步骤；可选显式凭证供工作区沙箱复用。"""
    if _cred_override is not None:
        key, base_url = _cred_override
    else:
        key, base_url = _resolve_anthropic_key_and_base()
    if not key:
        yield _format_cc_sse(
            {
                "step": "error",
                "status": "config",
                "detail": (
                    "未配置 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN，且未设置 ANTHROPIC_BASE_URL。"
                    "若用转流，请导出 BASE_URL 与 token；或 CC_BACKEND=cli。"
                ),
            }
        )
        return

    import time as _time
    _t0 = _time.monotonic()
    try:
        import anthropic
    except ImportError:
        yield _format_cc_sse(
            {"step": "error", "status": "config", "detail": "请安装: pip install -r requirements-cc.txt（需 anthropic）"}
        )
        return
    _t_import = _time.monotonic()

    model = _effective_anthropic_model()
    try:
        max_retries = max(0, int((os.environ.get("CC_ANTHROPIC_MAX_RETRIES") or "2").strip()))
    except ValueError:
        max_retries = 2

    cache_key = (key, base_url)
    if cache_key not in _anthropic_http_client_cache:
        transport = httpx.AsyncHTTPTransport(retries=_anthropic_transport_retries())
        _anthropic_http_client_cache[cache_key] = httpx.AsyncClient(
            transport=transport,
            timeout=_anthropic_httpx_timeout(),
            trust_env=_anthropic_httpx_trust_env(),
        )
    http_client = _anthropic_http_client_cache[cache_key]
    _t_client = _time.monotonic()

    try:
        client = anthropic.AsyncAnthropic(
            api_key=key,
            base_url=base_url.rstrip("/") if base_url else None,
            http_client=http_client,
            max_retries=max_retries,
        )
        _t1 = _time.monotonic()
        print(f"[timing] anthropic: init-client={_t1-_t0:.2f}s (import={_t_import-_t0:.2f}s, httpx={_t_client-_t_import:.2f}s, anthropic_client={_t1-_t_client:.2f}s)", flush=True)

        messages: list[dict[str, Any]] = [{"role": "user", "content": body.message}]
        trace: list[dict[str, Any]] = []
        max_rounds = _cc_max_tool_rounds()
        final_text = ""
        _username = body.username

        yield _format_cc_sse(
            {
                "step": "start",
                "backend": "anthropic",
                "model": model,
                "api": "Anthropic Messages API stream (client.messages.stream)",
            }
        )

        for round_idx in range(max_rounds):
            yield _format_cc_sse({"step": "round_start", "round": round_idx + 1})

            _t2 = _time.monotonic()
            print(f"[timing] anthropic: pre-stream={_t2-_t0:.2f}s, calling client.messages.stream", flush=True)
            _first_api_event = True
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=8192,
                    system=_cc_system_prompt(_username),
                    messages=messages,
                    tools=_active_tools(),
                ) as stream:
                    yield _format_cc_sse(
                        {
                            "step": "api",
                            "phase": "anthropic.messages.stream",
                            "round": round_idx + 1,
                        }
                    )
                    async for event in stream:
                        if _first_api_event:
                            _t3 = _time.monotonic()
                            print(f"[timing] anthropic: first-stream-event={_t3-_t0:.2f}s (stream_call→event={_t3-_t2:.2f}s)", flush=True)
                            _first_api_event = False
                        payload = _anthropic_stream_event_to_sse(event)
                        if payload:
                            yield _format_cc_sse(payload)
                    msg = await stream.get_final_message()
            except anthropic.APITimeoutError as e:
                yield _format_cc_sse(
                    {
                        "step": "error",
                        "kind": "APITimeoutError",
                        "detail": {
                            "msg": (
                                "访问 Anthropic/转流超时。可增大 CC_ANTHROPIC_CONNECT_TIMEOUT / "
                                "CC_ANTHROPIC_READ_TIMEOUT；或设 ANTHROPIC_HTTPX_TRUST_ENV=0。"
                            ),
                            "error": str(e),
                        },
                    }
                )
                return
            except anthropic.APIConnectionError as e:
                yield _format_cc_sse(
                    {
                        "step": "error",
                        "kind": "APIConnectionError",
                        "detail": {
                            "msg": (
                                "无法连接 Anthropic/转流。检查 ANTHROPIC_BASE_URL、代理；"
                                "可设 ANTHROPIC_HTTPX_TRUST_ENV=0；避免对 /cc/chat/stream 并发连发（CC_CHAT_MAX_CONCURRENT）。"
                            ),
                            "error": str(e),
                        },
                    }
                )
                return

            if msg.stop_reason == "end_turn" or not any(
                getattr(b, "type", None) == "tool_use" for b in msg.content
            ):
                final_text = _text_from_content(msg.content)
                break

            messages.append({"role": "assistant", "content": msg.content})

            tool_results: list[dict[str, Any]] = []
            for block in msg.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                tid = block.id
                raw_inp = getattr(block, "input", {}) or {}
                tool_input = raw_inp if isinstance(raw_inp, dict) else {}

                ext = _tool_external_call_info(name, tool_input)
                yield _format_cc_sse(
                    {
                        "step": "tool_executing",
                        "round": round_idx + 1,
                        "tool": name,
                        "tool_use_id": tid,
                        "input": tool_input,
                        **ext,
                    }
                )

                result = await _run_tool(name, tool_input, _username)
                trace.append({"tool": name, "input": tool_input, "result": result})
                log.info("stream tool %s ok=%s", name, result.get("ok", result.get("http_status") == 200))

                yield _format_cc_sse(
                    {
                        "step": "tool_finished",
                        "tool": name,
                        "tool_use_id": tid,
                        "result": _tool_result_for_stream(result, debug=body.debug),
                    }
                )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            if not tool_results:
                final_text = _text_from_content(msg.content)
                break

            yield _format_cc_sse(
                {
                    "step": "tool_round_complete",
                    "round": round_idx + 1,
                    "tools": [b.name for b in msg.content if getattr(b, "type", None) == "tool_use"],
                }
            )
            messages.append({"role": "user", "content": tool_results})

        if not final_text and _messages_end_with_tool_results_only(messages):
            yield _format_cc_sse(
                {
                    "step": "final_answer_round",
                    "note": "工具轮次用尽，基于已返回的 tool_result 再请求一轮无工具回复",
                }
            )
            try:
                async with client.messages.stream(
                    model=model,
                    max_tokens=8192,
                    system=_cc_system_prompt(_username),
                    messages=messages,
                ) as stream:
                    yield _format_cc_sse(
                        {
                            "step": "api",
                            "phase": "anthropic.messages.stream",
                            "round": "final",
                        }
                    )
                    async for event in stream:
                        payload = _anthropic_stream_event_to_sse(event)
                        if payload:
                            yield _format_cc_sse(payload)
                    msg = await stream.get_final_message()
                final_text = _text_from_content(msg.content)
            except anthropic.APITimeoutError as e:
                yield _format_cc_sse(
                    {
                        "step": "error",
                        "kind": "APITimeoutError",
                        "detail": {
                            "msg": (
                                "收尾轮访问 Anthropic 超时。可增大 CC_ANTHROPIC_READ_TIMEOUT 等。"
                            ),
                            "error": str(e),
                        },
                    }
                )
                return
            except anthropic.APIConnectionError as e:
                yield _format_cc_sse(
                    {
                        "step": "error",
                        "kind": "APIConnectionError",
                        "detail": {"msg": "收尾轮无法连接转流。", "error": str(e)},
                    }
                )
                return

        if not final_text and trace:
            last = trace[-1].get("result")
            final_text = json.dumps(last, ensure_ascii=False)[:8000]

        if not final_text:
            final_text = "（已达到最大工具轮次，请简化需求后重试。）"

        trace_out = [
            {
                "tool": item["tool"],
                "input": item["input"],
                "result": _tool_result_for_stream(item["result"], debug=body.debug),
            }
            for item in trace
        ]
        done_payload: dict[str, Any] = {
            "step": "done",
            "status": "ok",
            "reply": final_text or "（无文本回复）",
            "debug": body.debug,
        }
        if body.debug:
            done_payload["tool_trace"] = trace_out
        else:
            # 非 debug：最终包只保留摘要，避免一大段失败重试与控制台输出刷屏
            done_payload["tool_calls_count"] = len(trace)
            if trace:
                done_payload["tools_used"] = list(
                    dict.fromkeys(item["tool"] for item in trace)
                )
        yield _format_cc_sse(done_payload)

    finally:
        pass


async def _cc_chat_stream_via_cli(
    body: CCChatRequest,
    *,
    _cmd_override: Optional[list[str]] = None,
    _cwd_override: Optional[str] = None,
    _env_override: Optional[dict[str, str]] = None,
) -> AsyncIterator[bytes]:
    yield _format_cc_sse(
        {
            "step": "start",
            "backend": "cli",
            "note": "CLI 后端流式输出：调用 claude -p --output-format stream-json，逐 token 下发 text_delta。",
        }
    )
    raw = (os.environ.get("CC_CLI_CMD") or "").strip()
    if not raw and not _cmd_override:
        yield _format_cc_sse({"step": "error", "status_code": 503, "detail": "CC_CLI_CMD 未配置"})
        return
    timeout = float((os.environ.get("CC_CLI_TIMEOUT") or "600").strip())
    argv = _cmd_override or _shlex_split_cli(raw)
    if not argv:
        yield _format_cc_sse({"step": "error", "status_code": 500, "detail": "CC_CLI_CMD 解析为空"})
        return

    payload = json.dumps(
        {"message": body.message, "system": CC_SYSTEM_PROMPT},
        ensure_ascii=False,
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_cwd_override or str(REPO_ROOT),
        env=os.environ.copy() if _env_override is None else _env_override,
    )
    try:
        proc.stdin.write(payload.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except Exception as e:
        proc.kill()
        yield _format_cc_sse({"step": "error", "status_code": 500, "detail": f"stdin 写入失败: {e}"})
        return

    text_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
    reader_task = asyncio.create_task(
        _read_cli_stream_json(proc, text_queue)
    )
    _stderr_task = asyncio.create_task(
        _read_cli_stream_stderr(proc)
    )

    reply_parts: list[str] = []
    try:
        async for chunk in _iter_cli_stream_queue(reader_task, proc, timeout, text_queue, reply_parts):
            yield chunk
    except HTTPException as he:
        yield _format_cc_sse({"step": "error", "status_code": he.status_code, "detail": he.detail})
        return

    reply_text = "".join(reply_parts)
    yield _format_cc_sse(
        {
            "step": "done",
            "status": "ok",
            "reply": reply_text or "（CLI 无输出）",
            "tool_trace": [],
        }
    )


async def _cc_chat_stream_via_user_sandbox(body: CCChatRequest) -> AsyncIterator[bytes]:
    """使用用户工作区自己的 API 凭证，经 Anthropic SDK 直接对话（跳过 CLI 进程启动开销）。"""
    import time as _time
    _t0 = _time.monotonic()
    username = body.username
    if not username:
        yield _format_cc_sse({"step": "error", "detail": "缺少用户名"})
        return

    workspace = f"/root/data/workspace/{username}"
    if not os.path.isdir(workspace):
        yield _format_cc_sse({"step": "error", "detail": f"用户 '{username}' 的工作空间不存在"})
        return

    # 读取工作区 settings.json 获取用户自己的 API 配置
    settings_path = os.path.join(workspace, ".claude", "settings.json")
    user_api_key = None
    user_base_url = None
    if os.path.isfile(settings_path):
        try:
            with open(settings_path) as f:
                cfg = json.load(f)
            env_block = cfg.get("env", {})
            user_api_key = (
                env_block.get("ANTHROPIC_API_KEY")
                or env_block.get("ANTHROPIC_AUTH_TOKEN")
                or ""
            ).strip() or None
            user_base_url = (env_block.get("ANTHROPIC_BASE_URL") or "").strip() or None
        except Exception:
            pass
    _t1 = _time.monotonic()
    print(f"[timing] user_sandbox: read settings={_t1-_t0:.2f}s, calling _cc_chat_stream_via_anthropic", flush=True)

    if not user_api_key:
        yield _format_cc_sse({
            "step": "error",
            "status": "config",
            "detail": f"用户 '{username}' 的工作区未配置 ANTHROPIC_API_KEY",
        })
        return

    async for chunk in _cc_chat_stream_via_anthropic(body, (user_api_key, user_base_url)):
        yield chunk


async def _read_cli_stream_json(
    proc: asyncio.subprocess.Process,
    text_queue: asyncio.Queue[tuple[str, str] | None],
) -> None:
    """逐行读取 claude -p stdout 的 stream-json，将 text_delta / thinking_delta 推入队列。"""
    try:
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "stream_event":
                continue
            event = obj.get("event", {})
            if event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta", {})
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                if text:
                    await text_queue.put(("text", text))
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    await text_queue.put(("thinking", thinking))
    finally:
        await text_queue.put(None)  # sentinel


async def _read_cli_stream_stderr(proc: asyncio.subprocess.Process) -> None:
    """消费 stderr，防止子进程阻塞。"""
    while True:
        line_b = await proc.stderr.readline()
        if not line_b:
            break


async def _iter_cli_stream_queue(
    reader_task: asyncio.Task,
    proc: asyncio.subprocess.Process,
    timeout: float,
    text_queue: asyncio.Queue[tuple[str, str] | None],
    reply_parts: list[str],
) -> AsyncIterator[bytes]:
    """从队列实时读取 text_delta/thinking_delta 并下发，直到 sentinel 或超时。"""
    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            raise HTTPException(status_code=504, detail=f"CC_CLI_CMD 超时（{timeout}s）")

        try:
            item = await asyncio.wait_for(text_queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status_code=504, detail=f"CC_CLI_CMD 超时（{timeout}s）")

        if item is None:
            # sentinel — 读取结束
            break

        item_type, content = item
        if item_type == "text":
            reply_parts.append(content)
            yield _format_cc_sse({"step": "text_delta", "text": content})
        elif item_type == "thinking":
            yield _format_cc_sse({"step": "thinking_delta", "text": content})

    # 检查退出码
    ret = await proc.wait()
    if ret != 0:
        err_s = ""
        try:
            if proc.stderr:
                err_data = await asyncio.wait_for(proc.stderr.read(), timeout=5)
                err_s = err_data.decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail=f"CC_CLI_CMD 退出码 {ret}: {err_s or '无输出'}",
        )


# ---------------------------------------------------------------------------
# 路由（挂到 api_server 或独立小应用）
# ---------------------------------------------------------------------------

cc_router = APIRouter(tags=["cc"])


def _repair_json_invalid_backslashes_in_strings(s: str) -> str:
    """
    常见错误：用户在 JSON 字符串里写 Windows 路径（cppt\\ppt-master\\...），\\p 等不是合法 JSON 转义。
    在「字符串字面量」内将非法 \\ 替换为 /，合法转义（\\\\ \\" \\/ \\b \\f \\n \\r \\t \\uXXXX）保留。
    """
    out: list[str] = []
    i = 0
    in_string = False
    nlen = len(s)
    while i < nlen:
        c = s[i]
        if not in_string:
            if c == '"':
                in_string = True
            out.append(c)
            i += 1
            continue
        if c == "\\":
            if i + 1 >= nlen:
                out.append("/")
                i += 1
                continue
            n = s[i + 1]
            if n == "\\":
                out.append("\\\\")
                i += 2
                continue
            if n in '"\\/bfnrt':
                out.append("\\")
                out.append(n)
                i += 2
                continue
            if n == "u" and i + 5 < nlen:
                hx = s[i + 2 : i + 6]
                if len(hx) == 4 and all(
                    ch in "0123456789abcdefABCDEF" for ch in hx
                ):
                    out.append(s[i : i + 6])
                    i += 6
                    continue
            out.append("/")
            i += 1
            continue
        if c == '"':
            in_string = False
        out.append(c)
        i += 1
    return "".join(out)


def _normalize_request_json_text(text: str) -> str:
    """去掉 BOM、弯引号等，减轻 PowerShell / 编辑器带来的非严格 JSON。"""
    t = text.strip()
    if t.startswith("\ufeff"):
        t = t.lstrip("\ufeff")
    for u in ("\u201c", "\u201d", "\u201e", "\u00ab", "\u00bb"):
        t = t.replace(u, '"')
    for u in ("\u2018", "\u2019", "\u02bc"):
        t = t.replace(u, "'")
    return t


def _json_parse_error_hint(first_err: json.JSONDecodeError) -> str:
    msg = f"{first_err.msg} {first_err}".lower()
    if "property name" in msg and "quote" in msg:
        return (
            " 常见原因：在 **Windows PowerShell** 里把含双引号的 JSON 直接交给 "
            "`curl.exe -d $body`，引号会被剥掉，服务端收到类似 `{message:` 的非法 JSON。"
            "请改用文档中的 **`ConvertTo-Json` + `--data-binary \"@路径\"\"`**，或 Bash/Git Bash 里的 curl。"
        )
    return ""


async def _load_cc_chat_request(request: Request) -> CCChatRequest:
    raw = await request.body()
    text = _normalize_request_json_text(raw.decode("utf-8-sig", errors="replace"))
    if not text:
        raise HTTPException(status_code=422, detail="请求体为空")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_err:
        try:
            data = json.loads(_repair_json_invalid_backslashes_in_strings(text))
        except json.JSONDecodeError:
            hint = _json_parse_error_hint(first_err)
            raise HTTPException(
                status_code=422,
                detail=(
                    "JSON 无法解析。若在 message 里写了 Windows 路径，请改用正斜杠 "
                    "（如 cppt/ppt-master/...），或在 JSON 里把反斜杠写成双写 \\\\。"
                    f"{hint} 原始错误: {first_err}"
                ),
            ) from first_err
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="JSON 根必须是对象 { message, debug? }")
    try:
        return CCChatRequest.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e


class CCChatRequest(BaseModel):
    message: str = Field(
        ...,
        description='用户本轮需求。路径建议写成正斜杠 cppt/ppt-master/...，避免 JSON 里单反斜杠转义错误。',
    )
    debug: bool = Field(
        False,
        description=(
            "为 true 时：流式接口中途的 tool_finished 与最终 done 均含较完整的 tool_trace；"
            "为 false 时：最终 done 仅含 tool_calls_count / tools_used，不配长 trace，减少刷屏。"
        ),
    )
    username: Optional[str] = Field(
        None,
        description=(
            "登录用户名。设置后使用该用户工作区沙箱中的 Claude（proot），"
            "而非共享 API 后端。未登录调用时留空。"
        ),
    )


class CCChatResponse(BaseModel):
    reply: str
    status: Literal["ok", "error"]
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


# OpenAPI /docs 说明（Swagger UI 会渲染 description 中的 Markdown 代码块）
_CC_CHAT_API_DESCRIPTION = """\
一次性返回 JSON（非 SSE）。请求体与下方 **Request body** 一致。

### PowerShell

```powershell
$base = "http://127.0.0.1:8765"   # 与 api_server 监听端口一致
$body = @{ message = "你好"; debug = $false } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$base/cc/chat" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
```

### curl（Bash / Git Bash）

```bash
curl -sS -H "Content-Type: application/json" \
  -d '{"message":"你好","debug":false}' \
  http://127.0.0.1:8765/cc/chat
```
"""

_CC_CHAT_STREAM_API_DESCRIPTION = """\
**Server-Sent Events**：响应为 `text/event-stream`，多行 `data: {...}\\n\\n`。事件含 `text_delta`、`tool_executing`（含 `invoke` URL）、`tool_finished`、`done` 等。

请求体与 **Request body** 相同（JSON），**不是** Query 参数。

**Swagger「Try it out」** 常会把整段响应缓冲后再显示，看起来像非流式；要看逐条事件请用下面终端示例。

### PowerShell（推荐：`ConvertTo-Json` + 文件，避免 `-d $json` 弄丢引号）

在 Windows PowerShell 里 **`curl.exe -d $body`** 若 `$body` 内层带双引号，经常把键名的引号剥掉，服务端会报 `Expecting property name enclosed in double quotes`。请用下面写法：

```powershell
$base = "http://127.0.0.1:8765"
$path = Join-Path $env:TEMP "cc_stream_body.json"
@{ message = "总结 session2 下的情报 md"; debug = $true } | ConvertTo-Json -Compress | Set-Content -Path $path -Encoding utf8
curl.exe -sS -N -H "Content-Type: application/json; charset=utf-8" --data-binary "@$path" "$base/cc/chat/stream"
```

无 BOM（适用 Windows PowerShell 5.1）可用：

```powershell
$path = Join-Path $env:TEMP "cc_stream_body.json"
$json = @{ message = "你好"; debug = $false } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($path, $json, [Text.UTF8Encoding]::new($false))
curl.exe -sS -N -H "Content-Type: application/json; charset=utf-8" --data-binary "@$path" http://127.0.0.1:8765/cc/chat/stream
```

说明：`curl.exe` 随 Windows 提供；`--data-binary "@文件"` 把完整 JSON 原样 POST。`-N` 关闭缓冲便于边收边打印。

**`debug`**：`false`（默认）时最终 `done` 只有 `reply` 与 `tool_calls_count` / `tools_used`，不附带大段 `tool_trace`；需要排错再看 `true`。

### PowerShell 7+（`Invoke-WebRequest`，仍可能整包缓冲）

```powershell
$base = "http://127.0.0.1:8765"
$body = '{"message":"你好","debug":false}'
Invoke-WebRequest -Uri "$base/cc/chat/stream" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
# 流式场景更建议用上文的 curl.exe -N
```

### curl（Linux / macOS / Git Bash）

```bash
curl -sS -N -H "Content-Type: application/json" \
  -d '{"message":"总结 session2","debug":true}' \
  http://127.0.0.1:8765/cc/chat/stream
```
"""


def _cc_chat_openapi_request_body() -> dict[str, Any]:
    """Swagger/OpenAPI：路由里用 Request 手解析 JSON（含 Windows 路径修复），需显式声明请求体 schema。"""
    return {
        "requestBody": {
            "required": True,
            "description": "与 POST /cc/chat 相同：application/json 对象",
            "content": {
                "application/json": {
                    "schema": CCChatRequest.model_json_schema(),
                    "examples": {
                        "simple": {
                            "summary": "普通提问",
                            "value": {"message": "你好", "debug": False},
                        },
                        "debug": {
                            "summary": "返回更完整的工具结果（stream 的 done 与 tool_finished）",
                            "value": {"message": "总结 session2 下的情报 md", "debug": True},
                        },
                    },
                }
            },
        }
    }


def _cc_openapi_extra_with_code_samples(*, stream: bool) -> dict[str, Any]:
    extra: dict[str, Any] = {**_cc_chat_openapi_request_body()}
    if stream:
        extra["x-codeSamples"] = [
            {
                "lang": "PowerShell",
                "label": "流式：JSON 落盘 + curl --data-binary @",
                "source": (
                    '$base = "http://127.0.0.1:8765"\n'
                    '$path = Join-Path $env:TEMP "cc_stream_body.json"\n'
                    '@{ message = "总结 session2"; debug = $true } | ConvertTo-Json -Compress | '
                    "Set-Content -Path $path -Encoding utf8\n"
                    'curl.exe -sS -N -H "Content-Type: application/json; charset=utf-8" '
                    '--data-binary "@$path" "$base/cc/chat/stream"\n'
                ),
            },
            {
                "lang": "curl",
                "label": "流式：curl -N",
                "source": (
                    'curl -sS -N -H "Content-Type: application/json" \\\n'
                    '  -d \'{"message":"你好","debug":false}\' \\\n'
                    "  http://127.0.0.1:8765/cc/chat/stream\n"
                ),
            },
        ]
    else:
        extra["x-codeSamples"] = [
            {
                "lang": "PowerShell",
                "label": "Invoke-RestMethod",
                "source": (
                    '$base = "http://127.0.0.1:8765"\n'
                    '$body = @{ message = "你好"; debug = $false } | ConvertTo-Json -Compress\n'
                    'Invoke-RestMethod -Uri "$base/cc/chat" -Method Post -Body $body '
                    '-ContentType "application/json; charset=utf-8"\n'
                ),
            },
            {
                "lang": "curl",
                "label": "一次返回 JSON",
                "source": (
                    'curl -sS -H "Content-Type: application/json" \\\n'
                    '  -d \'{"message":"你好","debug":false}\' \\\n'
                    "  http://127.0.0.1:8765/cc/chat\n"
                ),
            },
        ]
    return extra


@cc_router.get("/cc/health")
def cc_health() -> dict[str, str]:
    return {"status": "ok", "service": "cc_bridge", "note": "与主网关同进程时请用根路径 /health 探活主服务"}


@cc_router.get("/cc/config", dependencies=[Depends(_require_cc_chat_access)])
def cc_config() -> dict[str, Any]:
    k, b = _resolve_anthropic_key_and_base()
    cli = (os.environ.get("CC_CLI_CMD") or "").strip()
    be = (os.environ.get("CC_BACKEND") or "auto").strip().lower()
    return {
        "cc_backend": be,
        "anthropic_model": _effective_anthropic_model(),
        "claude_settings_env_sources": list(_CLAUDE_SETTINGS_SOURCES),
        "anthropic_base_url_configured": bool(b),
        "anthropic_key_or_placeholder": bool(k),
        "anthropic_auth_token_env": bool((os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()),
        "anthropic_api_key_env": bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip()),
        "cc_cli_cmd_configured": bool(cli),
        "local_tools_enabled": _local_tools_enabled(),
        "local_sandbox_root": str(_sandbox_root()),
        "anthropic_connect_timeout_sec": _float_env("CC_ANTHROPIC_CONNECT_TIMEOUT", 120.0),
        "anthropic_read_timeout_sec": _float_env("CC_ANTHROPIC_READ_TIMEOUT", 600.0),
        "anthropic_httpx_trust_env": _anthropic_httpx_trust_env(),
        "anthropic_transport_retries": _anthropic_transport_retries(),
        "cc_chat_max_concurrent": _cc_chat_max_concurrent(),
        "cc_tool_max_rounds": _cc_max_tool_rounds(),
        "cc_chat_stream_path": "/cc/chat/stream",
    }


@cc_router.post(
    "/cc/chat",
    response_model=CCChatResponse,
    summary="CC 对话（JSON 一次返回）",
    description=_CC_CHAT_API_DESCRIPTION,
    openapi_extra=_cc_openapi_extra_with_code_samples(stream=False),
    dependencies=[Depends(_require_cc_chat_access)],
)
async def cc_chat(request: Request) -> CCChatResponse:
    body = await _load_cc_chat_request(request)
    backend = (os.environ.get("CC_BACKEND") or "auto").strip().lower()
    key, _base = _resolve_anthropic_key_and_base()
    cli_cmd = (os.environ.get("CC_CLI_CMD") or "").strip()

    async def anthropic_guarded() -> CCChatResponse:
        async with _get_cc_anthropic_chat_sem():
            return await _cc_chat_via_anthropic(body)

    if backend == "cli":
        return await _cc_chat_via_cli(body)
    if backend == "anthropic":
        return await anthropic_guarded()
    # auto
    if key:
        return await anthropic_guarded()
    if cli_cmd:
        return await _cc_chat_via_cli(body)
    raise HTTPException(
        status_code=503,
        detail=(
            "未检测到可用后端：请配置 ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN，"
            "或 ANTHROPIC_BASE_URL（可配合 ANTHROPIC_API_KEY_PLACEHOLDER），"
            "或设置 CC_CLI_CMD 并任选 CC_BACKEND=cli。"
        ),
    )


async def stream_cc_chat_events(body: CCChatRequest) -> AsyncIterator[bytes]:
    """
    与 POST /cc/chat/stream 相同的 SSE 字节序列，入参已为解析后的 CCChatRequest。
    供主站网关（如 backend/main.py）在拼装 message 后直连 Anthropic/CLI 流。
    （非 HTTP 路由；勿在此函数上加 @cc_router，否则 FastAPI 会把 AsyncIterator 当响应模型而报错。）
    """
    import time as _time
    _t0 = _time.monotonic()
    backend = (os.environ.get("CC_BACKEND") or "auto").strip().lower()
    key, _base = _resolve_anthropic_key_and_base()
    cli_cmd = (os.environ.get("CC_CLI_CMD") or "").strip()
    try:
        # 用户工作区沙箱模式（优先于全局配置）
        if body.username:
            print(f"[timing] stream_cc_chat_events: entering user_sandbox for '{body.username}' at +{_time.monotonic()-_t0:.2f}s", flush=True)
            async for chunk in _cc_chat_stream_via_user_sandbox(body):
                yield chunk
            return
        if backend == "cli":
            async for chunk in _cc_chat_stream_via_cli(body):
                yield chunk
            return
        if backend == "anthropic":
            async with _get_cc_anthropic_chat_sem():
                async for chunk in _cc_chat_stream_via_anthropic(body):
                    yield chunk
            return
        if key:
            async with _get_cc_anthropic_chat_sem():
                async for chunk in _cc_chat_stream_via_anthropic(body):
                    yield chunk
            return
        if cli_cmd:
            async for chunk in _cc_chat_stream_via_cli(body):
                yield chunk
            return
        yield _format_cc_sse(
            {
                "step": "error",
                "status": "config",
                "detail": (
                    "未检测到可用后端：请配置 ANTHROPIC 密钥/转流或 CC_CLI_CMD（与 POST /cc/chat 相同）。"
                ),
            }
        )
    except Exception as e:  # noqa: BLE001
        log.exception("stream_cc_chat_events")
        yield _format_cc_sse({"step": "error", "detail": str(e)})


@cc_router.post(
    "/cc/chat/stream",
    response_model=None,
    summary="CC 流式对话（SSE）",
    description=_CC_CHAT_STREAM_API_DESCRIPTION,
    openapi_extra=_cc_openapi_extra_with_code_samples(stream=True),
    dependencies=[Depends(_require_cc_chat_access)],
    responses={
        200: {
            "description": "text/event-stream（SSE），每行 `data: {json}\\n\\n`",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string", "format": "binary"},
                    "example": 'data: {"step":"start","backend":"anthropic"}\n\n',
                }
            },
        }
    },
)
async def cc_chat_stream(request: Request) -> StreamingResponse:
    """详情见 OpenAPI `description`（含 PowerShell / curl 示例）。"""
    body = await _load_cc_chat_request(request)

    async def event_gen() -> AsyncIterator[bytes]:
        upstream = stream_cc_chat_events(body)
        try:
            async for chunk in upstream:
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Pragma": "no-cache",
        },
    )


def create_standalone_app() -> FastAPI:
    """仅 CC 路由，供 `python cc_bridge.py` 调试。"""
    if is_production() or os.environ.get("CC_STANDALONE_ENABLE", "").strip() != "1":
        raise RuntimeError("CC standalone is disabled; use a non-production environment and CC_STANDALONE_ENABLE=1")
    a = FastAPI(title="CC Bridge (standalone)", version="1.0.0")
    _cors = os.environ.get("CORS_ORIGINS", "http://127.0.0.1:5173")
    _origins = ["*"] if _cors.strip() == "*" else [x.strip() for x in _cors.split(",") if x.strip()]
    a.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    a.include_router(cc_router)
    return a


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    host = os.environ.get("CC_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("CC_BRIDGE_PORT", "8770"))
    uvicorn.run(create_standalone_app(), host=host, port=port, reload=False)
