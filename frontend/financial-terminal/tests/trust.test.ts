import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  dashboardAllowsComposite,
  financialTrendDisclosurePresentation,
  hasConsistentDerivedMetricClaims,
  hasConsistentShortSampleTrendDisclosures,
  sanitizeFinancialAlertData,
  sanitizeFinancialDashboard,
} from '../src/lib/trust.ts'
import type { DashboardData, FinancialTrust } from '../src/types/index.ts'

function methodCard(): Record<string, unknown> {
  return {
    schema_version: 'financial-composite-method-card-v1',
    method_version: 'method-v1',
    implementation_status: 'prototype_code_extracted',
    approval_status: 'not_approved',
    formula_status: 'partially_extracted_not_governed',
    input_units_status: 'not_dimensionally_validated',
    baseline_status: 'not_established',
    threshold_status: 'not_approved',
    normalization: {
      status: 'partially_extracted_not_governed',
      count_transform: 'min(cap, base + ln(1 + count) * scale)',
      non_positive_fallback: 'base * 0.65',
      parameters: 'vary_by_subindex_in_implementation',
    },
    frequency_alignment: {
      status: 'not_approved',
      anchor_selection: 'longest_non_empty_input_array',
      interpolation: 'linear_by_array_position_not_observation_timestamp',
      empty_input: 'zero_fill',
      singleton_input: 'repeat_single_value',
    },
    missing_value_policy: 'not_established',
    revision_policy: 'not_established',
    wsi_aggregation: {
      status: 'extracted_from_deterministic_implementation',
      component_order: [
        'diplomacy', 'security', 'energy', 'supply', 'technology', 'society', 'macro',
      ],
      weights: {
        diplomacy: 0.14,
        security: 0.2,
        energy: 0.14,
        supply: 0.15,
        technology: 0.12,
        society: 0.13,
        macro: 0.12,
      },
      output_unit: '指数',
    },
    test_vectors: [
      {
        id: 'wsi-equal-components-v1',
        inputs: [50, 50, 50, 50, 50, 50, 50],
        expected: 50,
      },
      {
        id: 'wsi-ordered-components-v1',
        inputs: [10, 20, 30, 40, 50, 60, 70],
        expected: 37.8,
      },
    ],
  }
}

function trust(overrides: Partial<FinancialTrust> = {}): FinancialTrust {
  return {
    schema_version: 'financial-trust-v1',
    snapshot_id: 'fin-test',
    trust_status: 'trusted',
    freshness_status: 'live',
    computability: 'computable',
    computable: true,
    data_as_of: '2026-08-09T08:00:00Z',
    coverage_ratio: 1,
    minimum_coverage_ratio: 0.5,
    usable_sources: 4,
    source_total: 4,
    usable_source_ids: ['source-1', 'source-2', 'source-3', 'source-4'],
    unavailable_source_ids: [],
    source_status: { live: 4 },
    model_version: 'model-v1',
    method_version: 'method-v1',
    composite_method_card: methodCard(),
    unavailable_reasons: [],
    alerts_enabled: true,
    ...overrides,
  }
}

function dashboard(overrides: Partial<DashboardData> = {}): DashboardData {
  const rootTrust = trust()
  return {
    mode: 'live',
    bars: [{ time: 1, open: 1, high: 2, low: 1, close: 2 }],
    ma20: [{ time: 1, value: 2 }],
    ma50: [],
    ma200: [],
    indices: [{
      id: 'wsi',
      name: 'WSI',
      value: 52,
      change_pct: 1.2,
      spark: [50, 52],
      availability: 'available',
      schema_version: rootTrust.schema_version,
      snapshot_id: rootTrust.snapshot_id,
    }],
    watchlist: [],
    series: [{
      id: 'IDX-WSI',
      kind: 'index',
      label: 'WSI',
      points: [{ time: 1, value: 52 }],
      latest: 52,
      change_pct: 1.2,
      availability: 'available',
      schema_version: rootTrust.schema_version,
      snapshot_id: rootTrust.snapshot_id,
    }],
    sources: ['source-1', 'source-2', 'source-3', 'source-4'].map((id) => ({
      id,
      name: id,
      status: 'live',
      records: 1,
      cadence: 'daily',
      last_updated: rootTrust.data_as_of || undefined,
      freshness_status: 'live',
      contribution_state: 'usable',
    })),
    trust: rootTrust,
    trust_status: rootTrust.trust_status,
    freshness_status: rootTrust.freshness_status,
    computability: rootTrust.computability,
    computable: rootTrust.computable,
    alerts_enabled: rootTrust.alerts_enabled,
    data_as_of: rootTrust.data_as_of,
    coverage: {
      coverage_ratio: rootTrust.coverage_ratio,
      minimum_coverage_ratio: rootTrust.minimum_coverage_ratio,
      usable_sources: rootTrust.usable_sources,
      sources_total: rootTrust.source_total,
      source_status: rootTrust.source_status,
    },
    schema_version: rootTrust.schema_version,
    snapshot_id: rootTrust.snapshot_id,
    model_version: rootTrust.model_version,
    method_version: rootTrust.method_version,
    composite_method_card: rootTrust.composite_method_card,
    unavailable_reasons: rootTrust.unavailable_reasons,
    ...overrides,
  }
}

function attachDerivedClaimContracts(data: DashboardData): DashboardData {
  const rootTrust = data.trust!
  const identity = {
    schema_version: 'financial-derived-claim-identity-v1' as const,
    semantic_metric_id: 'IDX-WSI',
    metric_class: 'composite_index' as const,
    method_version: rootTrust.method_version,
    model_version: rootTrust.model_version,
    snapshot_id: rootTrust.snapshot_id,
    data_cutoff: rootTrust.data_as_of || null,
    availability: 'available' as const,
  }
  const contract = {
    claim_id: `fdc_${'a'.repeat(64)}`,
    claim_identity: identity,
    claim_unavailable_reason: null,
    citation_locator: null,
    citation_locator_state: 'unavailable' as const,
    citation_unavailable_reason: 'VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED' as const,
  }
  return {
    ...data,
    indices: data.indices.map((index) => ({
      ...index,
      metric_id: 'IDX-WSI',
      ...contract,
    })),
    series: data.series?.map((metric) => metric.id === 'IDX-WSI' ? {
      ...metric,
      ...contract,
    } : metric),
  }
}

function attachShortSampleTrendDisclosures(data: DashboardData, count = 1): DashboardData {
  const rootTrust = data.trust!
  const trendMethodCard = {
    schema_version: 'financial-short-sample-trend-method-card-v1',
    statistical_method_version: null,
    implementation_status: 'disclosure_gate_only',
    approval_status: 'not_approved',
    baseline_period_status: 'not_established',
    sample_size_semantics: 'provided_series_point_count_only',
    independence_status: 'not_validated',
    minimum_sample_size: null,
    uncertainty_method_status: 'not_established',
    confidence_level: null,
    outlier_policy_status: 'not_established',
    release_rule: 'suppress_change_pct_until_approved_method',
    maximum_provided_points: 4096,
  }
  const disclosure = {
    schema_version: 'financial-short-sample-trend-v1' as const,
    semantic_metric_id: 'IDX-WSI',
    snapshot_id: rootTrust.snapshot_id,
    data_cutoff: rootTrust.data_as_of || null,
    statistical_method_version: null,
    approval_status: 'not_approved' as const,
    trend_status: 'not_computable' as const,
    baseline_period: {
      status: 'not_established' as const,
      start: null,
      end: null,
    },
    sample_size: {
      status: 'provided_series_point_count' as const,
      count,
      unit: 'provided_series_points' as const,
      independence_status: 'not_validated' as const,
    },
    uncertainty: {
      status: 'not_computable' as const,
      confidence_level: null,
      interval_lower: null,
      interval_upper: null,
      reason_code: 'UNCERTAINTY_METHOD_NOT_ESTABLISHED' as const,
    },
    outlier_policy_status: 'not_established' as const,
    reason_codes: [
      'BASELINE_PERIOD_NOT_ESTABLISHED',
      'TREND_METHOD_NOT_APPROVED',
      'UNCERTAINTY_METHOD_NOT_ESTABLISHED',
    ] as const,
  }
  return {
    ...data,
    trust: {
      ...rootTrust,
      short_sample_trend_method_card: trendMethodCard,
    },
    short_sample_trend_method_card: trendMethodCard,
    indices: data.indices.map((index) => ({
      ...index,
      metric_id: 'IDX-WSI',
      trend_disclosure: disclosure,
    })),
    series: data.series?.map((metric) => metric.id === 'IDX-WSI' ? {
      ...metric,
      trend_disclosure: disclosure,
    } : metric),
  }
}

test('missing or contradictory trust clears every composite value and point', () => {
  const missing = dashboard({ trust: undefined })
  const missingResult = sanitizeFinancialDashboard(missing)
  assert.equal(dashboardAllowsComposite(missing), false)
  assert.equal(missingResult.indices[0].value, null)
  assert.deepEqual(missingResult.indices[0].spark, [])
  assert.equal(missingResult.series[0].latest, null)
  assert.deepEqual(missingResult.series[0].points, [])
  assert.deepEqual(missingResult.bars, [])

  const conflictingTrust = trust({ computable: false })
  const conflicting = dashboard({ trust: conflictingTrust })
  assert.equal(dashboardAllowsComposite(conflicting), false)
  assert.deepEqual(sanitizeFinancialDashboard(conflicting).series[0].points, [])

  const conflictingCoverage = dashboard({
    coverage: {
      coverage_ratio: 0.1,
      minimum_coverage_ratio: 0.5,
      usable_sources: 4,
      source_status: { live: 4 },
    },
  })
  assert.equal(dashboardAllowsComposite(conflictingCoverage), false)
  assert.deepEqual(sanitizeFinancialDashboard(conflictingCoverage).series[0].points, [])
})

test('source trust alone cannot release an unapproved financial composite', () => {
  const data = dashboard()
  assert.equal(dashboardAllowsComposite(data), false)
  const sanitized = sanitizeFinancialDashboard(data)
  assert.equal(sanitized.indices[0].value, null)
  assert.equal(
    (sanitized.trust?.composite_method_card as Record<string, unknown>).approval_status,
    'not_approved',
  )
})

test('derived claim contracts bind semantic metric identity and reject unknown shapes', () => {
  const data = attachDerivedClaimContracts(dashboard())
  assert.equal(hasConsistentDerivedMetricClaims(data), true)

  const relabelled = structuredClone(data)
  relabelled.indices[0].name = 'unverified display text'
  relabelled.series![0].label = 'another display string'
  assert.equal(hasConsistentDerivedMetricClaims(relabelled), true)

  const semanticMismatch = structuredClone(data)
  semanticMismatch.indices[0].claim_identity!.semantic_metric_id = 'IDX-SECURITY'
  assert.equal(hasConsistentDerivedMetricClaims(semanticMismatch), false)
  const sanitizedMismatch = sanitizeFinancialDashboard(semanticMismatch)
  assert.equal(sanitizedMismatch.indices[0].claim_id, null)
  assert.equal(
    sanitizedMismatch.indices[0].claim_unavailable_reason,
    'DERIVED_CLAIM_CONTRACT_INVALID',
  )
  assert.equal(sanitizedMismatch.indices[0].citation_locator, null)

  const unknownField = structuredClone(data)
  ;(unknownField.series![0].claim_identity as Record<string, unknown>).display_name = 'WSI'
  assert.equal(hasConsistentDerivedMetricClaims(unknownField), false)

  const fabricatedLocator = structuredClone(data)
  fabricatedLocator.indices[0].citation_locator = 'https://example.test/unverified'
  assert.equal(hasConsistentDerivedMetricClaims(fabricatedLocator), false)

  const swappedId = structuredClone(data)
  swappedId.series![0].claim_id = `fdc_${'b'.repeat(64)}`
  assert.equal(hasConsistentDerivedMetricClaims(swappedId), false)

  const malformedSeriesId = structuredClone(data) as unknown as {
    series: Array<Record<string, unknown>>
  }
  malformedSeriesId.series[0].id = null
  assert.doesNotThrow(() => sanitizeFinancialDashboard(
    malformedSeriesId as unknown as DashboardData,
  ))
  assert.equal(
    sanitizeFinancialDashboard(malformedSeriesId as unknown as DashboardData)
      .indices[0].claim_id,
    null,
  )
})

test('short-sample trend disclosure binds baseline, point count, and unavailable uncertainty', () => {
  const data = attachShortSampleTrendDisclosures(dashboard(), 1)
  assert.equal(hasConsistentShortSampleTrendDisclosures(data), true)
  assert.deepEqual(financialTrendDisclosurePresentation(
    data.series![0].trend_disclosure,
  ), {
    status: 'not_computable',
    preciseChangeAllowed: false,
    baseline: '基期未建立',
    sampleSize: '1 个提供的序列点（未验证为独立样本）',
    uncertainty: '不确定性不可计算（方法未建立）',
    outliers: '异常值规则未建立',
  })
  const sanitized = sanitizeFinancialDashboard(data)
  assert.deepEqual(sanitized.series![0].points, [])
  assert.equal(sanitized.series![0].trend_disclosure!.sample_size.count, 1)

  const tamperedInterval = structuredClone(data)
  tamperedInterval.indices[0].trend_disclosure!.uncertainty.interval_lower = 49
  assert.equal(hasConsistentShortSampleTrendDisclosures(tamperedInterval), false)
  assert.deepEqual(financialTrendDisclosurePresentation(
    tamperedInterval.indices[0].trend_disclosure,
  ), {
    status: 'not_computable',
    preciseChangeAllowed: false,
    baseline: '基期未建立',
    sampleSize: '样本量不可用',
    uncertainty: '不确定性不可计算（方法未建立）',
    outliers: '异常值规则未建立',
  })
  assert.equal(
    sanitizeFinancialDashboard(tamperedInterval).indices[0].trend_disclosure,
    undefined,
  )

  const inconsistentCount = structuredClone(data)
  inconsistentCount.series![0].trend_disclosure = structuredClone(
    inconsistentCount.series![0].trend_disclosure!,
  )
  inconsistentCount.series![0].trend_disclosure!.sample_size.count = 2
  assert.equal(hasConsistentShortSampleTrendDisclosures(inconsistentCount), false)

  const jointlyForgedCount = structuredClone(data)
  jointlyForgedCount.series![0].trend_disclosure!.sample_size.count = 2
  assert.equal(
    jointlyForgedCount.indices[0].trend_disclosure!.sample_size.count,
    2,
  )
  assert.equal(hasConsistentShortSampleTrendDisclosures(jointlyForgedCount), false)

  const pointWithUnknownField = structuredClone(data) as unknown as {
    series: Array<Record<string, unknown>>
  }
  pointWithUnknownField.series[0].points = [{ time: 1, value: 52, hidden: true }]
  assert.equal(hasConsistentShortSampleTrendDisclosures(
    pointWithUnknownField as unknown as DashboardData,
  ), false)

  const nonFinitePoint = structuredClone(data)
  nonFinitePoint.series![0].points[0].value = Number.POSITIVE_INFINITY
  assert.equal(hasConsistentShortSampleTrendDisclosures(nonFinitePoint), false)

  const unboundedPoints = structuredClone(data)
  unboundedPoints.series![0].points = Array.from(
    { length: 4097 },
    (_, index) => ({ time: index, value: 52 }),
  )
  assert.equal(hasConsistentShortSampleTrendDisclosures(unboundedPoints), false)

  const duplicateMetric = structuredClone(data)
  duplicateMetric.series!.push(structuredClone(duplicateMetric.series![0]))
  assert.equal(hasConsistentShortSampleTrendDisclosures(duplicateMetric), false)

  const unknownField = structuredClone(data)
  ;(unknownField.series![0].trend_disclosure as Record<string, unknown>).confidence_interval = [0, 1]
  assert.equal(hasConsistentShortSampleTrendDisclosures(unknownField), false)

  const forgedMethodCard = structuredClone(data)
  forgedMethodCard.trust!.short_sample_trend_method_card = {
    ...(forgedMethodCard.trust!.short_sample_trend_method_card as Record<string, unknown>),
    approval_status: 'approved',
  }
  forgedMethodCard.short_sample_trend_method_card = (
    forgedMethodCard.trust!.short_sample_trend_method_card
  )
  assert.equal(hasConsistentShortSampleTrendDisclosures(forgedMethodCard), false)
  assert.equal(
    sanitizeFinancialDashboard(forgedMethodCard).indices[0].trend_disclosure,
    undefined,
  )

  const malformedMetric = structuredClone(data) as unknown as {
    series: Array<Record<string, unknown>>
  }
  malformedMetric.series[0].id = null
  assert.doesNotThrow(() => hasConsistentShortSampleTrendDisclosures(
    malformedMetric as unknown as DashboardData,
  ))
  assert.equal(hasConsistentShortSampleTrendDisclosures(
    malformedMetric as unknown as DashboardData,
  ), false)
  assert.equal(
    sanitizeFinancialDashboard(malformedMetric as unknown as DashboardData).indices[0]
      .change_pct,
    null,
  )
  assert.equal(
    sanitizeFinancialDashboard(malformedMetric as unknown as DashboardData).indices[0]
      .trend_disclosure,
    undefined,
  )
})

test('a self-reported or absent composite method card cannot release precise values', () => {
  const missing = dashboard()
  missing.trust = trust({ composite_method_card: undefined })
  missing.composite_method_card = undefined
  assert.equal(dashboardAllowsComposite(missing), false)

  const selfReported = dashboard()
  const forgedCard = { ...methodCard(), approval_status: 'approved' }
  selfReported.trust = trust({
    composite_method_card: forgedCard,
  })
  selfReported.composite_method_card = forgedCard
  assert.equal(dashboardAllowsComposite(selfReported), false)
  const sanitized = sanitizeFinancialDashboard(selfReported)
  assert.equal(sanitized.indices[0].value, null)
  assert.equal(sanitized.trust?.composite_method_card, undefined)
})

test('terminal discloses units and unresolved method governance instead of a bare version', () => {
  const source = readFileSync(
    new URL('../src/pages/TerminalDashboard.tsx', import.meta.url),
    'utf8',
  )
  for (const disclosure of [
    '输入/输出单位',
    '基线未建立，阈值未批准',
    '当前按数组位置线性插值，不是按观测时间戳对齐',
    '当前空输入零填充、单值重复；修订与历史重算政策未建立',
    '短样本趋势',
    '基期未建立',
    '不确定性不可计算（方法未建立）',
    '历史序列仅供回溯；基期和不确定性方法未建立，不显示变化百分比。',
  ]) {
    assert.equal(source.includes(disclosure), true, disclosure)
  }
})

test('self-reported source coverage cannot replace a matching source inventory', () => {
  const missing = dashboard({ sources: [] })
  assert.equal(dashboardAllowsComposite(missing), false)
  assert.deepEqual(sanitizeFinancialDashboard(missing).series[0].points, [])

  const duplicate = dashboard()
  duplicate.sources[1] = { ...duplicate.sources[0] }
  assert.equal(dashboardAllowsComposite(duplicate), false)

  const contradictory = dashboard()
  contradictory.trust = trust({ source_status: { live: 3, offline: 1 } })
  contradictory.coverage = {
    ...contradictory.coverage,
    source_status: { live: 3, offline: 1 },
  }
  assert.equal(dashboardAllowsComposite(contradictory), false)
})

test('alerts pause when their trust contract is absent', () => {
  const result = sanitizeFinancialAlertData({
    rules: [{
      id: 'risk',
      metric: 'Risk',
      current: 99,
      threshold: 10,
      baseline: 5,
      unit: 'index',
      severity: 'high',
      breached: true,
      trend: 'up',
    }],
    history: [],
  })
  assert.equal(result.paused, true)
  assert.deepEqual(result.rules, [])
})
