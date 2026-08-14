<template>
  <main class="ground-feed">
    <header class="feed-hero">
      <div>
        <RouterLink class="back-link" to="/data-service/ground-news">Ground News 首页</RouterLink>
        <span class="eyebrow">{{ eyebrow }}</span>
        <h1>{{ title }}</h1>
        <p>{{ subtitle }}</p>
      </div>
    </header>

    <section v-if="loading" class="feed-state">正在加载...</section>
    <section v-else-if="error" class="feed-state error">{{ error }}</section>

    <template v-else>
      <section v-if="mode === 'search'" class="search-results">
        <article class="result-block">
          <div class="block-head">
            <span>Stories</span>
            <small>{{ searchData.stories?.length || 0 }}</small>
          </div>
          <div class="story-list-grid">
            <StoryCard v-for="story in searchData.stories || []" :key="story.cluster_id" :story="story" />
          </div>
        </article>
        <article class="result-block">
          <div class="block-head">
            <span>Sources</span>
            <small>{{ searchSources.length }}</small>
          </div>
          <div class="source-grid">
            <RouterLink
              v-for="source in searchSources"
              :key="source.domain"
              class="source-card"
              :to="sourcePath(source.domain)"
            >
              <strong>{{ source.sourceName }}</strong>
              <span>{{ source.domain }}</span>
              <small>{{ source.politicalLabel }} · {{ source.credibilityLabel }}</small>
            </RouterLink>
          </div>
        </article>
        <article class="result-block">
          <div class="block-head">
            <span>Timelines</span>
            <small>{{ searchData.timelines?.length || 0 }}</small>
          </div>
          <div class="timeline-grid">
            <RouterLink
              v-for="chain in searchData.timelines || []"
              :key="chain.chain_id"
              class="timeline-card"
              :to="timelinePath(chain.chain_id)"
            >
              <strong>{{ chain.title || chain.chain_id }}</strong>
              <small>{{ chain.segment_count }} 节点 · {{ chain.article_count }} 新闻 · 质量指标未知</small>
            </RouterLink>
          </div>
        </article>
      </section>

      <section v-else class="feed-layout">
        <aside class="feed-rail">
          <article>
            <span>结果</span>
            <strong>{{ formatNumber(total) }}</strong>
            <small>{{ mode === 'blindspot' ? '服务端候选顺序（排序方法未批准）' : topicLabel }}</small>
          </article>
          <details v-if="mode === 'blindspot'" class="metric-explanation">
            <summary>展开 Blindspot 公式、输入和证据</summary>
            <strong>{{ blindspotMetric.valueLabel }}</strong>
            <p>方法：{{ blindspotExplanation.method_version || '未建立' }}</p>
            <p>公式：{{ blindspotExplanation.formula }}</p>
            <p>证据定位：{{ blindspotExplanation.evidence.locator || '不可用' }}；{{ blindspotExplanation.reason_code }}</p>
          </details>
          <details class="metric-explanation">
            <summary>展开时间线质量公式、输入和证据</summary>
            <strong>{{ timelineQualityExplanation.valueLabel }}</strong>
            <p>方法：{{ timelineQualityExplanation.method_version || '未建立' }}</p>
            <p>公式：{{ timelineQualityExplanation.formula }}</p>
            <p>证据定位：{{ timelineQualityExplanation.evidence.locator || '不可用' }}；{{ timelineQualityExplanation.reason_code }}</p>
          </details>
        </aside>
        <section class="story-list-grid">
          <StoryCard v-for="story in items" :key="story.cluster_id" :story="story" :show-blindspot="mode === 'blindspot'" />
        </section>
      </section>
    </template>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute, RouterLink } from 'vue-router'
import { buildGroundNewsSourceProfileModel, groundNewsApi } from '@/features/ground-news/index.js'
import {
  graphMetricExplanation,
  graphMetricPresentation,
} from '@/governance/graphMetrics.js'
const route = useRoute()

const loading = ref(false)
const error = ref('')
const data = ref({})

const mode = computed(() => {
  if (route.name === 'GroundNewsTopic') return 'topic'
  return 'blindspot'
})

const topicLabel = computed(() => String(route.params.topic || 'global'))
const title = computed(() => {
  if (mode.value === 'topic') return `${familyLabel(topicLabel.value)} 话题`
  return '报道盲区'
})
const eyebrow = computed(() => (mode.value === 'blindspot' ? 'Blindspot Feed' : 'Topic Feed'))
const subtitle = computed(() => {
  if (mode.value === 'blindspot') return '按左右覆盖缺口、来源数量和未评级比例排序；第三方目录标签仅作目录构成展示，不代表事实准确率或来源可靠性。'
  if (mode.value === 'topic') return '同一主题下的 L1 事件卡，保留信源覆盖和 Bias 分布。'
  return ''
})

const items = computed(() => data.value.items || [])
const total = computed(() => Number(data.value.total || 0))
const formula = computed(() => data.value.formula || null)
const searchData = computed(() => data.value || {})
const blindspotMetric = computed(() => graphMetricPresentation('ground_news.blindspot_score', {
  value: items.value[0]?.blindspot_score ?? items.value[0]?.blindspot?.score,
  method_card: formula.value,
}))
const blindspotExplanation = computed(() => graphMetricExplanation('ground_news.blindspot_score', {
  inputs: {
    source_count: items.value[0]?.source_count,
    reviewed_known_source_count: items.value[0]?.blindspot?.reviewed_known_source_count,
    unknown_source_count: items.value[0]?.blindspot?.unknown_source_count,
  },
}))
const timelineQualityExplanation = computed(() => graphMetricExplanation('ground_news.timeline_quality', {
  inputs: {
    quality_score: searchData.value.timelines?.[0]?.quality_score,
    chain_quality: searchData.value.timelines?.[0]?.chain_quality,
  },
}))
const searchSources = computed(() => {
  const sources = Array.isArray(searchData.value.sources)
    ? searchData.value.sources.slice(0, 80)
    : []
  return sources.map((source) => {
    const model = buildGroundNewsSourceProfileModel(source)
    return {
      domain: model.domain,
      sourceName: model.sourceName,
      politicalLabel: model.labels.politicalLeaning,
      credibilityLabel: model.labels.credibility,
    }
  })
})

const StoryCard = defineComponent({
  name: 'StoryCard',
  props: {
    story: { type: Object, required: true },
    showBlindspot: { type: Boolean, default: false },
  },
  setup(props) {
    return () => h(RouterLink, { class: 'story-card', to: storyPath(props.story) }, () => [
      h('div', { class: 'story-card__meta' }, [
        h('span', familyLabel(props.story.event_family)),
        h('span', formatRange(props.story.start_date, props.story.end_date)),
      ]),
      h('h2', props.story.display_title || props.story.canonical_title || props.story.title || props.story.cluster_id),
      h('p', entityLine(props.story)),
      h('div', { class: 'bias-bar' }, biasBuckets(props.story).map((bucket) => h('span', {
        key: bucket.key,
        style: { width: `${Math.max(bucket.value, bucket.value > 0 ? 3 : 0)}%`, background: bucket.color },
      }))),
      h('div', { class: 'story-card__foot' }, [
        h('span', `${props.story.article_count || 0} 新闻`),
        h('span', `${props.story.source_count || 0} 信源`),
        props.showBlindspot ? h('strong', `Blindspot ${graphMetricPresentation('ground_news.blindspot_score', {
          value: props.story.blindspot_score ?? props.story.blindspot?.score,
        }).valueLabel}`) : null,
      ]),
      props.showBlindspot
        ? h('small', { class: 'blind-reason' }, '证据定位不可用；不代表事实准确率、来源可靠性或风险事实')
        : null,
    ])
  },
})

watch(() => route.fullPath, () => {
  void load()
})

onMounted(() => {
  void load()
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'blindspot') {
      data.value = await groundNewsApi.getBlindspots({ pageSize: 80 })
    } else if (mode.value === 'topic') {
      data.value = await groundNewsApi.getTopic(topicLabel.value, { pageSize: 80 })
    } else {
      data.value = { stories: [], sources: [], timelines: [] }
    }
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function storyPath(story) {
  return { path: '/data-service/ground-news-desk', query: { cluster_id: story.cluster_id } }
}

function sourcePath(domain) {
  return `/data-service/ground-news-source/${encodeURIComponent(domain || '')}`
}

function timelinePath(chainId) {
  return `/data-service/ground-news-timeline/${encodeURIComponent(chainId || '')}`
}

const biasModel = [
  { key: 'left', color: '#2f7dd1' },
  { key: 'center', color: '#2ca66f' },
  { key: 'right', color: '#d77b2a' },
  { key: 'state_aligned', color: '#7a6a55' },
  { key: 'unknown', color: '#aab3bd' },
]

function biasBuckets(story) {
  const raw = story?.political_group_pct_reviewed_known_sources || {}
  const rows = biasModel.map((bucket) => ({ ...bucket, value: Number(raw[bucket.key] || 0) }))
  const total = rows.reduce((sum, row) => sum + row.value, 0)
  if (!total) return rows.map((row) => ({ ...row, value: row.key === 'unknown' ? 100 : 0 }))
  return rows.map((row) => ({ ...row, value: (row.value / total) * 100 }))
}

function entityLine(story) {
  return `${story?.initiator || '未知主体'} -> ${story?.target || '未知对象'}`
}

function formatRange(start, end) {
  if (!start && !end) return '无日期'
  const left = String(start || end).slice(0, 10)
  const right = String(end || start).slice(0, 10)
  return left === right ? left : `${left} -> ${right}`
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

function familyLabel(value) {
  const labels = {
    diplomacy: '外交',
    military_security: '安全',
    economic_trade: '经贸',
    technology_industry: '科技产业',
    domestic_politics: '政治',
    law_policy: '法律政策',
    human_rights_migration: '人权迁徙',
    public_development: '公共发展',
    disaster_environment: '灾害环境',
  }
  return labels[value] || String(value || '全球').replaceAll('_', ' / ')
}

</script>

<style scoped>
.ground-feed {
  --ink: #121a21;
  --muted: #697682;
  --line: rgba(28, 42, 52, 0.15);
  --paper: #fffaf0;
  --navy: #102234;
  --green: #12624d;
  --serif: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", Georgia, serif;
  --sans: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  min-height: 100vh;
  padding: 92px 22px 40px;
  background: linear-gradient(115deg, #fffaf0, #eef5f0 62%, #f4efe4);
  color: var(--ink);
  font-family: var(--sans);
}

.feed-hero {
  max-width: 1440px;
  margin: 0 auto 18px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
  gap: 18px;
  align-items: end;
}

.back-link,
.eyebrow,
.block-head,
.feed-rail span {
  color: #8b6a38;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  text-decoration: none;
}

.feed-hero h1 {
  margin: 8px 0 8px;
  font-family: var(--serif);
  font-size: clamp(42px, 6vw, 86px);
  line-height: 0.95;
  letter-spacing: -0.045em;
}

.feed-hero p {
  max-width: 760px;
  margin: 0;
  color: #4c5c68;
  line-height: 1.65;
}

.feed-state,
.feed-layout,
.search-results {
  max-width: 1440px;
  margin: 0 auto;
}

.feed-state {
  border: 1px dashed var(--line);
  border-radius: 24px;
  padding: 28px;
  background: rgba(255, 255, 255, 0.54);
  color: var(--muted);
}

.feed-state.error {
  color: #a83c31;
}

.feed-layout {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.feed-rail {
  display: grid;
  gap: 12px;
}

.feed-rail article,
.result-block,
.story-card,
.source-card,
.timeline-card {
  border: 1px solid var(--line);
  border-radius: 24px;
  background: rgba(255, 250, 240, 0.86);
  box-shadow: 0 18px 44px rgba(64, 78, 88, 0.1);
}

.feed-rail article {
  padding: 16px;
}

.feed-rail strong {
  display: block;
  margin: 6px 0;
  font-family: var(--serif);
  font-size: 36px;
}

.feed-rail small {
  color: var(--muted);
}

.story-list-grid,
.source-grid,
.timeline-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.story-card,
.source-card,
.timeline-card {
  color: inherit;
  display: grid;
  gap: 10px;
  padding: 16px;
  text-decoration: none;
}

.story-card:hover,
.source-card:hover,
.timeline-card:hover {
  border-color: rgba(18, 98, 77, 0.38);
  background: #fffdf6;
}

.story-card__meta,
.story-card__foot {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
}

.story-card h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 22px;
  line-height: 1.15;
}

.story-card p,
.story-card small,
.source-card span,
.source-card small,
.timeline-card small {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.bias-bar {
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  display: flex;
  background: #d9dee2;
}

.bias-bar span {
  min-width: 2px;
}

.blind-reason {
  border-top: 1px solid rgba(18, 34, 52, 0.1);
  padding-top: 9px;
}

.search-results {
  display: grid;
  gap: 14px;
}

.result-block {
  padding: 16px;
}

.block-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}

.source-card strong,
.timeline-card strong {
  font-size: 16px;
  line-height: 1.3;
}

@media (max-width: 900px) {
  .ground-feed {
    padding: 76px 12px 24px;
  }

  .feed-hero,
  .feed-layout {
    grid-template-columns: 1fr;
  }
}

.ground-feed .back-link,
.ground-feed .story-card,
.ground-feed .source-card,
.ground-feed .timeline-card {
  box-sizing: border-box;
  min-height: 44px;
  touch-action: manipulation;
}

.ground-feed .back-link {
  display: inline-flex;
  align-items: center;
}
</style>
