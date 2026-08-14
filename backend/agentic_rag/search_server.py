"""
兼容入口：原独立图谱搜索服务已并入统一 API（`api.main:app`）。

请优先使用统一 API 的部署入口；Agentic RAG 不再反向依赖 HTTP API。

历史独立搜索服务已删除；逻辑统一在 `api/application.py` 与同进程路由中维护。
"""
from __future__ import annotations

def create_app() -> None:
    """Fail explicitly instead of exposing a misleading placeholder ASGI app."""

    raise RuntimeError(
        "The standalone Agentic RAG search server was retired; use the deployed API entry point."
    )


__all__ = ["create_app"]
