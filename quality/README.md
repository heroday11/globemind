# Quality contracts

Status: current quality policy navigation
Scope: repository checks, ratchets, data admission and runtime-path policy

`quality/` stores reviewed inputs to repository-level checks. These files are
policy and evidence boundaries, not production configuration.

- `import-boundaries-baseline.json` is a ratchet: new violations fail, while
  reducing existing debt requires an explicit baseline update.
- `frontend-budgets.json` and `frontend-ratchet.json` constrain the main Vue
  bundle and its tracked trend.
- `secret-scan-allowlist.json` contains only reviewed, non-secret matches.
- `data-assets-manifest.json` classifies tracked data, rejects generated/local
  runtime trees, and requires a provenance/owner entry for large data files.
- `runtime-path-policy.json` records a future compatibility migration for
  source-worktree logs/state. Its `activation` is intentionally
  `not-active`; it does not change service defaults.

Run the read-only repository check directly:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B scripts/ci/check_repository_hygiene.py
```

Do not add a broad allowlist to make a gate pass. New data should first be
classified as a small fixture/reference, an externally managed artifact, or a
local generated output. For large or licensed material, record owner,
provenance, retention and license status before requesting review.
