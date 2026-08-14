# V1 Release Checklist

This checklist is the current V1 release gate. Historical handoffs and acceptance records provide
evidence only; they do not supply version arguments for a new release.

## 1. Establish identities

- [ ] Read candidate version from `VERSION`; do not override it through an environment variable.
- [ ] For the formal V1 artifact, the reviewed version commit sets `VERSION` to `1.0.0` before the
  runtime, quality report, and release are built; all three must retain that exact identity.
- [ ] Resolve `current`, then read production version, build ID, Git SHA, and runtime version from
  its immutable `release.json`.
- [ ] Confirm production and the V1 rollback anchor are build
  `0.11.0-20260710T223243Z` plus runtime `0.11.0`. The current `previous` V0.10 link is historical
  state, not the V1 rollback identity.
- [ ] Record all resolved paths and manifest SHA-256 digests in the release evidence.

## 2. Close source provenance

- [ ] The final V1 release-scoped provenance reports `scope=release_inputs`, `dirty=false`, zero
  scoped Git status entries, and zero untracked or ignored included inputs.
- [ ] Do not set `ALLOW_DIRTY_RELEASE`, `ALLOW_UNVERIFIED_RELEASE`, or linked frontend dependency
  mode for the formal artifact.
- [ ] Run the complete quality gate on the exact frozen source; its recorded snapshot must match
  release assembly before and after packaging.

## 3. Build and verify artifacts

- [ ] Build and verify `/root/data/python-runtimes/globemind-web/${release_version}`, where
  `release_version` was read from `VERSION`. Never overwrite an existing different fingerprint.
- [ ] Create one immutable production release with clean `npm ci` inputs.
- [ ] Verify schema `3`, exact version/build/Git identity, SHA256SUMS closure, non-writable files,
  archived quality evidence, and the matching external runtime manifest.
- [ ] Independently reverify the V0.11 rollback release and runtime immediately before promotion.

## 4. Candidate acceptance

- [ ] Start only an isolated loopback candidate with its explicit release/runtime identity, four
  workers, disabled runtime schema mutation, disabled candidate scheduler, and isolated generated
  assets. Do not alter production links or Cloudflare.
- [ ] Pass HTTP acceptance, browser smoke at desktop/mobile viewports, worker replacement, capacity,
  authentication failure, representative API, static asset, and release identity checks.
- [ ] Treat 36 as the current HTTP base only. After V1 directory/static-closure checks are finalized,
  record the exact `acceptance.json.summary.total`; it must equal `required_passed` and the number of
  `checks/*.json` files, with `failed=blocked=degraded=skipped=0`. Record the equation
  `36 + new_directory_checks = final_total` rather than copying an older total.
- [ ] Stop the candidate by its strong identity and prove its isolated port is free.

## 5. Protect services and pipelines

- [ ] Before promotion, save `/usr/bin/python3 -B scripts/globemind_runtime.py catalog --json` and
  `/usr/bin/python3 -B scripts/globemind_runtime.py status --json` as immutable evidence.
- [ ] For every protected service/pipeline, capture PID, process start ticks, boot ID, executable,
  cwd, exact argv, PGID/SID where applicable, and checkpoint/progress evidence. PID-only evidence
  cannot prove an unchanged identity and blocks promotion for that protected process.
- [ ] Repeat the same capture after promotion. Strong identity tuples must be identical; durable
  checkpoints and monotonic counters must not regress. Cloudflare must retain its connector
  incarnation and readiness.

## 6. Promote and observe

- [ ] Follow [V1_WEB_PROMOTION.md](V1_WEB_PROMOTION.md): create the short-lived dry-run credential,
  separately retain its digest, then apply the exact bound request under the promotion lock.
- [ ] Do not use bare `restart`, manual PID signals, broad process matching, or manual link edits.
- [ ] Require target process identity, four workers, local readiness, database readiness, scheduler
  leadership, public readiness, and protected-pipeline comparison before declaring success.
- [ ] Preserve the credential, sealed promotion audit, candidate evidence, pre/post runtime evidence,
  and rollback verification under the final build ID.
- [ ] If any invariant fails, stop public handoff and use only the transaction's bound recovery path.
