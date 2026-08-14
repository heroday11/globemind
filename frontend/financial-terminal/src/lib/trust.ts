import type {
  AlertData,
  DashboardData,
  FinancialTrust,
  IndexTrustMetadata,
  ShortSampleTrendDisclosure,
  TrustReason,
} from '../types/index.ts'
import { sanitizeAlertTriageSummary } from './triage.ts'

export const FINANCIAL_TRUST_SCHEMA_VERSION = 'financial-trust-v1'
const FRESHNESS_STATUSES = ['live', 'delayed', 'stale', 'offline', 'mock'] as const
const DERIVED_CLAIM_IDENTITY_SCHEMA_VERSION = 'financial-derived-claim-identity-v1'
const DERIVED_CITATION_UNAVAILABLE_REASON = 'VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED' as const
const DERIVED_CLAIM_ID_PATTERN = /^fdc_[0-9a-f]{64}$/
const DERIVED_METRIC_ID_PATTERN = /^IDX-[A-Z0-9][A-Z0-9._:-]{0,123}$/
const MAX_TREND_DISCLOSURE_POINTS = 4096
const MAX_TREND_POINT_TIMESTAMP = 253_402_300_799
const MAX_ABSOLUTE_TREND_POINT_VALUE = 1_000_000_000_000_000
const TREND_REASON_CODES = [
  'BASELINE_PERIOD_NOT_ESTABLISHED',
  'TREND_METHOD_NOT_APPROVED',
  'UNCERTAINTY_METHOD_NOT_ESTABLISHED',
] as const
const INVALID_DERIVED_CLAIM_CONTRACT = {
  claim_id: null,
  claim_identity: null,
  claim_unavailable_reason: 'DERIVED_CLAIM_CONTRACT_INVALID' as const,
  citation_locator: null,
  citation_locator_state: 'unavailable' as const,
  citation_unavailable_reason: DERIVED_CITATION_UNAVAILABLE_REASON,
}

type JsonObject = Record<string, unknown>

function objectWithExactKeys(value: unknown, keys: string[]): value is JsonObject {
  return (
    Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
    && JSON.stringify(Object.keys(value as JsonObject).sort()) === JSON.stringify([...keys].sort())
  )
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as JsonObject).sort().map((key) => (
      `${JSON.stringify(key)}:${stableJson((value as JsonObject)[key])}`
    )).join(',')}}`
  }
  return JSON.stringify(value)
}

function knownCompositeMethodCard(value: unknown, methodVersion: string): value is JsonObject {
  if (!objectWithExactKeys(value, [
    'schema_version',
    'method_version',
    'implementation_status',
    'approval_status',
    'formula_status',
    'input_units_status',
    'baseline_status',
    'threshold_status',
    'normalization',
    'frequency_alignment',
    'missing_value_policy',
    'revision_policy',
    'wsi_aggregation',
    'test_vectors',
  ])) return false
  if (
    value.schema_version !== 'financial-composite-method-card-v1'
    || value.method_version !== methodVersion
    || value.implementation_status !== 'prototype_code_extracted'
    || value.approval_status !== 'not_approved'
    || value.formula_status !== 'partially_extracted_not_governed'
    || value.input_units_status !== 'not_dimensionally_validated'
    || value.baseline_status !== 'not_established'
    || value.threshold_status !== 'not_approved'
    || value.missing_value_policy !== 'not_established'
    || value.revision_policy !== 'not_established'
  ) return false
  if (!objectWithExactKeys(value.normalization, [
    'status', 'count_transform', 'non_positive_fallback', 'parameters',
  ])) return false
  if (
    value.normalization.status !== 'partially_extracted_not_governed'
    || value.normalization.count_transform !== 'min(cap, base + ln(1 + count) * scale)'
    || value.normalization.non_positive_fallback !== 'base * 0.65'
    || value.normalization.parameters !== 'vary_by_subindex_in_implementation'
  ) return false
  if (!objectWithExactKeys(value.frequency_alignment, [
    'status', 'anchor_selection', 'interpolation', 'empty_input', 'singleton_input',
  ])) return false
  if (
    value.frequency_alignment.status !== 'not_approved'
    || value.frequency_alignment.anchor_selection !== 'longest_non_empty_input_array'
    || value.frequency_alignment.interpolation !== 'linear_by_array_position_not_observation_timestamp'
    || value.frequency_alignment.empty_input !== 'zero_fill'
    || value.frequency_alignment.singleton_input !== 'repeat_single_value'
  ) return false
  const aggregation = value.wsi_aggregation
  if (!objectWithExactKeys(aggregation, [
    'status', 'component_order', 'weights', 'output_unit',
  ])) return false
  const order = ['diplomacy', 'security', 'energy', 'supply', 'technology', 'society', 'macro']
  const expectedWeights: Record<string, number> = {
    diplomacy: 0.14,
    security: 0.2,
    energy: 0.14,
    supply: 0.15,
    technology: 0.12,
    society: 0.13,
    macro: 0.12,
  }
  const weights = aggregation.weights
  if (!objectWithExactKeys(weights, order)) return false
  if (
    aggregation.status !== 'extracted_from_deterministic_implementation'
    || stableJson(aggregation.component_order) !== stableJson(order)
    || order.some((key) => (
      !Number.isFinite(weights[key])
      || weights[key] !== expectedWeights[key]
    ))
    || aggregation.output_unit !== '指数'
  ) return false
  if (!Array.isArray(value.test_vectors) || value.test_vectors.length !== 2) return false
  const [equalVector, orderedVector] = value.test_vectors
  if (
    !objectWithExactKeys(equalVector, ['id', 'inputs', 'expected'])
    || !objectWithExactKeys(orderedVector, ['id', 'inputs', 'expected'])
  ) return false
  return (
    equalVector.id === 'wsi-equal-components-v1'
    && Array.isArray(equalVector.inputs)
    && equalVector.inputs.length === 7
    && equalVector.inputs.every((item) => Number.isFinite(item) && item === 50)
    && Number.isFinite(equalVector.expected)
    && equalVector.expected === 50
    && orderedVector.id === 'wsi-ordered-components-v1'
    && stableJson(orderedVector.inputs) === stableJson([10, 20, 30, 40, 50, 60, 70])
    && Number.isFinite(orderedVector.expected)
    && orderedVector.expected === 37.8
  )
}

function knownShortSampleTrendMethodCard(value: unknown): value is JsonObject {
  if (!objectWithExactKeys(value, [
    'schema_version',
    'statistical_method_version',
    'implementation_status',
    'approval_status',
    'baseline_period_status',
    'sample_size_semantics',
    'independence_status',
    'minimum_sample_size',
    'uncertainty_method_status',
    'confidence_level',
    'outlier_policy_status',
    'release_rule',
    'maximum_provided_points',
  ])) return false
  return (
    value.schema_version === 'financial-short-sample-trend-method-card-v1'
    && value.statistical_method_version === null
    && value.implementation_status === 'disclosure_gate_only'
    && value.approval_status === 'not_approved'
    && value.baseline_period_status === 'not_established'
    && value.sample_size_semantics === 'provided_series_point_count_only'
    && value.independence_status === 'not_validated'
    && value.minimum_sample_size === null
    && value.uncertainty_method_status === 'not_established'
    && value.confidence_level === null
    && value.outlier_policy_status === 'not_established'
    && value.release_rule === 'suppress_change_pct_until_approved_method'
    && value.maximum_provided_points === MAX_TREND_DISCLOSURE_POINTS
  )
}

function knownShortSampleTrendDisclosure(
  value: unknown,
  semanticMetricId?: string,
  trust?: FinancialTrust,
): value is ShortSampleTrendDisclosure {
  if (!objectWithExactKeys(value, [
    'schema_version',
    'semantic_metric_id',
    'snapshot_id',
    'data_cutoff',
    'statistical_method_version',
    'approval_status',
    'trend_status',
    'baseline_period',
    'sample_size',
    'uncertainty',
    'outlier_policy_status',
    'reason_codes',
  ])) return false
  if (
    value.schema_version !== 'financial-short-sample-trend-v1'
    || typeof value.semantic_metric_id !== 'string'
    || !DERIVED_METRIC_ID_PATTERN.test(value.semantic_metric_id)
    || (semanticMetricId !== undefined && value.semantic_metric_id !== semanticMetricId)
    || !nonEmptyString(value.snapshot_id)
    || (value.data_cutoff !== null && !nonEmptyString(value.data_cutoff))
    || value.statistical_method_version !== null
    || value.approval_status !== 'not_approved'
    || value.trend_status !== 'not_computable'
    || value.outlier_policy_status !== 'not_established'
    || (trust !== undefined && (
      value.snapshot_id !== trust.snapshot_id
      || value.data_cutoff !== (trust.data_as_of ?? null)
    ))
  ) return false

  if (
    !objectWithExactKeys(value.baseline_period, ['status', 'start', 'end'])
    || value.baseline_period.status !== 'not_established'
    || value.baseline_period.start !== null
    || value.baseline_period.end !== null
  ) return false

  if (!objectWithExactKeys(value.sample_size, [
    'status', 'count', 'unit', 'independence_status',
  ])) return false
  const sampleAvailable = value.sample_size.status === 'provided_series_point_count'
  if (
    !['provided_series_point_count', 'not_available'].includes(
      String(value.sample_size.status),
    )
    || value.sample_size.unit !== 'provided_series_points'
    || value.sample_size.independence_status !== 'not_validated'
    || (sampleAvailable
      ? !Number.isInteger(value.sample_size.count)
        || Number(value.sample_size.count) < 0
        || Number(value.sample_size.count) > MAX_TREND_DISCLOSURE_POINTS
      : value.sample_size.count !== null)
  ) return false

  if (
    !objectWithExactKeys(value.uncertainty, [
      'status',
      'confidence_level',
      'interval_lower',
      'interval_upper',
      'reason_code',
    ])
    || value.uncertainty.status !== 'not_computable'
    || value.uncertainty.confidence_level !== null
    || value.uncertainty.interval_lower !== null
    || value.uncertainty.interval_upper !== null
    || value.uncertainty.reason_code !== 'UNCERTAINTY_METHOD_NOT_ESTABLISHED'
  ) return false

  const expectedReasons = sampleAvailable
    ? TREND_REASON_CODES
    : [...TREND_REASON_CODES, 'BOUNDED_SERIES_POINTS_NOT_AVAILABLE']
  return stableJson(value.reason_codes) === stableJson(expectedReasons)
}

export function financialTrendDisclosurePresentation(value: unknown) {
  const fallback = {
    status: 'not_computable' as const,
    preciseChangeAllowed: false as const,
    baseline: '基期未建立',
    sampleSize: '样本量不可用',
    uncertainty: '不确定性不可计算（方法未建立）',
    outliers: '异常值规则未建立',
  }
  if (!knownShortSampleTrendDisclosure(value)) return fallback
  const count = value.sample_size.count
  return {
    ...fallback,
    sampleSize: count === null
      ? fallback.sampleSize
      : `${count} 个提供的序列点（未验证为独立样本）`,
  }
}

function boundedProvidedSeriesPointCount(value: unknown): number | null {
  if (!Array.isArray(value) || value.length > MAX_TREND_DISCLOSURE_POINTS) {
    return null
  }
  for (const point of value) {
    if (!objectWithExactKeys(point, ['time', 'value'])) return null
    if (
      !Number.isSafeInteger(point.time)
      || Number(point.time) < 0
      || Number(point.time) > MAX_TREND_POINT_TIMESTAMP
      || typeof point.value !== 'number'
      || !Number.isFinite(point.value)
      || Math.abs(point.value) > MAX_ABSOLUTE_TREND_POINT_VALUE
    ) return null
  }
  return value.length
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function knownDerivedMetricClaim(
  value: IndexTrustMetadata,
  semanticMetricId: unknown,
  trust: FinancialTrust,
): boolean {
  if (
    typeof semanticMetricId !== 'string'
    || !DERIVED_METRIC_ID_PATTERN.test(semanticMetricId)
    || typeof value.claim_id !== 'string'
    || !DERIVED_CLAIM_ID_PATTERN.test(value.claim_id)
    || value.claim_unavailable_reason !== null
    || value.citation_locator !== null
    || value.citation_locator_state !== 'unavailable'
    || value.citation_unavailable_reason !== DERIVED_CITATION_UNAVAILABLE_REASON
    || !objectWithExactKeys(value.claim_identity, [
      'schema_version',
      'semantic_metric_id',
      'metric_class',
      'method_version',
      'model_version',
      'snapshot_id',
      'data_cutoff',
      'availability',
    ])
  ) return false
  const identity = value.claim_identity
  return (
    identity.schema_version === DERIVED_CLAIM_IDENTITY_SCHEMA_VERSION
    && identity.semantic_metric_id === semanticMetricId
    && identity.metric_class === 'composite_index'
    && identity.method_version === trust.method_version
    && identity.model_version === trust.model_version
    && identity.snapshot_id === trust.snapshot_id
    && identity.data_cutoff === (trust.data_as_of ?? null)
    && identity.availability === value.availability
  )
}

export function hasConsistentDerivedMetricClaims(data: DashboardData): boolean {
  const trust = data.trust
  if (!trust || !Array.isArray(data.indices) || data.indices.length === 0) return false
  if (!Array.isArray(data.series) || data.indices.length > 128 || data.series.length > 512) {
    return false
  }

  const indexClaims = new Map<string, string>()
  for (const index of data.indices) {
    if (!index || typeof index !== 'object') return false
    const semanticMetricId = index.metric_id
    if (
      !knownDerivedMetricClaim(index, semanticMetricId, trust)
      || indexClaims.has(semanticMetricId!)
    ) return false
    indexClaims.set(semanticMetricId!, index.claim_id!)
  }

  const seriesClaims = new Map<string, string>()
  for (const metric of data.series) {
    if (!metric || typeof metric !== 'object' || typeof metric.id !== 'string') {
      return false
    }
    if (!metric.id.startsWith('IDX-')) continue
    if (
      !knownDerivedMetricClaim(metric, metric.id, trust)
      || seriesClaims.has(metric.id)
    ) return false
    seriesClaims.set(metric.id, metric.claim_id!)
  }
  return (
    seriesClaims.size === indexClaims.size
    && [...indexClaims].every(([metricId, claimId]) => (
      seriesClaims.get(metricId) === claimId
    ))
  )
}

export function hasConsistentShortSampleTrendDisclosures(data: DashboardData): boolean {
  const trust = data.trust
  if (!trust || !Array.isArray(data.indices) || data.indices.length === 0) return false
  if (!Array.isArray(data.series) || data.indices.length > 128 || data.series.length > 512) {
    return false
  }
  const methodCard = trust.short_sample_trend_method_card
  if (
    !knownShortSampleTrendMethodCard(methodCard)
    || stableJson(data.short_sample_trend_method_card) !== stableJson(methodCard)
  ) return false

  const indexDisclosures = new Map<string, string>()
  for (const index of data.indices) {
    if (!index || typeof index !== 'object') return false
    const semanticMetricId = index.metric_id
    if (
      typeof semanticMetricId !== 'string'
      || !knownShortSampleTrendDisclosure(
        index.trend_disclosure,
        semanticMetricId,
        trust,
      )
      || indexDisclosures.has(semanticMetricId)
    ) return false
    indexDisclosures.set(semanticMetricId, stableJson(index.trend_disclosure))
  }

  const seriesDisclosures = new Map<string, string>()
  for (const metric of data.series) {
    if (!metric || typeof metric !== 'object' || typeof metric.id !== 'string') {
      return false
    }
    if (!metric.id.startsWith('IDX-')) continue
    const providedPointCount = boundedProvidedSeriesPointCount(metric.points)
    if (
      providedPointCount === null
      || !knownShortSampleTrendDisclosure(metric.trend_disclosure, metric.id, trust)
      || metric.trend_disclosure.sample_size.status !== 'provided_series_point_count'
      || metric.trend_disclosure.sample_size.count !== providedPointCount
      || seriesDisclosures.has(metric.id)
    ) return false
    seriesDisclosures.set(metric.id, stableJson(metric.trend_disclosure))
  }
  return (
    seriesDisclosures.size === indexDisclosures.size
    && [...indexDisclosures].every(([metricId, disclosure]) => (
      seriesDisclosures.get(metricId) === disclosure
    ))
  )
}

function validSortedSourceIds(value: unknown): value is string[] {
  return (
    Array.isArray(value)
    && value.length <= 128
    && value.every((id) => (
      typeof id === 'string'
      && /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(id)
    ))
    && new Set(value).size === value.length
    && value.every((id, index) => index === 0 || value[index - 1] <= id)
  )
}

function trustSourceSummaryMatches(trust: FinancialTrust): boolean {
  const status = trust.source_status as Record<string, unknown>
  if (!status || typeof status !== 'object' || Array.isArray(status)) return false
  if (Object.keys(status).some((key) => !FRESHNESS_STATUSES.includes(key as any))) {
    return false
  }
  const count = (key: typeof FRESHNESS_STATUSES[number]) => status[key] ?? 0
  if (FRESHNESS_STATUSES.some((key) => (
    !Number.isInteger(count(key)) || Number(count(key)) < 0
  ))) {
    return false
  }
  if (
    !Number.isInteger(trust.source_total)
    || trust.source_total <= 0
    || trust.source_total > 128
    || !validSortedSourceIds(trust.usable_source_ids)
    || !validSortedSourceIds(trust.unavailable_source_ids)
    || trust.usable_source_ids.length !== trust.usable_sources
    || trust.unavailable_source_ids.length !== trust.source_total - trust.usable_sources
    || trust.usable_source_ids.some((id) => trust.unavailable_source_ids.includes(id))
  ) {
    return false
  }
  return (
    Number(count('live'))
      + Number(count('delayed'))
      + Number(count('stale'))
      + Number(count('offline'))
    === trust.source_total
  )
}

function dashboardSourceInventoryMatches(
  data: DashboardData | AlertData,
  trust: FinancialTrust,
): boolean {
  if (!('indices' in data) && !('series' in data) && !('bars' in data)) return true
  const sources = (data as DashboardData).sources
  if (!Array.isArray(sources) || sources.length !== trust.source_total) return false

  const seen = new Set<string>()
  const usableIds: string[] = []
  const unavailableIds: string[] = []
  const statusCounts: Record<string, number> = Object.fromEntries(
    FRESHNESS_STATUSES.map((status) => [status, 0]),
  )
  for (const source of sources) {
    if (
      !source
      || typeof source !== 'object'
      || typeof source.id !== 'string'
      || !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(source.id)
      || seen.has(source.id)
      || !FRESHNESS_STATUSES.includes(source.freshness_status as any)
    ) {
      return false
    }
    seen.add(source.id)
    statusCounts[source.freshness_status!] += 1
    const usable = (
      source.freshness_status === 'live'
      && Number.isInteger(source.records)
      && Number(source.records) > 0
      && source.contribution_state === 'usable'
    )
    if (!usable && source.contribution_state !== 'not_usable') return false
    if (usable) usableIds.push(source.id)
    else unavailableIds.push(source.id)
  }
  usableIds.sort()
  unavailableIds.sort()
  if (JSON.stringify(usableIds) !== JSON.stringify(trust.usable_source_ids)) return false
  if (JSON.stringify(unavailableIds) !== JSON.stringify(trust.unavailable_source_ids)) {
    return false
  }
  return FRESHNESS_STATUSES.every((status) => (
    statusCounts[status] === Number((trust.source_status as any)[status] ?? 0)
  ))
}

function contractMatchesRoot(data: DashboardData | AlertData, trust: FinancialTrust): boolean {
  const coverage = data.coverage
  return (
    trust.schema_version === FINANCIAL_TRUST_SCHEMA_VERSION
    && data.schema_version === trust.schema_version
    && nonEmptyString(trust.snapshot_id)
    && data.snapshot_id === trust.snapshot_id
    && data.trust_status === trust.trust_status
    && data.freshness_status === trust.freshness_status
    && data.computability === trust.computability
    && data.computable === trust.computable
    && data.alerts_enabled === trust.alerts_enabled
    && data.data_as_of === trust.data_as_of
    && data.model_version === trust.model_version
    && data.method_version === trust.method_version
    && stableJson(data.composite_method_card) === stableJson(trust.composite_method_card)
    && stableJson(data.short_sample_trend_method_card)
      === stableJson(trust.short_sample_trend_method_card)
    && JSON.stringify(data.unavailable_reasons) === JSON.stringify(trust.unavailable_reasons)
    && coverage?.coverage_ratio === trust.coverage_ratio
    && coverage.minimum_coverage_ratio === trust.minimum_coverage_ratio
    && coverage.usable_sources === trust.usable_sources
    && coverage.sources_total === trust.source_total
    && JSON.stringify(coverage.source_status) === JSON.stringify(trust.source_status)
  )
}

export function hasConsistentFinancialTrust(data: DashboardData | AlertData): boolean {
  const trust = data.trust
  if (!trust || !contractMatchesRoot(data, trust)) return false
  if (trust.computable !== (trust.computability === 'computable')) return false
  if (trust.alerts_enabled !== trust.computable) return false
  if (trust.computable) {
    return (
      (trust.trust_status === 'trusted' || trust.trust_status === 'limited')
      && (trust.freshness_status === 'live' || trust.freshness_status === 'delayed')
      && nonEmptyString(trust.data_as_of)
      && Number.isFinite(trust.coverage_ratio)
      && Number.isFinite(trust.minimum_coverage_ratio)
      && trust.minimum_coverage_ratio >= 0.5
      && trust.coverage_ratio >= trust.minimum_coverage_ratio
      && Number.isInteger(trust.usable_sources)
      && trust.usable_sources > 0
      && trustSourceSummaryMatches(trust)
      && dashboardSourceInventoryMatches(data, trust)
      && trust.unavailable_reasons.length === 0
    )
  }
  return trust.trust_status === 'unavailable' && trust.alerts_enabled === false
}

export function dashboardAllowsComposite(data: DashboardData): boolean {
  if (
    !hasConsistentFinancialTrust(data)
    || !hasConsistentDerivedMetricClaims(data)
    || !hasConsistentShortSampleTrendDisclosures(data)
    || data.trust?.computable !== true
  ) return false
  const methodCard = data.trust.composite_method_card
  if (
    !knownCompositeMethodCard(methodCard, data.trust.method_version)
    || methodCard.approval_status !== 'approved'
  ) return false
  const trendMethodCard = data.trust.short_sample_trend_method_card
  if (
    !knownShortSampleTrendMethodCard(trendMethodCard)
    || trendMethodCard.approval_status !== 'approved'
  ) return false
  const snapshotId = data.trust.snapshot_id
  return (data.indices || []).every((index) => (
    index.availability === 'available'
    && index.schema_version === FINANCIAL_TRUST_SCHEMA_VERSION
    && index.snapshot_id === snapshotId
    && typeof index.value === 'number'
    && Number.isFinite(index.value)
    && typeof index.change_pct === 'number'
    && Number.isFinite(index.change_pct)
  )) && (data.series || [])
    .filter((metric) => metric.id.startsWith('IDX-'))
    .every((metric) => (
      metric.availability === 'available'
      && metric.schema_version === FINANCIAL_TRUST_SCHEMA_VERSION
      && metric.snapshot_id === snapshotId
      && typeof metric.latest === 'number'
      && Number.isFinite(metric.latest)
      && typeof metric.change_pct === 'number'
      && Number.isFinite(metric.change_pct)
    ))
}

function unavailableTrust(
  reason: TrustReason,
  existing?: FinancialTrust | null,
): FinancialTrust {
  const reasons = Array.isArray(existing?.unavailable_reasons)
    ? existing.unavailable_reasons
    : []
  const safeMethodCard = (
    existing
    && knownCompositeMethodCard(existing.composite_method_card, existing.method_version)
  ) ? existing.composite_method_card : undefined
  const safeTrendMethodCard = (
    existing
    && knownShortSampleTrendMethodCard(existing.short_sample_trend_method_card)
  ) ? existing.short_sample_trend_method_card : undefined
  return {
    schema_version: FINANCIAL_TRUST_SCHEMA_VERSION,
    snapshot_id: existing?.snapshot_id || `client-unavailable-${Date.now()}`,
    trust_status: 'unavailable',
    freshness_status: existing?.freshness_status && existing.freshness_status !== 'mock'
      ? existing.freshness_status
      : 'offline',
    computability: 'not_computable',
    computable: false,
    data_as_of: existing?.data_as_of || null,
    evaluated_at: new Date().toISOString(),
    coverage_ratio: Number.isFinite(existing?.coverage_ratio) ? existing!.coverage_ratio : 0,
    minimum_coverage_ratio: Number.isFinite(existing?.minimum_coverage_ratio)
      ? existing!.minimum_coverage_ratio
      : 0.5,
    usable_sources: Number.isFinite(existing?.usable_sources) ? existing!.usable_sources : 0,
    source_total: Number.isInteger(existing?.source_total) ? existing!.source_total : 0,
    usable_source_ids: Array.isArray(existing?.usable_source_ids)
      ? existing!.usable_source_ids
      : [],
    unavailable_source_ids: Array.isArray(existing?.unavailable_source_ids)
      ? existing!.unavailable_source_ids
      : [],
    source_status: existing?.source_status || { offline: 1 },
    critical_inputs: existing?.critical_inputs,
    model_version: existing?.model_version || 'unknown',
    method_version: existing?.method_version || 'unknown',
    composite_method_card: safeMethodCard,
    short_sample_trend_method_card: safeTrendMethodCard,
    unavailable_reasons: reasons.length ? reasons : [reason],
    alerts_enabled: false,
    method: existing?.method,
  }
}

export function sanitizeFinancialDashboard(data: DashboardData): DashboardData {
  if (dashboardAllowsComposite(data)) return data
  const consistent = hasConsistentFinancialTrust(data)
  const preserveDerivedClaims = consistent && hasConsistentDerivedMetricClaims(data)
  const preserveTrendDisclosures = (
    consistent && hasConsistentShortSampleTrendDisclosures(data)
  )
  const trust = unavailableTrust({
    code: consistent ? 'COMPOSITE_NOT_COMPUTABLE' : 'INVALID_TRUST_CONTRACT',
    message: consistent
      ? 'The financial composite is not computable.'
      : 'The financial trust contract is missing, malformed, or contradictory.',
  }, data.trust)
  return {
    ...data,
    mode: trust.freshness_status === 'stale' ? 'historical' : 'unavailable',
    bars: [],
    ma20: [],
    ma50: [],
    ma200: [],
    indices: (data.indices || []).map((index) => ({
      ...index,
      ...(preserveDerivedClaims ? {} : INVALID_DERIVED_CLAIM_CONTRACT),
      ...(preserveTrendDisclosures ? {} : { trend_disclosure: undefined }),
      value: null,
      change_pct: null,
      spark: [],
      availability: 'not_computable',
      trust_status: 'unavailable',
      freshness_status: trust.freshness_status,
      schema_version: trust.schema_version,
      snapshot_id: trust.snapshot_id,
      unavailable_reasons: trust.unavailable_reasons,
    })),
    series: (data.series || []).map((metric) => {
      const hasValidMetricId = Boolean(
        metric
        && typeof metric === 'object'
        && typeof metric.id === 'string',
      )
      if (hasValidMetricId && !metric.id.startsWith('IDX-')) return metric
      return {
        ...metric,
        ...((preserveDerivedClaims && hasValidMetricId)
          ? {}
          : INVALID_DERIVED_CLAIM_CONTRACT),
        ...(preserveTrendDisclosures ? {} : { trend_disclosure: undefined }),
        latest: null,
        change_pct: null,
        points: [],
        status: 'unavailable',
        availability: 'not_computable',
        trust_status: 'unavailable',
        freshness_status: trust.freshness_status,
        schema_version: trust.schema_version,
        snapshot_id: trust.snapshot_id,
        unavailable_reasons: trust.unavailable_reasons,
      }
    }),
    alert_rules: [],
    alerts_suppressed: true,
    trust,
    trust_status: trust.trust_status,
    freshness_status: trust.freshness_status,
    computability: trust.computability,
    computable: trust.computable,
    alerts_enabled: trust.alerts_enabled,
    data_as_of: trust.data_as_of,
    coverage: {
      ...(data.coverage || {}),
      coverage_ratio: trust.coverage_ratio,
      minimum_coverage_ratio: trust.minimum_coverage_ratio,
      usable_sources: trust.usable_sources,
      sources_total: trust.source_total,
      source_status: trust.source_status,
    },
    schema_version: trust.schema_version,
    snapshot_id: trust.snapshot_id,
    model_version: trust.model_version,
    method_version: trust.method_version,
    composite_method_card: trust.composite_method_card,
    short_sample_trend_method_card: trust.short_sample_trend_method_card,
    unavailable_reasons: trust.unavailable_reasons,
  }
}

export function sanitizeFinancialAlertData(data: AlertData): AlertData {
  const sanitizedHistory = Array.isArray(data.history)
    ? data.history.map((report) => {
      const { triage: rawTriage, ...safeReport } = report
      const triage = sanitizeAlertTriageSummary(rawTriage)
      return triage ? { ...safeReport, triage } : safeReport
    })
    : []
  if (hasConsistentFinancialTrust(data) && data.trust?.alerts_enabled === true) {
    return { ...data, history: sanitizedHistory, paused: false }
  }
  const trust = unavailableTrust({
    code: 'INVALID_ALERT_TRUST_CONTRACT',
    message: 'Alert evaluation is paused because its trust contract is unavailable.',
  }, data.trust)
  return {
    ...data,
    rules: [],
    history: sanitizedHistory,
    paused: true,
    trust,
    trust_status: trust.trust_status,
    freshness_status: trust.freshness_status,
    computability: trust.computability,
    computable: trust.computable,
    alerts_enabled: trust.alerts_enabled,
    data_as_of: trust.data_as_of,
    coverage: {
      ...(data.coverage || {}),
      coverage_ratio: trust.coverage_ratio,
      minimum_coverage_ratio: trust.minimum_coverage_ratio,
      usable_sources: trust.usable_sources,
      sources_total: trust.source_total,
      source_status: trust.source_status,
    },
    schema_version: trust.schema_version,
    snapshot_id: trust.snapshot_id,
    model_version: trust.model_version,
    method_version: trust.method_version,
    composite_method_card: trust.composite_method_card,
    unavailable_reasons: trust.unavailable_reasons,
  }
}
