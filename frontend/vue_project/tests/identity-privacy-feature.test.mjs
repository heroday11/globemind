import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'

const personalCenter = await readFile(
  new URL('../src/views/user/PersonalCenter.vue', import.meta.url),
  'utf8',
)
const register = await readFile(new URL('../src/views/Register.vue', import.meta.url), 'utf8')

test('registration minimizes optional name and phone collection', () => {
  assert.match(register, /姓名（可选）/)
  assert.match(register, /手机号（可选）/)
  assert.doesNotMatch(register, /id="register-full-name"[^>]*required/)
  assert.doesNotMatch(register, /id="register-phone"[^>]*required/)
  assert.match(register, /创建账号仅要求用户名、邮箱和密码/)
})

test('personal center exposes authenticated export with honest unavailable scopes', () => {
  assert.match(personalCenter, /user\/privacy\/export/)
  assert.match(personalCenter, /工作区文件元数据、文件哈希与下载路径/)
  assert.match(personalCenter, /当前项目权限下的项目元数据与本人提交内容/)
  assert.match(personalCenter, /工作区或报告正文、敏感任务上下文、其他项目成员私密字段/)
  assert.match(personalCenter, /data\.complete/)
  assert.match(personalCenter, /未导出或截断的范围/)
  assert.match(personalCenter, /excluded|不包含密码哈希、重置令牌或\s+API\s+Key\s+值/)
})

test('deletion intake requires password and exact acknowledgement without claiming erasure', () => {
  assert.match(personalCenter, /REQUEST ACCOUNT DELETION/)
  assert.match(personalCenter, /autocomplete="current-password"/)
  assert.match(personalCenter, /user\/privacy\/deletion-requests/)
  assert.match(personalCenter, /只安全登记和撤销申请，不会声称已经删除/)
  assert.match(personalCenter, /尚未执行/)
  assert.match(personalCenter, /cancelDeletionRequest/)
})

test('personal center exposes a read-only blocked deletion impact plan', () => {
  assert.match(personalCenter, /user\/privacy\/deletion-impact-plan/)
  assert.match(personalCenter, /account-deletion-impact-plan-v1/)
  assert.match(personalCenter, /data\.deletion_performed !== false/)
  assert.match(personalCenter, /data\.execution_state !== 'blocked'/)
  assert.match(personalCenter, /data\.operation_mode !== 'read_only_preflight'/)
  assert.match(personalCenter, /账号删除影响预检（只读）/)
  assert.match(personalCenter, /不会登记申请，也不会删除、匿名化或改写任何数据/)
  assert.match(personalCenter, /保留期限、法律依据、可恢复检查点和人工授权/)
  assert.match(personalCenter, /未能证明的范围不会按 0 条处理/)
  assert.match(personalCenter, /aria-live="assertive"/)
  assert.match(personalCenter, /min-height:\s*44px/)
})

test('personal center privacy slice compiles as a Vue SFC', () => {
  const parsed = parse(personalCenter, { filename: 'PersonalCenter.vue' })
  assert.deepEqual(parsed.errors, [])
  const script = compileScript(parsed.descriptor, { id: 'identity-privacy-feature' })
  const template = compileTemplate({
    id: 'identity-privacy-feature',
    filename: 'PersonalCenter.vue',
    source: parsed.descriptor.template.content,
    compilerOptions: { bindingMetadata: script.bindings },
  })
  assert.deepEqual(template.errors, [])
})
