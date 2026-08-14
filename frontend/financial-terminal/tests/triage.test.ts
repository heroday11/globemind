import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  fetchAlertTriageDetail,
  submitAdminAlertTriageEvent,
} from '../src/api/triage.ts'
import {
  sanitizeAlertTriageDetail,
  sanitizeAlertTriageSummary,
} from '../src/lib/triage.ts'


const HASH = 'a'.repeat(64)
const EVENT_ID = 'fat-20260809T080000000000Z-0000000000000001'
const ALERT_ID = 'fin-alert-risk-20260809080000'

function summary(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 'financial-alert-triage-status-v1',
    alert_event_id: ALERT_ID,
    status: 'acknowledged',
    has_audit: true,
    reviewed: false,
    transition_count: 1,
    last_transition_at: '2026-08-09T08:00:00Z',
    last_event_id: EVENT_ID,
    last_event_sha256: HASH,
    historical: false,
    mutations_enabled: true,
    operational_limitations: {
      sla: 'unavailable',
      notification_delivery: 'not_configured',
      institutional_incident_system: 'not_configured',
    },
    ...overrides,
  }
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...summary(),
    alert_history_sha256: HASH,
    trust_status: 'trusted',
    freshness_status: 'live',
    snapshot_id: 'fin-test',
    audit: [{
      event_id: EVENT_ID,
      occurred_at: '2026-08-09T08:00:00Z',
      event_type: 'transition',
      action: 'acknowledge',
      from_status: 'open',
      to_status: 'acknowledged',
      reason: { sha256: HASH, length: 12 },
      false_positive_classification: null,
      escalation_target_role: null,
      postmortem_outcome: null,
      previous_event_id: null,
      previous_event_sha256: null,
      event_sha256: HASH,
    }],
    ...overrides,
  }
}

test('public triage summary is allow-listed and drops sensitive injected fields', () => {
  const result = sanitizeAlertTriageSummary(summary({
    actor_user_id: 42,
    reason: 'must not reach the terminal',
  }))

  assert.equal(result?.status, 'acknowledged')
  assert.equal(result?.operational_limitations.sla, 'unavailable')
  assert.equal('actor_user_id' in (result || {}), false)
  assert.equal('reason' in (result || {}), false)
})

test('redacted lifecycle detail accepts hashes but no actor or reason body', () => {
  const result = sanitizeAlertTriageDetail(detail())
  const encoded = JSON.stringify(result)

  assert.equal(result.audit[0].reason.sha256, HASH)
  assert.equal(result.audit[0].reason.length, 12)
  assert.doesNotMatch(encoded, /actor_user_id/)
  assert.doesNotMatch(encoded, /reason body/)
})

test('detail sanitizer rejects unknown states, partial hashes and oversized audits atomically', () => {
  assert.throws(
    () => sanitizeAlertTriageDetail(detail({ status: 'silently_closed' })),
    /Invalid financial alert triage response/,
  )
  assert.throws(
    () => sanitizeAlertTriageDetail(detail({ last_event_sha256: null })),
    /Invalid financial alert triage response/,
  )
  assert.throws(
    () => sanitizeAlertTriageDetail(detail({ audit: Array(9).fill(detail().audit[0]) })),
    /Invalid financial alert triage response/,
  )
  assert.throws(
    () => sanitizeAlertTriageDetail(detail({
      audit: [{ ...detail().audit[0], action: 'resolve', to_status: 'resolved' }],
    })),
    /Invalid financial alert triage audit response/,
  )
})

test('triage API uses bearer auth, encoded IDs and an exact mutation body', async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: (key: string) => key === 'access_token' ? 'token-value' : null },
  })
  globalThis.fetch = async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(input), init })
    const body = init?.method === 'POST'
      ? { ...detail(), audit: [{ event_id: EVENT_ID, alert_event_id: ALERT_ID, actor_user_id: 9, reason: 'Reviewed', record_sha256: HASH }] }
      : detail()
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  try {
    const fetched = await fetchAlertTriageDetail(ALERT_ID)
    assert.equal(fetched.status, 'acknowledged')
    await submitAdminAlertTriageEvent(ALERT_ID, {
      action: 'resolve',
      reason: 'Evidence review completed.',
      expected_previous_event_id: EVENT_ID,
      expected_previous_event_sha256: HASH,
    })
  } finally {
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
  }

  assert.equal(requests.length, 2)
  assert.match(requests[0].url, /\/api\/financial\/alert\/triage\/fin-alert-risk-/)
  assert.equal((requests[0].init?.headers as Record<string, string>).Authorization, 'Bearer token-value')
  assert.equal(requests[1].init?.method, 'POST')
  assert.deepEqual(JSON.parse(String(requests[1].init?.body)), {
    action: 'resolve',
    reason: 'Evidence review completed.',
    expected_previous_event_id: EVENT_ID,
    expected_previous_event_sha256: HASH,
  })
})

test('triage transport never exposes an upstream error body', async () => {
  const originalFetch = globalThis.fetch
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null },
  })
  globalThis.fetch = async () => new Response(
    'database_url=postgresql' + '://user:secret@db token=should-not-leak',
    { status: 503 },
  )
  try {
    await assert.rejects(fetchAlertTriageDetail(ALERT_ID), (error: unknown) => {
      assert.ok(error instanceof Error)
      assert.doesNotMatch(error.message, /database|postgres|secret|token/i)
      assert.match(error.message, /503/)
      return true
    })
  } finally {
    globalThis.fetch = originalFetch
    if (originalStorage) Object.defineProperty(globalThis, 'localStorage', originalStorage)
    else Reflect.deleteProperty(globalThis, 'localStorage')
  }
})

test('dashboard and triage transports share the content-free error boundary', async () => {
  const dashboardApi = await readFile(
    new URL('../src/api/index.ts', import.meta.url),
    'utf8',
  )
  const triageApi = await readFile(
    new URL('../src/api/triage.ts', import.meta.url),
    'utf8',
  )
  for (const source of [dashboardApi, triageApi]) {
    assert.match(source, /safeFinancialApiError/)
    assert.doesNotMatch(source, /\.text\(\)/)
  }
})

test('terminal presents read-only lifecycle truth without guessing administrator role', async () => {
  const page = await readFile(
    new URL('../src/pages/NumericalAnalysisAlert.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /页面仅展示隐私最小化处置聚合/)
  assert.match(page, /自动 SLA、外部通知与机构事件系统尚未接入/)
  assert.match(page, /从视图隐藏/)
  assert.match(page, /不会改变处置状态/)
  assert.match(page, /min-h-11/)
  assert.doesNotMatch(page, /submitAdminAlertTriageEvent/)
  assert.doesNotMatch(page, /系统触发高危告警.*菲律宾船只/)
})
