# Repository governance

Status: current repository policy
Scope: source-tree governance, documentation, data admission and review configuration
Source of truth: `quality/`, `scripts/ci/` and the repository tree

This page describes the source-tree contracts that keep the monorepo
reviewable. It is current policy, not a production release record.

## Data and generated outputs

Large local run datasets are environment state and must stay outside Git.
Their machine-specific size is not a repository fact. `data-assets-manifest.json` is the source-tree admission policy:
tracked data must match a reviewed class, large assets require an explicit
owner/provenance/ceiling entry, and runtime/analysis/historical/proxy/Milvus
trees are forbidden when tracked. Use a small fixture or provenance-only
record for tests; do not commit generated checkpoints, database exports,
logs, model caches or local vector stores.

## Scripts

`scripts/manifest.json` classifies existing paths by intent and side effects.
It is an index, not a rename plan: old entrypoints remain valid. Before adding
a script, classify it and state whether it can write files/database, call
external services, or control a process. The CI category must remain
read-only. Operational execution still requires its runbook and checkpoint.

## Runtime paths

The repository still supports current service defaults. The compatibility-only
path policy records a future `GLOBEMIND_RUNTIME_ROOT` migration for logs/state;
it is inactive and does not alter supervisors, services or pipelines. Any
activation requires owner approval, checkpoint/replay proof, migration
rehearsal and a rollback-aware maintenance step.

## Documents and metadata

`docs/README.md` is the current navigation entrypoint. Current policy and
architecture documents should have a clear status/scope statement near the
top. Dated handoffs, benchmarks, progress notes and old workflows remain
historical evidence and are indexed under Archive; do not batch-rewrite old
claims or silently turn archive commands into current instructions. When
updating a historical document, preserve its date and add a correction note or
new current document instead.

## Ownership and legal review

`.github/CODEOWNERS`, issue forms and Dependabot keep review routing explicit.
`LICENSE_DECISION.md` records the unresolved license choice; no license text
is implied until the owner approves one.
