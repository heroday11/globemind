export const SENTIMENT_OPTIONS = Object.freeze([
  Object.freeze({ value: 'all', label: '全部立场' }),
  Object.freeze({ value: 'positive', label: '支持立场' }),
  Object.freeze({ value: 'negative', label: '批评立场' }),
])

export const DIAGNOSTIC_TABS = Object.freeze([
  Object.freeze({ value: 'dimensions', label: '维度' }),
  Object.freeze({ value: 'quality', label: '质量' }),
])

export const SEARCH_PUBLISH_TIME_OPTIONS = Object.freeze([
  Object.freeze({ label: '不限时间', value: '不限' }),
  Object.freeze({ label: '近一天', value: '近一天' }),
  Object.freeze({ label: '近一周', value: '近一周' }),
  Object.freeze({ label: '近一月', value: '近一月' }),
  Object.freeze({ label: '近三月', value: '近三月' }),
  Object.freeze({ label: '近一年', value: '近一年' }),
])

export const SEARCH_PAGE_SIZE_OPTIONS = Object.freeze([10, 20, 50])

export function buildOverviewQuery(days = 30, { now = Date.now(), refresh = false } = {}) {
  return {
    days: Math.max(7, Math.min(365, Number(days) || 30)),
    refresh: refresh ? 'true' : 'false',
    _: now,
  }
}

export function buildTrendQuery(days, sentimentFilter, { now = Date.now(), refresh = false } = {}) {
  return {
    days: Number(days) || 30,
    china_min_score: '0.4',
    sentiment_filter: sentimentFilter || 'all',
    refresh: refresh ? 'true' : 'false',
    _: now,
  }
}

export function buildInsightNewsQuery({
  days = 30,
  eventFamily = '',
  pageSize = 10,
  sentimentFilter = 'all',
} = {}) {
  return {
    days,
    sentiment_filter: sentimentFilter,
    page_size: pageSize,
    event_family: eventFamily || undefined,
  }
}

export function buildDateNewsQuery(date, sentimentFilter = 'all') {
  return {
    date_str: date,
    sentiment_filter: sentimentFilter,
    page_size: 50,
  }
}

const FEEDBACK_CORRECTIONS = new Set(['irrelevant', 'too_positive', 'too_negative', 'correct'])

export function buildFeedbackDto(news, correction) {
  const newsId = news?.id
  if (!Number.isSafeInteger(newsId) || newsId <= 0) {
    throw new TypeError('feedback news id must be a positive integer')
  }
  if (!FEEDBACK_CORRECTIONS.has(correction)) {
    throw new TypeError('feedback correction is invalid')
  }
  return {
    news_id: newsId,
    correction,
    purpose: 'quality_correction',
    training_consent: false,
    training_opt_out: true,
  }
}

export function buildMacroEventClustersQuery(macroEventId, date, page) {
  return {
    macro_event_id: macroEventId,
    date_str: date,
    page: Math.max(1, Number(page) || 1),
    page_size: 30,
  }
}

export function buildSentimentSearchDto({ keyword, page, pageSize, publishTime }) {
  return {
    keyword: String(keyword || '').trim(),
    publish_time: publishTime && publishTime !== '不限' ? publishTime : undefined,
    page: Math.max(1, Number(page) || 1),
    page_size: Number(pageSize) || 10,
    hit_location: '全文',
  }
}

export function normalizeTrendResponse(payload) {
  return {
    dates: Array.isArray(payload?.dates) ? payload.dates : [],
    values: Array.isArray(payload?.values) ? payload.values : [],
    heat: Array.isArray(payload?.heat) ? payload.heat : [],
    metric_id: payload?.metric_id,
    semantic_contract: payload?.semantic_contract,
    semantic_dimensions: payload?.semantic_dimensions,
    meta: payload?.meta && typeof payload.meta === 'object' ? payload.meta : {},
  }
}

function semanticEnvelope(payload) {
  const result = {}
  if (payload && Object.hasOwn(payload, 'semantic_contract')) {
    result.semantic_contract = payload.semantic_contract
  }
  if (payload && Object.hasOwn(payload, 'semantic_dimensions')) {
    result.semantic_dimensions = payload.semantic_dimensions
  }
  return result
}

export function normalizeInsightNewsResponse(payload) {
  return {
    news: Array.isArray(payload?.news) ? payload.news : [],
    trust: payload?.trust || null,
    meta: payload?.meta && typeof payload.meta === 'object' ? payload.meta : {},
    ...semanticEnvelope(payload),
  }
}

export function normalizeDateNewsResponse(payload) {
  const news = Array.isArray(payload?.news) ? payload.news : []
  return {
    news,
    total: Number(payload?.total || 0),
    summary: payload?.summary || null,
    trust: payload?.trust || null,
    meta: payload?.meta && typeof payload.meta === 'object' ? payload.meta : {},
    ...semanticEnvelope(payload),
  }
}

export function normalizeSearchResult(item) {
  return {
    ...item,
    id: Number(item?.id),
    is_favorited: Boolean(item?.is_favorited),
  }
}

export function normalizeSentimentSearchResponse(payload, fallback = {}) {
  const rows = payload?.data ?? payload?.data_list ?? []
  const results = Array.isArray(rows) ? rows.map(normalizeSearchResult) : []
  const total = Number(payload?.total || results.length || 0)
  const page = Number(payload?.page || fallback.page || 1)
  const pageSize = Number(payload?.page_size || fallback.pageSize || 10)
  return {
    results,
    total,
    page,
    pageSize,
    totalPages: Number(payload?.total_pages || Math.ceil(total / pageSize) || 0),
  }
}

export function normalizeSearchHistoryResponse(payload) {
  const rows = Array.isArray(payload?.data) ? payload.data : []
  return rows.map((row) => ({ query: row?.query, time: row?.time }))
}

export function normalizeFavoriteIds(payload) {
  if (!Array.isArray(payload?.news_ids)) return []
  return payload.news_ids.map(Number).filter(Number.isFinite)
}

export function normalizeMacroEventClustersResponse(payload) {
  return {
    subEvents: Array.isArray(payload?.sub_events) ? payload.sub_events : [],
    hasMore: Boolean(payload?.has_more),
    trust: payload?.trust || null,
    meta: payload?.meta && typeof payload.meta === 'object' ? payload.meta : {},
    ...semanticEnvelope(payload),
  }
}
