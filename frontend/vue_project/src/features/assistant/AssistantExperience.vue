<script setup>
import { computed, ref, onMounted, onUnmounted, reactive, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  BookOpen,
  BriefcaseBusiness,
  ChevronDown,
  FileText,
  FolderOpen,
  LayoutDashboard,
  MapPinned,
  MessageSquareText,
  MoreHorizontal,
  Newspaper,
  Paperclip,
  Plus,
  Search,
  SendHorizontal,
  SlidersHorizontal,
  Star,
  TrendingUp,
  UsersRound,
} from 'lucide-vue-next'
import { loadItemsForReportTopic } from '@/utils/reportCenterData'
import { getFavoritesMap, getReportFavoritesForTopic } from '@/utils/reportFavorites'
import { getToken, getAuthChangedEventName } from '@/utils/auth'
import { searchModeDisclosure } from '@/governance/searchQuerySemantics.js'
import {
  assistantApi,
  assistantErrorText as errorText,
} from './api.js'
import {
  normalizeAssistantMessageDto,
  normalizeAssistantSessionDto,
  normalizeAssistantSessionList,
  normalizeBriefingScheduleDto,
} from './dto.js'
import {
  BRIEFING_CADENCE_OPTIONS as briefingCadenceOptions,
  BRIEFING_PERSPECTIVE_OPTIONS as briefingPerspectiveOptions,
  BRIEFING_TIME_RANGE_OPTIONS as briefingTimeRangeOptions,
  BRIEFING_WEEKDAY_OPTIONS as briefingWeekdayOptions,
  briefingAssuranceLabel,
  briefingPlannedTimeLabel,
  briefingRecordedTimeLabel,
  briefingRunStatusLabel,
  briefingStatusLabel,
  buildAssistantDashboardTrend,
  buildBriefingPeriodicTasks,
  buildBriefingScheduleContext,
  buildBriefingSchedulePayload as createBriefingSchedulePayload,
  calculateBriefingStats,
  calculateDashboardBarHeight,
  createBriefingScheduleForm,
} from './briefing/model.js'
import {
  createBriefingScheduleService,
  createLatestBriefingScheduleLoader,
} from './briefing/service.js'
import { createAssistantWorkspaceController } from './workspace/controller.js'
import { triggerBrowserDownload } from './workspace/download.js'
import { isTextPreviewFile } from './workspace/model.js'
import { createAssistantWorkspaceService } from './workspace/service.js'
import {
  createChatStreamMessage,
  createChatStreamState,
  finalizeChatStreamState,
  reduceChatStreamEvent,
} from './chat/reducer.js'
import {
  createChatStreamController,
  isChatStreamAbortError,
} from './chat/stream.js'
import {
  REPORT_BRIEFING_QUESTIONS as reportBriefingQuestions,
  REPORT_INPUT_MODES as reportInputModes,
  REPORT_OUTPUT_FORMATS as reportOutputFormats,
  REPORT_TEMPLATES as reportTemplates,
  REPORT_TYPES as reportTypes,
  REPORT_WORKSPACE_NAME,
  buildConversationReportContext,
  buildReportPrompt as createReportPrompt,
  calculateReportEditorStats,
  createReportConfig,
  deriveReportTopic as deriveReportTopicFromState,
  loadReportHistory,
  normalizeReportRecord,
  reportFormatLabel,
  reportTypeLabel,
  reportTypeProfile,
  saveReportHistory,
  selectedReportFormat as findReportFormat,
} from './reports/model.js'
import { createAssistantReportArchiveService } from './reports/service.js'
import { createReportStreamController } from './reports/stream.js'
import {
  displayFolderName,
  formatAssistantDateTime as formatDateTime,
  formatConversationTime as formatConvTime,
  normalizeFolderName,
  pickSearchQueryFromPayload,
  safeParseJson,
} from './state.js'
import KbPanel from './components/KbPanel.vue'
import SitesMembersPanel from './components/SitesMembersPanel.vue'
import MarkdownRenderer from './components/MarkdownRenderer.vue'

const REPORT_CONTEXT_STORAGE_KEY = 'data_assistant_report_context_v1'
const ASSISTANT_AUTORUN_CONTEXT_KEY = 'data_assistant_autorun_context_v1'
const FAVORITE_THEME_NAME_KEY = 'data_search_theme_name_v1'
const FAVORITE_THEME_LIST_KEY = 'data_search_themes_list_v1'
const PINNED_FAVORITE_FOLDER_KEY = 'data_assistant_pinned_favorite_folder_v1'
const SELECTED_SKILLS_KEY = 'globemind_academic_data_selected_skills_v1'
const DB_STORAGE_KEY = 'globemind_academic_data_database_cards_v1'
const DEFAULT_FOLDER_NAME = '默认工作夹'
/** 与后端 AssistantChatRequest.message 上限对齐，预留用户问题空间 */
const MAX_MESSAGE_LEN = 3800

const props = defineProps({
  embedded: {
    type: Boolean,
    default: false,
  },
  pageSkill: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['page-action'])
const emittedPageActionKeys = new Set()

const router = useRouter()
const route = useRoute()
const isAuthenticated = ref(Boolean(getToken()))

function showError(message, fallback = '操作失败') {
  ElMessage.error(String(message || fallback))
}

function requestAssistantLogin() {
  try {
    window.dispatchEvent(new CustomEvent('showLoginModal'))
  } catch {
    router.push({ path: '/login', query: { redirect: route.fullPath } })
  }
}

function openDisplaySettings() {
  router.push({ path: '/user-center/personal-center', query: { tab: 'display' } })
}

// --- 定期简报 / 智能体报告 ---
const activeTopTab = ref('history') // briefing | agentReport | history
const topTabs = [
  { key: 'briefing', label: '定期简报' },
  { key: 'history', label: '历史会话' },
  { key: 'agentReport', label: '智能体报告' },
]

const sideNavItems = [
  { key: 'switch', label: '工作区', icon: BriefcaseBusiness },
  { key: 'dashboard', label: '看板', icon: LayoutDashboard },
  { key: 'favorites', label: '收藏', icon: Star },
  { key: 'chat', label: '对话', icon: MessageSquareText },
  { key: 'kb', label: '知识库', icon: BookOpen },
  { key: 'members', label: '成员', icon: UsersRound },
]
const activeSideNav = ref('chat')

function selectSideNav(key) {
  if (props.embedded && key !== 'chat') return
  activeSideNav.value = key
  activeTopTab.value = 'history'
}

function selectTopTab(key) {
  if (props.embedded && key !== 'history') return
  activeTopTab.value = key
  if (key === 'history') activeSideNav.value = 'chat'
  if (key === 'briefing') fetchBriefingSchedules()
}

function enforceEmbeddedChatState() {
  if (!props.embedded) return
  if (activeSideNav.value !== 'chat') activeSideNav.value = 'chat'
  if (activeTopTab.value !== 'history') activeTopTab.value = 'history'
}

const briefingSchedules = ref([])
const briefingLoading = ref(false)
const briefingSaving = ref(false)
const briefingRunningId = ref('')
const briefingError = ref('')
const selectedBriefingId = ref('')
const briefingScheduleForm = reactive(createBriefingScheduleForm())
const briefingScheduleService = createBriefingScheduleService(assistantApi)
const briefingScheduleLoader = createLatestBriefingScheduleLoader(briefingScheduleService)
let briefingIdentityGeneration = 0

function currentBriefingIdentity(generation) {
  return generation === briefingIdentityGeneration && Boolean(getToken())
}

const selectedBriefingSchedule = computed(() => (
  briefingSchedules.value.find((item) => String(item.id) === String(selectedBriefingId.value)) || null
))

const periodicTasks = computed(() => (
  buildBriefingPeriodicTasks(briefingSchedules.value, formatDateTime)
))

const briefingStats = computed(() => calculateBriefingStats(briefingSchedules.value))

// ---- 侧导航各页面状态 ----
// 看板
const dashboardStats = computed(() => {
  const trend = buildAssistantDashboardTrend({
    conversations: conversations.value,
    reports: reportHistory.value,
    schedules: briefingSchedules.value,
  })
  return {
    totalTasks: briefingStats.value.total,
    activeAlerts: briefingStats.value.enabled,
    reportsGenerated: reportHistory.value.length,
    dataSources: knowledgeContextCount.value + (pinnedWorkspace.value ? 1 : 0) + (pinnedFavoriteFolder.value ? 1 : 0),
    weeklyTrend: trend.points,
    weeklyMax: trend.max,
    weeklyTotal: trend.total,
  }
})

function dashboardBarHeight(value) {
  return calculateDashboardBarHeight(value, dashboardStats.value.weeklyMax)
}

// 收藏文件夹：与 /data-service/data-search 使用同一份本地收藏
const favoriteFolderVersion = ref(0)
const activeFavoriteFolder = ref(DEFAULT_FOLDER_NAME)
const pinnedFavoriteFolder = ref('')
const selectedHermesSkills = ref([])
const selectedHermesDatabases = ref([])
const knowledgeContextCount = computed(() => selectedHermesSkills.value.length + selectedHermesDatabases.value.length)

function resetBriefingForm() {
  selectedBriefingId.value = ''
  Object.assign(briefingScheduleForm, createBriefingScheduleForm())
}

function fillBriefingForm(item) {
  if (!item) {
    resetBriefingForm()
    return
  }
  selectedBriefingId.value = String(item.id || '')
  Object.assign(briefingScheduleForm, createBriefingScheduleForm(item))
}

function selectBriefingSchedule(id) {
  const schedule = briefingSchedules.value.find((item) => String(item.id) === String(id))
  fillBriefingForm(schedule)
}

function openBriefingTask(id) {
  selectTopTab('briefing')
  selectBriefingSchedule(id)
}

function syncBriefingSchedule(saved) {
  if (!saved?.id) return
  const normalized = normalizeBriefingScheduleDto(saved)
  const id = normalized.id
  const idx = briefingSchedules.value.findIndex((item) => String(item.id) === id)
  if (idx >= 0) {
    briefingSchedules.value.splice(idx, 1, normalized)
  } else {
    briefingSchedules.value.unshift(normalized)
  }
  fillBriefingForm(normalized)
}

function buildBriefingSchedulePayload() {
  refreshKnowledgeContextFromStorage()
  const context = buildBriefingScheduleContext({
    pinnedWorkspaceName: pinnedWorkspace.value?.name || '',
    favoriteFolderName: pinnedFavoriteFolder.value
      ? displayFolderName(pinnedFavoriteFolder.value)
      : '',
    favoriteItems: pinnedFavoriteFolder.value
      ? pinnedFavoriteCards.value.slice(0, 24).map(compactFavoriteMaterial)
      : [],
    skills: selectedHermesSkills.value,
    databaseCards: selectedHermesDatabases.value,
  })
  return createBriefingSchedulePayload(briefingScheduleForm, context)
}

async function fetchBriefingSchedules() {
  if (!getToken() || props.embedded) {
    briefingScheduleLoader.invalidate()
    briefingSchedules.value = []
    resetBriefingForm()
    briefingLoading.value = false
    briefingError.value = ''
    return
  }
  briefingLoading.value = true
  briefingError.value = ''
  const result = await briefingScheduleLoader.load()
  if (!result.current) return
  if (result.error) {
    briefingError.value = errorText(result.error, '读取定时任务失败')
  } else {
    briefingSchedules.value = result.rows
    if (!selectedBriefingId.value && briefingSchedules.value.length) {
      fillBriefingForm(briefingSchedules.value[0])
    }
  }
  briefingLoading.value = false
}

async function saveBriefingSchedule({ silent = false } = {}) {
  if (!getToken()) {
    briefingError.value = '请先登录后配置定时简报'
    return null
  }
  const payload = buildBriefingSchedulePayload()
  if (!payload.title || !payload.topic) {
    briefingError.value = '请填写任务名称和报告主题'
    return null
  }
  briefingSaving.value = true
  briefingError.value = ''
  briefingScheduleLoader.invalidate()
  briefingLoading.value = false
  const identityGeneration = briefingIdentityGeneration
  try {
    const id = selectedBriefingId.value
    const saved = await briefingScheduleService.save(id, payload)
    if (!currentBriefingIdentity(identityGeneration)) return null
    syncBriefingSchedule(saved)
    if (!silent) ElMessage.success('定时任务已保存')
    return saved
  } catch (e) {
    if (currentBriefingIdentity(identityGeneration)) {
      briefingError.value = errorText(e, '保存定时任务失败')
    }
    return null
  } finally {
    if (currentBriefingIdentity(identityGeneration)) briefingSaving.value = false
  }
}

function addScheduledReportHistory(schedule, fileInfo) {
  if (!fileInfo?.file_name) return
  const createdAt = new Date().toISOString()
  const record = normalizeReportRecord({
    id: `scheduled-report-${Date.now()}`,
    type: schedule?.report_type || 'brief',
    topic: schedule?.topic || schedule?.title || '定时简报',
    timeRange: schedule?.time_range || '24h',
    perspective: schedule?.perspective || '综合研判',
    template: 'brief',
    createdAt,
    status: 'done',
    workspace: fileInfo.workspace || REPORT_WORKSPACE_NAME,
    fileName: fileInfo.file_name,
    filePath: fileInfo.file_path || `${REPORT_WORKSPACE_NAME}/${fileInfo.file_name}`,
  })
  if (!record) return
  reportHistory.value = [record, ...reportHistory.value.filter((item) => item.filePath !== record.filePath)].slice(0, 80)
  saveReportHistory(reportHistory.value)
}

async function runBriefingSchedule(item = null) {
  if (!getToken()) {
    briefingError.value = '请先登录后运行定时简报'
    return
  }
  let target = item || selectedBriefingSchedule.value
  if (!target) {
    target = await saveBriefingSchedule({ silent: true })
    if (!target) return
  }
  briefingRunningId.value = String(target.id)
  briefingError.value = ''
  briefingScheduleLoader.invalidate()
  briefingLoading.value = false
  const identityGeneration = briefingIdentityGeneration
  try {
    const result = await briefingScheduleService.run(target.id)
    if (!currentBriefingIdentity(identityGeneration)) return
    const updated = result?.schedule
    if (updated) syncBriefingSchedule(updated)
    addScheduledReportHistory(updated || target, result?.file)
    await fetchWorkspaces()
    ElMessage.success('AI 草稿已生成，需人工审阅')
  } catch (e) {
    if (!currentBriefingIdentity(identityGeneration)) return
    await fetchBriefingSchedules()
    briefingError.value = errorText(e, '运行定时任务失败')
  } finally {
    if (currentBriefingIdentity(identityGeneration)) briefingRunningId.value = ''
  }
}

async function toggleBriefingSchedule(item) {
  if (!item) return
  fillBriefingForm(item)
  briefingScheduleForm.enabled = !item.enabled
  await saveBriefingSchedule({ silent: true })
}

async function deleteBriefingSchedule(item) {
  if (!item?.id) return
  try {
    await ElMessageBox.confirm(`删除定时任务「${item.title || item.topic}」？`, '删除定时任务', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  briefingScheduleLoader.invalidate()
  briefingLoading.value = false
  const identityGeneration = briefingIdentityGeneration
  try {
    await briefingScheduleService.remove(item.id)
    if (!currentBriefingIdentity(identityGeneration)) return
    briefingSchedules.value = briefingSchedules.value.filter((row) => String(row.id) !== String(item.id))
    if (String(selectedBriefingId.value) === String(item.id)) {
      fillBriefingForm(briefingSchedules.value[0] || null)
    }
    ElMessage.success('定时任务已删除')
  } catch (e) {
    if (currentBriefingIdentity(identityGeneration)) {
      briefingError.value = errorText(e, '删除定时任务失败')
    }
  }
}

function readFavoriteFolderList() {
  const storedList = safeParseJson(localStorage.getItem(FAVORITE_THEME_LIST_KEY), [])
  const folderSet = new Set(Array.isArray(storedList) ? storedList.map(normalizeFolderName) : [])
  const current = normalizeFolderName(localStorage.getItem(FAVORITE_THEME_NAME_KEY))
  folderSet.add(current)
  const favMap = getFavoritesMap()
  for (const key of Object.keys(favMap)) folderSet.add(normalizeFolderName(key))
  return [...folderSet].filter(Boolean)
}

const favoriteFolderNames = computed(() => {
  favoriteFolderVersion.value
  return readFavoriteFolderList()
})

watch(favoriteFolderNames, (names) => {
  if (!names.includes(activeFavoriteFolder.value)) {
    activeFavoriteFolder.value = names[0] || DEFAULT_FOLDER_NAME
  }
}, { immediate: true })

const favoriteFolderCards = computed(() => {
  const favMap = getFavoritesMap()
  const names = favoriteFolderNames.value
  return names.map((name) => {
    const list = Array.isArray(favMap[name]) ? favMap[name] : []
    const latest = list[0] || null
    return {
      name,
      displayName: displayFolderName(name),
      count: list.length,
      latestTitle: latest?.title || '',
      latestTime: formatDateTime(latest?.displayTime || latest?.pub_time || latest?.time),
    }
  })
})

const activeFavoriteCards = computed(() => {
  favoriteFolderVersion.value
  return getReportFavoritesForTopic(activeFavoriteFolder.value)
})

const pinnedFavoriteCards = computed(() => {
  favoriteFolderVersion.value
  if (!pinnedFavoriteFolder.value) return []
  return getReportFavoritesForTopic(pinnedFavoriteFolder.value)
})

const pinnedFavoriteSummary = computed(() => {
  const folder = pinnedFavoriteFolder.value
  return {
    folder,
    displayName: folder ? displayFolderName(folder) : '',
    count: pinnedFavoriteCards.value.length,
  }
})

function compactFavoriteMaterial(item) {
  return {
    id: item.id,
    title: item.title || '无标题',
    source: item.source || '未知来源',
    time: formatDateTime(item.displayTime || item.pub_time || item.time),
    url: item.url || item.link || '',
    abstract: item.displayDesc || item.abstract || item.desc || '',
  }
}

function refreshKnowledgeContextFromStorage() {
  const skillIds = safeParseJson(localStorage.getItem(SELECTED_SKILLS_KEY), []).filter(Boolean)
  const dbCards = safeParseJson(localStorage.getItem(DB_STORAGE_KEY), [])
  selectedHermesSkills.value = skillIds.map((id) => ({ id: String(id), name: String(id), type: 'skill' }))
  selectedHermesDatabases.value = Array.isArray(dbCards) ? dbCards : []
}

const favoriteSummary = computed(() => {
  const folders = favoriteFolderCards.value.length
  const articles = favoriteFolderCards.value.reduce((sum, folder) => sum + folder.count, 0)
  const latest = activeFavoriteCards.value[0]
  return {
    folders,
    articles,
    activeCount: activeFavoriteCards.value.length,
    latestTitle: latest?.title || '暂无收藏新闻',
  }
})

function selectFavoriteFolder(name) {
  activeFavoriteFolder.value = normalizeFolderName(name)
}

function activateFavoriteFolder(name) {
  const folder = normalizeFolderName(name)
  activeFavoriteFolder.value = folder
  pinnedFavoriteFolder.value = folder
  localStorage.setItem(PINNED_FAVORITE_FOLDER_KEY, folder)
  activeSourcePanel.value = null
  leftDrawerOpen.value = false
  rightDrawerOpen.value = true
}

function clearPinnedFavoriteFolder() {
  pinnedFavoriteFolder.value = ''
  localStorage.removeItem(PINNED_FAVORITE_FOLDER_KEY)
}

function handleKnowledgeContextChanged(payload = {}) {
  selectedHermesSkills.value = Array.isArray(payload.skills) ? payload.skills : []
  selectedHermesDatabases.value = Array.isArray(payload.databaseCards) ? payload.databaseCards : []
  if (selectedHermesSkills.value.length || selectedHermesDatabases.value.length) {
    activeSourcePanel.value = null
    rightDrawerOpen.value = true
    leftDrawerOpen.value = false
  }
}

function removeHermesSkillFromContext(skillId) {
  const target = String(skillId || '')
  const stored = safeParseJson(localStorage.getItem(SELECTED_SKILLS_KEY), [])
  const next = Array.isArray(stored) ? stored.filter((id) => String(id) !== target) : []
  localStorage.setItem(SELECTED_SKILLS_KEY, JSON.stringify(next))
  selectedHermesSkills.value = selectedHermesSkills.value.filter((skill) => String(skill.id || skill.name) !== target)
}

function removeHermesDatabaseFromContext(cardId) {
  const target = String(cardId || '')
  const stored = safeParseJson(localStorage.getItem(DB_STORAGE_KEY), [])
  const next = Array.isArray(stored) ? stored.filter((card) => String(card?.id || card?.name || card?.host) !== target) : []
  localStorage.setItem(DB_STORAGE_KEY, JSON.stringify(next))
  selectedHermesDatabases.value = selectedHermesDatabases.value.filter((card) => String(card?.id || card?.name || card?.host) !== target)
}

function clearKnowledgeContext() {
  localStorage.setItem(SELECTED_SKILLS_KEY, JSON.stringify([]))
  localStorage.setItem(DB_STORAGE_KEY, JSON.stringify([]))
  selectedHermesSkills.value = []
  selectedHermesDatabases.value = []
}

async function clearAllHermesContext() {
  if (pinnedWorkspace.value) await togglePinWorkspace(pinnedWorkspace.value)
  clearPinnedFavoriteFolder()
  clearKnowledgeContext()
}

function manageHermesContext(target = 'chat') {
  activeSourcePanel.value = null
  selectSideNav(target)
  leftDrawerOpen.value = false
  rightDrawerOpen.value = false
}

function askAboutFavoriteFolder() {
  const cards = activeFavoriteCards.value.slice(0, 8)
  if (!cards.length) return
  selectSideNav('chat')
  const titles = cards.map((item, idx) => `${idx + 1}. ${item.title || '无标题'}`).join('\n')
  inputText.value = `请基于「${displayFolderName(activeFavoriteFolder.value)}」文件夹中的收藏新闻做一次结构化研判：\n${titles}`
}

async function openKbFilePreview(f, categoryId) {
  if (!isTextPreviewFile(f.name)) return
  filePreviewVisible.value = true
  filePreviewTitle.value = f.name
  filePreviewFullPath.value = categoryId ? `${categoryId}/${f.name}` : f.name
  filePreviewContent.value = ''
  fileOriginalContent.value = ''
  filePreviewLoading.value = true
  try {
    const json = await assistantApi.readKbFile(f.name, categoryId)
    if (json.ok) {
      filePreviewContent.value = json.data.content
      fileOriginalContent.value = json.data.content
    } else {
      filePreviewContent.value = `// 无法读取文件: ${json.error}`
      fileOriginalContent.value = filePreviewContent.value
    }
  } catch (e) {
    filePreviewContent.value = `// 读取失败: ${e.message}`
    fileOriginalContent.value = filePreviewContent.value
  } finally {
    filePreviewLoading.value = false
  }
}

// 站点 & 成员（已移入 SitesMembersPanel 组件）

// 切换工作区（API 驱动）
const workspaces = ref([])
const workspaceLoading = ref(false)
const workspaceError = ref('')

/** 当前进入的工作区名称，非空时显示文件列表 */
const currentWorkspace = ref(null)
/** 当前子目录路径（相对于工作区根），空串表示根目录 */
const currentFilePath = ref('')
const workspaceFiles = ref([])
const selectedFileNames = ref(new Set())
const filesLoading = ref(false)
/** 文件展示模式: 'list' | 'card' */
const fileViewMode = ref('list')
const uploadInputEl = ref(null)
const uploadingFiles = ref(false)
/** 创建工作区弹窗 */
const createDialogVisible = ref(false)
const newWsName = ref('')
const newWsDesc = ref('')
const creatingWs = ref(false)

/** 新建文件弹窗 */
const createFileDialogVisible = ref(false)
const newFileDialogName = ref('')
const creatingFile = ref(false)

/** 文件编辑器 */
const fileOriginalContent = ref('')
const fileSaving = ref(false)

/** 文件预览 */
const filePreviewVisible = ref(false)
const filePreviewTitle = ref('')
const filePreviewFullPath = ref('')
const filePreviewContent = ref('')
const filePreviewLoading = ref(false)

/** 置顶工作区 */
const pinnedWorkspace = computed(() => workspaces.value.find(ws => ws.pinned) || null)
const hasHermesContext = computed(() => Boolean(pinnedWorkspace.value || pinnedFavoriteFolder.value || knowledgeContextCount.value))

async function confirmDeleteWorkspace(workspace) {
  try {
    await ElMessageBox.confirm(
      `确定删除工作区「${workspace.name}」吗？所有文件将被永久删除。`,
      '删除工作区',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    return true
  } catch {
    return false
  }
}

async function confirmCloseDirtyPreview() {
  try {
    await ElMessageBox.confirm(
      '内容尚未保存，确定关闭吗？',
      '关闭预览',
      {
        confirmButtonText: '关闭',
        cancelButtonText: '继续编辑',
        type: 'warning',
      },
    )
    return true
  } catch {
    return false
  }
}

function focusCreateFileInput() {
  setTimeout(() => {
    document.querySelector('.ws-dialog-input')?.focus()
  }, 50)
}

const workspaceService = createAssistantWorkspaceService(assistantApi)
const {
  closeFilePreview,
  createWorkspace,
  createWorkspaceFile,
  deleteWorkspace,
  downloadSelected,
  downloadWorkspaceFile,
  enterWorkspace,
  enterWorkspaceFolder,
  fetchWorkspaces,
  fileFullPath,
  goBackToParentFolder,
  leaveWorkspace,
  openCreateFileDialog,
  openUploadDialog,
  openFilePreview,
  saveFileContent,
  selectAllFiles,
  toggleFileSelection,
  togglePinWorkspace,
  uploadWorkspaceFiles,
} = createAssistantWorkspaceController({
  state: {
    workspaces,
    workspaceLoading,
    workspaceError,
    currentWorkspace,
    currentFilePath,
    workspaceFiles,
    selectedFileNames,
    filesLoading,
    createDialogVisible,
    newWorkspaceName: newWsName,
    newWorkspaceDescription: newWsDesc,
    creatingWorkspace: creatingWs,
    createFileDialogVisible,
    newFileName: newFileDialogName,
    creatingFile,
    uploadInputEl,
    uploadingFiles,
    filePreviewVisible,
    filePreviewTitle,
    filePreviewFullPath,
    filePreviewContent,
    fileOriginalContent,
    filePreviewLoading,
    fileSaving,
  },
  service: workspaceService,
  hasToken: getToken,
  showError,
  errorText,
  confirmDeleteWorkspace,
  confirmCloseDirtyPreview,
  focusCreateFileInput,
  triggerDownload: triggerBrowserDownload,
})

// ---- 智能体报告 ----
const reportConfig = reactive(createReportConfig())

function selectedReportFormat() {
  return findReportFormat(reportConfig.format)
}

const reportHistory = ref(loadReportHistory())
const reportGenerating = ref(false)
const reportProgress = ref(0)
const reportPreview = ref('')
const reportActiveStep = ref(1)
const reportError = ref('')
const reportSavedPath = ref('')
const reportSavingDraft = ref(false)
const reportHistoryOpen = ref(false)
const reportArchiveService = createAssistantReportArchiveService(assistantApi, {
  hasToken: getToken,
  getWorkspaces: () => workspaces.value,
  setWorkspaces: (rows) => { workspaces.value = rows },
  formatDateTime,
})
const reportStreamController = createReportStreamController(assistantApi)

function deriveReportTopic() {
  return deriveReportTopicFromState({
    config: reportConfig,
    conversations: conversations.value,
    currentConversationId: currentConversationId.value,
    messages: messages.value,
  })
}

const reportCanGenerate = computed(() => {
  const fmt = selectedReportFormat()
  if (fmt?.disabled) return false
  if (!deriveReportTopic()) return false
  if (reportConfig.inputMode === 'conversation' && !messages.value.length) return false
  return !reportGenerating.value
})

const reportEditorStats = computed(() => calculateReportEditorStats(reportPreview.value))

function updateReportProgress(step, pct) {
  reportActiveStep.value = Math.max(reportActiveStep.value, step)
  reportProgress.value = Math.max(reportProgress.value, Math.min(100, pct))
}

function buildReportPrompt() {
  const skills = selectedHermesSkills.value
    .map((item) => item.name || item.id)
    .filter(Boolean)
  const databases = selectedHermesDatabases.value
    .map((item) => item.name || item.database || item.host)
    .filter(Boolean)
  const conversationContext = buildConversationReportContext({
    messages: messages.value,
    activeConversationTitle: activeConversationTitle.value,
    toolName: toolLabel,
  })
  return createReportPrompt({
    config: reportConfig,
    topic: deriveReportTopic(),
    format: selectedReportFormat(),
    skills,
    databases,
    pinnedFavoriteFolder: pinnedFavoriteFolder.value,
    favoriteItems: pinnedFavoriteCards.value,
    reportPanelTopic: reportPanel.value?.topic || '',
    conversationContext,
    displayFolderName,
  })
}

async function streamReportFromAssistant(prompt) {
  const requestBody = {
    ...buildChatMessageBody(prompt),
    debug: false,
    user_visible_message: `生成报告：${deriveReportTopic()}`,
    mode: 'expert',
  }
  const sidRaw = convId(currentConversationId.value)
  if (getToken() && sidRaw && !sidRaw.startsWith('local')) {
    const sessionId = Number(sidRaw)
    if (!Number.isNaN(sessionId)) requestBody.session_id = sessionId
  }
  return reportStreamController.run({
    requestBody,
    onEvent(event) {
      if (event.step === 'context') updateReportProgress(2, 24)
      else if (event.step === 'thinking_delta') updateReportProgress(3, 38)
      else if (event.step === 'tool_executing') updateReportProgress(3, 45)
      else if (event.step === 'tool_finished') updateReportProgress(4, 62)
      else if (event.step === 'text_delta' && event.text) {
        reportPreview.value += event.text
        updateReportProgress(4, Math.min(88, 62 + Math.floor(reportPreview.value.length / 180)))
      }
    },
  })
}

function clearReportEditor() {
  if (reportGenerating.value || reportSavingDraft.value) return
  reportPreview.value = ''
  reportSavedPath.value = ''
  reportError.value = ''
}

async function saveReportEditorContent() {
  const content = String(reportPreview.value || '').trim()
  if (!content || reportSavingDraft.value || reportGenerating.value) return
  const topic = deriveReportTopic() || '未命名报告'
  const format = selectedReportFormat()
  if (format?.disabled) {
    reportError.value = `${format.label} 生成链路尚未接入，请先选择 Word 或 Markdown。`
    return
  }
  if (!getToken()) {
    reportError.value = '请先登录后保存报告，报告文件需要保存到用户目录。'
    return
  }
  reportSavingDraft.value = true
  reportError.value = ''
  try {
    const createdAt = new Date().toISOString()
    let filename = ''
    let sourceFileName = ''
    let pptRequestFileName = ''
    if (format.key === 'pptx') {
      const pack = await reportArchiveService.savePptMasterPackage(content, topic, createdAt)
      filename = pack.wordFilename
      sourceFileName = pack.wordFilename
      pptRequestFileName = pack.pptRequestFileName
      reportSavedPath.value = `${REPORT_WORKSPACE_NAME}/${pack.wordFilename} · ${REPORT_WORKSPACE_NAME}/${pack.pptRequestFileName}`
    } else {
      filename = await reportArchiveService.saveReport(content, topic, createdAt, format.key)
      reportSavedPath.value = `${REPORT_WORKSPACE_NAME}/${filename}`
    }
    const record = normalizeReportRecord({
      id: `report-edit-${Date.now()}`,
      type: reportConfig.type,
      format: format.key,
      inputMode: reportConfig.inputMode,
      topic,
      timeRange: reportConfig.timeRange,
      perspective: reportConfig.perspective,
      template: reportConfig.template,
      content,
      createdAt,
      status: 'done',
      workspace: REPORT_WORKSPACE_NAME,
      fileName: filename,
      filePath: reportSavedPath.value,
      sourceFileName,
      pptRequestFileName,
    })
    reportHistory.value = [record, ...reportHistory.value.filter((item) => item.id !== record.id)]
    saveReportHistory(reportHistory.value)
    await fetchWorkspaces()
    ElMessage.success(format.key === 'pptx' ? 'Word 源稿和 ppt-master 任务已保存' : '报告正文已保存')
  } catch (e) {
    reportError.value = e?.message || String(e)
  } finally {
    reportSavingDraft.value = false
  }
}

async function startReportGeneration() {
  const topic = deriveReportTopic()
  const format = selectedReportFormat()
  if (!topic || reportGenerating.value) return
  if (format?.disabled) {
    reportError.value = `${format.label} 生成链路尚未接入，请先选择 Word 或 Markdown。`
    return
  }
  if (reportConfig.inputMode === 'conversation' && !messages.value.length) {
    reportError.value = '当前会话暂无内容，请先选择或发起一个会话。'
    return
  }
  if (!getToken()) {
    reportError.value = '请先登录后生成报告，报告文件需要保存到用户目录。'
    return
  }
  refreshKnowledgeContextFromStorage()
  reportGenerating.value = true
  reportProgress.value = 8
  reportPreview.value = ''
  reportError.value = ''
  reportSavedPath.value = ''
  reportActiveStep.value = 1
  try {
    const prompt = buildReportPrompt()
    const content = await streamReportFromAssistant(prompt)
    if (!content) throw new Error('报告生成结果为空')
    const createdAt = new Date().toISOString()
    let filename = ''
    let sourceFileName = ''
    let pptRequestFileName = ''
    if (format.key === 'pptx') {
      const pack = await reportArchiveService.savePptMasterPackage(content, topic, createdAt)
      filename = pack.wordFilename
      sourceFileName = pack.wordFilename
      pptRequestFileName = pack.pptRequestFileName
      reportSavedPath.value = `${REPORT_WORKSPACE_NAME}/${pack.wordFilename} · ${REPORT_WORKSPACE_NAME}/${pack.pptRequestFileName}`
    } else {
      filename = await reportArchiveService.saveReport(content, topic, createdAt, format.key)
      reportSavedPath.value = `${REPORT_WORKSPACE_NAME}/${filename}`
    }
    reportPreview.value = content
    const record = normalizeReportRecord({
      id: `report-${Date.now()}`,
      type: reportConfig.type,
      format: format.key,
      inputMode: reportConfig.inputMode,
      topic,
      timeRange: reportConfig.timeRange,
      perspective: reportConfig.perspective,
      template: reportConfig.template,
      content,
      createdAt,
      status: 'done',
      workspace: REPORT_WORKSPACE_NAME,
      fileName: filename,
      filePath: reportSavedPath.value,
      sourceFileName,
      pptRequestFileName,
    })
    reportHistory.value = [record, ...reportHistory.value.filter((item) => item.id !== record.id)]
    saveReportHistory(reportHistory.value)
    updateReportProgress(5, 100)
    await fetchWorkspaces()
  } catch (e) {
    reportError.value = e?.message || String(e)
  } finally {
    reportGenerating.value = false
  }
}

async function askReportClarifyingQuestions() {
  if (sending.value || reportGenerating.value) return
  const topic = deriveReportTopic() || '待定报告主题'
  const prompt = [
    '请使用 brainstorming skill 的需求澄清流程帮助我明确一份 GlobeMind 智能体报告。',
    '只提出澄清问题，不要开始写报告；问题要一次性聚焦、数量控制在 5 个以内。',
    `当前报告主题：${topic}`,
    `候选报告类型：${reportTypeLabel(reportConfig.type)}`,
    `计划输出格式：${reportFormatLabel(reportConfig.format)}`,
    '',
    '请围绕报告用途、读者、范围、证据标准、必须回答的问题和输出约束提问。',
  ].join('\n')
  activeSideNav.value = 'chat'
  activeTopTab.value = 'history'
  await nextTick()
  await sendMessage({
    visibleMessage: `先澄清「${topic}」的报告需求`,
    prompt,
    mode: 'expert',
  })
}

function stopReportGeneration() {
  reportStreamController.abort()
  reportGenerating.value = false
}

async function loadReportToPreview(r) {
  const record = normalizeReportRecord(r)
  if (!record) return
  reportConfig.topic = record.topic
  reportConfig.type = record.type
  reportConfig.format = record.format || 'md'
  reportConfig.inputMode = record.inputMode || 'prompt'
  reportConfig.timeRange = record.timeRange
  reportConfig.perspective = record.perspective
  reportConfig.template = record.template
  reportError.value = ''
  reportSavedPath.value = record.filePath || (record.fileName ? `${REPORT_WORKSPACE_NAME}/${record.fileName}` : '')
  if (record.content) {
    reportPreview.value = record.content
    return
  }
  if (!record.fileName || !getToken()) {
    reportPreview.value = ''
    return
  }
  if (record.format === 'docx') {
    reportPreview.value = '该历史报告保存为 Word 文件，当前仅支持下载文件查看；本地历史中没有保留可预览文本。'
    return
  }
  try {
    const json = await assistantApi.readWorkspaceFile(
      record.workspace || REPORT_WORKSPACE_NAME,
      record.fileName,
    )
    if (json.ok) {
      reportPreview.value = json.data.content || ''
      record.content = reportPreview.value
      reportHistory.value = reportHistory.value.map((item) => (item.id === record.id ? record : item))
      saveReportHistory(reportHistory.value)
    } else {
      reportError.value = json.error || '读取报告文件失败'
    }
  } catch (e) {
    reportError.value = e?.message || '读取报告文件失败'
  }
}

// 定期简报页内：轻量对话（复用同一套 sendMessage/messages）
const briefingDraft = ref('')
const composerTextareaEl = ref(null)
/** 聊天模式：fast | pro | expert */
const chatMode = ref('fast')
const showModeMenu = ref(false)
const CHAT_MODE_LABELS = {
  fast: '快速',
  pro: '研判',
  expert: '专家',
}

function chatModeLabel(mode = chatMode.value) {
  return CHAT_MODE_LABELS[mode] || CHAT_MODE_LABELS.pro
}

function setChatMode(mode) {
  chatMode.value = mode
  showModeMenu.value = false
}

function closeModeMenu() {
  if (showModeMenu.value) showModeMenu.value = false
}

watch(showModeMenu, (val) => {
  if (val) {
    setTimeout(() => document.addEventListener('click', closeModeMenu), 0)
  } else {
    document.removeEventListener('click', closeModeMenu)
  }
})
const chatScrollBriefingEl = ref(null)
const chatScrollAiEl = ref(null)
const inputText = ref('')
const sending = ref(false)
const streaming = ref(false)
const chatStreamController = createChatStreamController(assistantApi)
const conversations = ref([])
const historySearch = ref('')
const currentConversationId = ref('')
/** 会话消息列表请求序号：防快速切换会话时旧请求覆盖新界面 */
const sessionLoadSeq = ref(0)
const messages = ref([])
const pendingAutorunContext = ref(null)
const hasConversation = computed(() => messages.value.length > 0)
const hasStreamContent = computed(() => {
  if (!streaming.value) return false
  const last = messages.value[messages.value.length - 1]
  return last?.role === 'assistant' && last.text?.length > 0
})
const newsHits = ref([])
const clusterHits = ref([])
/** 报告主题侧栏：进入后按主题拉取下属新闻 */
const reportPanel = ref(null)
const topicNewsLoading = ref(false)

/** 左右抽屉默认收起，降低三栏压迫感 */
const leftDrawerOpen = ref(false)
const rightDrawerOpen = ref(false)
const activeSourcePanel = ref(null)
/** 右侧抽屉内：新闻 / 聚类 折叠展开 */
const expandedClue = ref(null)
/** 侧栏「答复全文」折叠 */
const expandedReplyDetail = ref(false)
/** 当前在侧栏展示的完整回复（ capability 或点击详情） */
const panelDetailReply = ref('')

function toggleRightDrawer() {
  const next = !rightDrawerOpen.value
  rightDrawerOpen.value = next
  if (next) leftDrawerOpen.value = false
  if (!next) activeSourcePanel.value = null
}

function toggleContextDrawer() {
  if (rightDrawerOpen.value && !activeSourcePanel.value) {
    rightDrawerOpen.value = false
    return
  }
  activeSourcePanel.value = null
  leftDrawerOpen.value = false
  rightDrawerOpen.value = true
}

function toggleClue(kind) {
  expandedClue.value = expandedClue.value === kind ? null : kind
}

/** 能力胶囊：保持入口文案简短，点击后仍向接口发送完整任务描述。 */
const capabilityChips = [
  { icon: Newspaper, label: '全球要闻', query: '最近全球发生了什么大事' },
  { icon: FileText, label: '学术报告', query: '我想制作一份关于近期国际动态的学术报告' },
  { icon: TrendingUp, label: '趋势研判', query: '帮我分析近期重要新闻的趋势和影响' },
  { icon: MapPinned, label: '地区局势', query: '总结各地区局势的最新进展和关键事件' },
]

function openMessageDetail(m) {
  activeSourcePanel.value = null
  panelDetailReply.value = String(m.storedFullReply || m.text || '').trim()
  expandedReplyDetail.value = true
  leftDrawerOpen.value = false
  rightDrawerOpen.value = true
  if (newsHits.value.length) expandedClue.value = 'news'
  else if (clusterHits.value.length) expandedClue.value = 'cluster'
}

const hasMessages = computed(() => messages.value.length > 0)
const activeConversationTitle = computed(() => {
  const cur = convId(currentConversationId.value)
  const c = conversations.value.find((x) => convId(x.id) === cur)
  return c?.title || '新会话'
})

function compactConversationTitle(title, max = 22) {
  const text = String(title || '新会话').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

const conversationOptions = computed(() => conversations.value.slice(0, 40).map((conv) => ({
  id: convId(conv.id),
  label: compactConversationTitle(conv.title || '会话'),
  title: conv.title || '会话',
})))

const filteredConversations = computed(() => {
  const query = historySearch.value.trim().toLocaleLowerCase('zh-CN')
  if (!query) return conversations.value
  return conversations.value.filter((conv) => (
    String(conv?.title || '会话').toLocaleLowerCase('zh-CN').includes(query)
    || convId(conv?.id).toLocaleLowerCase('zh-CN').includes(query)
  ))
})

function isLatestStreamingMessage(index) {
  return streaming.value && index === messages.value.length - 1
}

function assistantMessageReady(message, index) {
  if (!message || message.role !== 'assistant') return false
  if (isLatestStreamingMessage(index)) return false
  return Boolean(String(message.storedFullReply || message.text || '').trim())
}

function hasRunningToolCalls(message) {
  return Boolean(message?.toolCalls?.some((tool) => tool.type === 'tool_executing'))
}

function hasSkippedToolCalls(message) {
  return Boolean(message?.toolCalls?.some((tool) => (
    tool.type === 'tool_finished' && (tool.result?.skipped || tool.result?.ok === false)
  )))
}

function toolBoxShouldOpen(message, index) {
  return hasRunningToolCalls(message) && !assistantMessageReady(message, index)
}

/** 与侧栏 id、URL 参数统一为字符串，避免 number / string 混用导致匹配失败 */
function convId(x) {
  return String(x ?? '').trim()
}

function messageKey(m, idx) {
  if (m.dbId != null) return `d-${m.dbId}`
  return `i-${idx}`
}

/* ── 工具显示辅助 ── */

const _TOOL_LABELS = {
  news_search: '新闻检索',
  macro_event_search: '宏观事件',
  search_news_corpus: '新闻检索',
  web_search: '联网搜索',
  image_generate: '图片生成',
  selected_favorites_list: '收藏列表',
  selected_favorite_read: '收藏读取',
  selected_skill_list: 'Skill 列表',
  selected_skill_read: 'Skill 读取',
  selected_database_list: '数据库卡片列表',
  selected_database_read: '数据库卡片读取',
  workspace_list_files: '工作区列表',
  workspace_read_file: '工作区读取',
  knowledge_list_files: '知识库列表',
  knowledge_read_file: '知识库读取',
  navigate_clusters: '事件聚类',
  query_opinion_index: '舆情指数',
  access_knowledge_base: '知识库',
  run_code: '代码执行',
  read_repo_file: '文件读取',
  run_shell_command: '命令执行',
}

const _TOOL_ICONS = {
  news_search: 'search',
  macro_event_search: 'cluster',
  search_news_corpus: 'search',
  web_search: 'globe',
  image_generate: 'image',
  selected_favorites_list: 'book',
  selected_favorite_read: 'book',
  selected_skill_list: 'book',
  selected_skill_read: 'book',
  selected_database_list: 'book',
  selected_database_read: 'book',
  workspace_list_files: 'file',
  workspace_read_file: 'file',
  knowledge_list_files: 'book',
  knowledge_read_file: 'book',
  navigate_clusters: 'cluster',
  query_opinion_index: 'chart',
  access_knowledge_base: 'book',
  run_code: 'code',
  read_repo_file: 'file',
  run_shell_command: 'code',
}

const _ACTION_LABELS = {
  search_clusters: '搜索簇',
  get_macro: '宏观详情',
  get_micros: '微观列表',
  get_micro: '微观详情',
  get_news: '新闻列表',
  china_trend: '涉华趋势',
  events_by_date: '按日检索',
  event_clusters: '聚类下钻',
  event_news: '事件新闻',
  list_categories: '分类列表',
  list_files: '文件列表',
  read_file: '读取文件',
}

function toolLabel(name) { return _TOOL_LABELS[name] || name }

function toolIcon(name) { return _TOOL_ICONS[name] || 'tool' }

function toolActionLabel(name, action) { return _ACTION_LABELS[action] || action }

function modeLabel(mode) {
  if (mode === 'cluster') return '聚类'
  return searchModeDisclosure(mode).label
}

function sourceDomain(url) {
  try {
    return new URL(String(url || '')).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

function formatToolUsage(usage) {
  if (!usage || typeof usage !== 'object') return ''
  const parts = []
  const requests = usage.server_tool_use?.web_search_requests
  if (requests != null) parts.push(`${requests} 次搜索`)
  if (usage.input_tokens != null) parts.push(`in ${usage.input_tokens}`)
  if (usage.output_tokens != null) parts.push(`out ${usage.output_tokens}`)
  return parts.join(' · ')
}

function formatToolDuration(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n <= 0) return ''
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`
}

function sourceItemKey(item, idx) {
  return item.url || item.id || item.title || idx
}

function normalizeSourceResults(items, kind) {
  if (!Array.isArray(items)) return []
  return items
    .map((item, idx) => {
      const url = item.url || item.link || ''
      return {
        id: item.id,
        rank: idx + 1,
        title: item.title || '未命名来源',
        url,
        domain: sourceDomain(url),
        source: item.source || item.publisher || item.domain || sourceDomain(url) || '',
        time: item.page_age || item.pub_time || item.time || item.displayTime || '',
        snippet: item.snippet || item.abstract || item.desc || '',
        relevanceScore: item.relevance_score ?? item.relevance ?? item.score ?? item.rank_score ?? null,
        kind,
      }
    })
    .filter((item) => item.title || item.url || item.id)
}

function normalizeWebSourceGroups(groups) {
  if (!Array.isArray(groups)) return []
  return groups.map((group, idx) => {
    const results = normalizeSourceResults(group.results || group.items || [], 'web')
    return {
      key: `web-${group.query || idx}-${results[0]?.url || idx}`,
      type: 'web',
      label: group.label || '联网来源',
      query: group.query || '',
      searchQueries: Array.isArray(group.search_queries) ? group.search_queries : [],
      usage: group.usage || {},
      usageText: formatToolUsage(group.usage),
      durationText: formatToolDuration(group.query_time_ms),
      summary: group.summary || '',
      items: results,
    }
  }).filter((group) => group.items.length || group.summary)
}

function sourceGroupsFromToolResult(name, result) {
  if (!result || typeof result !== 'object') return []
  const toolName = result.tool || name
  if (toolName === 'web_search') return normalizeWebSourceGroups([result])
  if (Array.isArray(result.news) && result.news.length) {
    const items = normalizeSourceResults(result.news, 'news')
    return [{
      key: `news-${result.label || toolName}-${result.params?.keyword || ''}`,
      type: 'news',
      label: result.label || '平台新闻',
      query: result.params?.keyword || '',
      searchQueries: [],
      usage: {},
      usageText: '',
      durationText: formatToolDuration(result.query_time_ms),
      summary: '',
      items,
    }]
  }
  return []
}

function mergeSourceGroups(existing, incoming) {
  const out = Array.isArray(existing) ? [...existing] : []
  for (const group of incoming || []) {
    if (!group) continue
    const key = group.key || `${group.type}-${group.query || ''}`
    const idx = out.findIndex((item) => (item.key || `${item.type}-${item.query || ''}`) === key)
    if (idx < 0) {
      out.push({ ...group, key })
      continue
    }
    const seen = new Set((out[idx].items || []).map((item) => item.url || item.id || item.title))
    const mergedItems = [...(out[idx].items || [])]
    for (const item of group.items || []) {
      const itemKey = item.url || item.id || item.title
      if (!itemKey || seen.has(itemKey)) continue
      seen.add(itemKey)
      mergedItems.push(item)
    }
    out[idx] = { ...out[idx], ...group, key, items: mergedItems }
  }
  return out.slice(0, 8)
}

function sourceGroupsFromExtra(extra) {
  const groups = []
  if (Array.isArray(extra?.web_sources)) groups.push(...normalizeWebSourceGroups(extra.web_sources))
  if (Array.isArray(extra?.news_hits) && extra.news_hits.length) {
    groups.push(...sourceGroupsFromToolResult('news_search', {
      tool: 'news_search',
      label: '平台新闻',
      news: extra.news_hits,
    }))
  }
  return groups
}

function sourceTotal(groups) {
  return (groups || []).reduce((sum, group) => sum + (group.items?.length || 0), 0)
}

function sourceGroupTotal(groups) {
  return (groups || []).filter((group) => group?.items?.length || group?.summary).length
}

function uniqueSourceQueries(groups, limit = 6) {
  const seen = new Set()
  const out = []
  for (const group of groups || []) {
    const values = [
      group?.query,
      ...(Array.isArray(group?.searchQueries) ? group.searchQueries : []),
    ]
    for (const value of values) {
      const text = String(value || '').trim()
      if (!text || seen.has(text)) continue
      seen.add(text)
      out.push(text)
      if (out.length >= limit) return out
    }
  }
  return out
}

function parseSourceDate(value) {
  const raw = String(value || '').trim()
  const now = new Date()
  if (!raw) return null
  if (/刚刚|分钟前|minute|hour|小时前|today|今天/i.test(raw)) return now
  if (/昨天|昨日|yesterday/i.test(raw)) return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  const dayAgo = raw.match(/(\d+)\s*(天前|days?\s+ago|day\s+ago)/i)
  if (dayAgo) return new Date(now.getFullYear(), now.getMonth(), now.getDate() - Number(dayAgo[1]))
  const ymd = raw.match(/(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})/)
  if (ymd) return new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3]))
  const md = raw.match(/(?:^|\D)(\d{1,2})[-/.月](\d{1,2})(?:日)?(?:\D|$)/)
  if (md) return new Date(now.getFullYear(), Number(md[1]) - 1, Number(md[2]))
  const parsed = new Date(raw)
  if (!Number.isNaN(parsed.getTime())) return parsed
  return null
}

function sourceDateKey(date) {
  if (!date) return 'unknown'
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function sourceDateLabel(date) {
  if (!date) return '时间未标注'
  const today = new Date()
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const delta = Math.round((startToday - startDate) / 86400000)
  if (delta === 0) return '今天'
  if (delta === 1) return '昨天'
  if (delta > 1 && delta < 7) return `${delta} 天前`
  return `${String(date.getMonth() + 1).padStart(2, '0')}月${String(date.getDate()).padStart(2, '0')}日`
}

function sourcePanelFromGroups(groups) {
  const buckets = new Map()
  const flatItems = []
  let sourceIndex = 0
  for (const [groupIndex, group] of (groups || []).entries()) {
    for (const item of group.items || []) {
      sourceIndex += 1
      const date = parseSourceDate(item.time)
      const key = sourceDateKey(date)
      if (!buckets.has(key)) {
        buckets.set(key, {
          key,
          date,
          label: sourceDateLabel(date),
          items: [],
          sources: new Set(),
          groups: new Set(),
        })
      }
      const bucket = buckets.get(key)
      const sourceName = item.source || item.domain || '未知来源'
      bucket.sources.add(sourceName)
      bucket.groups.add(group.label || '来源')
      const enriched = {
        ...item,
        sourceIndex,
        groupIndex,
        date,
        dateLabel: sourceDateLabel(date),
        groupLabel: group.label || '来源',
        groupQuery: group.query || '',
        groupUsageText: group.usageText || '',
        groupDurationText: group.durationText || '',
      }
      bucket.items.push(enriched)
      flatItems.push(enriched)
    }
  }
  const days = [...buckets.values()]
    .sort((a, b) => {
      if (!a.date && !b.date) return 0
      if (!a.date) return 1
      if (!b.date) return -1
      return b.date.getTime() - a.date.getTime()
    })
    .map((bucket) => ({
      key: bucket.key,
      label: bucket.label,
      count: bucket.items.length,
      sourceNames: [...bucket.sources].slice(0, 4),
      groupNames: [...bucket.groups].slice(0, 3),
      items: bucket.items,
    }))
  return {
    total: sourceTotal(groups),
    groupCount: sourceGroupTotal(groups),
    queries: uniqueSourceQueries(groups, 8),
    days,
    overviewItems: pickSourceOverviewItems(flatItems, 2),
  }
}

function numericSourceScore(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return n > 1 ? Math.min(n, 100) : Math.max(0, Math.min(n, 1)) * 100
}

function sourceOverviewScore(item) {
  const rank = Number(item.rank || item.sourceIndex || 99)
  const groupIndex = Number(item.groupIndex || 0)
  const titleLen = String(item.title || '').trim().length
  const snippetLen = String(item.snippet || '').trim().length
  let score = numericSourceScore(item.relevanceScore)
  score += Math.max(0, 32 - Math.min(rank, 32))
  score += Math.max(0, 10 - Math.min(groupIndex, 10))
  if (item.url || item.id) score += 8
  if (item.source || item.domain) score += 6
  if (titleLen >= 12) score += 8
  if (snippetLen >= 40) score += 5
  if (item.date) score += 3
  return score
}

function pickSourceOverviewItems(items, limit = 2) {
  const seen = new Set()
  const unique = []
  for (const item of items || []) {
    const key = item.url || item.id || item.title
    if (!key || seen.has(key)) continue
    seen.add(key)
    unique.push(item)
  }
  return unique
    .sort((a, b) => sourceOverviewScore(b) - sourceOverviewScore(a))
    .slice(0, limit)
}

function sourceOverviewItems(groups) {
  return sourcePanelFromGroups(groups).overviewItems
}

function sourceOverviewMeta(item) {
  const parts = [
    item.source || item.domain || '未知来源',
    item.dateLabel,
    item.groupLabel,
  ].filter(Boolean)
  return parts.join(' · ')
}

function sourceOverviewSnippet(item) {
  return item.snippet || item.url || item.groupQuery || '点击右侧查看完整来源列表'
}

function openSourcePanel(m) {
  const panel = sourcePanelFromGroups(m?.sources || [])
  if (!panel.total) return
  activeSourcePanel.value = panel
  leftDrawerOpen.value = false
  rightDrawerOpen.value = true
}

function openSourceItem(group, item) {
  if (item?.url) {
    window.open(item.url, '_blank', 'noopener,noreferrer')
    return
  }
  if (item?.id) {
    router.push(`/data-service/news/${item.id}`)
  }
}

function mapApiMessageRow(row) {
  const message = normalizeAssistantMessageDto(row)
  if (message.role === 'user') {
    return { role: 'user', text: message.content, dbId: message.id }
  }
  return reactive({
    role: 'assistant',
    text: message.content,
    storedFullReply: message.content,
    showDetailChip: false,
    detailIndex: 1,
    toolCalls: [],
    sources: sourceGroupsFromExtra(message.extra),
    dbId: message.id,
  })
}

function applyLastCluesFromRows(rows) {
  let lastNews = []
  let lastClusters = []
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const r = rows[i]
    if (r.role !== 'assistant' || !r.extra) continue
    if (Array.isArray(r.extra.news_hits)) lastNews = r.extra.news_hits
    if (Array.isArray(r.extra.cluster_hits)) lastClusters = r.extra.cluster_hits
    break
  }
  newsHits.value = lastNews
  clusterHits.value = lastClusters
}

async function loadSessionMessages(sessionId) {
  const target = convId(sessionId)
  const seq = (sessionLoadSeq.value += 1)
  let rows
  try {
    rows = await assistantApi.listSessionMessages(sessionId)
  } catch (error) {
    console.warn('[DataAssistant] 加载会话消息失败', error?.status || error?.message)
    if (seq === sessionLoadSeq.value && convId(currentConversationId.value) === target) {
      messages.value = []
      newsHits.value = []
      clusterHits.value = []
    }
    return false
  }
  if (seq !== sessionLoadSeq.value) return false
  if (convId(currentConversationId.value) !== target) return false
  messages.value = rows.map(mapApiMessageRow)
  applyLastCluesFromRows(rows)
  panelDetailReply.value = ''
  expandedReplyDetail.value = false
  scrollChatToBottom()
  return true
}

/** 一轮 CC 结束后与数据库对齐（含 message 的 dbId），避免仅内存有记录 */
async function syncSessionFromServerAfterTurn() {
  const token = getToken()
  const sid = convId(currentConversationId.value)
  if (!token || !sid || sid.startsWith('local')) return
  const n = Number(sid)
  if (Number.isNaN(n)) return
  try {
    const rows = await assistantApi.listSessionMessages(n)
    if (convId(currentConversationId.value) !== sid) return
    // 保存本地已有的 toolCalls/thinking，服务端返回的数据不含这些字段
    const localAssistantData = messages.value.map(m => ({
      toolCalls: m.toolCalls,
      thinking: m.thinking,
      sources: m.sources,
    }))
    messages.value = rows.map(mapApiMessageRow)
    // 恢复本地工具调用和思考过程
    for (let i = 0; i < messages.value.length && i < localAssistantData.length; i++) {
      if (messages.value[i].role !== 'assistant') continue
      if (localAssistantData[i].toolCalls?.length) {
        messages.value[i].toolCalls = localAssistantData[i].toolCalls
      }
      if (localAssistantData[i].thinking) {
        messages.value[i].thinking = localAssistantData[i].thinking
      }
      if (localAssistantData[i].sources?.length) {
        messages.value[i].sources = mergeSourceGroups(messages.value[i].sources || [], localAssistantData[i].sources)
      }
    }
    applyLastCluesFromRows(rows)
    scrollChatToBottom()
  } catch {
    /* 保留当前流式展示内容 */
  }
}

const SESSION_CACHE_KEY = 'data_assistant_sessions_v2'

function cacheSessions(list) {
  try {
    const all = JSON.parse(localStorage.getItem(SESSION_CACHE_KEY) || '{}')
    const key = getUserCacheKey()
    all[key] = list.map(s => ({ id: String(s.id), title: s.title, at: s.at || Date.now() }))
    localStorage.setItem(SESSION_CACHE_KEY, JSON.stringify(all))
  } catch { /* ignore */ }
}

function loadCachedSessions() {
  try {
    const all = JSON.parse(localStorage.getItem(SESSION_CACHE_KEY) || '{}')
    const key = getUserCacheKey()
    return all[key] || null
  } catch { return null }
}

async function bootstrapSessions() {
  const token = getToken()
  if (!token) {
    const cached = loadCachedSessions()
    if (cached && cached.length > 0) {
      conversations.value = cached
      currentConversationId.value = cached[0].id
      const msgs = loadCachedMessages(cached[0].id)
      if (msgs) messages.value = msgs
    } else {
      conversations.value = [{ id: 'local-1', title: '本地会话', at: Date.now() }]
      currentConversationId.value = 'local-1'
    }
    return
  }
  try {
    const rows = await assistantApi.listSessions()
    if (!rows.length) {
      const one = normalizeAssistantSessionDto(await assistantApi.createSession('新会话'))
      conversations.value = [
        one,
      ]
      currentConversationId.value = String(one.id)
      messages.value = []
      newsHits.value = []
      clusterHits.value = []
    } else {
      const prevCur = convId(currentConversationId.value)
      conversations.value = normalizeAssistantSessionList(rows)
      const stillHere = conversations.value.some((x) => convId(x.id) === prevCur)
      if (stillHere) {
        currentConversationId.value = prevCur
        await loadSessionMessages(Number(prevCur))
      } else {
        currentConversationId.value = conversations.value[0].id
        await loadSessionMessages(Number(currentConversationId.value))
      }
    }
  } catch {
    conversations.value = [{ id: 'local-1', title: '本地会话', at: Date.now() }]
    currentConversationId.value = 'local-1'
    messages.value = []
  }
}

async function selectConversation(id) {
  const nid = convId(id)
  if (nid === convId(currentConversationId.value)) return
  stopGeneration()
  currentConversationId.value = nid
  if (!getToken() || nid.startsWith('local')) {
    messages.value = []
    newsHits.value = []
    clusterHits.value = []
    return
  }
  await loadSessionMessages(Number(nid))
}

async function selectReportConversation(event) {
  const id = String(event?.target?.value || '').trim()
  if (!id || id === convId(currentConversationId.value)) return
  await selectConversation(id)
}

async function createConversation() {
  stopGeneration()
  const token = getToken()
  if (!token) {
    const id = `local-${Date.now()}`
    conversations.value.unshift({ id, title: '新会话', at: Date.now() })
    currentConversationId.value = id
    messages.value = []
    newsHits.value = []
    clusterHits.value = []
    panelDetailReply.value = ''
    expandedReplyDetail.value = false
    return
  }
  try {
    const row = normalizeAssistantSessionDto(await assistantApi.createSession('新会话'))
    const id = row.id
    conversations.value.unshift(row)
    currentConversationId.value = id
    messages.value = []
    newsHits.value = []
    clusterHits.value = []
    panelDetailReply.value = ''
    expandedReplyDetail.value = false
  } catch {
    const id = `local-${Date.now()}`
    conversations.value.unshift({ id, title: '新会话', at: Date.now() })
    currentConversationId.value = id
    messages.value = []
    newsHits.value = []
    clusterHits.value = []
    panelDetailReply.value = ''
    expandedReplyDetail.value = false
  }
}

async function startNewConversation() {
  await createConversation()
  scrollChatToBottom()
}

function switchConversationFromSelect(event) {
  const id = String(event?.target?.value || '').trim()
  if (!id || id === convId(currentConversationId.value)) return
  openHistoryConversation(id)
}

// ── 删除确认弹窗 ──
const deleteDialog = ref({
  visible: false,
  id: null,
  title: '',
})

function showDeleteDialog(id) {
  const nid = convId(id)
  if (!nid) return
  const conv = conversations.value.find((c) => convId(c.id) === nid)
  deleteDialog.value = {
    visible: true,
    id: nid,
    title: conv?.title || '该会话',
  }
}

function closeDeleteDialog() {
  deleteDialog.value.visible = false
}

async function confirmDelete() {
  const { id: nid } = deleteDialog.value
  if (!nid) return
  closeDeleteDialog()

  stopGeneration()
  const token = getToken()
  if (token && !nid.startsWith('local')) {
    try {
      await assistantApi.deleteSession(nid)
    } catch {
      /* The local deletion still proceeds when the remote session is unavailable. */
    }
  }
  // 清理本地缓存
  try {
    const all = JSON.parse(localStorage.getItem(MESSAGE_CACHE_KEY) || '{}')
    delete all[getUserCacheKey() + '_' + nid]
    localStorage.setItem(MESSAGE_CACHE_KEY, JSON.stringify(all))
  } catch {
    /* Ignore malformed legacy message cache. */
  }
  // 从列表中移除
  const idx = conversations.value.findIndex((c) => convId(c.id) === nid)
  if (idx >= 0) conversations.value.splice(idx, 1)
  // 如果删除的是当前会话，切换到下一个或新建
  if (convId(currentConversationId.value) === nid) {
    if (conversations.value.length > 0) {
      await selectConversation(conversations.value[0].id)
    } else {
      await createConversation()
    }
  }
}

async function deleteConversation(id) {
  showDeleteDialog(id)
}

const MESSAGE_CACHE_KEY = 'data_assistant_msg_cache_v2'

function getUserCacheKey() {
  const token = getToken()
  if (!token) return 'anonymous'
  // 用 token 后 8 位作为用户标识
  return 'u_' + token.slice(-8)
}

function getMessageCache() {
  try {
    const all = JSON.parse(localStorage.getItem(MESSAGE_CACHE_KEY) || '{}')
    const key = getUserCacheKey()
    return all[key] || {}
  } catch { return {} }
}

function cacheMessages(sessionId, msgs) {
  try {
    const all = JSON.parse(localStorage.getItem(MESSAGE_CACHE_KEY) || '{}')
    const key = getUserCacheKey()
    if (!all[key]) all[key] = {}
    all[key][sessionId] = msgs
    localStorage.setItem(MESSAGE_CACHE_KEY, JSON.stringify(all))
  } catch { /* ignore */ }
}

function loadCachedMessages(sessionId) {
  const cache = getMessageCache()
  return cache[sessionId] || null
}

async function openHistoryConversation(id) {
  // 切换回对话页面
  activeSideNav.value = 'chat'
  activeTopTab.value = 'history'
  // 先尝试缓存
  const cached = loadCachedMessages(String(id))
  if (cached && cached.length > 0) {
    stopGeneration()
    currentConversationId.value = String(id)
    messages.value = cached
    newsHits.value = []
    clusterHits.value = []
    scrollChatToBottom()
    return
  }
  // 从后端加载
  await selectConversation(id)
  if (messages.value.length > 0) {
    cacheMessages(String(id), messages.value)
  }
  scrollChatToBottom()
}

function autoSelectLatestConversation() {
  if (conversations.value.length > 0 && !currentConversationId.value) {
    const latest = conversations.value.reduce((a, b) => (a.at > b.at ? a : b))
    openHistoryConversation(latest.id)
  }
}

function routeSessionId() {
  const raw = route.query.session_id
  const value = Array.isArray(raw) ? raw[0] : raw
  const text = String(value || '').trim()
  if (!/^\d+$/.test(text)) return ''
  return text
}

async function openRouteConversation() {
  const sid = routeSessionId()
  if (!sid || !getToken()) return
  activeSideNav.value = 'chat'
  activeTopTab.value = 'history'
  if (!conversations.value.some((item) => convId(item.id) === sid)) {
    conversations.value.unshift({
      id: sid,
      title: '故事图谱协作',
      at: Date.now(),
    })
  }
  currentConversationId.value = sid
  const loaded = await loadSessionMessages(Number(sid))
  if (!loaded) {
    conversations.value = conversations.value.filter((item) => convId(item.id) !== sid)
    await bootstrapSessions()
  }
}

function autoResizeComposer() {
  const el = composerTextareaEl.value
  if (!el) return
  el.style.height = '56px'
  el.style.height = Math.min(el.scrollHeight, 140) + 'px'
}

/** 点击建议卡片直接发送消息 */
/** 中止当前流式请求（关闭连接）；服务端一般随即结束，不必杀整个进程 */
function stopGeneration() {
  chatStreamController.abort()
}

function appendStoppedNote(streamMsg) {
  if (!streamMsg) return
  const note = '\n\n（已停止生成）'
  if (!String(streamMsg.text || '').includes('（已停止生成）')) {
    streamMsg.text = `${streamMsg.text || ''}${note}`
  }
  streamMsg.storedFullReply = streamMsg.text
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function readStoredReportTopic() {
  try {
    const raw = sessionStorage.getItem(REPORT_CONTEXT_STORAGE_KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    const topic = String(data.topic || '').trim()
    if (!topic) return null
    return {
      topic,
      at: Number(data.at) || Date.now(),
      report: data.report && typeof data.report === 'object' ? data.report : null,
    }
  } catch {
    return null
  }
}

function readAutorunContext() {
  try {
    const raw = sessionStorage.getItem(ASSISTANT_AUTORUN_CONTEXT_KEY)
      || localStorage.getItem(ASSISTANT_AUTORUN_CONTEXT_KEY)
    if (!raw) return null
    sessionStorage.removeItem(ASSISTANT_AUTORUN_CONTEXT_KEY)
    localStorage.removeItem(ASSISTANT_AUTORUN_CONTEXT_KEY)
    const data = JSON.parse(raw)
    const visibleMessage = String(data.visibleMessage || data.message || '').trim()
    const prompt = String(data.prompt || visibleMessage).trim()
    const confirmMessage = String(data.confirmMessage || data.confirm_message || '').trim()
    if (!visibleMessage || !prompt) return null
    return {
      visibleMessage,
      prompt,
      confirmMessage,
      mode: ['fast', 'pro', 'expert'].includes(data.mode) ? data.mode : 'expert',
      extraBody: data.extraBody && typeof data.extraBody === 'object' ? data.extraBody : null,
      createNew: data.createNew === true,
    }
  } catch {
    return null
  }
}

function queueAssistantContextConfirmation(payload) {
  if (!payload?.visibleMessage || !payload?.prompt) return
  activeSideNav.value = 'chat'
  activeTopTab.value = 'history'
  pendingAutorunContext.value = payload
  inputText.value = ''
  nextTick(() => {
    if (chatScrollAiEl.value) chatScrollAiEl.value.scrollTop = 0
  })
}

function dismissAssistantContextConfirmation() {
  pendingAutorunContext.value = null
}

async function confirmAssistantContextSend() {
  const payload = pendingAutorunContext.value
  if (!payload || sending.value) return
  pendingAutorunContext.value = null
  if (payload.mode && ['fast', 'pro', 'expert'].includes(payload.mode)) {
    chatMode.value = payload.mode
  }
  if (payload.createNew) await createConversation()
  await sendMessage(payload)
}

async function refreshTopicNews() {
  const topic = reportPanel.value?.topic || readStoredReportTopic()?.topic
  if (!topic) return
  topicNewsLoading.value = true
  try {
    const { items, errorMessage: err, infoMessage } = await loadItemsForReportTopic(topic)
    const loadError = err || (!items.length && infoMessage ? infoMessage : '')
    reportPanel.value = {
      topic,
      items,
      loadError: loadError && !items.length ? loadError : '',
      loadedAt: Date.now(),
    }
  } finally {
    topicNewsLoading.value = false
  }
}

function compactPageSkillList(label, value, limit = 6) {
  const list = Array.isArray(value)
    ? value
    : String(value || '')
      .split(/[;\n]/)
      .map((item) => item.trim())
      .filter(Boolean)
  if (!list.length) return ''
  return `${label}: ${list.slice(0, limit).join('；')}`
}

function buildEmbeddedPageSkillContext() {
  if (!props.embedded || !props.pageSkill || typeof props.pageSkill !== 'object') return ''
  const skill = props.pageSkill
  const page = String(skill.page || skill.name || route.meta?.title || route.name || '当前页面').trim()
  const path = String(skill.path || route.fullPath || route.path || '').trim()
  const summary = String(skill.summary || '').replace(/\s+/g, ' ').trim()
  const aiActions = skill.aiActions || skill.ai_actions || []
  const lines = [
    '【当前页面 Skill】',
    `页面: ${page}`,
    path ? `访问路径: ${path}` : '',
    summary ? `页面内容: ${summary}` : '',
    compactPageSkillList('主要区域', skill.sections),
    compactPageSkillList('访问方式', skill.access),
    compactPageSkillList('用户可操作', skill.actions),
    compactPageSkillList('AI可协同', aiActions),
    '协作要求: 你正在当前页面侧栏中辅助用户。优先解释当前页面信息；能联动页面时说明会同步页面控件，不能直接操作时给出明确的页面操作路径。',
  ]
  return lines.filter(Boolean).join('\n')
}

function buildChatMessageBody(userQuestion) {
  const q = String(userQuestion || '').trim()
  const payload = { message: q, top_k_news: 8, top_k_clusters: 8 }
  const pw = pinnedWorkspace.value
  if (pw) {
    payload.pinned_workspace = pw.name
  }
  if (pinnedFavoriteFolder.value) {
    payload.favorite_context = {
      folder: displayFolderName(pinnedFavoriteFolder.value),
      items: pinnedFavoriteCards.value.slice(0, 24).map(compactFavoriteMaterial),
    }
  }
  if (selectedHermesSkills.value.length || selectedHermesDatabases.value.length) {
    payload.knowledge_context = {
      skills: selectedHermesSkills.value.slice(0, 30),
      database_cards: selectedHermesDatabases.value.slice(0, 20),
    }
  }
  const contextBlocks = []
  const pageSkillBlock = buildEmbeddedPageSkillContext()
  if (pageSkillBlock) {
    contextBlocks.push(pageSkillBlock)
    payload.page_context = props.pageSkill
  }
  const panel = reportPanel.value
  if (panel?.topic && panel.items?.length) {
    const lines = [
      `【报告中心主题：${panel.topic}】`,
      '下列为该主题下收藏/预警新闻标题（请结合用户问题作答）：',
    ]
    for (let i = 0; i < panel.items.length; i += 1) {
      const it = panel.items[i]
      const tag = it.__tagType === 'warning' ? '预警' : '收藏'
      const title = String(it.title || '无标题')
        .replace(/\s+/g, ' ')
        .slice(0, 180)
      lines.push(`${i + 1}. [${tag}] ${title} (id:${it.id})`)
    }
    contextBlocks.push(lines.join('\n'))
  }
  if (!contextBlocks.length) return payload
  const footer = `\n\n----\n【用户问题】\n${q}`
  let combined = `${contextBlocks.join('\n\n')}${footer}`
  if (combined.length > MAX_MESSAGE_LEN) {
    const compactBlocks = []
    if (pageSkillBlock) compactBlocks.push(pageSkillBlock)
    if (panel?.topic && panel.items?.length) {
      const head = `【报告中心主题：${panel.topic}】\n下列为该主题下收藏/预警新闻标题（已压缩）：\n`
      const restBudget = Math.max(200, MAX_MESSAGE_LEN - pageSkillBlock.length - head.length - footer.length - 80)
      const acc = [head]
      let used = 0
      for (let i = 0; i < panel.items.length && used < restBudget; i += 1) {
        const it = panel.items[i]
        const tag = it.__tagType === 'warning' ? '预警' : '收藏'
        const title = String(it.title || '无标题')
          .replace(/\s+/g, ' ')
          .slice(0, 72)
        const line = `${i + 1}. [${tag}] ${title} (id:${it.id})`
        acc.push(line)
        used += line.length + 1
      }
      acc.push('...（标题列表过长已截断）')
      compactBlocks.push(acc.join('\n'))
    }
    combined = `${compactBlocks.join('\n\n')}${footer}`
    if (combined.length > MAX_MESSAGE_LEN) {
      combined = `${combined.slice(0, MAX_MESSAGE_LEN - 18)}\n【上下文已截断】`
    }
  }
  payload.message = combined
  return payload
}

function onReportFavoritesUpdated() {
  favoriteFolderVersion.value += 1
  if (!reportPanel.value?.topic) return
  refreshTopicNews()
}

function onAuthChanged() {
  briefingIdentityGeneration += 1
  briefingScheduleLoader.invalidate()
  briefingLoading.value = false
  briefingSaving.value = false
  briefingRunningId.value = ''
  briefingSchedules.value = []
  resetBriefingForm()
  isAuthenticated.value = Boolean(getToken())
  bootstrapSessions()
  refreshKnowledgeContextFromStorage()
  if (!props.embedded) {
    fetchBriefingSchedules()
    fetchWorkspaces()
  }
}

watch(activeTopTab, (tab) => {
  if (props.embedded && tab !== 'history') {
    activeTopTab.value = 'history'
    return
  }
  if (tab === 'history') {
    autoSelectLatestConversation()
  }
})

watch(activeSideNav, (nav) => {
  if (props.embedded && nav !== 'chat') {
    activeSideNav.value = 'chat'
    return
  }
  if (nav === 'chat') {
    activeTopTab.value = 'history'
  }
})

watch(
  () => route.query.session_id,
  () => {
    if (props.embedded) return
    openRouteConversation()
  },
)

// 自动缓存会话列表
watch(conversations, (list) => {
  if (list.length > 0) cacheSessions(list)
}, { deep: true })

onMounted(async () => {
  enforceEmbeddedChatState()
  activeFavoriteFolder.value = normalizeFolderName(localStorage.getItem(FAVORITE_THEME_NAME_KEY))
  const rawPinnedFavorite = localStorage.getItem(PINNED_FAVORITE_FOLDER_KEY)
  if (rawPinnedFavorite && String(rawPinnedFavorite).trim()) {
    const storedPinnedFavorite = normalizeFolderName(rawPinnedFavorite)
    pinnedFavoriteFolder.value = storedPinnedFavorite
    activeFavoriteFolder.value = storedPinnedFavorite
  }
  refreshKnowledgeContextFromStorage()
  if (!props.embedded) {
    const stored = readStoredReportTopic()
    if (stored?.topic) {
      reportPanel.value = { topic: stored.topic, items: [], loadError: '', loadedAt: 0 }
      if (!reportConfig.topic.trim()) reportConfig.topic = stored.topic
      await refreshTopicNews()
    }
  }
  await bootstrapSessions()
  if (!props.embedded) {
    await openRouteConversation()
    fetchWorkspaces()
    fetchBriefingSchedules()
  }
  enforceEmbeddedChatState()
  window.addEventListener('reportFavoritesUpdated', onReportFavoritesUpdated)
  window.addEventListener(getAuthChangedEventName(), onAuthChanged)
  // 通用页面上下文：其他业务页面可传入结构化材料，但必须由用户确认后再发送。
  const autorunContext = readAutorunContext()
  const alertMsg = sessionStorage.getItem('data_assistant_alert_msg')
  if (autorunContext || alertMsg) {
    sessionStorage.removeItem('data_assistant_alert_msg')
    const payload = autorunContext || {
      visibleMessage: alertMsg,
      prompt: alertMsg,
      mode: 'expert',
      extraBody: null,
      createNew: false,
    }
    queueAssistantContextConfirmation(payload)
  }
})

// 一键研判反馈就绪：收到第一条回复时通知父页面
watch(hasStreamContent, (val) => {
  if (val && sessionStorage.getItem('data_assistant_alert_ready') === null) {
    sessionStorage.setItem('data_assistant_alert_ready', '1')
    window.parent.postMessage({ type: 'alertReady' }, '*')
  }
})

onUnmounted(() => {
  briefingIdentityGeneration += 1
  briefingScheduleLoader.invalidate()
  chatStreamController.dispose()
  reportStreamController.dispose()
  window.removeEventListener('reportFavoritesUpdated', onReportFavoritesUpdated)
  window.removeEventListener(getAuthChangedEventName(), onAuthChanged)
})

async function streamAssistantText(fullText, streamOpts = {}) {
  const text = String(fullText || '').trim() || '收到。'
  const streamMsg = reactive({
    role: 'assistant',
    text: '',
    showDetailChip: false,
    storedFullReply: '',
    detailIndex: 1,
    toolCalls: [],
    sources: [],
  })
  messages.value.push(streamMsg)
  streaming.value = true
  const len = text.length
  for (let i = 0; i < len; i += 1) {
    streamMsg.text += text[i]
    const delay = i < 80 ? 10 : i < 280 ? 16 : 22
    await sleep(delay)
  }
  streaming.value = false
  if (streamOpts.showDetailChip) streamMsg.showDetailChip = true
  if (streamOpts.storedFullReply) streamMsg.storedFullReply = String(streamOpts.storedFullReply)
}

/**
 * SSE：按 \\n\\n 分帧；同一帧内多行 data: 按规范用 \\n 拼接后再 JSON.parse
 */
function maybeEmitEmbeddedPageSearch(ev, phase) {
  if (!props.embedded) return
  const tool = String(ev?.tool || '').toLowerCase()
  if (tool === 'web_search') return
  const searchLikeTool =
    ['news_search', 'event_cluster_search', 'macro_event_search'].includes(tool)
    || ['news', 'cluster', 'retrieval'].some((part) => tool.includes(part))
  if (!searchLikeTool) return
  const query = pickSearchQueryFromPayload(ev)
  if (!query) return
  const key = `${tool}:${query}`
  if (emittedPageActionKeys.has(key)) return
  emittedPageActionKeys.add(key)
  emit('page-action', {
    type: 'search',
    phase,
    tool: ev.tool || '',
    query,
    input: ev.input || ev.invoke || ev.result || {},
  })
}

function scrollChatToBottom() {
  nextTick(() => {
    const el = chatScrollAiEl.value || chatScrollBriefingEl.value
    if (!el || typeof el.scrollTop !== 'number') return
    el.scrollTop = el.scrollHeight
  })
}

/** 页面只编排 Vue 状态与导航；SSE 生命周期和事件归约由 chat feature 负责。 */
async function sendMessage(sendOptions = {}) {
  const q = String(sendOptions.visibleMessage ?? inputText.value ?? '').trim()
  const requestMessage = String(sendOptions.prompt ?? q).trim()
  if (!q || !requestMessage || sending.value) return
  if (!getToken()) {
    showError('请先登录后使用数据助手')
    requestAssistantLogin()
    return
  }
  emittedPageActionKeys.clear()
  if (!hasMessages.value) {
    const cur = convId(currentConversationId.value)
    const c = conversations.value.find((x) => convId(x.id) === cur)
    if (c) c.title = q.slice(0, 20)
  }
  messages.value.push({ role: 'user', text: q })
  inputText.value = ''
  scrollChatToBottom()

  // 立即创建助手占位消息，让加载动画第一时间渲染
  const placeholderMsg = reactive(createChatStreamMessage())
  messages.value.push(placeholderMsg)
  const turnMessages = messages.value
  let cacheSessionId = convId(currentConversationId.value)
  sending.value = true
  streaming.value = true

  const streamMsg = placeholderMsg
  let streamState = createChatStreamState({
    message: streamMsg,
    newsHits: newsHits.value,
    clusterHits: clusterHits.value,
  })

  try {
    const requestBody = {
      ...buildChatMessageBody(requestMessage),
      ...(sendOptions.extraBody && typeof sendOptions.extraBody === 'object' ? sendOptions.extraBody : {}),
      debug: false,
      user_visible_message: q,
      mode: sendOptions.mode || chatMode.value,
    }
    const sidRaw = convId(currentConversationId.value)
    if (getToken() && sidRaw && !sidRaw.startsWith('local')) {
      const n = Number(sidRaw)
      if (!Number.isNaN(n)) requestBody.session_id = n
    }
    const applySessionId = (value) => {
      if (value == null || !getToken()) return
      const sessionId = String(value)
      cacheSessionId = sessionId
      if (!conversations.value.some((item) => convId(item.id) === sessionId)) {
        conversations.value.unshift({ id: sessionId, title: '新会话', at: Date.now() })
      }
      currentConversationId.value = sessionId
    }
    await chatStreamController.run({
      requestBody,
      onResponse(response) {
        const headerSessionId = response.headers.get('X-Assistant-Session-Id')
        if (headerSessionId) applySessionId(headerSessionId)
      },
      onEvent(event) {
        const reduction = reduceChatStreamEvent(streamState, event, {
          normalizeWebSourceGroups,
          sourceGroupsFromToolResult,
          mergeSourceGroups,
        })
        streamState = reduction.state
        Object.assign(streamMsg, streamState.message)
        if (reduction.effects.contextUpdated) {
          newsHits.value = streamState.newsHits
          clusterHits.value = streamState.clusterHits
        }
        applySessionId(reduction.effects.sessionId)
        if (reduction.effects.pageActionPhase) {
          maybeEmitEmbeddedPageSearch(event, reduction.effects.pageActionPhase)
        }
        if (reduction.effects.shouldScroll) scrollChatToBottom()
      },
    })
    streamState = finalizeChatStreamState(streamState)
    Object.assign(streamMsg, streamState.message)

    panelDetailReply.value = ''
    expandedReplyDetail.value = false
    await syncSessionFromServerAfterTurn()
  } catch (e) {
    if (isChatStreamAbortError(e)) {
      if (streamMsg) appendStoppedNote(streamMsg)
      panelDetailReply.value = ''
      expandedReplyDetail.value = false
      return
    }
    panelDetailReply.value = ''
    expandedReplyDetail.value = false
    const last = reactiveAssistantFromList()
    const errLine = `接口异常：${e?.message || e}`
    if (last?.role === 'assistant') {
      last.text = last.text ? `${last.text}\n\n${errLine}` : errLine
      last.storedFullReply = last.text
    } else {
      await streamAssistantText(errLine)
    }
  } finally {
    sending.value = false
    streaming.value = false
    // 缓存消息
    if (cacheSessionId && turnMessages.length > 0) {
      cacheMessages(cacheSessionId, turnMessages)
    }
    scrollChatToBottom()
  }
}

async function continueTruncatedAnswer() {
  if (sending.value) return
  await sendMessage({
    visibleMessage: '继续上一条回答',
    prompt: '请从上一条回答中断的位置直接继续，不要重复已经显示的内容；补完剩余部分并给出完整结论。',
    mode: chatMode.value,
  })
}

async function sendBriefingMessage() {
  const q = String(briefingDraft.value || '').trim()
  if (!q || sending.value) return
  inputText.value = q
  briefingDraft.value = ''
  await sendMessage()
  scrollChatToBottom()
}

function reactiveAssistantFromList() {
  const last = messages.value[messages.value.length - 1]
  return last?.role === 'assistant' ? last : null
}
</script>

<template>
  <div class="yisight-shell" data-tour="assistant-workspace" :class="{ 'yisight-shell--embedded': props.embedded }">
    <div v-if="!props.embedded && !isAuthenticated" class="assistant-auth-gate">
      <div class="assistant-auth-gate-card" data-tour="assistant-auth-gate">
        <div class="assistant-auth-gate-mark">G</div>
        <div>
          <p class="assistant-auth-gate-kicker">GlobeMind Data Assistant</p>
          <h1>登录后使用数据助手</h1>
          <p>工作区文件、知识库、定时简报、报告生成和历史会话都需要账户权限。登录后会自动加载你的工作台数据。</p>
        </div>
        <div class="assistant-auth-gate-actions">
          <button type="button" class="assistant-auth-gate-primary" @click="requestAssistantLogin">
            登录后进入
          </button>
          <router-link class="assistant-auth-gate-secondary" :to="{ path: '/register', query: { redirect: route.fullPath } }">免费注册</router-link>
          <router-link class="assistant-auth-gate-home" to="/">返回首页</router-link>
        </div>
      </div>
    </div>
    <template v-else>
    <!-- 左侧竖向导航（参考图） -->
    <aside v-if="!props.embedded" class="ys-sidenav" data-tour="assistant-navigation" aria-label="侧边导航">
      <div class="ys-sidenav-logo" title="GlobeMind">
        <div class="ys-sidenav-logo-badge">G</div>
      </div>
      <nav class="ys-sidenav-nav">
        <button
          v-for="it in sideNavItems"
          :key="it.key"
          type="button"
          class="ys-sidenav-item"
          :class="{ active: activeTopTab === 'history' && activeSideNav === it.key }"
          @click="selectSideNav(it.key)"
        >
          <component :is="it.icon" class="ys-sidenav-icon" :size="19" :stroke-width="1.8" aria-hidden="true" />
          <span class="ys-sidenav-label">{{ it.label }}</span>
        </button>
      </nav>
    </aside>

    <!-- 主区：顶部标签 + 内容 -->
    <section class="ys-body">
      <header class="ys-topbar" :class="{ 'ys-topbar--conversation': hasConversation && (activeTopTab === 'briefing' || activeTopTab === 'history') }" aria-label="顶部栏">
        <div class="ys-tabs" role="tablist" aria-label="工作区">
          <button
            v-for="t in topTabs"
            :key="t.key"
            type="button"
            role="tab"
            class="ys-tab"
            :class="{ active: activeTopTab === t.key }"
            @click="selectTopTab(t.key)"
          >
            {{ t.label }}
          </button>
        </div>
        <div v-if="hasConversation && (activeTopTab === 'briefing' || activeTopTab === 'history')" class="ys-topbar-brand">GlobeMind</div>
        <div class="ys-topbar-right">
          <span class="ys-topbar-link">套餐</span>
          <button type="button" class="ys-topbar-link ys-topbar-action" @click="openDisplaySettings">显示设置</button>
        </div>
      </header>

      <div class="ys-content">
        <!-- 定期简报：左侧任务列表 + 中间卡片 -->
        <aside v-if="activeTopTab === 'briefing'" class="ys-taskpanel" aria-label="定期任务列表">
          <button type="button" class="ys-task-new" @click="resetBriefingForm">新增定时任务</button>
          <div v-if="briefingLoading" class="ys-history-panel-empty">
            正在读取任务…
          </div>
          <div v-else-if="periodicTasks.length === 0" class="ys-history-panel-empty">
            暂无定时简报任务
          </div>
          <div class="ys-task-list">
            <button
              v-for="t in periodicTasks"
              :key="t.id"
              type="button"
              class="ys-task-card"
              :class="{ 'ys-history-session-card--active': String(selectedBriefingId) === String(t.id) }"
              @click="selectBriefingSchedule(t.id)"
            >
              <div class="ys-task-card-top">
                <span class="ys-task-pill">Hermes</span>
                <span class="ys-task-stats">{{ t.stats }}</span>
              </div>
              <div class="ys-task-title">{{ t.title }}</div>
              <div class="ys-task-meta">下次：{{ t.at }}</div>
              <div class="ys-task-row">
                <span class="ys-task-cadence">{{ t.cadence }}</span>
                <span class="ys-task-status">{{ t.status }}</span>
              </div>
            </button>
          </div>
        </aside>

        <aside v-if="activeTopTab === 'history'" class="ys-taskpanel" aria-label="历史会话列表">
          <button type="button" class="ys-task-new ys-history-start-btn" @click="startNewConversation">
            <Plus :size="17" :stroke-width="2.2" aria-hidden="true" />
            <span>发起新对话</span>
          </button>
          <label class="ys-history-search">
            <Search :size="16" :stroke-width="1.9" aria-hidden="true" />
            <input v-model="historySearch" type="search" placeholder="搜索会话" aria-label="搜索历史会话" />
            <SlidersHorizontal :size="15" :stroke-width="1.8" aria-hidden="true" />
          </label>
          <div class="ys-history-list-heading">
            <span>最近会话</span>
            <small>{{ filteredConversations.length }}</small>
          </div>
          <div v-if="conversations.length === 0" class="ys-history-panel-empty">
            暂无历史会话
          </div>
          <div v-else-if="filteredConversations.length === 0" class="ys-history-panel-empty">
            没有匹配的会话
          </div>
          <div v-else class="ys-task-list">
            <button
              v-for="conv in filteredConversations"
              :key="conv.id"
              type="button"
              class="ys-task-card ys-history-session-card"
              :class="{ 'ys-history-session-card--active': String(conv.id) === String(currentConversationId) }"
              @click="openHistoryConversation(conv.id)"
            >
              <div class="ys-task-card-top">
                <span class="ys-task-stats">{{ formatConvTime(conv.at) }}</span>
              </div>
              <div class="ys-task-card-body">
                <div class="ys-task-title">{{ conv.title || '会话' }}</div>
                <div class="ys-task-meta">会话记录</div>
              </div>
              <button class="ys-history-session-delete" @click.stop="deleteConversation(conv.id)" title="删除会话">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                  <line x1="10" y1="11" x2="10" y2="17"/>
                  <line x1="14" y1="11" x2="14" y2="17"/>
                </svg>
              </button>
            </button>
          </div>
        </aside>

        <main class="ys-main" data-tour="assistant-main">
          <!-- ====== 侧导航页面容器 ====== -->
          <div v-if="activeSideNav !== 'chat' && activeTopTab === 'history'" class="ys-page-wrap">

          <!-- 看板 -->
          <div v-if="activeSideNav === 'dashboard'" class="ys-page">
            <section class="dash-hero-panel">
              <div class="dash-hero-copy">
                <p class="dash-eyebrow">Global Intelligence Desk</p>
                <h1>数据助手工作台</h1>
                <p>围绕收藏新闻、知识库和会话记录，快速进入研判、报告和资料整理流程。</p>
              </div>
              <div class="dash-hero-actions">
                <button type="button" class="dash-primary-btn" @click="selectSideNav('chat'); startNewConversation()">新建对话</button>
                <button type="button" class="dash-secondary-btn" @click="selectSideNav('favorites')">查看收藏</button>
              </div>
            </section>

            <div class="dash-command-grid">
              <button class="dash-command-card dash-command-card--blue" @click="selectSideNav('favorites')">
                <span class="dash-command-k">收藏新闻</span>
                <strong>{{ favoriteSummary.articles }}</strong>
                <span>{{ favoriteSummary.folders }} 个工作文件夹</span>
              </button>
              <button class="dash-command-card dash-command-card--green" @click="selectSideNav('chat')">
                <span class="dash-command-k">历史会话</span>
                <strong>{{ conversations.length }}</strong>
                <span>{{ activeConversationTitle }}</span>
              </button>
              <button class="dash-command-card dash-command-card--amber" @click="selectTopTab('agentReport')">
                <span class="dash-command-k">报告产出</span>
                <strong>{{ dashboardStats.reportsGenerated }}</strong>
                <span>历史报告与草稿</span>
              </button>
              <button class="dash-command-card dash-command-card--violet" @click="selectSideNav('kb')">
                <span class="dash-command-k">知识连接</span>
                <strong>{{ dashboardStats.dataSources }}</strong>
                <span>{{ dashboardStats.dataSources ? '上下文已接入' : '尚未接入上下文' }}</span>
              </button>
            </div>

            <div class="dash-work-grid">
              <section class="dash-panel dash-panel--timeline">
                <div class="dash-panel-head">
                  <div>
                    <h2>研判任务队列</h2>
                    <p>按优先级整理需要继续跟进的主题</p>
                  </div>
                  <span class="dash-live-badge">任务配置</span>
                </div>
                <div class="dash-timeline-list">
                  <div v-if="!periodicTasks.length" class="dash-empty-state">
                    暂无定时简报任务。可进入定期简报创建主题队列。
                  </div>
                  <button v-for="t in periodicTasks" :key="t.id" type="button" class="dash-timeline-item" @click="openBriefingTask(t.id)">
                    <span class="dash-timeline-line"></span>
                    <span class="dash-timeline-status">{{ t.status }}</span>
                    <strong>{{ t.title }}</strong>
                    <small>{{ t.cadence }} · {{ t.at }}</small>
                  </button>
                </div>
              </section>

              <section class="dash-panel">
                <div class="dash-panel-head">
                  <div>
                    <h2>近 7 日产出</h2>
                    <p>报告、对话和收藏整理活跃度</p>
                  </div>
                </div>
                <div class="dash-chart-bars dash-chart-bars--modern" :class="{ 'dash-chart-bars--empty': !dashboardStats.weeklyTotal }">
                  <div v-for="point in dashboardStats.weeklyTrend" :key="point.key" class="dash-bar-col">
                    <div class="dash-bar-track">
                      <div class="dash-bar-fill" :style="{ height: dashboardBarHeight(point.value) }"></div>
                    </div>
                    <span class="dash-bar-label">{{ point.label }}</span>
                  </div>
                  <div v-if="!dashboardStats.weeklyTotal" class="dash-chart-empty">近 7 日暂无可统计活动</div>
                </div>
              </section>

              <section class="dash-panel dash-panel--focus">
                <div class="dash-panel-head">
                  <div>
                    <h2>当前收藏焦点</h2>
                    <p>{{ displayFolderName(activeFavoriteFolder) }} · {{ favoriteSummary.activeCount }} 条</p>
                  </div>
                </div>
                <p class="dash-focus-title">{{ favoriteSummary.latestTitle }}</p>
                <button type="button" class="dash-secondary-btn dash-secondary-btn--wide" @click="askAboutFavoriteFolder" :disabled="!activeFavoriteCards.length">
                  基于当前文件夹提问
                </button>
              </section>
            </div>
          </div>

          <!-- ====== 侧导航页面：收藏 ====== -->
          <div v-else-if="activeSideNav === 'favorites'" class="ys-page favorites-page">
            <div class="ys-page-head favorites-head">
              <div>
                <h1 class="ys-page-title">收藏文件夹</h1>
                <p class="ys-page-sub">与新闻检索台共用同一套星标收藏，可直接作为助手研判上下文。</p>
              </div>
              <button type="button" class="dash-primary-btn" @click="askAboutFavoriteFolder" :disabled="!activeFavoriteCards.length">
                让助手分析
              </button>
            </div>
            <div class="favorites-layout">
              <aside class="favorites-folder-rail">
                <article
                  v-for="folder in favoriteFolderCards"
                  :key="folder.name"
                  role="button"
                  tabindex="0"
                  class="favorites-folder-card"
                  :class="{ active: folder.name === activeFavoriteFolder, pinned: folder.name === pinnedFavoriteFolder }"
                  @click="selectFavoriteFolder(folder.name)"
                  @keydown.enter.prevent="selectFavoriteFolder(folder.name)"
                >
                  <span class="favorites-folder-tab"></span>
                  <button
                    type="button"
                    class="favorites-folder-pin"
                    :class="{ active: folder.name === pinnedFavoriteFolder }"
                    :title="folder.name === pinnedFavoriteFolder ? '关闭收藏上下文' : '启动为对话素材'"
                    @click.stop="folder.name === pinnedFavoriteFolder ? clearPinnedFavoriteFolder() : activateFavoriteFolder(folder.name)"
                  >
                    {{ folder.name === pinnedFavoriteFolder ? '●' : '○' }}
                  </button>
                  <strong>{{ folder.displayName }}</strong>
                  <span>{{ folder.count }} 条收藏</span>
                  <small>{{ folder.latestTitle || '暂无收藏' }}</small>
                </article>
              </aside>
              <section class="favorites-news-panel">
                <div class="favorites-news-head">
                  <div>
                    <h2>{{ displayFolderName(activeFavoriteFolder) }}</h2>
                    <p>{{ activeFavoriteCards.length }} 条收藏新闻</p>
                  </div>
                  <router-link class="favorites-open-search" to="/data-service/data-search">打开新闻检索台</router-link>
                </div>
                <div v-if="!activeFavoriteCards.length" class="favorites-empty">
                  当前文件夹还没有收藏。到新闻检索台点击星标后，会同步出现在这里。
                </div>
                <div v-else class="favorites-news-list">
                  <router-link
                    v-for="item in activeFavoriteCards"
                    :key="item.id"
                    class="favorites-news-card"
                    :to="`/data-service/news/${item.id}`"
                  >
                    <div class="favorites-news-title">{{ item.title || '无标题' }}</div>
                    <div class="favorites-news-meta">
                      <span>{{ item.source || '未知来源' }}</span>
                      <span>{{ formatDateTime(item.displayTime || item.pub_time || item.time) }}</span>
                    </div>
                    <p>{{ item.displayDesc || item.abstract || item.desc || '暂无摘要' }}</p>
                  </router-link>
                </div>
              </section>
            </div>
          </div>

          <!-- ====== 侧导航页面：知识库 ====== -->
          <KbPanel v-else-if="activeSideNav === 'kb'" @preview="openKbFilePreview" @context-changed="handleKnowledgeContextChanged" />

          <!-- ====== 侧导航页面：成员 ====== -->
          <SitesMembersPanel v-else-if="activeSideNav === 'sites' || activeSideNav === 'members'" :active-side-nav="activeSideNav" />

          <!-- ====== 侧导航页面：切换工作区 ====== -->
          <div v-else-if="activeSideNav === 'switch'" class="ys-page">
            <!-- 工作区文件列表 -->
            <template v-if="currentWorkspace">
              <div class="ws-file-header">
                <button class="ws-file-back-btn" @click="currentFilePath ? goBackToParentFolder() : leaveWorkspace()">← {{ currentFilePath ? '返回上级' : '返回工作区列表' }}</button>
                <h2 class="ws-file-title">{{ currentWorkspace.name }}<span v-if="currentFilePath" class="ws-file-path-sep"> / </span><span v-if="currentFilePath" class="ws-file-path-current">{{ currentFilePath }}</span><span v-else> / 文件</span></h2>
              </div>
              <div class="ws-file-toolbar">
                <input
                  ref="uploadInputEl"
                  class="ws-file-upload-input"
                  type="file"
                  multiple
                  @change="uploadWorkspaceFiles"
                />
                <label class="ws-file-select-all">
                  <input type="checkbox" :checked="selectedFileNames.size === workspaceFiles.length && workspaceFiles.length > 0" @change="selectAllFiles" />
                  <span>全选</span>
                </label>
                <span class="ws-file-count">已选 {{ selectedFileNames.size }} 个文件</span>
                <button class="ws-download-btn" :disabled="selectedFileNames.size === 0" @click="downloadSelected">
                  下载所选 ({{ selectedFileNames.size }})
                </button>
                <span class="ws-toolbar-spacer"></span>
                <button class="ws-upload-btn" :disabled="uploadingFiles || Boolean(currentFilePath)" :title="currentFilePath ? '当前仅支持上传到工作区根目录' : '上传文件到当前工作区'" @click="openUploadDialog">
                  {{ uploadingFiles ? '上传中…' : '上传文件' }}
                </button>
                <button class="ws-file-create-btn" @click="openCreateFileDialog">+ 新建</button>
                <div class="ws-view-toggle" role="group" aria-label="视图切换">
                  <button class="ws-view-toggle-btn" :class="{ active: fileViewMode === 'list' }" @click="fileViewMode = 'list'" title="列表视图">☰</button>
                  <button class="ws-view-toggle-btn" :class="{ active: fileViewMode === 'card' }" @click="fileViewMode = 'card'" title="卡片视图">⊞</button>
                </div>
              </div>
              <div v-if="filesLoading" class="ws-file-loading">加载中…</div>
              <div v-else-if="workspaceFiles.length === 0" class="ws-file-empty">
                <div class="ws-file-empty-text">{{ currentFilePath ? '该目录为空' : '该工作区暂无文件' }}</div>
                <div class="ws-file-empty-actions">
                  <button class="ws-upload-btn" :disabled="uploadingFiles || Boolean(currentFilePath)" @click="openUploadDialog">
                    {{ uploadingFiles ? '上传中…' : '上传文件' }}
                  </button>
                  <button class="ws-file-create-btn" @click="openCreateFileDialog">+ 新建文件</button>
                </div>
              </div>
              <!-- 列表视图 -->
              <div v-else-if="fileViewMode === 'list'" class="ws-file-list">
                <div v-for="f in workspaceFiles" :key="f.name" class="ws-file-item" :class="{ 'ws-file-item--selected': selectedFileNames.has(f.name), 'ws-file-item--dir': f.is_dir }" @click="f.is_dir ? enterWorkspaceFolder(f.name) : undefined">
                  <label class="ws-file-checkbox">
                    <input type="checkbox" :checked="selectedFileNames.has(f.name)" @change="toggleFileSelection(f.name)" />
                  </label>
                  <span class="ws-file-icon">{{ f.is_dir ? '📁' : '📄' }}</span>
                  <span v-if="f.is_dir" class="ws-file-name ws-file-name--dir" @click.stop="enterWorkspaceFolder(f.name)">{{ f.name }}</span>
                  <span v-else-if="isTextPreviewFile(f.name)" class="ws-file-name ws-file-name--clickable" @click="openFilePreview(f)" :title="'点击预览 ' + f.name">{{ f.name }}</span>
                  <span v-else class="ws-file-name">{{ f.name }}</span>
                  <span v-if="!f.is_dir" class="ws-file-size">{{ (f.size / 1024).toFixed(1) }} KB</span>
                  <span class="ws-file-modified">{{ f.modified }}</span>
                  <span class="ws-file-actions">
                    <button v-if="isTextPreviewFile(f.name) && !f.is_dir" class="ws-file-open-btn" @click.stop="openFilePreview(f)">打开</button>
                    <button v-if="!f.is_dir" type="button" class="ws-file-download-link" title="下载" @click.stop="downloadWorkspaceFile(fileFullPath(f))">↓</button>
                  </span>
                </div>
              </div>
              <!-- 卡片视图 -->
              <div v-else class="ws-file-card-grid">
                <div v-for="f in workspaceFiles" :key="f.name" class="ws-file-card" :class="{ 'ws-file-card--selected': selectedFileNames.has(f.name), 'ws-file-card--dir': f.is_dir }" @click="f.is_dir ? enterWorkspaceFolder(f.name) : toggleFileSelection(f.name)">
                  <div class="ws-file-card-top">
                    <label class="ws-file-checkbox" @click.stop>
                      <input type="checkbox" :checked="selectedFileNames.has(f.name)" @change="toggleFileSelection(f.name)" />
                    </label>
                    <span class="ws-file-card-icon">{{ f.is_dir ? '📁' : '📄' }}</span>
                    <button v-if="!f.is_dir" type="button" class="ws-file-download-link" title="下载" @click.stop="downloadWorkspaceFile(fileFullPath(f))">↓</button>
                  </div>
                  <div class="ws-file-card-name">
                    <span v-if="f.is_dir" class="ws-file-name--dir" @click.stop="enterWorkspaceFolder(f.name)">{{ f.name }}</span>
                    <span v-else-if="isTextPreviewFile(f.name)" class="ws-file-name--clickable" @click.stop="openFilePreview(f)" :title="'点击预览 ' + f.name">{{ f.name }}</span>
                    <span v-else>{{ f.name }}</span>
                  </div>
                  <div v-if="!f.is_dir" class="ws-file-card-size">{{ (f.size / 1024).toFixed(1) }} KB</div>
                  <div class="ws-file-card-modified">{{ f.modified }}</div>
                  <div class="ws-file-card-hover">
                    <button v-if="f.is_dir" class="ws-file-open-btn" @click.stop="enterWorkspaceFolder(f.name)">进入</button>
                    <button v-else-if="isTextPreviewFile(f.name)" class="ws-file-open-btn" @click.stop="openFilePreview(f)">打开</button>
                  </div>
                </div>
              </div>
            </template>
            <!-- 工作区列表 -->
            <template v-else>
              <div class="ys-page-head">
                <h1 class="ys-page-title">工作区</h1>
                <p class="ys-page-sub">管理 Hermes 可访问的资料目录，并可固定为当前对话工作位置。</p>
              </div>
              <div v-if="workspaceLoading" class="ws-loading">加载中…</div>
              <div v-else-if="workspaceError" class="ws-file-empty">
                <div class="ws-file-empty-text">{{ workspaceError }}</div>
                <router-link class="ws-file-create-btn ws-file-empty-link" to="/login">去登录</router-link>
              </div>
              <div v-else-if="workspaces.length === 0" class="ws-file-empty">
                <div class="ws-file-empty-text">暂无工作区。创建后可上传资料、保存报告，并固定为数据助手上下文。</div>
                <button type="button" class="ws-file-create-btn" @click="createDialogVisible = true">+ 创建工作区</button>
              </div>
              <div v-else class="ws-grid">
                <div v-for="ws in workspaces" :key="ws.name" class="ws-card" @click="enterWorkspace(ws)">
                  <button class="ws-card-del" @click.stop="deleteWorkspace(ws)" title="删除工作区">✕</button>
                  <div class="ws-card-icon" :class="{ 'ws-card-icon--pinned': ws.pinned }" @click.stop="togglePinWorkspace(ws)" :title="ws.pinned ? '取消置顶' : '置顶'">{{ ws.pinned ? '📌' : '📁' }}</div>
                  <div class="ws-card-name">{{ ws.name }}</div>
                  <div class="ws-card-desc">{{ ws.desc || '暂无描述' }}</div>
                  <div class="ws-card-meta">
                    <span>{{ ws.fileCount || 0 }} 个文件</span>
                    <span>更新于 {{ ws.updated ? ws.updated.slice(0, 10) : '-' }}</span>
                  </div>
                  <button class="ws-card-btn" @click.stop="enterWorkspace(ws)">进入</button>
                </div>
                <div class="ws-card ws-card--new" @click="createDialogVisible = true">
                  <div class="ws-card-icon ws-card-icon--add">+</div>
                  <div class="ws-card-name">创建新工作区</div>
                  <div class="ws-card-desc">从空白开始或导入现有配置</div>
                </div>
              </div>
            </template>
          </div>

          </div><!-- end ys-page-wrap -->

          <!-- ====== 对话页面 ====== -->
          <div v-else-if="activeSideNav === 'chat' && activeTopTab === 'history'" class="ys-chat-stage">
            <!-- 顶部栏 -->
            <div class="ys-chat-topbar">
              <div class="ys-chat-brand">
                <span class="ys-chat-brand-dot"></span>
                <span class="ys-chat-brand-name">GlobeMind</span>
                <span class="ys-chat-brand-session">{{ activeConversationTitle }}</span>
                <span v-if="pinnedWorkspace" class="ys-workspace-chip" :title="`Hermes 当前工作目录：${pinnedWorkspace.name}`">
                  <span class="ys-workspace-chip-dot"></span>
                  <span class="ys-workspace-chip-label">工作区</span>
                  <strong>{{ pinnedWorkspace.name }}</strong>
                  <button type="button" class="ys-workspace-chip-close" @click.stop="togglePinWorkspace(pinnedWorkspace)" title="关闭工作区固定">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </span>
                <span v-if="pinnedFavoriteFolder" class="ys-workspace-chip ys-workspace-chip--favorite" :title="`Hermes 收藏素材：${pinnedFavoriteSummary.displayName}`">
                  <span class="ys-workspace-chip-dot"></span>
                  <span class="ys-workspace-chip-label">收藏</span>
                  <strong>{{ pinnedFavoriteSummary.displayName }} · {{ pinnedFavoriteSummary.count }}</strong>
                  <button type="button" class="ys-workspace-chip-close" @click.stop="clearPinnedFavoriteFolder" title="关闭收藏上下文">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </span>
                <span v-if="knowledgeContextCount" class="ys-workspace-chip ys-workspace-chip--knowledge" :title="`Hermes 知识库上下文：${selectedHermesSkills.length} 个 Skill，${selectedHermesDatabases.length} 个数据库`">
                  <span class="ys-workspace-chip-dot"></span>
                  <span class="ys-workspace-chip-label">知识库</span>
                  <strong>{{ selectedHermesSkills.length }}S · {{ selectedHermesDatabases.length }}DB</strong>
                  <button type="button" class="ys-workspace-chip-close" @click.stop="clearKnowledgeContext" title="清空知识库上下文">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </span>
              </div>
              <div class="ys-chat-topbar-end">
                <div v-if="props.embedded" class="ys-chat-session-tools" aria-label="会话切换">
                  <select
                    class="ys-chat-session-select"
                    :value="convId(currentConversationId)"
                    title="切换会话"
                    @change="switchConversationFromSelect"
                  >
                    <option v-if="!conversationOptions.length" value="">暂无会话</option>
                    <option
                      v-for="conv in conversationOptions"
                      :key="conv.id"
                      :value="conv.id"
                      :title="conv.title"
                    >
                      {{ conv.label }}
                    </option>
                  </select>
                  <button type="button" class="ys-chat-session-new" title="新建会话" @click="startNewConversation">
                    新建
                  </button>
                </div>
                <button v-else class="ys-chat-topbar-btn" title="打开素材与线索" @click="toggleContextDrawer">
                  <MoreHorizontal :size="19" :stroke-width="2" aria-hidden="true" />
                </button>
              </div>
            </div>

            <!-- 消息滚动区 -->
            <div class="ys-chat-scroll" ref="chatScrollAiEl">
              <section v-if="pendingAutorunContext" class="ys-chat-context-confirm" aria-label="页面上下文确认">
                <div class="ys-chat-context-confirm-copy">
                  <span>页面上下文</span>
                  <strong>{{ pendingAutorunContext.confirmMessage || pendingAutorunContext.visibleMessage }}</strong>
                  <p>要我基于当前页面材料开始研判吗？确认后才会发送给数据助手。</p>
                </div>
                <div class="ys-chat-context-confirm-actions">
                  <button type="button" class="ys-chat-context-confirm-secondary" @click="dismissAssistantContextConfirmation">
                    先不发送
                  </button>
                  <button
                    type="button"
                    class="ys-chat-context-confirm-primary"
                    :disabled="sending"
                    @click="confirmAssistantContextSend"
                  >
                    开始研判
                  </button>
                </div>
              </section>
              <!-- 欢迎页 -->
              <div v-if="messages.length === 0" class="ys-chat-welcome">
                <div class="ys-chat-welcome-kicker">
                  <img src="/imgs/globemind-mark.png" alt="" />
                  <span>GlobeMind Assistant</span>
                </div>
                <h2 class="ys-chat-welcome-title">今天想研判什么？</h2>
                <p class="ys-chat-welcome-sub">检索新闻、比较事件、梳理趋势或生成报告</p>
                <div class="ys-chat-welcome-chips-card" aria-label="快速开始">
                  <div class="ys-chat-chips">
                    <button
                      v-for="chip in capabilityChips"
                      :key="chip.label"
                      type="button"
                      class="ys-chat-chip"
                      @click="inputText = chip.query; sendMessage()"
                    >
                      <span class="ys-chat-chip-icon" aria-hidden="true">
                        <component :is="chip.icon" :size="18" :stroke-width="1.9" />
                      </span>
                      <span class="ys-chat-chip-copy">
                        <strong>{{ chip.label }}</strong>
                      </span>
                    </button>
                  </div>
                </div>
              </div>
              <!-- 对话消息 -->
              <div v-else class="ys-chat-convo">
                <div class="ys-chat-messages">
                  <div
                    v-for="(m, i) in messages"
                    :key="messageKey(m, i)"
                    class="ys-chat-msg"
                    :class="'ys-chat-msg--' + m.role"
                  >
                    <div class="ys-chat-msg-author" :class="'ys-chat-msg-author--' + m.role">
                      <span v-if="m.role === 'assistant'" class="ys-chat-msg-author-mark">
                        <img src="/imgs/globemind-mark.png" alt="" />
                      </span>
                      <span>{{ m.role === 'assistant' ? 'GlobeMind Assistant' : '你' }}</span>
                    </div>
                    <div class="ys-chat-msg-row">
                      <div class="ys-chat-bubble" :class="'ys-chat-bubble--' + m.role">
                      <details
                        v-if="m.thinking || (m.toolCalls && m.toolCalls.length)"
                        class="ys-chat-toolbox"
                        :class="{ 'ys-chat-aux-ready': assistantMessageReady(m, i) }"
                        :open="toolBoxShouldOpen(m, i)"
                        :data-executing="m.toolCalls && m.toolCalls.some(t => t.type === 'tool_executing')"
                      >
                        <summary class="ys-chat-toolbox-summary">
                          <span class="ys-chat-toolbox-label">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                            工具调用
                          </span>
                          <span class="ys-chat-toolbox-summary-info">
                            <span class="ys-chat-toolbox-counter">{{ m.toolCalls ? m.toolCalls.filter(t => t.type === 'tool_finished').length : 0 }}/{{ m.toolCalls ? m.toolCalls.length : 0 }}</span>
                            <span v-if="m.toolCalls && m.toolCalls.some(t => t.type === 'tool_executing')" class="ys-chat-toolbox-summary-status executing">执行中</span>
                            <span v-else-if="hasSkippedToolCalls(m)" class="ys-chat-toolbox-summary-status skipped">部分跳过</span>
                            <span v-else class="ys-chat-toolbox-summary-status done">完成</span>
                            <span class="ys-chat-toolbox-toggle"></span>
                          </span>
                        </summary>
                        <div class="ys-chat-toolbox-body">
                          <div v-if="m.thinking" class="ys-chat-toolbox-section">
                            <div class="ys-chat-toolbox-section-title">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                              思考过程
                            </div>
                            <div class="ys-chat-toolbox-text">{{ m.thinking }}</div>
                          </div>
                          <div v-if="m.toolCalls && m.toolCalls.length" class="ys-chat-toolbox-section">
                            <div class="ys-chat-toolbox-section-title">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>
                              调用记录
                            </div>
                            <div class="ys-chat-toolbox-list">
                              <div
                                v-for="(tc, ti) in m.toolCalls"
                                :key="ti"
                                class="ys-chat-toolbox-item"
                                :class="['ys-chat-toolbox-item--' + tc.type, 'ys-chat-toolbox-item--' + toolIcon(tc.name)]"
                              >
                                <!-- 工具图标 -->
                                <span class="ys-chat-toolbox-item-icon" :class="'icon--' + toolIcon(tc.name)">
                                  <svg v-if="toolIcon(tc.name) === 'search'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'globe'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'image'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'cluster'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><circle cx="5" cy="19" r="2"/><line x1="12" y1="9" x2="12" y2="5"/><line x1="14.5" y1="13.5" x2="17" y2="17"/><line x1="9.5" y1="13.5" x2="7" y2="17"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'chart'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'book'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'code'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                                  <svg v-else-if="toolIcon(tc.name) === 'file'" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                  <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>
                                </span>
                                <!-- 内容 -->
                                <div class="ys-chat-toolbox-item-body">
                                  <div class="ys-chat-toolbox-item-name">{{ toolLabel(tc.name) }}</div>
                                  <div class="ys-chat-toolbox-item-detail">
                                    <template v-if="tc.invoke">
                                      <!-- Shell 命令 -->
                                      <template v-if="tc.invoke.kind === 'local_shell'">
                                        <span class="detail-cmd">$ {{ tc.invoke.command_preview || tc.input?.command || '' }}</span>
                                      </template>
                                      <!-- 图片生成 -->
                                      <template v-else-if="tc.name === 'image_generate'">
                                        <span class="detail-label">提示词</span>
                                        <span class="detail-value">{{ (tc.input?.prompt || '').slice(0, 160) }}</span>
                                        <span v-if="tc.input?.aspect_ratio" class="detail-tag">{{ tc.input.aspect_ratio }}</span>
                                        <span v-if="tc.input?.image_size" class="detail-tag">{{ tc.input.image_size }}</span>
                                      </template>
                                      <!-- 新闻检索 -->
                                      <template v-else-if="tc.invoke.kind === 'http_post' && tc.invoke.keyword_preview">
                                        <span class="detail-label">关键词</span>
                                        <span class="detail-value">{{ tc.invoke.keyword_preview }}</span>
                                        <span
                                          v-if="tc.invoke.mode"
                                          class="detail-tag"
                                          :title="searchModeDisclosure(tc.invoke.mode).description"
                                        >{{ modeLabel(tc.invoke.mode) }}</span>
                                      </template>
                                      <!-- 联网搜索 -->
                                      <template v-else-if="tc.invoke.kind === 'http_get' && tc.invoke.query_preview">
                                        <span class="detail-label">搜索</span>
                                        <span class="detail-value">{{ tc.invoke.query_preview }}</span>
                                      </template>
                                      <!-- API 查询（事件聚类 / 舆情指数 / 知识库） -->
                                      <template v-else-if="tc.invoke.kind === 'http_get' && tc.invoke.action">
                                        <span class="detail-label">{{ toolActionLabel(tc.name, tc.invoke.action) }}</span>
                                        <span class="detail-value">{{ tc.invoke.action }}</span>
                                      </template>
                                      <!-- 本地文件读取 -->
                                      <template v-else-if="tc.invoke.kind === 'local_read'">
                                        <span class="detail-label">文件</span>
                                        <span class="detail-value">{{ tc.invoke.relative_path || '' }}</span>
                                      </template>
                                      <!-- 代码执行 -->
                                      <template v-else-if="tc.invoke.kind === 'local_sandbox'">
                                        <span class="detail-label">语言</span>
                                        <span class="detail-value">{{ (tc.invoke.language || 'python').toUpperCase() }}</span>
                                        <span v-if="tc.invoke.timeout" class="detail-tag">{{ tc.invoke.timeout }}s</span>
                                      </template>
                                      <!-- 回退：显示输入参数 -->
                                      <template v-else>
                                        <span class="detail-value">{{ tc.input ? JSON.stringify(tc.input).slice(0, 150) : '' }}</span>
                                      </template>
                                    </template>
                                  </div>
                                  <!-- 完成后的结果摘要 -->
                                  <div v-if="tc.type === 'tool_finished' && tc.result" class="ys-chat-toolbox-item-result">
                                    <template v-if="tc.result.image_url">
                                      <div class="ys-chat-image-result">
                                        <a class="ys-chat-image-result-preview" :href="tc.result.image_url" target="_blank" rel="noopener noreferrer">
                                          <img :src="tc.result.image_url" :alt="tc.result.prompt || '生成图片'" loading="lazy" />
                                        </a>
                                        <div class="ys-chat-image-result-meta">
                                          <span class="result-badge ok">图片已生成</span>
                                          <span v-if="tc.result.model" class="result-time">{{ tc.result.model }}</span>
                                          <span v-if="tc.result.query_time_ms != null" class="result-time">{{ tc.result.query_time_ms }}ms</span>
                                          <a :href="tc.result.image_url" target="_blank" rel="noopener noreferrer">打开原图</a>
                                        </div>
                                        <div v-if="tc.result.prompt" class="ys-chat-image-result-prompt">{{ tc.result.prompt }}</div>
                                      </div>
                                    </template>
                                    <template v-else-if="tc.result.skipped">
                                      <span class="result-badge skipped">超时跳过</span>
                                    </template>
                                    <template v-else-if="tc.result.ok === false">
                                      <span class="result-badge skipped">未采用</span>
                                    </template>
                                    <template v-else-if="tc.result.items_returned != null">
                                      <span class="result-badge">返回 {{ tc.result.items_returned }} 条</span>
                                    </template>
                                    <template v-else-if="tc.result.results_count != null">
                                      <span class="result-badge">搜索结果 {{ tc.result.results_count }} 条</span>
                                    </template>
                                    <template v-else-if="tc.result.total != null">
                                      <span class="result-badge">共 {{ tc.result.total }} 条</span>
                                    </template>
                                    <template v-else-if="tc.result.exit_code != null">
                                      <span class="result-badge" :class="tc.result.exit_code === 0 ? 'ok' : 'fail'">退出码 {{ tc.result.exit_code }}</span>
                                    </template>
                                    <template v-else-if="tc.result.ok === true">
                                      <span class="result-badge ok">成功</span>
                                    </template>
                                    <template v-if="tc.result.query_time_ms != null">
                                      <span class="result-time">{{ tc.result.query_time_ms }}ms</span>
                                    </template>
                                    <template v-if="tc.result.titles_preview && tc.result.titles_preview.length">
                                      <div class="result-titles">
                                        <div v-for="(t, ti2) in tc.result.titles_preview.slice(0, 3)" :key="ti2" class="result-title-item">· {{ t }}</div>
                                      </div>
                                    </template>
                                  </div>
                                </div>
                                <!-- 状态 -->
                                <span class="ys-chat-toolbox-item-status" :class="tc.type">
                                  <template v-if="tc.type === 'tool_executing'">
                                    <span class="status-dot-pulse"></span>
                                    执行中
                                  </template>
                                  <template v-else-if="tc.result?.skipped || tc.result?.ok === false">
                                    跳过
                                  </template>
                                  <template v-else>
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                                  </template>
                                </span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </details>
                        <MarkdownRenderer v-if="m.text" :content="m.text" />
                        <div v-if="m.role === 'assistant' && m.truncated" class="ys-chat-truncated" role="status">
                          <span>回答达到当前长度上限，内容尚未完整结束。</span>
                          <button type="button" :disabled="sending" @click="continueTruncatedAnswer">
                            继续生成
                          </button>
                        </div>
                        <details
                          v-if="m.role === 'assistant' && sourceTotal(m.sources) > 0 && assistantMessageReady(m, i)"
                          class="ys-chat-sources ys-chat-aux-ready"
                          open
                        >
                          <summary class="ys-chat-sources-head">
                            <span class="ys-chat-sources-title">
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                              消息源
                            </span>
                            <span class="ys-chat-sources-meta">
                              <span class="ys-chat-sources-count">{{ sourceTotal(m.sources) }} 条</span>
                              <span class="ys-chat-sources-toggle"></span>
                            </span>
                          </summary>
                          <div class="ys-chat-sources-body">
                            <div class="ys-chat-source-days">
                              <button
                                v-for="item in sourceOverviewItems(m.sources)"
                                :key="sourceItemKey(item, item.sourceIndex)"
                                type="button"
                                class="ys-chat-source-day"
                                @click="openSourcePanel(m)"
                              >
                                <span class="ys-chat-source-day-date">{{ item.dateLabel || '来源' }}</span>
                                <strong :title="item.title">{{ item.title }}</strong>
                                <small>{{ sourceOverviewMeta(item) }}</small>
                                <em>{{ sourceOverviewSnippet(item) }}</em>
                              </button>
                            </div>
                            <div v-if="uniqueSourceQueries(m.sources, 2).length" class="ys-chat-source-queryline">
                              检索：
                              <code v-for="(sq, si) in uniqueSourceQueries(m.sources, 2)" :key="si">{{ sq }}</code>
                            </div>
                            <div class="ys-chat-source-actions">
                              <span>{{ sourceGroupTotal(m.sources) }} 组检索结果，已折叠完整列表</span>
                              <button type="button" class="ys-chat-source-expand" @click="openSourcePanel(m)">
                                展开来源列表
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
                              </button>
                            </div>
                          </div>
                        </details>
                        <!-- 等待回复时的加载动画（工具执行 / 思考阶段） -->
                        <div v-if="streaming && i === messages.length - 1 && !m.text" class="ys-chat-loading">
                          <span class="ys-chat-dot"></span>
                          <span class="ys-chat-dot"></span>
                          <span class="ys-chat-dot"></span>
                          <span class="ys-chat-loading-label">思考中</span>
                        </div>
                        <!-- 流式输出中的光标 -->
                        <span v-if="streaming && i === messages.length - 1 && m.text" class="ys-chat-cursor"></span>
                      </div>
                    </div>
                    <div v-if="m.showDetailChip" class="ys-chat-detail-chip-wrap">
                      <button class="ys-chat-detail-chip" @click="openMessageDetail(m)">查看详情</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 输入区 -->
            <div class="ys-chat-composer-wrap">
              <div class="ys-chat-composer">
                <textarea
                  ref="composerTextareaEl"
                  class="ys-chat-composer-input"
                  v-model="inputText"
                  placeholder="向数据助手提问，或让它基于收藏文件夹生成研判…"
                  @keydown.enter.prevent="sendMessage"
                  :disabled="sending"
                  @input="autoResizeComposer"
                ></textarea>
                <div class="ys-chat-composer-actions">
                  <div class="ys-chat-composer-tools">
                    <button type="button" class="ys-chat-composer-tool" title="添加素材与线索" @click="toggleContextDrawer">
                      <Paperclip :size="16" :stroke-width="1.9" aria-hidden="true" />
                      <span>添加上下文</span>
                    </button>
                    <button type="button" class="ys-chat-composer-tool" title="管理收藏上下文" @click="selectSideNav('favorites')">
                      <FolderOpen :size="16" :stroke-width="1.9" aria-hidden="true" />
                      <span>收藏上下文</span>
                      <strong>{{ favoriteSummary.articles }} 条</strong>
                    </button>
                  </div>
                  <button type="button" class="ys-chat-mode-pill" @click.stop="showModeMenu = !showModeMenu">
                    <span>回答模式</span>
                    <strong>{{ chatModeLabel() }}</strong>
                    <ChevronDown :size="14" :stroke-width="2" aria-hidden="true" />
                  </button>
                  <div v-if="showModeMenu" class="ys-chat-mode-menu" @click.stop>
                    <button type="button" :class="{ active: chatMode === 'fast' }" @click="setChatMode('fast')">快速</button>
                    <button type="button" :class="{ active: chatMode === 'pro' }" @click="setChatMode('pro')">研判</button>
                    <button type="button" :class="{ active: chatMode === 'expert' }" @click="setChatMode('expert')">专家</button>
                  </div>
                  <button v-if="streaming" class="ys-chat-stop-btn" @click="stopGeneration">停止</button>
                  <button v-else class="ys-chat-send-btn" @click="sendMessage" :disabled="sending || !inputText.trim()">
                    <span>发送</span>
                    <SendHorizontal :size="16" :stroke-width="2.2" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>

            <!-- 右抽屉：详情 -->
            <aside class="ys-chat-drawer ys-chat-drawer--right" :class="{ 'ys-chat-drawer--open': rightDrawerOpen }">
              <div class="ys-chat-drawer-head">
                <div>
                  <div class="ys-chat-drawer-title">{{ activeSourcePanel ? '消息来源' : '素材与线索' }}</div>
                  <div class="ys-chat-drawer-sub">
                    {{ activeSourcePanel ? `${activeSourcePanel.total} 条来源 · ${activeSourcePanel.groupCount} 组检索` : '当前启用素材会随消息传给 Hermes' }}
                  </div>
                </div>
                <button class="ys-chat-drawer-close" @click="toggleRightDrawer">×</button>
              </div>
              <div class="ys-chat-drawer-body">
                <template v-if="activeSourcePanel">
                  <section class="ys-source-drawer-card">
                    <div class="ys-source-drawer-summary">
                      <div>
                        <span>来源总数</span>
                        <strong>{{ activeSourcePanel.total }}</strong>
                      </div>
                      <div>
                        <span>检索组</span>
                        <strong>{{ activeSourcePanel.groupCount }}</strong>
                      </div>
                      <div>
                        <span>日期段</span>
                        <strong>{{ activeSourcePanel.days.length }}</strong>
                      </div>
                    </div>
                    <div v-if="activeSourcePanel.queries.length" class="ys-source-drawer-queries">
                      <span>search queries</span>
                      <code v-for="(query, qi) in activeSourcePanel.queries" :key="qi">{{ query }}</code>
                    </div>
                  </section>

                  <section
                    v-for="day in activeSourcePanel.days"
                    :key="day.key"
                    class="ys-source-drawer-day"
                  >
                    <div class="ys-source-drawer-day-head">
                      <div>
                        <strong>{{ day.label }}</strong>
                        <span>{{ day.sourceNames.join('、') || '未知来源' }}</span>
                      </div>
                      <em>{{ day.count }} 条</em>
                    </div>
                    <div class="ys-source-drawer-list">
                      <button
                        v-for="(item, si) in day.items"
                        :key="sourceItemKey(item, si)"
                        type="button"
                        class="ys-source-drawer-row"
                        :disabled="!item.url && !item.id"
                        @click="openSourceItem(null, item)"
                      >
                        <span class="ys-source-drawer-rank">{{ item.sourceIndex }}</span>
                        <span class="ys-source-drawer-copy">
                          <strong :title="item.title">{{ item.title }}</strong>
                          <small>
                            {{ item.source || item.domain || '未知来源' }}
                            <template v-if="item.time"> · {{ item.time }}</template>
                          </small>
                          <em v-if="item.url || item.domain">{{ item.url || item.domain }}</em>
                          <em v-if="item.groupQuery || item.groupLabel">
                            {{ item.groupLabel }}<template v-if="item.groupQuery"> · {{ item.groupQuery }}</template><template v-if="item.groupDurationText"> · {{ item.groupDurationText }}</template><template v-if="item.groupUsageText"> · {{ item.groupUsageText }}</template>
                          </em>
                          <p v-if="item.snippet">{{ item.snippet }}</p>
                        </span>
                        <svg class="ys-source-drawer-open" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7"/><path d="M7 7h10v10"/></svg>
                      </button>
                    </div>
                  </section>
                </template>

                <template v-else>
                <section class="ys-context-panel">
                  <div class="ys-context-panel-head">
                    <strong>Hermes 当前上下文</strong>
                    <button v-if="hasHermesContext" type="button" @click="clearAllHermesContext">全部移除</button>
                  </div>
                  <div class="ys-context-stack">
                    <button type="button" class="ys-context-row" :class="{ active: pinnedWorkspace }" @click="manageHermesContext('switch')">
                      <span>
                        <strong>工作区</strong>
                        <small>控制 Hermes 当前工作位置</small>
                      </span>
                      <em>{{ pinnedWorkspace ? pinnedWorkspace.name : '未固定' }}</em>
                    </button>
                    <button type="button" class="ys-context-row" :class="{ active: pinnedFavoriteFolder }" @click="manageHermesContext('favorites')">
                      <span>
                        <strong>收藏</strong>
                        <small>优先作为辅助资料</small>
                      </span>
                      <em>{{ pinnedFavoriteFolder ? `${pinnedFavoriteSummary.count} 条` : '未选择' }}</em>
                    </button>
                    <button type="button" class="ys-context-row" :class="{ active: knowledgeContextCount }" @click="manageHermesContext('kb')">
                      <span>
                        <strong>知识库 / Skill</strong>
                        <small>注入可用方法与连接卡片</small>
                      </span>
                      <em>{{ knowledgeContextCount ? `${selectedHermesSkills.length}S · ${selectedHermesDatabases.length}DB` : '未选择' }}</em>
                    </button>
                  </div>
                </section>
                <section v-if="pinnedWorkspace" class="ys-material-section">
                  <div class="ys-material-section-head">
                    <div>
                      <strong>{{ pinnedWorkspace.name }}</strong>
                      <span>当前工作区 · {{ pinnedWorkspace.fileCount || 0 }} 个文件</span>
                    </div>
                    <div class="ys-material-section-actions">
                      <button type="button" @click="manageHermesContext('switch')">切换</button>
                      <button type="button" @click="togglePinWorkspace(pinnedWorkspace)">移除</button>
                    </div>
                  </div>
                  <div class="ys-material-empty ys-material-empty--left">
                    {{ pinnedWorkspace.desc || 'Hermes 会把该工作区作为当前工作位置，回答中优先遵循这个上下文。' }}
                  </div>
                </section>
                <section v-if="pinnedFavoriteFolder" class="ys-material-section">
                  <div class="ys-material-section-head">
                    <div>
                      <strong>{{ pinnedFavoriteSummary.displayName }}</strong>
                      <span>{{ pinnedFavoriteSummary.count }} 条收藏素材</span>
                    </div>
                    <div class="ys-material-section-actions">
                      <button type="button" @click="manageHermesContext('favorites')">更换</button>
                      <button type="button" @click="clearPinnedFavoriteFolder">移除</button>
                    </div>
                  </div>
                  <div v-if="pinnedFavoriteCards.length" class="ys-material-list">
                    <router-link
                      v-for="item in pinnedFavoriteCards.slice(0, 12)"
                      :key="item.id"
                      class="ys-material-card"
                      :to="`/data-service/news/${item.id}`"
                    >
                      <strong>{{ item.title || '无标题' }}</strong>
                      <span>{{ item.source || '未知来源' }} · {{ formatDateTime(item.displayTime || item.pub_time || item.time) }}</span>
                      <p>{{ item.displayDesc || item.abstract || item.desc || '暂无摘要' }}</p>
                    </router-link>
                  </div>
                  <div v-else class="ys-material-empty">该文件夹暂无可用素材。</div>
                </section>
                <section v-if="selectedHermesSkills.length || selectedHermesDatabases.length" class="ys-material-section">
                  <div class="ys-material-section-head">
                    <div>
                      <strong>知识库连接</strong>
                      <span>{{ selectedHermesSkills.length }} 个 Skill · {{ selectedHermesDatabases.length }} 个数据库</span>
                    </div>
                    <div class="ys-material-section-actions">
                      <button type="button" @click="manageHermesContext('kb')">管理</button>
                      <button type="button" @click="clearKnowledgeContext">清空</button>
                    </div>
                  </div>
                  <div class="ys-material-mini-grid">
                    <div v-for="skill in selectedHermesSkills.slice(0, 8)" :key="`sk-${skill.id || skill.name}`" class="ys-material-mini-card ys-material-mini-card--removable">
                      <button type="button" class="ys-material-mini-remove" @click="removeHermesSkillFromContext(skill.id || skill.name)" title="移除该 Skill">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                      </button>
                      <span>Skill</span>
                      <strong>{{ skill.name || skill.id }}</strong>
                    </div>
                    <div v-for="db in selectedHermesDatabases.slice(0, 6)" :key="`db-${db.id || db.name}`" class="ys-material-mini-card ys-material-mini-card--removable">
                      <button type="button" class="ys-material-mini-remove" @click="removeHermesDatabaseFromContext(db.id || db.name || db.host)" title="移除该数据库卡片">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                      </button>
                      <span>DB</span>
                      <strong>{{ db.name || db.host || '数据库' }}</strong>
                    </div>
                  </div>
                </section>
                <section v-if="!hasHermesContext" class="ys-material-section">
                  <div class="ys-material-empty">
                    暂未启用上下文。可从左侧切换工作区、选择收藏文件夹，或在知识库中启用 Skill 与数据库卡片。
                  </div>
                </section>
                <button v-if="newsHits.length" class="ys-chat-ref-chip" @click="toggleClue('news')">
                  新闻线索 <span class="ys-chat-ref-chip-num">{{ newsHits.length }}</span>
                </button>
                <button v-if="clusterHits.length" class="ys-chat-ref-chip" @click="toggleClue('cluster')">
                  事件聚类 <span class="ys-chat-ref-chip-num">{{ clusterHits.length }}</span>
                </button>
                <div v-if="expandedReplyDetail && panelDetailReply" class="ys-chat-detail-section">
                  <div class="ys-chat-detail-section-title">完整回复</div>
                  <div class="ys-chat-detail-content">{{ panelDetailReply }}</div>
                </div>
                </template>
              </div>
            </aside>
          </div>

          <div v-else-if="activeTopTab === 'briefing'" class="ys-briefing ys-briefing--scheduler">
            <section class="ys-schedule-shell">
              <header class="ys-schedule-head">
                <div>
                  <span class="ys-schedule-eyebrow">Hermes scheduled call</span>
                  <h1>定时报告配置</h1>
                  <p>保存主题与周期配置；实际执行依赖后台调度器健康状态。生成内容仅作为待人工审阅的 Markdown 草稿。</p>
                </div>
                <div class="ys-schedule-stats" aria-label="定时任务状态">
                  <div>
                    <span>任务</span>
                    <strong>{{ briefingStats.total }}</strong>
                  </div>
                  <div>
                    <span>启用</span>
                    <strong>{{ briefingStats.enabled }}</strong>
                  </div>
                  <div>
                    <span>产出</span>
                    <strong>{{ briefingStats.generated ?? '—' }}</strong>
                  </div>
                </div>
              </header>

              <div v-if="briefingError" class="ys-schedule-error">{{ briefingError }}</div>

              <div class="ys-schedule-layout">
                <section class="ys-schedule-editor">
                  <div class="ys-schedule-section-head">
                    <div>
                      <h2>{{ selectedBriefingId ? '编辑定时任务' : '新增定时任务' }}</h2>
                      <p>此处保存计划记录；后台是否按期执行须结合调度器健康状态和最近任务记录确认。</p>
                    </div>
                    <label class="ys-schedule-switch">
                      <input v-model="briefingScheduleForm.enabled" type="checkbox" />
                      <span>{{ briefingScheduleForm.enabled ? '启用' : '暂停' }}</span>
                    </label>
                  </div>

                  <div class="ys-schedule-grid">
                    <label class="ys-schedule-field">
                      <span>任务名称</span>
                      <input v-model="briefingScheduleForm.title" type="text" placeholder="如：拉美金融科技舆情日报" />
                    </label>
                    <label class="ys-schedule-field">
                      <span>报告主题</span>
                      <input v-model="briefingScheduleForm.topic" type="text" placeholder="如：Nequi 与哥伦比亚汇款市场动态" />
                    </label>
                    <label class="ys-schedule-field">
                      <span>周期</span>
                      <select v-model="briefingScheduleForm.cadence">
                        <option v-for="opt in briefingCadenceOptions" :key="opt.value" :value="opt.value">
                          {{ opt.label }}
                        </option>
                      </select>
                    </label>
                    <label v-if="briefingScheduleForm.cadence === 'weekly'" class="ys-schedule-field">
                      <span>周几</span>
                      <select v-model.number="briefingScheduleForm.day_of_week">
                        <option v-for="opt in briefingWeekdayOptions" :key="opt.value" :value="opt.value">
                          {{ opt.label }}
                        </option>
                      </select>
                    </label>
                    <label v-if="briefingScheduleForm.cadence === 'custom_hours'" class="ys-schedule-field">
                      <span>间隔小时</span>
                      <input v-model.number="briefingScheduleForm.interval_hours" type="number" min="1" max="720" />
                    </label>
                    <label v-if="['daily', 'weekly'].includes(briefingScheduleForm.cadence)" class="ys-schedule-field">
                      <span>执行时间</span>
                      <input v-model="briefingScheduleForm.time_of_day" type="time" />
                    </label>
                    <label class="ys-schedule-field">
                      <span>时间范围</span>
                      <select v-model="briefingScheduleForm.time_range">
                        <option v-for="opt in briefingTimeRangeOptions" :key="opt.value" :value="opt.value">
                          {{ opt.label }}
                        </option>
                      </select>
                    </label>
                    <label class="ys-schedule-field">
                      <span>分析视角</span>
                      <select v-model="briefingScheduleForm.perspective">
                        <option v-for="opt in briefingPerspectiveOptions" :key="opt.value" :value="opt.value">
                          {{ opt.label }}
                        </option>
                      </select>
                    </label>
                  </div>

                  <label class="ys-schedule-field ys-schedule-field--wide">
                    <span>Hermes 调用提示词</span>
                    <textarea
                      v-model="briefingScheduleForm.prompt"
                      rows="6"
                      placeholder="写清希望 Hermes 每次定时报告重点关注的对象、证据口径、输出结构和需要避免的内容。"
                    ></textarea>
                  </label>

                  <div class="ys-schedule-options">
                    <label>
                      <input v-model="briefingScheduleForm.include_sources" type="checkbox" />
                      <span>保留来源和核验线索</span>
                    </label>
                    <label>
                      <input v-model="briefingScheduleForm.include_charts" type="checkbox" />
                      <span>生成图表数据点</span>
                    </label>
                  </div>

                  <div class="ys-schedule-context">
                    <span>上下文</span>
                    <b>{{ pinnedFavoriteFolder ? `收藏：${displayFolderName(pinnedFavoriteFolder)}` : '未固定收藏' }}</b>
                    <b>{{ pinnedWorkspace ? `工作区：${pinnedWorkspace.name}` : '未固定工作区' }}</b>
                    <b>{{ knowledgeContextCount ? `${selectedHermesSkills.length}S · ${selectedHermesDatabases.length}DB` : '未选择 Skill/DB' }}</b>
                  </div>

                  <div class="ys-schedule-actions">
                    <button
                      type="button"
                      class="ys-schedule-primary"
                      :disabled="briefingSaving || !briefingScheduleForm.title.trim() || !briefingScheduleForm.topic.trim()"
                      @click="saveBriefingSchedule()"
                    >
                      {{ briefingSaving ? '保存中…' : '保存任务' }}
                    </button>
                    <button
                      type="button"
                      class="ys-schedule-secondary"
                      :disabled="briefingSaving || briefingRunningId || (!selectedBriefingId && (!briefingScheduleForm.title.trim() || !briefingScheduleForm.topic.trim()))"
                      @click="runBriefingSchedule()"
                    >
                      {{ briefingRunningId ? '生成中…' : '立即生成' }}
                    </button>
                  </div>
                </section>

                <aside class="ys-schedule-status-panel">
                  <div class="ys-schedule-status-head">
                    <span>任务状态</span>
                    <strong>{{ selectedBriefingSchedule ? briefingStatusLabel(selectedBriefingSchedule.last_status, selectedBriefingSchedule.last_assurance) : '未保存' }}</strong>
                  </div>
                  <dl class="ys-schedule-status-list">
                    <div>
                      <dt>下次计划记录</dt>
                      <dd>{{ briefingPlannedTimeLabel(selectedBriefingSchedule?.next_run_at, formatDateTime) }}</dd>
                    </div>
                    <div>
                      <dt>最近任务记录</dt>
                      <dd>{{ briefingRecordedTimeLabel(selectedBriefingSchedule?.last_run_at, formatDateTime) }}</dd>
                    </div>
                    <div>
                      <dt>报告位置</dt>
                      <dd>{{ selectedBriefingSchedule?.last_file?.file_path || '暂无报告' }}</dd>
                    </div>
                    <div>
                      <dt>可信边界</dt>
                      <dd :title="briefingAssuranceLabel(selectedBriefingSchedule)">
                        {{ briefingAssuranceLabel(selectedBriefingSchedule) }}
                      </dd>
                    </div>
                  </dl>
                  <div v-if="selectedBriefingSchedule?.last_status === 'failed'" class="ys-schedule-last-error">
                    最近一次运行失败；内部错误详情未公开
                  </div>
                  <div class="ys-schedule-side-actions">
                    <button
                      type="button"
                      :disabled="!selectedBriefingSchedule || briefingSaving"
                      @click="toggleBriefingSchedule(selectedBriefingSchedule)"
                    >
                      {{ selectedBriefingSchedule?.enabled ? '暂停任务' : '启用任务' }}
                    </button>
                    <button
                      type="button"
                      :disabled="!selectedBriefingSchedule || briefingRunningId"
                      @click="runBriefingSchedule(selectedBriefingSchedule)"
                    >
                      立即运行
                    </button>
                    <button
                      type="button"
                      class="danger"
                      :disabled="!selectedBriefingSchedule"
                      @click="deleteBriefingSchedule(selectedBriefingSchedule)"
                    >
                      删除
                    </button>
                  </div>
                  <div class="ys-schedule-runs">
                    <h3>最近任务记录</h3>
                    <div v-if="!selectedBriefingSchedule?.recent_runs?.length" class="ys-schedule-runs-empty">暂无任务记录</div>
                    <article v-for="run in selectedBriefingSchedule?.recent_runs || []" :key="run.id" class="ys-schedule-run">
                      <div>
                        <strong>{{ briefingRunStatusLabel(run.status) }}</strong>
                        <span>{{ formatDateTime(run.created_at) }}</span>
                      </div>
                      <p>{{ run.file?.file_path || run.error || '未生成文件' }}</p>
                    </article>
                  </div>
                </aside>
              </div>
            </section>
          </div>

          <!-- ====== 智能体报告 ====== -->
          <div v-else-if="activeTopTab === 'agentReport'" class="ys-agent-report">
            <div class="ar-layout">
              <!-- 左侧：配置面板 -->
              <div class="ar-panel">
                <div class="ar-panel-head">
                  <h2 class="ar-panel-title">报告配置</h2>
                  <p class="ar-panel-sub">只保留生成必填项，高级参数可展开调整</p>
                </div>

                <!-- 报告主题 -->
                <div class="ar-field">
                  <label class="ar-label">报告主题</label>
                  <input
                    v-model="reportConfig.topic"
                    class="ar-input"
                    placeholder="输入报告主题，如：美伊冲突最新动态与风险评估"
                    @keydown.enter="startReportGeneration"
                  />
                </div>

                <!-- 核心设置 -->
                <div class="ar-field">
                  <label class="ar-label">材料来源</label>
                  <div class="ar-segment">
                    <button
                      v-for="mode in reportInputModes"
                      :key="mode.key"
                      type="button"
                      :class="{ active: reportConfig.inputMode === mode.key }"
                      @click="reportConfig.inputMode = mode.key"
                    >
                      {{ mode.label }}
                    </button>
                  </div>
                </div>

                <div class="ar-field-row">
                  <div class="ar-field ar-field--half">
                    <label class="ar-label">报告类型</label>
                    <select v-model="reportConfig.type" class="ar-select">
                      <option v-for="rt in reportTypes" :key="rt.key" :value="rt.key">{{ rt.label }}</option>
                    </select>
                  </div>
                  <div class="ar-field ar-field--half">
                    <label class="ar-label">输出格式</label>
                    <select v-model="reportConfig.format" class="ar-select">
                      <option v-for="fmt in reportOutputFormats" :key="fmt.key" :value="fmt.key" :disabled="fmt.disabled">
                        {{ fmt.label }}{{ fmt.disabled ? '（待接入）' : '' }}
                      </option>
                    </select>
                  </div>
                </div>

                <div class="ar-type-note">
                  <strong>{{ reportTypes.find(t => t.key === reportConfig.type)?.desc }}</strong>
                  <span>{{ reportTypeProfile(reportConfig.type).focus }}</span>
                </div>

                <div v-if="reportConfig.format === 'pptx'" class="ar-ppt-note">
                  <strong>Word → ppt-master</strong>
                  <span>系统会先保存 Word 源稿，并生成 ppt-master 任务说明；PPTX 由 ppt-master 按确认流程继续生成。</span>
                </div>

                <div class="ar-field">
                  <label class="ar-label">{{ reportConfig.inputMode === 'brainstorm' ? '澄清记录' : reportConfig.inputMode === 'conversation' ? '会话补充' : '生成提示词' }}</label>
                  <div v-if="reportConfig.inputMode === 'conversation'" class="ar-context-meter">
                    <select class="ar-select" :value="convId(currentConversationId)" @change="selectReportConversation">
                      <option v-for="conv in conversationOptions" :key="conv.id" :value="conv.id">
                        {{ conv.label }}
                      </option>
                    </select>
                    <span>{{ activeConversationTitle }} · {{ messages.length }} 条消息将作为报告材料线索</span>
                  </div>
                  <textarea
                    v-if="reportConfig.inputMode === 'brainstorm'"
                    v-model="reportConfig.clarificationNotes"
                    class="ar-textarea"
                    rows="5"
                    placeholder="填写已澄清的用途、读者、范围、证据标准和必须回答的问题。"
                  />
                  <textarea
                    v-else
                    v-model="reportConfig.customPrompt"
                    class="ar-textarea"
                    rows="5"
                    :placeholder="reportConfig.inputMode === 'conversation' ? '可选：补充希望从当前会话中重点提取的事实、判断或输出约束。' : '输入提示词、调查问题、必须覆盖的事实、来源要求或输出边界。'"
                  />
                  <button
                    v-if="reportConfig.inputMode === 'brainstorm'"
                    type="button"
                    class="ar-secondary-btn"
                    :disabled="sending || reportGenerating"
                    @click="askReportClarifyingQuestions"
                  >
                    让数据助手提问澄清
                  </button>
                </div>

                <details class="ar-advanced">
                  <summary>高级设置</summary>
                  <div class="ar-advanced-body">
                    <div class="ar-field-row">
                      <div class="ar-field ar-field--half">
                        <label class="ar-label">时间范围</label>
                        <select v-model="reportConfig.timeRange" class="ar-select">
                          <option value="24h">最近 24 小时</option>
                          <option value="3d">最近 3 天</option>
                          <option value="7d">最近 7 天</option>
                          <option value="30d">最近 30 天</option>
                          <option value="custom">自定义</option>
                        </select>
                      </div>
                      <div class="ar-field ar-field--half">
                        <label class="ar-label">分析视角</label>
                        <select v-model="reportConfig.perspective" class="ar-select">
                          <option value="analyst">政策分析师</option>
                          <option value="researcher">科研人员</option>
                          <option value="journalist">媒体记者</option>
                          <option value="executive">管理决策者</option>
                        </select>
                      </div>
                    </div>
                    <div class="ar-field">
                      <label class="ar-label">输出模板</label>
                      <select v-model="reportConfig.template" class="ar-select">
                        <option v-for="tpl in reportTemplates" :key="tpl.key" :value="tpl.key">{{ tpl.label }} - {{ tpl.desc }}</option>
                      </select>
                    </div>
                    <div v-if="reportConfig.inputMode === 'brainstorm'" class="ar-question-list ar-question-list--compact">
                      <div v-for="(q, qi) in reportBriefingQuestions" :key="q" class="ar-question-row">
                        <span>{{ qi + 1 }}</span>
                        <p>{{ q }}</p>
                      </div>
                    </div>
                    <div class="ar-toggles ar-toggles--inline">
                      <label class="ar-toggle">
                        <input type="checkbox" v-model="reportConfig.includeCharts" />
                        <span>包含数据图表</span>
                      </label>
                      <label class="ar-toggle">
                        <input type="checkbox" v-model="reportConfig.includeSources" />
                        <span>引用溯源标注</span>
                      </label>
                    </div>
                  </div>
                </details>

                <!-- 生成按钮 -->
                <button
                  class="ar-generate-btn"
                  :disabled="!reportCanGenerate"
                  @click="startReportGeneration"
                >
                  <template v-if="reportGenerating">生成中 {{ reportProgress }}%…</template>
                  <template v-else>{{ reportConfig.format === 'pptx' ? '生成 PPT 任务包' : '生成报告' }}</template>
                </button>
                <div v-if="reportError" class="ar-report-error">{{ reportError }}</div>
                <div v-if="reportSavedPath" class="ar-report-saved">已保存到 {{ reportSavedPath }}</div>

                <!-- 生成进度 -->
                <div v-if="reportGenerating" class="ar-progress-wrap">
                  <div class="ar-progress-bar">
                    <div class="ar-progress-fill" :style="{ width: reportProgress + '%' }"></div>
                  </div>
                  <div class="ar-progress-steps">
                    <div class="ar-step" :class="{ done: reportActiveStep > 1, active: reportActiveStep === 1 }">
                      <span class="ar-step-dot"></span>检索知识库
                    </div>
                    <div class="ar-step" :class="{ done: reportActiveStep > 2, active: reportActiveStep === 2 }">
                      <span class="ar-step-dot"></span>联网检索
                    </div>
                    <div class="ar-step" :class="{ done: reportActiveStep > 3, active: reportActiveStep === 3 }">
                      <span class="ar-step-dot"></span>整合信息
                    </div>
                    <div class="ar-step" :class="{ done: reportActiveStep > 4, active: reportActiveStep === 4 }">
                      <span class="ar-step-dot"></span>撰写报告
                    </div>
                    <div class="ar-step" :class="{ done: reportActiveStep > 5, active: reportActiveStep === 5 }">
                      <span class="ar-step-dot"></span>校验格式
                    </div>
                  </div>
                  <button v-if="reportGenerating" class="ar-stop-btn" @click="stopReportGeneration">停止生成</button>
                </div>
              </div>

              <!-- 右侧：编辑器 + 历史 -->
              <div class="ar-main">
                <div class="ar-editor">
                  <div class="ar-editor-head">
                    <div class="ar-editor-title-wrap">
                      <span class="ar-preview-title">报告编辑器</span>
                      <span class="ar-editor-stats">{{ reportEditorStats.lines }} 行 · {{ reportEditorStats.chars }} 字符</span>
                    </div>
                    <div class="ar-editor-actions">
                      <button type="button" class="ar-editor-btn" :disabled="reportGenerating || reportSavingDraft || !reportPreview.trim()" @click="clearReportEditor">
                        新建空白
                      </button>
                      <button type="button" class="ar-editor-btn primary" :disabled="reportGenerating || reportSavingDraft || !reportPreview.trim()" @click="saveReportEditorContent">
                        {{ reportSavingDraft ? '保存中…' : reportConfig.format === 'pptx' ? '保存 PPT 任务包' : '保存正文' }}
                      </button>
                    </div>
                  </div>
                  <textarea
                    v-model="reportPreview"
                    class="ar-editor-textarea"
                    :readonly="reportGenerating"
                    placeholder="这里是可编辑的报告正文。你可以直接撰写，也可以先在左侧配置后生成，再继续修改、删改段落并保存到 report 工作区。"
                    @keydown.ctrl.s.prevent="saveReportEditorContent"
                    @keydown.meta.s.prevent="saveReportEditorContent"
                  />
                  <div class="ar-editor-footer">
                    <span v-if="reportSavedPath" class="ar-preview-meta">{{ reportSavedPath }}</span>
                    <span v-else-if="reportGenerating" class="ar-preview-meta">实时生成中</span>
                    <span v-else class="ar-editor-hint">未保存的正文只保留在当前页面</span>
                  </div>
                </div>

                <!-- 历史报告 -->
                <div class="ar-history" :class="{ collapsed: !reportHistoryOpen }">
                  <button type="button" class="ar-history-head" @click="reportHistoryOpen = !reportHistoryOpen">
                    <span class="ar-history-title">历史报告</span>
                    <span class="ar-history-count">{{ reportHistoryOpen ? '收起' : '展开' }} · {{ reportHistory.length }} 份</span>
                  </button>
                  <div v-if="reportHistoryOpen && reportHistory.length" class="ar-history-list">
                    <button
                      v-for="r in reportHistory"
                      :key="r.id"
                      class="ar-history-card"
                      @click="loadReportToPreview(r)"
                    >
                      <div class="ar-history-card-top">
                        <span class="ar-history-type">{{ reportTypes.find(t => t.key === r.type)?.label || r.type }}</span>
                        <span class="ar-history-format">{{ reportFormatLabel(r.format) }}</span>
                        <span class="ar-history-status" :class="{ done: r.status === 'done' }">{{ r.status === 'done' ? '已完成' : (r.status || '草稿') }}</span>
                      </div>
                      <div class="ar-history-card-title">{{ r.topic || r.title || '未命名主题' }}</div>
                      <div class="ar-history-card-time">
                        {{ formatDateTime(r.createdAt || r.time) }}
                        <span v-if="r.filePath"> · {{ r.filePath }}</span>
                      </div>
                    </button>
                  </div>
                  <div v-else-if="reportHistoryOpen" class="ar-history-empty">暂无历史报告</div>
                </div>
              </div>
            </div>
          </div>

          <div v-else class="ys-briefing" :class="{ 'ys-briefing--conversation': hasConversation }">
            <template v-if="!hasConversation">
            <div class="ys-hero">
              <h1 class="ys-hero-title">学者您好，欢迎来到<span style="color:rgba(102,126,234,1)">GlobeMind</span></h1>
              <p class="ys-hero-sub">
                从左侧选择一个会话继续对话，或发起新对话开始探索。
              </p>
            </div>
</template>
            <section class="ys-briefing-chat" :class="{ 'ys-briefing-chat--expanded': hasConversation }" aria-label="历史会话对话">
              <div ref="chatScrollBriefingEl" v-if="messages.length > 0" class="ys-briefing-chat-scroll" :class="{ 'ys-briefing-chat-scroll--expanded': hasConversation }">
                <div class="ys-briefing-chat-list">
                  <div v-for="(m, idx) in messages" :key="messageKey(m, idx)" class="ys-bmsg" :class="'ys-bmsg--' + m.role">
                    <details v-if="m.thinking" class="ys-bmsg-thinking" :open="streaming && idx === messages.length - 1">
                      <summary class="ys-bmsg-thinking-summary">思考过程</summary>
                      <div class="ys-bmsg-thinking-content">{{ m.thinking }}</div>
                    </details>
                    <div v-if="m.statusLine" class="ys-bmsg-tool-status">{{ m.statusLine }}</div>
                    <div v-if="m.text" class="ys-bmsg-bubble">{{ m.text }}</div>
                  </div>
                  <div v-if="sending && !hasStreamContent" class="ys-bmsg ys-bmsg--assistant">
                    <div class="ys-bmsg-bubble ys-bmsg-bubble--typing">思考中…</div>
                  </div>
                </div>
              </div>
              <div class="ys-briefing-composer" :class="{ 'ys-briefing-composer--expanded': hasConversation }">
                <div class="ys-composer-capsule">
                  <div class="ys-composer-input-area">
                    <textarea
                      ref="composerTextareaEl"
                      v-model="briefingDraft"
                      rows="1"
                      class="ys-briefing-input"
                      placeholder="输入消息…"
                      @keydown.enter.exact.prevent="sendBriefingMessage"
                      @input="autoResizeComposer"
                    />
                  </div>
                  <div class="ys-composer-action-bar">
                    <div class="ys-mode-toggle" @click.stop="showModeMenu = !showModeMenu">
                      <span class="ys-mode-label">{{ chatModeLabel() }}</span>
                      <svg class="ys-mode-arrow" width="10" height="10" viewBox="0 0 10 10"><path d="M2 3l3 4 3-4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
                      <div v-if="showModeMenu" class="ys-mode-dropdown" @click.stop>
                        <div class="ys-mode-dropdown-item" :class="{ active: chatMode === 'fast' }" @click="setChatMode('fast')">快速</div>
                        <div class="ys-mode-dropdown-item" :class="{ active: chatMode === 'pro' }" @click="setChatMode('pro')">研判</div>
                        <div class="ys-mode-dropdown-item" :class="{ active: chatMode === 'expert' }" @click="setChatMode('expert')">专家</div>
                      </div>
                    </div>
                    <button type="button" class="ys-briefing-send" :disabled="sending || !briefingDraft.trim()" @click="sendBriefingMessage">
                      发送
                    </button>
                  </div>
                </div>
              </div>
              <div v-if="!hasConversation" class="ys-mini-tags">
                <span class="ys-mini-tag" @click="selectSideNav('favorites')"><i class="ys-mini-tag-dot" style="background:rgba(0,181,255,0.18);color:#00B5FF">☆</i>收藏上下文</span>
                <span class="ys-mini-tag"><i class="ys-mini-tag-dot" style="background:rgba(0,47,167,0.12);color:#002FA7">◇</i>定期简报生成</span>
                <span class="ys-mini-tag" @click="selectSideNav('kb')"><i class="ys-mini-tag-dot" style="background:rgba(16,185,129,0.14);color:#10B981">◎</i>知识库连接</span>
              </div>
            </section>
          </div>
        </main>
      </div>
    </section>
    </template>
  </div>

  <!-- ====== 文件预览弹窗（笔记本风格，可读写） ====== -->
  <div v-if="filePreviewVisible" class="ws-preview-overlay" @click.self="closeFilePreview">
    <div class="ws-preview-modal">
      <div class="ws-preview-header">
        <span class="ws-preview-title">
          📄 {{ filePreviewTitle }}
          <span v-if="filePreviewContent !== fileOriginalContent" class="ws-preview-dirty">● 未保存</span>
        </span>
        <div class="ws-preview-header-actions">
          <button class="ws-preview-save-btn" :disabled="filePreviewContent === fileOriginalContent || fileSaving" @click="saveFileContent">
            {{ fileSaving ? '保存中…' : '💾 保存' }}
          </button>
          <button class="ws-preview-close" @click="closeFilePreview">✕</button>
        </div>
      </div>
      <div class="ws-preview-body ws-preview-body--editor">
        <div v-if="filePreviewLoading" class="ws-preview-loading">加载中…</div>
        <textarea v-else class="ws-preview-textarea" v-model="filePreviewContent" spellcheck="false"></textarea>
      </div>
    </div>
  </div>

  <!-- ====== 创建工作区弹窗 ====== -->
  <div v-if="createDialogVisible" class="ws-preview-overlay" @click.self="createDialogVisible = false">
    <div class="ws-create-modal">
      <div class="ws-preview-header">
        <span class="ws-preview-title">📁 创建工作区</span>
        <button class="ws-preview-close" @click="createDialogVisible = false">✕</button>
      </div>
      <div class="ws-create-body">
        <div class="ws-create-field">
          <label class="ws-create-label">工作区名称 <span class="ws-create-required">*</span></label>
          <input v-model="newWsName" class="ws-create-input" placeholder="输入工作区名称" maxlength="100" @keydown.enter="createWorkspace" />
        </div>
        <div class="ws-create-field">
          <label class="ws-create-label">描述</label>
          <textarea v-model="newWsDesc" class="ws-create-textarea" placeholder="可选：描述该工作区的用途" maxlength="500" rows="3"></textarea>
        </div>
        <div class="ws-create-actions">
          <button class="ws-create-cancel" @click="createDialogVisible = false">取消</button>
          <button class="ws-create-confirm" :disabled="!newWsName.trim() || creatingWs" @click="createWorkspace">
            {{ creatingWs ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ====== 新建文件弹窗 ====== -->
  <div v-if="createFileDialogVisible" class="ws-preview-overlay" @click.self="createFileDialogVisible = false">
    <div class="ws-create-modal ws-create-modal--sm">
      <div class="ws-preview-header">
        <span class="ws-preview-title">📄 新建文件</span>
        <button class="ws-preview-close" @click="createFileDialogVisible = false">✕</button>
      </div>
      <div class="ws-create-body">
        <div class="ws-create-field">
          <label class="ws-create-label">文件名 <span class="ws-create-required">*</span></label>
          <input v-model="newFileDialogName" class="ws-create-input ws-dialog-input" placeholder="例如 readme.md" maxlength="200" @keydown.enter="createWorkspaceFile" />
        </div>
        <div class="ws-create-actions">
          <button class="ws-create-cancel" @click="createFileDialogVisible = false">取消</button>
          <button class="ws-create-confirm" :disabled="!newFileDialogName.trim() || creatingFile" @click="createWorkspaceFile">
            {{ creatingFile ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- ====== 删除会话确认弹窗 ====== -->
  <div v-if="deleteDialog.visible" class="ys-delete-overlay" @click.self="closeDeleteDialog">
    <div class="ys-delete-modal">
      <div class="ys-delete-modal-icon">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
      </div>
      <div class="ys-delete-modal-title">删除会话</div>
      <div class="ys-delete-modal-body">
        确定删除「<strong>{{ deleteDialog.title }}</strong>」吗？
      </div>
      <div class="ys-delete-modal-hint">此操作将从数据库中彻底删除，不可恢复。</div>
      <div class="ys-delete-modal-actions">
        <button class="ys-delete-modal-cancel" @click="closeDeleteDialog">取消</button>
        <button class="ys-delete-modal-confirm" @click="confirmDelete">确定删除</button>
      </div>
    </div>
  </div>
</template>

<style src="./components/style.css" scoped></style>
