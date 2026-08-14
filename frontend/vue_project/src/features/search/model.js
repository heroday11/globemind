import { safeExternalHttpUrl } from '../../utils/externalUrl.js'
import { searchModeDisclosure } from '../../governance/searchQuerySemantics.js'
import { normalizeSearchHitDisclosure } from './hit-fields.js'

export {
  SEARCH_MODE_SEMANTICS_VERSION,
  SEARCH_MODES,
  searchModeDisclosure,
} from '../../governance/searchQuerySemantics.js'

export const SEARCH_PARAM_DEFAULTS = Object.freeze({
  topic: '',
  mustInclude: '',
  anyInclude: '',
  needExclude: '',
  publishTime: '不限',
  startTime: '',
  endTime: '',
  hitLocation: '全文',
  dataSource: '',
  language: '',
  site: '',
  page: 1,
  pageSize: 10,
  sortBy: '',
  sortOrder: 'desc',
  mode: 'exact',
  searchType: 'news',
})

export const SEARCH_TIME_RANGES = Object.freeze([
  Object.freeze({ label: '不限时间', value: '不限' }),
  Object.freeze({ label: '近一天', value: '近一天' }),
  Object.freeze({ label: '近一周', value: '近一周' }),
  Object.freeze({ label: '近一月', value: '近一月' }),
  Object.freeze({ label: '近三月', value: '近三月' }),
  Object.freeze({ label: '近一年', value: '近一年' }),
])

export const SEARCH_HIT_LOCATIONS = Object.freeze([
  Object.freeze({ label: '标题优先', value: '全文' }),
  Object.freeze({ label: '标题', value: '标题' }),
  Object.freeze({ label: '摘要', value: '摘要' }),
  Object.freeze({ label: '正文', value: '正文' }),
])

export const SEARCH_TYPES = Object.freeze([
  Object.freeze({ label: '新闻', value: 'news' }),
  Object.freeze({ label: 'L1事件', value: 'l1' }),
  Object.freeze({ label: 'L2走势', value: 'l2' }),
  Object.freeze({ label: 'L3大事件', value: 'l3' }),
])

const SEARCH_RESULT_TIME_SCHEMA = 'search-result-time-semantics-v1'
const SEARCH_RESULT_TIME_KEYS = new Set([
  'schema_version',
  'published_at',
  'event_time_start',
  'event_time_end',
  'collected_at',
  'updated_at',
  'legacy_pub_time_status',
  'legacy_created_at_status',
])

function unavailableNewsTimeSemantics() {
  return {
    schemaVersion: 'unavailable',
    publishedAt: null,
    eventTimeStart: null,
    eventTimeEnd: null,
    collectedAt: null,
    updatedAt: null,
    legacyPubTimeStatus: 'legacy_unverified_not_used',
    legacyCreatedAtStatus: 'legacy_unverified_not_used',
  }
}

function boundedTimestamp(value) {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'string' || value.length > 64) return null
  const normalized = value.trim()
  if (!normalized || normalized.length > 64) return null
  if (!/^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$/.test(normalized)) {
    return null
  }
  return Number.isFinite(Date.parse(normalized.replace(' ', 'T'))) ? normalized : null
}

export function normalizeNewsTimeSemantics(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    return unavailableNewsTimeSemantics()
  }
  const value = item.time_semantics
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return unavailableNewsTimeSemantics()
  }
  const keys = Object.keys(value)
  if (
    keys.length > SEARCH_RESULT_TIME_KEYS.size
    || keys.some((key) => !SEARCH_RESULT_TIME_KEYS.has(key))
    || value.schema_version !== SEARCH_RESULT_TIME_SCHEMA
    || value.legacy_pub_time_status !== 'legacy_alias_of_published_at_value_unverified'
    || value.legacy_created_at_status !== 'legacy_unverified_not_used'
  ) {
    return unavailableNewsTimeSemantics()
  }
  const publishedAt = boundedTimestamp(value.published_at)
  const eventTimeStart = boundedTimestamp(value.event_time_start)
  const eventTimeEnd = boundedTimestamp(value.event_time_end)
  if (item.pub_time !== null && item.pub_time !== undefined && item.pub_time !== '') {
    const legacyPubTime = boundedTimestamp(item.pub_time)
    if (
      !legacyPubTime
      || !publishedAt
      || Date.parse(legacyPubTime.replace(' ', 'T')) !== Date.parse(publishedAt.replace(' ', 'T'))
    ) {
      return unavailableNewsTimeSemantics()
    }
  }
  if (
    eventTimeStart
    && eventTimeEnd
    && Date.parse(eventTimeStart.replace(' ', 'T')) > Date.parse(eventTimeEnd.replace(' ', 'T'))
  ) {
    return unavailableNewsTimeSemantics()
  }
  return {
    schemaVersion: SEARCH_RESULT_TIME_SCHEMA,
    publishedAt,
    eventTimeStart,
    eventTimeEnd,
    collectedAt: boundedTimestamp(value.collected_at),
    updatedAt: boundedTimestamp(value.updated_at),
    legacyPubTimeStatus: 'legacy_alias_of_published_at_value_unverified',
    legacyCreatedAtStatus: 'legacy_unverified_not_used',
  }
}

export const SEARCH_SITUATION_PRESETS = Object.freeze([
  Object.freeze({
    label: '台海风险',
    hint: '查看穿越、军演与军备动态的原始新闻',
    topic: 'Taiwan',
    mustInclude: '',
    anyInclude: '',
    type: 'news',
    typeLabel: '新闻',
    strategyLabel: '快讯',
    mode: 'fuzzy',
    time: '不限',
    timeField: 'published_at',
    timeLabel: '新闻发布日期：不限',
  }),
  Object.freeze({
    label: '中美博弈',
    hint: '追踪中美外交、贸易、科技与安全议题走势',
    topic: 'China USA',
    mustInclude: '',
    anyInclude: '',
    type: 'l2',
    typeLabel: 'L2走势',
    strategyLabel: '走势',
    mode: 'exact',
    time: '不限',
    timeField: 'event_time',
    timeLabel: '事件时间：不限',
  }),
  Object.freeze({
    label: '南海动向',
    hint: '聚焦南海、菲律宾、海警、军事活动相关报道',
    topic: 'South China Sea',
    mustInclude: '',
    anyInclude: '',
    type: 'l1',
    typeLabel: 'L1事件',
    strategyLabel: '事件',
    mode: 'exact',
    time: '不限',
    timeField: 'event_time',
    timeLabel: '事件时间：不限',
  }),
  Object.freeze({
    label: '俄乌战争',
    hint: '查看俄乌战争及外部响应的大事件结构',
    topic: 'Ukraine',
    mustInclude: '',
    anyInclude: '',
    type: 'l3',
    typeLabel: 'L3大事件',
    strategyLabel: '大事',
    mode: 'fuzzy',
    time: '不限',
    timeField: 'event_time',
    timeLabel: '事件时间：不限',
  }),
  Object.freeze({
    label: '加沙冲突',
    hint: '按 Gaza 主题扩展查看相关大事件；不代表整个中东冲突集合',
    topic: 'Gaza',
    mustInclude: '',
    anyInclude: '',
    type: 'l3',
    typeLabel: 'L3大事件',
    strategyLabel: '大事',
    mode: 'fuzzy',
    time: '不限',
    timeField: 'event_time',
    timeLabel: '事件时间：不限',
  }),
  Object.freeze({
    label: '科技管制',
    hint: '模糊召回科技、芯片与出口管制相关报道',
    topic: 'control',
    mustInclude: '',
    anyInclude: '',
    type: 'news',
    typeLabel: '新闻',
    strategyLabel: '关联',
    mode: 'fuzzy',
    time: '不限',
    timeField: 'published_at',
    timeLabel: '新闻发布日期：不限',
  }),
])

export function createSearchParams(overrides = {}) {
  return { ...SEARCH_PARAM_DEFAULTS, ...overrides }
}

/**
 * Keep the browser-visible state aligned with the backend's strict filter
 * contract. Hierarchy searches do not currently implement news-source,
 * language, hit-location, or caller-selected ordering controls.
 */
export function normalizeSearchParamsForType(searchParams, searchType = searchParams?.searchType) {
  const normalized = createSearchParams({
    ...(searchParams && typeof searchParams === 'object' ? searchParams : {}),
    searchType: searchType || 'news',
  })
  if (normalized.searchType !== 'news') {
    normalized.hitLocation = '全文'
    normalized.dataSource = ''
    normalized.language = ''
    normalized.site = ''
    normalized.sortBy = ''
    normalized.sortOrder = 'desc'
  } else {
    if (normalized.sortBy === 'pub_time') normalized.sortBy = 'published_at'
    if (!['', 'similarity', 'published_at'].includes(normalized.sortBy)) {
      normalized.sortBy = ''
    }
    if (!['asc', 'desc'].includes(normalized.sortOrder)) normalized.sortOrder = 'desc'
    if (normalized.sortBy !== 'published_at') normalized.sortOrder = 'desc'
  }
  return normalized
}

/**
 * Expand a visible situation preset into the existing editable search state.
 * Keeping the mutation here makes the state identity and filter reset contract
 * independently testable while remaining compatible with Vue reactive proxies.
 */
export function applySearchSituationPreset(searchParams, preset, pageSize = searchParams?.pageSize) {
  if (!searchParams || typeof searchParams !== 'object' || Array.isArray(searchParams)) {
    throw new TypeError('searchParams must be an editable object')
  }
  if (!preset || typeof preset !== 'object' || Array.isArray(preset)) {
    throw new TypeError('preset must be an object')
  }
  const searchType = preset.type || 'news'
  const normalized = normalizeSearchParamsForType({
    ...searchParams,
    topic: preset.topic || '',
    mustInclude: preset.mustInclude || '',
    anyInclude: preset.anyInclude || '',
    needExclude: '',
    publishTime: preset.time || '不限',
    startTime: '',
    endTime: '',
    hitLocation: '全文',
    dataSource: '',
    language: '',
    site: '',
    mode: preset.mode || 'exact',
    searchType,
    sortBy: searchType === 'news' ? 'published_at' : '',
    sortOrder: 'desc',
    page: 1,
    pageSize: pageSize ?? searchParams.pageSize ?? SEARCH_PARAM_DEFAULTS.pageSize,
  }, searchType)
  Object.assign(searchParams, normalized)
  return searchParams
}

export function searchSortSemantics(searchParams) {
  const searchType = String(searchParams?.searchType || 'news')
  if (searchType !== 'news') {
    return '系统排序（匹配覆盖、层级质量、事件规模与事件时间）'
  }
  if (searchParams?.sortBy === 'published_at' || searchParams?.sortBy === 'pub_time') {
    return searchParams?.sortOrder === 'asc' ? '新闻发布日期升序' : '新闻发布日期倒序'
  }
  return '标题命中优先，其次按新闻发布日期倒序'
}

export function buildSearchRequestDto(searchParams, favoriteScopeTopic) {
  const effectiveParams = normalizeSearchParamsForType(searchParams)
  const topic = String(effectiveParams.topic || '').trim()
  const mustInclude = String(effectiveParams.mustInclude || '').trim()
  const anyInclude = String(effectiveParams.anyInclude || '').trim()
  const params = {
    topic: topic || undefined,
    keyword: topic || undefined,
    must_include: mustInclude || undefined,
    any_include: anyInclude || undefined,
    need_exclude: effectiveParams.needExclude || undefined,
    publish_time:
      effectiveParams.publishTime && effectiveParams.publishTime !== '不限'
        ? effectiveParams.publishTime
        : undefined,
    start_time: effectiveParams.startTime || undefined,
    end_time: effectiveParams.endTime || undefined,
    time_field: effectiveParams.searchType === 'news' ? 'published_at' : 'event_time',
    hit_location: effectiveParams.hitLocation,
    data_source: effectiveParams.dataSource || undefined,
    language: effectiveParams.language || undefined,
    site: effectiveParams.site || undefined,
    page: effectiveParams.page,
    page_size: effectiveParams.pageSize,
    sort_by: effectiveParams.sortBy,
    sort_order: effectiveParams.sortOrder,
    mode: effectiveParams.mode,
    search_type: effectiveParams.searchType,
    favorite_scope_topic: favoriteScopeTopic || undefined,
  }
  for (const key of Object.keys(params)) {
    if (params[key] === '' || params[key] === undefined) delete params[key]
  }
  return params
}

export function buildSearchHistoryKeyword(searchParams) {
  const ellipsize = (text, length) => (
    text.length > length ? `${text.slice(0, length)}…` : text
  )
  const topic = String(searchParams.topic || '').trim()
  if (topic) return ellipsize(topic, 220)
  const mustInclude = String(searchParams.mustInclude || '').trim()
  if (mustInclude) return ellipsize(mustInclude, 220)
  const anyInclude = String(searchParams.anyInclude || '').trim()
  if (anyInclude) return ellipsize(anyInclude, 220)
  const parts = []
  if (searchParams.publishTime && searchParams.publishTime !== '不限') {
    const timeFieldLabel = searchParams.searchType === 'news' ? '新闻发布日期' : '事件时间'
    parts.push(`${timeFieldLabel}:${searchParams.publishTime}`)
  }
  if (searchParams.dataSource) parts.push(`来源:${searchParams.dataSource}`)
  if (searchParams.language) parts.push(`语种:${searchParams.language}`)
  if (searchParams.hitLocation && searchParams.hitLocation !== '全文') {
    parts.push(`命中:${searchParams.hitLocation}`)
  }
  if (searchParams.mode && searchParams.mode !== 'exact') {
    parts.push(`模式:${searchParams.mode}`)
  }
  if (searchParams.searchType && searchParams.searchType !== 'news') {
    parts.push(`类型:${searchParams.searchType}`)
  }
  return parts.join(' ') || '条件检索'
}

export function normalizeNewsListResponse(result) {
  const rows = (result?.data || []).map((item) => ({
    ...item,
    id: Number(item.id),
    is_favorited: !!item.is_favorited,
    is_warned: !!item.is_warned,
    selected: false,
    timeSemantics: normalizeNewsTimeSemantics(item),
  }))
  return {
    rows,
    total: result?.total ?? 0,
  }
}

export function resolveLanguageDisplay(item, languageOptions = []) {
  if (!item || typeof item !== 'object') return ''
  const raw = item.language_id ?? item.languageId
  if (raw === undefined || raw === null || raw === '') return ''
  const rawText = String(raw)
  const options = Array.isArray(languageOptions) ? languageOptions : []
  const hitById = options.find((option) => String(option?.id) === rawText)
  if (hitById?.name) return String(hitById.name)
  const hitByName = options.find((option) => String(option?.name) === rawText)
  if (hitByName?.name) return String(hitByName.name)
  return rawText
}

export function normalizeSearchResponse(result, searchType, fallbackPagination) {
  const response = result && typeof result === 'object' ? result : {}
  const normalized = {
    clusterTree: [],
    eventCorefClusters: [],
    microStoryItems: [],
    macroEventItems: [],
    searchResults: [],
    total: response.total || 0,
    page: response.page || fallbackPagination.page,
    pageSize: response.page_size || fallbackPagination.pageSize,
    queryExplain: normalizeQueryExplain(response.query_explain),
    queryReceipt: normalizeQueryReceipt(response.query_receipt),
  }
  if (searchType === 'l1') {
    normalized.eventCorefClusters = Array.isArray(response.event_coref_clusters)
      ? response.event_coref_clusters
      : []
    return normalized
  }
  if (searchType === 'l2' || searchType === 'l3') {
    normalized.macroEventItems = Array.isArray(response.macro_event_items)
      ? response.macro_event_items.map((item) => ({
          ...item,
          id: String(item.id),
          level: item.level || searchType,
        }))
      : []
    return normalized
  }
  normalized.clusterTree = Array.isArray(response.cluster_tree) ? response.cluster_tree : []
  normalized.eventCorefClusters = Array.isArray(response.event_coref_clusters)
    ? response.event_coref_clusters
    : []
  normalized.searchResults = (response.data || []).map((item) => ({
    ...item,
    id: Number(item.id),
    is_favorited: !!item.is_favorited,
    is_warned: !!item.is_warned,
    timeSemantics: normalizeNewsTimeSemantics(item),
  }))
  return normalized
}

export function normalizeQueryExplain(value) {
  if (!value || typeof value !== 'object') return null
  return {
    ...value,
    query_language: String(value.query_language || 'unavailable'),
    query_ast: value.query_ast && typeof value.query_ast === 'object'
      ? value.query_ast
      : { type: 'unavailable' },
    expanded_query_ast: value.expanded_query_ast && typeof value.expanded_query_ast === 'object'
      ? value.expanded_query_ast
      : { type: 'unavailable' },
    execution_expression: String(value.execution_expression || ''),
    limits: value.limits && typeof value.limits === 'object' ? value.limits : {},
    normalized_terms: Array.isArray(value.normalized_terms) ? value.normalized_terms : [],
    expanded_terms: Array.isArray(value.expanded_terms) ? value.expanded_terms : [],
    effective_search_fields: Array.isArray(value.effective_search_fields)
      ? value.effective_search_fields
      : [],
    entity_expansions: Array.isArray(value.entity_expansions)
      ? value.entity_expansions.map((item) => ({
          ...item,
          expanded_aliases: Array.isArray(item?.expanded_aliases) ? item.expanded_aliases : [],
          expanded_alias_details: Array.isArray(item?.expanded_alias_details)
            ? item.expanded_alias_details
            : [],
          review_status: item?.review_status || 'review_required',
        }))
      : [],
    applied_filters: Array.isArray(value.applied_filters) ? value.applied_filters : [],
    stages: Array.isArray(value.stages) ? value.stages : [],
    relaxation_suggestions: Array.isArray(value.relaxation_suggestions)
      ? value.relaxation_suggestions
      : [],
  }
}

export function normalizeQueryReceipt(value) {
  if (!value || typeof value !== 'object') return null
  const coverage = value.result_coverage && typeof value.result_coverage === 'object'
    ? value.result_coverage
    : {}
  return {
    ...value,
    schema_version: String(value.schema_version || 'unavailable'),
    receipt_kind: String(value.receipt_kind || 'execution_receipt'),
    method_version: String(value.method_version || 'unavailable'),
    receipt_id: String(value.receipt_id || ''),
    receipt_sha256: String(value.receipt_sha256 || ''),
    normalized_contract: value.normalized_contract && typeof value.normalized_contract === 'object'
      ? value.normalized_contract
      : {},
    normalized_contract_sha256: String(value.normalized_contract_sha256 || ''),
    entity_catalog_version: String(value.entity_catalog_version || 'unavailable'),
    entity_catalog_review_status: value.entity_catalog_review_status || 'review_required',
    time_field: value.time_field && typeof value.time_field === 'object'
      ? value.time_field
      : { requested: 'unavailable', applied: 'unavailable' },
    applied_filters: Array.isArray(value.applied_filters) ? value.applied_filters : [],
    ordered_returned_ids: Array.isArray(value.ordered_returned_ids)
      ? value.ordered_returned_ids.map((item) => String(item))
      : [],
    ordered_returned_ids_sha256: String(value.ordered_returned_ids_sha256 || ''),
    result_coverage: {
      ...coverage,
      status: coverage.status || 'unavailable',
      cutoff: coverage.cutoff || null,
      coverage_start: coverage.coverage_start || null,
      coverage_end: coverage.coverage_end || null,
      returned_result_count: Number(coverage.returned_result_count) || 0,
      timed_result_count: Number(coverage.timed_result_count) || 0,
    },
    snapshot_status: value.snapshot_status || 'not_frozen',
    frozen_data_snapshot_id: value.frozen_data_snapshot_id || null,
    receipt_note: String(value.receipt_note || ''),
  }
}

export function mergeFavoriteNewsIds(currentIds, rows) {
  if (!Array.isArray(rows) || rows.length === 0) return currentIds
  const ids = rows
    .filter((item) => item && item.is_favorited && item.id !== undefined && item.id !== null)
    .map((item) => Number(item.id))
    .filter((id) => Number.isFinite(id))
  if (!ids.length) return currentIds
  return Array.from(new Set([...currentIds, ...ids]))
}

export function normalizeDisplayNewsRows(rawRows, showOnlyFirst, createFallbackId = () => (
  Math.random().toString(36).substr(2, 9)
)) {
  const normalized = rawRows.map((item) => {
    const timeSemantics = normalizeNewsTimeSemantics(item)
    const title = item.title || '无标题'
    const displayDesc = item.abstract || item.desc || '—'
    return {
      ...item,
      id: item.id || createFallbackId(),
      title,
      displayDesc,
      displayTime: timeSemantics.publishedAt || undefined,
      displayUrl: safeExternalHttpUrl(item.request_url || item.requestUrl),
      isFirstRelease: item.is_first_release || item.isFirstRelease || false,
      is_favorited: !!item.is_favorited,
      is_warned: !!item.is_warned,
      selected: item.selected || false,
      timeSemantics,
      searchHit: normalizeSearchHitDisclosure(item.search_hit, {
        title,
        abstract: displayDesc,
      }),
    }
  })
  return showOnlyFirst ? normalized.filter((item) => item.isFirstRelease) : [...normalized]
}

export function activeSearchSurfaceLabel(searchParams, searchTypes = SEARCH_TYPES) {
  const type = searchTypes.find((item) => item.value === searchParams.searchType)?.label || '新闻'
  const time = searchParams.publishTime && searchParams.publishTime !== '不限'
    ? searchParams.publishTime
    : '全时段'
  const mode = searchModeDisclosure(searchParams.mode).label
  return `${type} · ${time} · ${mode}`
}

export function calculateTotalPages(totalItems, pageSize) {
  if (pageSize === 0) return 0
  return Math.ceil(totalItems / pageSize)
}

export function buildVisiblePages(currentPage, totalPages, forwardCount = 8) {
  const pages = []
  const start = Math.max(1, currentPage)
  const end = Math.min(totalPages, start + forwardCount)
  for (let page = start; page <= end; page += 1) pages.push(page)
  return pages
}

export function resolveJumpPage(value, currentPage, totalPages) {
  const page = parseInt(value)
  return page >= 1 && page <= totalPages ? page : currentPage
}
