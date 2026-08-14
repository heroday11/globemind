<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from './components/appNav.vue'
import NewUserGuide from './components/NewUserGuide.vue'
import DataFreshnessNotice from './components/DataFreshnessNotice.vue'
import GuestAccessBanner from './components/GuestAccessBanner.vue'
import { createOperationsHeartbeat } from './features/operations/index.js'
import { routePreloaders } from './router/routePreloaders.js'

const route = useRoute()
const routeContent = ref(null)
const isFinancialTerminal = computed(() => route.path === '/financial-terminal')
const operationsHeartbeat = createOperationsHeartbeat({
  getPath: () => route.path || '/',
})
const HEARTBEAT_START_DELAY_MS = 8_000
let heartbeatStartTimer = null

onMounted(() => {
  heartbeatStartTimer = window.setTimeout(() => {
    heartbeatStartTimer = null
    operationsHeartbeat.start()
  }, HEARTBEAT_START_DELAY_MS)
})

onUnmounted(() => {
  if (heartbeatStartTimer !== null) {
    window.clearTimeout(heartbeatStartTimer)
    heartbeatStartTimer = null
  }
  operationsHeartbeat.stop()
})

watch(() => route.path, async () => {
  operationsHeartbeat.notifyRouteChange()
  await nextTick()
  const container = routeContent.value
  const target = container?.querySelector('h1') || container
  if (!target) return
  if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1')
  target.focus({ preventScroll: true })
})
</script>

<template>
  <div class="app-shell" :class="{ 'app--financial-terminal': isFinancialTerminal }">
    <a class="skip-to-content" href="#main-content">跳到主要内容</a>
    <appNav :route-preloaders="routePreloaders" />
    <NewUserGuide />
    <DataFreshnessNotice />
    <GuestAccessBanner />
    <div id="main-content" ref="routeContent" class="route-content" tabindex="-1">
      <router-view v-slot="{ Component, route }">
        <keep-alive :include="['appHome']">
          <component :is="Component" :key="route.fullPath" />
        </keep-alive>
      </router-view>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  width: 100vw;
  overflow-x: hidden;
  background-color: #f6f8ff;
}

.app-shell.app--financial-terminal {
  background-color: #0b0e11;
}

.skip-to-content {
  position: fixed;
  top: 8px;
  left: 8px;
  z-index: 100000;
  padding: 10px 14px;
  border-radius: 8px;
  background: #fff;
  color: #0b4f87;
  font-weight: 800;
  transform: translateY(calc(-100% - 16px));
}

.skip-to-content:focus-visible {
  outline: 3px solid #0b6ea8;
  outline-offset: 2px;
  transform: none;
}

.route-content:focus {
  outline: none;
}

.route-content :deep(h1[tabindex='-1']:focus) {
  outline: none;
}

.main-content {
  padding-top: 0px; /* 为固定导航栏预留空间 */
  min-height: calc(100vh - 60px);
}
</style>

<!-- 无 scoped：真正作用于 html/body，保证主站浅色底与浅色 color-scheme，不被单页样式拖死 -->
<style>
html {
  margin: 0;
  padding: 0;
  width: 100%;
  color-scheme: light;
  background-color: #f6f8ff;
}

body {
  margin: 0;
  padding: 0;
  width: 100%;
  overflow-x: hidden;
  color-scheme: light;
  background-color: #f6f8ff;
}

.freshness-notice ~ .route-content .home {
  padding-top: 0;
}

.freshness-notice ~ .route-content .intel-page {
  --nav-offset: 0px;
}
</style>
