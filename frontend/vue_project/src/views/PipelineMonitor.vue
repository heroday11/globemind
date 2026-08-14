<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  Activity,
  AlertTriangle,
  Boxes,
  CheckCircle2,
  CircleSlash2,
  Clock3,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  Image as ImageIcon,
  Languages,
  Loader2,
  RadioTower,
  RefreshCw,
  Server,
  ShieldCheck,
  Users,
  Wifi,
  Zap,
} from 'lucide-vue-next'
import {
  CHART_GRID_TICKS,
  CHART_HEIGHT,
  CHART_PAD_X,
  CHART_PAD_Y,
  CHART_WIDTH,
  DEFAULT_ACTIVE_TREND_KEYS,
  DEFAULT_DISPLAY_WINDOW_MS,
  DEFAULT_FAST_REFRESH_INTERVAL_MS,
  DISPLAY_WINDOW_OPTIONS,
  PIPELINE_STATUS_TEXT,
  REFRESH_RATE_OPTIONS,
  TREND_METRICS,
  chartLaneAxisLabels,
  chartPath as buildChartPath,
  chartPoint as buildChartPoint,
  chartTickIndices,
  chartXPosition,
  buildPipelineKpis,
  choosePipelineId,
  createLatestPipelineMonitorRequest,
  createPipelineMonitorRefreshScheduler,
  formatBytes,
  formatDuration,
  formatMetricValue,
  formatNumber,
  formatPct,
  formatTime,
  groupPipelines,
  mergeFastPipelineSnapshot,
  metricColor as resolveMetricColor,
  metricRange as calculateMetricRange,
  metricUnit as resolveMetricUnit,
  metricValue,
  normalizePipelineMonitorSnapshot,
  pipelineMonitorApi,
  pipelineMonitorErrorMessage,
  pipelineMonitorErrorStatus,
  progressStyle,
  samplesWithinWindow,
  decimateSamples,
} from '@/features/operations/index.js'

const snapshot = ref(null)
const loading = ref(false)
const fastRefreshing = ref(false)
const error = ref('')
const autoRefresh = ref(true)
const refreshRateMs = ref(DEFAULT_FAST_REFRESH_INTERVAL_MS)
const displayWindowMs = ref(DEFAULT_DISPLAY_WINDOW_MS)
const selectedPipelineId = ref('')
const activeTrendKeys = ref([...DEFAULT_ACTIVE_TREND_KEYS])
const hoverIndex = ref(null)
const snapshotRequest = createLatestPipelineMonitorRequest()
const fastSnapshotRequest = createLatestPipelineMonitorRequest()
const refreshScheduler = createPipelineMonitorRefreshScheduler({
  refreshFull: () => fetchSnapshot(),
  refreshFast: () => fetchFastSnapshot(),
})

const statusIcon = {
  running: CheckCircle2,
  warning: AlertTriangle,
  failed: CircleSlash2,
  idle: Clock3,
  not_started: CircleSlash2,
  unknown: AlertTriangle,
}

const pipelineIcons = {
  wave1_extract: Database,
  wave1_loader: Database,
  daily_ingest: RadioTower,
  quality_labels: ShieldCheck,
  l1_prep: Boxes,
  l1_extract: Activity,
  ground_realtime: Wifi,
  story_images: ImageIcon,
  embeddings: Zap,
  translation: Languages,
  vllm: Cpu,
  web: Server,
}

const kpiIcons = {
  activity: Activity,
  database: Database,
  gauge: Gauge,
  radioTower: RadioTower,
  users: Users,
  wifi: Wifi,
}

const trendMetrics = TREND_METRICS
const chartGridTicks = CHART_GRID_TICKS
const refreshRateOptions = REFRESH_RATE_OPTIONS
const displayWindowOptions = DISPLAY_WINDOW_OPTIONS

const pipelines = computed(() => snapshot.value?.pipelines || [])
const overview = computed(() => snapshot.value?.overview || {})
const system = computed(() => snapshot.value?.system || {})
const db = computed(() => snapshot.value?.db || {})
const online = computed(() => snapshot.value?.online || {})
const runtimeCatalog = computed(() => snapshot.value?.runtime_catalog || {})
const seriesSamples = computed(() => snapshot.value?.series?.samples || [])
const seriesCollectionState = computed(
  () => snapshot.value?.series?.collection_state || 'unknown',
)
const recentSamples = computed(() => samplesWithinWindow(seriesSamples.value, displayWindowMs.value))
const displaySamples = computed(() => decimateSamples(recentSamples.value))
const activeTrendMetrics = computed(() => trendMetrics.filter((metric) => activeTrendKeys.value.includes(metric.key)))
const visibleSparkMetrics = computed(() => trendMetrics.slice(0, 6))
const hoveredSample = computed(() => {
  if (hoverIndex.value === null) return null
  return displaySamples.value[hoverIndex.value] || null
})
const trendTooltipStyle = computed(() => {
  if (!hoveredSample.value || displaySamples.value.length <= 1) return {}
  const pct = hoverIndex.value / Math.max(1, displaySamples.value.length - 1)
  const clamped = Math.max(0, Math.min(1, pct))
  const axisWidth = currentLaneAxisWidth()
  return { left: `calc(${axisWidth}px + ${clamped * 100}% - ${clamped * axisWidth}px)` }
})
const chartXTickIndices = computed(() => chartTickIndices(displaySamples.value.length))
const chartXLabels = computed(() => chartXTickIndices.value.map((index) => ({
  index,
  text: formatSampleTime(displaySamples.value[index]),
  left: `${(chartXForIndex(index) / CHART_WIDTH) * 100}%`,
})))
const trendScaleRows = computed(() => activeTrendMetrics.value.map((metric) => {
  const { min, max } = calculateMetricRange(displaySamples.value, metric.key)
  const latest = metricValue(seriesSamples.value[seriesSamples.value.length - 1], metric.key)
  return {
    ...metric,
    min,
    max,
    latest,
  }
}))

const groupedPipelines = computed(() => groupPipelines(pipelines.value))

const selectedPipeline = computed(() => (
  pipelines.value.find(
    (pipeline) => pipeline.id === choosePipelineId(pipelines.value, selectedPipelineId.value),
  ) || null
))

const criticalCount = computed(() => pipelines.value.filter((pipeline) => ['warning', 'failed'].includes(pipeline.status)).length)

const kpis = computed(() => buildPipelineKpis({
  overview: overview.value,
  db: db.value,
  online: online.value,
}).map((item) => ({ ...item, icon: kpiIcons[item.icon] })))

async function fetchSnapshot({ fresh = false } = {}) {
  loading.value = true
  error.value = ''
  const result = await snapshotRequest.run((signal) => (
    pipelineMonitorApi.fetchSnapshot({ fresh, signal })
  ))

  if (result.status === 'success') {
    snapshot.value = normalizePipelineMonitorSnapshot(result.value)
    if (!selectedPipelineId.value && pipelines.value.length) {
      selectedPipelineId.value = choosePipelineId(pipelines.value)
    }
  } else if (result.status === 'error') {
    if (pipelineMonitorErrorStatus(result.error) === 401) {
      error.value = '需要登录后查看管线监控。'
    } else {
      error.value = pipelineMonitorErrorMessage(result.error, '监控快照获取失败。')
    }
  }
  loading.value = snapshotRequest.isActive()
}

async function fetchFastSnapshot() {
  if (!pipelineMonitorApi.hasAccessToken() || fastSnapshotRequest.isActive()) return
  fastRefreshing.value = true
  const result = await fastSnapshotRequest.run((signal) => (
    pipelineMonitorApi.fetchFastSnapshot({ signal })
  ))

  if (result.status === 'success') {
    snapshot.value = mergeFastPipelineSnapshot(snapshot.value, result.value)
  } else if (
    result.status === 'error'
    && !error.value
    && pipelineMonitorErrorStatus(result.error) !== 401
  ) {
    error.value = pipelineMonitorErrorMessage(result.error, '快速指标刷新失败。')
  }
  fastRefreshing.value = fastSnapshotRequest.isActive()
}

async function refreshNow() {
  await fetchSnapshot({ fresh: true })
  await fetchFastSnapshot()
}

function setupRefreshTimer() {
  refreshScheduler.sync({
    enabled: autoRefresh.value,
    fastIntervalMs: refreshRateMs.value,
  })
}

function selectPipeline(pipeline) {
  selectedPipelineId.value = pipeline.id
}

function iconForPipeline(pipeline) {
  return pipelineIcons[pipeline?.id] || Activity
}

function iconForStatus(status) {
  return statusIcon[status] || AlertTriangle
}

function statusLabel(status) {
  return PIPELINE_STATUS_TEXT[status] || PIPELINE_STATUS_TEXT.unknown
}

function metricColor(key) {
  return resolveMetricColor(key)
}

function metricUnit(key) {
  return resolveMetricUnit(key)
}

function chartXForIndex(index, width = CHART_WIDTH) {
  return chartXPosition(index, displaySamples.value.length, width)
}

function chartPoint(index, key, width = CHART_WIDTH, height = CHART_HEIGHT) {
  return buildChartPoint(displaySamples.value, index, key, width, height)
}

function chartPath(key, width = CHART_WIDTH, height = CHART_HEIGHT) {
  return buildChartPath(displaySamples.value, key, width, height)
}

function sparkPath(key) {
  return chartPath(key, 160, 52)
}

function hoverX() {
  if (hoverIndex.value === null || displaySamples.value.length <= 1) return 0
  return chartXForIndex(hoverIndex.value)
}

function hoverPoint(key) {
  if (hoverIndex.value === null) return null
  return chartPoint(hoverIndex.value, key)
}

function hoverDotStyle(key) {
  const point = hoverPoint(key)
  if (!point) return { display: 'none' }
  return {
    left: `${(point.x / CHART_WIDTH) * 100}%`,
    top: `${(point.y / CHART_HEIGHT) * 100}%`,
    borderColor: metricColor(key),
    background: metricColor(key),
  }
}

function handleChartMove(event) {
  if (!displaySamples.value.length) return
  const rect = event.currentTarget.getBoundingClientRect()
  const axisWidth = currentLaneAxisWidth()
  const plotWidth = Math.max(1, rect.width - axisWidth)
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left - axisWidth) / plotWidth))
  hoverIndex.value = Math.round(ratio * (displaySamples.value.length - 1))
}

function currentLaneAxisWidth() {
  if (typeof window !== 'undefined' && window.innerWidth <= 760) return 118
  return 150
}

function laneAxisLabels(key) {
  return chartLaneAxisLabels(displaySamples.value, key, formatTrendValue)
}

function clearChartHover() {
  hoverIndex.value = null
}

function toggleTrendMetric(key) {
  if (activeTrendKeys.value.includes(key)) {
    if (activeTrendKeys.value.length === 1) return
    activeTrendKeys.value = activeTrendKeys.value.filter((item) => item !== key)
  } else {
    activeTrendKeys.value = [...activeTrendKeys.value, key]
  }
}

function formatTrendValue(value, key) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const number = Number(value)
  const unit = metricUnit(key)
  const formatted = Math.abs(number) >= 1000 ? formatNumber(Math.round(number)) : number.toFixed(number >= 10 ? 1 : 2)
  return `${formatted}${unit}`
}

function formatSampleTime(sample) {
  return formatTime(sample?.time, true)
}

function controllerName(path) {
  return String(path || '未声明').split('/').filter(Boolean).at(-1) || '未声明'
}

watch(autoRefresh, setupRefreshTimer)
watch(refreshRateMs, () => {
  setupRefreshTimer()
  fetchFastSnapshot()
})

onMounted(() => {
  fetchSnapshot({ fresh: true })
  fetchFastSnapshot()
  setupRefreshTimer()
})

onUnmounted(() => {
  refreshScheduler.stop()
  snapshotRequest.cancel()
  fastSnapshotRequest.cancel()
})
</script>

<template>
  <main class="ops-monitor">
    <section class="ops-toolbar">
      <div class="ops-identity">
        <span class="ops-identity__pulse" aria-hidden="true" />
        <div>
          <p class="ops-kicker">GlobeMind Operations</p>
          <h1>管线监控</h1>
        </div>
      </div>
      <div class="ops-actions">
        <span class="ops-stamp">完整 {{ formatTime(snapshot?.generated_at) }} · 快刷 {{ fastRefreshing ? '同步中' : `${refreshRateMs / 1000}s` }}</span>
        <label class="refresh-rate-control">
          <Clock3 :size="15" />
          <select v-model.number="refreshRateMs" :disabled="!autoRefresh">
            <option v-for="option in refreshRateOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="auto-toggle">
          <input v-model="autoRefresh" type="checkbox" />
          <span>自动刷新</span>
        </label>
        <button class="refresh-button" type="button" @click="refreshNow" :disabled="loading">
          <Loader2 v-if="loading" class="spin" :size="17" />
          <RefreshCw v-else :size="17" />
          <span>刷新</span>
        </button>
      </div>
    </section>

    <section v-if="error" class="ops-error">
      <AlertTriangle :size="18" />
      <span>{{ error }}</span>
    </section>

    <section class="kpi-grid" aria-label="核心指标">
      <article v-for="item in kpis" :key="item.label" class="kpi" :data-tone="item.tone">
        <div class="kpi__icon">
          <component :is="item.icon" :size="19" />
        </div>
        <div>
          <p>{{ item.label }}</p>
          <strong>{{ item.value }}</strong>
          <span>{{ item.sub }}</span>
        </div>
      </article>
    </section>

    <section class="trend-panel" aria-label="关键指标波动">
      <div class="trend-head">
        <div>
          <p class="ops-kicker">状态信号</p>
          <h2>关键波动</h2>
        </div>
        <div class="trend-controls">
          <label class="trend-window-control">
            <span>显示</span>
            <select v-model.number="displayWindowMs">
              <option v-for="option in displayWindowOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <button
            v-for="metric in trendMetrics"
            :key="metric.key"
            class="trend-chip"
            :class="{ 'trend-chip--active': activeTrendKeys.includes(metric.key) }"
            :style="{ '--metric-color': metric.color }"
            type="button"
            @click="toggleTrendMetric(metric.key)"
          >
            <span />
            {{ metric.label }}
          </button>
        </div>
      </div>

      <div class="trend-body" @mousemove="handleChartMove" @mouseleave="clearChartHover">
        <div v-for="metric in activeTrendMetrics" :key="`lane-${metric.key}`" class="trend-lane">
          <div class="trend-lane-axis">
            <strong><i :style="{ background: metric.color }" />{{ metric.label }}</strong>
            <span
              v-for="label in laneAxisLabels(metric.key)"
              :key="`${metric.key}-${label.top}-${label.text}`"
              :style="{ top: label.top }"
            >
              {{ label.text }}
            </span>
          </div>
          <div class="trend-lane-plot">
            <svg class="trend-chart" :viewBox="`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`" preserveAspectRatio="none">
              <line
                v-for="tick in chartGridTicks"
                :key="`${metric.key}-${tick}`"
                :x1="CHART_PAD_X"
                :x2="CHART_WIDTH - CHART_PAD_X"
                :y1="CHART_PAD_Y + tick * ((CHART_HEIGHT - CHART_PAD_Y * 2) / 4)"
                :y2="CHART_PAD_Y + tick * ((CHART_HEIGHT - CHART_PAD_Y * 2) / 4)"
                class="trend-grid-line"
              />
              <line
                v-for="label in chartXLabels"
                :key="`${metric.key}-xgrid-${label.index}`"
                class="trend-grid-line trend-grid-line--vertical"
                :x1="chartXForIndex(label.index)"
                :x2="chartXForIndex(label.index)"
                :y1="CHART_PAD_Y"
                :y2="CHART_HEIGHT - CHART_PAD_Y"
              />
              <line class="trend-axis-line" :x1="CHART_PAD_X" :x2="CHART_PAD_X" :y1="CHART_PAD_Y" :y2="CHART_HEIGHT - CHART_PAD_Y" />
              <line class="trend-axis-line" :x1="CHART_PAD_X" :x2="CHART_WIDTH - CHART_PAD_X" :y1="CHART_HEIGHT - CHART_PAD_Y" :y2="CHART_HEIGHT - CHART_PAD_Y" />
              <path class="trend-line" :d="chartPath(metric.key)" :stroke="metric.color" />
              <template v-if="hoveredSample">
                <line class="trend-hover-line" :x1="hoverX()" :x2="hoverX()" :y1="CHART_PAD_Y" :y2="CHART_HEIGHT - CHART_PAD_Y" />
              </template>
            </svg>
            <span v-if="hoveredSample" class="trend-hover-dot" :style="hoverDotStyle(metric.key)" />
          </div>
        </div>
        <div class="trend-x-axis">
          <span v-for="label in chartXLabels" :key="`xlabel-${label.index}`" :style="{ left: label.left }">{{ label.text }}</span>
        </div>
        <div v-if="hoveredSample" class="trend-tooltip" :style="trendTooltipStyle">
          <strong>{{ formatSampleTime(hoveredSample) }}</strong>
          <span v-for="metric in activeTrendMetrics" :key="`tip-${metric.key}`">
            <i :style="{ background: metric.color }" />
            {{ metric.label }} {{ formatTrendValue(metricValue(hoveredSample, metric.key), metric.key) }}
          </span>
        </div>
        <div
          v-if="displaySamples.length < 2"
          class="trend-empty"
          role="status"
          aria-live="polite"
        >
          {{ seriesCollectionState === 'not_configured' ? '历史采样未配置；当前读取不会生成趋势点' : '等待更多采样' }}
        </div>
      </div>

      <div class="trend-scale-grid">
        <div class="trend-scale-row trend-scale-row--head">
          <span>指标</span>
          <strong>最小</strong>
          <strong>当前</strong>
          <strong>最大</strong>
        </div>
        <div v-for="metric in trendScaleRows" :key="`scale-${metric.key}`" class="trend-scale-row">
          <span><i :style="{ background: metric.color }" />{{ metric.label }}</span>
          <strong>{{ formatTrendValue(metric.min, metric.key) }}</strong>
          <strong>{{ formatTrendValue(metric.latest, metric.key) }}</strong>
          <strong>{{ formatTrendValue(metric.max, metric.key) }}</strong>
        </div>
      </div>

      <div class="spark-grid">
        <article v-for="metric in visibleSparkMetrics" :key="`spark-${metric.key}`" class="spark-card">
          <div>
            <span>{{ metric.label }}</span>
            <strong>{{ formatTrendValue(metricValue(seriesSamples[seriesSamples.length - 1], metric.key), metric.key) }}</strong>
          </div>
          <svg viewBox="0 0 160 52" preserveAspectRatio="none">
            <path :d="sparkPath(metric.key)" :stroke="metric.color" />
          </svg>
        </article>
      </div>
    </section>

    <section class="status-ribbon" role="group" aria-label="管线运行状态摘要">
      <div class="ribbon-cell">
        <span class="ribbon-label">运行</span>
        <strong>{{ overview.status_counts?.running || 0 }}</strong>
      </div>
      <div class="ribbon-cell">
        <span class="ribbon-label">需关注</span>
        <strong>{{ criticalCount }}</strong>
      </div>
      <div class="ribbon-cell">
        <span class="ribbon-label">未启动</span>
        <strong>{{ overview.status_counts?.not_started || 0 }}</strong>
      </div>
      <div class="ribbon-cell">
        <span class="ribbon-label">服务目录</span>
        <strong>{{ runtimeCatalog.summary?.service_count || 0 }}</strong>
      </div>
    </section>

    <section class="ops-layout">
      <div class="pipeline-board">
        <section v-for="group in groupedPipelines" :key="group.name" class="pipeline-group">
          <div class="group-heading">
            <h2>{{ group.name }}</h2>
            <span>{{ group.items.length }} 条</span>
          </div>

          <div class="pipeline-grid">
            <article
              v-for="pipeline in group.items"
              :key="pipeline.id"
              class="pipeline-card"
              :class="{ 'pipeline-card--selected': selectedPipeline?.id === pipeline.id }"
              :data-status="pipeline.status"
              @click="selectPipeline(pipeline)"
            >
              <div class="pipeline-card__head">
                <div class="pipeline-card__icon">
                  <component :is="iconForPipeline(pipeline)" :size="18" />
                </div>
                <div class="pipeline-card__title">
                  <h3>{{ pipeline.name }}</h3>
                  <p>{{ pipeline.management?.owner || '目录未登记' }} · {{ pipeline.pid ? `遥测 PID ${pipeline.pid}` : '无 PID 遥测' }}</p>
                </div>
                <span class="status-pill" :data-status="pipeline.status">
                  <component :is="iconForStatus(pipeline.status)" :size="13" />
                  {{ statusLabel(pipeline.status) }}
                </span>
              </div>

              <div class="pipeline-progress" :class="{ 'pipeline-progress--empty': pipeline.progress_pct === null || pipeline.progress_pct === undefined }">
                <div class="pipeline-progress__bar" :style="progressStyle(pipeline.progress_pct)" />
              </div>
              <div class="pipeline-meta">
                <span>{{ pipeline.progress_pct !== null && pipeline.progress_pct !== undefined ? formatPct(pipeline.progress_pct) : '无百分比' }}</span>
                <span>ETA {{ formatDuration(pipeline.eta_sec) }}</span>
                <span>更新 {{ formatTime(pipeline.updated_at, true) }}</span>
              </div>

              <div class="metric-strip">
                <div v-for="metric in pipeline.metrics?.slice(0, 4)" :key="metric.label" class="metric">
                  <span>{{ metric.label }}</span>
                  <strong>{{ formatMetricValue(metric) }}</strong>
                </div>
              </div>

              <div v-if="pipeline.alerts?.length" class="alert-line">
                <AlertTriangle :size="14" />
                <span>{{ pipeline.alerts[0] }}</span>
              </div>
            </article>
          </div>
        </section>
      </div>

      <aside class="ops-side">
        <section class="side-panel side-panel--selected">
          <div class="side-panel__head">
            <div>
              <p>当前检查</p>
              <h2>{{ selectedPipeline?.name || '—' }}</h2>
            </div>
            <span v-if="selectedPipeline" class="status-pill" :data-status="selectedPipeline.status">
              {{ statusLabel(selectedPipeline.status) }}
            </span>
          </div>

          <div v-if="selectedPipeline" class="detail-grid">
            <div>
              <span>所有者</span>
              <strong>{{ selectedPipeline.management?.owner || '未登记' }}</strong>
            </div>
            <div>
              <span>身份</span>
              <strong>{{ selectedPipeline.management?.identity_contract?.assurance || '无目录证据' }}</strong>
            </div>
            <div>
              <span>健康策略</span>
              <strong>{{ selectedPipeline.management?.health_policy?.mode || '未声明' }}</strong>
            </div>
            <div>
              <span>生命周期</span>
              <strong>{{ selectedPipeline.management?.effective_lifecycle_state || 'not-authorized' }}</strong>
            </div>
          </div>

          <div v-if="selectedPipeline?.management?.controller" class="list-block">
            <h3>目录控制器</h3>
            <div class="list-row">
              <code>{{ controllerName(selectedPipeline.management.controller.path) }}</code>
              <strong>{{ selectedPipeline.management.controller.adoption }}</strong>
            </div>
            <div class="list-row">
              <span>声明接口</span>
              <strong>{{ selectedPipeline.management.controller.interface }}</strong>
            </div>
          </div>

          <div v-if="selectedPipeline?.management?.management_blockers?.length" class="list-block">
            <h3>管理阻断</h3>
            <div v-for="blocker in selectedPipeline.management.management_blockers.slice(0, 5)" :key="blocker" class="list-row">
              <span :title="blocker">{{ blocker }}</span>
            </div>
          </div>

          <div v-if="selectedPipeline?.top_errors?.length" class="list-block">
            <h3>主要错误</h3>
            <div v-for="item in selectedPipeline.top_errors" :key="`${item[0]}-${item[1]}`" class="list-row">
              <span>{{ item[0] || 'unknown' }}</span>
              <strong>{{ formatNumber(item[1]) }}</strong>
            </div>
          </div>

          <div v-if="selectedPipeline?.top_error_sites?.length" class="list-block">
            <h3>错误站点</h3>
            <div v-for="item in selectedPipeline.top_error_sites" :key="`${item[0]}-${item[1]}`" class="list-row">
              <span>{{ item[0] }}</span>
              <strong>{{ formatNumber(item[1]) }}</strong>
            </div>
          </div>

          <div v-if="selectedPipeline?.details?.last_log?.length" class="log-lines">
            <h3>最近日志</h3>
            <code v-for="line in selectedPipeline.details.last_log.slice(-4)" :key="line">{{ line }}</code>
          </div>
        </section>

        <section class="side-panel">
          <div class="side-panel__head">
            <div>
              <p>服务器压力</p>
              <h2>{{ system.host || '—' }}</h2>
            </div>
            <Cpu :size="20" />
          </div>
          <div class="pressure-row">
            <span>CPU load</span>
            <strong>{{ system.cpu?.load1 ?? '—' }} / {{ system.cpu?.count ?? '—' }}</strong>
          </div>
          <div class="meter">
            <span :style="progressStyle(system.cpu?.pressure_pct)" />
          </div>
          <div class="pressure-row">
            <span>Memory</span>
            <strong>{{ formatPct(system.memory?.used_pct) }}</strong>
          </div>
          <div class="meter meter--green">
            <span :style="progressStyle(system.memory?.used_pct)" />
          </div>
          <div class="pressure-row">
            <span>Disk</span>
            <strong>{{ formatPct(system.disk?.used_pct) }}</strong>
          </div>
          <div class="meter meter--amber">
            <span :style="progressStyle(system.disk?.used_pct)" />
          </div>
          <div class="capacity-line">
            <HardDrive :size="15" />
            <span>{{ formatBytes(system.disk?.free_bytes) }} free</span>
          </div>
        </section>

        <section class="side-panel">
          <div class="side-panel__head">
            <div>
              <p>GPU / LLM</p>
              <h2>{{ system.gpus?.filter?.((gpu) => gpu.available).length || 0 }} 块 GPU</h2>
            </div>
            <Zap :size="20" />
          </div>
          <div v-for="gpu in system.gpus || []" :key="gpu.index ?? gpu.error" class="gpu-row">
            <template v-if="gpu.available">
              <div class="gpu-row__top">
                <span>{{ gpu.index }} · {{ gpu.name }}</span>
                <strong>{{ gpu.utilization_pct }}%</strong>
              </div>
              <div class="meter meter--blue">
                <span :style="progressStyle(gpu.memory_used_pct)" />
              </div>
              <small>{{ Math.round(gpu.memory_used_mib || 0) }} / {{ Math.round(gpu.memory_total_mib || 0) }} MiB · {{ gpu.temperature_c }}°C</small>
            </template>
            <template v-else>
              <span>{{ gpu.error || 'GPU 不可用' }}</span>
            </template>
          </div>
        </section>

        <section class="side-panel">
          <div class="side-panel__head">
            <div>
              <p>在线路径</p>
              <h2>{{ online.active || 0 }} 活跃</h2>
            </div>
            <Users :size="20" />
          </div>
          <div v-if="online.paths?.length" class="list-block list-block--plain">
            <div v-for="path in online.paths" :key="path.path" class="list-row">
              <span>{{ path.path }}</span>
              <strong>{{ path.count }}</strong>
            </div>
          </div>
          <p v-else class="empty-note">暂无活跃 heartbeat</p>
        </section>
      </aside>
    </section>

    <section class="process-band">
      <div class="group-heading">
        <h2>进程遥测（启发式）</h2>
        <span>{{ system.processes?.length || 0 }} 条</span>
      </div>
      <div class="process-table">
        <div class="process-row process-row--head">
          <span>PID</span>
          <span>CPU</span>
          <span>MEM</span>
          <span>运行</span>
          <span>匹配标签</span>
        </div>
        <div v-for="proc in system.processes || []" :key="`${proc.pid}-${proc.label || proc.name}`" class="process-row">
          <strong>{{ proc.pid }}</strong>
          <span>{{ proc.cpu_pct }}%</span>
          <span>{{ proc.mem_pct }}%</span>
          <span>{{ proc.etime }}</span>
          <code>{{ proc.label || proc.name || '—' }}</code>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.ops-monitor {
  min-height: 100vh;
  padding: 86px 24px 48px;
  background:
    linear-gradient(90deg, rgba(32, 37, 43, 0.035) 1px, transparent 1px),
    linear-gradient(0deg, rgba(32, 37, 43, 0.025) 1px, transparent 1px),
    #f4f6f1;
  background-size: 44px 44px;
  color: #20252b;
  font-family: "IBM Plex Sans", Inter, "Microsoft YaHei", system-ui, sans-serif;
}

.ops-toolbar,
.kpi-grid,
.trend-panel,
.status-ribbon,
.ops-layout,
.process-band,
.ops-error {
  max-width: 1680px;
  margin: 0 auto;
}

.ops-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 18px;
}

.ops-identity {
  display: flex;
  align-items: center;
  gap: 14px;
}

.ops-identity__pulse {
  width: 14px;
  height: 48px;
  border-radius: 3px;
  background: repeating-linear-gradient(
    180deg,
    #2f9e69 0,
    #2f9e69 8px,
    #d8a31a 8px,
    #d8a31a 14px,
    #2b6cb0 14px,
    #2b6cb0 24px
  );
  box-shadow: inset 0 0 0 1px rgba(32, 37, 43, 0.14);
}

.ops-kicker,
.side-panel__head p,
.kpi p,
.pipeline-card__title p,
.ribbon-label,
.detail-grid span,
.metric span,
.group-heading span {
  margin: 0;
  color: #66706f;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

.ops-identity h1 {
  margin: 2px 0 0;
  color: #161a1f;
  font-size: 30px;
  font-weight: 800;
  line-height: 1.05;
  letter-spacing: 0;
}

.ops-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.ops-stamp {
  color: #4d5757;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
}

.refresh-rate-control,
.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid #d6ddd5;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.78);
  color: #273039;
  font-size: 13px;
  font-weight: 700;
}

.refresh-rate-control select {
  min-width: 64px;
  border: 0;
  outline: 0;
  background: transparent;
  color: #273039;
  font: inherit;
  font-weight: 800;
  cursor: pointer;
}

.refresh-rate-control select:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

.auto-toggle input {
  accent-color: #2f9e69;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 13px;
  border: 1px solid #20252b;
  border-radius: 8px;
  background: #20252b;
  color: #f8faf5;
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
}

.refresh-button:disabled {
  cursor: wait;
  opacity: 0.72;
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.ops-error {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #efb3aa;
  border-radius: 8px;
  background: #fff1ee;
  color: #a2332b;
  font-size: 14px;
  font-weight: 700;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(178px, 1fr));
  gap: 12px;
}

.kpi,
.pipeline-card,
.trend-panel,
.side-panel,
.process-band,
.status-ribbon {
  border: 1px solid #d7ded4;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 10px 24px rgba(32, 37, 43, 0.055), 0 1px 0 rgba(255, 255, 255, 0.7) inset;
}

.kpi {
  display: grid;
  grid-template-columns: 38px 1fr;
  gap: 10px;
  min-height: 116px;
  padding: 15px;
  transition: transform 150ms ease, border-color 150ms ease, box-shadow 150ms ease;
}

.kpi:hover {
  transform: translateY(-1px);
  border-color: #becbc0;
  box-shadow: 0 14px 28px rgba(32, 37, 43, 0.075);
}

.kpi__icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  color: #fff;
  background: #2b6cb0;
}

.kpi[data-tone="green"] .kpi__icon { background: #2f9e69; }
.kpi[data-tone="amber"] .kpi__icon { background: #d97706; }
.kpi[data-tone="red"] .kpi__icon { background: #c24135; }
.kpi[data-tone="blue"] .kpi__icon { background: #2b6cb0; }

.kpi strong {
  display: block;
  margin-top: 8px;
  color: #161a1f;
  font-size: clamp(17px, 1.45vw, 24px);
  font-weight: 850;
  line-height: 1.1;
  overflow-wrap: anywhere;
}

.kpi span {
  display: block;
  margin-top: 6px;
  color: #65706d;
  font-size: 12px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.trend-panel {
  margin-top: 12px;
  padding: 14px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 245, 0.92)),
    rgba(255, 255, 255, 0.86);
}

.trend-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 12px;
}

.trend-head h2 {
  margin: 2px 0 0;
  color: #161a1f;
  font-size: 18px;
  font-weight: 850;
  letter-spacing: 0;
}

.trend-controls {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.trend-window-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 9px;
  border: 1px solid #d9dfd6;
  border-radius: 8px;
  background: #eef3ec;
  color: #4b5655;
  font-size: 12px;
  font-weight: 850;
  transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
}

.trend-window-control select {
  min-width: 74px;
  border: 0;
  outline: 0;
  background: transparent;
  color: #20252b;
  font: inherit;
  font-weight: 900;
  cursor: pointer;
}

.trend-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid #d9dfd6;
  border-radius: 8px;
  background: #f8faf5;
  color: #4b5655;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  transition: border-color 150ms ease, background 150ms ease, color 150ms ease, transform 150ms ease;
}

.trend-chip:hover,
.trend-window-control:hover {
  border-color: #b9c8bd;
  background: #ffffff;
  box-shadow: 0 5px 14px rgba(32, 37, 43, 0.06);
}

.trend-chip span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--metric-color);
  opacity: 0.55;
}

.trend-chip--active {
  border-color: color-mix(in srgb, var(--metric-color), #ffffff 35%);
  background: color-mix(in srgb, var(--metric-color), #ffffff 90%);
  color: #1f252b;
}

.trend-chip--active span {
  opacity: 1;
}

.trend-body {
  position: relative;
  max-height: 540px;
  min-height: 214px;
  overflow-x: hidden;
  overflow-y: auto;
  border: 1px solid #e1e7df;
  border-radius: 8px;
  background: #fbfcf8;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75);
  overscroll-behavior: contain;
  scrollbar-width: thin;
}

.trend-lane {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  min-height: 88px;
  border-bottom: 1px solid #e4e9e2;
  transition: background 140ms ease;
}

.trend-lane:hover {
  background: #ffffff;
}

.trend-lane:last-of-type {
  border-bottom: 0;
}

.trend-lane-axis {
  position: relative;
  min-width: 0;
  padding: 10px 10px 10px 12px;
  border-right: 1px solid #e4e9e2;
  background: #f4f7f1;
}

.trend-lane-axis strong {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  gap: 6px;
  color: #20252b;
  font-size: 12px;
  font-weight: 900;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trend-lane-axis strong i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.trend-lane-axis span {
  position: absolute;
  right: 10px;
  color: #5e6968;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 10px;
  font-weight: 800;
  line-height: 1;
  transform: translateY(-50%);
}

.trend-lane-plot {
  position: relative;
  min-width: 0;
  min-height: 88px;
  overflow: hidden;
  padding: 6px 10px 6px 0;
  background:
    linear-gradient(90deg, rgba(32, 37, 43, 0.045) 1px, transparent 1px),
    linear-gradient(0deg, rgba(32, 37, 43, 0.035) 1px, transparent 1px),
    #fbfcf8;
  background-size: 48px 48px;
}

.trend-chart {
  display: block;
  width: 100%;
  height: 76px;
  cursor: crosshair;
  overflow: visible;
}

.trend-grid-line {
  stroke: rgba(32, 37, 43, 0.11);
  stroke-width: 1;
}

.trend-grid-line--vertical {
  stroke: rgba(32, 37, 43, 0.075);
}

.trend-axis-line {
  stroke: rgba(32, 37, 43, 0.42);
  stroke-width: 1.2;
  vector-effect: non-scaling-stroke;
}

.trend-line {
  fill: none;
  stroke-width: 2.6;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.trend-hover-line {
  stroke: rgba(32, 37, 43, 0.42);
  stroke-width: 1;
  stroke-dasharray: 4 4;
  vector-effect: non-scaling-stroke;
}

.trend-hover-dot {
  position: absolute;
  z-index: 3;
  width: 7px;
  height: 7px;
  border: 2px solid;
  border-radius: 50%;
  box-shadow: 0 0 0 2px #ffffff, 0 4px 10px rgba(32, 37, 43, 0.2);
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.trend-x-axis {
  position: sticky;
  bottom: 0;
  width: calc(100% - 150px);
  height: 22px;
  margin-left: 150px;
  border-top: 1px solid #dfe5dc;
  background: rgba(251, 252, 248, 0.94);
  pointer-events: none;
  z-index: 1;
}

.trend-x-axis span {
  position: absolute;
  bottom: 6px;
  color: #5e6968;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 10px;
  font-weight: 750;
  line-height: 1;
  transform: translateX(-50%);
  white-space: nowrap;
}

.trend-tooltip {
  position: absolute;
  top: 12px;
  z-index: 2;
  min-width: 172px;
  max-width: 260px;
  padding: 9px 10px;
  border: 1px solid #cfd8ce;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 12px 24px rgba(32, 37, 43, 0.12);
  transform: translateX(-50%);
  pointer-events: none;
  overflow-wrap: anywhere;
}

.trend-tooltip strong {
  display: block;
  margin-bottom: 6px;
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
}

.trend-tooltip span {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #3f4948;
  font-size: 12px;
  font-weight: 750;
  line-height: 1.45;
}

.trend-tooltip i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.trend-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #6b7472;
  font-size: 13px;
  font-weight: 750;
  pointer-events: none;
}

.trend-scale-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(216px, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.trend-scale-row {
  display: grid;
  grid-template-columns: minmax(88px, 1fr) repeat(3, minmax(56px, auto));
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 7px 9px;
  border: 1px solid #e1e6df;
  border-radius: 8px;
  background: #fbfcf8;
}

.trend-scale-row span {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 6px;
  color: #4d5756;
  font-size: 12px;
  font-weight: 850;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trend-scale-row span i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.trend-scale-row strong {
  color: #192026;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 11px;
  font-weight: 800;
  text-align: right;
  overflow-wrap: anywhere;
}

.trend-scale-row--head {
  background: #eef3ec;
}

.trend-scale-row--head span,
.trend-scale-row--head strong {
  color: #66706f;
  font-family: "IBM Plex Sans", Inter, "Microsoft YaHei", system-ui, sans-serif;
  font-size: 11px;
  font-weight: 900;
  text-transform: uppercase;
}

.spark-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.spark-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 86px;
  align-items: center;
  gap: 8px;
  min-height: 64px;
  padding: 9px 10px;
  border: 1px solid #e1e6df;
  border-radius: 8px;
  background: #f8faf5;
  transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
}

.spark-card:hover {
  border-color: #c8d3c8;
  background: #ffffff;
  box-shadow: 0 8px 18px rgba(32, 37, 43, 0.055);
}

.spark-card span {
  display: block;
  color: #66706f;
  font-size: 11px;
  font-weight: 800;
}

.spark-card strong {
  display: block;
  margin-top: 5px;
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.spark-card svg {
  width: 86px;
  height: 40px;
}

.spark-card path {
  fill: none;
  stroke-width: 2.2;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.status-ribbon {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 12px;
  overflow: hidden;
}

.ribbon-cell {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: 0 16px;
  border-right: 1px solid #d8ded6;
}

.ribbon-cell::before {
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: #2f9e69;
  content: "";
}

.ribbon-cell:nth-child(2)::before { background: #d97706; }
.ribbon-cell:nth-child(3)::before { background: #c24135; }
.ribbon-cell:nth-child(4)::before { background: #2b6cb0; }

.ribbon-cell:last-child {
  border-right: 0;
}

.ribbon-cell strong {
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 22px;
}

.ops-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  margin-top: 16px;
  align-items: start;
}

.pipeline-group + .pipeline-group {
  margin-top: 18px;
}

.group-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}

.group-heading h2 {
  margin: 0;
  color: #1c2228;
  font-size: 18px;
  font-weight: 850;
  letter-spacing: 0;
}

.pipeline-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(334px, 1fr));
  gap: 12px;
}

.pipeline-card {
  position: relative;
  min-height: 246px;
  padding: 14px;
  overflow: hidden;
  cursor: pointer;
  background: linear-gradient(180deg, #ffffff, #fafcf7);
  transition: transform 150ms ease, border-color 150ms ease, box-shadow 150ms ease;
}

.pipeline-card:hover,
.pipeline-card--selected {
  transform: translateY(-1px);
  border-color: #8ca49a;
  box-shadow: 0 14px 30px rgba(32, 37, 43, 0.095);
}

.pipeline-card::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 5px;
  background: #2f9e69;
  content: "";
}

.pipeline-card[data-status="warning"]::before { background: #d97706; }
.pipeline-card[data-status="failed"]::before,
.pipeline-card[data-status="not_started"]::before { background: #c24135; }
.pipeline-card[data-status="idle"]::before { background: #7a8585; }
.pipeline-card[data-status="unknown"]::before { background: #6b7280; }

.pipeline-card__head {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: start;
}

.pipeline-card__icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid #d7ddd4;
  border-radius: 8px;
  background: #f4f6f1;
  color: #20252b;
}

.pipeline-card__title {
  min-width: 0;
}

.pipeline-card__title h3 {
  margin: 0;
  color: #15191e;
  font-size: 15px;
  font-weight: 850;
  line-height: 1.25;
  letter-spacing: 0;
}

.pipeline-card__title p {
  margin-top: 5px;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  overflow-wrap: anywhere;
  text-transform: none;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 26px;
  padding: 0 8px;
  border-radius: 999px;
  background: #e8f5ee;
  color: #16704a;
  font-size: 12px;
  font-weight: 850;
  white-space: nowrap;
  max-width: 100%;
}

.status-pill[data-status="warning"] {
  background: #fff3d6;
  color: #9a5a00;
}

.status-pill[data-status="failed"],
.status-pill[data-status="not_started"] {
  background: #fee8e5;
  color: #a9332b;
}

.status-pill[data-status="idle"],
.status-pill[data-status="unknown"] {
  background: #edf0ef;
  color: #56605f;
}

.pipeline-progress,
.meter {
  position: relative;
  height: 8px;
  margin-top: 16px;
  overflow: hidden;
  border-radius: 999px;
  background: #e3e8e1;
}

.pipeline-progress__bar,
.meter span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #2f9e69;
}

.pipeline-card[data-status="warning"] .pipeline-progress__bar { background: #d97706; }
.pipeline-card[data-status="failed"] .pipeline-progress__bar,
.pipeline-card[data-status="not_started"] .pipeline-progress__bar { background: #c24135; }

.pipeline-progress--empty .pipeline-progress__bar {
  width: 100%;
  background: repeating-linear-gradient(90deg, #ccd4cd 0, #ccd4cd 6px, #e7ece5 6px, #e7ece5 12px);
}

.pipeline-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 9px;
  color: #5d6866;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 11px;
}

.pipeline-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 15px;
}

.metric {
  min-width: 0;
  min-height: 58px;
  padding: 9px 8px;
  border: 1px solid #e1e6df;
  border-radius: 8px;
  background: #f9fbf7;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75);
}

.metric strong {
  display: block;
  margin-top: 6px;
  color: #192026;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: clamp(12px, 0.82vw, 13px);
  line-height: 1.15;
  overflow-wrap: anywhere;
}

.alert-line {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 12px;
  color: #9a5a00;
  font-size: 12px;
  font-weight: 800;
  line-height: 1.35;
}

.ops-side {
  display: grid;
  gap: 12px;
  position: sticky;
  top: 82px;
}

.side-panel {
  padding: 14px;
  background: linear-gradient(180deg, #ffffff, #fafcf7);
}

.side-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.side-panel__head h2 {
  margin: 3px 0 0;
  color: #161a1f;
  font-size: 18px;
  font-weight: 850;
  line-height: 1.2;
  letter-spacing: 0;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.detail-grid div {
  padding: 9px 8px;
  border: 1px solid #e1e6df;
  border-radius: 8px;
  background: #f8faf5;
}

.detail-grid strong {
  display: block;
  min-width: 0;
  margin-top: 6px;
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.list-block {
  margin-top: 12px;
}

.list-block h3,
.log-lines h3 {
  margin: 0 0 8px;
  color: #333b42;
  font-size: 13px;
  font-weight: 850;
}

.list-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid #e4e9e2;
  color: #4c5756;
  font-size: 12px;
}

.list-row span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.list-row strong {
  min-width: 0;
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  overflow-wrap: anywhere;
  text-align: right;
}

.list-row code {
  min-width: 0;
  overflow-wrap: anywhere;
}

.log-lines {
  margin-top: 13px;
}

.log-lines code {
  display: block;
  max-height: 46px;
  margin-top: 6px;
  overflow: hidden;
  color: #3f4948;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 11px;
  line-height: 1.35;
  text-overflow: ellipsis;
}

.pressure-row,
.capacity-line,
.gpu-row__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  color: #4e5958;
  font-size: 13px;
  font-weight: 700;
}

.pressure-row strong,
.gpu-row__top strong {
  color: #161a1f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

.meter {
  margin-top: 7px;
}

.meter span {
  background: #d97706;
}

.meter--green span {
  background: #2f9e69;
}

.meter--amber span {
  background: #d97706;
}

.meter--blue span {
  background: #2b6cb0;
}

.capacity-line {
  justify-content: flex-start;
  color: #66706f;
}

.gpu-row {
  padding: 9px 0;
  border-bottom: 1px solid #e4e9e2;
}

.gpu-row:last-child {
  border-bottom: 0;
}

.gpu-row small {
  display: block;
  margin-top: 7px;
  color: #66706f;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 11px;
}

.empty-note {
  margin: 0;
  color: #6b7472;
  font-size: 13px;
}

.process-band {
  margin-top: 18px;
  padding: 14px;
}

.process-table {
  overflow-x: auto;
}

.process-row {
  display: grid;
  grid-template-columns: 80px 70px 70px 96px minmax(520px, 1fr);
  gap: 12px;
  align-items: center;
  min-width: 880px;
  padding: 9px 0;
  border-bottom: 1px solid #e4e9e2;
  color: #485352;
  font-size: 12px;
}

.process-row:last-child {
  border-bottom: 0;
}

.process-row--head {
  color: #66706f;
  font-weight: 850;
}

.process-row strong,
.process-row code {
  color: #192026;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

.process-row code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1280px) {
  .kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .spark-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .ops-layout {
    grid-template-columns: 1fr;
  }

  .ops-side {
    position: static;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pipeline-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .ops-monitor {
    padding: 76px 14px 32px;
  }

  .ops-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .ops-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .trend-head {
    flex-direction: column;
  }

  .trend-controls {
    justify-content: flex-start;
  }

  .trend-body {
    max-height: 520px;
    min-height: 214px;
  }

  .trend-lane {
    grid-template-columns: 118px minmax(0, 1fr);
  }

  .trend-lane-axis {
    padding-left: 9px;
  }

  .trend-lane-axis span {
    right: 8px;
    font-size: 9px;
  }

  .trend-x-axis {
    width: calc(100% - 118px);
    margin-left: 118px;
  }

  .trend-scale-grid {
    grid-template-columns: 1fr;
  }

  .kpi-grid,
  .spark-grid,
  .status-ribbon,
  .pipeline-grid,
  .ops-side {
    grid-template-columns: 1fr;
  }

  .status-ribbon {
    overflow: visible;
  }

  .ribbon-cell {
    border-right: 0;
    border-bottom: 1px solid #d8ded6;
  }

  .ribbon-cell:last-child {
    border-bottom: 0;
  }

  .pipeline-card__head {
    grid-template-columns: 36px minmax(0, 1fr);
  }

  .status-pill {
    grid-column: 1 / -1;
    justify-self: start;
  }

  .metric-strip,
  .detail-grid,
  .pipeline-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .spark-card {
    grid-template-columns: minmax(0, 1fr) 74px;
  }

  .spark-card svg {
    width: 74px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pipeline-card,
  .refresh-button,
  .spin {
    animation: none;
    transition: none;
  }
}
</style>
