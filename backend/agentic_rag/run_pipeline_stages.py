#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
舆情流水线 CLI：微观分析（1–4）→ Milvus 同步（4.5，写回后自动）→ 宏观建模（5）→ Obsidian/前端导出（6）。

实现已迁至 agentic_rag.pipeline.runner；本文件保留为稳定入口。
"""
from __future__ import annotations

from agentic_rag.pipeline.runner import main

if __name__ == "__main__":
    main()
