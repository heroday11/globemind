import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  briefingPlannedTimeLabel,
  briefingRecordedTimeLabel,
  briefingRunStatusLabel,
  buildBriefingPeriodicTasks,
  calculateBriefingStats,
  createBriefingScheduleForm,
  normalizeBriefingScheduleDto,
} from '../src/features/assistant/briefing/model.js'
import {
  createBriefingScheduleService,
  createLatestBriefingScheduleLoader,
} from '../src/features/assistant/briefing/service.js'


test('briefing DTO preserves unknown counters and redacts persisted failures', () => {
  const schedule = normalizeBriefingScheduleDto({
    id: 'sched-1',
    topic: 'Risk',
    enabled: true,
    run_count: null,
    last_status: 'failed',
    last_error: 'postgresql' + '://private-user:private-password@example.invalid/db',
    recent_runs: [
      {
        id: 'run-1',
        status: 'unexpected',
        error: 'token=secret-canary',
        duration_ms: null,
      },
    ],
  })

  assert.equal(schedule.run_count, null)
  assert.equal(schedule.last_error, '最近一次运行失败；内部错误详情未公开')
  assert.equal(schedule.recent_runs[0].status, 'unknown')
  assert.equal(schedule.recent_runs[0].error, '')
  assert.equal(JSON.stringify(schedule).includes('private-password'), false)
  assert.equal(JSON.stringify(schedule).includes('secret-canary'), false)
  assert.equal(briefingRunStatusLabel('done'), '草稿已生成 · 待审阅')
  assert.equal(briefingRunStatusLabel('unexpected'), '状态未知')
  assert.equal(createBriefingScheduleForm({ enabled: null }).enabled, false)

  assert.deepEqual(calculateBriefingStats([schedule]), {
    total: 1,
    enabled: 1,
    generated: null,
    running: false,
  })
  assert.equal(
    buildBriefingPeriodicTasks([schedule], (value) => `fmt:${value}`)[0].stats,
    '— · 1 · 1',
  )
})


test('briefing planned time labels future and overdue values as records, not execution proof', () => {
  const now = new Date('2026-08-09T04:00:00.000Z')
  assert.equal(briefingPlannedTimeLabel(null, String, now), '—')
  assert.equal(
    briefingPlannedTimeLabel(
      '2026-08-09T03:00:00.000Z',
      (value) => `fmt:${value}`,
      now,
    ),
    'fmt:2026-08-09T03:00:00.000Z · 已过计划时间，待调度器确认',
  )
  assert.equal(
    briefingPlannedTimeLabel(
      '2099-01-01T00:00:00.000Z',
      String,
      now,
    ),
    '时间状态不可用',
  )
  assert.equal(
    briefingRecordedTimeLabel('2099-01-01T00:00:00.000Z', String, now),
    '时间状态不可用',
  )
})


test('briefing service redacts backend and transport exception bodies', async () => {
  const nonOk = createBriefingScheduleService({
    async listSchedules() {
      return { ok: false, detail: 'database-url=secret-canary' }
    },
  })
  await assert.rejects(nonOk.list(), /^Error: 读取定时任务失败$/)

  const thrown = createBriefingScheduleService({
    async listSchedules() {
      throw new Error('Authorization: Bearer private-token')
    },
  })
  await assert.rejects(thrown.list(), /^Error: 读取定时任务失败$/)
})


test('briefing list loader is take-latest and supports invalidation', async () => {
  const pending = []
  const loader = createLatestBriefingScheduleLoader({
    list() {
      return new Promise((resolve) => pending.push(resolve))
    },
  })

  const older = loader.load()
  const newer = loader.load()
  pending[1]([{ id: 'newer' }])
  assert.deepEqual(await newer, {
    current: true,
    rows: [{ id: 'newer' }],
    error: null,
  })
  pending[0]([{ id: 'older' }])
  assert.deepEqual(await older, {
    current: false,
    rows: null,
    error: null,
  })

  const invalidated = loader.load()
  loader.invalidate()
  pending[2]([{ id: 'invalidated' }])
  assert.equal((await invalidated).current, false)
})


test('assistant schedule surface avoids unverified execution and raw error claims', async () => {
  const source = await readFile(
    new URL('../src/features/assistant/AssistantExperience.vue', import.meta.url),
    'utf8',
  )

  assert.doesNotMatch(source, /任务会在后端按到期时间执行/)
  assert.match(source, /实际执行依赖后台调度器健康状态/)
  assert.match(source, /计划记录/)
  assert.doesNotMatch(source, /selectedBriefingSchedule\.last_error\s*}}/)
  assert.match(source, /briefingRunStatusLabel\(run\.status\)/)
})
