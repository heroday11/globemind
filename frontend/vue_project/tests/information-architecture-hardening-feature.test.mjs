import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileTemplate, parse } from '@vue/compiler-sfc'

import { hardenIndexHtml, isUnapprovedActiveDocument } from '../vite.config.js'

const read = (relative) => readFile(new URL(relative, import.meta.url), 'utf8')

async function walkFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) files.push(...(await walkFiles(path)))
    else files.push(path)
  }
  return files
}

test('data-service root and retired deep links never render an empty shell or silently discard intent', async () => {
  const [router, notice] = await Promise.all([
    read('../src/router/index.js'),
    read('../src/views/LegacyDataServiceNotice.vue'),
  ])

  assert.match(
    router,
    /path: '\/data-service',[\s\S]*?children: \[[\s\S]*?path: '',\s*redirect: \(to\) => \(\{[\s\S]*?path: '\/data-service\/data-search',[\s\S]*?query: to\.query,[\s\S]*?hash: to\.hash/,
  )
  for (const path of ['alert-center', 'open-computing', 'algorithm-analysis', 'model-test/:modelId']) {
    const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const routeBlock = router.match(new RegExp(`path: '${escaped}',([\\s\\S]*?)(?=\\n\\s*\\},)`))?.[1] || ''
    assert.match(routeBlock, /component: \(\) => import\('@\/views\/LegacyDataServiceNotice\.vue'\)/, path)
    assert.doesNotMatch(routeBlock, /redirect:/, path)
  }
  assert.match(notice, /不会自动带入|不会自动迁移/)
  assert.match(notice, /手动选择/)
  assert.match(notice, /tabindex="-1"/)
})

test('authentication return targets are bounded, same-site, non-looping, and resolve to a real route', async () => {
  const { resolveSafeInternalRedirect } = await import('../src/utils/internalRedirect.js')
  const resolve = (target) => {
    const known = target.startsWith('/data-service/data-search')
    return known ? { name: 'DataSearch', matched: [{}] } : { name: 'NotFound', matched: [{}] }
  }

  assert.equal(resolveSafeInternalRedirect('/data-service/data-search?q=a#result', resolve), '/data-service/data-search?q=a#result')
  for (const value of [
    'https://attacker.example/path',
    '//attacker.example/path',
    '/\\\\attacker.example/path',
    '/%5c%5cattacker.example/path',
    '/data-service/data-search\nnext',
    '/login',
    '/register?redirect=%2Fdata-service%2Fdata-search',
    '/route-that-does-not-exist',
    ['/data-service/data-search', '//attacker.example'],
  ]) {
    assert.equal(resolveSafeInternalRedirect(value, resolve), '/', JSON.stringify(value))
  }

  const [router, login] = await Promise.all([
    read('../src/router/index.js'),
    read('../src/views/Login.vue'),
  ])
  assert.match(router, /resolveSafeInternalRedirect\(to\.query\.redirect, router\.resolve\)/)
  assert.match(login, /resolveSafeInternalRedirect\(route\.query\.redirect, router\.resolve\)/)
})

test('route preload scheduling deduplicates intent, cancels late timers, and retries failures', async () => {
  const { createRoutePreloadController } = await import('../src/router/routePreloadController.js')
  const timers = new Map()
  let timerId = 0
  const calls = []
  let shouldFail = false
  const controller = createRoutePreloadController(
    {
      '/known': async () => {
        calls.push('/known')
        if (shouldFail) throw new Error('chunk unavailable')
      },
    },
    {
      delay: 120,
      setTimer(callback) {
        timerId += 1
        timers.set(timerId, callback)
        return timerId
      },
      clearTimer(id) {
        timers.delete(id)
      },
    },
  )

  assert.equal(controller.schedule('/known'), true)
  assert.equal(controller.schedule('/known'), false)
  assert.equal(timers.size, 1)
  assert.equal(await controller.loadNow('/known'), true)
  assert.equal(timers.size, 0)
  assert.deepEqual(calls, ['/known'])
  assert.equal(await controller.loadNow('/known'), false)

  shouldFail = true
  const retrying = createRoutePreloadController(
    { '/known': async () => {
      calls.push('/retry')
      if (shouldFail) throw new Error('chunk unavailable')
    } },
    { delay: 120 },
  )
  assert.equal(await retrying.loadNow('/known'), false)
  shouldFail = false
  assert.equal(await retrying.loadNow('/known'), true)
  assert.deepEqual(calls.slice(-2), ['/retry', '/retry'])

  const disposed = createRoutePreloadController(
    { '/known': async () => calls.push('/late') },
    {
      setTimer(callback) {
        timerId += 1
        timers.set(timerId, callback)
        return timerId
      },
      clearTimer(id) {
        timers.delete(id)
      },
    },
  )
  disposed.schedule('/known')
  disposed.dispose()
  assert.equal(timers.size, 0)
  assert.equal(await disposed.loadNow('/known'), false)
  assert.ok(!calls.includes('/late'))

  const router = await read('../src/router/index.js')
  assert.match(
    router,
    /router\.onError[\s\S]*?sessionStorage\.setItem\([\s\S]*?catch \{[\s\S]{0,180}\breturn\s*\}[\s\S]*?window\.location\.assign/,
  )
})

test('protected production routes are not advertised to signed-out users and navigation controls are keyboard-native', async () => {
  const navigation = await read('../src/components/appNav.vue')
  for (const path of [
    '/data-service/pipeline-monitor',
    '/research-workspace',
    '/model-assurance',
    '/entity-governance',
  ]) {
    const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    assert.match(navigation, new RegExp(`v-if="hasToken"[\\s\\S]{0,220}${escaped}`), path)
  }

  for (const className of [
    'logo-corner',
    'dropdown-trigger',
    'dropdown-item-content',
    'mobile-nav-item-row',
    'mobile-submenu-item',
    'mobile-user-info',
  ]) {
    assert.doesNotMatch(
      navigation,
      new RegExp(`<div[^>]*class="[^"]*${className}[^"]*"[^>]*@click=`),
      className,
    )
  }
  assert.match(navigation, /class="dropdown-trigger"[\s\S]{0,220}:aria-expanded=/)
  assert.match(navigation, /class="mobile-nav-item-row"[\s\S]{0,220}:aria-expanded=/)
})

test('SPA route changes expose a keyboard skip target and move focus into the new page', async () => {
  const app = await read('../src/App.vue')
  assert.match(app, /href="#main-content"/)
  assert.match(app, /id="main-content"/)
  assert.match(app, /ref="routeContent"/)
  assert.match(app, /watch\(\(\) => route\.path,[\s\S]*nextTick\(\)[\s\S]*\.focus\(/)
  assert.match(app, /h1\[tabindex='-1'\]:focus\)\s*\{\s*outline:\s*none;/)
  assert.doesNotMatch(app, /h1\[tabindex='-1'\]:focus(?:-visible)?\)\s*\{[\s\S]*?outline:\s*3px/)
})

test('mobile navigation behaves as a labelled, focus-managed modal drawer', async () => {
  const navigation = await read('../src/components/appNav.vue')
  assert.match(navigation, /ref="mobileHamburger"/)
  assert.match(navigation, /class="mobile-drawer"[\s\S]{0,240}role="dialog"[\s\S]{0,160}aria-modal="true"/)
  assert.match(navigation, /ref="mobileDrawerClose"/)
  assert.match(navigation, /@keydown\.esc[^>]*closeMobileMenu/)
  assert.match(navigation, /@keydown\.tab[^>]*trapMobileMenuFocus/)
  assert.match(navigation, /mobileDrawerClose\.value\?\.focus\(\)/)
  assert.match(navigation, /function mobileNavigateTo[\s\S]{0,180}closeMobileMenu\(\{ restoreFocus: false \}\)/)
})

test('robots, canonical allowlist, and sitemap form one exact public-index contract', async () => {
  const [router, robots, sitemap] = await Promise.all([
    read('../src/router/index.js'),
    read('../public/robots.txt'),
    read('../public/sitemap.xml'),
  ])
  const allowlistBody = router.match(/const INDEXABLE_CANONICAL_PATHS = new Set\(\[([\s\S]*?)\]\)/)?.[1] || ''
  const allowlist = [...allowlistBody.matchAll(/'([^']+)'/g)].map((match) => match[1]).sort()
  const sitemapPaths = [...sitemap.matchAll(/<loc>https:\/\/globemind\.top([^<]*)<\/loc>/g)]
    .map((match) => match[1] || '/')
    .sort()

  assert.deepEqual(sitemapPaths, allowlist)
  assert.equal(new Set(sitemapPaths).size, sitemapPaths.length)
  for (const path of [
    '/user-center',
    '/research-workspace',
    '/model-assurance',
    '/entity-governance',
    '/data-service/pipeline-monitor',
    '/amazing-globe',
    '/fin-terminal',
    '/datasets',
    '/design-review',
  ]) {
    assert.match(robots, new RegExp(`^Disallow: ${path.replaceAll('/', '\\/')}$`, 'm'), path)
    assert.ok(!allowlist.includes(path), path)
  }
})

test('CSP hardening inserts or rewrites the policy regardless of attribute order', () => {
  const absent = hardenIndexHtml('<!doctype html><html><head><title>x</title></head><body><script>boot()</script></body></html>')
  assert.match(absent, /http-equiv="Content-Security-Policy"/)
  assert.match(absent, /script-src 'self' 'sha256-/)

  const reordered = hardenIndexHtml(
    '<html><head><meta content="script-src * \'unsafe-inline\'" data-owner="qa" http-equiv="Content-Security-Policy"></head><body><script src="/app.js"></script></body></html>',
  )
  assert.doesNotMatch(reordered, /unsafe-inline|script-src \*/)
  assert.match(reordered, /content="script-src 'self'; object-src 'none'; base-uri 'self'"/)
  const latePolicy = hardenIndexHtml(
    '<html><head><script>boot()</script><meta http-equiv="Content-Security-Policy" content="script-src *"></head><body></body></html>',
  )
  assert.equal((latePolicy.match(/http-equiv="Content-Security-Policy"/g) || []).length, 1)
  assert.ok(latePolicy.indexOf('Content-Security-Policy') < latePolicy.indexOf('<script'))
  assert.throws(() => hardenIndexHtml('<script>boot()</script>'), /document head/i)
})

test('embedded static entries are noindex and script-restricted while generated app documents stay isolated', async () => {
  const [globe, vite, buildRelease] = await Promise.all([
    read('../public/amazing-globe/index.html'),
    read('../vite.config.js'),
    read('../scripts/build-release.mjs'),
  ])
  for (const [name, html] of [['globe', globe]]) {
    assert.match(html, /<html lang="zh-CN">/, name)
    assert.match(html, /<meta name="robots" content="noindex,nofollow"/, name)
    assert.match(html, /Content-Security-Policy[^>]*script-src 'self'/, name)
    assert.doesNotMatch(html, /unsafe-inline|unsafe-eval|script-src \*/, name)
    for (const tag of html.match(/<script\b[^>]*>/g) || []) {
      assert.match(tag, /data-cfasync="false"/, `${name}: ${tag}`)
    }
  }
  assert.match(vite, /PRODUCTION_PUBLIC_EXCLUDES[\s\S]*['"]design-review['"]/)
  assert.match(vite, /PRODUCTION_PUBLIC_ACTIVE_DOCUMENT_ROOTS[\s\S]*['"]datasets\/expert-skills['"]/)
  assert.match(vite, /removeUnapprovedActiveDocuments/)
  assert.match(vite, /removeExcludedPublicArtifactsPlugin/)
  assert.match(buildRelease, /financialTarget = path\.join\(outputRoot, ['"]fin-terminal['"]\)/)
  assert.match(buildRelease, /cpSync\(financialOutput, financialTarget/)
  for (const name of ['viewer.html', 'template.htm', 'plugin.js', 'worker.mjs']) {
    assert.equal(isUnapprovedActiveDocument(name), true, name)
  }
  for (const name of ['SKILL.md', 'catalog.json', 'preview.svg']) {
    assert.equal(isUnapprovedActiveDocument(name), false, name)
  }
})

test('every target=_blank link severs both opener and referrer channels', async () => {
  const sourceRoot = new URL('../src/', import.meta.url).pathname
  const files = (await walkFiles(sourceRoot)).filter((file) => /\.(?:vue|html)$/.test(file))
  for (const path of files) {
    const source = await readFile(path, 'utf8')
    for (const tag of source.match(/<(?:a|router-link)\b[^>]*target=["']_blank["'][^>]*>/gi) || []) {
      const rel = tag.match(/\brel=["']([^"']*)["']/i)?.[1]?.toLowerCase().split(/\s+/) || []
      assert.ok(rel.includes('noopener'), `${path}: ${tag}`)
      assert.ok(rel.includes('noreferrer'), `${path}: ${tag}`)
    }
  }
})

test('untrusted outbound link values allow only credential-free HTTP(S) or catalog Markdown paths', async () => {
  const { safeExpertSkillDocumentPath, safeExternalHttpUrl } = await import('../src/utils/externalUrl.js')
  assert.equal(safeExternalHttpUrl('https://news.example/world?q=1#source'), 'https://news.example/world?q=1#source')
  assert.equal(safeExternalHttpUrl('http://127.0.0.1:8080/article'), 'http://127.0.0.1:8080/article')
  for (const value of [
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'file:///etc/passwd',
    '//attacker.example/path',
    '/relative/path',
    'https://user:password@news.example/path',
    'https://news.example/\\attacker',
    'https://news.example/path\nnext',
    `https://news.example/${'x'.repeat(4096)}`,
    ['https://news.example/', 'javascript:alert(1)'],
  ]) {
    assert.equal(safeExternalHttpUrl(value), '', JSON.stringify(value))
  }
  assert.equal(
    safeExpertSkillDocumentPath('/datasets/expert-skills/sources/selection-policy.md'),
    '/datasets/expert-skills/sources/selection-policy.md',
  )
  for (const value of [
    'javascript:alert(1)',
    'https://globemind.top/datasets/expert-skills/sources/selection-policy.md',
    '/datasets/expert-skills/%2e%2e/catalog.json',
    '/datasets/expert-skills/template.html',
    '/datasets/expert-skills/source.md?download=1',
    '/outside/source.md',
  ]) {
    assert.equal(safeExpertSkillDocumentPath(value), '', value)
  }

  const [searchModel, detail, source, story, academic] = await Promise.all([
    read('../src/features/search/model.js'),
    read('../src/views/DataService/news-detail.vue'),
    read('../src/views/GroundNewsSource.vue'),
    read('../src/views/StoryGraphView.vue'),
    read('../src/views/AcademicData.vue'),
  ])
  assert.match(searchModel, /displayUrl: safeExternalHttpUrl\(/)
  assert.match(detail, /safeOriginalUrl/)
  assert.match(source, /safeEvidenceUrl/)
  assert.match(source, /safe_url: safeExternalHttpUrl\(item\.url\)/)
  assert.match(story, /safe_url: safeExternalHttpUrl\(item\.url\)/)
  assert.match(academic, /safeExpertSkillDocumentPath\(source\?\.localPath\)/)
  assert.match(academic, /safeExternalHttpUrl\(source\?\.repoUrl\)/)
})

test('financial terminal message bridge accepts only its iframe and approved internal destination', async () => {
  const terminal = await read('../src/views/FinancialTerminal.vue')
  assert.match(terminal, /e\.source !== terminalFrame\.value\?\.contentWindow/)
  assert.match(terminal, /e\.data\.path !== ['"]\/data-assistant['"]/)
  assert.match(terminal, /e\.origin !== window\.location\.origin/)
})

test('changed navigation and migration views remain compilable Vue templates', async () => {
  for (const relative of [
    '../src/components/appNav.vue',
    '../src/views/Login.vue',
    '../src/views/LegacyDataServiceNotice.vue',
  ]) {
    const source = await read(relative)
    const { descriptor, errors } = parse(source, { filename: relative })
    assert.deepEqual(errors, [], `${relative}: parse errors`)
    assert.ok(descriptor.template, `${relative}: missing template`)
    const compiled = compileTemplate({
      filename: relative,
      id: `ia-${relative.replaceAll('/', '-')}`,
      source: descriptor.template.content,
    })
    assert.deepEqual(compiled.errors, [], `${relative}: template compile errors`)
  }
})
