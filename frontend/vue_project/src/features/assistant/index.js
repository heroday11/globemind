export { default as AssistantExperience } from './AssistantExperience.vue'
export { default as AssistantDrawer } from './AssistantDrawer.vue'

export {
  AssistantApiError,
  assistantApi,
  assistantErrorText,
  buildAssistantAuthHeaders,
  createAssistantApi,
} from './api.js'

export {
  normalizeAssistantMessageDto,
  normalizeAssistantProviderDto,
  normalizeAssistantSessionDto,
  normalizeAssistantSessionList,
  normalizeAssistantWorkspaceDto,
  normalizeAssistantWorkspaceList,
  normalizeBriefingScheduleDto,
  normalizeBriefingScheduleList,
} from './dto.js'

export {
  createRequestAbortScope,
  displayFolderName,
  formatAssistantDateTime,
  formatConversationTime,
  isAssistantAbortError,
  normalizeFolderName,
  normalizePageActionQuery,
  parseSseDataBlocks,
  pickSearchQueryFromPayload,
  safeParseJson,
} from './state.js'

export {
  BRIEFING_CADENCE_OPTIONS,
  BRIEFING_ASSURANCE_SCHEMA,
  BRIEFING_PERSPECTIVE_OPTIONS,
  BRIEFING_SCHEDULE_FORM_DEFAULTS,
  BRIEFING_STATUS_LABELS,
  BRIEFING_TIME_RANGE_OPTIONS,
  BRIEFING_WEEKDAY_OPTIONS,
  briefingCadenceLabel,
  briefingAssuranceLabel,
  briefingPlannedTimeLabel,
  briefingRecordedTimeLabel,
  briefingRunStatusLabel,
  briefingStatusLabel,
  buildAssistantDashboardTrend,
  buildBriefingPeriodicTasks,
  buildBriefingScheduleContext,
  buildBriefingSchedulePayload,
  calculateBriefingStats,
  calculateDashboardBarHeight,
  createBriefingScheduleForm,
  normalizeBriefingAssurance,
  normalizeBriefingRunDto,
  dashboardDateKey,
  dashboardShortDateLabel,
} from './briefing/model.js'

export {
  createBriefingScheduleService,
  createLatestBriefingScheduleLoader,
} from './briefing/service.js'

export {
  CHAT_STREAM_ERROR_FALLBACK,
  chatStreamEventError,
  createChatStreamMessage,
  createChatStreamState,
  finalizeChatStreamState,
  reduceChatStreamEvent,
} from './chat/reducer.js'

export {
  ChatStreamError,
  createChatStreamController,
  isChatStreamAbortError,
} from './chat/stream.js'

export {
  TEXT_PREVIEW_EXTENSIONS,
  isTextPreviewFile,
  normalizeWorkspaceFileList,
  selectedWorkspaceFilePaths,
  toggleAllWorkspaceFiles,
  toggleWorkspaceFileSelection,
  workspaceArchiveFilename,
  workspaceChildPath,
  workspaceDownloadFilename,
  workspaceFilePath,
  workspaceFilePreviewFromError,
  workspaceFilePreviewFromResponse,
  workspaceParentPath,
} from './workspace/model.js'

export { createAssistantWorkspaceController } from './workspace/controller.js'
export { triggerBrowserDownload } from './workspace/download.js'
export { createAssistantWorkspaceService } from './workspace/service.js'

export {
  REPORT_BRIEFING_QUESTIONS,
  REPORT_CONFIG_DEFAULTS,
  REPORT_INPUT_MODES,
  REPORT_OUTPUT_FORMATS,
  REPORT_STORAGE_KEY,
  REPORT_TEMPLATES,
  REPORT_TYPES,
  REPORT_TYPE_PROFILES,
  REPORT_WORKSPACE_NAME,
  buildConversationReportContext,
  buildPptMasterRequestMarkdown,
  buildReportFilename,
  buildReportPreparationBlock,
  buildReportPrompt,
  calculateReportEditorStats,
  compactReportContextList,
  compactReportMessage,
  createReportConfig,
  deriveReportTopic,
  loadReportHistory,
  normalizeReportRecord,
  reportFormatLabel,
  reportPerspectiveLabel,
  reportTemplateLabel,
  reportTimeRangeLabel,
  reportTypeLabel,
  reportTypeProfile,
  sanitizeReportFilename,
  saveReportHistory,
  selectedReportFormat,
} from './reports/model.js'

export { createAssistantReportArchiveService } from './reports/service.js'
export {
  ReportStreamError,
  createReportStreamController,
  reportStreamEventError,
} from './reports/stream.js'
