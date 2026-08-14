/**
 * 开发期静态样本数据：字段形状对齐 api/application.py 中 NewsItem / NewsListResponse / SearchResponse。
 * 仅被 scripts/mock-api-plugin.mjs 引用。
 */

const MOCK_LANGUAGE_OPTIONS = [
  { id: 1, name: '中文' },
  { id: 2, name: 'English' },
  { id: 3, name: 'Español' },
]

const MOCK_DATA_SOURCES = ['路透社', '美联社', '新华社', '财经通讯社', '本地聚合']

const MOCK_SITES = ['新闻网站', '博客', '社交媒体', '论坛']

const TOPICS = [
  '能源转型与电网投资',
  '南海局势与外交表态',
  '人工智能监管框架讨论',
  '全球供应链重组观察',
  '货币政策与通胀预期',
  '气候峰会与碳市场',
  '半导体出口管制影响',
  '中东停火斡旋进展',
]

/** 与标题轮换搭配，读起来像通讯社电讯稿 */
const TITLE_ANGLE = [
  '多方密集表态，后续政策窗口受关注',
  '市场波动收窄，机构研判分歧仍存',
  '关键数据公布后，产业链上下游联动升温',
  '国际会议前夕，主要经济体释放协调信号',
  '地缘风险溢价回落，风险偏好阶段性修复',
  '监管细则征求意见截止临近，行业酝酿调整',
  '跨境资本流动指标改善，外汇市场交投趋稳',
  '区域供应链重构提速，企业评估产能布局',
]

const ABSTRACT_OPEN = [
  '综合公开报道与机构研报，',
  '市场人士指出，',
  '据多方信源梳理，',
  '观察人士认为，',
  '从近期高频指标看，',
]

const ABSTRACT_CLOSE = [
  '后续走向仍取决于政策落地节奏与外部环境变化。',
  '投资者更关注下周关键日程与主要央行沟通措辞。',
  '相关议题预计在季度末迎来新一轮密集披露。',
  '产业链企业正评估成本与合规边界的可能调整。',
  '跨资产类别联动或放大短期波动，需警惕流动性扰动。',
]

function padNewsId(n) {
  return 9000 + n
}

function requestUrlForRow(id, sourceIndex) {
  const paths = [
    `https://www.reuters.com/world/asia-pacific/article-${id}`,
    `https://apnews.com/article/world-${id}`,
    `https://www.news.cn/world/${id}.htm`,
    `https://www.caixin.com/2026-03-${String((id % 28) + 1).padStart(2, '0')}/finance-${id}.html`,
    `https://finance.sina.com.cn/roll/2026-03-${String((id % 28) + 1).padStart(2, '0')}/doc-i${id}.shtml`,
  ]
  return paths[sourceIndex % paths.length]
}

export function buildMockNewsList() {
  const out = []
  for (let i = 0; i < 48; i++) {
    const lang = MOCK_LANGUAGE_OPTIONS[i % MOCK_LANGUAGE_OPTIONS.length]
    const topic = TOPICS[i % TOPICS.length]
    const angle = TITLE_ANGLE[i % TITLE_ANGLE.length]
    const id = padNewsId(i + 1)
    const pub = new Date(Date.now() - i * 3_600_000)
    const pubTime = pub.toISOString().slice(0, 19).replace('T', ' ')
    const sourceIndex = i % MOCK_DATA_SOURCES.length
    const source = MOCK_DATA_SOURCES[sourceIndex]
    const open = ABSTRACT_OPEN[i % ABSTRACT_OPEN.length]
    const close = ABSTRACT_CLOSE[i % ABSTRACT_CLOSE.length]
    const title = `${topic}：${angle}`
    const abstract = `${open}围绕「${topic}」的讨论在近期升温，主要经济体与行业龙头相继释放信号。${close}`
    const body = `（${source}）${topic}相关议题持续受到市场与政策制定者关注。一方面，主要指标显示预期正在缓慢修正；另一方面，结构性矛盾仍待化解。\n\n分析人士指出，短期内事件驱动因素可能放大波动，但中长期主线仍取决于基本面修复与规则体系的演进。企业端反馈显示，资本开支与用工计划更趋审慎，同时也在寻找确定性更高的区域与合作模式。\n\n从区域视角看，贸易与投融资活动呈现“总量平稳、结构分化”的特征。汇率与利率环境的变化，正在重新定价风险溢价。后续值得关注的节点包括：主要经济体数据发布、行业监管细则落地节奏，以及地缘局势对供应链的边际影响。\n\n（稿件综合公开信息整理，不构成任何投资建议。）`
    out.push({
      id,
      title,
      abstract,
      body,
      pub_time: pubTime,
      request_url: requestUrlForRow(id, sourceIndex),
      language_id: lang.id,
      created_at: null,
      source,
      location: lang.name,
      is_first_release: i % 6 === 0,
      is_favorited: false,
      is_warned: false,
      has_translation: i % 4 === 0,
      trans_title: i % 4 === 0 ? `${topic}：国际媒体综述` : null,
      trans_abstract: i % 4 === 0 ? `海外媒体普遍关注${topic}对区域产业链的潜在影响，并援引多方观点进行对照。` : null,
      trans_body: null,
      is_translated: i % 4 === 0 ? true : null,
    })
  }
  return out
}

const ALL_NEWS = buildMockNewsList()

function textHaystack(item) {
  return `${item.title || ''}\n${item.abstract || ''}\n${item.body || ''}`.toLowerCase()
}

function matchesFilters(item, params) {
  const hay = textHaystack(item)
  const exclude = String(params.need_exclude || '')
    .trim()
    .toLowerCase()
  if (exclude && hay.includes(exclude)) return false

  const must = String(params.must_include || '')
    .trim()
    .toLowerCase()
  if (must && !hay.includes(must)) return false

  const anyI = String(params.any_include || '')
    .trim()
    .toLowerCase()
  if (anyI && !hay.includes(anyI)) return false

  const topic = String(params.topic || '')
    .trim()
    .toLowerCase()
  if (topic && !hay.includes(topic)) return false

  const kw = String(params.keyword || '')
    .trim()
    .toLowerCase()
  if (kw && !hay.includes(kw)) return false

  const lang = params.language != null && params.language !== '' ? String(params.language) : ''
  if (lang && String(item.language_id) !== lang) return false

  const ds = String(params.data_source || '').trim()
  if (ds && String(item.source || '') !== ds) return false

  const site = String(params.site || '').trim()
  if (site) {
    const u = String(item.request_url || '').toLowerCase()
    if (site === '新闻网站') {
      const newsLike =
        /reuters|apnews|news\.cn|caixin|sina|bbc\.com|ft\.com|wsj\.com|nytimes/.test(u)
      if (!newsLike) return false
    }
    if (site === '博客') {
      if (!/blog|medium\.com|substack/.test(u)) return false
    }
    if (site === '社交媒体') {
      if (!/twitter|x\.com|weibo|facebook/.test(u)) return false
    }
    if (site === '论坛') {
      if (!/reddit|discourse|v2ex|tieba/.test(u)) return false
    }
  }

  return true
}

function sortNewsList(list, sortBy, sortOrder) {
  const key = sortBy === 'id' ? 'id' : 'pub_time'
  const mul = (sortOrder || 'desc').toLowerCase() === 'asc' ? 1 : -1
  return [...list].sort((a, b) => {
    if (key === 'id') return (a.id - b.id) * mul
    const ta = new Date(String(a.pub_time || 0)).getTime()
    const tb = new Date(String(b.pub_time || 0)).getTime()
    return (ta - tb) * mul
  })
}

export function paginateNews(list, page, pageSize) {
  const p = Math.max(1, Number(page) || 1)
  const s = Math.min(100, Math.max(1, Number(pageSize) || 10))
  const total = list.length
  const totalPages = s ? Math.ceil(total / s) : 0
  const slice = list.slice((p - 1) * s, p * s)
  return {
    data: slice,
    total,
    page: p,
    page_size: s,
    total_pages: totalPages,
    has_next: p * s < total,
    has_prev: p > 1,
  }
}

export function getNewsListResponse(pathWithQuery) {
  const url = new URL(pathWithQuery, 'http://localhost')
  const page = Number(url.searchParams.get('page') || 1)
  const size = Number(url.searchParams.get('size') || 10)
  const sortBy = url.searchParams.get('sort_by') || 'pub_time'
  const sortOrder = url.searchParams.get('sort_order') || 'desc'
  const sorted = sortNewsList(ALL_NEWS, sortBy, sortOrder)
  return paginateNews(sorted, page, size)
}

export function filterNewsForSearch(body) {
  const filtered = ALL_NEWS.filter((it) => matchesFilters(it, body || {}))
  const sortBy = body?.sort_by || 'pub_time'
  const sortOrder = body?.sort_order || 'desc'
  return sortNewsList(filtered, sortBy, sortOrder)
}

export function buildSearchResponse(body) {
  const page = Math.max(1, Number(body?.page) || 1)
  const pageSize = Math.min(100, Math.max(1, Number(body?.page_size) || 10))
  const list = filterNewsForSearch(body)
  const { data, total, page: p, page_size, total_pages, has_next, has_prev } = paginateNews(
    list,
    page,
    pageSize,
  )
  const mode = String(body?.mode || 'exact').toLowerCase()
  const cluster_tree =
    mode === 'cluster' ? buildClusterTreeFromSlice(data) : undefined
  const payload = {
    data,
    total,
    page: p,
    page_size,
    total_pages,
    has_next,
    has_prev,
    query_time_ms: 8 + Math.random() * 20,
  }
  if (cluster_tree !== undefined) payload.cluster_tree = cluster_tree
  return payload
}

/** 与 data-search.vue 模板一致：macro.micro_events[].news[] */
export function buildClusterTreeFromSlice(slice) {
  if (!slice.length) return []
  const half = Math.ceil(slice.length / 2)
  const a = slice.slice(0, half)
  const b = slice.slice(half)
  return [
    {
      storyline_id: 10001,
      title: '宏簇 A：政策与市场',
      micro_count: 2,
      news_count: slice.length,
      micro_events: [
        {
          event_id: 20001,
          title: '微簇 A1：政策与监管',
          news_count: a.length,
          news: a.map((n) => ({
            id: n.id,
            title: n.title,
            pub_time: n.pub_time,
            request_url: n.request_url,
          })),
        },
        {
          event_id: 20002,
          title: '微簇 A2：市场与企业',
          news_count: b.length,
          news: b.map((n) => ({
            id: n.id,
            title: n.title,
            pub_time: n.pub_time,
            request_url: n.request_url,
          })),
        },
      ],
    },
    {
      storyline_id: 10002,
      title: '宏簇 B：国际与舆情',
      micro_count: 1,
      news_count: Math.min(3, slice.length),
      micro_events: [
        {
          event_id: 20003,
          title: '微簇 B1：热点追踪',
          news_count: Math.min(3, slice.length),
          news: slice.slice(0, 3).map((n) => ({
            id: n.id,
            title: n.title,
            pub_time: n.pub_time,
            request_url: n.request_url,
          })),
        },
      ],
    },
  ]
}

export function getNewsById(id) {
  const n = Number(id)
  return ALL_NEWS.find((x) => x.id === n) || null
}

export function getNewsByIdsParam(idsParam) {
  const ids = String(idsParam || '')
    .split(',')
    .map((x) => Number(x.trim()))
    .filter((x) => Number.isFinite(x))
  const uniq = [...new Set(ids)].slice(0, 500)
  const map = new Map(ALL_NEWS.map((x) => [x.id, x]))
  return uniq.map((id) => map.get(id)).filter(Boolean)
}

export function getSearchOptionsPayload() {
  return {
    language_options: MOCK_LANGUAGE_OPTIONS,
    data_sources: MOCK_DATA_SOURCES,
    sites: MOCK_SITES,
  }
}

export function getStatsPayload() {
  const langCounts = new Map()
  for (const n of ALL_NEWS) {
    const k = n.language_id ?? '未知'
    langCounts.set(k, (langCounts.get(k) || 0) + 1)
  }
  const language_stats = MOCK_LANGUAGE_OPTIONS.map((l) => ({
    id: l.id,
    count: langCounts.get(l.id) || 0,
    name: l.name,
  }))
  return {
    total_news: ALL_NEWS.length,
    total_languages: language_stats.length,
    language_stats,
  }
}
