# GlobeMind Runtime Control

`ops/runtime/services.json` is the authoritative management catalog for
long-running services and pipelines. It records ownership, controllers,
dependencies, health policy, checkpoint and replay assurance, secret-file
references, lifecycle authorization, and runbook ownership in one strict
schema-v2 document. Every production entry remains `observe-only` and has no enabled
`controller.lifecycle` capability, so the production CLI can list, inspect,
and diagnose runtime state but cannot dispatch a controller.

## Read-only commands

Run from the repository root:

```bash
python scripts/globemind_runtime.py list
python scripts/globemind_runtime.py catalog
python scripts/globemind_runtime.py status
python scripts/globemind_runtime.py doctor
```

Selecting a service includes its transitive manifest dependencies. A required
dependency problem is reported with the dependent service; optional
dependencies remain visible but do not turn the dependent service unhealthy.
All diagnostic output is redacted and must not include process environment
values or unredacted sensitive command-line arguments.

`catalog` is deliberately separate from `status`. It validates the manifest,
selects the dependency closure, and performs bounded read-only drift checks on
controller files, runbook sections, and declared replay-test selectors. It
does not inspect processes, open network connections, read secret files, or
dispatch a controller. JSON output sets `process_inspection=false` and omits
secret paths and replay source paths.

## Management catalog contract

Every service entry must contain these management facts in addition to the
existing owner, controller, dependency, and observation declarations:

| Field | Contract |
| --- | --- |
| `health_policy` | References the exact PID, port, active health, log, state, or output entries used as management signals. Every required active health check must be included. |
| `checkpoint` | Classifies state as `durable`, `progress-only`, `not-evidenced`, or `not-applicable`; state references are bounds checked. |
| `replay` | Records mode and assurance. `verified` or `documented` assurance must cite bounded trusted source selectors that the catalog drift check can find. |
| `secret_refs` | Names policy-file indexes only. It must cover every `secret_policy.files` entry exactly once and never duplicates secret material. |
| `lifecycle_authorization` | Separates lifecycle permission from controller availability. Authorization requires managed adoption, the fixed operation set, a change request, a maintenance window, and owner plus platform approval. |
| `runbook` | Points to a trusted UTF-8 file and a service-specific Markdown heading checked for drift. |

The loader rejects missing fields, unknown fields, invalid cross-references,
incomplete secret references, non-pipeline checkpoint claims, replay assurance
without evidence, and any disagreement between authorization and controller
adoption. `checkpoint.takeover_ready=true` additionally requires a durable
checkpoint, verified replay assurance, and explicit lifecycle authorization.
It is not inferred from a running PID or from the existence of controller
source code.

The human companion is
`docs/operations/RUNTIME_SERVICE_CATALOG.md`. Its service headings are stable
catalog identifiers, not lifecycle commands.

### Current takeover blockers

No production entry is authorized for generic lifecycle takeover. The catalog
reports the gaps rather than treating them as implicit support:

- `web` and `tunnel` have strong runtime identity and active health but remain
  governed by release-specific procedures.
- `wave1_loader` has durable checkpoint and verified replay evidence, but its
  generic lifecycle authorization is absent.
- `wave1_extractor` and `daily_ingest` expose progress evidence only; this is
  insufficient for restart authorization.
- `l1_prep`, `l1_extract`, `quality_labels`, `ground_refresh`, and
  `ground_images` have no durable checkpoint or verified replay contract.
- `proxy_pool`, the Wave1 extractor, L1 streams, daily ingest, quality labels,
  and Ground loops still lack strong PID plus start-ticks identity in the
  cataloged production runtime.

## Health truth hierarchy

Use these sources in order. A log line alone is not proof that work is making
progress.

| Runtime | Authoritative signal | Supporting signal |
| --- | --- | --- |
| Production web | PID plus process start ticks, TCP port, liveness and readiness | Canonical regular log |
| Cloudflare tunnel | PID plus process start ticks and `127.0.0.1:20242/ready` | Active `tunnel-v092.log` |
| Wave1 extractor | `wave1_articles_merged_progress.json` timestamp and counters | Supervisor state and buffered log |
| Wave1 loader | Managed PID plus JSON start-ticks metadata, fresh heartbeat, checkpoint, and authenticated Unix `status` | Managed loader log |
| News quality labels | Managed PID plus start ticks, process group, session, exact loop argv, executable, and cwd | Recent completed-pass log |
| Ground image backfill | Loop PID, plus recent business-run output | Loop log; stale output is a warning |

`stopped` and `failed` are terminal control states, not successful completion.
Only an explicit success value such as `completed` can satisfy a completion
condition.

The production Cloudflare connector still owns the historically named
`cloudflared-v092.pid`, `cloudflared-v092.pid.meta`, and `tunnel-v092.log`.
Those filenames identify the canonical controller records, not the deployed
application version.
`deploy/start_cloudflared.sh` explicitly binds metrics to `127.0.0.1:20242`
and atomically records PID plus `/proc` start ticks. Do not
copy a bare PID file between connectors as proof of identity.

The production web canonical log was historically a link to a candidate log.
On a verified web start, after the old instance is stopped and the port is
free, `deploy/start_web_prod.sh` atomically replaces only such a canonical link
with a mode `0640` regular file. The historical target is retained. The
normalization is skipped while a verified instance is running.

## Managed Wave1 loader evidence

The active Wave1 loader is owned by `deploy/wave1_loader_ctl.sh`. Runtime
identity and liveness are read from `/root/data/runtime/globemind/wave1_loader/`:

- `wave1_loader.pid` identifies the managed process, but is never sufficient
  on its own.
- `wave1_loader.pid.meta` is schema-v2 JSON. Its `identity.pid` and immutable
  `identity.start_ticks` must agree with both the PID file and `/proc`.
- `wave1_loader.pid.heartbeat` must have a current `heartbeat_at`; its status,
  checkpoint key, offset, and counters are diagnostic progress evidence.
- `wave1_loader.pid.sock` must retain the recorded inode, owner, and mode
  `0600`. The inspector verifies Unix peer credentials before sending the
  fixed, non-mutating `status` request and accepts only a matching running
  response.
- `news_loader_state.json` remains the authoritative durable checkpoint mirror.

Any missing, stale, replaced, mismatched, oversized, or malformed identity,
heartbeat, socket, peer, or response evidence fails closed. The inventory does
not execute the controller and cannot request `stop` through the socket.

The retired repository-local `logs/wave1_loader.pid` and
`logs/wave1_loader.log` are not active health sources.

The daily ingest and news quality loops remain PID-only until their respective
checkpointed maintenance windows. Their new controllers can create PID plus
start-ticks metadata for a newly launched isolated instance, but controller
code alone is not takeover evidence. Neither active legacy loop may be stopped
through the new controller. After a completed takeover, the token metadata must
agree on PID and start ticks; the controller additionally requires the recorded
process to be its own process-group and session leader with the exact loop argv,
executable, and working directory.

## Read-only dependency probes

V0.11 dependency probes are top-level manifest declarations. They are not
commands and have no lifecycle capability. The schema accepts only literal
loopback IP addresses, fixed numeric ports, canonical local paths, GET or TCP,
a timeout no greater than five seconds, and an explicit evidence TTL. There is
no URL, hostname, shell, argv, header, credential, or request-body field. The
HTTP client disables proxies and redirects, bounds a readiness response to 64
KiB, and closes every response. Invalid, oversized, timed-out, redirected, or
unreachable evidence fails closed.

The supported probe forms have deliberately narrow meanings:

| Probe type | Success means | Can verify an external dependency |
| --- | --- | --- |
| `postgres-tcp` | A fresh loopback PostgreSQL TCP listener accepted a connection | No; reports `local-up` only |
| `postgres-application-readiness` | The identity-bound GlobeMind readiness response says `ready=true` and `checks.database.status=up` | Yes, for `postgres-*` |
| `cloudflare-tunnel-ready` | The identity-bound connector `/ready` endpoint reports ready | Yes, for `cloudflare-edge` |
| `model-http-health` | The local model server `/health` endpoint reports HTTP 200 | No; reports `local-up` only |

HTTP probes must be bound to the same service that consumes their evidence,
and their target must match one of that service's declared ports. PostgreSQL
application and Cloudflare evidence is promoted to `external-verified` only
when the inspected PID has strong PID plus process-start-ticks identity and
owns the exact listening socket before and after the probe. Listener ownership
is cross-checked between `/proc/net/tcp*` and `/proc/<pid>/fd`; a passing
endpoint without that proof remains `local-up`. Every result records
`checked_at`, `fresh_until`, the fixed target, and, when available, the bound
PID incarnation. This prevents an old or unrelated response from being treated
as current dependency proof.

`business-stalled` is distinct from transport failure: it means the local
endpoint answered, but the expected readiness status or PostgreSQL business
assertion failed. `unreachable` means the fixed local target did not answer.
Both fail a required dependency; `local-up` and `unverified` keep it degraded
with a concrete reason.

The production inventory enables only three safe local observations:

- web application readiness for `postgres-news`;
- Cloudflare connector readiness for `cloudflare-edge`; and
- local vLLM health for the declared model dependency.

The vLLM result intentionally cannot prove GPU or loaded-model business
correctness, so it remains degraded until stronger evidence exists. The proxy
pool also remains explicitly `unverified`: its member PID files do not carry
process start ticks, and open local TCP listeners cannot establish either
member identity or upstream provider reachability. No probe in this release
starts, stops, restarts, signals, authenticates to, or mutates a production
service or pipeline.

## Known runtime debt

The following runtime debt remains recorded rather than being cleaned up
automatically:

- Several remaining legacy pipeline controllers store only a PID. They are vulnerable to
  PID reuse and cannot safely support generic stop actions. Migrate each one to
  atomic PID plus start-ticks metadata during its own checkpointed maintenance
  window.
- Historical shell supervisors have left zombie children and adopted orphan
  shells in the container process namespace. A zombie cannot be fixed by
  signaling the zombie PID; its parent or PID 1 must reap it. Reaper changes
  require a separate container maintenance window and process-tree audit.
- Direct loops, PM2 entries, and hand-managed launchers overlap for some old
  workloads. Do not infer ownership from command-name matching or use broad
  `pkill` cleanup. First identify the controller, PID file, process start ticks,
  checkpoint, and unique process group.
- Retired Cloudflare handoff PID and log files are retained as audit evidence.
  They are not active controllers and should be archived only under a defined
  retention policy.

## Lifecycle adoption gate

Lifecycle authority is a narrow service capability, not a global enable flag.
Missing lifecycle configuration, `enabled=false`, `observe-only`, an unknown
service, or more than one requested service all fail before inspection or
controller execution. `web` and `tunnel` deliberately remain observation-only.

An adopted manifest entry must declare all four exact two-token argv arrays:
the attested controller absolute path followed by one of `status`, `start`,
`stop`, or `restart`. It must also provide:

- caller-owned, non-group/world-writable controller artifacts with pinned
  SHA-256 digests;
- one single-process PID contract with start-ticks metadata and at least one
  required health check;
- a supported secret policy with required secret-file permission checks;
- SHA-256-pinned checkpoint and rollback procedures;
- a caller-owned, non-group/world-writable audit directory; and
- complete required dependency health with reverse-dependent protection for
  stop and restart.

The CLI defaults lifecycle requests to plan-only. A future adopted service
would require both `--apply` and a reviewed `--request-id` before dispatch.
The subprocess receives a fixed minimal environment and `shell=False`; caller
overrides such as `PYTHON_BIN`, `MANAGED_LOOP_PROC_ROOT`, `PYTHONPATH`, and
`LD_PRELOAD` are not inherited. Controller output is discarded rather than
stored. Each plan, dispatch start, completion, and eligible preflight denial is
written as a redacted JSON event through a mode-`0400` temporary file, `fsync`,
and atomic rename. This is crash-safe local evidence, not a tamper-proof remote
audit log.

Static adoption is still insufficient. Before enabling a production entry, a
checkpointed maintenance window must prove drain/checkpoint behavior, exact
old-instance death, post-start identity-bound health, rollback behavior, and
an isolated stop/start drill. Long-running Wave1 processes additionally need
replay proof.

### Quality labels assessment

`deploy/news_quality_labels_ctl.sh` and the shared managed-loop library are a
useful controller fixture: their source contract verifies PID, start ticks,
PGID/SID, exact argv, executable, working directory, and a controller lock.
That does not make the production service adoptable. Its inventory still lacks
PID metadata and a required health/checkpoint signal. The database consumer
contract also records `maintenance_window=not_scheduled`, an unassigned target
role, runtime DDL that has not been separated, plaintext-compatible credential
sources, and unverified TLS. The loop can continue after a batch error, so PID
liveness alone is not business health.

Therefore `quality_labels` cannot enter an automated maintenance window in
this batch and must remain `observe-only`. Separate runtime DDL, assign and
verify a least-privilege role, harden credential/TLS transport, add a durable
heartbeat/checkpoint plus rollback drill, then submit a reviewed manifest
change and maintenance request for a new assessment.
