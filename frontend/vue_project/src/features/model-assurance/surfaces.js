export const MODEL_OUTPUT_SURFACE_SCHEMA_VERSION = 'globemind.model-output-surfaces.v1'
export const MODEL_OUTPUT_SURFACE_INVENTORY_REVISION = 'model-output-surfaces-2026-08-09.1'

const FIELD_STATUSES = Object.freeze(['unknown', 'not_available'])
const DOMAINS = Object.freeze(['article', 'assistant', 'financial', 'opinion', 'story_graph'])
const OUTPUT_KINDS = Object.freeze(['classification', 'derived', 'generative'])
const REASON_CODE = /^[A-Z0-9_]+$/
const SOURCE_PATH = /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)+$/
const SURFACE_ID = /^[a-z][a-z0-9.-]{2,79}$/

export class ModelOutputSurfaceContractError extends Error {
  constructor(path, reason) {
    super(`模型输出面清单不符合受信契约：${path} ${reason}`)
    this.name = 'ModelOutputSurfaceContractError'
    this.code = 'MODEL_OUTPUT_SURFACE_CONTRACT_INVALID'
    this.path = path
  }
}

function fail(path, reason) {
  throw new ModelOutputSurfaceContractError(path, reason)
}

function recordValue(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(path, '必须是对象')
  return value
}

function exactKeys(value, expected, path) {
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    fail(path, `字段必须精确为 ${wanted.join(', ')}`)
  }
}

function stringValue(value, path, { pattern, maximum = 2_000 } = {}) {
  if (
    typeof value !== 'string'
    || !value
    || value.trim() !== value
    || value.length > maximum
    || [...value].some((character) => {
      const code = character.charCodeAt(0)
      return code < 32 || code === 127
    })
  ) {
    fail(path, '必须是有界、规范化的非空字符串')
  }
  if (pattern && !pattern.test(value)) fail(path, '格式无效')
  return value
}

function literal(value, expected, path) {
  if (value !== expected) fail(path, `必须是 ${String(expected)}`)
  return value
}

function enumValue(value, allowed, path) {
  if (!allowed.includes(value)) fail(path, '枚举值无效')
  return value
}

function stringArray(value, path, { pattern, minimum = 0, maximum = 20 } = {}) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    fail(path, '必须是有界数组')
  }
  const normalized = value.map((item, index) =>
    stringValue(item, `${path}[${index}]`, { pattern, maximum: 500 }),
  )
  if (new Set(normalized).size !== normalized.length) fail(path, '不能包含重复值')
  return Object.freeze(normalized)
}

function sanitizeField(value, expectedStatus, path) {
  const raw = recordValue(value, path)
  exactKeys(raw, ['status', 'value'], path)
  const status = enumValue(raw.status, FIELD_STATUSES, `${path}.status`)
  if (status !== expectedStatus) fail(`${path}.status`, `必须是 ${expectedStatus}`)
  if (raw.value !== null) fail(`${path}.value`, '未知或不可用字段必须为 null')
  return Object.freeze({ status, value: null })
}

function sanitizeIdentity(value, path, { modelVersionAvailable }) {
  const raw = recordValue(value, path)
  exactKeys(raw, ['model_id', 'model_version', 'deployed_at', 'change_notes'], path)
  return Object.freeze({
    modelId: sanitizeField(raw.model_id, 'not_available', `${path}.model_id`),
    modelVersion: sanitizeField(
      raw.model_version,
      modelVersionAvailable ? 'unknown' : 'not_available',
      `${path}.model_version`,
    ),
    deployedAt: sanitizeField(raw.deployed_at, 'not_available', `${path}.deployed_at`),
    changeNotes: sanitizeField(raw.change_notes, 'not_available', `${path}.change_notes`),
  })
}

function sanitizeAttestation(value, path) {
  const raw = recordValue(value, path)
  exactKeys(raw, ['status', 'attestation_id', 'observed_at'], path)
  literal(raw.status, 'not_available', `${path}.status`)
  literal(raw.attestation_id, null, `${path}.attestation_id`)
  literal(raw.observed_at, null, `${path}.observed_at`)
  return Object.freeze({ status: 'not_available', attestationId: null, observedAt: null })
}

function sanitizeSourceLocator(value, path) {
  const raw = recordValue(value, path)
  exactKeys(raw, ['path', 'locator'], path)
  return Object.freeze({
    path: stringValue(raw.path, `${path}.path`, { pattern: SOURCE_PATH, maximum: 300 }),
    locator: stringValue(raw.locator, `${path}.locator`, { maximum: 300 }),
  })
}

function sanitizeSurface(value, path) {
  const raw = recordValue(value, path)
  exactKeys(raw, [
    'surface_id',
    'domain',
    'output_kind',
    'route_patterns',
    'ui_surfaces',
    'identity_contract_fields',
    'identity',
    'runtime_attestation',
    'source_locators',
    'reason_codes',
  ], path)
  const reasonCodes = stringArray(raw.reason_codes, `${path}.reason_codes`, {
    pattern: REASON_CODE,
    minimum: 3,
  })
  for (const required of [
    'RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE',
    'DEPLOYMENT_TIME_NOT_AVAILABLE',
    'CHANGE_NOTES_NOT_AVAILABLE',
  ]) {
    if (!reasonCodes.includes(required)) fail(`${path}.reason_codes`, `缺少 ${required}`)
  }
  if (!Array.isArray(raw.source_locators) || raw.source_locators.length < 2
    || raw.source_locators.length > 20) {
    fail(`${path}.source_locators`, '必须包含有界的后端与展示面定位')
  }
  const identityContractFields = stringArray(
    raw.identity_contract_fields,
    `${path}.identity_contract_fields`,
  )
  return Object.freeze({
    surfaceId: stringValue(raw.surface_id, `${path}.surface_id`, { pattern: SURFACE_ID }),
    domain: enumValue(raw.domain, DOMAINS, `${path}.domain`),
    outputKind: enumValue(raw.output_kind, OUTPUT_KINDS, `${path}.output_kind`),
    routePatterns: stringArray(raw.route_patterns, `${path}.route_patterns`, { minimum: 1 }),
    uiSurfaces: stringArray(raw.ui_surfaces, `${path}.ui_surfaces`, {
      pattern: SOURCE_PATH,
      minimum: 1,
    }),
    identityContractFields,
    identity: sanitizeIdentity(raw.identity, `${path}.identity`, {
      modelVersionAvailable: identityContractFields.includes('model_version'),
    }),
    runtimeAttestation: sanitizeAttestation(
      raw.runtime_attestation,
      `${path}.runtime_attestation`,
    ),
    sourceLocators: Object.freeze(raw.source_locators.map((item, index) =>
      sanitizeSourceLocator(item, `${path}.source_locators[${index}]`),
    )),
    reasonCodes,
  })
}

export function sanitizeModelOutputSurfaceInventory(value) {
  const raw = recordValue(value, 'inventory')
  exactKeys(raw, [
    'schema_version',
    'inventory_revision',
    'scope',
    'coverage_state',
    'complete_runtime_deployment_claim',
    'runtime_attestation_state',
    'reason_codes',
    'surfaces',
  ], 'inventory')
  literal(raw.schema_version, MODEL_OUTPUT_SURFACE_SCHEMA_VERSION, 'inventory.schema_version')
  literal(
    raw.inventory_revision,
    MODEL_OUTPUT_SURFACE_INVENTORY_REVISION,
    'inventory.inventory_revision',
  )
  literal(raw.scope, 'bounded_public_model_output_surfaces', 'inventory.scope')
  literal(raw.coverage_state, 'source_located', 'inventory.coverage_state')
  literal(
    raw.complete_runtime_deployment_claim,
    false,
    'inventory.complete_runtime_deployment_claim',
  )
  literal(
    raw.runtime_attestation_state,
    'not_available',
    'inventory.runtime_attestation_state',
  )
  const reasonCodes = stringArray(raw.reason_codes, 'inventory.reason_codes', {
    pattern: REASON_CODE,
    minimum: 2,
  })
  if (!reasonCodes.includes('BOUNDED_INVENTORY_ONLY')) {
    fail('inventory.reason_codes', '缺少 BOUNDED_INVENTORY_ONLY')
  }
  if (!reasonCodes.includes('RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE')) {
    fail('inventory.reason_codes', '缺少运行时证明边界')
  }
  if (!Array.isArray(raw.surfaces) || raw.surfaces.length < 1 || raw.surfaces.length > 50) {
    fail('inventory.surfaces', '必须是有界数组')
  }
  const surfaces = raw.surfaces.map((item, index) =>
    sanitizeSurface(item, `inventory.surfaces[${index}]`),
  )
  const ids = surfaces.map((item) => item.surfaceId)
  const sorted = [...ids].sort()
  if (new Set(ids).size !== ids.length || ids.some((id, index) => id !== sorted[index])) {
    fail('inventory.surfaces', '输出面 ID 必须有序且唯一')
  }
  return Object.freeze({
    schemaVersion: MODEL_OUTPUT_SURFACE_SCHEMA_VERSION,
    inventoryRevision: MODEL_OUTPUT_SURFACE_INVENTORY_REVISION,
    scope: 'bounded_public_model_output_surfaces',
    coverageState: 'source_located',
    completeRuntimeDeploymentClaim: false,
    runtimeAttestationState: 'not_available',
    reasonCodes,
    surfaces: Object.freeze(surfaces),
  })
}

export function formatModelSurfaceField(field) {
  if (field?.status === 'unknown') return '未知'
  if (field?.status === 'not_available') return '不可用'
  return '不可用'
}

export function modelSurfaceCopyText(surface) {
  if (!surface || typeof surface !== 'object') return ''
  return [
    `输出面：${surface.surfaceId}`,
    `模型 ID：${formatModelSurfaceField(surface.identity?.modelId)}`,
    `模型版本：${formatModelSurfaceField(surface.identity?.modelVersion)}`,
    `部署时间：${formatModelSurfaceField(surface.identity?.deployedAt)}`,
    `变更说明：${formatModelSurfaceField(surface.identity?.changeNotes)}`,
    `运行时证明：${surface.runtimeAttestation?.status === 'not_available' ? '不可用' : '未知'}`,
    `原因码：${Array.isArray(surface.reasonCodes) ? surface.reasonCodes.join(', ') : '不可用'}`,
  ].join('\n')
}
