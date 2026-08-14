import {
  enforceOpinionSemanticBoundary,
  opinionSemanticSnapshot,
} from './semantics.js'

export const FALLBACK_OVERVIEW_SUMMARY = Object.freeze({
  current_index: null,
  change_24h: null,
  trend_label: '等待数据',
  article_count: 0,
  source_count: 0,
  family_count: 0,
  positive_pct: 0,
  negative_pct: 0,
  neutral_pct: 0,
})

export const CORRECTION_LABELS = Object.freeze({
  irrelevant: '无关',
  too_positive: '立场偏支持',
  too_negative: '立场偏批评',
  correct: '准确',
})

const OVERVIEW_ENTITY_NAMES = Object.freeze({
  china: '中国',
  'north korea': '朝鲜',
  korea: '韩国',
  japan: '日本',
  taiwan: '台湾',
  russia: '俄罗斯',
  us: '美国',
  usa: '美国',
  eu: '欧盟',
  india: '印度',
})

const TARGET_INDEX_LABELS = Object.freeze({
  CN: Object.freeze({ label: '总体立场', description: '加权目标立场指数' }),
  NEG: Object.freeze({ label: '批评立场', description: '批评立场分布（非影响）' }),
  POS: Object.freeze({ label: '支持立场', description: '支持立场分布（非影响）' }),
})

const FAMILY_NAMES = Object.freeze({
  diplomacy: '外交',
  economic_trade: '经贸',
  military_security: '军事安全',
  domestic_politics: '国内政治',
  general_non_political: '一般议题',
  technology_industry: '科技产业',
  civil_unrest: '社会抗议',
  law_policy: '法律政策',
  public_development: '公共发展',
  security_crime: '安全犯罪',
  disaster_environment: '灾害环境',
  human_rights_migration: '人权迁移',
})

const DIMENSION_LABELS = Object.freeze({
  regions: '区域',
  languages: '语种',
  sources: '信源',
  families: '议题',
})

export function formatDateYmd(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function resolveAutoEndDate(currentValue, previousDataEnd, nextDataEnd) {
  const current = String(currentValue || '')
  const previous = String(previousDataEnd || '')
  const next = String(nextDataEnd || '')
  return !current || current === previous ? next : current
}

export function formatIndexValue(value) {
  if (value === null || value === undefined || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${number > 0 ? '+' : ''}${number.toFixed(Math.abs(number) >= 100 ? 0 : 1)}`
}

export function formatCompactCount(value) {
  const number = Number(value || 0)
  if (!Number.isFinite(number)) return '0'
  if (Math.abs(number) >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`
  if (Math.abs(number) >= 1_000) return `${(number / 1_000).toFixed(1)}K`
  return String(Math.round(number))
}

export function formatOverviewTime(iso) {
  if (!iso) return '--:--'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '--:--'
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

export function formatOverviewEntity(name) {
  const key = String(name || '').trim().toLowerCase()
  return OVERVIEW_ENTITY_NAMES[key] || String(name || '').trim()
}

export function formatOverviewEventTitle(title) {
  const value = String(title || '').trim()
  const pair = value.match(/^([a-z][a-z\s.-]+?)↔([a-z][a-z\s.-]+?):\s*(.+)$/i)
  if (!pair) return value
  return `${formatOverviewEntity(pair[1])} ↔ ${formatOverviewEntity(pair[2])}：${pair[3]}`
}

export function formatFamilyName(family) {
  return FAMILY_NAMES[family] || family || '未知议题'
}

export function formatSearchSnippet(item) {
  const raw = item?.abstract || item?.summary || item?.body || ''
  return String(raw).replace(/\s+/g, ' ').trim().slice(0, 140)
}

export function briefClass(severity) {
  if (severity === 'unavailable') return 'brief-item--unavailable'
  if (severity === 'critical') return 'brief-item--critical'
  if (severity === 'high') return 'brief-item--high'
  return 'brief-item--info'
}

export function briefSeverityLabel(severity) {
  if (severity === 'unavailable') return '不可计算'
  if (severity === 'critical') return '高强度'
  if (severity === 'high') return '显著'
  return '常规'
}

export function overviewSummary(data) {
  return data?.summary || FALLBACK_OVERVIEW_SUMMARY
}

export function overviewTopEventTitle(data, { loading = false, error = '' } = {}) {
  const title = data?.top_event?.title
  if (title) return formatOverviewEventTitle(title)
  if (loading) return '加载中'
  if (error) return '数据异常'
  return '暂无主题事件'
}

export function overviewTrendClass(summary) {
  const value = Number(summary?.current_index || 0)
  if (value < -12) return 'trend--neg'
  if (value > 12) return 'trend--pos'
  return 'trend--neutral'
}

export function overviewScoreClass(summary) {
  const value = Number(summary?.current_index || 0)
  if (value < -12) return 'risk-score-panel--neg'
  if (value > 12) return 'risk-score-panel--pos'
  return 'risk-score-panel--neutral'
}

export function overviewTargetIndices(data) {
  return data?.target_indices || [
    { label: 'CN', value: null, trend_values: [], state: 'unavailable' },
    { label: 'NEG', value: null, trend_values: [], state: 'unavailable' },
    { label: 'POS', value: null, trend_values: [], state: 'unavailable' },
  ]
}

export function overviewReadableIndices(data) {
  return overviewTargetIndices(data).map((item) => {
    const key = String(item.label || '').toUpperCase()
    const copy = TARGET_INDEX_LABELS[key] || {
      label: item.label || '未知指标',
      description: '舆情分项',
    }
    return { ...item, displayLabel: copy.label, description: copy.description }
  })
}

export function overviewMetrics(data) {
  return data?.metrics || [
    { label: '较前一日', value: '不可计算', display_tone: 'neutral' },
    { label: '报道量', value: '0', display_tone: 'neutral' },
    { label: '信源数', value: '0', display_tone: 'neutral' },
  ]
}

export function maxOverviewTagCount(tags) {
  return Math.max(1, ...(tags || []).map((item) => Number(item?.article_count || 0)))
}

export function tagBarWidth(count, maximum) {
  return `${Math.max(12, Math.min(100, (Number(count || 0) / Math.max(1, maximum)) * 100))}%`
}

export function tagStyle(index) {
  const opacity = Math.max(0.05, 0.16 - index * 0.025)
  const accent = Math.max(0.35, 0.86 - index * 0.08)
  return {
    '--tag-accent': `rgba(59, 130, 246, ${accent})`,
    '--tag-glow': `rgba(59, 130, 246, ${opacity})`,
  }
}

export function dimensionGroups(data) {
  const dimensions = data?.dimensions || {}
  return ['regions', 'languages', 'sources', 'families'].map((key) => ({
    key,
    label: DIMENSION_LABELS[key],
    items: dimensions[key] || [],
  }))
}

export function formatDimensionLabel(groupKey, value) {
  if (groupKey === 'families') return formatFamilyName(value)
  if (!value || value === 'unknown') return '未知'
  return String(value)
}

export function qualitySnapshot(data) {
  const rows = data?.coverage_by_date || []
  const today = rows[0] || {}
  const news = Number(today.news_count || 0)
  const scored = Number(today.scored_relevant || today.scored_count || 0)
  const percentage = news > 0 ? Math.round((scored / news) * 100) : 0
  const method = String(data?.method_version || '--')
  return {
    latestScoreDate: data?.freshness?.latest_score_date || '--',
    latestCoverage: news > 0
      ? `${formatCompactCount(scored)}/${formatCompactCount(news)} · ${percentage}%`
      : '--',
    coverageDate: today.date || '--',
    pendingFeedback: String(data?.pending_feedback_30d || 0),
    methodVersion: method,
  }
}

export const OPINION_TRUST_SCHEMA_VERSION = 'opinion-trust-v1'
export const OPINION_FRESHNESS_MAX_AGE_DAYS = 2

const TRUST_REASON_LABELS = Object.freeze({
  CONFLICTING_COMPUTABILITY_METADATA: '可计算状态元数据互相冲突',
  CONFLICTING_COVERAGE_METADATA: '覆盖门槛元数据互相冲突',
  CONFLICTING_CUTOFF_METADATA: '数据截止时间元数据互相冲突',
  CONFLICTING_DISPLAY_MODE_METADATA: '展示模式元数据互相冲突',
  CONFLICTING_FRESHNESS_METADATA: '数据时效元数据互相冲突',
  CONFLICTING_METHOD_METADATA: '评分方法元数据互相冲突',
  CONFLICTING_MODEL_METADATA: '模型元数据互相冲突',
  CONFLICTING_SNAPSHOT_METADATA: '快照元数据互相冲突',
  CONFLICTING_SOURCE_METADATA: '数据源元数据互相冲突',
  CONFLICTING_STATUS_METADATA: '可信状态元数据互相冲突',
  CONFLICTING_TRUST_METADATA: '响应包含冲突的可信状态',
  DECLARED_UNCOMPUTABLE: '后端明确标记为不可计算',
  INVALID_COVERAGE_METADATA: '覆盖元数据损坏',
  INVALID_SCHEMA_METADATA: '可信契约版本异常',
  INVALID_TRUST_METADATA: '可信状态元数据损坏',
  INVALID_DERIVED_CLAIM_CONTRACT: '衍生结论身份契约损坏',
  MISSING_CUTOFF: '缺少数据截止时间',
  MISSING_METHOD_METADATA: '缺少评分方法元数据',
  MISSING_MODEL_METADATA: '缺少模型元数据',
  MISSING_SNAPSHOT_METADATA: '缺少快照元数据',
  MISSING_SOURCE_COVERAGE: '缺少信源覆盖统计',
  MISSING_SOURCE_METADATA: '缺少数据源元数据',
  MISSING_TRUST_METADATA: '缺少可信状态元数据',
  MISSING_DERIVED_CLAIM_CONTRACT: '缺少衍生结论身份契约',
  OVERVIEW_TRUST_UNAVAILABLE: '总览可信门禁未通过',
  REFRESH_FAILED: '最新快照刷新失败',
  STALE_DATA: '数据已超过时效门槛',
  INVALID_FUTURE_CUTOFF: '数据截止时间异常',
  LOW_ARTICLE_COVERAGE: '样本量不足',
  LOW_SOURCE_COVERAGE: '信源覆盖不足',
})

const FRONTEND_NULL_WHEN_UNTRUSTED = new Set([
  'avg_impact',
  'avg_stance',
  'average_weighted_stance_index',
  'change_24h',
  'china_importance',
  'china_index',
  'confidence',
  'current_index',
  'daily_impact',
  'directness_score',
  'growth_pct',
  'impact_abs',
  'impact_index',
  'l1_total_impact',
  'max_heat',
  'max_impact',
  'maximum_weighted_stance_index',
  'min_impact',
  'minimum_weighted_stance_index',
  'negative_pct',
  'neutral_pct',
  'positive_pct',
  'quality_score',
  'relevance_score',
  'sentiment',
  'stance_score',
  'weighted_stance_contribution',
  'weighted_stance_contribution_abs',
  'weighted_stance_index',
  'total_raw_daily',
])

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(',')}}`
  }
  return JSON.stringify(value)
}

function cloneJson(value) {
  try {
    return JSON.parse(JSON.stringify(value))
  } catch {
    return {}
  }
}

function finiteNonNegativeInteger(value) {
  if (typeof value === 'boolean' || value === null || value === '') return null
  const number = Number(value)
  return Number.isInteger(number) && number >= 0 ? number : null
}

function utcDateStamp(value) {
  if (!value) return null
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return null
  const stamp = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const parsed = new Date(stamp)
  return Number.isNaN(stamp) || parsed.toISOString().slice(0, 10) !== match[0] ? null : stamp
}

function currentUtcDateStamp(now) {
  const instant = new Date(now)
  if (Number.isNaN(instant.getTime())) return null
  return Date.UTC(instant.getUTCFullYear(), instant.getUTCMonth(), instant.getUTCDate())
}

function appendReason(reasons, code) {
  if (code && !reasons.includes(code)) reasons.push(code)
}

function suppressFrontendComposites(value) {
  if (Array.isArray(value)) {
    value.forEach(suppressFrontendComposites)
    return
  }
  if (!value || typeof value !== 'object') return
  for (const key of Object.keys(value)) {
    if (FRONTEND_NULL_WHEN_UNTRUSTED.has(key)) value[key] = null
    else if (key === 'trend_values') value[key] = []
    else if (key === 'severity') value[key] = 'unavailable'
    else suppressFrontendComposites(value[key])
  }
  if ('polarity' in value && ('impact_index' in value || 'daily_impact' in value)) {
    value.polarity = 'unavailable'
  }
}

function missingTrust(reasonCodes) {
  return {
    schema_version: OPINION_TRUST_SCHEMA_VERSION,
    status: 'unavailable',
    trust_status: 'unavailable',
    is_computable: false,
    computability: 'not_computable',
    display_mode: 'historical_context',
    reason_codes: reasonCodes,
    cutoff_date: null,
    freshness: {
      state: 'missing',
      age_days: null,
      maximum_age_days: OPINION_FRESHNESS_MAX_AGE_DAYS,
    },
    coverage: {
      state: 'insufficient',
      article_count: 0,
      source_count: null,
      minimum_articles: 10,
      minimum_sources: 3,
    },
    model_version: null,
    method_version: null,
    source_status: 'missing',
    snapshot_id: null,
    model: null,
    method: null,
    source: null,
    snapshot: null,
  }
}

const OPINION_CLAIM_SCHEMA_VERSION = 'opinion-derived-claim-contract-v1'
const OPINION_CLAIM_MAX_CLAIMS = 48
const OPINION_CORE_CLAIM_METRICS = new Set([
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
])
const OPINION_OPTIONAL_CLAIM_METRICS = new Set([
  'event_family_article_count',
  'event_family_average_stance',
  'article_stance_score',
  'article_model_confidence',
  'top_event_average_stance',
  'top_event_article_count',
  'top_event_china_article_count',
])
const OPINION_CLAIM_ID_RE = /^opinion-claim-[0-9a-f]{64}$/
const OPINION_CLAIM_PATH_RE = /^(summary\.[a-z0-9_]+|target_indices\.(CN|NEG|POS)\.value|families\.event_family=[a-z][a-z0-9_]{0,63}\.(article_count|avg_stance)|briefs\.news_id=[1-9][0-9]*\.(stance_score|confidence)|top_event\.(avg_stance|article_count|china_articles))$/
const OPINION_CLAIM_SLICE_KEYS = new Set([
  'population', 'window_days', 'event_family', 'news_id', 'chain_id', 'target',
])
const OPINION_CORE_CLAIM_PATHS = Object.freeze({
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
})

function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value).sort()
  return actual.length === expected.length
    && actual.every((key, index) => key === [...expected].sort()[index])
}

function claimPathsMatchSemanticBinding(claim) {
  const expectedCore = OPINION_CORE_CLAIM_PATHS[claim.metric]
  if (expectedCore) {
    const targetByMetric = {
      target_weighted_stance_index: 'CN',
      negative_stance_pressure_index: 'NEG',
      positive_stance_support_index: 'POS',
    }
    const target = targetByMetric[claim.metric]
    const sliceValid = target
      ? exactKeys(claim.identity?.slice, ['population', 'window_days', 'target'])
        && claim.identity.slice.target === target
      : exactKeys(claim.identity?.slice, ['population', 'window_days'])
    return sliceValid
      && canonicalJson(claim.output_paths) === canonicalJson(expectedCore)
  }
  const slice = claim.identity?.slice || {}
  if (claim.metric === 'event_family_article_count') {
    return exactKeys(slice, ['population', 'window_days', 'event_family'])
      && claim.output_paths[0] === `families.event_family=${slice.event_family}.article_count`
  }
  if (claim.metric === 'event_family_average_stance') {
    return exactKeys(slice, ['population', 'window_days', 'event_family'])
      && claim.output_paths[0] === `families.event_family=${slice.event_family}.avg_stance`
  }
  if (claim.metric === 'article_stance_score') {
    return exactKeys(slice, ['population', 'window_days', 'news_id'])
      && claim.output_paths[0] === `briefs.news_id=${slice.news_id}.stance_score`
  }
  if (claim.metric === 'article_model_confidence') {
    return exactKeys(slice, ['population', 'window_days', 'news_id'])
      && claim.output_paths[0] === `briefs.news_id=${slice.news_id}.confidence`
  }
  const topEventFields = {
    top_event_average_stance: 'avg_stance',
    top_event_article_count: 'article_count',
    top_event_china_article_count: 'china_articles',
  }
  return exactKeys(slice, ['population', 'window_days', 'chain_id'])
    && claim.output_paths[0] === `top_event.${topEventFields[claim.metric]}`
}

function sameOptionalNumber(left, right) {
  if (left === null || left === undefined || right === null || right === undefined) {
    return (left === null || left === undefined) && (right === null || right === undefined)
  }
  const leftNumber = typeof left === 'boolean' ? NaN : Number(left)
  const rightNumber = typeof right === 'boolean' ? NaN : Number(right)
  return Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
    && Math.abs(leftNumber - rightNumber) <= 1e-9
}

function targetIndicesMatchSemanticValues(payload) {
  const targets = payload?.target_indices
  if (!Array.isArray(targets) || targets.length !== 3) return false
  const labels = targets.map((item) => item?.label)
  if (canonicalJson(labels) !== canonicalJson(['CN', 'NEG', 'POS'])) return false
  const summary = payload?.summary
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) return false
  const expected = [
    summary.current_index,
    summary.negative_pct === null || summary.negative_pct === undefined
      ? null
      : -Number(summary.negative_pct),
    summary.positive_pct,
  ]
  if (targets.some((item, index) => !sameOptionalNumber(item.value, expected[index]))) return false
  if (
    !Array.isArray(targets[0].trend_values)
    || targets[0].trend_values.length > 14
    || targets[0].trend_values.some((value) => !Number.isFinite(value))
    || (
      targets[0].trend_values.length
      && expected[0] !== null
      && !sameOptionalNumber(targets[0].trend_values.at(-1), expected[0])
    )
    || !Array.isArray(targets[1].trend_values)
    || targets[1].trend_values.length !== 0
    || !Array.isArray(targets[2].trend_values)
    || targets[2].trend_values.length !== 0
  ) return false
  const expectedStates = [
    expected[0] === null
      ? 'unavailable'
      : expected[0] < -12 ? 'negative' : expected[0] > 12 ? 'positive' : 'warning',
    expected[1] === null ? 'unavailable' : 'negative',
    expected[2] === null ? 'unavailable' : 'positive',
  ]
  return targets.every((item, index) => item.state === expectedStates[index])
}

function topEventClaimCoverageMatches(payload, claims) {
  const topEvent = payload?.top_event
  if (topEvent === null || topEvent === undefined) return true
  if (!topEvent || typeof topEvent !== 'object' || Array.isArray(topEvent)) return false
  const fields = {
    avg_stance: 'top_event_average_stance',
    article_count: 'top_event_article_count',
    china_articles: 'top_event_china_article_count',
  }
  const published = Object.entries(fields)
    .filter(([field]) => topEvent[field] !== null && topEvent[field] !== undefined)
  if (!published.length) return true
  const chainId = String(topEvent.chain_id ?? '')
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(chainId)) return false
  for (const [field, metric] of published) {
    if (typeof topEvent[field] !== 'number' || !Number.isFinite(topEvent[field])) return false
    const claim = claims.find((item) => item.metric === metric)
    if (
      !claim
      || claim.claim_state !== 'derived_not_verified'
      || claim.identity?.slice?.chain_id !== chainId
      || claim.output_paths?.[0] !== `top_event.${field}`
    ) return false
  }
  return true
}

function claimBoundOutput(payload, claim) {
  const summaryFields = {
    weighted_stance_index: 'current_index',
    weighted_stance_change_24h: 'change_24h',
    article_volume_change_pct: 'growth_pct',
    article_count: 'article_count',
    source_count: 'source_count',
    event_family_count: 'family_count',
    positive_stance_share_pct: 'positive_pct',
    negative_stance_share_pct: 'negative_pct',
    neutral_stance_share_pct: 'neutral_pct',
  }
  const summaryField = summaryFields[claim.metric]
  if (summaryField) {
    return {
      exists: Boolean(payload?.summary && Object.hasOwn(payload.summary, summaryField)),
      value: payload?.summary?.[summaryField],
    }
  }
  const targetLabels = {
    target_weighted_stance_index: 'CN',
    negative_stance_pressure_index: 'NEG',
    positive_stance_support_index: 'POS',
  }
  if (targetLabels[claim.metric]) {
    const item = payload?.target_indices?.find((value) => value?.label === targetLabels[claim.metric])
    return { exists: Boolean(item && Object.hasOwn(item, 'value')), value: item?.value }
  }
  if (claim.metric.startsWith('event_family_')) {
    const item = payload?.families?.find(
      (value) => value?.event_family === claim.identity?.slice?.event_family,
    )
    const field = claim.metric === 'event_family_article_count' ? 'article_count' : 'avg_stance'
    return { exists: Boolean(item && Object.hasOwn(item, field)), value: item?.[field] }
  }
  if (claim.metric.startsWith('article_')) {
    const item = payload?.briefs?.find(
      (value) => value?.id === claim.identity?.slice?.news_id,
    )
    const field = claim.metric === 'article_stance_score' ? 'stance_score' : 'confidence'
    return { exists: Boolean(item && Object.hasOwn(item, field)), value: item?.[field] }
  }
  const topFields = {
    top_event_average_stance: 'avg_stance',
    top_event_article_count: 'article_count',
    top_event_china_article_count: 'china_articles',
  }
  const field = topFields[claim.metric]
  return {
    exists: Boolean(field && payload?.top_event && Object.hasOwn(payload.top_event, field)),
    value: field ? payload?.top_event?.[field] : undefined,
  }
}

function claimStateMatchesBoundOutput(payload, claim) {
  const bound = claimBoundOutput(payload, claim)
  if (!bound.exists) return false
  if (bound.value === null) {
    return claim.claim_state === 'explicit_unknown'
      && claim.reason_code === 'DERIVED_VALUE_UNAVAILABLE'
  }
  return typeof bound.value === 'number'
    && Number.isFinite(bound.value)
    && claim.claim_state === 'derived_not_verified'
    && claim.reason_code === 'DERIVED_VALUE_NOT_SOURCE_VERIFIED'
}

function suppressClaimBoundDerivations(output) {
  const summary = output?.summary
  if (summary && typeof summary === 'object' && !Array.isArray(summary)) {
    for (const field of [
      'current_index', 'change_24h', 'growth_pct', 'article_count', 'source_count',
      'family_count', 'positive_pct', 'negative_pct', 'neutral_pct',
    ]) summary[field] = null
    summary.trend_label = '不可计算'
  }
  for (const family of Array.isArray(output?.families) ? output.families : []) {
    if (family && typeof family === 'object') {
      family.article_count = null
      family.avg_stance = null
    }
  }
  for (const brief of Array.isArray(output?.briefs) ? output.briefs : []) {
    if (brief && typeof brief === 'object') {
      brief.stance_score = null
      brief.confidence = null
      brief.severity = 'unavailable'
    }
  }
  if (output?.top_event && typeof output.top_event === 'object') {
    output.top_event.avg_stance = null
    output.top_event.article_count = null
    output.top_event.china_articles = null
  }
  for (const metric of Array.isArray(output?.metrics) ? output.metrics : []) {
    if (metric && typeof metric === 'object') {
      metric.value = '不可计算'
      metric.display_tone = 'neutral'
    }
  }
}

export function validateOpinionClaimContract(contract, trust, payload) {
  const invalid = { valid: false, reasonCodes: ['INVALID_DERIVED_CLAIM_CONTRACT'] }
  if (!contract || typeof contract !== 'object' || Array.isArray(contract)) {
    return { valid: false, reasonCodes: ['MISSING_DERIVED_CLAIM_CONTRACT'] }
  }
  if (
    contract.schema_version !== OPINION_CLAIM_SCHEMA_VERSION
    || contract.status !== 'complete'
    || contract.max_claims !== OPINION_CLAIM_MAX_CLAIMS
    || !Array.isArray(contract.reason_codes)
    || contract.reason_codes.length !== 0
    || !Array.isArray(contract.claims)
    || contract.claims.length < OPINION_CORE_CLAIM_METRICS.size
    || contract.claims.length > OPINION_CLAIM_MAX_CLAIMS
  ) return invalid

  const days = trust?.snapshot?.filters?.days
  if (
    !Number.isInteger(days)
    || days < 7
    || days > 365
    || !targetIndicesMatchSemanticValues(payload)
  ) return invalid
  const seenIds = new Set()
  const coreCounts = new Map()
  for (const claim of contract.claims) {
    if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return invalid
    const metric = claim.metric
    if (!OPINION_CORE_CLAIM_METRICS.has(metric) && !OPINION_OPTIONAL_CLAIM_METRICS.has(metric)) {
      return invalid
    }
    if (!OPINION_CLAIM_ID_RE.test(claim.claim_id) || seenIds.has(claim.claim_id)) return invalid
    seenIds.add(claim.claim_id)
    if (OPINION_CORE_CLAIM_METRICS.has(metric)) {
      coreCounts.set(metric, (coreCounts.get(metric) || 0) + 1)
    }
    if (
      !Array.isArray(claim.output_paths)
      || claim.output_paths.length < 1
      || claim.output_paths.length > 2
      || new Set(claim.output_paths).size !== claim.output_paths.length
      || claim.output_paths.some((path) => typeof path !== 'string' || !OPINION_CLAIM_PATH_RE.test(path))
      || !claimPathsMatchSemanticBinding(claim)
    ) return invalid
    const identity = claim.identity
    if (!exactKeys(identity, [
      'metric', 'slice', 'model_version', 'method_version', 'data_cutoff',
      'snapshot_id', 'source_id',
    ])) return invalid
    const slice = identity.slice
    if (
      !slice || typeof slice !== 'object' || Array.isArray(slice)
      || Object.keys(slice).some((key) => !OPINION_CLAIM_SLICE_KEYS.has(key))
      || slice.population !== 'china_relevant_direct_articles'
      || slice.window_days !== days
    ) return invalid
    if ('event_family' in slice && !/^[a-z][a-z0-9_]{0,63}$/.test(slice.event_family)) return invalid
    if ('news_id' in slice && (!Number.isInteger(slice.news_id) || slice.news_id <= 0)) return invalid
    if ('chain_id' in slice && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(slice.chain_id)) return invalid
    if (
      identity.metric !== metric
      || identity.model_version !== trust?.model_version
      || identity.method_version !== trust?.method_version
      || identity.data_cutoff !== trust?.cutoff_date
      || identity.snapshot_id !== trust?.snapshot_id
      || identity.source_id !== 'public.china_opinion_article_scores'
      || claim.citation_locator !== null
      || claim.citation_status !== 'unavailable'
      || claim.citation_reason_code !== 'SAFE_CITATION_LOCATOR_UNAVAILABLE'
      || claim.source_truth_state !== 'not_verified'
      || !['derived_not_verified', 'explicit_unknown'].includes(claim.claim_state)
      || !['DERIVED_VALUE_NOT_SOURCE_VERIFIED', 'DERIVED_VALUE_UNAVAILABLE'].includes(claim.reason_code)
      || (claim.claim_state === 'derived_not_verified' && claim.reason_code !== 'DERIVED_VALUE_NOT_SOURCE_VERIFIED')
      || (claim.claim_state === 'explicit_unknown' && claim.reason_code !== 'DERIVED_VALUE_UNAVAILABLE')
      || !claimStateMatchesBoundOutput(payload, claim)
    ) return invalid
  }
  if (
    [...OPINION_CORE_CLAIM_METRICS].some((metric) => coreCounts.get(metric) !== 1)
    || !topEventClaimCoverageMatches(payload, contract.claims)
  ) return invalid
  return { valid: true, reasonCodes: [] }
}

export function sanitizeOpinionPayload(data, {
  forceReasonCodes = [],
  now = Date.now(),
  requireClaimContract = false,
} = {}) {
  const output = cloneJson(data && typeof data === 'object' ? data : {})
  const topTrust = output.trust
  const nestedTrust = output.meta?.trust
  const candidates = [topTrust, nestedTrust].filter((item) => item !== undefined && item !== null)
  const reasons = []
  let trust

  if (!candidates.length) {
    appendReason(reasons, 'MISSING_TRUST_METADATA')
    trust = missingTrust(reasons)
  } else if (candidates.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) {
    appendReason(reasons, 'INVALID_TRUST_METADATA')
    trust = missingTrust(reasons)
  } else {
    trust = cloneJson(candidates[0])
    if (candidates.length === 2 && canonicalJson(candidates[0]) !== canonicalJson(candidates[1])) {
      appendReason(reasons, 'CONFLICTING_TRUST_METADATA')
    }
  }

  if (trust.schema_version !== OPINION_TRUST_SCHEMA_VERSION) {
    appendReason(reasons, 'INVALID_SCHEMA_METADATA')
  }
  const coverage = trust.coverage && typeof trust.coverage === 'object' ? trust.coverage : null
  const freshness = trust.freshness && typeof trust.freshness === 'object' ? trust.freshness : null
  const model = trust.model && typeof trust.model === 'object' ? trust.model : null
  const method = trust.method && typeof trust.method === 'object' ? trust.method : null
  const source = trust.source && typeof trust.source === 'object' ? trust.source : null
  const snapshot = trust.snapshot && typeof trust.snapshot === 'object' ? trust.snapshot : null
  if (!coverage || !freshness) appendReason(reasons, 'INVALID_TRUST_METADATA')
  if (!model?.version) appendReason(reasons, 'MISSING_MODEL_METADATA')
  if (!method?.version) appendReason(reasons, 'MISSING_METHOD_METADATA')
  if (!source?.id || !source?.status) appendReason(reasons, 'MISSING_SOURCE_METADATA')
  else if (source.id !== 'public.china_opinion_article_scores') {
    appendReason(reasons, 'CONFLICTING_SOURCE_METADATA')
  }
  if (!snapshot?.id || !snapshot?.evaluated_on) appendReason(reasons, 'MISSING_SNAPSHOT_METADATA')
  if (trust.model_version !== model?.version) appendReason(reasons, 'CONFLICTING_MODEL_METADATA')
  if (trust.method_version !== method?.version) appendReason(reasons, 'CONFLICTING_METHOD_METADATA')
  if (trust.source_status !== source?.status) appendReason(reasons, 'CONFLICTING_SOURCE_METADATA')
  if (trust.snapshot_id !== snapshot?.id) appendReason(reasons, 'CONFLICTING_SNAPSHOT_METADATA')
  if (requireClaimContract) {
    for (const code of validateOpinionClaimContract(output.claim_contract, trust, output).reasonCodes) {
      appendReason(reasons, code)
    }
  }

  const declaredComputable = trust.is_computable
  if (typeof declaredComputable !== 'boolean') appendReason(reasons, 'INVALID_TRUST_METADATA')
  else {
    if (trust.computability !== (declaredComputable ? 'computable' : 'not_computable')) {
      appendReason(reasons, 'CONFLICTING_COMPUTABILITY_METADATA')
    }
    if (trust.status !== (declaredComputable ? 'ready' : 'unavailable')) {
      appendReason(reasons, 'CONFLICTING_STATUS_METADATA')
    }
    if (trust.trust_status !== (declaredComputable ? 'trusted' : 'unavailable')) {
      appendReason(reasons, 'CONFLICTING_STATUS_METADATA')
    }
    if (trust.display_mode !== (declaredComputable ? 'current' : 'historical_context')) {
      appendReason(reasons, 'CONFLICTING_DISPLAY_MODE_METADATA')
    }
  }

  const cutoffStamp = utcDateStamp(trust.cutoff_date)
  const todayStamp = currentUtcDateStamp(now)
  const ageDays = cutoffStamp === null || todayStamp === null
    ? null
    : Math.round((todayStamp - cutoffStamp) / 86_400_000)
  if (cutoffStamp === null) appendReason(reasons, 'MISSING_CUTOFF')
  else if (ageDays < 0) appendReason(reasons, 'INVALID_FUTURE_CUTOFF')
  else if (ageDays > OPINION_FRESHNESS_MAX_AGE_DAYS) appendReason(reasons, 'STALE_DATA')
  if (snapshot?.cutoff_date !== trust.cutoff_date) appendReason(reasons, 'CONFLICTING_SNAPSHOT_METADATA')
  if (source?.cutoff_date !== trust.cutoff_date) appendReason(reasons, 'CONFLICTING_SOURCE_METADATA')
  const snapshotEvaluatedStamp = utcDateStamp(trust.revalidated_on || snapshot?.evaluated_on)
  const snapshotAgeDays = cutoffStamp === null || snapshotEvaluatedStamp === null
    ? null
    : Math.round((snapshotEvaluatedStamp - cutoffStamp) / 86_400_000)
  const snapshotFreshnessState = cutoffStamp === null
    ? 'missing'
    : snapshotAgeDays < 0
      ? 'invalid'
      : snapshotAgeDays > OPINION_FRESHNESS_MAX_AGE_DAYS
        ? 'stale'
        : 'fresh'
  if (freshness && (
    freshness.age_days !== snapshotAgeDays
    || freshness.state !== snapshotFreshnessState
    || freshness.maximum_age_days !== OPINION_FRESHNESS_MAX_AGE_DAYS
  )) appendReason(reasons, 'CONFLICTING_FRESHNESS_METADATA')
  for (const value of [output.latest_date, output.meta?.last_article_date]) {
    if (value !== undefined && value !== null && value !== '' && value !== trust.cutoff_date) {
      appendReason(reasons, 'CONFLICTING_CUTOFF_METADATA')
    }
  }

  const articleCount = finiteNonNegativeInteger(coverage?.article_count)
  const sourceCount = finiteNonNegativeInteger(coverage?.source_count)
  const minimumArticles = finiteNonNegativeInteger(coverage?.minimum_articles)
  const minimumSources = finiteNonNegativeInteger(coverage?.minimum_sources)
  if (articleCount === null || minimumArticles === null || minimumSources === null) {
    appendReason(reasons, 'INVALID_COVERAGE_METADATA')
  } else if (minimumArticles !== 10 || minimumSources !== 3) {
    appendReason(reasons, 'CONFLICTING_COVERAGE_METADATA')
  }
  if (articleCount !== null && articleCount < 10) appendReason(reasons, 'LOW_ARTICLE_COVERAGE')
  if (sourceCount === null) appendReason(reasons, 'MISSING_SOURCE_COVERAGE')
  else if (sourceCount < 3) {
    appendReason(reasons, 'LOW_SOURCE_COVERAGE')
  }
  const expectedSourceStatus = cutoffStamp !== null && (articleCount || 0) > 0
    ? 'available'
    : 'missing'
  if (source?.status && source.status !== expectedSourceStatus) {
    appendReason(reasons, 'CONFLICTING_SOURCE_METADATA')
  }
  for (const code of Array.isArray(trust.reason_codes) ? trust.reason_codes : []) {
    appendReason(reasons, String(code || ''))
  }
  if (declaredComputable === false && reasons.length === 0) {
    appendReason(reasons, 'DECLARED_UNCOMPUTABLE')
  }
  for (const code of forceReasonCodes) appendReason(reasons, String(code || ''))
  const computable = declaredComputable === true && reasons.length === 0
  trust.reason_codes = reasons
  trust.is_computable = computable
  trust.computability = computable ? 'computable' : 'not_computable'
  trust.status = computable ? 'ready' : 'unavailable'
  trust.trust_status = computable ? 'trusted' : 'unavailable'
  trust.display_mode = computable ? 'current' : 'historical_context'
  trust.freshness = {
    ...(freshness || {}),
    state: cutoffStamp === null ? 'missing' : ageDays < 0 ? 'invalid' : ageDays > 2 ? 'stale' : 'fresh',
    age_days: ageDays,
    maximum_age_days: OPINION_FRESHNESS_MAX_AGE_DAYS,
  }
  trust.revalidated_on = todayStamp === null
    ? null
    : new Date(todayStamp).toISOString().slice(0, 10)
  output.trust = trust
  output.meta = output.meta && typeof output.meta === 'object' ? output.meta : {}
  output.meta.trust = cloneJson(trust)

  if (!computable) {
    if ('dates' in output || 'values' in output || 'heat' in output) {
      output.dates = []
      output.values = []
      output.heat = []
    }
    suppressFrontendComposites(output)
    for (const item of Array.isArray(output.target_indices) ? output.target_indices : []) {
      item.value = null
      item.trend_values = []
      item.state = 'unavailable'
    }
    if (output.summary && typeof output.summary === 'object') output.summary.trend_label = '不可计算'
    for (const metric of Array.isArray(output.metrics) ? output.metrics : []) {
      if (/变化|指数|立场|影响|当前值/.test(String(metric?.label || ''))) {
        metric.value = '不可计算'
        metric.display_tone = 'neutral'
      }
    }
    if (
      requireClaimContract
      && reasons.some((code) => [
        'MISSING_DERIVED_CLAIM_CONTRACT',
        'INVALID_DERIVED_CLAIM_CONTRACT',
      ].includes(code))
    ) suppressClaimBoundDerivations(output)
    output.meta.composite_suppressed = true
  }
  return enforceOpinionSemanticBoundary(output)
}

export function invalidateOpinionPayload(data, reasonCode = 'REFRESH_FAILED', options = {}) {
  return sanitizeOpinionPayload(data, {
    ...options,
    forceReasonCodes: [reasonCode],
  })
}

export function opinionTrustSnapshot(data, options = {}) {
  const sanitized = sanitizeOpinionPayload(data, options)
  const trust = sanitized?.trust || null
  const reasonCodes = Array.isArray(trust?.reason_codes) ? trust.reason_codes : []
  const computable = trust?.is_computable === true
  const articleCount = finiteNonNegativeInteger(trust?.coverage?.article_count) ?? 0
  const sourceCount = finiteNonNegativeInteger(trust?.coverage?.source_count)
  const minimumArticles = finiteNonNegativeInteger(trust?.coverage?.minimum_articles) ?? 0
  const minimumSources = finiteNonNegativeInteger(trust?.coverage?.minimum_sources) ?? 0
  const ageDays = trust?.freshness?.age_days
  const reasons = reasonCodes.map((code) => TRUST_REASON_LABELS[code] || code)
  return {
    computable,
    headline: computable ? '目标立场指数输入通过门禁' : '目标立场指数不可计算',
    detail: computable
      ? `截止 ${trust.cutoff_date}，样本与信源覆盖满足最低门槛`
      : reasons.join('；') || '可信状态待核验',
    cutoffDate: trust?.cutoff_date || '--',
    ageDays: Number.isFinite(ageDays) ? ageDays : null,
    articleCount,
    sourceCount,
    minimumArticles,
    minimumSources,
    methodVersion: trust?.method_version || data?.meta?.method_version || '--',
    modelVersion: trust?.model_version || '--',
    schemaVersion: trust?.schema_version || '--',
    snapshotId: trust?.snapshot_id || '--',
    sourceStatus: trust?.source_status || '--',
  }
}

export function buildDateExplanation(newsRows, summary) {
  const news = Array.isArray(newsRows) ? newsRows : []
  const measuredNews = news
    .map((item) => ({ item, stance: opinionSemanticSnapshot(item).stance }))
    .filter((entry) => entry.stance.state === 'available')
  const supportiveCount = measuredNews.filter(
    (entry) => entry.stance.category === 'supportive',
  ).length
  const criticalCount = measuredNews.filter(
    (entry) => entry.stance.category === 'critical',
  ).length
  const neutralCount = measuredNews.filter(
    (entry) => entry.stance.category === 'neutral',
  ).length
  const unknownCount = Math.max(0, news.length - measuredNews.length)
  const familyCounts = news.reduce((accumulator, item) => {
    const key = item.event_family || 'unknown'
    accumulator[key] = (accumulator[key] || 0) + 1
    return accumulator
  }, {})
  const topFamily = Object.entries(familyCounts).sort((left, right) => right[1] - left[1])[0]
  const directionalCount = supportiveCount + criticalCount || 1
  const sourceCount = Number(summary?.source_count || 0)
    || new Set(news.map((item) => item.source).filter(Boolean)).size
  const leadingNews = measuredNews
    .sort((left, right) => Math.abs(right.stance.score) - Math.abs(left.stance.score))
    .slice(0, 3)
    .map((entry) => entry.item)
  return {
    supportiveCount,
    criticalCount,
    neutralCount,
    unknownCount,
    supportiveWidth: supportiveCount
      ? Math.max(2, Math.round((supportiveCount / directionalCount) * 50))
      : 0,
    criticalWidth: criticalCount
      ? Math.max(2, Math.round((criticalCount / directionalCount) * 50))
      : 0,
    sourceCount,
    newsCount: news.length,
    topFamily: topFamily ? formatFamilyName(topFamily[0]) : '暂无',
    topFamilyCount: topFamily ? topFamily[1] : 0,
    leadingNews,
  }
}

export function addLocalSearchHistory(current, query, now = new Date()) {
  const normalized = String(query || '').trim()
  if (!normalized) return current
  const time = [
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
  ].join(' ')
  return [
    { query: normalized, time },
    ...(current || []).filter((item) => String(item?.query || '').trim() !== normalized),
  ].slice(0, 50)
}

export function mergeFavoriteIds(currentIds, rows) {
  const additions = (Array.isArray(rows) ? rows : [])
    .filter((item) => item?.is_favorited && item.id !== undefined && item.id !== null)
    .map((item) => Number(item.id))
    .filter(Number.isFinite)
  return additions.length ? Array.from(new Set([...(currentIds || []), ...additions])) : currentIds
}

export function applyFavoriteResult(currentIds, newsId, favorited) {
  const normalized = Number(newsId)
  if (favorited) return Array.from(new Set([...(currentIds || []), normalized]))
  return (currentIds || []).filter((id) => id !== normalized)
}
