export const IDENTITY_SECURITY_CAPABILITY_VERSION = 'identity-security-capabilities-v1'

const STATUS_KEYS = Object.freeze([
  'schema_version',
  'status',
  'enabled',
  'pending_enrollment',
  'pending_expires_at',
  'pending_attempts_remaining',
  'recovery_codes_remaining',
  'assurance',
  'capabilities',
  'storage',
  'capability_inventory',
])
const ASSURANCE_KEYS = Object.freeze([
  'type',
  'enrollment_state',
  'institutional_sso',
  'device_attestation',
  'independent_security_review',
])
const LEGACY_CAPABILITY_KEYS = Object.freeze([
  'totp_enrollment',
  'recovery_codes',
  'tracked_sessions',
])
const STORAGE_KEYS = Object.freeze([
  'status',
  'backend',
  'writes_on_read',
  'last_seen',
])
const INVENTORY_KEYS = Object.freeze([
  'schema_version',
  'evidence_scope',
  'totp',
  'recovery_codes',
  'tracked_web_sessions',
  'institutional_sso',
  'security_keys',
  'trusted_devices',
  'device_attestation',
  'runtime_idp_attestation',
  'independent_security_review',
])
const SESSION_LIST_KEYS = Object.freeze([
  'schema_version',
  'tracking',
  'untracked_tokens',
  'items',
])
const SESSION_KEYS = Object.freeze([
  'session_id',
  'issued_at',
  'expires_at',
  'status',
  'current',
  'last_seen_at',
  'last_seen_status',
])
const AUDIT_KEYS = Object.freeze(['schema_version', 'events', 'redaction'])
const AUDIT_EVENT_KEYS = Object.freeze([
  'event_id',
  'sequence',
  'timestamp',
  'action',
  'reason_sha256',
  'reason_length',
  'changed_fields',
])
const REDACTION_KEYS = Object.freeze([
  'token',
  'totp_secret',
  'recovery_code',
  'reason',
  'body_fields',
])
const ACTIONS = new Set([
  'mfa.enrollment_started',
  'mfa.confirm_failed',
  'mfa.enabled',
  'mfa.disabled',
  'login.challenge_created',
  'login.challenge_failed',
  'login.completed',
  'session.issued',
  'session.revoked',
  'sessions.revoked',
])
const HEX_32 = /^[0-9a-f]{32}$/
const HEX_64 = /^[0-9a-f]{64}$/

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index])
}

function canonicalTimestamp(value) {
  return typeof value === 'string'
    && value.endsWith('Z')
    && Number.isFinite(Date.parse(value))
}

function invalidSnapshot() {
  return {
    contractState: 'invalid',
    status: {
      status: 'unknown',
      enabled: null,
      pendingEnrollment: null,
      pendingExpiresAt: null,
      pendingAttemptsRemaining: null,
      recoveryCodesRemaining: null,
    },
    capabilities: {
      totp: 'unknown',
      recoveryCodes: 'unknown',
      trackedWebSessions: 'unknown',
      institutionalSso: 'unknown',
      securityKeys: 'unknown',
      trustedDevices: 'unknown',
      deviceAttestation: 'unknown',
      runtimeIdpAttestation: 'unknown',
      independentSecurityReview: 'unknown',
    },
    sessions: [],
    audit: [],
  }
}

function normalizeStatus(value) {
  if (
    !exactKeys(value, STATUS_KEYS)
    || value.schema_version !== 'identity-mfa-status-v1'
    || !['disabled', 'pending', 'enabled'].includes(value.status)
    || typeof value.enabled !== 'boolean'
    || typeof value.pending_enrollment !== 'boolean'
    || value.enabled !== (value.status === 'enabled')
    || value.pending_enrollment !== (value.status === 'pending')
    || !Number.isSafeInteger(value.recovery_codes_remaining)
    || value.recovery_codes_remaining < 0
    || value.recovery_codes_remaining > 10
    || !exactKeys(value.assurance, ASSURANCE_KEYS)
    || value.assurance.type !== 'totp-rfc6238'
    || !['available', 'unavailable'].includes(value.assurance.enrollment_state)
    || value.assurance.institutional_sso !== 'unavailable'
    || value.assurance.device_attestation !== 'unavailable'
    || value.assurance.independent_security_review !== 'unavailable'
    || !exactKeys(value.capabilities, LEGACY_CAPABILITY_KEYS)
    || !['available', 'unavailable'].includes(value.capabilities.totp_enrollment)
    || !['available', 'unavailable'].includes(value.capabilities.recovery_codes)
    || value.capabilities.tracked_sessions !== 'available'
    || !exactKeys(value.storage, STORAGE_KEYS)
    || value.storage.status !== 'available'
    || value.storage.backend !== 'append-only-filesystem'
    || value.storage.writes_on_read !== false
    || value.storage.last_seen !== 'unavailable'
    || !exactKeys(value.capability_inventory, INVENTORY_KEYS)
  ) return null
  const inventory = value.capability_inventory
  if (
    inventory.schema_version !== IDENTITY_SECURITY_CAPABILITY_VERSION
    || inventory.evidence_scope !== 'repository_source_and_local_ledger_only'
    || !['available', 'unavailable'].includes(inventory.totp)
    || !['available', 'unavailable'].includes(inventory.recovery_codes)
    || inventory.totp !== value.capabilities.totp_enrollment
    || inventory.totp !== value.assurance.enrollment_state
    || inventory.recovery_codes !== value.capabilities.recovery_codes
    || inventory.tracked_web_sessions !== 'available'
    || inventory.institutional_sso !== 'not_configured'
    || inventory.security_keys !== 'not_configured'
    || inventory.trusted_devices !== 'not_configured'
    || inventory.device_attestation !== 'not_configured'
    || inventory.runtime_idp_attestation !== 'not_available'
    || inventory.independent_security_review !== 'not_provided'
  ) return null
  if (value.status === 'pending') {
    if (
      !canonicalTimestamp(value.pending_expires_at)
      || !Number.isSafeInteger(value.pending_attempts_remaining)
      || value.pending_attempts_remaining < 0
      || value.pending_attempts_remaining > 5
    ) return null
  } else if (
    value.pending_expires_at !== null
    || value.pending_attempts_remaining !== null
  ) return null
  return {
    status: value.status,
    enabled: value.enabled,
    pendingEnrollment: value.pending_enrollment,
    pendingExpiresAt: value.pending_expires_at,
    pendingAttemptsRemaining: value.pending_attempts_remaining,
    recoveryCodesRemaining: value.recovery_codes_remaining,
    capabilities: {
      totp: inventory.totp,
      recoveryCodes: inventory.recovery_codes,
      trackedWebSessions: inventory.tracked_web_sessions,
      institutionalSso: inventory.institutional_sso,
      securityKeys: inventory.security_keys,
      trustedDevices: inventory.trusted_devices,
      deviceAttestation: inventory.device_attestation,
      runtimeIdpAttestation: inventory.runtime_idp_attestation,
      independentSecurityReview: inventory.independent_security_review,
    },
  }
}

function normalizeSessions(value) {
  if (
    !exactKeys(value, SESSION_LIST_KEYS)
    || value.schema_version !== 'identity-session-list-v1'
    || value.tracking !== 'login-issued-tokens-only'
    || value.untracked_tokens !== 'not_listed_and_not_claimed_revocable'
    || !Array.isArray(value.items)
    || value.items.length > 10_000
  ) return null
  const seen = new Set()
  const items = []
  let currentCount = 0
  for (const item of value.items) {
    if (
      !exactKeys(item, SESSION_KEYS)
      || typeof item.session_id !== 'string'
      || !HEX_64.test(item.session_id)
      || seen.has(item.session_id)
      || !canonicalTimestamp(item.issued_at)
      || !canonicalTimestamp(item.expires_at)
      || Date.parse(item.issued_at) >= Date.parse(item.expires_at)
      || !['active', 'revoked', 'expired', 'password_changed'].includes(item.status)
      || typeof item.current !== 'boolean'
      || item.last_seen_at !== null
      || item.last_seen_status !== 'unavailable'
    ) return null
    seen.add(item.session_id)
    currentCount += item.current ? 1 : 0
    items.push({
      sessionId: item.session_id,
      issuedAt: item.issued_at,
      expiresAt: item.expires_at,
      status: item.status,
      current: item.current,
      lastSeenAt: null,
      lastSeenStatus: 'unavailable',
    })
  }
  if (currentCount !== 1) return null
  return items
}

function normalizeAudit(value) {
  if (
    !exactKeys(value, AUDIT_KEYS)
    || value.schema_version !== 'identity-security-audit-v1'
    || !Array.isArray(value.events)
    || value.events.length > 200
    || !exactKeys(value.redaction, REDACTION_KEYS)
    || value.redaction.token !== 'never_stored'
    || value.redaction.totp_secret !== 'not_in_audit'
    || value.redaction.recovery_code !== 'never_stored'
    || value.redaction.reason !== 'sha256_and_length_only'
    || value.redaction.body_fields !== 'none'
  ) return null
  const events = []
  const seen = new Set()
  for (const event of value.events) {
    if (
      !exactKeys(event, AUDIT_EVENT_KEYS)
      || typeof event.event_id !== 'string'
      || !HEX_32.test(event.event_id)
      || seen.has(event.event_id)
      || !Number.isSafeInteger(event.sequence)
      || event.sequence < 1
      || !canonicalTimestamp(event.timestamp)
      || !ACTIONS.has(event.action)
      || typeof event.reason_sha256 !== 'string'
      || !HEX_64.test(event.reason_sha256)
      || !Number.isSafeInteger(event.reason_length)
      || event.reason_length < 0
      || event.reason_length > 500
      || !Array.isArray(event.changed_fields)
      || event.changed_fields.length > 16
      || event.changed_fields.some((field) => typeof field !== 'string' || field.length > 80)
    ) return null
    seen.add(event.event_id)
    events.push({
      eventId: event.event_id,
      sequence: event.sequence,
      timestamp: event.timestamp,
      action: event.action,
      reasonSha256: event.reason_sha256,
      reasonLength: event.reason_length,
      changedFields: [...event.changed_fields],
    })
  }
  return events
}

export function normalizeIdentitySecuritySnapshot(status, sessions, audit) {
  const normalizedStatus = normalizeStatus(status)
  const normalizedSessions = normalizeSessions(sessions)
  const normalizedAudit = normalizeAudit(audit)
  if (!normalizedStatus || !normalizedSessions || !normalizedAudit) {
    return invalidSnapshot()
  }
  return {
    contractState: 'contract_validated',
    status: {
      status: normalizedStatus.status,
      enabled: normalizedStatus.enabled,
      pendingEnrollment: normalizedStatus.pendingEnrollment,
      pendingExpiresAt: normalizedStatus.pendingExpiresAt,
      pendingAttemptsRemaining: normalizedStatus.pendingAttemptsRemaining,
      recoveryCodesRemaining: normalizedStatus.recoveryCodesRemaining,
    },
    capabilities: normalizedStatus.capabilities,
    sessions: normalizedSessions,
    audit: normalizedAudit,
  }
}

export function securityFailureMessage(payload) {
  const code = payload?.detail && typeof payload.detail === 'object'
    ? payload.detail.code
    : null
  if (code === 'CURRENT_SESSION_UNTRACKED') {
    return '当前会话不可管理，请重新登录后重试'
  }
  return '身份安全操作失败'
}

export function formatRevokedSessionCount(value) {
  if (!Number.isSafeInteger(value) || value < 0 || value > 10_000) {
    return '撤销数量未知'
  }
  return `已撤销 ${value} 个其他活动会话`
}
