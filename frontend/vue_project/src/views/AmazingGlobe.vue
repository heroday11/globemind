<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import coverageFallback from '@/assets/ground-news/coverage-atlas.webp'

const LOAD_TIMEOUT_MS = 10_000
const iframeKey = ref(0)
const globeFrame = ref(null)
const stateTitle = ref(null)
const loadState = ref('loading')
let loadTimer = null
let focusFrameAfterRetry = false

const iframeSrc = computed(() => {
  const b = import.meta.env.BASE_URL || '/'
  const prefix = b.endsWith('/') ? b : `${b}/`
  return `${prefix}amazing-globe/index.html?healthAttempt=${iframeKey.value}`
})

function clearLoadTimer() {
  if (loadTimer === null) return
  window.clearTimeout(loadTimer)
  loadTimer = null
}

function startLoadTimer() {
  clearLoadTimer()
  loadState.value = 'loading'
  loadTimer = window.setTimeout(() => {
    loadTimer = null
    if (loadState.value !== 'ready') loadState.value = 'timeout'
  }, LOAD_TIMEOUT_MS)
}

function handleFrameMessage(event) {
  if (event.origin !== window.location.origin) return
  if (event.source !== globeFrame.value?.contentWindow) return
  if (String(event.data?.attempt) !== String(iframeKey.value)) return
  if (loadState.value !== 'loading') return
  if (event.data?.type === 'globemind:globe-ready') {
    clearLoadTimer()
    loadState.value = 'ready'
    if (focusFrameAfterRetry) {
      focusFrameAfterRetry = false
      nextTick(() => globeFrame.value?.focus())
    }
  } else if (event.data?.type === 'globemind:globe-error') {
    clearLoadTimer()
    loadState.value = 'error'
  }
}

function handleFrameError(event) {
  if (event?.currentTarget !== globeFrame.value) return
  if (loadState.value !== 'loading') return
  clearLoadTimer()
  loadState.value = 'error'
}

function retry() {
  focusFrameAfterRetry = true
  iframeKey.value += 1
  startLoadTimer()
  nextTick(() => stateTitle.value?.focus())
}

onMounted(() => {
  window.addEventListener('message', handleFrameMessage)
  startLoadTimer()
})

onUnmounted(() => {
  clearLoadTimer()
  window.removeEventListener('message', handleFrameMessage)
})
</script>

<template>
  <div class="globe-shell">
    <iframe
      :key="iframeKey"
      ref="globeFrame"
      :src="iframeSrc"
      class="globe-iframe"
      title="3D 地球态势视图"
      :tabindex="loadState === 'ready' ? 0 : -1"
      :aria-hidden="loadState === 'ready' ? undefined : 'true'"
      @error="handleFrameError"
    />
    <section v-if="loadState !== 'ready'" class="globe-fallback" :aria-busy="loadState === 'loading'" aria-labelledby="globe-state-title" aria-live="polite" aria-atomic="true">
      <img :src="coverageFallback" alt="" />
      <div class="globe-fallback__veil"></div>
      <div class="globe-fallback__content">
        <p class="globe-fallback__eyebrow">GEOSPATIAL VIEW</p>
        <h1 id="globe-state-title" ref="stateTitle" tabindex="-1">
          {{ loadState === 'loading' ? '正在加载地球视图' : '地球视图暂时不可用' }}
        </h1>
        <p v-if="loadState === 'loading'">最多等待 10 秒；若渲染未就绪，将自动显示可操作的降级入口。</p>
        <p v-else>当前显示的是静态覆盖示意，不代表实时地理态势。你可以重试，或转到新闻检索和观察台继续研究。</p>
        <div v-if="loadState !== 'loading'" class="globe-fallback__actions">
          <button type="button" @click="retry">重新加载</button>
          <router-link to="/data-service/data-search">新闻检索</router-link>
          <router-link to="/data-service/ground-news">全球新闻观察台</router-link>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.globe-shell {
  position: relative;
  margin-top: 64px;
  width: 100%;
  height: calc(100vh - 64px);
  min-height: 320px;
  box-sizing: border-box;
  background: #000;
  overflow: hidden;
}

.globe-iframe {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  vertical-align: top;
}

.globe-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #eaf2ff;
  background: #07111f;
}

.globe-fallback > img,
.globe-fallback__veil {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.globe-fallback > img { object-fit: cover; opacity: 0.48; }
.globe-fallback__veil { background: linear-gradient(145deg, rgba(2, 8, 23, 0.7), rgba(10, 30, 58, 0.9)); }

.globe-fallback__content {
  position: relative;
  z-index: 1;
  width: min(680px, calc(100% - 36px));
  padding: clamp(24px, 5vw, 48px);
  box-sizing: border-box;
  border: 1px solid rgba(148, 185, 232, 0.28);
  border-radius: 24px;
  background: rgba(5, 18, 36, 0.82);
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.38);
  backdrop-filter: blur(12px);
}

.globe-fallback__eyebrow { margin: 0 0 10px; color: #7dd3fc; font: 750 0.72rem/1.4 ui-monospace, monospace; letter-spacing: 0.14em; }
.globe-fallback h1 { margin: 0; color: #f8fbff; font-size: clamp(1.8rem, 5vw, 3.2rem); line-height: 1.08; }
.globe-fallback h1:focus-visible { outline: 3px solid #7dd3fc; outline-offset: 6px; }
.globe-fallback__content > p:not(.globe-fallback__eyebrow) { margin: 16px 0 0; color: #bfd0e7; font-size: 1rem; line-height: 1.75; }
.globe-fallback__actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
.globe-fallback__actions button,
.globe-fallback__actions a { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 9px 15px; box-sizing: border-box; border: 1px solid #60a5fa; border-radius: 10px; color: #eff6ff; background: rgba(37, 99, 235, 0.82); font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
.globe-fallback__actions a { border-color: rgba(148, 185, 232, 0.4); background: rgba(15, 38, 68, 0.8); }

@media (max-width: 560px) {
  .globe-fallback__actions { flex-direction: column; }
  .globe-fallback__actions button,
  .globe-fallback__actions a { width: 100%; }
}
</style>
