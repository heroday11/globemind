const STATUS_SCHEMA_VERSION = 'globemind.public-status.v1'
const MODULE_EVIDENCE_SCHEMA_VERSION = 'globemind.home-module-evidence.v1'
const METHOD_ID = 'business-freshness-health-projection'
const METHOD_VERSION = 'v1'
const MAX_REPORT_AGE_MS = 15 * 60 * 1000
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000

const MODULES = Object.freeze([
  Object.freeze({
    moduleId: 'home-opinion-analysis',
    featureId: 'opinion-analysis',
    path: 'sentiment-analysis',
    title: '智能舆情分析',
    desc: '对历史采集新闻进行聚合分析；历史资料不代表实时或完整覆盖，截止时间、覆盖与可计算状态以页内门禁为准。',
    featured: true,
    bgPosition: '0%',
    scopeId: 'public-opinion-analysis-scores',
    scopeLabel: '公开涉华舆情聚合结果',
    cutoffMetric: 'latest_score_date',
  }),
  Object.freeze({
    moduleId: 'home-ground-news',
    featureId: 'ground-news',
    path: 'data-service/ground-news-desk',
    title: '全球新闻观察台',
    desc: '按事件层级浏览新闻、信源与报道差异；结果作为研究线索，需结合原文复核。',
    featured: false,
    bgPosition: '33.333%',
    scopeId: 'public-ground-news-story-sources',
    scopeLabel: '公开事件卡、报道与来源构成',
    cutoffMetric: 'latest_story_source_at',
  }),
  Object.freeze({
    moduleId: 'home-data-search',
    featureId: 'search',
    path: 'data-service/data-search',
    title: '数据服务',
    desc: '支持关键词、短语、布尔和时间筛选，并显示实际查询解释与结果截止时间。',
    featured: false,
    bgPosition: '66.667%',
    scopeId: 'public-news-event-search',
    scopeLabel: '公开新闻与事件检索结果',
    cutoffMetric: 'latest_news_at',
  }),
  Object.freeze({
    moduleId: 'home-agent-data-connections',
    featureId: null,
    path: 'academic-data',
    title: 'Agent 数据连接',
    desc: '浏览 Agent Skill 与数据连接入口；来源许可和实际可用性以数据目录登记为准。',
    featured: false,
    bgPosition: '100%',
    scopeId: 'agent-skill-data-connection-catalog',
    scopeLabel: 'Agent Skill 与数据连接目录入口',
    cutoffMetric: null,
  }),
])

const STATE_LABELS = Object.freeze({
  live: '更新时限内',
  delayed: '更新延迟',
  stale: '历史快照',
  offline: '当前不可用',
})

function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const keys = Object.keys(value)
  return keys.length === expected.length && expected.every((key) => Object.hasOwn(value, key))
}

function timestamp(value) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value.trim())) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function reportGeneration(report, now) {
  if (
    report?.phase !== 'ready'
    || report.schemaVersion !== STATUS_SCHEMA_VERSION
    || !(now instanceof Date)
    || Number.isNaN(now.getTime())
  ) return null
  const generatedAt = timestamp(report.generatedAt)
  const receivedAt = timestamp(report.receivedAt)
  if (
    !generatedAt
    || !receivedAt
    || generatedAt.getTime() > now.getTime() + MAX_FUTURE_SKEW_MS
    || now.getTime() - generatedAt.getTime() > MAX_REPORT_AGE_MS
    || receivedAt.getTime() < generatedAt.getTime() - MAX_FUTURE_SKEW_MS
    || receivedAt.getTime() > now.getTime() + MAX_FUTURE_SKEW_MS
  ) return null
  return generatedAt
}

function formatUtc(value) {
  return value.toISOString().replace('T', ' ').replace(/:\d{2}\.\d{3}Z$/, ' UTC')
}

function unknownDisclosure(definition) {
  return Object.freeze({
    state: 'unknown',
    scope: '范围无法确认',
    generatedAt: '无法确认',
    cutoff: '无法确认',
    freshness: '状态无法确认',
    method: '方法无法确认',
    evidence: '证据无法确认',
    statusSource: definition?.featureId
      ? `/api/status · ${definition.featureId}`
      : '状态来源无法确认',
  })
}

function unconfiguredDisclosure(definition) {
  return Object.freeze({
    state: 'not_configured',
    scope: definition.scopeLabel,
    generatedAt: '无法确认',
    cutoff: '无法确认',
    freshness: '状态未配置',
    method: '方法未配置',
    evidence: '证据未配置',
    statusSource: '未绑定公开状态来源',
  })
}

function validEvidence(value, definition, hasCutoff, evidenceUsable) {
  return exactKeys(value, [
    'schemaVersion',
    'moduleId',
    'scope',
    'cutoffMetric',
    'cutoffStatus',
    'method',
    'evidenceStatus',
  ])
    && value.schemaVersion === MODULE_EVIDENCE_SCHEMA_VERSION
    && value.moduleId === definition.moduleId
    && exactKeys(value.scope, ['id', 'label'])
    && value.scope.id === definition.scopeId
    && value.scope.label === definition.scopeLabel
    && value.cutoffMetric === definition.cutoffMetric
    && value.cutoffStatus === (hasCutoff ? 'available' : 'unknown')
    && exactKeys(value.method, ['id', 'version', 'status'])
    && value.method.id === METHOD_ID
    && value.method.version === METHOD_VERSION
    && value.method.status === 'configured'
    && value.evidenceStatus === (
      evidenceUsable ? 'contract_validated' : 'unavailable'
    )
}

function boundDisclosure(report, definition, generatedAt) {
  const feature = report?.features?.[definition.featureId]
  if (!feature || feature.featureId !== definition.featureId) return unknownDisclosure(definition)
  const cutoff = timestamp(feature.cutoff)
  const hasCutoff = Boolean(
    cutoff && cutoff.getTime() <= generatedAt.getTime() + MAX_FUTURE_SKEW_MS,
  )
  const evidenceUsable = hasCutoff && !['offline', 'unknown'].includes(feature.state)
  if (!validEvidence(feature.moduleEvidence, definition, hasCutoff, evidenceUsable)) {
    return unknownDisclosure(definition)
  }
  const expectedResearchUse = feature.state === 'live'
    ? 'current'
    : ['delayed', 'stale'].includes(feature.state)
      ? 'historical'
      : feature.state === 'offline'
        ? 'unavailable'
        : ''
  if (!expectedResearchUse || feature.researchUse !== expectedResearchUse) {
    return unknownDisclosure(definition)
  }
  const base = {
    state: evidenceUsable ? 'projection_bound' : 'unknown',
    scope: definition.scopeLabel,
    generatedAt: formatUtc(generatedAt),
    cutoff: evidenceUsable ? formatUtc(cutoff) : '无法确认',
    freshness: evidenceUsable ? STATE_LABELS[feature.state] : '状态无法确认',
    method: '业务新鲜度投影 v1',
    evidence: evidenceUsable
      ? '状态契约已校验；不验证数据真实性或完整覆盖'
      : '后端状态证据不可用',
    statusSource: `/api/status · ${definition.featureId}`,
  }
  return Object.freeze(base)
}

export function buildHomeCapabilityCards(report, { now = new Date() } = {}) {
  const generatedAt = reportGeneration(report, now)
  return Object.freeze(MODULES.map((definition) => Object.freeze({
    moduleId: definition.moduleId,
    path: definition.path,
    title: definition.title,
    desc: definition.desc,
    featured: definition.featured,
    bgPosition: definition.bgPosition,
    disclosure: definition.featureId === null
      ? unconfiguredDisclosure(definition)
      : generatedAt
        ? boundDisclosure(report, definition, generatedAt)
        : unknownDisclosure(definition),
  })))
}

export const homeCapabilityModuleIds = Object.freeze(
  MODULES.map((definition) => definition.moduleId),
)
