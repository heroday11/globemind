# Vue feature catalog

Status: current developer navigation
Scope: public feature boundaries under `frontend/vue_project/src/features/`
Source of truth: [`ops/features/registry.json`](../../../../ops/features/registry.json)

Every directory exposes its supported surface through `index.js`. Pages and other
features must import that public entry instead of reaching into private API, model or
component files. Route-level compatibility pages remain under `src/views/` while the
incremental migration continues.

## Catalog

| Directory | Responsibility | Focused contract test |
| --- | --- | --- |
| [`assistant/`](assistant/) | Assistant chat, workspaces, reports and briefing UI contracts | [`assistant-feature.test.mjs`](../../tests/assistant-feature.test.mjs) |
| [`collections/`](collections/) | User collection models used by identity/account surfaces | [`user-collections-accessibility-feature.test.mjs`](../../tests/user-collections-accessibility-feature.test.mjs) |
| [`country-profiles/`](country-profiles/) | Bounded country and institution profile presentation | [`country-profile-catalog-feature.test.mjs`](../../tests/country-profile-catalog-feature.test.mjs) |
| [`entity-governance/`](entity-governance/) | Entity evidence and governance workbench contracts | [`entity-governance-feature.test.mjs`](../../tests/entity-governance-feature.test.mjs) |
| [`evidence/`](evidence/) | Evidence-chain DTOs and ledger presentation | [`evidence-chain-feature.test.mjs`](../../tests/evidence-chain-feature.test.mjs) |
| [`ground-news/`](ground-news/) | Ground News sources, timelines and presentation rules | [`ground-news-feature.test.mjs`](../../tests/ground-news-feature.test.mjs) |
| [`model-assurance/`](model-assurance/) | Model evaluation and release-assurance surfaces | [`model-assurance-feature.test.mjs`](../../tests/model-assurance-feature.test.mjs) |
| [`operations/`](operations/) | Health, freshness and pipeline-monitor state | [`operations-feature.test.mjs`](../../tests/operations-feature.test.mjs) |
| [`research-workflow/`](research-workflow/) | Research projects, reviewed downloads and monitoring | [`research-workflow-feature.test.mjs`](../../tests/research-workflow-feature.test.mjs) |
| [`search/`](search/) | Search requests, result DTOs, storage and hit fields | [`search-feature.test.mjs`](../../tests/search-feature.test.mjs) |
| [`sentiment/`](sentiment/) | Sentiment semantics, trends, cache and presentation | [`sentiment-feature.test.mjs`](../../tests/sentiment-feature.test.mjs) |
| [`story-graph/`](story-graph/) | Story graph query state, layout and claim presentation | [`story-graph-feature.test.mjs`](../../tests/story-graph-feature.test.mjs) |
| [`translation/`](translation/) | Dashboard translation provenance and display model | [`translation-provenance-feature.test.mjs`](../../tests/translation-provenance-feature.test.mjs) |

## Change workflow

1. Find the owning feature and page in the registry before editing a view.
2. Put reusable business behavior in the feature; keep the route page as composition.
3. Export only the supported contract from `index.js`.
4. Run the focused Node test, then `npm run test:web` and `npm run typecheck:web`.
5. Run `make quality` before submitting a pull request.

The independent React financial terminal is documented separately in
[`frontend/financial-terminal/README.md`](../../../financial-terminal/README.md).
