import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  opinionSemanticMethodSnapshot,
  opinionSemanticSnapshot,
  semanticTrendProjection,
} from '../src/features/sentiment/semantics.js'
import { sanitizeOpinionPayload } from '../src/features/sentiment/presentation.js'

const MODEL_VERSION = 'china-stance-model-full-version'

function trust() {
  return {
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
      state: 'sufficient',
      article_count: 10,
      source_count: 3,
      minimum_articles: 10,
      minimum_sources: 3,
    },
    model_version: MODEL_VERSION,
    method_version: MODEL_VERSION,
    source_status: 'available',
    snapshot_id: 'snapshot-1',
    model: { version: MODEL_VERSION },
    method: { version: MODEL_VERSION },
    source: {
      id: 'public.china_opinion_article_scores',
      status: 'available',
      cutoff_date: '2026-08-09',
    },
    snapshot: {
      id: 'snapshot-1',
      evaluated_on: '2026-08-09',
      cutoff_date: '2026-08-09',
    },
  }
}

function semanticContract() {
  return {
    schema_version: 'opinion-semantic-dimensions-v1',
    contract_version: 'opinion-three-axis-method-v1',
    dimensions: {
      stance: {
        meaning: 'targeted attitude toward the named target',
        categories: ['supportive', 'neutral', 'critical', 'unknown'],
        score_scales: {
          article_stance: {
            unit: 'dimensionless', minimum: -1, maximum: 1, neutral_band: [-0.15, 0.15],
          },
          aggregate_stance_index: {
            unit: 'index_points', minimum: -100, maximum: 100, neutral_band: [-15, 15],
          },
        },
        source_model: MODEL_VERSION,
        source_table: 'public.china_opinion_article_scores',
        availability: 'available_when_trust_gate_passes',
      },
      tone: {
        meaning: 'affective or linguistic tone independent of target',
        categories: ['positive', 'neutral', 'negative', 'mixed', 'unknown'],
        score_scale: { state: 'not_established', unit: 'unknown' },
        source_model: null,
        source_table: null,
        availability: 'not_available',
      },
      impact: {
        meaning: 'observed or modeled downstream effect independent of stance',
        directions: ['positive', 'neutral', 'negative', 'mixed', 'unknown'],
        score_scale: { state: 'not_established', unit: 'unknown' },
        source_model: null,
        source_table: null,
        availability: 'not_available',
      },
    },
    combination: {
      scope: 'response_projection',
      state: 'not_combined',
      combined_score: null,
      rules: {
        stance_from_tone: false,
        stance_from_impact: false,
        tone_from_stance: false,
        tone_from_impact: false,
        impact_from_stance: false,
        impact_from_tone: false,
      },
    },
    assurance: {
      quality_state: 'not_established',
      fact_truth_state: 'not_verified',
      upstream_axis_independence_state: 'not_established',
    },
    legacy_aliases: {
      sentiment: 'deprecated_ambiguous_output_suppressed',
      tone: 'deprecated_ui_style_alias_suppressed',
      impact_index: 'deprecated_ambiguous_output_suppressed',
      daily_impact: 'deprecated_ambiguous_output_suppressed',
      china_importance: 'deprecated_stance_derived_output_suppressed',
      sentiment_filter: 'legacy_wire_name_for_stance_filter_only',
    },
  }
}

function dimensions({
  category = 'supportive',
  score = 0.7,
  scale = 'article_stance',
  sourceField = 'news.stance_score',
} = {}) {
  return {
    stance: {
      state: 'available',
      category,
      score,
      scale,
      unit: scale === 'article_stance' ? 'dimensionless' : 'index_points',
      source_field: sourceField,
      source_model: MODEL_VERSION,
      reason_code: null,
    },
    tone: {
      state: 'unknown', category: 'unknown', score: null, unit: 'unknown',
      source_field: null, source_model: null, reason_code: 'TONE_MODEL_NOT_AVAILABLE',
    },
    impact: {
      state: 'unknown', direction: 'unknown', score: null, unit: 'unknown',
      source_field: null, source_model: null, reason_code: 'IMPACT_MODEL_NOT_AVAILABLE',
    },
  }
}

function sanitize(payload) {
  const provenance = trust()
  return sanitizeOpinionPayload({
    ...payload,
    semantic_contract: semanticContract(),
    trust: provenance,
    meta: { ...(payload.meta || {}), trust: provenance },
  }, { now: Date.UTC(2026, 7, 9) })
}

test('frontend validates the versioned method card and consumes only separated axes', () => {
  const payload = sanitize({
    news: [{ stance_score: 0.7, semantic_dimensions: dimensions() }],
  })
  const method = opinionSemanticMethodSnapshot(payload)
  const snapshot = opinionSemanticSnapshot(payload.news[0])

  assert.equal(method.valid, true)
  assert.equal(method.contractVersion, 'opinion-three-axis-method-v1')
  assert.equal(method.combinationState, 'not_combined')
  assert.equal(method.combinationScope, 'response_projection')
  assert.equal(method.upstreamAxisIndependenceState, 'not_established')
  assert.equal(method.qualityState, 'not_established')
  assert.equal(method.factTruthState, 'not_verified')
  assert.deepEqual(snapshot.stance, {
    state: 'available',
    category: 'supportive',
    score: 0.7,
    scale: 'article_stance',
    unit: 'dimensionless',
    sourceField: 'news.stance_score',
    sourceModel: MODEL_VERSION,
    reasonCode: null,
  })
  assert.equal(snapshot.tone.state, 'unknown')
  assert.equal(snapshot.impact.state, 'unknown')
})

test('missing or contradictory semantics fail only the precise derived dimension', () => {
  const contradictory = sanitize({
    news: [{
      stance_score: 0.8,
      sentiment: -0.9,
      impact_index: 99,
      semantic_dimensions: dimensions({ category: 'critical', score: 0.8 }),
    }],
  })
  const snapshot = opinionSemanticSnapshot(contradictory.news[0])
  assert.equal(snapshot.stance.state, 'unknown')
  assert.equal(snapshot.stance.score, null)
  assert.equal(snapshot.tone.state, 'unknown')
  assert.equal(snapshot.impact.state, 'unknown')

  const provenance = trust()
  const missing = sanitizeOpinionPayload({
    trust: provenance,
    meta: { trust: provenance },
    news: [{ sentiment: 0.9, impact_index: 95 }],
  }, { now: Date.UTC(2026, 7, 9) })
  const legacy = opinionSemanticSnapshot(missing.news[0])
  assert.equal(legacy.stance.state, 'unknown')
  assert.equal(legacy.tone.state, 'unknown')
  assert.equal(legacy.impact.state, 'unknown')
})

test('numeric strings, whitespace, and self-reported semantic values fail closed', () => {
  for (const [score, category] of [['0.7', 'supportive'], [' ', 'neutral']]) {
    const payload = sanitize({
      news: [{
        stance_score: score,
        semantic_dimensions: dimensions({ score, category }),
      }],
    })
    assert.equal(opinionSemanticSnapshot(payload.news[0]).stance.state, 'unknown')
  }

  const stringTrend = sanitize({
    dates: ['2026-08-09'],
    values: ['20'],
    metric_id: 'weighted_target_stance_index',
    semantic_dimensions: dimensions({
      category: 'supportive',
      score: 20,
      scale: 'aggregate_stance_index',
      sourceField: 'values[-1]',
    }),
  })
  assert.deepEqual(semanticTrendProjection(stringTrend).values, [])
})

test('method cards reject unknown keys instead of extending reviewed semantics', () => {
  const mutations = [
    (card) => { card.unreviewed = true },
    (card) => { card.dimensions.stance.unreviewed = true },
    (card) => { card.assurance.unreviewed = true },
    (card) => { card.legacy_aliases.unreviewed = 'unsafe' },
  ]
  for (const mutate of mutations) {
    const card = semanticContract()
    mutate(card)
    const provenance = trust()
    const payload = sanitizeOpinionPayload({
      semantic_contract: card,
      trust: provenance,
      meta: { trust: provenance },
      news: [{ stance_score: 0.7, semantic_dimensions: dimensions() }],
    }, { now: Date.UTC(2026, 7, 9) })
    assert.equal(opinionSemanticMethodSnapshot(payload).valid, false)
    assert.equal(opinionSemanticSnapshot(payload.news[0]).stance.state, 'unknown')
  }
})

test('semantic stance must bind to the controlled raw field, scale, value, and target label', () => {
  const cases = [
    {
      record: { stance_score: 0.2 },
      semantic: dimensions({ score: 0.9, sourceField: 'news.stance_score' }),
    },
    {
      record: { stance_score: 0.9 },
      semantic: dimensions({ score: 0.9, sourceField: 'foo' }),
    },
    {
      record: { stance_score: 0.9 },
      semantic: dimensions({
        score: 0.9,
        scale: 'aggregate_stance_index',
        sourceField: 'news.stance_score',
      }),
    },
  ]
  for (const item of cases) {
    const payload = sanitize({
      news: [{ ...item.record, semantic_dimensions: item.semantic }],
    })
    assert.equal(opinionSemanticSnapshot(payload.news[0]).stance.state, 'unknown')
  }

  const target = sanitize({
    target_indices: [{
      label: 'NEG',
      value: 20,
      semantic_dimensions: dimensions({
        score: 20,
        scale: 'aggregate_stance_index',
        sourceField: 'target_indices.CN.value',
      }),
    }],
  })
  assert.equal(opinionSemanticSnapshot(target.target_indices[0]).stance.state, 'unknown')

  const rootSummary = sanitize({
    current_index: 90,
    summary: { current_index: 20 },
    semantic_dimensions: dimensions({
      category: 'supportive',
      score: 90,
      scale: 'aggregate_stance_index',
      sourceField: 'summary.current_index',
    }),
  })
  assert.equal(opinionSemanticSnapshot(rootSummary).stance.state, 'unknown')
})

test('trend projection requires an exact semantic contract and stance series identity', () => {
  const valid = sanitize({
    dates: ['2026-08-08', '2026-08-09'],
    values: [-5, 20],
    metric_id: 'weighted_target_stance_index',
    semantic_dimensions: dimensions({
      category: 'supportive', score: 20, scale: 'aggregate_stance_index',
      sourceField: 'values[-1]',
    }),
  })
  assert.deepEqual(semanticTrendProjection(valid), {
    dates: ['2026-08-08', '2026-08-09'],
    values: [-5, 20],
    heat: [],
    meta: valid.meta,
    metric_id: 'weighted_target_stance_index',
    semantic_contract: valid.semantic_contract,
    semantic_dimensions: valid.semantic_dimensions,
  })

  const provenance = trust()
  const legacy = sanitizeOpinionPayload({
    dates: ['2026-08-09'],
    values: [91],
    trust: provenance,
    meta: { trust: provenance },
  }, { now: Date.UTC(2026, 7, 9) })
  assert.deepEqual(semanticTrendProjection(legacy).values, [])
})

test('overview, detail, target, dimension, and trend UI contain no cross-axis fallback', async () => {
  const page = await readFile(
    new URL('../src/views/sentimentAnalysis.vue', import.meta.url),
    'utf8',
  )
  const chart = await readFile(
    new URL('../src/features/sentiment/trend.js', import.meta.url),
    'utf8',
  )

  assert.doesNotMatch(page, /impact_index\s*\?\?\s*news\.stance_score\s*\?\?\s*news\.sentiment/)
  assert.doesNotMatch(page, /brief\.stance_score[^\n]*\*\s*100[^\n]*[},]/)
  assert.doesNotMatch(page, /正向影响|负面影响/)
  assert.match(page, /三维语义方法卡/)
  assert.match(page, /目标立场/)
  assert.match(page, /文本语气/)
  assert.match(page, /现实影响/)
  assert.match(page, /opinionSemanticSnapshot/)
  assert.match(page, /semanticTrendProjection/)
  assert.match(page, /仅约束响应投影/)
  assert.match(page, /响应投影组合规则/)
  assert.match(page, /上游轴独立性：未建立/)
  assert.doesNotMatch(chart, /name:\s*['"]舆情指数['"]|舆情指数\s*\$\{/)
  assert.match(chart, /加权目标立场指数/)
})
