#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_BIN = PROJECT_ROOT / ".env_torch" / "bin" / "python"
EXTRACTOR_SCRIPT = PROJECT_ROOT / "scripts" / "adaptive_global_extractor.py"

STOP_REQUESTED = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervise adaptive extractor with auto-restart.")
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--errors", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--progress-path", type=Path, required=True)
    parser.add_argument("--python-bin", type=Path, default=PYTHON_BIN)
    parser.add_argument("--proxy-pool", type=Path)
    parser.add_argument("--global-concurrency", type=int, default=16)
    parser.add_argument("--max-per-domain", type=int, default=4)
    parser.add_argument("--min-per-domain", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--base-delay-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=150)
    parser.add_argument("--retry-limit", type=int, default=2)
    parser.add_argument("--progress-interval-sec", type=float, default=5.0)
    parser.add_argument("--flush-every", type=int, default=25)
    parser.add_argument("--heartbeat-interval-sec", type=float, default=30.0)
    parser.add_argument("--stale-progress-sec", type=float, default=900.0)
    parser.add_argument("--restart-delay-sec", type=float, default=5.0)
    parser.add_argument("--proxy-failure-threshold", type=int, default=3)
    parser.add_argument("--proxy-base-cooldown-sec", type=float, default=120.0)
    parser.add_argument("--proxy-max-cooldown-sec", type=float, default=1800.0)
    parser.add_argument("--proxy-health-path", type=Path)
    return parser.parse_args()


def install_signal_handlers() -> None:
    def _handle(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


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


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


class Supervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.logs_dir = self.args.job_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path = self.args.job_dir / "extractor_supervisor_heartbeat.json"
        self.state_path = self.args.job_dir / "extractor_supervisor_state.json"
        self.stop_path = self.args.job_dir / "extractor_supervisor.stop"
        self.child_log_path = self.logs_dir / "extractor_stdout.log"
        self.supervisor_log_path = self.logs_dir / "extractor_supervisor.log"
        self.child: subprocess.Popen[bytes] | None = None
        self.child_log_handle = None
        self.restart_count = 0
        self.last_progress_snapshot: dict[str, Any] = {}
        self.last_progress_ts = 0.0

    def log(self, message: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        with self.supervisor_log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    def build_cmd(self) -> list[str]:
        cmd = [
            str(self.args.python_bin),
            str(EXTRACTOR_SCRIPT),
            "--input",
            str(self.args.input),
            "--output",
            str(self.args.output),
            "--errors",
            str(self.args.errors),
            "--stats",
            str(self.args.stats),
            "--progress-path",
            str(self.args.progress_path),
            "--resume",
            "--global-concurrency",
            str(self.args.global_concurrency),
            "--max-per-domain",
            str(self.args.max_per_domain),
            "--min-per-domain",
            str(self.args.min_per_domain),
            "--timeout",
            str(self.args.timeout),
            "--base-delay-ms",
            str(self.args.base_delay_ms),
            "--jitter-ms",
            str(self.args.jitter_ms),
            "--retry-limit",
            str(self.args.retry_limit),
            "--progress-interval-sec",
            str(self.args.progress_interval_sec),
            "--flush-every",
            str(self.args.flush_every),
            "--shuffle",
            "--proxy-failure-threshold",
            str(self.args.proxy_failure_threshold),
            "--proxy-base-cooldown-sec",
            str(self.args.proxy_base_cooldown_sec),
            "--proxy-max-cooldown-sec",
            str(self.args.proxy_max_cooldown_sec),
        ]
        if self.args.proxy_pool:
            cmd.extend(["--proxy-pool", str(self.args.proxy_pool)])
        if self.args.proxy_health_path:
            cmd.extend(["--proxy-health-path", str(self.args.proxy_health_path)])
        return cmd

    def read_progress(self) -> dict[str, Any]:
        progress = load_json(self.args.progress_path)
        if progress:
            self.last_progress_snapshot = progress
            self.last_progress_ts = self.args.progress_path.stat().st_mtime
        return progress

    def write_state(self, status: str, note: str = "") -> None:
        progress = self.read_progress()
        payload = {
            "updated_at": now_iso(),
            "status": status,
            "note": note,
            "restart_count": self.restart_count,
            "supervisor_pid": os.getpid(),
            "child_pid": self.child.pid if self.child else None,
            "child_alive": pid_alive(self.child.pid if self.child else None),
            "progress_mtime": self.last_progress_ts,
            "progress": {
                "processed": progress.get("processed"),
                "rows": progress.get("rows"),
                "rows_remaining": progress.get("rows_remaining"),
                "successes": progress.get("successes"),
                "failures": progress.get("failures"),
                "successes_per_min": progress.get("successes_per_min"),
                "running": progress.get("running"),
            },
        }
        atomic_write_json(self.state_path, payload)
        atomic_write_json(
            self.heartbeat_path,
            {
                "created_at": now_iso(),
                "alive": not STOP_REQUESTED,
                "supervisor_pid": os.getpid(),
                "child_pid": self.child.pid if self.child else None,
                "status": status,
                "restart_count": self.restart_count,
            },
        )

    def start_child(self) -> None:
        self.child_log_handle = self.child_log_path.open("ab")
        self.child = subprocess.Popen(
            self.build_cmd(),
            cwd=PROJECT_ROOT,
            stdout=self.child_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.log(f"started extractor pid={self.child.pid}")
        self.write_state("running", "child_started")

    def stop_child(self, sig: int = signal.SIGTERM, wait_sec: float = 20.0) -> None:
        if not self.child or self.child.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.child.pid), sig)
        except ProcessLookupError:
            return
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if self.child.poll() is not None:
                break
            time.sleep(0.5)
        if self.child.poll() is None:
            try:
                os.killpg(os.getpgid(self.child.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.child_log_handle:
            self.child_log_handle.flush()
            self.child_log_handle.close()
            self.child_log_handle = None

    def is_complete(self) -> bool:
        progress = self.read_progress()
        rows = progress.get("rows")
        processed = progress.get("processed")
        remaining = progress.get("rows_remaining")
        if rows is None or processed is None:
            return False
        if remaining == 0:
            return True
        return bool(rows) and processed >= rows

    def is_stale(self) -> bool:
        progress = self.read_progress()
        if not progress:
            return False
        if self.is_complete():
            return False
        if not self.last_progress_ts:
            return False
        return (time.time() - self.last_progress_ts) >= self.args.stale_progress_sec

    def run(self) -> int:
        self.log("supervisor started")
        self.start_child()
        last_heartbeat = 0.0
        while not STOP_REQUESTED:
            if self.stop_path.exists():
                self.log("stop file detected")
                break
            now = time.time()
            if now - last_heartbeat >= max(5.0, self.args.heartbeat_interval_sec):
                self.write_state("running", "heartbeat")
                last_heartbeat = now

            if self.child and self.child.poll() is not None:
                code = self.child.returncode
                if self.child_log_handle:
                    self.child_log_handle.flush()
                    self.child_log_handle.close()
                    self.child_log_handle = None
                if self.is_complete():
                    self.log(f"extractor exited code={code} after completion")
                    self.write_state("completed", f"child_exit_{code}")
                    return 0
                self.restart_count += 1
                self.log(f"extractor exited code={code}, restarting")
                self.write_state("restarting", f"child_exit_{code}")
                time.sleep(max(1.0, self.args.restart_delay_sec))
                self.start_child()

            if self.child and self.child.poll() is None and self.is_stale():
                self.restart_count += 1
                self.log("progress stale, restarting extractor")
                self.write_state("restarting", "stale_progress")
                self.stop_child(sig=signal.SIGTERM)
                time.sleep(max(1.0, self.args.restart_delay_sec))
                self.start_child()

            if self.is_complete():
                self.log("progress indicates completion")
                self.write_state("completed", "progress_complete")
                self.stop_child(sig=signal.SIGTERM)
                return 0

            time.sleep(5.0)

        self.write_state("stopping", "signal_or_stop_file")
        self.stop_child(sig=signal.SIGTERM)
        self.write_state("stopped", "supervisor_stopped")
        self.log("supervisor stopped")
        return 0


def main() -> None:
    install_signal_handlers()
    args = parse_args()
    supervisor = Supervisor(args)
    raise SystemExit(supervisor.run())


if __name__ == "__main__":
    main()
