import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'

import {
  ModelAssuranceApiError,
  ModelAssuranceContractError,
  createLatestModelAssuranceRequest,
  createModelAssuranceApi,
  formatAssuranceDelta,
  formatAssuranceMetric,
  parseModelAssuranceManifestJson,
  reconcileModelAssuranceLedger,
  sanitizeModelAssuranceResponse,
} from '../src/features/model-assurance/index.js'

const STORED_AT = '2026-08-09T12:00:00Z'
const CUTOFF_AT = '2026-08-01T00:00:00Z'
const REVIEWED_AT = '2026-08-08T00:00:00Z'
const REVIEW_VALID_UNTIL = '2099-09-01T00:00:00Z'

function calibrationSlice() {
  return {
    confusion: {
      true_positive: 40,
      false_positive: 10,
      true_negative: 40,
      false_negative: 10,
    },
    calibration_bins: [
      {
        lower_bound: 0,
        upper_bound: 0.5,
        sample_count: 50,
        positive_count: 10,
        predicted_probability_sum: 10,
        positive_probability_sum: 2,
        squared_probability_sum: 2,
      },
      {
        lower_bound: 0.5,
        upper_bound: 1,
        sample_count: 50,
        positive_count: 40,
        predicted_probability_sum: 40,
        positive_probability_sum: 32,
        squared_probability_sum: 32,
      },
    ],
  }
}

function recomputedMetrics() {
  return {
    sample_count: 100,
    positive_count: 50,
    predicted_positive_count: 50,
    precision: 0.8,
    recall: 0.8,
    f1: 0.8,
    brier_score: 0.16,
    expected_calibration_error: 0,
  }
}

function manifestFixture() {
  return {
    evaluation_id: 'eval.stance.baseline',
    evaluation_version: '2026.08.09.1',
    dataset: {
      dataset_id: 'gold.news-stance.v1',
      dataset_version: '2026.08.01',
      sha256: 'a'.repeat(64),
      cutoff_at: CUTOFF_AT,
      evaluation_role: 'gold_standard',
      gold_standard_status: 'independently_reviewed',
      label_schema_version: 'stance-label-v1',
      annotation_protocol_ref: 'governance:annotation:v1',
      provenance_ref: 'governance:dataset:v1',
    },
    model: {
      model_id: 'stance.classifier',
      model_version: '1.0.0',
      method_version: 'stance-method-v3',
      owner_organization: 'GlobeMind Model Team',
      task_type: 'binary_classification',
      positive_label: 'relevant',
    },
    classification_threshold: 0.5,
    overall: calibrationSlice(),
    strata: [
      { dimension: 'country', value: 'CHN', ...calibrationSlice() },
      { dimension: 'language', value: 'zh', ...calibrationSlice() },
      { dimension: 'topic', value: 'economy', ...calibrationSlice() },
    ],
    coverage: {
      countries: ['CHN'],
      languages: ['zh'],
      topics: ['economy'],
    },
    thresholds: {
      minimum_precision: 0.75,
      minimum_recall: 0.75,
      minimum_f1: 0.75,
      maximum_brier_score: 0.2,
      maximum_ece: 0.05,
      minimum_stratum_f1: 0.75,
      minimum_overall_samples: 100,
      minimum_samples_per_stratum: 100,
      maximum_f1_drop_from_baseline: 0.05,
      maximum_brier_increase_from_baseline: 0.05,
      maximum_ece_increase_from_baseline: 0.05,
    },
    independent_review: {
      review_id: 'review:stance-baseline',
      reviewer_id: 'reviewer:external-7',
      reviewer_organization: 'Independent Evaluation Lab',
      independence_attestation: true,
      decision: 'approved',
      reviewed_at: REVIEWED_AT,
      valid_until: REVIEW_VALID_UNTIL,
      evidence_ref: 'review-evidence:stance-baseline',
      evidence_sha256: 'b'.repeat(64),
    },
    evaluation_integrity: {
      label_source: 'human_gold',
      partition_role: 'holdout',
      holdout_access_status: 'sealed',
      development_dataset_sha256s: ['c'.repeat(64)],
      separation_evidence_ref: 'separation-evidence:stance-baseline',
      separation_evidence_sha256: 'f'.repeat(64),
    },
    baseline: null,
  }
}

function resultFixture() {
  return {
    schema_version: 'globemind.model-assurance.v1',
    contract_version: '1.0.0',
    metric_method_version: 'binary-assurance-metrics-1.0.0',
    evaluation_id: 'eval.stance.baseline',
    manifest_sha256: 'd'.repeat(64),
    evaluated_at: STORED_AT,
    evidence_status: 'manifest_only',
    overall: recomputedMetrics(),
    strata: [
      { dimension: 'country', value: 'CHN', metrics: recomputedMetrics() },
      { dimension: 'language', value: 'zh', metrics: recomputedMetrics() },
      { dimension: 'topic', value: 'economy', metrics: recomputedMetrics() },
    ],
    coverage: {
      state: 'complete',
      expected: { country: ['CHN'], language: ['zh'], topic: ['economy'] },
      observed: { country: ['CHN'], language: ['zh'], topic: ['economy'] },
      missing: { country: [], language: [], topic: [] },
      unexpected: { country: [], language: [], topic: [] },
      minimum_samples_satisfied: true,
    },
    drift: {
      state: 'not_observed',
      baseline_evaluation_id: null,
      f1_delta: null,
      brier_delta: null,
      ece_delta: null,
      reason_codes: ['BASELINE_NOT_PROVIDED'],
    },
    rollback: {
      action: 'hold_release',
      target_evaluation_id: null,
      reason_codes: ['BASELINE_NOT_PROVIDED'],
    },
    gate_state: 'blocked',
    release_eligible: false,
    reason_codes: ['BASELINE_NOT_PROVIDED'],
  }
}

function detailFixture() {
  return {
    storage_schema_version: 'globemind.model-assurance.entry.v1',
    sequence: 1,
    stored_at: STORED_AT,
    submitted_by: 'user:7',
    previous_entry_sha256: null,
    manifest: manifestFixture(),
    result: resultFixture(),
    entry_sha256: 'e'.repeat(64),
  }
}

function releaseEligibleDetailFixture() {
  const detail = detailFixture()
  detail.sequence = 2
  detail.previous_entry_sha256 = 'f'.repeat(64)
  detail.manifest.baseline = {
    evaluation_id: 'eval.stance.prior',
    entry_sha256: '1'.repeat(64),
  }
  detail.result.drift = {
    state: 'within_threshold',
    baseline_evaluation_id: 'eval.stance.prior',
    f1_delta: 0,
    brier_delta: 0,
    ece_delta: 0,
    reason_codes: [],
  }
  detail.result.rollback = {
    action: 'proceed',
    target_evaluation_id: null,
    reason_codes: [],
  }
  detail.result.gate_state = 'eligible'
  detail.result.release_eligible = true
  detail.result.reason_codes = []
  return detail
}

function summaryFixture() {
  return {
    evaluation_id: 'eval.stance.baseline',
    model_id: 'stance.classifier',
    model_version: '1.0.0',
    method_version: 'stance-method-v3',
    dataset_id: 'gold.news-stance.v1',
    dataset_sha256: 'a'.repeat(64),
    cutoff_at: CUTOFF_AT,
    stored_at: STORED_AT,
    entry_sha256: 'e'.repeat(64),
    gate_state: 'blocked',
    release_eligible: false,
    drift_state: 'not_observed',
    rollback_action: 'hold_release',
    reason_codes: ['BASELINE_NOT_PROVIDED'],
  }
}

function statusFixture() {
  return {
    schema_version: 'globemind.model-assurance.v1',
    generated_at: STORED_AT,
    available: true,
    operational_state: 'observed',
    release_status: 'blocked',
    gold_standard_state: 'manifest_attested',
    evaluation_count: 1,
    eligible_count: 0,
    latest: summaryFixture(),
    reason_codes: ['BASELINE_NOT_PROVIDED', 'RELEASE_BLOCKED'],
  }
}

function emptyStatusFixture() {
  return {
    schema_version: 'globemind.model-assurance.v1',
    generated_at: STORED_AT,
    available: false,
    operational_state: 'not_observed',
    release_status: 'blocked',
    gold_standard_state: 'not_observed',
    evaluation_count: 0,
    eligible_count: 0,
    latest: null,
    reason_codes: [
      'NO_EVALUATION_MANIFESTS',
      'GOLD_STANDARD_NOT_OBSERVED',
      'RELEASE_BLOCKED',
    ],
  }
}

function response(payload, { status = 200, ok = status >= 200 && status < 300 } = {}) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

test('shared strict sanitizer accepts consistent status/list/detail and preserves null', () => {
  const status = sanitizeModelAssuranceResponse('status', statusFixture())
  const evaluations = sanitizeModelAssuranceResponse('list', [summaryFixture()])
  const detail = sanitizeModelAssuranceResponse('detail', detailFixture())
  const ledger = reconcileModelAssuranceLedger(status, evaluations)

  assert.equal(ledger.status.releaseStatus, 'blocked')
  assert.equal(ledger.evaluations[0].evaluationId, 'eval.stance.baseline')
  assert.equal(detail.result.overall.precision, 0.8)
  assert.equal(detail.result.overall.brierScore, 0.16)
  assert.equal(detail.result.overall.expectedCalibrationError, 0)
  assert.equal(detail.result.drift.f1Delta, null)
  assert.equal(formatAssuranceMetric(null), '未定义')
  assert.equal(formatAssuranceMetric(0), '0.0000')
  assert.equal(formatAssuranceDelta(null), '未观测')
  assert.equal(Object.isFrozen(detail.result.strata), true)

  const emptyStatus = sanitizeModelAssuranceResponse('status', emptyStatusFixture())
  assert.equal(emptyStatus.available, false)
  assert.equal(emptyStatus.operationalState, 'not_observed')
  assert.deepEqual(reconcileModelAssuranceLedger(emptyStatus, []), {
    status: emptyStatus,
    evaluations: [],
  })
})

test('sanitizer rejects claimed metrics and cross-resource contradictions', () => {
  const falseMetric = detailFixture()
  falseMetric.result.overall.f1 = 0.99
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', falseMetric),
    (error) => error instanceof ModelAssuranceContractError && /重算/.test(error.message),
  )

  const nullAsZeroClaim = detailFixture()
  nullAsZeroClaim.result.overall.precision = null
  assert.throws(() => sanitizeModelAssuranceResponse('detail', nullAsZeroClaim), /重算/)

  const leakedHoldout = detailFixture()
  leakedHoldout.manifest.evaluation_integrity.development_dataset_sha256s = [
    leakedHoldout.manifest.dataset.sha256,
  ]
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', leakedHoldout),
    /开发集重叠/,
  )

  const falseEmpty = emptyStatusFixture()
  falseEmpty.available = true
  assert.throws(() => sanitizeModelAssuranceResponse('status', falseEmpty), /blocked\/not_observed/)

  const status = sanitizeModelAssuranceResponse('status', statusFixture())
  const mismatched = summaryFixture()
  mismatched.entry_sha256 = 'f'.repeat(64)
  const rows = sanitizeModelAssuranceResponse('list', [mismatched])
  assert.throws(() => reconcileModelAssuranceLedger(status, rows), /状态与列表矛盾/)
})

test('sanitizer rejects impossible calibration moments even when claimed metrics match', () => {
  const impossible = detailFixture()
  impossible.manifest.overall.calibration_bins[0].squared_probability_sum = 0.1
  for (const stratum of impossible.manifest.strata) {
    stratum.calibration_bins[0].squared_probability_sum = 0.1
  }
  impossible.result.overall.brier_score = 0.141
  for (const stratum of impossible.result.strata) {
    stratum.metrics.brier_score = 0.141
  }
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', impossible),
    /充分统计不自洽/,
  )
})

test('eligible detail fails closed on thresholds, governance, and review time', () => {
  const unmetThreshold = releaseEligibleDetailFixture()
  unmetThreshold.manifest.thresholds.minimum_f1 = 0.9
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', unmetThreshold),
    /放行结果缺少|阈值/,
  )

  const missingGovernance = releaseEligibleDetailFixture()
  missingGovernance.manifest.dataset.provenance_ref = null
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', missingGovernance),
    /放行结果缺少|金标准/,
  )

  const futureReview = releaseEligibleDetailFixture()
  futureReview.manifest.independent_review.reviewed_at = '2026-08-10T00:00:00Z'
  assert.throws(
    () => sanitizeModelAssuranceResponse('detail', futureReview),
    /审阅|放行结果缺少/,
  )
})

test('authenticated API uses no-store, sanitizes every response, and preserves backend 403', async () => {
  const calls = []
  const api = createModelAssuranceApi({
    apiOrigin: 'https://example.test',
    getAccessToken: () => 'opaque-access-token',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      if (options.method === 'POST') return response(detailFixture(), { status: 201 })
      if (url.endsWith('/status')) return response(statusFixture())
      if (url.includes('?limit=')) return response([summaryFixture()])
      return response(detailFixture())
    },
  })

  await api.fetchStatus()
  await api.listEvaluations({ limit: 50, modelId: 'stance.classifier' })
  await api.getEvaluation('eval.stance.baseline')
  await api.submitEvaluation(manifestFixture())

  assert.equal(calls.length, 4)
  assert.equal(calls[0].url, 'https://example.test/api/model-assurance/status')
  assert.match(calls[1].url, /limit=50&model_id=stance\.classifier/)
  assert.equal(calls[2].url, 'https://example.test/api/model-assurance/evaluations/eval.stance.baseline')
  assert.equal(calls[3].options.method, 'POST')
  assert.equal(calls[3].options.cache, 'no-store')
  assert.equal(calls[3].options.credentials, 'same-origin')
  assert.equal(calls[3].options.headers.Authorization, 'Bearer opaque-access-token')
  assert.equal(JSON.parse(calls[3].options.body).evaluation_id, 'eval.stance.baseline')

  let unauthenticatedCalls = 0
  const noAuth = createModelAssuranceApi({
    getAccessToken: () => '',
    fetchImpl: async () => {
      unauthenticatedCalls += 1
      return response({})
    },
  })
  await assert.rejects(noAuth.fetchStatus(), (error) => {
    assert.equal(error instanceof ModelAssuranceApiError, true)
    assert.equal(error.code, 'AUTH_REQUIRED')
    return true
  })
  assert.equal(unauthenticatedCalls, 0)

  const forbidden = createModelAssuranceApi({
    getAccessToken: () => 'non-admin-token',
    fetchImpl: async () => response({ detail: '需要管理员权限' }, { status: 403 }),
  })
  await assert.rejects(forbidden.submitEvaluation(manifestFixture()), (error) => {
    assert.equal(error.status, 403)
    assert.equal(error.code, 'ADMIN_REQUIRED')
    assert.match(error.message, /管理员/)
    return true
  })
})

test('latest-request gate aborts old work and suppresses stale completion', async () => {
  const gate = createLatestModelAssuranceRequest()
  const pending = []
  const first = gate.run((signal) => new Promise((resolve) => pending.push({ signal, resolve })))
  const second = gate.run((signal) => new Promise((resolve) => pending.push({ signal, resolve })))

  assert.equal(pending[0].signal.aborted, true)
  assert.equal(pending[1].signal.aborted, false)
  pending[1].resolve('latest')
  pending[0].resolve('stale')

  assert.deepEqual(await second, { status: 'success', latest: true, value: 'latest' })
  assert.deepEqual(await first, { status: 'stale', latest: false })
})

test('admin JSON preparation never injects examples or accepts derived metrics', () => {
  const source = JSON.stringify(manifestFixture())
  const parsed = parseModelAssuranceManifestJson(source)
  assert.equal(parsed.evaluation_id, 'eval.stance.baseline')
  assert.equal(parsed.result, undefined)
  assert.equal(Object.isFrozen(parsed), true)
  assert.throws(() => parseModelAssuranceManifestJson('{broken'), /JSON 语法无效/)
  assert.throws(
    () => parseModelAssuranceManifestJson(JSON.stringify({ ...manifestFixture(), result: {} })),
    /派生指标或放行结果/,
  )
  assert.throws(
    () => parseModelAssuranceManifestJson(
      JSON.stringify({ ...manifestFixture(), overall: { ...calibrationSlice(), precision: 0.8 } }),
    ),
    /派生指标或放行结果/,
  )
  assert.throws(
    () => parseModelAssuranceManifestJson(
      source.replace(
        '"evaluation_id":"eval.stance.baseline"',
        '"evaluation_id":"eval.stance.shadow",'
          + '"evaluation_id":"eval.stance.baseline"',
      ),
    ),
    /重复 JSON 键/,
  )
})

test('view is accessible, manifest-only, admin-gated, atomic, and compiles as an SFC', async () => {
  const filename = new URL('../src/views/ModelAssurance.vue', import.meta.url)
  const source = await readFile(filename, 'utf8')
  assert.match(source, /manifest-only/)
  assert.match(source, /不运行模型，也不生成 benchmark/)
  assert.match(source, /不是对金标准数据的外部验真/)
  assert.match(source, /Precision/)
  assert.match(source, /Recall/)
  assert.match(source, /Brier/)
  assert.match(source, /ECE/)
  assert.match(source, /country/)
  assert.match(source, /language/)
  assert.match(source, /topic/)
  assert.match(source, /Rollback recommendation/)
  assert.match(source, /评测隔离声明/)
  assert.match(source, /审阅有效期/)
  assert.match(source, /holdout 访问/)
  assert.match(source, /当前门禁/)
  assert.match(source, /入账时历史判定/)
  assert.match(source, /blocked \/ not_observed/)
  assert.match(source, /v-if="isAdmin"/)
  assert.match(source, /const manifestText = ref\(''\)/)
  assert.match(source, /第一步：解析并核对/)
  assert.match(source, /第二步：确认提交/)
  assert.match(source, /aria-live="assertive"/)
  assert.match(source, /tabindex="-1"/)
  assert.match(source, /min-height: 44px/)
  assert.match(source, /emptyModelAssuranceLedger\('loading'\)/)
  assert.match(source, /emptyModelAssuranceDetail\('loading'\)/)

  const { descriptor, errors } = parse(source, { filename: 'ModelAssurance.vue' })
  assert.deepEqual(errors, [])
  assert.doesNotThrow(() => compileScript(descriptor, { id: 'model-assurance-test' }))
  const template = compileTemplate({
    id: 'model-assurance-test',
    filename: 'ModelAssurance.vue',
    source: descriptor.template.content,
  })
  assert.deepEqual(template.errors, [])
})
