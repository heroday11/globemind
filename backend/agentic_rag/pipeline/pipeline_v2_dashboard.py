"""v2 全流程：控制台底部累计进度行 + 结束汇总（便于对照 pipeline.log）。"""
from __future__ import annotations

import os
from typing import List, Tuple


def print_v2_footer(*, current: str, done: List[Tuple[str, float]]) -> None:
    """每完成一阶段或进入下一阶段前打印一行；grep: [pipeline_v2_footer]"""
    if os.getenv("PIPELINE_V2_FOOTER", "1").strip().lower() in ("0", "false", "no"):
        return
    # 避免 Windows GBK 控制台对 U+25B6/U+2713 等字符报错
    parts_done = " | ".join(f"{name} {sec:.1f}s OK" for name, sec in done) if done else "-"
    line = f"[pipeline_v2_footer] done: {parts_done} | >> {current}"
    print(line, flush=True)


def print_v2_final_summary(rows: List[Tuple[str, str, float]]) -> None:
    """rows: (stage_id, 中文说明, elapsed_s)"""
    if os.getenv("PIPELINE_V2_FOOTER", "1").strip().lower() in ("0", "false", "no"):
        return
    total = sum(r[2] for r in rows)
    print("", flush=True)
    print("[pipeline_v2] === timing summary (see also pipeline.log STAGE_TIMING) ===", flush=True)
    for sid, label, sec in rows:
        print(f"  - {label}  [{sid}]  {sec:.1f}s", flush=True)
    print(f"  - sum  {total:.1f}s  (excl. overhead; micro still logs 1a/1b/1)", flush=True)
    print("[pipeline_v2] " + "=" * 60, flush=True)
