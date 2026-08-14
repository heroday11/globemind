# GlobeMind Runtime Service Catalog

This runbook is the human-readable companion to `ops/runtime/services.json`.
The manifest is the machine source of truth. This file records operational
limits and does not grant lifecycle authority. An `observe-only` entry must not
be started, stopped, restarted, or signaled through generic runtime control.

## Contract states

- `durable` checkpoint evidence means the manifest points at authoritative
  state. It does not prove that a generic controller may take over the process.
- `progress-only` means state is useful for observation but is insufficient for
  restart or replay authorization.
- `not-evidenced` records a known management gap instead of inferring support.
- `verified` replay assurance requires a referenced test selector that is
  checked for drift by the read-only catalog command.
- `takeover_ready=false` is authoritative until a reviewed maintenance window,
  rollback drill, replay proof, strong identity, and lifecycle authorization
  are all recorded.

## Entries

### web

Owner: platform. Observe through strong PID identity, the loopback listener,
and both liveness and readiness endpoints. Application deployment and rollback
remain governed by the release runbook, outside generic runtime control.

### tunnel

Owner: platform. Observe through strong PID identity and the identity-bound
local readiness endpoint. The historical v092 filenames remain canonical
runtime records and do not indicate the deployed application version.

### vllm

Owner: ml-platform. Local HTTP health proves serving reachability only; GPU and
loaded-model correctness remain unverified external dependencies. Lifecycle
control remains observe-only.

### proxy_pool

Owner: data-platform. Member PID files provide weak identity and provider
reachability is unverified. Open local member ports are not lifecycle or
upstream-health authorization.

### wave1_extractor

Owner: data-ingestion. The merged progress document is authoritative progress
evidence and resume behavior has focused test evidence. The active legacy
process lacks strong start-ticks metadata, so generic takeover is forbidden.

### wave1_loader

Owner: data-ingestion. The authenticated control status, heartbeat, database
checkpoint, and checkpoint mirror form durable observational evidence. Replay
and crash-gap behavior have focused test evidence. Despite this, generic
lifecycle authorization is absent and `takeover_ready` remains false.

### l1_prep

Owner: news-intelligence. Only process and log freshness are currently
cataloged. No durable checkpoint or replay proof is evidenced.

### l1_extract

Owner: news-intelligence. The stream additionally depends on vLLM, but still
has only process and log freshness evidence. No durable checkpoint or replay
proof is evidenced.

### daily_ingest

Owner: data-ingestion. The newest load state is optional progress evidence,
not an authoritative restart checkpoint. The active legacy loop has weak PID
identity; its new controller has not completed a checkpointed takeover.

### quality_labels

Owner: data-quality. Recent log freshness supports observation only. The
active legacy loop has weak PID identity and no durable heartbeat/checkpoint or
replay evidence, so controller adoption remains unauthorized.

### ground_refresh

Owner: news-intelligence. Current evidence is process plus log freshness. The
direct loop has no durable checkpoint, replay proof, or lifecycle authority.

### ground_images

Owner: news-intelligence. Recent business-run output is a non-authoritative
warning signal. The direct loop has no durable checkpoint, replay proof, or
lifecycle authority.
