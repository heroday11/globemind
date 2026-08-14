import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const login = await readFile(new URL('../src/views/Login.vue', import.meta.url), 'utf8')
const loginModal = await readFile(
  new URL('../src/components/LoginModal.vue', import.meta.url),
  'utf8',
)
const personalCenter = await readFile(
  new URL('../src/views/user/PersonalCenter.vue', import.meta.url),
  'utf8',
)

for (const [name, source] of [
  ['login page', login],
  ['login modal', loginModal],
]) {
  test(`${name} waits for MFA challenge completion before storing a token`, () => {
    assert.match(source, /\/auth\/login\/mfa/)
    assert.match(source, /data\.mfa_required === true/)
    assert.match(source, /!data\.access_token/)
    assert.match(source, /one-time-code/)
    assert.match(source, /恢复码/)
    assert.match(source, /mfaField\.value\?\.focus\(\)/)
    assert.match(source, /setToken\(data\.access_token\)/)
  })
}

test('personal center exposes MFA enrollment, one-time recovery and disable proofs', () => {
  assert.match(personalCenter, /user\/security\/mfa\/enroll/)
  assert.match(personalCenter, /user\/security\/mfa\/confirm/)
  assert.match(personalCenter, /user\/security\/mfa\/disable/)
  assert.match(personalCenter, /恢复码仅显示这一次/)
  assert.match(personalCenter, /页面关闭或刷新后无法重新读取/)
  assert.match(personalCenter, /password: disableForm\.password, code: disableForm\.code/)
  assert.match(personalCenter, /recovery_code/)
  assert.match(personalCenter, /autocomplete="current-password"/)
  assert.match(personalCenter, /autocomplete="one-time-code"/)
})

test('personal center lists and revokes only tracked sessions with honest assurance labels', () => {
  assert.match(personalCenter, /user\/security\/sessions/)
  assert.match(personalCenter, /sessions\/revoke-others/)
  assert.match(personalCenter, /current_session_revoked/)
  assert.match(personalCenter, /旧 untracked token\s+不冒充为可撤销会话/)
  assert.match(personalCenter, /last_seen/)
  assert.match(personalCenter, /user\/security\/audit/)
  assert.match(personalCenter, /机构 SSO、设备认证或独立安全验收/)
})

test('security controls retain accessible announcements, labels, focus and touch targets', () => {
  assert.match(personalCenter, /aria-live="polite"/)
  assert.match(personalCenter, /securityNoticeElement\.value\?\.focus\(\)/)
  assert.match(personalCenter, /for="mfa-confirm-code"/)
  assert.match(personalCenter, /for="mfa-disable-password"/)
  assert.match(personalCenter, /\.pc-security-button \{[\s\S]*min-height: 44px/)
})
