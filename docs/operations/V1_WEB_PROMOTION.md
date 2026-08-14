# V1 Web Promotion Transaction

`deploy/promote_web_release.py` is the V1 two-phase Web promotion tool. It does not create a
release, run candidate acceptance, manage Cloudflare, or control data pipelines. Its only
lifecycle capability is invoking the reviewed Web controller with the exact action `stop` or
`start`.

The command is a dry-run unless `--apply` is present. Do not use `--apply` until the target
schema-v3 release, its versioned Web runtime, candidate HTTP/browser checks, worker replacement,
capacity check, protected-pipeline comparison, and rollback artifact verification have all
passed.

## Transaction invariants

Before writing a credential, and again under the promotion lock immediately before stopping Web,
the tool proves all of the following:

1. Target, current, and previous are immutable schema-v3 releases directly below the managed
   release root. The target is not the current release.
2. `verify_release.py` runs with `--production`, exact version/build/git identity, and the matching
   external runtime directory and manifest. There is no unverified or legacy flag. The verifier,
   `release_lib.py`, promotion library/CLI, controller, interpreter, release manifests, runtime
   manifests, environment files, and their content hashes are bound into the credential.
3. `current` and `previous` are trusted symlinks in the same release directory. Promotion writes
   `previous` first and `current` second using same-directory temporary symlinks plus `os.replace`.
   A crash before the second replacement leaves Web pointing at the old current release. A
   durable, content-bound `.promotion-active.json` records each destructive phase for explicit
   recovery.
4. The production PID and token metadata agree on PID, start ticks, port, and instance, and the
   live kernel boot ID is bound into the observation. The master is a PID-1 child and its own
   process-group/session leader; executable, cwd, command line, and listener ownership match the
   current release and runtime. Exactly the configured number of direct Uvicorn workers have
   matching executable, cwd, PGID, SID, and stable start ticks.
5. Local readiness reports the exact release, database `up`, and an enabled, healthy, running
   assistant scheduler. Its fresh leader instance must be bound to one of the verified workers.
   Master and worker identities are checked again after the health request.
6. The Web database identity is fixed to `web_runtime`. Its only password source is
   `/root/data/secrets/globemind/web_runtime.password`, which must be an operator-owned,
   non-symlink regular file with exact mode `0600`. The credential and preflight facts bind the
   file path, SHA-256 digest, size, mode, and owner ID. They never contain the password.

The tool contains no process-signal API. It never invokes `restart`, a shell command string,
`pkill`, `killall`, or a negative PID. The content-bound controller is the sole stop/start
authority.

## Global serialization

Apply and recovery hold `/root/data/releases/globemind/.promotion.lock` for the complete
transaction. The exact locked file description is inherited by `deploy/start_web_prod.sh`, which
validates its path, ownership, permissions, link count, and live lock before touching the Web
instance. A direct production start, stop, or restart obtains the same lock itself. Consequently a
direct controller call cannot enter between the transaction's under-lock revalidation and its
stop/link/start phases. Candidate instances do not use the production promotion lock.

## Preflight credential

Use one shell array for both phases so that apply cannot silently change a path, timeout, worker
count, database pool limit, or environment-file order:

```bash
BUILD_ID=1.0.0-YYYYMMDDTHHMMSSZ
REQUEST_ID=web-v1-${BUILD_ID}
CREDENTIAL=/root/data/runtime/globemind/web/preflight-${REQUEST_ID}.json

PROMOTION_ARGS=(
  --request-id "$REQUEST_ID"
  --target-release "/root/data/releases/globemind/${BUILD_ID}"
  --env-file /root/data/globemind/backend/api/.env
  --env-file /root/data/globemind/backend/agentic_rag/.env
  --env-file /root/data/globemind/.env
  --project-dir /root/data/globemind
  --release-root /root/data/releases/globemind
  --runtime-root /root/data/python-runtimes/globemind-web
  --current-link /root/data/releases/globemind/current
  --previous-link /root/data/releases/globemind/previous
  --controller /root/data/globemind/deploy/start_web_prod.sh
  --verifier /root/data/globemind/deploy/verify_release.py
  --verify-python /usr/bin/python3
  --pid-file /root/data/web/pids/globemind_web_prod.pid
  --database-password-file /root/data/secrets/globemind/web_runtime.password
  --generated-asset-root /root/data/web/generated-assets
  --audit-root /root/data/runtime/globemind/web/promotions
  --host 127.0.0.1
  --port 18089
  --web-workers 4
  --credential-ttl 180
)

/usr/bin/python3 -B deploy/promote_web_release.py \
  "${PROMOTION_ARGS[@]}" \
  --credential-out "$CREDENTIAL"
```

The credential output path must not already exist. The file is created mode `0600`; its parent
must already exist, be owned by the operator, and not be group/world writable. Record the printed
`credential_sha256` separately. A credential is valid for at most 300 seconds, is bound to all
arguments and observed facts, and is consumed once by creating an audit directory containing its
nonce. Editing the JSON, changing any explicit argument, changing source/runtime/config content,
changing the database password file content or metadata, replacing a worker, or allowing the TTL
to expire makes apply fail before the controller is called. Rotate the database password before
creating a new dry-run credential, never between dry-run and apply.
After apply durably creates the exact active journal, `--recover` may use the same expired
credential. Digest, request, schema, policy, facts, journal links/audit path, and live
release/runtime/config evidence remain mandatory; expiry is bypassed only for that interrupted
transaction, never for a new apply.

## Apply

Recheck that the public handoff has not begun and no other lifecycle command is running. Then use
the exact digest printed by the dry-run:

```bash
CREDENTIAL_SHA256=<exact-64-character-digest>

/usr/bin/python3 -B deploy/promote_web_release.py \
  "${PROMOTION_ARGS[@]}" \
  --credential "$CREDENTIAL" \
  --credential-sha256 "$CREDENTIAL_SHA256" \
  --apply
```

Apply acquires `/root/data/releases/globemind/.promotion.lock`, consumes the credential, repeats
the full preflight, passes the same locked file description to every controller invocation, and
then performs these explicit phases:

1. `controller stop` with the old release/runtime/environment identity.
2. Prove the old master incarnation is gone and the declared TCP listener is absent.
3. Atomically set `previous -> old current`, then `current -> target` in the release directory.
4. `controller start` with an exact, non-ambient environment. Runtime schema mutation and legacy
   release flags are forced off; the production assistant scheduler is explicitly enabled. The
   controller receives `DB_USER=web_runtime`, the fixed `GLOBEMIND_DB_PASSWORD_FILE` path,
   `DB_SSLMODE=disable`, and `GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT=1`. All
   `L1_DB_{HOST,PORT,USER,NAME}` and `OPINION_DB_{HOST,PORT,USER,NAME}` aliases are cleared so an
   environment file cannot select a different database identity. Disabled TLS is permitted only
   for the application's explicitly guarded private SCRAM transport; do not expose that database
   connection outside the approved private or loopback network.
5. Prove the new strong process identity, release readiness, database readiness, scheduler leader,
   and identity stability across the health gate.

The active journal is cleared only after the audit is sealed. Controller/verifier/promotion and
environment-file hashes are checked before and after each controller call and after the final
health gate.

## Interrupted recovery

An active `/root/data/releases/globemind/.promotion-active.json` blocks new apply operations. Do
not delete or edit it. Use the original arguments, credential, and separately recorded digest:

```bash
/usr/bin/python3 -B deploy/promote_web_release.py \
  "${PROMOTION_ARGS[@]}" \
  --credential "$CREDENTIAL" \
  --credential-sha256 "$CREDENTIAL_SHA256" \
  --recover
```

Recovery accepts only the original links, the `previous -> old current` intermediate state, or the
completed target/old-current pair. It re-verifies all releases, runtimes, tools, environment files,
database password file metadata/content digest, and production verifier evidence. If target is
selected, recovery stops it with the exact controller and proves the port free before restoring
either link. It then restores both original links and proves the old release's full
process/readiness/database/scheduler gate. A partially written audit seal is validated against the
journal and live outcome, then finished idempotently. A password rotation during an interrupted
transaction is fail-closed; restore the bound file before recovery rather than editing evidence.

## Failure and rollback

Any exception after a stop attempt enters recovery. After any target start attempt, the same exact
fail-closed controller is called with `stop`; that controller sends a signal only when its own
PID/meta/start-ticks/instance checks pass. When the transaction captured the target's full strong
identity, it additionally proves that exact incarnation died. In every case the target port must be
free before link restoration is allowed. A stop failure, unreadable death proof, or occupied port
therefore produces `rollback_failed` without changing the links back or attempting to start the old
release on top of an uncertain target. Only after target cleanup is proven does the tool restore
both original links, start the old current release with its exact runtime/environment, and repeat
the complete process/readiness/scheduler gate.

Exit status is `0` only for a completed dry-run, promotion, or recovery. Apply returns `1` when
target promotion failed but rollback passed, `3` when rollback/recovery failed, and `2` for an
invalid credential, mismatched journal, or pre-mutation invariant failure. Exit `3` is a production
incident: freeze automatic attempts; preserve the journal, audit directory, and controller log;
and investigate exact PID, start ticks, port ownership, links, verifier hashes, and scheduler state.
Never delete the journal, manually rewrite links, or use broad process cleanup.

## Audit evidence

Each apply creates
`/root/data/runtime/globemind/web/promotions/<request-id>-<nonce>/`. It contains numbered
phase records, `result.json`, and `SHA256SUMS`. Controller stdout/stderr and response bodies are not
stored; records retain only bounded byte counts, SHA-256 values, exact release/process identities,
database password file metadata, and semantic health results. Neither credential nor audit output
contains the database password. A completed directory is sealed mode `0550`, with files mode
`0440`.
When recovery cannot be proven, `result.json` remains a durable checkpoint in a mode `0700`
directory so a later `--recover` can append evidence and seal the final result. Numbered records
are published only after their complete content is fsynced.

The audit result does not authorize Cloudflare handoff. Public routing changes, post-cutover
sampling, protected-pipeline comparison, and release observation remain separate reviewed gates.
