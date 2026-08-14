<template>
  <div class="debug-page">
    <aside class="debug-panel">
      <div>
        <span class="debug-kicker">React Flow Sandbox</span>
        <h1>Handle Debug</h1>
        <p>只校准节点触点位置、大小和箭头，不掺杂业务图谱。</p>
      </div>

      <section class="debug-section">
        <label>Handle Size <strong>{{ config.handleSize }}</strong></label>
        <input v-model="config.handleSize" type="range" min="4" max="18" step="1" />
      </section>

      <section class="debug-section">
        <label>Handle Offset <strong>{{ config.handleOffset }}</strong></label>
        <input v-model="config.handleOffset" type="range" min="-10" max="10" step="1" />
      </section>

      <section class="debug-section">
        <label>Handle Border <strong>{{ config.handleBorderWidth }}</strong></label>
        <input v-model="config.handleBorderWidth" type="range" min="0.5" max="3" step="0.25" />
      </section>

      <section class="debug-section">
        <label>Arrow Size <strong>{{ config.arrowSize }}</strong></label>
        <input v-model="config.arrowSize" type="range" min="6" max="20" step="1" />
      </section>

      <section class="debug-section">
        <label>Edge Width <strong>{{ config.edgeWidth }}</strong></label>
        <input v-model="config.edgeWidth" type="range" min="1" max="6" step="0.2" />
      </section>

      <section class="debug-section">
        <label>Node Width <strong>{{ config.nodeWidth }}</strong></label>
        <input v-model="config.nodeWidth" type="range" min="120" max="260" step="2" />
      </section>

      <section class="debug-section">
        <label>Node Height <strong>{{ config.nodeHeight }}</strong></label>
        <input v-model="config.nodeHeight" type="range" min="80" max="180" step="2" />
      </section>

      <section class="debug-section">
        <label>Node Radius <strong>{{ config.nodeRadius }}</strong></label>
        <input v-model="config.nodeRadius" type="range" min="8" max="40" step="1" />
      </section>

      <div class="debug-readout">
        <div>Recommended</div>
        <code>
          size={{ config.handleSize }}, offset={{ config.handleOffset }}, border={{ config.handleBorderWidth }},
          arrow={{ config.arrowSize }}
        </code>
      </div>
    </aside>

    <main class="debug-canvas-shell">
      <div ref="graphContainer" class="debug-canvas"></div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref, shallowRef, watch } from 'vue'

const graphContainer = ref(null)
const graph = shallowRef(null)
let rendererPromise = null

const config = reactive({
  handleSize: 6,
  handleOffset: 0,
  handleBorderWidth: 1.25,
  arrowSize: 10,
  edgeWidth: 2.8,
  nodeWidth: 172,
  nodeHeight: 116,
  nodeRadius: 24,
})

onMounted(async () => {
  await renderDebugGraph()
})

onUnmounted(() => {
  graph.value?.destroy()
  graph.value = null
})

watch(
  config,
  async () => {
    await renderDebugGraph()
  },
  { deep: true },
)

async function renderDebugGraph() {
  if (!graphContainer.value) return
  if (!rendererPromise) {
    rendererPromise = import('./story-graph-handle-debug.js')
  }
  const { createHandleDebugRenderer } = await rendererPromise
  if (!graph.value) {
    graph.value = createHandleDebugRenderer(graphContainer.value)
  }
  graph.value.render({
    config: sanitizeConfig(config),
  })
}

function sanitizeConfig(raw) {
  return {
    handleSize: Number(raw.handleSize),
    handleOffset: Number(raw.handleOffset),
    handleBorderWidth: Number(raw.handleBorderWidth),
    arrowSize: Number(raw.arrowSize),
    edgeWidth: Number(raw.edgeWidth),
    nodeWidth: Number(raw.nodeWidth),
    nodeHeight: Number(raw.nodeHeight),
    nodeRadius: Number(raw.nodeRadius),
  }
}
</script>

<style scoped>
.debug-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  background: #eef5fb;
}

.debug-panel {
  padding: 24px;
  border-right: 1px solid rgba(203, 213, 225, 0.9);
  background: rgba(255, 255, 255, 0.95);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.debug-kicker {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  color: #2563eb;
}

.debug-panel h1 {
  margin: 6px 0 0;
  font-size: 28px;
  color: #16263d;
}

.debug-panel p {
  margin: 8px 0 0;
  color: #64748b;
  line-height: 1.5;
}

.debug-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.debug-section label {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #334155;
}

.debug-section input {
  width: 100%;
}

.debug-readout {
  margin-top: auto;
  border: 1px solid rgba(191, 219, 254, 0.9);
  background: #eff6ff;
  border-radius: 16px;
  padding: 14px;
  color: #1e3a8a;
  font-size: 12px;
}

.debug-readout code {
  display: block;
  margin-top: 8px;
  white-space: pre-wrap;
}

.debug-canvas-shell {
  min-width: 0;
  min-height: 100vh;
  padding: 18px;
}

.debug-canvas {
  width: 100%;
  height: calc(100vh - 36px);
  border-radius: 20px;
  overflow: hidden;
  border: 1px solid rgba(191, 219, 254, 0.92);
  background: #ffffff;
  box-shadow: 0 20px 40px rgba(148, 163, 184, 0.15);
}

@media (max-width: 960px) {
  .debug-page {
    grid-template-columns: 1fr;
  }

  .debug-panel {
    border-right: 0;
    border-bottom: 1px solid rgba(203, 213, 225, 0.9);
  }

  .debug-canvas-shell,
  .debug-canvas {
    min-height: 60vh;
    height: 60vh;
  }
}
</style>
