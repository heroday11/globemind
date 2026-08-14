import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildGroundNewsListParams,
  buildGroundNewsHomeModel,
  buildGroundNewsSourceProfileModel,
  homeBiasSummary,
  homeFormatNumber,
  homeProfileCoveragePct,
  homeSectionBadge,
  homeSourceTypeItems,
  homeTopicPath,
  createGroundNewsApi,
  edgeTypeLabel,
  formatRange,
  groundNewsTabs,
  newsDetailPath,
  normalizeBiasBuckets,
  normalizeLeaning,
  qualityPct,
  sortOptions,
  timelineCountLabel,
  timelineNodeResearchNote,
  timelineQualityLabel,
  timelineSamplingNotice,
  sourcePath,
  statusLabel,
  topEntries,
} from '../src/features/ground-news/index.js'

function controlledSourceProfile(overrides = {}) {
  return {
    profile_contract_version: 'ground-news-source-profile-v1',
    domain: 'example.test',
    source_name: 'Example News',
    country: 'Exampleland',
    source_type: 'wire_service',
    ownership_type: 'wire_service',
    geo_alignment: 'western',
    political_leaning: 'center_left',
    credibility_tier: 'high',
    label_confidence: 'high',
    review_status: 'reviewed',
    profile_version: 'media_profile_seed_v1',
    evidence_url: 'https://directory.example.test/source/example',
    updated_at: '2026-06-26T12:00:00Z',
    method_card: {
      schema_version: 'ground-news-source-profile-method-card-v1',
      profile_contract_version: 'ground-news-source-profile-v1',
      catalog_profile_version: 'media_profile_seed_v1',
      catalog_profile_version_state: 'recognized',
      overall_state: 'partial_unknown',
      methods: [
        {
          method_id: 'historical_wave1_seed_v1',
          method_version: 'v1',
          kind: 'catalog_seed',
          version_state: 'profile_version_bound',
          supports_fields: ['source_identity', 'source_structure'],
          assurance_scope: 'catalog_structure_only',
        },
        {
          method_id: 'ground_news_rating_v1',
          method_version: 'v1',
          kind: 'third_party_directory_rating',
          version_state: 'explicit',
          supports_fields: ['political_leaning', 'credibility_tier'],
          assurance_scope: 'third_party_catalog_label_only',
        },
      ],
      note_disposition_codes: [],
      unknown_method_count: 1,
      method_input_truncated: false,
      assurance: {
        state: 'catalog_labels_only',
        independent_validation: 'not_performed',
        source_reliability_conclusion: 'not_established',
        fact_accuracy_conclusion: 'not_established',
        reason_code: 'DIRECTORY_LABELS_ARE_NOT_RELIABILITY_FINDINGS',
      },
      field_dispositions: {
        source_type: {
          state: 'catalog_value',
          reason_code: 'CONTROLLED_PROFILE_VERSION',
          method_ids: ['historical_wave1_seed_v1'],
        },
        ownership_type: {
          state: 'catalog_value',
          reason_code: 'CONTROLLED_PROFILE_VERSION',
          method_ids: ['historical_wave1_seed_v1'],
        },
        geo_alignment: {
          state: 'catalog_value',
          reason_code: 'CONTROLLED_PROFILE_VERSION',
          method_ids: ['historical_wave1_seed_v1'],
        },
        political_leaning: {
          state: 'third_party_catalog_label',
          reason_code: 'CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD',
          method_ids: ['ground_news_rating_v1'],
        },
        credibility_tier: {
          state: 'third_party_catalog_label',
          reason_code: 'CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD',
          method_ids: ['ground_news_rating_v1'],
        },
        label_confidence: {
          state: 'catalog_metadata',
          reason_code: 'QUALITY_LABEL_RELEASED_WITH_CONTROLLED_METHOD',
          method_ids: ['ground_news_rating_v1'],
        },
        review_status: {
          state: 'catalog_metadata',
          reason_code: 'CONTROLLED_PROFILE_VERSION',
          method_ids: ['historical_wave1_seed_v1'],
        },
      },
    },
    ...overrides,
  }
}

test('list params preserve the backend query contract', () => {
  assert.deepEqual(
    buildGroundNewsListParams({
      pageSize: 18,
      minArticles: 5,
      minSources: 3,
      dateDays: 30,
      sort: 'coverage',
      quality: 'ready',
      includeFirstDetail: false,
      query: '',
      eventFamily: 'diplomacy',
    }),
    {
      page_size: 18,
      min_articles: 5,
      min_sources: 3,
      date_days: 30,
      sort: 'coverage',
      quality: 'ready',
      include_first_detail: false,
      q: undefined,
      event_family: 'diplomacy',
    },
  )
})

test('API facade owns ground-news endpoints and encodes route identifiers', async () => {
  const calls = []
  const client = {
    async get(url, options) {
      calls.push({ url, options })
      return { data: { request: calls.length } }
    },
  }
  const api = createGroundNewsApi({ client, apiRoot: '/api' })

  assert.deepEqual(await api.listStories({ query: 'trade' }), { request: 1 })
  assert.deepEqual(await api.getStory('cluster/中文 ?'), { request: 2 })
  assert.deepEqual(await api.getHome({ candidateLimit: 42, minArticles: 3 }), { request: 3 })
  assert.deepEqual(await api.getBlindspots({ pageSize: 25 }), { request: 4 })
  assert.deepEqual(await api.getTopic('trade/中文 ?', { pageSize: 30 }), { request: 5 })
  assert.deepEqual(await api.getSource('example.com/world'), { request: 6 })
  assert.equal((await api.getTimeline('timeline/a+b')).request, 7)
  assert.equal((await api.getL2Chain('chain/a+b')).request, 8)

  assert.equal(calls[0].url, '/api/story-graph/ground-news/list')
  assert.equal(calls[0].options.params.q, 'trade')
  assert.equal(
    calls[1].url,
    `/api/story-graph/ground-news/${encodeURIComponent('cluster/中文 ?')}`,
  )
  assert.equal(calls[2].url, '/api/story-graph/ground-news/home')
  assert.deepEqual(calls[2].options.params, { candidate_limit: 42, min_articles: 3 })
  assert.equal(calls[3].url, '/api/story-graph/ground-news/blindspots')
  assert.deepEqual(calls[3].options.params, { page_size: 25 })
  assert.equal(
    calls[4].url,
    `/api/story-graph/ground-news/topic/${encodeURIComponent('trade/中文 ?')}`,
  )
  assert.deepEqual(calls[4].options.params, { page_size: 30 })
  assert.equal(
    calls[5].url,
    `/api/story-graph/ground-news/source/${encodeURIComponent('example.com/world')}`,
  )
  assert.equal(
    calls[6].url,
    `/api/story-graph/ground-news/timeline/${encodeURIComponent('timeline/a+b')}`,
  )
  assert.equal(calls[7].url, `/api/story-graph/l2-chain/${encodeURIComponent('chain/a+b')}`)
})

test('timeline adapter preserves unknown counts and rejects old relation schemas', async () => {
  const validSampling = {
    schema_version: 'graph-sampling-provenance-v1',
    coverage_state: 'partial',
    components: [{
      unit: 'l15_segment_node',
      state: 'bounded_partial',
      requested_count: 240,
      evaluated_count: 3,
      returned_count: 2,
      excluded_count: 1,
      limit: 240,
      overflow: true,
      selection_rule: 'ordered_chain_segments',
      reason_codes: ['DISPLAY_LIMIT', 'GRAPH_COMPLETENESS_NOT_ESTABLISHED'],
      excluded_node_ids_disclosed: false,
    }],
    complete_graph_claim: false,
  }
  const client = {
    async get() {
      return {
        data: {
          chain: { chain_id: 'chain-1', article_count: null, chain_quality: 'strong' },
          nodes: [
            { segment_id: 'one', source_count: null },
            { segment_id: 'two' },
          ],
          edges: [{
            from_id: 'one',
            to_id: 'two',
            edge_type: 'influence',
            relation_reason: '相邻节点存在时间和主题连续性。',
          }],
          sampling: validSampling,
        },
      }
    },
  }
  const model = await createGroundNewsApi({ client, apiRoot: '/api' }).getTimeline('chain-1')

  assert.equal(model.edges[0].edge_type, 'relation_unknown')
  assert.equal(model.edges[0].relation_semantics.ontology_state, 'explicit_unknown')
  assert.match(model.edges[0].relation_reason, /未知.*不可.*影响.*因果/)
  assert.equal(model.nodes[0].source_count, null)
  assert.equal(model.nodes[1].source_count, null)
  assert.equal(model.articleCount, null)
  assert.equal(model.totalNodeCount, 3)
  assert.equal(timelineCountLabel(model.articleCount, '条新闻'), '新闻数未知')
  assert.equal(timelineCountLabel(null, '个信源'), '信源数未知')
  assert.equal(timelineCountLabel(0, '个信源'), '0 个信源')
  assert.match(timelineSamplingNotice(model), /已返回 2\/3 个节点/)
  assert.match(timelineSamplingNotice(model), /本次未返回 1 个/)
  assert.match(timelineSamplingNotice(model), /不是完整图/)
  assert.match(timelineQualityLabel('strong'), /派生链质量.*非事实.*非因果/)
  assert.equal(edgeTypeLabel('parallel'), '时间重叠（非影响/因果）')
  assert.equal(edgeTypeLabel('influence'), '关系类型未知（不可作影响/因果）')

  const unknown = await createGroundNewsApi({
    client: {
      async get() {
        return { data: { chain: {}, nodes: [], edges: [] } }
      },
    },
    apiRoot: '/api',
  }).getTimeline('old')
  assert.equal(unknown.totalNodeCount, null)
  assert.match(timelineSamplingNotice(unknown), /抽样范围未知/)
})

test('timeline page consumes strict model helpers and does not invent counts, errors, or relations', async () => {
  const source = await readFile(
    new URL('../src/views/GroundNewsTimeline.vue', import.meta.url),
    'utf8',
  )

  assert.match(source, /timelineSamplingNotice\(data\.value\)/)
  assert.match(source, /timelineCountLabel\(/)
  assert.match(source, /timelineQualityLabel\(/)
  assert.match(source, /edgeTypeLabel\(edges\[index\]\.edge_type\)/)
  assert.match(source, /timelineNodeResearchNote\(node, nodes\.value\[index - 1\]\)/)
  assert.match(source, /研究增量/)
  assert.doesNotMatch(source, /segment_count\s*\|\|\s*nodes\.length/)
  assert.doesNotMatch(source, /article_count\s*\|\|\s*0/)
  assert.doesNotMatch(source, /source_count\s*\|\|\s*0/)
  assert.doesNotMatch(source, /err\?\.response\?\.data\?\.detail|err\?\.message|err\.message/)
  assert.doesNotMatch(source, /相邻节点存在时间和主题连续性/)
  assert.doesNotMatch(source, /function edgeLabel\(/)
  assert.doesNotMatch(source, /强关联|\b可用\b/)
})

test('timeline research notes separate baselines, possible duplicates, and candidate deltas', () => {
  assert.equal(
    timelineNodeResearchNote({ display_title: 'First report' }).state,
    'baseline',
  )
  assert.equal(
    timelineNodeResearchNote(
      { display_title: 'Same Report!' },
      { display_title: 'same report' },
    ).state,
    'possible_duplicate',
  )
  const candidate = timelineNodeResearchNote(
    {
      display_title: 'Agreement signed',
      event_action: 'agreement_signed',
      start_date: '2026-08-02',
    },
    {
      display_title: 'Talks continue',
      event_action: 'negotiation_talks',
      start_date: '2026-08-01',
    },
  )
  assert.equal(candidate.state, 'candidate_delta')
  assert.match(candidate.message, /待核验线索/)
})

test('bias normalization produces stable five-bucket percentages', () => {
  assert.equal(normalizeLeaning('government aligned'), 'state_aligned')
  assert.equal(normalizeLeaning('least-biased center'), 'center')
  assert.equal(normalizeLeaning('neutral centre'), 'center')
  assert.equal(normalizeLeaning('unrated'), 'unknown')

  const rows = normalizeBiasBuckets({ left: 2, right: 1 })
  assert.deepEqual(rows.map((row) => row.key), [
    'left',
    'center',
    'right',
    'state_aligned',
    'unknown',
  ])
  assert.ok(Math.abs(rows.find((row) => row.key === 'left').value - (200 / 3)) < 1e-9)
  assert.ok(Math.abs(rows.reduce((sum, row) => sum + row.value, 0) - 100) < 1e-9)

  const emptyRows = normalizeBiasBuckets()
  assert.equal(emptyRows.find((row) => row.key === 'unknown').value, 100)
})

test('presentation helpers keep route, ordering, and fallback behavior stable', () => {
  assert.equal(newsDetailPath('news/1'), '/data-service/news/news%2F1')
  assert.equal(sourcePath('example.com/world'), '/data-service/ground-news-source/example.com%2Fworld')
  assert.equal(sourcePath(''), '/data-service/ground-news')
  assert.equal(formatRange(null, null), '无日期')
  assert.equal(formatRange('2026-01-01T10:00:00Z', '2026-01-03'), '2026-01-01 -> 2026-01-03')
  assert.equal(qualityPct(null), '质量指标未知')
  assert.equal(qualityPct('0.876'), '质量指标未知')
  assert.equal(qualityPct(Number.NaN), '质量指标未知')
  assert.equal(qualityPct(0.876), '质量指标未知')
  assert.equal(statusLabel('single_source'), '单一信源')
  assert.deepEqual(sortOptions.map((item) => item.label), ['媒体关注', '最新', '多来源'])
  assert.deepEqual(groundNewsTabs.map((item) => item.label), ['报道切面', '时间线', '来源目录', '关联线索'])
  assert.deepEqual(topEntries({ low: 1, high: 8, medium: 3 }, 2), [
    { key: 'high', value: 8 },
    { key: 'medium', value: 3 },
  ])
})

test('source profile method card exposes readable catalog labels without reliability inference', () => {
  const model = buildGroundNewsSourceProfileModel(controlledSourceProfile())

  assert.equal(model.contractValid, true)
  assert.equal(model.sourceName, 'Example News')
  assert.equal(model.labels.sourceType, '通讯社')
  assert.equal(model.labels.politicalLeaning, '第三方目录政治倾向：偏左')
  assert.equal(model.labels.credibility, '第三方目录标签：高')
  assert.equal(model.labels.confidence, '标签置信度：高')
  assert.equal(model.labels.reviewStatus, '已复核')
  assert.equal(model.values.politicalLeaning, 'center_left')
  assert.equal(model.values.credibilityTier, 'high')
  assert.equal(model.method.status, '部分方法未知')
  assert.equal(model.method.profileVersion, 'media_profile_seed_v1')
  assert.deepEqual(model.method.methods.map((method) => method.label), [
    '历史 Wave1 来源目录种子',
    'Ground News 第三方目录评级',
  ])
  assert.equal(model.method.unknownMethodCount, 1)
  assert.deepEqual(model.method.notes, [])
  assert.equal(
    model.method.limitation,
    '目录标签不代表独立事实准确率或来源可靠性结论。',
  )
})

test('unknown source profile cards and values fail closed without echoing internal codes', () => {
  const unknown = controlledSourceProfile({
    profile_version: 'trusted_seed_v99',
    source_type: 'elite_source',
    political_leaning: 'objective',
    credibility_tier: 'very_high',
    label_confidence: 'certain',
    review_status: 'certified',
    method_card: {
      schema_version: 'future-method-card-v99',
      overall_state: 'trusted',
      methods: [{ method_id: 'ai_certified_v9', label: 'Flawless' }],
    },
  })
  const model = buildGroundNewsSourceProfileModel(unknown)

  assert.equal(model.contractValid, false)
  assert.equal(model.labels.sourceType, '来源类型未知')
  assert.equal(model.labels.politicalLeaning, '政治倾向未评级')
  assert.equal(model.labels.credibility, '目录标签：未知')
  assert.equal(model.labels.confidence, '标签置信度未知')
  assert.equal(model.labels.reviewStatus, '复核状态未知')
  assert.equal(model.values.politicalLeaning, 'unknown')
  assert.equal(model.values.credibilityTier, 'unknown')
  assert.equal(model.method.status, '方法未知')
  assert.equal(model.method.profileVersion, '未知')
  assert.deepEqual(model.method.methods, [])
  assert.equal(JSON.stringify(model).includes('ai_certified'), false)
  assert.equal(JSON.stringify(model).includes('very_high'), false)
  assert.equal(JSON.stringify(model).includes('elite_source'), false)
})

test('source profile evidence locators fail closed and discard query secrets', () => {
  assert.equal(
    buildGroundNewsSourceProfileModel(controlledSourceProfile({
      evidence_url: 'javascript:alert(1)',
    })).evidenceUrl,
    null,
  )
  assert.equal(
    buildGroundNewsSourceProfileModel(controlledSourceProfile({
      evidence_url: 'https://reviewer:secret@example.test/method',
    })).evidenceUrl,
    null,
  )
  assert.equal(
    buildGroundNewsSourceProfileModel(controlledSourceProfile({
      evidence_url: 'https://directory.example.test/source/example?token=secret#private',
    })).evidenceUrl,
    'https://directory.example.test/source/example',
  )
})

test('source profile page consumes the controlled method model and never renders raw notes', async () => {
  const source = await readFile(
    new URL('../src/views/GroundNewsSource.vue', import.meta.url),
    'utf8',
  )

  assert.match(source, /buildGroundNewsSourceProfileModel/)
  assert.match(source, /safeExternalHttpUrl\(sourceModel\.value\.evidenceUrl\)/)
  assert.match(source, /目录标签不代表独立事实准确率或来源可靠性结论/)
  assert.doesNotMatch(source, /profile\.evidence_note/)
  assert.doesNotMatch(source, /labels\[value\]\s*\|\|\s*biasLabel\(value\)/)
})

test('blindspot copy treats directory labels as catalog metadata, not factuality risk', async () => {
  const source = await readFile(
    new URL('../src/views/GroundNewsFeed.vue', import.meta.url),
    'utf8',
  )

  assert.match(source, /第三方目录标签仅作目录构成展示/)
  assert.match(source, /不代表事实准确率或来源可靠性/)
  assert.doesNotMatch(source, /事实性风险/)
})

test('Ground News list surfaces validate source-profile cards before rendering labels', async () => {
  const [feed, desk] = await Promise.all([
    readFile(new URL('../src/views/GroundNewsFeed.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/views/GroundNewsDesk.vue', import.meta.url), 'utf8'),
  ])

  assert.match(feed, /buildGroundNewsSourceProfileModel\(source\)/)
  assert.match(feed, /source\.politicalLabel/)
  assert.match(feed, /source\.credibilityLabel/)
  assert.match(desk, /buildGroundNewsSourceProfileModel\(row\)/)
  assert.match(desk, /profile\.values\.politicalLeaning/)
  assert.match(desk, /profile\.values\.credibilityTier/)
})

test('home model owns deterministic section, topic, source, and front-page selection', () => {
  const lead = {
    cluster_id: 'lead',
    event_family: 'diplomacy',
    article_count: 10,
    source_count: 3,
    source_names: ['A', 'B'],
    rank_score: 5,
  }
  const latest = {
    cluster_id: 'latest',
    event_family: 'economic_trade',
    article_count: 7,
    source_count: 2,
    source_names: ['A'],
    end_date: '2026-07-10',
  }
  const week = {
    cluster_id: 'week',
    event_family: 'economic_trade',
    article_count: 4,
    source_count: 2,
    source_names: ['C'],
    end_date: '2026-07-09',
  }
  const model = buildGroundNewsHomeModel({
    lead_story: lead,
    metrics: {
      source_profile_coverage: { total_profiles: 4, known_bias_profiles: 3 },
    },
    edition: { rotation_days: 4 },
    sections: [
      { key: 'latest', title: 'Latest', stories: [latest], min_articles: 2 },
      { key: 'week_watch', title: 'Week', stories: [week] },
      { key: 'diplomacy', title: 'Diplomacy', stories: [lead] },
    ],
    l2_watchlist: [{ chain_id: 'chain-1' }],
  })

  assert.equal(model.profileCoveragePct, '75%')
  assert.deepEqual(model.allHomeStories.map((story) => story.cluster_id), [
    'lead',
    'latest',
    'week',
  ])
  assert.deepEqual(model.frontLatestStories, [latest, week, lead])
  assert.deepEqual(model.frontWeekStories, [week])
  assert.deepEqual(model.latestStories, [latest, week, lead])
  assert.deepEqual(model.storySections.map((section) => section.key), ['diplomacy'])
  assert.deepEqual(model.sourceLeaders, [
    { name: 'A', count: 2 },
    { name: 'B', count: 1 },
    { name: 'C', count: 1 },
  ])
  assert.equal(model.topicChips[0].key, 'economic_trade')
  assert.equal(homeTopicPath('economy_tech', latest), '/data-service/ground-news-topic/economic_trade')
  assert.equal(
    homeSectionBadge(model.sectionByKey.latest, model.sectionByKey, model.edition),
    '2 条起 · 按历史快照排序 · 1 张卡片',
  )
  assert.equal(
    homeSectionBadge(
      model.sectionByKey.latest,
      model.sectionByKey,
      model.edition,
      { historical: false },
    ),
    '2 条起 · 按更新批次滚动 · 1 张卡片',
  )
})

test('home latest selection prioritizes event recency over the API latest bucket', () => {
  const staleFeatured = {
    cluster_id: 'stale-featured',
    event_family: 'diplomacy',
    article_count: 12,
    source_count: 6,
    end_date: '2026-06-20',
  }
  const freshStory = {
    cluster_id: 'fresh-story',
    event_family: 'military_security',
    article_count: 3,
    source_count: 2,
    end_date: '2026-07-13',
  }
  const model = buildGroundNewsHomeModel({
    sections: [
      { key: 'latest', title: 'Latest', stories: [staleFeatured], requires_image: true },
      { key: 'pulse_72h', title: 'Pulse', stories: [freshStory] },
    ],
  })

  assert.deepEqual(
    model.frontLatestStories.map((story) => story.cluster_id),
    ['fresh-story', 'stale-featured'],
  )
})

test('home model exposes stable collections for a legal empty response', () => {
  const model = buildGroundNewsHomeModel({
    lead_story: null,
    metrics: {
      total_stories: 0,
      total_articles: 0,
      source_profile_coverage: {
        total_profiles: 0,
        known_bias_profiles: 0,
      },
    },
    edition: {},
    sections: [],
    l2_watchlist: [],
  })

  assert.deepEqual(model.latestStories, [])
  assert.deepEqual(model.frontLatestStories, [])
  assert.deepEqual(model.frontWeekStories, [])
  assert.deepEqual(model.storySections, [])
  assert.deepEqual(model.l2Watchlist, [])
  assert.equal(model.profileCoveragePct, '不可计算')
})

test('home metrics preserve missing values instead of presenting fabricated zero', () => {
  assert.equal(homeFormatNumber(null), '待核验')
  assert.equal(homeFormatNumber(undefined), '待核验')
  assert.equal(homeFormatNumber('not-a-number'), '待核验')
  assert.equal(homeFormatNumber(0), '0')
  assert.equal(homeProfileCoveragePct({}), '待核验')
  assert.equal(
    homeProfileCoveragePct({
      source_profile_coverage: { total_profiles: 4, known_bias_profiles: null },
    }),
    '待核验',
  )
})

test('home page consumes latest stories through the feature model contract', async () => {
  const source = await readFile(
    new URL('../src/views/GroundNewsHome.vue', import.meta.url),
    'utf8',
  )

  assert.match(
    source,
    /const latestStories = computed\(\(\) => homeModel\.value\.latestStories\)/,
  )
  assert.match(source, /featureFreshness\(freshnessReport\.value, ['"]ground-news['"]\)/)
  assert.match(source, /groundNewsFreshness\.value\.historical/)
  assert.doesNotMatch(source, />\s*Live Wire\s*</)
  assert.doesNotMatch(source, />\s*今日简报\s*</)
  assert.doesNotMatch(source, />\s*本周多源\s*</)
  assert.doesNotMatch(
    source,
    /product_candidate_count\s*\|\|\s*metrics\.candidate_count/,
  )
})

test('home presentation helpers preserve bias and source fallbacks', () => {
  assert.equal(homeBiasSummary({}), '第三方目录分组未知')
  assert.equal(
    homeBiasSummary({ political_group_pct_reviewed_known_sources: { left: 3, right: 1 } }),
    '目录偏左占比最高，75%',
  )
  assert.deepEqual(
    homeSourceTypeItems({ source_type_counts: { specialist_media: 2, unknown: 0 } }),
    [{ key: 'specialist_media', label: '专业媒体', value: 2 }],
  )
})

test('every routed Ground News page consumes API only through the feature entry', async () => {
  const pages = [
    'GroundNewsDesk.vue',
    'GroundNewsFeed.vue',
    'GroundNewsHome.vue',
    'GroundNewsSource.vue',
    'GroundNewsTimeline.vue',
  ]
  for (const page of pages) {
    const source = await readFile(new URL(`../src/views/${page}`, import.meta.url), 'utf8')
    assert.match(source, /from ['"]@\/features\/ground-news\/index\.js['"]/, page)
    assert.doesNotMatch(source, /@\/features\/ground-news\/(?!index\.js)/, page)
    assert.doesNotMatch(source, /\baxios\b|\bfetch\s*\(/, page)
    assert.doesNotMatch(source, /@\/config\/api/, page)
  }
})
