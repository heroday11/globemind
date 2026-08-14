import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'

import {
  EntityGovernanceApiError,
  EntityGovernanceContractError,
  createEntityGovernanceApi,
  createLatestEntityGovernanceRequest,
  sanitizeEntityGovernanceStatus,
  sanitizeEntityGovernanceWorkspace,
} from '../src/features/entity-governance/index.js'

const CN = 'urn:globemind:entity:country:CN'
const US = 'urn:globemind:entity:country:US'
const RELATION = `urn:globemind:relation:${'1'.repeat(32)}`
const E1 = 'egv-0000000001-20260809T120000000000Z-aaaaaaaaaaaaaaaa'
const E2 = 'egv-0000000002-20260809T120000000001Z-bbbbbbbbbbbbbbbb'
const E3 = 'egv-0000000003-20260809T120000000002Z-cccccccccccccccc'

function evidenceFixture() {
  return {
    verification_status: 'verified',
    schema_version: 'source-snapshot-v1',
    snapshot_id: `article-17-${'d'.repeat(64)}`,
    article_id: 17,
    content_sha256: 'd'.repeat(64),
    parser_version: 'article-display-v1',
    verification_scope: 'normalized-body-content-address-and-reference-fields',
    source_metadata_verification: 'not_measured',
    body_persistence: 'forbidden',
  }
}

function statusFixture(overrides = {}) {
  return {
    schema_version: 'entity-governance-status-v2',
    storage_status: 'available',
    reason: null,
    root_initialized: true,
    event_count: 3,
    latest_event_id: E3,
    integrity_status: 'verified',
    mutation_status: 'ready',
    mutation_blocker: null,
    chain: 'sha256-and-hmac-sha256',
    append_semantics: 'no-replace-local-filesystem',
    hmac_key_id: 'unavailable',
    hmac_key_rotation: 'offline-controlled-migration-not-implemented',
    worm_status: 'unavailable',
    digital_signature_status: 'unavailable',
    institutional_directory_integration: 'unavailable',
    accuracy_claim: 'not_measured',
    seed_review_default: 'review_required',
    evidence_policy: 'verified-evidence-snapshot-required-for-mutations',
    review_expiry_policy: 'not_configured',
    ...overrides,
  }
}

function approvedEntity(entityId, name, decisionEventId) {
  return {
    entity_id: entityId,
    entity_type: 'country',
    canonical_names: { 'zh-Hans': name, en: name },
    review_status: 'approved',
    valid_from: null,
    valid_to: null,
    approved_aliases: [],
    decision_event_id: decisionEventId,
    decision_evidence: evidenceFixture(),
    merge_target_id: null,
    canonical_entity_id: entityId,
    split_into_entity_ids: [],
  }
}

function catalogFixture() {
  return {
    schema_version: 'entity-governance-catalog-v2',
    accuracy_claim: 'not_measured',
    projection_policy: 'approved-and-not-retracted-only',
    review_expiry_policy: 'not_configured',
    seed_inventory_scope: 'bounded-public-search-facade-probes',
    approved_entities: [
      approvedEntity(CN, '中国', E1),
      approvedEntity(US, '美国', E2),
    ],
    review_required_entities: [
      {
        entity_id: 'urn:globemind:entity:country:JP',
        entity_type: 'country',
        canonical_names: { 'zh-Hans': '日本', en: 'Japan' },
        review_status: 'review_required',
        source_catalog_version: 'entity-aliases-2026.08.09-v2',
        source_catalog_review_status: 'review_required',
        accuracy_claim: 'not_measured',
      },
    ],
    rejected_entity_ids: [],
    merge_decisions: [],
    split_decisions: [],
    event_count: 3,
    latest_event_id: E3,
    assurance: {
      worm: 'unavailable',
      digital_signature: 'unavailable',
      institutional_directory: 'unavailable',
    },
  }
}

function relationsFixture() {
  return {
    schema_version: 'entity-governance-relations-v2',
    projection_policy: 'approved-and-not-retracted-only',
    accuracy_claim: 'not_measured',
    review_expiry_policy: 'not_configured',
    relation_count: 1,
    items: [
      {
        relation_id: RELATION,
        subject_id: CN,
        predicate: 'urn:globemind:predicate:related-to',
        object_id: US,
        valid_from: '1979-01-01',
        valid_to: null,
        review_status: 'approved',
        added_event_id: E3,
        evidence: evidenceFixture(),
      },
    ],
  }
}

function eventFixture({
  eventId,
  sequence,
  occurredAt,
  eventType,
  record,
  hmac,
  previousEventId,
  previousRecord,
  previousHmac,
}) {
  return {
    schema_version: 'entity-governance-event-v1',
    event_id: eventId,
    sequence,
    occurred_at: occurredAt,
    actor_ref: 'user:7',
    event_type: eventType,
    reason: `Human review event ${sequence}`,
    evidence: evidenceFixture(),
    payload: {},
    previous_event_id: previousEventId,
    previous_record_sha256: previousRecord,
    previous_chain_hmac_sha256: previousHmac,
    record_sha256: record,
    chain_hmac_sha256: hmac,
  }
}

function historyFixture() {
  const first = eventFixture({
    eventId: E1,
    sequence: 1,
    occurredAt: '2026-08-09T12:00:00Z',
    eventType: 'entity.decision',
    record: '1'.repeat(64),
    hmac: 'a'.repeat(64),
    previousEventId: null,
    previousRecord: null,
    previousHmac: null,
  })
  const second = eventFixture({
    eventId: E2,
    sequence: 2,
    occurredAt: '2026-08-09T12:00:00.000001Z',
    eventType: 'entity.decision',
    record: '2'.repeat(64),
    hmac: 'b'.repeat(64),
    previousEventId: E1,
    previousRecord: first.record_sha256,
    previousHmac: first.chain_hmac_sha256,
  })
  const third = eventFixture({
    eventId: E3,
    sequence: 3,
    occurredAt: '2026-08-09T12:00:00.000002Z',
    eventType: 'relation.added',
    record: '3'.repeat(64),
    hmac: 'c'.repeat(64),
    previousEventId: E2,
    previousRecord: second.record_sha256,
    previousHmac: second.chain_hmac_sha256,
  })
  return {
    schema_version: 'entity-governance-history-v1',
    event_count: 3,
    items: [third, second, first],
    semantic_projection_verified: true,
    latest_event_id: E3,
    worm_status: 'unavailable',
    digital_signature_status: 'unavailable',
    visibility: 'authenticated-users',
    actor_reference_semantics: 'local-canonical-user-id-not-directory-resolved',
    reason_visibility: 'authenticated-users',
  }
}

function workspaceFixture() {
  return {
    status: statusFixture(),
    catalog: catalogFixture(),
    relations: relationsFixture(),
    history: historyFixture(),
  }
}

function response(payload, { status = 200, contentType = 'application/json' } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === 'content-type' ? contentType : '' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

test('strict workspace sanitizer accepts coherent approved projection and audit chain', () => {
  const workspace = sanitizeEntityGovernanceWorkspace(workspaceFixture())

  assert.equal(workspace.status.integrityStatus, 'verified')
  assert.equal(workspace.status.reviewExpiryPolicy, 'not_configured')
  assert.equal(workspace.catalog.reviewExpiryPolicy, 'not_configured')
  assert.equal(workspace.relations.reviewExpiryPolicy, 'not_configured')
  assert.equal(workspace.catalog.reviewRequiredEntities[0].reviewStatus, 'review_required')
  assert.equal(workspace.catalog.approvedEntities.length, 2)
  assert.equal(workspace.relations.items[0].validFrom, '1979-01-01')
  assert.equal(workspace.history.items[0].actorRef, 'user:7')
  assert.equal(workspace.history.items[0].evidence.bodyPersistence, 'forbidden')
  assert.equal(Object.isFrozen(workspace), true)
  assert.equal(Object.isFrozen(workspace.history.items), true)
})

test('sanitizer rejects false assurance, cross-resource references, and broken visible chain', () => {
  const measured = workspaceFixture()
  measured.status.accuracy_claim = '95_percent'
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(measured),
    (error) => error instanceof EntityGovernanceContractError && /保障边界/.test(error.message),
  )

  const missingExpiryPolicy = workspaceFixture()
  delete missingExpiryPolicy.catalog.review_expiry_policy
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(missingExpiryPolicy),
    (error) => error instanceof EntityGovernanceContractError && /\u76ee\u5f55\u58f0\u660e/.test(error.message),
  )

  const unknownEndpoint = workspaceFixture()
  unknownEndpoint.relations.items[0].object_id = 'urn:globemind:entity:country:ZZ'
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(unknownEndpoint),
    (error) => error instanceof EntityGovernanceContractError && /未批准实体/.test(error.message),
  )

  const brokenChain = workspaceFixture()
  brokenChain.history.items[0].previous_record_sha256 = 'f'.repeat(64)
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(brokenChain),
    (error) => error instanceof EntityGovernanceContractError && /链关系不连续/.test(error.message),
  )

  const leakedBody = workspaceFixture()
  leakedBody.history.items[0].evidence.normalized_body = 'body must never enter governance history'
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(leakedBody),
    (error) => error instanceof EntityGovernanceContractError && /证据验真状态/.test(error.message),
  )

  const overlappingReview = workspaceFixture()
  overlappingReview.catalog.rejected_entity_ids = [CN]
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(overlappingReview),
    (error) => error instanceof EntityGovernanceContractError && /审核状态重叠/.test(error.message),
  )
})

test('sanitizer verifies approved entity evidence, aliases, and identity decisions', () => {
  const badDecisionEvidence = workspaceFixture()
  badDecisionEvidence.catalog.approved_entities[0].decision_evidence.verification_status = 'claimed'
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(badDecisionEvidence),
    (error) => error instanceof EntityGovernanceContractError && /证据验真状态无效/.test(error.message),
  )

  const malformedAlias = workspaceFixture()
  malformedAlias.catalog.approved_entities[0].approved_aliases = [{
    value: 'PRC',
    language: true,
    kind: 'abbreviation',
    context_dependent: false,
    valid_from: null,
    valid_to: null,
    review_event_id: E3,
    evidence: evidenceFixture(),
  }]
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(malformedAlias),
    (error) => error instanceof EntityGovernanceContractError && /字符串无效/.test(error.message),
  )

  const unrecordedMerge = workspaceFixture()
  unrecordedMerge.catalog.approved_entities[0].merge_target_id = US
  unrecordedMerge.catalog.approved_entities[0].canonical_entity_id = US
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(unrecordedMerge),
    (error) => error instanceof EntityGovernanceContractError && /合并裁决/.test(error.message),
  )

  const duplicateSplit = workspaceFixture()
  duplicateSplit.catalog.approved_entities[0].split_into_entity_ids = [US, US]
  assert.throws(
    () => sanitizeEntityGovernanceWorkspace(duplicateSplit),
    (error) => error instanceof EntityGovernanceContractError && /拆分/.test(error.message),
  )
})

test('unavailable status remains displayable without requesting derived read models', async () => {
  const calls = []
  const unavailable = statusFixture({
    storage_status: 'unavailable',
    reason: 'ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE',
    root_initialized: false,
    event_count: null,
    latest_event_id: null,
    integrity_status: 'unavailable',
    mutation_status: 'blocked',
    mutation_blocker: 'ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE',
  })
  const api = createEntityGovernanceApi({
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return response(unavailable)
    },
    getAccessToken: () => 'access-token',
    apiOrigin: 'https://app.example.test',
  })

  const workspace = await api.loadWorkspace()
  assert.equal(workspace.status.storageStatus, 'unavailable')
  assert.equal(workspace.catalog, null)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://app.example.test/api/entity-governance/status')
  assert.equal(calls[0].options.method, 'GET')
  assert.equal(calls[0].options.cache, 'no-store')
})

test('read-only API authenticates every GET and sanitizes the combined response', async () => {
  const calls = []
  const fixtures = workspaceFixture()
  const payloads = new Map([
    ['/api/entity-governance/status', fixtures.status],
    ['/api/entity-governance/catalog', fixtures.catalog],
    ['/api/entity-governance/relations', fixtures.relations],
    ['/api/entity-governance/history?limit=50', fixtures.history],
  ])
  const api = createEntityGovernanceApi({
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      const path = new URL(url).pathname + new URL(url).search
      return response(payloads.get(path))
    },
    getAccessToken: () => 'access-token',
    apiOrigin: 'https://app.example.test',
  })

  const workspace = await api.loadWorkspace({ limit: 50 })
  assert.equal(workspace.relations.relationCount, 1)
  assert.equal(calls.length, 4)
  assert.ok(calls.every(({ options }) => options.method === 'GET'))
  assert.ok(calls.every(({ options }) => options.headers.Authorization === 'Bearer access-token'))
  assert.ok(calls.every(({ options }) => options.credentials === 'same-origin'))
  assert.ok(calls.every(({ options }) => options.cache === 'no-store'))
  assert.ok(calls.every(({ options }) => options.body === undefined))
})

test('API fails closed on missing auth, structured service failure, and non-JSON response', async () => {
  let calls = 0
  const noAuth = createEntityGovernanceApi({
    fetchImpl: async () => {
      calls += 1
      return response({})
    },
    getAccessToken: () => '',
  })
  await assert.rejects(
    noAuth.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError && error.code === 'AUTH_REQUIRED',
  )
  assert.equal(calls, 0)

  const unavailable = createEntityGovernanceApi({
    fetchImpl: async () => response({
      detail: {
        code: 'ENTITY_GOVERNANCE_UNAVAILABLE',
        reason_code: 'ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID',
        fallback: 'none',
      },
    }, { status: 503 }),
    getAccessToken: () => 'token',
  })
  await assert.rejects(
    unavailable.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError
      && error.status === 503
      && error.code === 'ENTITY_GOVERNANCE_UNAVAILABLE'
      && error.message === 'ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID',
  )

  const html = createEntityGovernanceApi({
    fetchImpl: async () => response('<html>', { contentType: 'text/html' }),
    getAccessToken: () => 'token',
  })
  await assert.rejects(
    html.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError
      && error.code === 'INVALID_RESPONSE_TYPE',
  )

  const jsonp = createEntityGovernanceApi({
    fetchImpl: async () => response({}, { contentType: 'application/jsonp' }),
    getAccessToken: () => 'token',
  })
  await assert.rejects(
    jsonp.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError
      && error.code === 'INVALID_RESPONSE_TYPE',
  )

  const unstructured = createEntityGovernanceApi({
    fetchImpl: async () => response({
      detail: 'postgresql' + '://user:secret@example.test/private',
    }, { status: 500 }),
    getAccessToken: () => 'token',
  })
  await assert.rejects(
    unstructured.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError
      && error.message === '读取实体治理状态失败'
      && !error.message.includes('secret'),
  )

  const network = createEntityGovernanceApi({
    fetchImpl: async () => {
      throw new Error('https://user:secret@example.test/private?token=leak')
    },
    getAccessToken: () => 'token',
  })
  await assert.rejects(
    network.loadWorkspace(),
    (error) => error instanceof EntityGovernanceApiError
      && error.code === 'NETWORK_ERROR'
      && error.message === '读取实体治理状态失败'
      && !error.message.includes('secret'),
  )
})

test('latest request controller aborts superseded reads and never commits stale data', async () => {
  const controller = createLatestEntityGovernanceRequest()
  let releaseFirst
  const first = controller.run((signal) => new Promise((resolve, reject) => {
    releaseFirst = () => resolve('stale-value')
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {
      name: 'AbortError',
    })))
  }))
  const second = controller.run(async () => 'current-value')
  releaseFirst()

  assert.deepEqual(await first, { status: 'stale' })
  assert.deepEqual(await second, { status: 'success', value: 'current-value' })
})

test('status sanitizer preserves the honest empty-ledger zero-write semantics', () => {
  const status = sanitizeEntityGovernanceStatus(statusFixture({
    root_initialized: false,
    event_count: 0,
    latest_event_id: null,
  }))
  assert.equal(status.storageStatus, 'available')
  assert.equal(status.integrityStatus, 'verified')
  assert.equal(status.rootInitialized, false)
  assert.equal(status.eventCount, 0)
})

test('standalone Vue view compiles and states read-only trust and audit boundaries', async () => {
  const source = await readFile(
    new URL('../src/views/EntityGovernance.vue', import.meta.url),
    'utf8',
  )
  const parsed = parse(source, { filename: 'EntityGovernance.vue' })
  assert.equal(parsed.errors.length, 0)
  assert.doesNotThrow(() => compileScript(parsed.descriptor, { id: 'entity-governance' }))
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    filename: 'EntityGovernance.vue',
    id: 'entity-governance',
  })
  assert.equal(template.errors.length, 0)
  assert.match(source, /不提供写入入口/)
  assert.match(source, /not_measured/)
  assert.match(source, /不是 WORM 或数字签名/)
  assert.match(source, /actor_ref 与裁决理由/)
  assert.match(source, /在线轮换不可用/)
  assert.match(source, /审批到期复核策略尚未配置/)
  assert.match(source, /不代表审批仍在有效期/)
  assert.match(source, /已批准别名/)
  assert.doesNotMatch(source, /v-html/)
  assert.doesNotMatch(source, /method:\s*['"]POST['"]/)
})
