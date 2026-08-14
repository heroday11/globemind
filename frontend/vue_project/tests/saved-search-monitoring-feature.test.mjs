import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  SAVED_SEARCH_MONITORING_SCHEMA,
  normalizeSavedSearchMonitoring,
} from '../src/features/research-workflow/index.js'

function fixture() {
  return {
    schema_version: SAVED_SEARCH_MONITORING_SCHEMA,
    project_id: 'project-01',
    evidence_scope: 'project_saved_search_records_only',
    read_side_effects: 'none',
    scheduler_state: 'not_configured',
    checkpoint_state: 'not_established',
    delta_semantics_state: 'not_established',
    new_only_state: 'not_available',
    notification_state: 'not_configured',
    items: [{
      saved_search_id: 'saved-01',
      query_contract_sha256: 'a'.repeat(64),
      linked_snapshot_state: 'verified',
      monitor_run_state: 'never_run',
      last_monitor_run_at: null,
      checkpoint_snapshot_id: null,
      delta_state: 'not_computable',
      added_result_count: null,
      new_only_available: false,
      notification_delivery_state: 'not_configured',
      reason_code: 'SCHEDULER_CHECKPOINT_AND_DELTA_NOT_CONFIGURED',
    }],
  }
}

test('saved-search monitoring accepts only the exact content-free unavailable contract', () => {
  const normalized = normalizeSavedSearchMonitoring(fixture(), 'project-01')
  assert.equal(normalized.contractState, 'contract_validated')
  assert.equal(normalized.itemCount, 1)
  assert.equal(normalized.schedulerState, 'not_configured')
  assert.equal(normalized.deltaState, 'not_established')
  assert.equal(normalized.newOnlyAvailable, false)
})

test('saved-search monitoring rejects coercion, contradictions, stale projects and extra data', () => {
  const candidates = [
    [{ ...fixture(), project_id: 'project-02' }, 'project-01'],
    [{ ...fixture(), scheduler_state: 'active' }, 'project-01'],
    [{ ...fixture(), items: [{ ...fixture().items[0], added_result_count: 0 }] }, 'project-01'],
    [{ ...fixture(), items: [{ ...fixture().items[0], new_only_available: 0 }] }, 'project-01'],
    [{ ...fixture(), items: [{ ...fixture().items[0], query: 'secret-canary' }] }, 'project-01'],
    [{ ...fixture(), items: [fixture().items[0], fixture().items[0]] }, 'project-01'],
  ]
  for (const [payload, projectId] of candidates) {
    const normalized = normalizeSavedSearchMonitoring(payload, projectId)
    assert.equal(normalized.contractState, 'invalid')
    assert.equal(normalized.itemCount, null)
    assert.equal(normalized.newOnlyAvailable, false)
  }
})

test('research workspace makes missing scheduler, delta and notifications explicit', async () => {
  const source = await readFile(
    new URL('../src/views/ResearchWorkspace.vue', import.meta.url),
    'utf8',
  )
  assert.match(source, /savedSearchMonitoring/)
  assert.match(source, /计划运行：未配置/)
  assert.match(source, /checkpoint：未建立/)
  assert.match(source, /仅看新增：不可用/)
  assert.match(source, /通知投递：未配置/)
  assert.doesNotMatch(source, /后台监测已启用/)
})
