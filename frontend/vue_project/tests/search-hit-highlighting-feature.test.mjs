import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  SEARCH_HIT_SCHEMA_VERSION,
  buildHighlightedSegments,
  normalizeSearchHitDisclosure,
} from '../src/features/search/index.js'

function validHit(overrides = {}) {
  return {
    schema_version: SEARCH_HIT_SCHEMA_VERSION,
    status: 'available',
    offset_encoding: 'unicode_code_points',
    coverage: 'positive_literal_terms_in_returned_display_only',
    effective_search_fields: ['news.title', 'news.body'],
    alias_span_state: 'not_available',
    relevance_score_state: 'not_available',
    document_match_state: 'not_asserted',
    reason_code: 'DISPLAY_LITERAL_MATCHES_FOUND',
    spans: [
      { field: 'title', start: 2, end: 7 },
      { field: 'abstract', start: 0, end: 5 },
    ],
    ...overrides,
  }
}

test('strict display spans preserve astral Unicode and remain plain text segments', () => {
  const title = '😀 China <img src=x onerror=secret()>'
  const hit = normalizeSearchHitDisclosure(validHit(), { title, abstract: 'China summary' })
  assert.equal(hit.status, 'available')
  assert.equal(hit.spans.length, 2)

  const segments = buildHighlightedSegments(title, hit, 'title')
  assert.deepEqual(segments, [
    { text: '😀 ', matched: false },
    { text: 'China', matched: true },
    { text: ' <img src=x onerror=secret()>', matched: false },
  ])
  assert.equal(segments.map((item) => item.text).join(''), title)
  assert.equal(Object.prototype.hasOwnProperty.call(hit.spans[0], 'matched_text'), false)
})

test('malformed, overlapping, extra-key and out-of-range spans fail closed', () => {
  const display = { title: 'China title', abstract: 'China abstract' }
  const candidates = [
    validHit({ spans: [{ field: 'title', start: 0, end: 99 }] }),
    validHit({
      spans: [
        { field: 'title', start: 0, end: 5 },
        { field: 'title', start: 4, end: 7 },
      ],
    }),
    validHit({ spans: [{ field: 'title', start: 0, end: 5, matched_text: 'China' }] }),
    validHit({ document_match_state: 'matched' }),
    validHit({ schema_version: 'future-search-hit-v99' }),
  ]

  for (const candidate of candidates) {
    const hit = normalizeSearchHitDisclosure(candidate, display)
    assert.equal(hit.status, 'unavailable')
    assert.equal(hit.reasonCode, 'SEARCH_HIT_CONTRACT_INVALID')
    assert.deepEqual(buildHighlightedSegments(display.title, hit, 'title'), [
      { text: display.title, matched: false },
    ])
  }
})

test('search result UI uses escaped mark nodes and discloses highlight limits', async () => {
  const source = await readFile(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8')

  assert.match(source, /buildHighlightedSegments\(item\.title, item\.searchHit, 'title'\)/)
  assert.match(source, /buildHighlightedSegments\(item\.displayDesc, item\.searchHit, 'abstract'\)/)
  assert.match(source, /<mark v-if="segment\.matched"/)
  assert.doesNotMatch(source, /v-html=.*(?:title|displayDesc|highlight)/)
  assert.match(source, /仅标记返回页显示文本中的正向原样词项/)
  assert.match(source, /不代表相关性分数、别名命中或正文其他位置/)
})
