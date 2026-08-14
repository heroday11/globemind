# Continuous audit registry and offline runner

`ops/audit/registry.json` is the machine-readable inventory for all 130 audit
items (`IA-01..10`, `SR-01..12`, `FR-01..12`, `ML-01..10`, `EV-01..13`,
`EG-01..14`, `CD-01..18`, `AI-01..12`, `WF-01..14`, and `QA-01..15`). Each
record names one severity, accountable role, validator, evidence locator,
status basis, and any remaining blocker. The only permitted statuses are:

- `PROVEN_CODE`
- `OBSERVED_SAMPLE`
- `PARTIAL`
- `EXTERNAL_BLOCKED`
- `NOT_STARTED_OR_UNVERIFIED`

The registry records `automation_state=configured_discovery_only`. The
read-only repository workflow schedules daily bounded discovery, validation,
and content-free triage, and retains only the declared audit artifacts for 30
days. This is not an observed run, issue-creation integration, completed human
triage process, or background self-improvement loop.

`config/continuous-audit-validators.json` is a bounded offline validator plan.
The runner validates nine cross-domain entrypoints, policy locators, the
repository workflow schedule and retention declarations, and all-false
network/database/service/release/mutation capabilities. It reports
`execution_state=configured_not_observed`; issue integration remains
`not_configured`, and the responsible role is declared without assigning a
named accountable person.

The separate `scripts/run_continuous_audit_validators.py` command executes that
checked-in plan manually or inside the repository workflow. Commands are
derived only from validator kind and locator, use an explicitly validated
absolute Python runtime with `-B`, receive a minimal environment, and retain
only bounded stdout/stderr hashes. Local verification uses the locked web
runtime; GitHub CI uses its pinned setup-python 3.11 runtime.

## Safe offline run

Use the locked web runtime and set bytecode suppression before Python starts:

```bash
AUDIT_OUTPUT_DIR="$(mktemp -d /tmp/globemind-continuous-audit.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  scripts/continuous_audit.py --output-dir "$AUDIT_OUTPUT_DIR"
```

`--output-dir` is mandatory. It must be an absolute, nonexistent or empty
directory outside the repository and outside
`/root/data/releases/globemind`. The runner creates
`continuous-audit.json` and `continuous-audit.md` with no-replace semantics;
it refuses an existing nonempty directory.

The runner reads only the registry, repository-relative validator/evidence
file metadata, local Git HEAD, and the clean/dirty worktree status. It never
reports changed path names and does not compute or claim a worktree content
hash. It does not import application code or access releases,
services, databases, credentials, external APIs, article bodies, or personal
information. It checks:

- exact 130-ID completeness and uniqueness;
- registry schema and the five-state contract;
- validator/evidence locator existence;
- declared evidence age;
- the explicit automation configuration state;
- the validator-plan schema, locators, workflow evidence, and safety capabilities.

Exit code `0` means the registry integrity checks completed, including when
honest open findings such as a dirty, unattested worktree remain. Exit
code `1` means a report was produced with an integrity failure. Exit code `2`
means the registry or requested output location violated the input/safety
contract.

This v2 core adds an explicit HEAD-versus-worktree identity boundary and
validates a bounded cross-domain plan. To execute its nine offline validators
without replacing prior evidence:

```bash
VALIDATOR_OUTPUT_DIR="$(mktemp -d /tmp/globemind-validator-run.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  scripts/run_continuous_audit_validators.py --output-dir "$VALIDATOR_OUTPUT_DIR"
```

The repository workflow provides daily discovery and 30-day artifact
retention. It then runs `scripts/continuous_audit_triage.py` over the two
content-free summaries. The triage receipt retains finding codes and validator
status only; it never retains finding details or validator stdout/stderr and
never creates an issue or sends an external message. Neither the core nor
validator runner observes CI execution or provides a historical trend
baseline, issue creation, completed human triage, or a named accountable
automation owner; those limitations remain explicit even after a manual pass.

`scripts/continuous_audit_trend.py` can compare two exact triage artifacts
offline. It reports only finding identities, validator status transitions,
validator scope changes, and registry status-count deltas. It refuses retained
content, reversed/future timestamps, changed registry item scope, symlinks,
hardlinks, release paths, repository output paths, and replacement of prior
evidence. It does not approve thresholds, create issues, send messages, finish
human triage, or accept a candidate/production release. The repository workflow
does not yet retrieve a prior run automatically, so the historical baseline
state remains unconfigured.

To reproduce the triage step after the two commands above:

```bash
TRIAGE_OUTPUT_DIR="$(mktemp -d /tmp/globemind-audit-triage.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  scripts/continuous_audit_triage.py \
  --audit-report "$AUDIT_OUTPUT_DIR/continuous-audit.json" \
  --validator-report "$VALIDATOR_OUTPUT_DIR/continuous-audit-validator-run.json" \
  --output-dir "$TRIAGE_OUTPUT_DIR"
```

Given a separately retained prior triage artifact, reproduce the descriptive
comparison without replacing evidence:

```bash
TREND_OUTPUT_DIR="$(mktemp -d /tmp/globemind-audit-trend.XXXXXX)"
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  scripts/continuous_audit_trend.py \
  --baseline-triage /absolute/path/to/prior/continuous-audit-triage.json \
  --current-triage "$TRIAGE_OUTPUT_DIR/continuous-audit-triage.json" \
  --output-dir "$TREND_OUTPUT_DIR"
```
