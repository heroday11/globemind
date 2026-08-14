const EXPECTED_SECTION_FIELDS = Object.freeze({
  overview: Object.freeze([
    'overview.iso_alpha2',
    'overview.official_name',
    'overview.short_name',
    'overview.local_names',
    'overview.capital',
    'overview.geography',
  ]),
  institutions: Object.freeze([
    'institutions.system_of_government',
    'institutions.constitution',
    'institutions.administrative_divisions',
  ]),
  politics: Object.freeze([
    'politics.executive_leadership',
    'politics.legislature',
    'politics.elections',
    'politics.parties',
  ]),
  law_policy: Object.freeze([
    'law_policy.constitution',
    'law_policy.legal_system',
    'law_policy.official_publications',
  ]),
  economy: Object.freeze(['economy.gdp', 'economy.currency', 'economy.trade']),
  society: Object.freeze([
    'society.population',
    'society.languages',
    'society.human_development',
  ]),
  security: Object.freeze([
    'security.security_institutions',
    'security.conflict_status',
  ]),
  external_relations: Object.freeze([
    'external_relations.memberships',
    'external_relations.treaties',
  ]),
  environment: Object.freeze(['environment.climate', 'environment.emissions']),
  evidence_governance: Object.freeze([
    'evidence_governance.profile_owner',
    'evidence_governance.source_cutoff',
    'evidence_governance.license_review',
    'evidence_governance.last_review',
  ]),
})

const EXPECTED_SECTION_IDS = Object.freeze(Object.keys(EXPECTED_SECTION_FIELDS))
const EXPECTED_FIELD_IDS = Object.freeze(Object.values(EXPECTED_SECTION_FIELDS).flat())
const REQUIRED_FIELD_IDS = new Set([
  'overview.iso_alpha2',
  'overview.official_name',
  'overview.short_name',
  'evidence_governance.profile_owner',
  'evidence_governance.source_cutoff',
  'evidence_governance.license_review',
  'evidence_governance.last_review',
])

export const COUNTRY_PROFILE_REASON_CODES = Object.freeze([
  'PILOT_COUNTRIES_NOT_SELECTED',
  'COUNTRY_PROFILES_NOT_CONFIGURED',
  'SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED',
  'LICENSE_EVIDENCE_NOT_CONFIGURED',
  'OWNER_AND_REVIEW_NOT_CONFIGURED',
])

const EXPECTED_EVIDENCE = Object.freeze({
  source_locator: 'absolute_https_url',
  source_authority: 'required',
  source_retrieved_at: 'required_utc_datetime',
  source_cutoff: 'required_utc_datetime_or_period',
  future_source_cutoff_policy: 'fail_closed',
  license_state: 'verified_or_restricted',
  owner_role: 'country-data-stewardship',
  owner_identifier: 'required_stable_identifier',
  review_state: 'approved',
  reviewer_identifier: 'required_stable_identifier',
  reviewed_at: 'required_utc_datetime',
  future_review_policy: 'fail_closed',
  review_expires_at: 'required_future_utc_datetime',
  expired_review_policy: 'fail_closed',
  invalid_evidence_policy: 'fail_closed',
})

const VALUE_KINDS = new Set([
  'identifier',
  'text',
  'classification',
  'quantity',
  'date',
  'relation',
  'document',
  'review',
])
const CARDINALITIES = new Set(['one', 'zero_or_one', 'many'])

export class CountryProfileCatalogContractError extends Error {
  constructor(path, reason) {
    super(`国家档案目录响应不符合受信契约：${path} ${reason}`)
    this.name = 'CountryProfileCatalogContractError'
    this.path = path
  }
}

function fail(path, reason) {
  throw new CountryProfileCatalogContractError(path, reason)
}

function record(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(path, '必须是对象')
  }
  return value
}

function exactKeys(value, keys, path) {
  record(value, path)
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(path, '字段集合与版本化契约不一致')
  }
}

function literal(value, expected, path) {
  if (value !== expected) fail(path, `必须为 ${String(expected)}`)
  return value
}

function boundedString(value, path, { max = 400, pattern } = {}) {
  if (typeof value !== 'string' || value.length < 1 || value.length > max) {
    fail(path, `必须是 1..${max} 字符的字符串`)
  }
  if (pattern && !pattern.test(value)) fail(path, '格式无效')
  return value
}

function utcTimestamp(value, path) {
  boundedString(value, path, { max: 64 })
  if (!/(?:Z|\+00:00)$/.test(value)) fail(path, '必须显式使用 UTC')
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) fail(path, '时间无效')
  return parsed.toISOString()
}

function exactArray(value, expected, path) {
  if (!Array.isArray(value)
    || value.length !== expected.length
    || value.some((item, index) => item !== expected[index])) {
    fail(path, '内容或顺序与版本化契约不一致')
  }
  return Object.freeze([...value])
}

function sanitizeEvidence(value) {
  exactKeys(value, Object.keys(EXPECTED_EVIDENCE), 'profile_schema.minimum_evidence')
  for (const [key, expected] of Object.entries(EXPECTED_EVIDENCE)) {
    literal(value[key], expected, `profile_schema.minimum_evidence.${key}`)
  }
  return Object.freeze({
    sourceLocator: value.source_locator,
    sourceAuthority: value.source_authority,
    sourceRetrievedAt: value.source_retrieved_at,
    sourceCutoff: value.source_cutoff,
    futureSourceCutoffPolicy: value.future_source_cutoff_policy,
    licenseState: value.license_state,
    ownerRole: value.owner_role,
    ownerIdentifier: value.owner_identifier,
    reviewState: value.review_state,
    reviewerIdentifier: value.reviewer_identifier,
    reviewedAt: value.reviewed_at,
    futureReviewPolicy: value.future_review_policy,
    reviewExpiresAt: value.review_expires_at,
    expiredReviewPolicy: value.expired_review_policy,
    invalidEvidencePolicy: value.invalid_evidence_policy,
  })
}

function sanitizeSections(value) {
  if (!Array.isArray(value) || value.length !== EXPECTED_SECTION_IDS.length) {
    fail('profile_schema.sections', '必须完整列出十个 v1 section')
  }
  return Object.freeze(value.map((section, index) => {
    const path = `profile_schema.sections[${index}]`
    exactKeys(section, ['section_id', 'title', 'purpose', 'field_ids'], path)
    const sectionId = literal(section.section_id, EXPECTED_SECTION_IDS[index], `${path}.section_id`)
    const fieldIds = exactArray(
      section.field_ids,
      EXPECTED_SECTION_FIELDS[sectionId],
      `${path}.field_ids`,
    )
    return Object.freeze({
      sectionId,
      title: boundedString(section.title, `${path}.title`, { max: 120 }),
      purpose: boundedString(section.purpose, `${path}.purpose`, { max: 400 }),
      fieldIds,
    })
  }))
}

function sanitizeFields(value) {
  if (!Array.isArray(value) || value.length !== EXPECTED_FIELD_IDS.length) {
    fail('profile_schema.fields', '必须完整列出 32 个 v1 field')
  }
  return Object.freeze(value.map((field, index) => {
    const path = `profile_schema.fields[${index}]`
    exactKeys(field, [
      'field_id',
      'section_id',
      'title',
      'value_kind',
      'cardinality',
      'required_for_publish',
      'evidence_required',
    ], path)
    const fieldId = literal(field.field_id, EXPECTED_FIELD_IDS[index], `${path}.field_id`)
    const sectionId = fieldId.split('.', 1)[0]
    literal(field.section_id, sectionId, `${path}.section_id`)
    if (!VALUE_KINDS.has(field.value_kind)) fail(`${path}.value_kind`, '枚举无效')
    if (!CARDINALITIES.has(field.cardinality)) fail(`${path}.cardinality`, '枚举无效')
    literal(
      field.required_for_publish,
      REQUIRED_FIELD_IDS.has(fieldId),
      `${path}.required_for_publish`,
    )
    literal(field.evidence_required, true, `${path}.evidence_required`)
    return Object.freeze({
      fieldId,
      sectionId,
      title: boundedString(field.title, `${path}.title`, { max: 120 }),
      valueKind: field.value_kind,
      cardinality: field.cardinality,
      requiredForPublish: field.required_for_publish,
      evidenceRequired: true,
    })
  }))
}

function sanitizeSchema(value) {
  exactKeys(value, [
    'schema_id',
    'schema_version',
    'country_identifier_standard',
    'country_identifier_pattern',
    'profile_identifier_format',
    'sections',
    'fields',
    'minimum_evidence',
  ], 'profile_schema')
  literal(value.schema_id, 'urn:globemind:country-profile:schema:v1', 'profile_schema.schema_id')
  literal(value.schema_version, 'globemind.country-profile.v1', 'profile_schema.schema_version')
  literal(
    value.country_identifier_standard,
    'ISO 3166-1 alpha-2',
    'profile_schema.country_identifier_standard',
  )
  literal(value.country_identifier_pattern, '^[A-Z]{2}$', 'profile_schema.country_identifier_pattern')
  literal(
    value.profile_identifier_format,
    'urn:globemind:country-profile:{iso-alpha2-lower}:{sha256}',
    'profile_schema.profile_identifier_format',
  )
  const sections = sanitizeSections(value.sections)
  const fields = sanitizeFields(value.fields)
  return Object.freeze({
    schemaId: value.schema_id,
    schemaVersion: value.schema_version,
    countryIdentifierStandard: value.country_identifier_standard,
    countryIdentifierPattern: value.country_identifier_pattern,
    profileIdentifierFormat: value.profile_identifier_format,
    sections,
    fields,
    minimumEvidence: sanitizeEvidence(value.minimum_evidence),
  })
}

export function sanitizeCountryProfileCatalog(value) {
  exactKeys(value, [
    'catalog_id',
    'schema_version',
    'contract_version',
    'generated_at',
    'available',
    'operational_state',
    'live_checked',
    'implementation_scope',
    'profile_schema',
    'profiles',
    'reason_codes',
  ], 'catalog')
  literal(value.catalog_id, 'urn:globemind:country-profile:catalog:v1', 'catalog_id')
  literal(value.schema_version, 'globemind.country-profile.v1', 'schema_version')
  literal(value.contract_version, '1.0.0', 'contract_version')
  literal(value.available, false, 'available')
  literal(value.operational_state, 'not_configured', 'operational_state')
  literal(value.live_checked, false, 'live_checked')
  literal(value.implementation_scope, 'schema_catalog_only', 'implementation_scope')
  if (!Array.isArray(value.profiles) || value.profiles.length !== 0) {
    fail('profiles', '必须为空数组；本契约不承载国家事实')
  }
  const reasonCodes = exactArray(value.reason_codes, COUNTRY_PROFILE_REASON_CODES, 'reason_codes')
  return Object.freeze({
    catalogId: value.catalog_id,
    schemaVersion: value.schema_version,
    contractVersion: value.contract_version,
    generatedAt: utcTimestamp(value.generated_at, 'generated_at'),
    available: false,
    operationalState: 'not_configured',
    liveChecked: false,
    implementationScope: 'schema_catalog_only',
    profileSchema: sanitizeSchema(value.profile_schema),
    profiles: Object.freeze([]),
    reasonCodes,
  })
}

export function createLatestCountryProfileCatalogRequest() {
  let generation = 0
  let active = null

  return Object.freeze({
    async run(operation) {
      if (typeof operation !== 'function') throw new TypeError('operation must be a function')
      generation += 1
      const id = generation
      active?.abort()
      const controller = new AbortController()
      active = controller
      try {
        const value = await operation(controller.signal)
        if (id !== generation) return { status: 'stale', latest: false }
        return { status: 'success', latest: true, value }
      } catch (error) {
        if (id !== generation) return { status: 'stale', latest: false, error }
        if (error?.name === 'AbortError' || controller.signal.aborted) {
          return { status: 'aborted', latest: true, error }
        }
        return { status: 'error', latest: true, error }
      } finally {
        if (active === controller) active = null
      }
    },
    cancel() {
      generation += 1
      active?.abort()
      active = null
    },
  })
}
