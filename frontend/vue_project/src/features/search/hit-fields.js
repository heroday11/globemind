export const SEARCH_HIT_SCHEMA_VERSION = 'search-hit-display-v1'

const TOP_LEVEL_KEYS = Object.freeze([
  'schema_version',
  'status',
  'offset_encoding',
  'coverage',
  'effective_search_fields',
  'alias_span_state',
  'relevance_score_state',
  'document_match_state',
  'reason_code',
  'spans',
])
const SPAN_KEYS = Object.freeze(['field', 'start', 'end'])
const ALLOWED_FIELDS = Object.freeze(['news.title', 'news.body'])
const FIELD_TO_SOURCE = Object.freeze({ title: 'news.title', abstract: 'news.body' })

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index])
}

function unavailable(reasonCode = 'SEARCH_HIT_CONTRACT_INVALID') {
  return {
    schemaVersion: SEARCH_HIT_SCHEMA_VERSION,
    status: 'unavailable',
    offsetEncoding: 'unicode_code_points',
    coverage: 'positive_literal_terms_in_returned_display_only',
    effectiveSearchFields: [],
    aliasSpanState: 'not_available',
    relevanceScoreState: 'not_available',
    documentMatchState: 'not_asserted',
    reasonCode,
    spans: [],
  }
}

export function normalizeSearchHitDisclosure(value, display = {}) {
  if (!exactKeys(value, TOP_LEVEL_KEYS)) return unavailable()
  if (
    value.schema_version !== SEARCH_HIT_SCHEMA_VERSION
    || !['available', 'no_display_span', 'unavailable'].includes(value.status)
    || value.offset_encoding !== 'unicode_code_points'
    || value.coverage !== 'positive_literal_terms_in_returned_display_only'
    || value.alias_span_state !== 'not_available'
    || value.relevance_score_state !== 'not_available'
    || value.document_match_state !== 'not_asserted'
    || !Array.isArray(value.effective_search_fields)
    || value.effective_search_fields.length > 2
    || new Set(value.effective_search_fields).size !== value.effective_search_fields.length
    || value.effective_search_fields.some((field) => !ALLOWED_FIELDS.includes(field))
    || !Array.isArray(value.spans)
    || value.spans.length > 64
  ) return unavailable()

  const validReason = (
    (value.status === 'available' && value.reason_code === 'DISPLAY_LITERAL_MATCHES_FOUND')
    || (
      value.status === 'no_display_span'
      && value.reason_code === 'NO_LITERAL_SPAN_IN_RETURNED_DISPLAY_TEXT'
    )
    || (
      value.status === 'unavailable'
      && ['NOT_A_SEARCH_RESPONSE', 'SEARCH_TERMS_NOT_AVAILABLE'].includes(value.reason_code)
    )
  )
  if (!validReason) return unavailable()
  if (value.status === 'available' ? value.spans.length === 0 : value.spans.length > 0) {
    return unavailable()
  }

  const displayText = {
    title: typeof display.title === 'string' ? display.title : '',
    abstract: typeof display.abstract === 'string' ? display.abstract : '',
  }
  const previous = { title: 0, abstract: 0 }
  let lastField = -1
  const spans = []
  for (const span of value.spans) {
    if (
      !exactKeys(span, SPAN_KEYS)
      || !['title', 'abstract'].includes(span.field)
      || !Number.isSafeInteger(span.start)
      || !Number.isSafeInteger(span.end)
      || span.start < 0
      || span.end <= span.start
      || span.end > Array.from(displayText[span.field]).length
      || span.start < previous[span.field]
      || !value.effective_search_fields.includes(FIELD_TO_SOURCE[span.field])
    ) return unavailable()
    const fieldIndex = span.field === 'title' ? 0 : 1
    if (fieldIndex < lastField) return unavailable()
    previous[span.field] = span.end
    lastField = fieldIndex
    spans.push({ field: span.field, start: span.start, end: span.end })
  }

  return {
    schemaVersion: value.schema_version,
    status: value.status,
    offsetEncoding: value.offset_encoding,
    coverage: value.coverage,
    effectiveSearchFields: [...value.effective_search_fields],
    aliasSpanState: value.alias_span_state,
    relevanceScoreState: value.relevance_score_state,
    documentMatchState: value.document_match_state,
    reasonCode: value.reason_code,
    spans,
  }
}

export function buildHighlightedSegments(text, disclosure, field) {
  const plainText = typeof text === 'string' ? text : ''
  const codePoints = Array.from(plainText)
  if (
    !disclosure
    || disclosure.status !== 'available'
    || !['title', 'abstract'].includes(field)
  ) return [{ text: plainText, matched: false }]

  const spans = disclosure.spans.filter((span) => span.field === field)
  if (!spans.length) return [{ text: plainText, matched: false }]
  const segments = []
  let cursor = 0
  for (const span of spans) {
    if (span.start > cursor) {
      segments.push({ text: codePoints.slice(cursor, span.start).join(''), matched: false })
    }
    segments.push({ text: codePoints.slice(span.start, span.end).join(''), matched: true })
    cursor = span.end
  }
  if (cursor < codePoints.length) {
    segments.push({ text: codePoints.slice(cursor).join(''), matched: false })
  }
  return segments
}
