export const SAVED_SEARCH_MONITORING_SCHEMA = 'research-saved-search-monitoring-v1'

const MAX_ITEMS = 200
const OUTER_KEYS = Object.freeze([
  'schema_version',
  'project_id',
  'evidence_scope',
  'read_side_effects',
  'scheduler_state',
  'checkpoint_state',
  'delta_semantics_state',
  'new_only_state',
  'notification_state',
  'items',
])
const ITEM_KEYS = Object.freeze([
  'saved_search_id',
  'query_contract_sha256',
  'linked_snapshot_state',
  'monitor_run_state',
  'last_monitor_run_at',
  'checkpoint_snapshot_id',
  'delta_state',
  'added_result_count',
  'new_only_available',
  'notification_delivery_state',
  'reason_code',
])
const SAFE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/
const SHA256_RE = /^[0-9a-f]{64}$/

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index])
}

function invalidMonitoring() {
  return Object.freeze({
    contractState: 'invalid',
    itemCount: null,
    schedulerState: 'unknown',
    checkpointState: 'unknown',
    deltaState: 'unknown',
    newOnlyAvailable: false,
    notificationState: 'unknown',
    items: Object.freeze([]),
  })
}

export function normalizeSavedSearchMonitoring(payload, expectedProjectId) {
  if (
    !exactKeys(payload, OUTER_KEYS)
    || payload.schema_version !== SAVED_SEARCH_MONITORING_SCHEMA
    || typeof expectedProjectId !== 'string'
    || payload.project_id !== expectedProjectId
    || !SAFE_ID_RE.test(payload.project_id)
    || payload.evidence_scope !== 'project_saved_search_records_only'
    || payload.read_side_effects !== 'none'
    || payload.scheduler_state !== 'not_configured'
    || payload.checkpoint_state !== 'not_established'
    || payload.delta_semantics_state !== 'not_established'
    || payload.new_only_state !== 'not_available'
    || payload.notification_state !== 'not_configured'
    || !Array.isArray(payload.items)
    || payload.items.length > MAX_ITEMS
  ) return invalidMonitoring()

  const seen = new Set()
  const items = []
  for (const item of payload.items) {
    if (
      !exactKeys(item, ITEM_KEYS)
      || typeof item.saved_search_id !== 'string'
      || !SAFE_ID_RE.test(item.saved_search_id)
      || seen.has(item.saved_search_id)
      || typeof item.query_contract_sha256 !== 'string'
      || !SHA256_RE.test(item.query_contract_sha256)
      || !['verified', 'unavailable'].includes(item.linked_snapshot_state)
      || item.monitor_run_state !== 'never_run'
      || item.last_monitor_run_at !== null
      || item.checkpoint_snapshot_id !== null
      || item.delta_state !== 'not_computable'
      || item.added_result_count !== null
      || item.new_only_available !== false
      || item.notification_delivery_state !== 'not_configured'
      || item.reason_code !== 'SCHEDULER_CHECKPOINT_AND_DELTA_NOT_CONFIGURED'
    ) return invalidMonitoring()
    seen.add(item.saved_search_id)
    items.push(Object.freeze({
      savedSearchId: item.saved_search_id,
      queryContractSha256: item.query_contract_sha256,
      linkedSnapshotState: item.linked_snapshot_state,
    }))
  }
  return Object.freeze({
    contractState: 'contract_validated',
    itemCount: items.length,
    schedulerState: payload.scheduler_state,
    checkpointState: payload.checkpoint_state,
    deltaState: payload.delta_semantics_state,
    newOnlyAvailable: false,
    notificationState: payload.notification_state,
    items: Object.freeze(items),
  })
}
