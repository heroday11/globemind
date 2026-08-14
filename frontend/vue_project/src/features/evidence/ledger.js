export const EVIDENCE_LEDGER_SCHEMA_VERSION = 'evidence-ledger-v1'

const EVENT_ID = /^evt-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$/
const SNAPSHOT_ID = /^article-([1-9][0-9]*)-([0-9a-f]{64})$/
const CLAIM_ID = /^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$/
const MAX_EVENTS_PER_ARTICLE = 1000
const MAX_PAGE_ITEMS = 100
const MAX_CLAIMS_PER_EVENT = 200

function validClaimIds(value) {
  return Array.isArray(value)
    && value.length <= MAX_CLAIMS_PER_EVENT
    && value.every((item) => typeof item === 'string' && CLAIM_ID.test(item))
    && new Set(value).size === value.length
}

export function unavailableEvidenceLedger(articleId, reason = 'LEDGER_UNAVAILABLE') {
  return {
    schema_version: EVIDENCE_LEDGER_SCHEMA_VERSION,
    article_id: Number(articleId) || 0,
    event_count: 0,
    items: [],
    available: false,
    reason,
  }
}

export function normalizeEvidenceLedgerHistory(value, expectedArticleId) {
  const articleId = Number(expectedArticleId)
  if (
    !Number.isInteger(articleId)
    || articleId <= 0
    || value?.schema_version !== EVIDENCE_LEDGER_SCHEMA_VERSION
    || Number(value?.article_id) !== articleId
    || !Number.isInteger(value?.event_count)
    || value.event_count < 0
    || value.event_count > MAX_EVENTS_PER_ARTICLE
    || !Array.isArray(value?.items)
    || value.items.length > MAX_PAGE_ITEMS
    || value.items.length > value.event_count
  ) {
    return unavailableEvidenceLedger(articleId, 'INVALID_LEDGER_CONTRACT')
  }
  const seen = new Set()
  const items = []
  for (const raw of value.items) {
    const snapshotMatch = SNAPSHOT_ID.exec(String(raw?.snapshot_id || ''))
    const impactStatus = raw?.impact_status
    const impactReview = raw?.impact_review
    if (
      Number(raw?.article_id) !== articleId
      || !EVENT_ID.test(String(raw?.event_id || ''))
      || seen.has(raw.event_id)
      || !snapshotMatch
      || Number(snapshotMatch[1]) !== articleId
      || !['initial', 'update', 'correction', 'withdrawal'].includes(raw?.declared_change_type)
      || typeof raw?.content_changed !== 'boolean'
      || !validClaimIds(raw?.claim_ids)
      || !validClaimIds(raw?.impacted_claim_ids)
      || !['none', 'review_required'].includes(impactStatus)
      || !impactReview
      || !['none', 'review_required', 'reviewed'].includes(impactReview.status)
      || !Number.isInteger(impactReview.review_count)
      || impactReview.review_count < 0
    ) {
      return unavailableEvidenceLedger(articleId, 'INVALID_LEDGER_EVENT')
    }
    if (
      (impactReview.review_count === 0 && impactReview.latest != null)
      || (impactReview.review_count > 0 && !impactReview.latest)
      || (
        impactStatus === 'none'
        && (
          raw.impacted_claim_ids.length
          || impactReview.status !== 'none'
          || impactReview.review_count !== 0
        )
      )
      || (
        impactStatus === 'review_required'
        && (
          !raw.impacted_claim_ids.length
          || (impactReview.review_count === 0 && impactReview.status !== 'review_required')
          || (impactReview.review_count > 0 && impactReview.status !== 'reviewed')
        )
      )
    ) {
      return unavailableEvidenceLedger(articleId, 'CONFLICTING_LEDGER_EVENT')
    }
    seen.add(raw.event_id)
    items.push({ ...raw })
  }
  return {
    ...value,
    article_id: articleId,
    items,
    available: true,
    reason: null,
  }
}
