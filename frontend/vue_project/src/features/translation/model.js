const RESPONSE_KEYS = ['generated_at', 'provenance', 'schema_version', 'text']
const PROVENANCE_KEYS = [
  'backend',
  'human_review_state',
  'mode',
  'model_id',
  'persistence',
  'provider_scope',
  'quality_state',
  'source_language',
  'source_text_length',
  'source_text_sha256',
  'target_language',
  'terminology_version',
]

const MAX_SEGMENTS = 100
const MAX_SEGMENT_CHARACTERS = 6000
const MAX_TOTAL_CHARACTERS = 60_000
const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/+:-]{0,255}$/
const SHA256_PATTERN = /^[a-f0-9]{64}$/
const LANGUAGE_PATTERN = /^(?:und|[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*)$/
const UTC_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

function translationError(code) {
  const error = new Error(code)
  error.code = code
  return error
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(value, expectedKeys) {
  if (!isRecord(value)) return false
  const actual = Object.keys(value).sort()
  return actual.length === expectedKeys.length
    && actual.every((key, index) => key === expectedKeys[index])
}

function characterLength(value) {
  return Array.from(value).length
}

function hasUnsafeControl(value) {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0)
    return codePoint === 127
      || (codePoint >= 0xD800 && codePoint <= 0xDFFF)
      || (codePoint < 32 && codePoint !== 9 && codePoint !== 10 && codePoint !== 13)
  })
}

function normalizeSegment(value) {
  return typeof value === 'string' ? value.trim() : ''
}

export async function sha256Text(value, cryptoImpl = globalThis.crypto) {
  if (typeof value !== 'string' || !cryptoImpl?.subtle?.digest) {
    throw translationError('TRANSLATION_SOURCE_BINDING_UNAVAILABLE')
  }
  const digest = await cryptoImpl.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function normalizeMachineTranslationResponse(payload, expected) {
  if (!hasExactKeys(payload, RESPONSE_KEYS)) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }
  if (
    payload.schema_version !== 'globemind.news-translation.v1'
    || typeof payload.text !== 'string'
    || !payload.text.trim()
    || characterLength(payload.text) > 24_000
    || hasUnsafeControl(payload.text)
    || typeof payload.generated_at !== 'string'
    || !UTC_TIMESTAMP_PATTERN.test(payload.generated_at)
    || !Number.isFinite(Date.parse(payload.generated_at))
  ) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }

  const provenance = payload.provenance
  const sourceText = expected?.sourceText
  const sourceTextSha256 = expected?.sourceTextSha256
  const sourceLanguage = expected?.sourceLanguage || 'und'
  const targetLanguage = expected?.targetLanguage || 'zh-Hans'
  if (
    !hasExactKeys(provenance, PROVENANCE_KEYS)
    || typeof sourceText !== 'string'
    || !SHA256_PATTERN.test(String(sourceTextSha256 || ''))
    || provenance.mode !== 'machine_translation'
    || provenance.backend !== 'local-vllm-loopback'
    || !MODEL_ID_PATTERN.test(String(provenance.model_id || ''))
    || provenance.source_language !== sourceLanguage
    || provenance.target_language !== targetLanguage
    || provenance.source_text_sha256 !== sourceTextSha256
    || provenance.source_text_length !== characterLength(sourceText)
    || provenance.human_review_state !== 'not_reviewed'
    || provenance.quality_state !== 'not_measured'
    || provenance.terminology_version !== 'not_configured'
    || provenance.persistence !== 'not_persisted_by_endpoint'
    || provenance.provider_scope !== 'loopback_only'
  ) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }

  return Object.freeze({
    schema_version: payload.schema_version,
    generated_at: payload.generated_at,
    text: payload.text.trim(),
    provenance: Object.freeze({ ...provenance }),
  })
}

export function buildTranslationWorkload({ title = '', abstract = '', paragraphs = [] } = {}) {
  if (!Array.isArray(paragraphs)) {
    throw translationError('TRANSLATION_WORKLOAD_LIMIT_EXCEEDED')
  }
  const normalized = {
    title: normalizeSegment(title),
    abstract: normalizeSegment(abstract),
    paragraphs: paragraphs.map(normalizeSegment).filter(Boolean),
  }
  const segments = [normalized.title, normalized.abstract, ...normalized.paragraphs].filter(Boolean)
  const lengths = segments.map(characterLength)
  const totalCharacters = lengths.reduce((total, length) => total + length, 0)
  if (
    !segments.length
    || segments.length > MAX_SEGMENTS
    || lengths.some((length) => length > MAX_SEGMENT_CHARACTERS)
    || totalCharacters > MAX_TOTAL_CHARACTERS
    || segments.some(hasUnsafeControl)
  ) {
    throw translationError('TRANSLATION_WORKLOAD_LIMIT_EXCEEDED')
  }
  return Object.freeze({
    ...normalized,
    paragraphs: Object.freeze(normalized.paragraphs),
    segmentCount: segments.length,
    totalCharacters,
  })
}

export function summarizeMachineTranslationProvenance(receipts) {
  if (!Array.isArray(receipts) || !receipts.length || receipts.length > MAX_SEGMENTS) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }
  if (receipts.some((receipt) => (
    !hasExactKeys(receipt, PROVENANCE_KEYS)
    || receipt.mode !== 'machine_translation'
    || receipt.backend !== 'local-vllm-loopback'
    || !MODEL_ID_PATTERN.test(String(receipt.model_id || ''))
    || !isTranslationLanguage(receipt.source_language)
    || receipt.target_language !== 'zh-Hans'
    || !SHA256_PATTERN.test(String(receipt.source_text_sha256 || ''))
    || !Number.isInteger(receipt.source_text_length)
    || receipt.source_text_length < 1
    || receipt.source_text_length > MAX_SEGMENT_CHARACTERS
    || receipt.human_review_state !== 'not_reviewed'
    || receipt.quality_state !== 'not_measured'
    || receipt.terminology_version !== 'not_configured'
    || receipt.persistence !== 'not_persisted_by_endpoint'
    || receipt.provider_scope !== 'loopback_only'
  ))) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }
  return Object.freeze({
    mode: 'machine_translation',
    receipt_count: receipts.length,
    model_ids: Object.freeze([...new Set(receipts.map((receipt) => receipt.model_id))]),
    human_review_state: 'not_reviewed',
    quality_state: 'not_measured',
    terminology_version: 'not_configured',
    source_binding: 'sha256_verified_in_client',
    persistence: 'not_persisted_by_endpoint',
  })
}

export function databaseTranslationProvenance() {
  return Object.freeze({
    mode: 'database_translation',
    provenance_state: 'not_available',
    human_review_state: 'unknown',
    quality_state: 'not_measured',
    source_binding: 'not_available',
  })
}

export function translationDisclosure(provenance) {
  if (provenance?.mode === 'machine_translation') {
    const receipt = Number.isInteger(provenance.receipt_count)
      && provenance.receipt_count > 0
      && provenance.receipt_count <= MAX_SEGMENTS
      ? ` · ${provenance.receipt_count} 段来源哈希已在客户端核对`
      : ''
    return `机器翻译 · 未经人工复核 · 质量未测量${receipt}`
  }
  if (provenance?.mode === 'database_translation') {
    return '数据库既有译文 · provenance 未登记 · 人工复核状态未知 · 质量未测量'
  }
  return '翻译 provenance unavailable · 人工复核状态未知 · 质量未测量'
}

export function isTranslationLanguage(value) {
  return typeof value === 'string' && LANGUAGE_PATTERN.test(value)
}

export function isTranslationSourceText(value) {
  return typeof value === 'string'
    && Boolean(value.trim())
    && characterLength(value) <= MAX_SEGMENT_CHARACTERS
    && !hasUnsafeControl(value)
}
