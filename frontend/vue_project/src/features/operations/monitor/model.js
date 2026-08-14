export const FULL_REFRESH_INTERVAL_MS = 30_000
export const DEFAULT_FAST_REFRESH_INTERVAL_MS = 3_000
export const DEFAULT_DISPLAY_WINDOW_MS = 5 * 60_000
export const CHART_WIDTH = 720
export const CHART_HEIGHT = 76
export const CHART_PAD_X = 10
export const CHART_PAD_Y = 9
export const MAX_CHART_POINTS = 220

export const PIPELINE_GROUP_ORDER = Object.freeze([
  '数据获取',
  '质量与清洗',
  '事件处理',
  '前端内容',
  '智能计算',
  '服务',
])

export const PIPELINE_STATUS_TEXT = Object.freeze({
  running: '运行中',
  warning: '需关注',
  failed: '中断',
  idle: '空闲',
  not_started: '未启动',
  unknown: '未知',
})

export const TREND_METRICS = Object.freeze([
  { key: 'daily_rate_per_min', label: '每日吞吐', unit: '/min', color: '#2b6cb0' },
  { key: 'wave_rate_per_min', label: 'Wave1 吞吐', unit: '/min', color: '#d97706' },
  { key: 'cpu_pressure_pct', label: 'CPU 压力', unit: '%', color: '#c24135' },
  { key: 'memory_used_pct', label: '内存', unit: '%', color: '#2f9e69' },
  { key: 'online_active', label: '在线', unit: '', color: '#6d5bd0' },
  { key: 'daily_progress_pct', label: '每日进度', unit: '%', color: '#168aad' },
  { key: 'wave_progress_pct', label: 'Wave1 进度', unit: '%', color: '#9a5a00' },
  { key: 'gpu_utilization_pct', label: 'GPU', unit: '%', color: '#7c3aed' },
])

export const DEFAULT_ACTIVE_TREND_KEYS = Object.freeze([
  'daily_rate_per_min',
  'wave_rate_per_min',
  'cpu_pressure_pct',
  'memory_used_pct',
])

export const CHART_GRID_TICKS = Object.freeze([0, 1, 2, 3, 4])

export const REFRESH_RATE_OPTIONS = Object.freeze([
  { label: '0.25s', value: 250 },
  { label: '0.5s', value: 500 },
  { label: '1s', value: 1_000 },
  { label: '2s', value: 2_000 },
  { label: '3s', value: 3_000 },
  { label: '5s', value: 5_000 },
  { label: '10s', value: 10_000 },
  { label: '30s', value: 30_000 },
])

export const DISPLAY_WINDOW_OPTIONS = Object.freeze([
  { label: '30秒', value: 30_000 },
  { label: '1分钟', value: 60_000 },
  { label: '5分钟', value: 5 * 60_000 },
  { label: '15分钟', value: 15 * 60_000 },
  { label: '1小时', value: 60 * 60_000 },
])

function record(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function normalizeRuntimeCatalog(value) {
  const catalog = record(value)
  return {
    ...catalog,
    available: catalog.available === true,
    read_only: catalog.read_only !== false,
    summary: record(catalog.summary),
    control: record(catalog.control),
    services: Array.isArray(catalog.services) ? catalog.services : [],
  }
}

export function normalizePipelineMonitorSnapshot(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const series = record(value.series)
  return {
    ...value,
    overview: record(value.overview),
    system: record(value.system),
    db: record(value.db),
    online: record(value.online),
    runtime_catalog: normalizeRuntimeCatalog(value.runtime_catalog),
    pipelines: Array.isArray(value.pipelines) ? value.pipelines : [],
    series: {
      ...series,
      samples: Array.isArray(series.samples) ? series.samples : [],
    },
  }
}

export function choosePipelineId(pipelines, selectedId = '') {
  const items = Array.isArray(pipelines) ? pipelines : []
  if (selectedId && items.some((pipeline) => pipeline?.id === selectedId)) return selectedId
  return (
    items.find((pipeline) => ['warning', 'failed', 'not_started'].includes(pipeline?.status))?.id
    || items[0]?.id
    || ''
  )
}

export function mergePipelineMetrics(metrics, update = {}) {
  const hasRate = update.rate_per_min !== null && update.rate_per_min !== undefined
  const rate = hasRate ? Number(update.rate_per_min) : Number.NaN
  const replacements = {
    '成功': update.successes,
    '失败': update.failures,
    '剩余': update.remaining,
    '速率': Number.isFinite(rate) ? Number(rate.toFixed(1)) : undefined,
  }
  return (Array.isArray(metrics) ? metrics : []).map((metric) => {
    if (!Object.prototype.hasOwnProperty.call(replacements, metric?.label)) return metric
    const replacement = replacements[metric.label]
    if (replacement === undefined || replacement === null) return metric
    return { ...metric, value: replacement }
  })
}

export function mergeFastPipelineSnapshot(currentValue, updateValue) {
  if (!updateValue || typeof updateValue !== 'object') return currentValue
  const current = normalizePipelineMonitorSnapshot(currentValue)
  if (!current) return normalizePipelineMonitorSnapshot(updateValue)

  const updateMap = new Map(
    (Array.isArray(updateValue.pipeline_updates) ? updateValue.pipeline_updates : [])
      .filter((item) => item?.id)
      .map((item) => [item.id, item]),
  )
  const pipelines = current.pipelines.map((pipeline) => {
    const update = updateMap.get(pipeline.id)
    if (!update) return pipeline
    return {
      ...pipeline,
      updated_at: update.updated_at || pipeline.updated_at,
      progress_pct: update.progress_pct ?? pipeline.progress_pct,
      rate_per_min: update.rate_per_min ?? pipeline.rate_per_min,
      eta_sec: update.eta_sec ?? pipeline.eta_sec,
      metrics: mergePipelineMetrics(pipeline.metrics, update),
      details: {
        ...(pipeline.details || {}),
        active_tasks: update.active_tasks ?? pipeline.details?.active_tasks,
      },
    }
  })

  return {
    ...current,
    generated_at: updateValue.generated_at || current.generated_at,
    overview: { ...current.overview, ...record(updateValue.overview) },
    online: updateValue.online || current.online,
    system: {
      ...current.system,
      ...record(updateValue.system),
      disk: current.system?.disk,
      processes: current.system?.processes,
    },
    pipelines,
    series: updateValue.series || current.series,
  }
}

export function groupPipelines(pipelines, order = PIPELINE_GROUP_ORDER) {
  const items = Array.isArray(pipelines) ? pipelines : []
  const groups = order
    .map((name) => ({ name, items: items.filter((pipeline) => pipeline?.group === name) }))
    .filter((group) => group.items.length)
  const known = new Set(order)
  const extra = items.filter((pipeline) => !known.has(pipeline?.group))
  if (extra.length) groups.push({ name: '其他', items: extra })
  return groups
}

export function buildPipelineKpis({ overview = {}, db = {}, online = {} } = {}) {
  const onlineActive = finiteNumber(overview.online_active)
  const onlineTtl = finiteNumber(online.ttl_sec)
  return [
    {
      label: '库内新闻',
      value: formatNumber(overview.news_total),
      sub: `近 24h 好新闻 ${formatNumber(overview.good_last_24h)}`,
      icon: 'database',
      tone: 'blue',
    },
    {
      label: '最新好新闻',
      value: formatTime(overview.latest_good_published_at, true),
      sub: db?.news?.raw_latest_published_at
        ? `原始最新 ${formatTime(db.news.raw_latest_published_at, true)}`
        : '等待库内时间',
      icon: 'radioTower',
      tone: 'green',
    },
    {
      label: 'Wave1 进度',
      value: formatPct(overview.wave1_progress_pct),
      sub: '历史新闻抽取',
      icon: 'activity',
      tone: 'amber',
    },
    {
      label: '每日更新',
      value: formatPct(overview.daily_progress_pct),
      sub: '滚动窗口批次',
      icon: 'wifi',
      tone: 'blue',
    },
    {
      label: '在线浏览器',
      value: formatNumber(onlineActive),
      sub: onlineTtl === null ? '活跃窗口未确认' : `${onlineTtl}s 活跃窗口`,
      icon: 'users',
      tone: onlineActive === null ? 'neutral' : 'green',
    },
    {
      label: '服务器压力',
      value: formatPct(overview.server_pressure_pct),
      sub: `内存 ${formatPct(overview.memory_used_pct)}`,
      icon: 'gauge',
      tone: pressureTone(overview.server_pressure_pct),
    },
  ]
}

export function decimateSamples(samples, limit = MAX_CHART_POINTS) {
  if (!Array.isArray(samples) || samples.length <= limit) return samples || []
  const step = Math.ceil(samples.length / limit)
  const result = []
  for (let index = 0; index < samples.length; index += step) result.push(samples[index])
  const last = samples[samples.length - 1]
  if (result[result.length - 1] !== last) result.push(last)
  return result.slice(-limit)
}

export function samplesWithinWindow(
  samples,
  windowMs = DEFAULT_DISPLAY_WINDOW_MS,
  fallbackLimit = MAX_CHART_POINTS,
) {
  if (!Array.isArray(samples) || !samples.length) return []
  const latestTs = Number(samples[samples.length - 1]?.ts || 0)
  if (!Number.isFinite(latestTs) || latestTs <= 0) return samples.slice(-fallbackLimit)
  const duration = Number(windowMs) || DEFAULT_DISPLAY_WINDOW_MS
  const cutoff = latestTs - duration / 1_000
  const filtered = samples.filter((sample) => Number(sample?.ts || 0) >= cutoff)
  return filtered.length ? filtered : samples.slice(-1)
}

export function formatNumber(value) {
  const number = finiteNumber(value)
  return number === null ? '—' : new Intl.NumberFormat('zh-CN').format(number)
}

export function formatPct(value) {
  const number = finiteNumber(value)
  if (number === null) return '—'
  return `${number.toFixed(number >= 10 ? 1 : 2)}%`
}

export function formatMetricValue(metric) {
  if (
    !metric
    || metric.value === null
    || metric.value === undefined
    || typeof metric.value === 'boolean'
    || metric.value === '—'
  ) return '—'
  if (typeof metric.value === 'number') {
    if (!Number.isFinite(metric.value)) return '—'
    const value = Math.abs(metric.value) >= 1_000 ? formatNumber(metric.value) : String(metric.value)
    return `${value}${metric.unit || ''}`
  }
  return `${metric.value}${metric.unit || ''}`
}

export function formatTime(value, compact = false) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    month: compact ? '2-digit' : '2-digit',
    day: compact ? '2-digit' : '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function formatDuration(seconds) {
  const value = finiteNumber(seconds)
  if (value === null || value <= 0) return '—'
  const days = Math.floor(value / 86_400)
  const hours = Math.floor((value % 86_400) / 3_600)
  const minutes = Math.floor((value % 3_600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分钟`
  return `${Math.max(1, minutes)}分钟`
}

export function formatBytes(bytes) {
  const value = finiteNumber(bytes)
  if (value === null || value <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1_024 && index < units.length - 1) {
    size /= 1_024
    index += 1
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[index]}`
}

export function pressureTone(value) {
  const number = finiteNumber(value)
  if (number === null) return 'neutral'
  if (number >= 90) return 'red'
  if (number >= 65) return 'amber'
  return 'green'
}

export function progressStyle(value) {
  const percentage = finiteNumber(value)
  const width = percentage === null ? 0 : Math.max(0, Math.min(100, percentage))
  return { width: `${width}%` }
}

export function metricDefinition(key, metrics = TREND_METRICS) {
  return metrics.find((metric) => metric.key === key)
}

export function metricColor(key, metrics = TREND_METRICS) {
  return metricDefinition(key, metrics)?.color || '#20252b'
}

export function metricUnit(key, metrics = TREND_METRICS) {
  return metricDefinition(key, metrics)?.unit || ''
}

export function metricValue(sample, key) {
  return finiteNumber(sample?.[key])
}

export function metricRange(samples, key) {
  const values = (Array.isArray(samples) ? samples : [])
    .map((sample) => metricValue(sample, key))
    .filter((value) => value !== null)
  if (!values.length) return { min: null, max: null }
  let min = Math.min(...values)
  let max = Math.max(...values)
  if (min === max) {
    const padding = Math.max(1, Math.abs(max) * 0.08)
    min -= padding
    max += padding
  }
  return { min, max }
}

export function chartXPosition(index, sampleCount, width = CHART_WIDTH) {
  if (sampleCount <= 1) return CHART_PAD_X
  return CHART_PAD_X + (index / (sampleCount - 1)) * (width - CHART_PAD_X * 2)
}

export function chartPoint(samples, index, key, width = CHART_WIDTH, height = CHART_HEIGHT) {
  const items = Array.isArray(samples) ? samples : []
  const value = metricValue(items[index], key)
  if (value === null || items.length <= 1) return null
  const { min, max } = metricRange(items, key)
  const x = chartXPosition(index, items.length, width)
  const normalized = (value - min) / (max - min || 1)
  const y = height - CHART_PAD_Y - normalized * (height - CHART_PAD_Y * 2)
  return { x, y, value }
}

export function chartPath(samples, key, width = CHART_WIDTH, height = CHART_HEIGHT) {
  const items = Array.isArray(samples) ? samples : []
  const commands = []
  let segmentStarted = false
  items.forEach((_sample, index) => {
    const point = chartPoint(items, index, key, width, height)
    if (!point) {
      segmentStarted = false
      return
    }
    commands.push(`${segmentStarted ? 'L' : 'M'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    segmentStarted = true
  })
  return commands.join(' ')
}

export function chartTickIndices(sampleCount) {
  if (!sampleCount) return []
  if (sampleCount <= 4) return Array.from({ length: sampleCount }, (_item, index) => index)
  return [...new Set([
    0,
    Math.floor((sampleCount - 1) / 3),
    Math.floor(((sampleCount - 1) * 2) / 3),
    sampleCount - 1,
  ])]
}

export function chartLaneAxisLabels(samples, key, formatValue) {
  const { min, max } = metricRange(samples, key)
  if (min === null || max === null) {
    return [
      { text: formatValue(null, key), top: '50%' },
    ]
  }
  const middle = min + (max - min) / 2
  return [
    { text: formatValue(max, key), top: `${(CHART_PAD_Y / CHART_HEIGHT) * 100}%` },
    { text: formatValue(middle, key), top: '50%' },
    {
      text: formatValue(min, key),
      top: `${((CHART_HEIGHT - CHART_PAD_Y) / CHART_HEIGHT) * 100}%`,
    },
  ]
}
