import { readonly, shallowRef } from 'vue'

import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'

const FEATURE_FRESHNESS_ENDPOINT = `${API_PREFIX}/status`
const PUBLIC_STATUS_SCHEMA_VERSION = 'globemind.public-status.v1'
const MAX_PUBLIC_STATUS_AGE_MS = 15 * 60 * 1000
const SAFE_FRESHNESS_STATES = new Set(['live', 'not_applicable'])
const FRESHNESS_NOTICE_STATES = new Set([
  'live',
  'delayed',
  'stale',
  'missing',
  'offline',
  'unknown',
  'not_applicable',
])
const FEATURE_CUTOFF_METRICS = Object.freeze({
  search: 'latest_news_at',
  'ground-news': 'latest_story_source_at',
  'opinion-analysis': 'latest_score_date',
})
const HOME_MODULE_EVIDENCE_SCHEMA_VERSION = 'globemind.home-module-evidence.v1'
const HOME_MODULE_METHOD = Object.freeze({
  id: 'business-freshness-health-projection',
  version: 'v1',
  status: 'configured',
})
const FEATURE_HOME_MODULE_EVIDENCE = Object.freeze({
  search: Object.freeze({
    moduleId: 'home-data-search',
    scopeId: 'public-news-event-search',
    scopeLabel: '公开新闻与事件检索结果',
  }),
  'ground-news': Object.freeze({
    moduleId: 'home-ground-news',
    scopeId: 'public-ground-news-story-sources',
    scopeLabel: '公开事件卡、报道与来源构成',
  }),
  'opinion-analysis': Object.freeze({
    moduleId: 'home-opinion-analysis',
    scopeId: 'public-opinion-analysis-scores',
    scopeLabel: '公开涉华舆情聚合结果',
  }),
})
const FRESHNESS_MONITORED_FEATURES = Object.freeze(Object.keys(FEATURE_CUTOFF_METRICS))
const FRESHNESS_OBJECTIVES = Object.freeze({
  'search-freshness': '新闻与事件检索数据新鲜度',
  'ground-news-freshness': '全球新闻观察数据新鲜度',
  'opinion-analysis-freshness': '涉华舆情分析数据新鲜度',
})
const FRESHNESS_LAG_TOLERANCE_HOURS = 0.11
const WORKFLOW_OBJECTIVES = Object.freeze({
  'search-response': '检索响应',
  'export-delivery': '导出交付',
  'report-generation': '报告生成',
})
const WORKFLOW_MEASUREMENT_STATES = new Set([
  'observed',
  'not_observed',
  'partial',
  'unavailable',
])
const MAX_MAINTENANCE_EVENTS = 100
const MAINTENANCE_HISTORY_MAX_AGE_MS = 24 * 60 * 60 * 1000
const MAINTENANCE_FEATURES = new Set(FRESHNESS_MONITORED_FEATURES)
const MAINTENANCE_HISTORY_KEYS = Object.freeze([
  'status',
  'freshness',
  'generated_at',
  'events',
  'reason',
  'retention',
  'subscription',
  'owner',
  'bounds',
])
const MAINTENANCE_EVENT_KEYS = Object.freeze([
  'id',
  'type',
  'status',
  'title',
  'summary',
  'started_at',
  'ended_at',
  'affected_features',
])
const DEGRADATION_DISCLOSURE_KEYS = Object.freeze([
  'status',
  'trigger',
  'incident_owner',
  'recovery_estimate',
  'last_status_update',
  'reason',
])

function parseTimestamp(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : new Date(value.getTime())
  }
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function parseContractTimestamp(value, { allowDateOnly = false } = {}) {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (allowDateOnly && /^\d{4}-\d{2}-\d{2}$/.test(normalized)) return parseTimestamp(normalized)
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(normalized)) return null
  return parseTimestamp(normalized)
}

function nonnegativeNumber(value, { positive = false } = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  if (positive && value === 0) return null
  return value
}

function nonnegativeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}

function normalizeWorkflowMetrics(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const counts = Object.fromEntries(
    ['sample_count', 'success_count', 'error_count', 'timeout_count', 'cancelled_count']
      .map((key) => [key, nonnegativeInteger(value[key])]),
  )
  if (Object.values(counts).some((item) => item === null) || counts.sample_count === 0) return null
  if (
    counts.success_count
      + counts.error_count
      + counts.timeout_count
      + counts.cancelled_count
    !== counts.sample_count
  ) return null
  const successRate = nonnegativeNumber(value.success_rate)
  const errorRate = nonnegativeNumber(value.error_rate)
  const p50 = nonnegativeInteger(value.p50_ms)
  const p95 = nonnegativeInteger(value.p95_ms)
  const p99 = nonnegativeInteger(value.p99_ms)
  if (
    successRate === null
    || errorRate === null
    || successRate > 1
    || errorRate > 1
    || Math.abs(successRate - (counts.success_count / counts.sample_count)) > 1e-12
    || Math.abs(errorRate - (1 - successRate)) > 1e-12
    || p50 === null
    || p95 === null
    || p99 === null
    || p50 > p95
    || p95 > p99
  ) return null
  return Object.freeze({
    ...counts,
    success_rate: successRate,
    error_rate: errorRate,
    p50_ms: p50,
    p95_ms: p95,
    p99_ms: p99,
  })
}

function normalizeWorkflowObjectives(value) {
  if (!Array.isArray(value) || value.length !== Object.keys(WORKFLOW_OBJECTIVES).length) {
    throw new Error('数据状态响应缺少服务级观测契约')
  }
  const seen = new Set()
  const normalized = value.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error('服务级观测契约无效')
    }
    const id = typeof item.id === 'string' ? item.id : ''
    const measurementStatus = typeof item.measurement_status === 'string'
      ? item.measurement_status
      : ''
    const reason = typeof item.reason === 'string' ? item.reason.trim() : ''
    const indicator = typeof item.indicator === 'string' ? item.indicator.trim() : ''
    const observed = item.observed === null ? null : normalizeWorkflowMetrics(item.observed)
    if (
      !Object.hasOwn(WORKFLOW_OBJECTIVES, id)
      || seen.has(id)
      || item.label !== WORKFLOW_OBJECTIVES[id]
      || !WORKFLOW_MEASUREMENT_STATES.has(measurementStatus)
      || item.objective !== null
      || item.compliance !== 'not_computable'
      || item.approval_state !== 'not_approved'
      || !reason
      || !indicator
      || (['observed', 'partial'].includes(measurementStatus) && observed === null)
      || (['not_observed', 'unavailable'].includes(measurementStatus) && item.observed !== null)
    ) {
      throw new Error('服务级观测契约矛盾')
    }
    seen.add(id)
    return Object.freeze({
      id,
      label: item.label,
      indicator,
      measurement_status: measurementStatus,
      objective: null,
      observed,
      compliance: 'not_computable',
      approval_state: 'not_approved',
      reason,
      source: typeof item.source === 'string' ? item.source : '',
    })
  })
  if (seen.size !== Object.keys(WORKFLOW_OBJECTIVES).length) {
    throw new Error('服务级观测契约不完整')
  }
  return Object.freeze(normalized)
}

function normalizeFreshnessObjectives(value, features) {
  if (!Array.isArray(value) || value.length !== Object.keys(FRESHNESS_OBJECTIVES).length) {
    throw new Error('数据状态响应缺少新鲜度目标契约')
  }
  const seen = new Set()
  const normalized = value.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw new Error('新鲜度目标契约无效')
    }
    const id = typeof item.id === 'string' ? item.id : ''
    const featureId = id.endsWith('-freshness') ? id.slice(0, -'-freshness'.length) : ''
    const feature = features[featureId]
    const threshold = item.threshold
    const thresholdValue = nonnegativeNumber(threshold?.value, { positive: true })
    const observedValue = nonnegativeNumber(item.observed?.value)
    const expectedAssessment = thresholdValue === null || observedValue === null
      ? 'unknown'
      : observedValue <= thresholdValue
        ? 'within'
        : 'exceeded'
    const expectedMeasurement = thresholdValue === null
      ? observedValue === null ? 'unavailable' : 'partial'
      : observedValue === null ? 'partial' : 'active'
    if (
      !Object.hasOwn(FRESHNESS_OBJECTIVES, id)
      || seen.has(id)
      || !feature
      || item.label !== FRESHNESS_OBJECTIVES[id]
      || item.indicator !== '最后有效数据距当前时间'
      || item.measurement_status !== expectedMeasurement
      || item.objective !== null
      || item.compliance !== 'not_computable'
      || item.approval_state !== 'not_approved'
      || item.threshold_assessment !== expectedAssessment
      || (thresholdValue === null && item.threshold !== null)
      || (thresholdValue !== null && (
        threshold?.comparison !== 'less_than_or_equal'
        || threshold?.unit !== 'hours'
        || thresholdValue !== feature.slaHours
      ))
      || (observedValue === null && item.observed !== null)
      || (observedValue !== null && (
        item.observed?.unit !== 'hours'
        || observedValue !== feature.lagHours
      ))
      || typeof item.reason !== 'string'
      || !item.reason.trim()
    ) {
      throw new Error('新鲜度目标契约矛盾')
    }
    seen.add(id)
    return Object.freeze({
      id,
      label: item.label,
      indicator: item.indicator,
      measurement_status: expectedMeasurement,
      objective: null,
      threshold: thresholdValue === null
        ? null
        : Object.freeze({
          comparison: 'less_than_or_equal',
          value: thresholdValue,
          unit: 'hours',
        }),
      observed: observedValue === null
        ? null
        : Object.freeze({ value: observedValue, unit: 'hours' }),
      threshold_assessment: expectedAssessment,
      compliance: 'not_computable',
      approval_state: 'not_approved',
      reason: item.reason.trim(),
      source: typeof item.source === 'string' ? item.source : '',
    })
  })
  if (seen.size !== Object.keys(FRESHNESS_OBJECTIVES).length) {
    throw new Error('新鲜度目标契约不完整')
  }
  return Object.freeze(normalized)
}

function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const keys = Object.keys(value)
  return keys.length === expected.length && expected.every((key) => Object.hasOwn(value, key))
}

function boundedPublicText(value, maximum) {
  if (typeof value !== 'string' || !value || value !== value.trim() || value.length > maximum) {
    return ''
  }
  return [...value].some((character) => {
    const point = character.codePointAt(0)
    return point < 32 || point === 127 || (point >= 0xd800 && point <= 0xdfff)
  }) ? '' : value
}

function normalizeMaintenanceGovernance(value) {
  const retention = value.retention
  const subscription = value.subscription
  const owner = value.owner
  const bounds = value.bounds
  if (
    !exactKeys(retention, ['status', 'published_event_limit'])
    || retention.status !== 'not_approved'
    || retention.published_event_limit !== MAX_MAINTENANCE_EVENTS
    || !exactKeys(subscription, ['status'])
    || subscription.status !== 'not_configured'
    || !exactKeys(owner, ['status'])
    || owner.status !== 'not_configured'
    || !exactKeys(bounds, [
      'max_source_bytes',
      'max_events',
      'max_title_chars',
      'max_summary_chars',
      'max_affected_features',
    ])
    || bounds.max_source_bytes !== 65536
    || bounds.max_events !== MAX_MAINTENANCE_EVENTS
    || bounds.max_title_chars !== 120
    || bounds.max_summary_chars !== 500
    || bounds.max_affected_features !== 20
  ) throw new Error('事件历史治理契约无效')
  return Object.freeze({
    retention: Object.freeze({ status: 'not_approved', published_event_limit: 100 }),
    subscription: Object.freeze({ status: 'not_configured' }),
    owner: Object.freeze({ status: 'not_configured' }),
    bounds: Object.freeze({ ...bounds }),
  })
}

function normalizeMaintenanceEvent(value, { evaluatedAt }) {
  if (!exactKeys(value, MAINTENANCE_EVENT_KEYS)) throw new Error('维护事件契约无效')
  const id = boundedPublicText(value.id, 64)
  const title = boundedPublicText(value.title, 120)
  const summary = boundedPublicText(value.summary, 500)
  const startedAt = parseContractTimestamp(value.started_at)
  const endedAt = parseContractTimestamp(value.ended_at)
  const expectedState = value.type === 'maintenance'
    ? ['completed', 'cancelled'].includes(value.status)
    : value.type === 'incident' && value.status === 'resolved'
  const affected = value.affected_features
  if (
    !id
    || !/^[a-z0-9][a-z0-9._:-]{0,63}$/.test(id)
    || !title
    || !summary
    || !expectedState
    || !startedAt
    || !endedAt
    || startedAt.getTime() > endedAt.getTime()
    || endedAt.getTime() > evaluatedAt.getTime()
    || !Array.isArray(affected)
    || affected.length < 1
    || affected.length > 20
    || new Set(affected).size !== affected.length
    || affected.some((featureId) => !MAINTENANCE_FEATURES.has(featureId))
  ) throw new Error('维护事件契约矛盾')
  return Object.freeze({
    id,
    type: value.type,
    status: value.status,
    title,
    summary,
    started_at: startedAt.toISOString(),
    ended_at: endedAt.toISOString(),
    affected_features: Object.freeze([...affected]),
  })
}

function normalizeIncidentHistory(value, { evaluatedAt }) {
  if (
    exactKeys(value, ['status', 'reason'])
    && value.status === 'not_available'
    && boundedPublicText(value.reason, 500)
  ) {
    return Object.freeze({ status: 'not_available', reason: value.reason })
  }
  if (!exactKeys(value, MAINTENANCE_HISTORY_KEYS)) throw new Error('事件历史契约无效')
  const governance = normalizeMaintenanceGovernance(value)
  const reason = boundedPublicText(value.reason, 500)
  if (!reason || !['available', 'not_configured', 'unavailable'].includes(value.status)) {
    throw new Error('事件历史契约无效')
  }
  if (value.status !== 'available') {
    if (
      value.freshness !== 'unknown'
      || value.generated_at !== null
      || !Array.isArray(value.events)
      || value.events.length !== 0
    ) throw new Error('事件历史不可用状态矛盾')
    return Object.freeze({
      status: value.status,
      freshness: 'unknown',
      generatedAt: '',
      events: Object.freeze([]),
      reason,
      ...governance,
    })
  }
  const generatedAt = parseContractTimestamp(value.generated_at)
  if (
    !generatedAt
    || generatedAt.getTime() > evaluatedAt.getTime()
    || !Array.isArray(value.events)
    || value.events.length > MAX_MAINTENANCE_EVENTS
  ) throw new Error('事件历史时间或数量无效')
  const expectedFreshness = evaluatedAt.getTime() - generatedAt.getTime()
    <= MAINTENANCE_HISTORY_MAX_AGE_MS ? 'current' : 'stale'
  if (value.freshness !== expectedFreshness) throw new Error('事件历史新鲜度矛盾')
  const events = value.events.map((event) => normalizeMaintenanceEvent(event, { evaluatedAt }))
  const ids = new Set()
  let previousStartedAt = Number.POSITIVE_INFINITY
  for (const event of events) {
    const startedAt = Date.parse(event.started_at)
    if (
      ids.has(event.id)
      || startedAt >= previousStartedAt
      || Date.parse(event.ended_at) > generatedAt.getTime()
    ) throw new Error('事件历史顺序矛盾')
    ids.add(event.id)
    previousStartedAt = startedAt
  }
  return Object.freeze({
    status: 'available',
    freshness: expectedFreshness,
    generatedAt: generatedAt.toISOString(),
    events: Object.freeze(events),
    reason,
    ...governance,
  })
}

function normalizeUnavailableResponseField(value) {
  if (
    !exactKeys(value, ['availability', 'value'])
    || value.availability !== 'unavailable'
    || value.value !== null
  ) throw new Error('降级处置公开字段无效')
  return Object.freeze({ availability: 'unavailable', value: null })
}

function normalizeDegradationDisclosure(value, features) {
  if (!exactKeys(value, DEGRADATION_DISCLOSURE_KEYS)) {
    throw new Error('降级处置公开契约无效')
  }
  const trigger = value.trigger
  const reason = boundedPublicText(value.reason, 500)
  if (!exactKeys(trigger, [
    'capability_state',
    'affected_capability_ids',
    'workflow_breach_state',
    'affected_workflow_ids',
  ]) || !reason) throw new Error('降级处置触发契约无效')

  const expectedAffected = FRESHNESS_MONITORED_FEATURES.filter(
    (featureId) => features[featureId]?.state === 'offline',
  )
  const affected = trigger.affected_capability_ids
  const expectedAction = expectedAffected.length > 0
  if (
    !Array.isArray(affected)
    || affected.length > FRESHNESS_MONITORED_FEATURES.length
    || new Set(affected).size !== affected.length
    || affected.some((featureId, index) => featureId !== expectedAffected[index])
    || trigger.capability_state !== (expectedAction ? 'down_observed' : 'no_down_observed')
    || value.status !== (expectedAction ? 'action_required' : 'monitoring')
    || trigger.workflow_breach_state !== 'unknown'
    || !Array.isArray(trigger.affected_workflow_ids)
    || trigger.affected_workflow_ids.length !== 0
  ) throw new Error('降级处置公开契约矛盾')

  return Object.freeze({
    status: value.status,
    trigger: Object.freeze({
      capabilityState: trigger.capability_state,
      affectedCapabilityIds: Object.freeze([...affected]),
      workflowBreachState: 'unknown',
      affectedWorkflowIds: Object.freeze([]),
    }),
    incidentOwner: normalizeUnavailableResponseField(value.incident_owner),
    recoveryEstimate: normalizeUnavailableResponseField(value.recovery_estimate),
    lastStatusUpdate: normalizeUnavailableResponseField(value.last_status_update),
    reason,
  })
}

function normalizeHomeModuleEvidence(value, featureId, { hasCutoff, evidenceUsable }) {
  const expected = FEATURE_HOME_MODULE_EVIDENCE[featureId]
  if (
    !expected
    || !exactKeys(value, [
      'schema_version',
      'module_id',
      'scope',
      'cutoff_metric',
      'cutoff_status',
      'method',
      'evidence_status',
    ])
    || value.schema_version !== HOME_MODULE_EVIDENCE_SCHEMA_VERSION
    || value.module_id !== expected.moduleId
    || !exactKeys(value.scope, ['id', 'label'])
    || value.scope.id !== expected.scopeId
    || value.scope.label !== expected.scopeLabel
    || value.cutoff_metric !== FEATURE_CUTOFF_METRICS[featureId]
    || value.cutoff_status !== (hasCutoff ? 'available' : 'unknown')
    || !exactKeys(value.method, ['id', 'version', 'status'])
    || value.method.id !== HOME_MODULE_METHOD.id
    || value.method.version !== HOME_MODULE_METHOD.version
    || value.method.status !== HOME_MODULE_METHOD.status
    || value.evidence_status !== (
      evidenceUsable ? 'contract_validated' : 'unavailable'
    )
  ) return null
  return Object.freeze({
    schemaVersion: HOME_MODULE_EVIDENCE_SCHEMA_VERSION,
    moduleId: expected.moduleId,
    scope: Object.freeze({ id: expected.scopeId, label: expected.scopeLabel }),
    cutoffMetric: FEATURE_CUTOFF_METRICS[featureId],
    cutoffStatus: value.cutoff_status,
    method: HOME_MODULE_METHOD,
    evidenceStatus: value.evidence_status,
  })
}

export function normalizeFeatureFreshness(check, featureId = '', { evaluatedAt = null } = {}) {
  if (!check || typeof check !== 'object') {
    return Object.freeze({
      featureId,
      label: featureId,
      state: 'unknown',
      historical: true,
      researchUse: 'unavailable',
      cutoff: '',
      lagHours: null,
      slaHours: null,
      detail: '数据状态尚未确认',
    })
  }

  const metrics = check.metrics && typeof check.metrics === 'object' ? check.metrics : {}
  const backendFreshness = String(metrics.freshness_status || '').trim().toLowerCase()
  const capabilityStatus = String(check.status || '').trim().toLowerCase()
  const normalizedFeatureId = String(check.feature_id || featureId)
  const expectedCutoffMetric = FEATURE_CUTOFF_METRICS[featureId]
  const cutoffTimestamp = parseContractTimestamp(metrics[expectedCutoffMetric], {
    allowDateOnly: expectedCutoffMetric === 'latest_score_date',
  })
  const generatedTimestamp = parseTimestamp(evaluatedAt)
  const cutoffIsFuture = Boolean(
    cutoffTimestamp
      && generatedTimestamp
      && cutoffTimestamp.getTime() > generatedTimestamp.getTime() + 5 * 60 * 1000,
  )
  const cutoff = cutoffTimestamp && !cutoffIsFuture ? cutoffTimestamp.toISOString() : ''
  const reportedLagHours = nonnegativeNumber(metrics.freshness_lag_hours)
  const slaHours = nonnegativeNumber(metrics.freshness_sla_hours, { positive: true })
  let state = 'unknown'

  if (cutoffTimestamp && generatedTimestamp && reportedLagHours !== null) {
    const derivedLagHours = Math.max(
      0,
      (generatedTimestamp.getTime() - cutoffTimestamp.getTime()) / (60 * 60 * 1000),
    )
    if (Math.abs(derivedLagHours - reportedLagHours) > FRESHNESS_LAG_TOLERANCE_HOURS) {
      throw new Error('新鲜度证据矛盾')
    }
  }
  const lagHours = cutoff ? reportedLagHours : null

  if (!expectedCutoffMetric || normalizedFeatureId !== featureId) state = 'unknown'
  else if (capabilityStatus === 'down' || backendFreshness === 'offline') state = 'offline'
  else if (!cutoff) state = 'unknown'
  else if (backendFreshness === 'stale' || capabilityStatus === 'stale') state = 'stale'
  else if (backendFreshness === 'delayed' || capabilityStatus === 'degraded') state = 'delayed'
  else if (
    backendFreshness === 'live'
    && capabilityStatus === 'up'
    && lagHours !== null
    && slaHours !== null
    && lagHours <= slaHours
  ) state = 'live'

  const moduleEvidence = normalizeHomeModuleEvidence(check.module_evidence, featureId, {
    hasCutoff: Boolean(cutoff),
    evidenceUsable: Boolean(cutoff) && state !== 'offline' && state !== 'unknown',
  })

  return Object.freeze({
    featureId: normalizedFeatureId,
    label: String(check.label || normalizedFeatureId),
    state,
    historical: !SAFE_FRESHNESS_STATES.has(state),
    researchUse: state === 'live' ? 'current' : state === 'offline' || state === 'unknown' ? 'unavailable' : 'historical',
    cutoff,
    lagHours,
    slaHours,
    detail: String(check.detail || ''),
    moduleEvidence,
  })
}

export function normalizeFeatureFreshnessReport(payload, { receivedAt = new Date() } = {}) {
  if (payload?.schema_version !== PUBLIC_STATUS_SCHEMA_VERSION) {
    throw new Error('数据状态响应 schema 不兼容')
  }
  const generatedAt = parseContractTimestamp(payload?.generated_at)
  if (!(receivedAt instanceof Date) || Number.isNaN(receivedAt.getTime())) {
    throw new Error('数据状态响应接收时间无效')
  }
  const receivedTimestamp = receivedAt
  if (
    !generatedAt
    || (receivedTimestamp && generatedAt.getTime() > receivedTimestamp.getTime() + 5 * 60 * 1000)
    || (receivedTimestamp && receivedTimestamp.getTime() - generatedAt.getTime() > MAX_PUBLIC_STATUS_AGE_MS)
  ) {
    throw new Error('数据状态响应生成时间无效')
  }
  const checks = payload?.checks
  if (!checks || typeof checks !== 'object' || Array.isArray(checks)) {
    throw new Error('数据状态响应缺少 checks')
  }
  if (FRESHNESS_MONITORED_FEATURES.some((featureId) => !Object.hasOwn(checks, featureId))) {
    throw new Error('数据状态响应缺少受监测能力')
  }
  const features = Object.fromEntries(
    FRESHNESS_MONITORED_FEATURES.map((featureId) => [
      featureId,
      normalizeFeatureFreshness(checks[featureId], featureId, { evaluatedAt: generatedAt }),
    ]),
  )
  const affected = Object.values(features).filter((feature) => feature.historical)
  const cutoffs = affected
    .map((feature) => parseTimestamp(feature.cutoff))
    .filter(Boolean)

  const derivedStatus = affected.some((feature) => ['offline', 'unknown'].includes(feature.state))
    ? 'unavailable'
    : affected.length
      ? 'historical'
      : 'current'
  const derivedResearchMode = derivedStatus === 'current' ? 'current' : 'historical'
  const derivedReady = !affected.some((feature) => ['offline', 'unknown'].includes(feature.state))
  if (
    payload.status !== derivedStatus
    || payload.research_mode !== derivedResearchMode
    || payload.ready !== derivedReady
  ) {
    throw new Error('数据状态响应内部冲突')
  }
  const workflowObjectives = normalizeWorkflowObjectives(payload?.objectives?.workflows)
  const freshnessObjectives = normalizeFreshnessObjectives(
    payload?.objectives?.freshness,
    features,
  )
  const incidentHistory = normalizeIncidentHistory(payload?.incident_history, {
    evaluatedAt: generatedAt,
  })
  const degradationDisclosure = normalizeDegradationDisclosure(
    payload?.degradation_disclosure,
    features,
  )

  return Object.freeze({
    phase: 'ready',
    schemaVersion: PUBLIC_STATUS_SCHEMA_VERSION,
    generatedAt: generatedAt.toISOString(),
    backendStatus: derivedStatus,
    researchMode: derivedResearchMode,
    ready: derivedReady,
    features: Object.freeze(features),
    affected: Object.freeze(affected),
    historicalMode: affected.length > 0,
    cutoff: cutoffs.length
      ? new Date(Math.min(...cutoffs.map((value) => value.getTime()))).toISOString()
      : '',
    receivedAt: receivedTimestamp
      ? receivedTimestamp.toISOString()
      : '',
    objectives: Object.freeze({
      freshness: freshnessObjectives,
      workflows: Object.freeze(
        workflowObjectives,
      ),
    }),
    incidentHistory,
    degradationDisclosure,
    error: '',
  })
}

export function unavailableFeatureFreshnessReport(error = '', { receivedAt = new Date() } = {}) {
  // Keep the transport failure available to local diagnostics only. Public consumers receive a
  // stable message so URLs, credentials, query values, and backend implementation details cannot
  // be reflected into the status page.
  void error
  const receivedTimestamp = parseTimestamp(receivedAt)
  return Object.freeze({
    phase: 'error',
    schemaVersion: '',
    generatedAt: '',
    backendStatus: 'unavailable',
    researchMode: 'historical',
    ready: false,
    features: Object.freeze({}),
    affected: Object.freeze([]),
    historicalMode: true,
    cutoff: '',
    receivedAt: receivedTimestamp ? receivedTimestamp.toISOString() : '',
    objectives: Object.freeze({ freshness: Object.freeze([]), workflows: Object.freeze([]) }),
    incidentHistory: Object.freeze({}),
    error: '数据状态服务暂不可用，请稍后重试。',
  })
}

export function featureFreshness(report, featureId) {
  return report?.features?.[featureId] || normalizeFeatureFreshness(null, featureId)
}

export function freshnessNoticeReportKey(report) {
  const phase = ['idle', 'ready', 'error'].includes(report?.phase) ? report.phase : 'error'
  const generatedAt = parseContractTimestamp(report?.generatedAt)?.toISOString() || ''
  const cutoff = parseContractTimestamp(report?.cutoff)?.toISOString() || ''
  const affectedByFeature = new Map()

  if (Array.isArray(report?.affected)) {
    for (const feature of report.affected) {
      const featureId = String(feature?.featureId || '')
      if (!FRESHNESS_MONITORED_FEATURES.includes(featureId)) continue
      const state = FRESHNESS_NOTICE_STATES.has(feature?.state) ? feature.state : 'unknown'
      affectedByFeature.set(featureId, state)
    }
  }

  const affected = FRESHNESS_MONITORED_FEATURES
    .filter((featureId) => affectedByFeature.has(featureId))
    .map((featureId) => `${featureId}:${affectedByFeature.get(featureId)}`)

  return ['v2', phase, generatedAt, cutoff, ...affected].join('|')
}

export function freshnessStateLabel(state) {
  return {
    live: '更新时限内',
    delayed: '更新延迟',
    stale: '历史快照',
    missing: '截止时间缺失',
    offline: '数据源离线',
    unknown: '状态未确认',
    not_applicable: '无需时效判定',
  }[state] || '状态未确认'
}

export function formatFreshnessCutoff(value, locale = 'zh-CN') {
  const parsed = parseTimestamp(value)
  if (!parsed) return ''
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
}

export async function fetchFeatureFreshness({
  endpoint = `${resolveApiOrigin()}${FEATURE_FRESHNESS_ENDPOINT}`,
  fetchImpl = globalThis.fetch?.bind(globalThis),
} = {}) {
  if (typeof fetchImpl !== 'function') throw new Error('数据状态请求不可用')
  const response = await fetchImpl(endpoint, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
  const payload = await response.json()
  return normalizeFeatureFreshnessReport(payload)
}

const sharedFreshnessReport = shallowRef(Object.freeze({
  phase: 'idle',
  schemaVersion: '',
  generatedAt: '',
  backendStatus: 'unknown',
  researchMode: 'historical',
  ready: false,
  features: Object.freeze({}),
  affected: Object.freeze([]),
  historicalMode: true,
  cutoff: '',
  receivedAt: '',
  objectives: Object.freeze({ freshness: Object.freeze([]), workflows: Object.freeze([]) }),
  incidentHistory: Object.freeze({}),
  error: '',
}))
let activeRefresh = null
let refreshGeneration = 0

export function refreshFeatureFreshness(options = {}) {
  if (activeRefresh && options.force !== true) return activeRefresh
  refreshGeneration += 1
  const requestGeneration = refreshGeneration
  activeRefresh = fetchFeatureFreshness(options)
    .then((report) => {
      if (requestGeneration === refreshGeneration) sharedFreshnessReport.value = report
      return report
    })
    .catch((error) => {
      const report = unavailableFeatureFreshnessReport(error?.message)
      if (requestGeneration === refreshGeneration) sharedFreshnessReport.value = report
      return report
    })
    .finally(() => {
      if (requestGeneration === refreshGeneration) activeRefresh = null
    })
  return activeRefresh
}

export function useFeatureFreshness() {
  return Object.freeze({
    report: readonly(sharedFreshnessReport),
    refresh: refreshFeatureFreshness,
  })
}

export {
  FEATURE_FRESHNESS_ENDPOINT,
  PUBLIC_STATUS_SCHEMA_VERSION,
}
