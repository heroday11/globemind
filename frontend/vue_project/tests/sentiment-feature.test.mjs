import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  SentimentApiError,
  buildSentimentAuthHeaders,
  buildSentimentUrl,
  createSentimentApi,
  createSentimentEndpoints,
} from '../src/features/sentiment/api.js'
import {
  OVERVIEW_CACHE_KEY,
  createSentimentSnapshotCache,
} from '../src/features/sentiment/cache.js'
import {
  buildDateNewsQuery,
  buildFeedbackDto,
  buildInsightNewsQuery,
  buildMacroEventClustersQuery,
  buildOverviewQuery,
  buildSentimentSearchDto,
  buildTrendQuery,
  normalizeDateNewsResponse,
  normalizeFavoriteIds,
  normalizeSentimentSearchResponse,
} from '../src/features/sentiment/dto.js'
import {
  addLocalSearchHistory,
  applyFavoriteResult,
  buildDateExplanation,
  formatIndexValue,
  formatOverviewEventTitle,
  mergeFavoriteIds,
  opinionTrustSnapshot,
  qualitySnapshot,
  resolveAutoEndDate,
  sanitizeOpinionPayload,
} from '../src/features/sentiment/presentation.js'
import { createLatestSentimentRequest } from '../src/features/sentiment/request.js'
import {
  buildSearchChartMarkPoints,
  buildSentimentChartOption,
  currentRangeDays,
  dataZoomRange,
  findAnomalyPoints,
  rangeIndexes,
  resolveChartPointDate,
  selectDatePoint,
  sparklinePoints,
  trimTrendData,
  yAxisRange,
} from '../src/features/sentiment/trend.js'

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async text() {
      return JSON.stringify(payload)
    },
  }
}

function abortError() {
  const error = new Error('aborted')
  error.name = 'AbortError'
  return error
}

function completeTrust({
  articleCount = 10,
  cutoffDate = '2026-08-09',
  evaluatedOn = '2026-08-09',
  sourceCount = 3,
} = {}) {
  const ageDays = Math.round(
    (Date.parse(`${evaluatedOn}T00:00:00Z`) - Date.parse(`${cutoffDate}T00:00:00Z`))
      / 86_400_000,
  )
  const computable = ageDays >= 0 && ageDays <= 2 && articleCount >= 10 && sourceCount >= 3
  const reasons = []
  if (ageDays > 2) reasons.push('STALE_DATA')
  if (articleCount < 10) reasons.push('LOW_ARTICLE_COVERAGE')
  if (sourceCount < 3) reasons.push('LOW_SOURCE_COVERAGE')
  return {
    schema_version: 'opinion-trust-v1',
    status: computable ? 'ready' : 'unavailable',
    trust_status: computable ? 'trusted' : 'unavailable',
    is_computable: computable,
    computability: computable ? 'computable' : 'not_computable',
    display_mode: computable ? 'current' : 'historical_context',
    reason_codes: reasons,
    cutoff_date: cutoffDate,
    freshness: {
      state: ageDays > 2 ? 'stale' : 'fresh',
      age_days: ageDays,
      maximum_age_days: 2,
    },
    coverage: {
      state: articleCount >= 10 && sourceCount >= 3 ? 'sufficient' : 'insufficient',
      article_count: articleCount,
      source_count: sourceCount,
      minimum_articles: 10,
      minimum_sources: 3,
    },
    model_version: 'china-stance-model-full-version',
    method_version: 'china-stance-method-full-version',
    source_status: 'available',
    snapshot_id: 'opinion-snapshot-full-id',
    model: { version: 'china-stance-model-full-version' },
    method: { version: 'china-stance-method-full-version' },
    source: {
      id: 'public.china_opinion_article_scores',
      status: 'available',
      cutoff_date: cutoffDate,
    },
    snapshot: {
      id: 'opinion-snapshot-full-id',
      evaluated_on: evaluatedOn,
      cutoff_date: cutoffDate,
    },
  }
}

test('API facade owns every sentiment endpoint, authentication header, and wire value', async () => {
  const calls = []
  const api = createSentimentApi({
    apiPrefix: '/api',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return jsonResponse({ call: calls.length, data: [] })
    },
    getAccessToken: () => 'token-1',
  })

  await api.getOverview(buildOverviewQuery(30, { now: 10, refresh: true }))
  await api.getTrend(buildTrendQuery(90, 'negative', { now: 11 }))
  await api.getTopNews(buildInsightNewsQuery({ eventFamily: 'economic_trade' }))
  await api.getDimensions()
  await api.getQuality()
  await api.getNewsByDate(buildDateNewsQuery('2026-07-10', 'positive'))
  await api.submitFeedback(buildFeedbackDto({ id: 7, impact_index: -12, sentiment: -0.3 }, 'too_negative'))
  await api.getMacroEventClusters(buildMacroEventClustersQuery('macro/中文', '2026-07-10', 2))
  await api.search(buildSentimentSearchDto({
    keyword: '台海 风险',
    page: 2,
    pageSize: 20,
    publishTime: '近三月',
  }))
  await api.listSearchHistory()
  await api.recordSearchHistory('台海 风险')
  await api.listFavorites()
  await api.toggleFavorite(42)

  const endpoints = createSentimentEndpoints('/api')
  assert.equal(endpoints.overview, '/api/opinion/overview')
  assert.equal(endpoints.search, '/api/dashboard/search')
  assert.equal(endpoints.favoriteToggle, '/api/user/favorites/toggle')
  assert.match(calls[0].url, /^\/api\/opinion\/overview\?days=30&refresh=true&_=10$/)
  assert.match(calls[1].url, /sentiment_filter=negative/)
  assert.match(calls[2].url, /event_family=economic_trade/)
  assert.match(calls[5].url, /date_str=2026-07-10/)
  assert.deepEqual(JSON.parse(calls[6].options.body), {
    news_id: 7,
    correction: 'too_negative',
    purpose: 'quality_correction',
    training_consent: false,
    training_opt_out: true,
  })
  assert.equal(calls[6].options.headers.Authorization, 'Bearer token-1')
  assert.deepEqual(JSON.parse(calls[8].options.body), {
    keyword: '台海 风险',
    publish_time: '近三月',
    page: 2,
    page_size: 20,
    hit_location: '全文',
  })
  assert.equal(calls[8].options.headers.Authorization, 'Bearer token-1')
  assert.equal(calls[9].options.headers.Authorization, 'Bearer token-1')
  for (const index of [0, 1, 2, 3, 4, 5, 7]) {
    assert.equal(calls[index].options.headers.Authorization, undefined)
  }
  for (const index of [6, 8, 9, 10, 11, 12]) {
    assert.equal(calls[index].options.headers.Authorization, 'Bearer token-1')
  }
  assert.deepEqual(buildSentimentAuthHeaders(''), {})
  assert.equal(api.isAuthenticated(), true)
})

test('feedback and trend helpers preserve unavailable values instead of fabricating zero', () => {
  assert.deepEqual(buildFeedbackDto({ id: 8, impact_index: null, sentiment: undefined }, 'irrelevant'), {
    news_id: 8,
    correction: 'irrelevant',
    purpose: 'quality_correction',
    training_consent: false,
    training_opt_out: true,
  })

  assert.deepEqual(trimTrendData({
    dates: ['d1', 'd2', 'd3', 'd4'],
    values: [null, 0, 5, null],
  }), {
    dates: ['d3'],
    values: [5],
  })
  assert.deepEqual(findAnomalyPoints({
    dates: ['d1', 'd2', 'd3', 'd4'],
    values: [10, null, -10, -10],
  }), [])
  assert.deepEqual(selectDatePoint('d2', {
    dates: ['d1', 'd2'],
    values: [5, null],
  }), {
    date: 'd2',
    value: null,
    delta: null,
    anomaly: false,
  })
  assert.deepEqual(buildSearchChartMarkPoints(
    [{ pub_time: '2026-08-09T01:00:00Z' }],
    { dates: ['2026-08-09'], values: [null] },
  ), [])
  assert.deepEqual(yAxisRange([null, 5, 10], 0, 100), { min: -3, max: 18 })
  assert.equal(sparklinePoints([1, null, 3]), '')
})

test('API facade maps backend details, network failures, and aborts deterministically', async () => {
  const rejected = createSentimentApi({
    fetchImpl: async () => jsonResponse({ detail: '检索条件无效' }, { ok: false, status: 422 }),
  })
  await assert.rejects(
    () => rejected.search({ keyword: 'x' }),
    (error) => error instanceof SentimentApiError
      && error.message === '检索条件无效'
      && error.operation === 'search'
      && error.status === 422,
  )

  const offline = createSentimentApi({
    fetchImpl: async () => {
      throw new Error('socket closed')
    },
  })
  await assert.rejects(
    () => offline.getQuality(),
    (error) => error instanceof SentimentApiError
      && error.message === '质量数据加载失败，请检查网络连接',
  )

  const controller = new AbortController()
  const aborted = createSentimentApi({
    fetchImpl: async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(abortError()), { once: true })
    }),
  })
  const pending = aborted.getQuality({ signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, (error) => error.name === 'AbortError')
})

test('take-latest aborts the old request and never exposes stale results or errors', async () => {
  const request = createLatestSentimentRequest()
  const first = request.run((signal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(abortError()), { once: true })
  }))
  const second = request.run(async () => 'latest')

  assert.deepEqual(await second, { latest: true, status: 'success', value: 'latest' })
  const stale = await first
  assert.equal(stale.status, 'stale')
  assert.equal(stale.latest, false)
  assert.equal(request.isActive(), false)

  const failure = await request.run(async () => {
    throw new Error('current failure')
  })
  assert.equal(failure.status, 'error')
  assert.equal(failure.latest, true)
  assert.equal(failure.error.message, 'current failure')

  const cancelled = request.run((signal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(abortError()), { once: true })
  }))
  request.cancel()
  assert.equal((await cancelled).status, 'stale')
})

test('request DTOs preserve Chinese backend wire values and normalize response boundaries', () => {
  assert.deepEqual(buildOverviewQuery(999, { now: 123, refresh: true }), {
    days: 365,
    refresh: 'true',
    _: 123,
  })
  assert.deepEqual(buildTrendQuery(7, 'positive', { now: 124 }), {
    days: 7,
    china_min_score: '0.4',
    sentiment_filter: 'positive',
    refresh: 'false',
    _: 124,
  })
  assert.deepEqual(buildSentimentSearchDto({
    keyword: '  经贸合作  ',
    page: 0,
    pageSize: 50,
    publishTime: '不限',
  }), {
    keyword: '经贸合作',
    publish_time: undefined,
    page: 1,
    page_size: 50,
    hit_location: '全文',
  })

  assert.deepEqual(normalizeDateNewsResponse({ news: null, total: '3' }), {
    news: [],
    total: 3,
    summary: null,
    trust: null,
    meta: {},
  })
  assert.deepEqual(normalizeFavoriteIds({ news_ids: ['2', 'bad', 4] }), [2, 4])
  assert.deepEqual(
    normalizeSentimentSearchResponse({
      data_list: [{ id: '8', is_favorited: 1 }],
      total: 11,
      page: 2,
      page_size: 10,
    }, { page: 1, pageSize: 20 }),
    {
      results: [{ id: 8, is_favorited: true }],
      total: 11,
      page: 2,
      pageSize: 10,
      totalPages: 2,
    },
  )
})

test('trend, date range, KPI, and chart models are pure and boundary-safe', () => {
  const data = trimTrendData({
    dates: ['d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6'],
    values: [0, 10, 11, 12, 50, 51, 0],
  })
  assert.deepEqual(data, {
    dates: ['d1', 'd2', 'd3', 'd4', 'd5'],
    values: [10, 11, 12, 50, 51],
  })
  const anomalies = findAnomalyPoints(data)
  assert.equal(anomalies[0].date, 'd4')
  assert.equal(anomalies[0].delta, 38)

  const marks = buildSearchChartMarkPoints([
    { pub_time: 'd4' },
    { pub_time: '2026-07-10T09:00:00Z' },
  ], {
    dates: ['2026-07-09', '2026-07-10'],
    values: [3, -4],
  })
  assert.equal(marks.length, 1)
  assert.equal(marks[0].count, 1)
  assert.deepEqual(selectDatePoint('d4', data, anomalies), {
    date: 'd4',
    value: 50,
    delta: 38,
    anomaly: true,
  })
  assert.equal(currentRangeDays('custom', 999), 365)
  const indexes = rangeIndexes(['a', 'b', 'c', 'd'], {
    customDays: 2,
    endDate: 'c',
    timeRange: 'custom',
  })
  assert.deepEqual(indexes, { startIndex: 1, endIndex: 2, total: 3 })
  const zoom = dataZoomRange(indexes)
  assert.ok(Math.abs(zoom.start - (100 / 3)) < 1e-10)
  assert.ok(Math.abs(zoom.end - (200 / 3)) < 1e-10)
  assert.deepEqual(yAxisRange([10, 20, 30], 50, 100), { min: 12, max: 38 })
  assert.equal(sparklinePoints([5], 100, 20), '')
  assert.equal(resolveAutoEndDate('', '', '2026-08-08'), '2026-08-08')
  assert.equal(
    resolveAutoEndDate('2026-08-08', '2026-08-08', '2026-08-09'),
    '2026-08-09',
  )
  assert.equal(
    resolveAutoEndDate('2026-08-01', '2026-08-08', '2026-08-09'),
    '2026-08-01',
  )
  assert.equal(resolveAutoEndDate('', '', ''), '')

  const option = buildSentimentChartOption({
    anomalyPoints: anomalies,
    data,
    searchMarkPoints: marks,
    start: 20,
    end: 80,
    yRange: { min: -20, max: 60 },
  })
  assert.equal(option.dataZoom[0].start, 20)
  assert.equal(option.dataZoom[0].end, 80)
  assert.equal(option.yAxis.min, -20)
  assert.equal(option.series[0].data, data.values)
  assert.equal(option.series[0].markPoint.data.length, 2 + anomalies.length + marks.length)
  assert.equal(resolveChartPointDate({ componentType: 'markPoint', data: { coord: [3, 50] } }, data), 'd4')
})

test('presentation models keep quality, event, history, and favorite behavior stable', () => {
  assert.equal(
    formatOverviewEventTitle('China↔US: export controls'),
    '中国 ↔ 美国：export controls',
  )
  assert.deepEqual(qualitySnapshot({
    coverage_by_date: [{ date: '2026-07-10', news_count: 8, scored_relevant: 6 }],
    freshness: { latest_score_date: '2026-07-10' },
    method_version: 'stance-v3',
    pending_feedback_30d: 2,
  }), {
    latestScoreDate: '2026-07-10',
    latestCoverage: '6/8 · 75%',
    coverageDate: '2026-07-10',
    pendingFeedback: '2',
    methodVersion: 'stance-v3',
  })

  const explanation = buildDateExplanation([
    { impact_index: 8, event_family: 'diplomacy', source: 'A', title: 'one' },
    { impact_index: -12, event_family: 'diplomacy', source: 'B', title: 'two' },
    { impact_index: -3, event_family: 'economic_trade', source: 'A', title: 'three' },
  ], {})
  assert.equal(explanation.supportiveCount, 0)
  assert.equal(explanation.criticalCount, 0)
  assert.equal(explanation.unknownCount, 3)
  assert.equal(explanation.topFamily, '外交')
  assert.equal(explanation.sourceCount, 2)
  assert.deepEqual(explanation.leadingNews, [])

  const unavailableExplanation = buildDateExplanation([
    { impact_index: null, event_family: 'diplomacy', source: 'A', title: 'unknown' },
    { impact_index: 4, event_family: 'diplomacy', source: 'B', title: 'measured' },
  ], {})
  assert.equal(unavailableExplanation.supportiveCount, 0)
  assert.equal(unavailableExplanation.unknownCount, 2)
  assert.deepEqual(unavailableExplanation.leadingNews, [])

  const history = addLocalSearchHistory(
    [{ query: '旧词', time: 'old' }, { query: '经贸', time: 'older' }],
    ' 经贸 ',
    new Date(2026, 6, 10, 9, 5),
  )
  assert.deepEqual(history, [
    { query: '经贸', time: '2026-07-10 09:05' },
    { query: '旧词', time: 'old' },
  ])
  assert.deepEqual(mergeFavoriteIds([1], [{ id: '2', is_favorited: true }]), [1, 2])
  assert.deepEqual(applyFavoriteResult([1, 2], 2, false), [1])
  assert.deepEqual(applyFavoriteResult([1], 2, true), [1, 2])
})

test('opinion presentation fails closed when trust metadata rejects the composite', () => {
  const snapshot = opinionTrustSnapshot({
    trust: {
      is_computable: false,
      cutoff_date: '2026-07-21',
      reason_codes: ['STALE_DATA', 'LOW_SOURCE_COVERAGE'],
      freshness: { age_days: 19 },
      coverage: {
        article_count: 24,
        source_count: 1,
        minimum_articles: 10,
        minimum_sources: 3,
      },
      method_version: 'stance-v6',
    },
  })
  assert.equal(snapshot.computable, false)
  assert.equal(snapshot.headline, '目标立场指数不可计算')
  assert.equal(snapshot.cutoffDate, '2026-07-21')
  assert.match(snapshot.detail, /超过时效门槛/)
  assert.match(snapshot.detail, /信源覆盖不足/)
  assert.equal(formatIndexValue(null), '--')
  assert.equal(formatIndexValue(undefined), '--')
  assert.equal(formatIndexValue(0), '0.0')
})

test('snapshot cache rejects expired and corrupt entries without blocking the page', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  let now = Date.UTC(2026, 7, 9)
  const cache = createSentimentSnapshotCache({ now: () => now, storage, ttlMs: 50 })
  const trust = completeTrust()
  assert.equal(cache.write(OVERVIEW_CACHE_KEY, {
    latest_date: '2026-08-09',
    summary: { current_index: 15.2 },
    trust,
    meta: { trust },
  }), true)
  const cached = cache.read(OVERVIEW_CACHE_KEY)
  assert.equal(cached.latest_date, '2026-08-09')
  assert.equal(cached.summary.current_index, 15.2)
  assert.equal(cached.trust.is_computable, true)
  now += 51
  assert.equal(cache.read(OVERVIEW_CACHE_KEY), null)
  values.set(OVERVIEW_CACHE_KEY, '{broken')
  assert.equal(cache.read(OVERVIEW_CACHE_KEY), null)
})

test('snapshot cache re-ages trust on every read and removes stale exact composites', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }
  let now = Date.UTC(2026, 7, 9)
  const cache = createSentimentSnapshotCache({
    now: () => now,
    storage,
    ttlMs: 10 * 86_400_000,
  })
  const trust = completeTrust({ cutoffDate: '2026-08-08', evaluatedOn: '2026-08-09' })
  cache.write(OVERVIEW_CACHE_KEY, {
    latest_date: '2026-08-08',
    summary: { current_index: -27.4, positive_pct: 20 },
    families: [{ avg_stance: -0.4 }],
    trust,
    meta: { trust },
  })
  assert.equal(cache.read(OVERVIEW_CACHE_KEY).summary.current_index, -27.4)

  now = Date.UTC(2026, 7, 12)
  const stale = cache.read(OVERVIEW_CACHE_KEY)
  assert.equal(stale.trust.is_computable, false)
  assert.equal(stale.summary.current_index, null)
  assert.equal(stale.summary.positive_pct, null)
  assert.equal(stale.families[0].avg_stance, null)
  assert.ok(stale.trust.reason_codes.includes('STALE_DATA'))
})

test('unified frontend sanitizer rejects missing, damaged, and contradictory trust metadata', () => {
  const missing = sanitizeOpinionPayload({
    dates: ['2026-08-09'],
    values: [33],
    summary: { current_index: 33, positive_pct: 70 },
    top_event: { avg_stance: 0.5 },
    dimensions: { sources: [{ impact_index: 30 }] },
    news: [{ impact_index: 20, confidence: 0.86, china_index: 0.92 }],
  }, { now: Date.UTC(2026, 7, 9) })
  assert.equal(missing.values.length, 0)
  assert.equal(missing.summary.current_index, null)
  assert.equal(missing.summary.positive_pct, null)
  assert.equal(missing.top_event.avg_stance, null)
  assert.equal(missing.dimensions.sources[0].impact_index, null)
  assert.equal(missing.news[0].confidence, null)
  assert.ok(missing.trust.reason_codes.includes('MISSING_TRUST_METADATA'))

  const trust = completeTrust()
  const contradictory = { ...trust, status: 'unavailable' }
  const blocked = sanitizeOpinionPayload({
    values: [12],
    trust: contradictory,
    meta: { trust: contradictory },
  }, { now: Date.UTC(2026, 7, 9) })
  assert.equal(blocked.values.length, 0)
  assert.ok(blocked.trust.reason_codes.includes('CONFLICTING_STATUS_METADATA'))

  const weakened = JSON.parse(JSON.stringify(trust))
  weakened.coverage.minimum_articles = 1
  weakened.coverage.minimum_sources = 1
  const thresholdBlocked = sanitizeOpinionPayload({
    values: [12],
    trust: weakened,
    meta: { trust: weakened },
  }, { now: Date.UTC(2026, 7, 9) })
  assert.equal(thresholdBlocked.values.length, 0)
  assert.ok(thresholdBlocked.trust.reason_codes.includes('CONFLICTING_COVERAGE_METADATA'))
})

test('quality and trust presentation keep complete version and snapshot identifiers', () => {
  const fullVersion = 'china_stance_v6_title_context_actor_eval_20260629_build_abcdef'
  assert.equal(qualitySnapshot({ method_version: fullVersion }).methodVersion, fullVersion)
  const trust = completeTrust()
  trust.method_version = fullVersion
  trust.method.version = fullVersion
  const snapshot = opinionTrustSnapshot(
    { trust, meta: { trust } },
    { now: Date.UTC(2026, 7, 9) },
  )
  assert.equal(snapshot.methodVersion, fullVersion)
  assert.equal(snapshot.modelVersion, 'china-stance-model-full-version')
  assert.equal(snapshot.schemaVersion, 'opinion-trust-v1')
  assert.equal(snapshot.snapshotId, 'opinion-snapshot-full-id')
  assert.equal(snapshot.sourceStatus, 'available')
})

test('sentiment page consumes the feature through its public entry and has no direct transport', async () => {
  const page = await readFile(
    new URL('../src/views/sentimentAnalysis.vue', import.meta.url),
    'utf8',
  )
  const styles = await readFile(
    new URL('../src/views/sentimentAnalysis.css', import.meta.url),
    'utf8',
  )
  const entry = await readFile(
    new URL('../src/features/sentiment/index.js', import.meta.url),
    'utf8',
  )
  const component = await readFile(
    new URL('../src/features/sentiment/SentimentFilterToggle.vue', import.meta.url),
    'utf8',
  )

  assert.match(page, /from ['"]@\/features\/sentiment\/index\.js['"]/)
  assert.doesNotMatch(page, /@\/features\/sentiment\/(?!index\.js)/)
  assert.doesNotMatch(page, /\baxios\b|\bfetch\s*\(|@\/config\/api|\bAPI_PREFIX\b/)
  assert.match(entry, /SentimentFilterToggle/)
  assert.match(component, /variant === 'drawer'/)
  assert.match(component, /SENTIMENT_OPTIONS/)
  assert.match(page, /invalidateOpinionPayload\(overviewData\.value, 'REFRESH_FAILED'\)/)
  assert.match(page, /snapshotCache\.remove\(TREND_CACHE_KEY\)/)
  assert.match(page, /sanitizeOpinionPayload\(outcome\.value\)/)
  assert.doesNotMatch(page, /class="bg-orbs"/)
  assert.match(styles, /\.search-container\s*{[^}]*min-width:\s*0;[^}]*max-width:\s*560px;/s)
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*?\.nav-right\s*{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/)
})

test('URL builder omits absent filters and safely encodes Chinese values', () => {
  assert.equal(
    buildSentimentUrl('/api/opinion/top-news', {
      event_family: undefined,
      sentiment_filter: '正面 舆情',
    }),
    '/api/opinion/top-news?sentiment_filter=%E6%AD%A3%E9%9D%A2+%E8%88%86%E6%83%85',
  )
})
