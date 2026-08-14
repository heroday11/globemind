export const ENTITY_GOVERNANCE_SCHEMAS = Object.freeze({
  status: 'entity-governance-status-v2',
  catalog: 'entity-governance-catalog-v2',
  relations: 'entity-governance-relations-v2',
  history: 'entity-governance-history-v1',
  event: 'entity-governance-event-v1',
})

const ENTITY_URN = /^urn:globemind:entity:(country|person|organization|location):[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const RELATION_URN = /^urn:globemind:relation:[0-9a-f]{32}$/
const PREDICATE_URN = /^urn:globemind:predicate:[a-z0-9][a-z0-9._-]{0,95}$/
const EVENT_ID = /^egv-[0-9]{10}-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$/
const USER_REF = /^user:[1-9][0-9]*$/
const SHA256 = /^[0-9a-f]{64}$/
const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/
const LANGUAGE_TAG = /^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|und)$/
const EVENT_TIME = /^(?<second>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.(?<fraction>[0-9]{1,6}))?Z$/
const EVENT_TYPES = Object.freeze([
  'entity.decision',
  'alias.review',
  'relation.added',
  'relation.retracted',
  'merge.decision',
  'split.decision',
])
const ALIAS_KINDS = Object.freeze([
  'preferred_name',
  'formal_name',
  'abbreviation',
  'alternative_name',
  'transliterated_name',
  'historical_or_contextual_name',
])
const EVIDENCE_KEYS = Object.freeze([
  'article_id',
  'body_persistence',
  'content_sha256',
  'parser_version',
  'schema_version',
  'snapshot_id',
  'source_metadata_verification',
  'verification_scope',
  'verification_status',
])

export class EntityGovernanceContractError extends Error {
  constructor(path, reason) {
    super(`实体治理响应不符合受信契约：${path} ${reason}`)
    this.name = 'EntityGovernanceContractError'
    this.code = 'ENTITY_GOVERNANCE_CONTRACT_INVALID'
    this.path = path
  }
}

function fail(path, reason) {
  throw new EntityGovernanceContractError(path, reason)
}

function record(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(path, '必须是对象')
  return value
}

function text(value, path, { pattern, nullable = false, maximum = 4000 } = {}) {
  if (nullable && value === null) return null
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > maximum
    || value.trim() !== value
    || Array.from(value).some((character) => character.codePointAt(0) < 32 || character.codePointAt(0) === 127)
    || value.normalize('NFC') !== value
    || (pattern && !pattern.test(value))
  ) {
    fail(path, '字符串无效')
  }
  return value
}

function nullableText(value, path, options = {}) {
  return text(value, path, { ...options, nullable: true })
}

function integer(value, path, { minimum = 0, maximum = 10_000 } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    fail(path, '整数超出范围')
  }
  return value
}

function boolean(value, path) {
  if (typeof value !== 'boolean') fail(path, '必须是布尔值')
  return value
}

function enumValue(value, path, allowed) {
  if (!allowed.includes(value)) fail(path, '枚举值无效')
  return value
}

function boundedArray(value, path, maximum = 10_000) {
  if (!Array.isArray(value) || value.length > maximum) fail(path, '必须是有界数组')
  return value
}

function nullableDate(value, path) {
  if (value === null) return null
  const normalized = text(value, path, { pattern: DATE, maximum: 10 })
  const parsed = new Date(`${normalized}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== normalized) {
    fail(path, '日期无效')
  }
  return normalized
}

function interval(raw, path) {
  const validFrom = nullableDate(raw.valid_from, `${path}.valid_from`)
  const validTo = nullableDate(raw.valid_to, `${path}.valid_to`)
  if (validFrom && validTo && validFrom > validTo) fail(path, '有效期倒置')
  return { validFrom, validTo }
}

function governanceEventTime(value, path) {
  const normalized = text(value, path, { maximum: 32 })
  const match = EVENT_TIME.exec(normalized)
  if (!match || Number.isNaN(Date.parse(`${match.groups.second}Z`))) {
    fail(path, '治理事件时间无效')
  }
  return normalized
}

function governanceEventOrder(value) {
  const match = EVENT_TIME.exec(value)
  const baseMilliseconds = Date.parse(`${match.groups.second}Z`)
  const microseconds = Number((match.groups.fraction || '').padEnd(6, '0'))
  return baseMilliseconds * 1000 + microseconds
}

function governanceEventStamp(value) {
  const match = EVENT_TIME.exec(value)
  const compactSecond = match.groups.second.replaceAll('-', '').replaceAll(':', '')
  return `${compactSecond}${(match.groups.fraction || '').padEnd(6, '0')}Z`
}

function canonicalNames(value, path) {
  const raw = record(value, path)
  const entries = Object.entries(raw)
  if (!entries.length || entries.length > 20) fail(path, '规范名称集合无效')
  return Object.freeze(Object.fromEntries(entries.map(([language, name]) => [
    text(language, `${path}.language`, { pattern: LANGUAGE_TAG, maximum: 48 }),
    text(name, `${path}.${language}`, { maximum: 300 }),
  ])))
}

function evidence(value, path) {
  const raw = record(value, path)
  const keys = Object.keys(raw).sort()
  if (
    keys.length !== EVIDENCE_KEYS.length
    || keys.some((key, index) => key !== EVIDENCE_KEYS[index])
    || raw.verification_status !== 'verified'
    || raw.schema_version !== 'source-snapshot-v1'
    || raw.body_persistence !== 'forbidden'
    || raw.parser_version !== 'article-display-v1'
    || raw.verification_scope !== 'normalized-body-content-address-and-reference-fields'
    || raw.source_metadata_verification !== 'not_measured'
  ) {
    fail(path, '证据验真状态无效')
  }
  const articleId = integer(raw.article_id, `${path}.article_id`, {
    minimum: 1,
    maximum: Number.MAX_SAFE_INTEGER,
  })
  const digest = text(raw.content_sha256, `${path}.content_sha256`, { pattern: SHA256, maximum: 64 })
  const snapshotId = text(raw.snapshot_id, `${path}.snapshot_id`, { maximum: 160 })
  if (snapshotId !== `article-${articleId}-${digest}`) fail(path, '证据快照标识不自洽')
  return Object.freeze({
    snapshotId,
    articleId,
    contentSha256: digest,
    parserVersion: 'article-display-v1',
    verificationScope: raw.verification_scope,
    sourceMetadataVerification: raw.source_metadata_verification,
    bodyPersistence: 'forbidden',
  })
}

export function sanitizeEntityGovernanceStatus(value) {
  const raw = record(value, 'status')
  if (raw.schema_version !== ENTITY_GOVERNANCE_SCHEMAS.status) fail('status.schema_version', '版本不受支持')
  const storageStatus = enumValue(raw.storage_status, 'status.storage_status', ['available', 'unavailable'])
  const integrityStatus = enumValue(raw.integrity_status, 'status.integrity_status', [
    'verified',
    'unavailable',
    'failed_closed',
  ])
  const eventCount = raw.event_count === null ? null : integer(raw.event_count, 'status.event_count')
  const latestEventId = nullableText(raw.latest_event_id, 'status.latest_event_id', { pattern: EVENT_ID, maximum: 64 })
  const rootInitialized = boolean(raw.root_initialized, 'status.root_initialized')
  const reason = nullableText(raw.reason, 'status.reason', { maximum: 200 })
  const mutationStatus = enumValue(raw.mutation_status, 'status.mutation_status', [
    'ready',
    'blocked',
  ])
  const mutationBlocker = nullableText(raw.mutation_blocker, 'status.mutation_blocker', {
    maximum: 200,
  })
  if (storageStatus === 'available') {
    if (integrityStatus !== 'verified' || eventCount === null || reason !== null) fail('status', '可用状态不自洽')
  } else if (eventCount !== null || latestEventId !== null || reason === null || integrityStatus === 'verified') {
    fail('status', '不可用状态不自洽')
  }
  if (eventCount === 0 && latestEventId !== null) fail('status.latest_event_id', '空账本不能有最新事件')
  if (eventCount && latestEventId === null) fail('status.latest_event_id', '非空账本缺少最新事件')
  if (!rootInitialized && eventCount !== null && eventCount !== 0) {
    fail('status.root_initialized', '未初始化根不能包含事件')
  }
  if ((mutationStatus === 'ready') !== (storageStatus === 'available' && mutationBlocker === null)) {
    fail('status.mutation_status', '写入就绪声明不自洽')
  }
  if (mutationStatus === 'blocked' && mutationBlocker === null) {
    fail('status.mutation_blocker', '受阻写入缺少原因')
  }
  if (
    raw.chain !== 'sha256-and-hmac-sha256'
    || raw.append_semantics !== 'no-replace-local-filesystem'
    || raw.hmac_key_id !== 'unavailable'
    || raw.hmac_key_rotation !== 'offline-controlled-migration-not-implemented'
    || raw.worm_status !== 'unavailable'
    || raw.digital_signature_status !== 'unavailable'
    || raw.institutional_directory_integration !== 'unavailable'
    || raw.accuracy_claim !== 'not_measured'
    || raw.seed_review_default !== 'review_required'
    || raw.evidence_policy !== 'verified-evidence-snapshot-required-for-mutations'
    || raw.review_expiry_policy !== 'not_configured'
  ) {
    fail('status', '保障边界声明无效')
  }
  return Object.freeze({
    storageStatus,
    integrityStatus,
    eventCount,
    latestEventId,
    rootInitialized,
    reason,
    mutationStatus,
    mutationBlocker,
    hmacKeyRotation: raw.hmac_key_rotation,
    accuracyClaim: 'not_measured',
    reviewExpiryPolicy: 'not_configured',
  })
}

function sanitizeReviewSeed(value, path) {
  const raw = record(value, path)
  if (raw.review_status !== 'review_required' || raw.accuracy_claim !== 'not_measured') {
    fail(path, '种子审核状态无效')
  }
  const entityId = text(raw.entity_id, `${path}.entity_id`, { pattern: ENTITY_URN, maximum: 180 })
  const entityType = enumValue(raw.entity_type, `${path}.entity_type`, ['country', 'person', 'organization', 'location'])
  if (!entityId.startsWith(`urn:globemind:entity:${entityType}:`)) fail(path, '实体类型与 ID 不一致')
  return Object.freeze({
    entityId,
    entityType,
    canonicalNames: canonicalNames(raw.canonical_names, `${path}.canonical_names`),
    reviewStatus: 'review_required',
    sourceCatalogVersion: text(raw.source_catalog_version, `${path}.source_catalog_version`, { maximum: 200 }),
    sourceCatalogReviewStatus: enumValue(
      raw.source_catalog_review_status,
      `${path}.source_catalog_review_status`,
      ['approved', 'review_required'],
    ),
  })
}

function sanitizeApprovedEntity(value, path) {
  const raw = record(value, path)
  if (raw.review_status !== 'approved') fail(path, '实体未批准')
  const validity = interval(raw, path)
  const entityId = text(raw.entity_id, `${path}.entity_id`, { pattern: ENTITY_URN, maximum: 180 })
  const entityType = enumValue(raw.entity_type, `${path}.entity_type`, ['country', 'person', 'organization', 'location'])
  if (!entityId.startsWith(`urn:globemind:entity:${entityType}:`)) fail(path, '实体类型与 ID 不一致')
  const approvedAliases = Object.freeze(
    boundedArray(raw.approved_aliases, `${path}.approved_aliases`, 500)
      .map((item, index) => {
        const aliasPath = `${path}.approved_aliases[${index}]`
        const alias = record(item, aliasPath)
        const aliasValidity = interval(alias, aliasPath)
        return Object.freeze({
          value: text(alias.value, `${aliasPath}.value`, { maximum: 300 }),
          language: text(alias.language, `${aliasPath}.language`, {
            pattern: LANGUAGE_TAG,
            maximum: 48,
          }),
          kind: enumValue(alias.kind, `${aliasPath}.kind`, ALIAS_KINDS),
          contextDependent: boolean(alias.context_dependent, `${aliasPath}.context_dependent`),
          validFrom: aliasValidity.validFrom,
          validTo: aliasValidity.validTo,
          reviewEventId: text(alias.review_event_id, `${aliasPath}.review_event_id`, {
            pattern: EVENT_ID,
            maximum: 64,
          }),
          evidence: evidence(alias.evidence, `${aliasPath}.evidence`),
        })
      }),
  )
  if (new Set(approvedAliases.map((item) => item.value.toLowerCase())).size !== approvedAliases.length) {
    fail(`${path}.approved_aliases`, '批准别名重复')
  }
  const splitIntoEntityIds = Object.freeze(
    boundedArray(raw.split_into_entity_ids, `${path}.split_into_entity_ids`, 20)
      .map((item, index) => text(item, `${path}.split_into_entity_ids[${index}]`, { pattern: ENTITY_URN, maximum: 180 })),
  )
  if (
    new Set(splitIntoEntityIds).size !== splitIntoEntityIds.length
    || splitIntoEntityIds.includes(entityId)
  ) {
    fail(`${path}.split_into_entity_ids`, '拆分目标重复或包含源实体')
  }
  return Object.freeze({
    entityId,
    entityType,
    canonicalNames: canonicalNames(raw.canonical_names, `${path}.canonical_names`),
    validFrom: validity.validFrom,
    validTo: validity.validTo,
    canonicalEntityId: text(raw.canonical_entity_id, `${path}.canonical_entity_id`, { pattern: ENTITY_URN, maximum: 180 }),
    mergeTargetId: nullableText(raw.merge_target_id, `${path}.merge_target_id`, { pattern: ENTITY_URN, maximum: 180 }),
    splitIntoEntityIds,
    approvedAliases,
    decisionEventId: text(raw.decision_event_id, `${path}.decision_event_id`, { pattern: EVENT_ID, maximum: 64 }),
    decisionEvidence: evidence(raw.decision_evidence, `${path}.decision_evidence`),
  })
}

function sanitizeMergeDecision(value, path) {
  const raw = record(value, path)
  const sourceEntityId = text(raw.source_entity_id, `${path}.source_entity_id`, {
    pattern: ENTITY_URN,
    maximum: 180,
  })
  const targetEntityId = text(raw.target_entity_id, `${path}.target_entity_id`, {
    pattern: ENTITY_URN,
    maximum: 180,
  })
  if (sourceEntityId === targetEntityId) fail(path, '合并裁决不能自环')
  return Object.freeze({
    sourceEntityId,
    targetEntityId,
    decisionEventId: text(raw.decision_event_id, `${path}.decision_event_id`, {
      pattern: EVENT_ID,
      maximum: 64,
    }),
  })
}

function sanitizeSplitDecision(value, path) {
  const raw = record(value, path)
  const sourceEntityId = text(raw.source_entity_id, `${path}.source_entity_id`, {
    pattern: ENTITY_URN,
    maximum: 180,
  })
  const resultingEntityIds = Object.freeze(
    boundedArray(raw.resulting_entity_ids, `${path}.resulting_entity_ids`, 20)
      .map((item, index) => text(item, `${path}.resulting_entity_ids[${index}]`, {
        pattern: ENTITY_URN,
        maximum: 180,
      })),
  )
  if (
    resultingEntityIds.length < 2
    || new Set(resultingEntityIds).size !== resultingEntityIds.length
    || resultingEntityIds.includes(sourceEntityId)
  ) {
    fail(path, '拆分裁决目标无效')
  }
  return Object.freeze({
    sourceEntityId,
    resultingEntityIds,
    decisionEventId: text(raw.decision_event_id, `${path}.decision_event_id`, {
      pattern: EVENT_ID,
      maximum: 64,
    }),
  })
}

function sanitizeCatalog(value) {
  const raw = record(value, 'catalog')
  if (
    raw.schema_version !== ENTITY_GOVERNANCE_SCHEMAS.catalog
    || raw.accuracy_claim !== 'not_measured'
    || raw.projection_policy !== 'approved-and-not-retracted-only'
    || raw.review_expiry_policy !== 'not_configured'
    || raw.seed_inventory_scope !== 'bounded-public-search-facade-probes'
  ) {
    fail('catalog', '目录声明无效')
  }
  const approvedEntities = Object.freeze(
    boundedArray(raw.approved_entities, 'catalog.approved_entities')
      .map((item, index) => sanitizeApprovedEntity(item, `catalog.approved_entities[${index}]`)),
  )
  const reviewRequiredEntities = Object.freeze(
    boundedArray(raw.review_required_entities, 'catalog.review_required_entities')
      .map((item, index) => sanitizeReviewSeed(item, `catalog.review_required_entities[${index}]`)),
  )
  const rejectedEntityIds = Object.freeze(
    boundedArray(raw.rejected_entity_ids, 'catalog.rejected_entity_ids')
      .map((item, index) => text(item, `catalog.rejected_entity_ids[${index}]`, { pattern: ENTITY_URN, maximum: 180 })),
  )
  const identifiers = [
    ...approvedEntities.map((item) => item.entityId),
    ...reviewRequiredEntities.map((item) => item.entityId),
    ...rejectedEntityIds,
  ]
  if (new Set(identifiers).size !== identifiers.length) fail('catalog', '实体审核状态重叠或重复')
  const mergeDecisions = Object.freeze(
    boundedArray(raw.merge_decisions, 'catalog.merge_decisions')
      .map((item, index) => sanitizeMergeDecision(item, `catalog.merge_decisions[${index}]`)),
  )
  const splitDecisions = Object.freeze(
    boundedArray(raw.split_decisions, 'catalog.split_decisions')
      .map((item, index) => sanitizeSplitDecision(item, `catalog.split_decisions[${index}]`)),
  )
  if (new Set(mergeDecisions.map((item) => item.sourceEntityId)).size !== mergeDecisions.length) {
    fail('catalog.merge_decisions', '合并裁决源实体重复')
  }
  if (new Set(splitDecisions.map((item) => item.sourceEntityId)).size !== splitDecisions.length) {
    fail('catalog.split_decisions', '拆分裁决源实体重复')
  }
  const assurance = record(raw.assurance, 'catalog.assurance')
  if (
    assurance.worm !== 'unavailable'
    || assurance.digital_signature !== 'unavailable'
    || assurance.institutional_directory !== 'unavailable'
  ) {
    fail('catalog.assurance', '保障边界无效')
  }
  return Object.freeze({
    approvedEntities,
    reviewRequiredEntities,
    rejectedEntityIds,
    mergeDecisions,
    splitDecisions,
    eventCount: integer(raw.event_count, 'catalog.event_count'),
    latestEventId: nullableText(raw.latest_event_id, 'catalog.latest_event_id', { pattern: EVENT_ID, maximum: 64 }),
    accuracyClaim: 'not_measured',
    reviewExpiryPolicy: 'not_configured',
  })
}

function sanitizeRelations(value) {
  const raw = record(value, 'relations')
  if (
    raw.schema_version !== ENTITY_GOVERNANCE_SCHEMAS.relations
    || raw.projection_policy !== 'approved-and-not-retracted-only'
    || raw.accuracy_claim !== 'not_measured'
    || raw.review_expiry_policy !== 'not_configured'
  ) {
    fail('relations', '关系投影声明无效')
  }
  const items = Object.freeze(
    boundedArray(raw.items, 'relations.items').map((item, index) => {
      const path = `relations.items[${index}]`
      const relation = record(item, path)
      if (relation.review_status !== 'approved') fail(path, '关系未批准')
      const validity = interval(relation, path)
      return Object.freeze({
        relationId: text(relation.relation_id, `${path}.relation_id`, { pattern: RELATION_URN, maximum: 160 }),
        subjectId: text(relation.subject_id, `${path}.subject_id`, { pattern: ENTITY_URN, maximum: 180 }),
        predicate: text(relation.predicate, `${path}.predicate`, { pattern: PREDICATE_URN, maximum: 130 }),
        objectId: text(relation.object_id, `${path}.object_id`, { pattern: ENTITY_URN, maximum: 180 }),
        validFrom: validity.validFrom,
        validTo: validity.validTo,
        addedEventId: text(relation.added_event_id, `${path}.added_event_id`, { pattern: EVENT_ID, maximum: 64 }),
        evidence: evidence(relation.evidence, `${path}.evidence`),
      })
    }),
  )
  if (integer(raw.relation_count, 'relations.relation_count') !== items.length) {
    fail('relations.relation_count', '与列表长度不一致')
  }
  if (new Set(items.map((item) => item.relationId)).size !== items.length) {
    fail('relations.items', '关系 ID 重复')
  }
  return Object.freeze({
    items,
    relationCount: items.length,
    reviewExpiryPolicy: 'not_configured',
  })
}

function sanitizeHistory(value, expectedLimit) {
  const raw = record(value, 'history')
  if (
    raw.schema_version !== ENTITY_GOVERNANCE_SCHEMAS.history
    || raw.semantic_projection_verified !== true
    || raw.worm_status !== 'unavailable'
    || raw.digital_signature_status !== 'unavailable'
    || raw.visibility !== 'authenticated-users'
    || raw.reason_visibility !== 'authenticated-users'
    || raw.actor_reference_semantics !== 'local-canonical-user-id-not-directory-resolved'
  ) {
    fail('history', '历史可见性或保障声明无效')
  }
  const eventCount = integer(raw.event_count, 'history.event_count')
  const latestEventId = nullableText(raw.latest_event_id, 'history.latest_event_id', { pattern: EVENT_ID, maximum: 64 })
  const items = Object.freeze(
    boundedArray(raw.items, 'history.items', 100).map((item, index) => {
      const path = `history.items[${index}]`
      const event = record(item, path)
      if (event.schema_version !== ENTITY_GOVERNANCE_SCHEMAS.event) fail(`${path}.schema_version`, '版本不受支持')
      const eventId = text(event.event_id, `${path}.event_id`, { pattern: EVENT_ID, maximum: 64 })
      const occurredAt = governanceEventTime(event.occurred_at, `${path}.occurred_at`)
      if (!eventId.includes(`-${governanceEventStamp(occurredAt)}-`)) {
        fail(path, '事件 ID 与时间不一致')
      }
      return Object.freeze({
        eventId,
        sequence: integer(event.sequence, `${path}.sequence`, { minimum: 1 }),
        occurredAt,
        actorRef: text(event.actor_ref, `${path}.actor_ref`, { pattern: USER_REF, maximum: 64 }),
        eventType: enumValue(event.event_type, `${path}.event_type`, EVENT_TYPES),
        reason: text(event.reason, `${path}.reason`, { maximum: 1000 }),
        evidence: evidence(event.evidence, `${path}.evidence`),
        recordSha256: text(event.record_sha256, `${path}.record_sha256`, { pattern: SHA256, maximum: 64 }),
        chainHmacSha256: text(event.chain_hmac_sha256, `${path}.chain_hmac_sha256`, { pattern: SHA256, maximum: 64 }),
        previousEventId: nullableText(event.previous_event_id, `${path}.previous_event_id`, { pattern: EVENT_ID, maximum: 64 }),
        previousRecordSha256: nullableText(event.previous_record_sha256, `${path}.previous_record_sha256`, { pattern: SHA256, maximum: 64 }),
        previousChainHmacSha256: nullableText(event.previous_chain_hmac_sha256, `${path}.previous_chain_hmac_sha256`, { pattern: SHA256, maximum: 64 }),
      })
    }),
  )
  if (eventCount === 0 && (latestEventId !== null || items.length)) fail('history', '空历史不自洽')
  if (eventCount > 0 && (!latestEventId || !items.length || items[0].eventId !== latestEventId)) {
    fail('history', '最新事件不自洽')
  }
  if (items.length !== Math.min(eventCount, expectedLimit)) fail('history.items', '历史窗口不完整')
  if (items.length && items[0].sequence !== eventCount) fail('history.items[0]', '最新序号不自洽')
  for (let index = 0; index < items.length - 1; index += 1) {
    const newer = items[index]
    const older = items[index + 1]
    if (
      newer.sequence !== older.sequence + 1
      || newer.previousEventId !== older.eventId
      || newer.previousRecordSha256 !== older.recordSha256
      || newer.previousChainHmacSha256 !== older.chainHmacSha256
      || governanceEventOrder(newer.occurredAt) <= governanceEventOrder(older.occurredAt)
    ) {
      fail(`history.items[${index}]`, '分页内链关系不连续')
    }
  }
  const oldestVisible = items[items.length - 1]
  if (
    items.length === eventCount
    && oldestVisible
    && (
      oldestVisible.sequence !== 1
      || oldestVisible.previousEventId !== null
      || oldestVisible.previousRecordSha256 !== null
      || oldestVisible.previousChainHmacSha256 !== null
    )
  ) {
    fail('history.items', '完整窗口缺少链首')
  }
  if (new Set(items.map((item) => item.eventId)).size !== items.length) {
    fail('history.items', '事件 ID 重复')
  }
  return Object.freeze({ eventCount, latestEventId, items })
}

export function sanitizeEntityGovernanceWorkspace(payload, { historyLimit = 50 } = {}) {
  if (!Number.isInteger(historyLimit) || historyLimit < 1 || historyLimit > 100) {
    throw new TypeError('Entity governance history limit must be in [1, 100]')
  }
  const raw = record(payload, 'workspace')
  const status = sanitizeEntityGovernanceStatus(raw.status)
  const catalog = sanitizeCatalog(raw.catalog)
  const relations = sanitizeRelations(raw.relations)
  const history = sanitizeHistory(raw.history, historyLimit)
  if (
    status.storageStatus !== 'available'
    || status.eventCount !== catalog.eventCount
    || status.eventCount !== history.eventCount
    || status.latestEventId !== catalog.latestEventId
    || status.latestEventId !== history.latestEventId
    || status.reviewExpiryPolicy !== catalog.reviewExpiryPolicy
    || status.reviewExpiryPolicy !== relations.reviewExpiryPolicy
  ) {
    fail('workspace', '跨资源账本版本不一致')
  }
  const approved = new Set(catalog.approvedEntities.map((item) => item.entityId))
  if (catalog.approvedEntities.some((item) => (
    !approved.has(item.canonicalEntityId)
    || (item.mergeTargetId !== null && !approved.has(item.mergeTargetId))
    || item.splitIntoEntityIds.some((entityId) => !approved.has(entityId))
  ))) {
    fail('workspace.catalog', '活动身份决策引用未批准实体')
  }
  const approvedById = new Map(catalog.approvedEntities.map((item) => [item.entityId, item]))
  const mergeBySource = new Map(catalog.mergeDecisions.map((item) => [item.sourceEntityId, item]))
  const splitBySource = new Map(catalog.splitDecisions.map((item) => [item.sourceEntityId, item]))
  if (catalog.mergeDecisions.some((item) => !approved.has(item.sourceEntityId) || !approved.has(item.targetEntityId))) {
    fail('workspace.catalog.merge_decisions', '合并裁决引用未批准实体')
  }
  if (catalog.splitDecisions.some((item) => (
    !approved.has(item.sourceEntityId)
    || item.resultingEntityIds.some((entityId) => !approved.has(entityId))
  ))) {
    fail('workspace.catalog.split_decisions', '拆分裁决引用未批准实体')
  }
  for (const entity of catalog.approvedEntities) {
    const merge = mergeBySource.get(entity.entityId)
    const split = splitBySource.get(entity.entityId)
    if ((entity.mergeTargetId === null) !== !merge || (merge && merge.targetEntityId !== entity.mergeTargetId)) {
      fail('workspace.catalog', '实体投影与合并裁决不一致')
    }
    if (
      (entity.splitIntoEntityIds.length === 0) !== !split
      || (split && (
        split.resultingEntityIds.length !== entity.splitIntoEntityIds.length
        || split.resultingEntityIds.some((value, index) => value !== entity.splitIntoEntityIds[index])
      ))
    ) {
      fail('workspace.catalog', '实体投影与拆分裁决不一致')
    }
    if (merge && split) fail('workspace.catalog', '实体不能同时存在合并与拆分裁决')

    let canonical = entity.entityId
    const seen = new Set()
    while (mergeBySource.has(canonical)) {
      if (seen.has(canonical)) fail('workspace.catalog', '合并裁决形成循环')
      seen.add(canonical)
      canonical = mergeBySource.get(canonical).targetEntityId
    }
    if (!approvedById.has(canonical) || entity.canonicalEntityId !== canonical) {
      fail('workspace.catalog', '规范实体 ID 与合并裁决不一致')
    }
  }
  const aliasOwners = new Map()
  for (const entity of catalog.approvedEntities) {
    for (const alias of entity.approvedAliases) {
      const key = alias.value.toLowerCase()
      const owner = aliasOwners.get(key)
      if (owner && owner !== entity.entityId) fail('workspace.catalog', '批准别名映射到多个实体')
      aliasOwners.set(key, entity.entityId)
    }
  }
  if (relations.items.some((item) => !approved.has(item.subjectId) || !approved.has(item.objectId))) {
    fail('workspace.relations', '活动关系引用未批准实体')
  }
  return Object.freeze({ status, catalog, relations, history })
}

export function emptyEntityGovernanceState(phase = 'idle', error = '') {
  return Object.freeze({ phase, error, data: null })
}

export function readyEntityGovernanceState(data) {
  return Object.freeze({ phase: 'ready', error: '', data })
}

export function createLatestEntityGovernanceRequest() {
  let generation = 0
  let controller = null
  return Object.freeze({
    async run(operation) {
      generation += 1
      const current = generation
      controller?.abort()
      controller = new AbortController()
      try {
        const value = await operation(controller.signal)
        return current === generation
          ? Object.freeze({ status: 'success', value })
          : Object.freeze({ status: 'stale' })
      } catch (error) {
        if (current !== generation) return Object.freeze({ status: 'stale' })
        if (error?.name === 'AbortError') return Object.freeze({ status: 'aborted' })
        return Object.freeze({ status: 'error', error })
      }
    },
    cancel() {
      generation += 1
      controller?.abort()
      controller = null
    },
  })
}
