import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'

import {
  CountryProfileCatalogApiError,
  CountryProfileCatalogContractError,
  createCountryProfileCatalogApi,
  createLatestCountryProfileCatalogRequest,
  sanitizeCountryProfileCatalog,
} from '../src/features/country-profiles/index.js'

const SECTION_FIELDS = Object.freeze({
  overview: [
    'overview.iso_alpha2',
    'overview.official_name',
    'overview.short_name',
    'overview.local_names',
    'overview.capital',
    'overview.geography',
  ],
  institutions: [
    'institutions.system_of_government',
    'institutions.constitution',
    'institutions.administrative_divisions',
  ],
  politics: [
    'politics.executive_leadership',
    'politics.legislature',
    'politics.elections',
    'politics.parties',
  ],
  law_policy: [
    'law_policy.constitution',
    'law_policy.legal_system',
    'law_policy.official_publications',
  ],
  economy: ['economy.gdp', 'economy.currency', 'economy.trade'],
  society: ['society.population', 'society.languages', 'society.human_development'],
  security: ['security.security_institutions', 'security.conflict_status'],
  external_relations: ['external_relations.memberships', 'external_relations.treaties'],
  environment: ['environment.climate', 'environment.emissions'],
  evidence_governance: [
    'evidence_governance.profile_owner',
    'evidence_governance.source_cutoff',
    'evidence_governance.license_review',
    'evidence_governance.last_review',
  ],
})

const REQUIRED_FIELDS = new Set([
  'overview.iso_alpha2',
  'overview.official_name',
  'overview.short_name',
  'evidence_governance.profile_owner',
  'evidence_governance.source_cutoff',
  'evidence_governance.license_review',
  'evidence_governance.last_review',
])

const REASON_CODES = [
  'PILOT_COUNTRIES_NOT_SELECTED',
  'COUNTRY_PROFILES_NOT_CONFIGURED',
  'SOURCE_AND_CUTOFF_EVIDENCE_NOT_CONFIGURED',
  'LICENSE_EVIDENCE_NOT_CONFIGURED',
  'OWNER_AND_REVIEW_NOT_CONFIGURED',
]

function catalogFixture() {
  const sections = Object.entries(SECTION_FIELDS).map(([sectionId, fieldIds]) => ({
    section_id: sectionId,
    title: sectionId.replaceAll('_', ' '),
    purpose: `Schema purpose for ${sectionId}.`,
    field_ids: [...fieldIds],
  }))
  const fields = Object.entries(SECTION_FIELDS).flatMap(([sectionId, fieldIds]) =>
    fieldIds.map((fieldId) => ({
      field_id: fieldId,
      section_id: sectionId,
      title: fieldId.split('.')[1].replaceAll('_', ' '),
      value_kind: fieldId.includes('owner') || fieldId.includes('review') ? 'review' : 'text',
      cardinality: REQUIRED_FIELDS.has(fieldId) ? 'one' : 'many',
      required_for_publish: REQUIRED_FIELDS.has(fieldId),
      evidence_required: true,
    })),
  )
  return {
    catalog_id: 'urn:globemind:country-profile:catalog:v1',
    schema_version: 'globemind.country-profile.v1',
    contract_version: '1.0.0',
    generated_at: '2026-08-09T18:40:00Z',
    available: false,
    operational_state: 'not_configured',
    live_checked: false,
    implementation_scope: 'schema_catalog_only',
    profile_schema: {
      schema_id: 'urn:globemind:country-profile:schema:v1',
      schema_version: 'globemind.country-profile.v1',
      country_identifier_standard: 'ISO 3166-1 alpha-2',
      country_identifier_pattern: '^[A-Z]{2}$',
      profile_identifier_format: 'urn:globemind:country-profile:{iso-alpha2-lower}:{sha256}',
      sections,
      fields,
      minimum_evidence: {
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
      },
    },
    profiles: [],
    reason_codes: [...REASON_CODES],
  }
}

function jsonResponse(raw, { status = 200, contentType = 'application/json' } = {}) {
  const text = typeof raw === 'string' ? raw : JSON.stringify(raw)
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        if (name.toLowerCase() === 'content-type') return contentType
        if (name.toLowerCase() === 'content-length') return String(new TextEncoder().encode(text).byteLength)
        return null
      },
    },
    text: async () => text,
  }
}

test('strict catalog sanitizer accepts only the schema-only empty-state contract', () => {
  const catalog = sanitizeCountryProfileCatalog(catalogFixture())

  assert.equal(catalog.available, false)
  assert.equal(catalog.operationalState, 'not_configured')
  assert.equal(catalog.liveChecked, false)
  assert.equal(catalog.implementationScope, 'schema_catalog_only')
  assert.deepEqual(catalog.profiles, [])
  assert.deepEqual(catalog.reasonCodes, REASON_CODES)
  assert.equal(catalog.profileSchema.sections.length, 10)
  assert.equal(catalog.profileSchema.fields.length, 32)
  assert.equal(catalog.profileSchema.minimumEvidence.expiredReviewPolicy, 'fail_closed')
  assert.equal(Object.isFrozen(catalog), true)
  assert.equal(Object.isFrozen(catalog.profileSchema.sections), true)
})

test('sanitizer fails closed on capability inflation, schema drift, and fact-shaped additions', () => {
  for (const mutate of [
    (value) => { value.available = true },
    (value) => { value.live_checked = true },
    (value) => { value.operational_state = 'available' },
    (value) => { value.implementation_scope = 'country_facts' },
    (value) => { value.profiles = [{ country_code: 'ZZ', population: 1 }] },
    (value) => { value.country_fact = 'not allowed' },
    (value) => { value.reason_codes.pop() },
    (value) => { value.profile_schema.fields[0].field_id = 'overview.population' },
    (value) => { value.profile_schema.sections[0].field_ids.push('overview.iso_alpha2') },
    (value) => { value.profile_schema.minimum_evidence.review_state = 'optional' },
  ]) {
    const payload = catalogFixture()
    mutate(payload)
    assert.throws(
      () => sanitizeCountryProfileCatalog(payload),
      (error) => error instanceof CountryProfileCatalogContractError,
    )
  }
})

test('public read-only API uses a bounded strict JSON transport and generic failures', async () => {
  const calls = []
  const api = createCountryProfileCatalogApi({
    apiOrigin: 'https://app.example.test',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return jsonResponse(catalogFixture(), { contentType: 'application/json; charset=utf-8' })
    },
  })

  const catalog = await api.fetchCatalog()
  assert.equal(catalog.profileSchema.fields.length, 32)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://app.example.test/api/authoritative-data/country-profiles/catalog')
  assert.equal(calls[0].options.method, 'GET')
  assert.equal(calls[0].options.cache, 'no-store')
  assert.equal(calls[0].options.credentials, 'same-origin')
  assert.equal(calls[0].options.body, undefined)
  assert.deepEqual(calls[0].options.headers, { Accept: 'application/json' })

  const duplicate = JSON.stringify(catalogFixture()).replace(
    '"available":false',
    '"available":false,"available":true',
  )
  for (const failingApi of [
    createCountryProfileCatalogApi({ fetchImpl: async () => jsonResponse(duplicate) }),
    createCountryProfileCatalogApi({ fetchImpl: async () => jsonResponse(catalogFixture(), { contentType: 'text/html' }) }),
    createCountryProfileCatalogApi({ fetchImpl: async () => jsonResponse('x'.repeat(262_145)) }),
    createCountryProfileCatalogApi({
      fetchImpl: async () => jsonResponse('postgresql' + '://user:secret@example.test/private', { status: 503 }),
    }),
    createCountryProfileCatalogApi({
      fetchImpl: async () => { throw new Error('https://user:secret@example.test/?token=leak') },
    }),
  ]) {
    await assert.rejects(
      failingApi.fetchCatalog(),
      (error) => error instanceof CountryProfileCatalogApiError
        && error.message === '国家档案目录暂不可用，请稍后重试。'
        && !error.message.includes('secret'),
    )
  }
})

test('take-latest controller aborts superseded reads and cannot publish stale data', async () => {
  const controller = createLatestCountryProfileCatalogRequest()
  let releaseFirst
  const first = controller.run((signal) => new Promise((resolve, reject) => {
    releaseFirst = () => resolve('stale')
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {
      name: 'AbortError',
    })))
  }))
  const second = controller.run(async () => 'latest')
  releaseFirst()

  assert.deepEqual(await second, { status: 'success', latest: true, value: 'latest' })
  assert.equal((await first).status, 'stale')

  let signal
  const cancelled = controller.run((requestSignal) => {
    signal = requestSignal
    return new Promise((_resolve, reject) => requestSignal.addEventListener('abort', () => {
      reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    }))
  })
  controller.cancel()
  assert.equal(signal.aborted, true)
  assert.equal((await cancelled).status, 'stale')
})

test('catalog page, public noindex route, and desktop/mobile navigation stay honest', async () => {
  const [view, router, navigation, preloaders] = await Promise.all([
    readFile(new URL('../src/views/CountryProfileCatalog.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/router/index.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/appNav.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/router/routePreloaders.js', import.meta.url), 'utf8'),
  ])
  const canonicalPaths = router.match(/const INDEXABLE_CANONICAL_PATHS = new Set\(\[([\s\S]*?)\]\)/)?.[1] || ''

  assert.match(view, /schema_catalog_only/)
  assert.match(view, /available=false/)
  assert.match(view, /operational_state=not_configured/)
  assert.match(view, /live_checked=false/)
  assert.match(view, /profiles=\[\]/)
  assert.match(view, /不包含任何国家事实/)
  assert.match(view, /仍未完成 CD-01/)
  assert.match(view, /createLatestCountryProfileCatalogRequest/)
  assert.doesNotMatch(view, /中国|美国|俄罗斯|人口为|GDP 为/)
  assert.match(
    router,
    /path: '\/country-profiles',[\s\S]*?component: routeViewLoaders\.countryProfileCatalog,[\s\S]*?meta: \{ hideNavbar: false, public: true \}/,
  )
  assert.doesNotMatch(canonicalPaths, /country-profiles/)
  assert.match(navigation, /@click="navigateTo\('\/country-profiles'\)"[\s\S]{0,160}>国家档案目录</)
  assert.match(navigation, /@click="mobileNavigateTo\('\/country-profiles'\)"[\s\S]{0,160}>国家档案目录</)
  assert.match(preloaders, /countryProfileCatalog: \(\) => import\('@\/views\/CountryProfileCatalog\.vue'\)/)

  const parsed = parse(view, { filename: 'CountryProfileCatalog.vue' })
  assert.equal(parsed.errors.length, 0)
  compileScript(parsed.descriptor, { id: 'country-profile-catalog' })
  const compiled = compileTemplate({
    filename: 'CountryProfileCatalog.vue',
    id: 'country-profile-catalog',
    source: parsed.descriptor.template?.content || '',
  })
  assert.equal(compiled.errors.length, 0)
})
