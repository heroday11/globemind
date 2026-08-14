import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  L2_RUN_ID,
  L3_RUN_ID,
  buildMacroDisplayEdges,
  buildStoryGraphAccessibleList,
  buildStoryGraphEdgeInspector,
  buildStoryGraphEvidenceParams,
  buildStoryGraphListRequest,
  buildStoryGraphQueryStatus,
  buildStoryStatsText,
  buildStoryGraphWorkspaceRequest,
  buildStorySummaryLine,
  createLatestRequestGate,
  createStoryGraphApi,
  edgeLabel,
  evidenceLevel,
  familyLabel,
  filterStorySummaries,
  formatRange,
  graphSamplingNotice,
  hasStoryGraphEvidenceTarget,
  normalizeL2ChainSummary,
  normalizeL3MacroSummary,
  normalizeGraphSampling,
  normalizeMacroLane,
  normalizeStoryRelation,
  orderStoryNodes,
  relationEvidenceText,
  relationResearchGuidance,
  relationStrengthLabel,
  reportingCoverageGuidance,
  storyGraphAdapterConfig,
  transformL2ChainResponse,
  transformL3MacroResponse,
} from '../src/features/story-graph/index.js'

const boundedRelationSemantics = (publicEdgeType, overrides = {}) => ({
  schema_version: 'story-relation-semantics-v1',
  ontology_state: 'bounded',
  public_edge_type: publicEdgeType,
  relation_kind: publicEdgeType === 'parallel' ? 'temporal_overlap' : 'temporal_sequence',
  temporal_basis: publicEdgeType === 'parallel' ? 'overlap' : 'ordered_or_adjacent',
  causal_status: 'not_established',
  influence_status: 'not_established',
  evidence_role: 'derived_signal',
  derivation: 'stored_derived_relation',
  reason_code: publicEdgeType === 'parallel'
    ? 'TEMPORAL_OVERLAP_NOT_INFLUENCE'
    : 'TEMPORAL_ORDER_NOT_CAUSAL',
  source_reason_disclosed: false,
  ...overrides,
})

test('story graph API requests preserve L2/L3 endpoint and parameter contracts', async () => {
  assert.deepEqual(buildStoryGraphListRequest('l3'), {
    path: '/story-graph/l3-macro/list',
    params: { page_size: 100, min_chains: 8, run_id: L3_RUN_ID },
  })
  assert.deepEqual(buildStoryGraphListRequest('l2'), {
    path: '/story-graph/l2-chain/list',
    params: { page_size: 100, min_segments: 2, run_id: L2_RUN_ID },
  })
  assert.deepEqual(buildStoryGraphWorkspaceRequest('l3', 'macro/a b'), {
    path: '/story-graph/l3-macro/macro%2Fa%20b',
    params: { run_id: L3_RUN_ID, max_nodes: 56 },
  })

  const calls = []
  const client = {
    async get(url, options) {
      calls.push({ url, options })
      return { data: { request: calls.length } }
    },
  }
  const api = createStoryGraphApi({ client, apiRoot: '/api', assistantApiRoot: '/api' })
  const signal = new AbortController().signal

  assert.deepEqual(await api.listStories('l2', { signal }), { request: 1 })
  assert.deepEqual(await api.getWorkspace('l3', 'macro/a b', { signal }), { request: 2 })
  assert.deepEqual(await api.getEvidence({ limit: 60, segment_id: 's/1' }, { signal }), {
    request: 3,
  })
  assert.deepEqual(
    await api.getAssistantSessionMessages('session/a b', { token: 'test-token', signal }),
    { request: 4 },
  )
  assert.equal(calls[0].url, '/api/story-graph/l2-chain/list')
  assert.equal(calls[0].options.signal, signal)
  assert.equal(calls[1].url, '/api/story-graph/l3-macro/macro%2Fa%20b')
  assert.equal(calls[1].options.signal, signal)
  assert.equal(calls[2].url, '/api/story-graph/evidence')
  assert.equal(calls[3].url, '/api/assistant/sessions/session%2Fa%20b/messages')
  assert.deepEqual(calls[3].options.headers, {
    Accept: 'application/json',
    Authorization: 'Bearer test-token',
  })
  assert.equal(calls[3].options.signal, signal)
  assert.equal(calls[3].options.withCredentials, true)
})

test('query status explicitly clears provenance for empty and failed searches', () => {
  assert.deepEqual(
    buildStoryGraphQueryStatus('search-empty', { mode: 'l3', query: '伊朗' }),
    {
      kind: 'search-empty',
      message: '当前查询“伊朗”没有匹配的大事件，未沿用上一次图谱、统计或结论。',
      role: 'status',
    },
  )
  const failed = buildStoryGraphQueryStatus('error', {
    error: 'network unavailable',
    mode: 'l2',
  })
  assert.equal(failed.role, 'alert')
  assert.match(failed.message, /画布、统计和结论已清空/)
  assert.doesNotMatch(failed.message, /network unavailable/)
  assert.match(
    buildStoryGraphQueryStatus('workspace-loading', { mode: 'l2', storyId: 'chain-1' }).message,
    /旧图谱已清空/,
  )
  assert.match(
    buildStoryGraphQueryStatus('workspace-empty', { mode: 'l3', storyId: 'macro-1' }).message,
    /未保留上一次图谱、统计或结论/,
  )
})

test('latest request gate rejects late responses after replacement or explicit invalidation', () => {
  const gate = createLatestRequestGate()
  const first = gate.begin()
  assert.equal(gate.isCurrent(first), true)
  const second = gate.begin()
  assert.equal(gate.isCurrent(first), false)
  assert.equal(gate.isCurrent(second), true)
  gate.invalidate()
  assert.equal(gate.isCurrent(second), false)
})

test('evidence params select exactly one node identity contract', () => {
  assert.deepEqual(
    buildStoryGraphEvidenceParams(
      { kind: 'l3-chain', l2ChainId: 'chain-1' },
      { currentL2RunId: 'run-current' },
    ),
    { limit: 60, chain_id: 'chain-1', run_id: 'run-current' },
  )
  assert.deepEqual(
    buildStoryGraphEvidenceParams({ kind: 'l2-segment', segmentId: 'segment-1' }),
    { limit: 60, segment_id: 'segment-1' },
  )
  assert.deepEqual(buildStoryGraphEvidenceParams({ originalClusterId: 'cluster-1' }), {
    limit: 60,
    cluster_id: 'cluster-1',
  })
  assert.equal(hasStoryGraphEvidenceTarget({ limit: 60 }), false)
})

test('L3 response normalization creates renderer-ready nodes, edges, and metadata', () => {
  const graph = transformL3MacroResponse({
    macro: {
      macro_id: 'macro-1',
      title: 'Macro title',
      family_group: 'conflict',
      l2_chain_count: 4,
      article_count: 12,
    },
    nodes: [
      {
        l2_chain_id: 'chain-1',
        title: 'Chain one',
        lane: 'diplomacy',
        metadata: { initiator: 'A', target: 'B' },
      },
    ],
    edges: [{
      from_chain_id: 'chain-1',
      to_chain_id: 'chain-2',
      edge_type: 'macro_sequence',
      relation_semantics: boundedRelationSemantics('macro_sequence'),
      edge_weight: 0.8,
      claim: {
        claim_id: `sgc_${'a'.repeat(64)}`,
        citation_locator: null,
        citation_status: 'unavailable',
        reason_code: 'GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE',
        unknown_gate: 'explicit_unknown',
        usable_as_fact: false,
      },
    }],
    visible_node_count: 1,
    sampling: {
      schema_version: 'graph-sampling-provenance-v1',
      coverage_state: 'partial',
      components: [{
        unit: 'l2_chain_node',
        state: 'bounded_partial',
        requested_count: 56,
        evaluated_count: 4,
        returned_count: 1,
        excluded_count: 3,
        limit: 56,
        overflow: true,
        selection_rule: 'lane_quota_then_importance',
        reason_codes: ['DISPLAY_LIMIT', 'GRAPH_COMPLETENESS_NOT_ESTABLISHED'],
        excluded_node_ids_disclosed: false,
      }],
      complete_graph_claim: false,
    },
  })

  assert.equal(graph.source, 'l3-macro')
  assert.equal(graph.story_id, 'macro-1')
  assert.equal(graph.nodes[0].source_kind, 'l3-chain')
  assert.equal(graph.nodes[0].l2_run_id, L2_RUN_ID)
  assert.equal(graph.nodes[0].initiator, 'A')
  assert.equal(graph.edges[0].edge_type, 'macro_sequence')
  assert.equal(graph.edges[0].claim.claim_id, `sgc_${'a'.repeat(64)}`)
  assert.equal(buildStoryGraphEdgeInspector(graph.edges[0]).unknownGate, 'explicit_unknown')
  assert.match(relationStrengthLabel(graph.edges[0]), /不可作为事实/)
  assert.match(relationEvidenceText(graph.edges[0]), /定位符不可用/)
  assert.match(relationResearchGuidance(graph.edges[0]).join(' '), /不得写成事实/)
  assert.doesNotMatch(relationResearchGuidance(graph.edges[0]).join(' '), /进入报告草稿/)
  assert.equal(graph.meta.run_id, L3_RUN_ID)
  assert.equal(graph.meta.total_node_count, 4)
})

test('sampling provenance remains partial and old or contradictory payloads fail closed', () => {
  const validSampling = {
    schema_version: 'graph-sampling-provenance-v1',
    coverage_state: 'partial',
    components: [{
      unit: 'l2_chain_node',
      state: 'bounded_partial',
      requested_count: 2,
      evaluated_count: 4,
      returned_count: 1,
      excluded_count: 3,
      limit: 2,
      overflow: true,
      selection_rule: 'lane_quota_then_importance',
      reason_codes: ['DISPLAY_LIMIT', 'GRAPH_COMPLETENESS_NOT_ESTABLISHED'],
      excluded_node_ids_disclosed: false,
    }],
    complete_graph_claim: false,
  }
  const graph = transformL3MacroResponse({
    macro: { macro_id: 'macro-sampled', l2_chain_count: 999 },
    nodes: [{ l2_chain_id: 'chain-visible' }],
    sampling: validSampling,
  })
  assert.equal(graph.sampling.coverage_state, 'partial')
  assert.equal(graph.meta.visible_node_count, 1)
  assert.equal(graph.meta.total_node_count, 4)
  assert.match(graphSamplingNotice(graph), /展示 1\/4 个 L2 节点/)
  assert.match(graphSamplingNotice(graph), /排除 3 个/)
  assert.match(graphSamplingNotice(graph), /不是完整图/)

  const legacy = transformL3MacroResponse({
    macro: { macro_id: 'macro-legacy', l2_chain_count: 999 },
    nodes: [{ l2_chain_id: 'chain-visible' }],
  })
  assert.equal(legacy.sampling.coverage_state, 'unknown')
  assert.equal(legacy.meta.visible_node_count, 1)
  assert.equal(legacy.meta.total_node_count, null)
  assert.match(graphSamplingNotice(legacy), /抽样范围未知/)
  assert.match(graphSamplingNotice(legacy), /不得视为完整图/)

  const contradicted = normalizeGraphSampling(
    {
      ...validSampling,
      components: [{ ...validSampling.components[0], returned_count: 2 }],
    },
    { unit: 'l2_chain_node', returnedCount: 1 },
  )
  assert.equal(contradicted.coverage_state, 'unknown')
  assert.equal(contradicted.components[0].evaluated_count, null)
  assert.equal(contradicted.components[0].excluded_count, null)
  assert.equal(contradicted.components[0].overflow, null)

  const wrongLayer = normalizeGraphSampling(
    {
      ...validSampling,
      components: [{ ...validSampling.components[0], unit: 'l15_segment_node' }],
    },
    { unit: 'l2_chain_node', returnedCount: 1 },
  )
  assert.equal(wrongLayer.coverage_state, 'unknown')
  assert.equal(wrongLayer.components[0].unit, 'l2_chain_node')

  const identifierLeak = normalizeGraphSampling(
    {
      ...validSampling,
      components: [{
        ...validSampling.components[0],
        excluded_node_ids: ['private-node-id'],
      }],
    },
    { unit: 'l2_chain_node', returnedCount: 1 },
  )
  assert.equal(identifierLeak.coverage_state, 'unknown')
  assert.equal(JSON.stringify(identifierLeak).includes('private-node-id'), false)

  const completenessUpgrade = normalizeGraphSampling(
    {
      ...validSampling,
      components: [{
        ...validSampling.components[0],
        reason_codes: ['DISPLAY_LIMIT'],
      }],
    },
    { unit: 'l2_chain_node', returnedCount: 1 },
  )
  assert.equal(completenessUpgrade.coverage_state, 'unknown')

  const mixedCoverageGraph = transformL3MacroResponse({
    macro: { macro_id: 'macro-mixed' },
    nodes: [{ l2_chain_id: 'chain-visible' }],
    sampling: {
      ...validSampling,
      coverage_state: 'unknown',
      components: [
        validSampling.components[0],
        {
          unit: 'related_story',
          state: 'unknown',
          requested_count: null,
          evaluated_count: null,
          returned_count: 0,
          excluded_count: null,
          limit: null,
          overflow: null,
          selection_rule: 'related_story_rank',
          reason_codes: [
            'CANDIDATE_UNIVERSE_NOT_COUNTED',
            'GRAPH_COMPLETENESS_NOT_ESTABLISHED',
          ],
          excluded_node_ids_disclosed: false,
        },
      ],
    },
  })
  assert.equal(mixedCoverageGraph.sampling.coverage_state, 'unknown')
  assert.equal(mixedCoverageGraph.meta.total_node_count, null)
  assert.match(graphSamplingNotice(mixedCoverageGraph), /抽样范围未知/)
})

test('L2 legacy sampling is unknown and does not infer a complete segment universe', () => {
  const graph = transformL2ChainResponse({
    chain: { chain_id: 'chain-legacy', segment_count: 999 },
    nodes: [{ segment_id: 'segment-visible' }],
  })

  assert.equal(graph.sampling.coverage_state, 'unknown')
  assert.equal(graph.meta.visible_node_count, 1)
  assert.equal(graph.meta.total_node_count, null)
  assert.match(graphSamplingNotice(graph), /抽样范围未知/)
})

test('offset-page coverage describes candidates not returned in this page, not truncation', () => {
  const notice = graphSamplingNotice({
    source: 'graph-briefing-page',
    sampling_unit: 'micro_node',
    items: [{ chain_id: 'chain-visible' }],
    sampling: {
      schema_version: 'graph-sampling-provenance-v1',
      coverage_state: 'partial',
      components: [{
        unit: 'micro_node',
        state: 'bounded_partial',
        requested_count: 1,
        evaluated_count: 3,
        returned_count: 1,
        excluded_count: 2,
        limit: 1,
        overflow: true,
        selection_rule: 'offset_page_stable_order',
        reason_codes: [
          'PAGE_WINDOW_NOT_RETURNED',
          'DISPLAY_LIMIT',
          'GRAPH_COMPLETENESS_NOT_ESTABLISHED',
        ],
        excluded_node_ids_disclosed: false,
      }],
      complete_graph_claim: false,
    },
  })

  assert.match(notice, /本页未返回 2 个/)
  assert.doesNotMatch(notice, /排除|截断/)
  assert.match(notice, /不是完整图/)
})

test('L2 response normalization preserves pair and encoded detail navigation', () => {
  const graph = transformL2ChainResponse({
    chain: {
      chain_id: 'chain-1',
      title: 'Chain title',
      pair_key: 'Actor A -> Actor B',
      event_family: 'diplomacy',
    },
    nodes: [
      {
        segment_id: 'segment-1',
        title: 'Segment',
        l1_cluster_id: 'cluster/a b',
        story_angle: 'main_event',
      },
    ],
    edges: [{ from_id: 'segment-1', to_id: 'segment-2', edge_weight: 0.6 }],
  })

  assert.deepEqual(graph.meta.pair_key, ['Actor A', 'Actor B'])
  assert.equal(graph.nodes[0].source_kind, 'l2-segment')
  assert.equal(
    graph.nodes[0].detail_url,
    '/data-service/ground-news-desk?cluster_id=cluster%2Fa%20b',
  )
  assert.equal(graph.edges[0].edge_type, 'relation_unknown')
  assert.equal(graph.edges[0].claim.claim_id, null)
  assert.equal(graph.edges[0].claim.reason_code, 'GRAPH_RELATION_CLAIM_CONTRACT_MISSING')
  assert.equal(graph.edges[0].claim.unknown_gate, 'explicit_unknown')
  assert.equal(graph.edges[0].claim.usable_as_fact, false)
  assert.match(relationStrengthLabel(graph.edges[0]), /不可作为事实/)
})

test('flow model orders nodes and synthesizes stable macro lane edges', () => {
  const nodes = [{ id: 'c' }, { id: 'a' }, { id: 'b' }]
  const edges = [
    { from_id: 'a', to_id: 'b' },
    { from_id: 'b', to_id: 'c' },
  ]
  assert.deepEqual(orderStoryNodes(nodes, edges).map((node) => node.id), ['a', 'b', 'c'])
  assert.equal(normalizeMacroLane({ lane: 'military_security' }), 'conflict')

  const orderedNodes = [
    { id: 'c1', lane: 'conflict', node_order: 1 },
    { id: 'd1', lane: 'diplomacy', node_order: 2 },
    { id: 'c2', lane: 'conflict', node_order: 3 },
    { id: 'd2', lane: 'diplomacy', node_order: 4 },
  ]
  const displayEdges = buildMacroDisplayEdges(
    { storyId: 'macro-1', orderedNodes },
    [{ from_id: 'd2', to_id: 'c2', edge_type: 'influence', layer: 'story' }],
  )
  assert.ok(displayEdges.some((edge) => edge.edge_type === 'macro_sequence' && edge.synthetic))
  assert.ok(displayEdges.some((edge) => edge.edge_type === 'branch_sequence' && edge.synthetic))
  assert.ok(displayEdges.some((edge) => edge.edge_type === 'branch' && edge.synthetic))
  assert.ok(displayEdges.some((edge) => edge.edge_type === 'relation_unknown' && !edge.synthetic))
  assert.equal(displayEdges.some((edge) => edge.edge_type === 'influence'), false)
  for (const edge of displayEdges.filter((item) => item.synthetic)) {
    assert.match(edge.claim.claim_id, /^sgc_[0-9a-f]{64}$/)
    assert.equal(edge.claim.citation_locator, null)
    assert.equal(edge.claim.reason_code, 'GRAPH_LAYOUT_EDGE_NOT_EVIDENCE')
    assert.equal(edge.claim.unknown_gate, 'explicit_unknown')
    assert.equal(edge.claim.usable_as_fact, false)
    assert.equal(relationStrengthLabel(edge), '布局辅助线（非证据关系）')
    assert.match(relationEvidenceText(edge), /不代表事实、相关或因果已核验/)
    assert.equal(buildStoryGraphEdgeInspector(edge).synthetic, true)
  }
  assert.equal(edgeLabel('influence'), '关系类型未知（不可作影响/因果）')
  assert.equal(
    relationStrengthLabel({ edge_weight: 0.9 }),
    '关系强度未知（方法或证据不可用，不可作为事实）',
  )
})

test('relation ontology rejects influence, causal, malformed, and synthetic upgrades', () => {
  const cases = [
    { edge_type: 'influence', relation_reason: '相邻节点' },
    { edge_type: 'causal_escalation', relation_reason: 'model output' },
    { edge_type: 'future_schema_relation' },
    {
      edge_type: 'parallel',
      relation_semantics: boundedRelationSemantics('parallel', { causal_status: 'established' }),
    },
  ]
  for (const raw of cases) {
    const edge = normalizeStoryRelation(raw)
    assert.equal(edge.edge_type, 'relation_unknown')
    assert.equal(edge.relation_semantics.ontology_state, 'explicit_unknown')
    assert.equal(edge.relation_semantics.causal_status, 'not_established')
    assert.equal(edge.relation_semantics.influence_status, 'not_established')
    assert.match(edge.relation_reason, /未知.*不可.*影响.*因果/)
  }

  const overlap = normalizeStoryRelation({
    edge_type: 'parallel',
    relation_reason: 'untrusted free text that must not become a label',
    relation_semantics: boundedRelationSemantics('parallel'),
  })
  assert.equal(overlap.edge_type, 'parallel')
  assert.equal(overlap.relation_semantics.relation_kind, 'temporal_overlap')
  assert.equal(overlap.relation_reason, '仅表示时间重叠，不代表影响或因果')
  assert.equal(edgeLabel(overlap.edge_type), '时间重叠（非影响/因果）')

  const displayEdges = buildMacroDisplayEdges(
    {
      storyId: 'macro-adversarial',
      orderedNodes: [
        { id: 'a', lane: 'context', node_order: 1 },
        { id: 'b', lane: 'context', node_order: 2 },
      ],
    },
    [{ from_id: 'a', to_id: 'b', edge_type: 'influence', layer: 'story' }],
  )
  assert.equal(displayEdges.some((edge) => edge.edge_type === 'influence'), false)
  assert.ok(displayEdges.every((edge) => edge.relation_semantics))
  assert.ok(displayEdges.every((edge) => edge.relation_semantics.causal_status === 'not_established'))
})

test('L2 and L3 adapters require matching relation semantics and fail old payloads closed', () => {
  const l2 = transformL2ChainResponse({
    chain: { chain_id: 'chain-contract' },
    nodes: [{ segment_id: 'one' }, { segment_id: 'two' }],
    edges: [{
      from_id: 'one',
      to_id: 'two',
      edge_type: 'parallel',
      relation_semantics: boundedRelationSemantics('parallel'),
    }],
  })
  assert.equal(l2.edges[0].edge_type, 'parallel')
  assert.equal(l2.edges[0].relation_semantics.temporal_basis, 'overlap')

  const oldL2 = transformL2ChainResponse({
    chain: { chain_id: 'chain-old' },
    nodes: [{ segment_id: 'one' }, { segment_id: 'two' }],
    edges: [{ from_id: 'one', to_id: 'two', edge_type: 'parallel' }],
  })
  assert.equal(oldL2.edges[0].edge_type, 'relation_unknown')
  assert.equal(oldL2.edges[0].relation_semantics.ontology_state, 'explicit_unknown')

  const mismatch = transformL3MacroResponse({
    macro: { macro_id: 'macro-mismatch' },
    nodes: [{ l2_chain_id: 'one' }, { l2_chain_id: 'two' }],
    edges: [{
      from_chain_id: 'one',
      to_chain_id: 'two',
      edge_type: 'influence',
      relation_semantics: boundedRelationSemantics('parallel'),
    }],
  })
  assert.equal(mismatch.edges[0].edge_type, 'relation_unknown')
  assert.equal(mismatch.edges[0].relation_semantics.ontology_state, 'explicit_unknown')
})

test('renderer and presentation consume the central relation ontology without raw label fallbacks', async () => {
  const [renderer, presentation, model, flow] = await Promise.all([
    readFile(new URL('../src/features/story-graph/story-graph-react-flow.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/features/story-graph/presentation.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/features/story-graph/model.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/features/story-graph/flow-model.js', import.meta.url), 'utf8'),
  ])
  assert.match(renderer, /\.map\(\(edge\) => normalizeStoryRelation\(edge\)\)/)
  assert.match(presentation, /return storyRelationLabel\(value\)/)
  assert.match(model, /normalizeStoryRelation\(edge\)/)
  assert.match(flow, /normalizeLayoutStoryRelation\(/)
  for (const source of [renderer, presentation, model, flow]) {
    assert.doesNotMatch(source, /影响假设|\binfluence\s*:/)
  }
})

test('missing graph counts remain unknown instead of becoming observed zeros', () => {
  assert.equal(normalizeL2ChainSummary({ chain_id: 'chain-unknown' }).article_count, null)
  assert.equal(normalizeL3MacroSummary({ macro_id: 'macro-unknown' }).article_count, null)
  assert.equal(normalizeL2ChainSummary({ chain_id: 'chain-boolean', article_count: false }).article_count, null)
  assert.equal(normalizeL2ChainSummary({ chain_id: 'chain-blank', article_count: '  ' }).article_count, null)
  assert.equal(normalizeL2ChainSummary({ chain_id: 'chain-string', article_count: '3' }).article_count, null)

  const graph = transformL2ChainResponse({
    chain: { chain_id: 'chain-unknown', title: 'Unknown counts' },
    nodes: [{ segment_id: 'segment-1', title: 'Node without count' }],
  })
  assert.equal(graph.nodes[0].article_count, null)
  assert.equal(graph.meta.article_count, null)
  assert.doesNotMatch(
    buildStoryStatsText(graph, { mainNodes: 1, contextLinks: 0 }, []),
    /0 条新闻/,
  )
  assert.match(
    buildStoryStatsText(graph, { mainNodes: 1, contextLinks: 0 }, []),
    /新闻数未知/,
  )
  assert.match(buildStoryGraphAccessibleList(graph).nodes[0].meta, /新闻数未知/)
  assert.equal(evidenceLevel(null), '报道数量未知')
  assert.equal(evidenceLevel(false), '报道数量未知')
  assert.equal(evidenceLevel('  '), '报道数量未知')
  assert.equal(evidenceLevel(0), '已加载 0 条报道（未核验）')
  assert.equal(evidenceLevel(5), '已加载 5 条报道（未核验）')
  assert.match(reportingCoverageGuidance(null), /数量未知.*不得下结论/)
  assert.match(reportingCoverageGuidance(2), /仅加载 2 条.*不得下结论/)
  assert.match(reportingCoverageGuidance(8), /不等于事实已核验/)
})

test('presentation and adapter configuration remain deterministic', () => {
  const stories = [
    { id: '1', title: 'Trade conflict', initiator: 'A' },
    { id: '2', title: 'Diplomatic visit', initiator: 'B' },
  ]
  assert.deepEqual(filterStorySummaries(stories, 'trade'), [stories[0]])
  assert.equal(formatRange('2026-01-01T08:00:00Z', '2026-01-03'), '2026-01-01 → 2026-01-03')
  assert.equal(familyLabel('diplomacy'), '外交谈判')
  assert.match(
    buildStorySummaryLine({
      source: 'l2-chain',
      start_date: '2026-01-01',
      end_date: '2026-01-03',
      meta: { pair_key: ['A', 'B'], dominant_type: 'diplomacy', chain_quality: 'strong' },
    }),
    /A ↔ B.*外交谈判.*链质量未知/,
  )
  assert.deepEqual(storyGraphAdapterConfig.fitViewOptions, { padding: 0.12, duration: 0 })
  assert.equal(storyGraphAdapterConfig.storageKeyPrefix, 'globemind:story-graph-layout:v2:')
})

test('route consumes story graph only through the feature public entry', async () => {
  const pageUrl = new URL('../src/views/StoryGraphView.vue', import.meta.url)
  const source = await readFile(pageUrl, 'utf8')
  assert.match(source, /from ['"]@\/features\/story-graph\/index\.js['"]/)
  assert.doesNotMatch(source, /@\/features\/story-graph\/(?!index\.js)/)
  assert.doesNotMatch(source, /story-graph-react-flow/)
  assert.doesNotMatch(source, /\baxios\b|\bfetch\s*\(/)
  assert.doesNotMatch(source, /@\/config\/api/)
  assert.doesNotMatch(source, /err\?\.response\?\.data\?\.detail|err\.message/)
  assert.match(source, /createLatestRequestGate\(\)/)
  assert.match(source, /const listRequestGate = createLatestRequestGate\(\)/)
  assert.match(source, /const workspaceRequestGate = createLatestRequestGate\(\)/)
  assert.match(source, /const evidenceRequestGate = createLatestRequestGate\(\)/)
  assert.match(source, /listRequestGate\.isCurrent\(requestToken\)/)
  assert.match(source, /clearWorkspaceState\(\{ clearSelection: true \}\)/)
  assert.match(source, /queryStatus\.value = buildStoryGraphQueryStatus\(['"]search-empty['"]/)
  assert.match(source, /workspaceRequestGate\.isCurrent\(requestToken\)/)
  assert.match(source, /evidenceRequestGate\.isCurrent\(requestToken\)/)
  assert.match(source, /return relationResearchGuidance\(item\)/)
  assert.match(source, /graphSamplingNotice\(currentStory\.value\)/)
  assert.match(source, /class="intel-sampling-notice"/)
  assert.match(source, /\['主张 ID', item\.claimId/)
  assert.match(source, /\['引用定位', item\.citationLocator/)
  assert.doesNotMatch(source, /该边可进入报告草稿/)
})

test('story graph primary actions keep 44px keyboard and touch targets', async () => {
  const pageUrl = new URL('../src/views/StoryGraphView.vue', import.meta.url)
  const source = await readFile(pageUrl, 'utf8')
  assert.match(source, /\.intel-mode-switch button \{[\s\S]*?min-width: 44px;[\s\S]*?min-height: 44px;/)
  assert.match(source, /\.intel-icon-btn,[\s\S]*?\.intel-btn \{[\s\S]*?min-height: 44px;/)
  assert.match(source, /\.intel-icon-btn \{[\s\S]*?min-width: 44px;/)
  assert.match(source, /\.intel-evidence-link \{[\s\S]*?min-height: 44px;/)
  assert.match(source, /\.intel-side-toggle \{[\s\S]*?min-width: 44px;[\s\S]*?min-height: 44px;/)
})

test('story graph has a narrow mobile layout that wraps controls instead of clipping them', async () => {
  const pageUrl = new URL('../src/views/StoryGraphView.vue', import.meta.url)
  const source = await readFile(pageUrl, 'utf8')
  assert.match(source, /@media \(max-width: 640px\)/)
  assert.match(source, /\.intel-topbar__brand\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\)/)
  assert.match(source, /\.intel-topbar__meta\s*\{[\s\S]*?width:\s*100%/)
  assert.match(source, /\.intel-mode-switch\s*\{[\s\S]*?width:\s*100%/)
  assert.match(source, /\.intel-badge,[\s\S]*?\.intel-chip\s*\{[\s\S]*?white-space:\s*normal/)
})
