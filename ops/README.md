# Operations inventories

Status: current operations inventory policy
Scope: read-only feature, runtime, audit and release catalogs

`ops/` contains versioned, read-only inventories used by quality and release
checks. It is not a lifecycle-control API and does not authorize a service or
pipeline restart.

- `features/registry.json` records feature ownership, public facades, health
  evidence and rollback references.
- `runtime/services.json` records service identity, dependencies, probes,
  checkpoints and runbooks. Its control policy is observe-only.
- `runtime/database-consumers.json` records database consumers and their
  runtime role assumptions.
- `audit/registry.json` records bounded audit evidence and validator inputs.
- `release/content-bundles.json` records explicitly attested release content.

Any catalog change must preserve redaction, path safety and an owner-approved
runbook reference. Do not infer process identity from a PID file or alter
running state while updating an inventory. The planned externalization of
source-worktree `logs/` and `data/runtime/` is documented in
[`quality/runtime-path-policy.json`](../quality/runtime-path-policy.json); it
is intentionally a compatibility plan and has no runtime activation in this
repository change.
