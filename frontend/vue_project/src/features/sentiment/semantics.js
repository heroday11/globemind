export const OPINION_SEMANTIC_SCHEMA_VERSION = 'opinion-semantic-dimensions-v1'
export const OPINION_SEMANTIC_CONTRACT_VERSION = 'opinion-three-axis-method-v1'

const VALIDATED_CONTRACT = Symbol('validated-opinion-semantic-contract')
const VALIDATED_DIMENSIONS = Symbol('validated-opinion-semantic-dimensions')

const STANCE_CATEGORIES = Object.freeze(['supportive', 'neutral', 'critical', 'unknown'])
const TONE_CATEGORIES = Object.freeze(['positive', 'neutral', 'negative', 'mixed', 'unknown'])
const IMPACT_DIRECTIONS = Object.freeze(['positive', 'neutral', 'negative', 'mixed', 'unknown'])
const STANCE_SCALES = Object.freeze({
  article_stance: Object.freeze({
    unit: 'dimensionless', minimum: -1, maximum: 1, neutralBand: [-0.15, 0.15],
  }),
  aggregate_stance_index: Object.freeze({
    unit: 'index_points', minimum: -100, maximum: 100, neutralBand: [-15, 15],
  }),
})

function jsonEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right)
}

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  return jsonEqual(Object.keys(value).sort(), [...keys].sort())
}

function validMethodCard(contract, trust) {
  const stance = contract?.dimensions?.stance
  const tone = contract?.dimensions?.tone
  const impact = contract?.dimensions?.impact
  const combination = contract?.combination
  const assurance = contract?.assurance
  const legacyAliases = contract?.legacy_aliases
  const rules = combination?.rules
  if (
    !contract || typeof contract !== 'object' || Array.isArray(contract)
    || !exactKeys(contract, [
      'schema_version', 'contract_version', 'dimensions', 'combination',
      'assurance', 'legacy_aliases',
    ])
    || !exactKeys(contract.dimensions, ['stance', 'tone', 'impact'])
    || !exactKeys(stance, [
      'meaning', 'categories', 'score_scales', 'source_model', 'source_table', 'availability',
    ])
    || !exactKeys(stance?.score_scales, ['article_stance', 'aggregate_stance_index'])
    || !exactKeys(stance?.score_scales?.article_stance, [
      'unit', 'minimum', 'maximum', 'neutral_band',
    ])
    || !exactKeys(stance?.score_scales?.aggregate_stance_index, [
      'unit', 'minimum', 'maximum', 'neutral_band',
    ])
    || !exactKeys(tone, [
      'meaning', 'categories', 'score_scale', 'source_model', 'source_table', 'availability',
    ])
    || !exactKeys(tone?.score_scale, ['state', 'unit'])
    || !exactKeys(impact, [
      'meaning', 'directions', 'score_scale', 'source_model', 'source_table', 'availability',
    ])
    || !exactKeys(impact?.score_scale, ['state', 'unit'])
    || !exactKeys(combination, ['scope', 'state', 'combined_score', 'rules'])
    || !exactKeys(rules, [
      'stance_from_tone', 'stance_from_impact', 'tone_from_stance',
      'tone_from_impact', 'impact_from_stance', 'impact_from_tone',
    ])
    || !exactKeys(assurance, [
      'quality_state', 'fact_truth_state', 'upstream_axis_independence_state',
    ])
    || !exactKeys(legacyAliases, [
      'sentiment', 'tone', 'impact_index', 'daily_impact', 'china_importance', 'sentiment_filter',
    ])
    || contract.schema_version !== OPINION_SEMANTIC_SCHEMA_VERSION
    || contract.contract_version !== OPINION_SEMANTIC_CONTRACT_VERSION
    || stance?.meaning !== 'targeted attitude toward the named target'
    || !jsonEqual(stance?.categories, STANCE_CATEGORIES)
    || stance?.source_table !== 'public.china_opinion_article_scores'
    || stance?.availability !== 'available_when_trust_gate_passes'
    || typeof stance?.source_model !== 'string' || !stance.source_model
    || stance.source_model !== trust?.model_version
    || stance.source_model !== trust?.method_version
    || !jsonEqual(stance?.score_scales?.article_stance, {
      unit: 'dimensionless', minimum: -1, maximum: 1, neutral_band: [-0.15, 0.15],
    })
    || !jsonEqual(stance?.score_scales?.aggregate_stance_index, {
      unit: 'index_points', minimum: -100, maximum: 100, neutral_band: [-15, 15],
    })
    || tone?.meaning !== 'affective or linguistic tone independent of target'
    || !jsonEqual(tone?.categories, TONE_CATEGORIES)
    || !jsonEqual(tone?.score_scale, { state: 'not_established', unit: 'unknown' })
    || tone?.source_model !== null || tone?.source_table !== null
    || tone?.availability !== 'not_available'
    || impact?.meaning !== 'observed or modeled downstream effect independent of stance'
    || !jsonEqual(impact?.directions, IMPACT_DIRECTIONS)
    || !jsonEqual(impact?.score_scale, { state: 'not_established', unit: 'unknown' })
    || impact?.source_model !== null || impact?.source_table !== null
    || impact?.availability !== 'not_available'
    || combination?.scope !== 'response_projection'
    || combination?.state !== 'not_combined'
    || combination?.combined_score !== null
    || !rules || Object.keys(rules).length !== 6
    || Object.values(rules).some((allowed) => allowed !== false)
    || assurance?.quality_state !== 'not_established'
    || assurance?.fact_truth_state !== 'not_verified'
    || assurance?.upstream_axis_independence_state !== 'not_established'
    || !jsonEqual(legacyAliases, {
      sentiment: 'deprecated_ambiguous_output_suppressed',
      tone: 'deprecated_ui_style_alias_suppressed',
      impact_index: 'deprecated_ambiguous_output_suppressed',
      daily_impact: 'deprecated_ambiguous_output_suppressed',
      china_importance: 'deprecated_stance_derived_output_suppressed',
      sentiment_filter: 'legacy_wire_name_for_stance_filter_only',
    })
  ) return false
  return true
}

function markSemanticDimensionsInvalid(value, validation) {
  if (Array.isArray(value)) {
    value.forEach((item) => markSemanticDimensionsInvalid(item, validation))
    return
  }
  if (!value || typeof value !== 'object') return
  if (value.semantic_dimensions && typeof value.semantic_dimensions === 'object') {
    Object.defineProperty(value.semantic_dimensions, VALIDATED_DIMENSIONS, {
      configurable: true,
      enumerable: false,
      value: validation,
    })
  }
  Object.values(value).forEach((item) => markSemanticDimensionsInvalid(item, validation))
}

function markRecord(record, validation, sourceField = null, readBoundValue = null) {
  const dimensions = record?.semantic_dimensions
  if (!dimensions || typeof dimensions !== 'object' || Array.isArray(dimensions)) return
  const allowedSourceFields = sourceField ? [sourceField] : []
  Object.defineProperty(dimensions, VALIDATED_DIMENSIONS, {
    configurable: true,
    enumerable: false,
    value: {
      ...validation,
      allowedSourceFields,
      readBoundValue: sourceField && typeof readBoundValue === 'function'
        ? (candidate) => candidate === sourceField ? readBoundValue() : null
        : null,
    },
  })
}

export function enforceOpinionSemanticBoundary(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return payload
  const contract = payload.semantic_contract
  const trust = payload.trust || payload.meta?.trust
  const valid = validMethodCard(contract, trust)
  const validation = {
    valid,
    stanceModel: valid ? contract.dimensions.stance.source_model : null,
    allowedSourceFields: [],
  }
  if (contract && typeof contract === 'object' && !Array.isArray(contract)) {
    Object.defineProperty(contract, VALIDATED_CONTRACT, {
      configurable: true,
      enumerable: false,
      value: validation,
    })
  }
  markSemanticDimensionsInvalid(payload, { ...validation, valid: false })
  if (payload.metric_id === 'weighted_target_stance_index') {
    markRecord(
      payload,
      validation,
      'values[-1]',
      () => Array.isArray(payload.values) && payload.values.length
        ? payload.values[payload.values.length - 1]
        : null,
    )
  } else if (payload.summary && typeof payload.summary === 'object') {
    markRecord(
      payload,
      validation,
      'summary.current_index',
      () => payload.summary?.current_index,
    )
  } else {
    markRecord(payload, validation)
  }
  markRecord(
    payload.summary,
    validation,
    'summary.current_index',
    () => payload.summary?.current_index,
  )
  for (const item of Array.isArray(payload.target_indices) ? payload.target_indices : []) {
    if (String(item?.label || '').toUpperCase() === 'CN') {
      markRecord(item, validation, 'target_indices.CN.value', () => item?.value)
    } else {
      markRecord(item, validation)
    }
  }
  for (const item of Array.isArray(payload.briefs) ? payload.briefs : []) {
    markRecord(item, validation, 'briefs.stance_score', () => item?.stance_score)
  }
  for (const item of Array.isArray(payload.news) ? payload.news : []) {
    markRecord(item, validation, 'news.stance_score', () => item?.stance_score)
  }
  for (const item of Array.isArray(payload.events) ? payload.events : []) {
    markRecord(
      item,
      validation,
      'events.weighted_stance_index',
      () => item?.weighted_stance_index,
    )
  }
  for (const item of Array.isArray(payload.sub_events) ? payload.sub_events : []) {
    markRecord(
      item,
      validation,
      'sub_events.weighted_stance_index',
      () => item?.weighted_stance_index,
    )
  }
  markRecord(
    payload.top_event,
    validation,
    'top_event.avg_stance',
    () => payload.top_event?.avg_stance,
  )
  for (const item of Array.isArray(payload.families) ? payload.families : []) {
    markRecord(item, validation, 'families.avg_stance', () => item?.avg_stance)
  }
  for (const rows of Object.values(payload.dimensions || {})) {
    for (const item of Array.isArray(rows) ? rows : []) {
      markRecord(
        item,
        validation,
        'dimensions.weighted_stance_index',
        () => item?.weighted_stance_index,
      )
    }
  }
  return payload
}

function unknownStance(reasonCode = 'SEMANTIC_CONTRACT_NOT_VALIDATED') {
  return {
    state: 'unknown',
    category: 'unknown',
    score: null,
    scale: null,
    unit: 'unknown',
    sourceField: null,
    sourceModel: null,
    reasonCode,
  }
}

function unknownTone(reasonCode = 'TONE_MODEL_NOT_AVAILABLE') {
  return {
    state: 'unknown',
    category: 'unknown',
    score: null,
    unit: 'unknown',
    sourceField: null,
    sourceModel: null,
    reasonCode,
  }
}

function unknownImpact(reasonCode = 'IMPACT_MODEL_NOT_AVAILABLE') {
  return {
    state: 'unknown',
    direction: 'unknown',
    score: null,
    unit: 'unknown',
    sourceField: null,
    sourceModel: null,
    reasonCode,
  }
}

function stanceCategory(score, neutralBand) {
  if (score < neutralBand[0]) return 'critical'
  if (score > neutralBand[1]) return 'supportive'
  return 'neutral'
}

const STANCE_SOURCE_BINDINGS = Object.freeze({
  'values[-1]': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'summary.current_index': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'target_indices.CN.value': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'briefs.stance_score': Object.freeze({
    scale: 'article_stance',
  }),
  'news.stance_score': Object.freeze({
    scale: 'article_stance',
  }),
  'events.weighted_stance_index': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'sub_events.weighted_stance_index': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'dimensions.weighted_stance_index': Object.freeze({
    scale: 'aggregate_stance_index',
  }),
  'top_event.avg_stance': Object.freeze({
    scale: 'article_stance',
  }),
  'families.avg_stance': Object.freeze({
    scale: 'article_stance',
  }),
})

function validatedStance(raw, validation) {
  if (!validation?.valid) return unknownStance()
  if (
    !raw || typeof raw !== 'object' || Array.isArray(raw)
    || !exactKeys(raw, [
      'state', 'category', 'score', 'scale', 'unit', 'source_field',
      'source_model', 'reason_code',
    ])
  ) {
    return unknownStance('STANCE_METADATA_MISSING')
  }
  if (raw.state === 'unknown') {
    if (raw.category !== 'unknown' || raw.score !== null) {
      return unknownStance('STANCE_UNKNOWN_STATE_CONFLICT')
    }
    return unknownStance(String(raw.reason_code || 'STANCE_VALUE_UNAVAILABLE'))
  }
  const scale = STANCE_SCALES[raw.scale]
  const score = finiteNumber(raw.score)
  const binding = STANCE_SOURCE_BINDINGS[raw.source_field]
  const boundScore = binding && typeof validation.readBoundValue === 'function'
    ? finiteNumber(validation.readBoundValue(raw.source_field))
    : null
  if (
    raw.state !== 'available'
    || !scale
    || score === null
    || score < scale.minimum
    || score > scale.maximum
    || raw.unit !== scale.unit
    || raw.category !== stanceCategory(score, scale.neutralBand)
    || !STANCE_CATEGORIES.includes(raw.category)
    || !binding
    || !validation.allowedSourceFields.includes(raw.source_field)
    || binding.scale !== raw.scale
    || boundScore === null
    || boundScore !== score
    || raw.source_model !== validation.stanceModel
    || raw.reason_code !== null
  ) return unknownStance('STANCE_METADATA_CONFLICT')
  return {
    state: 'available',
    category: raw.category,
    score,
    scale: raw.scale,
    unit: raw.unit,
    sourceField: raw.source_field,
    sourceModel: raw.source_model,
    reasonCode: null,
  }
}

function validatedUnknownTone(raw, validation) {
  if (
    !validation?.valid
    || !raw || typeof raw !== 'object' || Array.isArray(raw)
    || !exactKeys(raw, [
      'state', 'category', 'score', 'unit', 'source_field', 'source_model', 'reason_code',
    ])
    || raw.state !== 'unknown'
    || raw.category !== 'unknown'
    || raw.score !== null
    || raw.unit !== 'unknown'
    || raw.source_field !== null
    || raw.source_model !== null
  ) return unknownTone('TONE_METADATA_CONFLICT')
  return unknownTone(String(raw.reason_code || 'TONE_MODEL_NOT_AVAILABLE'))
}

function validatedUnknownImpact(raw, validation) {
  if (
    !validation?.valid
    || !raw || typeof raw !== 'object' || Array.isArray(raw)
    || !exactKeys(raw, [
      'state', 'direction', 'score', 'unit', 'source_field', 'source_model', 'reason_code',
    ])
    || raw.state !== 'unknown'
    || raw.direction !== 'unknown'
    || raw.score !== null
    || raw.unit !== 'unknown'
    || raw.source_field !== null
    || raw.source_model !== null
  ) return unknownImpact('IMPACT_METADATA_CONFLICT')
  return unknownImpact(String(raw.reason_code || 'IMPACT_MODEL_NOT_AVAILABLE'))
}

export function opinionSemanticSnapshot(record) {
  const dimensions = record?.semantic_dimensions
  const validation = dimensions?.[VALIDATED_DIMENSIONS]
  if (!validation?.valid || !exactKeys(dimensions, ['stance', 'tone', 'impact'])) {
    return {
      stance: unknownStance(),
      tone: unknownTone('SEMANTIC_CONTRACT_NOT_VALIDATED'),
      impact: unknownImpact('SEMANTIC_CONTRACT_NOT_VALIDATED'),
    }
  }
  return {
    stance: validatedStance(dimensions.stance, validation),
    tone: validatedUnknownTone(dimensions.tone, validation),
    impact: validatedUnknownImpact(dimensions.impact, validation),
  }
}

export function opinionSemanticMethodSnapshot(payload) {
  const contract = payload?.semantic_contract
  const validation = contract?.[VALIDATED_CONTRACT]
  if (!validation?.valid) {
    return {
      valid: false,
      contractVersion: '--',
      stanceModel: '--',
      combinationScope: 'unknown',
      combinationState: 'unknown',
      qualityState: 'not_established',
      factTruthState: 'not_verified',
      upstreamAxisIndependenceState: 'not_established',
    }
  }
  return {
    valid: true,
    contractVersion: contract.contract_version,
    stanceModel: contract.dimensions.stance.source_model,
    combinationScope: contract.combination.scope,
    combinationState: contract.combination.state,
    qualityState: contract.assurance.quality_state,
    factTruthState: contract.assurance.fact_truth_state,
    upstreamAxisIndependenceState: contract.assurance.upstream_axis_independence_state,
  }
}

export function formatSemanticAxis(axis, { includeUnit = false } = {}) {
  if (axis?.state !== 'available' || !Number.isFinite(axis.score)) return '未知'
  const precision = axis.scale === 'article_stance' ? 2 : 1
  const value = `${axis.score > 0 ? '+' : ''}${axis.score.toFixed(precision)}`
  if (!includeUnit) return value
  return axis.unit === 'index_points' ? `${value} 指数点` : `${value}（无量纲）`
}

export function semanticStanceClass(record) {
  const stance = opinionSemanticSnapshot(record).stance
  if (stance.state !== 'available') return null
  if (stance.category === 'supportive') return 'semantic-stance--supportive'
  if (stance.category === 'critical') return 'semantic-stance--critical'
  return 'semantic-stance--neutral'
}

export function semanticTrendProjection(payload) {
  const empty = {
    dates: [],
    values: [],
    heat: [],
    meta: payload?.meta && typeof payload.meta === 'object' ? payload.meta : {},
    metric_id: payload?.metric_id,
    semantic_contract: payload?.semantic_contract,
    semantic_dimensions: payload?.semantic_dimensions,
  }
  const stance = opinionSemanticSnapshot(payload).stance
  const dates = Array.isArray(payload?.dates) ? payload.dates : []
  const values = Array.isArray(payload?.values) ? payload.values.map(finiteNumber) : []
  if (
    stance.state !== 'available'
    || stance.scale !== 'aggregate_stance_index'
    || payload?.metric_id !== 'weighted_target_stance_index'
    || !dates.length
    || dates.length !== values.length
    || values.some((value) => value === null || value < -100 || value > 100)
    || Math.abs(values[values.length - 1] - stance.score) > 1e-9
  ) return empty
  return {
    ...empty,
    dates,
    values,
    heat: Array.isArray(payload?.heat) ? payload.heat : [],
  }
}
