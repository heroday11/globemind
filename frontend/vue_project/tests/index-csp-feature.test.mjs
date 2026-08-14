import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'

import { hardenIndexHtml } from '../vite.config.js'

test('index CSP permits only same-origin scripts and exact generated inline hashes', () => {
  const inline = 'window.__legacy_bootstrap__ = true'
  const html = `<!doctype html><html><head>
    <meta http-equiv="Content-Security-Policy" content="script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'">
  </head><body>
    <script type="module" src="/assets/app.js"></script>
    <script nomodule>${inline}</script>
  </body></html>`

  const hardened = hardenIndexHtml(html)
  const expectedHash = createHash('sha256').update(inline, 'utf8').digest('base64')

  assert.doesNotMatch(hardened, /unsafe-inline/)
  assert.ok(hardened.includes(`script-src 'self' 'sha256-${expectedHash}'`))
  assert.match(hardened, /<script data-cfasync="false" type="module" src="\/assets\/app\.js">/)
  assert.match(hardened, /<script data-cfasync="false" nomodule>/)
  assert.doesNotMatch(hardened, /cloudflareinsights|https?:\/\//)
})

test('changing inline bootstrap content changes its CSP allowlist hash', () => {
  const wrap = (body) =>
    `<html><head><meta http-equiv="Content-Security-Policy" content="script-src 'self'"></head>` +
    `<body><script>${body}</script></body></html>`

  assert.notEqual(hardenIndexHtml(wrap('one')), hardenIndexHtml(wrap('two')))
})
