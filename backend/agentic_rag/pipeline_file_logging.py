#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
流水线会话级文件日志（与 stdout / pipeline.log 并行）。

- 详细运行日志：默认 agentic_rag/logs/runs/（可用 PIPELINE_RUN_LOG_DIR 覆盖）
- Token 用量快照：默认 agentic_rag/logs/tokens/（可用 PIPELINE_TOKEN_LOG_DIR 覆盖）

关闭详细文件日志：PIPELINE_DETAIL_FILE_LOG=0
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SESSION_ID: str | None = None
_DETAIL_FP: Any = None
_DETAIL_PATH: Path | None = None


def _repo_agentic_root() -> Path:
    return Path(__file__).resolve().parent


def detail_file_log_enabled() -> bool:
    return os.getenv("PIPELINE_DETAIL_FILE_LOG", "1").strip().lower() not in ("0", "false", "no")


def run_log_dir() -> Path:
    raw = (os.getenv("PIPELINE_RUN_LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_agentic_root() / "logs" / "runs"


def token_log_dir() -> Path:
    raw = (os.getenv("PIPELINE_TOKEN_LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_agentic_root() / "logs" / "tokens"


def get_session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    return _SESSION_ID


def init_detail_session_log(label: str = "pipeline") -> Path | None:
    """创建本会话详细日志文件，返回路径；未启用时返回 None。"""
    global _DETAIL_FP, _DETAIL_PATH
    if not detail_file_log_enabled():
        return None
    if _DETAIL_FP is not None:
        return _DETAIL_PATH
    d = run_log_dir()
    d.mkdir(parents=True, exist_ok=True)
    sid = get_session_id()
    _DETAIL_PATH = d / f"{label}_{sid}.log"
    _DETAIL_FP = open(_DETAIL_PATH, "a", encoding="utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _DETAIL_FP.write(f"=== {ts} session={sid} label={label} ===\n")
    for key in (
        "LLM_BACKEND",
        "VLLM_MODEL",
        "VLLM_BASE_URL",
        "CLOUD_API_MODEL",
        "CHINA_GATE_THRESHOLD",
        "MILVUS_HOST",
        "MILVUS_PORT",
        "PG_HOST",
        "PG_PORT",
    ):
        _DETAIL_FP.write(f"# {key}={os.getenv(key, '')!r}\n")
    _DETAIL_FP.flush()
    print(f"[LogFile] 详细日志: {_DETAIL_PATH}", flush=True)
    return _DETAIL_PATH


def log_detail(message: str) -> None:
    if _DETAIL_FP is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _DETAIL_FP.write(f"{ts} {message}\n")
        _DETAIL_FP.flush()
    except OSError:
        pass


def close_detail_session_log() -> None:
    global _DETAIL_FP, _DETAIL_PATH
    if _DETAIL_FP is None:
        return
    try:
        _DETAIL_FP.write(f"=== closed {datetime.now(timezone.utc).isoformat()} ===\n")
        _DETAIL_FP.close()
    except OSError:
        pass
    _DETAIL_FP = None
    _DETAIL_PATH = None


def write_token_snapshot(
    phase: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Path:
    """将当前 llm_usage 累计写入 token 目录（JSON）。"""
    from agentic_rag.llm_usage import get_usage_snapshot

    d = token_log_dir()
    d.mkdir(parents=True, exist_ok=True)
    sid = get_session_id()
    path = d / f"tokens_{sid}_{phase}.json"
    payload = {
        "phase": phase,
        "session_id": sid,
        "recorded_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usage": get_usage_snapshot(),
    }
    if extra:
        payload["extra"] = extra
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[TokenLog] 已写入 {path}", flush=True)
    return path


def reset_session_for_tests() -> None:
    global _SESSION_ID, _DETAIL_FP, _DETAIL_PATH
    _SESSION_ID = None
    _DETAIL_FP = None
    _DETAIL_PATH = None
