# GlobeMind handoff for a new Codex session

Generated at: 2026-07-10 21:48 Asia/Shanghai

This document records the current operating state, progress, risks, and the
next-session plan. It is intentionally operational, not a product summary.

## 0. Read this first

- The broad V0.9.1 to V1.0 goal is not complete. It has been marked
  `blocked` to stop automatic background continuation after repeated session
  interruptions and duplicate worker interference.
- Do not start another agent team or another migration audit until the current
  Wave1 audit state below is resolved.
- Current production web/API is still serving V0.9.2 and is healthy.
- The legacy Wave1 loader PID `51304` is currently frozen (`T`) on purpose
  while the authoritative V0.9.3 audit is running.
- If the new session will not continue immediately, abort the running audit via
  the supervisor and verify the legacy loader resumes before leaving.

## 1. Current production state

Public readiness endpoint at last check:

```text
https://globemind.top/api/health/ready
status: healthy
ready: true
service: globemind-api
release version: 0.9.2
build_id: 0.9.2-20260710T065201Z
git_sha: 4c1cdb50064eb119e4189d403154d0611c33b14e
database: up, latency 37.4 ms
assistant_scheduler: healthy, running, leader_pid 48016
```

The current public deployment has not been cut over to V0.9.3. No V0.9.3
checkpoint seed or production cutover has been completed.

## 2. Current Wave1 loader and audit state

Legacy loader identity:

```text
pid: 51304
expected_start_ticks: 618810016
boot_id: 66c53ee9-7e17-4b1c-88a3-ddc9734158fa
exe: /root/data/globemind/.env_torch/bin/python3.10
cwd: /root/data/globemind
current_state_at_2026-07-10T21:48:49+08:00: T
```

Authoritative migration directory:

```text
/root/data/runtime/globemind/migrations/wave1-v093-authoritative4-20260710T132826Z
```

Frozen state captured for this authoritative run:

```text
state_sha256: 5942d2af3976081bfbd40373060b29e49c8e0064b31d12415376dbd55bcfe958
input_size_at_freeze: 9,773,267,546
offset: 9,773,156,601
seen: 2,210,528
inserted: 2,173,242
skipped: 37,286
quality_skipped: 20,669
database_backend: idle, no transaction
```

Detached audit process at last check:

```text
supervisor_pid: 88848
audit_pid: 88851
supervisor_ppid: 1
supervisor_sid: 88848
audit_state: R
audit_elapsed: 19m53s
audit_cpu: about 95%
audit_rchar_at_2026-07-10T21:48:29+08:00: 5,491,774,815
live_input_size_at_2026-07-10T21:48:29+08:00: 9,785,625,999
legacy_state: T
```

Interpretation: audit4 was about 56% through the live file read at the last
check. The live input file is still growing, while the migration freeze point
is fixed. Do not seed a checkpoint or cut over until the audit result is
reviewed against the frozen state and exact closure requirements.

Previous migration snapshots are stale and must not be used for seeding or
cutover:

```text
wave1-v093-authoritative-*
wave1-v093-authoritative2-*
wave1-v093-authoritative3-20260710T132403Z
```

Only `wave1-v093-authoritative4-20260710T132826Z` is current.

## 3. Commands for the next session

First status check:

```bash
cd /root/data/globemind
MIG=/root/data/runtime/globemind/migrations/wave1-v093-authoritative4-20260710T132826Z

date -Is
cat "$MIG/audit.heartbeat" 2>/dev/null || true
cat "$MIG/audit.exit" 2>/dev/null || true
ps -o pid=,ppid=,pgid=,sid=,stat=,etime=,%cpu=,rss=,comm= -p 88848,88851 || true
awk '{print "legacy_state=" $3, "start_ticks=" $22}' /proc/51304/stat
curl -fsS --max-time 10 https://globemind.top/api/health/ready | head -c 2000
```

If the audit is still running, keep monitoring it. Do not start a second audit.

If the audit completes successfully, `audit.exit` should contain
`"exit_code":0`. The current supervisor is designed to leave the legacy loader
frozen on success (`legacy_resumed:false`) so that the next step can safely
seed/check cut over from the exact audited state.

Before seeding or cutover, inspect:

```bash
MIG=/root/data/runtime/globemind/migrations/wave1-v093-authoritative4-20260710T132826Z
cat "$MIG/audit.exit"
sha256sum "$MIG/legacy-audit.json"
head -c 4000 "$MIG/legacy-audit.json"
```

If you need to abort and resume the legacy loader instead of continuing V0.9.3:

```bash
MIG=/root/data/runtime/globemind/migrations/wave1-v093-authoritative4-20260710T132826Z
kill -TERM "$(cat "$MIG/audit-supervisor.pid")"
sleep 5
cat "$MIG/audit.exit" 2>/dev/null || true

pid=51304
expected_start=618810016
if [ -r /proc/$pid/stat ] && [ "$(awk '{print $22}' /proc/$pid/stat)" = "$expected_start" ]; then
  kill -CONT "$pid"
fi
awk '{print "legacy_state=" $3, "start_ticks=" $22}' /proc/51304/stat
curl -fsS --max-time 10 https://globemind.top/api/health/ready | head -c 2000
```

Never print the legacy loader command line or environment because its argv
contains a plaintext database password.

## 4. Why the previous session kept interrupting

Observed causes:

- The active long-running goal consumed about `2,800,957` counted tokens and
  `17,190` seconds before it was blocked.
- There were many stored rollout records and repeated tool/subagent activity.
  This is stored session state, not all active compute, but it explains the
  quota warning.
- A duplicate SSH/Codex tree was spawning work outside the current root
  session and interfered with audit attempts. That duplicate SSH tree was
  terminated. Other old low-CPU Codex terminals were not killed to avoid
  disrupting unrelated work.
- The client warning about `gpt-5.6-sol` versus `gpt-5.6-terra` is a session
  model mismatch warning from the Codex client. The local config observed in
  this workspace was `model = "gpt-5.5"` with `xhigh` reasoning. The current
  agent cannot safely switch the already-running session model.

Recommendation for the new session:

- Start exactly one Codex session.
- Pin the model in the client/UI before continuing, or start with the desired
  CLI model explicitly.
- Do not resume the old broad goal automatically. Continue manually from this
  handoff until the audit/cutover state is resolved.

## 5. Work completed so far

Confirmed completed or materially advanced:

- V0.9.2 is deployed and healthy.
- Release boundary rules were added in `AGENTS.md`.
- Runtime safety discipline was established: do not import from deployed
  release dirs; diagnostics must use `PYTHONDONTWRITEBYTECODE=1` and `-B`.
- Production readiness endpoint was verified multiple times during the
  interruption investigation.
- The legacy Wave1 loader PID identity was verified before every signal:
  PID, start ticks, boot ID, executable, working directory, session/process
  group.
- Stale audit attempts were not used for migration state.
- A detached audit supervisor was launched for the current authoritative
  audit4 run, with heartbeat and exit files under the migration directory.

Known incomplete:

- V0.9.3 checkpoint seed and cutover are not done.
- V0.9.3 production verifier/cutover validation is not done.
- Unified service/pipeline management is designed but not fully implemented.
- Frontend/backend modularization toward high cohesion and low coupling is not
  finished.
- V1.0 acceptance gates are not complete.

## 6. Planned route to V1.0

V0.9.3 - Wave1 loader transition and runtime safety:

- Finish audit4.
- If audit output proves exact closure, seed the managed checkpoint and perform
  the safe cutover.
- If audit does not prove exact closure, abort, resume the legacy loader, and
  create a new plan before touching production state.
- Add or verify tests around the migration command, PID identity checks,
  checkpoint semantics, rollback path, and release-boundary bytecode hygiene.

V0.9.4 - unified service and pipeline control:

- Inventory all always-on services and long-running pipelines.
- Normalize PID files, ownership metadata, health checks, logs, and start/stop
  controls under one runtime-control surface.
- Add operator commands for status, pause, resume, restart, and drain.
- Make pipelines idempotent and checkpoint-aware by default.

V0.9.5 - frontend modularization:

- Split frontend by page/feature modules.
- Move API clients, stores, route guards, and page-local components into clear
  boundaries.
- Remove cross-page implicit dependencies and global side effects.
- Add route/page smoke tests for the core public flows.

V0.9.6 - backend modularization:

- Split API routes, services, repositories, background jobs, and domain logic
  into feature-aligned packages.
- Establish stable contracts between pages/features and backend APIs.
- Reduce shared helper sprawl; keep shared code limited to genuine platform
  concerns.

V0.9.7 - observability and release gates:

- Add structured health/readiness for all managed services and pipelines.
- Add dashboards or operator views for pipeline progress and failures.
- Enforce release verification, dependency/runtime lock checks, and rollback
  rehearsal.

V1.0 - production management baseline:

- One source of truth for services, pipelines, secrets, release state, and
  operator actions.
- Page/feature modules are independently understandable and upgradeable.
- New modules can be shipped through a predictable release path with tests,
  health checks, and rollback instructions.
- No known high-severity production safety issues remain open.

## 7. Worktree note

The worktree is dirty with many modified and untracked files across backend,
frontend, deploy scripts, docs, tests, runtime control, and data artifacts.
Preserve unrelated changes. Do not run destructive git commands such as
`git reset --hard` or `git checkout --` unless the user explicitly asks.

Run this in the new session for the exact current file list:

```bash
cd /root/data/globemind
git status --short
```
