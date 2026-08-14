import type {
  AlertTriageAction,
  AlertTriageDetail,
  AlertTriageRedactedEvent,
  AlertTriageStatus,
  AlertTriageSummary,
  EscalationTargetRole,
  FalsePositiveClassification,
  FreshnessStatus,
  PostmortemOutcome,
  TrustStatus,
} from '../types'

const STATUSES = new Set<AlertTriageStatus>([
  'open',
  'acknowledged',
  'escalated',
  'false_positive',
  'resolved',
])
const ACTIONS = new Set<AlertTriageAction>([
  'acknowledge',
  'escalate',
  'mark_false_positive',
  'resolve',
  'postmortem',
])
const FALSE_POSITIVE_CLASSIFICATIONS = new Set<FalsePositiveClassification>([
  'data_quality',
  'duplicate_signal',
  'threshold_miscalibration',
  'known_activity',
  'insufficient_context',
])
const ESCALATION_TARGET_ROLES = new Set<EscalationTargetRole>([
  'financial_duty_officer',
  'data_quality_reviewer',
  'research_lead',
  'security_duty_officer',
])
const POSTMORTEM_OUTCOMES = new Set<PostmortemOutcome>([
  'confirmed_response',
  'process_improvement_identified',
  'no_follow_up_required',
])
const TRANSITION_TARGETS: Partial<Record<`${AlertTriageStatus}:${AlertTriageAction}`, AlertTriageStatus>> = {
  'open:acknowledge': 'acknowledged',
  'acknowledged:escalate': 'escalated',
  'acknowledged:mark_false_positive': 'false_positive',
  'acknowledged:resolve': 'resolved',
  'escalated:resolve': 'resolved',
}
const TRUST_STATUSES = new Set<TrustStatus>([
  'trusted',
  'limited',
  'unavailable',
  'mock',
])
const FRESHNESS_STATUSES = new Set<FreshnessStatus>([
  'live',
  'delayed',
  'stale',
  'offline',
  'mock',
])
const SHA256 = /^[0-9a-f]{64}$/
const EVENT_ID = /^fat-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$/
const ALERT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$/
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function nullableString(value: unknown, pattern?: RegExp): string | null | undefined {
  if (value === null) return null
  if (typeof value !== 'string' || (pattern && !pattern.test(value))) return undefined
  return value
}

function nullableTimestamp(value: unknown): string | null | undefined {
  const timestamp = nullableString(value, ISO_TIMESTAMP)
  if (typeof timestamp !== 'string') return timestamp
  return Number.isNaN(Date.parse(timestamp)) ? undefined : timestamp
}

function closedValue<T extends string>(value: unknown, values: Set<T>): T | undefined {
  return typeof value === 'string' && values.has(value as T) ? value as T : undefined
}

function closedNullable<T extends string>(
  value: unknown,
  values: Set<T>,
): T | null | undefined {
  if (value === null) return null
  return closedValue(value, values)
}

export function sanitizeAlertTriageSummary(value: unknown): AlertTriageSummary | null {
  const row = object(value)
  if (!row) return null
  const alertEventId = nullableString(row.alert_event_id, ALERT_ID)
  const status = closedValue(row.status, STATUSES)
  const lastAt = nullableTimestamp(row.last_transition_at)
  const lastId = nullableString(row.last_event_id, EVENT_ID)
  const lastHash = nullableString(row.last_event_sha256, SHA256)
  const limitations = object(row.operational_limitations)
  if (
    row.schema_version !== 'financial-alert-triage-status-v1'
    || typeof alertEventId !== 'string'
    || !status
    || typeof row.has_audit !== 'boolean'
    || typeof row.reviewed !== 'boolean'
    || typeof row.transition_count !== 'number'
    || !Number.isSafeInteger(row.transition_count)
    || Number(row.transition_count) < 0
    || Number(row.transition_count) > 8
    || lastAt === undefined
    || lastId === undefined
    || lastHash === undefined
    || !limitations
    || limitations.sla !== 'unavailable'
    || limitations.notification_delivery !== 'not_configured'
    || limitations.institutional_incident_system !== 'not_configured'
    || (row.historical !== undefined && typeof row.historical !== 'boolean')
    || (row.mutations_enabled !== undefined && typeof row.mutations_enabled !== 'boolean')
    || ((lastId === null) !== (lastHash === null))
    || ((lastId === null) !== (lastAt === null))
    || (row.has_audit && (lastId === null || Number(row.transition_count) < 1))
    || (!row.has_audit && (
      lastId !== null
      || Number(row.transition_count) !== 0
      || row.reviewed
      || status !== 'open'
    ))
    || (row.reviewed && status !== 'resolved' && status !== 'false_positive')
    || (
      typeof row.historical === 'boolean'
      && typeof row.mutations_enabled === 'boolean'
      && row.historical === row.mutations_enabled
    )
  ) return null

  return {
    schema_version: 'financial-alert-triage-status-v1',
    alert_event_id: alertEventId,
    status,
    has_audit: row.has_audit,
    reviewed: row.reviewed,
    transition_count: Number(row.transition_count),
    last_transition_at: lastAt,
    last_event_id: lastId,
    last_event_sha256: lastHash,
    ...(typeof row.historical === 'boolean' ? { historical: row.historical } : {}),
    ...(typeof row.mutations_enabled === 'boolean'
      ? { mutations_enabled: row.mutations_enabled }
      : {}),
    operational_limitations: {
      sla: 'unavailable',
      notification_delivery: 'not_configured',
      institutional_incident_system: 'not_configured',
    },
  }
}

function sanitizeRedactedEvent(value: unknown): AlertTriageRedactedEvent | null {
  const row = object(value)
  const reason = object(row?.reason)
  if (!row || !reason) return null
  const eventId = nullableString(row.event_id, EVENT_ID)
  const occurredAt = nullableTimestamp(row.occurred_at)
  const action = closedValue(row.action, ACTIONS)
  const fromStatus = closedValue(row.from_status, STATUSES)
  const toStatus = closedValue(row.to_status, STATUSES)
  const falsePositive = closedNullable(
    row.false_positive_classification,
    FALSE_POSITIVE_CLASSIFICATIONS,
  )
  const escalation = closedNullable(row.escalation_target_role, ESCALATION_TARGET_ROLES)
  const postmortem = closedNullable(row.postmortem_outcome, POSTMORTEM_OUTCOMES)
  const previousId = nullableString(row.previous_event_id, EVENT_ID)
  const previousHash = nullableString(row.previous_event_sha256, SHA256)
  const eventHash = nullableString(row.event_sha256, SHA256)
  const reasonHash = nullableString(reason.sha256, SHA256)
  const semanticsValid = Boolean(
    action
    && fromStatus
    && toStatus
    && (
      row.event_type === 'postmortem'
        ? action === 'postmortem'
          && (fromStatus === 'resolved' || fromStatus === 'false_positive')
          && toStatus === fromStatus
          && postmortem !== null
          && postmortem !== undefined
          && falsePositive === null
          && escalation === null
        : action !== 'postmortem'
          && TRANSITION_TARGETS[`${fromStatus}:${action}`] === toStatus
          && (action === 'mark_false_positive'
            ? falsePositive !== null && falsePositive !== undefined
            : falsePositive === null)
          && (action === 'escalate'
            ? escalation !== null && escalation !== undefined
            : escalation === null)
          && postmortem === null
    )
  )
  if (
    typeof eventId !== 'string'
    || typeof occurredAt !== 'string'
    || (row.event_type !== 'transition' && row.event_type !== 'postmortem')
    || !action
    || !fromStatus
    || !toStatus
    || typeof reasonHash !== 'string'
    || typeof reason.length !== 'number'
    || !Number.isSafeInteger(reason.length)
    || Number(reason.length) < 3
    || falsePositive === undefined
    || escalation === undefined
    || postmortem === undefined
    || previousId === undefined
    || previousHash === undefined
    || typeof eventHash !== 'string'
    || ((previousId === null) !== (previousHash === null))
    || !semanticsValid
  ) return null
  return {
    event_id: eventId,
    occurred_at: occurredAt,
    event_type: row.event_type,
    action,
    from_status: fromStatus,
    to_status: toStatus,
    reason: { sha256: reasonHash, length: reason.length },
    false_positive_classification: falsePositive,
    escalation_target_role: escalation,
    postmortem_outcome: postmortem,
    previous_event_id: previousId,
    previous_event_sha256: previousHash,
    event_sha256: eventHash,
  }
}

export function sanitizeAlertTriageDetail(value: unknown): AlertTriageDetail {
  const row = object(value)
  const summary = sanitizeAlertTriageSummary(row)
  const historyHash = nullableString(row?.alert_history_sha256, SHA256)
  const trustStatus = closedValue(row?.trust_status, TRUST_STATUSES)
  const freshnessStatus = closedValue(row?.freshness_status, FRESHNESS_STATUSES)
  const snapshotId = nullableString(row?.snapshot_id)
  if (
    !row
    || !summary
    || typeof historyHash !== 'string'
    || !Array.isArray(row.audit)
    || row.audit.length > 8
    || typeof row.historical !== 'boolean'
    || typeof row.mutations_enabled !== 'boolean'
    || !trustStatus
    || !freshnessStatus
    || typeof snapshotId !== 'string'
  ) throw new Error('Invalid financial alert triage response')
  const audit = row.audit.map(sanitizeRedactedEvent)
  if (audit.some((event) => event === null)) {
    throw new Error('Invalid financial alert triage audit response')
  }
  return {
    ...summary,
    historical: row.historical,
    mutations_enabled: row.mutations_enabled,
    alert_history_sha256: historyHash,
    audit: audit as AlertTriageRedactedEvent[],
    trust_status: trustStatus,
    freshness_status: freshnessStatus,
    snapshot_id: snapshotId,
  }
}
