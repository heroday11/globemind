import { normalizeStoryRelation } from '../../governance/storyRelations.js'

const UNKNOWN_SAMPLING = Object.freeze({
  schema_version: 'graph-sampling-provenance-v1',
  coverage_state: 'unknown',
  components: Object.freeze([]),
  complete_graph_claim: false,
})

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function countOrNull(value) {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0
    && value <= 2_147_483_647
    ? value
    : null
}

function normalizeTimelineSampling(value, returnedCount) {
  if (
    !isPlainObject(value)
    || value.schema_version !== 'graph-sampling-provenance-v1'
    || value.coverage_state !== 'partial'
    || value.complete_graph_claim !== false
    || !Array.isArray(value.components)
    || value.components.length !== 1
  ) return UNKNOWN_SAMPLING

  const component = value.components[0]
  if (!isPlainObject(component)) return UNKNOWN_SAMPLING
  const requested = countOrNull(component.requested_count)
  const evaluated = countOrNull(component.evaluated_count)
  const returned = countOrNull(component.returned_count)
  const excluded = countOrNull(component.excluded_count)
  const limit = countOrNull(component.limit)
  const reasons = Array.isArray(component.reason_codes)
    ? component.reason_codes.filter((reason) => typeof reason === 'string')
    : []
  const arithmeticIsValid = returned === returnedCount
    && evaluated !== null
    && evaluated >= returned
    && excluded === evaluated - returned
    && component.overflow === (excluded > 0)
    && (requested === null || requested >= returned)
    && (limit === null || limit >= returned)
  if (
    component.unit !== 'l15_segment_node'
    || component.state !== 'bounded_partial'
    || component.selection_rule !== 'ordered_chain_segments'
    || component.excluded_node_ids_disclosed !== false
    || !reasons.includes('GRAPH_COMPLETENESS_NOT_ESTABLISHED')
    || !arithmeticIsValid
  ) return UNKNOWN_SAMPLING

  return Object.freeze({
    schema_version: 'graph-sampling-provenance-v1',
    coverage_state: 'partial',
    components: Object.freeze([Object.freeze({
      unit: 'l15_segment_node',
      state: 'bounded_partial',
      requested_count: requested,
      evaluated_count: evaluated,
      returned_count: returned,
      excluded_count: excluded,
      limit,
      overflow: excluded > 0,
      selection_rule: 'ordered_chain_segments',
      reason_codes: Object.freeze([...new Set(reasons)]),
      excluded_node_ids_disclosed: false,
    })]),
    complete_graph_claim: false,
  })
}

export function normalizeGroundNewsTimeline(value) {
  const payload = isPlainObject(value) ? value : {}
  const chain = isPlainObject(payload.chain)
    ? { ...payload.chain, quality_score: null, chain_quality: null }
    : {}
  const nodes = (Array.isArray(payload.nodes) ? payload.nodes : []).map((node) => ({
    ...(isPlainObject(node) ? node : {}),
    source_count: countOrNull(node?.source_count),
    political_group_pct_reviewed_known_sources: {},
    blindspot: {
      score: null,
      level: 'unknown',
      reasons: [],
      evidence_locator: null,
      value_state: 'unknown',
      reason_code: 'EVIDENCE_LOCATOR_NOT_ESTABLISHED',
    },
  }))
  const edges = (Array.isArray(payload.edges) ? payload.edges : []).map((edge) => ({
    ...normalizeStoryRelation(edge),
    edge_weight: null,
    weight: null,
    title_similarity: null,
    shared_topic_count: null,
    shared_actor_count: null,
    gap_days: null,
  }))
  const sampling = normalizeTimelineSampling(payload.sampling, nodes.length)
  const component = sampling.coverage_state === 'partial' ? sampling.components[0] : null

  return {
    ...payload,
    chain,
    nodes,
    edges,
    sampling,
    articleCount: null,
    returnedNodeCount: nodes.length,
    totalNodeCount: component?.evaluated_count ?? null,
  }
}

export function timelineCountLabel(value, unit) {
  const count = countOrNull(value)
  if (count !== null) return `${count} ${unit}`
  if (String(unit).includes('新闻')) return '新闻数未知'
  if (String(unit).includes('信源')) return '信源数未知'
  return '节点总数未知'
}

export function timelineSamplingNotice(value) {
  const model = isPlainObject(value) && 'returnedNodeCount' in value
    ? value
    : normalizeGroundNewsTimeline(value)
  const component = model.sampling?.coverage_state === 'partial'
    ? model.sampling.components?.[0]
    : null
  if (!component) return '抽样范围未知；当前节点不得视为完整图。'
  const omitted = component.excluded_count > 0
    ? `；本次未返回 ${component.excluded_count} 个候选节点`
    : ''
  return `已返回 ${component.returned_count}/${component.evaluated_count} 个节点${omitted}；该时间线不是完整图。`
}

export function timelineQualityLabel(value) {
  void value
  return '派生链质量未知（方法、输入身份或证据不可用；非事实、非因果验证）'
}

function normalizedTitle(node) {
  return String(node?.display_title || node?.title || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
}

function fieldChanged(current, previous, field) {
  const currentValue = String(current?.[field] || '').trim()
  const previousValue = String(previous?.[field] || '').trim()
  return Boolean(currentValue || previousValue) && currentValue !== previousValue
}

export function timelineNodeResearchNote(node, previousNode = null) {
  if (!isPlainObject(node)) {
    return Object.freeze({ state: 'unknown', message: '节点信息不足，无法识别事实增量。' })
  }
  if (!isPlainObject(previousNode)) {
    return Object.freeze({
      state: 'baseline',
      message: '当前返回链的首个节点，仅作为比较起点，不代表事件真正起点。',
    })
  }

  const title = normalizedTitle(node)
  const previousTitle = normalizedTitle(previousNode)
  if (title && title === previousTitle) {
    return Object.freeze({
      state: 'possible_duplicate',
      message: '未发现标题层面的新增事实；可能是重复收录或再发布，需人工合并核验。',
    })
  }

  const changes = []
  if (fieldChanged(node, previousNode, 'event_action')) changes.push('事件动作字段')
  if (
    fieldChanged(node, previousNode, 'initiator')
    || fieldChanged(node, previousNode, 'target')
  ) changes.push('主体关系字段')
  if (fieldChanged(node, previousNode, 'story_angle')) changes.push('报道切面字段')
  if (
    fieldChanged(node, previousNode, 'start_date')
    || fieldChanged(node, previousNode, 'end_date')
  ) changes.push('时间窗口')

  if (!changes.length) {
    return Object.freeze({
      state: 'title_only',
      message: '标题发生变化，但结构化字段未显示事实增量；请对照原文确认。',
    })
  }
  return Object.freeze({
    state: 'candidate_delta',
    message: `${changes.join('、')}发生变化；这是待核验线索，不代表已确认的事件演化。`,
  })
}

export function timelineErrorMessage() {
  return '时间线暂时无法加载，请稍后重试。'
}
