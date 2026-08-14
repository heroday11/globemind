"""
跨多次运行的微观处理「总条数」预算（持久化）。

仅统计 Phase 1a（BGE 筛）或 legacy run_analysis 每批写回的行数，避免与 1b 重复计数。
状态文件：agentic_rag/data/micro_total_budget.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Tuple

_STATE_NAME = "micro_total_budget.json"


def budget_state_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / _STATE_NAME


def load_consumed() -> int:
    p = budget_state_path()
    if not p.is_file():
        return 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("consumed_micro", 0))
    except Exception:
        return 0


def _save_consumed(consumed: int) -> None:
    p = budget_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"consumed_micro": int(consumed)}, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def reset_budget() -> None:
    """将累计计数清零（与 CLI --micro-total-cap-reset 对应）。"""
    _save_consumed(0)


def remaining(micro_total_cap: int) -> int:
    return max(0, int(micro_total_cap) - load_consumed())


def add_consumed_micro(n: int) -> None:
    """本批成功写回后增加累计（n 为写回行数）。"""
    if n <= 0:
        return
    _save_consumed(load_consumed() + int(n))


def effective_row_limit(
    max_rows: Optional[int],
    micro_total_cap: Optional[int],
) -> Tuple[Optional[int], Optional[str]]:
    """
    返回本段流水线应使用的条数上限，及对「跳过」原因的说明。

    - 若 micro_total_cap 为 None：等价于 max_rows。
    - 若二者皆有：取 min。
    - 若仅 micro_total_cap：本段最多还可处理 remaining 条。
    - 若剩余为 0：返回 (0, 原因) 表示本段不应启动。
    """
    if micro_total_cap is None:
        return max_rows, None
    rem = remaining(micro_total_cap)
    if rem <= 0:
        return 0, f"micro_total_cap={micro_total_cap} 已满（已累计 {load_consumed()}）"
    if max_rows is None:
        return rem, None
    return min(int(max_rows), rem), None
