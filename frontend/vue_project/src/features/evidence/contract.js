export const ARTICLE_EVIDENCE_SCHEMA_VERSION = 'article-evidence-v1'
export const CLAIM_TYPES = Object.freeze([
  'information',
  'hypothesis',
  'judgment',
  'unknown',
  'indicator',
])

const CLAIM_LABELS = Object.freeze({
  information: '信息',
  hypothesis: '假设',
  judgment: '判断',
  unknown: '未知',
  indicator: '观察指标',
})

export function normalizeClaimType(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return CLAIM_TYPES.includes(normalized) ? normalized : 'unknown'
}

export function claimTypeLabel(value) {
  return CLAIM_LABELS[normalizeClaimType(value)]
}

export function articleParagraphAnchor(articleId, paragraphNumber) {
  const id = Number(articleId)
  const paragraph = Number(paragraphNumber)
  if (!Number.isInteger(id) || id <= 0 || !Number.isInteger(paragraph) || paragraph <= 0) {
    return ''
  }
  return `article-${id}-paragraph-${paragraph}`
}

function normalizeCitation(value, articleId, paragraphCount) {
  if (!value || value.status !== 'available') return null
  const paragraphNumber = Number(value.paragraph_number)
  const expectedAnchor = articleParagraphAnchor(articleId, paragraphNumber)
  if (paragraphNumber > paragraphCount) return null
  if (!expectedAnchor || value.anchor_id !== expectedAnchor) return null
  if (Number(value.article_id) !== Number(articleId)) return null
  const excerpt = String(value.excerpt || '').trim()
  const matchedText = String(value.matched_text || '').trim()
  if (!excerpt || !matchedText || !excerpt.includes(matchedText)) return null
  return {
    status: 'available',
    article_id: Number(articleId),
    paragraph_number: paragraphNumber,
    anchor_id: expectedAnchor,
    relation: ['input', 'support', 'oppose', 'background'].includes(value.relation)
      ? value.relation
      : 'input',
    matched_text: matchedText,
    excerpt,
    source_url: value.source_url || null,
  }
}

function unavailableChain(articleId, reason) {
  return {
    schema_version: ARTICLE_EVIDENCE_SCHEMA_VERSION,
    article_id: Number(articleId) || 0,
    paragraph_count: 0,
    claims: [{
      id: `article:${Number(articleId) || 0}:contract-unknown`,
      claim_type: 'unknown',
      text: '证据链契约缺失或不一致。',
      source: 'article-analysis',
      evidence_status: 'unavailable',
      citations: [],
      unavailable_reason: reason,
    }],
    provenance: {
      body_status: 'unavailable',
      response_body_sha256: null,
      hash_scope: null,
      snapshot_status: 'unavailable',
      snapshot_id: null,
      captured_at: null,
      parser_version: null,
      update_status: 'unavailable',
      correction_status: 'unavailable',
    },
  }
}

export function normalizeEvidenceChain(value, expectedArticleId) {
  const articleId = Number(expectedArticleId)
  const paragraphCount = Number(value?.paragraph_count)
  const provenance = value?.provenance
  const provenanceConsistent = (
    provenance
    && ['available', 'unavailable'].includes(provenance.body_status)
    && provenance.snapshot_status === 'unavailable'
    && provenance.snapshot_id == null
    && provenance.captured_at == null
    && provenance.parser_version == null
    && provenance.update_status === 'unavailable'
    && provenance.correction_status === 'unavailable'
    && (
      provenance.body_status === 'unavailable'
        ? paragraphCount === 0
        : paragraphCount > 0
          && provenance.hash_scope === 'normalized-display-body'
          && /^[a-f0-9]{64}$/.test(String(provenance.response_body_sha256 || ''))
    )
  )
  if (
    !value
    || value.schema_version !== ARTICLE_EVIDENCE_SCHEMA_VERSION
    || Number(value.article_id) !== articleId
    || !Number.isInteger(paragraphCount)
    || paragraphCount < 0
    || !Array.isArray(value.claims)
    || !provenanceConsistent
  ) {
    return unavailableChain(articleId, 'INVALID_EVIDENCE_CONTRACT')
  }
  const claims = value.claims.map((claim, index) => {
    const citations = Array.isArray(claim?.citations)
      ? claim.citations
        .map((item) => normalizeCitation(item, articleId, paragraphCount))
        .filter(Boolean)
      : []
    const requestedAvailable = claim?.evidence_status === 'available'
    const evidenceStatus = requestedAvailable
      && citations.length
      && !claim?.unavailable_reason
      ? 'available'
      : 'unavailable'
    return {
      id: String(claim?.id || `article:${articleId}:claim-${index + 1}`),
      claim_type: normalizeClaimType(claim?.claim_type),
      text: String(claim?.text || '未提供主张文本'),
      source: String(claim?.source || 'unknown'),
      evidence_status: evidenceStatus,
      citations: evidenceStatus === 'available' ? citations : [],
      unavailable_reason: evidenceStatus === 'available'
        ? null
        : String(claim?.unavailable_reason || 'INVALID_CITATION_CONTRACT'),
    }
  })
  return {
    ...value,
    article_id: articleId,
    claims: claims.length
      ? claims
      : unavailableChain(articleId, 'CLAIMS_UNAVAILABLE').claims,
  }
}

export function buildAssistantEvidenceContext(chain) {
  const claims = Array.isArray(chain?.claims) ? chain.claims : []
  if (!claims.length) {
    return ['- [unknown] 证据链 unavailable；不得基于标题生成确定性判断。']
  }
  return claims.map((claim) => {
    const kind = normalizeClaimType(claim.claim_type)
    if (claim.evidence_status !== 'available' || !claim.citations?.length) {
      return `- [${kind}] ${claim.text}；正文证据 unavailable（${claim.unavailable_reason || 'UNKNOWN'}），不得基于标题补足，也不得作为确定性结论。`
    }
    const anchors = claim.citations.map((citation) => `#${citation.anchor_id}`).join('、')
    return `- [${kind}] ${claim.text}；仅可引用 ${anchors}。`
  })
}
