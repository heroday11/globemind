import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileTemplate, parse } from '@vue/compiler-sfc'

import { createLatestRequestGate } from '../src/utils/latestRequest.js'
import {
  collectBrowserFavorites,
  mergeFavoriteSources,
  normalizeServerFavorites,
} from '../src/features/collections/index.js'

const viewUrl = new URL('../src/views/user/MyCollections.vue', import.meta.url)

test('latest-request gate invalidates older and detached async work', async () => {
  const gate = createLatestRequestGate()
  let published = 'initial'
  let releaseFirst
  const firstDelay = new Promise((resolve) => {
    releaseFirst = resolve
  })

  async function publishAfter(delay, value) {
    const isCurrent = gate.begin()
    await delay
    if (isCurrent()) published = value
  }

  const first = publishAfter(firstDelay, 'stale')
  await publishAfter(Promise.resolve(), 'newest')
  releaseFirst()
  await first
  assert.equal(published, 'newest')

  let releaseDetached
  const detachedDelay = new Promise((resolve) => {
    releaseDetached = resolve
  })
  const detached = publishAfter(detachedDelay, 'detached')

  gate.invalidate()
  releaseDetached()
  await detached
  assert.equal(published, 'newest')
})

test('collections loading keeps only the newest request eligible to publish state', async () => {
  const source = await readFile(viewUrl, 'utf8')

  assert.match(source, /import \{ createLatestRequestGate \} from '@\/utils\/latestRequest'/)
  assert.match(source, /const favoritesLoadGate = createLatestRequestGate\(\)/)
  assert.match(source, /const isCurrent = favoritesLoadGate\.begin\(\)/)
  assert.match(source, /if \(!isCurrent\(\)\) return/)
  assert.match(source, /favoritesLoadGate\.invalidate\(\)/)
})

test('collections exposes labelled search and announced loading, fallback, and empty states', async () => {
  const source = await readFile(viewUrl, 'utf8')

  assert.match(source, /<label class="mc-visually-hidden" for="collection-search">/)
  assert.match(source, /id="collection-search"/)
  assert.match(source, /name="collection-search"/)
  assert.match(source, /class="mc-state" role="status" aria-live="polite" aria-atomic="true"/)
  assert.match(source, /class="mc-spinner" aria-hidden="true"/)
  assert.match(source, /class="mc-empty" role="status" aria-live="polite"/)
  assert.match(
    source,
    /class="mc-banner"\s+role="status"\s+aria-live="polite"\s+aria-atomic="true"/,
  )
  assert.match(source, /class="mc-filter-empty" role="status"/)
})

test('collections source defines visible keyboard focus, touch-sized controls, and reduced motion', async () => {
  const source = await readFile(viewUrl, 'utf8')

  assert.match(source, /\.mc-search-input\s*\{[\s\S]*?min-height:\s*44px;/)
  assert.match(source, /\.mc-btn\s*\{[\s\S]*?min-height:\s*44px;/)
  assert.match(source, /\.mc-item\s*\{[\s\S]*?min-height:\s*44px;/)
  assert.match(source, /\.mc-item:focus-visible,[\s\S]*?\.mc-btn:focus-visible\s*\{[\s\S]*?outline:/)
  assert.match(source, /\.mc-item:focus-visible \.mc-item-arrow/)
  assert.match(source, /@media \(prefers-reduced-motion:\s*reduce\)/)
  assert.match(source, /overflow-wrap:\s*anywhere;/)
})

test('collections remains a valid compilable Vue template', async () => {
  const source = await readFile(viewUrl, 'utf8')
  const { descriptor, errors } = parse(source, { filename: 'MyCollections.vue' })
  assert.deepEqual(errors, [])
  assert.ok(descriptor.template)
  const compiled = compileTemplate({
    filename: 'MyCollections.vue',
    id: 'user-collections-accessibility',
    source: descriptor.template.content,
  })
  assert.deepEqual(compiled.errors, [])
})

test('collections does not leak API error bodies or claim that display merge synchronizes data', async () => {
  const source = await readFile(viewUrl, 'utf8')

  assert.doesNotMatch(source, /formatApiDetail/)
  assert.doesNotMatch(source, /服务器收藏列表暂不可用（\$\{msg\}）/)
  assert.doesNotMatch(source, /收藏会同步到服务器并在多设备可见/)
  assert.match(source, /不会上传或改写/)
  assert.match(source, /仅在本页按新闻 ID 去重展示/)
})

test('collections renders explicit account and browser source semantics', async () => {
  const source = await readFile(viewUrl, 'utf8')

  assert.match(source, /row\.sourceLabel/)
  assert.match(source, /服务器返回的预警不会作为收藏显示/)
  assert.match(source, /当前显示/)
  assert.doesNotMatch(source, />条收录</)
})

test('server collection model rejects unknown counts and excludes warnings from favorites', () => {
  const response = {
    schema_version: 'user-favorites-v2',
    items: [
      { news_id: 7, topic: 'policy', kind: 'favorite' },
      { news_id: 8, topic: 'alerts', kind: 'warning' },
      { news_id: 7, topic: 'second', kind: 'favorite' },
    ],
    news_ids: [7],
    counts: {
      favorite_records: 2,
      warning_records: 1,
      invalid_records: 0,
      distinct_favorite_news: 1,
    },
  }
  const normalized = normalizeServerFavorites(response)
  assert.deepEqual(normalized.records, [
    { id: 7, topic: 'policy' },
    { id: 7, topic: 'second' },
  ])
  assert.equal(normalized.counts.warningRecords, 1)

  assert.throws(
    () => normalizeServerFavorites({ ...response, counts: null }),
    /summary is unavailable/,
  )
  assert.throws(
    () => normalizeServerFavorites({
      ...response,
      counts: { ...response.counts, warning_records: null },
    }),
    /count is unavailable/,
  )
  assert.throws(
    () => normalizeServerFavorites({ ...response, news_ids: [7, 8] }),
    /projection is inconsistent/,
  )
})

test('collection display merge deduplicates by news id without hiding source or topics', () => {
  const browser = collectBrowserFavorites({
    local: [
      { id: 7, title: 'Local title' },
      { id: '8', title: 'coerced id must not enter' },
    ],
  })
  assert.deepEqual(browser.records, [{ id: 7, title: 'Local title', topic: 'local' }])
  assert.equal(browser.invalidRecords, 1)

  const merged = mergeFavoriteSources(
    [
      { id: 7, topic: 'policy' },
      { id: 9, topic: 'account-only' },
    ],
    browser.records,
  )
  assert.equal(merged.total, 2)
  assert.deepEqual(merged.rows, [
    {
      id: 7,
      title: 'Local title',
      topics: ['policy', 'local'],
      topic: 'policy · local',
      source: 'account+browser',
      sourceLabel: '账号与本机均有记录',
    },
    {
      id: 9,
      title: '',
      topics: ['account-only'],
      topic: 'account-only',
      source: 'account',
      sourceLabel: '账号收藏',
    },
  ])
})
