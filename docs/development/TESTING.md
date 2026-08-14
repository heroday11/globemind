# Testing GlobeMind

Status: current developer guide
Scope: source-checkout tests and CI-equivalent offline validation
Source of truth: `pyproject.toml`, workspace package scripts and `deploy/run_quality_gate.sh`

## Pick the smallest useful test

| Change | First command |
| --- | --- |
| Backend feature | Test linked from [`backend/api/features/README.md`](../../backend/api/features/README.md) |
| Vue feature | Test linked from [`frontend/vue_project/src/features/README.md`](../../frontend/vue_project/src/features/README.md) |
| Financial terminal | `make test-financial` |
| Shared frontend package | `make test-shared` |
| Repository policy or CI | Relevant `backend/tests/test_*contract.py`, then `make quality` |
| Documentation only | Link/hygiene test, then `make quality` before merge |

Python diagnostics must disable bytecode before the interpreter starts:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q path/to/test.py
```

## Offline developer suite

```bash
make test
```

This runs Python tests excluding explicit external-resource markers, plus maintained
frontend workspace tests.

## Python markers

| Marker | Meaning |
| --- | --- |
| `integration` | Crosses a process or service boundary |
| `live_db` | Requires an explicitly authorized non-ephemeral database |
| `gpu` | Requires CUDA hardware and model weights |
| `slow` | Materially longer than the default developer suite |

Do not remove a marker or weaken an assertion simply to make a local environment pass.
Record which external prerequisite was unavailable instead.

## Static and type checks

```bash
make lint
make typecheck
```

Python Ruff coverage is deliberately ratcheted: current feature, release and security
surfaces are checked, while remaining legacy files are migrated incrementally. New
code must not use the historical backlog as permission to add new lint debt.

## CI-equivalent quality gate

```bash
make quality
```

The gate combines configuration validation, root layout, repository hygiene, Ruff,
module boundaries, feature ownership, runtime configuration, database consumers,
content bundles, sensitive-source scanning, Python tests and frontend contracts. It is
offline and does not authorize a deployment or production database connection.

When CI fails, use the named failed step and test identifier shown in the Actions
annotation. Reproduce only that focused check first, then rerun `make quality`.
