import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'

import {
  createOperationsHeartbeat,
  featureFreshness,
  fetchFeatureFreshness,
  formatFreshnessCutoff,
  freshnessNoticeReportKey,
  freshnessStateLabel,
  HEARTBEAT_CLIENT_KEY,
  HEARTBEAT_ENDPOINT,
  HEARTBEAT_INTERVAL_MS,
  normalizeFeatureFreshness,
  normalizeFeatureFreshnessReport,
  normalizeHeartbeatPath,
  refreshFeatureFreshness,
  unavailableFeatureFreshnessReport,
  useFeatureFreshness,
} from '../src/features/operations/index.js'

function createFakeClock() {
  let currentTime = 0
  let nextId = 1
  const tasks = new Map()

  function schedule(callback, delay, interval) {
    const id = nextId
    nextId += 1
    tasks.set(id, {
      at: currentTime + Math.max(0, Number(delay) || 0),
      callback,
      interval,
    })
    return id
  }

  async function settle() {
    for (let index = 0; index < 5; index += 1) await Promise.resolve()
  }

  return {
    now: () => currentTime,
    setTimeout: (callback, delay) => schedule(callback, delay, 0),
    clearTimeout: (id) => tasks.delete(id),
    setInterval: (callback, delay) => schedule(callback, delay, Math.max(1, delay)),
    clearInterval: (id) => tasks.delete(id),
    pendingCount: () => tasks.size,
    async advance(milliseconds) {
      const target = currentTime + milliseconds
      while (true) {
        const due = [...tasks.entries()]
          .filter(([, task]) => task.at <= target)
          .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0]
        if (!due) break

        const [id, task] = due
        currentTime = task.at
        if (task.interval > 0) {
          tasks.set(id, { ...task, at: task.at + task.interval })
        } else {
          tasks.delete(id)
        }
        task.callback()
        await settle()
      }
      currentTime = target
      await settle()
    },
    settle,
  }
}

function createEventTarget() {
  const listeners = new Map()
  return {
    visibilityState: 'visible',
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type).add(callback)
    },
    removeEventListener(type, callback) {
      listeners.get(type)?.delete(callback)
    },
    dispatch(type) {
      for (const callback of listeners.get(type) || []) callback()
    },
    listenerCount(type) {
      return listeners.get(type)?.size || 0
    },
  }
}

function createStorage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
  }
}

function response(status, retryAfter = '') {
  return {
    status,
    headers: {
      get(name) {
        return name.toLowerCase() === 'retry-after' ? retryAfter : null
      },
    },
  }
}

function publicStatusPayload({
  status = 'current',
  researchMode = 'current',
  ready = true,
  generatedAt = '2026-08-09T09:00:00Z',
  checks = {},
} = {}) {
  const mergedChecks = {
    search: {
      feature_id: 'search',
      status: 'up',
      metrics: {
        freshness_status: 'live',
        latest_news_at: '2026-08-09T08:00:00Z',
        freshness_lag_hours: 1,
        freshness_sla_hours: 48,
      },
    },
    'ground-news': {
      feature_id: 'ground-news',
      status: 'up',
      metrics: {
        freshness_status: 'live',
        latest_story_source_at: '2026-08-09T08:00:00Z',
        freshness_lag_hours: 1,
        freshness_sla_hours: 48,
      },
    },
    'opinion-analysis': {
      feature_id: 'opinion-analysis',
      status: 'up',
      metrics: {
        freshness_status: 'live',
        latest_score_date: '2026-08-09',
        freshness_lag_hours: 9,
        freshness_sla_hours: 72,
      },
    },
    ...checks,
  }
  const freshness = [
    ['search', '新闻与事件检索'],
    ['ground-news', '全球新闻观察'],
    ['opinion-analysis', '涉华舆情分析'],
  ].map(([id, label]) => {
    const threshold = mergedChecks[id].metrics.freshness_sla_hours ?? null
    const observed = mergedChecks[id].metrics.freshness_lag_hours ?? null
    return {
      id: `${id}-freshness`,
      label: `${label}数据新鲜度`,
      indicator: '最后有效数据距当前时间',
      measurement_status: threshold === null
        ? observed === null ? 'unavailable' : 'partial'
        : observed === null ? 'partial' : 'active',
      objective: null,
      threshold: threshold === null
        ? null
        : { comparison: 'less_than_or_equal', value: threshold, unit: 'hours' },
      observed: observed === null ? null : { value: observed, unit: 'hours' },
      threshold_assessment: threshold === null || observed === null
        ? 'unknown'
        : observed <= threshold ? 'within' : 'exceeded',
      compliance: 'not_computable',
      approval_state: 'not_approved',
      reason: '内部更新时限可用于降级；尚无经批准目标，不能判定 SLO 达标。',
      source: '运行时业务新鲜度探针',
    }
  })
  const affectedCapabilityIds = Object.keys(mergedChecks).filter(
    (featureId) => mergedChecks[featureId].status === 'down'
      || mergedChecks[featureId].metrics.freshness_status === 'offline',
  )
  const actionRequired = affectedCapabilityIds.length > 0
  return {
    schema_version: 'globemind.public-status.v1',
    generated_at: generatedAt,
    status,
    research_mode: researchMode,
    ready,
    checks: mergedChecks,
    objectives: {
      freshness,
      workflows: [
        ['search-response', '检索响应', '端到端检索成功率与延迟'],
        ['export-delivery', '导出交付', '导出成功率与完成时间'],
        ['report-generation', '报告生成', '报告成功率与完成时间'],
      ].map(([id, label, indicator]) => ({
        id,
        label,
        indicator,
        measurement_status: 'not_observed',
        objective: null,
        observed: null,
        compliance: 'not_computable',
        approval_state: 'not_approved',
        reason: '尚无观测样本，且目标未批准。',
        source: '持久化服务级观测（脱敏聚合）',
      })),
    },
    incident_history: {
      status: 'not_available',
      reason: '公开事件时间线尚未上线。',
    },
    degradation_disclosure: {
      status: actionRequired ? 'action_required' : 'monitoring',
      trigger: {
        capability_state: actionRequired ? 'down_observed' : 'no_down_observed',
        affected_capability_ids: affectedCapabilityIds,
        workflow_breach_state: 'unknown',
        affected_workflow_ids: [],
      },
      incident_owner: { availability: 'unavailable', value: null },
      recovery_estimate: { availability: 'unavailable', value: null },
      last_status_update: { availability: 'unavailable', value: null },
      reason: actionRequired
        ? '已观测到公开能力离线，处置证据尚不可用。'
        : '未观测到公开能力离线，但这不证明没有事件。',
    },
  }
}

function heartbeatOptions(clock, overrides = {}) {
  return {
    burstDelayMs: 100,
    clearIntervalImpl: clock.clearInterval,
    clearTimeoutImpl: clock.clearTimeout,
    intervalMs: HEARTBEAT_INTERVAL_MS,
    now: clock.now,
    randomUUID: () => '00000000-0000-4000-8000-000000000001',
    setIntervalImpl: clock.setInterval,
    setTimeoutImpl: clock.setTimeout,
    ...overrides,
  }
}

test('backend freshness metrics drive live, historical, and offline states', () => {
  const report = normalizeFeatureFreshnessReport(publicStatusPayload({
    status: 'unavailable',
    researchMode: 'historical',
    ready: false,
    checks: {
      'ground-news': {
        feature_id: 'ground-news',
        status: 'stale',
        metrics: {
          freshness_status: 'stale',
          latest_story_source_at: '2026-07-22T10:30:00Z',
          freshness_lag_hours: 430.5,
          freshness_sla_hours: 48,
        },
      },
      'opinion-analysis': {
        feature_id: 'opinion-analysis',
        status: 'down',
        metrics: { freshness_status: 'offline', freshness_sla_hours: 72 },
      },
    },
  }), { receivedAt: new Date('2026-08-09T09:00:00Z') })

  assert.equal(featureFreshness(report, 'search').state, 'live')
  assert.equal(featureFreshness(report, 'search').historical, false)
  assert.equal(featureFreshness(report, 'ground-news').state, 'stale')
  assert.equal(featureFreshness(report, 'ground-news').historical, true)
  assert.equal(featureFreshness(report, 'ground-news').cutoff, '2026-07-22T10:30:00.000Z')
  assert.equal(featureFreshness(report, 'opinion-analysis').state, 'offline')
  assert.deepEqual(report.affected.map((feature) => feature.featureId), [
    'ground-news',
    'opinion-analysis',
  ])
  assert.equal(report.historicalMode, true)
  assert.equal(report.cutoff, '2026-07-22T10:30:00.000Z')
  assert.equal(freshnessStateLabel('stale'), '历史快照')
  assert.match(formatFreshnessCutoff(report.cutoff), /2026/)

  const unanchored = normalizeFeatureFreshness({
    feature_id: 'search',
    status: 'down',
    metrics: {
      freshness_status: 'offline',
      freshness_lag_hours: 1,
      freshness_sla_hours: 48,
    },
  }, 'search', { evaluatedAt: new Date('2026-08-09T09:00:00Z') })
  assert.equal(unanchored.state, 'offline')
  assert.equal(unanchored.cutoff, '')
  assert.equal(unanchored.lagHours, null)
})

test('freshness transport accepts degraded HTTP responses with checks and fails closed otherwise', async () => {
  const calls = []
  const degraded = await fetchFeatureFreshness({
    endpoint: '/api/status',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return {
        status: 503,
        async json() {
          return publicStatusPayload({
            status: 'unavailable',
            researchMode: 'historical',
            ready: false,
            generatedAt: new Date().toISOString(),
            checks: {
              search: {
                feature_id: 'search',
                status: 'down',
                metrics: { freshness_status: 'offline', freshness_sla_hours: 48 },
              },
              'ground-news': {
                feature_id: 'ground-news',
                status: 'down',
                metrics: { freshness_status: 'offline' },
              },
              'opinion-analysis': {
                feature_id: 'opinion-analysis',
                status: 'down',
                metrics: { freshness_status: 'offline', freshness_sla_hours: 72 },
              },
            },
          })
        },
      }
    },
  })

  assert.equal(calls[0].options.method, 'GET')
  assert.equal(calls[0].options.cache, 'no-store')
  assert.equal(featureFreshness(degraded, 'ground-news').state, 'offline')

  await assert.rejects(
    fetchFeatureFreshness({
      fetchImpl: async () => ({ json: async () => ({ detail: 'not found' }) }),
    }),
    /schema 不兼容/,
  )
  const unavailable = unavailableFeatureFreshnessReport(
    'postgres' + '://operator:secret@internal.example/private?token=leak',
    { receivedAt: new Date('2026-08-09T09:00:00Z') },
  )
  assert.equal(unavailable.historicalMode, true)
  assert.equal(unavailable.receivedAt, '2026-08-09T09:00:00.000Z')
  assert.equal(unavailable.error, '数据状态服务暂不可用，请稍后重试。')
  assert.doesNotMatch(JSON.stringify(unavailable), /operator|secret|internal\.example|token=leak/)
})

test('freshness notice generation preserves unknown and changes for a new report generation', () => {
  const base = {
    phase: 'ready',
    generatedAt: '2026-08-09T09:00:00.000Z',
    receivedAt: '2026-08-09T09:00:01.000Z',
    cutoff: '',
    affected: [
      { featureId: 'search', state: 'unknown', lagHours: null, slaHours: null },
    ],
  }
  const first = freshnessNoticeReportKey(base)
  const next = freshnessNoticeReportKey({
    ...base,
    generatedAt: '2026-08-09T09:05:00.000Z',
  })
  const sameGenerationReceivedLater = freshnessNoticeReportKey({
    ...base,
    receivedAt: '2026-08-09T09:03:00.000Z',
  })

  assert.notEqual(first, next)
  assert.equal(first, sameGenerationReceivedLater)
  assert.match(first, /search:unknown/)
  assert.doesNotMatch(first, /search:(?:live|0)/)
})

test('forced freshness refresh ignores a late response from an older request generation', async () => {
  const pending = []
  const makeOfflinePayload = (generatedAt) => publicStatusPayload({
    status: 'unavailable',
    researchMode: 'historical',
    ready: false,
    generatedAt,
    checks: Object.fromEntries(['search', 'ground-news', 'opinion-analysis'].map((featureId) => [
      featureId,
      {
        feature_id: featureId,
        status: 'down',
        metrics: { freshness_status: 'offline' },
      },
    ])),
  })
  const fetchImpl = async () => new Promise((resolve) => pending.push(resolve))
  const olderGeneratedAt = new Date(Date.now() - 2_000).toISOString()
  const newerGeneratedAt = new Date(Date.now() - 1_000).toISOString()

  const older = refreshFeatureFreshness({ force: true, fetchImpl })
  const newer = refreshFeatureFreshness({ force: true, fetchImpl })
  pending[1]({ json: async () => makeOfflinePayload(newerGeneratedAt) })
  await newer
  pending[0]({ json: async () => makeOfflinePayload(olderGeneratedAt) })
  await older

  assert.equal(useFeatureFreshness().report.value.generatedAt, newerGeneratedAt)
})

test('freshness normalization rejects partial, contradictory, and null-as-zero contracts', () => {
  assert.throws(
    () => normalizeFeatureFreshnessReport({
      schema_version: 'globemind.public-status.v1',
      generated_at: '2026-08-09T09:00:00Z',
      status: 'current',
      research_mode: 'current',
      ready: true,
      checks: {},
    }, { receivedAt: new Date('2026-08-09T09:00:00Z') }),
    /缺少受监测能力/,
  )

  const invalidLive = publicStatusPayload({
    checks: {
      search: {
        feature_id: 'search',
        status: 'up',
        metrics: {
          freshness_status: 'live',
          latest_news_at: '2026-08-09T08:00:00Z',
          freshness_lag_hours: null,
          freshness_sla_hours: null,
        },
      },
    },
  })
  const normalizationOptions = { receivedAt: new Date('2026-08-09T09:00:00Z') }
  assert.throws(
    () => normalizeFeatureFreshnessReport(invalidLive, normalizationOptions),
    /内部冲突/,
  )

  const contradictory = publicStatusPayload({ researchMode: 'historical' })
  assert.throws(
    () => normalizeFeatureFreshnessReport(contradictory, normalizationOptions),
    /内部冲突/,
  )

  assert.throws(
    () => normalizeFeatureFreshnessReport(
      publicStatusPayload({ generatedAt: '2026-08-09T08:00:00Z' }),
      normalizationOptions,
    ),
    /生成时间无效/,
  )
  assert.throws(
    () => normalizeFeatureFreshnessReport(
      publicStatusPayload(),
      { receivedAt: new Date('invalid') },
    ),
    /接收时间无效/,
  )

  const mismatchedLag = publicStatusPayload()
  mismatchedLag.checks.search.metrics.latest_news_at = '2026-07-01T00:00:00Z'
  assert.throws(
    () => normalizeFeatureFreshnessReport(mismatchedLag, normalizationOptions),
    /新鲜度证据矛盾/,
  )

  const approvedFreshness = publicStatusPayload()
  approvedFreshness.objectives.freshness[0].approval_state = 'approved'
  assert.throws(
    () => normalizeFeatureFreshnessReport(approvedFreshness, normalizationOptions),
    /新鲜度目标契约矛盾/,
  )
})

test('global freshness notice fails closed without overlaying page interactions', async () => {
  const source = await readFile(
    new URL('../src/components/DataFreshnessNotice.vue', import.meta.url),
    'utf8',
  )
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')

  assert.match(source, /useFeatureFreshness\(\)/)
  assert.match(source, /report\.value\.phase === ['"]idle['"]/)
  assert.match(source, /report\.value\.phase === ['"]error['"]/)
  assert.match(source, /freshnessNoticeReportKey\(report\.value\)/)
  assert.doesNotMatch(source, /routeGeneration/)
  assert.doesNotMatch(source, /watch\(\(\) => route\.fullPath/)
  assert.match(source, /getAuthChangedEventName\(\)/)
  assert.match(source, /identityGeneration\.value \+= 1/)
  assert.match(source, /role=['"]region['"]/)
  assert.match(source, /aria-live=['"]polite['"]/)
  assert.match(source, /aria-atomic=['"]true['"]/)
  assert.match(source, /sessionStorage\.setItem/)
  assert.match(source, /:aria-expanded=/)
  assert.match(source, /展开业务数据时效提示/)
  assert.match(source, /onMounted\(\(\) => \{\s*void refresh\(\)/)
  assert.doesNotMatch(source, /setTimeout\([^)]*refresh/)
  assert.doesNotMatch(source, /\.freshness-notice\s*\{[^}]*position:\s*fixed/s)
  assert.match(source, /\.freshness-notice button\s*\{[^}]*width:\s*44px[^}]*height:\s*44px/s)
  assert.match(source, /\.freshness-notice button:focus-visible/)
  assert.match(source, /@media \(prefers-reduced-motion:\s*reduce\)/)
  assert.doesNotMatch(source, /已知快照最晚截止/)
  assert.match(app, /\.freshness-notice ~ \.route-content \.home\s*\{\s*padding-top:\s*0;/)
  assert.match(app, /\.freshness-notice ~ \.route-content \.intel-page\s*\{\s*--nav-offset:\s*0px;/)
})

test('mount, initial route, and visibility bursts send one heartbeat with the latest state', async () => {
  const clock = createFakeClock()
  const eventTarget = createEventTarget()
  const calls = []
  let path = '/'
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget,
      fetchImpl: async (url, options) => {
        calls.push({ url, options })
        return response(200)
      },
      getPath: () => path,
      storage: createStorage(),
    }),
  )

  heartbeat.start()
  path = '/data-assistant?topic=risk'
  heartbeat.notifyRouteChange()
  eventTarget.visibilityState = 'hidden'
  eventTarget.dispatch('visibilitychange')
  path = '/data-service/story-graph'
  heartbeat.notifyRouteChange()

  await clock.advance(99)
  assert.equal(calls.length, 0)
  await clock.advance(1)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, HEARTBEAT_ENDPOINT)
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    client_id: '00000000-0000-4000-8000-000000000001',
    path: '/data-service/story-graph',
    visibility: 'hidden',
  })
  assert.equal(calls[0].options.keepalive, true)
})

test('heartbeat payloads strip query parameters, fragments, and non-route values', async () => {
  assert.equal(normalizeHeartbeatPath('/reset-password?token=secret#fragment'), '/reset-password')
  assert.equal(normalizeHeartbeatPath('https://globemind.top/private?token=secret'), '/')

  const clock = createFakeClock()
  const calls = []
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async (_url, options) => {
        calls.push(JSON.parse(options.body))
        return response(200)
      },
      getPath: () => '/reset-password?token=reset-secret#fragment-secret',
      storage: createStorage(),
    }),
  )

  heartbeat.start()
  await clock.advance(100)
  assert.equal(calls[0].path, '/reset-password')
  assert.doesNotMatch(JSON.stringify(calls), /reset-secret|fragment-secret/)
})

test('the controller preserves the 30 second heartbeat interval and client id', async () => {
  const clock = createFakeClock()
  const storage = createStorage()
  const payloads = []
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async (_url, options) => {
        payloads.push(JSON.parse(options.body))
        return response(200)
      },
      getPath: () => '/reports',
      storage,
    }),
  )

  heartbeat.start()
  await clock.advance(100)
  await clock.advance(HEARTBEAT_INTERVAL_MS - 100)
  assert.equal(payloads.length, 1)
  await clock.advance(100)
  assert.equal(payloads.length, 2)
  assert.equal(payloads[0].client_id, payloads[1].client_id)
  assert.equal(storage.getItem(HEARTBEAT_CLIENT_KEY), payloads[0].client_id)

  heartbeat.stop()
  const recreated = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async (_url, options) => {
        payloads.push(JSON.parse(options.body))
        return response(200)
      },
      randomUUID: () => '00000000-0000-4000-8000-000000000002',
      storage,
    }),
  )
  recreated.start()
  await clock.advance(100)
  assert.equal(payloads[2].client_id, payloads[0].client_id)

  const unsafeStorage = createStorage()
  unsafeStorage.setItem(HEARTBEAT_CLIENT_KEY, 'unsafe/@client')
  const regeneratedPayloads = []
  const regenerated = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async (_url, options) => {
        regeneratedPayloads.push(JSON.parse(options.body))
        return response(200)
      },
      randomUUID: () => '00000000-0000-4000-8000-000000000003',
      storage: unsafeStorage,
    }),
  )
  regenerated.start()
  await clock.advance(100)
  assert.equal(regeneratedPayloads[0].client_id, '00000000-0000-4000-8000-000000000003')
})

test('429 Retry-After backs off and coalesces changes until the retry boundary', async () => {
  const clock = createFakeClock()
  const calls = []
  let path = '/first'
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async (_url, options) => {
        calls.push(JSON.parse(options.body))
        return calls.length === 1 ? response(429, '2') : response(200)
      },
      getPath: () => path,
      storage: createStorage(),
    }),
  )

  heartbeat.start()
  await clock.advance(100)
  assert.equal(calls.length, 1)

  await clock.advance(400)
  path = '/latest'
  heartbeat.notifyRouteChange()
  await clock.advance(1599)
  assert.equal(calls.length, 1)
  await clock.advance(1)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].path, '/latest')
})

test('stop removes timers and listeners, including a pending 429 response', async () => {
  const clock = createFakeClock()
  const eventTarget = createEventTarget()
  let resolveRequest
  let calls = 0
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget,
      fetchImpl: () => {
        calls += 1
        return new Promise((resolve) => {
          resolveRequest = resolve
        })
      },
      storage: createStorage(),
    }),
  )

  heartbeat.start()
  heartbeat.start()
  assert.equal(eventTarget.listenerCount('visibilitychange'), 1)
  await clock.advance(100)
  assert.equal(calls, 1)

  heartbeat.stop()
  assert.equal(eventTarget.listenerCount('visibilitychange'), 0)
  assert.equal(clock.pendingCount(), 0)
  resolveRequest(response(429, '60'))
  await clock.settle()
  eventTarget.dispatch('visibilitychange')
  await clock.advance(2 * HEARTBEAT_INTERVAL_MS)
  assert.equal(calls, 1)
  assert.equal(clock.pendingCount(), 0)
})

test('network failures remain isolated and do not stop future intervals', async () => {
  const clock = createFakeClock()
  let calls = 0
  const heartbeat = createOperationsHeartbeat(
    heartbeatOptions(clock, {
      eventTarget: createEventTarget(),
      fetchImpl: async () => {
        calls += 1
        if (calls === 1) throw new Error('offline')
        return response(200)
      },
      storage: createStorage(),
    }),
  )

  heartbeat.start()
  await clock.advance(100)
  await clock.advance(HEARTBEAT_INTERVAL_MS)
  assert.equal(calls, 2)
})

async function walkFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) files.push(...(await walkFiles(path)))
    else files.push(path)
  }
  return files
}

test('App composes the operations public API and leaves a single app mount id', async () => {
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8')
  const operationsIndex = await readFile(
    new URL('../src/features/operations/index.js', import.meta.url),
    'utf8',
  )
  const monitor = await readFile(new URL('../src/views/PipelineMonitor.vue', import.meta.url), 'utf8')

  assert.match(
    app,
    /import \{ createOperationsHeartbeat \} from ['"]\.\/features\/operations\/index\.js['"]/,
  )
  assert.match(app, /operationsHeartbeat\.start\(\)/)
  assert.match(app, /operationsHeartbeat\.notifyRouteChange\(\)/)
  assert.match(app, /operationsHeartbeat\.stop\(\)/)
  assert.match(app, /getPath:\s*\(\) => route\.path/)
  assert.doesNotMatch(app, /getPath:[^\n]*route\.fullPath/)
  assert.doesNotMatch(app, /\bfetch\s*\(|localStorage|visibilitychange|setInterval/)
  assert.equal([...app.matchAll(/\bid=['"]app['"]/g)].length, 0)
  assert.match(app, /<div class="app-shell"/)
  assert.equal([...html.matchAll(/id=['"]app['"]/g)].length, 1)
  assert.match(operationsIndex, /from ['"]\.\/heartbeat\.js['"]/)
  assert.doesNotMatch(monitor, /Live Signals/)
  assert.match(monitor, />状态信号<\/p>/)
  assert.match(monitor, /历史采样未配置；当前读取不会生成趋势点/)

  const sourceRoot = new URL('../src', import.meta.url).pathname
  const sourceFiles = (await walkFiles(sourceRoot)).filter((path) => /\.(?:js|vue)$/.test(path))
  for (const path of sourceFiles) {
    const source = await readFile(path, 'utf8')
    for (const match of source.matchAll(/from ['"][^'"]*features\/operations\/([^'"]+)['"]/g)) {
      assert.equal(match[1], 'index.js', `deep operations import in ${path}`)
    }
  }
})
