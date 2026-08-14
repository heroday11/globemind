<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  DISPLAY_PREFERENCES_EVENT,
  getDisplayPreferencesSnapshot,
} from '@/composables/useDisplayPreferences.js'

const router = useRouter()
const terminalFrame = ref(null)

function handleMessage(e) {
  if (e.origin !== window.location.origin) return
  if (e.source !== terminalFrame.value?.contentWindow) return
  if (e.data?.type === 'storeAndNavigate') {
    if (e.data.path !== '/data-assistant') return
    if (e.data.assistantContext && typeof e.data.assistantContext === 'object') {
      sessionStorage.setItem(
        'data_assistant_autorun_context_v1',
        JSON.stringify(e.data.assistantContext),
      )
    } else if (e.data.alertMsg) {
      sessionStorage.setItem('data_assistant_alert_msg', e.data.alertMsg)
    } else {
      return
    }
    sessionStorage.removeItem('data_assistant_alert_ready')
    // 停留 2s 展示加载动效
    setTimeout(() => {
      router.push('/data-assistant')
    }, 2000)
  }
  if (e.data?.type === 'alertReady') {
    sessionStorage.setItem('data_assistant_alert_ready', '1')
  }
}

function syncDisplayPreferences(preferences = getDisplayPreferencesSnapshot()) {
  const frameWindow = terminalFrame.value?.contentWindow
  if (!frameWindow) return
  let targetOrigin = window.location.origin
  try {
    targetOrigin = new URL(iframeSrc.value, window.location.href).origin
  } catch {
    // The computed source is always a valid same-site URL in production.
  }
  frameWindow.postMessage({ type: DISPLAY_PREFERENCES_EVENT, preferences }, targetOrigin)
}

function handleDisplayPreferences(event) {
  syncDisplayPreferences(event.detail)
}

onMounted(() => {
  window.addEventListener('message', handleMessage)
  window.addEventListener(DISPLAY_PREFERENCES_EVENT, handleDisplayPreferences)
})
onUnmounted(() => {
  window.removeEventListener('message', handleMessage)
  window.removeEventListener(DISPLAY_PREFERENCES_EVENT, handleDisplayPreferences)
})

/** 开发时可在 vue_project/.env.development 设置 VITE_FIN_TERMINAL_URL=http://127.0.0.1:5175/ 指向 React dev server */
const iframeSrc = computed(() => {
  const dev = import.meta.env.VITE_FIN_TERMINAL_URL
  if (dev) return dev
  const b = import.meta.env.BASE_URL || '/'
  const prefix = b.endsWith('/') ? b : `${b}/`
  return `${prefix}fin-terminal/index.html`
})
</script>

<template>
  <div class="financial-terminal-shell" data-tour="financial-terminal">
    <iframe
      ref="terminalFrame"
      :src="iframeSrc"
      class="financial-terminal-iframe"
      title="金融终端"
      @load="syncDisplayPreferences()"
    />
  </div>
</template>

<style scoped>
/* 与 appNav.vue 固定导航栏高度一致（.header height: 64px），避免底部露出主站浅色底 */
.financial-terminal-shell {
  margin-top: 64px;
  width: 100%;
  height: calc(100vh - 64px);
  min-height: 320px;
  box-sizing: border-box;
  background: #0b0e11;
  overflow: hidden;
}

.financial-terminal-iframe {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  vertical-align: top;
}
</style>
