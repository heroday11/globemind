from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = PROJECT_ROOT / "deploy"
HELPER_NAME = "managed_loop_ctl_lib.sh"

CONTROLLERS = (
    ("daily_news_ingest_ctl.sh", "daily_news_ingest_loop.sh", "daily_news_ingest"),
    ("news_quality_labels_ctl.sh", "news_quality_labels_loop.sh", "news_quality_labels"),
)

RUNNING_LOOP = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" >"$GLOBEMIND_HOME/candidate-observed.pid"
trap 'exit 0' TERM INT
while true; do
  sleep 0.1 &
  wait "$!" || true
done
"""


def _sandbox(
    tmp_path: Path,
    controller_name: str,
    loop_name: str,
    *,
    loop_body: str = RUNNING_LOOP,
    stability: str = "0.1",
) -> tuple[Path, Path, Path, dict[str, str]]:
    root = tmp_path / "globemind"
    deploy = root / "deploy"
    logs = root / "logs"
    deploy.mkdir(parents=True)
    logs.mkdir()
    controller = deploy / controller_name
    helper = deploy / HELPER_NAME
    loop = deploy / loop_name
    shutil.copy2(DEPLOY_ROOT / controller_name, controller)
    shutil.copy2(DEPLOY_ROOT / HELPER_NAME, helper)
    loop.write_text(loop_body, encoding="utf-8")
    controller.chmod(0o755)
    helper.chmod(0o755)
    loop.chmod(0o755)
    env = {
        **os.environ,
        "GLOBEMIND_HOME": str(root),
        "MANAGED_LOOP_PROC_ROOT": "/proc",
        "MANAGED_LOOP_START_TIMEOUT_SEC": "2",
        "MANAGED_LOOP_START_STABILITY_SEC": stability,
        "MANAGED_LOOP_STOP_TIMEOUT_SEC": "2",
        "MANAGED_LOOP_KILL_TIMEOUT_SEC": "1",
        "MANAGED_LOOP_LOCK_TIMEOUT_SEC": "1",
    }
    return root, controller, loop, env


def _run(controller: Path, env: dict[str, str], operation: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(controller), operation],
        check=False,
        cwd=controller.parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=8,
    )


def _identity(pid: int) -> tuple[int, int, int, str]:
    stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").strip()
    tail = stat_line.rsplit(") ", 1)[1].split()
    return int(tail[19]), int(tail[2]), int(tail[3]), tail[0]


def _alive(pid: int) -> bool:
    try:
        _ticks, _pgid, _sid, state = _identity(pid)
    except (FileNotFoundError, IndexError, ValueError):
        return False
    return state not in {"Z", "X", "x"}


def _wait_dead(pid: int, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _terminate_fixture_process(pid: int, expected_script: Path) -> None:
    if not _alive(pid):
        return
    raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    assert str(expected_script).encode() in raw
    assert os.getpgid(pid) == pid
    os.killpg(pid, signal.SIGKILL)
    assert _wait_dead(pid)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_controller_writes_atomic_strong_identity_and_stops_verified_group(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, _loop, env = _sandbox(tmp_path, controller_name, loop_name)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")

    started = _run(controller, env, "start")
    assert started.returncode == 0, started.stderr
    pid = int(pid_file.read_text(encoding="utf-8"))
    ticks, pgid, sid, _state = _identity(pid)
    assert meta_file.read_text(encoding="utf-8").split() == [
        str(pid),
        str(ticks),
        str(pgid),
        str(sid),
        service_id,
    ]
    assert pgid == pid
    assert sid == pid
    assert pid_file.stat().st_mode & 0o777 == 0o640
    assert meta_file.stat().st_mode & 0o777 == 0o640
    assert not list(pid_file.parent.glob(f".{service_id}.*.*"))

    status = _run(controller, env, "status")
    assert status.returncode == 0
    assert f"running pid={pid}" in status.stdout

    stopped = _run(controller, env, "stop")
    assert stopped.returncode == 0, stopped.stderr
    assert _wait_dead(pid)
    assert not pid_file.exists()
    assert not meta_file.exists()


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_pid_only_legacy_instance_is_observed_but_never_signaled(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    process = subprocess.Popen(
        [str(loop)],
        cwd=root,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        status = _run(controller, env, "status")
        assert status.returncode == 0
        assert "unverified live metadata" in status.stdout

        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 3
        assert "refusing to signal" in stopped.stderr
        assert process.poll() is None
        assert pid_file.exists()
    finally:
        _terminate_fixture_process(process.pid, loop)
        process.wait(timeout=3)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_forged_pid_and_meta_never_signal_unrelated_process(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, _loop, env = _sandbox(tmp_path, controller_name, loop_name)
    unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    try:
        ticks, pgid, sid, _state = _identity(unrelated.pid)
        pid_file.write_text(f"{unrelated.pid}\n", encoding="utf-8")
        meta_file.write_text(
            f"{unrelated.pid} {ticks} {pgid} {sid} {service_id}\n",
            encoding="utf-8",
        )

        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 3
        assert "refusing to signal" in stopped.stderr
        assert unrelated.poll() is None
        assert pid_file.exists()
        assert meta_file.exists()
    finally:
        os.killpg(unrelated.pid, signal.SIGKILL)
        unrelated.wait(timeout=3)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_loop_path_as_an_extra_argument_never_satisfies_exact_argv_identity(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    unrelated = subprocess.Popen(
        [
            "bash",
            "-c",
            "trap 'exit 0' TERM INT; while true; do sleep 0.1 & wait \"$!\" || true; done",
            str(loop),
        ],
        cwd=root,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    try:
        ticks, pgid, sid, _state = _identity(unrelated.pid)
        assert pgid == unrelated.pid
        assert sid == unrelated.pid
        assert str(loop).encode() in Path(f"/proc/{unrelated.pid}/cmdline").read_bytes().split(
            b"\0"
        )
        pid_file.write_text(f"{unrelated.pid}\n", encoding="utf-8")
        meta_file.write_text(
            f"{unrelated.pid} {ticks} {pgid} {sid} {service_id}\n",
            encoding="utf-8",
        )

        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 3
        assert "refusing to signal" in stopped.stderr
        assert unrelated.poll() is None
        assert pid_file.exists()
        assert meta_file.exists()
    finally:
        _terminate_fixture_process(unrelated.pid, loop)
        unrelated.wait(timeout=3)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_tampered_meta_for_a_real_managed_process_blocks_stop(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    started = _run(controller, env, "start")
    assert started.returncode == 0, started.stderr
    pid = int(pid_file.read_text(encoding="utf-8"))
    values = meta_file.read_text(encoding="utf-8").split()
    values[-1] = "forged_service"
    meta_file.write_text(" ".join(values) + "\n", encoding="utf-8")
    try:
        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 3
        assert "refusing to signal" in stopped.stderr
        assert _alive(pid)
        assert pid_file.exists()
        assert meta_file.exists()
    finally:
        _terminate_fixture_process(pid, loop)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_tampered_ticks_on_the_expected_live_loop_block_duplicate_start(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    started = _run(controller, env, "start")
    assert started.returncode == 0, started.stderr
    pid = int(pid_file.read_text(encoding="utf-8"))
    values = meta_file.read_text(encoding="utf-8").split()
    values[1] = str(int(values[1]) + 1)
    meta_file.write_text(" ".join(values) + "\n", encoding="utf-8")
    try:
        second_start = _run(controller, env, "start")
        assert second_start.returncode == 3
        assert "refusing automatic takeover" in second_start.stderr
        assert int(pid_file.read_text(encoding="utf-8")) == pid
        assert _alive(pid)
        assert int((root / "candidate-observed.pid").read_text(encoding="utf-8")) == pid
    finally:
        _terminate_fixture_process(pid, loop)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_expected_loop_without_its_own_process_group_is_never_signaled(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    process = subprocess.Popen(
        [str(loop)],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    try:
        ticks, pgid, sid, _state = _identity(process.pid)
        assert pgid != process.pid or sid != process.pid
        pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
        meta_file.write_text(
            f"{process.pid} {ticks} {pgid} {sid} {service_id}\n",
            encoding="utf-8",
        )

        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 3
        assert "refusing to signal" in stopped.stderr
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=3)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_reused_pid_metadata_is_cleared_without_signaling_new_process(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, _loop, env = _sandbox(tmp_path, controller_name, loop_name)
    unrelated = subprocess.Popen(["sleep", "30"], start_new_session=True)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    try:
        ticks, pgid, sid, _state = _identity(unrelated.pid)
        pid_file.write_text(f"{unrelated.pid}\n", encoding="utf-8")
        meta_file.write_text(
            f"{unrelated.pid} {ticks + 1} {pgid} {sid} {service_id}\n",
            encoding="utf-8",
        )

        stopped = _run(controller, env, "stop")
        assert stopped.returncode == 0, stopped.stderr
        assert "without a signal" in stopped.stdout
        assert unrelated.poll() is None
        assert not pid_file.exists()
        assert not meta_file.exists()
    finally:
        os.killpg(unrelated.pid, signal.SIGKILL)
        unrelated.wait(timeout=3)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_stale_dead_records_are_replaced_by_a_new_verified_instance(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, loop, env = _sandbox(tmp_path, controller_name, loop_name)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    pid_file.write_text("999999999\n", encoding="utf-8")
    meta_file.write_text(f"999999999 1 999999999 999999999 {service_id}\n", encoding="utf-8")

    started = _run(controller, env, "start")
    assert started.returncode == 0, started.stderr
    assert "clearing stale" in started.stdout
    pid = int(pid_file.read_text(encoding="utf-8"))
    assert pid != 999999999
    try:
        assert _alive(pid)
    finally:
        stopped = _run(controller, env, "stop")
        if stopped.returncode != 0:
            _terminate_fixture_process(pid, loop)
    assert stopped.returncode == 0, stopped.stderr


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_startup_failure_cleans_only_the_bound_candidate_records(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    failing_loop = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" >"$GLOBEMIND_HOME/candidate-observed.pid"
sleep 0.15
exit 7
"""
    root, controller, _loop, env = _sandbox(
        tmp_path,
        controller_name,
        loop_name,
        loop_body=failing_loop,
        stability="0.4",
    )
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")

    started = _run(controller, env, "start")

    assert started.returncode == 1
    candidate = int((root / "candidate-observed.pid").read_text(encoding="utf-8"))
    assert _wait_dead(candidate)
    assert not pid_file.exists()
    assert not meta_file.exists()


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_stop_uses_bounded_term_wait_then_kills_only_reverified_group(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    term_ignoring_loop = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$$" >"$GLOBEMIND_HOME/candidate-observed.pid"
trap '' TERM
while true; do
  sleep 0.1 &
  wait "$!" || true
done
"""
    root, controller, loop, env = _sandbox(
        tmp_path,
        controller_name,
        loop_name,
        loop_body=term_ignoring_loop,
    )
    env["MANAGED_LOOP_STOP_TIMEOUT_SEC"] = "1"
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    started = _run(controller, env, "start")
    assert started.returncode == 0, started.stderr
    pid = int(pid_file.read_text(encoding="utf-8"))
    began = time.monotonic()
    try:
        stopped = _run(controller, env, "stop")
    finally:
        if _alive(pid):
            _terminate_fixture_process(pid, loop)

    assert stopped.returncode == 0, stopped.stderr
    assert time.monotonic() - began < 4
    assert "killed" in stopped.stdout
    assert _wait_dead(pid)


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_controller_lock_prevents_concurrent_lifecycle_start(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    del service_id
    root, controller, _loop, env = _sandbox(tmp_path, controller_name, loop_name)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    lock_file = Path(f"{pid_file}.ctl.lock")
    with lock_file.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = _run(controller, env, "start")

    assert started.returncode == 1
    assert "controller lock is busy" in started.stderr
    assert not pid_file.exists()
    assert not Path(f"{pid_file}.meta").exists()
    assert not (root / "candidate-observed.pid").exists()


def test_unreadable_live_stat_is_not_proof_of_death(tmp_path: Path) -> None:
    root = tmp_path / "globemind"
    logs = root / "logs"
    process_root = root / "proc" / "4242"
    logs.mkdir(parents=True)
    process_root.mkdir(parents=True)
    pid_file = logs / "worker.pid"
    meta_file = Path(f"{pid_file}.meta")
    pid_file.write_text("4242\n", encoding="utf-8")
    meta_file.write_text("4242 77 4242 4242 worker\n", encoding="utf-8")
    stat_file = process_root / "stat"
    stat_file.write_text("malformed but present\n", encoding="utf-8")
    harness = f"""
set +e
MANAGED_LOOP_PID_FILE={pid_file!s}
MANAGED_LOOP_SERVICE_ID=worker
MANAGED_LOOP_PROC_ROOT={root / "proc"!s}
source {DEPLOY_ROOT / HELPER_NAME!s}
managed_loop_identity_is_dead
identity=$?
managed_loop_fresh_identity_is_dead 4242 77
fresh=$?
printf 'identity=%s fresh=%s\n' "$identity" "$fresh"
"""

    present = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert present.returncode == 0
    assert present.stdout.strip() == "identity=1 fresh=1"

    stat_file.unlink()
    absent = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert absent.returncode == 0
    assert absent.stdout.strip() == "identity=0 fresh=0"


@pytest.mark.parametrize(("controller_name", "loop_name", "service_id"), CONTROLLERS)
def test_present_but_unreadable_stat_blocks_start_and_record_cleanup(
    tmp_path: Path, controller_name: str, loop_name: str, service_id: str
) -> None:
    root, controller, _loop, env = _sandbox(tmp_path, controller_name, loop_name)
    fake_proc = root / "fake-proc"
    process_root = fake_proc / "4242"
    process_root.mkdir(parents=True)
    (process_root / "stat").write_text("malformed but present\n", encoding="utf-8")
    env["MANAGED_LOOP_PROC_ROOT"] = str(fake_proc)
    pid_file = root / "logs" / f"{loop_name.removesuffix('.sh')}.pid"
    meta_file = Path(f"{pid_file}.meta")
    pid_file.write_text("4242\n", encoding="utf-8")
    meta_file.write_text(f"4242 77 4242 4242 {service_id}\n", encoding="utf-8")

    started = _run(controller, env, "start")

    assert started.returncode == 3
    assert "refusing automatic takeover" in started.stderr
    assert pid_file.read_text(encoding="utf-8") == "4242\n"
    assert meta_file.exists()
    assert not (root / "candidate-observed.pid").exists()
