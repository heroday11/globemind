import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  PRODUCT_DATA_FLOW_VERSION,
  PRODUCT_MODULES,
  PRODUCT_TASK_FLOWS,
  productModuleById,
} from '../src/governance/productDataFlow.js'

test('product data-flow catalog is bounded, versioned and never implies automatic truth transfer', () => {
  assert.equal(PRODUCT_DATA_FLOW_VERSION, 'product-data-flow-v1')
  assert.ok(PRODUCT_MODULES.length >= 6)
  assert.ok(PRODUCT_MODULES.length <= 12)
  assert.ok(PRODUCT_TASK_FLOWS.length >= 6)
  assert.ok(PRODUCT_TASK_FLOWS.length <= 16)

  const moduleIds = PRODUCT_MODULES.map((item) => item.id)
  const flowIds = PRODUCT_TASK_FLOWS.map((item) => item.id)
  assert.equal(new Set(moduleIds).size, moduleIds.length)
  assert.equal(new Set(flowIds).size, flowIds.length)
  assert.equal(productModuleById('story_graph').route, '/data-service/story-graph')
  assert.equal(productModuleById('article').route, '/data-service/news/:id')

  for (const module of PRODUCT_MODULES) {
    assert.deepEqual(Object.keys(module).sort(), [
      'boundary',
      'id',
      'input',
      'label',
      'output',
      'route',
      'state',
      'version',
    ].sort())
    assert.equal(module.version, PRODUCT_DATA_FLOW_VERSION)
    assert.equal(productModuleById(module.id), module)
    assert.match(module.route, /^\//)
    assert.ok(module.boundary)
  }

  for (const flow of PRODUCT_TASK_FLOWS) {
    assert.deepEqual(Object.keys(flow).sort(), [
      'automatic',
      'from',
      'handoff',
      'id',
      'provenanceState',
      'to',
      'truthAssurance',
      'version',
    ].sort())
    assert.equal(flow.version, PRODUCT_DATA_FLOW_VERSION)
    assert.ok(moduleIds.includes(flow.from), flow.id)
    assert.ok(moduleIds.includes(flow.to), flow.id)
    assert.equal(flow.automatic, false)
    assert.equal(flow.truthAssurance, 'not_established')
    assert.match(flow.provenanceState, /^(explicit_receipt|explicit_snapshot|manual_entry|required_review)$/)
    assert.ok(flow.handoff)
  }

  const unavailable = productModuleById('secret-module-name')
  assert.equal(unavailable.id, 'unavailable')
  assert.equal(unavailable.state, 'unavailable')
  assert.doesNotMatch(JSON.stringify(unavailable), /secret-module-name/)
})

test('public help renders the governed task map and states non-equivalence boundaries', async () => {
  const source = await readFile(new URL('../src/views/user/HelpDocs.vue', import.meta.url), 'utf8')

  assert.match(source, /PRODUCT_TASK_FLOWS/)
  assert.match(source, /productModuleById/)
  assert.match(source, /不是自动数据管线/)
  assert.match(source, /不会把检索命中、模型输出或图上关系自动升级为已核验事实/)
  assert.match(source, /flow\.automatic \? '自动' : '用户显式操作'/)
})
