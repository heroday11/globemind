# Configuration map

Status: current source configuration navigation
Scope: application policy, runtime environment manifests and role examples

Configuration is split by risk and activation boundary:

- `settings.py` and `db_runtime_config.py` are importable application
  configuration boundaries; they do not contain credentials.
- `runtime/env-manifest.json` is the reviewed environment-variable inventory.
- `runtime/*.env.example` files are safe role-shaped examples only. Copy them
  to a private local file and never commit populated secrets.
- `*-policy.json` and `*-inventory.json` files are read-only quality/audit
  contracts, not commands to start or migrate a service.

The planned externalization of source-worktree logs/state is described in
[`../quality/runtime-path-policy.json`](../quality/runtime-path-policy.json).
That plan is inactive and does not change current defaults.
