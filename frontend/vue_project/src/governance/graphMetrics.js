const METHOD_SCHEMA_VERSION = 'graph-metric-method-card-v1'

function methodCard(metricId, {
  methodVersion = null,
  formula = null,
  inputs = [],
  approvalState = 'not_approved',
  thresholdState = 'not_approved',
  outputUnit = null,
  identityRequirements = ['run_id', 'snapshot_id', 'data_cutoff'],
} = {}) {
  return Object.freeze({
    schema_version: METHOD_SCHEMA_VERSION,
    metric_id: metricId,
    method_version: methodVersion,
    formula,
    inputs: Object.freeze([...inputs]),
    input_identity_requirements: Object.freeze([...identityRequirements]),
    approval_state: approvalState,
    threshold_state: thresholdState,
    output_unit: outputUnit,
    release_rule: 'numeric_or_ranked_display_requires_exact_card_inputs_identity_and_evidence',
  })
}

function metric(metricId, displayName, {
  surfaces,
  publicFields,
  metricKind,
  card,
  valueState = 'unknown',
  evidenceState = 'unavailable',
  reasonCode = 'EVIDENCE_LOCATOR_NOT_ESTABLISHED',
}) {
  return Object.freeze({
    metric_id: metricId,
    display_name: displayName,
    surfaces: Object.freeze([...surfaces]),
    public_fields: Object.freeze([...publicFields]),
    metric_kind: metricKind,
    method_card: card,
    evidence_locator: null,
    evidence_state: evidenceState,
    value_state: valueState,
    fact_status: 'not_established',
    reason_code: reasonCode,
  })
}

const METRICS = Object.freeze([
  metric('story_graph.research_value', '研究价值', {
    surfaces: ['StoryGraphView.story_card', 'StoryGraphView.focus_sheet'],
    publicFields: ['research_value'],
    metricKind: 'derived_score',
    card: methodCard('story_graph.research_value', {
      methodVersion: 'story-graph-research-value-extracted-v1',
      formula: 'round(min(100, min(34, ln(1 + article_count) * 5.2) + min(22, ln(1 + segment_count + l2_chain_count) * 4.2) + min(14, ln(1 + date_span_days) * 2.5) + (quality_score * 20 if 0 < quality_score <= 1 else min(20, quality_score / 5)) + min(10, actor_count * 0.8 + topic_count * 0.4)))',
      inputs: ['article_count', 'segment_count', 'l2_chain_count', 'quality_score', 'date_span_days', 'actor_count', 'topic_count'],
      outputUnit: 'prototype_points_0_100',
    }),
  }),
  metric('story_graph.relation_strength', '关系强度', {
    surfaces: ['StoryGraphView.edge_focus', 'StoryGraphView.assistant_context'],
    publicFields: ['relation_strength'],
    metricKind: 'derived_label',
    card: methodCard('story_graph.relation_strength', {
      methodVersion: 'story-relation-strength-extracted-v1',
      formula: 'strong if edge_weight >= 0.75 or shared_actor_count + shared_topic_count >= 3; medium if edge_weight >= 0.55 or shared_actor_count + shared_topic_count >= 1; pending if relation_reason is non-empty and not temporal_overlap; else weak',
      inputs: ['edge_weight', 'shared_actor_count', 'shared_topic_count', 'relation_reason'],
      outputUnit: 'ordinal_label',
    }),
  }),
  metric('story_graph.edge_weight', '关系边权重', {
    surfaces: ['StoryGraph.current', 'StoryGraph.legacy', 'GroundNewsTimeline'],
    publicFields: ['edge_weight', 'weight'],
    metricKind: 'stored_derived_score',
    card: methodCard('story_graph.edge_weight', {
      inputs: ['stored_edge_weight'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('story_graph.layout_weight', '布局权重', {
    surfaces: ['StoryGraph.client_layout', 'StoryGraph.l3_layout_edge'],
    publicFields: ['layout_weight', 'synthetic_weight'],
    metricKind: 'layout_only',
    card: methodCard('story_graph.layout_weight', {
      methodVersion: 'story-graph-layout-weights-v1',
      formula: 'fixed layout constants by visual edge class: 0.36/0.66/0.72/0.82',
      inputs: ['layout_edge_class'],
      approvalState: 'not_applicable_layout_only',
      thresholdState: 'not_applicable_layout_only',
      outputUnit: 'layout_only',
      identityRequirements: ['layout_algorithm_version'],
    }),
    valueState: 'layout_only',
    evidenceState: 'not_applicable',
    reasonCode: 'LAYOUT_VALUE_NOT_ANALYTIC',
  }),
  metric('story_graph.related_story_score', '关联故事排序分', {
    surfaces: ['StoryGraph.related_stories'],
    publicFields: ['score'],
    metricKind: 'hidden_ranking_score',
    card: methodCard('story_graph.related_story_score', {
      inputs: ['stored_related_story_score'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('story_graph.quality_score', '链质量分', {
    surfaces: ['StoryGraph.l2', 'StoryGraph.l3', 'DataSearch.hierarchy_results'],
    publicFields: ['quality_score', 'chain_quality', 'importance_score'],
    metricKind: 'legacy_quality_score',
    card: methodCard('story_graph.quality_score', {
      inputs: ['quality_score', 'chain_quality', 'importance_score'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('ground_news.rank_score', '首页候选排序分', {
    surfaces: ['GroundNewsHome.lead', 'GroundNewsHome.topic_selection'],
    publicFields: ['rank_score'],
    metricKind: 'editorial_candidate_score',
    card: methodCard('ground_news.rank_score', {
      methodVersion: 'ground-news-rank-score-extracted-v1',
      formula: 'round(min(article_count,90)*1.25 + min(source_count,30)*2.8 + (max(0,28-min(age_days,28))*1.2 if dates valid else 0) + (max(0,30-abs(left_pct-right_pct))*0.16 if known_bias>0 else 0) + min(l2_chain_count,6)*5 + min(l2_quality_score,1)*8, 4)',
      inputs: ['article_count', 'source_count', 'story_date', 'latest_story_date', 'left_pct', 'right_pct', 'center_pct', 'state_aligned_pct', 'l2_chain_count', 'l2_quality_score'],
      outputUnit: 'prototype_rank_points',
    }),
    reasonCode: 'METHOD_NOT_APPROVED',
  }),
  metric('ground_news.blindspot_score', 'Blindspot 候选分', {
    surfaces: ['GroundNewsFeed', 'GroundNewsTimeline', 'GroundNewsDesk'],
    publicFields: ['blindspot_score', 'blindspot.score', 'blindspot.level'],
    metricKind: 'directory_composition_candidate_score',
    card: methodCard('ground_news.blindspot_score', {
      methodVersion: 'blindspot_v2',
      formula: 'score=max(0, abs(left_pct-right_pct) + (24 if one side<8 and the other>=8 else 0) + max(0,18-center_pct)*0.35 + min(max(source_count-2,0),18)*1.8 + state_aligned_pct*0.25 - min(18, unknown_source_count/max(source_count,1)*26)); level=insufficient_data if source_count<4 or reviewed_known_source_count<=0, else high>=55, medium>=32, watch>=18, low',
      inputs: ['left_pct', 'center_pct', 'right_pct', 'state_aligned_pct', 'source_count', 'reviewed_known_source_count', 'unknown_source_count', 'directory_low_or_unknown_label_pct'],
      outputUnit: 'prototype_candidate_points',
    }),
  }),
  metric('ground_news.timeline_quality', '时间线链质量', {
    surfaces: ['GroundNewsTimeline', 'GroundNewsDesk.l2_chain'],
    publicFields: ['quality_score', 'chain_quality'],
    metricKind: 'legacy_quality_score',
    card: methodCard('ground_news.timeline_quality', {
      inputs: ['quality_score', 'chain_quality'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('ground_news.event_research_value', '事件研究价值', {
    surfaces: ['GroundNewsDesk.event_value'],
    publicFields: ['event_value_score', 'event_value_label'],
    metricKind: 'derived_score',
    card: methodCard('ground_news.event_research_value', {
      methodVersion: 'ground-news-event-value-extracted-v1',
      formula: 'score=round(min(32,source_count*4) + min(24,article_count*1.2) + min(18,related_chain_count*6) + min(16,segment_count*2) + min(10,blindspot_score/10)); label=high_value if score>=72, track if score>=48, else observe',
      inputs: ['source_count', 'article_count', 'related_chain_count', 'segment_count', 'blindspot_score'],
      outputUnit: 'prototype_points_0_100',
    }),
  }),
  metric('ground_news.coverage_signal', '覆盖信号', {
    surfaces: ['GroundNewsDesk.coverage_signal', 'GroundNewsDesk.coverage_cards'],
    publicFields: ['coverage_signal', 'known_bias_pct', 'coverage_gap'],
    metricKind: 'derived_label',
    card: methodCard('ground_news.coverage_signal', {
      methodVersion: 'ground-news-coverage-signal-extracted-v1',
      formula: 'single_source if source_count<=1 or analysis_status=single_source; low_coverage if source_count<4 or analysis_status=low_source_count; rating_gap if no known directory composition or analysis_status=missing_political_ratings; blindspot_candidate if (left_pct<8 and right_pct>=20) or (right_pct<8 and left_pct>=20); else multi_source_coverage',
      inputs: ['source_count', 'analysis_status', 'left_pct', 'right_pct', 'known_directory_label_pct'],
      outputUnit: 'ordinal_label',
    }),
  }),
  metric('graph_briefing.quality_score', 'Graph Briefing 质量别名', {
    surfaces: ['GraphBriefing.macro', 'GraphBriefing.micro'],
    publicFields: ['quality_score', 'china_index_avg', 'chain_quality'],
    metricKind: 'legacy_alias_score',
    card: methodCard('graph_briefing.quality_score', {
      inputs: ['quality_score'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('graph_briefing.membership_score', 'Graph Briefing 成员分', {
    surfaces: ['GraphBriefing.micro'],
    publicFields: ['membership_score', 'importance_score'],
    metricKind: 'legacy_alias_score',
    card: methodCard('graph_briefing.membership_score', {
      inputs: ['importance_score'],
      outputUnit: 'unknown',
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('graph_briefing.opinion_aggregate', 'Graph Briefing 舆情聚合', {
    surfaces: ['GraphBriefing.briefing'],
    publicFields: ['avg_sentiment_score', 'sentiment_distribution', 'topic_distribution'],
    metricKind: 'model_aggregate',
    card: methodCard('graph_briefing.opinion_aggregate', {
      inputs: ['stance_score', 'event_family', 'linked_news_membership'],
      outputUnit: 'unknown',
      identityRequirements: ['model_id', 'model_version', 'method_version', 'snapshot_id', 'data_cutoff'],
    }),
    reasonCode: 'FORMULA_NOT_ESTABLISHED',
  }),
  metric('graph_sampling.coverage', '图抽样覆盖', {
    surfaces: ['StoryGraph', 'GroundNewsTimeline', 'GraphBriefing'],
    publicFields: ['sampling'],
    metricKind: 'delegated_contract',
    card: methodCard('graph_sampling.coverage', {
      methodVersion: 'graph-sampling-provenance-v1',
      formula: 'validated returned/evaluated/excluded arithmetic per bounded component',
      inputs: ['sampling'],
      approvalState: 'delegated_contract_required',
      thresholdState: 'not_applicable',
      outputUnit: 'mechanical_response_counts',
      identityRequirements: ['sampling_component_unit', 'selection_rule'],
    }),
    valueState: 'delegated_gate_required',
    evidenceState: 'delegated',
    reasonCode: 'GRAPH_SAMPLING_CONTRACT_REQUIRED',
  }),
  metric('ground_news.source_profile_labels', '来源目录标签', {
    surfaces: ['GroundNewsSource', 'GroundNewsDesk.source_profile'],
    publicFields: ['source_profile'],
    metricKind: 'delegated_contract',
    card: methodCard('ground_news.source_profile_labels', {
      methodVersion: 'ground-news-source-profile-method-card-v1',
      formula: 'field-level controlled method disposition',
      inputs: ['source_profile.method_card', 'source_profile.field_dispositions'],
      approvalState: 'delegated_contract_required',
      thresholdState: 'not_applicable',
      outputUnit: 'directory_labels_not_fact_accuracy',
      identityRequirements: ['profile_version', 'method_ids'],
    }),
    valueState: 'delegated_gate_required',
    evidenceState: 'delegated',
    reasonCode: 'SOURCE_PROFILE_CONTRACT_REQUIRED',
  }),
])

export const GRAPH_METRIC_INVENTORY = Object.freeze({
  schema_version: 'graph-metric-inventory-v1',
  inventory_id: 'globemind-public-graph-metrics-v1',
  scope: Object.freeze(['graph_briefing', 'ground_news', 'story_graph']),
  complete_runtime_surface_claim: false,
  metrics: METRICS,
})

const METRIC_BY_ID = new Map(METRICS.map((item) => [item.metric_id, item]))

function unknownProjection(metricId, reasonCode = 'METRIC_NOT_IN_BOUNDED_INVENTORY') {
  return Object.freeze({
    schema_version: 'graph-metric-projection-v1',
    metric_id: metricId || null,
    value: null,
    value_state: 'unknown',
    evidence_locator: null,
    evidence_state: 'unavailable',
    fact_status: 'not_established',
    usable_for_ranking: false,
    usable_as_fact: false,
    reason_code: reasonCode,
  })
}

export function projectGraphMetric(metricId, payload = {}) {
  void payload
  const entry = typeof metricId === 'string' ? METRIC_BY_ID.get(metricId) : null
  if (!entry) return unknownProjection(null)
  return Object.freeze({
    schema_version: 'graph-metric-projection-v1',
    metric_id: entry.metric_id,
    value: null,
    value_state: entry.value_state,
    evidence_locator: null,
    evidence_state: entry.evidence_state,
    fact_status: 'not_established',
    usable_for_ranking: false,
    usable_as_fact: false,
    reason_code: entry.reason_code,
  })
}

export function graphMetricPresentation(metricId, payload = {}) {
  const entry = METRIC_BY_ID.get(metricId)
  const projection = projectGraphMetric(metricId, payload)
  const valueLabel = projection.value_state === 'layout_only'
    ? '仅用于布局'
    : projection.value_state === 'delegated_gate_required'
      ? '需独立合同校验'
      : '未知'
  return Object.freeze({
    ...projection,
    label: entry?.display_name || '未登记指标',
    valueLabel,
  })
}

function boundedInputValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value).slice(0, 80)
  if (typeof value === 'string' && value.length > 0 && value.length <= 160) return value
  if (typeof value === 'boolean') return String(value)
  return null
}

export function graphMetricExplanation(metricId, payload = {}) {
  const entry = METRIC_BY_ID.get(metricId)
  if (!entry) {
    return Object.freeze({
      ...graphMetricPresentation(metricId, payload),
      formula: '公式未建立',
      method_version: null,
      approval_state: 'not_approved',
      inputs: Object.freeze([]),
      identity_requirements: Object.freeze([]),
      evidence: Object.freeze({ state: 'unavailable', locator: null }),
    })
  }
  const rawInputs = payload && typeof payload === 'object' && !Array.isArray(payload)
    && payload.inputs && typeof payload.inputs === 'object' && !Array.isArray(payload.inputs)
    ? payload.inputs
    : {}
  const inputs = entry.method_card.inputs.map((field) => {
    const value = boundedInputValue(rawInputs[field])
    return Object.freeze({
      field,
      state: value === null ? 'unavailable' : 'provided_unverified',
      value,
    })
  })
  return Object.freeze({
    ...graphMetricPresentation(metricId, payload),
    formula: entry.method_card.formula || '公式未建立',
    method_version: entry.method_card.method_version,
    approval_state: entry.method_card.approval_state,
    inputs: Object.freeze(inputs),
    identity_requirements: entry.method_card.input_identity_requirements,
    evidence: Object.freeze({ state: entry.evidence_state, locator: null }),
  })
}

export function graphMetricMethodCard(metricId) {
  return METRIC_BY_ID.get(metricId)?.method_card || null
}
