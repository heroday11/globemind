# GitHub repository controls

Status: current collaboration configuration
Scope: review routing, dependency updates and issue intake

- `CODEOWNERS` routes policy, data, operations, frontend and documentation
  changes to the repository owner until dedicated teams are created.
- `dependabot.yml` keeps GitHub Actions, npm workspaces and the role dependency
  directory on a monthly review cadence.
- `ISSUE_TEMPLATE/` requires reproducible evidence, scope, safety and rollback
  considerations without collecting secrets or production exports.
- `workflows/quality-gate.yml` is the read-only CI entrypoint; its quality gate
  includes repository hygiene and Remotion checks.

The files are statically checked by
`scripts/ci/check_repository_hygiene.py`; they do not grant production or
deployment authority.
