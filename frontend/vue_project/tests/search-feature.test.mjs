import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'

import {
  SEARCH_API_ENDPOINTS,
  SearchApiError,
  buildNewsListUrl,
  buildSearchAuthHeaders,
  createSearchApi,
  createSearchEndpoints,
} from '../src/features/search/api.js'
import {
  SEARCH_HIT_LOCATIONS,
  SEARCH_MODES,
  SEARCH_SITUATION_PRESETS,
  SEARCH_TIME_RANGES,
  SEARCH_TYPES,
  activeSearchSurfaceLabel,
  applySearchSituationPreset,
  buildSearchHistoryKeyword,
  buildSearchRequestDto,
  buildVisiblePages,
  calculateTotalPages,
  createSearchParams,
  mergeFavoriteNewsIds,
  normalizeSearchParamsForType,
  normalizeDisplayNewsRows,
  normalizeNewsListResponse,
  normalizeNewsTimeSemantics,
  normalizeQueryExplain,
  normalizeQueryReceipt,
  normalizeSearchResponse,
  resolveLanguageDisplay,
  resolveJumpPage,
  searchSortSemantics,
} from '../src/features/search/model.js'
import {
  SEARCH_DATA_OPERATION_KINDS,
  SEARCH_REQUEST_TIMEOUT_MS,
  SearchRequestTimeoutError,
  createLatestSearchRequest,
  createSearchListOperation,
  createSearchOperationRunner,
  createSearchQueryOperation,
} from '../src/features/search/request.js'
import {
  DEFAULT_SEARCH_FOLDER_NAME,
  LEGACY_DEFAULT_THEME_NAME,
  SEARCH_THEME_LIST_KEY,
  SEARCH_THEME_NAME_KEY,
  createSearchThemeName,
  createSearchThemeStorage,
  displaySearchFolderName,
  normalizeSearchFolderName,
  normalizeSearchThemeList,
  renameSearchThemeList,
} from '../src/features/search/storage.js'

function jsonResponse(payload, status = 200, statusText = '') {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    async json() {
      return payload
    },
  }
}

test('search query DTO preserves defaults, trimming, omission, and Chinese wire values', () => {
  const defaults = createSearchParams()
  assert.deepEqual(defaults, {
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
  const input = createSearchParams({
    topic: '  China risk  ',
    mustInclude: '  policy ',
    anyInclude: ' trade  ',
    needExclude: '   ',
    publishTime: '近一月',
    startTime: '2026-07-01T00:00',
    hitLocation: '标题',
    language: '中文',
    page: 3,
    pageSize: 20,
    sortBy: 'pub_time',
    mode: 'fuzzy',
    searchType: 'l2',
  })
  const before = structuredClone(input)
  assert.deepEqual(buildSearchRequestDto(input, '政策观察'), {
    topic: 'China risk',
    keyword: 'China risk',
    must_include: 'policy',
    any_include: 'trade',
    need_exclude: '   ',
    publish_time: '近一月',
    start_time: '2026-07-01T00:00',
    time_field: 'event_time',
    hit_location: '全文',
    page: 3,
    page_size: 20,
    sort_order: 'desc',
    mode: 'fuzzy',
    search_type: 'l2',
    favorite_scope_topic: '政策观察',
  })
  assert.deepEqual(input, before)
  assert.deepEqual(buildSearchRequestDto(defaults, ''), {
    time_field: 'published_at',
    hit_location: '全文',
    page: 1,
    page_size: 10,
    sort_order: 'desc',
    mode: 'exact',
    search_type: 'news',
  })
  assert.equal(
    buildSearchRequestDto(createSearchParams({ sortBy: 'pub_time' }), '').sort_by,
    'published_at',
  )
  assert.equal(
    Object.hasOwn(buildSearchRequestDto(createSearchParams({ sortBy: 'time' }), ''), 'sort_by'),
    false,
  )
  assert.equal(buildSearchHistoryKeyword(input), 'China risk')
  assert.equal(
    buildSearchHistoryKeyword(createSearchParams({ publishTime: '近一周', searchType: 'l1' })),
    '事件时间:近一周 类型:l1',
  )
  assert.equal(buildSearchHistoryKeyword(defaults), '条件检索')
})

test('hierarchy type normalization removes unsupported news-only filters without mutating input', () => {
  const input = createSearchParams({
    topic: 'South China Sea',
    hitLocation: '正文',
    dataSource: 'example.test',
    language: 'en',
    site: 'news',
    sortBy: 'pub_time',
    sortOrder: 'asc',
    searchType: 'news',
  })
  const before = structuredClone(input)
  const hierarchy = normalizeSearchParamsForType(input, 'l2')

  assert.deepEqual(input, before)
  assert.deepEqual({
    searchType: hierarchy.searchType,
    hitLocation: hierarchy.hitLocation,
    dataSource: hierarchy.dataSource,
    language: hierarchy.language,
    site: hierarchy.site,
    sortBy: hierarchy.sortBy,
    sortOrder: hierarchy.sortOrder,
  }, {
    searchType: 'l2',
    hitLocation: '全文',
    dataSource: '',
    language: '',
    site: '',
    sortBy: '',
    sortOrder: 'desc',
  })
  assert.deepEqual(buildSearchRequestDto(hierarchy, ''), {
    topic: 'South China Sea',
    keyword: 'South China Sea',
    time_field: 'event_time',
    hit_location: '全文',
    page: 1,
    page_size: 10,
    sort_order: 'desc',
    mode: 'exact',
    search_type: 'l2',
  })
  assert.equal(searchSortSemantics(hierarchy), '系统排序（匹配覆盖、层级质量、事件规模与事件时间）')
  assert.equal(
    searchSortSemantics(createSearchParams({ sortBy: 'pub_time' })),
    '新闻发布日期倒序',
  )
  assert.equal(
    searchSortSemantics(createSearchParams()),
    '标题命中优先，其次按新闻发布日期倒序',
  )
})

test('language display never re-labels a geographic location as a language', () => {
  const options = [
    { id: 'en', name: '英语' },
    { id: 'zh', name: '中文' },
  ]

  assert.equal(resolveLanguageDisplay({ language_id: 'en', location: '美国' }, options), '英语')
  assert.equal(resolveLanguageDisplay({ language_id: 'es', location: '西班牙' }, options), 'es')
  assert.equal(resolveLanguageDisplay({ location: '法国' }, options), '')
})

test('news detail presents language and unverified location as separate fields', async () => {
  const source = await readFile(
    new URL('../src/views/DataService/news-detail.vue', import.meta.url),
    'utf8',
  )

  assert.match(source, /语言代码：\{\{ news\.language_id \}\}/)
  assert.match(source, /位置（记录值，未核验）：\{\{ news\.location \}\}/)
  assert.match(source, /新闻发布日期：\{\{ formatTime\(newsTimeSemantics\.publishedAt\) \}\}/)
  assert.match(source, /事件时间：\{\{ eventTimeDisplay \}\}/)
  assert.match(source, /采集时间：\{\{ formatTime\(newsTimeSemantics\.collectedAt\) \}\}/)
  assert.match(source, /更新时间：\{\{ formatTime\(newsTimeSemantics\.updatedAt\) \}\}/)
  assert.match(source, /；事件时间 \$\{range\}/)
  assert.doesNotMatch(source, /；时间 \$\{range\}/)
  assert.doesNotMatch(source, /news\.pub_time/)
  assert.doesNotMatch(source, /语言\/地区：\{\{ news\.location \}\}/)
  assert.doesNotMatch(source, /label: '语言\/地区', value: news\.value\.location/)
})

test('filter constants and surface labels remain stable public contracts', () => {
  assert.deepEqual(SEARCH_TIME_RANGES.map((item) => item.value), [
    '不限',
    '近一天',
    '近一周',
    '近一月',
    '近三月',
    '近一年',
  ])
  assert.deepEqual(SEARCH_HIT_LOCATIONS.map((item) => item.value), ['全文', '标题', '摘要', '正文'])
  assert.deepEqual(SEARCH_HIT_LOCATIONS.map((item) => item.label), ['标题优先', '标题', '摘要', '正文'])
  assert.deepEqual(SEARCH_MODES.map((item) => item.value), ['exact', 'fuzzy'])
  assert.deepEqual(SEARCH_MODES.map((item) => item.label), ['全部词', '主题扩展'])
  assert.deepEqual(SEARCH_TYPES.map((item) => item.value), ['news', 'l1', 'l2', 'l3'])
  assert.equal(SEARCH_SITUATION_PRESETS.length, 6)
  assert.equal(SEARCH_SITUATION_PRESETS[0].label, '台海风险')
  assert.equal(SEARCH_SITUATION_PRESETS[0].type, 'news')
  assert.equal(SEARCH_SITUATION_PRESETS[0].topic, 'Taiwan')
  assert.equal(SEARCH_SITUATION_PRESETS[0].mode, 'fuzzy')
  assert.equal(SEARCH_SITUATION_PRESETS[1].topic, 'China USA')
  assert.equal(SEARCH_SITUATION_PRESETS[4].label, '加沙冲突')
  assert.deepEqual(
    SEARCH_SITUATION_PRESETS.slice(3).map((preset) => [preset.topic, preset.mode]),
    [['Ukraine', 'fuzzy'], ['Gaza', 'fuzzy'], ['control', 'fuzzy']],
  )
  assert.equal(SEARCH_SITUATION_PRESETS[5].type, 'news')
  assert.deepEqual(
    SEARCH_SITUATION_PRESETS.map((preset) => [preset.time, preset.timeField, preset.timeLabel]),
    [
      ['不限', 'published_at', '新闻发布日期：不限'],
      ['不限', 'event_time', '事件时间：不限'],
      ['不限', 'event_time', '事件时间：不限'],
      ['不限', 'event_time', '事件时间：不限'],
      ['不限', 'event_time', '事件时间：不限'],
      ['不限', 'published_at', '新闻发布日期：不限'],
    ],
  )
  assert.equal(
    activeSearchSurfaceLabel(createSearchParams({ publishTime: '近三月', mode: 'fuzzy', searchType: 'l3' })),
    'L3大事件 · 近三月 · 主题扩展',
  )
})

test('every visible situation preset mutates one editable state and clears cross-type filters', () => {
  for (const preset of SEARCH_SITUATION_PRESETS) {
    const searchParams = createSearchParams({
      topic: 'stale topic',
      mustInclude: 'stale required',
      anyInclude: 'stale optional',
      needExclude: 'stale exclusion',
      publishTime: '近三月',
      startTime: '2026-01-01T00:00',
      endTime: '2026-02-01T00:00',
      hitLocation: '正文',
      dataSource: 'stale.example',
      language: 'en',
      site: 'stale-site',
      page: 9,
      pageSize: 99,
      sortBy: 'similarity',
      sortOrder: 'asc',
      mode: 'exact',
      searchType: preset.type === 'news' ? 'l3' : 'news',
    })

    const returned = applySearchSituationPreset(searchParams, preset, 37)

    assert.equal(returned, searchParams, `${preset.label} must retain searchParams identity`)
    assert.equal(Object.isFrozen(searchParams), false)
    assert.deepEqual({
      topic: searchParams.topic,
      mustInclude: searchParams.mustInclude,
      anyInclude: searchParams.anyInclude,
      needExclude: searchParams.needExclude,
      publishTime: searchParams.publishTime,
      startTime: searchParams.startTime,
      endTime: searchParams.endTime,
      hitLocation: searchParams.hitLocation,
      dataSource: searchParams.dataSource,
      language: searchParams.language,
      site: searchParams.site,
      page: searchParams.page,
      pageSize: searchParams.pageSize,
      sortBy: searchParams.sortBy,
      sortOrder: searchParams.sortOrder,
      mode: searchParams.mode,
      searchType: searchParams.searchType,
    }, {
      topic: preset.topic,
      mustInclude: preset.mustInclude || '',
      anyInclude: preset.anyInclude || '',
      needExclude: '',
      publishTime: preset.time,
      startTime: '',
      endTime: '',
      hitLocation: '全文',
      dataSource: '',
      language: '',
      site: '',
      page: 1,
      pageSize: 37,
      sortBy: preset.type === 'news' ? 'published_at' : '',
      sortOrder: 'desc',
      mode: preset.mode || 'exact',
      searchType: preset.type,
    })

    const dto = buildSearchRequestDto(searchParams, '')
    assert.equal(dto.topic, preset.topic)
    assert.equal(dto.keyword, preset.topic)
    assert.equal(dto.search_type, preset.type)
    assert.equal(dto.time_field, preset.timeField)
    assert.equal(dto.page_size, 37)
    assert.equal(Object.hasOwn(dto, 'need_exclude'), false)
    assert.equal(Object.hasOwn(dto, 'publish_time'), false)
    assert.equal(Object.hasOwn(dto, 'start_time'), false)
    assert.equal(Object.hasOwn(dto, 'end_time'), false)
    assert.equal(Object.hasOwn(dto, 'data_source'), false)
    assert.equal(Object.hasOwn(dto, 'language'), false)
    assert.equal(Object.hasOwn(dto, 'site'), false)
    assert.equal(dto.sort_by, preset.type === 'news' ? 'published_at' : undefined)

    searchParams.topic = `${preset.topic} edited`
    assert.equal(searchParams.topic, `${preset.topic} edited`)
  }
})

test('news and hierarchy responses normalize against the request search-type snapshot', () => {
  assert.deepEqual(normalizeNewsListResponse({
    data: [{ id: '7', is_favorited: 1, is_warned: 0 }],
    total: 9,
  }), {
    rows: [{
      id: 7,
      is_favorited: true,
      is_warned: false,
      selected: false,
      timeSemantics: {
        schemaVersion: 'unavailable',
        publishedAt: null,
        eventTimeStart: null,
        eventTimeEnd: null,
        collectedAt: null,
        updatedAt: null,
        legacyPubTimeStatus: 'legacy_unverified_not_used',
        legacyCreatedAtStatus: 'legacy_unverified_not_used',
      },
    }],
    total: 9,
  })

  const news = normalizeSearchResponse(
    {
      data: [{ id: '8', is_favorited: 1 }],
      cluster_tree: [{ story_id: 's1' }],
      event_coref_clusters: [{ cluster_id: 'c1' }],
      total: 1,
      page: 2,
      page_size: 25,
      query_receipt: {
        schema_version: 'search-query-receipt-v1',
        receipt_id: 'qr-test',
        ordered_returned_ids: [8],
        result_coverage: { status: 'available', returned_result_count: 1 },
      },
    },
    'news',
    { page: 1, pageSize: 10 },
  )
  assert.equal(news.searchResults[0].id, 8)
  assert.equal(news.searchResults[0].is_favorited, true)
  assert.deepEqual(news.clusterTree, [{ story_id: 's1' }])
  assert.deepEqual(news.eventCorefClusters, [{ cluster_id: 'c1' }])
  assert.equal(news.page, 2)
  assert.equal(news.pageSize, 25)
  assert.equal(news.queryReceipt.receipt_id, 'qr-test')
  assert.deepEqual(news.queryReceipt.ordered_returned_ids, ['8'])

  const l1 = normalizeSearchResponse(
    { event_coref_clusters: [{ cluster_id: 'c2' }] },
    'l1',
    { page: 4, pageSize: 10 },
  )
  assert.deepEqual(l1.eventCorefClusters, [{ cluster_id: 'c2' }])
  assert.deepEqual(l1.searchResults, [])
  assert.equal(l1.page, 4)

  const l3 = normalizeSearchResponse(
    { macro_event_items: [{ id: 12 }, { id: '13', level: 'l2' }] },
    'l3',
    { page: 1, pageSize: 10 },
  )
  assert.deepEqual(l3.macroEventItems, [
    { id: '12', level: 'l3' },
    { id: '13', level: 'l2' },
  ])

  const displayRows = normalizeDisplayNewsRows(
    [{ id: 0, abstract: 'Summary', is_first_release: true }],
    true,
    () => 'fallback-id',
  )
  assert.deepEqual(displayRows, [{
    id: 'fallback-id',
    abstract: 'Summary',
    is_first_release: true,
    title: '无标题',
    displayDesc: 'Summary',
    displayTime: undefined,
    displayUrl: '',
    isFirstRelease: true,
    is_favorited: false,
    is_warned: false,
    selected: false,
    timeSemantics: {
      schemaVersion: 'unavailable',
      publishedAt: null,
      eventTimeStart: null,
      eventTimeEnd: null,
      collectedAt: null,
      updatedAt: null,
      legacyPubTimeStatus: 'legacy_unverified_not_used',
      legacyCreatedAtStatus: 'legacy_unverified_not_used',
    },
    searchHit: {
      schemaVersion: 'search-hit-display-v1',
      status: 'unavailable',
      offsetEncoding: 'unicode_code_points',
      coverage: 'positive_literal_terms_in_returned_display_only',
      effectiveSearchFields: [],
      aliasSpanState: 'not_available',
      relevanceScoreState: 'not_available',
      documentMatchState: 'not_asserted',
      reasonCode: 'SEARCH_HIT_CONTRACT_INVALID',
      spans: [],
    },
  }])
  assert.deepEqual(mergeFavoriteNewsIds([1], [
    { id: '2', is_favorited: true },
    { id: 1, is_favorited: true },
    { id: 3, is_favorited: false },
  ]), [1, 2])
})

test('news time normalization requires the explicit four-semantic contract', () => {
  const legacyOnly = normalizeNewsTimeSemantics({
    pub_time: '2026-08-09T12:30:00Z',
    published_at: '2026-08-09T12:31:00Z',
    time: '2026-08-09T12:32:00Z',
    created_at: '2026-08-09T12:33:00Z',
  })
  assert.deepEqual(legacyOnly, {
    schemaVersion: 'unavailable',
    publishedAt: null,
    eventTimeStart: null,
    eventTimeEnd: null,
    collectedAt: null,
    updatedAt: null,
    legacyPubTimeStatus: 'legacy_unverified_not_used',
    legacyCreatedAtStatus: 'legacy_unverified_not_used',
  })

  const explicit = normalizeNewsTimeSemantics({
    time_semantics: {
      schema_version: 'search-result-time-semantics-v1',
      published_at: '2026-08-09T12:30:00Z',
      event_time_start: null,
      event_time_end: null,
      collected_at: null,
      updated_at: null,
      legacy_pub_time_status: 'legacy_alias_of_published_at_value_unverified',
      legacy_created_at_status: 'legacy_unverified_not_used',
    },
    pub_time: '2026-08-09T12:30:00Z',
    time: '1998-01-01T00:00:00Z',
  })
  assert.deepEqual(explicit, {
    schemaVersion: 'search-result-time-semantics-v1',
    publishedAt: '2026-08-09T12:30:00Z',
    eventTimeStart: null,
    eventTimeEnd: null,
    collectedAt: null,
    updatedAt: null,
    legacyPubTimeStatus: 'legacy_alias_of_published_at_value_unverified',
    legacyCreatedAtStatus: 'legacy_unverified_not_used',
  })

  assert.equal(normalizeNewsTimeSemantics({
    time_semantics: {
      schema_version: 'search-result-time-semantics-v1',
      published_at: 'x'.repeat(65),
    },
  }).publishedAt, null)

  assert.equal(normalizeNewsTimeSemantics({
    time_semantics: {
      schema_version: 'search-result-time-semantics-v1',
      published_at: '2026-08-09T12:30:00Z',
      event_time_start: '2026-08-10T00:00:00Z',
      event_time_end: '2026-08-09T00:00:00Z',
      collected_at: null,
      updated_at: null,
      legacy_pub_time_status: 'legacy_alias_of_published_at_value_unverified',
      legacy_created_at_status: 'legacy_unverified_not_used',
    },
  }).schemaVersion, 'unavailable')

  assert.equal(normalizeNewsTimeSemantics({
    time_semantics: {
      schema_version: 'search-result-time-semantics-v1',
      published_at: '2026-08-09T12:30:00Z',
      event_time_start: null,
      event_time_end: null,
      collected_at: null,
      updated_at: null,
      legacy_pub_time_status: 'legacy_alias_of_published_at_value_unverified',
      legacy_created_at_status: 'legacy_unverified_not_used',
    },
    pub_time: '1999-01-01T00:00:00Z',
  }).schemaVersion, 'unavailable')
})

test('query explain normalization preserves truthful stages and stable entity identifiers', () => {
  const explain = normalizeQueryExplain({
    query_language: 'boolean-v1',
    query_ast: { type: 'and', children: [{ type: 'term', value: 'China' }] },
    expanded_query_ast: { type: 'term', value: 'China', expanded_values: ['China', '中国'] },
    execution_expression: 'ANY("China", "中国")',
    limits: { max_ast_nodes: 64, observed_ast_nodes: 2 },
    normalized_terms: ['China', 'semiconductor'],
    expanded_terms: ['China', '中国', 'semiconductor'],
    effective_search_fields: ['news.title'],
    entity_expansions: [{
      query_field: 'primary_query',
      entity_id: 'urn:globemind:entity:country:CN',
      expanded_aliases: ['China', '中国'],
      expanded_alias_details: [{ value: 'China', status: 'active' }],
      review_status: 'review_required',
    }],
    applied_filters: [],
    stages: [
      { stage: 'retrieval', status: 'executed', matched_count: 0 },
      { stage: 'relaxation', status: 'not_run', matched_count: null },
    ],
    relaxation_suggestions: ['手动改用主题扩展'],
    automatic_relaxation: false,
  })
  assert.equal(explain.entity_expansions[0].entity_id, 'urn:globemind:entity:country:CN')
  assert.equal(explain.entity_expansions[0].query_field, 'primary_query')
  assert.equal(explain.entity_expansions[0].review_status, 'review_required')
  assert.equal(explain.entity_expansions[0].expanded_alias_details[0].status, 'active')
  assert.equal(explain.query_language, 'boolean-v1')
  assert.equal(explain.query_ast.type, 'and')
  assert.equal(explain.limits.max_ast_nodes, 64)
  assert.equal(explain.stages[0].matched_count, 0)
  assert.equal(explain.stages[1].status, 'not_run')
  assert.equal(explain.automatic_relaxation, false)
  assert.equal(normalizeQueryExplain(null), null)

  const sparse = normalizeQueryExplain({})
  assert.deepEqual(sparse.normalized_terms, [])
  assert.deepEqual(sparse.stages, [])
  assert.equal(sparse.query_language, 'unavailable')
  assert.equal(sparse.query_ast.type, 'unavailable')
  assert.deepEqual(sparse.limits, {})
})

test('query receipt normalization keeps hashes, ordered IDs, coverage, and snapshot disclaimer state', () => {
  const receipt = normalizeQueryReceipt({
    schema_version: 'search-query-receipt-v1',
    receipt_kind: 'execution_receipt',
    method_version: 'dashboard-search-v2+boolean-v1+receipt-v1',
    receipt_id: 'qr-abc',
    receipt_sha256: 'a'.repeat(64),
    normalized_contract: { schema_version: 'normalized-search-contract-v1' },
    normalized_contract_sha256: 'b'.repeat(64),
    entity_catalog_version: 'entity-aliases-2026.08.09-v2',
    entity_catalog_review_status: 'review_required',
    time_field: { requested: 'published_at', applied: 'public.news.published_at' },
    applied_filters: [{ field: 'start_time' }],
    ordered_returned_ids: [9, '4'],
    ordered_returned_ids_sha256: 'c'.repeat(64),
    result_coverage: {
      status: 'partial',
      cutoff: '2026-08-09T02:00:00Z',
      returned_result_count: 2,
      timed_result_count: 1,
    },
    snapshot_status: 'not_frozen',
    frozen_data_snapshot_id: null,
    receipt_note: 'execution receipt only',
  })

  assert.deepEqual(receipt.ordered_returned_ids, ['9', '4'])
  assert.equal(receipt.result_coverage.status, 'partial')
  assert.equal(receipt.result_coverage.timed_result_count, 1)
  assert.equal(receipt.normalized_contract_sha256, 'b'.repeat(64))
  assert.equal(receipt.entity_catalog_review_status, 'review_required')
  assert.equal(receipt.snapshot_status, 'not_frozen')
  assert.equal(normalizeQueryReceipt(null), null)
  assert.deepEqual(normalizeQueryReceipt({}).ordered_returned_ids, [])
})

test('pagination rules preserve zero, forward-window, and invalid-jump behavior', () => {
  assert.equal(calculateTotalPages(0, 10), 0)
  assert.equal(calculateTotalPages(95, 10), 10)
  assert.equal(calculateTotalPages(95, '20'), 5)
  assert.equal(calculateTotalPages(95, 0), 0)
  assert.deepEqual(buildVisiblePages(1, 10), [1, 2, 3, 4, 5, 6, 7, 8, 9])
  assert.deepEqual(buildVisiblePages(8, 10), [8, 9, 10])
  assert.deepEqual(buildVisiblePages(1, 0), [])
  assert.equal(resolveJumpPage('3', 1, 10), 3)
  assert.equal(resolveJumpPage('999', 4, 10), 4)
  assert.equal(resolveJumpPage('bad', 4, 10), 4)
})

test('search API preserves URL/body/auth and maps backend errors', async () => {
  const calls = []
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), options })
    if (String(url).endsWith('/dashboard/search')) {
      return jsonResponse({ data: [], total: 0 })
    }
    return jsonResponse({ data: [], total: 3 })
  }
  const api = createSearchApi({
    fetchImpl,
    apiPrefix: '/api',
    getAccessToken: () => 'token-1',
  })
  const signal = { aborted: false }
  await api.listNews(
    { page: 2, size: 20, sortBy: '', sortOrder: 'desc', favoriteScopeTopic: '政策观察' },
    { signal, origin: 'https://globemind.top' },
  )
  await api.search({ topic: 'risk', search_type: 'news' }, { signal })
  const history = await api.recordHistory('x'.repeat(300))

  const listUrl = new URL(calls[0].url)
  assert.equal(listUrl.pathname, '/api/dashboard/news')
  assert.equal(listUrl.searchParams.get('page'), '2')
  assert.equal(listUrl.searchParams.get('size'), '20')
  assert.equal(listUrl.searchParams.has('sort_by'), false)
  assert.equal(listUrl.searchParams.get('favorite_scope_topic'), '政策观察')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.equal(calls[0].options.signal, signal)
  assert.equal(calls[1].url, '/api/dashboard/search')
  assert.deepEqual(JSON.parse(calls[1].options.body), { topic: 'risk', search_type: 'news' })
  assert.equal(JSON.parse(calls[2].options.body).keyword.length, 255)
  assert.deepEqual(history, { status: 'ok' })
  assert.deepEqual(buildSearchAuthHeaders(''), {})
  assert.deepEqual(buildSearchAuthHeaders('abc'), { Authorization: 'Bearer abc' })
  assert.equal(
    createSearchEndpoints('/v1').v11Children('story/a', 'l 2'),
    '/v1/dashboard/search/v11-clusters/story%2Fa/children?level=l%202&page=1&page_size=50',
  )
  assert.equal(SEARCH_API_ENDPOINTS.search.endsWith('/dashboard/search'), true)

  const errorApi = createSearchApi({
    fetchImpl: async () => jsonResponse({ detail: [{ msg: 'keyword required' }] }, 422, 'Unprocessable Entity'),
    apiPrefix: '/api',
    getAccessToken: () => '',
  })
  await assert.rejects(
    errorApi.search({}),
    (error) => (
      error instanceof SearchApiError
      && error.operation === 'search'
      && error.status === 422
      && error.message === 'keyword required'
    ),
  )
  assert.equal(
    buildNewsListUrl('/api/dashboard/news', 'https://globemind.top', {
      page: 1,
      size: 10,
      sortBy: '',
      sortOrder: 'desc',
      favoriteScopeTopic: '',
    }).searchParams.has('sort_by'),
    false,
  )
  assert.equal(
    buildNewsListUrl('/api/dashboard/news', 'https://globemind.top', {
      page: 1,
      size: 10,
      sortBy: 'pub_time',
      sortOrder: 'desc',
      favoriteScopeTopic: '',
    }).searchParams.get('sort_by'),
    'published_at',
  )
  assert.equal(
    buildNewsListUrl('/api/dashboard/news', 'https://globemind.top', {
      page: 1,
      size: 10,
      sortBy: 'time',
      sortOrder: 'sideways',
      favoriteScopeTopic: '',
    }).searchParams.has('sort_by'),
    false,
  )
})

test('search API owns options, stats, hierarchy, and favorite transport contracts', async () => {
  const calls = []
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), options })
    return jsonResponse({ items: [] })
  }
  const api = createSearchApi({
    fetchImpl,
    apiPrefix: '/api',
    getAccessToken: () => 'token-2',
  })
  const signal = { aborted: false }
  const favorite = { news_id: 17, topic: '政策观察', kind: 'favorite' }

  await api.getSearchOptions({ signal })
  await api.getStats({ signal })
  await api.getV11Children('story/a', 'l 2', { signal })
  await api.listFavorites({ signal })
  await api.toggleFavorite(favorite, { signal })
  await api.removeFavorite(favorite, { signal })

  assert.deepEqual(calls.map((call) => call.url), [
    '/api/dashboard/search/options',
    '/api/dashboard/stats',
    '/api/dashboard/search/v11-clusters/story%2Fa/children?level=l%202&page=1&page_size=50',
    '/api/user/favorites',
    '/api/user/favorites/toggle',
    '/api/user/favorites/remove',
  ])
  assert.equal(calls.every((call) => call.options.signal === signal), true)
  assert.equal(calls[0].options.headers, undefined)
  assert.equal(calls[1].options.headers, undefined)
  assert.equal(calls[2].options.headers.Authorization, 'Bearer token-2')
  assert.equal(calls[3].options.headers.Authorization, 'Bearer token-2')
  assert.equal(calls[4].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[4].options.body), favorite)
  assert.equal(calls[5].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[5].options.body), favorite)
})

test('search API exposes explicit authenticated snapshot capture, list, get, and ID-only replay', async () => {
  const calls = []
  const api = createSearchApi({
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options })
      return jsonResponse({ schema_version: 'search-snapshot-v1' })
    },
    apiPrefix: '/api',
    getAccessToken: () => 'snapshot-token',
  })
  const signal = { aborted: false }
  const receipt = { receipt_id: 'qr-test' }
  const previous = 'search-snap-20260809T010203000000Z-0000000000000000'

  await api.captureSearchSnapshot(receipt, previous, { signal })
  await api.listSearchSnapshots(25, { signal })
  await api.getSearchSnapshot('snap/a', { signal })
  await api.replaySearchSnapshot('snap/a', { signal })

  assert.deepEqual(calls.map((call) => call.url), [
    '/api/search-snapshots',
    '/api/search-snapshots?limit=25',
    '/api/search-snapshots/snap%2Fa',
    '/api/search-snapshots/snap%2Fa/replay',
  ])
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    receipt,
    expected_previous_snapshot_id: previous,
  })
  assert.equal(calls.every((call) => call.options.headers.Authorization === 'Bearer snapshot-token'), true)
  assert.equal(calls.every((call) => call.options.signal === signal), true)
  assert.equal(createSearchEndpoints('/v2').searchSnapshots, '/v2/search-snapshots')
})

test('search API errors are stable while abort errors remain identifiable', async () => {
  const responseApi = createSearchApi({
    fetchImpl: async () => jsonResponse({ detail: 'service unavailable' }, 503, 'Unavailable'),
    apiPrefix: '/api',
  })
  await assert.rejects(
    responseApi.listFavorites(),
    (error) => (
      error instanceof SearchApiError
      && error.operation === 'list-favorites'
      && error.status === 503
      && error.message === 'service unavailable'
    ),
  )

  const networkFailure = new Error('socket closed')
  const networkApi = createSearchApi({
    fetchImpl: async () => { throw networkFailure },
    apiPrefix: '/api',
  })
  await assert.rejects(
    networkApi.getStats(),
    (error) => (
      error instanceof SearchApiError
      && error.operation === 'stats'
      && error.status === 0
      && error.cause === networkFailure
      && error.message === '加载统计信息失败，请检查网络连接'
    ),
  )

  const abortError = new Error('cancelled')
  abortError.name = 'AbortError'
  const abortApi = createSearchApi({
    fetchImpl: async () => { throw abortError },
    apiPrefix: '/api',
  })
  await assert.rejects(abortApi.getV11Children('1', 'l2'), (error) => error === abortError)
})

test('latest request coordinator aborts old work and rejects stale commits', async () => {
  const controllers = []
  class FakeAbortController {
    constructor() {
      this.signal = { aborted: false }
      controllers.push(this)
    }
    abort() {
      this.signal.aborted = true
    }
  }
  const deferred = () => {
    let resolve
    let reject
    const promise = new Promise((resolvePromise, rejectPromise) => {
      resolve = resolvePromise
      reject = rejectPromise
    })
    return { promise, resolve, reject }
  }
  const first = deferred()
  const second = deferred()
  const coordinator = createLatestSearchRequest({ AbortControllerImpl: FakeAbortController })
  const firstRun = coordinator.run(() => first.promise)
  const secondRun = coordinator.run(() => second.promise)
  assert.equal(controllers[0].signal.aborted, true)
  assert.equal(coordinator.isActive(), true)
  first.resolve('old')
  second.resolve('new')
  assert.deepEqual(await firstRun, { status: 'stale', latest: false })
  assert.deepEqual(await secondRun, { status: 'success', latest: true, value: 'new' })
  assert.equal(coordinator.isActive(), false)

  const third = deferred()
  const thirdRun = coordinator.run(() => third.promise)
  coordinator.cancel()
  assert.equal(controllers[2].signal.aborted, true)
  third.resolve('cancelled')
  assert.deepEqual(await thirdRun, { status: 'stale', latest: false })
  assert.equal(coordinator.isActive(), false)

  const failure = new Error('network down')
  const failed = await coordinator.run(async () => { throw failure })
  assert.deepEqual(failed, { status: 'error', latest: true, error: failure })
})

test('latest request coordinator enforces a bounded search time', async () => {
  let timeoutCallback
  let rejectRequest
  let aborted = false
  class FakeAbortController {
    constructor() {
      this.signal = { aborted: false }
    }
    abort() {
      aborted = true
      this.signal.aborted = true
      rejectRequest?.(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    }
  }
  const coordinator = createLatestSearchRequest({
    AbortControllerImpl: FakeAbortController,
    timeoutMs: 25,
    setTimeoutImpl(callback) {
      timeoutCallback = callback
      return 1
    },
    clearTimeoutImpl() {},
  })
  const run = coordinator.run(() => new Promise((resolve, reject) => {
    void resolve
    rejectRequest = reject
  }))
  timeoutCallback()
  const outcome = await run
  assert.equal(aborted, true)
  assert.equal(outcome.status, 'timeout')
  assert.equal(outcome.latest, true)
  assert.equal(outcome.error instanceof SearchRequestTimeoutError, true)
  assert.equal(outcome.error.timeoutMs, 25)
  assert.equal(SEARCH_REQUEST_TIMEOUT_MS, 8_000)
})

test('operation-aware retry repeats the failed GET or POST snapshot without switching modes', async () => {
  const calls = []
  let listAttempts = 0
  let searchAttempts = 0
  const api = {
    async listNews(query, options) {
      calls.push({ kind: 'list', query, options })
      listAttempts += 1
      if (listAttempts === 1) throw new Error('list failed')
      return { data: [], total: 0 }
    },
    async search(params, options) {
      calls.push({ kind: 'search', params, options })
      searchAttempts += 1
      if (searchAttempts === 1) throw new Error('search failed')
      return { data: [], total: 0 }
    },
  }
  const runner = createSearchOperationRunner({
    api,
    request: createLatestSearchRequest(),
  })

  const listQuery = { page: 4, size: 20, sortBy: '', sortOrder: 'desc' }
  const listOperation = createSearchListOperation({
    query: listQuery,
    origin: 'https://globemind.top',
  })
  listQuery.page = 99
  const failedList = await runner.execute(listOperation)
  assert.equal(failedList.status, 'error')
  assert.equal(runner.getFailedOperation().kind, SEARCH_DATA_OPERATION_KINDS.LIST)
  const retriedList = await runner.retry()
  assert.equal(retriedList.status, 'success')
  assert.deepEqual(calls.slice(0, 2).map((call) => call.kind), ['list', 'list'])
  assert.deepEqual(calls.slice(0, 2).map((call) => call.query.page), [4, 4])
  assert.equal(calls[0].options.origin, 'https://globemind.top')
  assert.equal(calls[0].options.signal instanceof AbortSignal, true)

  const payload = { topic: 'original', search_type: 'l2' }
  const requestParams = { topic: 'original', searchType: 'l2', page: 2, pageSize: 10 }
  const searchOperation = createSearchQueryOperation({
    params: payload,
    requestParams,
    searchType: 'l2',
  })
  payload.topic = 'changed'
  requestParams.searchType = 'news'
  const failedSearch = await runner.execute(searchOperation)
  assert.equal(failedSearch.status, 'error')
  assert.equal(runner.getFailedOperation().kind, SEARCH_DATA_OPERATION_KINDS.SEARCH)
  const retriedSearch = await runner.retry()
  assert.equal(retriedSearch.status, 'success')
  assert.deepEqual(calls.slice(2).map((call) => call.kind), ['search', 'search'])
  assert.deepEqual(calls.slice(2).map((call) => call.params.topic), ['original', 'original'])
  assert.equal(retriedSearch.operation.searchType, 'l2')
  assert.equal(runner.getFailedOperation(), null)
  assert.deepEqual(await runner.retry(), { status: 'idle', latest: true, operation: null })
  assert.equal(Object.isFrozen(listOperation.query), true)
  assert.equal(Object.isFrozen(searchOperation.requestParams), true)
})

test('theme storage preserves keys, malformed-data fallback, aliases, and naming', () => {
  assert.equal(SEARCH_THEME_NAME_KEY, 'data_search_theme_name_v1')
  assert.equal(SEARCH_THEME_LIST_KEY, 'data_search_themes_list_v1')
  assert.equal(DEFAULT_SEARCH_FOLDER_NAME, '默认工作夹')
  assert.equal(LEGACY_DEFAULT_THEME_NAME, '新闻分析主题')
  assert.equal(normalizeSearchFolderName('  '), '默认工作夹')
  assert.equal(displaySearchFolderName('新闻分析主题'), '默认工作夹')
  assert.equal(displaySearchFolderName('政策观察'), '政策观察')
  assert.deepEqual(normalizeSearchThemeList([' A ', 'A', '', 'B'], 'C'), ['A', 'B', 'C'])
  assert.deepEqual(renameSearchThemeList(['A', 'B'], 'A', 'B'), ['B'])
  assert.deepEqual(renameSearchThemeList(['A'], 'missing', 'C'), ['A', 'C'])
  assert.equal(createSearchThemeName([], new Date(2026, 6, 11)), '工作文件夹 7-11')
  assert.equal(
    createSearchThemeName(['工作文件夹 7-11', '工作文件夹 7-11 (1)'], new Date(2026, 6, 11)),
    '工作文件夹 7-11 (2)',
  )

  const values = new Map([
    [SEARCH_THEME_NAME_KEY, ' 政策观察 '],
    [SEARCH_THEME_LIST_KEY, '{broken'],
  ])
  const storage = {
    getItem(key) {
      return values.get(key) ?? null
    },
    setItem(key, value) {
      values.set(key, value)
    },
  }
  const model = createSearchThemeStorage(storage)
  assert.equal(model.readCurrent(), '政策观察')
  assert.deepEqual(model.loadThemes('政策观察'), ['政策观察'])
  assert.equal(values.get(SEARCH_THEME_LIST_KEY), '["政策观察"]')
  model.saveCurrent('风险观察')
  assert.equal(values.get(SEARCH_THEME_NAME_KEY), '风险观察')
})

async function walkFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) files.push(...(await walkFiles(path)))
    else files.push(path)
  }
  return files
}

test('data-search route consumes the feature through its public entry', async () => {
  const route = await readFile(
    new URL('../src/views/DataService/data-search.vue', import.meta.url),
    'utf8',
  )
  const publicEntry = await readFile(new URL('../src/features/search/index.js', import.meta.url), 'utf8')
  assert.match(route, /from ['"]@\/features\/search\/index\.js['"]/)
  assert.doesNotMatch(route, /from ['"]@\/features\/search\/(?!index\.js)/)
  assert.doesNotMatch(route, /\bAPI_PREFIX\b/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
  assert.doesNotMatch(route, /data_search_theme_(?:name|themes_list)_v1/)
  assert.match(route, /dataRequest\.cancel\(\)/)
  assert.match(route, /auxiliaryRequestController\.abort\(\)/)
  assert.match(route, /@click="retryLastDataRequest"/)
  assert.match(route, /dataOperations\.retry\(\)/)
  assert.match(publicEntry, /from ['"]\.\/api\.js['"]/)
  assert.match(publicEntry, /from ['"]\.\/model\.js['"]/)
  assert.match(publicEntry, /from ['"]\.\/request\.js['"]/)
  assert.match(publicEntry, /from ['"]\.\/storage\.js['"]/)

  const sourceFiles = (await walkFiles(new URL('../src', import.meta.url).pathname)).filter((path) => (
    /\.(vue|js)$/.test(path)
  ))
  let publicImports = 0
  for (const path of sourceFiles) {
    const source = await readFile(path, 'utf8')
    for (const match of source.matchAll(/from ['"]@\/features\/search\/([^'"]+)['"]/g)) {
      publicImports += 1
      assert.equal(match[1], 'index.js', `deep search import in ${path}`)
    }
  }
  assert.equal(publicImports, 2)
})

test('data-search exposes one page heading and programmatic labels for search controls', async () => {
  const source = await readFile(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8')
  const styles = await readFile(new URL('../src/views/DataService/data-search.less', import.meta.url), 'utf8')

  assert.equal(source.match(/<h1\b/g)?.length, 1)
  assert.match(source, /<h1>新闻与事件检索<\/h1>/)

  for (const id of [
    'search-folder-name',
    'search-topic',
    'search-must-include',
    'search-any-include',
    'search-need-exclude',
    'search-start-time',
    'search-end-time',
    'search-data-source',
    'search-language',
    'search-page-size',
    'search-jump-page',
  ]) {
    assert.match(source, new RegExp(`<label[^>]*for=["']${id}["']`), `${id}: label`)
    assert.match(source, new RegExp(`<(?:input|select)[^>]*id=["']${id}["']`), `${id}: control`)
  }

  assert.match(source, /aria-label="上一页"/)
  assert.match(source, /aria-label="下一页"/)
  assert.match(source, /:aria-current="page === currentPage \? 'page' : undefined"/)
  assert.match(styles, /\.search-sr-only,\s*\n\.critical-action-status\s*\{/)
})

test('critical search actions expose guest persistence, login resume, export, and sync outcomes', async () => {
  const source = await readFile(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8')
  const loginModal = await readFile(new URL('../src/components/LoginModal.vue', import.meta.url), 'utf8')

  assert.match(source, /role="status" aria-live="polite" aria-atomic="true"[^>]*>\{\{ criticalActionStatus \}\}/)
  assert.match(source, /aria-describedby="search-mode-hint"/)
  assert.match(source, /searchModeDisclosure\(searchParams\.mode\)\.description/)
  assert.match(source, /PENDING_ASSISTANT_OPEN_KEY/)
  assert.match(source, /当前检索与勾选素材已保留/)
  assert.match(source, /new CustomEvent\('showLoginModal'\)/)
  assert.match(source, /仅保存在此浏览器/)
  assert.match(source, /账号同步失败/)
  assert.match(source, /浏览器阻止了会话存储/)
  assert.match(source, /已从本浏览器的临时收藏/)
  assert.match(source, /导出失败，请重试或检查浏览器下载权限/)
  assert.match(loginModal, /role="dialog"/)
  assert.match(loginModal, /for="login-modal-username"/)
  assert.match(loginModal, /aria-live="assertive"/)
  assert.match(loginModal, /usernameField\.value\?\.focus\(\)/)
  assert.match(loginModal, /event\.key === 'Escape'/)
  assert.match(loginModal, /trapDialogTab/)
  assert.match(loginModal, /restoreDialogFocus/)
  assert.match(loginModal, /formatApiErrorDetail/)
})

test('data-search displays actual query, time-field semantics, and non-fabricated explain stages', async () => {
  const source = await readFile(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8')
  const styles = await readFile(new URL('../src/views/DataService/data-search.less', import.meta.url), 'utf8')

  assert.match(source, /实际查询：\{\{ preset\.topic \}\}/)
  assert.match(source, /const searchParams = reactive\(createSearchParams\(\)\)/)
  assert.match(source, /applySearchSituationPreset\(searchParams, preset, pageSize\.value\)/)
  assert.match(source, />新闻发布日期<\/button>/)
  assert.match(source, /事件时间：\{\{ item\.start_date \|\| '—' \}\} 至 \{\{ item\.end_date \|\| '—' \}\}/)
  assert.match(source, /事件时间：\{\{ child\.start_date \|\| '—' \}\} 至 \{\{ child\.end_date \|\| '—' \}\}/)
  assert.match(source, />标题命中优先<\/button>/)
  assert.match(source, /层级搜索暂不支持来源筛选/)
  assert.match(source, /normalizeSearchParamsForType\(\{ \.\.\.searchParams, page: 1 \}, type\)/)
  assert.match(source, /queryExplain\.entity_expansions/)
  assert.match(source, /queryExplain\.expanded_terms\.join/)
  assert.match(source, /queryExplain\.query_ast/)
  assert.match(source, /queryExplain\.execution_expression/)
  assert.match(source, /queryLimitText\(queryExplain\.limits\)/)
  assert.match(source, /queryExplain\.applied_filters\.map\(queryFilterText\)/)
  assert.match(source, /entity\.entity_id/)
  assert.match(source, /entity\.matched_alias_status !== 'active'/)
  assert.match(source, /queryFieldLabel\(entity\.query_field\)/)
  assert.match(source, /queryExplain\.alias_catalog_version/)
  assert.match(source, /entity\.valid_from \|\| '未知'/)
  assert.match(source, /自动放宽：/)
  assert.match(source, /本次查询执行收据/)
  assert.match(source, /queryReceipt\.normalized_contract_sha256/)
  assert.match(source, /queryReceipt\.ordered_returned_ids_sha256/)
  assert.match(source, /queryReceipt\.result_coverage\.cutoff/)
  assert.match(source, /这是执行收据，不是冻结数据快照/)
  assert.match(styles, /\.query-receipt-grid/)
  assert.match(source, /采集时间和更新时间当前不可筛选/)
  assert.match(source, /searchModeDisclosure\(searchParams\.mode\)\.description/)
  assert.match(source, /每个 OR 分支必须有正向词/)
  assert.match(source, /不声称 95% 准确率/)
  assert.match(source, /searchParams\.searchType === 'news' \? '新闻发布日期' : '事件发生时间'/)
  assert.match(source, /relative_time: `\$\{timeFilterLabel\.value\}相对范围`/)
  assert.match(source, /start_time: `\$\{timeFilterLabel\.value\}起点`/)
  assert.match(source, /end_time: `\$\{timeFilterLabel\.value\}终点`/)
  assert.match(source, /`\$\{timeFilterLabel\.value\}：\$\{searchParams\.publishTime/)
  assert.doesNotMatch(source, /`发布时间：\$\{searchParams\.publishTime/)
  assert.doesNotMatch(source, /displayTime \|\| card\.pub_time \|\| card\.time/)
  assert.doesNotMatch(source, /item\.pub_time \|\| item\.published_at \|\| item\.time/)
  assert.doesNotMatch(source, /child\.displayTime \|\| child\.pub_time \|\| child\.time/)
  assert.match(styles, /\.query-explain\s*\{/)
  assert.match(styles, /\.query-stage-list\s*\{/)
  assert.match(styles, /\.query-ast-value\s*\{/)
})

test('data-search mobile pagination wraps without widening the page', async () => {
  const styles = await readFile(
    new URL('../src/views/DataService/data-search.less', import.meta.url),
    'utf8',
  )
  const mobileStyles = styles.slice(styles.indexOf('@media (max-width: 900px)'))

  assert.match(mobileStyles, /\.pagination\s*\{[^}]*flex-wrap:\s*wrap;/s)
  assert.match(mobileStyles, /\.pagination\s*\{[^}]*gap:\s*12px 16px;/s)
  assert.match(
    mobileStyles,
    /\.pagination-info,[\s\S]*\.pagination-jump\s*\{[^}]*min-width:\s*0;/,
  )
})
