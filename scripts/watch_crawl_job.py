#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOB_ROOT = PROJECT_ROOT / "data" / "historical_news" / "jobs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write periodic status snapshots for a crawl job.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)
    parser.add_argument("--interval-sec", type=int, default=3600)
    parser.add_argument("--tail-lines", type=int, default=20)
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return content[-max(1, lines) :]


def append_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    job_dir = args.job_root / args.run_id
    state_path = job_dir / "state.json"
    heartbeat_path = job_dir / "heartbeat.json"
    progress_path = job_dir / "wave1_articles_merged_progress.json"
    manager_log_path = job_dir / "logs" / "manager.log"
    output_path = job_dir / "logs" / "hourly_status.jsonl"

    while True:
        state = load_json(state_path)
        heartbeat = load_json(heartbeat_path)
        progress = load_json(progress_path)
        alive = pid_alive(state.get("pid"))
        payload = {
            "logged_at": now_iso(),
            "run_id": args.run_id,
            "alive": alive,
            "state": {
                "status": state.get("status"),
                "current_phase": state.get("current_phase"),
                "pid": state.get("pid"),
                "updated_at": state.get("updated_at"),
                "started_at": state.get("started_at"),
                "finished_at": state.get("finished_at"),
            },
            "heartbeat_at": heartbeat.get("created_at"),
            "extract_progress": {
                "processed": progress.get("processed"),
                "rows": progress.get("rows"),
                "rows_remaining": progress.get("rows_remaining"),
                "successes": progress.get("successes"),
                "failures": progress.get("failures"),
                "successes_per_min": progress.get("successes_per_min"),
                "completion_rate": progress.get("completion_rate"),
                "top_errors": progress.get("top_errors"),
                "top_error_sites": progress.get("top_error_sites"),
            },
            "manager_tail": read_tail(manager_log_path, args.tail_lines),
        }
        append_snapshot(output_path, payload)

        terminal_state = str(state.get("status") or "")
        if terminal_state in {"completed", "completed_with_errors", "failed", "stopped"} and not alive:
            break
        time.sleep(max(60, args.interval_sec))


if __name__ == "__main__":
    main()
