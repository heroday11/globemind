import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  GOVERNANCE_CONTACT,
  GOVERNANCE_PATHS,
  GOVERNANCE_REVISION,
  PUBLIC_GOVERNANCE_PAGES,
  RESPONSIBILITY_MATRIX,
  buildGovernanceMailto,
} from '../src/governance/publicGovernance.js'

const EXPECTED_GOVERNANCE_PATHS = [
  '/corrections',
  '/methodology',
  '/privacy',
  '/security',
  '/sources',
  '/terms',
]

function allPageText(path) {
  const page = PUBLIC_GOVERNANCE_PAGES[path]
  return [page.title, page.status, page.summary, ...page.sections.flatMap((section) => [section.title, ...section.paragraphs])].join('\n')
}

test('public governance metadata exposes stable, honest V0.9 entry points', () => {
  assert.deepEqual([...GOVERNANCE_PATHS].sort(), EXPECTED_GOVERNANCE_PATHS)
  assert.equal(GOVERNANCE_CONTACT.email, 'contact@globemind.top')
  assert.equal(GOVERNANCE_REVISION.version, 'V0.9')
  assert.equal(GOVERNANCE_REVISION.updatedAt, '2026-08-09')
  assert.match(GOVERNANCE_CONTACT.note, /未公布|尚未/)
  assert.match(GOVERNANCE_CONTACT.note, /SLA/)
  assert.match(buildGovernanceMailto('[SECURITY] test'), /^mailto:contact@globemind\.top\?subject=/)

  for (const path of EXPECTED_GOVERNANCE_PATHS) {
    const page = PUBLIC_GOVERNANCE_PAGES[path]
    assert.ok(page)
    assert.match(page.status, /待|尚未/)
    assert.ok(page.sections.length >= 2)
    assert.ok(page.related.length >= 3)
  }

  assert.match(allPageText('/privacy'), /姓名/)
  assert.match(allPageText('/privacy'), /手机号/)
  assert.match(allPageText('/privacy'), /保留期/)
  assert.match(allPageText('/terms'), /不应被直接当作实时预警/)
  assert.match(allPageText('/terms'), /许可登记尚未完成/)
  assert.match(allPageText('/security'), /不是安全认证/)
  assert.match(allPageText('/methodology'), /不是完整可复现实验记录/)
  assert.match(allPageText('/methodology'), /不自动等同于经校准概率/)
  assert.match(allPageText('/sources'), /尚未公布.*全量来源目录/)
  assert.match(allPageText('/sources'), /不提供覆盖全部数据的统一开放许可证/)
  assert.match(allPageText('/corrections'), /跟踪编号.*待上线/)
})

test('responsibility matrix names scopes and intake without inventing owners', () => {
  assert.equal(RESPONSIBILITY_MATRIX.length, 4)
  for (const row of RESPONSIBILITY_MATRIX) {
    assert.ok(row.area)
    assert.ok(row.role)
    assert.equal(row.owner, '待指定')
    assert.match(row.intake, /^\/(?:corrections|security)/)
  }
})

test('production router keeps showcases development-only and publishes governance routes', async () => {
  const router = await readFile(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const developmentBlock = router.match(
    /const DEVELOPMENT_ONLY_ROUTES\s*=\s*\[([\s\S]*?)\]\s*\n\s*const router/,
  )
  assert.ok(developmentBlock)

  for (const path of ['/showcase', '/showcase/delta-force', '/story-graph-handle-debug']) {
    assert.match(developmentBlock[1], new RegExp(`path: ['"]${path.replaceAll('/', '\\/')}['"]`))
    assert.equal(router.match(new RegExp(`path: ['"]${path.replaceAll('/', '\\/')}['"]`, 'g'))?.length, 1)
  }
  assert.match(router, /\.\.\.\(import\.meta\.env\.DEV \? DEVELOPMENT_ONLY_ROUTES : \[\]\)/)
  assert.match(router, /import \{ resolveSafeInternalRedirect \} from ['"]@\/utils\/internalRedirect\.js['"]/)
  assert.match(router, /next\(resolveSafeInternalRedirect\(to\.query\.redirect, router\.resolve\)\)/)

  for (const path of EXPECTED_GOVERNANCE_PATHS) {
    assert.match(router, new RegExp(`path: ['"]${path.replaceAll('/', '\\/')}['"]`))
  }
  assert.match(router, /path: ['"]help-docs['"][\s\S]*name: ['"]PublicHelpDocs['"]/)
  assert.match(router, /path: ['"]\/status['"][\s\S]*name: ['"]SystemStatus['"]/)

  const legacyRoute = router.match(/path: ['"]\/data-statistics['"]([\s\S]*?)\n\s*\},/)
  assert.ok(legacyRoute)
  assert.match(legacyRoute[1], /LegacyDataStatisticsNotice/)
  assert.doesNotMatch(legacyRoute[1], /redirect:/)

  for (const [path, routeName] of [
    ['ground-news-search', 'LegacyGroundNewsSearchNotice'],
    ['ground-news-blindspot', 'LegacyGroundNewsBlindspotNotice'],
  ]) {
    const route = router.match(new RegExp(`path: ['"]${path}['"]([\\s\\S]*?)\\n\\s*\\},`))
    assert.ok(route, path)
    assert.match(route[1], new RegExp(routeName))
    assert.match(route[1], /LegacyGroundNewsNotice/)
    assert.doesNotMatch(route[1], /redirect:/)
  }
})

test('retired Ground News deep links explain migration and require a manual destination', async () => {
  const source = await readFile(new URL('../src/views/LegacyGroundNewsNotice.vue', import.meta.url), 'utf8')

  for (const path of ['/data-service/ground-news-search', '/data-service/ground-news-blindspot']) {
    assert.match(source, new RegExp(path.replaceAll('/', '\\/')))
  }
  for (const target of [
    '/data-service/data-search',
    '/data-service/ground-news-desk',
    '/data-service/ground-news',
  ]) {
    assert.match(source, new RegExp(`to: ['"]${target.replaceAll('/', '\\/')}['"]`))
  }
  assert.match(source, /避免静默/)
  assert.match(source, /手动选择/)
  assert.match(source, /不会自动恢复旧盲点页/)
  assert.match(source, /<h1[^>]*tabindex="-1"/)
})

test('public help and about pages make every governance entry discoverable', async () => {
  const sources = await Promise.all(
    [
      '../src/components/appNav.vue',
      '../src/views/AboutUs.vue',
      '../src/views/user/HelpDocs.vue',
      '../src/views/Register.vue',
    ].map((path) => readFile(new URL(path, import.meta.url), 'utf8')),
  )
  const combined = sources.join('\n')

  assert.match(sources[0], /\/data-service\/help-docs/)
  assert.match(combined, /(?:to|href)=['"]\/status['"]/)
  for (const path of EXPECTED_GOVERNANCE_PATHS) {
    assert.match(combined, new RegExp(`(?:to|href)=['"]${path.replaceAll('/', '\\/')}['"]`))
  }
})

test('security page discovers the bounded RFC 9116 contact document without inventing SLA', async () => {
  const source = await readFile(new URL('../src/views/PublicGovernance.vue', import.meta.url), 'utf8')

  assert.match(source, /route\.path === ['"]\/security['"]/)
  assert.match(source, /href="\/\.well-known\/security\.txt"/)
  assert.match(source, /不表示已有具名安全负责人或响应 SLA/)
})

test('authentication pages bind labels and expose focusable live status messages', async () => {
  const filenames = ['Register.vue', 'Login.vue', 'ForgotPassword.vue', 'ResetPassword.vue']
  for (const filename of filenames) {
    const source = await readFile(new URL(`../src/views/${filename}`, import.meta.url), 'utf8')
    const labelTargets = [...source.matchAll(/<label\s+for="([^"]+)"/g)].map((match) => match[1])
    assert.ok(labelTargets.length > 0, filename)
    for (const id of labelTargets) {
      assert.match(source, new RegExp(`<(?:input|textarea|select)[^>]*\\bid="${id}"`), `${filename}: ${id}`)
    }
    assert.match(source, /role="alert"[^>]*aria-live="assertive"[^>]*tabindex="-1"/, filename)
    assert.match(source, /role="status"[^>]*aria-live="polite"[^>]*tabindex="-1"/, filename)
    assert.match(source, /\.value\?\.focus\(\)/, filename)
  }

  const register = await readFile(new URL('../src/views/Register.vue', import.meta.url), 'utf8')
  assert.match(register, /id="register-consent"[\s\S]*type="checkbox"[\s\S]*required/)
  assert.match(register, /to="\/terms"/)
  assert.match(register, /to="\/privacy"/)
  assert.match(register, /姓名与手机号为可选资料/)
  assert.doesNotMatch(register, /id="register-full-name"[^>]*required/)
  assert.doesNotMatch(register, /id="register-phone"[^>]*required/)

  const login = await readFile(new URL('../src/views/Login.vue', import.meta.url), 'utf8')
  assert.match(login, /autocomplete="username"/)
  assert.match(login, /autocomplete="current-password"/)
  assert.match(login, /resolveSafeInternalRedirect\(route\.query\.redirect, router\.resolve\)/)
})
