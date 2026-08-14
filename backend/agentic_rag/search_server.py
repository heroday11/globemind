"""
兼容入口：原独立图谱搜索服务已并入统一 API（`api.main:app`）。

请优先使用::

    uvicorn api.main:app --host 0.0.0.0 --port 8000

历史独立搜索服务已删除；逻辑统一在 `api/application.py` 与同进程路由中维护。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.main import app  # noqa: E402

__all__ = ["app"]
