export const STORY_RELATION_SEMANTICS_VERSION = 'story-relation-semantics-v1'

const PUBLIC_EDGE_TYPES = new Set([
  'parallel',
  'macro_sequence',
  'pair_sequence',
  'branch_sequence',
  'continuation',
  'continued',
  'progression',
  'transition',
  'response',
  'same_thread',
  'preview_to_event',
  'event_to_outcome',
  'outcome_to_context',
  'chain_start',
  'start',
  'escalation',
  'resolution',
  'de_escalation',
  'diplomacy',
  'market_reaction',
  'analysis_context',
  'context',
  'branch',
  'pair_family',
  'relation_unknown',
])

const TEMPORAL_TYPES = new Set([
  'macro_sequence',
  'pair_sequence',
  'branch_sequence',
  'continuation',
  'continued',
  'progression',
  'transition',
  'response',
  'same_thread',
  'preview_to_event',
  'event_to_outcome',
  'outcome_to_context',
  'chain_start',
  'start',
])

const EVENT_SIGNAL_TYPES = new Set([
  'escalation',
  'resolution',
  'de_escalation',
  'diplomacy',
  'market_reaction',
  'analysis_context',
])

const CONTEXT_TYPES = new Set(['context', 'branch', 'pair_family'])
const LAYOUT_TYPES = new Set(['macro_sequence', 'branch_sequence', 'branch', 'context'])
const DERIVATIONS = new Set([
  'stored_derived_relation',
  'computed_bridge',
  'layout_sequence',
  'legacy_payload_unknown',
])

const REQUIRED_KEYS = [
  'schema_version',
  'ontology_state',
  'public_edge_type',
  'relation_kind',
  'temporal_basis',
  'causal_status',
  'influence_status',
  'evidence_role',
  'derivation',
  'reason_code',
  'source_reason_disclosed',
]

const REASON_CODES = new Set([
  'TEMPORAL_OVERLAP_NOT_INFLUENCE',
  'TEMPORAL_ORDER_NOT_CAUSAL',
  'CONTEXT_ASSOCIATION_NOT_CAUSAL',
  'DERIVED_EVENT_SIGNAL_NOT_CAUSAL',
  'LAYOUT_EDGE_NOT_RELATION_EVIDENCE',
  'UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE',
  'UNKNOWN_LEGACY_RELATION_SCHEMA',
  'RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID',
  'SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED',
])

const PUBLIC_REASONS = Object.freeze({
  temporal_overlap: '仅表示时间重叠，不代表影响或因果',
  temporal_sequence: '仅表示时间排序或相邻，不代表影响或因果',
  contextual_association: '仅表示派生关联线索，不代表影响或因果',
  event_progression_signal: '仅表示事件进展分类信号，不代表影响或因果',
  layout_only: '仅用于图形布局，不代表影响、相关或因果',
  unknown: '关系类型未知，不可解释为影响或因果',
})

const DISPLAY_LABELS = Object.freeze({
  parallel: '时间重叠（非影响/因果）',
  macro_sequence: '时间推进（非因果）',
  pair_sequence: '时间配对（非因果）',
  branch_sequence: '支线排序（非因果）',
  continuation: '延续分类信号（非因果）',
  continued: '延续分类信号（非因果）',
  progression: '进展分类信号（非因果）',
  transition: '转折分类信号（非因果）',
  response: '回应分类信号（非因果）',
  same_thread: '同线索关联（非因果）',
  preview_to_event: '时间前后关系（非因果）',
  event_to_outcome: '时间后续关系（非因果）',
  outcome_to_context: '后续背景关系（非因果）',
  chain_start: '链起点',
  start: '链起点',
  escalation: '升级分类信号（非因果）',
  resolution: '缓和分类信号（非因果）',
  de_escalation: '降级分类信号（非因果）',
  diplomacy: '外交分类信号（非因果）',
  market_reaction: '市场相关进展（非因果）',
  analysis_context: '分析背景关联（非因果）',
  context: '背景关联（非因果）',
  branch: '分支关联（非因果）',
  pair_family: '同类关联（非因果）',
  relation_unknown: '关系类型未知（不可作影响/因果）',
})

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value) {
  const keys = Object.keys(value).sort()
  return keys.length === REQUIRED_KEYS.length
    && keys.every((key, index) => key === [...REQUIRED_KEYS].sort()[index])
}

function hasValidCombination(semantics) {
  const {
    ontology_state: state,
    public_edge_type: edgeType,
    relation_kind: relationKind,
    temporal_basis: temporalBasis,
    evidence_role: evidenceRole,
    derivation,
    reason_code: reasonCode,
  } = semantics
  if (state === 'explicit_unknown') {
    return edgeType === 'relation_unknown'
      && relationKind === 'unknown'
      && temporalBasis === 'unknown'
      && evidenceRole === 'unknown'
      && [
        'UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE',
        'UNKNOWN_LEGACY_RELATION_SCHEMA',
        'RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID',
        'SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED',
      ].includes(reasonCode)
  }
  if (state !== 'bounded' || edgeType === 'relation_unknown') return false
  if (relationKind === 'temporal_overlap') {
    return edgeType === 'parallel'
      && temporalBasis === 'overlap'
      && evidenceRole === 'derived_signal'
      && reasonCode === 'TEMPORAL_OVERLAP_NOT_INFLUENCE'
  }
  if (relationKind === 'temporal_sequence') {
    return TEMPORAL_TYPES.has(edgeType)
      && temporalBasis === 'ordered_or_adjacent'
      && evidenceRole === 'derived_signal'
      && reasonCode === 'TEMPORAL_ORDER_NOT_CAUSAL'
  }
  if (relationKind === 'event_progression_signal') {
    return EVENT_SIGNAL_TYPES.has(edgeType)
      && temporalBasis === 'not_applicable'
      && evidenceRole === 'derived_signal'
      && reasonCode === 'DERIVED_EVENT_SIGNAL_NOT_CAUSAL'
  }
  if (relationKind === 'contextual_association') {
    return CONTEXT_TYPES.has(edgeType)
      && temporalBasis === 'not_applicable'
      && evidenceRole === 'derived_signal'
      && reasonCode === 'CONTEXT_ASSOCIATION_NOT_CAUSAL'
  }
  if (relationKind === 'layout_only') {
    return LAYOUT_TYPES.has(edgeType)
      && temporalBasis === 'not_applicable'
      && evidenceRole === 'layout_only'
      && derivation === 'layout_sequence'
      && reasonCode === 'LAYOUT_EDGE_NOT_RELATION_EVIDENCE'
  }
  return false
}

export function isStoryRelationSemantics(value, edgeType) {
  return isPlainObject(value)
    && exactKeys(value)
    && value.schema_version === STORY_RELATION_SEMANTICS_VERSION
    && PUBLIC_EDGE_TYPES.has(value.public_edge_type)
    && value.public_edge_type === edgeType
    && value.causal_status === 'not_established'
    && value.influence_status === 'not_established'
    && value.source_reason_disclosed === false
    && DERIVATIONS.has(value.derivation)
    && REASON_CODES.has(value.reason_code)
    && hasValidCombination(value)
}

export function unknownStoryRelationSemantics(
  reasonCode = 'RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID',
) {
  const safeReason = [
    'UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE',
    'UNKNOWN_LEGACY_RELATION_SCHEMA',
    'RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID',
    'SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED',
  ].includes(reasonCode)
    ? reasonCode
    : 'RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID'
  return Object.freeze({
    schema_version: STORY_RELATION_SEMANTICS_VERSION,
    ontology_state: 'explicit_unknown',
    public_edge_type: 'relation_unknown',
    relation_kind: 'unknown',
    temporal_basis: 'unknown',
    causal_status: 'not_established',
    influence_status: 'not_established',
    evidence_role: 'unknown',
    derivation: 'legacy_payload_unknown',
    reason_code: safeReason,
    source_reason_disclosed: false,
  })
}

export function buildLayoutStoryRelationSemantics(edgeType) {
  if (!LAYOUT_TYPES.has(edgeType)) {
    return unknownStoryRelationSemantics('SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED')
  }
  return Object.freeze({
    schema_version: STORY_RELATION_SEMANTICS_VERSION,
    ontology_state: 'bounded',
    public_edge_type: edgeType,
    relation_kind: 'layout_only',
    temporal_basis: 'not_applicable',
    causal_status: 'not_established',
    influence_status: 'not_established',
    evidence_role: 'layout_only',
    derivation: 'layout_sequence',
    reason_code: 'LAYOUT_EDGE_NOT_RELATION_EVIDENCE',
    source_reason_disclosed: false,
  })
}

export function normalizeStoryRelation(value) {
  const edge = isPlainObject(value) ? value : {}
  const rawEdgeType = typeof edge.edge_type === 'string' ? edge.edge_type : ''
  const semantics = isStoryRelationSemantics(edge.relation_semantics, rawEdgeType)
    ? Object.freeze({ ...edge.relation_semantics })
    : unknownStoryRelationSemantics()
  const suppressedKeys = new Set(['edge_type', 'type', 'relation_reason', 'relation_semantics'])
  const safeFields = Object.fromEntries(
    Object.entries(edge).filter(([key]) => !suppressedKeys.has(key)),
  )
  return {
    ...safeFields,
    edge_type: semantics.public_edge_type,
    relation_reason: PUBLIC_REASONS[semantics.relation_kind],
    relation_semantics: semantics,
  }
}

export function normalizeLayoutStoryRelation(value) {
  const edge = isPlainObject(value) ? value : {}
  const requestedType = typeof edge.edge_type === 'string' ? edge.edge_type : ''
  const semantics = buildLayoutStoryRelationSemantics(requestedType)
  return normalizeStoryRelation({
    ...edge,
    edge_type: semantics.public_edge_type,
    relation_semantics: semantics,
  })
}

export function storyRelationLabel(value) {
  const edgeType = typeof value === 'string' && PUBLIC_EDGE_TYPES.has(value)
    ? value
    : 'relation_unknown'
  return DISPLAY_LABELS[edgeType]
}

export function storyRelationReason(value) {
  const semantics = isPlainObject(value?.relation_semantics)
    ? value.relation_semantics
    : null
  return PUBLIC_REASONS[semantics?.relation_kind] || PUBLIC_REASONS.unknown
}
