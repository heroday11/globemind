"""
utils/monitor.py  —  后台静默系统监控工具

每 5 秒采集一次快照：
  - CPU 总逻辑核心利用率 (%)
  - RAM 系统占用 (GB) 及增量
  - VRAM RTX 3080 显存占用 (MB)
  - Disk I/O 写入压力 (MB/s)

使用方式：
    from utils.monitor import SystemMonitor
    mon = SystemMonitor(interval=5)
    mon.start()
    # ... 业务逻辑 ...
    summary = mon.stop()
    mon.save_report("outputs/monitor_log.json")
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

import psutil

# GPU 支持（nvidia-ml-py，官方推荐）
try:
    from pynvml import (
        nvmlInit, nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetName, nvmlDeviceGetMemoryInfo,
    )
    nvmlInit()
    _GPU_HANDLE = nvmlDeviceGetHandleByIndex(0)
    _GPU_NAME   = nvmlDeviceGetName(_GPU_HANDLE)
    if isinstance(_GPU_NAME, bytes):
        _GPU_NAME = _GPU_NAME.decode()
    _GPU_TOTAL  = nvmlDeviceGetMemoryInfo(_GPU_HANDLE).total / 1024 / 1024
    GPU_BACKEND = "nvidia-ml-py"
except Exception:
    try:
        import GPUtil
        _GPUS = GPUtil.getGPUs()
        if _GPUS:
            _GPU_NAME  = _GPUS[0].name
            _GPU_TOTAL = _GPUS[0].memoryTotal
        else:
            _GPU_NAME  = "N/A"
            _GPU_TOTAL = 0
        GPU_BACKEND = "gputil"
    except Exception:
        GPU_BACKEND = "none"
        _GPU_NAME   = "N/A"
        _GPU_TOTAL  = 0


@dataclass
class Snapshot:
    ts:           float   # Unix epoch
    cpu_pct:      float   # 总 CPU 利用率 %
    ram_gb:       float   # 系统 RAM 使用 GB
    ram_pct:      float   # 系统 RAM 使用 %
    vram_mb:      float   # GPU 显存使用 MB
    vram_pct:     float   # GPU 显存使用 %
    disk_write_mb: float  # 磁盘写入速度 MB/s（区间均值）
    label:        str = ""  # 可选标注（当前阶段名称）


@dataclass
class MonitorSummary:
    gpu_name:       str   = ""
    gpu_total_mb:   float = 0.0
    cpu_peak_pct:   float = 0.0
    cpu_avg_pct:    float = 0.0
    ram_peak_gb:    float = 0.0
    ram_avg_gb:     float = 0.0
    ram_delta_gb:   float = 0.0   # 峰值 - 初始
    vram_peak_mb:   float = 0.0
    vram_avg_mb:    float = 0.0
    disk_peak_mbs:  float = 0.0
    disk_avg_mbs:   float = 0.0
    duration_s:     float = 0.0
    n_snapshots:    int   = 0
    snapshots:      List[dict] = field(default_factory=list)


class SystemMonitor:
    """
    后台线程监控器，每 interval 秒采集一次系统快照。
    线程安全，可在任意时刻调用 label() 标注当前阶段。
    """

    def __init__(self, interval: int = 5):
        self.interval    = interval
        self._snapshots: List[Snapshot] = []
        self._lock       = threading.Lock()
        self._stop_evt   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_label = ""
        self._start_ram_gb  = 0.0
        self._last_disk_bytes = 0
        self._last_disk_ts    = 0.0
        # 确定监控磁盘（C: 或项目所在盘）
        self._disk = self._detect_disk()
        print(f"[Monitor] GPU={_GPU_NAME} ({_GPU_TOTAL:.0f}MB) backend={GPU_BACKEND}")
        print(f"[Monitor] Disk={self._disk} interval={interval}s")

    @staticmethod
    def _detect_disk() -> str:
        """返回项目所在磁盘分区。"""
        try:
            path = str(Path(__file__).resolve().anchor)
            for part in psutil.disk_partitions():
                if part.mountpoint.lower() == path.lower() or \
                   path.lower().startswith(part.mountpoint.lower()):
                    return part.mountpoint
        except Exception:
            pass
        return "C:\\\\"

    def _get_vram_mb(self) -> float:
        try:
            if GPU_BACKEND == "nvidia-ml-py":
                from pynvml import nvmlDeviceGetMemoryInfo
                info = nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                return info.used / 1024 / 1024
            elif GPU_BACKEND == "gputil":
                gpus = GPUtil.getGPUs()
                return gpus[0].memoryUsed if gpus else 0.0
        except Exception:
            pass
        return 0.0

    def _get_disk_write_mbs(self) -> float:
        """返回自上次采样以来的磁盘写入速度 (MB/s)。"""
        try:
            now   = time.perf_counter()
            stats = psutil.disk_io_counters(perdisk=False)
            if stats is None:
                return 0.0
            curr_bytes = stats.write_bytes
            if self._last_disk_ts > 0:
                dt    = now - self._last_disk_ts
                delta = curr_bytes - self._last_disk_bytes
                speed = delta / dt / 1024 / 1024
            else:
                speed = 0.0
            self._last_disk_bytes = curr_bytes
            self._last_disk_ts    = now
            return max(speed, 0.0)
        except Exception:
            return 0.0

    def _collect(self) -> Snapshot:
        """采集一次快照。"""
        cpu   = psutil.cpu_percent(interval=None)
        mem   = psutil.virtual_memory()
        vram  = self._get_vram_mb()
        disk  = self._get_disk_write_mbs()
        return Snapshot(
            ts            = time.time(),
            cpu_pct       = cpu,
            ram_gb        = mem.used / 1024**3,
            ram_pct       = mem.percent,
            vram_mb       = vram,
            vram_pct      = vram / _GPU_TOTAL * 100 if _GPU_TOTAL > 0 else 0,
            disk_write_mb = disk,
            label         = self._current_label,
        )

    def _run(self) -> None:
        # 预热 CPU 采样
        psutil.cpu_percent(interval=None)
        self._last_disk_ts = 0.0
        while not self._stop_evt.is_set():
            snap = self._collect()
            with self._lock:
                self._snapshots.append(snap)
            self._stop_evt.wait(self.interval)

    def start(self) -> None:
        """启动后台监控线程。"""
        psutil.cpu_percent(interval=0.1)  # 预热
        self._start_ram_gb = psutil.virtual_memory().used / 1024**3
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="SysMonitor")
        self._thread.start()
        print(f"[Monitor] Started. Initial RAM={self._start_ram_gb:.2f}GB")

    def label(self, name: str) -> None:
        """标注当前阶段名称，写入后续快照的 label 字段。"""
        self._current_label = name
        print(f"[Monitor] Phase: {name}")

    def stop(self) -> MonitorSummary:
        """停止监控，返回汇总统计。"""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=10)

        with self._lock:
            snaps = list(self._snapshots)

        if not snaps:
            return MonitorSummary(gpu_name=_GPU_NAME, gpu_total_mb=_GPU_TOTAL)

        cpus  = [s.cpu_pct      for s in snaps]
        rams  = [s.ram_gb       for s in snaps]
        vrams = [s.vram_mb      for s in snaps]
        disks = [s.disk_write_mb for s in snaps]

        summary = MonitorSummary(
            gpu_name      = _GPU_NAME,
            gpu_total_mb  = _GPU_TOTAL,
            cpu_peak_pct  = max(cpus),
            cpu_avg_pct   = sum(cpus) / len(cpus),
            ram_peak_gb   = max(rams),
            ram_avg_gb    = sum(rams) / len(rams),
            ram_delta_gb  = max(rams) - self._start_ram_gb,
            vram_peak_mb  = max(vrams),
            vram_avg_mb   = sum(vrams) / len(vrams),
            disk_peak_mbs = max(disks),
            disk_avg_mbs  = sum(disks) / len(disks),
            duration_s    = snaps[-1].ts - snaps[0].ts if len(snaps) > 1 else 0,
            n_snapshots   = len(snaps),
            snapshots     = [asdict(s) for s in snaps],
        )
        return summary

    def save_report(self, path: str) -> None:
        """将所有快照和汇总数据保存为 JSON。"""
        with self._lock:
            snaps = list(self._snapshots)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "gpu_name":     _GPU_NAME,
            "gpu_total_mb": _GPU_TOTAL,
            "n_snapshots":  len(snaps),
            "snapshots":    [asdict(s) for s in snaps],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Monitor] Report saved: {path} ({len(snaps)} snapshots)")

    def print_live(self) -> None:
        """打印最新一条快照（用于调试）。"""
        with self._lock:
            if not self._snapshots:
                print("[Monitor] No snapshots yet")
                return
            s = self._snapshots[-1]
        print(f"[Monitor] CPU={s.cpu_pct:.1f}% RAM={s.ram_gb:.2f}GB "
              f"VRAM={s.vram_mb:.0f}MB Disk_W={s.disk_write_mb:.1f}MB/s "
              f"label={s.label!r}")


if __name__ == "__main__":
    # 快速自测
    mon = SystemMonitor(interval=2)
    mon.start()
    for i in range(3):
        time.sleep(2)
        mon.label(f"phase_{i}")
        mon.print_live()
    summary = mon.stop()
    print(f"\nSummary: CPU_peak={summary.cpu_peak_pct:.1f}% "
          f"RAM_peak={summary.ram_peak_gb:.2f}GB "
          f"VRAM_peak={summary.vram_peak_mb:.0f}MB "
          f"duration={summary.duration_s:.1f}s")
