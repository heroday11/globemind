import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import postcss from 'postcss'

import {
  DEFAULT_DISPLAY_PREFERENCES,
  DISPLAY_PREFERENCES_STORAGE_PREFIX,
  applyDisplayPreferences,
  loadDisplayPreferences,
  normalizeDisplayPreferences,
  resolveDisplayPreferencesStorageKey,
  saveDisplayPreferences,
} from '@globemind/shared/display-preferences'
import { createTypographyPreferencesPlugin } from '@globemind/shared/postcss-typography'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function createStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null
    },
    setItem(key, value) {
      values.set(key, String(value))
    },
    removeItem(key) {
      values.delete(key)
    },
  }
}

test('display preferences normalize user input and isolate storage by account', () => {
  const storage = createStorage({ current_user: JSON.stringify({ id: 42, username: 'other' }) })
  const storageKey = resolveDisplayPreferencesStorageKey(storage)
  assert.equal(storageKey, `${DISPLAY_PREFERENCES_STORAGE_PREFIX}:42`)

  assert.deepEqual(
    normalizeDisplayPreferences({ fontFamily: 'serif', fontSizeOffset: 99, lineHeight: 'relaxed' }),
    { version: 1, fontFamily: 'serif', fontSizeOffset: 3, lineHeight: 'relaxed' },
  )
  assert.deepEqual(normalizeDisplayPreferences({ fontFamily: 'unknown', fontSizeOffset: 'bad' }), {
    ...DEFAULT_DISPLAY_PREFERENCES,
  })
})

test('display preferences persist safely and apply document-level CSS variables', () => {
  const storage = createStorage({ current_user: JSON.stringify({ username: 'qa-user' }) })
  const storageKey = resolveDisplayPreferencesStorageKey(storage)
  const saved = saveDisplayPreferences(
    { fontFamily: 'sans', fontSizeOffset: 2, lineHeight: 'compact' },
    storage,
    storageKey,
  )
  assert.deepEqual(loadDisplayPreferences(storage, storageKey), saved)

  const properties = new Map()
  const documentRoot = {
    dataset: {},
    style: {
      setProperty(name, value) {
        properties.set(name, value)
      },
    },
  }
  applyDisplayPreferences(saved, documentRoot)
  assert.equal(properties.get('--gm-root-font-size'), '17.25px')
  assert.equal(properties.get('--gm-leading'), '-0.08')
  assert.equal(properties.get('--gm-leading-px'), '-1px')
  assert.match(properties.get('--gm-font-sans'), /Noto Sans CJK SC/)
  assert.deepEqual(documentRoot.dataset, {
    gmFontFamily: 'sans',
    gmFontSize: '2',
    gmLineHeight: 'compact',
  })
})

test('typography PostCSS layer preserves hierarchy while making existing declarations adjustable', async () => {
  const result = await postcss([createTypographyPreferencesPlugin()]).process(
    `.body { font-size: 14px; line-height: 1.5; font-family: Inter, sans-serif; }
     .title { font-size: clamp(24px, 4vw, 42px); line-height: 42px; font-family: Georgia, serif; }
     .code { font-family: ui-monospace, Consolas, monospace; }
     .icon { font-size: 0; line-height: 0; font-family: inherit; }`,
    { from: undefined },
  )

  assert.match(result.css, /font-size: \.875rem/)
  assert.match(result.css, /line-height: calc\(1\.5 \+ var\(--gm-leading\)\)/)
  assert.match(result.css, /font-size: clamp\(1\.5rem, 4vw, 2\.625rem\)/)
  assert.match(result.css, /line-height: calc\(42px \+ var\(--gm-leading-px\)\)/)
  assert.match(result.css, /font-family: var\(--gm-font-sans\)/)
  assert.match(result.css, /font-family: var\(--gm-font-serif\)/)
  assert.match(result.css, /font-family: var\(--gm-font-mono\)/)
  assert.match(result.css, /\.icon \{ font-size: 0; line-height: 0; font-family: inherit; \}/)
})

test('typography PostCSS layer removes only declarations superseded by the same selector', async () => {
  const result = await postcss([createTypographyPreferencesPlugin()]).process(
    `.card { color: red; padding: 8px; }
     .other { color: green; }
     .card { color: blue; margin: 4px; }
     @media (min-width: 900px) { .card { color: purple; } }`,
    { from: undefined },
  )

  assert.doesNotMatch(result.css, /color: red/)
  assert.match(result.css, /padding: 8px/)
  assert.match(result.css, /color: blue/)
  assert.match(result.css, /color: purple/)
})

test('personal settings expose accessible live display controls and initialize before mount', () => {
  const panel = fs.readFileSync(
    path.join(root, 'src/views/user/DisplayPreferencesPanel.vue'),
    'utf8',
  )
  const personalCenter = fs.readFileSync(
    path.join(root, 'src/views/user/PersonalCenter.vue'),
    'utf8',
  )
  const main = fs.readFileSync(path.join(root, 'src/main.js'), 'utf8')
  const terminal = fs.readFileSync(
    path.join(root, '../financial-terminal/src/displayPreferences.ts'),
    'utf8',
  )

  assert.match(panel, /data-display-preferences/)
  assert.match(panel, /role="group"/)
  assert.match(panel, /:aria-pressed=/)
  assert.match(panel, /aria-labelledby="dp-font-size-title"/)
  assert.match(panel, /:aria-valuetext="fontSizeLabel"/)
  assert.match(panel, /type="range"/)
  assert.match(panel, /恢复默认/)
  assert.match(personalCenter, /\{ id: 'display', label: '显示设置' \}/)
  assert.match(personalCenter, /<DisplayPreferencesPanel v-if="activeTab === 'display'"/)
  assert.ok(main.indexOf('initializeDisplayPreferences()') < main.indexOf('createApp(App)'))
  assert.match(terminal, /window\.addEventListener\(['"]message['"], handleMessage\)/)
})
