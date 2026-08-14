# Database Runtime Roles (V0.10)

## Contract

V0.9.3 separates object ownership from runtime access:

| Role | Purpose | Persistent DDL | Data access |
|---|---|---:|---|
| `postgres` | Existing object owner and approved migration operator | Yes, maintenance only | Owner |
| `web_runtime` | Production Web/API workers | No | Explicit 31-table allowlist; DML only on application and opinion working tables |
| `wave1_loader` | Wave1 JSONL loader | No | `news`, `media_source`, `globemind_pipeline_checkpoint` only |

Both runtime roles are `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
NOREPLICATION NOBYPASSRLS`. They receive `CONNECT`, schema `USAGE`, explicit
table privileges, and only the sequences needed by their inserts. They do not
own objects and must not be members of another role.

Default privileges are deliberately revoked. PostgreSQL default grants apply
to every future object of a type and therefore conflict with a fixed allowlist.
Every schema migration must update `deploy/db_role_policy.py`, rerun the role
tool, and pass `verify` before the new code is promoted.

## Fixed Scope

The tool is fixed to database `news`, schema `public`, and owner `postgres`.
It accepts no database, owner, role, schema, table, sequence, manifest, or raw
SQL override. Endpoint host/port and TLS mode are connection transport only.

## Database Consumer Inventory

`ops/runtime/database-consumers.json` is the V0.10 machine-readable database
consumer contract. Its long-running scope is derived from
`ops/runtime/services.json`: every service whose `external_dependencies`
contains `postgres-news` must appear exactly once, and no other service may be
added. Validate it offline with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  scripts/ci/check_database_consumers.py --format json
```

The checker rejects duplicate JSON keys and IDs, schema drift, missing or extra
coverage, unknown services, owner/controller mismatches, paths outside the
repository, unreviewed roles, embedded credential values, and maintenance
entrypoints outside the approved set. Credential records contain reference
names and reference types only; paths and secret values are deliberately not
inventory data.

| Service | Runtime entrypoint | Current role | Target role | TLS status | Migration |
|---|---|---|---|---|---|
| `web` | `backend/serve_prod.py` | `web_runtime` | `web_runtime` | Private SCRAM exception; TLS disabled | Role assigned; TLS pending |
| `wave1_loader` | `scripts/stream_load_news_to_postgres.py` | `wave1_loader` | `wave1_loader` | Private SCRAM exception; TLS disabled | Role assigned; TLS pending |
| `daily_ingest` | `deploy/daily_news_ingest_loop.sh` | Unverified legacy instance | `wave1_loader` | Active TLS unverified; managed private SCRAM policy pending | Checkpointed takeover pending |
| `l1_prep` | `scripts/stream_l1_event_features.py` | `postgres` owner default | Unassigned | Driver default; unverified | Blocked |
| `l1_extract` | `scripts/stream_l1_event_features.py` | `postgres` owner default | Unassigned | Driver default; unverified | Blocked |
| `quality_labels` | `deploy/news_quality_labels_loop.sh` | `postgres` owner default | Unassigned | Driver default; unverified | Blocked |
| `ground_refresh` | `deploy/ground_news_realtime_refresh_loop.sh` | `postgres` owner default | Unassigned | Driver default; unverified | Blocked |
| `ground_images` | `deploy/ground_news_image_backfill_loop.sh` | `postgres` owner default | Unassigned | Driver default; unverified | Blocked |

`daily_ingest` is deliberately not marked as running under `wave1_loader`.
The active legacy instance predates the managed controller takeover; its role,
credential source, network scope, and TLS mode remain unverified. The new source
policy targets `wave1_loader`, a controller source secret-file reference, and
the audited private SCRAM transition, but those facts are not active-runtime
evidence. A durable checkpoint, replay proof, maintenance window, managed
controller takeover, and rollback evidence are required before changing its
current status.

The five unassigned targets are intentional. Source proves that they currently
default to `postgres`, accept legacy plaintext environment or dotenv password
references, do not enforce an SSL mode, and execute schema DDL inside their
runtime path. A role name must not be invented from this inventory. Separate
DDL into an approved maintenance path, review the required capabilities, add a
least-privilege role policy, harden the credential and TLS boundary, then use a
checkpointed maintenance window to migrate each service.

Every listed service is long-running. Its maintenance window remains required
and `not_scheduled`; the inventory is evidence for planning, not authorization
to restart, adopt, migrate, or signal a process.

### Approved Maintenance Surface

Only these on-demand database maintenance entrypoint classes are approved and
recorded:

| Class | Entrypoint | Execution role |
|---|---|---|
| Schema migration | `deploy/v093_database_schema.py` | maintenance-only `postgres` |
| Runtime role administration | `deploy/db_runtime_roles.py` | maintenance-only `postgres` |

They accept secret-file **references** and require an explicit TLS selection.
Modules such as `deploy/db_role_policy.py` are libraries, not entrypoints. Other
ad hoc migration, schema, backfill, repair, or inspection scripts are not
approved HBA consumers by default. They must receive a separate audit and
inventory entry before any new HBA rule is created for them.

`web_runtime` can write:

- `app_user`: `SELECT, INSERT, UPDATE`
- `assistant_chat_session`, `assistant_chat_message`, `assistant_user_memory`:
  `SELECT, INSERT, UPDATE, DELETE`
- `password_reset_token`: `SELECT, INSERT, UPDATE, DELETE`
- `user_favorite`, `user_search_history`: `SELECT, INSERT, DELETE`
- `china_opinion_article_scores`: `SELECT, INSERT, UPDATE, DELETE`
- `china_opinion_feedback`: `SELECT, INSERT`

Its other allowlisted tables are read-only. The current `StoryGraphView` contract uses the L2/L3
event, segment, member, edge, coreference, and news relations already present in that allowlist.
The authenticated `/api/health/features` endpoint reads the same ten current relations and columns, so candidate capability
evidence matches the page rather than a legacy fallback. `story_edges`, `story_trees`, and
`story_relations` remain outside the role policy: they are not used by the current page, and
`story_edges` is absent from the production `news` schema. Do not create or grant legacy relations
to satisfy a health check.

`wave1_loader` receives only:

- `news`: `SELECT, INSERT`; sequence `news_id_seq`
- `media_source`: `SELECT, INSERT, UPDATE`; sequence `media_source_id_seq`
- `globemind_pipeline_checkpoint`: `SELECT, INSERT, UPDATE`

## Preconditions

Do not apply the role policy until all checks below are true:

1. A reviewed backup and tested rollback path exist.
2. The target is the `news` database and the administrative session is the
   `postgres` superuser.
3. Schema `public`, every allowlisted table, and every allowlisted sequence are
   owned by `postgres`.
4. `deploy/v093_database_schema.py dry-run` reports exactly the two pinned V0.9.3
   migrations. Do not run either underlying SQL source directly.
5. The fixed schema wrapper has completed `apply` and `verify`, so the opinion
   write schema and `public.globemind_pipeline_checkpoint` satisfy the complete
   V0.9.3 contract before role provisioning. Web requests only perform read-only
   schema readiness checks.
6. Both new role passwords are independently generated and stored in distinct,
   absolute, owner-matched, regular, non-symlink files with exact mode `0600`.
7. The temporary `postgres` credential file used by the provisioning session
   also has exact mode `0600` and is removed after the approved operation.

The schema SQL is idempotent for a fresh or already-complete schema. If an
existing table is only partially migrated, inspect it first; `CREATE TABLE IF
NOT EXISTS` does not repair incompatible columns.

## Offline Review

This command connects nowhere and reads no secret:

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/v093_database_schema.py dry-run | python3 -m json.tool

PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/db_runtime_roles.py dry-run | python3 -m json.tool

PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/db_runtime_roles.py dry-run | jq -e '
    .roles.web_runtime.tables.story_edges == null and
    .roles.web_runtime.tables.event_l2_chains == ["SELECT"] and
    .roles.web_runtime.tables.event_l3_macro_edges == ["SELECT"]
  '
```

Generate role secrets under an approved secret root:

```bash
install -d -m 0700 /root/data/secrets/globemind
umask 077
openssl rand -base64 48 > /root/data/secrets/globemind/web_runtime.password
openssl rand -base64 48 > /root/data/secrets/globemind/wave1_loader.password
chmod 0600 /root/data/secrets/globemind/*.password
```

Never put these values in argv, `.env`, shell history, a URL, JSON output, or a
release directory.

## Approved Apply And Verify

The schema wrapper must run first. Both tools are fixed to database `news`,
schema `public`, owner `postgres`, use advisory transaction lock `(908731, 2)`,
and verify before committing. Replace only the transport endpoint and secret
file paths. For the current V1 acceptance review, the read-only role `verify`
has been executed and returned `status=ready` with `findings=[]` (0 findings).
No role `apply` was executed or required: run `apply` only when a reviewed
policy difference exists, then repeat `verify` before promotion.

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/v093_database_schema.py apply \
  --host <postgres-host> --port <postgres-port> --sslmode require \
  --admin-password-file /run/globemind-secrets/postgres.password

PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/v093_database_schema.py verify \
  --host <postgres-host> --port <postgres-port> --sslmode require \
  --admin-password-file /run/globemind-secrets/postgres.password

PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/db_runtime_roles.py apply \
  --host <postgres-host> --port <postgres-port> --sslmode require \
  --admin-password-file /run/globemind-secrets/postgres.password \
  --web-password-file /root/data/secrets/globemind/web_runtime.password \
  --loader-password-file /root/data/secrets/globemind/wave1_loader.password

PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/db_runtime_roles.py verify \
  --host <postgres-host> --port <postgres-port> --sslmode require \
  --admin-password-file /run/globemind-secrets/postgres.password
```

The current production server has `ssl=off` on private address
`192.168.207.171:54333`. During that audited transition, replace
`--sslmode require` in every command above with:

```text
--sslmode disable --allow-private-scram-transport
```

That exception accepts only a literal private IP, a remote private client, and
SCRAM-SHA-256 server/password policy. In `rule_number` order it simulates the
first HBA match for database `news`, each fixed runtime role, and the connected
client address; both first matches must use SCRAM. Group, regex, include,
`sameuser`, `samehost`, or other selectors the tool cannot prove are rejected.
Prefer `verify-full` once PostgreSQL TLS and hostname verification are available.

Role `apply` locally derives SCRAM verifiers, sends no new plaintext role
password over SQL, removes existing direct grants, revokes broad owner default
grants, grants the fixed allowlists, and verifies before commit. Missing or
incompatible objects, unexpected owners, memberships, owned objects, schema
`CREATE`, unexpected effective privileges, or default grants cause rollback.

## Web Candidate

The candidate overlay contains the role, secret-file path, and explicit
transport exception for the current private `ssl=off` deployment:

```bash
set -a
. config/runtime/web-database-role.env.example
set +a
```

Use a deployment-specific copy outside the release if its path differs. The
Web resolver requires `GLOBEMIND_DB_PASSWORD_FILE` outside tests and rejects a
relative path, symlink, non-regular file, wrong owner, mode other than `0600`,
empty content, embedded newline, NUL, or an oversized secret. It never falls
back to `DB_PASSWORD`, `PG_PASSWORD`, `L1_DB_PASSWORD`, or
`OPINION_DB_PASSWORD`; Web bootstrap removes those legacy variables after the
secret-file setting is loaded.

All Web database engines use the same resolver: primary ORM, L1 search, story
graph, opinion, and financial aggregation. SQLAlchemy URLs are constructed with
`URL.create`, not password-bearing string interpolation.

Before promotion, start the candidate with `DB_USER=web_runtime` and the secret
file path, then exercise login/registration, favorites, search history,
assistant sessions, opinion feedback/admin refresh, story graph, search,
financial dashboard, readiness, and worker failover. A permission error is a
deployment blocker; do not widen the role interactively. Update the reviewed
policy instead.

The candidate HTTP gate now calls `/api/health/features` with its short-lived Bearer identity and requires the exact V1 feature set,
`ready=true`, `status=healthy`, and every check `status=up`. The Story Graph check reads the exact
current L2/L3 page dependency set. This is also mandatory because the legacy
`/api/story-graph/list` compatibility query can turn a database exception into an empty result;
that fallback is not accepted as capability evidence. After read-only role verification, run
against an isolated candidate only:

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/conda/envs/Globemind_env/bin/python -B \
  deploy/candidate_smoke.py \
  --base-url http://127.0.0.1:<candidate-port> \
  --expected-build-id <exact-build-id> \
  --auth-token-file /run/globemind-secrets/candidate-<exact-build-id>.token \
  --output-dir <new-empty-evidence-directory>

jq -e '
  .status == "passed" and
  any(.checks[]; .check_id == "health_features" and .outcome == "passed") and
  any(.checks[]; .check_id == "runtime_catalog" and .outcome == "passed")
' <new-empty-evidence-directory>/acceptance.json
```

The read-only role `verify` above has been executed for the current V1 review and returned
`status=ready` with 0 findings. Role `apply` was not executed and is not needed without a reviewed
policy difference. Candidate acceptance remains a separate future step against an isolated V1
candidate; it is not implied by the successful database role verification.

The candidate token is a short-lived local artifact used only to authenticate black-box checks such
as `/api/ops/runtime-catalog`. It must satisfy the ownership, mode, non-symlink, size, and single-line
rules in `V010_ACCEPTANCE.md`; neither its value nor its path is retained in acceptance evidence.

## Pipeline Cutover

The Wave1 controller and daily ingest loop default to `DB_USER=wave1_loader`,
require their own source-file variable, materialize only a managed mode-`0600`
credential file, and remove plaintext database secret variables from child
process environments. Load the corresponding overlay before a checkpointed
restart:

```bash
set -a
. config/runtime/wave1-loader-database-role.env.example
set +a
deploy/wave1_loader_ctl.sh start

set -a
. config/runtime/daily-ingest-database-role.env.example
set +a
deploy/daily_news_ingest_ctl.sh restart
```

Wait for a durable loader/ingest checkpoint before replacement. The temporary
rollback flags `WAVE1_LOADER_ALLOW_LEGACY_DB_ROLE=1` and
`DAILY_INGEST_ALLOW_LEGACY_DB_ROLE=1` permit `DB_USER=postgres` only during an
approved rollback; they are not normal production settings.
For rollback, point the same controller-specific password source variable at
the approved mode-`0600` `postgres` secret file; plaintext environment fallback
remains disabled.

## V0.10 Legacy Surface Status

All audited legacy relation gaps are closed. The graph briefing API and its
assistant/smoke consumers now use the current L3/L2/L1 hierarchy; no legacy
graph relation was added to the runtime grant policy.

| Surface | V0.10 behavior | Relation status |
|---|---|---|
| `/api/graph/*` | `migrated_current_l3_l2_l1` | Uses current L3/L2/L1 membership, `news`, and existing opinion-score relations already granted to `web_runtime` |
| Nine legacy opinion analysis paths | `retired_410` | No database dependency; stable contract in `api.features.legacy_retirement` |
| `/api/dashboard/search/v11-clusters*` | `migrated_current_l3_l2_l1` | Uses current L3, L2, L1, and `news` relations already granted to `web_runtime` |

The graph migration did not create placeholder relations or widen
`web_runtime`; it reuses the existing current-hierarchy allowlist.

`web_runtime` is intentionally capped at 64 connections. The Web database consumers now share the
single `api.core.db.engine` and `SessionLocal` within each worker. With the production defaults in
`deploy/start_web_prod.sh` and `api.db_pool.engine_pool_kwargs` (`WEB_WORKERS=4`,
`DB_POOL_SIZE=3`, and `DB_MAX_OVERFLOW=2`), four workers can theoretically request
`1 engine * 4 workers * (3 pool + 2 overflow) = 20` connections. This consolidation is enforced by
`backend/tests/test_database_engine_consolidation.py::test_all_web_database_exports_share_one_engine_and_session_factory`,
`::test_only_core_database_module_constructs_an_engine_or_session_factory`, and
`::test_four_worker_connection_budget_uses_one_pool_per_worker`. The server still has
`max_connections=100`; the role cap remains protection, not proof of adequate peak capacity.
Worker failover and sustained-load evidence are still required before changing either limit.

The current production HBA still has a cluster-wide `all`/`all` SCRAM catch-all,
although the preflight also accepts precise runtime-role and CIDR rules.
Database-level `PUBLIC CONNECT` and that broad production HBA surface remain
explicit residual risk. The V0.10 inventory closes the enumeration prerequisite
but does not authorize the maintenance step. Verified TLS, revocation of
unneeded `PUBLIC CONNECT`, and narrower HBA rules remain pending until every
blocked consumer has an assigned role and a reviewed maintenance window.

## Rollback

Role provisioning does not revoke or rotate the existing `postgres` password.
If the candidate fails, route traffic back to the prior release and its prior
credential configuration. Keep the new roles disabled from traffic while the
allowlist or migration is corrected. Do not grant ownership, schema `CREATE`,
superuser, role administration, or `ALL TABLES` as an emergency workaround.
