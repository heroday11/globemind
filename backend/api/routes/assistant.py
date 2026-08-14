"""
Assistant/AI route module: AI analyze (sync+stream), assistant chat (vLLM + CC stream), sessions CRUD.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from api.core.db import SessionLocal, get_db
from api.core.environment import (
    bool_setting,
    float_setting,
    int_setting,
    string_setting,
)
from api.core.runtime_security import is_production
from api.features.assistant import (
    assistant_mode_prompt_id,
    bind_interactive_tool_result,
    finalize_interactive_output,
    interactive_prompt_bundle_receipt,
    MAX_INTERACTIVE_OUTPUT_LENGTH,
    render_registered_prompt,
)
from api.features.identity import provider_base_url_or_none
from api.models.schemas import SearchRequest
from api.orm import models
from api.services.auth import get_current_user_required
from api.services.helpers import extract_source_from_url
from api.services.hermes_assistant import (
    call_hermes_once,
    resolve_hermes_config,
    stream_hermes_chat_events,
    stream_hermes_tool_agent_events,
)
from api.services.news_search_v2 import NEWS_ENGINE, search_dashboard_v2

router = APIRouter(prefix="")
WORKSPACE_ROOT = Path(string_setting("GLOBEMIND_WORKSPACE_ROOT", "/root/data/workspace"))
CODE_ROOT = Path(string_setting("RELEASE_DIR", str(Path(__file__).resolve().parents[3]))).resolve()
_DEFAULT_FRONTEND_DIST = CODE_ROOT / "frontend-dist"
if not is_production() and not _DEFAULT_FRONTEND_DIST.is_dir():
    _DEFAULT_FRONTEND_DIST = CODE_ROOT / "frontend/vue_project/dist"
FRONTEND_DIST_ROOT = Path(
    string_setting(
        "FRONTEND_DIST",
        string_setting("GLOBEMIND_FRONTEND_DIST_ROOT", str(_DEFAULT_FRONTEND_DIST)),
    )
).resolve()
FRONTEND_PUBLIC_ROOT = Path(
    string_setting("GLOBEMIND_FRONTEND_PUBLIC_ROOT", str(FRONTEND_DIST_ROOT))
).resolve()
FRONTEND_PUBLIC_DATASETS_ROOT = (FRONTEND_PUBLIC_ROOT / "datasets").resolve()
GENERATED_ASSET_ROOT = Path(
    string_setting("GLOBEMIND_GENERATED_ASSET_ROOT", "/root/data/web/generated-assets")
).resolve()
HERMES_GENERATED_IMG_DIRNAME = "hermes-generated"
HERMES_IMAGE_SCRIPT = Path(
    string_setting(
        "HERMES_IMAGE_SCRIPT",
        str(CODE_ROOT / "backend/cppt/ppt-master/skills/ppt-master/scripts/image_gen.py"),
    )
).resolve()
HERMES_IMAGE_ENV_FILE = Path(
    string_setting("HERMES_IMAGE_ENV_FILE", "/root/data/globemind/backend/cppt/ppt-master/.env")
).resolve()
if is_production():
    try:
        HERMES_IMAGE_SCRIPT.relative_to(CODE_ROOT)
    except ValueError as exc:
        raise RuntimeError("HERMES_IMAGE_SCRIPT must remain inside the immutable release") from exc
ASSISTANT_IMAGE_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
ASSISTANT_IMAGE_SIZES = {"512px", "1K", "2K", "4K"}
SAFE_WORKSPACE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")
ASSISTANT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".xml", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".less", ".sql", ".sh", ".env", ".ini", ".cfg", ".conf",
    ".toml", ".gradle", ".properties",
}
ASSISTANT_KB_CATEGORY_DIRS: Dict[str, Tuple[str, str]] = {
    "geo": ("GEO", "地缘政治"),
    "mil": ("MIL", "军事安全"),
    "econ": ("ECO", "经济贸易"),
    "tech": ("TEC", "科技情报"),
    "social": ("PUB", "社会舆情"),
    "law": ("LAW", "法律法规"),
}

# ────────────────────────
# Pydantic models
# ────────────────────────

class AIAnalyzeRequest(BaseModel):
    text: Optional[str] = Field(None, description="待分析的文本内容")
    data: Optional[Dict[str, Any]] = Field(None, description="可选的结构化数据，将转为简要描述后分析")


class AIAnalyzeResponse(BaseModel):
    analysis: str


class AssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=12000)
    top_k_news: int = Field(8, ge=1, le=20)
    top_k_clusters: int = Field(8, ge=1, le=20)
    session_id: Optional[int] = Field(None, description="登录用户：会话 ID")
    user_visible_message: Optional[str] = Field(None, max_length=4000, description="界面短问句，入库优先")
    mode: Literal["fast", "pro", "expert"] = Field("pro", description="回答模式：fast 快速 / pro 研判 / expert 专家")


class AssistantChatResponse(BaseModel):
    reply: str
    news_hits: List[Dict[str, Any]] = Field(default_factory=list)
    cluster_hits: List[Dict[str, Any]] = Field(default_factory=list)
    citation_assurance: Dict[str, Any] = Field(default_factory=dict)
    prompt_registry: Dict[str, Any] = Field(default_factory=dict)


class AssistantCCStreamRequest(AssistantChatRequest):
    debug: bool = Field(False, description="CC：是否在流式 done/tool 中带更完整工具信息")
    pinned_workspace: Optional[str] = Field(None, description="用户当前置顶的工作区名称，用作工作目录")
    favorite_context: Optional[Dict[str, Any]] = Field(None, description="当前固定收藏文件夹及素材卡片")
    knowledge_context: Optional[Dict[str, Any]] = Field(None, description="当前启用的 Skill 与数据库连接卡片")
    tool_mode: Literal["auto", "context_only"] = Field(
        "auto",
        description="工具策略：auto 按需调用；context_only 仅基于随请求提供的页面材料作答",
    )


class AssistantSessionCreateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=256)


@dataclass(frozen=True)
class AssistantPlatformToolPlan:
    name: str
    label: str
    params: SearchRequest
    retry_without_time: bool = True
    terms: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AssistantModeConfig:
    key: str
    label: str
    max_tool_calls: int
    max_tokens: int
    temperature: float
    prompt: str


def _assistant_mode_config(raw: str | None) -> AssistantModeConfig:
    mode = (raw or "pro").strip().lower()
    if mode not in {"fast", "pro", "expert"}:
        mode = "pro"
    registered = render_registered_prompt(assistant_mode_prompt_id(mode))
    parameters = registered.spec.model_parameters
    return AssistantModeConfig(
        key=mode,
        label={"fast": "快速", "pro": "研判", "expert": "专家"}[mode],
        max_tool_calls=int(parameters["max_tool_calls"]),
        max_tokens=int(parameters["max_tokens"]),
        temperature=float(parameters["temperature"]),
        prompt=registered.text,
    )


# ────────────────────────
# Helper functions
# ────────────────────────

def _resolve_ai_raw_content(body: AIAnalyzeRequest) -> str:
    raw_content = body.text
    if not raw_content and body.data is not None:
        raw_content = str(body.data)
    if not raw_content or not str(raw_content).strip():
        raise HTTPException(status_code=400, detail="请提供 text 或 data 内容")
    return str(raw_content).strip()


def _extract_user_question_for_search(message: str) -> str:
    """从带报告上下文的 message 中取出用户原问句，供关键词检索。"""
    raw = (message or "").strip()
    m = re.search(r"【用户问题】\s*\n(.+)$", raw, re.S)
    if m:
        return m.group(1).strip()[:800]
    return raw[:800]


def _assistant_workspace_username(user: Dict[str, Any]) -> str:
    username = str(user.get("username") or "").strip()
    if not username or not SAFE_WORKSPACE_USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=400, detail="当前用户名不能作为安全工作区目录")
    return username


def _resolve_assistant_workspace(user: Dict[str, Any], workspace_name: str) -> Tuple[Path, Path]:
    name = str(workspace_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="工作区名称不能为空")
    username = _assistant_workspace_username(user)
    user_root = (WORKSPACE_ROOT / username).resolve()
    target = (user_root / name).resolve()
    try:
        target.relative_to(user_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="工作区路径越界，已被用户沙箱拦截")
    if not target.is_dir() or not (target / ".workspace.json").is_file():
        raise HTTPException(status_code=404, detail="工作区不存在或无权访问")
    return target, user_root


def _assistant_safe_child(root: Path, *parts: str) -> Path:
    root_resolved = root.resolve()
    target = root_resolved.joinpath(
        *[str(part).replace("\\", "/") for part in parts if str(part)]
    ).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="路径越界，已被用户沙箱拦截")
    return target


def _assistant_file_item(entry: Path) -> Dict[str, Any]:
    stat = entry.stat()
    return {
        "name": entry.name,
        "is_dir": entry.is_dir(),
        "size": stat.st_size if entry.is_file() else 0,
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _assistant_read_text_path(file_path: Path, *, max_chars: int = 12000) -> Dict[str, Any]:
    if not file_path.is_file():
        return {"ok": False, "error": "文件不存在"}
    ext = file_path.suffix.lower()
    if ext not in ASSISTANT_TEXT_EXTENSIONS:
        return {"ok": False, "error": f"不支持读取该文件类型：{ext or '(无扩展名)'}"}
    max_chars = min(max(int(max_chars or 12000), 1000), 30000)
    max_bytes = min(max_chars * 4 + 1024, 2_000_000)
    try:
        with file_path.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as e:
        return {"ok": False, "error": f"读取文件失败：{e}"}
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "文件编码不支持读取"}
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {
        "ok": True,
        "content": content,
        "ext": ext,
        "chars": len(content),
        "truncated": truncated,
    }


def _compact_context_items(items: Any, *, limit: int = 20) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in items[:limit]:
        if not isinstance(raw, dict):
            continue
        item: Dict[str, Any] = {}
        for key in ("id", "title", "name", "source", "time", "url", "desc", "abstract", "type", "host", "port", "purpose", "domainName"):
            val = raw.get(key)
            if val is None:
                continue
            text = str(val).strip()
            if not text:
                continue
            item[key] = text[:500]
        if item:
            out.append(item)
    return out


def _assistant_context_raw_items(body: Optional[AssistantCCStreamRequest], kind: str) -> List[Dict[str, Any]]:
    if not body:
        return []
    if kind == "favorites":
        fav = body.favorite_context if isinstance(body.favorite_context, dict) else None
        raw_items = fav.get("items") if fav else []
    else:
        know = body.knowledge_context if isinstance(body.knowledge_context, dict) else None
        raw_items = know.get("skills" if kind == "skills" else "database_cards") if know else []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


_ASSISTANT_CONTEXT_LIST_KEYS = {
    "id", "title", "name", "source", "time", "type", "domainName", "host", "port",
    "database", "tags", "createdAt", "description", "nameZh", "descriptionZh",
    "status", "license", "runtime", "quality", "hasSkillMd", "activationHint",
    "activationHintZh", "tasksZh", "knowledgeInputsZh", "safetyZh",
}
_ASSISTANT_CONTEXT_DETAIL_KEYS = {
    *_ASSISTANT_CONTEXT_LIST_KEYS,
    "url", "desc", "abstract", "purpose", "username", "domainId", "tasks",
    "knowledgeInputs", "requires", "safety", "sourceRepo", "skillPath", "localPath",
    "artifactPath", "progressiveDisclosure", "installMode", "upstreamReferenceUrl",
    "sourceAvailableNotice",
}
_ASSISTANT_SENSITIVE_KEY_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)", re.I)


def _assistant_sanitize_context_item(raw: Dict[str, Any], *, detailed: bool) -> Dict[str, Any]:
    allowed = _ASSISTANT_CONTEXT_DETAIL_KEYS if detailed else _ASSISTANT_CONTEXT_LIST_KEYS
    out: Dict[str, Any] = {}
    for key, val in raw.items():
        key_str = str(key)
        if key_str not in allowed or _ASSISTANT_SENSITIVE_KEY_RE.search(key_str):
            continue
        if val is None:
            continue
        if isinstance(val, list):
            clean_list = [str(x).strip()[:80] for x in val if str(x).strip()]
            if clean_list:
                out[key_str] = clean_list[:12]
            continue
        if isinstance(val, (dict, tuple, set)):
            continue
        text = str(val).strip()
        if not text:
            continue
        out[key_str] = text[:2000 if detailed else 220]
    return out


def _assistant_read_public_dataset_text(public_path: str, *, max_chars: int) -> Dict[str, Any]:
    raw_path = str(public_path or "").strip()
    if not raw_path.startswith("/datasets/"):
        return {"ok": False, "error": "只允许读取 /datasets/ 下的公开 skill 文件"}
    try:
        rel_path = Path(raw_path.lstrip("/"))
        target = (FRONTEND_PUBLIC_ROOT / rel_path).resolve()
        target.relative_to(FRONTEND_PUBLIC_DATASETS_ROOT)
    except Exception:
        return {"ok": False, "error": "路径不在公开 datasets 目录内"}
    if not target.is_file():
        return {"ok": False, "error": "公开 skill 文件不存在"}
    if target.suffix.lower() not in ASSISTANT_TEXT_EXTENSIONS:
        return {"ok": False, "error": "只允许读取文本类型 skill 文件"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _assistant_short_error(e)}
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "path": raw_path,
        "chars": len(text),
        "truncated": truncated,
        "content": text[:max_chars],
    }


def _assistant_context_list_result(
    *,
    tool_name: str,
    label: str,
    items: List[Dict[str, Any]],
    args: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    page = _assistant_tool_int(args, "page", 1, 1, 1000)
    page_size = _assistant_tool_int(args, "page_size", 8, 1, 30)
    start = (page - 1) * page_size
    sliced = items[start : start + page_size]
    payload: Dict[str, Any] = {
        "ok": True,
        "tool": tool_name,
        "label": label,
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "items_returned": len(sliced),
        "items": [
            {"index": start + idx + 1, **_assistant_sanitize_context_item(item, detailed=False)}
            for idx, item in enumerate(sliced)
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def _assistant_find_context_item(items: List[Dict[str, Any]], args: Dict[str, Any]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    idx_raw = args.get("index")
    if idx_raw not in (None, ""):
        try:
            idx = int(idx_raw)
            if 1 <= idx <= len(items):
                return idx, items[idx - 1]
        except Exception:
            pass
    wanted = (
        _assistant_tool_arg_str(args, "id")
        or _assistant_tool_arg_str(args, "name")
        or _assistant_tool_arg_str(args, "title")
    ).strip().lower()
    if not wanted:
        return None, None
    for idx, item in enumerate(items, 1):
        candidates = [
            item.get("id"),
            item.get("name"),
            item.get("title"),
            item.get("host"),
        ]
        if any(str(candidate or "").strip().lower() == wanted for candidate in candidates):
            return idx, item
    return None, None


def _assistant_selected_context_block(body: AssistantCCStreamRequest) -> str:
    parts: List[str] = []
    fav = body.favorite_context if isinstance(body.favorite_context, dict) else None
    if fav:
        folder = str(fav.get("folder") or "").strip()[:120]
        count = len(_assistant_context_raw_items(body, "favorites"))
        if folder or count:
            parts.append(
                "【固定收藏文件夹｜渐进式读取】"
                f"folder={folder or '未命名'}；count={count}。"
                "不要把收藏素材当作已读取全文；需要标题列表时调用 selected_favorites_list，"
                "需要具体素材详情时调用 selected_favorite_read。"
            )

    know = body.knowledge_context if isinstance(body.knowledge_context, dict) else None
    if know:
        skills = _compact_context_items(know.get("skills"), limit=12)
        dbs = _compact_context_items(know.get("database_cards"), limit=8)
        lines: List[str] = []
        if skills:
            skill_names: List[str] = []
            for item in skills:
                name = item.get("name") or item.get("title") or item.get("id") or "未命名 Skill"
                domain = item.get("domainName") or item.get("type") or ""
                skill_names.append(f"{name}{f'/{domain}' if domain else ''}"[:80])
            lines.append(
                "【已启用 Skill｜渐进式读取】"
                f"count={len(_assistant_context_raw_items(body, 'skills'))}；"
                f"visible={'; '.join(skill_names)}。"
                "不要假设已读取 Skill 正文；需要 Skill 指令时调用 selected_skill_read，"
                "它会按需返回对应 SKILL.md；需要完整列表时调用 selected_skill_list。"
            )
        if dbs:
            db_names: List[str] = []
            for item in dbs:
                name = item.get("name") or item.get("title") or "未命名数据库"
                kind = item.get("type") or ""
                db_names.append(f"{name}{f'/{kind}' if kind else ''}"[:80])
            lines.append(
                "【已启用数据库连接卡片｜渐进式读取】"
                f"count={len(_assistant_context_raw_items(body, 'databases'))}；"
                f"visible={'; '.join(db_names)}。"
                "数据库卡片只是连接说明，不代表已经连库；需要详情时调用 selected_database_read；"
                "需要完整列表时调用 selected_database_list。"
            )
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


_ASSISTANT_CN_STOPWORDS = {
    "请问", "请", "帮我", "帮忙", "分析", "一下", "哪些", "有什么", "有哪些",
    "如何", "怎么", "为什么", "是否", "是不是", "当前", "近期", "最近",
    "相关", "情况", "影响", "趋势", "原因", "问题", "数据", "基于", "进行",
    "一次", "一个", "这个", "那个", "以及", "和", "与", "对", "的", "了",
    "你好", "您好", "在吗", "谢谢", "感谢", "好的", "收到", "再见", "拜拜",
}

_ASSISTANT_DATA_INTENT_MARKERS = {
    "分析", "检索", "查询", "搜索", "查找", "看看", "新闻", "报道", "事件", "舆情",
    "聚类", "宏观", "趋势", "影响", "风险", "原因", "对比", "数据", "证据", "来源",
    "出口", "进口", "贸易", "销量", "订单", "同比", "环比", "增长", "下降", "制裁",
    "关税", "冲突", "战争", "外交", "政策", "市场", "供应链", "最近", "近期", "今天",
    "昨日", "昨天", "本周", "本月", "有哪些", "有什么", "为什么", "如何",
}

_ASSISTANT_SMALLTALK_RE = re.compile(
    r"^(你好|您好|哈喽|hello|hi|hey|在吗|谢谢|感谢|辛苦了|好的|ok|嗯|哦|收到|再见|拜拜)[\s。.!！?？~～]*$",
    re.I,
)

_ASSISTANT_META_RE = re.compile(
    r"^(你是谁|你是什么|你能做什么|你可以做什么|怎么用|如何使用|帮助|help|介绍一下自己|自我介绍)[\s。.!！?？]*$",
    re.I,
)

_ASSISTANT_TERM_ALIASES = {
    "中国": ["China", "Chinese", "PRC", "Beijing", "中國"],
    "欧洲": ["Europe", "European", "EU", "欧盟"],
    "欧盟": ["EU", "European Union", "Europe", "欧洲"],
    "美国": ["United States", "US", "U.S.", "America"],
    "俄罗斯": ["Russia", "Russian"],
    "乌克兰": ["Ukraine", "Ukrainian"],
    "以色列": ["Israel", "Israeli"],
    "伊朗": ["Iran", "Iranian"],
    "中东": ["Middle East"],
    "南海": ["South China Sea"],
    "台湾": ["Taiwan"],
    "高温": ["heatwave", "heat wave", "extreme heat", "high temperature"],
    "极端高温": ["extreme heat", "heatwave", "heat wave"],
    "热浪": ["heatwave", "heat wave", "extreme heat"],
    "空调": ["air conditioner", "air conditioners", "air conditioning", "HVAC"],
    "出口": ["export", "exports", "shipment", "shipments"],
    "进口": ["import", "imports"],
    "贸易": ["trade"],
    "关税": ["tariff", "tariffs"],
    "制裁": ["sanction", "sanctions"],
    "供应链": ["supply chain"],
    "需求": ["demand"],
}


def _dedupe_keep_order(values: List[str], *, limit: int = 40) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _assistant_question_terms(message: str) -> List[str]:
    """给平台检索使用的保守关键词。中文无分词依赖，先命中领域词，再兜底 2-4 字短语。"""
    question = _extract_user_question_for_search(message)
    cleaned = re.sub(r"[^\w\u4e00-\u9fff+.-]+", " ", question).strip()
    candidates: List[str] = []

    lowered = cleaned.lower()
    for term in sorted(_ASSISTANT_TERM_ALIASES.keys(), key=len, reverse=True):
        if term.lower() in lowered or term in cleaned:
            candidates.append(term)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}", cleaned):
        if token.lower() not in {"the", "and", "with", "from", "into", "what", "why", "how"}:
            candidates.append(token)

    # 领域词不足时，从中文连续片段里抽取可检索的短词。优先 4/3/2 字，过滤虚词。
    if len(candidates) < 4:
        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned):
            segment = re.sub(
                r"(请问|帮我|帮忙|分析|一下|有哪些|有什么|如何|怎么|为什么|是否|当前|近期|最近|相关|情况|影响|趋势|原因|基于|数据|进行)",
                " ",
                segment,
            )
            for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", segment):
                if 2 <= len(chunk) <= 4 and chunk not in _ASSISTANT_CN_STOPWORDS:
                    candidates.append(chunk)
                else:
                    for n in (4, 3, 2):
                        for i in range(0, max(0, len(chunk) - n + 1)):
                            gram = chunk[i : i + n]
                            if gram and gram not in _ASSISTANT_CN_STOPWORDS:
                                candidates.append(gram)
                            if len(candidates) >= 12:
                                break
                        if len(candidates) >= 12:
                            break
                if len(candidates) >= 12:
                    break
            if len(candidates) >= 12:
                break

    return _dedupe_keep_order(candidates, limit=12) or [question[:80]]


def _assistant_tool_intent_text(message: str) -> str:
    question = _extract_user_question_for_search(message)
    text_value = re.sub(r"\s+", " ", str(question or "")).strip()
    text_value = re.sub(r"^(你好|您好|哈喽|hello|hi|hey)[，,。\s!！?？~～]+", "", text_value, flags=re.I).strip()
    return text_value


def _assistant_should_use_platform_tools(message: str) -> bool:
    text_value = _assistant_tool_intent_text(message)
    compact = re.sub(r"[\s，,。.!！?？~～]+", "", text_value).strip()
    if not compact:
        return False
    if re.search(r"(不要|无需|不需要|禁止)(调用|使用)?(任何)?(工具|检索|搜索)", compact):
        return False
    if _ASSISTANT_SMALLTALK_RE.fullmatch(compact) or _ASSISTANT_META_RE.fullmatch(compact):
        return False
    lowered = text_value.lower()
    if any(marker.lower() in lowered for marker in _ASSISTANT_DATA_INTENT_MARKERS):
        return True
    if any(term.lower() in lowered or term in text_value for term in _ASSISTANT_TERM_ALIASES):
        return True
    # 单个短关键词在数据助手里通常是搜索意图，但排除明显闲聊/代词类输入。
    if 2 <= len(compact) <= 8 and not re.search(r"(你|我|他|她|它|这个|那个|什么|怎么)$", compact):
        return True
    return False


def _assistant_requires_evidence(message: str) -> bool:
    """Detect factual/data intent even when the user explicitly disables tools."""

    text_value = _assistant_tool_intent_text(message)
    text_value = re.sub(
        r"(不要|无需|不需要|禁止)(调用|使用)?(任何)?(工具|检索|搜索)",
        "",
        text_value,
    )
    compact = re.sub(r"[\s，,。.!！?？~～]+", "", text_value).strip()
    if not compact:
        return False
    if _ASSISTANT_SMALLTALK_RE.fullmatch(compact) or _ASSISTANT_META_RE.fullmatch(compact):
        return False
    if _assistant_should_use_platform_tools(text_value):
        return True
    # Pure transformations of text supplied by the user do not introduce a
    # platform factual claim. Everything else defaults to evidence-required.
    if re.search(r"(改写|润色|校对|翻译|压缩|扩写|格式化)(这段|以下|下面|上述)?(文字|文本|内容)?", compact):
        return False
    return True


def _assistant_alias_terms(terms: List[str]) -> List[str]:
    aliases: List[str] = []
    for term in terms:
        aliases.extend(_ASSISTANT_TERM_ALIASES.get(term, []))
    return _dedupe_keep_order(aliases, limit=24)


def _assistant_term_groups(terms: List[str] | Tuple[str, ...]) -> List[List[str]]:
    groups: List[List[str]] = []
    for term in terms:
        variants = [term, *_ASSISTANT_TERM_ALIASES.get(term, [])]
        clean_variants = _dedupe_keep_order([v for v in variants if len(str(v).strip()) >= 2], limit=8)
        if clean_variants:
            groups.append(clean_variants)
    return groups[:10]


def _assistant_relevance_score(text_value: str, terms: List[str] | Tuple[str, ...]) -> int:
    haystack = str(text_value or "").lower()
    if not haystack:
        return 0
    score = 0
    for group in _assistant_term_groups(terms):
        if any(str(v).lower() in haystack for v in group):
            score += 1
    return score


def _assistant_min_relevance_score(terms: List[str] | Tuple[str, ...]) -> int:
    n = len(terms)
    if n >= 5:
        return 3
    if n >= 3:
        return 2
    return 1


def _assistant_build_platform_tool_plans(body: AssistantCCStreamRequest) -> List[AssistantPlatformToolPlan]:
    if not bool_setting("ASSISTANT_PLATFORM_TOOLS", True):
        return []
    if not _assistant_should_use_platform_tools(body.message):
        return []
    terms = _assistant_question_terms(body.message)
    aliases = _assistant_alias_terms(terms)
    keyword = " ".join([*terms[:10], *aliases[:14]]).strip() or _extract_user_question_for_search(body.message)[:120]
    news_k = min(max(int(body.top_k_news or 8), 3), 20)
    cluster_k = min(max(int(body.top_k_clusters or 8), 3), 20)
    common = {
        "keyword": keyword,
        "any_include": None,
        "publish_time": "近一年",
        "page": 1,
        "sort_by": "pub_time",
        "sort_order": "desc",
    }
    return [
        AssistantPlatformToolPlan(
            name="assistant_news_evidence_search",
            label="新闻检索",
            params=SearchRequest(
                **common,
                page_size=news_k,
                mode="fuzzy",
                search_type="news",
            ),
            terms=tuple(terms),
        ),
        AssistantPlatformToolPlan(
            name="event_coref_l1_search",
            label="L1 事件聚类检索",
            params=SearchRequest(
                **common,
                page_size=cluster_k,
                mode="event_coref",
                search_type="l1",
            ),
            terms=tuple(terms),
        ),
        AssistantPlatformToolPlan(
            name="macro_l2_search",
            label="L2 宏观事件检索",
            params=SearchRequest(
                **common,
                page_size=cluster_k,
                mode="fuzzy",
                search_type="l2",
            ),
            terms=tuple(terms),
        ),
    ]


def _assistant_model_dump(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return jsonable_encoder(obj)


def _assistant_trim_text(value: Any, limit: int) -> str:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text_value) > limit:
        return text_value[: max(0, limit - 1)] + "…"
    return text_value


def _assistant_compact_news_item(item: Any) -> Dict[str, Any]:
    row = _assistant_model_dump(item)
    return {
        "id": row.get("id"),
        "title": _assistant_trim_text(row.get("title"), 180),
        "source": row.get("source") or "",
        "location": row.get("location") or row.get("language_id") or "",
        "pub_time": row.get("pub_time"),
        "url": row.get("request_url") or "",
        "abstract": _assistant_trim_text(row.get("abstract") or row.get("body"), 320),
        "cluster_title": _assistant_trim_text(row.get("cluster_title"), 120),
    }


def _assistant_compact_cluster_item(item: Any) -> Dict[str, Any]:
    row = _assistant_model_dump(item)
    cluster_id = row.get("cluster_id") or row.get("id")
    title = (
        row.get("title")
        or row.get("dominant_trigger")
        or " / ".join(x for x in [row.get("event_type"), row.get("initiator"), row.get("target")] if x)
    )
    return {
        "id": cluster_id,
        "title": _assistant_trim_text(title, 180),
        "event_type": row.get("event_type") or row.get("event_family") or "",
        "initiator": row.get("initiator") or "",
        "target": row.get("target") or "",
        "article_count": row.get("article_count") or row.get("member_count") or 0,
        "story_count": row.get("story_count") or row.get("cluster_count") or 0,
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "summary": _assistant_trim_text(row.get("summary"), 260),
    }


def _assistant_response_hit_count(resp: Any) -> int:
    return (
        len(getattr(resp, "data", None) or [])
        + len(getattr(resp, "event_coref_clusters", None) or [])
        + len(getattr(resp, "micro_story_items", None) or [])
        + len(getattr(resp, "macro_event_items", None) or [])
    )


def _assistant_short_error(e: Exception) -> str:
    return _assistant_trim_text(f"{type(e).__name__}: {e}", 700)


def _assistant_build_score_sql(
    terms: Tuple[str, ...],
    *,
    columns: Tuple[str, ...],
) -> Tuple[str, Dict[str, Any]]:
    groups = _assistant_term_groups(terms)
    bind: Dict[str, Any] = {}
    score_parts: List[str] = []
    for gi, group in enumerate(groups[:8]):
        predicates: List[str] = []
        for vi, variant in enumerate(group[:7]):
            key = f"g{gi}_{vi}"
            bind[key] = f"%{variant}%"
            predicates.extend(f"{col} ILIKE :{key}" for col in columns)
        if predicates:
            score_parts.append(f"CASE WHEN ({' OR '.join(predicates)}) THEN 1 ELSE 0 END")
    return (" + ".join(score_parts) if score_parts else "0"), bind


def _assistant_build_score_and_filter_sql(
    terms: Tuple[str, ...],
    *,
    columns: Tuple[str, ...],
) -> Tuple[str, str, Dict[str, Any]]:
    groups = _assistant_term_groups(terms)
    bind: Dict[str, Any] = {}
    score_parts: List[str] = []
    filter_parts: List[str] = []
    for gi, group in enumerate(groups[:8]):
        predicates: List[str] = []
        for vi, variant in enumerate(group[:7]):
            key = f"g{gi}_{vi}"
            bind[key] = f"%{variant}%"
            predicates.extend(f"{col} ILIKE :{key}" for col in columns)
        if predicates:
            clause = f"({' OR '.join(predicates)})"
            score_parts.append(f"CASE WHEN {clause} THEN 1 ELSE 0 END")
            filter_parts.append(clause)
    score_sql = " + ".join(score_parts) if score_parts else "0"
    filter_sql = " OR ".join(filter_parts) if filter_parts else "FALSE"
    return score_sql, filter_sql, bind


def _assistant_execute_news_evidence_search_sync(
    plan: AssistantPlatformToolPlan,
) -> Dict[str, Any]:
    params = plan.params
    terms = tuple(plan.terms)
    limit = min(max(int(params.page_size or 8), 3), 20)
    min_score = _assistant_min_relevance_score(terms)

    def _query(*, with_time: bool) -> Tuple[List[Dict[str, Any]], float]:
        score_sql, title_filter_sql, bind = _assistant_build_score_and_filter_sql(
            terms,
            columns=("n.title",),
        )
        bind["min_score"] = min_score
        bind["limit"] = limit
        where = [f"({title_filter_sql})"]
        if with_time:
            where.append("n.published_at >= NOW() - INTERVAL '365 days'")
        sql = text(
            f"""
            WITH scored AS MATERIALIZED (
              SELECT
                n.id,
                COALESCE(NULLIF(n.title, ''), '') AS title,
                LEFT(COALESCE(n.body, ''), 900) AS body,
                n.url AS request_url,
                n.published_at AS pub_time,
                n.language AS language_id,
                ({score_sql}) AS relevance_score
              FROM public.news n
              WHERE {' AND '.join(where)}
            ),
            ranked AS (
              SELECT *, COUNT(*) OVER() AS total_count
              FROM scored
              WHERE relevance_score >= :min_score
            )
            SELECT
              id,
              title,
              body,
              request_url,
              pub_time,
              language_id,
              relevance_score,
              total_count
            FROM ranked
            ORDER BY relevance_score DESC, pub_time DESC NULLS LAST, id DESC
            LIMIT :limit
            """
        )
        started = time.time()
        timeout_ms = int_setting("ASSISTANT_DB_TOOL_STATEMENT_TIMEOUT_MS", 25_000)
        with NEWS_ENGINE.connect() as conn:
            with conn.begin():
                conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                rows = conn.execute(sql, bind).mappings().fetchall()
        return [dict(r) for r in rows], (time.time() - started) * 1000

    retried = False
    try:
        rows, query_ms = _query(with_time=True)
        if not rows and plan.retry_without_time:
            rows, query_ms = _query(with_time=False)
            retried = True
        news = []
        for row in rows:
            news.append(
                {
                    "id": int(row["id"]),
                    "title": _assistant_trim_text(row.get("title"), 180),
                    "source": extract_source_from_url(row.get("request_url") or ""),
                    "location": row.get("language_id") or "",
                    "pub_time": row.get("pub_time"),
                    "url": row.get("request_url") or "",
                    "abstract": _assistant_trim_text(row.get("body"), 320),
                    "cluster_title": "",
                    "relevance_score": int(row.get("relevance_score") or 0),
                }
            )
        return {
            "ok": True,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(params),
            "retried_without_time": retried,
            "total": int(rows[0].get("total_count") or len(rows)) if rows else 0,
            "query_time_ms": round(float(query_ms), 1),
            "news": news,
            "clusters": [],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(params),
            "error": _assistant_short_error(e),
        }


def _assistant_execute_l1_evidence_search_sync(
    plan: AssistantPlatformToolPlan,
) -> Dict[str, Any]:
    params = plan.params
    terms = tuple(plan.terms[:6])
    limit = min(max(int(params.page_size or 8), 3), 20)
    min_score = _assistant_min_relevance_score(terms)
    try:
        score_sql, bind = _assistant_build_score_sql(
            terms,
            columns=(
                "COALESCE(title, '')",
                "COALESCE(dominant_trigger, '')",
            ),
        )
        bind["min_score"] = min_score
        bind["limit"] = limit
        sql = text(
            f"""
            SELECT
              cluster_id,
              COALESCE(NULLIF(title, ''), dominant_trigger, event_type, '') AS title,
              event_type,
              initiator,
              target,
              dominant_trigger,
              article_count,
              start_date,
              end_date,
              ({score_sql}) AS relevance_score,
              COUNT(*) OVER() AS total_count
            FROM public.event_coref_clusters
            WHERE ({score_sql}) >= :min_score
            ORDER BY relevance_score DESC, article_count DESC NULLS LAST
            LIMIT :limit
            """
        )
        started = time.time()
        timeout_ms = int_setting("ASSISTANT_DB_TOOL_STATEMENT_TIMEOUT_MS", 25_000)
        with NEWS_ENGINE.connect() as conn:
            with conn.begin():
                conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                rows = conn.execute(sql, bind).mappings().fetchall()
        clusters = [
            {
                "id": row.get("cluster_id"),
                "title": _assistant_trim_text(row.get("title") or row.get("dominant_trigger"), 180),
                "event_type": row.get("event_type") or "",
                "initiator": row.get("initiator") or "",
                "target": row.get("target") or "",
                "article_count": int(row.get("article_count") or 0),
                "story_count": 0,
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "summary": "",
                "relevance_score": int(row.get("relevance_score") or 0),
            }
            for row in rows
        ]
        return {
            "ok": True,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(params),
            "total": int(rows[0].get("total_count") or len(rows)) if rows else 0,
            "query_time_ms": round((time.time() - started) * 1000, 1),
            "news": [],
            "clusters": clusters,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(params),
            "error": _assistant_short_error(e),
        }


def _assistant_execute_platform_tool_sync(
    plan: AssistantPlatformToolPlan,
    user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if plan.name == "assistant_news_evidence_search":
        return _assistant_execute_news_evidence_search_sync(plan)
    if plan.name == "event_cluster_search":
        return _assistant_execute_l1_evidence_search_sync(plan)

    db = SessionLocal()
    try:
        params = plan.params
        started = time.time()
        resp = search_dashboard_v2(params, user=user, app_db=db, start_ts=started)
        retried = False
        if (
            plan.retry_without_time
            and getattr(params, "publish_time", None)
            and _assistant_response_hit_count(resp) == 0
        ):
            retry_params = params.model_copy(update={"publish_time": None})
            started = time.time()
            resp = search_dashboard_v2(retry_params, user=user, app_db=db, start_ts=started)
            params = retry_params
            retried = True

        news = [_assistant_compact_news_item(x) for x in (getattr(resp, "data", None) or [])[:20]]
        min_score = _assistant_min_relevance_score(plan.terms)
        if plan.terms:
            news = [
                item for item in news
                if _assistant_relevance_score(
                    " ".join(str(item.get(k) or "") for k in ("title", "abstract", "cluster_title")),
                    plan.terms,
                ) >= min_score
            ]
        clusters: List[Dict[str, Any]] = []
        for seq in (
            getattr(resp, "event_coref_clusters", None) or [],
            getattr(resp, "micro_story_items", None) or [],
            getattr(resp, "macro_event_items", None) or [],
        ):
            clusters.extend(_assistant_compact_cluster_item(x) for x in seq[:20])
        if plan.terms:
            clusters = [
                item for item in clusters
                if _assistant_relevance_score(
                    " ".join(str(item.get(k) or "") for k in ("title", "summary", "initiator", "target", "event_type")),
                    plan.terms,
                ) >= min_score
            ]
        raw_total = int(getattr(resp, "total", 0) or 0)
        filtered_total = len(news) + len(clusters[:20])
        return {
            "ok": True,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(params),
            "retried_without_time": retried,
            "total": filtered_total,
            "raw_total": raw_total,
            "query_time_ms": round(float(getattr(resp, "query_time_ms", 0.0) or 0.0), 1),
            "news": news,
            "clusters": clusters[:20],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": plan.name,
            "label": plan.label,
            "params": _assistant_model_dump(plan.params),
            "error": _assistant_short_error(e),
        }
    finally:
        db.close()


def _assistant_merge_tool_hits(tool_results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    news: List[Dict[str, Any]] = []
    clusters: List[Dict[str, Any]] = []
    seen_news: set[str] = set()
    seen_clusters: set[str] = set()
    for result in tool_results:
        for item in result.get("news") or []:
            key = str(item.get("id") or item.get("title") or "")
            if not key or key in seen_news:
                continue
            seen_news.add(key)
            news.append(item)
        for item in result.get("clusters") or []:
            key = f"{result.get('tool')}:{item.get('id') or item.get('title') or ''}"
            if not key or key in seen_clusters:
                continue
            seen_clusters.add(key)
            clusters.append(item)
    return news[:30], clusters[:30]


def _assistant_merge_web_sources(tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    web_sources: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for result in tool_results:
        if result.get("tool") != "web_search":
            continue
        items: List[Dict[str, Any]] = []
        for item in result.get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append(
                {
                    "title": _assistant_trim_text(item.get("title") or "", 180),
                    "url": url,
                    "page_age": item.get("page_age") or "",
                    "snippet": _assistant_trim_text(item.get("snippet") or "", 220),
                    "citation_source_id": item.get("citation_source_id") or "",
                }
            )
        if not items and not result.get("summary"):
            continue
        web_sources.append(
            {
                "tool": "web_search",
                "label": result.get("label") or "联网搜索",
                "source": result.get("source") or "external_web",
                "query": result.get("query") or (result.get("params") or {}).get("query") or "",
                "search_queries": result.get("search_queries") or [],
                "usage": result.get("usage") or {},
                "query_time_ms": result.get("query_time_ms"),
                "summary": _assistant_trim_text(result.get("summary") or "", 1000),
                "results": items[:12],
            }
        )
    return web_sources[:8]


def _assistant_tool_evidence_block(tool_results: List[Dict[str, Any]]) -> str:
    if not tool_results:
        return ""
    lines: List[str] = [
        "【平台真实检索结果】",
        "以下结果由后端在本轮回答前真实执行。你必须基于这些结果作答；不得声称又调用了其他接口，也不得把未命中的数据当成已验证事实。",
        "如果用户问题需要出口量、订单量、销量、同比等贸易统计，而检索结果没有给出这些数字，必须明确说明“当前平台检索未提供可量化贸易统计”，只能做方向性影响分析。",
    ]
    for result in tool_results:
        status = "成功" if result.get("ok") else "失败"
        params = result.get("params") or {}
        lines.append(
            f"\n工具：{result.get('label') or result.get('tool')}（{status}）"
            f"\n参数：keyword={params.get('keyword')!r}, mode={params.get('mode')!r}, search_type={params.get('search_type')!r}, publish_time={params.get('publish_time')!r}"
        )
        if not result.get("ok"):
            lines.append(f"错误：{result.get('error')}")
            continue
        lines.append(
            f"命中：total={result.get('total', 0)}, 返回新闻={len(result.get('news') or [])}, 返回事件={len(result.get('clusters') or [])}"
            + ("；已放宽时间范围重试" if result.get("retried_without_time") else "")
        )
        for idx, item in enumerate((result.get("news") or [])[:6], 1):
            lines.append(
                f"新闻{idx}: id={item.get('id')} | {item.get('title')} | {item.get('source')} | {item.get('pub_time')} | 摘要：{item.get('abstract')}"
            )
        for idx, item in enumerate((result.get("clusters") or [])[:6], 1):
            lines.append(
                f"事件{idx}: id={item.get('id')} | {item.get('title')} | articles={item.get('article_count')} | {item.get('start_date') or ''}~{item.get('end_date') or ''}"
            )
    lines.append(
        "\n回答要求：直接回答用户问题，不要输出“检索方案/正在执行/假设接口返回”。先给结论，再列证据与不确定性，最后给需要补充的数据。控制在 1200 字以内并完整收束。"
    )
    return "\n".join(lines)


def _assistant_hermes_tool_schemas(
    body: Optional[AssistantCCStreamRequest] = None,
) -> List[Dict[str, Any]]:
    time_enum = ["近一天", "近一周", "近一月", "近三月", "近一年"]
    common_props: Dict[str, Any] = {
        "keyword": {
            "type": "string",
            "description": "检索关键词。用用户问题中的核心实体、事件、行业、国家和英文同义词组成，不要传问候语。",
        },
        "publish_time": {
            "type": "string",
            "enum": time_enum,
            "description": "相对时间范围。用户未指定时默认近一年。",
        },
        "start_time": {"type": "string", "description": "可选，ISO 日期或日期时间。"},
        "end_time": {"type": "string", "description": "可选，ISO 日期或日期时间。"},
        "page_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "返回条数，默认 8。",
        },
    }
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "news_search",
                "description": (
                    "搜索 GlobeMind 新闻库，获取可引用的新闻证据。"
                    "仅当用户需要新闻、事实证据、趋势、影响、风险、贸易/出口等数据分析时调用；"
                    "问候、闲聊、使用说明不要调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": common_props,
                    "required": ["keyword"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "macro_event_search",
                "description": (
                    "搜索 L2/L3 宏观事件链，用于判断问题是否关联更大走势或宏观事件。"
                    "仅在用户需要宏观事件、长期趋势、影响链条时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        **common_props,
                        "level": {
                            "type": "string",
                            "enum": ["l2", "l3"],
                            "description": "宏观层级，默认 l2。",
                        },
                    },
                    "required": ["keyword"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_favorites_list",
                "description": (
                    "列出当前固定收藏文件夹中的素材索引。"
                    "当用户要求基于收藏素材、辅助资料或右侧素材栏作答时，先调用该工具查看可用素材。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "minimum": 1, "description": "页码，默认 1。"},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 30, "description": "每页数量，默认 8。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_favorite_read",
                "description": (
                    "读取当前固定收藏文件夹中的单条素材详情。"
                    "应在 selected_favorites_list 之后，按 index 或 id 读取真正需要的素材。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 1, "description": "素材在 selected_favorites_list 中的 1-based index。"},
                        "id": {"type": "string", "description": "可选，收藏新闻/素材 id。"},
                        "title": {"type": "string", "description": "可选，标题精确匹配。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_skill_list",
                "description": "列出当前启用的 Skill 索引。需要理解可用方法/能力约束时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "minimum": 1, "description": "页码，默认 1。"},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 30, "description": "每页数量，默认 8。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_skill_read",
                "description": "读取当前启用的单个 Skill 详情，并在可用时返回真实 SKILL.md。应按 index、id 或 name 读取需要的 Skill。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 1, "description": "Skill 的 1-based index。"},
                        "id": {"type": "string", "description": "可选，Skill id。"},
                        "name": {"type": "string", "description": "可选，Skill 名称精确匹配。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_database_list",
                "description": "列出当前启用的数据库连接卡片索引。需要知道有哪些连接说明时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "minimum": 1, "description": "页码，默认 1。"},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 30, "description": "每页数量，默认 8。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "selected_database_read",
                "description": (
                    "读取当前启用的单个数据库连接卡片详情。"
                    "该工具只返回连接说明，不会执行 SQL，也不会证明数据库已经连通。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 1, "description": "数据库卡片的 1-based index。"},
                        "id": {"type": "string", "description": "可选，数据库卡片 id。"},
                        "name": {"type": "string", "description": "可选，数据库卡片名称精确匹配。"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_list_files",
                "description": (
                    "列出用户已固定工作区中的文件和目录。"
                    "仅当用户要求查看当前工作区文件、基于工作区资料回答，或需要先确认文件名时调用。"
                    "该工具只访问当前登录用户沙箱内的固定工作区。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subpath": {
                            "type": "string",
                            "description": "可选，工作区内相对目录；不传则列根目录。",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_read_file",
                "description": (
                    "读取用户已固定工作区中的文本文件内容。"
                    "仅当用户要求查看/分析工作区里的具体文件，或回答必须依赖该文件内容时调用。"
                    "不得读取未固定工作区或沙箱外路径。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "工作区内相对文件路径，例如 report.md 或 docs/data.csv。",
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 30000,
                            "description": "最多返回字符数，默认 12000。",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "knowledge_list_files",
                "description": (
                    "列出当前登录用户知识库某个分类下的文件。"
                    "仅当用户要求查看知识库资料，或需要基于知识库文件回答时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": list(ASSISTANT_KB_CATEGORY_DIRS.keys()),
                            "description": "知识库分类：geo/mil/econ/tech/social/law。",
                        },
                    },
                    "required": ["category"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "knowledge_read_file",
                "description": (
                    "读取当前登录用户知识库分类下的文本文件内容。"
                    "仅当用户要求查看/分析知识库中的具体文件，或回答必须依赖该文件内容时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": list(ASSISTANT_KB_CATEGORY_DIRS.keys()),
                            "description": "知识库分类：geo/mil/econ/tech/social/law。",
                        },
                        "filename": {
                            "type": "string",
                            "description": "知识库分类目录内的文件名。",
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 30000,
                            "description": "最多返回字符数，默认 12000。",
                        },
                    },
                    "required": ["category", "filename"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "image_generate",
                "description": (
                    "调用已配置的图片生成模型生成原创位图，并返回可打开的图片 URL。"
                    "仅当用户明确要求生成、绘制、设计图片/海报/封面/插图/PPT 配图/视觉素材时调用；"
                    "新闻分析、普通问答、问候和只需要文字方案的请求不要调用。"
                    "调用成功前不得声称图片已经生成。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "图片生成提示词。要写清主体、场景、风格、构图、文字限制和用途，默认用中文或用户指定语言。",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
                            "description": "画幅比例，默认 1:1；海报常用 3:4/4:5，封面和幻灯片常用 16:9。",
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["512px", "1K", "2K", "4K"],
                            "description": "图片尺寸档位，默认 1K。除非用户明确要求高清，优先 1K。",
                        },
                        "negative_prompt": {
                            "type": "string",
                            "description": "可选，排除不希望出现的元素，例如低清、变形、乱码文字、水印。",
                        },
                        "filename_hint": {
                            "type": "string",
                            "description": "可选，用于生成文件名的短英文/拼音提示，不要包含路径。",
                        },
                        "model": {
                            "type": "string",
                            "description": "可选，显式指定图片模型；一般留空使用后端默认配置。",
                        },
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "通过 DeepSeek Anthropic Web Search server tool 执行外部联网搜索，返回公开网页来源、引用和摘要。"
                    "仅当用户明确要求联网/网上查/搜索，或问题依赖最新公开网页、官网、文档、价格、政策、人物/公司状态时调用；"
                    "问候、闲聊、平台已有新闻库能覆盖的常规历史问题不要调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询词。应简洁、具体，优先保留实体、时间、地点和关键限定词。",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 12,
                            "description": "返回来源数量上限，默认 8。",
                        },
                        "max_uses": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "description": "DeepSeek server-side web search 调用次数上限，默认 1；复杂比较可提高到 3-5。",
                        },
                        "allowed_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "可选，只搜索这些域名。不要和 blocked_domains 同时使用。",
                        },
                        "blocked_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "可选，排除这些域名。不要和 allowed_domains 同时使用。",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if body is None:
        return schemas

    favorite_context = body.favorite_context if isinstance(body.favorite_context, dict) else {}
    knowledge_context = body.knowledge_context if isinstance(body.knowledge_context, dict) else {}
    has_favorites = bool(favorite_context.get("items"))
    has_skills = bool(knowledge_context.get("skills"))
    has_databases = bool(knowledge_context.get("database_cards"))
    message = str(body.message or "")
    lowered = message.lower()
    wants_web = bool(re.search(r"(联网|互联网|网上|网页|官网|公开网站|web\s*search|online\s*search)", lowered, re.I))
    wants_image = bool(re.search(r"(生成|绘制|设计).{0,8}(图片|海报|封面|插图|配图)|image\s*generate", lowered, re.I))
    wants_knowledge = bool(re.search(r"(知识库|knowledge\s*base)", lowered, re.I))
    unavailable = set()
    if not has_favorites:
        unavailable.update({"selected_favorites_list", "selected_favorite_read"})
    if not has_skills:
        unavailable.update({"selected_skill_list", "selected_skill_read"})
    if not has_databases:
        unavailable.update({"selected_database_list", "selected_database_read"})
    if not body.pinned_workspace:
        unavailable.update({"workspace_list_files", "workspace_read_file"})
    if not wants_knowledge:
        unavailable.update({"knowledge_list_files", "knowledge_read_file"})
    if not wants_web:
        unavailable.add("web_search")
    if not wants_image:
        unavailable.add("image_generate")

    return [
        schema
        for schema in schemas
        if str((schema.get("function") or {}).get("name") or "") not in unavailable
    ]


def _assistant_tool_arg_str(args: Dict[str, Any], key: str, default: str = "") -> str:
    value = args.get(key, default)
    return str(value or "").strip()


def _assistant_tool_page_size(args: Dict[str, Any], default: int = 8) -> int:
    try:
        return min(max(int(args.get("page_size") or default), 1), 20)
    except Exception:
        return default


def _assistant_tool_int(args: Dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(args.get(key) or default), low), high)
    except Exception:
        return default


def _assistant_tool_str_list(args: Dict[str, Any], key: str, *, limit: int = 20) -> List[str]:
    raw = args.get(key)
    values: List[str] = []
    if isinstance(raw, str):
        raw_items = [x.strip() for x in re.split(r"[,，\s]+", raw) if x.strip()]
    elif isinstance(raw, list):
        raw_items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        raw_items = []
    for item in raw_items:
        item = item.replace("https://", "").replace("http://", "").strip("/")
        if item and item not in values:
            values.append(item)
        if len(values) >= limit:
            break
    return values


def _assistant_deepseek_anthropic_messages_url(raw_base: str) -> str:
    base = (raw_base or "https://api.deepseek.com/anthropic").strip().rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _assistant_deepseek_web_config(user_row: Optional[models.User]) -> Tuple[str, str, str]:
    cfg = resolve_hermes_config(user_row)
    cfg_base = (cfg.base_url or "").strip()
    cfg_model = (cfg.model or "").strip()
    cfg_is_deepseek = "deepseek" in (cfg.source or "").lower() or "api.deepseek.com" in cfg_base.lower()

    env_base = string_setting("HERMES_BASE_URL")
    env_model = string_setting("HERMES_MODEL")
    env_is_deepseek = "api.deepseek.com" in env_base.lower() or env_model.lower().startswith("deepseek")
    anthropic_env_base = string_setting("ANTHROPIC_BASE_URL")
    anthropic_env_is_deepseek = "api.deepseek.com" in anthropic_env_base.lower()

    anthropic_base = (
        string_setting("HERMES_WEB_SEARCH_ANTHROPIC_BASE_URL")
        or string_setting("DEEPSEEK_ANTHROPIC_BASE_URL")
        or (anthropic_env_base if anthropic_env_is_deepseek else "")
        or "https://api.deepseek.com/anthropic"
    )
    api_key = (
        string_setting("HERMES_WEB_SEARCH_API_KEY")
        or (cfg.api_key if cfg_is_deepseek else "")
        or string_setting("PUBLIC_DEEPSEEK_API_KEY")
        or string_setting("DEEPSEEK_API_KEY")
        or (string_setting("HERMES_API_KEY") if env_is_deepseek else "")
        or (string_setting("ANTHROPIC_AUTH_TOKEN") if anthropic_env_is_deepseek else "")
        or (string_setting("ANTHROPIC_API_KEY") if anthropic_env_is_deepseek else "")
        or ""
    ).strip()
    model = (
        string_setting("HERMES_WEB_SEARCH_MODEL")
        or (cfg_model if cfg_is_deepseek else "")
        or string_setting("PUBLIC_DEEPSEEK_MODEL")
        or string_setting("DEEPSEEK_MODEL")
        or (env_model if env_is_deepseek else "")
        or "deepseek-v4-flash"
    ).strip()
    return anthropic_base, api_key, model


def _assistant_extract_deepseek_web_response(data: Dict[str, Any], max_results: int) -> Dict[str, Any]:
    content = data.get("content") or []
    if not isinstance(content, list):
        content = []
    text_parts: List[str] = []
    search_queries: List[str] = []
    results: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_urls: set[str] = set()

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if text:
                text_parts.append(text)
            for citation in block.get("citations") or []:
                if not isinstance(citation, dict):
                    continue
                if citation.get("url") and citation.get("url") not in seen_urls:
                    citations.append(
                        {
                            "title": citation.get("title") or "",
                            "url": citation.get("url"),
                            "cited_text": _assistant_trim_text(citation.get("cited_text") or "", 220),
                        }
                    )
        elif block_type == "server_tool_use":
            tool_input = block.get("input") or {}
            if isinstance(tool_input, dict) and tool_input.get("query"):
                query = str(tool_input.get("query") or "").strip()
                if query and query not in search_queries:
                    search_queries.append(query)
        elif block_type == "web_search_tool_result":
            raw_content = block.get("content")
            if isinstance(raw_content, dict):
                err = raw_content.get("error_code") or raw_content.get("type")
                if err:
                    errors.append(str(err))
                continue
            if not isinstance(raw_content, list):
                continue
            for item in raw_content:
                if not isinstance(item, dict) or item.get("type") != "web_search_result":
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "title": _assistant_trim_text(item.get("title") or "", 180),
                        "url": url,
                        "page_age": item.get("page_age") or "",
                    }
                )
                if len(results) >= max_results:
                    break
    for citation in citations:
        url = str(citation.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(
            {
                "title": _assistant_trim_text(citation.get("title") or "", 180),
                "url": url,
                "page_age": "",
                "snippet": citation.get("cited_text") or "",
            }
        )
        if len(results) >= max_results:
            break

    summary = "\n".join(text_parts).strip()
    summary_chars = int_setting("HERMES_WEB_SEARCH_SUMMARY_CHARS", 1800)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "summary": _assistant_trim_text(summary, summary_chars),
        "search_queries": search_queries[:8],
        "results": results[:max_results],
        "citations": citations[:max_results],
        "errors": errors,
        "usage": usage,
    }


def _assistant_execute_web_search_sync(
    args: Dict[str, Any],
    user_row: Optional[models.User],
) -> Dict[str, Any]:
    query = _assistant_tool_arg_str(args, "query") or _assistant_tool_arg_str(args, "keyword")
    if not query:
        return {"ok": False, "tool": "web_search", "label": "联网搜索", "error": "query 不能为空"}
    query = query[:500]
    max_results = _assistant_tool_int(args, "max_results", 8, 1, 12)
    max_uses = _assistant_tool_int(args, "max_uses", 1, 1, 5)
    timeout_s = float_setting("HERMES_WEB_SEARCH_TIMEOUT_SEC", 8.0)
    anthropic_base, api_key, model = _assistant_deepseek_web_config(user_row)
    if not api_key:
        return {
            "ok": False,
            "tool": "web_search",
            "label": "联网搜索",
            "source": "deepseek_anthropic_web_search",
            "query": query,
            "error": (
                "未配置 DeepSeek Web Search API key。请配置用户 DeepSeek provider，"
                "或设置 HERMES_WEB_SEARCH_API_KEY / PUBLIC_DEEPSEEK_API_KEY / DEEPSEEK_API_KEY。"
            ),
        }

    tool_def: Dict[str, Any] = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
    }
    allowed_domains = _assistant_tool_str_list(args, "allowed_domains")
    blocked_domains = _assistant_tool_str_list(args, "blocked_domains")
    if allowed_domains:
        tool_def["allowed_domains"] = allowed_domains
    elif blocked_domains:
        tool_def["blocked_domains"] = blocked_domains

    prompt = (
        f"请使用 Web Search 搜索这个问题：{query}\n"
        f"最多保留 {max_results} 个高相关来源。只输出不超过 220 字的中文要点摘要，"
        "不要逐条展开来源列表；来源 URL 会由工具结果结构化返回。"
        "除非该问题必须拆成多个搜索，否则只调用一次 web_search。"
    )
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": int_setting("HERMES_WEB_SEARCH_MAX_TOKENS", 900),
        "temperature": 0.1,
        "system": "你是 GlobeMind 的联网搜索执行器。必须调用 web_search，并返回可追踪来源。",
        "messages": [{"role": "user", "content": prompt}],
        "tools": [tool_def],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
    }
    url = _assistant_deepseek_anthropic_messages_url(anthropic_base)
    started = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=3.0), trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400 and "tool_choice" in payload:
                retry_payload = dict(payload)
                retry_payload.pop("tool_choice", None)
                resp = client.post(url, headers=headers, json=retry_payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "web_search",
            "label": "联网搜索",
            "source": "deepseek_anthropic_web_search",
            "query": query,
            "endpoint": url,
            "model": model,
            "error": _assistant_short_error(e),
        }

    parsed = _assistant_extract_deepseek_web_response(data, max_results=max_results)
    ok = bool(parsed.get("results") or parsed.get("summary")) and not parsed.get("errors")
    return {
        "ok": ok,
        "tool": "web_search",
        "label": "联网搜索",
        "source": "deepseek_anthropic_web_search",
        "query": query,
        "params": {
            "query": query,
            "max_results": max_results,
            "max_uses": max_uses,
            "allowed_domains": allowed_domains,
            "blocked_domains": blocked_domains,
        },
        "model": model,
        "query_time_ms": round((time.time() - started) * 1000, 1),
        "search_queries": parsed.get("search_queries") or [],
        "results_count": len(parsed.get("results") or []),
        "results": parsed.get("results") or [],
        "titles_preview": [x.get("title") for x in (parsed.get("results") or [])[:3] if x.get("title")],
        "citations": parsed.get("citations") or [],
        "summary": parsed.get("summary") or "",
        "usage": parsed.get("usage") or {},
        "errors": parsed.get("errors") or [],
    }


def _assistant_slug_filename(value: str, default: str = "image") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return (text or default)[:48]


def _assistant_load_env_file_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        if not path.is_file():
            return values
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
    except Exception:
        return values
    return values


def _assistant_json_obj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _assistant_normalize_qwen_base_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/services/aigc/multimodal-generation/generation"):
        return base
    if base.endswith("/api/v1"):
        return f"{base}/services/aigc/multimodal-generation/generation"
    return f"{base}/api/v1/services/aigc/multimodal-generation/generation"


def _assistant_user_image_config(user_row: Optional[models.User]) -> Dict[str, str]:
    if user_row is None:
        return {}
    keys = _assistant_json_obj(getattr(user_row, "api_keys", None))
    image = keys.get("image") if isinstance(keys.get("image"), dict) else {}

    def pick(*names: str) -> str:
        for name in names:
            value = image.get(name) if isinstance(image, dict) else None
            if value is None:
                value = keys.get(name)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    api_key = pick(
        "api_key",
        "openai_api_key",
        "qwen_api_key",
        "image_api_key",
        "image_openai_api_key",
        "image_qwen_api_key",
        "qwen_image",
    )
    if not api_key:
        return {}
    default_backend = string_setting("HERMES_IMAGE_BACKEND", "openai").lower()
    backend = (pick("backend", "image_backend") or default_backend).lower()
    user_base_url = provider_base_url_or_none(
        pick(
            "base_url",
            "openai_base_url",
            "qwen_base_url",
            "image_base_url",
            "image_openai_base_url",
            "image_qwen_base_url",
        )
    ) or ""
    return {
        "backend": backend,
        "api_key": api_key,
        "base_url": user_base_url,
        "model": pick(
            "model",
            "openai_model",
            "qwen_model",
            "image_model",
            "image_openai_model",
            "image_qwen_model",
        ),
    }


def _assistant_apply_image_config_to_env(env: Dict[str, str], cfg: Dict[str, str]) -> None:
    backend = (cfg.get("backend") or string_setting("HERMES_IMAGE_BACKEND", "openai")).strip().lower()
    env["IMAGE_BACKEND"] = backend
    api_key = (cfg.get("api_key") or "").strip()
    base_url = (cfg.get("base_url") or "").strip()
    model = (cfg.get("model") or "").strip()
    if backend == "qwen":
        if api_key:
            env["QWEN_API_KEY"] = api_key
        if base_url:
            env["QWEN_BASE_URL"] = _assistant_normalize_qwen_base_url(base_url)
        if model:
            env["QWEN_MODEL"] = model
    elif backend == "openai":
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        if base_url:
            env["OPENAI_BASE_URL"] = base_url.rstrip("/")
        if model:
            env["OPENAI_MODEL"] = model


def _assistant_prepare_image_env(
    user_row: Optional[models.User] = None,
    *,
    use_user_config: bool = False,
) -> Dict[str, str]:
    env = os.environ.copy()
    for key, value in _assistant_load_env_file_values(HERMES_IMAGE_ENV_FILE).items():
        env.setdefault(key, value)

    if not bool_setting("HERMES_IMAGE_USE_PROXY"):
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        env.setdefault("NO_PROXY", "*")
        env.setdefault("no_proxy", "*")

    aliases = {
        "HERMES_IMAGE_BACKEND": "IMAGE_BACKEND",
        "HERMES_IMAGE_QWEN_API_KEY": "QWEN_API_KEY",
        "HERMES_IMAGE_QWEN_BASE_URL": "QWEN_BASE_URL",
        "HERMES_IMAGE_QWEN_MODEL": "QWEN_MODEL",
        "HERMES_IMAGE_DASHSCOPE_API_KEY": "DASHSCOPE_API_KEY",
        "HERMES_IMAGE_OPENAI_API_KEY": "OPENAI_API_KEY",
        "HERMES_IMAGE_OPENAI_BASE_URL": "OPENAI_BASE_URL",
        "HERMES_IMAGE_OPENAI_MODEL": "OPENAI_MODEL",
    }
    for source, target in aliases.items():
        value = string_setting(source)
        if value:
            env[target] = value

    backend = (env.get("IMAGE_BACKEND") or "").strip().lower()
    generic_key = string_setting("HERMES_IMAGE_API_KEY")
    generic_model = string_setting("HERMES_IMAGE_MODEL")
    generic_base = string_setting("HERMES_IMAGE_BASE_URL")
    if generic_key and backend == "qwen":
        env["QWEN_API_KEY"] = generic_key
    if generic_key and backend == "openai":
        env["OPENAI_API_KEY"] = generic_key
    if generic_model and backend == "qwen":
        env["QWEN_MODEL"] = generic_model
    if generic_model and backend == "openai":
        env["OPENAI_MODEL"] = generic_model
    if generic_base and backend == "qwen":
        env["QWEN_BASE_URL"] = generic_base
    if generic_base and backend == "openai":
        env["OPENAI_BASE_URL"] = generic_base.rstrip("/")
    if env.get("QWEN_BASE_URL"):
        env["QWEN_BASE_URL"] = _assistant_normalize_qwen_base_url(env["QWEN_BASE_URL"])
    if use_user_config:
        _assistant_apply_image_config_to_env(env, _assistant_user_image_config(user_row))
    return env


def _assistant_should_retry_image_with_user(result: Dict[str, Any]) -> bool:
    if result.get("ok"):
        return False
    error = str(result.get("error") or "").lower()
    markers = (
        "invalidapikey",
        "invalid api key",
        "api-key is blocked",
        "quota",
        "insufficient",
        "balance",
        "billing",
        "payment",
        "exceeded",
        "over limit",
        "rate limit",
        "too many requests",
        "401",
        "403",
        "429",
        "未配置",
        "api key",
    )
    return any(marker in error for marker in markers)


def _assistant_execute_image_generate_once_sync(
    args: Dict[str, Any],
    user_row: Optional[models.User],
    *,
    use_user_config: bool,
    credential_source: str,
) -> Dict[str, Any]:
    prompt = _assistant_tool_arg_str(args, "prompt")
    if not prompt:
        return {"ok": False, "tool": "image_generate", "label": "图片生成", "error": "prompt 不能为空"}
    prompt = prompt[:1800]

    aspect_ratio = _assistant_tool_arg_str(args, "aspect_ratio", "1:1") or "1:1"
    if aspect_ratio not in ASSISTANT_IMAGE_RATIOS:
        aspect_ratio = "1:1"
    image_size = _assistant_tool_arg_str(args, "image_size", "1K") or "1K"
    if image_size not in ASSISTANT_IMAGE_SIZES:
        image_size = "1K"
    negative_prompt = _assistant_tool_arg_str(args, "negative_prompt")[:800]
    model = _assistant_tool_arg_str(args, "model")[:160]

    if not HERMES_IMAGE_SCRIPT.is_file():
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "error": f"图片生成脚本不存在：{HERMES_IMAGE_SCRIPT}",
        }

    env = _assistant_prepare_image_env(user_row, use_user_config=use_user_config)
    backend = (env.get("IMAGE_BACKEND") or "").strip()
    if not backend:
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "error": "未配置 IMAGE_BACKEND。请配置 HERMES_IMAGE_BACKEND 或图片脚本 .env。",
        }

    filename_hint = _assistant_slug_filename(_assistant_tool_arg_str(args, "filename_hint"), "image")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"hermes-{filename_hint}-{stamp}-{uuid.uuid4().hex[:8]}"

    output_dir = (GENERATED_ASSET_ROOT / "imgs" / HERMES_GENERATED_IMG_DIRNAME).resolve()
    try:
        output_dir.relative_to(GENERATED_ASSET_ROOT)
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "error": f"创建图片输出目录失败：{_assistant_short_error(e)}",
        }

    cmd = [
        sys.executable,
        str(HERMES_IMAGE_SCRIPT),
        prompt,
        "--aspect_ratio",
        aspect_ratio,
        "--image_size",
        image_size,
        "--output",
        str(output_dir),
        "--filename",
        filename,
    ]
    if negative_prompt:
        cmd.extend(["--negative_prompt", negative_prompt])
    if model:
        cmd.extend(["--model", model])

    timeout_s = float_setting("HERMES_IMAGE_TIMEOUT_SEC", 360.0)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(HERMES_IMAGE_SCRIPT.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "provider": backend,
            "credential_source": credential_source,
            "model": model or env.get(f"{backend.upper()}_MODEL") or env.get("QWEN_MODEL") or "",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "query_time_ms": round((time.time() - started) * 1000, 1),
            "error": f"图片生成超时（{timeout_s:.0f}s）",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "provider": backend,
            "credential_source": credential_source,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "query_time_ms": round((time.time() - started) * 1000, 1),
            "error": _assistant_short_error(e),
        }

    generated = output_dir / f"{filename}.png"
    if not generated.exists():
        matches = sorted(output_dir.glob(f"{filename}.*"))
        generated = matches[0] if matches else generated

    if proc.returncode != 0 or not generated.exists():
        stderr = _assistant_trim_text(proc.stderr or "", 1200)
        stdout = _assistant_trim_text(proc.stdout or "", 800)
        detail = stderr or stdout or f"图片生成脚本退出码 {proc.returncode}"
        return {
            "ok": False,
            "tool": "image_generate",
            "label": "图片生成",
            "provider": backend,
            "credential_source": credential_source,
            "model": model or env.get(f"{backend.upper()}_MODEL") or env.get("QWEN_MODEL") or "",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            "query_time_ms": round((time.time() - started) * 1000, 1),
            "error": detail,
        }

    image_url = f"/imgs/{HERMES_GENERATED_IMG_DIRNAME}/{generated.name}"
    return {
        "ok": True,
        "tool": "image_generate",
        "label": "图片生成",
        "source": "ppt_master_image_gen",
        "provider": backend,
        "credential_source": credential_source,
        "model": model or env.get(f"{backend.upper()}_MODEL") or env.get("QWEN_MODEL") or "",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "image_url": image_url,
        "url": image_url,
        "filename": generated.name,
        "relative_path": f"imgs/{HERMES_GENERATED_IMG_DIRNAME}/{generated.name}",
        "query_time_ms": round((time.time() - started) * 1000, 1),
        "warning": "",
    }


def _assistant_execute_image_generate_sync(
    args: Dict[str, Any],
    user_row: Optional[models.User] = None,
) -> Dict[str, Any]:
    public_result = _assistant_execute_image_generate_once_sync(
        args,
        user_row,
        use_user_config=False,
        credential_source="public",
    )
    user_cfg = _assistant_user_image_config(user_row)
    if user_cfg and _assistant_should_retry_image_with_user(public_result):
        user_result = _assistant_execute_image_generate_once_sync(
            args,
            user_row,
            use_user_config=True,
            credential_source="user",
        )
        user_result["fallback_from_public"] = True
        if not user_result.get("ok"):
            user_result["public_error"] = _assistant_trim_text(public_result.get("error") or "", 500)
        return user_result
    return public_result


def _assistant_http_error_result(tool_name: str, e: HTTPException) -> Dict[str, Any]:
    return {"ok": False, "tool": tool_name, "error": str(e.detail or "请求失败")}


def _assistant_execute_selected_context_list_sync(
    *,
    tool_name: str,
    label: str,
    kind: str,
    args: Dict[str, Any],
    body: Optional[AssistantCCStreamRequest],
) -> Dict[str, Any]:
    items = _assistant_context_raw_items(body, kind)
    extra: Dict[str, Any] = {}
    if kind == "favorites":
        fav = body.favorite_context if body and isinstance(body.favorite_context, dict) else None
        extra["folder"] = str((fav or {}).get("folder") or "").strip()
    return _assistant_context_list_result(
        tool_name=tool_name,
        label=label,
        items=items,
        args=args,
        extra=extra,
    )


def _assistant_execute_selected_context_read_sync(
    *,
    tool_name: str,
    label: str,
    kind: str,
    args: Dict[str, Any],
    body: Optional[AssistantCCStreamRequest],
) -> Dict[str, Any]:
    items = _assistant_context_raw_items(body, kind)
    idx, item = _assistant_find_context_item(items, args)
    if not item or idx is None:
        return {
            "ok": False,
            "tool": tool_name,
            "label": label,
            "total": len(items),
            "error": "未找到匹配素材；请先调用对应 list 工具查看 index/id/name",
        }
    clean_item = _assistant_sanitize_context_item(item, detailed=True)
    if kind == "skills" and clean_item.get("localPath"):
        max_chars = int_setting("HERMES_SELECTED_SKILL_MD_CHARS", 24_000)
        skill_doc = _assistant_read_public_dataset_text(str(clean_item.get("localPath") or ""), max_chars=max_chars)
        if skill_doc.get("ok"):
            clean_item["skill_md"] = skill_doc.get("content") or ""
            clean_item["skill_md_path"] = skill_doc.get("path") or clean_item.get("localPath")
            clean_item["skill_md_chars"] = skill_doc.get("chars")
            clean_item["skill_md_truncated"] = skill_doc.get("truncated")
        else:
            clean_item["skill_md_error"] = skill_doc.get("error") or "读取 Skill 文件失败"
    return {
        "ok": True,
        "tool": tool_name,
        "label": label,
        "index": idx,
        "total": len(items),
        "item": clean_item,
    }


def _assistant_workspace_from_body(
    body: Optional[AssistantCCStreamRequest],
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
) -> Tuple[Optional[Path], Optional[str], Optional[Dict[str, Any]]]:
    if not user:
        return None, None, {"ok": False, "error": "需要登录后才能访问工作区"}
    workspace_name = (
        _assistant_tool_arg_str(args, "workspace")
        or _assistant_tool_arg_str(args, "workspace_name")
        or (body.pinned_workspace if body else "")
    )
    if not workspace_name:
        return None, None, {"ok": False, "error": "当前未固定工作区，请先在左侧“切换”中固定一个工作区"}
    try:
        workspace_path, _ = _resolve_assistant_workspace(user, workspace_name)
    except HTTPException as e:
        return None, workspace_name, _assistant_http_error_result("workspace", e)
    return workspace_path, workspace_name, None


def _assistant_execute_workspace_list_files_sync(
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
    body: Optional[AssistantCCStreamRequest],
) -> Dict[str, Any]:
    workspace_path, workspace_name, err = _assistant_workspace_from_body(body, args, user)
    if err:
        return {"tool": "workspace_list_files", "label": "工作区文件列表", **err}
    subpath = _assistant_tool_arg_str(args, "subpath")
    try:
        target = _assistant_safe_child(workspace_path, subpath)
    except HTTPException as e:
        return _assistant_http_error_result("workspace_list_files", e)
    if not target.is_dir():
        return {"ok": False, "tool": "workspace_list_files", "label": "工作区文件列表", "workspace": workspace_name, "subpath": subpath, "error": "目录不存在"}
    files = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name == ".workspace.json":
                continue
            files.append(_assistant_file_item(entry))
            if len(files) >= 200:
                break
    except OSError as e:
        return {"ok": False, "tool": "workspace_list_files", "label": "工作区文件列表", "workspace": workspace_name, "subpath": subpath, "error": f"读取目录失败：{e}"}
    return {
        "ok": True,
        "tool": "workspace_list_files",
        "label": "工作区文件列表",
        "workspace": workspace_name,
        "subpath": subpath,
        "items_returned": len(files),
        "total": len(files),
        "files": files,
    }


def _assistant_execute_workspace_read_file_sync(
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
    body: Optional[AssistantCCStreamRequest],
) -> Dict[str, Any]:
    workspace_path, workspace_name, err = _assistant_workspace_from_body(body, args, user)
    if err:
        return {"tool": "workspace_read_file", "label": "工作区文件读取", **err}
    rel_path = _assistant_tool_arg_str(args, "path") or _assistant_tool_arg_str(args, "filename")
    if not rel_path:
        return {"ok": False, "tool": "workspace_read_file", "label": "工作区文件读取", "workspace": workspace_name, "error": "path 不能为空"}
    try:
        file_path = _assistant_safe_child(workspace_path, rel_path)
    except HTTPException as e:
        return _assistant_http_error_result("workspace_read_file", e)
    result = _assistant_read_text_path(
        file_path,
        max_chars=_assistant_tool_int(args, "max_chars", 12000, 1000, 30000),
    )
    return {
        "tool": "workspace_read_file",
        "label": "工作区文件读取",
        "workspace": workspace_name,
        "path": rel_path,
        **result,
    }


def _assistant_kb_category_dir(category: str) -> Tuple[str, str]:
    clean = str(category or "").strip()
    info = ASSISTANT_KB_CATEGORY_DIRS.get(clean)
    if not info:
        raise HTTPException(status_code=400, detail="无效知识库分类")
    return info


def _assistant_kb_root(user: Dict[str, Any], category: str) -> Tuple[Path, str]:
    username = _assistant_workspace_username(user)
    dirname, label = _assistant_kb_category_dir(category)
    user_root = (WORKSPACE_ROOT / username).resolve()
    return _assistant_safe_child(user_root, "knowledge_base", dirname), label


def _assistant_execute_knowledge_list_files_sync(
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not user:
        return {"ok": False, "tool": "knowledge_list_files", "label": "知识库文件列表", "error": "需要登录后才能访问知识库"}
    category = _assistant_tool_arg_str(args, "category")
    try:
        kb_dir, label = _assistant_kb_root(user, category)
    except HTTPException as e:
        return _assistant_http_error_result("knowledge_list_files", e)
    files: List[Dict[str, Any]] = []
    if kb_dir.is_dir():
        try:
            for entry in sorted(kb_dir.iterdir(), key=lambda p: p.name.lower()):
                if not entry.is_file():
                    continue
                item = _assistant_file_item(entry)
                item["ext"] = entry.suffix.lower()
                files.append(item)
                if len(files) >= 200:
                    break
        except OSError as e:
            return {"ok": False, "tool": "knowledge_list_files", "label": "知识库文件列表", "category": category, "error": f"读取目录失败：{e}"}
    return {
        "ok": True,
        "tool": "knowledge_list_files",
        "label": "知识库文件列表",
        "category": category,
        "category_label": label,
        "items_returned": len(files),
        "total": len(files),
        "files": files,
    }


def _assistant_execute_knowledge_read_file_sync(
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not user:
        return {"ok": False, "tool": "knowledge_read_file", "label": "知识库文件读取", "error": "需要登录后才能访问知识库"}
    category = _assistant_tool_arg_str(args, "category")
    filename = _assistant_tool_arg_str(args, "filename") or _assistant_tool_arg_str(args, "path")
    if not filename:
        return {"ok": False, "tool": "knowledge_read_file", "label": "知识库文件读取", "category": category, "error": "filename 不能为空"}
    try:
        kb_dir, label = _assistant_kb_root(user, category)
        file_path = _assistant_safe_child(kb_dir, filename)
    except HTTPException as e:
        return _assistant_http_error_result("knowledge_read_file", e)
    result = _assistant_read_text_path(
        file_path,
        max_chars=_assistant_tool_int(args, "max_chars", 12000, 1000, 30000),
    )
    return {
        "tool": "knowledge_read_file",
        "label": "知识库文件读取",
        "category": category,
        "category_label": label,
        "filename": filename,
        **result,
    }


def _assistant_tool_search_request(
    args: Dict[str, Any],
    *,
    search_type: str,
    mode: str,
    default_size: int = 8,
) -> SearchRequest:
    keyword = _assistant_tool_arg_str(args, "keyword") or _assistant_tool_arg_str(args, "query")
    return SearchRequest(
        keyword=keyword[:500],
        publish_time=_assistant_tool_arg_str(args, "publish_time", "近一年") or "近一年",
        start_time=_assistant_tool_arg_str(args, "start_time") or None,
        end_time=_assistant_tool_arg_str(args, "end_time") or None,
        page=1,
        page_size=_assistant_tool_page_size(args, default_size),
        sort_by="pub_time",
        sort_order="desc",
        mode=mode,
        search_type=search_type,  # type: ignore[arg-type]
    )


def _assistant_execute_hermes_tool_sync(
    tool_name: str,
    args: Dict[str, Any],
    user: Optional[Dict[str, Any]],
    user_row: Optional[models.User] = None,
    body: Optional[AssistantCCStreamRequest] = None,
) -> Dict[str, Any]:
    safe_args = dict(args) if isinstance(args, dict) else {}
    if tool_name == "web_search":
        return _assistant_execute_web_search_sync(safe_args, user_row)

    if tool_name == "image_generate":
        return _assistant_execute_image_generate_sync(safe_args, user_row)

    if tool_name == "selected_favorites_list":
        return _assistant_execute_selected_context_list_sync(
            tool_name=tool_name,
            label="固定收藏素材列表",
            kind="favorites",
            args=safe_args,
            body=body,
        )

    if tool_name == "selected_favorite_read":
        return _assistant_execute_selected_context_read_sync(
            tool_name=tool_name,
            label="固定收藏素材读取",
            kind="favorites",
            args=safe_args,
            body=body,
        )

    if tool_name == "selected_skill_list":
        return _assistant_execute_selected_context_list_sync(
            tool_name=tool_name,
            label="已启用 Skill 列表",
            kind="skills",
            args=safe_args,
            body=body,
        )

    if tool_name == "selected_skill_read":
        return _assistant_execute_selected_context_read_sync(
            tool_name=tool_name,
            label="已启用 Skill 读取",
            kind="skills",
            args=safe_args,
            body=body,
        )

    if tool_name == "selected_database_list":
        return _assistant_execute_selected_context_list_sync(
            tool_name=tool_name,
            label="已启用数据库卡片列表",
            kind="databases",
            args=safe_args,
            body=body,
        )

    if tool_name == "selected_database_read":
        return _assistant_execute_selected_context_read_sync(
            tool_name=tool_name,
            label="已启用数据库卡片读取",
            kind="databases",
            args=safe_args,
            body=body,
        )

    if tool_name == "workspace_list_files":
        return _assistant_execute_workspace_list_files_sync(safe_args, user, body)

    if tool_name == "workspace_read_file":
        return _assistant_execute_workspace_read_file_sync(safe_args, user, body)

    if tool_name == "knowledge_list_files":
        return _assistant_execute_knowledge_list_files_sync(safe_args, user)

    if tool_name == "knowledge_read_file":
        return _assistant_execute_knowledge_read_file_sync(safe_args, user)

    if not (_assistant_tool_arg_str(safe_args, "keyword") or _assistant_tool_arg_str(safe_args, "query")):
        raw = str(safe_args.get("_raw") or "")
        m = re.search(r'"(?:keyword|query)"\s*:\s*"([^"]+)', raw)
        if m:
            safe_args["keyword"] = m.group(1).strip()
    keyword = _assistant_tool_arg_str(safe_args, "keyword") or _assistant_tool_arg_str(safe_args, "query")
    if not keyword:
        return {"ok": False, "tool": tool_name, "error": "keyword 不能为空"}

    if tool_name == "news_search":
        req = _assistant_tool_search_request(safe_args, search_type="news", mode="fuzzy")
        terms = tuple(_assistant_question_terms(keyword))
        plan = AssistantPlatformToolPlan(
            name="news_search",
            label="新闻检索",
            params=req,
            terms=terms,
        )
        return _assistant_execute_news_evidence_search_sync(plan)

    if tool_name == "event_cluster_search":
        req = _assistant_tool_search_request(safe_args, search_type="l1", mode="event_coref")
        terms = tuple(_assistant_question_terms(keyword))
        plan = AssistantPlatformToolPlan(
            name="event_cluster_search",
            label="L1 事件聚类检索",
            params=req,
            terms=terms,
        )
        return _assistant_execute_platform_tool_sync(plan, user)

    if tool_name == "macro_event_search":
        level = _assistant_tool_arg_str(safe_args, "level", "l2").lower()
        search_type = "l3" if level == "l3" else "l2"
        req = _assistant_tool_search_request(safe_args, search_type=search_type, mode="fuzzy")
        terms = tuple(_assistant_question_terms(keyword))
        plan = AssistantPlatformToolPlan(
            name="macro_event_search",
            label=f"{search_type.upper()} 宏观事件检索",
            params=req,
            terms=terms,
        )
        return _assistant_execute_platform_tool_sync(plan, user)

    return {"ok": False, "tool": tool_name, "error": f"未知工具：{tool_name}"}


# ────────────────────────
# vLLM helpers
# ────────────────────────

_vllm_client: Optional[httpx.Client] = None  # type: ignore[name-defined]

def _get_vllm_client():
    import httpx as _httpx
    global _vllm_client
    if _vllm_client is None:
        _vllm_client = _httpx.Client(
            timeout=_httpx.Timeout(60.0, connect=8.0),
            trust_env=False,
        )
    return _vllm_client


def _openai_compat_v1_base(raw: Optional[str] = None) -> str:
    base = (
        raw
        if raw is not None
        else string_setting("VLLM_BASE_URL", "http://127.0.0.1:8000")
    ).strip().rstrip("/")
    while base.endswith("/v1/v1"):
        base = base[:-3]
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _vllm_list_model_ids(v1_base: str) -> List[str]:
    try:
        client = _get_vllm_client()
        resp = client.get(f"{v1_base.rstrip('/')}/models", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        out: List[str] = []
        for it in data.get("data") or []:
            if isinstance(it, dict) and it.get("id"):
                out.append(str(it["id"]))
        return out
    except Exception:
        return []


def _call_openai_compat_chat(
    v1_base: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    timeout: int = 60,
    max_tokens: int = 896,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    client = _get_vllm_client()
    resp = client.post(
        url=f"{v1_base.rstrip('/')}/chat/completions",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def _assistant_chat_core(body: AssistantChatRequest, db: Session) -> AssistantChatResponse:
    import httpx as _httpx
    q = (body.message or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="message 不能为空")

    news_hits: List[Dict[str, Any]] = []
    cluster_hits: List[Dict[str, Any]] = []

    v1_base = _openai_compat_v1_base()
    configured = string_setting("VLLM_MODEL")
    default_fallback = "/model/Qwen2.5-1.5B-Instruct"
    model_order: List[str] = []
    if configured:
        model_order.append(configured)
    model_order.extend(_vllm_list_model_ids(v1_base))
    if default_fallback not in model_order:
        model_order.append(default_fallback)
    seen: set = set()
    model_candidates: List[str] = []
    for m in model_order:
        if m and m not in seen:
            seen.add(m)
            model_candidates.append(m)

    mode_cfg = _assistant_mode_config(body.mode)
    prompt = q
    messages = [
        {
            "role": "system",
            "content": render_registered_prompt(
                "assistant.interactive.system"
            ).text
            + "\n"
            + mode_cfg.prompt,
        },
        {"role": "user", "content": prompt},
    ]

    llm_timeout = int_setting("VLLM_TIMEOUT_SEC", 55)
    max_tries = int_setting("VLLM_MODEL_TRIES", 2)
    reply = ""
    generation_complete = False
    for model_name in model_candidates[:max_tries]:
        try:
            reply = _call_openai_compat_chat(
                v1_base,
                model_name,
                messages,
                temperature=mode_cfg.temperature,
                timeout=llm_timeout,
                max_tokens=min(mode_cfg.max_tokens, 1400),
            )
            if reply:
                generation_complete = True
                break
        except (_httpx.RequestError, _httpx.HTTPStatusError, TimeoutError, json.JSONDecodeError, OSError):
            continue
    bounded = finalize_interactive_output(
        reply,
        (),
        evidence_required=_assistant_requires_evidence(q),
        generation_complete=generation_complete,
        require_structured_claims=True,
    )
    return AssistantChatResponse(
        reply=bounded.content,
        news_hits=news_hits,
        cluster_hits=cluster_hits,
        citation_assurance=bounded.assurance,
        prompt_registry=interactive_prompt_bundle_receipt(mode_cfg.key),
    )


def _assistant_user_visible_text(body: AssistantChatRequest) -> str:
    uv = (body.user_visible_message or "").strip()
    if uv:
        return uv[:4000]
    return _extract_user_question_for_search(body.message)[:4000]


def _assistant_extra_payload(
    news_hits: List[Dict[str, Any]],
    cluster_hits: List[Dict[str, Any]],
    web_sources: Optional[List[Dict[str, Any]]] = None,
    *,
    streaming: bool = False,
    error: Optional[str] = None,
    citation_assurance: Optional[Dict[str, Any]] = None,
    prompt_registry: Optional[Dict[str, Any]] = None,
) -> str:
    d: Dict[str, Any] = {
        "news_hits": jsonable_encoder(news_hits),
        "cluster_hits": jsonable_encoder(cluster_hits),
        "web_sources": jsonable_encoder(web_sources or []),
        "streaming": streaming,
    }
    if citation_assurance:
        d["citation_assurance"] = jsonable_encoder(citation_assurance)
    if prompt_registry:
        d["prompt_registry"] = jsonable_encoder(prompt_registry)
    if error:
        d["error"] = error
    return json.dumps(d, ensure_ascii=False)


def _assistant_persist_chat_turn_sync(
    db: Session,
    user_id: int,
    session_id: int,
    body: AssistantChatRequest,
    reply: str,
    news_hits: List[Dict[str, Any]],
    cluster_hits: List[Dict[str, Any]],
    citation_assurance: Optional[Dict[str, Any]] = None,
    prompt_registry: Optional[Dict[str, Any]] = None,
) -> None:
    sess = (
        db.query(models.AssistantChatSession)
        .filter(
            models.AssistantChatSession.id == session_id,
            models.AssistantChatSession.user_id == user_id,
        )
        .first()
    )
    if not sess:
        return
    visible = _assistant_user_visible_text(body)
    now = datetime.now(timezone.utc)
    n_before = (
        db.query(models.AssistantChatMessage)
        .filter(models.AssistantChatMessage.session_id == session_id)
        .count()
    )
    if n_before == 0 and visible:
        sess.title = visible[:80]
    um = models.AssistantChatMessage(
        session_id=session_id,
        user_id=user_id,
        role="user",
        content=visible,
        extra_json=None,
        created_at=now,
    )
    am = models.AssistantChatMessage(
        session_id=session_id,
        user_id=user_id,
        role="assistant",
        content=reply or "",
        extra_json=_assistant_extra_payload(
            news_hits,
            cluster_hits,
            streaming=False,
            citation_assurance=citation_assurance,
            prompt_registry=prompt_registry,
        ),
        created_at=now,
    )
    db.add(um)
    db.add(am)
    sess.updated_at = now
    db.commit()


def _format_assistant_cc_sse(obj: Dict[str, Any]) -> bytes:
    safe = jsonable_encoder(obj)
    return f"data: {json.dumps(safe, ensure_ascii=False)}\n\n".encode("utf-8")


def _assistant_parse_sse_chunks_for_persist(buffer: str) -> Tuple[str, List[Dict[str, Any]]]:
    events: List[Dict[str, Any]] = []
    rest = buffer
    while "\n\n" in rest:
        raw_line, rest = rest.split("\n\n", 1)
        data_lines: List[str] = []
        for line_item in raw_line.split("\n"):
            if line_item.startswith("data:"):
                data_lines.append(line_item[5:].lstrip())
        t = "\n".join(data_lines).strip()
        if not t:
            continue
        try:
            events.append(json.loads(t))
        except json.JSONDecodeError:
            pass
    return rest, events


def _assistant_flush_stream_message(
    assistant_msg_id: int,
    session_id: int,
    content: str,
    news_hits: List[Dict[str, Any]],
    cluster_hits: List[Dict[str, Any]],
    web_sources: Optional[List[Dict[str, Any]]] = None,
    *,
    streaming: bool,
    error: Optional[str] = None,
    citation_assurance: Optional[Dict[str, Any]] = None,
    prompt_registry: Optional[Dict[str, Any]] = None,
) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(models.AssistantChatMessage)
            .filter(models.AssistantChatMessage.id == assistant_msg_id)
            .first()
        )
        if not row:
            return
        row.content = content or ""
        row.extra_json = _assistant_extra_payload(
            news_hits,
            cluster_hits,
            web_sources,
            streaming=streaming,
            error=error,
            citation_assurance=citation_assurance,
            prompt_registry=prompt_registry,
        )
        sess = (
            db.query(models.AssistantChatSession)
            .filter(models.AssistantChatSession.id == session_id)
            .first()
        )
        if sess:
            sess.updated_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _assistant_format_history_rows(rows: List[models.AssistantChatMessage]) -> str:
    parts: List[str] = []
    for r in rows:
        role = (r.role or "").strip()
        if role not in ("user", "assistant"):
            continue
        text_value = (r.content or "").strip()
        if not text_value:
            continue
        cap = 8000 if role == "assistant" else 5000
        if len(text_value) > cap:
            text_value = text_value[: cap - 20] + "\n…(省略)"
        label = "用户" if role == "user" else "助手"
        parts.append(f"{label}: {text_value}")
    return "\n\n".join(parts)


def _assistant_get_user_memory_sync(db: Session, user_id: int) -> str:
    row = (
        db.query(models.AssistantUserMemory)
        .filter(models.AssistantUserMemory.user_id == user_id)
        .first()
    )
    return (row.memory_summary or "").strip() if row else ""


def _assistant_qwen_compress_history_sync(old_dialogue_text: str) -> str:
    raw = (old_dialogue_text or "").strip()
    if not raw:
        return ""
    base = (
        (string_setting("ASSISTANT_CONTEXT_QWEN_BASE") or string_setting("AI_BASE_URL"))
        .strip()
        .rstrip("/")
    )
    if not base:
        base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    key = (
        (string_setting("ASSISTANT_CONTEXT_QWEN_API_KEY") or string_setting("AI_API_KEY"))
        .strip()
    )
    model = string_setting("ASSISTANT_CONTEXT_QWEN_MODEL", "qwen-plus")
    if not key:
        return raw[:12_000] + "\n\n…(未配置 ASSISTANT_CONTEXT_QWEN_API_KEY 或 AI_API_KEY，历史已截断)"
    system = (
        "你是上下文压缩助手。输入为多轮中文对话。输出须保留：\n"
        "1) 用户任务目标、交付形式、硬性约束；\n"
        "2) 已确认事实、数字、专名；\n"
        "3) 未完成子任务。\n"
        "去掉寒暄与重复。输出纯中文要点，不超过 3500 字。"
    )
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": raw[:120_000]},
        ],
        "max_tokens": 4096,
        "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            url=f"{base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        out = str(
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        ).strip()
        return out or raw[:8000]
    except Exception as e:
        return raw[:10_000] + f"\n\n…(压缩请求失败，已截断: {type(e).__name__})"


async def _assistant_build_history_prefix(
    rows: List[models.AssistantChatMessage],
) -> str:
    if not rows:
        return ""
    verbatim_n = max(0, int_setting("ASSISTANT_CONTEXT_VERBATIM_MESSAGES", 4))
    soft_limit = max(5000, int_setting("ASSISTANT_CONTEXT_SOFT_LIMIT_CHARS", 14_000))

    if len(rows) <= verbatim_n:
        return _assistant_format_history_rows(rows)

    old_rows = rows[:-verbatim_n]
    new_rows = rows[-verbatim_n:]
    old_text = _assistant_format_history_rows(old_rows)
    new_text = _assistant_format_history_rows(new_rows)
    combined = f"{old_text}\n\n----\n{new_text}" if old_text and new_text else (old_text or new_text)
    if len(combined) <= soft_limit:
        return combined

    compressed = await asyncio.to_thread(_assistant_qwen_compress_history_sync, old_text)
    return (
        "【较早多轮对话 — 已由 qwen 压缩摘要】\n"
        f"{compressed}\n\n----\n【最近对话（原文）】\n{new_text}"
    )


async def _assistant_build_context_prefix(
    db: Session,
    user_id: int,
    session_row: Optional[models.AssistantChatSession],
    rows: List[models.AssistantChatMessage],
) -> str:
    parts: List[str] = []
    user_memory = _assistant_get_user_memory_sync(db, user_id)
    if user_memory:
        parts.append(f"【该用户长期记忆】\n{user_memory}")

    session_summary = (getattr(session_row, "context_summary", None) or "").strip() if session_row else ""
    if session_summary:
        parts.append(f"【本会话压缩上下文】\n{session_summary}")
        verbatim_n = max(2, int_setting("ASSISTANT_CONTEXT_VERBATIM_MESSAGES", 4))
        recent_text = _assistant_format_history_rows(rows[-verbatim_n:])
        if recent_text:
            parts.append(f"【最近对话原文】\n{recent_text}")
        return "\n\n----\n".join(parts)

    history = await _assistant_build_history_prefix(rows)
    if history:
        parts.append(f"【本会话此前多轮】\n{history}")
    return "\n\n----\n".join(parts)


async def _assistant_compress_with_hermes(
    *,
    task: str,
    text: str,
    user_row: Optional[models.User],
    max_chars_fallback: int,
) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    messages = [
        {
            "role": "system",
            "content": (
                "你是中文上下文压缩器。只输出压缩后的要点，不要解释过程。"
                "保留用户目标、硬性约束、已确认事实、数字、专名、未完成事项。"
            ),
        },
        {"role": "user", "content": f"{task}\n\n{raw[:60_000]}"},
    ]
    try:
        out = await call_hermes_once(
            messages=messages,
            user_row=user_row,
            max_tokens=2048,
            temperature=0.1,
        )
        out = (out or "").strip()
        if out:
            return out[:max_chars_fallback]
    except Exception:
        pass
    if "长期记忆" in task:
        cleaned = raw
        marker = "最新一轮："
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[1].strip()
        cleaned = cleaned.replace("既有长期记忆：", "").strip()
        return cleaned[:max_chars_fallback]
    return raw[:max_chars_fallback]


async def _assistant_refresh_memory_after_turn(
    *,
    user_id: int,
    session_id: int,
    user_text: str,
    assistant_text: str,
) -> None:
    if user_id <= 0 or session_id <= 0 or not (assistant_text or "").strip():
        return

    db = SessionLocal()
    try:
        user_row = db.query(models.User).filter(models.User.id == user_id).first()
        sess = (
            db.query(models.AssistantChatSession)
            .filter(
                models.AssistantChatSession.id == session_id,
                models.AssistantChatSession.user_id == user_id,
            )
            .first()
        )
        if not user_row or not sess:
            return

        rows = (
            db.query(models.AssistantChatMessage)
            .filter(models.AssistantChatMessage.session_id == session_id)
            .order_by(models.AssistantChatMessage.id.asc())
            .all()
        )
        dialogue = _assistant_format_history_rows(rows[-24:])
        existing_session_summary = (sess.context_summary or "").strip()
        session_source = (
            f"既有会话摘要：\n{existing_session_summary}\n\n"
            f"最近对话：\n{dialogue}"
            if existing_session_summary
            else dialogue
        )
        sess.context_summary = await _assistant_compress_with_hermes(
            task="请把以下会话压缩为可供后续继续对话使用的上下文摘要，控制在 2500 字内：",
            text=session_source,
            user_row=user_row,
            max_chars_fallback=5000,
        )
        sess.updated_at = datetime.now(timezone.utc)

        mem = (
            db.query(models.AssistantUserMemory)
            .filter(models.AssistantUserMemory.user_id == user_id)
            .first()
        )
        now = datetime.now(timezone.utc)
        if not mem:
            mem = models.AssistantUserMemory(
                user_id=user_id,
                memory_summary="",
                created_at=now,
                updated_at=now,
            )
            db.add(mem)
        memory_source = (
            f"既有长期记忆：\n{mem.memory_summary or '(空)'}\n\n"
            "最新一轮：\n"
            f"用户：{(user_text or '').strip()}\n"
            f"助手：{(assistant_text or '').strip()[:8000]}"
        )
        mem.memory_summary = await _assistant_compress_with_hermes(
            task=(
                "请更新该用户的长期记忆。只保留跨会话仍有用的信息：用户偏好、长期项目、"
                "常用交付格式、明确身份/组织信息、未完成长期事项。不要保存一次性闲聊。控制在 2000 字内："
            ),
            text=memory_source,
            user_row=user_row,
            max_chars_fallback=4000,
        )
        mem.updated_at = now
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[assistant-memory] refresh failed: {type(e).__name__}: {e}", flush=True)
    finally:
        db.close()


# ===================================================================
# Route: POST /api/ai/analyze
# ===================================================================
@router.post(
    "/api/ai/analyze",
    response_model=AIAnalyzeResponse,
    tags=["AI"],
    dependencies=[Depends(get_current_user_required)],
)
def ai_analyze(body: AIAnalyzeRequest):
    """
    接收一定格式的数据，调用 DeepSeek 接口返回简要分析结果。
    需配置环境变量 DEEPSEEK_API_KEY。
    """
    deepseek_api_key = string_setting("DEEPSEEK_API_KEY")
    if not deepseek_api_key:
        raise HTTPException(
            status_code=503,
            detail="未配置 DEEPSEEK_API_KEY，无法使用 AI 分析服务",
        )
    raw_content = _resolve_ai_raw_content(body)
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=deepseek_api_key,
            base_url="https://api.deepseek.com",
        )
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是一个有帮助的助手。请对用户提供的内容进行简要分析，输出简洁的结论与要点，控制在 300 字以内。",
                },
                {"role": "user", "content": raw_content[:8000]},
            ],
            stream=False,
        )
        analysis = (response.choices[0].message.content or "").strip()
        return AIAnalyzeResponse(analysis=analysis)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败: {str(e)}")


# ===================================================================
# Route: POST /api/ai/analyze/stream
# ===================================================================
@router.post(
    "/api/ai/analyze/stream",
    tags=["AI"],
    dependencies=[Depends(get_current_user_required)],
)
def ai_analyze_stream(body: AIAnalyzeRequest):
    deepseek_api_key = string_setting("DEEPSEEK_API_KEY")
    if not deepseek_api_key:
        raise HTTPException(status_code=503, detail="未配置 DEEPSEEK_API_KEY，无法使用 AI 分析服务")
    raw_content = _resolve_ai_raw_content(body)

    def gen():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=deepseek_api_key, base_url="https://api.deepseek.com")
            stream = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个有帮助的助手。请对用户提供的内容进行简要分析，输出简洁的结论与要点。",
                    },
                    {"role": "user", "content": raw_content[:8000]},
                ],
                stream=True,
            )
            for chunk in stream:
                delta = ""
                try:
                    if chunk and chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta.content or ""
                except Exception:
                    delta = ""
                if delta:
                    yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ===================================================================
# Route: POST /api/assistant/chat
# ===================================================================
@router.post("/api/assistant/chat", response_model=AssistantChatResponse, tags=["AI"])
def assistant_chat(
    body: AssistantChatRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    """
    数据助手（MVP）：PostgreSQL 检索 + 本地 OpenAI 兼容 vLLM。
    带 session_id 时写入 assistant_chat_*。
    """
    try:
        resp = _assistant_chat_core(body, db)
    except HTTPException:
        raise
    except Exception:
        bounded = finalize_interactive_output(
            "",
            (),
            evidence_required=True,
            generation_complete=False,
            require_structured_claims=True,
        )
        return AssistantChatResponse(
            reply=bounded.content,
            news_hits=[],
            cluster_hits=[],
            citation_assurance=bounded.assurance,
            prompt_registry=interactive_prompt_bundle_receipt(body.mode),
        )
    uid = int(user.get("user_id") or 0)
    if uid > 0 and body.session_id is not None:
        try:
            _assistant_persist_chat_turn_sync(
                db,
                uid,
                int(body.session_id),
                body,
                resp.reply,
                resp.news_hits,
                resp.cluster_hits,
                resp.citation_assurance,
                resp.prompt_registry,
            )
        except Exception:
            pass
    return resp


# ===================================================================
# Route: GET /api/assistant/sessions
# ===================================================================
@router.get("/api/assistant/memory", tags=["AI"])
def assistant_user_memory(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持助手记忆")
    row = (
        db.query(models.AssistantUserMemory)
        .filter(models.AssistantUserMemory.user_id == uid)
        .first()
    )
    return {
        "ok": True,
        "memory_summary": row.memory_summary if row else "",
        "updated_at": row.updated_at if row else None,
        "created_at": row.created_at if row else None,
    }


@router.delete("/api/assistant/memory", tags=["AI"])
def assistant_user_memory_clear(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持助手记忆")
    row = (
        db.query(models.AssistantUserMemory)
        .filter(models.AssistantUserMemory.user_id == uid)
        .first()
    )
    if row:
        row.memory_summary = ""
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "memory_summary": "", "updated_at": datetime.now(timezone.utc)}


@router.get("/api/assistant/sessions", tags=["AI"])
def assistant_sessions_list(
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持会话")
    rows = (
        db.query(models.AssistantChatSession)
        .filter(models.AssistantChatSession.user_id == uid)
        .order_by(
            desc(models.AssistantChatSession.updated_at),
            desc(models.AssistantChatSession.id),
        )
        .limit(80)
        .all()
    )
    return [
        {
            "id": r.id,
            "title": r.title or "新会话",
            "pinned": bool(r.pinned) if r.pinned else False,
            "updated_at": r.updated_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ===================================================================
# Route: POST /api/assistant/sessions
# ===================================================================
@router.post("/api/assistant/sessions", tags=["AI"])
def assistant_sessions_create(
    body: AssistantSessionCreateRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持会话")
    now = datetime.now(timezone.utc)
    title = ((body.title or "").strip() or "新会话")[:256]
    sess = models.AssistantChatSession(
        user_id=uid, title=title, created_at=now, updated_at=now
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return {
        "id": sess.id,
        "title": sess.title,
        "updated_at": sess.updated_at,
        "created_at": sess.created_at,
    }


# ===================================================================
# Route: PUT /api/assistant/sessions/{session_id}
# ===================================================================
@router.put("/api/assistant/sessions/{session_id}", tags=["AI"])
def assistant_sessions_update(
    session_id: int,
    body: dict,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持会话")
    sess = (
        db.query(models.AssistantChatSession)
        .filter(
            models.AssistantChatSession.id == session_id,
            models.AssistantChatSession.user_id == uid,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    title = body.get("title")
    pinned = body.get("pinned")
    if title is not None:
        sess.title = (str(title).strip() or "新会话")[:256]
    if pinned is not None:
        sess.pinned = bool(pinned)
    sess.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "ok": True,
        "id": sess.id,
        "title": sess.title,
        "pinned": sess.pinned,
        "updated_at": sess.updated_at,
        "created_at": sess.created_at,
    }


# ===================================================================
# Route: DELETE /api/assistant/sessions/{session_id}
# ===================================================================
@router.delete("/api/assistant/sessions/{session_id}", tags=["AI"])
def assistant_sessions_delete(
    session_id: int,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持会话")
    sess = (
        db.query(models.AssistantChatSession)
        .filter(
            models.AssistantChatSession.id == session_id,
            models.AssistantChatSession.user_id == uid,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    if sess.pinned:
        return JSONResponse(
            content={"ok": False, "error": "固定会话不能删除"},
            status_code=400,
        )
    db.delete(sess)
    db.commit()
    return {"ok": True, "id": session_id}


# ===================================================================
# Route: GET /api/assistant/sessions/{session_id}/messages
# ===================================================================
@router.get("/api/assistant/sessions/{session_id}/messages", tags=["AI"])
def assistant_session_messages(
    session_id: int,
    user: Dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    uid = int(user.get("user_id") or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="当前账号不支持会话")
    sess = (
        db.query(models.AssistantChatSession)
        .filter(
            models.AssistantChatSession.id == session_id,
            models.AssistantChatSession.user_id == uid,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = (
        db.query(models.AssistantChatMessage)
        .filter(models.AssistantChatMessage.session_id == session_id)
        .order_by(models.AssistantChatMessage.id.asc())
        .all()
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        extra: Optional[Dict[str, Any]] = None
        if r.extra_json:
            try:
                extra = json.loads(r.extra_json)
            except json.JSONDecodeError:
                extra = None
        out.append(
            {
                "id": r.id,
                "role": r.role,
                "content": r.content or "",
                "extra": extra,
                "created_at": r.created_at,
            }
        )
    return out


# ===================================================================
# Route: POST /api/assistant/cc/stream
# ===================================================================
@router.post("/api/assistant/cc/stream", tags=["AI"], response_model=None)
async def assistant_cc_stream(
    request: Request,
    body: AssistantCCStreamRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user_required),
):
    """
    数据助手 + Hermes：SSE news_hits / cluster_hits + Hermes/OpenAI-compatible 流。
    已登录：落库 assistant_chat_*，流式过程中周期性写入，离开后仍可恢复。
    """
    import time as _time
    _t0 = _time.monotonic()

    news_hits: List[Dict[str, Any]] = []
    cluster_hits: List[Dict[str, Any]] = []
    web_sources: List[Dict[str, Any]] = []

    uid = int(user.get("user_id") or 0)
    user_row: Optional[models.User] = None
    session_resolved_id: Optional[int] = None
    assistant_msg_id: Optional[int] = None
    now = datetime.now(timezone.utc)
    history_prefix = ""
    mode_cfg = _assistant_mode_config(body.mode)
    base_prompt_receipt = interactive_prompt_bundle_receipt(mode_cfg.key)

    if uid > 0:
        user_row = db.query(models.User).filter(models.User.id == uid).first()
        if body.session_id is not None:
            sess = (
                db.query(models.AssistantChatSession)
                .filter(
                    models.AssistantChatSession.id == body.session_id,
                    models.AssistantChatSession.user_id == uid,
                )
                .first()
            )
            if not sess:
                raise HTTPException(
                    status_code=404, detail="会话不存在或无权访问"
                )
            session_resolved_id = sess.id
        else:
            sess = models.AssistantChatSession(
                user_id=uid,
                title="新会话",
                created_at=now,
                updated_at=now,
            )
            db.add(sess)
            db.commit()
            db.refresh(sess)
            session_resolved_id = sess.id

        visible = _assistant_user_visible_text(body)
        sess_row = (
            db.query(models.AssistantChatSession)
            .filter(models.AssistantChatSession.id == session_resolved_id)
            .first()
        )
        n_before = (
            db.query(models.AssistantChatMessage)
            .filter(models.AssistantChatMessage.session_id == session_resolved_id)
            .count()
        )
        if n_before == 0 and visible and sess_row:
            sess_row.title = visible[:80]
            sess_row.updated_at = now
            db.commit()

        hist_rows = (
            db.query(models.AssistantChatMessage)
            .filter(models.AssistantChatMessage.session_id == session_resolved_id)
            .order_by(models.AssistantChatMessage.id.asc())
            .all()
        )
        if hist_rows:
            hp = await _assistant_build_context_prefix(db, uid, sess_row, hist_rows)
            if (hp or "").strip():
                history_prefix = (hp or "").strip()

    intro = (
        "用户来自 Globemind 新闻舆情后台，当前可能附加工作区、收藏素材、知识库 Skill、数据库连接卡片与报告主题上下文。\n"
        "无需重复身份介绍——系统提示词已定义完整。请基于用户输入和可用工具直接分析。\n"
    )
    if body.pinned_workspace:
        workspace_path, workspace_root = _resolve_assistant_workspace(user, body.pinned_workspace)
        intro += (
            f"\n【当前工作目录】用户已固定工作区「{body.pinned_workspace}」，"
            f"后续所有文件操作、数据查询默认在此工作区目录下进行。\n"
            f"工作区绝对路径：{workspace_path}\n"
            f"安全边界：只能在当前用户沙箱 {workspace_root} 内工作，不得读取、写入或推断其他用户目录与系统路径。\n"
        )
    selected_context = _assistant_selected_context_block(body)
    if selected_context:
        intro += f"\n{selected_context}\n"
    hist_block = ""
    if history_prefix:
        hist_block = f"{history_prefix}\n\n----\n"

    if uid > 0:
        um = models.AssistantChatMessage(
            session_id=session_resolved_id,
            user_id=uid,
            role="user",
            content=visible,
            extra_json=None,
            created_at=now,
        )
        am = models.AssistantChatMessage(
            session_id=session_resolved_id,
            user_id=uid,
            role="assistant",
            content="",
            extra_json=_assistant_extra_payload(
                news_hits,
                cluster_hits,
                web_sources,
                streaming=True,
                prompt_registry=base_prompt_receipt,
            ),
            created_at=now,
        )
        db.add(um)
        db.add(am)
        db.commit()
        db.refresh(am)
        assistant_msg_id = am.id

    stream_headers: Dict[str, str] = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Pragma": "no-cache",
    }
    if session_resolved_id is not None:
        stream_headers["X-Assistant-Session-Id"] = str(session_resolved_id)

    async def event_gen() -> AsyncIterator[bytes]:
        nonlocal news_hits, cluster_hits, web_sources
        ctx: Dict[str, Any] = {
            "step": "context",
            "news_hits": news_hits,
            "cluster_hits": cluster_hits,
            "web_sources": web_sources,
            "mode": {"key": mode_cfg.key, "label": mode_cfg.label},
            "prompt_registry": base_prompt_receipt,
        }
        if session_resolved_id is not None:
            ctx["session_id"] = session_resolved_id
        yield _format_assistant_cc_sse(ctx)
        if await request.is_disconnected():
            if assistant_msg_id and session_resolved_id:
                interrupted = finalize_interactive_output(
                    "",
                    (),
                    evidence_required=True,
                    generation_complete=False,
                    require_structured_claims=True,
                )
                _assistant_flush_stream_message(
                    assistant_msg_id,
                    session_resolved_id,
                    interrupted.content,
                    news_hits,
                    cluster_hits,
                    web_sources,
                    streaming=False,
                    error="MODEL_GENERATION_INCOMPLETE",
                    citation_assurance=interrupted.assurance,
                    prompt_registry=base_prompt_receipt,
                )
            return
        _t1 = _time.monotonic()
        hermes_messages = [
            {
                "role": "system",
                "content": render_registered_prompt(
                    "assistant.interactive.system"
                ).text
                + "\n"
                + mode_cfg.prompt,
            },
            {
                "role": "user",
                "content": (
                    f"{intro}{hist_block}"
                    f"用户完整输入：\n{body.message.strip()}"
                ),
            },
        ]

        tool_results: List[Dict[str, Any]] = []

        async def execute_agent_tool(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
            raw_result = await asyncio.to_thread(
                _assistant_execute_hermes_tool_sync,
                tool_name,
                tool_args,
                user,
                user_row,
                body,
            )
            return bind_interactive_tool_result(raw_result)

        _t_tools = _time.monotonic()
        print(
            f"[timing] assistant_hermes_stream: pre-hermes={_t_tools-_t0:.2f}s "
            f"(prep={_t_tools-_t1:.2f}s), selecting direct/tool stream",
            flush=True,
        )
        use_platform_tools = (
            body.tool_mode != "context_only"
            and _assistant_should_use_platform_tools(body.message)
        )
        if use_platform_tools:
            upstream = stream_hermes_tool_agent_events(
                messages=hermes_messages,
                tools=_assistant_hermes_tool_schemas(body),
                execute_tool=execute_agent_tool,
                user_row=user_row,
                temperature=mode_cfg.temperature,
                max_tokens=mode_cfg.max_tokens,
                max_tool_calls=mode_cfg.max_tool_calls,
            )
        else:
            upstream = stream_hermes_chat_events(
                messages=hermes_messages,
                user_row=user_row,
                temperature=mode_cfg.temperature,
                max_tokens=(min(mode_cfg.max_tokens, 4096) if body.tool_mode == "context_only" else mode_cfg.max_tokens),
                reasoning_effort=("off" if body.tool_mode == "context_only" else "high"),
            )
        sse_tail = ""
        accumulated = ""
        authoritative = ""
        generation_done = False
        generation_truncated = False
        stream_failed = False
        disconnected = False
        output_limit_exceeded = False
        _first_chunk = True
        try:
            async for chunk in upstream:
                if _first_chunk:
                    _t2 = _time.monotonic()
                    print(
                        f"[timing] assistant_hermes_stream: first-chunk-from-hermes={_t2-_t_tools:.2f}s "
                        f"(total since request: {_t2-_t0:.2f}s)",
                        flush=True,
                    )
                    _first_chunk = False
                if await request.is_disconnected():
                    disconnected = True
                    break
                sse_tail += chunk.decode("utf-8", errors="replace")
                if len(sse_tail) > MAX_INTERACTIVE_OUTPUT_LENGTH * 4:
                    output_limit_exceeded = True
                    stream_failed = True
                    sse_tail = ""
                    break
                sse_tail, evs = _assistant_parse_sse_chunks_for_persist(sse_tail)
                context_changed = False
                for ev in evs:
                    st = ev.get("step")
                    if st == "text_delta" and ev.get("text"):
                        delta = str(ev["text"])
                        if len(accumulated) + len(delta) > MAX_INTERACTIVE_OUTPUT_LENGTH:
                            output_limit_exceeded = True
                            stream_failed = True
                            accumulated = ""
                        else:
                            accumulated += delta
                    elif st == "done" and ev.get("reply") is not None:
                        authoritative = str(ev.get("reply") or "")
                        accumulated = authoritative
                        if len(authoritative) > MAX_INTERACTIVE_OUTPUT_LENGTH:
                            output_limit_exceeded = True
                            stream_failed = True
                            authoritative = ""
                            accumulated = ""
                        finish_reason = str(
                            ev.get("finish_reason") or ""
                        ).strip().casefold()
                        reported_complete = ev.get("complete")
                        generation_done = (
                            reported_complete
                            if isinstance(reported_complete, bool)
                            else finish_reason
                            in {"stop", "end_turn", "eos", "completed"}
                        )
                        generation_truncated = (
                            bool(ev.get("truncated"))
                            or finish_reason
                            in {"length", "content_filter", "error", "cancelled", "timeout"}
                            or not generation_done
                        )
                    elif st == "tool_finished":
                        result = ev.get("result")
                        if isinstance(result, dict):
                            tool_results.append(result)
                            news_hits, cluster_hits = _assistant_merge_tool_hits(tool_results)
                            web_sources = _assistant_merge_web_sources(tool_results)
                            context_changed = True
                    elif st == "error":
                        stream_failed = True
                    if st in {"start", "tool_executing", "tool_finished"}:
                        yield _format_assistant_cc_sse(ev)
                if output_limit_exceeded:
                    break
                if context_changed:
                    ctx_update: Dict[str, Any] = {
                        "step": "context",
                        "news_hits": news_hits,
                        "cluster_hits": cluster_hits,
                        "web_sources": web_sources,
                    }
                    if session_resolved_id is not None:
                        ctx_update["session_id"] = session_resolved_id
                    yield _format_assistant_cc_sse(ctx_update)
        except Exception:
            stream_failed = True
        finally:
            try:
                await upstream.aclose()
            except Exception:
                stream_failed = True

        prompt_receipt = interactive_prompt_bundle_receipt(
            mode_cfg.key,
            include_tool_finalize=bool(tool_results),
            tool_limit_reached=len(tool_results) >= mode_cfg.max_tool_calls,
        )
        bounded = finalize_interactive_output(
            authoritative if generation_done else accumulated,
            tool_results,
            evidence_required=(
                body.tool_mode == "context_only"
                or _assistant_requires_evidence(body.message)
            ),
            generation_complete=(
                generation_done
                and not generation_truncated
                and not stream_failed
                and not disconnected
            ),
            require_structured_claims=True,
        )
        final_text = bounded.content
        boundary_blocked = bounded.assurance.get("status") == "blocked_replaced_unknown"
        if not disconnected:
            for offset in range(0, len(final_text), 256):
                yield _format_assistant_cc_sse(
                    {"step": "text_delta", "text": final_text[offset : offset + 256]}
                )
            yield _format_assistant_cc_sse(
                {
                    "step": "done",
                    "reply": final_text,
                    "backend": "globemind-citation-boundary",
                    "truncated": False,
                    "upstream_truncated": generation_truncated,
                    "citation_assurance": bounded.assurance,
                    "prompt_registry": prompt_receipt,
                }
            )
        if assistant_msg_id and session_resolved_id:
            _assistant_flush_stream_message(
                assistant_msg_id,
                session_resolved_id,
                final_text,
                news_hits,
                cluster_hits,
                web_sources,
                streaming=False,
                error=(
                    "INTERACTIVE_OUTPUT_REPLACED_UNKNOWN"
                    if boundary_blocked
                    else None
                ),
                citation_assurance=bounded.assurance,
                prompt_registry=prompt_receipt,
            )
            if final_text and not boundary_blocked:
                await _assistant_refresh_memory_after_turn(
                    user_id=uid,
                    session_id=session_resolved_id,
                    user_text=_assistant_user_visible_text(body),
                    assistant_text=final_text,
                )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=stream_headers,
    )
