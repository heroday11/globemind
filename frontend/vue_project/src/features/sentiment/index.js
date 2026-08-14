export { default as SentimentFilterToggle } from './SentimentFilterToggle.vue'

export {
  SENTIMENT_ENDPOINTS,
  SentimentApiError,
  buildSentimentAuthHeaders,
  buildSentimentUrl,
  createSentimentApi,
  createSentimentEndpoints,
  sentimentApi,
} from './api.js'

export {
  OPINION_CACHE_TTL_MS,
  OPINION_REFRESH_INTERVAL_MS,
  OVERVIEW_CACHE_KEY,
  TREND_CACHE_KEY,
  createSentimentSnapshotCache,
} from './cache.js'

export {
  DIAGNOSTIC_TABS,
  SEARCH_PAGE_SIZE_OPTIONS,
  SEARCH_PUBLISH_TIME_OPTIONS,
  SENTIMENT_OPTIONS,
  buildDateNewsQuery,
  buildFeedbackDto,
  buildInsightNewsQuery,
  buildMacroEventClustersQuery,
  buildOverviewQuery,
  buildSentimentSearchDto,
  buildTrendQuery,
  normalizeDateNewsResponse,
  normalizeFavoriteIds,
  normalizeInsightNewsResponse,
  normalizeMacroEventClustersResponse,
  normalizeSearchHistoryResponse,
  normalizeSearchResult,
  normalizeSentimentSearchResponse,
  normalizeTrendResponse,
} from './dto.js'

export {
  CORRECTION_LABELS,
  FALLBACK_OVERVIEW_SUMMARY,
  OPINION_FRESHNESS_MAX_AGE_DAYS,
  OPINION_TRUST_SCHEMA_VERSION,
  addLocalSearchHistory,
  applyFavoriteResult,
  briefClass,
  briefSeverityLabel,
  buildDateExplanation,
  dimensionGroups,
  formatCompactCount,
  formatDateYmd,
  formatDimensionLabel,
  formatFamilyName,
  formatIndexValue,
  formatOverviewEntity,
  formatOverviewEventTitle,
  formatOverviewTime,
  formatSearchSnippet,
  invalidateOpinionPayload,
  maxOverviewTagCount,
  mergeFavoriteIds,
  overviewMetrics,
  overviewReadableIndices,
  overviewScoreClass,
  overviewSummary,
  overviewTargetIndices,
  overviewTopEventTitle,
  overviewTrendClass,
  opinionTrustSnapshot,
  qualitySnapshot,
  resolveAutoEndDate,
  sanitizeOpinionPayload,
  tagBarWidth,
  tagStyle,
  validateOpinionClaimContract,
} from './presentation.js'

export {
  createLatestSentimentRequest,
  isSentimentAbortError,
} from './request.js'

export {
  OPINION_SEMANTIC_CONTRACT_VERSION,
  OPINION_SEMANTIC_SCHEMA_VERSION,
  enforceOpinionSemanticBoundary,
  formatSemanticAxis,
  opinionSemanticMethodSnapshot,
  opinionSemanticSnapshot,
  semanticStanceClass,
  semanticTrendProjection,
} from './semantics.js'

export {
  PRESET_DAYS_MAP,
  buildSearchChartMarkPoints,
  buildSentimentChartOption,
  currentRangeDays,
  dataZoomRange,
  findAnomalyPoints,
  normalizeResultDate,
  rangeIndexes,
  resolveChartPointDate,
  selectDatePoint,
  sparklinePoints,
  trimTrendData,
  yAxisRange,
} from './trend.js'
