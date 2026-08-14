"""流水线追加日志：长任务排障、对照断点续跑（关闭：PIPELINE_LOG=0）。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_FILE = "pipeline.log"


def pipeline_logging_enabled() -> bool:
    return os.getenv("PIPELINE_LOG", "1").strip().lower() not in ("0", "false", "no")


def pipeline_log_path() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR / _LOG_FILE


def log_pipeline(message: str) -> None:
    if not pipeline_logging_enabled():
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {message}\n"
    try:
        with open(pipeline_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def log_pipeline_progress(
    stage: str,
    *,
    done: int,
    total: int | None = None,
    elapsed_s: float | None = None,
    eta_s: float | None = None,
    extra: str = "",
) -> None:
    """
    控制台 + pipeline.log 同步一行，便于外部 grep / 估算 ETA。
    格式固定前缀 [PIPELINE_PROGRESS]，字段空格分隔。
    """
    parts = [
        f"[PIPELINE_PROGRESS] stage={stage}",
        f"done={done}",
    ]
    if total is not None:
        parts.append(f"total={total}")
        if total > 0:
            parts.append(f"pct={100.0 * done / total:.2f}")
    if elapsed_s is not None:
        parts.append(f"elapsed_s={elapsed_s:.2f}")
    if eta_s is not None and eta_s >= 0:
        parts.append(f"eta_s={eta_s:.2f}")
    if extra:
        parts.append(extra.strip())
    line = " ".join(parts)
    print(line, flush=True)
    log_pipeline(line)


_STAGE_LABELS: dict[str, str] = {
    "445_part44": "44·Milvus同步",
    "445_stage5": "5·宏观/Snowball",
    "4456_part44": "44·Milvus同步",
    "4456_stage5": "5·宏观/Snowball",
    "4456_stage6": "6·Obsidian导出",
    "0": "0·预处理",
    "2": "2·BGE",
    "4": "4·写库",
    "v2_1_micro": "v2·①微观(1a+1b)",
    "v2_2_graph": "v2·②图/Snowball",
    "v2_3_llm": "v2·③宏观LLM补全",
    "v2_4_export": "v2·④Obsidian导出",
}


def _timing_json_payload(stage: str, elapsed_s: float, **kwargs: object) -> dict:
    payload: dict = {
        "event": "stage_timing",
        "stage": stage,
        "elapsed_s": round(float(elapsed_s), 3),
    }
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, Path):
            payload[k] = str(v)
        elif isinstance(v, (str, int, float, bool)):
            payload[k] = v
        else:
            payload[k] = str(v)
    return payload


def log_stage_timing(stage: str, elapsed_s: float, **kwargs: object) -> None:
    """
    结构化写入 pipeline.log；控制台输出人类可读行 + 单行 JSON（便于 grep/采集）。
    关闭 JSON 行：PIPELINE_TIMING_JSON=0
    kwargs 仅记录简单标量（max_rows、batch_size、limit 等）。
    """
    parts = [f"STAGE_TIMING stage={stage!r}", f"elapsed_s={elapsed_s:.3f}"]
    for k in sorted(kwargs.keys()):
        v = kwargs[k]
        if v is None:
            continue
        parts.append(f"{k}={v!r}")
    log_pipeline(" ".join(parts))

    label = _STAGE_LABELS.get(str(stage), str(stage))
    print(f"[阶段计时] {label} ({stage}) 用时 {elapsed_s:.1f}s", flush=True)
    if os.getenv("PIPELINE_TIMING_JSON", "1").strip().lower() not in ("0", "false", "no"):
        try:
            line = json.dumps(
                _timing_json_payload(stage, elapsed_s, **kwargs),
                ensure_ascii=False,
            )
            print(f"[PIPELINE_JSON] {line}", flush=True)
        except (TypeError, ValueError):
            pass
