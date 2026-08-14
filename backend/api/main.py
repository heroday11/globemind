"""
ASGI 入口：与旧 `backend/main.py` 行为一致，路径为 `uvicorn api.main:app`。

运行示例（在仓库根目录）::

    uvicorn api.main:app --host 0.0.0.0 --port 8088
"""
from api.application import app

__all__ = ["app"]
