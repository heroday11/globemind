<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, LockKeyhole, X } from 'lucide-vue-next'
import { getAuthChangedEventName, getToken } from '@/utils/auth'

const DISMISSED_KEY = 'globemind:guest-access-banner:dismissed:v1'
const route = useRoute()
const router = useRouter()

function readHasToken() {
  try {
    return Boolean(getToken())
  } catch {
    return false
  }
}

function readDismissed() {
  try {
    return sessionStorage.getItem(DISMISSED_KEY) === 'dismissed'
  } catch {
    return false
  }
}

const hasToken = ref(readHasToken())
const dismissed = ref(readDismissed())
const actionStatus = ref('')

const hidden = computed(() => {
  const authPage = ['/login', '/register', '/forgot-password', '/reset-password'].includes(route.path)
  return hasToken.value || dismissed.value || authPage
})

function syncAuth() {
  hasToken.value = readHasToken()
}

function dismiss() {
  dismissed.value = true
  try {
    sessionStorage.setItem(DISMISSED_KEY, 'dismissed')
  } catch {
    // The current render still stays dismissed when storage is unavailable.
  }
}

function requestLogin() {
  actionStatus.value = '正在打开登录窗口。'
  window.dispatchEvent(new CustomEvent('showLoginModal'))
}

async function register() {
  actionStatus.value = '正在前往注册页面。'
  try {
    await router.push({ path: '/register', query: { redirect: route.fullPath } })
  } catch {
    actionStatus.value = '暂时无法打开注册页面，请稍后重试。'
  }
}

onMounted(() => window.addEventListener(getAuthChangedEventName(), syncAuth))
onUnmounted(() => window.removeEventListener(getAuthChangedEventName(), syncAuth))
</script>

<template>
  <Transition name="guest-access">
    <aside
      v-if="!hidden"
      class="guest-access"
      data-tour="guest-access"
      role="region"
      aria-labelledby="guest-access-title"
      aria-describedby="guest-access-description"
    >
      <div class="guest-access__icon" aria-hidden="true"><LockKeyhole :size="18" /></div>
      <div class="guest-access__copy">
        <strong id="guest-access-title">当前为访客模式</strong>
        <span id="guest-access-description">搜索与导出可直接使用；收藏仅临时保存在此浏览器。跨设备同步、数据助手、报告和个人配置需要登录。</span>
      </div>
      <div class="guest-access__actions">
        <button type="button" class="guest-access__login" @click="requestLogin">
          登录解锁 <ArrowRight :size="14" />
        </button>
        <button type="button" class="guest-access__register" @click="register">免费注册</button>
      </div>
      <button type="button" class="guest-access__close" aria-label="关闭访客模式提示" @click="dismiss">
        <X :size="15" />
      </button>
      <p class="guest-access__status" role="status" aria-live="polite" aria-atomic="true">{{ actionStatus }}</p>
    </aside>
  </Transition>
</template>

<style scoped>
.guest-access {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 120;
  width: min(430px, calc(100vw - 44px));
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  padding: 13px 54px 13px 13px;
  border: 1px solid rgba(37, 99, 235, 0.22);
  border-radius: 16px;
  color: #172033;
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 18px 55px rgba(30, 64, 175, 0.18), 0 2px 8px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(16px);
}

.guest-access__icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  color: #1d4ed8;
  background: #eaf1ff;
}

.guest-access__copy { display: grid; gap: 3px; min-width: 0; }
.guest-access__copy strong { font-size: 14px; line-height: 1.25; }
.guest-access__copy span { color: #526077; font-size: 12px; line-height: 1.45; }
.guest-access__actions { display: grid; justify-items: start; gap: 3px; }
.guest-access__actions button { white-space: nowrap; cursor: pointer; font-weight: 800; }
.guest-access__login { min-height: 44px; display: inline-flex; align-items: center; gap: 5px; padding: 0 11px; border: 0; border-radius: 9px; color: #fff; background: #1d4ed8; }
.guest-access__register { min-height: 44px; padding: 2px 7px; border: 0; color: #3157a5; background: transparent; font-size: 11px; }
.guest-access__close { position: absolute; top: 4px; right: 4px; width: 44px; height: 44px; display: grid; place-items: center; border: 0; border-radius: 8px; color: #64748b; background: transparent; cursor: pointer; }
.guest-access__close:hover { color: #1e293b; background: #f1f5f9; }
.guest-access button:focus-visible { outline: 3px solid rgba(29, 78, 216, 0.35); outline-offset: 2px; }
.guest-access__status { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.guest-access-enter-active, .guest-access-leave-active { transition: opacity .18s ease, transform .18s ease; }
.guest-access-enter-from, .guest-access-leave-to { opacity: 0; transform: translateY(8px); }

@media (max-width: 760px) {
  .guest-access { left: 12px; right: 12px; bottom: 12px; width: auto; grid-template-columns: 34px minmax(0, 1fr); }
  .guest-access__actions { grid-column: 2; grid-template-columns: auto auto; align-items: center; gap: 8px; }
}

@media (prefers-reduced-motion: reduce) {
  .guest-access-enter-active, .guest-access-leave-active { transition: none; }
}
</style>
