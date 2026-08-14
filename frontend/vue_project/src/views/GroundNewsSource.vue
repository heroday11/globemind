<template>
  <main class="source-page">
    <header class="source-hero">
      <RouterLink class="back-link" to="/data-service/ground-news">Ground News 首页</RouterLink>
      <span class="eyebrow">Source Profile</span>
      <h1>{{ sourceModel.sourceName }}</h1>
      <p>{{ sourceModel.domain }} · {{ sourceModel.country }} · {{ sourceModel.labels.sourceType }}</p>
      <p class="source-scope-note">本页展示来源目录元数据，不评价某篇报道的事实真伪；跨国家政治标签不可直接横向比较。</p>
    </header>

    <section v-if="loading" class="state-card">正在加载来源画像...</section>
    <section v-else-if="error" class="state-card error">{{ error }}</section>

    <template v-else>
      <section class="source-layout">
        <aside class="profile-panel">
          <article>
            <span>第三方政治目录标签</span>
            <strong>{{ sourceModel.labels.politicalLeaning }}</strong>
            <small>{{ sourceModel.labels.reviewStatus }}</small>
          </article>
          <article>
            <span>第三方目录评级</span>
            <strong>{{ sourceModel.labels.credibility }}</strong>
            <small>{{ sourceModel.labels.confidence }}</small>
          </article>
          <article>
            <span>所有权记录</span>
            <strong>{{ sourceModel.labels.ownership }}</strong>
            <small>{{ sourceModel.labels.geoAlignment }}</small>
          </article>
          <a v-if="safeEvidenceUrl" class="evidence-link" :href="safeEvidenceUrl" target="_blank" rel="noopener noreferrer">
            查看评级证据
          </a>
        </aside>

        <section class="source-main">
          <article class="note-card">
            <div class="block-head">
              <span>方法说明</span>
              <small>{{ sourceModel.method.schemaVersion }} · {{ sourceModel.method.profileVersion }}</small>
            </div>
            <strong>{{ sourceModel.method.status }}</strong>
            <p>{{ sourceModel.method.limitation || '目录标签不代表独立事实准确率或来源可靠性结论。' }}</p>
            <ul v-if="sourceModel.method.methods.length" class="method-list">
              <li v-for="method in sourceModel.method.methods" :key="method.id">
                <strong>{{ method.label }} · {{ method.version }}</strong>
                <small>{{ method.scope }}</small>
              </li>
            </ul>
            <p v-else>没有可验证的受控方法映射；相关评级保持未知。</p>
            <small v-if="sourceModel.method.unknownMethodCount">
              另有 {{ sourceModel.method.unknownMethodCount }} 个未知方法标记，未用于评级展示。
            </small>
            <ul v-if="sourceModel.method.notes.length" class="method-notes">
              <li v-for="note in sourceModel.method.notes" :key="note">{{ note }}</li>
            </ul>
          </article>

          <article class="story-block">
            <div class="block-head">
              <span>收录事件</span>
              <small>{{ stories.length }}</small>
            </div>
            <div class="story-grid">
              <RouterLink v-for="story in stories" :key="story.cluster_id" class="story-card" :to="storyPath(story)">
                <strong>{{ story.display_title }}</strong>
                <small>{{ familyLabel(story.event_family) }} · {{ story.article_count }} 新闻 · {{ story.source_count }} 信源</small>
              </RouterLink>
            </div>
          </article>

          <article class="story-block">
            <div class="block-head">
              <span>近期报道</span>
              <small>{{ recentArticles.length }}</small>
            </div>
            <div class="article-list">
              <template v-for="item in recentArticles" :key="item.news_id">
                <a v-if="item.safe_url" :href="item.safe_url" target="_blank" rel="noopener noreferrer">
                  <span>{{ formatDate(item.published_at) }}</span>
                  <strong>{{ item.title }}</strong>
                </a>
                <div v-else class="article-link-unavailable" aria-disabled="true" title="原文链接不可用">
                  <span>{{ formatDate(item.published_at) }}</span>
                  <strong>{{ item.title }}</strong>
                </div>
              </template>
            </div>
          </article>

          <article class="story-block">
            <div class="block-head">
              <span>同类目录来源</span>
              <small>{{ similarSources.length }}</small>
            </div>
            <div class="source-grid">
              <RouterLink v-for="source in similarSources" :key="source.domain" class="peer-card" :to="sourcePath(source.domain)">
                <strong>{{ source.sourceName }}</strong>
                <small>{{ source.labels.politicalLeaning }} · {{ source.labels.credibility }}</small>
              </RouterLink>
            </div>
          </article>
        </section>
      </section>
    </template>
  </main>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  buildGroundNewsSourceProfileModel,
  familyLabel,
  groundNewsApi,
} from '@/features/ground-news/index.js'
import { safeExternalHttpUrl } from '@/utils/externalUrl.js'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const data = ref({})

const domain = computed(() => String(route.params.domain || ''))
const profile = computed(() => data.value.profile || {})
const sourceModel = computed(() => buildGroundNewsSourceProfileModel(profile.value))
const safeEvidenceUrl = computed(() => safeExternalHttpUrl(sourceModel.value.evidenceUrl))
const stories = computed(() => data.value.stories || [])
const recentArticles = computed(() => (
  Array.isArray(data.value.recent_articles)
    ? data.value.recent_articles.map((item) => ({
        ...item,
        safe_url: safeExternalHttpUrl(item.url),
      }))
    : []
))
const similarSources = computed(() => (
  Array.isArray(data.value.similar_sources)
    ? data.value.similar_sources.map((source) => buildGroundNewsSourceProfileModel(source))
    : []
))

watch(() => route.params.domain, () => {
  void load()
})

onMounted(() => {
  void load()
})

async function load() {
  if (!domain.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await groundNewsApi.getSource(domain.value)
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function storyPath(story) {
  return { path: '/data-service/ground-news-desk', query: { cluster_id: story.cluster_id } }
}

function sourcePath(value) {
  return `/data-service/ground-news-source/${encodeURIComponent(value || '')}`
}

function formatDate(value) {
  return value ? String(value).slice(0, 10) : '无日期'
}

</script>

<style scoped>
.source-page {
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

.source-hero,
.source-layout,
.state-card {
  max-width: 1360px;
  margin: 0 auto;
}

.source-hero {
  margin-bottom: 18px;
}

.back-link,
.eyebrow,
.block-head,
.profile-panel span {
  color: #8b6a38;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  text-decoration: none;
}

.source-hero h1 {
  margin: 8px 0;
  font-family: var(--serif);
  font-size: clamp(42px, 6vw, 86px);
  line-height: 0.95;
  letter-spacing: -0.045em;
}

.source-hero p {
  color: var(--muted);
}

.source-hero .source-scope-note {
  max-width: 860px;
  border-left: 3px solid #8b6a38;
  padding-left: 12px;
  color: #4c5963;
  line-height: 1.6;
}

.source-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.profile-panel,
.source-main {
  display: grid;
  gap: 12px;
}

.profile-panel article,
.note-card,
.story-block,
.state-card {
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 16px;
  background: rgba(255, 250, 240, 0.88);
  box-shadow: 0 18px 44px rgba(64, 78, 88, 0.1);
}

.profile-panel strong {
  display: block;
  margin: 6px 0;
  font-family: var(--serif);
  font-size: 30px;
  line-height: 1;
}

.profile-panel small,
.note-card p,
.story-card small,
.peer-card small,
.article-list span {
  color: var(--muted);
}

.evidence-link {
  border-radius: 999px;
  padding: 10px 12px;
  background: var(--navy);
  color: #fffaf0;
  text-align: center;
  text-decoration: none;
  font-weight: 900;
}

.block-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
}

.story-grid,
.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.story-card,
.peer-card,
.article-list a,
.article-link-unavailable {
  border: 1px solid rgba(16, 34, 52, 0.12);
  border-radius: 18px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.62);
  color: inherit;
  display: grid;
  gap: 6px;
  text-decoration: none;
}

.article-link-unavailable {
  opacity: 0.72;
}

.article-list {
  display: grid;
  gap: 9px;
}

.story-card strong,
.peer-card strong,
.article-list strong {
  line-height: 1.35;
}

@media (max-width: 900px) {
  .source-page {
    padding: 76px 12px 24px;
  }

  .source-layout {
    grid-template-columns: 1fr;
  }
}

.source-page .back-link,
.source-page .evidence-link,
.source-page .story-card,
.source-page .peer-card,
.source-page .article-list a,
.source-page .article-link-unavailable {
  box-sizing: border-box;
  min-height: 44px;
  touch-action: manipulation;
}

.source-page .back-link,
.source-page .evidence-link {
  display: inline-flex;
  align-items: center;
}

.source-page .evidence-link {
  justify-content: center;
}
</style>
