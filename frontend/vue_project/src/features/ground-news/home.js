import { normalizeBiasBuckets } from './presentation.js'

const FRONT_SECTION_KEYS = new Set(['latest', 'pulse_72h', 'week_watch'])

const HOME_FAMILY_LABELS = Object.freeze({
  civil_unrest: '社会冲突',
  diplomacy: '外交',
  disaster_environment: '环境灾害',
  domestic_politics: '政治',
  economic_trade: '经贸',
  human_rights_migration: '人权迁徙',
  law_policy: '法律政策',
  military_security: '安全',
  public_development: '公共发展',
  security_crime: '安全犯罪',
  technology_industry: '科技产业',
})

const HOME_SOURCE_TYPE_LABELS = Object.freeze({
  business_media: '商业财经',
  global_major_media: '全球主流',
  local_media: '本地媒体',
  national_major_media: '全国主流',
  public_broadcaster: '公共广播',
  regional_major_media: '区域主流',
  specialist_media: '专业媒体',
  state_media: '国家媒体',
  unknown: '未分类',
  wire_service: '通讯社',
})

const HOME_BIAS_LABELS = Object.freeze({
  center: '目录中间',
  left: '目录偏左',
  right: '目录偏右',
  state_aligned: '目录国家关联',
  unknown: '目录未评级',
})

function storyDate(story) {
  return String(story?.end_date || story?.start_date || '')
}

function storyRecencyRank(story) {
  const value = storyDate(story)
  const time = value ? Date.parse(value) : 0
  return Number.isFinite(time) ? time : 0
}

function storyCoverageRank(story) {
  return Number(story?.source_count || 0) * 1000
    + Number(story?.article_count || 0) * 10
}

function sortStoriesByRecency(stories) {
  return [...stories].sort((left, right) => (
    storyRecencyRank(right) - storyRecencyRank(left)
    || storyCoverageRank(right) - storyCoverageRank(left)
    || String(left?.cluster_id || '').localeCompare(String(right?.cluster_id || ''))
  ))
}

function uniqueStories(rows) {
  const seen = new Set()
  return rows.filter((story) => {
    const clusterId = story?.cluster_id
    if (!clusterId || seen.has(clusterId)) return false
    seen.add(clusterId)
    return true
  })
}

export function buildGroundNewsHomeModel(payload) {
  const value = payload && typeof payload === 'object' ? payload : {}
  const leadStory = value.lead_story || null
  const metrics = value.metrics || {}
  const edition = value.edition || {}
  const visibleSections = (Array.isArray(value.sections) ? value.sections : [])
    .filter((section) => section?.stories?.length)
  const sectionByKey = Object.fromEntries(
    visibleSections.map((section) => [section.key, section]),
  )
  const storySections = visibleSections.filter(
    (section) => !FRONT_SECTION_KEYS.has(section.key),
  )
  const l2Watchlist = Array.isArray(value.l2_watchlist) ? value.l2_watchlist : []
  const allHomeStories = uniqueStories([
    ...(leadStory ? [leadStory] : []),
    ...visibleSections.flatMap((section) => section.stories || []),
  ])
  const latestStories = selectLatestStories(allHomeStories)
  const weekStories = (
    sectionByKey.week_watch?.stories?.length
      ? sectionByKey.week_watch.stories
      : sectionByKey.pulse_72h?.stories?.length
        ? sectionByKey.pulse_72h.stories
        : latestStories
  )
  const frontLatestStories = latestStories.slice(0, 6)

  return {
    allHomeStories,
    blindspotStories: selectBlindspotStories(sectionByKey, allHomeStories),
    edition,
    frontChainItems: l2Watchlist.slice(0, 6),
    frontLatestStories,
    frontWeekStories: selectFrontWeekStories(
      weekStories,
      frontLatestStories,
      allHomeStories,
    ),
    l2Watchlist,
    leadStory,
    latestStories,
    metrics,
    profileCoveragePct: homeProfileCoveragePct(metrics),
    sectionByKey,
    sectionNavItems: [
      { id: 'front-news', title: '头条', count: null },
      ...(leadStory ? [{ id: 'lead-story', title: '主线', count: null }] : []),
      ...storySections.map((section) => ({
        id: `section-${section.key}`,
        title: section.title,
        count: section.stories?.length || 0,
      })),
    ],
    sourceLeaders: buildHomeSourceLeaders(allHomeStories),
    storySections,
    topicChips: buildHomeTopicChips(allHomeStories),
    visibleSections,
  }
}

export function homeProfileCoveragePct(metrics) {
  const coverage = metrics?.source_profile_coverage
  if (!coverage || typeof coverage !== 'object') return '待核验'
  const rawTotal = coverage.total_profiles
  const rawKnown = coverage.known_bias_profiles
  if (rawTotal === null || rawTotal === undefined || rawTotal === '') return '待核验'
  if (rawKnown === null || rawKnown === undefined || rawKnown === '') return '待核验'
  const total = Number(rawTotal)
  const known = Number(rawKnown)
  if (!Number.isFinite(total) || !Number.isFinite(known)) return '待核验'
  if (total === 0) return '不可计算'
  if (total < 0 || known < 0 || known > total) return '待核验'
  return `${Math.round((known / total) * 100)}%`
}

export function selectLatestStories(allHomeStories) {
  return sortStoriesByRecency(uniqueStories(allHomeStories || []))
}

export function selectFrontWeekStories(
  weekStories,
  frontLatestStories,
  allHomeStories,
  targetSize = 6,
) {
  const selected = []
  const seen = new Set(
    frontLatestStories.map((story) => story?.cluster_id).filter(Boolean),
  )
  const addStory = (story) => {
    const clusterId = story?.cluster_id
    if (!clusterId || seen.has(clusterId)) return
    selected.push(story)
    seen.add(clusterId)
  }
  weekStories.forEach(addStory)
  if (selected.length < targetSize) {
    [...allHomeStories]
      .filter(
        (story) => Number(story?.source_count || 0) >= 2
          && Number(story?.article_count || 0) >= 2,
      )
      .sort((left, right) => (
        storyRecencyRank(right) - storyRecencyRank(left)
        || Number(right.article_count || 0) - Number(left.article_count || 0)
      ))
      .forEach(addStory)
  }
  if (!selected.length) {
    weekStories.slice(0, targetSize).forEach((story) => {
      if (story?.cluster_id) selected.push(story)
    })
  }
  return selected.slice(0, targetSize)
}

export function selectBlindspotStories(sectionByKey, allHomeStories) {
  const blindspot = sectionByKey.blindspot
  if (blindspot?.stories?.length) return blindspot.stories
  return sortStoriesByRecency(allHomeStories)
}

export function buildHomeTopicChips(stories) {
  const topics = new Map()
  for (const story of stories) {
    const key = story.event_family || 'other'
    const item = topics.get(key) || {
      key,
      label: homeFamilyLabel(key),
      count: 0,
      articles: 0,
      story,
    }
    item.count += 1
    item.articles += Number(story.article_count || 0)
    if (
      storyRecencyRank(story) > storyRecencyRank(item.story)
      || (
        storyRecencyRank(story) === storyRecencyRank(item.story)
        && String(story.cluster_id || '').localeCompare(String(item.story?.cluster_id || '')) < 0
      )
    ) {
      item.story = story
    }
    topics.set(key, item)
  }
  return [...topics.values()].sort(
    (left, right) => right.articles - left.articles || right.count - left.count,
  )
}

export function buildHomeSourceLeaders(stories) {
  const counts = new Map()
  for (const story of stories) {
    for (const source of homeSourceNames(story)) {
      counts.set(source, (counts.get(source) || 0) + 1)
    }
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))
}

export function homeStoryPath(story) {
  return {
    path: '/data-service/ground-news-desk',
    query: { cluster_id: story.cluster_id },
  }
}

export function homeTopicRouteKey(topic, story = null) {
  const key = String(topic || '').trim()
  if (!key) return 'global'
  const family = String(story?.event_family || '').trim()
  if (key === 'economy_tech') {
    return [
      'economic_trade',
      'technology_industry',
      'law_policy',
      'public_development',
    ].includes(family)
      ? family
      : 'economic_trade'
  }
  if (
    key === 'security'
    && ['military_security', 'security_crime', 'civil_unrest', 'disaster_environment']
      .includes(family)
  ) {
    return family
  }
  return key
}

export function homeTopicPath(topic, story = null) {
  return `/data-service/ground-news-topic/${encodeURIComponent(homeTopicRouteKey(topic, story))}`
}

export function homeTimelinePath(chain) {
  return `/data-service/ground-news-timeline/${encodeURIComponent(chain?.chain_id || '')}`
}

export function homeEditionLabel(sectionByKey, edition, key, freshness = null) {
  const section = sectionByKey[key]
  const historical = freshness?.historical !== false
  const rotationFallback = historical ? '按历史快照排序' : '按更新批次滚动'
  if (!section) return rotationFallback
  if (key === 'latest' && section.requires_image) {
    return `重要事件 · 必须配图 · ${rotationFallback}`
  }
  const minArticles = Number(section.min_articles || 0)
  const rotationText = section.rotation === '3d'
    ? `${edition.rotation_days || 3} 天轮换`
    : rotationFallback
  if (key === 'latest' && minArticles > 1) return `${minArticles} 条起 · ${rotationText}`
  const windowText = section.window_days ? `${section.window_days} 天窗口` : '全局窗口'
  return minArticles > 1
    ? `${windowText} · ${minArticles} 条起 · ${rotationText}`
    : `${windowText} · ${rotationText}`
}

export function homeSectionBadge(section, sectionByKey, edition, freshness = null) {
  return `${homeEditionLabel(sectionByKey, edition, section?.key, freshness)} · ${section?.stories?.length || 0} 张卡片`
}

export function homeBiasBuckets(story) {
  return normalizeBiasBuckets(
    story?.political_group_pct_reviewed_known_sources || {},
  ).map((bucket) => ({ ...bucket, label: HOME_BIAS_LABELS[bucket.key] }))
}

export function homeVisibleBiasBuckets(story) {
  const rows = homeBiasBuckets(story)
  const visible = rows.filter((bucket) => bucket.value > 0.2)
  return visible.length ? visible : rows.filter((bucket) => bucket.key === 'unknown')
}

export function homeBiasSummary(story) {
  const rows = homeVisibleBiasBuckets(story).filter((bucket) => bucket.key !== 'unknown')
  if (!rows.length) return '第三方目录分组未知'
  const top = [...rows].sort((left, right) => right.value - left.value)[0]
  return `${top.label}占比最高，${homeFormatPct(top.value)}%`
}

export function homeCodeLabel(value, fallback = '未知') {
  return value ? String(value).replaceAll('_', ' / ') : fallback
}

export function homeFamilyLabel(value) {
  return HOME_FAMILY_LABELS[value] || homeCodeLabel(value, '全球')
}

export function homeCoverShort(story) {
  const value = story?.location
    || story?.initiator
    || story?.target
    || homeFamilyLabel(story?.event_family)
  return String(value || 'Global').slice(0, 18)
}

export function homeHasStoryImage(story) {
  return Boolean(story?.cover?.image_url || story?.cover_url)
}

export function homeEntityLine(story) {
  return `${story?.initiator || '未知主体'} -> ${story?.target || '未知对象'}`
}

export function homeSourceNames(story) {
  return (Array.isArray(story?.source_names) ? story.source_names : []).filter(Boolean)
}

export function homeSourceTypeItems(story) {
  return Object.entries(story?.source_type_counts || {})
    .map(([key, value]) => ({
      key,
      label: HOME_SOURCE_TYPE_LABELS[key] || homeCodeLabel(key, '未分类'),
      value: Number(value || 0),
    }))
    .filter((item) => item.value > 0)
    .sort((left, right) => right.value - left.value)
}

export function homeSampleTitle(story) {
  const sample = story?.sample_news?.find((item) => item?.title)
  return sample?.title ? String(sample.title).trim() : ''
}

export function homeSampleNews(story, limit = 3) {
  const rows = (Array.isArray(story?.sample_news) ? story.sample_news : [])
    .filter((item) => item?.title)
    .slice(0, limit)
  if (rows.length) return rows
  return [{
    news_id: `${story?.cluster_id || 'story'}-fallback`,
    title: story?.display_title || '进入详情页查看该事件的完整报道样本',
    source_name: homeSourceNames(story)[0] || '事件详情',
  }]
}

export function homeQualityLabel(value) {
  void value
  return '链质量未知'
}

export function homeFormatDate(value) {
  return value ? String(value).slice(0, 10) : '暂无'
}

export function homeFormatRange(start, end) {
  const left = homeFormatDate(start)
  const right = homeFormatDate(end)
  return left === right ? left : `${left} -> ${right}`
}

export function homeFormatNumber(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') {
    return '待核验'
  }
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '待核验'
}

export function homeFormatPct(value) {
  const number = Number(value || 0)
  return number.toFixed(number >= 10 ? 0 : 1)
}
