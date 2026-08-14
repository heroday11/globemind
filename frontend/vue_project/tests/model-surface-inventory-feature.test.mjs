import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

import {
  MODEL_OUTPUT_SURFACE_SCHEMA_VERSION,
  createModelAssuranceApi,
  formatModelSurfaceField,
  modelSurfaceCopyText,
  sanitizeModelOutputSurfaceInventory,
} from '../src/features/model-assurance/index.js'


function field(status, value = null) {
  return { status, value }
}


function surface(overrides = {}) {
  return {
    surface_id: 'opinion.aggregate',
    domain: 'opinion',
    output_kind: 'classification',
    route_patterns: ['GET /opinion/**'],
    ui_surfaces: ['frontend/vue_project/src/views/sentimentAnalysis.vue'],
    identity_contract_fields: ['model_version', 'method_version'],
    identity: {
      model_id: field('not_available'),
      model_version: field('unknown'),
      deployed_at: field('not_available'),
      change_notes: field('not_available'),
    },
    runtime_attestation: {
      status: 'not_available',
      attestation_id: null,
      observed_at: null,
    },
    source_locators: [
      {
        path: 'backend/api/routes/opinion_v2.py',
        locator: '@router.get("/opinion/overview"',
      },
      {
        path: 'frontend/vue_project/src/views/sentimentAnalysis.vue',
        locator: '<h1 class="sentiment-sr-only">智能舆情分析</h1>',
      },
    ],
    reason_codes: [
      'RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE',
      'DEPLOYMENT_TIME_NOT_AVAILABLE',
      'CHANGE_NOTES_NOT_AVAILABLE',
    ],
    ...overrides,
  }
}


function fixture(overrides = {}) {
  return {
    schema_version: 'globemind.model-output-surfaces.v1',
    inventory_revision: 'model-output-surfaces-2026-08-09.1',
    scope: 'bounded_public_model_output_surfaces',
    coverage_state: 'source_located',
    complete_runtime_deployment_claim: false,
    runtime_attestation_state: 'not_available',
    reason_codes: [
      'BOUNDED_INVENTORY_ONLY',
      'RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE',
    ],
    surfaces: [surface()],
    ...overrides,
  }
}


test('surface inventory keeps runtime identity explicitly unknown or unavailable', () => {
  const inventory = sanitizeModelOutputSurfaceInventory(fixture())
  assert.equal(MODEL_OUTPUT_SURFACE_SCHEMA_VERSION, fixture().schema_version)
  assert.equal(inventory.completeRuntimeDeploymentClaim, false)
  assert.equal(inventory.runtimeAttestationState, 'not_available')
  assert.equal(inventory.surfaces[0].identity.modelVersion.status, 'unknown')
  assert.equal(inventory.surfaces[0].identity.modelVersion.value, null)
  assert.equal(formatModelSurfaceField(inventory.surfaces[0].identity.modelVersion), '未知')
  assert.equal(formatModelSurfaceField(inventory.surfaces[0].identity.modelId), '不可用')
  assert.equal(formatModelSurfaceField(inventory.surfaces[0].identity.deployedAt), '不可用')

  const copy = modelSurfaceCopyText(inventory.surfaces[0])
  assert.match(copy, /输出面：opinion\.aggregate/)
  assert.match(copy, /模型版本：未知/)
  assert.match(copy, /部署时间：不可用/)
  assert.match(copy, /变更说明：不可用/)
  assert.match(copy, /运行时证明：不可用/)
  assert.doesNotMatch(copy, /prompt|reply|正文|secret|token/i)
})


test('authenticated client fetches the no-cache surface inventory endpoint', async () => {
  const calls = []
  const api = createModelAssuranceApi({
    apiOrigin: 'https://example.test',
    getAccessToken: () => 'opaque-access-token',
    fetchImpl: async (url, options) => {
      calls.push({ url, options })
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => fixture(),
      }
    },
  })

  const inventory = await api.fetchSurfaces()
  assert.equal(inventory.surfaces[0].surfaceId, 'opinion.aggregate')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://example.test/api/model-assurance/surfaces')
  assert.equal(calls[0].options.cache, 'no-store')
  assert.equal(calls[0].options.credentials, 'same-origin')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer opaque-access-token')
})


test('model assurance page exposes selectable copy text without claiming deployment proof', async () => {
  const source = await readFile(
    new URL('../src/views/ModelAssurance.vue', import.meta.url),
    'utf8',
  )
  assert.match(source, /模型输出面清单/)
  assert.match(source, /静态源码覆盖不代表运行时部署证明/)
  assert.match(source, /modelSurfaceCopyText/)
  assert.match(source, /fetchSurfaces/)
  assert.match(source, /复制身份状态/)
  assert.match(source, /<pre[^>]*>\{\{ modelSurfaceCopyText\(surface\) \}\}<\/pre>/)
})


test('surface inventory rejects identity guesses, extra content, and completeness claims', () => {
  const guessedVersion = fixture()
  guessedVersion.surfaces[0].identity.model_version.value = 'deepseek-chat'
  assert.throws(
    () => sanitizeModelOutputSurfaceInventory(guessedVersion),
    /model_version|未知|value/,
  )

  const leakedBody = fixture()
  leakedBody.surfaces[0].response_body = 'sensitive generated output'
  assert.throws(
    () => sanitizeModelOutputSurfaceInventory(leakedBody),
    /字段|response_body|契约/,
  )

  assert.throws(
    () => sanitizeModelOutputSurfaceInventory(fixture({
      complete_runtime_deployment_claim: true,
    })),
    /complete_runtime_deployment_claim|完整|运行时/,
  )

  const forgedAttestation = fixture()
  forgedAttestation.surfaces[0].runtime_attestation.status = 'attested'
  assert.throws(
    () => sanitizeModelOutputSurfaceInventory(forgedAttestation),
    /runtime_attestation|证明|attested/,
  )
})
