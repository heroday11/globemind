"""
诊断 public.app_user：行数、是否存在 id=1。

仓库根目录执行::
    python -m api.scripts.check_app_users
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text  # noqa: E402

from api.core.db import engine  # noqa: E402


def main() -> None:
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM public.app_user")).scalar() or 0
        row = conn.execute(
            text("SELECT id, username, role FROM public.app_user WHERE id = 1 LIMIT 1")
        ).first()
        print(f"[check] app_user 总行数: {n}")
        print(f"[check] id=1 行: {dict(row._mapping) if row else None}")


if __name__ == "__main__":
    main()
