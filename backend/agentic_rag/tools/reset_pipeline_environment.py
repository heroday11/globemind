#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键清理流水线环境：转发到仓库根目录 `tools/reset_system.py`（PG + Milvus + 可选文件）。

用法（在仓库根 `D:\\datasearch`、已激活 .venv）：

  # 预览
  python -m agentic_rag.tools.reset_pipeline_environment

  # 全量重置（清空 news_analysis 分析字段、宏观表、Milvus、SQLite、部分导出文件）
  python -m agentic_rag.tools.reset_pipeline_environment --execute

  # 只清宏观表 + Milvus（保留已跑好的微观分析）
  python -m agentic_rag.tools.reset_pipeline_environment --execute --only-macro-milvus

其它参数与原脚本一致：`--skip-milvus`、`--skip-pg`、`--skip-fs`、`--skip-sqlite`。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def main() -> int:
    script = _repo_root() / "tools" / "reset_system.py"
    if not script.is_file():
        print(f"[reset_pipeline_environment] 未找到 {script}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script), *sys.argv[1:]]
    return int(subprocess.call(cmd))


if __name__ == "__main__":
    raise SystemExit(main())
