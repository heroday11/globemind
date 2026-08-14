const CLAIM_ID_PATTERN = /^sgc_[0-9a-f]{64}$/
const KNOWN_REASON_CODES = new Set([
  'GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE',
  'GRAPH_LAYOUT_EDGE_NOT_EVIDENCE',
])

function failClosedClaim() {
  return Object.freeze({
    claim_id: null,
    citation_locator: null,
    citation_status: 'unavailable',
    reason_code: 'GRAPH_RELATION_CLAIM_CONTRACT_MISSING',
    unknown_gate: 'explicit_unknown',
    usable_as_fact: false,
  })
}

function boundedIdentity(value, maximum = 256) {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (
    !normalized
    || normalized.length > maximum
    || Array.from(normalized).some((character) => {
      const code = character.codePointAt(0)
      return code < 32 || code === 127
    })
  ) return null
  return normalized
}

function fnv1a32(value) {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash.toString(16).padStart(8, '0')
}

function stableLayoutClaimId(parts) {
  const canonical = JSON.stringify(parts)
  const digest = Array.from({ length: 8 }, (_, index) => fnv1a32(`${index}\u001f${canonical}`)).join('')
  return `sgc_${digest}`
}

export function normalizeStoryRelationClaim(value) {
  if (
    !value
    || typeof value !== 'object'
    || !CLAIM_ID_PATTERN.test(String(value.claim_id || ''))
    || value.citation_locator !== null
    || value.citation_status !== 'unavailable'
    || !KNOWN_REASON_CODES.has(value.reason_code)
    || value.unknown_gate !== 'explicit_unknown'
    || value.usable_as_fact !== false
  ) {
    return failClosedClaim()
  }
  return Object.freeze({
    claim_id: value.claim_id,
    citation_locator: null,
    citation_status: 'unavailable',
    reason_code: value.reason_code,
    unknown_gate: 'explicit_unknown',
    usable_as_fact: false,
  })
}

export function buildLayoutStoryRelationClaim({
  graphScopeId,
  fromId,
  toId,
  relationKind,
}) {
  const identity = [
    boundedIdentity(graphScopeId),
    boundedIdentity(fromId),
    boundedIdentity(toId),
    boundedIdentity(relationKind, 96),
  ]
  if (identity.some((value) => value === null)) return failClosedClaim()
  return Object.freeze({
    claim_id: stableLayoutClaimId(['globemind.story-layout-claim.v1', ...identity]),
    citation_locator: null,
    citation_status: 'unavailable',
    reason_code: 'GRAPH_LAYOUT_EDGE_NOT_EVIDENCE',
    unknown_gate: 'explicit_unknown',
    usable_as_fact: false,
  })
}
