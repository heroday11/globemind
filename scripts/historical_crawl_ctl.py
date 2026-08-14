#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOB_ROOT = PROJECT_ROOT / "data" / "historical_news" / "jobs"
JOB_SCRIPT = PROJECT_ROOT / "scripts" / "historical_crawl_job.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control resumable historical crawl jobs.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a job in background.")
    start.add_argument("--run-id", required=True)
    start.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)
    start.add_argument("--wave1-output-root", type=Path, default=PROJECT_ROOT / "data" / "historical_news" / "wave1")
    start.add_argument("--start-date", default="2023-06-21")
    start.add_argument("--end-date", default="2026-06-20")
    start.add_argument("--site-id", action="append", default=[])
    start.add_argument("--site-limit", type=int, default=0)
    start.add_argument("--parallel-sites", type=int, default=4)
    start.add_argument("--discovery-workers", type=int, default=2)
    start.add_argument("--timeout", type=float, default=20.0)
    start.add_argument("--max-sitemaps-per-site", type=int, default=80)
    start.add_argument("--site-retries", type=int, default=2)
    start.add_argument("--fail-fast", action="store_true")
    start.add_argument("--skip-extract", action="store_true")
    start.add_argument("--pipeline-extract", action="store_true")
    start.add_argument("--pipeline-interval-sec", type=float, default=120.0)
    start.add_argument("--global-concurrency", type=int, default=8)
    start.add_argument("--max-per-domain", type=int, default=4)
    start.add_argument("--min-per-domain", type=int, default=1)
    start.add_argument("--proxy-pool", type=Path)
    start.add_argument("--base-delay-ms", type=int, default=0)
    start.add_argument("--jitter-ms", type=int, default=150)
    start.add_argument("--retry-limit", type=int, default=2)
    start.add_argument("--heartbeat-interval-sec", type=float, default=5.0)

    status = sub.add_parser("status", help="Show job status.")
    status.add_argument("--run-id", required=True)
    status.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)

    stop = sub.add_parser("stop", help="Stop a running job.")
    stop.add_argument("--run-id", required=True)
    stop.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)

    tail = sub.add_parser("tail", help="Show recent log lines.")
    tail.add_argument("--run-id", required=True)
    tail.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)
    tail.add_argument("--site-id")
    tail.add_argument("--lines", type=int, default=40)

    return parser.parse_args()


def job_dir(job_root: Path, run_id: str) -> Path:
    return job_root / run_id


def state_path(job_root: Path, run_id: str) -> Path:
    return job_dir(job_root, run_id) / "state.json"


def heartbeat_path(job_root: Path, run_id: str) -> Path:
    return job_dir(job_root, run_id) / "heartbeat.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def build_start_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        "python3",
        str(JOB_SCRIPT),
        "--run-id",
        args.run_id,
        "--job-root",
        str(args.job_root),
        "--wave1-output-root",
        str(args.wave1_output_root),
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--parallel-sites",
        str(args.parallel_sites),
        "--discovery-workers",
        str(args.discovery_workers),
        "--timeout",
        str(args.timeout),
        "--max-sitemaps-per-site",
        str(args.max_sitemaps_per_site),
        "--site-retries",
        str(args.site_retries),
        "--pipeline-interval-sec",
        str(args.pipeline_interval_sec),
        "--global-concurrency",
        str(args.global_concurrency),
        "--max-per-domain",
        str(args.max_per_domain),
        "--min-per-domain",
        str(args.min_per_domain),
        "--base-delay-ms",
        str(args.base_delay_ms),
        "--jitter-ms",
        str(args.jitter_ms),
        "--retry-limit",
        str(args.retry_limit),
        "--heartbeat-interval-sec",
        str(args.heartbeat_interval_sec),
    ]
    for site_id in args.site_id:
        cmd.extend(["--site-id", site_id])
    if args.site_limit > 0:
        cmd.extend(["--site-limit", str(args.site_limit)])
    if args.fail_fast:
        cmd.append("--fail-fast")
    if args.skip_extract:
        cmd.append("--skip-extract")
    if args.pipeline_extract:
        cmd.append("--pipeline-extract")
    if args.proxy_pool:
        cmd.extend(["--proxy-pool", str(args.proxy_pool)])
    return cmd


def cmd_start(args: argparse.Namespace) -> int:
    state_file = state_path(args.job_root, args.run_id)
    if state_file.exists():
        state = load_json(state_file)
        if pid_alive(state.get("pid")):
            print(f"job already running: pid={state.get('pid')}")
            return 1
    target_dir = job_dir(args.job_root, args.run_id)
    logs_dir = target_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    manager_log = logs_dir / "manager.log"
    cmd = build_start_cmd(args)
    log_handle = manager_log.open("ab")
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"started run_id={args.run_id} pid={proc.pid}")
    print(f"job_dir={target_dir}")
    print(f"manager_log={manager_log}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_file = state_path(args.job_root, args.run_id)
    if not state_file.exists():
        print("job not found")
        return 1
    state = load_json(state_file)
    heartbeat_file = heartbeat_path(args.job_root, args.run_id)
    heartbeat = load_json(heartbeat_file) if heartbeat_file.exists() else {}
    progress_path = Path(state["paths"]["extract_progress"])
    extract_progress = load_json(progress_path) if progress_path.exists() else {}

    sites = list(state["sites"].values())
    done = sum(1 for site in sites if site["discovery"]["status"] == "completed")
    failed = sum(1 for site in sites if site["discovery"]["status"] == "failed")
    running = [site["site_id"] for site in sites if site["discovery"]["status"] == "running"]
    pending = sum(1 for site in sites if site["discovery"]["status"] == "pending")
    urls_total = sum(int(site["discovery"].get("rows", 0)) for site in sites)

    print(f"run_id: {args.run_id}")
    print(f"status: {state.get('status')} alive={pid_alive(state.get('pid'))} pid={state.get('pid')}")
    print(f"phase: {state.get('current_phase')}")
    print(f"updated_at: {state.get('updated_at')}")
    if heartbeat:
        print(f"heartbeat_at: {heartbeat.get('created_at')}")
    print(f"discovery: completed={done} failed={failed} pending={pending} running={len(running)} total={len(sites)}")
    print(f"urls_discovered_total: {urls_total}")
    if running:
        print("running_sites:", ", ".join(running[:12]))
    merge_phase = state["phases"]["merge"]
    print(f"merge: status={merge_phase.get('status')} rows={merge_phase.get('rows', 0)}")
    extract_phase = state["phases"]["extract"]
    print(f"extract: status={extract_phase.get('status')}")
    if extract_progress:
        print(
            "extract_progress:"
            f" processed={extract_progress.get('processed', 0)}/{extract_progress.get('rows', 0)}"
            f" success={extract_progress.get('successes', 0)}"
            f" fail={extract_progress.get('failures', 0)}"
            f" remaining={extract_progress.get('rows_remaining', 0)}"
            f" rate={extract_progress.get('successes_per_min', 0)}/min"
        )
        top_error_sites = extract_progress.get("top_error_sites") or []
        if top_error_sites:
            print("extract_top_error_sites:")
            for site_id, count in top_error_sites[:10]:
                print(f"  {site_id}: {count}")
    failed_sites = [
        (site["site_id"], site.get("last_error", ""), site.get("log_path", ""))
        for site in sites
        if site["discovery"]["status"] == "failed"
    ]
    if failed_sites:
        print("failed_sites:")
        for site_id, last_error, log_path in failed_sites[:10]:
            print(f"  {site_id} error={last_error} log={log_path}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state_file = state_path(args.job_root, args.run_id)
    if not state_file.exists():
        print("job not found")
        return 1
    state = load_json(state_file)
    pid = state.get("pid")
    if not pid_alive(pid):
        print("job is not running")
        return 1
    os.kill(int(pid), signal.SIGTERM)
    print(f"sent SIGTERM to pid={pid}")
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    base_dir = job_dir(args.job_root, args.run_id)
    if args.site_id:
        target = base_dir / "logs" / "sites" / f"{args.site_id}.log"
    else:
        target = base_dir / "logs" / "manager.log"
    if not target.exists():
        print("log not found")
        return 1
    lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def main() -> None:
    args = parse_args()
    if args.command == "start":
        raise SystemExit(cmd_start(args))
    if args.command == "status":
        raise SystemExit(cmd_status(args))
    if args.command == "stop":
        raise SystemExit(cmd_stop(args))
    if args.command == "tail":
        raise SystemExit(cmd_tail(args))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
