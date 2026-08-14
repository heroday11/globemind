import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  PIPELINE_MONITOR_ENDPOINTS,
  buildPipelineKpis,
  buildPipelineMonitorAuthHeaders,
  chartPath,
  chartPoint,
  chartTickIndices,
  choosePipelineId,
  createLatestPipelineMonitorRequest,
  createPipelineMonitorApi,
  createPipelineMonitorRefreshScheduler,
  decimateSamples,
  formatMetricValue,
  formatNumber,
  groupPipelines,
  mergeFastPipelineSnapshot,
  mergePipelineMetrics,
  metricValue,
  metricRange,
  normalizePipelineMonitorSnapshot,
  normalizeRuntimeCatalog,
  pressureTone,
  samplesWithinWindow,
} from '../src/features/operations/index.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

test('pipeline monitor API preserves endpoints, auth, fresh params, and abort signals', async () => {
  const calls = []
  const httpClient = {
    async get(url, options) {
      calls.push({ url, options })
      return { data: { generated_at: '2026-07-11T00:00:00Z' } }
    },
  }
  const api = createPipelineMonitorApi({
    httpClient,
    getAccessToken: () => 'ops-token',
  })
  const fullSignal = { aborted: false }
  const fastSignal = { aborted: false }

  assert.equal(api.hasAccessToken(), true)
  assert.deepEqual(await api.fetchSnapshot({ fresh: true, signal: fullSignal }), {
    generated_at: '2026-07-11T00:00:00Z',
  })
  await api.fetchFastSnapshot({ signal: fastSignal })
  await api.fetchRuntimeCatalog({ signal: fastSignal })

  assert.equal(calls[0].url, '/api/ops/pipeline-monitor')
  assert.deepEqual(calls[0].options.params, { fresh: true })
  assert.deepEqual(calls[0].options.headers, { Authorization: 'Bearer ops-token' })
  assert.equal(calls[0].options.signal, fullSignal)
  assert.equal(calls[1].url, '/api/ops/pipeline-monitor/fast')
  assert.equal(calls[1].options.signal, fastSignal)
  assert.equal(calls[2].url, '/api/ops/runtime-catalog')
  assert.equal(calls[2].options.signal, fastSignal)
  assert.deepEqual(buildPipelineMonitorAuthHeaders(''), {})
  assert.deepEqual(buildPipelineMonitorAuthHeaders('x'), { Authorization: 'Bearer x' })
  assert.deepEqual(PIPELINE_MONITOR_ENDPOINTS, {
    snapshot: '/api/ops/pipeline-monitor',
    fastSnapshot: '/api/ops/pipeline-monitor/fast',
    runtimeCatalog: '/api/ops/runtime-catalog',
  })
})

test('snapshot DTO normalization and pipeline selection tolerate incomplete payloads', () => {
  assert.equal(normalizePipelineMonitorSnapshot(null), null)
  assert.deepEqual(normalizePipelineMonitorSnapshot({
    overview: null,
    system: 'invalid',
    pipelines: 'invalid',
    series: { samples: null, interval_sec: 2 },
  }), {
    overview: {},
    system: {},
    pipelines: [],
    series: { samples: [], interval_sec: 2 },
    db: {},
    online: {},
    runtime_catalog: {
      available: false,
      read_only: true,
      summary: {},
      control: {},
      services: [],
    },
  })

  assert.deepEqual(normalizeRuntimeCatalog({
    available: true,
    read_only: true,
    summary: null,
    control: { enabled: false },
    services: null,
  }), {
    available: true,
    read_only: true,
    summary: {},
    control: { enabled: false },
    services: [],
  })

  const pipelines = [
    { id: 'healthy', status: 'running', group: '服务' },
    { id: 'attention', status: 'warning', group: '其他组' },
  ]
  assert.equal(choosePipelineId(pipelines), 'attention')
  assert.equal(choosePipelineId(pipelines, 'healthy'), 'healthy')
  assert.equal(choosePipelineId(pipelines, 'missing'), 'attention')
  assert.deepEqual(groupPipelines(pipelines), [
    { name: '服务', items: [pipelines[0]] },
    { name: '其他', items: [pipelines[1]] },
  ])
})

test('fast snapshots merge dynamic fields without mutating full snapshot details', () => {
  const current = {
    generated_at: 'old',
    overview: { news_total: 10, stable: true },
    online: { active: 1 },
    db: { news: { total: 10 } },
    system: {
      cpu: { pressure_pct: 12 },
      disk: { used_pct: 45 },
      processes: [{ pid: 1 }],
    },
    pipelines: [{
      id: 'daily_ingest',
      updated_at: 'old',
      progress_pct: 10,
      rate_per_min: 2,
      eta_sec: 60,
      metrics: [
        { label: '成功', value: 1 },
        { label: '速率', value: 2, unit: '/min' },
        { label: '保留', value: 9 },
      ],
      details: { active_tasks: 1, last_log: ['keep'] },
    }],
    series: { samples: [{ ts: 1 }] },
  }
  const before = structuredClone(current)
  const merged = mergeFastPipelineSnapshot(current, {
    generated_at: 'new',
    overview: { news_total: 11 },
    online: { active: 2 },
    system: {
      cpu: { pressure_pct: 50 },
      disk: { used_pct: 99 },
      processes: [{ pid: 999 }],
    },
    pipeline_updates: [{
      id: 'daily_ingest',
      updated_at: 'new',
      progress_pct: 20,
      rate_per_min: '12.34',
      eta_sec: 30,
      successes: 7,
      active_tasks: 3,
    }],
    series: { samples: [{ ts: 2 }] },
  })

  assert.deepEqual(current, before)
  assert.equal(merged.generated_at, 'new')
  assert.deepEqual(merged.overview, { news_total: 11, stable: true })
  assert.deepEqual(merged.system.cpu, { pressure_pct: 50 })
  assert.deepEqual(merged.system.disk, { used_pct: 45 })
  assert.deepEqual(merged.system.processes, [{ pid: 1 }])
  assert.equal(merged.pipelines[0].progress_pct, 20)
  assert.equal(merged.pipelines[0].metrics[0].value, 7)
  assert.equal(merged.pipelines[0].metrics[1].value, 12.3)
  assert.equal(merged.pipelines[0].metrics[2].value, 9)
  assert.deepEqual(merged.pipelines[0].details, { active_tasks: 3, last_log: ['keep'] })
  assert.deepEqual(
    mergePipelineMetrics([{ label: '速率', value: 2 }], { rate_per_min: null }),
    [{ label: '速率', value: 2 }],
  )
})

test('trend helpers preserve windowing, decimation, flat ranges, and chart boundaries', () => {
  const samples = Array.from({ length: 300 }, (_item, index) => ({
    ts: index + 1,
    time: `2026-07-11T00:${String(index).padStart(2, '0')}:00Z`,
    cpu_pressure_pct: index,
  }))
  const recent = samplesWithinWindow(samples, 30_000)
  assert.equal(recent.length, 31)
  assert.equal(recent[0].ts, 270)
  assert.equal(recent.at(-1).ts, 300)

  const decimated = decimateSamples(samples, 50)
  assert.ok(decimated.length <= 50)
  assert.equal(decimated.at(-1), samples.at(-1))
  assert.deepEqual(metricRange([{ value: 5 }, { value: 5 }], 'value'), { min: 4, max: 6 })
  assert.deepEqual(chartTickIndices(0), [])
  assert.deepEqual(chartTickIndices(3), [0, 1, 2])
  assert.deepEqual(chartTickIndices(10), [0, 3, 6, 9])
  assert.equal(chartPoint([{ value: 1 }], 0, 'value'), null)
  assert.match(chartPath([{ value: 1 }, { value: 2 }], 'value'), /^M .+ L /)
  assert.equal(metricValue({ value: null }, 'value'), null)
  assert.equal(metricValue({ value: false }, 'value'), null)
  assert.equal(metricValue({ value: '0' }, 'value'), null)
  assert.equal(metricValue({ value: 0 }, 'value'), 0)
  assert.deepEqual(metricRange([{ value: null }], 'value'), { min: null, max: null })
  assert.doesNotMatch(
    chartPath([{ value: 1 }, { value: null }, { value: 2 }], 'value'),
    / L /,
  )
})

test('KPI view model keeps operations labels, icon keys, and pressure thresholds stable', () => {
  const kpis = buildPipelineKpis({
    overview: {
      news_total: 1_234,
      good_last_24h: 7,
      wave1_progress_pct: 80,
      daily_progress_pct: 5,
      online_active: 2,
      server_pressure_pct: 91,
      memory_used_pct: 50,
    },
    db: {},
    online: { ttl_sec: 120 },
  })
  assert.deepEqual(kpis.map((item) => item.label), [
    '库内新闻',
    '最新好新闻',
    'Wave1 进度',
    '每日更新',
    '在线浏览器',
    '服务器压力',
  ])
  assert.equal(kpis[0].value, '1,234')
  assert.equal(kpis[4].sub, '120s 活跃窗口')
  assert.equal(kpis[5].tone, 'red')
  assert.deepEqual(kpis.map((item) => item.icon), [
    'database',
    'radioTower',
    'activity',
    'wifi',
    'users',
    'gauge',
  ])

  const unknown = buildPipelineKpis({
    overview: {
      news_total: null,
      good_last_24h: null,
      online_active: null,
      server_pressure_pct: null,
    },
    online: { measurement_state: 'unavailable', ttl_sec: 90 },
  })
  assert.equal(formatNumber(null), '—')
  assert.equal(formatNumber(false), '—')
  assert.equal(formatNumber('7'), '—')
  assert.equal(formatMetricValue({ value: null, unit: '条' }), '—')
  assert.equal(formatMetricValue({ value: Number.NaN, unit: '条' }), '—')
  assert.equal(unknown[0].value, '—')
  assert.match(unknown[0].sub, /—/)
  assert.equal(unknown[4].value, '—')
  assert.equal(unknown[4].tone, 'neutral')
  assert.equal(unknown[5].tone, 'neutral')
  assert.equal(pressureTone(null), 'neutral')
})

test('latest request coordinator aborts replaced work and blocks stale commits', async () => {
  const controllers = []
  class FakeAbortController {
    constructor() {
      this.signal = { aborted: false }
      controllers.push(this)
    }
    abort() {
      this.signal.aborted = true
    }
  }
  const first = deferred()
  const second = deferred()
  const requests = createLatestPipelineMonitorRequest({
    AbortControllerImpl: FakeAbortController,
  })

  const firstRun = requests.run(() => first.promise)
  const secondRun = requests.run(() => second.promise)
  assert.equal(controllers[0].signal.aborted, true)
  assert.equal(requests.isActive(), true)
  first.resolve('old')
  second.resolve('new')
  assert.deepEqual(await firstRun, { status: 'stale', latest: false })
  assert.deepEqual(await secondRun, { status: 'success', latest: true, value: 'new' })
  assert.equal(requests.isActive(), false)

  const aborted = await requests.run(async (signal) => {
    signal.aborted = true
    const error = new Error('cancelled')
    error.name = 'CanceledError'
    throw error
  })
  assert.equal(aborted.status, 'aborted')

  const pending = deferred()
  const cancelledRun = requests.run(() => pending.promise)
  requests.cancel()
  assert.equal(controllers.at(-1).signal.aborted, true)
  pending.resolve('ignored')
  assert.deepEqual(await cancelledRun, { status: 'stale', latest: false })
  assert.equal(requests.isActive(), false)
})

test('refresh scheduler replaces timers and stops both refresh lanes', () => {
  let nextId = 1
  const timers = new Map()
  const cleared = []
  let fullRefreshes = 0
  let fastRefreshes = 0
  const scheduler = createPipelineMonitorRefreshScheduler({
    refreshFull: () => { fullRefreshes += 1 },
    refreshFast: () => { fastRefreshes += 1 },
    setIntervalImpl(callback, delay) {
      const id = nextId++
      timers.set(id, { callback, delay })
      return id
    },
    clearIntervalImpl(id) {
      cleared.push(id)
      timers.delete(id)
    },
  })

  scheduler.sync({ enabled: true, fastIntervalMs: 250 })
  assert.deepEqual([...timers.values()].map((timer) => timer.delay), [30_000, 250])
  for (const timer of timers.values()) timer.callback()
  assert.equal(fullRefreshes, 1)
  assert.equal(fastRefreshes, 1)

  scheduler.sync({ enabled: true, fastIntervalMs: 1_000 })
  assert.deepEqual(cleared, [1, 2])
  assert.deepEqual([...timers.values()].map((timer) => timer.delay), [30_000, 1_000])
  scheduler.sync({ enabled: false })
  assert.equal(timers.size, 0)
  assert.deepEqual(cleared, [1, 2, 3, 4])
  scheduler.stop()
  assert.deepEqual(cleared, [1, 2, 3, 4])
})

test('PipelineMonitor consumes only the operations public entry and cancels owned requests', async () => {
  const page = await readFile(
    new URL('../src/views/PipelineMonitor.vue', import.meta.url),
    'utf8',
  )
  const operationsEntry = await readFile(
    new URL('../src/features/operations/index.js', import.meta.url),
    'utf8',
  )
  const monitorEntry = await readFile(
    new URL('../src/features/operations/monitor/index.js', import.meta.url),
    'utf8',
  )

  assert.match(page, /from ['"]@\/features\/operations\/index\.js['"]/)
  assert.doesNotMatch(page, /from ['"]@\/features\/operations\/(?!index\.js)/)
  assert.doesNotMatch(page, /\baxios\b|\bgetToken\b|\bfetch\s*\(/)
  assert.doesNotMatch(page, /\/api\/ops\/pipeline-monitor/)
  assert.match(page, /snapshotRequest\.cancel\(\)/)
  assert.match(page, /fastSnapshotRequest\.cancel\(\)/)
  assert.match(page, /runtimeCatalog\.summary\?\.service_count/)
  assert.match(page, /selectedPipeline\.management\?\.owner/)
  assert.match(page, /management\?\.identity_contract\?\.assurance/)
  assert.match(page, /management\?\.health_policy\?\.mode/)
  assert.match(page, /management\?\.effective_lifecycle_state/)
  assert.match(page, /management\.controller\.interface/)
  assert.match(page, /management\.management_blockers/)
  assert.match(page, /进程遥测（启发式）/)
  assert.ok(page.split('\n').length < 2_050)
  assert.match(operationsEntry, /normalizeHeartbeatPath/)
  assert.match(operationsEntry, /export \* from ['"]\.\/monitor\/index\.js['"]/)
  assert.match(monitorEntry, /from ['"]\.\/api\.js['"]/)
  assert.match(monitorEntry, /from ['"]\.\/model\.js['"]/)
  assert.match(monitorEntry, /from ['"]\.\/request\.js['"]/)
  assert.match(monitorEntry, /from ['"]\.\/scheduler\.js['"]/)
})
