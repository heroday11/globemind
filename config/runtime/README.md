# Runtime configuration catalog

`env-manifest.json` is the machine-readable ownership and change-control catalog for GlobeMind runtime environment variables. It documents configuration metadata only and must never contain deployed secret values.

Each variable declares:

- `owner`: the team accountable for validation and rollback.
- `sensitivity`: `public`, `internal`, or `secret`.
- `services`: every process that consumes the value.
- `restart_required`: whether an already running process must be replaced.
- `activation`: `process_restart`, `service_restart`, `checkpointed_restart`, or `next_run`.
- `default_policy`: whether the value is required, derived, safely defaulted, optional, disabled, or a legacy contextual setting.
- `scope`: `web`, `database`, `security`, `ai`, or `pipeline`.

Validate after every catalog change:

```bash
python3 scripts/ci/check_runtime_config_manifest.py
python3 scripts/ci/check_runtime_config_manifest.py --format json
python3 -m pytest -q backend/tests/test_architecture_gates.py
```

For production changes, record the old value, new value, owner, affected services, activation point, health check, and rollback condition. A `checkpointed_restart` must wait for the pipeline's durable checkpoint; configuration work is not permission to interrupt an active long-running job.

Secret rotation requires a separate runbook. The catalog may name a secret or a secret-file path, but validators reject embedded default values on variables marked `secret`.
