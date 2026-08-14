import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  sanitizeOpinionPayload,
  validateOpinionClaimContract,
} from '../src/features/sentiment/presentation.js'

const CORE_METRICS = [
  'weighted_stance_index',
  'weighted_stance_change_24h',
  'article_volume_change_pct',
  'article_count',
  'source_count',
  'event_family_count',
  'positive_stance_share_pct',
  'negative_stance_share_pct',
  'neutral_stance_share_pct',
  'target_weighted_stance_index',
  'negative_stance_pressure_index',
  'positive_stance_support_index',
]
const CORE_PATHS = {
  weighted_stance_index: ['summary.current_index'],
  weighted_stance_change_24h: ['summary.change_24h'],
  article_volume_change_pct: ['summary.growth_pct'],
  article_count: ['summary.article_count'],
  source_count: ['summary.source_count'],
  event_family_count: ['summary.family_count'],
  positive_stance_share_pct: ['summary.positive_pct'],
  negative_stance_share_pct: ['summary.negative_pct'],
  neutral_stance_share_pct: ['summary.neutral_pct'],
  target_weighted_stance_index: ['target_indices.CN.value'],
  negative_stance_pressure_index: ['target_indices.NEG.value'],
  positive_stance_support_index: ['target_indices.POS.value'],
}

function fixture() {
  const trust = {
    schema_version: 'opinion-trust-v1',
    status: 'ready',
    trust_status: 'trusted',
    is_computable: true,
    computability: 'computable',
    display_mode: 'current',
    reason_codes: [],
    cutoff_date: '2026-08-09',
    freshness: { state: 'fresh', age_days: 0, maximum_age_days: 2 },
    coverage: {
      state: 'sufficient', article_count: 10, source_count: 3,
      minimum_articles: 10, minimum_sources: 3,
    },
    model_version: 'stance-model-v1',
    method_version: 'stance-method-v1',
    source_status: 'available',
    snapshot_id: `opinion-${'a'.repeat(64)}`,
    model: { version: 'stance-model-v1' },
    method: { version: 'stance-method-v1' },
    source: {
      id: 'public.china_opinion_article_scores',
      status: 'available',
      cutoff_date: '2026-08-09',
    },
    snapshot: {
      id: `opinion-${'a'.repeat(64)}`,
      evaluated_on: '2026-08-09',
      cutoff_date: '2026-08-09',
      filters: { days: 30 },
    },
  }
  const claims = CORE_METRICS.map((metric, index) => ({
    claim_id: `opinion-claim-${index.toString(16).padStart(64, '0')}`,
    metric,
    output_paths: CORE_PATHS[metric],
    identity: {
      metric,
      slice: {
        population: 'china_relevant_direct_articles',
        window_days: 30,
        ...({
          target_weighted_stance_index: { target: 'CN' },
          negative_stance_pressure_index: { target: 'NEG' },
          positive_stance_support_index: { target: 'POS' },
        }[metric] || {}),
      },
      model_version: trust.model_version,
      method_version: trust.method_version,
      data_cutoff: trust.cutoff_date,
      snapshot_id: trust.snapshot_id,
      source_id: 'public.china_opinion_article_scores',
    },
    claim_state: 'derived_not_verified',
    reason_code: 'DERIVED_VALUE_NOT_SOURCE_VERIFIED',
    source_truth_state: 'not_verified',
    citation_locator: null,
    citation_status: 'unavailable',
    citation_reason_code: 'SAFE_CITATION_LOCATOR_UNAVAILABLE',
  }))
  return {
    summary: {
      current_index: 12.5,
      change_24h: 1.5,
      growth_pct: 4,
      article_count: 10,
      source_count: 3,
      family_count: 0,
      positive_pct: 40,
      negative_pct: 30,
      neutral_pct: 30,
    },
    target_indices: [
      { label: 'CN', value: 12.5, trend_values: [9, 12.5], state: 'positive' },
      { label: 'NEG', value: -30, trend_values: [], state: 'negative' },
      { label: 'POS', value: 40, trend_values: [], state: 'positive' },
    ],
    trust,
    meta: { trust: structuredClone(trust) },
    claim_contract: {
      schema_version: 'opinion-derived-claim-contract-v1',
      status: 'complete',
      reason_codes: [],
      claims,
      max_claims: 48,
    },
  }
}

test('frontend accepts only the bounded no-citation claim contract bound to trust identity', () => {
  const payload = fixture()
  assert.deepEqual(validateOpinionClaimContract(payload.claim_contract, payload.trust, payload), {
    valid: true,
    reasonCodes: [],
  })
  assert.equal(sanitizeOpinionPayload(payload, {
    now: Date.UTC(2026, 7, 9),
    requireClaimContract: true,
  }).summary.current_index, 12.5)

  for (const mutate of [
    (value) => { value.claim_contract.claims[0].citation_locator = 'db://secret' },
    (value) => { value.claim_contract.claims[0].identity.model_version = 'forged' },
    (value) => { value.claim_contract.claims[0].identity.slice.feedback_text = 'trust me' },
    (value) => { value.claim_contract.claims.push(...value.claim_contract.claims.slice(0, 40)) },
    (value) => { [value.target_indices[0], value.target_indices[1]] = [value.target_indices[1], value.target_indices[0]] },
    (value) => { value.target_indices[0].label = 'NEG' },
    (value) => { value.target_indices[0].value = 999 },
    (value) => {
      const claim = value.claim_contract.claims.find(
        (item) => item.metric === 'target_weighted_stance_index',
      )
      claim.claim_state = 'explicit_unknown'
      claim.reason_code = 'DERIVED_VALUE_UNAVAILABLE'
    },
  ]) {
    const damaged = fixture()
    mutate(damaged)
    const sanitized = sanitizeOpinionPayload(damaged, {
      now: Date.UTC(2026, 7, 9),
      requireClaimContract: true,
    })
    assert.equal(sanitized.trust.is_computable, false)
    assert.equal(sanitized.summary.current_index, null)
    assert.equal(sanitized.summary.growth_pct, null)
    assert.ok(sanitized.trust.reason_codes.includes('INVALID_DERIVED_CLAIM_CONTRACT'))
  }
})

test('overview presentation explicitly requires the derived claim contract', async () => {
  const page = await readFile(
    new URL('../src/views/sentimentAnalysis.vue', import.meta.url),
    'utf8',
  )
  assert.match(page, /sanitizeOpinionPayload\(overviewData\.value,\s*\{[\s\S]*?requireClaimContract:\s*true/)
  assert.match(page, /sanitizeOpinionPayload\(outcome\.value,\s*\{\s*requireClaimContract:\s*true\s*\}\)/)
})

test('unbound top-event counts fail closed instead of remaining visible without claims', () => {
  const damaged = fixture()
  damaged.top_event = {
    chain_id: 'unsafe chain / prose',
    avg_stance: 0.7,
    article_count: 21,
    china_articles: 13,
  }
  const sanitized = sanitizeOpinionPayload(damaged, {
    now: Date.UTC(2026, 7, 9),
    requireClaimContract: true,
  })
  assert.equal(sanitized.trust.is_computable, false)
  assert.ok(sanitized.trust.reason_codes.includes('INVALID_DERIVED_CLAIM_CONTRACT'))
  assert.equal(sanitized.top_event.avg_stance, null)
  assert.equal(sanitized.top_event.article_count, null)
  assert.equal(sanitized.top_event.china_articles, null)
})
