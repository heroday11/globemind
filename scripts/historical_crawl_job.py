#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WAVE1_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
MANIFEST_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_source_manifest_v1_fast.csv"
DISCOVERY_SCRIPT = PROJECT_ROOT / "scripts" / "discover_historical_urls.py"
ADAPTIVE_EXTRACTOR = PROJECT_ROOT / "scripts" / "adaptive_global_extractor.py"
PRUNE_QUEUE_SCRIPT = PROJECT_ROOT / "scripts" / "prune_discovered_urls_queue.py"
PYTHON_BIN = PROJECT_ROOT / ".env_torch" / "bin" / "python"
DEFAULT_JOB_ROOT = PROJECT_ROOT / "data" / "historical_news" / "jobs"
DEFAULT_WAVE1_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "wave1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def read_wave1_rows() -> list[dict[str, str]]:
    with WAVE1_PATH.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resumable historical crawl job manager.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)
    parser.add_argument("--wave1-output-root", type=Path, default=DEFAULT_WAVE1_OUTPUT)
    parser.add_argument("--start-date", default="2023-06-21")
    parser.add_argument("--end-date", default="2026-06-20")
    parser.add_argument("--site-id", action="append", default=[])
    parser.add_argument("--site-limit", type=int, default=0)
    parser.add_argument("--parallel-sites", type=int, default=4)
    parser.add_argument("--discovery-workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-sitemaps-per-site", type=int, default=80)
    parser.add_argument("--site-retries", type=int, default=2)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--pipeline-extract", action="store_true")
    parser.add_argument("--pipeline-interval-sec", type=float, default=120.0)
    parser.add_argument("--global-concurrency", type=int, default=8)
    parser.add_argument("--max-per-domain", type=int, default=4)
    parser.add_argument("--min-per-domain", type=int, default=1)
    parser.add_argument("--proxy-pool", type=Path)
    parser.add_argument("--base-delay-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=150)
    parser.add_argument("--retry-limit", type=int, default=2)
    parser.add_argument("--heartbeat-interval-sec", type=float, default=5.0)
    return parser.parse_args()


class CrawlJob:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.job_dir = args.job_root / args.run_id
        self.logs_dir = self.job_dir / "logs"
        self.site_logs_dir = self.logs_dir / "sites"
        self.state_path = self.job_dir / "state.json"
        self.heartbeat_path = self.job_dir / "heartbeat.json"
        self.manager_log_path = self.logs_dir / "manager.log"
        self.pipeline_extract_log_path = self.logs_dir / "pipeline_extract.log"
        self.prune_log_path = self.logs_dir / "prune_queue.log"
        self.merge_state_path = self.job_dir / "merge_state.json"
        self.merge_output_path = self.job_dir / "wave1_discovered_urls_merged.jsonl"
        self.pruned_merge_output_path = self.job_dir / "wave1_discovered_urls_merged_pruned.jsonl"
        self.pruned_merge_stats_path = self.job_dir / "wave1_discovered_urls_merged_pruned_stats.json"
        self.extract_output_path = self.job_dir / "wave1_articles_merged.jsonl"
        self.extract_error_path = self.job_dir / "wave1_articles_merged_errors.jsonl"
        self.extract_stats_path = self.job_dir / "wave1_articles_merged_stats.json"
        self.extract_progress_path = self.job_dir / "wave1_articles_merged_progress.json"
        self.state_lock = threading.Lock()
        self.merge_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.pipeline_stop_requested = threading.Event()
        self.rows = self._select_rows()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.site_logs_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_or_init_state()
        self.merge_state = self._load_or_init_merge_state()

    def _select_rows(self) -> list[dict[str, str]]:
        rows = read_wave1_rows()
        if self.args.site_id:
            keep = set(self.args.site_id)
            rows = [row for row in rows if row["site_id"] in keep]
        if self.args.site_limit > 0:
            rows = rows[: self.args.site_limit]
        return rows

    def _site_paths(self, site_id: str) -> dict[str, str]:
        site_dir = self.args.wave1_output_root / site_id
        return {
            "site_dir": str(site_dir),
            "discovery_output": str(site_dir / f"{site_id}_urls_{self.args.start_date}_{self.args.end_date}.jsonl"),
            "discovery_report": str(site_dir / f"{site_id}_discovery_{self.args.start_date}_{self.args.end_date}.md"),
            "log_path": str(self.site_logs_dir / f"{site_id}.log"),
        }

    def _site_entry(self, row: dict[str, str]) -> dict[str, Any]:
        paths = self._site_paths(row["site_id"])
        return {
            "site_id": row["site_id"],
            "domain": row.get("domain", ""),
            "source_url": row.get("url", ""),
            "status": "pending",
            "current_stage": "pending",
            "last_error": "",
            "log_path": paths["log_path"],
            "discovery": {
                "status": "pending",
                "attempts": 0,
                "started_at": "",
                "finished_at": "",
                "exit_code": None,
                "rows": 0,
                "output_path": paths["discovery_output"],
                "report_path": paths["discovery_report"],
            },
        }

    def _load_or_init_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            state = {
                "run_id": self.args.run_id,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "status": "pending",
                "current_phase": "pending",
                "pid": None,
                "started_at": "",
                "finished_at": "",
                "requested_stop_at": "",
                "config": {
                    "start_date": self.args.start_date,
                    "end_date": self.args.end_date,
                    "parallel_sites": self.args.parallel_sites,
                    "discovery_workers": self.args.discovery_workers,
                    "timeout": self.args.timeout,
                    "max_sitemaps_per_site": self.args.max_sitemaps_per_site,
                    "skip_extract": self.args.skip_extract,
                    "pipeline_extract": self.args.pipeline_extract,
                    "pipeline_interval_sec": self.args.pipeline_interval_sec,
                    "global_concurrency": self.args.global_concurrency,
                    "max_per_domain": self.args.max_per_domain,
                    "proxy_pool": str(self.args.proxy_pool) if self.args.proxy_pool else "",
                },
                "paths": {
                    "job_dir": str(self.job_dir),
                    "manager_log": str(self.manager_log_path),
                    "pipeline_extract_log": str(self.pipeline_extract_log_path),
                    "merge_state": str(self.merge_state_path),
                    "merge_output": str(self.merge_output_path),
                    "pruned_merge_output": str(self.pruned_merge_output_path),
                    "pruned_merge_stats": str(self.pruned_merge_stats_path),
                    "extract_output": str(self.extract_output_path),
                    "extract_errors": str(self.extract_error_path),
                    "extract_stats": str(self.extract_stats_path),
                    "extract_progress": str(self.extract_progress_path),
                },
                "sites": {},
                "phases": {
                    "discovery": {"status": "pending", "started_at": "", "finished_at": "", "last_error": ""},
                    "merge": {
                        "status": "pending",
                        "started_at": "",
                        "finished_at": "",
                        "rows": 0,
                        "last_error": "",
                        "output_path": str(self.merge_output_path),
                        "pruned_output_path": str(self.pruned_merge_output_path),
                        "pruned_stats_path": str(self.pruned_merge_stats_path),
                    },
                    "extract": {
                        "status": "skipped" if self.args.skip_extract else "pending",
                        "started_at": "",
                        "finished_at": "",
                        "last_error": "",
                        "output_path": str(self.extract_output_path),
                        "error_path": str(self.extract_error_path),
                        "stats_path": str(self.extract_stats_path),
                        "progress_path": str(self.extract_progress_path),
                    },
                },
            }

        selected_ids = [row["site_id"] for row in self.rows]
        known_sites = set(state.get("sites", {}).keys())
        for row in self.rows:
            if row["site_id"] not in known_sites:
                state["sites"][row["site_id"]] = self._site_entry(row)

        for site_id in list(state.get("sites", {}).keys()):
            if site_id not in selected_ids:
                del state["sites"][site_id]

        for site_id, site in state["sites"].items():
            discovery = site["discovery"]
            output_path = Path(discovery["output_path"])
            if discovery["status"] == "running":
                discovery["status"] = "pending"
            if site["status"] == "running":
                site["status"] = "pending"
            site["current_stage"] = "pending"
            if discovery["status"] == "completed":
                discovery["rows"] = count_jsonl_rows(output_path)
                if not output_path.exists():
                    discovery["status"] = "pending"
                    discovery["rows"] = 0

        for phase_name in ("discovery", "merge", "extract"):
            phase = state["phases"][phase_name]
            if phase["status"] == "running":
                phase["status"] = "pending"

        return state

    def _load_or_init_merge_state(self) -> dict[str, Any]:
        if self.merge_state_path.exists():
            try:
                state = json.loads(self.merge_state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        else:
            state = {}
        merged_site_ids = [site_id for site_id in state.get("merged_site_ids", []) if site_id in self.state["sites"]]
        rows_written = int(state.get("rows_written", 0))
        payload = {
            "updated_at": now_iso(),
            "merged_site_ids": merged_site_ids,
            "rows_written": rows_written,
            "last_extract_merged_site_count": int(state.get("last_extract_merged_site_count", 0)),
        }
        atomic_write_json(self.merge_state_path, payload)
        return payload

    def log(self, message: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
        print(line, flush=True)

    def save_state(self) -> None:
        with self.state_lock:
            self.state["updated_at"] = now_iso()
            atomic_write_json(self.state_path, self.state)

    def save_merge_state(self) -> None:
        with self.merge_lock:
            self.merge_state["updated_at"] = now_iso()
            atomic_write_json(self.merge_state_path, self.merge_state)

    def summary(self) -> dict[str, Any]:
        sites = list(self.state["sites"].values())
        discovery_completed = sum(1 for site in sites if site["discovery"]["status"] == "completed")
        discovery_failed = sum(1 for site in sites if site["discovery"]["status"] == "failed")
        discovery_running = sum(1 for site in sites if site["discovery"]["status"] == "running")
        discovery_pending = sum(1 for site in sites if site["discovery"]["status"] == "pending")
        urls_discovered = sum(int(site["discovery"].get("rows", 0)) for site in sites)
        return {
            "run_id": self.args.run_id,
            "status": self.state["status"],
            "current_phase": self.state["current_phase"],
            "pid": self.state.get("pid"),
            "sites_total": len(sites),
            "sites_discovery_completed": discovery_completed,
            "sites_discovery_failed": discovery_failed,
            "sites_discovery_running": discovery_running,
            "sites_discovery_pending": discovery_pending,
            "urls_discovered_total": urls_discovered,
            "failed_sites": [
                {
                    "site_id": site["site_id"],
                    "last_error": site.get("last_error", ""),
                    "log_path": site.get("log_path", ""),
                }
                for site in sites
                if site["discovery"]["status"] == "failed"
            ][:20],
        }

    def write_heartbeat(self) -> None:
        payload = {
            "created_at": now_iso(),
            "alive": not self.stop_requested.is_set(),
            "summary": self.summary(),
        }
        atomic_write_json(self.heartbeat_path, payload)

    def heartbeat_loop(self) -> None:
        while not self.stop_requested.is_set():
            self.write_heartbeat()
            time.sleep(max(1.0, self.args.heartbeat_interval_sec))
        self.write_heartbeat()

    def update_site(self, site_id: str, **changes: Any) -> None:
        with self.state_lock:
            site = self.state["sites"][site_id]
            for key, value in changes.items():
                site[key] = value
            self.state["updated_at"] = now_iso()
            atomic_write_json(self.state_path, self.state)

    def update_site_discovery(self, site_id: str, **changes: Any) -> None:
        with self.state_lock:
            discovery = self.state["sites"][site_id]["discovery"]
            for key, value in changes.items():
                discovery[key] = value
            self.state["updated_at"] = now_iso()
            atomic_write_json(self.state_path, self.state)

    def update_phase(self, phase_name: str, **changes: Any) -> None:
        with self.state_lock:
            phase = self.state["phases"][phase_name]
            for key, value in changes.items():
                phase[key] = value
            self.state["updated_at"] = now_iso()
            atomic_write_json(self.state_path, self.state)

    def append_completed_sites_to_merge(self) -> tuple[int, int]:
        merged_site_ids = set(self.merge_state.get("merged_site_ids", []))
        rows_appended = 0
        sites_appended = 0
        self.merge_output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.merge_lock:
            with self.merge_output_path.open("a", encoding="utf-8") as out_fh:
                for row in self.rows:
                    site_id = row["site_id"]
                    if site_id in merged_site_ids:
                        continue
                    site = self.state["sites"][site_id]
                    discovery = site["discovery"]
                    if discovery["status"] != "completed":
                        continue
                    src_path = Path(discovery["output_path"])
                    if not src_path.exists():
                        continue
                    site_rows = 0
                    with src_path.open("r", encoding="utf-8") as in_fh:
                        for line in in_fh:
                            if line.strip():
                                out_fh.write(line)
                                rows_appended += 1
                                site_rows += 1
                    merged_site_ids.add(site_id)
                    self.merge_state["merged_site_ids"] = sorted(merged_site_ids)
                    self.merge_state["rows_written"] = int(self.merge_state.get("rows_written", 0)) + site_rows
                    sites_appended += 1
        if sites_appended > 0:
            self.save_merge_state()
        return sites_appended, rows_appended

    def extract_progress_complete(self) -> bool:
        if not self.extract_progress_path.exists():
            return False
        try:
            progress = json.loads(self.extract_progress_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        rows = int(progress.get("rows") or 0)
        processed = int(progress.get("processed") or 0)
        return rows > 0 and processed >= rows

    def effective_extract_input_path(self) -> Path:
        if self.pruned_merge_output_path.exists():
            return self.pruned_merge_output_path
        return self.merge_output_path

    def prune_merged_queue(self) -> int:
        if not self.merge_output_path.exists():
            return 0
        cmd = [
            "python3",
            str(PRUNE_QUEUE_SCRIPT),
            "--input",
            str(self.merge_output_path),
            "--output",
            str(self.pruned_merge_output_path),
            "--stats",
            str(self.pruned_merge_stats_path),
            "--start-date",
            self.args.start_date,
            "--end-date",
            self.args.end_date,
        ]
        self.log(f"pruning merged queue: {self.merge_output_path.name} -> {self.pruned_merge_output_path.name}")
        return self.run_logged_command(cmd, self.prune_log_path)

    def maybe_run_pipeline_extract_pass(self) -> None:
        if self.args.skip_extract or self.stop_requested.is_set():
            return
        merged_sites = len(self.merge_state.get("merged_site_ids", []))
        if merged_sites == 0 or not self.merge_output_path.exists():
            return
        if self.extract_progress_complete() and merged_sites <= int(self.merge_state.get("last_extract_merged_site_count", 0)):
            return
        self.update_phase("extract", status="running", started_at=now_iso(), finished_at="", last_error="")
        cmd = [
            str(PYTHON_BIN),
            str(ADAPTIVE_EXTRACTOR),
            "--input",
            str(self.effective_extract_input_path()),
            "--output",
            str(self.extract_output_path),
            "--errors",
            str(self.extract_error_path),
            "--stats",
            str(self.extract_stats_path),
            "--progress-path",
            str(self.extract_progress_path),
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
            "--shuffle",
        ]
        if self.args.proxy_pool:
            cmd.extend(["--proxy-pool", str(self.args.proxy_pool)])
        self.log(f"pipeline extract pass starting for {merged_sites} merged sites")
        rc = self.run_logged_command(cmd, self.pipeline_extract_log_path)
        if rc == 0:
            self.merge_state["last_extract_merged_site_count"] = merged_sites
            self.save_merge_state()
            self.update_phase("extract", status="pending", finished_at=now_iso(), last_error="")
            return
        status = "stopped" if self.stop_requested.is_set() else "failed"
        last_error = "stopped" if self.stop_requested.is_set() else f"pipeline_extract_exit_{rc}"
        self.update_phase("extract", status=status, finished_at=now_iso(), last_error=last_error)

    def pipeline_extract_loop(self) -> None:
        while not self.stop_requested.is_set() and not self.pipeline_stop_requested.is_set():
            sites_appended, rows_appended = self.append_completed_sites_to_merge()
            if sites_appended > 0:
                self.log(f"pipeline merge appended sites={sites_appended} rows={rows_appended}")
            if sites_appended > 0 or (self.merge_output_path.exists() and not self.extract_progress_complete()):
                self.maybe_run_pipeline_extract_pass()
            time.sleep(max(5.0, self.args.pipeline_interval_sec))

    def run_logged_command(self, cmd: list[str], log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log_handle:
            header = f"\n=== {now_iso()} CMD: {' '.join(cmd)} ===\n"
            log_handle.write(header.encode("utf-8", errors="ignore"))
            log_handle.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            while True:
                rc = proc.poll()
                if rc is not None:
                    footer = f"\n=== {now_iso()} EXIT: {rc} ===\n"
                    log_handle.write(footer.encode("utf-8", errors="ignore"))
                    log_handle.flush()
                    return rc
                if self.stop_requested.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    footer = f"\n=== {now_iso()} EXIT: stopped ===\n"
                    log_handle.write(footer.encode("utf-8", errors="ignore"))
                    log_handle.flush()
                    return 130
                time.sleep(1.0)

    def run_site_discovery(self, row: dict[str, str]) -> None:
        site_id = row["site_id"]
        site = self.state["sites"][site_id]
        discovery = site["discovery"]
        if discovery["status"] == "completed" and Path(discovery["output_path"]).exists():
            return
        if discovery["attempts"] >= max(1, self.args.site_retries + 1):
            return
        if self.stop_requested.is_set():
            return

        attempts = int(discovery["attempts"]) + 1
        self.update_site(site_id, status="running", current_stage="discovery", last_error="")
        self.update_site_discovery(
            site_id,
            status="running",
            attempts=attempts,
            started_at=now_iso(),
            finished_at="",
            exit_code=None,
        )
        cmd = [
            "python3",
            str(DISCOVERY_SCRIPT),
            "--input",
            str(MANIFEST_PATH),
            "--output",
            discovery["output_path"],
            "--report",
            discovery["report_path"],
            "--workers",
            str(self.args.discovery_workers),
            "--timeout",
            str(self.args.timeout),
            "--max-sitemaps-per-site",
            str(self.args.max_sitemaps_per_site),
            "--site-id",
            site_id,
            "--start-date",
            self.args.start_date,
            "--end-date",
            self.args.end_date,
        ]
        rc = self.run_logged_command(cmd, Path(site["log_path"]))
        if rc == 0:
            rows = count_jsonl_rows(Path(discovery["output_path"]))
            self.update_site_discovery(
                site_id,
                status="completed",
                finished_at=now_iso(),
                exit_code=0,
                rows=rows,
            )
            self.update_site(site_id, status="completed", current_stage="idle", last_error="")
            return

        status = "pending" if self.stop_requested.is_set() else "failed"
        last_error = "stopped" if self.stop_requested.is_set() else f"discovery_exit_{rc}"
        self.update_site_discovery(
            site_id,
            status=status,
            finished_at=now_iso(),
            exit_code=rc,
        )
        self.update_site(site_id, status=status, current_stage="idle", last_error=last_error)
        if self.args.fail_fast and not self.stop_requested.is_set():
            self.stop_requested.set()

    def run_discovery_phase(self) -> None:
        self.state["current_phase"] = "discovery"
        self.state["status"] = "running"
        self.update_phase("discovery", status="running", started_at=now_iso(), finished_at="", last_error="")
        pipeline_thread = None
        if self.args.pipeline_extract and not self.args.skip_extract:
            pipeline_thread = threading.Thread(target=self.pipeline_extract_loop, daemon=True)
            pipeline_thread.start()
        pending_rows = []
        for row in self.rows:
            site = self.state["sites"][row["site_id"]]
            discovery = site["discovery"]
            if discovery["status"] == "completed" and Path(discovery["output_path"]).exists():
                continue
            if discovery["attempts"] >= max(1, self.args.site_retries + 1) and discovery["status"] == "failed":
                continue
            pending_rows.append(row)

        if not pending_rows:
            self.update_phase("discovery", status="completed", finished_at=now_iso())
            self.save_state()
            return

        with ThreadPoolExecutor(max_workers=max(1, self.args.parallel_sites)) as executor:
            futures = [executor.submit(self.run_site_discovery, row) for row in pending_rows]
            for future in as_completed(futures):
                future.result()
                if self.stop_requested.is_set():
                    break

        phase_status = "completed"
        last_error = ""
        if self.stop_requested.is_set():
            phase_status = "stopped"
            last_error = "stop_requested"
        elif any(site["discovery"]["status"] == "failed" for site in self.state["sites"].values()):
            phase_status = "completed_with_errors"
            last_error = "site_failures"
        self.update_phase("discovery", status=phase_status, finished_at=now_iso(), last_error=last_error)
        if pipeline_thread is not None:
            self.pipeline_stop_requested.set()
            pipeline_thread.join(timeout=max(10.0, self.args.pipeline_interval_sec + 10.0))

    def run_merge_phase(self) -> None:
        if self.stop_requested.is_set():
            return
        self.state["current_phase"] = "merge"
        self.update_phase("merge", status="running", started_at=now_iso(), finished_at="", last_error="")
        sites_appended, rows_appended = self.append_completed_sites_to_merge()
        rows_written = int(self.merge_state.get("rows_written", 0))
        self.log(f"final merge appended sites={sites_appended} rows={rows_appended} total_rows={rows_written}")
        prune_rc = self.prune_merged_queue()
        if prune_rc != 0 and not self.stop_requested.is_set():
            self.update_phase("merge", status="failed", finished_at=now_iso(), rows=rows_written, last_error=f"prune_exit_{prune_rc}")
            return
        self.update_phase("merge", status="completed", finished_at=now_iso(), rows=rows_written, last_error="")

    def run_extract_phase(self) -> None:
        if self.args.skip_extract or self.stop_requested.is_set():
            return
        extract_phase = self.state["phases"]["extract"]
        if extract_phase["status"] == "completed" and self.extract_stats_path.exists():
            return
        self.state["current_phase"] = "extract"
        self.update_phase("extract", status="running", started_at=now_iso(), finished_at="", last_error="")
        cmd = [
            str(PYTHON_BIN),
            str(ADAPTIVE_EXTRACTOR),
            "--input",
            str(self.effective_extract_input_path()),
            "--output",
            str(self.extract_output_path),
            "--errors",
            str(self.extract_error_path),
            "--stats",
            str(self.extract_stats_path),
            "--progress-path",
            str(self.extract_progress_path),
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
            "--shuffle",
        ]
        if self.args.proxy_pool:
            cmd.extend(["--proxy-pool", str(self.args.proxy_pool)])
        rc = self.run_logged_command(cmd, self.manager_log_path)
        if rc == 0:
            self.update_phase("extract", status="completed", finished_at=now_iso(), last_error="")
            return
        status = "stopped" if self.stop_requested.is_set() else "failed"
        last_error = "stopped" if self.stop_requested.is_set() else f"extract_exit_{rc}"
        self.update_phase("extract", status=status, finished_at=now_iso(), last_error=last_error)

    def finalize(self) -> None:
        discovery_failed = any(site["discovery"]["status"] == "failed" for site in self.state["sites"].values())
        merge_status = self.state["phases"]["merge"]["status"]
        extract_status = self.state["phases"]["extract"]["status"]
        if self.stop_requested.is_set():
            self.state["status"] = "stopped"
        elif merge_status == "failed" or extract_status == "failed":
            self.state["status"] = "failed"
        elif discovery_failed:
            self.state["status"] = "completed_with_errors"
        else:
            self.state["status"] = "completed"
        self.state["current_phase"] = "idle"
        self.state["finished_at"] = now_iso()
        self.save_state()
        self.write_heartbeat()

    def run(self) -> int:
        self.state["pid"] = os.getpid()
        self.state["started_at"] = self.state["started_at"] or now_iso()
        self.state["status"] = "running"
        self.state["current_phase"] = "bootstrap"
        self.save_state()

        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        self.log(f"job {self.args.run_id} started pid={os.getpid()}")

        try:
            self.run_discovery_phase()
            if not self.stop_requested.is_set():
                self.run_merge_phase()
            if not self.stop_requested.is_set():
                self.run_extract_phase()
        finally:
            self.finalize()
        self.log(f"job {self.args.run_id} finished status={self.state['status']}")
        return 0 if self.state["status"] in {"completed", "completed_with_errors", "stopped"} else 1


def install_signal_handlers(job: CrawlJob) -> None:
    def _handle(_signum: int, _frame: Any) -> None:
        job.log("stop signal received")
        job.state["requested_stop_at"] = now_iso()
        job.stop_requested.set()
        job.pipeline_stop_requested.set()
        job.save_state()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    args = parse_args()
    job = CrawlJob(args)
    install_signal_handlers(job)
    raise SystemExit(job.run())


if __name__ == "__main__":
    main()
