<template>
  <div class="ground-home" :style="{ '--atlas-image': `url(${atlasImage})` }">
    <nav class="ground-subnav" aria-label="全球新闻观察台次级导航">
      <span>Ground News</span>
      <RouterLink to="/data-service/ground-news">事件总览</RouterLink>
      <RouterLink to="/data-service/ground-news-desk">分析工作台</RouterLink>
    </nav>

    <section class="home-hero" data-tour="ground-news-overview">
      <div class="home-hero__copy">
        <span class="eyebrow">GlobeMind 事件首页</span>
        <h1>国际焦点新闻</h1>
        <p>
          {{ historicalMode ? '按数据截止时间呈现已收录历史新闻' : '按事件时间优先呈现已收录新闻' }}，保留来源目录、报道切面和关联线索。
        </p>
        <div class="hero-actions">
          <RouterLink class="primary-link" to="/data-service/ground-news-desk">进入分析工作台</RouterLink>
          <button type="button" class="ghost-link" :disabled="loading" @click="loadHome">
            {{ loading ? '刷新中' : '刷新事件流' }}
          </button>
        </div>
      </div>

      <div class="home-hero__metrics" role="group" aria-label="Ground News 当前快照指标">
        <article>
          <strong>{{ formatNumber(metrics.total_stories) }}</strong>
          <span>故事卡</span>
        </article>
        <article>
          <strong>{{ formatNumber(metrics.total_articles) }}</strong>
          <span>聚合新闻</span>
        </article>
        <article>
          <strong>{{ groundCutoffDate }}</strong>
          <span>{{ historicalMode ? '快照截止' : '数据截止' }}</span>
        </article>
      </div>
    </section>

    <section
      class="home-freshness"
      :class="`is-${groundNewsFreshness.state}`"
      role="status"
      aria-live="polite"
    >
      <strong>{{ freshnessStateLabel(groundNewsFreshness.state) }}</strong>
      <span>{{ groundFreshnessMessage }}</span>
      <span v-if="groundNewsFreshness.lagHours !== null">
        延迟 {{ groundNewsFreshness.lagHours }} 小时 · 时限 {{ groundNewsFreshness.slaHours }} 小时
      </span>
      <span>
        近 7 日目录处理状态 {{ usableStories7dLabel }}
      </span>
    </section>

    <nav v-if="sectionNavItems.length" class="section-nav" aria-label="新闻栏目">
      <button
        v-for="item in sectionNavItems"
        :key="`nav-${item.id}`"
        type="button"
        @click="scrollToSection(item.id)"
      >
        <span>{{ item.title }}</span>
        <small v-if="item.count !== null">{{ item.count }}</small>
      </button>
    </nav>

    <section v-if="loading && !leadStory" class="home-loading">
      <div class="pulse-cover"></div>
      <p>正在加载首页事件流...</p>
    </section>

    <section v-else-if="error" class="home-error">
      <strong>首页事件流加载失败</strong>
      <p>{{ error }}</p>
      <button type="button" @click="loadHome">重新加载</button>
    </section>

    <main v-else class="home-layout">
      <section id="front-news" class="front-grid" aria-label="新闻首页版面">
        <article class="front-column front-column--latest" data-tour="ground-news-edition">
          <header>
            <span class="eyebrow">{{ historicalMode ? 'Archive Wire' : 'Update Wire' }}</span>
            <h2>{{ historicalMode ? '历史快讯' : '最新快讯' }}</h2>
            <small>{{ editionLabel('latest') }}</small>
          </header>
          <div class="front-column__list">
            <RouterLink
              v-for="(story, index) in frontLatestStories"
              :key="`front-latest-${story.cluster_id}`"
              :to="storyPath(story)"
              :class="['front-headline', { 'front-headline--lead': index === 0 }]"
            >
              <EventCover v-if="index === 0" :story="story" size="strip" />
              <span>{{ formatDate(story.end_date || story.start_date) }} · {{ familyLabel(story.event_family) }}</span>
              <strong>{{ story.display_title }}</strong>
              <small>{{ story.article_count }} 条新闻 · {{ story.source_count }} 信源 · {{ storyLanguageLabel(story) }}</small>
            </RouterLink>
          </div>
        </article>

        <article class="front-column front-column--week">
          <header>
            <span class="eyebrow">{{ historicalMode ? 'Archive Window' : 'Rolling Window' }}</span>
            <h2>{{ historicalMode ? '历史多源' : '近期多源' }}</h2>
            <small>{{ editionLabel('week_watch') }}</small>
          </header>
          <div class="front-column__list">
            <RouterLink
              v-for="story in frontWeekStories"
              :key="`front-week-${story.cluster_id}`"
              :to="storyPath(story)"
              class="front-headline front-headline--compact"
            >
              <span>{{ formatRange(story.start_date, story.end_date) }}</span>
              <strong>{{ story.display_title }}</strong>
              <small>{{ story.article_count }} 条新闻 · {{ story.source_count }} 信源 · {{ storyLanguageLabel(story) }} · {{ biasSummary(story) }}</small>
            </RouterLink>
          </div>
        </article>

        <article class="front-column front-column--chains">
          <header>
            <span class="eyebrow">Related Threads</span>
            <h2>关联线索</h2>
            <small>近期优先</small>
          </header>
          <details class="metric-explanation">
            <summary>展开链质量公式、输入和证据</summary>
            <p>方法：{{ chainQualityExplanation.method_version || '未建立' }}</p>
            <p>公式：{{ chainQualityExplanation.formula }}</p>
            <p>证据定位：{{ chainQualityExplanation.evidence.locator || '不可用' }}；{{ chainQualityExplanation.reason_code }}</p>
          </details>
          <div class="front-column__list">
            <RouterLink
              v-for="chain in frontChainItems"
              :key="`front-chain-${chain.chain_id}`"
              :to="timelinePath(chain)"
              class="front-headline front-headline--compact"
            >
              <span>{{ formatDate(chain.end_date || chain.start_date) }} · {{ qualityLabel(chain.chain_quality) }}</span>
              <strong>{{ chainTitle(chain) }}</strong>
              <small>{{ chain.segment_count }} 个切面 · {{ chain.article_count }} 条新闻</small>
            </RouterLink>
          </div>
        </article>
      </section>

      <section v-if="leadStory" id="lead-story" class="lead-feature">
        <div class="lead-media">
          <RouterLink class="lead-cover" :to="storyPath(leadStory)">
            <EventCover :story="leadStory" size="large" />
          </RouterLink>
          <div class="lead-brief">
            <div class="lead-brief__head">
              <span>主要报道样本</span>
              <strong>{{ leadStory.cover?.credit || sourceNames(leadStory)[0] || '来源待核查' }}</strong>
            </div>
            <RouterLink
              v-for="item in sampleNews(leadStory, 3)"
              :key="item.news_id || item.title"
              :to="storyPath(leadStory)"
              class="lead-brief__item"
            >
              <span>{{ item.source_name || item.domain || '新闻来源' }}</span>
              <strong>{{ item.title }}</strong>
            </RouterLink>
          </div>
        </div>
        <article class="lead-story">
          <div class="story-kicker">
            <span>{{ familyLabel(leadStory.event_family) }}</span>
            <span>{{ formatRange(leadStory.start_date, leadStory.end_date) }}</span>
            <span>{{ leadStory.cover?.label || '编辑封面' }}</span>
          </div>
          <RouterLink :to="storyPath(leadStory)" class="lead-title">
            {{ leadStory.display_title }}
          </RouterLink>
          <p class="lead-entity">
            {{ displayEntityLine(leadStory) }}
            <span v-if="leadStory.location"> · {{ leadStory.location }}</span>
            <span> · {{ storyLanguageLabel(leadStory) }}</span>
          </p>
          <div class="lead-stats">
            <span><strong>{{ leadStory.article_count }}</strong> 条新闻</span>
            <span><strong>{{ leadStory.source_count }}</strong> 个信源</span>
            <span><strong>{{ leadStory.l2_chain_count }}</strong> 条关联线索</span>
            <span><strong>{{ rankMetric.valueLabel }}</strong> 候选排序分</span>
          </div>
          <details class="metric-explanation">
            <summary>展开候选排序公式、输入和证据</summary>
            <p>方法：{{ rankMetricExplanation.method_version || '未建立' }}</p>
            <p>公式：{{ rankMetricExplanation.formula }}</p>
            <p>证据定位：{{ rankMetricExplanation.evidence.locator || '不可用' }}；{{ rankMetricExplanation.reason_code }}</p>
            <ul>
              <li v-for="input in rankMetricExplanation.inputs" :key="input.field">
                <code>{{ input.field }}</code>：{{ input.state === 'provided_unverified' ? `已提供但未核验（${input.value}）` : '不可用' }}
              </li>
            </ul>
          </details>
          <div class="coverage-panel">
            <div class="coverage-panel__head">
              <strong>第三方目录构成</strong>
              <span>{{ biasSummary(leadStory) }}</span>
            </div>
            <p class="coverage-panel__note">仅描述已收录来源的目录标签，不代表事实准确率、叙事立场或地区民意。</p>
            <BiasBar :story="leadStory" />
            <div class="bias-chips">
              <span
                v-for="bucket in visibleBiasBuckets(leadStory)"
                :key="bucket.key"
                :style="{ '--chip-color': bucket.color }"
              >
                {{ bucket.label }} {{ formatPct(bucket.value) }}%
              </span>
            </div>
          </div>
          <div class="source-type-strip">
            <span v-for="item in sourceTypeItems(leadStory).slice(0, 5)" :key="item.key">
              {{ item.label }} <strong>{{ item.value }}</strong>
            </span>
          </div>
          <div class="source-strip">
            <span v-for="source in sourceNames(leadStory).slice(0, 4)" :key="source">{{ source }}</span>
          </div>
          <RouterLink class="story-link" :to="storyPath(leadStory)">打开完整报道分析</RouterLink>
        </article>
      </section>

      <aside class="right-rail">
        <section class="rail-card daily-brief">
          <span class="eyebrow">{{ historicalMode ? '历史快照' : '数据简报' }}</span>
          <h2>事件池状态</h2>
          <div class="brief-grid">
            <span><strong>{{ formatNumber(metrics.total_stories) }}</strong> 事件</span>
            <span><strong>{{ formatNumber(metrics.total_articles) }}</strong> 新闻</span>
            <span><strong>{{ formatNumber(metrics.product_candidate_count ?? metrics.candidate_count) }}</strong> 主候选</span>
            <span><strong>未知</strong> Ready 指标</span>
            <span><strong>未知</strong> 7日可用指标</span>
            <span><strong>{{ formatDate(metrics.latest_valid_story_date || metrics.latest_story_date) }}</strong> 多源截止</span>
            <span><strong>{{ groundCutoffDate }}</strong> 采集截止</span>
            <span><strong>{{ formatNumber(metrics.ok_story_covers) }}</strong> 封面</span>
            <span><strong>未知</strong> 评级覆盖指标</span>
          </div>
          <div class="edition-policy">
            <span>最新栏按事件时间优先</span>
            <span>专题栏 {{ edition.rotation_days || 3 }} 天轮换</span>
            <span>旧 Ready 阈值未批准，不作为质量通过</span>
            <span>L2 走势近期优先</span>
          </div>
          <p>
            当前顺序是未批准方法生成的候选展示，不代表重要性、风险或研究质量。
            <span v-if="metrics.future_story_count">未来日期异常 {{ metrics.future_story_count }} 条，已进入健康监控。</span>
          </p>
        </section>

        <section class="rail-card">
          <div class="rail-card__head">
            <span>话题索引</span>
            <small>{{ topicChips.length }} 类</small>
          </div>
          <div class="topic-cloud">
            <RouterLink v-for="topic in topicChips.slice(0, 10)" :key="topic.key" :to="topicPath(topic.key, topic.story)">
              <strong>{{ topic.label }}</strong>
              <span>{{ topic.count }} 件 · {{ topic.articles }} 条</span>
            </RouterLink>
          </div>
        </section>

        <section class="rail-card">
          <div class="rail-card__head">
            <span>覆盖缺口候选</span>
            <small>{{ blindspotStories.length }} 条 · 未评估</small>
          </div>
          <p class="rail-card__note">用于提示需要补看原文和在地来源，不直接给出风险结论。</p>
          <div class="blindspot-list">
            <RouterLink v-for="story in blindspotStories.slice(0, 5)" :key="story.cluster_id" :to="storyPath(story)">
              <strong>{{ story.display_title }}</strong>
              <span>{{ biasSummary(story) }} · {{ story.source_count }} 信源</span>
              <BiasBar :story="story" compact />
            </RouterLink>
          </div>
        </section>

        <section class="rail-card">
          <div class="rail-card__head">
            <span>关联线索</span>
            <small>{{ l2Watchlist.length }} 条</small>
          </div>
          <div class="chain-list">
            <RouterLink v-for="chain in l2Watchlist.slice(0, 7)" :key="chain.chain_id" :to="timelinePath(chain)">
              <strong>{{ chainTitle(chain) }}</strong>
              <small>
                {{ chain.segment_count }} 个切面 · {{ chain.article_count }} 条新闻 ·
                {{ qualityLabel(chain.chain_quality) }}
              </small>
            </RouterLink>
          </div>
        </section>

        <section class="rail-card">
          <div class="rail-card__head">
            <span>最新事件</span>
            <small>{{ latestStories.length }} 条</small>
          </div>
          <div class="latest-list">
            <RouterLink v-for="story in latestStories.slice(0, 6)" :key="story.cluster_id" :to="storyPath(story)">
              <span>{{ formatDate(story.end_date || story.start_date) }}</span>
              <strong>{{ story.display_title }}</strong>
              <small>{{ story.article_count }} 条 · {{ story.source_count }} 信源</small>
            </RouterLink>
          </div>
        </section>

        <section class="rail-card">
          <div class="rail-card__head">
            <span>高频来源</span>
            <small>{{ sourceLeaders.length }} 个</small>
          </div>
          <div class="source-leaders">
            <span v-for="source in sourceLeaders.slice(0, 9)" :key="source.name">
              {{ source.name }} <strong>{{ source.count }}</strong>
            </span>
          </div>
        </section>
      </aside>

      <section class="section-stack">
        <article
          v-for="section in storySections"
          :key="section.key"
          :id="`section-${section.key}`"
          :class="['story-section', `story-section--${section.key}`]"
        >
          <header class="section-title">
            <div>
              <span class="eyebrow">{{ section.key }}</span>
              <h2>{{ section.title }}</h2>
              <p>{{ section.subtitle }}</p>
            </div>
            <small>{{ sectionBadge(section) }}</small>
          </header>
          <div class="story-grid">
            <RouterLink
              v-for="story in section.stories"
              :key="`${section.key}-${story.cluster_id}`"
              :to="storyPath(story)"
              :class="['story-card', { 'story-card--text-only': !hasStoryImage(story) }]"
            >
              <EventCover v-if="hasStoryImage(story)" :story="story" size="card" />
              <div class="story-card__body">
                <div class="story-card__meta">
                  <span>{{ familyLabel(story.event_family) }}</span>
                  <span>{{ formatRange(story.start_date, story.end_date) }}</span>
                  <span>{{ storyLanguageLabel(story) }}</span>
                </div>
                <h3>{{ story.display_title }}</h3>
                <p class="story-card__entity">{{ displayEntityLine(story) }}</p>
                <p v-if="sampleTitle(story)" class="story-card__sample">{{ sampleTitle(story) }}</p>
                <BiasBar :story="story" compact />
                <div class="bias-mini">
                  <span
                    v-for="bucket in visibleBiasBuckets(story).slice(0, 4)"
                    :key="bucket.key"
                    :style="{ '--chip-color': bucket.color }"
                  >
                    {{ bucket.label }} {{ formatPct(bucket.value) }}%
                  </span>
                </div>
                <div class="story-card__foot">
                  <span>{{ story.article_count }} 条新闻</span>
                  <span>{{ story.source_count }} 信源</span>
                </div>
              </div>
            </RouterLink>
          </div>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, nextTick, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import atlasImage from '@/assets/ground-news/coverage-atlas.webp'
import {
  featureFreshness,
  formatFreshnessCutoff,
  freshnessStateLabel,
  useFeatureFreshness,
} from '@/features/operations/index.js'
import {
  buildGroundNewsHomeModel,
  groundNewsApi,
  homeBiasBuckets as normalizeBiasBuckets,
  homeBiasSummary as biasSummary,
  homeCoverShort as coverShort,
  homeEditionLabel,
  homeEntityLine as baseEntityLine,
  homeFamilyLabel as familyLabel,
  homeFormatDate as formatDate,
  homeFormatNumber as formatNumber,
  homeFormatPct as formatPct,
  homeFormatRange as formatRange,
  homeHasStoryImage as hasStoryImage,
  homeQualityLabel as qualityLabel,
  homeSampleNews as sampleNews,
  homeSampleTitle as sampleTitle,
  homeSectionBadge,
  homeSourceNames as sourceNames,
  homeSourceTypeItems as sourceTypeItems,
  homeStoryPath as storyPath,
  homeTimelinePath as timelinePath,
  homeTopicPath as topicPath,
  homeVisibleBiasBuckets as visibleBiasBuckets,
} from '@/features/ground-news/index.js'
import {
  graphMetricExplanation,
  graphMetricPresentation,
} from '@/governance/graphMetrics.js'

const loading = ref(false)
const error = ref('')
const home = ref(null)
const { report: freshnessReport, refresh: refreshFreshness } = useFeatureFreshness()

const homeModel = computed(() => buildGroundNewsHomeModel(home.value))
const leadStory = computed(() => homeModel.value.leadStory)
const rankMetric = computed(() => graphMetricPresentation('ground_news.rank_score', {
  value: leadStory.value?.rank_score,
}))
const rankMetricExplanation = computed(() => graphMetricExplanation('ground_news.rank_score', {
  inputs: {
    article_count: leadStory.value?.article_count,
    source_count: leadStory.value?.source_count,
    story_date: leadStory.value?.end_date || leadStory.value?.start_date,
    left_pct: leadStory.value?.left_pct,
    right_pct: leadStory.value?.right_pct,
    center_pct: leadStory.value?.center_pct,
    state_aligned_pct: leadStory.value?.state_aligned_pct,
    l2_chain_count: leadStory.value?.l2_chain_count,
    l2_quality_score: leadStory.value?.l2_best_quality_score,
  },
}))
const metrics = computed(() => homeModel.value.metrics)
const edition = computed(() => homeModel.value.edition)
const sectionByKey = computed(() => homeModel.value.sectionByKey)
const storySections = computed(() => homeModel.value.storySections)
const sectionNavItems = computed(() => homeModel.value.sectionNavItems)
const l2Watchlist = computed(() => homeModel.value.l2Watchlist)
const frontLatestStories = computed(() => homeModel.value.frontLatestStories)
const frontWeekStories = computed(() => homeModel.value.frontWeekStories)
const frontChainItems = computed(() => homeModel.value.frontChainItems)
const chainQualityExplanation = computed(() => graphMetricExplanation('ground_news.timeline_quality', {
  inputs: {
    quality_score: frontChainItems.value[0]?.quality_score,
    chain_quality: frontChainItems.value[0]?.chain_quality,
  },
}))
const latestStories = computed(() => homeModel.value.latestStories)
const blindspotStories = computed(() => homeModel.value.blindspotStories)
const topicChips = computed(() => homeModel.value.topicChips)
const sourceLeaders = computed(() => homeModel.value.sourceLeaders)
const groundNewsFreshness = computed(() => featureFreshness(freshnessReport.value, 'ground-news'))
const historicalMode = computed(() => groundNewsFreshness.value.historical)
const groundCutoffLabel = computed(() => formatFreshnessCutoff(groundNewsFreshness.value.cutoff))
const groundCutoffDate = computed(() => (
  groundCutoffLabel.value
  || formatDate(metrics.value.latest_valid_story_date || metrics.value.latest_story_date)
))
const usableStories7dLabel = computed(() => {
  return '未知（阈值/证据未建立）'
})
const groundFreshnessMessage = computed(() => {
  const cutoff = groundCutoffLabel.value ? `数据截止 ${groundCutoffLabel.value}。` : '数据截止时间暂不可用。'
  if (historicalMode.value) {
    return `${cutoff} 当前内容按历史快照呈现，不应用于当前态判断。`
  }
  return `${cutoff} 后端 freshness 判定在更新时限内。`
})

const EventCover = defineComponent({
  name: 'EventCover',
  props: {
    story: { type: Object, required: true },
    size: { type: String, default: 'card' },
  },
  setup(props) {
    return () => {
      const imageUrl = props.story.cover?.image_url || ''
      return h('div', {
        class: [
          'event-cover',
          `event-cover--${props.size}`,
          `theme-${props.story.cover?.theme || 'global_dispatch'}`,
          { 'has-image': Boolean(imageUrl) },
        ],
        style: {
          '--seed-x': `${(Number(props.story.cover?.seed || 0) % 73) + 8}%`,
          '--seed-y': `${(Number(props.story.cover?.seed || 0) % 41) + 12}%`,
        },
      }, [
        imageUrl ? h('div', {
          class: 'event-cover__image-blur',
          style: { backgroundImage: `url("${imageUrl}")` },
        }) : null,
        imageUrl ? h('img', {
          class: 'event-cover__image',
          src: imageUrl,
          alt: '',
          loading: 'lazy',
          referrerpolicy: 'no-referrer',
          onLoad: (event) => {
            const image = event.currentTarget
            const frame = image.closest('.event-cover')
            if (!frame || !image.naturalWidth || !image.naturalHeight) return
            const ratio = image.naturalWidth / image.naturalHeight
            const safeRatio = Math.min(2.25, Math.max(1.08, ratio))
            frame.style.setProperty('--cover-ratio', `${safeRatio} / 1`)
            frame.classList.toggle('is-portrait', ratio < 1)
            frame.classList.toggle('is-wide', ratio > 1.85)
          },
          onError: (event) => {
            const image = event.currentTarget
            const frame = image.closest('.event-cover')
            image.style.display = 'none'
            frame?.classList.remove('has-image')
            frame?.classList.add('image-failed')
          },
        }) : null,
        h('div', { class: 'event-cover__map' }),
        h('div', { class: 'event-cover__signal' }),
        h('div', { class: 'event-cover__rings' }),
        h('div', { class: 'event-cover__label' }, [
          h('span', props.story.cover?.label || '全球快讯'),
          h('strong', coverShort(props.story)),
          h('small', imageUrl ? `新闻图片 · ${props.story.cover?.credit || '来源见详情'}` : '编辑封面 · 非新闻照片'),
        ]),
      ])
    }
  },
})

const BiasBar = defineComponent({
  name: 'BiasBar',
  props: {
    story: { type: Object, required: true },
    compact: { type: Boolean, default: false },
  },
  setup(props) {
    return () => h('div', { class: ['bias-meter', { compact: props.compact }] }, [
      h('div', { class: 'bias-meter__bar' }, normalizeBiasBuckets(props.story).map((bucket) => (
        h('span', {
          key: bucket.key,
          style: {
            width: `${Math.max(bucket.value, bucket.value > 0 ? 3 : 0)}%`,
            background: bucket.color,
          },
        })
      ))),
      props.compact ? null : h('div', { class: 'bias-meter__legend' }, normalizeBiasBuckets(props.story).map((bucket) => (
        h('span', { key: bucket.key }, `${bucket.label} ${formatPct(bucket.value)}%`)
      ))),
    ])
  },
})

onMounted(() => {
  void refreshFreshness()
  void loadHome()
})

async function loadHome() {
  loading.value = true
  error.value = ''
  try {
    home.value = await groundNewsApi.getHome({ candidateLimit: 180, minArticles: 2 })
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || '未知错误'
  } finally {
    loading.value = false
  }
}

async function scrollToSection(id) {
  await nextTick()
  const target = document.getElementById(id)
  if (!target) return
  const headerOffset = 86
  const top = target.getBoundingClientRect().top + window.scrollY - headerOffset
  window.scrollTo({
    top: Math.max(0, top),
    behavior: 'smooth',
  })
  if (window.history?.replaceState) {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${id}`)
  }
}

function editionLabel(key) {
  return homeEditionLabel(sectionByKey.value, edition.value, key, groundNewsFreshness.value)
}

function sectionBadge(section) {
  return homeSectionBadge(section, sectionByKey.value, edition.value, groundNewsFreshness.value)
}

function displayEntityLine(story) {
  return formatRelationText(baseEntityLine(story))
}

function chainTitle(chain) {
  return formatRelationText(chain?.title || chain?.chain_id || '关联线索')
}

function formatRelationText(value) {
  return String(value || '')
    .replaceAll('_', ' ')
    .replace(/\s*->\s*/g, ' → ')
    .replace(/\s*↔\s*/g, ' ↔ ')
    .split(/(\s+|→|↔|:|：|-)/)
    .map((part) => formatEntityToken(part))
    .join('')
    .replace(/\s+/g, ' ')
    .trim()
}

function formatEntityToken(part) {
  const value = String(part || '')
  if (!/^[a-z][a-z0-9.'&/()]*$/i.test(value) || value !== value.toLowerCase()) return value
  const upper = value.toUpperCase()
  if (['AI', 'ASEAN', 'BRICS', 'EU', 'G7', 'G20', 'ICC', 'NATO', 'OPEC', 'UK', 'UN', 'US', 'UAE'].includes(upper)) {
    return upper
  }
  if (value.length <= 2) return upper
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function storyLanguageLabel(story) {
  const explicit = Array.isArray(story?.languages)
    ? story.languages[0]
    : story?.language
  const normalized = normalizeLanguage(explicit)
  if (normalized) return normalized
  const text = [
    story?.display_title,
    story?.title,
    story?.sample_news?.[0]?.title,
  ].filter(Boolean).join(' ')
  return inferLanguageLabel(text)
}

function normalizeLanguage(value) {
  const key = String(value || '').trim().toLowerCase()
  if (!key) return ''
  if (['zh', 'zh-cn', 'cn', 'chinese'].includes(key)) return '中文'
  if (['en', 'eng', 'english'].includes(key)) return '英文'
  if (['es', 'spa', 'spanish'].includes(key)) return '西语'
  if (['fr', 'fre', 'fra', 'french'].includes(key)) return '法语'
  if (['ar', 'ara', 'arabic'].includes(key)) return '阿语'
  if (['ru', 'rus', 'russian'].includes(key)) return '俄语'
  return '多语种'
}

function inferLanguageLabel(text) {
  const value = String(text || '')
  if (/[\u4e00-\u9fff]/.test(value)) return '中文'
  if (/[¿¡ñáéíóúü]/i.test(value)) return '西语'
  if (/[àâæçéèêëîïôœùûüÿ]/i.test(value)) return '法语'
  if (/[\u0600-\u06ff]/.test(value)) return '阿语'
  if (/[\u0400-\u04ff]/.test(value)) return '俄语'
  return '英文'
}
</script>

<style scoped>
.ground-home {
  --ink: #121a21;
  --muted: #697682;
  --line: rgba(28, 42, 52, 0.14);
  --paper: rgba(255, 252, 244, 0.9);
  --paper-solid: #fffaf0;
  --navy: #102234;
  --green: #12624d;
  --amber: #a86019;
  --blue: #2f7dd1;
  --serif: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "STSong", Georgia, serif;
  --sans: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", "IBM Plex Sans", sans-serif;
  min-height: 100vh;
  padding: 92px 22px 42px;
  color: var(--ink);
  font-family: var(--sans);
  background:
    linear-gradient(115deg, rgba(255, 250, 240, 0.88), rgba(233, 241, 239, 0.82)),
    radial-gradient(circle at 5% 7%, rgba(168, 96, 25, 0.24), transparent 25%),
    radial-gradient(circle at 88% 8%, rgba(47, 125, 209, 0.18), transparent 34%),
    #edf2ee;
  position: relative;
  overflow-x: hidden;
}

.ground-home::before {
  content: "";
  position: fixed;
  inset: 64px 0 0;
  background-image:
    linear-gradient(90deg, rgba(255, 250, 240, 0.56), rgba(255, 250, 240, 0.14) 38%, rgba(238, 244, 239, 0.3)),
    var(--atlas-image);
  background-size: cover, cover;
  background-position: center top;
  opacity: 0.24;
  mix-blend-mode: multiply;
  pointer-events: none;
}

.ground-home::after {
  content: "";
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(18, 26, 33, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(18, 26, 33, 0.026) 1px, transparent 1px);
  background-size: 44px 44px;
  pointer-events: none;
  mask-image: linear-gradient(180deg, transparent 0%, #000 18%, transparent 92%);
}

.home-hero,
.ground-subnav,
.home-layout {
  position: relative;
  z-index: 1;
  max-width: 1580px;
  margin: 0 auto;
}

.ground-subnav {
  min-height: 48px;
  width: fit-content;
  margin-bottom: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  max-width: min(1580px, 100%);
  padding: 5px;
  border: 1px solid rgba(17, 17, 17, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 8px 20px rgba(17, 17, 17, 0.06);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  overflow-x: auto;
}

.ground-subnav span {
  flex: 0 0 auto;
  padding: 0 10px;
  color: var(--accent);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.ground-subnav a {
  flex: 0 0 auto;
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 22px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #333;
  font-size: 14px;
  font-weight: 900;
  text-decoration: none;
  box-shadow: none;
}

.ground-subnav a:hover {
  border-color: rgba(184, 0, 0, 0.14);
  background: rgba(184, 0, 0, 0.06);
  color: var(--accent);
}

.ground-subnav a.router-link-exact-active {
  border-color: rgba(184, 0, 0, 0.28);
  background: rgba(184, 0, 0, 0.1);
  color: var(--accent);
  box-shadow: inset 0 -2px 0 var(--accent);
}

.home-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, 0.65fr);
  gap: 20px;
  align-items: end;
  margin-bottom: 22px;
}

.home-freshness {
  position: relative;
  z-index: 1;
  max-width: 1580px;
  min-height: 46px;
  margin: -4px auto 18px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  padding: 10px 14px;
  border: 1px solid rgba(168, 96, 25, 0.28);
  border-radius: 14px;
  color: #704410;
  background: rgba(255, 248, 224, 0.94);
  box-shadow: 0 10px 28px rgba(91, 63, 25, 0.08);
  font-size: 12px;
  line-height: 1.45;
}

.home-freshness > strong {
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(168, 96, 25, 0.12);
  font-size: 12px;
}

.home-freshness > span:nth-of-type(1) { flex: 1 1 360px; }
.home-freshness.is-live {
  border-color: rgba(18, 98, 77, 0.24);
  color: #154f40;
  background: rgba(234, 247, 240, 0.94);
}

.home-freshness.is-live > strong { background: rgba(18, 98, 77, 0.12); }

.eyebrow {
  color: #8b6a38;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.home-hero h1 {
  margin: 5px 0 12px;
  font-family: var(--serif);
  font-size: clamp(48px, 7vw, 104px);
  line-height: 0.9;
  letter-spacing: -0.065em;
  word-break: keep-all;
}

.home-hero p {
  max-width: 760px;
  margin: 0;
  color: #42515d;
  font-size: 16px;
  line-height: 1.72;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 22px;
}

.primary-link,
.ghost-link,
.story-link {
  border: 0;
  border-radius: 999px;
  padding: 12px 16px;
  background: var(--navy);
  color: #fffaf0;
  font-size: 13px;
  font-weight: 900;
  text-decoration: none;
  cursor: pointer;
}

.ghost-link {
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.62);
  color: var(--navy);
}

.home-hero__metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.home-hero__metrics article,
.lead-feature,
.rail-card,
.story-section,
.home-loading,
.home-error {
  border: 1px solid var(--line);
  background: var(--paper);
  box-shadow: 0 24px 70px rgba(55, 69, 78, 0.13);
}

.home-hero__metrics article {
  border-radius: 24px;
  padding: 16px;
}

.home-hero__metrics strong {
  display: block;
  font-family: var(--serif);
  font-size: 28px;
  line-height: 1;
}

.home-hero__metrics span {
  display: block;
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 900;
}

.home-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 390px);
  gap: 18px;
  align-items: start;
}

.front-grid {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.8fr) minmax(300px, 0.75fr);
  gap: 14px;
  align-items: stretch;
}

.front-column {
  min-width: 0;
  height: clamp(520px, 56vh, 640px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: 30px;
  padding: 16px;
  background:
    linear-gradient(180deg, rgba(255, 252, 244, 0.92), rgba(241, 247, 244, 0.86)),
    rgba(255, 252, 244, 0.9);
  box-shadow: 0 24px 70px rgba(55, 69, 78, 0.12);
}

.front-column--latest {
  background:
    linear-gradient(135deg, rgba(16, 34, 52, 0.96), rgba(17, 82, 72, 0.88)),
    var(--navy);
  color: #fffaf0;
}

.front-column header {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: end;
  border-bottom: 1px solid rgba(18, 34, 52, 0.12);
  padding-bottom: 12px;
}

.front-column--latest header {
  border-bottom-color: rgba(255, 250, 240, 0.22);
}

.front-column__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
}

.front-column h2 {
  margin: 3px 0 0;
  font-family: var(--serif);
  font-size: 32px;
  line-height: 0.98;
  letter-spacing: -0.045em;
}

.front-column header small {
  max-width: 150px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
  line-height: 1.35;
  text-align: right;
}

.front-column--latest header small,
.front-column--latest .eyebrow {
  color: rgba(255, 250, 240, 0.72);
}

.front-headline {
  display: grid;
  gap: 6px;
  border-top: 1px solid rgba(18, 34, 52, 0.1);
  padding: 12px 0;
  color: inherit;
  text-decoration: none;
}

.front-headline:first-of-type {
  border-top: 0;
}

.front-column--latest .front-headline {
  border-top-color: rgba(255, 250, 240, 0.18);
}

.front-headline:hover strong {
  color: var(--green);
}

.front-column--latest .front-headline:hover strong {
  color: #f2c94c;
}

.front-headline span {
  color: #8b6a38;
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.front-column--latest .front-headline span {
  color: rgba(255, 250, 240, 0.66);
}

.front-headline strong {
  display: -webkit-box;
  overflow: hidden;
  font-family: var(--serif);
  font-size: 18px;
  line-height: 1.12;
  letter-spacing: -0.025em;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.front-headline--lead strong {
  font-size: clamp(26px, 3vw, 46px);
  line-height: 0.98;
  -webkit-line-clamp: 3;
}

.front-headline small {
  color: var(--muted);
  font-size: 11px;
  font-weight: 850;
  line-height: 1.35;
}

.front-column--latest .front-headline small {
  color: rgba(255, 250, 240, 0.68);
}

.front-headline--compact strong {
  font-size: 16px;
}

.front-headline :deep(.event-cover--strip) {
  min-height: 164px;
  aspect-ratio: 16 / 8;
  border-radius: 22px;
}

.lead-feature {
  grid-column: 1 / 2;
  border-radius: 34px;
  padding: 18px;
  display: grid;
  grid-template-columns: minmax(0, 0.94fr) minmax(360px, 0.78fr);
  gap: 18px;
  align-items: start;
  overflow: hidden;
}

.lead-media {
  display: grid;
  min-width: 0;
  gap: 12px;
  overflow: hidden;
}

.lead-cover {
  display: block;
  min-height: 0;
  max-width: 100%;
  overflow: hidden;
  border-radius: 26px;
  text-decoration: none;
}

.lead-media :deep(.event-cover) {
  width: 100%;
  min-height: 330px;
  max-height: 500px;
}

.lead-brief {
  display: grid;
  gap: 8px;
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-radius: 22px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.58);
}

.lead-brief__head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
}

.lead-brief__head strong {
  max-width: 55%;
  overflow: hidden;
  color: #43515c;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lead-brief__item {
  display: grid;
  gap: 3px;
  border-radius: 16px;
  padding: 10px;
  background: rgba(255, 250, 240, 0.82);
  color: var(--ink);
  text-decoration: none;
}

.lead-brief__item:hover {
  background: rgba(238, 244, 239, 0.94);
}

.lead-brief__item span {
  color: #8b6a38;
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.08em;
}

.lead-brief__item strong {
  display: -webkit-box;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.34;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.lead-story {
  position: relative;
  z-index: 2;
  min-width: 0;
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-radius: 28px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  background: rgba(255, 252, 244, 0.82);
  box-shadow: 0 16px 34px rgba(55, 69, 78, 0.08);
}

.story-kicker,
.story-card__meta,
.story-card__foot,
.rail-card__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.story-kicker span,
.story-card__meta span,
.story-card__foot span,
.source-strip span {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 7px 10px;
  background: rgba(255, 255, 255, 0.66);
  color: #485866;
  font-size: 12px;
  font-weight: 850;
}

.lead-title {
  margin: 16px 0 0;
  color: var(--ink);
  font-family: var(--serif);
  font-size: clamp(34px, 4vw, 62px);
  font-weight: 900;
  line-height: 0.98;
  letter-spacing: -0.055em;
  text-decoration: none;
}

.lead-title:hover,
.story-card:hover h3 {
  color: var(--green);
}

.lead-entity {
  margin: 16px 0;
  color: #475864;
  font-size: 15px;
  font-weight: 800;
}

.lead-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 9px;
  margin-bottom: 14px;
}

.lead-stats span {
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.74);
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
}

.lead-stats strong {
  display: block;
  color: var(--navy);
  font-size: 26px;
  line-height: 1;
}

.source-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 14px 0 16px;
}

.right-rail {
  grid-column: 2 / 3;
  grid-row: 1 / span 2;
  position: relative;
  display: grid;
  gap: 18px;
  align-self: start;
}

.rail-card {
  border-radius: 28px;
  padding: 22px;
}

.rail-card h2 {
  margin: 8px 0 8px;
  font-family: var(--serif);
  font-size: 28px;
  line-height: 1.06;
}

.rail-card p {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
}

.brief-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 12px 0;
}

.brief-grid span {
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-radius: 18px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.62);
  color: var(--muted);
  font-size: 11px;
  font-weight: 850;
}

.brief-grid strong {
  display: block;
  color: var(--navy);
  font-family: var(--serif);
  font-size: 22px;
  line-height: 1;
}

.edition-policy {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin: 10px 0 12px;
}

.edition-policy span {
  border: 1px solid rgba(18, 98, 77, 0.14);
  border-radius: 999px;
  padding: 7px 9px;
  background: rgba(18, 98, 77, 0.08);
  color: #2f594f;
  font-size: 11px;
  font-weight: 900;
}

.topic-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.topic-cloud a {
  flex: 1 1 132px;
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-radius: 18px;
  padding: 11px;
  background: rgba(255, 255, 255, 0.62);
  color: var(--ink);
  text-decoration: none;
}

.topic-cloud a:hover {
  border-color: rgba(18, 98, 77, 0.38);
  background: rgba(255, 250, 240, 0.92);
}

.blindspot-list a:hover {
  background: linear-gradient(90deg, rgba(18, 98, 77, 0.08), transparent 82%);
}

.topic-cloud strong,
.topic-cloud span {
  display: block;
}

.topic-cloud strong {
  font-size: 13px;
}

.topic-cloud span {
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 850;
}

.blindspot-list {
  display: grid;
  gap: 0;
  margin-top: 12px;
}

.blindspot-list a {
  display: grid;
  gap: 7px;
  border-top: 1px solid rgba(18, 34, 52, 0.11);
  padding: 13px 0;
  background: transparent;
  color: var(--ink);
  text-decoration: none;
}

.blindspot-list a:first-child {
  border-top: 0;
  padding-top: 0;
}

.blindspot-list strong {
  display: -webkit-box;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.32;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.blindspot-list span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 850;
}

.source-leaders {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.source-leaders span {
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-radius: 999px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.66);
  color: #4a5965;
  font-size: 11px;
  font-weight: 850;
}

.source-leaders strong {
  color: var(--navy);
}

.rail-card__head {
  justify-content: space-between;
  color: #8b6a38;
  font-size: 12px;
  font-weight: 900;
}

.rail-card__head a {
  color: var(--green);
  font-size: 11px;
  font-weight: 900;
  text-decoration: none;
}

.chain-list {
  display: grid;
  gap: 0;
  margin-top: 12px;
}

.chain-list a {
  border-top: 1px solid rgba(18, 34, 52, 0.11);
  padding: 13px 0;
  background: transparent;
  color: inherit;
  text-decoration: none;
}

.chain-list a:first-child {
  border-top: 0;
  padding-top: 0;
}

.chain-list strong,
.chain-list small {
  display: block;
}

.chain-list strong {
  font-size: 13px;
  line-height: 1.35;
}

.chain-list small {
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
}

.section-stack {
  grid-column: 1 / 2;
  display: grid;
  gap: 18px;
}

.story-section {
  border-radius: 30px;
  padding: 18px;
}

.section-title {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: end;
  margin-bottom: 14px;
}

.section-title h2 {
  margin: 4px 0 3px;
  font-family: var(--serif);
  font-size: 34px;
  line-height: 1;
  letter-spacing: -0.04em;
}

.section-title p {
  margin: 0;
  color: var(--muted);
}

.section-title small {
  color: var(--muted);
  white-space: nowrap;
}

.story-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
  gap: 14px;
}

.story-card {
  align-self: start;
  border: 1px solid var(--line);
  border-radius: 26px;
  overflow: hidden;
  background: rgba(255, 250, 240, 0.86);
  color: var(--ink);
  text-decoration: none;
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
}

.story-card--text-only {
  border-radius: 18px;
  border-color: rgba(18, 34, 52, 0.13);
  background:
    linear-gradient(90deg, rgba(18, 98, 77, 0.08), transparent 48%),
    rgba(255, 252, 246, 0.9);
  box-shadow: inset 4px 0 0 rgba(18, 98, 77, 0.35);
}

.story-card--editorial,
.story-card--text-only {
  border-color: rgba(18, 98, 77, 0.16);
  background:
    linear-gradient(180deg, rgba(255, 252, 246, 0.94), rgba(238, 246, 242, 0.9)),
    rgba(255, 252, 246, 0.92);
}

.story-card--editorial :deep(.event-cover) {
  border-bottom: 1px solid rgba(18, 98, 77, 0.12);
}

.story-card:hover {
  transform: translateY(-3px);
  border-color: rgba(18, 98, 77, 0.38);
  box-shadow: 0 18px 38px rgba(55, 69, 78, 0.14);
}

.story-card__body {
  padding: 14px;
}

.story-card--text-only .story-card__body {
  padding: 13px 14px 12px 16px;
}

.story-card h3 {
  margin: 11px 0 9px;
  font-family: var(--serif);
  font-size: 18px;
  line-height: 1.18;
  letter-spacing: -0.025em;
}

.story-card--text-only h3 {
  display: -webkit-box;
  margin: 9px 0 7px;
  overflow: hidden;
  font-size: 16px;
  line-height: 1.18;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.story-card__entity,
.story-card__sample {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 12px;
}

.story-card--text-only .story-card__entity {
  margin-bottom: 7px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.story-card__sample {
  display: -webkit-box;
  min-height: 32px;
  overflow: hidden;
  color: #3f4d58;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.story-card--text-only .story-card__sample {
  min-height: 0;
  margin-bottom: 8px;
  font-size: 11px;
  line-height: 1.32;
}

.story-card__meta,
.story-card__foot {
  justify-content: space-between;
}

.story-card__meta span,
.story-card__foot span {
  padding: 6px 8px;
  font-size: 11px;
}

.story-card--text-only .story-card__meta {
  gap: 6px;
}

.story-card--text-only .story-card__meta span,
.story-card--text-only .story-card__foot span {
  padding: 4px 7px;
  font-size: 10px;
}

.story-card--text-only .bias-mini {
  display: none;
}

.story-card--text-only :deep(.bias-meter.compact .bias-meter__bar) {
  height: 5px;
}

.story-card--text-only .story-card__foot {
  margin-top: 8px;
}

.event-cover {
  position: relative;
  height: auto;
  aspect-ratio: var(--cover-ratio, 16 / 10);
  min-height: 190px;
  border-radius: 24px;
  overflow: hidden;
  background:
    radial-gradient(circle at var(--seed-x) var(--seed-y), rgba(255, 255, 255, 0.35), transparent 25%),
    linear-gradient(135deg, #122235, #2e6157);
  isolation: isolate;
}

.event-cover--card {
  min-height: 150px;
  aspect-ratio: var(--cover-ratio, 16 / 10);
  border-radius: 22px 22px 0 0;
}

.event-cover--strip {
  min-height: 164px;
  aspect-ratio: 16 / 8;
  border-radius: 22px;
}

.event-cover--large {
  min-height: 420px;
  aspect-ratio: var(--cover-ratio, 16 / 10);
}

.event-cover::before {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 250, 240, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 250, 240, 0.08) 1px, transparent 1px),
    var(--atlas-image);
  background-size: 36px 36px, 36px 36px, cover;
  background-position: center;
  opacity: 0.38;
  mix-blend-mode: screen;
}

.event-cover__map,
.event-cover__signal,
.event-cover__rings {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.event-cover__map {
  background:
    linear-gradient(22deg, transparent 0 42%, rgba(255, 250, 240, 0.52) 43% 44%, transparent 45%),
    linear-gradient(144deg, transparent 0 53%, rgba(255, 250, 240, 0.24) 54% 55%, transparent 56%);
  opacity: 0.72;
}

.event-cover__signal {
  width: 42%;
  height: 42%;
  left: 48%;
  top: 16%;
  border: 1px solid rgba(255, 250, 240, 0.26);
  border-radius: 50%;
  box-shadow:
    0 0 0 22px rgba(255, 250, 240, 0.04),
    0 0 0 52px rgba(255, 250, 240, 0.03);
}

.event-cover__rings {
  background:
    radial-gradient(circle at 24% 72%, transparent 0 12%, rgba(255, 250, 240, 0.16) 12.5% 13.5%, transparent 14%),
    radial-gradient(circle at 76% 28%, transparent 0 10%, rgba(255, 250, 240, 0.12) 10.5% 11.5%, transparent 12%);
}

.event-cover__label {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 16px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 22px;
  padding: 14px;
  background: rgba(9, 22, 34, 0.72);
  color: #fffaf0;
  backdrop-filter: blur(10px);
}

.event-cover--card .event-cover__label {
  left: 10px;
  right: 10px;
  bottom: 10px;
  border-radius: 17px;
  padding: 10px;
}

.event-cover__label span,
.event-cover__label small {
  display: block;
  color: rgba(255, 250, 240, 0.72);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.event-cover__label strong {
  display: block;
  margin: 4px 0;
  font-family: var(--serif);
  font-size: 27px;
  line-height: 1;
}

.event-cover--card .event-cover__label strong {
  font-size: 18px;
}

.theme-diplomatic_wire {
  background: radial-gradient(circle at 20% 22%, #d6a63d, transparent 28%), linear-gradient(135deg, #102234, #46665f);
}

.theme-security_grid {
  background: radial-gradient(circle at 68% 20%, #d8563c, transparent 30%), linear-gradient(135deg, #0d1b29, #34495c);
}

.theme-market_routes {
  background: radial-gradient(circle at 22% 70%, #d78a2a, transparent 30%), linear-gradient(135deg, #12342b, #2f99aa);
}

.theme-tech_signal {
  background: radial-gradient(circle at 74% 28%, #6bbce5, transparent 30%), linear-gradient(135deg, #0f2034, #253a7b);
}

.theme-civic_chamber,
.theme-street_signal,
.theme-legal_index {
  background: radial-gradient(circle at 68% 18%, #b26045, transparent 30%), linear-gradient(135deg, #26313a, #7a6a55);
}

.theme-public_works,
.theme-border_crossing,
.theme-climate_map,
.theme-global_dispatch {
  background: radial-gradient(circle at 22% 24%, #8dbb88, transparent 30%), linear-gradient(135deg, #102234, #3d7167);
}

.bias-meter {
  display: grid;
  gap: 8px;
}

.bias-meter__bar {
  height: 12px;
  border-radius: 999px;
  display: flex;
  overflow: hidden;
  background: #d7dde1;
}

.bias-meter.compact .bias-meter__bar {
  height: 8px;
}

.bias-meter__bar span {
  min-width: 0;
}

.bias-meter__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}

.ground-home :deep(.event-cover__map),
.ground-home :deep(.event-cover__signal),
.ground-home :deep(.event-cover__rings) {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.ground-home :deep(.event-cover__image) {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  opacity: 1;
  filter: saturate(0.96) contrast(1.03);
  transform: none;
}

.ground-home :deep(.event-cover.has-image .event-cover__map) {
  background:
    linear-gradient(180deg, rgba(10, 20, 31, 0.08), rgba(10, 20, 31, 0.44)),
    linear-gradient(22deg, transparent 0 42%, rgba(255, 250, 240, 0.24) 43% 44%, transparent 45%);
}

.ground-home :deep(.event-cover.has-image .event-cover__rings),
.ground-home :deep(.event-cover.has-image .event-cover__signal) {
  opacity: 0.42;
}

.ground-home :deep(.event-cover__map) {
  background:
    linear-gradient(22deg, transparent 0 42%, rgba(255, 250, 240, 0.52) 43% 44%, transparent 45%),
    linear-gradient(144deg, transparent 0 53%, rgba(255, 250, 240, 0.24) 54% 55%, transparent 56%);
  opacity: 0.72;
}

.ground-home :deep(.event-cover__signal) {
  width: 42%;
  height: 42%;
  left: 48%;
  top: 16%;
  border: 1px solid rgba(255, 250, 240, 0.26);
  border-radius: 50%;
  box-shadow:
    0 0 0 22px rgba(255, 250, 240, 0.04),
    0 0 0 52px rgba(255, 250, 240, 0.03);
}

.ground-home :deep(.event-cover__rings) {
  background:
    radial-gradient(circle at 24% 72%, transparent 0 12%, rgba(255, 250, 240, 0.16) 12.5% 13.5%, transparent 14%),
    radial-gradient(circle at 76% 28%, transparent 0 10%, rgba(255, 250, 240, 0.12) 10.5% 11.5%, transparent 12%);
}

.ground-home :deep(.event-cover__label) {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 16px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 22px;
  padding: 14px;
  background: rgba(9, 22, 34, 0.72);
  color: #fffaf0;
  backdrop-filter: blur(10px);
}

.ground-home :deep(.event-cover--card .event-cover__label) {
  left: 10px;
  right: 10px;
  bottom: 10px;
  border-radius: 17px;
  padding: 10px;
}

.ground-home :deep(.event-cover__label span),
.ground-home :deep(.event-cover__label small) {
  display: block;
  color: rgba(255, 250, 240, 0.72);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.ground-home :deep(.event-cover__label strong) {
  display: block;
  margin: 4px 0;
  color: #fffaf0;
  font-family: var(--serif);
  font-size: 27px;
  line-height: 1;
}

.ground-home :deep(.event-cover--card .event-cover__label strong) {
  font-size: 18px;
}

.ground-home :deep(.bias-meter__bar) {
  height: 12px;
  border-radius: 999px;
  display: flex;
  overflow: hidden;
  background: #d7dde1;
}

.ground-home :deep(.bias-meter.compact .bias-meter__bar) {
  height: 8px;
}

.ground-home :deep(.bias-meter__bar span) {
  min-width: 0;
}

.ground-home :deep(.bias-meter__legend) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}

.coverage-panel {
  border: 1px solid rgba(18, 34, 52, 0.12);
  border-radius: 22px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.68);
}

.coverage-panel__head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: #43515c;
  font-size: 12px;
  font-weight: 900;
}

.coverage-panel__head span {
  color: var(--muted);
  font-weight: 800;
}

.coverage-panel__note,
.rail-card__note {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.bias-chips,
.bias-mini,
.source-type-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.bias-chips {
  margin-top: 10px;
}

.bias-chips span,
.bias-mini span {
  border: 1px solid rgba(18, 34, 52, 0.1);
  border-left: 4px solid var(--chip-color);
  border-radius: 999px;
  padding: 6px 9px;
  background: rgba(255, 250, 240, 0.82);
  color: #3f4d58;
  font-size: 11px;
  font-weight: 900;
}

.bias-mini {
  margin-top: 9px;
}

.bias-mini span {
  padding: 5px 7px;
  font-size: 10px;
}

.source-type-strip {
  margin: 12px 0 2px;
}

.source-type-strip span {
  border-radius: 14px;
  padding: 9px 10px;
  background: rgba(18, 34, 52, 0.06);
  color: #56636f;
  font-size: 11px;
  font-weight: 850;
}

.source-type-strip strong {
  color: var(--navy);
  font-size: 13px;
}

.latest-list {
  display: grid;
  gap: 0;
  margin-top: 12px;
}

.latest-list a {
  display: grid;
  gap: 4px;
  border-top: 1px solid rgba(18, 34, 52, 0.11);
  padding: 13px 0;
  background: transparent;
  color: var(--ink);
  text-decoration: none;
}

.latest-list a:first-child {
  border-top: 0;
  padding-top: 0;
}

.latest-list a:hover {
  background: linear-gradient(90deg, rgba(18, 98, 77, 0.08), transparent 82%);
}

.latest-list span {
  color: #8b6a38;
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.12em;
}

.latest-list strong {
  font-size: 13px;
  line-height: 1.28;
}

.latest-list small {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}

.ground-home :deep(.event-cover) {
  height: auto;
  aspect-ratio: var(--cover-ratio, 16 / 10);
  background: linear-gradient(135deg, #102234, #3d7167);
}

.ground-home :deep(.event-cover--large) {
  min-height: 420px;
}

.ground-home :deep(.event-cover--card) {
  min-height: 156px;
}

.ground-home :deep(.event-cover__image-blur) {
  position: absolute;
  inset: -20px;
  background-position: center;
  background-size: cover;
  filter: blur(22px) saturate(0.9);
  opacity: 0.62;
  transform: scale(1.08);
}

.ground-home :deep(.event-cover__image) {
  position: absolute;
  inset: 0;
  z-index: 2;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  opacity: 1;
  filter: saturate(0.96) contrast(1.03);
  transform: none;
}

.ground-home :deep(.event-cover.has-image::before) {
  opacity: 0.14;
}

.ground-home :deep(.event-cover.has-image .event-cover__map) {
  z-index: 3;
  background:
    linear-gradient(180deg, rgba(6, 14, 24, 0.02) 0%, rgba(6, 14, 24, 0.48) 100%),
    radial-gradient(circle at 12% 18%, rgba(255, 250, 240, 0.12), transparent 28%);
}

.ground-home :deep(.event-cover.has-image .event-cover__rings),
.ground-home :deep(.event-cover.has-image .event-cover__signal) {
  z-index: 4;
  opacity: 0.2;
}

.ground-home :deep(.event-cover.image-failed .event-cover__map),
.ground-home :deep(.event-cover.image-failed .event-cover__rings),
.ground-home :deep(.event-cover.image-failed .event-cover__signal) {
  opacity: 0.72;
}

.ground-home :deep(.event-cover__label) {
  z-index: 6;
  left: 12px;
  right: 12px;
  bottom: 12px;
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(9, 22, 34, 0.62);
}

.ground-home :deep(.event-cover.has-image .event-cover__label) {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 12px;
}

.ground-home :deep(.event-cover.has-image .event-cover__label strong) {
  font-family: var(--sans);
  font-size: 13px;
  letter-spacing: 0;
}

.ground-home :deep(.event-cover.has-image .event-cover__label small) {
  max-width: 56%;
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-loading,
.home-error {
  position: relative;
  z-index: 1;
  max-width: 920px;
  margin: 40px auto;
  border-radius: 30px;
  padding: 34px;
  text-align: center;
}

.pulse-cover {
  width: 140px;
  height: 90px;
  margin: 0 auto 18px;
  border-radius: 24px;
  background: linear-gradient(90deg, rgba(16, 34, 52, 0.08), rgba(16, 98, 77, 0.22), rgba(16, 34, 52, 0.08));
  background-size: 200% 100%;
  animation: pulse 1.3s ease-in-out infinite;
}

.home-error button {
  border: 0;
  border-radius: 999px;
  padding: 10px 14px;
  background: var(--navy);
  color: #fffaf0;
  cursor: pointer;
}

@keyframes pulse {
  from { background-position: 100% 0; }
  to { background-position: -100% 0; }
}

/* Newsroom layout pass: dense sections, hard rules, minimal cards. */
.ground-home {
  --ink: #111111;
  --muted: #5f6368;
  --line: #d6d6d6;
  --paper: #ffffff;
  --paper-solid: #ffffff;
  --navy: #111111;
  --green: #006c5b;
  --accent: #b80000;
  padding: 78px 18px 42px;
  background: #f6f6f4;
}

.ground-home::before,
.ground-home::after {
  display: none;
}

.home-hero,
.ground-subnav,
.home-layout,
.section-nav {
  max-width: 1600px;
}

.ground-subnav {
  box-sizing: border-box;
  width: fit-content;
  min-height: 48px;
  margin: 0 auto 18px;
  gap: 6px;
  padding: 5px;
  border: 1px solid rgba(17, 17, 17, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 8px 20px rgba(17, 17, 17, 0.06);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.ground-subnav span {
  align-self: stretch;
  display: inline-flex;
  align-items: center;
  padding: 0 12px;
  border-right: 1px solid rgba(17, 17, 17, 0.08);
  background: transparent;
  color: var(--accent);
  font-size: 11px;
  letter-spacing: 0.08em;
}

.ground-subnav a {
  min-height: 38px;
  padding: 0 22px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #333;
  font-size: 14px;
  font-weight: 950;
  box-shadow: none;
}

.ground-subnav a:hover {
  border-color: rgba(184, 0, 0, 0.14);
  background: rgba(184, 0, 0, 0.06);
  color: var(--accent);
}

.ground-subnav a.router-link-exact-active {
  border-color: rgba(184, 0, 0, 0.28);
  background: rgba(184, 0, 0, 0.1);
  color: var(--accent);
  box-shadow: inset 0 -2px 0 var(--accent);
}

.home-hero {
  grid-template-columns: minmax(0, 1fr) minmax(320px, 0.46fr);
  align-items: end;
  gap: 16px;
  margin-bottom: 0;
  padding: 8px 0 8px;
  border-bottom: 4px solid #111;
}

.eyebrow {
  color: var(--accent);
  letter-spacing: 0;
}

.home-hero h1,
.front-column h2,
.lead-title,
.rail-card h2,
.section-title h2,
.story-card h3 {
  letter-spacing: 0;
}

.home-hero h1 {
  margin: 0 0 6px;
  font-size: clamp(30px, 3.4vw, 48px);
  line-height: 1.04;
}

.home-hero p {
  max-width: 780px;
  color: #414141;
  font-size: 13px;
  line-height: 1.45;
}

.hero-actions {
  margin-top: 7px;
}

.primary-link,
.ghost-link,
.story-link {
  border: 1px solid #111;
  border-radius: 0;
  padding: 8px 11px;
  background: #111;
  color: #fff;
}

.ghost-link {
  background: #fff;
  color: #111;
}

.home-hero__metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  border: 1px solid var(--line);
  background: #fff;
}

.home-hero__metrics article,
.lead-feature,
.rail-card,
.story-section,
.home-loading,
.home-error {
  background: transparent;
  box-shadow: none;
}

.home-hero__metrics article {
  border: 0;
  border-left: 1px solid var(--line);
  border-radius: 0;
  padding: 8px 10px;
}

.home-hero__metrics article:first-child {
  border-left: 0;
}

.home-hero__metrics strong {
  font-family: var(--sans);
  font-size: 20px;
}

.section-nav {
  position: relative;
  z-index: 1;
  margin: 0 auto 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  border-bottom: 1px solid var(--line);
  padding: 8px 0;
  background: transparent;
  overflow: visible;
}

.section-nav button {
  flex: 0 1 auto;
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line);
  padding: 0 11px;
  background: #fff;
  color: #111;
  font-family: inherit;
  font-size: 13px;
  font-weight: 850;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
}

.section-nav small {
  min-width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  background: #f6f6f4;
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
}

.section-nav button:hover {
  background: #111;
  color: #fff;
}

.section-nav button:hover small {
  border-color: rgba(255, 255, 255, 0.35);
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

.home-layout {
  grid-template-columns: minmax(0, 1fr) minmax(300px, 360px);
  gap: 22px;
}

.front-grid {
  grid-row: 1;
  grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.9fr) minmax(280px, 0.9fr);
  gap: 18px;
  align-items: start;
  padding: 0 0 18px;
  border-bottom: 1px solid var(--line);
}

.front-column {
  height: auto;
  display: flex;
  flex-direction: column;
  border: 0;
  border-left: 1px solid var(--line);
  border-radius: 0;
  padding: 0 0 0 18px;
  background: transparent;
  box-shadow: none;
}

.front-column:first-child {
  border-left: 0;
  padding-left: 0;
}

.front-column--latest {
  background: transparent;
  color: #111;
}

.front-column header {
  min-height: 58px;
  align-items: center;
  border-top: 4px solid #111;
  border-bottom: 1px solid #111;
  padding: 8px 0;
}

.front-column__list {
  flex: 1 1 auto;
  min-height: auto;
  overflow: visible;
}

.front-column--latest header {
  border-bottom-color: #111;
}

.front-column h2 {
  font-family: var(--sans);
  font-size: 23px;
  line-height: 1.08;
}

.front-column header small,
.front-column--latest header small,
.front-column--latest .eyebrow {
  color: var(--muted);
}

.front-headline {
  gap: 5px;
  border-top-color: var(--line);
  padding: 9px 0;
}

.front-column--latest .front-headline {
  border-top-color: var(--line);
}

.front-headline span,
.front-column--latest .front-headline span {
  color: var(--accent);
  letter-spacing: 0;
}

.front-headline strong {
  font-size: 16px;
  line-height: 1.22;
  letter-spacing: 0;
}

.front-headline--lead strong {
  font-size: clamp(25px, 2.4vw, 36px);
  line-height: 1.08;
}

.front-headline small,
.front-column--latest .front-headline small {
  color: var(--muted);
}

.front-headline:hover strong,
.front-column--latest .front-headline:hover strong,
.lead-title:hover,
.story-card:hover h3 {
  color: var(--accent);
}

.front-headline :deep(.event-cover--strip) {
  min-height: 176px;
  margin-bottom: 8px;
  border-radius: 0;
  aspect-ratio: 16 / 9;
}

.lead-feature {
  grid-row: 2;
  border: 0;
  border-top: 4px solid #111;
  border-radius: 0;
  padding: 18px 0 0;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 0.8fr);
}

.lead-cover,
.lead-brief,
.lead-brief__item,
.lead-story,
.coverage-panel,
.lead-stats span,
.story-kicker span,
.story-card__meta span,
.story-card__foot span,
.source-strip span,
.bias-chips span,
.bias-mini span,
.source-type-strip span {
  border-radius: 0;
}

.lead-cover,
.lead-story,
.lead-brief {
  box-shadow: none;
}

.lead-media :deep(.event-cover) {
  min-height: 360px;
}

.lead-story {
  border: 0;
  border-left: 1px solid var(--line);
  padding: 0 0 0 18px;
  background: transparent;
}

.lead-title {
  font-size: clamp(32px, 3.6vw, 54px);
  line-height: 1.05;
}

.lead-stats span,
.coverage-panel,
.lead-brief,
.lead-brief__item {
  background: #fff;
}

.right-rail {
  grid-row: 2 / span 2;
  gap: 16px;
}

.rail-card {
  border: 0;
  border-top: 4px solid #111;
  border-radius: 0;
  padding: 12px 0 0;
}

.rail-card h2 {
  font-family: var(--sans);
  font-size: 22px;
}

.brief-grid span,
.topic-cloud a,
.source-leaders span,
.edition-policy span {
  border-radius: 0;
  background: #fff;
}

.topic-cloud {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.topic-cloud a {
  flex: none;
}

.section-stack {
  grid-row: 3;
  gap: 24px;
}

.story-section {
  border: 0;
  border-top: 4px solid #111;
  border-radius: 0;
  padding: 14px 0 0;
}

.section-title {
  align-items: center;
  margin-bottom: 6px;
}

.section-title h2 {
  font-family: var(--sans);
  font-size: 24px;
}

.story-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 22px;
  row-gap: 0;
}

.story-card {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 12px;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  padding: 10px 0;
  background: transparent;
  box-shadow: none;
  overflow: visible;
}

.story-card--editorial,
.story-card--text-only {
  background: transparent;
}

.story-card--editorial :deep(.event-cover) {
  border-bottom: 0;
}

.story-card--text-only {
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
  border-left: 4px solid rgba(18, 98, 77, 0.28);
  padding-left: 12px;
}

.story-card:hover {
  transform: none;
  border-color: #111;
  background: #fff;
  box-shadow: none;
}

.story-card__body {
  padding: 0;
}

.story-card h3 {
  margin: 4px 0 6px;
  font-family: var(--serif);
  font-size: 16px;
  line-height: 1.22;
}

.story-card__meta,
.story-card__foot {
  gap: 8px;
  justify-content: flex-start;
}

.story-card__meta span,
.story-card__foot span,
.source-strip span {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--muted);
}

.story-card__sample {
  min-height: 0;
  margin-bottom: 8px;
}

.story-card :deep(.event-cover--card) {
  min-height: 82px;
  border-radius: 0;
  aspect-ratio: 4 / 3;
}

.event-cover,
.event-cover--card,
.event-cover--strip,
.event-cover--large,
.ground-home :deep(.event-cover),
.ground-home :deep(.event-cover--card),
.ground-home :deep(.event-cover__label),
.ground-home :deep(.event-cover--card .event-cover__label) {
  border-radius: 0;
}

.ground-home :deep(.event-cover__image),
.ground-home :deep(.event-cover--list .event-cover__image) {
  object-fit: cover;
}

.story-card :deep(.event-cover__label),
.front-headline :deep(.event-cover__label) {
  display: none;
}

.ground-home :deep(.event-cover--card .event-cover__label),
.ground-home :deep(.event-cover--card.has-image .event-cover__label),
.story-grid .story-card :deep(.event-cover__label) {
  display: none !important;
}

.ground-home :deep(.event-cover__label) {
  left: 0;
  right: 0;
  bottom: 0;
  padding: 8px 10px;
  background: rgba(0, 0, 0, 0.66);
}

.ground-home :deep(.bias-meter__bar) {
  border-radius: 0;
}

.home-loading,
.home-error,
.pulse-cover,
.home-error button {
  border-radius: 0;
}

@media (max-width: 1280px) {
  .home-layout,
  .home-hero,
  .front-grid,
  .lead-feature {
    grid-template-columns: 1fr;
  }

  .front-column {
    height: auto;
    min-height: 0;
  }

  .front-column__list {
    overflow: visible;
  }

  .right-rail {
    grid-column: 1;
    grid-row: auto;
    position: static;
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .story-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .ground-home {
    width: 100vw;
    max-width: 100vw;
    box-sizing: border-box;
    padding: 72px 12px 28px;
    overflow-x: hidden;
  }

  .ground-subnav,
  .home-hero,
  .section-nav,
  .home-layout {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
  }

  .ground-subnav {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    border-radius: 14px;
    overflow: hidden;
  }

  .ground-subnav span {
    display: none;
  }

  .ground-subnav a {
    flex: none;
    min-width: 0;
    padding: 0 6px;
    font-size: 14px;
  }

  .lead-stats,
  .story-grid {
    grid-template-columns: 1fr;
  }

  .home-hero__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .home-hero__metrics article:nth-child(3) {
    grid-column: 1 / -1;
  }

  .home-hero__metrics strong {
    font-size: 17px;
  }

  .home-hero__metrics span {
    font-size: 10px;
  }

  .home-hero {
    gap: 10px;
    padding-top: 4px;
  }

  .home-hero h1 {
    font-size: clamp(30px, 9.5vw, 40px);
    line-height: 1.06;
  }

  .home-hero p {
    font-size: 12px;
    line-height: 1.45;
  }

  .hero-actions {
    margin-top: 8px;
  }

  .home-hero__metrics article {
    padding: 7px 9px;
  }

  .section-nav {
    gap: 6px;
    margin-bottom: 12px;
    padding: 7px 0;
  }

  .section-nav button {
    flex: 1 1 calc(50% - 6px);
    justify-content: space-between;
    min-width: 0;
    padding: 0 10px;
  }

  .section-nav button span {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .lead-cover {
    min-height: 0;
  }

  .lead-cover :deep(.event-cover),
  .ground-home :deep(.event-cover--large) {
    min-height: 320px;
  }

  .lead-story {
    padding: 14px;
  }

  .section-title {
    display: grid;
  }

  .lead-brief__head {
    display: grid;
  }

  .lead-brief__head strong {
    max-width: none;
    text-align: left;
  }
}

/* QA-02/QA-03: source-verifiable target sizing and a mobile section shortcut. */
.ground-home button,
.ground-home .ground-subnav a,
.ground-home .primary-link,
.ground-home .ghost-link,
.ground-home .story-link,
.ground-home .front-headline,
.ground-home .lead-cover,
.ground-home .lead-brief__item,
.ground-home .lead-title,
.ground-home .topic-cloud a,
.ground-home .blindspot-list a,
.ground-home .chain-list a,
.ground-home .latest-list a,
.ground-home .story-card {
  box-sizing: border-box;
  min-height: 44px;
  touch-action: manipulation;
}

.ground-home .ground-subnav a,
.ground-home .primary-link,
.ground-home .ghost-link,
.ground-home .story-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

@media (max-width: 760px) {
  .ground-home .section-nav {
    position: sticky;
    top: 64px;
    z-index: 14;
    flex-wrap: nowrap;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scrollbar-width: thin;
    background: rgba(246, 246, 244, 0.96);
    backdrop-filter: blur(10px);
  }

  .ground-home .section-nav button {
    flex: 0 0 auto;
    min-width: 136px;
    min-height: 44px;
    scroll-snap-align: start;
  }

  .ground-home .story-section,
  .ground-home #front-news,
  .ground-home #lead-story {
    scroll-margin-top: 124px;
  }
}
</style>
