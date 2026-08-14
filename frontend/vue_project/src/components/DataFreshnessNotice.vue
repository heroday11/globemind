<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { AlertTriangle, ChevronDown, X } from 'lucide-vue-next'
import {
  formatFreshnessCutoff,
  freshnessNoticeReportKey,
  freshnessStateLabel,
  useFeatureFreshness,
} from '@/features/operations/index.js'
import { getAuthChangedEventName } from '@/utils/auth.js'

const IDENTITY_CONTEXT_KEY = 'globemind:freshness-notice:identity-context'
const collapsed = ref(false)
const identityGeneration = ref(0)
const { report, refresh } = useFeatureFreshness()

function createIdentityContext() {
  try {
    const value = globalThis.crypto?.randomUUID?.()
    if (value) return value
  } catch {
    // The opaque context is presentation state, not an authentication credential.
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function readIdentityContext() {
  try {
    const current = sessionStorage.getItem(IDENTITY_CONTEXT_KEY)
    if (current && /^[a-zA-Z0-9-]{8,80}$/.test(current)) return current
    const created = createIdentityContext()
    sessionStorage.setItem(IDENTITY_CONTEXT_KEY, created)
    return created
  } catch {
    return createIdentityContext()
  }
}

const identityContext = ref(readIdentityContext())

const affectedFeatures = computed(() => report.value.affected || [])
const visible = computed(() => (
  report.value.phase === 'idle'
  || report.value.phase === 'error'
  || (report.value.phase === 'ready' && affectedFeatures.value.length > 0)
))
const cutoffLabel = computed(() => formatFreshnessCutoff(report.value.cutoff))
const noticeFingerprint = computed(() => [
  'v2',
  identityContext.value,
  identityGeneration.value,
  freshnessNoticeReportKey(report.value),
].join('|'))

const affectedSummary = computed(() => {
  const counts = new Map()
  for (const feature of affectedFeatures.value) {
    counts.set(feature.state, (counts.get(feature.state) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([state, count]) => `${freshnessStateLabel(state)} ${count} 项`)
    .join('，')
})

const noticeText = computed(() => {
  if (report.value.phase === 'idle') {
    return '正在核验业务数据更新时间。核验完成前，当前内容按历史资料处理。'
  }
  if (report.value.phase === 'error') {
    return '暂时无法核验业务数据更新时间。当前内容按历史资料处理，不得作为实时结论。'
  }
  const cutoff = cutoffLabel.value
    ? `；受影响数据的最早已知截止时间为 ${cutoffLabel.value}`
    : ''
  const summary = affectedSummary.value ? `（${affectedSummary.value}）` : ''
  return `部分业务数据处于历史、延迟、离线或未确认状态${summary}${cutoff}。当前仅按历史资料处理，不得据此作实时判断。`
})

const collapsedText = computed(() => (
  report.value.phase === 'ready'
    ? '部分业务数据不是可确认的当前状态。展开查看使用限制。'
    : '业务数据时效仍未核验。展开查看使用限制。'
))

function storageKey(fingerprint) {
  return `globemind:freshness-notice:collapsed:${fingerprint}`
}

function readCollapsed(fingerprint) {
  try {
    return sessionStorage.getItem(storageKey(fingerprint)) === 'collapsed'
  } catch {
    return false
  }
}

function toggleNotice() {
  collapsed.value = !collapsed.value
  try {
    if (collapsed.value) {
      sessionStorage.setItem(storageKey(noticeFingerprint.value), 'collapsed')
    } else {
      sessionStorage.removeItem(storageKey(noticeFingerprint.value))
    }
  } catch {
    // The notice remains operable for this render when storage is unavailable.
  }
}

watch(noticeFingerprint, (fingerprint) => {
  collapsed.value = readCollapsed(fingerprint)
}, { immediate: true })

function onIdentityChanged() {
  identityGeneration.value += 1
  identityContext.value = createIdentityContext()
  collapsed.value = false
  try {
    sessionStorage.setItem(IDENTITY_CONTEXT_KEY, identityContext.value)
  } catch {
    // In-memory isolation remains effective when storage is unavailable.
  }
}

function onAuthStorageChanged(event) {
  if (event?.key === 'access_token' || event?.key === 'current_user') onIdentityChanged()
}

onMounted(() => {
  void refresh()
  window.addEventListener(getAuthChangedEventName(), onIdentityChanged)
  window.addEventListener('storage', onAuthStorageChanged)
})

onUnmounted(() => {
  window.removeEventListener(getAuthChangedEventName(), onIdentityChanged)
  window.removeEventListener('storage', onAuthStorageChanged)
})
</script>

<template>
  <aside
    v-if="visible"
    class="freshness-notice"
    :class="{ 'is-collapsed': collapsed }"
    role="region"
    aria-labelledby="freshness-notice-title"
  >
    <AlertTriangle :size="18" aria-hidden="true" />
    <div id="freshness-notice-detail" class="freshness-notice__copy">
      <p
        id="freshness-notice-title"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ collapsed ? collapsedText : noticeText }}
      </p>
    </div>
    <button
      type="button"
      :aria-label="collapsed ? '展开业务数据时效提示' : '收起业务数据时效提示'"
      :aria-expanded="!collapsed"
      aria-controls="freshness-notice-detail"
      @click="toggleNotice"
    >
      <ChevronDown v-if="collapsed" :size="18" aria-hidden="true" />
      <X v-else :size="17" aria-hidden="true" />
    </button>
  </aside>
</template>

<style scoped>
.freshness-notice {
  position: relative;
  z-index: 2;
  width: min(920px, calc(100vw - 28px));
  min-height: 48px;
  margin: 72px auto 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 9px 8px 14px;
  border: 1px solid rgba(217, 119, 6, 0.32);
  border-radius: 12px;
  color: #78350f;
  background: #fffbeb;
  box-shadow: 0 8px 22px rgba(120, 53, 15, 0.1);
  font-size: 13px;
  font-weight: 700;
  line-height: 1.45;
}

.freshness-notice > svg { flex: 0 0 auto; color: #d97706; }
.freshness-notice__copy { flex: 1; min-width: 0; }
.freshness-notice p { margin: 0; overflow-wrap: anywhere; }
.freshness-notice.is-collapsed { min-height: 44px; padding-block: 0; box-shadow: none; }
.freshness-notice button {
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 9px;
  color: #92400e;
  background: rgba(245, 158, 11, 0.12);
  cursor: pointer;
}

.freshness-notice button:hover { background: rgba(245, 158, 11, 0.2); }
.freshness-notice button:focus-visible { outline: 3px solid #2563eb; outline-offset: 2px; }

@media (max-width: 720px) {
  .freshness-notice { width: calc(100vw - 20px); margin-top: 68px; align-items: flex-start; }
  .freshness-notice.is-collapsed { align-items: center; }
}

@media (prefers-reduced-motion: reduce) {
  .freshness-notice,
  .freshness-notice button {
    scroll-behavior: auto;
    transition: none;
    animation: none;
  }
}
</style>
