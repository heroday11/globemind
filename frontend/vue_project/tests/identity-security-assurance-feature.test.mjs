import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  formatRevokedSessionCount,
  normalizeIdentitySecuritySnapshot,
  securityFailureMessage,
} from '../src/governance/identitySecurity.js'

function validStatus() {
  return {
    schema_version: 'identity-mfa-status-v1',
    status: 'disabled',
    enabled: false,
    pending_enrollment: false,
    pending_expires_at: null,
    pending_attempts_remaining: null,
    recovery_codes_remaining: 0,
    assurance: {
      type: 'totp-rfc6238',
      enrollment_state: 'available',
      institutional_sso: 'unavailable',
      device_attestation: 'unavailable',
      independent_security_review: 'unavailable',
    },
    capabilities: {
      totp_enrollment: 'available',
      recovery_codes: 'available',
      tracked_sessions: 'available',
    },
    storage: {
      status: 'available',
      backend: 'append-only-filesystem',
      writes_on_read: false,
      last_seen: 'unavailable',
    },
    capability_inventory: {
      schema_version: 'identity-security-capabilities-v1',
      evidence_scope: 'repository_source_and_local_ledger_only',
      totp: 'available',
      recovery_codes: 'available',
      tracked_web_sessions: 'available',
      institutional_sso: 'not_configured',
      security_keys: 'not_configured',
      trusted_devices: 'not_configured',
      device_attestation: 'not_configured',
      runtime_idp_attestation: 'not_available',
      independent_security_review: 'not_provided',
    },
  }
}

function validSessions() {
  return {
    schema_version: 'identity-session-list-v1',
    tracking: 'login-issued-tokens-only',
    untracked_tokens: 'not_listed_and_not_claimed_revocable',
    items: [{
      session_id: 'a'.repeat(64),
      issued_at: '2026-08-09T20:00:00Z',
      expires_at: '2026-08-10T20:00:00Z',
      status: 'active',
      current: true,
      last_seen_at: null,
      last_seen_status: 'unavailable',
    }],
  }
}

function validAudit() {
  return {
    schema_version: 'identity-security-audit-v1',
    events: [{
      event_id: 'b'.repeat(32),
      sequence: 1,
      timestamp: '2026-08-09T20:00:00Z',
      action: 'session.issued',
      reason_sha256: 'c'.repeat(64),
      reason_length: 16,
      changed_fields: ['sessions[]'],
    }],
    redaction: {
      token: 'never_stored',
      totp_secret: 'not_in_audit',
      recovery_code: 'never_stored',
      reason: 'sha256_and_length_only',
      body_fields: 'none',
    },
  }
}

test('identity security snapshot accepts only the exact bounded assurance contracts', () => {
  const snapshot = normalizeIdentitySecuritySnapshot(
    validStatus(),
    validSessions(),
    validAudit(),
  )
  assert.equal(snapshot.contractState, 'contract_validated')
  assert.equal(snapshot.status.enabled, false)
  assert.equal(snapshot.sessions.length, 1)
  assert.equal(snapshot.audit.length, 1)
  assert.equal(snapshot.capabilities.institutionalSso, 'not_configured')
  assert.equal(snapshot.capabilities.securityKeys, 'not_configured')
  assert.equal(snapshot.capabilities.runtimeIdpAttestation, 'not_available')
})

test('unknown, coerced, extra-key, and contradictory security values fail closed', () => {
  const candidates = [
    [validStatus(), { ...validSessions(), items: [{ ...validSessions().items[0], status: 'trusted' }] }, validAudit()],
    [{ ...validStatus(), enabled: 0 }, validSessions(), validAudit()],
    [{ ...validStatus(), capability_inventory: { ...validStatus().capability_inventory, institutional_sso: 'enabled' } }, validSessions(), validAudit()],
    [{ ...validStatus(), capability_inventory: { ...validStatus().capability_inventory, totp: 'unavailable' } }, validSessions(), validAudit()],
    [validStatus(), validSessions(), { ...validAudit(), events: [{ ...validAudit().events[0], reason_length: true }] }],
    [{ ...validStatus(), unexpected: 'secret' }, validSessions(), validAudit()],
  ]
  for (const values of candidates) {
    const snapshot = normalizeIdentitySecuritySnapshot(...values)
    assert.equal(snapshot.contractState, 'invalid')
    assert.equal(snapshot.status.enabled, null)
    assert.deepEqual(snapshot.sessions, [])
    assert.deepEqual(snapshot.audit, [])
  }
})

test('security errors and unknown revoke counts never echo provider details or fabricate zero', () => {
  assert.equal(
    securityFailureMessage({ detail: 'postgres' + '://user:secret@example.test/db' }),
    '身份安全操作失败',
  )
  assert.equal(
    securityFailureMessage({ detail: { code: 'CURRENT_SESSION_UNTRACKED' } }),
    '当前会话不可管理，请重新登录后重试',
  )
  assert.equal(formatRevokedSessionCount(null), '撤销数量未知')
  assert.equal(formatRevokedSessionCount(0), '已撤销 0 个其他活动会话')
  assert.equal(formatRevokedSessionCount('0'), '撤销数量未知')
})

test('personal center consumes the strict projection and displays unconfigured capabilities', async () => {
  const source = await readFile(
    new URL('../src/views/user/PersonalCenter.vue', import.meta.url),
    'utf8',
  )
  assert.match(source, /normalizeIdentitySecuritySnapshot/)
  assert.match(source, /securityFailureMessage/)
  assert.match(source, /formatRevokedSessionCount/)
  assert.match(source, /机构 SSO：未配置/)
  assert.match(source, /安全密钥：未配置/)
  assert.match(source, /运行 IdP 证明：不可用/)
  assert.doesNotMatch(source, /result\.revoked_count \|\| 0/)
})
