"""阶段 6：Obsidian 库与前端产物导出。"""
from __future__ import annotations

import time

from agentic_rag.sync_obsidian_v4 import run_sync_v4


def run_stage6(
    *,
    output_dir: str,
    export_front_artifacts: bool,
    clear_vault: bool,
    silent: bool = False,
) -> None:
    """silent=True 时不打印阶段⑥耗时（供 stage 999 全链路末尾统一汇总）。"""
    t0 = time.perf_counter()
    run_sync_v4(
        output_dir=output_dir,
        export_front_artifacts=export_front_artifacts,
        clear_vault=clear_vault,
    )
    if not silent:
        print(f"[阶段⑥] 完成，耗时 {time.perf_counter() - t0:.1f}s")
