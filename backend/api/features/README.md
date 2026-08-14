# Backend feature catalog

Status: current developer navigation
Scope: public feature boundaries under `backend/api/features/`
Source of truth: [`ops/features/registry.json`](../../../ops/features/registry.json)

This directory contains the backend's vertical business capabilities. The package
`__init__.py` in each feature is its public facade. Code outside that feature must
import the facade, not its internal `application`, `repository`, `service`, or storage
modules. HTTP route compatibility adapters remain under `api/routes/` while the
internal migration continues.

## Catalog

| Directory | Responsibility | Focused contract test |
| --- | --- | --- |
| [`assistant/`](assistant/) | Assistant schedules, workspaces, reports, citations and privacy exports | [`test_assistant_schedule_feature.py`](../../tests/test_assistant_schedule_feature.py) |
| [`authoritative_data/`](authoritative_data/) | Bounded country, institution and primary-document connectors | [`test_authoritative_data_connectors.py`](../../tests/test_authoritative_data_connectors.py) |
| [`dashboard/`](dashboard/) | Dashboard aggregates, article presentation and feature health | [`test_dashboard_feature.py`](../../tests/test_dashboard_feature.py) |
| [`data_governance/`](data_governance/) | Data and model governance catalog | [`test_data_governance_catalog.py`](../../tests/test_data_governance_catalog.py) |
| [`entity_governance/`](entity_governance/) | Temporal entities, evidence, aliases and approval ledger | [`test_entity_governance_feature.py`](../../tests/test_entity_governance_feature.py) |
| [`evidence/`](evidence/) | Article claim evidence and append-only snapshots | [`test_evidence_chain.py`](../../tests/test_evidence_chain.py) |
| [`financial/`](financial/) | Financial alerts, trust gates and triage state | [`test_financial_alert_feature.py`](../../tests/test_financial_alert_feature.py) |
| [`graph_briefing/`](graph_briefing/) | Graph briefing contracts, repository and health | [`test_graph_briefing_feature.py`](../../tests/test_graph_briefing_feature.py) |
| [`ground_news/`](ground_news/) | Ground News source profiles and readiness | [`test_ground_news_source_profile_contract.py`](../../tests/test_ground_news_source_profile_contract.py) |
| [`identity/`](identity/) | Authentication, MFA, privacy rights and account repositories | [`test_identity_feature.py`](../../tests/test_identity_feature.py) |
| [`legacy_retirement/`](legacy_retirement/) | Explicit legacy endpoint retirement records | [`test_legacy_endpoint_retirement.py`](../../tests/test_legacy_endpoint_retirement.py) |
| [`model_assurance/`](model_assurance/) | Model evaluation evidence, drift and release assurance | [`test_model_assurance.py`](../../tests/test_model_assurance.py) |
| [`operations/`](operations/) | Runtime inventory, heartbeat, history and maintenance evidence | [`test_operations_heartbeat_feature.py`](../../tests/test_operations_heartbeat_feature.py) |
| [`opinion/`](opinion/) | Opinion/sentiment semantics, analytics, feedback and trust | [`test_opinion_feature.py`](../../tests/test_opinion_feature.py) |
| [`research_workflow/`](research_workflow/) | Versioned projects, snapshots, comparisons and reviewed artifacts | [`test_research_workflow_feature.py`](../../tests/test_research_workflow_feature.py) |
| [`search/`](search/) | Search contracts, evaluation, qrels, receipts and snapshots | [`test_search_application_feature.py`](../../tests/test_search_application_feature.py) |
| [`service_level/`](service_level/) | Bounded service-level measurement and evidence ledger | [`test_service_level_feature.py`](../../tests/test_service_level_feature.py) |
| [`story_graph/`](story_graph/) | Story graph claims, relations, metrics and presentation | [`test_story_graph_feature_boundary.py`](../../tests/test_story_graph_feature_boundary.py) |

## Change workflow

1. Confirm ownership, routes, dependencies and all contract tests in the registry.
2. Add behavior behind the feature facade; keep protocol conversion in `api/routes/`.
3. Run the focused test above, then the other tests listed by the registry record.
4. Run `make quality` before submitting a pull request.
5. Update this catalog only when responsibility or the recommended first test changes.

Do not create a README in every small feature by default. Add one only when a feature
has a non-obvious state machine, external connector protocol, migration procedure or
security boundary that cannot be explained by this catalog and its contracts.
