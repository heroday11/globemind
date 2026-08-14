import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  FEATURE_FRESHNESS_ENDPOINT,
  normalizeFeatureFreshnessReport,
} from '../src/features/operations/index.js'

function degradationDisclosure(affectedCapabilityIds = []) {
  const actionRequired = affectedCapabilityIds.length > 0
  return {
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
  }
}

test('public status contract retains bounded SLO evidence without changing freshness semantics', () => {
  const report = normalizeFeatureFreshnessReport({
    schema_version: 'globemind.public-status.v1',
    generated_at: '2026-08-09T09:00:00Z',
    status: 'historical',
    research_mode: 'historical',
    ready: true,
    checks: {
      search: {
        feature_id: 'search',
        label: '新闻与事件检索',
        status: 'up',
        research_use: 'current',
        metrics: {
          freshness_status: 'live',
          latest_news_at: '2026-08-09T08:00:00Z',
          freshness_lag_hours: 1,
          freshness_sla_hours: 48,
        },
      },
      'ground-news': {
        feature_id: 'ground-news',
        label: '全球新闻观察',
        status: 'stale',
        research_use: 'historical',
        metrics: {
          freshness_status: 'stale',
          latest_story_source_at: '2026-08-08T08:00:00Z',
          freshness_lag_hours: 25,
          freshness_sla_hours: 24,
        },
      },
      'opinion-analysis': {
        feature_id: 'opinion-analysis',
        label: '涉华舆情分析',
        status: 'stale',
        research_use: 'historical',
        metrics: {
          freshness_status: 'stale',
          latest_score_date: '2026-08-08',
          freshness_lag_hours: 33,
          freshness_sla_hours: 24,
        },
      },
    },
    objectives: {
      freshness: [
        ['search', '新闻与事件检索', 48, 1, 'within'],
        ['ground-news', '全球新闻观察', 24, 25, 'exceeded'],
        ['opinion-analysis', '涉华舆情分析', 24, 33, 'exceeded'],
      ].map(([id, label, threshold, observed, assessment]) => ({
        id: `${id}-freshness`,
        label: `${label}数据新鲜度`,
        indicator: '最后有效数据距当前时间',
        measurement_status: 'active',
        objective: null,
        threshold: { comparison: 'less_than_or_equal', value: threshold, unit: 'hours' },
        observed: { value: observed, unit: 'hours' },
        threshold_assessment: assessment,
        compliance: 'not_computable',
        approval_state: 'not_approved',
        reason: '内部更新时限可用于降级；尚无经批准目标。',
        source: '运行时业务新鲜度探针',
      })),
      workflows: [
        {
          id: 'search-response',
          label: '检索响应',
          indicator: '端到端检索成功率与延迟',
          measurement_status: 'observed',
          objective: null,
          observed: {
            sample_count: 2,
            success_count: 1,
            error_count: 1,
            timeout_count: 0,
            cancelled_count: 0,
            success_rate: 0.5,
            error_rate: 0.5,
            p50_ms: 10,
            p95_ms: 20,
            p99_ms: 20,
          },
          compliance: 'not_computable',
          approval_state: 'not_approved',
          reason: '已有观测，但目标未批准。',
        },
        ...[
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
          reason: '尚无观测，且目标未批准。',
        })),
      ],
    },
    incident_history: {
      status: 'not_available',
      reason: '公开事件时间线尚未上线。',
    },
    degradation_disclosure: degradationDisclosure(),
  }, { receivedAt: new Date('2026-08-09T09:00:00Z') })

  assert.equal(FEATURE_FRESHNESS_ENDPOINT, '/api/status')
  assert.equal(report.schemaVersion, 'globemind.public-status.v1')
  assert.equal(report.researchMode, 'historical')
  assert.equal(report.features.search.label, '新闻与事件检索')
  assert.equal(report.features.search.researchUse, 'current')
  assert.equal(report.objectives.freshness[0].threshold_assessment, 'within')
  assert.equal(report.objectives.freshness[0].compliance, 'not_computable')
  assert.equal(report.objectives.freshness[0].objective, null)
  assert.equal(report.objectives.workflows[0].objective, null)
  assert.equal(report.objectives.workflows[0].observed.p95_ms, 20)
  assert.equal(report.incidentHistory.status, 'not_available')
  assert.equal(report.degradationDisclosure.status, 'monitoring')
  assert.deepEqual(report.degradationDisclosure.trigger.affectedCapabilityIds, [])
  assert.equal(report.degradationDisclosure.incidentOwner.availability, 'unavailable')
  assert.equal(report.degradationDisclosure.recoveryEstimate.value, null)
  assert.equal(report.degradationDisclosure.lastStatusUpdate.value, null)
})

test('status page exposes freshness, honest measurement gaps, retry, and governance links', async () => {
  const source = await readFile(new URL('../src/views/SystemStatus.vue', import.meta.url), 'utf8')

  assert.match(source, /<h1[^>]*id="system-status-title"[^>]*tabindex="-1"/)
  assert.match(source, /useFeatureFreshness\(\)/)
  assert.match(source, /refresh\(\{ force: true \}\)/)
  assert.match(source, /已有观测.*不是达标/)
  assert.match(source, /内部更新时限.*不是经批准 SLO/)
  assert.match(source, /不在公共接口披露/)
  assert.match(source, /incidentHistory/)
  assert.match(source, /事件负责人/)
  assert.match(source, /恢复预计/)
  assert.match(source, /最近状态更新/)
  assert.match(source, /value === null \|\| value === undefined/)
  assert.match(source, /to="\/methodology"/)
  assert.match(source, /to="\/sources"/)
  assert.match(source, /min-height: 44px/)
})

test('maintenance history distinguishes unavailable evidence from a verified empty ledger', () => {
  const base = {
    schema_version: 'globemind.public-status.v1',
    generated_at: '2026-08-09T19:30:00Z',
    status: 'unavailable',
    research_mode: 'historical',
    ready: false,
    checks: {
      search: {
        feature_id: 'search',
        label: '新闻与事件检索',
        status: 'down',
        research_use: 'unavailable',
        metrics: { freshness_status: 'offline', freshness_sla_hours: 48 },
      },
      'ground-news': {
        feature_id: 'ground-news',
        label: '全球新闻观察',
        status: 'down',
        research_use: 'unavailable',
        metrics: { freshness_status: 'offline', freshness_sla_hours: 48 },
      },
      'opinion-analysis': {
        feature_id: 'opinion-analysis',
        label: '涉华舆情分析',
        status: 'down',
        research_use: 'unavailable',
        metrics: { freshness_status: 'offline', freshness_sla_hours: 48 },
      },
    },
    objectives: {
      freshness: [
        ['search', '新闻与事件检索'],
        ['ground-news', '全球新闻观察'],
        ['opinion-analysis', '涉华舆情分析'],
      ].map(([id, label]) => ({
        id: `${id}-freshness`,
        label: `${label}数据新鲜度`,
        indicator: '最后有效数据距当前时间',
        measurement_status: 'partial',
        objective: null,
        threshold: { comparison: 'less_than_or_equal', value: 48, unit: 'hours' },
        observed: null,
        threshold_assessment: 'unknown',
        compliance: 'not_computable',
        approval_state: 'not_approved',
        reason: '内部更新时限可用于降级；尚无经批准目标。',
      })),
      workflows: [
        ['search-response', '检索响应', '端到端检索成功率与延迟'],
        ['export-delivery', '导出交付', '导出成功率与完成时间'],
        ['report-generation', '报告生成', '报告成功率与完成时间'],
      ].map(([id, label, indicator]) => ({
        id,
        label,
        indicator,
        measurement_status: 'unavailable',
        objective: null,
        observed: null,
        compliance: 'not_computable',
        approval_state: 'not_approved',
        reason: '尚无观测，且目标未批准。',
      })),
    },
    degradation_disclosure: degradationDisclosure([
      'search',
      'ground-news',
      'opinion-analysis',
    ]),
  }
  const governance = {
    retention: { status: 'not_approved', published_event_limit: 100 },
    subscription: { status: 'not_configured' },
    owner: { status: 'not_configured' },
    bounds: {
      max_source_bytes: 65536,
      max_events: 100,
      max_title_chars: 120,
      max_summary_chars: 500,
      max_affected_features: 20,
    },
  }
  const unconfigured = normalizeFeatureFreshnessReport({
    ...base,
    incident_history: {
      status: 'not_configured',
      freshness: 'unknown',
      generated_at: null,
      events: [],
      reason: '维护事件账本尚未配置；不能据此推断历史无事件。',
      ...governance,
    },
  }, { receivedAt: new Date('2026-08-09T19:30:00Z') })
  assert.equal(unconfigured.incidentHistory.status, 'not_configured')
  assert.deepEqual(unconfigured.incidentHistory.events, [])
  assert.equal(unconfigured.degradationDisclosure.status, 'action_required')
  assert.deepEqual(
    unconfigured.degradationDisclosure.trigger.affectedCapabilityIds,
    ['search', 'ground-news', 'opinion-analysis'],
  )
  assert.equal(unconfigured.degradationDisclosure.trigger.workflowBreachState, 'unknown')

  const availableHistory = {
    status: 'available',
    freshness: 'current',
    generated_at: '2026-08-09T19:00:00Z',
    events: [{
      id: 'maintenance-20260809',
      type: 'maintenance',
      status: 'completed',
      title: '公开检索索引维护',
      summary: '维护期间当前资料查询不可用，完成后恢复。',
      started_at: '2026-08-09T17:00:00Z',
      ended_at: '2026-08-09T17:30:00Z',
      affected_features: ['search'],
    }],
    reason: '维护事件账本已核验。',
    ...governance,
  }
  const available = normalizeFeatureFreshnessReport({
    ...base,
    incident_history: availableHistory,
  }, { receivedAt: new Date('2026-08-09T19:30:00Z') })
  assert.equal(available.incidentHistory.status, 'available')
  assert.equal(available.incidentHistory.events[0].id, 'maintenance-20260809')
  assert.equal(available.incidentHistory.subscription.status, 'not_configured')

  const future = structuredClone(base)
  future.incident_history = {
    status: 'available',
    freshness: 'current',
    generated_at: '2026-08-09T19:30:01Z',
    events: [],
    reason: '维护事件账本已核验。',
    ...governance,
  }
  assert.throws(
    () => normalizeFeatureFreshnessReport(
      future,
      { receivedAt: new Date('2026-08-09T19:30:00Z') },
    ),
    /事件历史时间或数量无效/,
  )

  const ambiguous = structuredClone(base)
  ambiguous.incident_history = {
    ...availableHistory,
    events: [{
      ...availableHistory.events[0],
      generatedAt: 'self-claimed',
    }],
  }
  assert.throws(
    () => normalizeFeatureFreshnessReport(
      ambiguous,
      { receivedAt: new Date('2026-08-09T19:30:00Z') },
    ),
    /维护事件契约无效/,
  )
})

test('status page renders maintenance events without presenting unknown governance as success', async () => {
  const source = await readFile(new URL('../src/views/SystemStatus.vue', import.meta.url), 'utf8')

  assert.match(source, /账本未配置/)
  assert.match(source, /账本不可核验/)
  assert.match(source, /这不等于历史无事件/)
  assert.match(source, /正式事件 owner/)
  assert.match(source, /保留策略/)
  assert.match(source, /状态订阅/)
  assert.match(source, /incidentHistory\.events/)
})

test('public status rejects service observations that overstate success or SLO approval', () => {
  const base = {
    schema_version: 'globemind.public-status.v1',
    generated_at: '2026-08-09T09:00:00Z',
    status: 'current',
    research_mode: 'current',
    ready: true,
    checks: {
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
          latest_score_date: '2026-08-09T08:00:00Z',
          freshness_lag_hours: 1,
          freshness_sla_hours: 48,
        },
      },
    },
    objectives: {
      freshness: [
        ['search', '新闻与事件检索', 48, 1],
        ['ground-news', '全球新闻观察', 48, 1],
        ['opinion-analysis', '涉华舆情分析', 48, 1],
      ].map(([id, label, threshold, observed]) => ({
        id: `${id}-freshness`,
        label: `${label}数据新鲜度`,
        indicator: '最后有效数据距当前时间',
        measurement_status: 'active',
        objective: null,
        threshold: { comparison: 'less_than_or_equal', value: threshold, unit: 'hours' },
        observed: { value: observed, unit: 'hours' },
        threshold_assessment: 'within',
        compliance: 'not_computable',
        approval_state: 'not_approved',
        reason: '内部更新时限可用于降级；尚无经批准目标。',
      })),
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
        reason: '尚无观测，且目标未批准。',
      })),
    },
    incident_history: {
      status: 'not_available',
      reason: '公开事件时间线尚未上线。',
    },
    degradation_disclosure: degradationDisclosure(),
  }
  const receivedAt = new Date('2026-08-09T09:00:00Z')
  const overstated = structuredClone(base)
  overstated.objectives.workflows[0] = {
    ...overstated.objectives.workflows[0],
    measurement_status: 'observed',
    observed: {
      sample_count: 2,
      success_count: 1,
      error_count: 1,
      timeout_count: 0,
      cancelled_count: 0,
      success_rate: 1,
      error_rate: 0,
      p50_ms: 10,
      p95_ms: 20,
      p99_ms: 20,
    },
  }
  assert.throws(
    () => normalizeFeatureFreshnessReport(overstated, { receivedAt }),
    /服务级观测契约矛盾/,
  )

  const approved = structuredClone(base)
  approved.objectives.workflows[0].approval_state = 'approved'
  assert.throws(
    () => normalizeFeatureFreshnessReport(approved, { receivedAt }),
    /服务级观测契约矛盾/,
  )

  const guessedRecovery = structuredClone(base)
  guessedRecovery.degradation_disclosure.recovery_estimate = {
    availability: 'available',
    value: guessedRecovery.generated_at,
  }
  assert.throws(
    () => normalizeFeatureFreshnessReport(guessedRecovery, { receivedAt }),
    /降级处置公开字段无效/,
  )

  const selfClaimedBreach = structuredClone(base)
  selfClaimedBreach.degradation_disclosure.trigger.workflow_breach_state = 'breached'
  selfClaimedBreach.degradation_disclosure.trigger.affected_workflow_ids = ['search-response']
  assert.throws(
    () => normalizeFeatureFreshnessReport(selfClaimedBreach, { receivedAt }),
    /降级处置公开契约矛盾/,
  )
})
