const MAX_COUNT = 2_147_483_647

const COMPONENT_KEYS = [
  'unit',
  'state',
  'requested_count',
  'evaluated_count',
  'returned_count',
  'excluded_count',
  'limit',
  'overflow',
  'selection_rule',
  'reason_codes',
  'excluded_node_ids_disclosed',
]

const ENVELOPE_KEYS = [
  'schema_version',
  'coverage_state',
  'components',
  'complete_graph_claim',
]

const UNITS = new Set([
  'macro_node',
  'micro_node',
  'news_item',
  'unclustered_news_item',
  'l2_chain_node',
  'l15_segment_node',
  'legacy_story_node',
  'related_story',
])

const RULES = new Set([
  'top_article_count_then_stable_id',
  'per_parent_article_count_then_stable_id',
  'recent_news_per_parent',
  'orphan_then_ambient_recent',
  'offset_page_stable_order',
  'lane_quota_then_importance',
  'ordered_chain_segments',
  'stored_edge_referenced_nodes',
  'related_story_rank',
])

const REASONS = new Set([
  'DISPLAY_LIMIT',
  'PER_PARENT_LIMIT',
  'PAGE_WINDOW_NOT_RETURNED',
  'CANDIDATE_UNIVERSE_NOT_COUNTED',
  'EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT',
  'FILTERED_BY_SELECTION_RULE',
  'ISOLATED_NODES_NOT_EVALUATED',
  'RELATED_STORY_LIMIT',
  'AMBIENT_FILL_NOT_GRAPH_MEMBERSHIP',
  'GRAPH_COMPLETENESS_NOT_ESTABLISHED',
])

const DEFAULT_RULE_BY_UNIT = Object.freeze({
  l2_chain_node: 'lane_quota_then_importance',
  l15_segment_node: 'ordered_chain_segments',
  legacy_story_node: 'stored_edge_referenced_nodes',
  related_story: 'related_story_rank',
  macro_node: 'top_article_count_then_stable_id',
  micro_node: 'per_parent_article_count_then_stable_id',
  news_item: 'recent_news_per_parent',
  unclustered_news_item: 'orphan_then_ambient_recent',
})

const UNIT_LABELS = Object.freeze({
  l2_chain_node: 'L2 节点',
  l15_segment_node: 'L1.5 节点',
  legacy_story_node: '故事节点',
  related_story: '关联故事',
  macro_node: 'L3 节点',
  micro_node: 'L2 节点',
  news_item: '候选报道',
  unclustered_news_item: '未聚类候选报道',
})

const RULE_LABELS = Object.freeze({
  top_article_count_then_stable_id: '报道量排序后按稳定标识打破并列',
  per_parent_article_count_then_stable_id: '每个父节点内按报道量与稳定标识选择',
  recent_news_per_parent: '每个父节点内选择近期候选报道',
  orphan_then_ambient_recent: '先选未聚类候选，再按近期候选补位',
  offset_page_stable_order: '稳定排序分页',
  lane_quota_then_importance: '先按泳道配额，再按重要度选择',
  ordered_chain_segments: '按链段顺序返回',
  stored_edge_referenced_nodes: '仅返回已存关系边引用的节点',
  related_story_rank: '按关联故事排名选择',
})

const REASON_LABELS = Object.freeze({
  DISPLAY_LIMIT: '展示上限',
  PER_PARENT_LIMIT: '父节点内上限',
  PAGE_WINDOW_NOT_RETURNED: '本次分页窗口未返回',
  CANDIDATE_UNIVERSE_NOT_COUNTED: '候选全集未计数',
  EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT: '候选计数不可用或矛盾',
  FILTERED_BY_SELECTION_RULE: '按选择规则过滤',
  ISOLATED_NODES_NOT_EVALUATED: '孤立节点未纳入评估',
  RELATED_STORY_LIMIT: '关联故事上限',
  AMBIENT_FILL_NOT_GRAPH_MEMBERSHIP: '补位候选不代表图成员关系',
  GRAPH_COMPLETENESS_NOT_ESTABLISHED: '图完整性未建立',
})

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(value, expected) {
  if (!isRecord(value)) return false
  const actual = Object.keys(value).sort()
  const wanted = [...expected].sort()
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index])
}

function isCount(value) {
  return Number.isInteger(value) && value >= 0 && value <= MAX_COUNT
}

function isNullableCount(value) {
  return value === null || isCount(value)
}

function normalizeComponent(raw) {
  if (!hasExactKeys(raw, COMPONENT_KEYS)) return null
  if (!UNITS.has(raw.unit) || !RULES.has(raw.selection_rule)) return null
  if (!['bounded_partial', 'unknown'].includes(raw.state)) return null
  if (!isNullableCount(raw.requested_count) || !isCount(raw.returned_count)) return null
  if (!isNullableCount(raw.evaluated_count) || !isNullableCount(raw.excluded_count)) return null
  if (!isNullableCount(raw.limit) || ![true, false, null].includes(raw.overflow)) return null
  if (raw.excluded_node_ids_disclosed !== false) return null
  if (
    !Array.isArray(raw.reason_codes)
    || raw.reason_codes.length < 1
    || raw.reason_codes.length > 8
    || raw.reason_codes.some((reason) => !REASONS.has(reason))
    || new Set(raw.reason_codes).size !== raw.reason_codes.length
  ) return null
  if (!raw.reason_codes.includes('GRAPH_COMPLETENESS_NOT_ESTABLISHED')) return null
  if (raw.requested_count !== null && raw.returned_count > raw.requested_count) return null
  if (raw.limit !== null && raw.returned_count > raw.limit) return null

  if (raw.state === 'unknown') {
    if (raw.evaluated_count !== null || raw.excluded_count !== null || raw.overflow !== null) return null
    if (!raw.reason_codes.some((reason) => [
      'CANDIDATE_UNIVERSE_NOT_COUNTED',
      'EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT',
    ].includes(reason))) return null
  } else {
    if (!isCount(raw.evaluated_count) || !isCount(raw.excluded_count)) return null
    if (typeof raw.overflow !== 'boolean') return null
    if (raw.evaluated_count < raw.returned_count) return null
    const excluded = raw.evaluated_count - raw.returned_count
    if (raw.excluded_count !== excluded || raw.overflow !== (excluded > 0)) return null
  }

  return Object.freeze({
    unit: raw.unit,
    state: raw.state,
    requested_count: raw.requested_count,
    evaluated_count: raw.evaluated_count,
    returned_count: raw.returned_count,
    excluded_count: raw.excluded_count,
    limit: raw.limit,
    overflow: raw.overflow,
    selection_rule: raw.selection_rule,
    reason_codes: Object.freeze([...raw.reason_codes]),
    excluded_node_ids_disclosed: false,
  })
}

function unknownSampling(unit, returnedCount) {
  const component = Object.freeze({
    unit,
    state: 'unknown',
    requested_count: null,
    evaluated_count: null,
    returned_count: isCount(returnedCount) ? returnedCount : 0,
    excluded_count: null,
    limit: null,
    overflow: null,
    selection_rule: DEFAULT_RULE_BY_UNIT[unit] || 'stored_edge_referenced_nodes',
    reason_codes: Object.freeze([
      'CANDIDATE_UNIVERSE_NOT_COUNTED',
      'GRAPH_COMPLETENESS_NOT_ESTABLISHED',
    ]),
    excluded_node_ids_disclosed: false,
  })
  return Object.freeze({
    schema_version: 'graph-sampling-provenance-v1',
    coverage_state: 'unknown',
    components: Object.freeze([component]),
    complete_graph_claim: false,
  })
}

export function normalizeGraphSampling(raw, { unit, returnedCount }) {
  const safeUnit = UNITS.has(unit) ? unit : 'legacy_story_node'
  const fallback = unknownSampling(safeUnit, returnedCount)
  if (!hasExactKeys(raw, ENVELOPE_KEYS)) return fallback
  if (raw.schema_version !== 'graph-sampling-provenance-v1') return fallback
  if (!['partial', 'unknown'].includes(raw.coverage_state)) return fallback
  if (raw.complete_graph_claim !== false) return fallback
  if (!Array.isArray(raw.components) || raw.components.length < 1 || raw.components.length > 8) {
    return fallback
  }
  const components = raw.components.map(normalizeComponent)
  if (components.some((component) => component === null)) return fallback
  if (new Set(components.map((component) => component.unit)).size !== components.length) {
    return fallback
  }
  const expectedCoverage = components.every((component) => component.state === 'bounded_partial')
    ? 'partial'
    : 'unknown'
  if (raw.coverage_state !== expectedCoverage) return fallback
  const target = components.find((component) => component.unit === safeUnit)
  if (!target || !isCount(returnedCount) || target.returned_count !== returnedCount) return fallback

  return Object.freeze({
    schema_version: 'graph-sampling-provenance-v1',
    coverage_state: expectedCoverage,
    components: Object.freeze(components),
    complete_graph_claim: false,
  })
}

export function graphSamplingNotice(story) {
  if (!story) return ''
  const unit = UNITS.has(story.sampling_unit)
    ? story.sampling_unit
    : story.source === 'l3-macro'
      ? 'l2_chain_node'
      : story.source === 'l2-chain'
        ? 'l15_segment_node'
        : 'legacy_story_node'
  const returnedCount = Array.isArray(story.nodes)
    ? story.nodes.length
    : Array.isArray(story.items)
      ? story.items.length
      : 0
  const sampling = normalizeGraphSampling(story.sampling, { unit, returnedCount })
  const component = sampling.components.find((item) => item.unit === unit)
  const unitLabel = UNIT_LABELS[unit]
  if (!component || sampling.coverage_state === 'unknown' || component.state === 'unknown') {
    return `图谱抽样范围未知；当前仅显示 ${returnedCount} 个已加载的 ${unitLabel}；规则、候选总数与未返回数量均未知；不得视为完整图。`
  }
  const rule = RULE_LABELS[component.selection_rule]
  const limit = component.limit === null ? '未声明' : String(component.limit)
  const reasons = component.reason_codes.map((reason) => REASON_LABELS[reason]).join('、')
  const isPageWindow = component.selection_rule === 'offset_page_stable_order'
  const coverageLoss = isPageWindow
    ? `本页未返回 ${component.excluded_count} 个（${component.overflow ? '存在分页窗口外候选' : '本页已覆盖当前候选范围'}）`
    : `排除 ${component.excluded_count} 个（${component.overflow ? '发生截断' : '当前候选范围内未截断'}）`
  return `当前展示 ${component.returned_count}/${component.evaluated_count} 个 ${unitLabel}；规则：${rule}；上限：${limit}；${coverageLoss}；原因：${reasons}；不是完整图。`
}
