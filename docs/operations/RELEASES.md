# GlobeMind Release Process

`VERSION` is the only editable application version source. Runtime identity is copied into
`release.json`; production startup reads it from that immutable manifest.

The commands below derive candidate identity from `VERSION`. Never paste a version from an older
handoff or acceptance record into a new command. The current production and V1 rollback baseline is
the immutable build `/root/data/releases/globemind/0.11.0-20260710T223243Z` with runtime
`/root/data/python-runtimes/globemind-web/0.11.0`. Resolve its operational identity from the
selected release manifest before every gate:

```bash
current_release="$(readlink -f /root/data/releases/globemind/current)"
current_manifest="${current_release}/release.json"
read -r current_version current_build current_runtime_version < <(
  /usr/bin/python3 -B - "$current_manifest" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(manifest["version"], manifest["build_id"], manifest["python_runtime"]["version"])
PY
)
current_runtime="/root/data/python-runtimes/globemind-web/${current_runtime_version}"
```

For the V1 promotion, the resolved values must still identify build
`0.11.0-20260710T223243Z` and runtime version `0.11.0`. A disagreement is a release blocker, not a
reason to rewrite a symlink or override the manifest.

## Quality gate

Run the complete offline gate from the repository root:

```bash
release_version="$(tr -d '\r\n' < VERSION)"
runtime_dir="/root/data/python-runtimes/globemind-web/${release_version}"
PYTHON_BIN="${runtime_dir}/bin/python" \
RUFF_BIN=/opt/conda/envs/Globemind_env/bin/ruff \
  deploy/run_quality_gate.sh --output /tmp/globemind-quality-gate.json
```

`PYTHON_BIN` remains the attested Web runtime for source snapshots, import/configuration gates,
secret scanning, and application tests. Ruff is a development tool and is deliberately absent from
that runtime. Select it independently with `RUFF_BIN`; alternatively set `TOOL_PYTHON_BIN` to an
interpreter that provides `python -m ruff`. Selection order is `RUFF_BIN`, `TOOL_PYTHON_BIN`, then
the backward-compatible `PYTHON_BIN -m ruff` fallback. The gate verifies Ruff before linting and
records its version, resolved executable, selection source, working directory, and complete lint
command. A missing or invalid Ruff tool fails the report closed.

The report is valid only for its recorded source snapshot. Existing Vue ESLint and financial
TypeScript debt is capped by `quality/frontend-ratchet.json`; a change may reduce these counts but
must not increase them. Lower the baseline in the same change when debt is removed.
Full main-frontend ESLint and feature-level Node contract tests run before the frontend ratchets.
They reject static regressions and validate each migrated feature public API, request mapping, and
pure presentation/model behavior without adding a browser test dependency.

Production release tooling validates the complete copied report, not only its top-level status. It
requires unskipped Python and frontend scope, passing zero-failure tests, passing ratchets within
their recorded maxima, every required gate step exactly once with exit code zero, and unchanged
source evidence. `--allow-unverified` remains a non-production escape hatch and cannot weaken a
production assembly or finalization.

## Create a release

Production releases use clean `npm ci` installs inside staging and require a source-bound passing
quality report. Schema v3 also requires the prebuilt, versioned Web role runtime whose manifest is
bound to `requirements/roles/web.lock`:

```bash
release_version="$(tr -d '\r\n' < VERSION)"
runtime_dir="/root/data/python-runtimes/globemind-web/${release_version}"
deploy/build_python_runtime.sh build
deploy/build_python_runtime.sh verify
QUALITY_METADATA=/tmp/globemind-quality-gate.json \
PYTHON_RUNTIME_DIR="$runtime_dir" \
  deploy/create_release.sh
```

`deploy/build_python_runtime.sh lock` is a dependency-update operation, not a release step. Run it
only for a reviewed change to `requirements/roles/web.in`, then review and retain the generated
lock and metadata before building a candidate.

The release archives `web.in`, the hash-locked `web.lock`, the runtime manifest, and its pip-check,
import-closure, pytest, and installed-distribution evidence. It records the runtime fingerprint,
CPython ABI/platform, and interpreter digest without copying the virtual environment into the
release. Missing, skipped, mismatched, shared-live, or writable runtime evidence fails production
creation closed.

Production creation rejects dirty release inputs by default. V1 requires clean release-scoped
provenance: `scope=release_inputs`, `dirty=false`, zero scoped Git status entries, and zero untracked
or ignored included inputs. Generate and review the same evidence used by release assembly:

```bash
release_version="$(tr -d '\r\n' < VERSION)"
runtime_dir="/root/data/python-runtimes/globemind-web/${release_version}"
"${runtime_dir}/bin/python" -B deploy/release_tool.py provenance \
  --project "$PWD" --output /tmp/globemind-v1-provenance.json
```

`ALLOW_DIRTY_RELEASE=1` records migration debt but does not satisfy the V1 gate and must not be set
for the formal V1 artifact. `ALLOW_UNVERIFIED_RELEASE`, linked frontend dependencies, skipped gate
scope, or a quality report from a different source snapshot are equally disallowed.

Ordinary CI runs the complete quality gate and fixture-based release-tooling contract tests. It
does not invoke `create_release.sh` or claim to produce a production artifact: production creation
requires the prebuilt versioned runtime and its full evidence, and therefore runs only on a
controlled deployment host after that runtime has been built and verified.

The command prints only the final release directory on stdout. Build progress is written to stderr.
It aborts if the live source changes during packaging, the staged build mutates its inputs, a secret
pattern is found, a lock file is missing, or a required frontend asset is absent.

`FRONTEND_DEPENDENCY_MODE=linked PRODUCTION_RELEASE=0` is available only for offline developer
checks. Production verification rejects releases built in linked mode.

## Verify and promote

Artifact verification is independent of the application runtime. Derive the candidate release and
runtime from the newly created `release.json`, then bind all three expected identities:

```bash
candidate_release="/root/data/releases/globemind/<new-build-id>"
candidate_manifest="${candidate_release}/release.json"
read -r candidate_version candidate_build candidate_git candidate_runtime_version < <(
  /usr/bin/python3 -B - "$candidate_manifest" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    manifest["version"],
    manifest["build_id"],
    manifest["git_sha"],
    manifest["python_runtime"]["version"],
)
PY
)
candidate_runtime="/root/data/python-runtimes/globemind-web/${candidate_runtime_version}"
deploy/verify_release.py "$candidate_release" --production \
  --expected-version "$candidate_version" \
  --expected-build-id "$candidate_build" \
  --expected-git-sha "$candidate_git" \
  --python-runtime-dir "$candidate_runtime" \
  --python-runtime-manifest "$candidate_runtime/inventory/runtime.json"
```

The verifier recomputes every entry in `SHA256SUMS`, rejects unlisted files and writable artifacts,
checks copied dependency locks and quality metadata, scans for secrets, and validates both frontend
entry graphs. Production startup additionally verifies the selected external runtime against the
archived lock, manifest, evidence, fingerprint, actual `pip freeze`, `pip check`, and interpreter
ABI before using its `bin/python`. Do not use a direct `restart` or an ad hoc `start` as the V1
handoff. Complete isolated candidate HTTP/browser gates, verify the exact V0.11 rollback
release/runtime, capture protected-pipeline strong identities, and use the credential/apply phases
in [V1_WEB_PROMOTION.md](V1_WEB_PROMOTION.md).

The HTTP candidate gate currently has a 36-check base. Any V1 directory/static-closure checks must
be added to that base, and the release record must report the final exact total from
`acceptance.json`, equal to the number of `checks/*.json` files. Do not copy `35` or `36` from an
older runbook as the final V1 result.

Schema v2 and v1 releases are rejected by default. Emergency rollback requires both
`ALLOW_LEGACY_RELEASE=1` and an explicit `LEGACY_PYTHON_BIN`; the current full verifier still checks
the legacy artifact before launch. Unknown schema versions are always rejected.

## Release-safe diagnostics

Never import application modules from `current`, `previous`, a versioned release, or a rejected
release. In particular, do not place a release or its `backend` directory on `PYTHONPATH` or
`sys.path`. A read-only HTTP/control-plane check is preferred. If an import-level investigation is
unavoidable, copy the artifact to an isolated temporary directory and operate only on that copy.

Every diagnostic interpreter must be started with the protection enabled before Python begins:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B /absolute/path/to/diagnostic.py
```

Setting `PYTHONDONTWRITEBYTECODE` from inside an already-running interpreter does not prevent imports
which happened earlier from writing bytecode. Directory mode `0555` is also not an enforcement
boundary for a root process. The release tooling disables bytecode internally, but that protection
does not make arbitrary application imports from a release acceptable.

Run the production verifier before and after any release-adjacent investigation. If an unexpected
file is found, preserve the original directory as incident evidence. Reconstruct and verify a clean
copy, then promote it atomically; never delete the unexpected file in place.

Release creation never updates a current symlink, restarts a service, or changes Cloudflare. Those
are separate promotion operations after candidate smoke testing. V1 promotion is the reviewed
two-phase transaction; protected service and pipeline identities must match before and after it.
