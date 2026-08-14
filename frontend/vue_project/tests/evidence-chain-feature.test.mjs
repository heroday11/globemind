import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  articleParagraphAnchor,
  buildAssistantEvidenceContext,
  normalizeClaimType,
  normalizeEvidenceChain,
} from '../src/features/evidence/contract.js'
import {
  normalizeEvidenceLedgerHistory,
} from '../src/features/evidence/ledger.js'

const newsDetailSource = await readFile(
  new URL('../src/views/DataService/news-detail.vue', import.meta.url),
  'utf8',
)

test('claim classification only accepts the five public categories', () => {
  assert.equal(normalizeClaimType('information'), 'information')
  assert.equal(normalizeClaimType('hypothesis'), 'hypothesis')
  assert.equal(normalizeClaimType('judgment'), 'judgment')
  assert.equal(normalizeClaimType('indicator'), 'indicator')
  assert.equal(normalizeClaimType('invented'), 'unknown')
})

test('citation contract only accepts matching article paragraph anchors', () => {
  const chain = normalizeEvidenceChain({
    schema_version: 'article-evidence-v1',
    article_id: 7,
    paragraph_count: 2,
    claims: [{
      id: 'article:7:judgment',
      claim_type: 'judgment',
      text: 'A supported judgment.',
      source: 'test-model',
      evidence_status: 'available',
      citations: [{
        status: 'available',
        article_id: 7,
        paragraph_number: 2,
        anchor_id: articleParagraphAnchor(7, 2),
        relation: 'input',
        matched_text: 'China',
        excerpt: 'The article paragraph mentions China here.',
      }],
    }],
    provenance: {
      body_status: 'available',
      response_body_sha256: 'a'.repeat(64),
      hash_scope: 'normalized-display-body',
      snapshot_status: 'unavailable',
      snapshot_id: null,
      captured_at: null,
      parser_version: null,
      update_status: 'unavailable',
      correction_status: 'unavailable',
    },
  }, 7)
  assert.equal(chain.claims[0].evidence_status, 'available')
  assert.equal(chain.claims[0].citations[0].anchor_id, 'article-7-paragraph-2')

  const conflicting = normalizeEvidenceChain({
    ...chain,
    claims: [{
      ...chain.claims[0],
      citations: [{ ...chain.claims[0].citations[0], anchor_id: 'headline-7' }],
    }],
  }, 7)
  assert.equal(conflicting.claims[0].evidence_status, 'unavailable')
  assert.deepEqual(conflicting.claims[0].citations, [])

  const outOfRange = normalizeEvidenceChain({
    ...chain,
    claims: [{
      ...chain.claims[0],
      citations: [{
        ...chain.claims[0].citations[0],
        paragraph_number: 3,
        anchor_id: 'article-7-paragraph-3',
      }],
    }],
  }, 7)
  assert.equal(outOfRange.claims[0].evidence_status, 'unavailable')
})

test('missing contracts fail closed and assistant context forbids title inference', () => {
  const chain = normalizeEvidenceChain(null, 9)
  assert.equal(chain.claims[0].claim_type, 'unknown')
  assert.equal(chain.claims[0].evidence_status, 'unavailable')
  assert.match(buildAssistantEvidenceContext(chain)[0], /不得基于标题/)
})

test('revision ledger history rejects conflicting article and impact metadata', () => {
  const valid = normalizeEvidenceLedgerHistory({
    schema_version: 'evidence-ledger-v1',
    article_id: 7,
    event_count: 1,
    items: [{
      event_id: 'evt-20260809T010203000000Z-0123456789abcdef',
      article_id: 7,
      snapshot_id: `article-7-${'a'.repeat(64)}`,
      declared_change_type: 'initial',
      content_changed: false,
      claim_ids: [],
      impacted_claim_ids: [],
      impact_status: 'none',
      impact_review: { status: 'none', review_count: 0, latest: null },
    }],
  }, 7)
  assert.equal(valid.available, true)
  assert.equal(valid.items.length, 1)

  const conflicting = normalizeEvidenceLedgerHistory({
    ...valid,
    items: [{ ...valid.items[0], article_id: 8 }],
  }, 7)
  assert.equal(conflicting.available, false)
  assert.equal(conflicting.reason, 'INVALID_LEDGER_EVENT')

  const impossibleReview = normalizeEvidenceLedgerHistory({
    ...valid,
    items: [{
      ...valid.items[0],
      impact_review: {
        status: 'reviewed',
        review_count: 1,
        latest: { review_id: 'review-20260809T010204000000Z-0123456789abcdef' },
      },
    }],
  }, 7)
  assert.equal(impossibleReview.available, false)
  assert.equal(impossibleReview.reason, 'CONFLICTING_LEDGER_EVENT')

  const excessiveClaims = normalizeEvidenceLedgerHistory({
    ...valid,
    items: [{
      ...valid.items[0],
      claim_ids: Array.from({ length: 201 }, (_, index) => `article:7:claim-${index}`),
    }],
  }, 7)
  assert.equal(excessiveClaims.available, false)

  const excessivePage = normalizeEvidenceLedgerHistory({
    ...valid,
    event_count: 101,
    items: Array.from({ length: 101 }, (_, index) => ({
      ...valid.items[0],
      event_id: `evt-20260809T010203000000Z-${index.toString(16).padStart(16, '0')}`,
    })),
  }, 7)
  assert.equal(excessivePage.available, false)
})

test('article reader only captures snapshots through an explicit authenticated action', () => {
  assert.match(newsDetailSource, /evidence-ledger\/articles\/\$\{articleId\}\/history/)
  assert.match(newsDetailSource, /evidence-ledger\/articles\/\$\{articleId\}\/captures/)
  assert.match(newsDetailSource, /Authorization: `Bearer \$\{token\}`/)
  assert.match(newsDetailSource, /@click="captureEvidenceRevision"/)
  assert.match(newsDetailSource, /下游影响待复核/)
  assert.match(newsDetailSource, /aria-live="polite"/)
})

test('article evidence requests reject late responses and hide server error bodies', () => {
  assert.match(newsDetailSource, /createLatestRequestGate/)
  assert.match(newsDetailSource, /const newsRequestGate = createLatestRequestGate\(\)/)
  assert.match(newsDetailSource, /const evidenceCaptureRequestGate = createLatestRequestGate\(\)/)
  assert.match(newsDetailSource, /if \(!isCurrent\(\)\) return/)
  assert.doesNotMatch(newsDetailSource, /typeof data\.detail === 'string'/)
  assert.doesNotMatch(newsDetailSource, /ElMessage\.error\(e\.message/)
  assert.match(newsDetailSource, /证据快照保存失败，请稍后重试。/)
})
