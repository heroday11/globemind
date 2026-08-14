# Model assurance contract

This feature is a fail-closed, manifest-only foundation for binary model
evaluation, calibration, drift detection, and rollback recommendations. It does
not run a model, fetch a dataset, inspect the bytes named by a dataset SHA-256,
or verify an external review artifact. A submitted manifest is an attestation;
the API labels derived output as `evidence_status=manifest_only` and must not be
presented as an independently observed benchmark run.

## API and authorization

The standalone router namespace is `/api/model-assurance`:

- authenticated GET: `/catalog`, `/status`, `/surfaces`,
  `/generative-evaluation/surfaces`, `/evaluations`, and
  `/evaluations/{evaluation_id}`;
- administrator-only POST: `/evaluations`.

POST accepts only versioned inputs: dataset identity and SHA-256, dataset
cutoff, model and method versions, decision threshold, overall and
country/language/topic sufficient statistics, coverage declarations, explicit
release/drift thresholds, an optional independent-review attestation, and an
optional exact hash reference to a prior entry. A separate evaluation-integrity
declaration records whether labels are human gold, silver, synthetic, or
unreviewed; whether the evaluated partition is a holdout; its access state; the
bounded set of development-dataset digests; and a hash-addressed separation
review. Client-supplied derived metrics are forbidden by the schema.
The POST boundary also rejects duplicate JSON keys and non-finite JSON numbers
before appending, so ambiguous wire representations cannot be normalized into
the ledger.

`GET /surfaces` is a separate, content-free inventory of six bounded public
output surfaces: opinion aggregates, article opinion detail, interactive
assistant output, scheduled reports, derived story-graph relations, and
financial derived indicators. Its source locators are covered by a repository
gate, but that static coverage is not runtime deployment evidence. The endpoint
does not read environment/provider configuration, model paths, prompts, article
bodies, generated responses, or secrets. Where a response contract exposes a
model-version field, its current runtime value remains `unknown`; where the
field is absent it is `not_available`. Deployment time and change notes remain
`not_available` for every surface until a separately verified runtime
attestation is introduced. The route is authenticated, read-only, and returned
with `Cache-Control: private, no-store`.

`GET /generative-evaluation/surfaces` is a narrower, content-free inventory for
offline generative-output boundary fixtures. Interactive and scheduled
assistant output is free-form Markdown, so the contract keeps per-claim
citation coverage `unknown` and rejects any manifest that fabricates claim
records for those surfaces. A manifest may carry a body-free projection of
research-citation-export-v3 claim bindings: exact hash-shaped claim/citation
IDs, statement digests, supporting/opposing citation bindings, and unresolved-
gap disposition metadata. The evaluator derives syntactic counts against the
manifest's declared citation-ID inventory, but does not read the artifact named
by its digest or independently replay the observations. Results therefore say
`manifest_attested_not_independently_observed`; they never say the fixture or
surface passed. The projection counts do not verify source truth, semantic
entailment, factuality, or generation quality. Synthetic, silver-manifest, and
human-gold-manifest-claim tiers remain distinct; none establishes observed
human gold or permits a hallucination-rate or quality conclusion. Broad
semantic prompt injection, non-stream interactive truncation, scheduled-
provider truncation, and provider-failure artifact absence remain explicit
open findings because the current bounded gates do not observe them. The
endpoint is authenticated, read-only, and returned with `Cache-Control:
private, no-store`.

## Server-derived metrics

For confusion counts `TP`, `FP`, `TN`, and `FN`, the server computes:

- precision = `TP / (TP + FP)`;
- recall = `TP / (TP + FN)`;
- F1 = `2 TP / (2 TP + FP + FN)`;
- Brier = `(sum(p^2) - 2 sum(p for positive labels) + positives) / N`;
- ECE = `sum((N_bin / N) * abs(mean_probability_bin - positive_rate_bin))`.

Every calibration bin therefore carries bounded sufficient statistics rather
than a claimed Brier or ECE value. The contract rejects non-finite values,
empty samples, impossible probability moments, gaps or overlaps in bin
boundaries, calibration totals that disagree with confusion counts, duplicate
strata, and complete strata declarations that do not partition both overall
confusion counts and calibration statistics.

## Release and drift gate

`release_eligible` defaults to false. It can become true only when all three
coverage dimensions are exactly represented, sample and metric thresholds are
met, dataset governance references and an independently reviewed gold-standard
claim are present, the label source is human gold, a non-overlapping holdout is
declared sealed, an independent approved review has an explicit unexpired
`valid_until`, and drift against a compatible, hash-addressed, qualified
baseline stays within explicit tolerances. Silver/synthetic labels remain
mechanically computable but cannot cross the gold release gate.

The first otherwise complete evaluation is still blocked because it has no
baseline. It may serve as a bootstrap baseline for a later candidate only when
`BASELINE_NOT_PROVIDED` is its sole blocker. A regressing candidate recommends
`rollback_to_baseline` only when that baseline is qualified; otherwise the
recommendation remains `hold_release`.

Baseline comparison requires the complete dataset governance identity to match,
including dataset and label-schema versions plus annotation and provenance
references. It also requires the same declared coverage, overall and per-slice
sample/positive-label cohorts, and calibration-bin scheme. A digest/cutoff
match alone is insufficient. Stored results remain historical records of the
gate at submission time; current summaries and exact-match quality projection
fail closed after the candidate review, its baseline review, or any ancestor
baseline review expires. The browser labels the stored detail as an admission-
time historical result and shows the dynamically projected gate separately.
New descendants are blocked at admission when an ancestor review has already
expired, and a candidate timestamp cannot precede the referenced baseline's
stored timestamp. Append timestamps cannot move backwards relative to the
ledger high-water mark; current projections fail closed if the service clock
falls behind that mark. Review IDs are single-use within the ledger; an exact
attestation identity cannot be replayed for another evaluation.

An empty ledger reports `available=false`, `operational_state=not_observed`,
`gold_standard_state=not_observed`, and `release_status=blocked`. Submitting a
manifest does not turn its external evidence references into locally verified
facts; `manifest_attested` is deliberately distinct from observed evidence.

## Persistence

`MODEL_ASSURANCE_ROOT` selects the absolute writable root and defaults to
`/root/data/web/model_assurance`. It must stay outside
`/root/data/releases/globemind` and may not contain symbolic-link path
components. GET on a missing root is read-only and returns the blocked empty
state.

Entries are immutable, bounded JSON files under `entries/`, appended with
no-replace semantics and linked by `previous_entry_sha256`. Readers validate
the complete chain and deterministically recompute all stored results. A
shared/exclusive file lock prevents readers from observing an in-progress
append. The root therefore requires a local POSIX filesystem with reliable
`flock`, hard-link/unlink, and `fsync` behavior; unsupported or shared
filesystem semantics have not been certified. This detects accidental or local
file tampering but is not a substitute for external WORM storage, signatures,
key custody, or a separately operated review system.
