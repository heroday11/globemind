# Local development modes

Status: current developer guide
Scope: a source checkout with local or mocked dependencies
Source of truth: `Makefile`, environment examples and workspace package scripts

Choose the smallest mode that can prove your change. Most frontend and contract work
does not need a database, model download or long-running pipeline.

## Common setup

```bash
python3.11 -m venv .venv
. .venv/bin/activate
make install-python
make install-web
```

Never copy production secrets into the checkout. Local `.env` files are ignored, but
that is not a reason to reuse production credentials.

## Mode A: frontend with local mock API

Use this for layout, interaction, accessibility and client-side feature work:

```bash
make dev-web-mock
```

The Vite development server answers its bounded `/api` mock routes locally. Mock data
does not prove backend, database, model or production availability.

## Mode B: frontend against a local API

Copy the frontend example only when the local file does not already exist:

```bash
test -e frontend/vue_project/.env.local || \
  cp frontend/vue_project/.env.example frontend/vue_project/.env.local
make dev-web
```

Set `VITE_API_PROXY_TARGET` to the local API. Values prefixed with `VITE_` may enter the
browser bundle and must never contain secrets.

## Mode C: API contract development

Unit and contract tests are the default API workflow:

```bash
make test-python
```

To start the HTTP application, prepare an isolated PostgreSQL database and a local
`backend/api/.env`, then run `make dev-api`. The optional `docker-compose.yml` provides
PostgreSQL only; it does not create the complete GlobeMind news schema or production
data. [`DB_SCHEMA_GLOBEMIND.md`](../DB_SCHEMA_GLOBEMIND.md) is a sanitized structural
reference, not an executable migration.

## Mode D: pipeline or model work

Read [`../../core_pipeline/README.md`](../../core_pipeline/README.md), the relevant
script entry in [`../../scripts/README.md`](../../scripts/README.md), and the test
markers before installing heavyweight dependencies. Do not use a pipeline command as
a smoke test. A long-running or database-writing workflow requires its own checkpoint,
replay and rollback plan.

## Known local limitation

The repository does not currently claim a one-command full product bootstrap from an
empty database. This is intentional until an authoritative, reviewed migration and
redistributable demo dataset exist. Frontend mock mode and offline contract tests are
the supported zero-production-dependency onboarding paths.
