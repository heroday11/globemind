// ============================================================
// Financial Terminal — 数据模型类型定义
// 前端字段名与后端 API 返回的 JSON 字段名保持一致（snake_case）
// ============================================================

/** K线 Bar */
export interface OhlcBar {
  time: number // unix 秒时间戳
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** 移动平均线点 */
export interface MaPoint {
  time: number
  value: number
}

/** 指数摘要卡片 */
export type FreshnessStatus = 'live' | 'delayed' | 'stale' | 'offline' | 'mock'
export type TrustStatus = 'trusted' | 'limited' | 'unavailable' | 'mock'
export type Computability = 'computable' | 'not_computable'
export type IndexAvailability = 'available' | 'not_computable'

export interface DerivedClaimIdentity {
  schema_version: 'financial-derived-claim-identity-v1'
  semantic_metric_id: string
  metric_class: 'composite_index'
  method_version: string
  model_version: string
  snapshot_id: string
  data_cutoff: string | null
  availability: IndexAvailability
}

export interface ShortSampleTrendDisclosure {
  schema_version: 'financial-short-sample-trend-v1'
  semantic_metric_id: string | null
  snapshot_id: string
  data_cutoff: string | null
  statistical_method_version: null
  approval_status: 'not_approved'
  trend_status: 'not_computable'
  baseline_period: {
    status: 'not_established'
    start: null
    end: null
  }
  sample_size: {
    status: 'provided_series_point_count' | 'not_available'
    count: number | null
    unit: 'provided_series_points'
    independence_status: 'not_validated'
  }
  uncertainty: {
    status: 'not_computable'
    confidence_level: null
    interval_lower: null
    interval_upper: null
    reason_code: 'UNCERTAINTY_METHOD_NOT_ESTABLISHED'
  }
  outlier_policy_status: 'not_established'
  reason_codes: readonly (
    | 'BASELINE_PERIOD_NOT_ESTABLISHED'
    | 'TREND_METHOD_NOT_APPROVED'
    | 'UNCERTAINTY_METHOD_NOT_ESTABLISHED'
    | 'BOUNDED_SERIES_POINTS_NOT_AVAILABLE'
  )[]
}

export interface TrustReason {
  code: string
  message: string
  group?: string
  required?: number
  available?: number
  actual?: number
  maximum?: number
  invalid_count?: number
  source_ids?: string[]
  index_ids?: string[]
}

export interface FinancialTrust {
  schema_version: string
  snapshot_id: string
  trust_status: TrustStatus
  freshness_status: FreshnessStatus
  computability: Computability
  computable: boolean
  data_as_of?: string | null
  evaluated_at?: string | null
  coverage_ratio: number
  minimum_coverage_ratio: number
  usable_sources: number
  source_total: number
  usable_source_ids: string[]
  unavailable_source_ids: string[]
  source_status: Partial<Record<FreshnessStatus, number>>
  critical_inputs?: Array<{
    id: string
    required: number
    available: number
    source_ids: string[]
    index_ids: string[]
    available_source_ids: string[]
    stale_source_ids: string[]
    as_of?: string | null
  }>
  model_version: string
  method_version: string
  composite_method_card?: unknown
  short_sample_trend_method_card?: unknown
  unavailable_reasons: TrustReason[]
  alerts_enabled: boolean
  method?: Record<string, unknown>
}

export interface IndexTrustMetadata {
  availability?: IndexAvailability
  trust_status?: TrustStatus
  freshness_status?: FreshnessStatus
  computability?: Computability
  computable?: boolean
  alerts_enabled?: boolean
  data_as_of?: string | null
  coverage_ratio?: number
  model_version?: string
  method_version?: string
  composite_method_card?: unknown
  schema_version?: string
  snapshot_id?: string
  unavailable_reasons?: TrustReason[]
  claim_id?: string | null
  claim_identity?: DerivedClaimIdentity | null
  claim_unavailable_reason?: 'SEMANTIC_METRIC_ID_INVALID' | 'DERIVED_CLAIM_CONTRACT_INVALID' | null
  citation_locator?: string | null
  citation_locator_state?: 'unavailable'
  citation_unavailable_reason?: 'VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED'
  trend_disclosure?: ShortSampleTrendDisclosure
}

export interface IndexCard {
  id: string
  name: string
  value: number | null
  change_pct: number | null
  spark: number[] // 迷你走势 24 个点
  source?: string
  metric_id?: string
  availability?: IndexAvailability
  trust_status?: TrustStatus
  freshness_status?: FreshnessStatus
  computability?: Computability
  computable?: boolean
  alerts_enabled?: boolean
  data_as_of?: string | null
  coverage_ratio?: number
  model_version?: string
  method_version?: string
  composite_method_card?: unknown
  schema_version?: string
  snapshot_id?: string
  unavailable_reasons?: TrustReason[]
  claim_id?: string | null
  claim_identity?: DerivedClaimIdentity | null
  claim_unavailable_reason?: 'SEMANTIC_METRIC_ID_INVALID' | 'DERIVED_CLAIM_CONTRACT_INVALID' | null
  citation_locator?: string | null
  citation_locator_state?: 'unavailable'
  citation_unavailable_reason?: 'VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED'
  trend_disclosure?: ShortSampleTrendDisclosure
}

/** 外部数据源状态 */
export interface DataSourceStatus {
  id: string
  name: string
  status: 'live' | 'degraded' | 'disabled' | 'mock' | 'unavailable'
  records?: number
  detail?: string
  cadence?: string
  url?: string
  latency_ms?: number | null
  last_updated?: string
  checked_at?: string
  freshness_status?: FreshnessStatus
  data_as_of?: string | null
  contribution_state?: 'usable' | 'not_usable'
  contribution_reason_code?: string | null
}

export type MetricCategory = 'politics' | 'economy' | 'science' | 'energy' | 'security' | 'society' | 'logistics'

export interface DataCoverage {
  series_total?: number
  watchlist_total?: number
  sources_total?: number
  live_sources?: number
  degraded_sources?: number
  disabled_sources?: number
  politics_security_series?: number
  near_realtime_series?: number
  ground_news_records?: number
  ground_news_latest_story_date?: string
  chart_latest_age_days?: number | null
  chart_points?: number
  coverage_ratio?: number
  minimum_coverage_ratio?: number
  usable_sources?: number
  source_status?: Partial<Record<FreshnessStatus, number>>
}

export interface SeriesPoint {
  time: number
  value: number
  samples?: number
}

export interface MetricSeries extends IndexTrustMetadata {
  id: string
  kind?: 'index' | 'metric'
  label: string
  unit?: string
  source?: string
  cadence?: string
  status?: DataSourceStatus['status']
  category?: MetricCategory
  region?: string
  description?: string
  points: SeriesPoint[]
  latest: number | null
  change_pct: number | null
}

/** 行情监视行 */
export interface WatchRow {
  symbol: string
  metric_id?: string
  label: string
  price: number
  change_pct: number
  category?: MetricCategory
  region?: string
  source?: string
  cadence?: string
  status?: DataSourceStatus['status']
  unit?: string
  description?: string
}

/** 预警严重等级 */
export type Severity = 'high' | 'medium' | 'low'

export type AlertTriageStatus =
  | 'open'
  | 'acknowledged'
  | 'escalated'
  | 'false_positive'
  | 'resolved'

export type AlertTriageAction =
  | 'acknowledge'
  | 'escalate'
  | 'mark_false_positive'
  | 'resolve'
  | 'postmortem'

export type FalsePositiveClassification =
  | 'data_quality'
  | 'duplicate_signal'
  | 'threshold_miscalibration'
  | 'known_activity'
  | 'insufficient_context'

export type EscalationTargetRole =
  | 'financial_duty_officer'
  | 'data_quality_reviewer'
  | 'research_lead'
  | 'security_duty_officer'

export type PostmortemOutcome =
  | 'confirmed_response'
  | 'process_improvement_identified'
  | 'no_follow_up_required'

export interface AlertTriageSummary {
  schema_version: 'financial-alert-triage-status-v1'
  alert_event_id: string
  status: AlertTriageStatus
  has_audit: boolean
  reviewed: boolean
  transition_count: number
  last_transition_at: string | null
  last_event_id: string | null
  last_event_sha256: string | null
  historical?: boolean
  mutations_enabled?: boolean
  operational_limitations: {
    sla: 'unavailable'
    notification_delivery: 'not_configured'
    institutional_incident_system: 'not_configured'
  }
}

export interface AlertTriageRedactedEvent {
  event_id: string
  occurred_at: string
  event_type: 'transition' | 'postmortem'
  action: AlertTriageAction
  from_status: AlertTriageStatus
  to_status: AlertTriageStatus
  reason: { sha256: string; length: number }
  false_positive_classification: FalsePositiveClassification | null
  escalation_target_role: EscalationTargetRole | null
  postmortem_outcome: PostmortemOutcome | null
  previous_event_id: string | null
  previous_event_sha256: string | null
  event_sha256: string
}

export interface AlertTriageDetail extends AlertTriageSummary {
  alert_history_sha256: string
  audit: AlertTriageRedactedEvent[]
  historical: boolean
  mutations_enabled: boolean
  trust_status: TrustStatus
  freshness_status: FreshnessStatus
  snapshot_id: string
}

export interface AlertTriageMutation {
  action: AlertTriageAction
  reason: string
  expected_previous_event_id: string | null
  expected_previous_event_sha256: string | null
  false_positive_classification?: FalsePositiveClassification
  escalation_target_role?: EscalationTargetRole
  postmortem_outcome?: PostmortemOutcome
}

export interface AlertTriageAdminEvent {
  event_id: string
  alert_event_id: string
  actor_user_id: number
  reason: string
  record_sha256: string
  [key: string]: unknown
}

export interface AlertTriageAdminDetail extends Omit<AlertTriageDetail, 'audit'> {
  audit: AlertTriageAdminEvent[]
}

/** 预警规则（数值分析预警页 — 实时监控面板） */
export interface AlertRule {
  id: string
  /** 指标名称，如"波动率指数" */
  metric: string
  /** 单位，如 "%"、"bps" */
  unit: string
  /** 当前值 */
  current: number
  /** 告警阈值 */
  threshold: number
  /** 基线值（正常水平） */
  baseline: number
  /** 严重等级 */
  severity: Severity
  /** 是否已突破阈值 */
  breached: boolean
  /** 趋势方向 */
  trend: 'up' | 'down' | 'flat'
}

/** 告警报告（告警历史 / 触发性告警） */
export interface AlertReport {
  id: string
  metric: string
  current: number
  threshold: number
  severity: Severity
  /** ISO 8601 时间字符串 */
  triggered_at: string
  message: string
  /** 关联事件标签 */
  eventTags?: string[]
  /** 公开的处置聚合；不含操作者和理由正文。 */
  triage?: AlertTriageSummary
}

/** Dashboard 完整响应 */
export interface DashboardData {
  mode?: 'live' | 'delayed' | 'historical' | 'unavailable' | 'mock' | 'mock-fallback'
  cache?: 'hit' | 'miss' | 'shared' | 'coalesced' | 'stale' | 'invalid'
  last_updated?: string
  bars: OhlcBar[]
  ma20: MaPoint[]
  ma50: MaPoint[]
  ma200: MaPoint[]
  indices: IndexCard[]
  watchlist: WatchRow[]
  series?: MetricSeries[]
  default_metric_id?: string
  sources?: DataSourceStatus[]
  alert_rules?: AlertRule[]
  coverage?: DataCoverage
  trust?: FinancialTrust
  trust_status?: TrustStatus
  freshness_status?: FreshnessStatus
  computability?: Computability
  computable?: boolean
  alerts_enabled?: boolean
  data_as_of?: string | null
  model_version?: string
  method_version?: string
  composite_method_card?: unknown
  short_sample_trend_method_card?: unknown
  schema_version?: string
  snapshot_id?: string
  unavailable_reasons?: TrustReason[]
  alerts_suppressed?: boolean
}

/** Alert 完整响应 */
export interface AlertData {
  rules: AlertRule[]
  history: AlertReport[]
  paused?: boolean
  trust?: FinancialTrust
  trust_status?: TrustStatus
  freshness_status?: FreshnessStatus
  computability?: Computability
  computable?: boolean
  alerts_enabled?: boolean
  data_as_of?: string | null
  coverage?: DataCoverage
  model_version?: string
  method_version?: string
  composite_method_card?: unknown
  short_sample_trend_method_card?: unknown
  schema_version?: string
  snapshot_id?: string
  unavailable_reasons?: TrustReason[]
}

export type AlertRulesData = Omit<AlertData, 'history'>
