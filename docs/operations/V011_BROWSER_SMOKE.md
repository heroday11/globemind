# V0.11 Browser Smoke

`deploy/browser_smoke.py` is the browser-level release-candidate gate. It is
separate from the Web Python runtime and from `frontend/src`; Playwright remains
an optional validation dependency and is imported only when the tool runs.

## Safety contract

- The target must be an explicitly supplied literal loopback origin with a
  port, such as `http://127.0.0.1:18091`. Public hosts, `localhost`, wildcard
  addresses, credentials, path prefixes, queries, and fragments are rejected.
- The tool does not start or stop a server, import application code, connect to
  a database, use a real account, or call production APIs.
- Browser requests outside the exact candidate origin are blocked. Within the
  origin, only registered page documents and fixed static-asset prefixes may be
  fetched directly with `GET`/`HEAD`; all dynamic and non-idempotent traffic is
  blocked. Encoded `/api` paths cannot bypass the in-memory stubs. WebSocket
  connections are intercepted before server connection and closed. HTTP 3xx
  responses and undeclared API requests fail the gate.
- Candidate HTML and static assets are loaded normally. `/api/**` requests are
  answered by deterministic in-memory fixtures. A fixed non-secret dummy token
  is used only inside the browser context.
- Every desktop/mobile observation carries the same fixed, non-secret fixture
  snapshot ID and generated-at timestamp. The report fails if a registered page is absent from either
  viewport or fixture identity drifts. Declared semantic probes compare
  normalized visible-text SHA-256 across viewports and verify explicit
  `alert`/`status`/`group`, live-region, atomic, and label-presence semantics;
  only hash, character count, role metadata, and label presence are retained.
  This proves fixture pairing and those declared probes, not all displayed
  values, accessible names, screen-reader behavior, or candidate API consistency.
- The evidence path must be absolute and new or empty, with no symbolic-link
  component. The tool never appends to an earlier evidence set.
- Evidence contains sanitized paths, status codes, selector names, console/page
  error summaries, viewport/DOM dimensions, bounded redirect verdicts, and
  screenshots. It never stores URL userinfo/query values, request headers, API
  response bodies, credentials, or the dummy token.

## Coverage

Both `1440x900` desktop and `390x844` mobile contexts check five high-risk
business pages with nine declared semantic probes:

1. Root and login pages.
2. The public country-profile catalog fail-closed error state using an
   intentionally invalid schema fixture; valid schema rendering remains in the
   frontend contract suite. Its visible failure message is a declared semantic
   probe and must have the same normalized-text hash in both viewports.
3. Ground News business metrics and freshness semantics.
4. Pipeline Monitor empty-trend and status-ribbon semantics.
5. Model Assurance status-grid and empty-state semantics.
6. Entity Governance status-grid and fail-closed error semantics.
7. Unauthenticated Pipeline Monitor redirect to the login page.
8. Authenticated route chunks and root selectors for Data Search, Story Graph,
   Ground News, Assistant, Pipeline Monitor, and Sentiment.

A check fails for a blank page, a missing/invisible root selector, missing or
drifted declared accessibility semantics, the wrong
final route, a protected route returning to login, horizontal document
overflow, obvious overlap between top-level layout siblings, console/page
errors, critical static-resource errors, cross-origin traffic, or redirects.

## Candidate run

Use a dedicated browser-validation virtual environment. Do not install
Playwright into `/root/data/python-runtimes/globemind-web/*` or add it to Web
runtime requirements.

```bash
python -m pip install playwright
python -m playwright install chromium

python -B deploy/browser_smoke.py \
  --base-url http://127.0.0.1:18091 \
  --output-dir /root/data/evidence/globemind/v0.11-browser-smoke
```

To use a system or pre-provisioned browser, add an explicit executable:

```bash
python -B deploy/browser_smoke.py \
  --base-url http://127.0.0.1:18091 \
  --chromium-executable /absolute/path/to/chromium \
  --output-dir /root/data/evidence/globemind/v0.11-browser-smoke
```

Run this only after the candidate is already listening on its isolated
loopback port. The normal HTTP candidate acceptance gate should pass first.
The browser gate does not create, restart, promote, or roll back a release.

Exit codes:

- `0`: every browser check passed.
- `1`: the candidate ran but one or more acceptance checks failed.
- `2`: invalid configuration or missing/broken Playwright runtime.

The machine-readable v2 result is `browser-smoke.json`; screenshots are under
`screenshots/`. Treat the entire directory as immutable release evidence.

## Offline verification

The unit suite does not import Playwright or open a socket:

```bash
PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B -m pytest \
  backend/tests/test_browser_smoke.py -q

/opt/conda/envs/Globemind_env/bin/ruff check \
  deploy/browser_smoke.py backend/tests/test_browser_smoke.py
```
