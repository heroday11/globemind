import { API_PREFIX, resolveApiOrigin } from '../config/api.js'

export const DATA_CATALOG_ENDPOINT = `${API_PREFIX}/data-governance/catalog`

const EMPTY_SUMMARY = Object.freeze({
  recordCount: 0,
  datasetCount: 0,
  sourceCount: 0,
  modelCount: 0,
  eligibleCount: 0,
  blockedCount: 0,
  formalReleaseStatus: 'blocked',
})

const KIND_LABELS = Object.freeze({ dataset: '数据集', source: '数据源', model: '模型' })
const OPERATIONAL_LABELS = Object.freeze({
  available: '技术可用',
  degraded: '技术降级',
  offline: '技术离线',
  unknown: '技术状态未知',
})
const FRESHNESS_LABELS = Object.freeze({
  live: '更新时限内',
  delayed: '更新延迟',
  stale: '历史快照',
  offline: '离线或未验证',
})

const BLOCKER_LABELS = Object.freeze({
  CAPABILITY_DEGRADED: '底层能力当前降级',
  CAPABILITY_OFFLINE: '底层能力当前离线',
  CHANGE_LOG_UNAVAILABLE: '变更记录未登记',
  COVERAGE_DIMENSIONS_MISSING: '覆盖维度不完整',
  COVERAGE_EVIDENCE_INCOMPLETE: '覆盖证据不完整',
  COVERAGE_UNVERIFIED: '覆盖状态未验证',
  CUTOFF_UNKNOWN: '数据截止时间未知',
  FRESHNESS_DELAYED: '数据更新延迟',
  FRESHNESS_EVIDENCE_INCOMPLETE: '时效证据不完整',
  FRESHNESS_EVIDENCE_MISSING: '缺少时效观测证据',
  FRESHNESS_OFFLINE: '数据离线或未验证',
  FRESHNESS_SLA_EXCEEDED: '数据超过更新时限',
  FRESHNESS_STALE: '数据已过期',
  FRESHNESS_STATE_UNVERIFIED: '时效状态无法验证',
  FRESHNESS_UNVERIFIED: '时效状态未验证',
  LAST_SUCCESS_UNKNOWN: '最后成功时间未知',
  LICENSE_EVIDENCE_INCOMPLETE: '许可证据不完整',
  LICENSE_UNKNOWN: '许可与使用边界未知',
  MODEL_REVIEW_DATE_UNKNOWN: '模型复核日期未知',
  MODEL_RUNTIME_STATUS_UNKNOWN: '模型运行状态未知',
  OPERATIONAL_DEGRADED: '技术可用性降级',
  OPERATIONAL_EVIDENCE_INCOMPLETE: '技术可用性证据不完整',
  OPERATIONAL_EVIDENCE_MISSING: '缺少技术可用性观测证据',
  OPERATIONAL_OFFLINE: '技术能力离线',
  OPERATIONAL_STATUS_UNVERIFIED: '技术可用性未验证',
  OPERATIONAL_UNKNOWN: '技术可用性未知',
  OWNER_EVIDENCE_INCOMPLETE: '负责人证据不完整',
  OWNER_NOT_NAMED: '未指定具名负责人',
  PROVENANCE_EVIDENCE_INCOMPLETE: '溯源证据不完整',
  PROVENANCE_INCOMPLETE: '快照、哈希或修订链不完整',
  QUALITY_EVIDENCE_INCOMPLETE: '质量评测证据不完整',
  QUALITY_NOT_PASSED: '质量门禁未通过',
  QUALITY_UNVERIFIED: '质量状态未验证',
  SCHEMA_EVIDENCE_INCOMPLETE: 'Schema 证据不完整',
  SCHEMA_GOVERNANCE_INCOMPLETE: '数据字典、映射或变更记录不完整',
  SOURCE_LEVEL_FRESHNESS_UNKNOWN: '来源级时效与最后成功时间未知',
  SOURCE_LEVEL_OPERATIONAL_STATUS_UNKNOWN: '来源级技术可用性未知',
  VERSION_EVIDENCE_INCOMPLETE: '版本证据不完整',
  VERSION_UNVERIFIED: '版本未验证',
})

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function stringValue(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function uniqueStrings(values) {
  return [...new Set((Array.isArray(values) ? values : []).map(stringValue).filter(Boolean))]
}

function hasVerifiedEvidence(value) {
  return Array.isArray(value?.evidence)
    && value.evidence.some((item) => isObject(item) && item.status === 'verified')
}

function clientBlockers(record) {
  const reasons = []
  const add = (reason) => {
    if (!reasons.includes(reason)) reasons.push(reason)
  }
  if (record.owner?.assignment_status !== 'named') add('OWNER_NOT_NAMED')
  else if (!stringValue(record.owner?.owner_id)
    || !stringValue(record.owner?.display_name)
    || !hasVerifiedEvidence(record.owner)) add('OWNER_EVIDENCE_INCOMPLETE')
  if (record.version?.status !== 'verified' || !stringValue(record.version?.value)) {
    add('VERSION_UNVERIFIED')
  } else if (!stringValue(record.version?.scheme)
    || !stringValue(record.version?.change_log_ref)
    || !hasVerifiedEvidence(record.version)) {
    add('VERSION_EVIDENCE_INCOMPLETE')
  }
  if (record.operational?.evidence_status !== 'verified') add('OPERATIONAL_STATUS_UNVERIFIED')
  const operationalState = stringValue(record.operational?.state)
  if (operationalState !== 'available') {
    add(`OPERATIONAL_${(operationalState || 'unknown').toUpperCase()}`)
  }
  if (record.operational?.evidence_status === 'verified'
    && (!stringValue(record.operational?.observed_at)
      || !stringValue(record.operational?.source))) add('OPERATIONAL_EVIDENCE_INCOMPLETE')
  uniqueStrings(record.operational?.reason_codes).forEach(add)
  if (record.freshness?.evidence_status !== 'verified') add('FRESHNESS_UNVERIFIED')
  const freshnessState = stringValue(record.freshness?.state)
  if (freshnessState !== 'live') add(`FRESHNESS_${(freshnessState || 'offline').toUpperCase()}`)
  if (!stringValue(record.freshness?.cutoff_at)) add('CUTOFF_UNKNOWN')
  if (!stringValue(record.freshness?.last_success_at)) add('LAST_SUCCESS_UNKNOWN')
  if (record.freshness?.evidence_status === 'verified'
    && (!stringValue(record.freshness?.source)
      || !Number.isFinite(Number(record.freshness?.lag_hours))
      || !Number.isFinite(Number(record.freshness?.sla_hours))
      || (Array.isArray(record.freshness?.reason_codes) && record.freshness.reason_codes.length))) {
    add('FRESHNESS_EVIDENCE_INCOMPLETE')
  }
  uniqueStrings(record.freshness?.reason_codes).forEach(add)
  if (record.coverage?.status !== 'verified') add('COVERAGE_UNVERIFIED')
  else if (!stringValue(record.coverage?.scope)
    || !isObject(record.coverage?.metrics)
    || Object.keys(record.coverage.metrics).length === 0
    || !hasVerifiedEvidence(record.coverage)) add('COVERAGE_EVIDENCE_INCOMPLETE')
  if (Array.isArray(record.coverage?.missing_dimensions) && record.coverage.missing_dimensions.length) {
    add('COVERAGE_DIMENSIONS_MISSING')
  }
  if (!['verified', 'restricted'].includes(record.license?.status)) add('LICENSE_UNKNOWN')
  else if (!stringValue(record.license?.identifier)
    || !stringValue(record.license?.usage_scope)
    || !stringValue(record.license?.terms_ref)
    || !stringValue(record.license?.retention_policy)
    || !hasVerifiedEvidence(record.license)) add('LICENSE_EVIDENCE_INCOMPLETE')
  if (record.quality?.status !== 'passed') add('QUALITY_UNVERIFIED')
  else if (!stringValue(record.quality?.evaluated_at)
    || !stringValue(record.quality?.evaluation_version)
    || !isObject(record.quality?.metrics)
    || Object.keys(record.quality.metrics).length === 0
    || !hasVerifiedEvidence(record.quality)) add('QUALITY_EVIDENCE_INCOMPLETE')
  if (record.provenance?.status !== 'verified') add('PROVENANCE_INCOMPLETE')
  else if (record.provenance?.capture_timestamp_status !== 'verified'
    || record.provenance?.web_snapshot_status !== 'verified'
    || record.provenance?.content_hash_status !== 'verified'
    || record.provenance?.revision_tracking_status !== 'verified'
    || !stringValue(record.provenance?.parser_version)
    || !hasVerifiedEvidence(record.provenance)) add('PROVENANCE_EVIDENCE_INCOMPLETE')
  if (record.schema?.status !== 'verified') add('SCHEMA_GOVERNANCE_INCOMPLETE')
  else if (!stringValue(record.schema?.record_identifier)
    || !stringValue(record.schema?.schema_ref)
    || !stringValue(record.schema?.data_dictionary_ref)
    || !Array.isArray(record.schema?.mapping_refs)
    || record.schema.mapping_refs.length === 0
    || !stringValue(record.schema?.change_log_ref)
    || !hasVerifiedEvidence(record.schema)) add('SCHEMA_EVIDENCE_INCOMPLETE')
  return reasons
}

function normalizeRecord(raw) {
  if (!isObject(raw)) return null
  const recordId = stringValue(raw.record_id)
  const kind = stringValue(raw.kind)
  if (!recordId || !Object.hasOwn(KIND_LABELS, kind)) return null
  const backendReasons = uniqueStrings(raw.status?.reason_codes)
  const reasons = uniqueStrings([...backendReasons, ...clientBlockers(raw)])
  const eligible = raw.status?.release_eligible === true
    && raw.status?.research_ready === true
    && raw.status?.state === 'eligible'
    && reasons.length === 0
  return Object.freeze({
    recordId,
    kind,
    kindLabel: KIND_LABELS[kind],
    title: stringValue(raw.title) || recordId,
    description: stringValue(raw.description),
    owner: Object.freeze({
      name: stringValue(raw.owner?.display_name) || '未知（未登记）',
      assignmentStatus: stringValue(raw.owner?.assignment_status) || 'unknown',
      assignmentLabel: raw.owner?.assignment_status === 'named'
        ? '具名负责人'
        : raw.owner?.assignment_status === 'role_only'
          ? '责任角色，未具名'
          : '负责人未知',
    }),
    version: stringValue(raw.version?.value) || '未知',
    operational: Object.freeze({
      state: Object.hasOwn(OPERATIONAL_LABELS, raw.operational?.state)
        ? raw.operational.state
        : 'unknown',
      label: OPERATIONAL_LABELS[raw.operational?.state] || OPERATIONAL_LABELS.unknown,
    }),
    freshness: Object.freeze({
      state: Object.hasOwn(FRESHNESS_LABELS, raw.freshness?.state)
        ? raw.freshness.state
        : 'offline',
      label: FRESHNESS_LABELS[raw.freshness?.state] || FRESHNESS_LABELS.offline,
      cutoffAt: stringValue(raw.freshness?.cutoff_at),
      lastSuccessAt: stringValue(raw.freshness?.last_success_at),
    }),
    coverage: Object.freeze({
      status: stringValue(raw.coverage?.status) || 'unknown',
      scope: stringValue(raw.coverage?.scope),
      metrics: isObject(raw.coverage?.metrics) ? Object.freeze({ ...raw.coverage.metrics }) : Object.freeze({}),
      missingDimensions: Object.freeze(uniqueStrings(raw.coverage?.missing_dimensions)),
    }),
    license: Object.freeze({
      status: stringValue(raw.license?.status) || 'unknown',
      identifier: stringValue(raw.license?.identifier),
      usageScope: stringValue(raw.license?.usage_scope),
    }),
    quality: Object.freeze({
      status: stringValue(raw.quality?.status) || 'unknown',
      evaluatedAt: stringValue(raw.quality?.evaluated_at),
    }),
    provenanceStatus: stringValue(raw.provenance?.status) || 'unknown',
    schemaStatus: stringValue(raw.schema?.status) || 'unknown',
    status: Object.freeze({
      state: eligible ? 'eligible' : 'blocked',
      releaseEligible: eligible,
      researchReady: eligible,
      reasonCodes: Object.freeze(reasons),
    }),
  })
}

export function unavailableDataCatalog(error = '') {
  return Object.freeze({
    phase: 'error',
    available: false,
    catalogStatus: 'unavailable',
    generatedAt: '',
    summary: EMPTY_SUMMARY,
    records: Object.freeze([]),
    error: stringValue(error) || '数据登记目录暂不可用；所有记录按未知和不可用于正式研究处理。',
  })
}

export function normalizeDataCatalog(payload) {
  if (!isObject(payload)
    || payload.schema_version !== 'data-governance-catalog-v1'
    || payload.contract_version !== '1.0.0'
    || payload.available !== true
    || !Array.isArray(payload.records)) {
    return unavailableDataCatalog('数据登记目录响应缺失或契约版本不兼容。')
  }
  const records = payload.records.map(normalizeRecord).filter(Boolean)
  if (records.length !== payload.records.length) {
    return unavailableDataCatalog('数据登记目录包含无法验证的记录。')
  }
  const eligibleCount = records.filter((record) => record.status.releaseEligible).length
  const counts = Object.fromEntries(
    Object.keys(KIND_LABELS).map((kind) => [kind, records.filter((item) => item.kind === kind).length]),
  )
  const formalReleaseStatus = records.length > 0 && eligibleCount === records.length
    ? 'ready'
    : 'blocked'
  return Object.freeze({
    phase: 'ready',
    available: true,
    catalogStatus: formalReleaseStatus === 'ready' ? 'ready' : 'incomplete',
    generatedAt: stringValue(payload.generated_at),
    summary: Object.freeze({
      recordCount: records.length,
      datasetCount: counts.dataset,
      sourceCount: counts.source,
      modelCount: counts.model,
      eligibleCount,
      blockedCount: records.length - eligibleCount,
      formalReleaseStatus,
    }),
    records: Object.freeze(records),
    error: '',
  })
}

export async function fetchDataCatalog({
  endpoint = `${resolveApiOrigin()}${DATA_CATALOG_ENDPOINT}`,
  fetchImpl = globalThis.fetch?.bind(globalThis),
  signal,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new Error('数据登记目录请求不可用')
  const response = await fetchImpl(endpoint, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!response?.ok) throw new Error('数据登记目录服务暂不可用')
  const catalog = normalizeDataCatalog(await response.json())
  if (!catalog.available) throw new Error(catalog.error)
  return catalog
}

export function catalogBlockerLabel(code) {
  return BLOCKER_LABELS[code] || code
}

export function catalogStatusLabel(status) {
  return {
    eligible: '正式登记已满足',
    blocked: '正式登记受阻',
    verified: '已验证',
    restricted: '已登记限制',
    partial: '部分登记',
    passed: '已通过',
    degraded: '降级',
    failed: '未通过',
    unknown: '未知',
  }[status] || '未知'
}
