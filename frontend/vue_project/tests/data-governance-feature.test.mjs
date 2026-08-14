import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  DATA_CATALOG_ENDPOINT,
  catalogBlockerLabel,
  fetchDataCatalog,
  normalizeDataCatalog,
} from '../src/governance/dataCatalog.js'


function completeRecord(overrides = {}) {
  const evidence = [{
    reference: 'docs/governance/evidence.json',
    claim: 'verified fixture evidence',
    status: 'verified',
  }]
  return {
    record_id: 'dataset.fixture',
    kind: 'dataset',
    title: 'Fixture',
    description: 'Complete record',
    owner: {
      assignment_status: 'named',
      owner_id: 'named-owner',
      display_name: 'Named owner',
      evidence,
    },
    version: {
      status: 'verified',
      value: 'v1',
      scheme: 'immutable-snapshot',
      change_log_ref: 'docs/governance/change-log.md',
      evidence,
    },
    operational: {
      state: 'available',
      evidence_status: 'verified',
      observed_at: '2026-08-09T12:00:00Z',
      source: 'verified-capability-probe',
      reason_codes: [],
    },
    freshness: {
      state: 'live',
      evidence_status: 'verified',
      cutoff_at: '2026-08-09T10:00:00Z',
      last_success_at: '2026-08-09T10:10:00Z',
      lag_hours: 2,
      sla_hours: 48,
      source: 'verified-watermark',
      reason_codes: [],
    },
    coverage: {
      status: 'verified',
      scope: 'all fixture records',
      missing_dimensions: [],
      metrics: { record_count: 1 },
      evidence,
    },
    license: {
      status: 'verified',
      identifier: 'license-v1',
      usage_scope: 'test',
      terms_ref: 'docs/governance/license.md',
      retention_policy: 'immutable fixture retention',
      evidence,
    },
    quality: {
      status: 'passed',
      evaluated_at: '2026-08-09T10:00:00Z',
      evaluation_version: 'fixture-eval-v1',
      metrics: { acceptance_rate: 1 },
      evidence,
    },
    provenance: {
      status: 'verified',
      capture_timestamp_status: 'verified',
      web_snapshot_status: 'verified',
      content_hash_status: 'verified',
      revision_tracking_status: 'verified',
      parser_version: 'fixture-parser-v1',
      evidence,
    },
    schema: {
      status: 'verified',
      record_identifier: 'fixture.id',
      schema_ref: 'docs/governance/schema.json',
      data_dictionary_ref: 'docs/governance/dictionary.md',
      mapping_refs: ['docs/governance/mapping.json'],
      change_log_ref: 'docs/governance/schema-changes.md',
      evidence,
    },
    status: {
      state: 'eligible',
      release_eligible: true,
      research_ready: true,
      reason_codes: [],
    },
    ...overrides,
  }
}

function payload(records) {
  return {
    schema_version: 'data-governance-catalog-v1',
    contract_version: '1.0.0',
    available: true,
    generated_at: '2026-08-09T12:00:00Z',
    records,
  }
}

test('catalog normalizer recomputes readiness and rejects contradictory eligible claims', () => {
  const complete = normalizeDataCatalog(payload([completeRecord()]))
  assert.equal(complete.available, true)
  assert.equal(complete.summary.eligibleCount, 1)
  assert.equal(complete.summary.formalReleaseStatus, 'ready')

  const contradicted = normalizeDataCatalog(payload([
    completeRecord({ license: { status: 'unknown' } }),
  ]))
  assert.equal(contradicted.summary.eligibleCount, 0)
  assert.equal(contradicted.records[0].status.state, 'blocked')
  assert.ok(contradicted.records[0].status.reasonCodes.includes('LICENSE_UNKNOWN'))
  assert.equal(catalogBlockerLabel('LICENSE_UNKNOWN'), '许可与使用边界未知')

  const technicallyUnknown = normalizeDataCatalog(payload([
    completeRecord({
      operational: {
        state: 'unknown',
        evidence_status: 'unknown',
        observed_at: '2026-08-09T12:00:00Z',
        source: null,
        reason_codes: ['OPERATIONAL_EVIDENCE_MISSING'],
      },
    }),
  ]))
  assert.equal(technicallyUnknown.summary.eligibleCount, 0)
  assert.equal(technicallyUnknown.records[0].operational.label, '技术状态未知')
  assert.ok(technicallyUnknown.records[0].status.reasonCodes.includes('OPERATIONAL_UNKNOWN'))
})

test('catalog normalizer fails closed for missing contract fields or damaged records', () => {
  const wrongVersion = normalizeDataCatalog({
    schema_version: 'old-contract',
    available: true,
    records: [],
  })
  assert.equal(wrongVersion.available, false)
  assert.equal(wrongVersion.summary.formalReleaseStatus, 'blocked')

  const damaged = normalizeDataCatalog(payload([{ kind: 'dataset' }]))
  assert.equal(damaged.available, false)
  assert.match(damaged.error, /无法验证/)
})

test('catalog fetch uses the public governance endpoint and no-store semantics', async () => {
  let request
  const result = await fetchDataCatalog({
    endpoint: DATA_CATALOG_ENDPOINT,
    fetchImpl: async (url, init) => {
      request = { url, init }
      return { ok: true, json: async () => payload([completeRecord()]) }
    },
  })
  assert.equal(request.url, '/api/data-governance/catalog')
  assert.equal(request.init.method, 'GET')
  assert.equal(request.init.cache, 'no-store')
  assert.equal(result.summary.eligibleCount, 1)

  await assert.rejects(
    fetchDataCatalog({
      endpoint: DATA_CATALOG_ENDPOINT,
      fetchImpl: async () => ({ ok: false }),
    }),
    /暂不可用/,
  )
})

test('sources governance page exposes an accessible fail-closed catalog panel', async () => {
  const page = await readFile(
    new URL('../src/views/PublicGovernance.vue', import.meta.url),
    'utf8',
  )
  assert.match(page, /fetchDataCatalog/)
  assert.match(page, /route\.path === '\/sources'/)
  assert.match(page, /aria-labelledby="data-catalog-title"/)
  assert.match(page, /正式门禁受阻/)
  assert.match(page, /负责人、版本、时效、覆盖、许可、质量、溯源或 Schema/)
  assert.match(page, /技术状态/)
  assert.match(page, /record\.status\.reasonCodes/)
})
