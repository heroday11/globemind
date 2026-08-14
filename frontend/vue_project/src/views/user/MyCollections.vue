<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { API_PREFIX } from '@/config/api'
import { getAuthChangedEventName, getToken } from '@/utils/auth'
import { createLatestRequestGate } from '@/utils/latestRequest'
import { getFavoritesMap } from '@/utils/reportFavorites'
import {
  collectBrowserFavorites,
  mergeFavoriteSources,
  normalizeServerFavorites,
} from '@/features/collections'

const router = useRouter()
const loading = ref(true)
const items = ref([])
const infoBanner = ref('')
const searchText = ref('')
const favoritesLoadGate = createLatestRequestGate()
let favoritesAbortController = null

const FAV_EVENT = 'reportFavoritesUpdated'

async function readBoundedJson(response, maxBytes) {
  const contentType = String(response.headers?.get?.('content-type') || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase()
  if (contentType !== 'application/json' && !contentType.endsWith('+json')) {
    throw new TypeError('response is not JSON')
  }
  const declaredLength = Number(response.headers?.get?.('content-length'))
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new RangeError('response is too large')
  }
  const text = await response.text()
  if (new TextEncoder().encode(text).byteLength > maxBytes) {
    throw new RangeError('response is too large')
  }
  return JSON.parse(text)
}

async function fetchArticleTitle(newsId, token, signal) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    const r = await fetch(`${API_PREFIX}/article/${newsId}`, { headers, signal })
    if (!r.ok) return ''
    const d = await readBoundedJson(r, 256 * 1024)
    return String(d?.title || '').trim()
  } catch (error) {
    if (error?.name === 'AbortError' || signal.aborted) throw error
    return ''
  }
}

async function fillMissingTitles(rows, token, signal) {
  const need = rows.filter((r) => !r.title)
  const chunk = 6
  for (let i = 0; i < need.length; i += chunk) {
    const part = need.slice(i, i + chunk)
    await Promise.all(
      part.map(async (r) => {
        const t = await fetchArticleTitle(r.id, token, signal)
        r.title = t || `新闻 #${r.id}`
      }),
    )
  }
}

async function load() {
  favoritesAbortController?.abort()
  const controller = new AbortController()
  favoritesAbortController = controller
  const isCurrent = favoritesLoadGate.begin()
  loading.value = true
  infoBanner.value = ''
  items.value = []

  let nextInfoBanner = ''
  let rows = []
  try {
    const token = getToken()
    const browserCollection = collectBrowserFavorites(getFavoritesMap())

    let accountRecords = []
    let accountInvalidRecords = 0
    if (token) {
      try {
        const res = await fetch(`${API_PREFIX}/user/favorites`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        })
        if (res.ok) {
          const data = await readBoundedJson(res, 128 * 1024)
          const normalizedAccount = normalizeServerFavorites(data)
          accountRecords = normalizedAccount.records
          accountInvalidRecords = normalizedAccount.counts.invalidRecords
          nextInfoBanner =
            '账号收藏与本机临时收藏仅在本页按新闻 ID 去重展示；不会上传或改写任一来源。服务器返回的预警不会作为收藏显示。'
        } else if (res.status === 401 || res.status === 403) {
          nextInfoBanner =
            '登录状态无效，当前仅显示本机临时收藏；本页不会上传或改写这些记录。'
        } else {
          nextInfoBanner =
            '账号收藏暂不可用，当前仅显示本机临时收藏；服务器错误正文未展示。'
        }
      } catch (error) {
        if (error?.name === 'AbortError' || controller.signal.aborted) return
        nextInfoBanner =
          '账号收藏响应不可用，当前仅显示本机临时收藏；服务器错误正文未展示。'
      }
    } else {
      nextInfoBanner =
        '当前未登录：仅显示本浏览器的临时收藏。本页不会上传或改写这些记录，登录后也不会自动迁移。'
    }

    const merged = mergeFavoriteSources(accountRecords, browserCollection.records)
    rows = merged.rows.map((row) => ({ ...row }))
    if (merged.truncated || browserCollection.truncated) {
      nextInfoBanner += ' 当前列表已达到有界展示上限，不能据此推断完整收藏总数。'
    }
    const invalidRecords = accountInvalidRecords + browserCollection.invalidRecords
    if (invalidRecords > 0) {
      nextInfoBanner += ` 另有 ${invalidRecords} 条格式无效记录未显示。`
    }
    await fillMissingTitles(rows, token, controller.signal)
  } catch (error) {
    if (error?.name === 'AbortError' || controller.signal.aborted) return
    nextInfoBanner = '本机收藏暂时无法读取，请刷新后重试。'
    rows = []
  }

  if (!isCurrent()) return
  items.value = rows
  infoBanner.value = nextInfoBanner
  loading.value = false
}

const filteredItems = computed(() => {
  if (!searchText.value.trim()) return items.value
  const q = searchText.value.trim().toLowerCase()
  return items.value.filter(
    (r) =>
      r.title.toLowerCase().includes(q) ||
      String(r.id).includes(q) ||
      (r.topic && r.topic.toLowerCase().includes(q)),
  )
})

function openNews(id) {
  router.push(`/data-service/news/${id}`)
}

function onFavoritesUpdated() {
  load()
}

onMounted(() => {
  load()
  window.addEventListener(FAV_EVENT, onFavoritesUpdated)
  window.addEventListener(getAuthChangedEventName(), onFavoritesUpdated)
})

onActivated(() => {
  load()
})

onDeactivated(() => {
  favoritesAbortController?.abort()
  favoritesLoadGate.invalidate()
})

onUnmounted(() => {
  favoritesAbortController?.abort()
  favoritesLoadGate.invalidate()
  window.removeEventListener(FAV_EVENT, onFavoritesUpdated)
  window.removeEventListener(getAuthChangedEventName(), onFavoritesUpdated)
})
</script>

<template>
  <div class="mc-root" :aria-busy="loading">
    <!-- Header -->
    <header class="mc-header">
      <div class="mc-header-body">
        <div class="mc-header-overline">FAVORITES</div>
        <h1 class="mc-header-title">我的收录</h1>
        <p class="mc-header-desc">账号与本机记录仅在本页按新闻 ID 去重展示，不代表同步或集合合并</p>
      </div>
      <div class="mc-header-stat" v-if="items.length">
        <span class="mc-header-count">{{ items.length }}</span>
        <span class="mc-header-unit">当前显示</span>
      </div>
    </header>

    <!-- Info banner -->
    <div
      v-if="infoBanner && !loading"
      class="mc-banner"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <svg class="mc-banner-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      <span>{{ infoBanner }}</span>
    </div>

    <!-- Search -->
    <div class="mc-search-bar" v-if="items.length > 0 && !loading">
      <svg class="mc-search-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
      <label class="mc-visually-hidden" for="collection-search">筛选我的收录</label>
      <input
        id="collection-search"
        v-model="searchText"
        type="text"
        name="collection-search"
        class="mc-search-input"
        autocomplete="off"
        placeholder="搜索标题、ID 或主题…"
      />
    </div>

    <!-- Loading -->
    <div v-if="loading" class="mc-state" role="status" aria-live="polite" aria-atomic="true">
      <div class="mc-state-card">
        <div class="mc-spinner" aria-hidden="true"></div>
        <span class="mc-state-text">加载中…</span>
      </div>
    </div>

    <!-- Empty -->
    <div v-else-if="items.length === 0" class="mc-empty" role="status" aria-live="polite">
      <div class="mc-empty-card">
        <svg class="mc-empty-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 22 12 18.56 5.82 22 7 14.14 2 9.27l6.91-1.01L12 2z" />
        </svg>
        <h2 class="mc-empty-title">暂无收录</h2>
        <p class="mc-empty-desc">
          在<strong>数据搜索</strong>结果或新闻详情中点击星标收藏。本页不会上传或改写本机记录，也不会自动合并账号集合。
        </p>
        <button type="button" class="mc-btn mc-btn--primary" @click="router.push('/data-service/data-search')">
          <span>前往数据搜索</span>
          <svg class="mc-btn-arrow" aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
        </button>
      </div>
    </div>

    <!-- List -->
    <div v-else class="mc-list">
      <p
        class="mc-list-summary"
        v-if="searchText && filteredItems.length !== items.length"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        筛选结果：{{ filteredItems.length }} / {{ items.length }} 条
      </p>
      <p v-if="filteredItems.length === 0" class="mc-filter-empty" role="status">
        没有与“{{ searchText }}”匹配的收录，请调整筛选词。
      </p>
      <button
        v-for="(row, idx) in filteredItems"
        :key="row.id"
        type="button"
        class="mc-item"
        :style="{ animationDelay: `${idx * 0.03}s` }"
        @click="openNews(row.id)"
      >
        <div class="mc-item-accent"></div>
        <div class="mc-item-body">
          <h3 class="mc-item-title">{{ row.title }}</h3>
          <div class="mc-item-meta">
            <span class="mc-item-id">#{{ row.id }}</span>
            <span v-if="row.topic" class="mc-item-topic">{{ row.topic }}</span>
            <span class="mc-item-source">{{ row.sourceLabel }}</span>
          </div>
        </div>
        <div class="mc-item-arrow">
          <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
        </div>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* ============================================
   DESIGN TOKENS
   ============================================ */
.mc-root {
  --mc-surface: #ffffff;
  --mc-border: #e8e5f0;
  --mc-border-light: #f0edf6;
  --mc-text: #1a1824;
  --mc-text-secondary: #5c5870;
  --mc-text-muted: #8b869e;
  --mc-accent-soft: rgba(91, 114, 223, 0.08);
  --mc-shadow-sm: 0 1px 2px rgba(26, 24, 36, 0.04);
  --mc-shadow: 0 1px 3px rgba(26, 24, 36, 0.04), 0 4px 16px rgba(26, 24, 36, 0.05);
  --mc-radius-sm: 8px;
  --mc-radius: 14px;
  --mc-radius-lg: 20px;
  --mc-transition: 0.22s cubic-bezier(0.33, 1, 0.68, 1);

  width: 100%;
  max-width: 860px;
  min-width: 0;
  margin: 0 auto;
  animation: mc-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.mc-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@keyframes mc-fade-up {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ---- Header ---- */
.mc-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 24px;
  padding: 30px 34px;
  background: var(--mc-surface);
  border-radius: var(--mc-radius-lg);
  border: 1px solid var(--mc-border);
  box-shadow: var(--mc-shadow);
}

.mc-header-overline {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--uc-accent, #5b72df);
  margin-bottom: 8px;
}

.mc-header-title {
  margin: 0;
  font-size: 1.8rem;
  font-weight: 750;
  color: var(--mc-text);
  letter-spacing: -0.02em;
  line-height: 1.15;
}

.mc-header-desc {
  margin: 6px 0 0;
  font-size: 0.92rem;
  color: var(--mc-text-muted);
  font-weight: 460;
  overflow-wrap: anywhere;
}

.mc-header-desc strong {
  color: var(--mc-text-secondary);
  font-weight: 620;
}

.mc-header-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 22px;
  background: var(--mc-accent-soft);
  border-radius: var(--mc-radius);
  flex-shrink: 0;
}

.mc-header-count {
  font-size: 2rem;
  font-weight: 800;
  color: var(--uc-accent, #5b72df);
  letter-spacing: -0.03em;
  line-height: 1;
}

.mc-header-unit {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--mc-text-secondary);
  margin-top: 4px;
}

/* ---- Banner ---- */
.mc-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 20px;
  padding: 13px 18px;
  font-size: 0.85rem;
  line-height: 1.6;
  font-weight: 500;
  color: var(--mc-text-secondary);
  background: #faf9fd;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  animation: mc-fade-up 0.4s ease both;
  overflow-wrap: anywhere;
}

.mc-banner-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: var(--uc-accent, #5b72df);
  opacity: 0.6;
  margin-top: 1px;
}

/* ---- Search ---- */
.mc-search-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
  padding: 10px 16px;
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  box-shadow: var(--mc-shadow-sm);
  transition: border-color var(--mc-transition);
}

.mc-search-bar:focus-within {
  border-color: var(--uc-accent, #5b72df);
  box-shadow: 0 0 0 3px var(--mc-accent-soft);
}

.mc-search-icon {
  width: 17px;
  height: 17px;
  color: var(--mc-text-muted);
  flex-shrink: 0;
}

.mc-search-input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  border: none;
  outline: none;
  background: transparent;
  font-size: 0.9rem;
  font-weight: 460;
  color: var(--mc-text);
  font-family: inherit;
}

.mc-search-input::placeholder {
  color: #c4bfd2;
}

/* ---- State ---- */
.mc-state {
  display: flex;
  justify-content: center;
  padding: 48px 0;
}

.mc-state-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 28px;
  background: var(--mc-surface);
  border-radius: var(--mc-radius);
  border: 1px solid var(--mc-border);
  box-shadow: var(--mc-shadow-sm);
}

.mc-spinner {
  width: 18px;
  height: 18px;
  border: 2.5px solid var(--mc-border);
  border-top-color: var(--uc-accent, #5b72df);
  border-radius: 50%;
  animation: mc-spin 0.7s linear infinite;
}

@keyframes mc-spin {
  to { transform: rotate(360deg); }
}

.mc-state-text {
  font-size: 0.9rem;
  font-weight: 560;
  color: var(--mc-text-muted);
}

/* ---- Empty ---- */
.mc-empty {
  animation: mc-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.08s both;
}

.mc-empty-card {
  background: var(--mc-surface);
  border-radius: var(--mc-radius-lg);
  border: 1px solid var(--mc-border);
  box-shadow: var(--mc-shadow);
  padding: clamp(48px, 8vw, 72px) clamp(24px, 5vw, 48px);
  text-align: center;
}

.mc-empty-icon {
  width: 56px;
  height: 56px;
  color: var(--uc-accent, #5b72df);
  opacity: 0.4;
  margin-bottom: 20px;
}

.mc-empty-title {
  margin: 0 0 12px;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--mc-text);
  letter-spacing: -0.01em;
}

.mc-empty-desc {
  margin: 0 auto 28px;
  max-width: 420px;
  font-size: 0.92rem;
  line-height: 1.65;
  color: var(--mc-text-muted);
}

.mc-empty-desc strong {
  color: var(--mc-text-secondary);
  font-weight: 620;
}

.mc-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 22px;
  border-radius: var(--mc-radius-sm);
  font-size: 0.9rem;
  font-weight: 620;
  cursor: pointer;
  font-family: inherit;
  min-height: 44px;
  transition: all 0.18s ease;
}

.mc-btn--primary {
  background: var(--uc-accent, #5b72df);
  color: #fff;
  border: none;
  box-shadow: 0 2px 8px rgba(91, 114, 223, 0.28);
}

.mc-btn--primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(91, 114, 223, 0.34);
}

.mc-btn-arrow {
  width: 16px;
  height: 16px;
  transition: transform 0.2s ease;
}

.mc-btn--primary:hover .mc-btn-arrow {
  transform: translateX(2px);
}

/* ---- List ---- */
.mc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  animation: mc-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.06s both;
}

.mc-list-summary {
  margin: 0;
  font-size: 0.82rem;
  color: var(--mc-text-muted);
  font-weight: 500;
  padding: 0 4px 4px;
}

.mc-filter-empty {
  margin: 0;
  padding: 24px;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  color: var(--mc-text-muted);
  background: var(--mc-surface);
  text-align: center;
  overflow-wrap: anywhere;
}

.mc-item {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 44px;
  text-align: left;
  padding: 0;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: var(--mc-surface);
  box-shadow: var(--mc-shadow-sm);
  cursor: pointer;
  overflow: hidden;
  transition:
    border-color var(--mc-transition),
    box-shadow var(--mc-transition);
  animation: mc-item-in 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
  font-family: inherit;
}

@keyframes mc-item-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

.mc-item:hover {
  border-color: rgba(91, 114, 223, 0.3);
  box-shadow: var(--mc-shadow);
}

.mc-item:focus-visible,
.mc-btn:focus-visible {
  outline: 3px solid rgba(91, 114, 223, 0.45);
  outline-offset: 3px;
}

.mc-item-accent {
  width: 3px;
  align-self: stretch;
  flex-shrink: 0;
  background: transparent;
  transition: background var(--mc-transition);
}

.mc-item:hover .mc-item-accent {
  background: var(--uc-accent, #5b72df);
}

.mc-item-body {
  flex: 1;
  padding: 15px 20px;
  min-width: 0;
}

.mc-item-title {
  margin: 0 0 6px;
  font-size: 0.94rem;
  font-weight: 620;
  color: var(--mc-text);
  letter-spacing: -0.01em;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.mc-item-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.mc-item-id {
  font-size: 0.78rem;
  font-weight: 620;
  color: var(--uc-accent, #5b72df);
  font-variant-numeric: tabular-nums;
}

.mc-item-topic {
  font-size: 0.78rem;
  font-weight: 490;
  color: var(--mc-text-muted);
  padding: 2px 9px;
  background: var(--mc-accent-soft);
  border-radius: 100px;
}

.mc-item-source {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--mc-text-secondary);
  overflow-wrap: anywhere;
}

.mc-item-arrow {
  padding: 0 16px;
  color: var(--mc-text-muted);
  opacity: 0;
  transform: translateX(-4px);
  transition: all 0.2s ease;
}

.mc-item:hover .mc-item-arrow,
.mc-item:focus-visible .mc-item-arrow {
  opacity: 1;
  transform: translateX(0);
  color: var(--uc-accent, #5b72df);
}

@media (prefers-reduced-motion: reduce) {
  .mc-root,
  .mc-banner,
  .mc-empty,
  .mc-list,
  .mc-item,
  .mc-spinner {
    animation: none;
  }

  .mc-search-bar,
  .mc-btn,
  .mc-btn-arrow,
  .mc-item,
  .mc-item-accent,
  .mc-item-arrow {
    transition: none;
  }
}

.mc-item-arrow svg {
  width: 18px;
  height: 18px;
}

/* ---- Responsive ---- */
@media (max-width: 640px) {
  .mc-header {
    flex-direction: column;
    padding: 22px 20px;
  }

  .mc-header-stat {
    margin-top: 14px;
    flex-direction: row;
    gap: 8px;
    padding: 8px 16px;
  }

  .mc-header-count {
    font-size: 1.4rem;
  }

  .mc-item-body {
    padding: 14px 16px;
  }
}
</style>
