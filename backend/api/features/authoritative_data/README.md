# Authoritative data connector contract

This package provides the first bounded, read-only connector layer for four
authorities. A configured connector is not reported as live. Only a validated
response can produce `available=true`, and every response carries cache
`cutoff`, `last_success`, `license`, `coverage`, `source`, `version`, and
`available` evidence.

It also exposes `GET /api/authoritative-data/country-profiles/catalog`, a
schema-only catalog for the future standard country profile. The response is
deliberately fixed at `available=false`, `operational_state=not_configured`,
`live_checked=false`, and `profiles=[]`. It performs no network or data-store
read and contains no country facts.

## Country profile schema boundary

The catalog publishes a versioned, ordered inventory for overview,
institutions, politics, law and policy, economy, society, security, external
relations, environment, and evidence governance. Each field has a stable
namespaced ID, value kind, cardinality, publish requirement, and explicit
evidence requirement. Future profiles are required to use ISO 3166-1 alpha-2
country identifiers and content-addressed GlobeMind URNs.

This descriptor is not a future fact or snapshot payload contract. Before any
profile can be added, that separate contract must bound collections and JSON
depth/size, require unique identifiers and finite numeric values, reject
duplicate fields, validate absolute HTTPS source locators, and fail closed on
future source cutoffs or expired/future reviews. Publication also requires a
source authority and cutoff, a verified or restricted license state, an
assigned `country-data-stewardship` owner, and an unexpired human approval.

The response always carries these blocker reason codes:

- `PILOT_COUNTRIES_NOT_SELECTED`
- `COUNTRY_PROFILES_NOT_CONFIGURED`
- `SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED`
- `LICENSE_EVIDENCE_NOT_CONFIGURED`
- `OWNER_AND_REVIEW_NOT_CONFIGURED`

Consequently this schema slice does **not** complete CD-01: there is still no
country page, no governed country fact set, no assigned country owner, and no
country-researcher acceptance evidence.

## Institution and governance schema boundary

`GET /api/authoritative-data/country-profiles/institutions/catalog` exposes a
separate v1 inventory for constitutional order, formal and observed power,
administrative systems, and evidence governance. It is also schema-only: the
response fixes `available=false`, `facts=[]`, `live_checked=false`, and the
live-data, owner, reviewer, licence, and country-scope states to
`not_configured`. The endpoint does not read a database or call a network
source.

Every future field requires a citation, temporal scope, licence evidence, an
owner, and a human reviewer. Legal claims require official primary evidence;
observed-power claims require independently corroborated observation evidence;
and comparisons require separate de-jure and de-facto bindings. These are
future publication gates, not evidence already held by GlobeMind. No country,
constitutional arrangement, power relation, or administrative fact is bundled
or inferred, so this slice cannot complete CD-02 without an approved pilot,
official sources, reuse rights, a named owner, and qualified country review.

## First-party API references

- World Bank Indicators API v2:
  [basic call structure](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures).
  The adapter calls
  `https://api.worldbank.org/v2/country/{country}/indicator/{indicator}` with
  JSON output, WDI source `2`, one page, and a maximum of 100 records.
- IMF DataMapper API v2:
  [official DataMapper API page](https://www.imf.org/external/datamapper/api/).
  The adapter calls
  `https://www.imf.org/external/datamapper/api/v2/{indicator}/{entities}` and
  locally reapplies entity and period filters before accepting records.
- United Nations Statistics Division SDG API v1:
  [official Swagger contract](https://unstats.un.org/SDGAPI/swagger/).
  The adapter calls
  `https://unstats.un.org/SDGAPI/v1/sdg/Series/Data` with one series, one M49
  area, page `1`, and a maximum page size of 50.
- Crossref REST API v1:
  [official REST API documentation](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
  and [access/rate-limit guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/).
  The adapter calls `https://api.crossref.org/v1/works`, returns no more than
  20 records, caches results, and never emits abstracts, references, or
  full-text links.

## Network and trust boundary

- Only the four checked-in HTTPS hosts are accepted.
- Redirects and ambient proxy/environment inheritance are disabled.
- Connect/read/write/pool timeouts and a 1 MiB decompressed JSON ceiling are
  enforced.
- Only HTTP 200 JSON responses with the expected source-specific shape are
  normalized.
- Duplicate JSON keys, duplicate normalized record IDs, missing cutoff
  evidence, empty results, and coverage mismatches fail closed.
- Stale cache metadata can explain a prior success, but stale records are not
  returned as available after a failed refresh.
- Crossref title text is sent to Crossref for the requested lookup but is not
  retained in the cache contract; only its SHA-256 digest and length are kept.
  Personal or confidential text must not be submitted as a bibliographic query.
- Actual upstream query routes require an authenticated GlobeMind user. The
  public catalog is static registration evidence and always reports
  `operational_state=not_observed`.

## Licensing boundary

API accessibility is not treated as a reusable-data license. World Bank and
Crossref registrations are marked `restricted` because their first-party
terms describe dataset/record-level exceptions. IMF and UNSD registrations
remain `unknown` until a dataset-specific reuse basis is approved. These
states intentionally block any claim that the connectors alone make a formal
research release eligible.
