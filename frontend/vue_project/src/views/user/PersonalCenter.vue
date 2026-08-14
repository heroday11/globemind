<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/theme-chalk/el-message-box.css'
import { API_PREFIX } from '@/config/api'
import {
  formatRevokedSessionCount,
  normalizeIdentitySecuritySnapshot,
  securityFailureMessage,
} from '@/governance/identitySecurity'
import { clearAuth, getToken, setCurrentUser } from '@/utils/auth'
import DisplayPreferencesPanel from './DisplayPreferencesPanel.vue'

const router = useRouter()
const route = useRoute()
const settingsTabs = Object.freeze([
  { id: 'basic-info', label: '基本资料' },
  { id: 'password', label: '修改密码' },
  { id: 'security', label: '登录安全' },
  { id: 'api-config', label: 'API 配置' },
  { id: 'assistant-memory', label: '助手记忆' },
  { id: 'privacy', label: '隐私权利' },
  { id: 'display', label: '显示设置' },
])
const settingsTabIds = new Set(settingsTabs.map((tab) => tab.id))

function normalizeSettingsTab(value) {
  const tab = Array.isArray(value) ? value[0] : value
  return settingsTabIds.has(tab) ? tab : 'basic-info'
}
const loading = ref(false)
const savingProfile = ref(false)
const changingPwd = ref(false)
const securityLoading = ref(false)
const securityStatus = ref(null)
const securityCapabilities = ref(null)
const securitySessions = ref([])
const securityAudit = ref([])
const securityNotice = ref('')
const securityNoticeKind = ref('status')
const securityNoticeElement = ref(null)
const securityMfaLabel = computed(() => {
  if (securityStatus.value?.enabled === true) return '已启用'
  if (securityStatus.value?.enabled === false) return '未启用'
  return '状态未知'
})
const enrollment = ref(null)
const oneTimeRecoveryCodes = ref([])
const mfaConfirmCode = ref('')
const disableMode = ref('totp')
const disableForm = reactive({ password: '', code: '', recovery_code: '' })
let securityLoadGeneration = 0
const savingApi = ref(false)
const memoryLoading = ref(false)
const clearingMemory = ref(false)
const privacyLoading = ref(false)
const exportingPersonalData = ref(false)
const creatingDeletionRequest = ref(false)
const cancellingDeletionRequestId = ref('')
const deletionRequests = ref([])
const deletionPlanLoading = ref(false)
const deletionPlan = ref(null)
const deletionPlanError = ref('')
const deletionForm = reactive({ password: '', acknowledgement: '' })
const activeTab = ref(normalizeSettingsTab(route.query.tab))
const activeTabIndex = computed(() =>
  Math.max(
    0,
    settingsTabs.findIndex((tab) => tab.id === activeTab.value),
  ),
)
const deletionDispositionLabels = Object.freeze({
  delete: '计划删除',
  anonymize: '计划匿名化',
  retain: '计划保留',
  review_required: '需要复核',
  unavailable: '范围不可用',
})
const deletionPlanSummary = computed(() => {
  const summary = deletionPlan.value?.disposition_summary
  if (!summary || typeof summary !== 'object') return []
  return Object.keys(deletionDispositionLabels).map((disposition) => {
    const item = summary[disposition] || {}
    return {
      disposition,
      label: deletionDispositionLabels[disposition],
      scopes: Number(item.scope_count) || 0,
      records: Number(item.exact_record_count) || 0,
      unavailable: Number(item.unavailable_scope_count) || 0,
    }
  })
})
const deletionPlanItems = computed(() =>
  Array.isArray(deletionPlan.value?.impact_items) ? deletionPlan.value.impact_items : [],
)
const deletionPlanBlockers = computed(() =>
  Array.isArray(deletionPlan.value?.external_blockers) ? deletionPlan.value.external_blockers : [],
)
const formData = reactive({ full_name: '', email: '', phone: '' })
const profile = reactive({ username: '', full_name: '', email: '', phone: '', created_at: '' })
const passwordForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const assistantMemory = reactive({ memory_summary: '', updated_at: '', created_at: '' })
const apiConfig = reactive({
  active_provider: '',
  default_model: '',
  base_url: '',
})
const apiKeyStatus = reactive({})
const apiKeysToClear = ref([])
const advancedApiKeysDraft = ref('')
const apiDraft = reactive({
  api_key: '',
})
const imageApiDraft = reactive({
  backend: 'openai',
  api_key: '',
  base_url: '',
  model: 'gpt-image-2',
})
const showRawApiJson = ref(false)
const imageBackendOptions = [
  { value: 'openai', label: 'OpenAI-compatible / gpt-image-2' },
  { value: 'qwen', label: 'Qwen / DashScope' },
]
const imageBackendTemplates = {
  openai: {
    model: 'gpt-image-2',
    baseLabel: 'OpenAI-compatible API 地址（可选）',
    basePlaceholder: 'https://img-cn.65535.space/v1',
    help: '填写兼容 OpenAI Images API 的 /v1 地址；留空时使用平台公共 65535 图片账号。',
  },
  qwen: {
    model: 'qwen-image-plus',
    baseLabel: 'DashScope API 地址（可选）',
    basePlaceholder: 'https://your-workspace.cn-beijing.maas.aliyuncs.com/api/v1',
    help: '填 DashScope 原生地址，不要填 OpenAI 兼容地址；系统会自动补齐文生图 generation 路径。',
  },
}
const providerOptions = [
  { value: 'deepseek', label: 'DeepSeek', badge: '推荐' },
  { value: 'hermes', label: 'Hermes', badge: '助手' },
  { value: 'openai', label: 'OpenAI', badge: '兼容' },
  { value: 'anthropic', label: 'Anthropic', badge: '兼容' },
  { value: 'custom', label: '自定义', badge: '高级' },
]
const providerTemplates = {
  deepseek: {
    title: 'DeepSeek OpenAI 兼容模板',
    desc: '适合直接驱动数据助手，保存后下一次提问立即使用。',
    keyName: 'deepseek',
    keyLabel: 'DeepSeek API Key（可选）',
    keyPlaceholder: '留空使用公共 DeepSeek Flash',
    baseUrl: 'https://api.deepseek.com',
    anthropicBaseUrl: 'https://api.deepseek.com/anthropic',
    models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
    defaultModel: 'deepseek-v4-flash',
    basePlaceholder: 'https://api.deepseek.com',
    note: 'API Key 留空时使用公共账户 deepseek-v4-flash；填写个人 Key 后优先使用个人配置。',
    code: `client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[{"role": "user", "content": "Hello"}],
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)`,
  },
  hermes: {
    title: 'Hermes 网关模板',
    desc: '用于 GlobeMind 数据助手的 Hermes / OpenAI-compatible 网关。',
    keyName: 'hermes',
    keyLabel: 'Hermes API Key',
    keyPlaceholder: '输入 Hermes 网关密钥',
    baseUrl: '',
    models: [],
    defaultModel: '',
    basePlaceholder: 'https://your-hermes-gateway/v1',
    note: '请填写 Hermes 的 OpenAI-compatible /v1 地址和模型名。',
  },
  openai: {
    title: 'OpenAI 模板',
    desc: '使用标准 OpenAI Chat Completions 兼容接口。',
    keyName: 'openai',
    keyLabel: 'OpenAI API Key',
    keyPlaceholder: 'sk-...',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-4o', 'gpt-4.1', 'gpt-4.1-mini'],
    defaultModel: 'gpt-4o',
    basePlaceholder: 'https://api.openai.com/v1',
    note: '如果使用代理网关，可把端点替换为代理的 /v1 地址。',
  },
  anthropic: {
    title: 'Anthropic 兼容模板',
    desc: '适合兼容 Anthropic 格式的网关；若要用于当前数据助手，请确保网关也支持 OpenAI Chat Completions。',
    keyName: 'anthropic',
    keyLabel: 'Anthropic API Key',
    keyPlaceholder: 'sk-ant-...',
    baseUrl: 'https://api.anthropic.com',
    models: ['claude-sonnet-4-6', 'claude-sonnet-4-5'],
    defaultModel: 'claude-sonnet-4-6',
    basePlaceholder: 'https://api.anthropic.com',
    note: '当前数据助手主链路调用 OpenAI-compatible /chat/completions；纯 Anthropic endpoint 仅保存配置。',
  },
  custom: {
    title: '自定义 OpenAI-compatible 模板',
    desc: '用于公司内网、转发网关或其他兼容服务。',
    keyName: 'api_key',
    keyLabel: 'API Key',
    keyPlaceholder: '输入服务密钥',
    baseUrl: '',
    models: [],
    defaultModel: '',
    basePlaceholder: 'https://gateway.example.com/v1',
    note: '需要兼容 /v1/chat/completions。保存后数据助手下一轮请求会读取该配置。',
  },
}

function selectSettingsTab(tab) {
  const normalized = normalizeSettingsTab(tab)
  activeTab.value = normalized
  const query = { ...route.query }
  if (normalized === 'basic-info') delete query.tab
  else query.tab = normalized
  router.replace({ query })
}

watch(
  () => route.query.tab,
  (tab) => {
    activeTab.value = normalizeSettingsTab(tab)
  },
)

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${getToken()}`,
  }
}

function safeSecurityError(error, fallback) {
  return error?.code === 'IDENTITY_SECURITY_SAFE_ERROR' ? error.message : fallback
}

async function announceSecurity(message, kind = 'status') {
  securityNotice.value = message
  securityNoticeKind.value = kind
  await nextTick()
  securityNoticeElement.value?.focus()
}

async function securityRequest(path, { method = 'GET', body } = {}) {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method,
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(securityFailureMessage(payload))
    error.code = 'IDENTITY_SECURITY_SAFE_ERROR'
    throw error
  }
  return payload
}

async function loadSecurity() {
  const generation = ++securityLoadGeneration
  securityLoading.value = true
  securityStatus.value = null
  securityCapabilities.value = null
  securitySessions.value = []
  securityAudit.value = []
  try {
    const [status, sessions, audit] = await Promise.all([
      securityRequest('/user/security/mfa'),
      securityRequest('/user/security/sessions'),
      securityRequest('/user/security/audit'),
    ])
    if (generation !== securityLoadGeneration) return
    const snapshot = normalizeIdentitySecuritySnapshot(status, sessions, audit)
    if (snapshot.contractState !== 'contract_validated') {
      const error = new Error('身份安全状态契约不可用')
      error.code = 'IDENTITY_SECURITY_SAFE_ERROR'
      throw error
    }
    securityStatus.value = snapshot.status
    securityCapabilities.value = snapshot.capabilities
    securitySessions.value = snapshot.sessions
    securityAudit.value = snapshot.audit
  } catch (error) {
    if (generation !== securityLoadGeneration) return
    await announceSecurity(safeSecurityError(error, '身份安全状态加载失败'), 'error')
  } finally {
    if (generation === securityLoadGeneration) securityLoading.value = false
  }
}

async function beginMfaEnrollment() {
  securityLoading.value = true
  oneTimeRecoveryCodes.value = []
  try {
    enrollment.value = await securityRequest('/user/security/mfa/enroll', { method: 'POST' })
    await loadSecurity()
    await announceSecurity('已生成待确认的 TOTP 密钥。完成动态码确认前不会启用 MFA。')
  } catch (error) {
    await announceSecurity(safeSecurityError(error, '无法开始 MFA 设置'), 'error')
  } finally {
    securityLoading.value = false
  }
}

async function confirmMfaEnrollment() {
  securityLoading.value = true
  try {
    const result = await securityRequest('/user/security/mfa/confirm', {
      method: 'POST',
      body: { code: mfaConfirmCode.value },
    })
    oneTimeRecoveryCodes.value = Array.isArray(result.recovery_codes) ? result.recovery_codes : []
    enrollment.value = null
    mfaConfirmCode.value = ''
    await loadSecurity()
    await announceSecurity('MFA 已启用。请立即离线保存恢复码；离开后无法再次查看。')
  } catch (error) {
    await announceSecurity(safeSecurityError(error, '动态码确认失败'), 'error')
  } finally {
    securityLoading.value = false
  }
}

async function disableMfa() {
  securityLoading.value = true
  try {
    const body =
      disableMode.value === 'totp'
        ? { password: disableForm.password, code: disableForm.code }
        : {
            recovery_code: String(disableForm.recovery_code || '')
              .trim()
              .toUpperCase(),
          }
    await securityRequest('/user/security/mfa/disable', { method: 'POST', body })
    Object.assign(disableForm, { password: '', code: '', recovery_code: '' })
    oneTimeRecoveryCodes.value = []
    await loadSecurity()
    await announceSecurity('MFA 已停用。')
  } catch (error) {
    await announceSecurity(safeSecurityError(error, 'MFA 停用失败'), 'error')
  } finally {
    securityLoading.value = false
  }
}

async function revokeSession(session) {
  securityLoading.value = true
  try {
    const result = await securityRequest(
      `/user/security/sessions/${encodeURIComponent(session.sessionId)}/revoke`,
      { method: 'POST', body: { reason: 'user revoked session from personal center' } },
    )
    if (result.current_session_revoked) {
      clearAuth()
      await router.replace('/login')
      return
    }
    await loadSecurity()
    await announceSecurity('会话已撤销。')
  } catch (error) {
    await announceSecurity(safeSecurityError(error, '会话撤销失败'), 'error')
  } finally {
    securityLoading.value = false
  }
}

async function revokeOtherSessions() {
  securityLoading.value = true
  try {
    const result = await securityRequest('/user/security/sessions/revoke-others', {
      method: 'POST',
      body: { reason: 'user revoked other sessions from personal center' },
    })
    await loadSecurity()
    await announceSecurity(formatRevokedSessionCount(result.revoked_count))
  } catch (error) {
    await announceSecurity(safeSecurityError(error, '其他会话撤销失败'), 'error')
  } finally {
    securityLoading.value = false
  }
}

function getTemplate(provider = apiConfig.active_provider) {
  return providerTemplates[provider] || providerTemplates.custom
}

function parseApiKeys(raw) {
  if (!raw || !raw.trim()) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function syncImageApiDraftFromConfig(publicConfig = {}) {
  const keys = publicConfig && typeof publicConfig === 'object' ? publicConfig : {}
  const image =
    keys.image && typeof keys.image === 'object' && !Array.isArray(keys.image) ? keys.image : {}
  imageApiDraft.backend = image.backend || keys.image_backend || 'openai'
  const tpl = getImageBackendTemplate(imageApiDraft.backend)
  imageApiDraft.api_key = ''
  imageApiDraft.base_url =
    image.base_url ||
    image.openai_base_url ||
    image.qwen_base_url ||
    keys.image_base_url ||
    keys.image_openai_base_url ||
    keys.image_qwen_base_url ||
    ''
  imageApiDraft.model =
    image.model ||
    image.openai_model ||
    image.qwen_model ||
    keys.image_model ||
    keys.image_openai_model ||
    keys.image_qwen_model ||
    tpl.model
}

function replaceApiKeyStatus(status = {}) {
  Object.keys(apiKeyStatus).forEach((key) => delete apiKeyStatus[key])
  Object.entries(status || {}).forEach(([key, value]) => {
    if (value) apiKeyStatus[key] = true
  })
}

function hasStoredApiKey(path) {
  return Boolean(apiKeyStatus[path]) && !apiKeysToClear.value.includes(path)
}

function clearStoredApiKey(path) {
  if (!apiKeysToClear.value.includes(path)) apiKeysToClear.value.push(path)
  if (path === getTemplate().keyName) apiDraft.api_key = ''
  if (path === 'image.api_key') imageApiDraft.api_key = ''
}

function keepStoredApiKey(path) {
  apiKeysToClear.value = apiKeysToClear.value.filter((item) => item !== path)
}

function getImageBackendTemplate(backend = imageApiDraft.backend) {
  return imageBackendTemplates[backend] || imageBackendTemplates.openai
}

function handleImageBackendChange(backend) {
  const tpl = getImageBackendTemplate(backend)
  imageApiDraft.model = tpl.model
  imageApiDraft.base_url = ''
}

function formatMemoryTime(value) {
  if (!value) return '暂无更新'
  const text = String(value)
  return text
    .replace('T', ' ')
    .replace(/\.\d+/, '')
    .replace(/\+00:00$/, '')
    .slice(0, 19)
}

function applyProviderTemplate(provider, { preserveKey = true, force = false } = {}) {
  const previousProvider = apiConfig.active_provider
  const previousKey = apiDraft.api_key
  apiConfig.active_provider = provider
  const tpl = getTemplate(provider)
  if (!preserveKey || previousProvider !== provider) apiDraft.api_key = ''
  if (force || !apiConfig.base_url) apiConfig.base_url = tpl.baseUrl || ''
  if (force || !apiConfig.default_model) apiConfig.default_model = tpl.defaultModel || ''
  if (preserveKey && previousProvider === provider && previousKey) {
    apiDraft.api_key = previousKey
  }
}

function handleProviderChange(provider) {
  applyProviderTemplate(provider, { preserveKey: true, force: true })
}

function buildApiKeysPayload() {
  const tpl = getTemplate()
  const keys = parseApiKeys(advancedApiKeysDraft.value)
  const cleanKey = (apiDraft.api_key || '').trim()
  if (cleanKey) {
    keys[tpl.keyName] = cleanKey
    keepStoredApiKey(tpl.keyName)
  }
  const backend = imageApiDraft.backend || 'openai'
  const image =
    keys.image && typeof keys.image === 'object' && !Array.isArray(keys.image) ? keys.image : {}
  keys.image = {
    ...image,
    backend,
    base_url: (imageApiDraft.base_url || '').trim(),
    model: (imageApiDraft.model || getImageBackendTemplate(backend).model).trim(),
  }
  const imageKey = (imageApiDraft.api_key || '').trim()
  if (imageKey) {
    keys.image.api_key = imageKey
    keepStoredApiKey('image.api_key')
  }
  return JSON.stringify(keys, null, 2)
}

async function loadProfile() {
  loading.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/profile`, { headers: authHeaders() })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || '获取个人资料失败')
    }
    profile.username = data.username || ''
    profile.full_name = data.full_name || ''
    profile.email = data.email || ''
    profile.phone = data.phone || ''
    profile.created_at = data.created_at ? data.created_at.replace('T', ' ').slice(0, 19) : ''
    formData.full_name = profile.full_name
    formData.email = profile.email
    formData.phone = profile.phone
    // API 配置
    apiConfig.active_provider = data.active_provider || 'deepseek'
    apiConfig.default_model = data.default_model || ''
    apiConfig.base_url = data.base_url || ''
    if (!apiConfig.default_model || !apiConfig.base_url) {
      applyProviderTemplate(apiConfig.active_provider, { preserveKey: true, force: false })
    }
    apiDraft.api_key = ''
    advancedApiKeysDraft.value = ''
    apiKeysToClear.value = []
    replaceApiKeyStatus(data.api_key_status)
    syncImageApiDraftFromConfig(data.api_config_public)
    setCurrentUser(data)
  } catch (e) {
    ElMessage.error(e.message || '获取个人资料失败')
    if ((e.message || '').includes('未登录')) {
      clearAuth()
      router.replace('/login')
    }
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  savingProfile.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/profile`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify(formData),
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || '保存失败')
    }
    ElMessage.success('资料更新成功')
    await loadProfile()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingProfile.value = false
  }
}

async function handlePasswordSubmit() {
  changingPwd.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/change-password`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(passwordForm),
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || '修改密码失败')
    }
    ElMessage.success('密码修改成功')
    passwordForm.old_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    clearAuth()
    await router.replace('/login')
  } catch (e) {
    ElMessage.error(e.message || '修改密码失败')
  } finally {
    changingPwd.value = false
  }
}

async function handleSaveApi() {
  savingApi.value = true
  try {
    const normalizedKeys = buildApiKeysPayload()
    const res = await fetch(`${API_PREFIX}/user/api-config`, {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({
        api_keys: normalizedKeys,
        clear_api_keys: apiKeysToClear.value,
        active_provider: apiConfig.active_provider,
        default_model: apiConfig.default_model,
        base_url: apiConfig.base_url,
      }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || '保存失败')
    apiDraft.api_key = ''
    imageApiDraft.api_key = ''
    advancedApiKeysDraft.value = ''
    apiKeysToClear.value = []
    replaceApiKeyStatus(data.api_key_status)
    syncImageApiDraftFromConfig(data.api_config_public)
    setCurrentUser(data)
    ElMessage.success('API 配置已保存，数据助手下一次提问会立即使用')
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    savingApi.value = false
  }
}

async function loadAssistantMemory() {
  memoryLoading.value = true
  try {
    const res = await fetch(`${API_PREFIX}/assistant/memory`, { headers: authHeaders() })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || '获取助手记忆失败')
    }
    assistantMemory.memory_summary = data.memory_summary || ''
    assistantMemory.updated_at = data.updated_at || ''
    assistantMemory.created_at = data.created_at || ''
  } catch (e) {
    ElMessage.error(e.message || '获取助手记忆失败')
  } finally {
    memoryLoading.value = false
  }
}

async function clearAssistantMemory() {
  try {
    await ElMessageBox.confirm(
      '清空后，Hermes 将不再保留对你长期偏好和背景的摘要。此操作不会删除历史会话。',
      '清空助手记忆',
      {
        confirmButtonText: '清空记忆',
        cancelButtonText: '取消',
        type: 'warning',
        appendTo: 'body',
        lockScroll: true,
      },
    )
  } catch {
    return
  }

  clearingMemory.value = true
  try {
    const res = await fetch(`${API_PREFIX}/assistant/memory`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || '清空助手记忆失败')
    }
    assistantMemory.memory_summary = ''
    assistantMemory.updated_at = data.updated_at || ''
    assistantMemory.created_at = ''
    ElMessage.success('助手记忆已清空')
  } catch (e) {
    ElMessage.error(e.message || '清空助手记忆失败')
  } finally {
    clearingMemory.value = false
  }
}

async function loadDeletionRequests() {
  privacyLoading.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/privacy/deletion-requests`, {
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '获取删除申请失败')
    deletionRequests.value = Array.isArray(data.items) ? data.items : []
  } catch (e) {
    ElMessage.error(e.message || '获取删除申请失败')
  } finally {
    privacyLoading.value = false
  }
}

async function loadDeletionImpactPlan() {
  deletionPlanLoading.value = true
  deletionPlanError.value = ''
  try {
    const res = await fetch(`${API_PREFIX}/user/privacy/deletion-impact-plan`, {
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '账号删除影响预检失败')
    if (
      data.schema_version !== 'account-deletion-impact-plan-v1' ||
      data.deletion_performed !== false ||
      data.execution_state !== 'blocked' ||
      data.operation_mode !== 'read_only_preflight'
    ) {
      throw new Error('账号删除影响预检返回了不安全的执行状态')
    }
    deletionPlan.value = data
  } catch (e) {
    deletionPlan.value = null
    deletionPlanError.value = e.message || '账号删除影响预检失败'
  } finally {
    deletionPlanLoading.value = false
  }
}

async function loadPrivacyOverview() {
  await Promise.all([loadDeletionRequests(), loadDeletionImpactPlan()])
}

async function exportPersonalData() {
  exportingPersonalData.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/privacy/export`, {
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '个人数据导出失败')
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `globemind-personal-data-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    if (data.complete) {
      ElMessage.success('个人数据导出已下载')
    } else {
      ElMessage.warning('已下载当前可安全导出的数据；文件内列出了未导出或截断的范围')
    }
  } catch (e) {
    ElMessage.error(e.message || '个人数据导出失败')
  } finally {
    exportingPersonalData.value = false
  }
}

async function createDeletionRequest() {
  if (deletionForm.acknowledgement !== 'REQUEST ACCOUNT DELETION') {
    ElMessage.warning('请输入完整确认短语')
    return
  }
  try {
    await ElMessageBox.confirm(
      '此步骤只登记删除申请，不会立即删除账号。工作区、定时任务、研究项目和法定保留范围完成核验前，申请会保持待人工执行。',
      '登记账号删除申请',
      {
        confirmButtonText: '登记申请',
        cancelButtonText: '取消',
        type: 'warning',
        appendTo: 'body',
      },
    )
  } catch {
    return
  }
  creatingDeletionRequest.value = true
  try {
    const res = await fetch(`${API_PREFIX}/user/privacy/deletion-requests`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(deletionForm),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '删除申请登记失败')
    deletionForm.password = ''
    deletionForm.acknowledgement = ''
    await loadDeletionRequests()
    ElMessage.success('删除申请已登记；当前尚未执行删除')
  } catch (e) {
    ElMessage.error(e.message || '删除申请登记失败')
  } finally {
    deletionForm.password = ''
    creatingDeletionRequest.value = false
  }
}

async function cancelDeletionRequest(requestId) {
  try {
    await ElMessageBox.confirm('撤销后，该申请不会进入后续人工执行流程。', '撤销删除申请', {
      confirmButtonText: '撤销申请',
      cancelButtonText: '保留申请',
      type: 'warning',
      appendTo: 'body',
    })
  } catch {
    return
  }
  cancellingDeletionRequestId.value = requestId
  try {
    const res = await fetch(
      `${API_PREFIX}/user/privacy/deletion-requests/${encodeURIComponent(requestId)}/cancel`,
      { method: 'POST', headers: authHeaders() },
    )
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '撤销删除申请失败')
    await loadDeletionRequests()
    ElMessage.success('删除申请已撤销')
  } catch (e) {
    ElMessage.error(e.message || '撤销删除申请失败')
  } finally {
    cancellingDeletionRequestId.value = ''
  }
}

watch(activeTab, (tab) => {
  if (tab === 'privacy') loadPrivacyOverview()
  if (tab === 'security') loadSecurity()
})

onMounted(async () => {
  await loadProfile()
  await loadAssistantMemory()
  if (activeTab.value === 'privacy') await loadPrivacyOverview()
  if (activeTab.value === 'security') await loadSecurity()
})

onBeforeUnmount(() => {
  ElMessageBox.close()
})
</script>

<template>
  <div class="pc-root" data-tour="account-settings">
    <!-- Header -->
    <header class="pc-header">
      <div class="pc-header-body">
        <div class="pc-header-overline">ACCOUNT SETTINGS</div>
        <h1 class="pc-header-title">个人中心</h1>
        <p class="pc-header-desc">管理账号、安全、模型与全站显示偏好</p>
      </div>
      <div class="pc-header-visual" aria-hidden="true">
        <div class="pc-header-ring pc-header-ring--1"></div>
        <div class="pc-header-ring pc-header-ring--2"></div>
        <div class="pc-header-dot"></div>
      </div>
    </header>

    <!-- Two-column layout -->
    <div class="pc-layout">
      <!-- LEFT: Identity Card -->
      <aside class="pc-card pc-card--identity">
        <!-- Subtle grain overlay -->
        <div class="pc-card-grain" aria-hidden="true"></div>

        <!-- Avatar section -->
        <div class="pc-avatar-block">
          <div class="pc-avatar-outer">
            <div class="pc-avatar-inner">
              <span class="pc-avatar-init">
                {{ (profile.full_name || profile.username || '?').charAt(0).toUpperCase() }}
              </span>
            </div>
            <svg class="pc-avatar-progress" viewBox="0 0 100 100" aria-hidden="true">
              <circle
                cx="50"
                cy="50"
                r="46"
                fill="none"
                stroke="var(--pc-border-light)"
                stroke-width="3"
              />
              <circle
                cx="50"
                cy="50"
                r="46"
                fill="none"
                stroke="var(--uc-accent)"
                stroke-width="3"
                stroke-linecap="round"
                stroke-dasharray="289"
                stroke-dashoffset="72"
                transform="rotate(-90 50 50)"
                class="pc-avatar-arc"
              />
            </svg>
          </div>
          <div class="pc-avatar-info">
            <span class="pc-avatar-name">{{
              profile.full_name || profile.username || '未设置'
            }}</span>
            <span class="pc-avatar-handle">@{{ profile.username || '...' }}</span>
          </div>
          <div class="pc-badge">
            <span class="pc-badge-dot"></span>
            <span class="pc-badge-text">Active Account</span>
          </div>
        </div>

        <hr class="pc-divider" />

        <!-- Detail rows -->
        <dl class="pc-meta">
          <div class="pc-meta-row">
            <dt class="pc-meta-key">
              <span class="pc-meta-icon pc-meta-icon--email" aria-hidden="true"></span>
              邮箱
            </dt>
            <dd class="pc-meta-val">{{ profile.email || '—' }}</dd>
          </div>
          <div class="pc-meta-row">
            <dt class="pc-meta-key">
              <span class="pc-meta-icon pc-meta-icon--phone" aria-hidden="true"></span>
              手机
            </dt>
            <dd class="pc-meta-val">{{ profile.phone || '—' }}</dd>
          </div>
          <div class="pc-meta-row">
            <dt class="pc-meta-key">
              <span class="pc-meta-icon pc-meta-icon--date" aria-hidden="true"></span>
              注册时间
            </dt>
            <dd class="pc-meta-val pc-meta-val--date">{{ profile.created_at || '—' }}</dd>
          </div>
        </dl>

        <!-- Decorative bottom accent -->
        <div class="pc-card-accent" aria-hidden="true"></div>
      </aside>

      <!-- RIGHT: Settings Card -->
      <section class="pc-card pc-card--settings">
        <!-- Tab bar -->
        <nav
          class="pc-tabs"
          data-tour="account-preferences"
          :data-tab="activeTab"
          :style="{ '--pc-tab-index': activeTabIndex, '--pc-tab-count': settingsTabs.length }"
          aria-label="个人设置分类"
        >
          <button
            v-for="(tab, index) in settingsTabs"
            :key="tab.id"
            type="button"
            class="pc-tab"
            :class="{ 'is-active': activeTab === tab.id }"
            :aria-current="activeTab === tab.id ? 'page' : undefined"
            @click="selectSettingsTab(tab.id)"
          >
            <span class="pc-tab-num">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="pc-tab-label">{{ tab.label }}</span>
          </button>
          <div class="pc-tab-track">
            <div class="pc-tab-thumb"></div>
          </div>
        </nav>

        <!-- Form area -->
        <div class="pc-form-stage" v-loading="loading">
          <!-- Basic Info Form -->
          <el-form
            v-if="activeTab === 'basic-info'"
            :model="formData"
            label-width="0"
            class="pc-form"
          >
            <div class="pc-field">
              <label class="pc-field-label" for="profile-full-name">姓名（可选）</label>
              <el-input
                id="profile-full-name"
                v-model="formData.full_name"
                placeholder="可留空或清除"
                class="pc-input"
              />
            </div>
            <div class="pc-field">
              <label class="pc-field-label" for="profile-email">邮箱地址</label>
              <el-input
                id="profile-email"
                v-model="formData.email"
                placeholder="name@example.com"
                class="pc-input"
              />
            </div>
            <div class="pc-field">
              <label class="pc-field-label" for="profile-phone">手机号码（可选）</label>
              <el-input
                id="profile-phone"
                v-model="formData.phone"
                placeholder="可留空或清除"
                class="pc-input"
              />
            </div>
            <div class="pc-actions">
              <el-button type="primary" :loading="savingProfile" @click="handleSave" class="pc-btn">
                <span class="pc-btn-text">保存更改</span>
                <span class="pc-btn-shimmer" aria-hidden="true"></span>
              </el-button>
              <span class="pc-actions-hint">修改后点击保存即可生效</span>
            </div>
          </el-form>

          <!-- Password Form -->
          <el-form
            v-if="activeTab === 'password'"
            :model="passwordForm"
            label-width="0"
            class="pc-form"
          >
            <div class="pc-field">
              <label class="pc-field-label">当前密码</label>
              <el-input
                v-model="passwordForm.old_password"
                type="password"
                show-password
                placeholder="输入当前密码"
                class="pc-input"
              />
            </div>
            <div class="pc-field">
              <label class="pc-field-label">新密码</label>
              <el-input
                v-model="passwordForm.new_password"
                type="password"
                show-password
                placeholder="至少 8 位，包含字母与数字"
                class="pc-input"
              />
            </div>
            <div class="pc-field">
              <label class="pc-field-label">确认新密码</label>
              <el-input
                v-model="passwordForm.confirm_password"
                type="password"
                show-password
                placeholder="再次输入新密码"
                class="pc-input"
              />
            </div>
            <div class="pc-actions">
              <el-button
                type="primary"
                :loading="changingPwd"
                @click="handlePasswordSubmit"
                class="pc-btn"
              >
                <span class="pc-btn-text">更新密码</span>
                <span class="pc-btn-shimmer" aria-hidden="true"></span>
              </el-button>
              <span class="pc-actions-hint">建议使用您未在其他平台使用的密码</span>
            </div>
          </el-form>

          <section
            v-if="activeTab === 'security'"
            class="pc-form pc-security-panel"
            :aria-busy="securityLoading"
          >
            <div class="pc-security-heading">
              <div>
                <span class="pc-api-kicker">ACCOUNT ASSURANCE</span>
                <h2>登录安全</h2>
              </div>
              <strong
                :class="
                  securityStatus?.enabled === true
                    ? 'is-enabled'
                    : securityStatus?.enabled === false
                      ? 'is-disabled'
                      : 'is-unknown'
                "
              >
                MFA {{ securityMfaLabel }}
              </strong>
            </div>
            <p class="pc-security-boundary">
              当前为基础 RFC 6238 TOTP 与可撤销 Web 会话；不代表机构 SSO、设备认证或独立安全验收。
            </p>
            <ul v-if="securityCapabilities" class="pc-security-capabilities">
              <li>机构 SSO：未配置</li>
              <li>安全密钥：未配置</li>
              <li>受信设备：未配置</li>
              <li>运行 IdP 证明：不可用</li>
              <li>独立安全复核：未提供</li>
            </ul>
            <p v-else class="pc-security-boundary">能力状态契约尚不可用。</p>
            <p
              v-if="securityNotice"
              ref="securityNoticeElement"
              class="pc-security-notice"
              :class="{ 'is-error': securityNoticeKind === 'error' }"
              :role="securityNoticeKind === 'error' ? 'alert' : 'status'"
              aria-live="polite"
              tabindex="-1"
            >
              {{ securityNotice }}
            </p>

            <section class="pc-security-card" aria-labelledby="mfa-settings-title">
              <h3 id="mfa-settings-title">双因素认证</h3>
              <template v-if="securityStatus?.enabled === false">
                <p>
                  启用后，密码验证成功只会生成短期、单次的登录挑战；完成动态码或恢复码验证后才签发访问令牌。
                </p>
                <button
                  type="button"
                  class="pc-security-button"
                  :disabled="securityLoading || securityStatus?.pendingEnrollment"
                  @click="beginMfaEnrollment"
                >
                  {{
                    securityStatus?.pendingEnrollment
                      ? `待确认设置已存在（剩余 ${securityStatus.pendingAttemptsRemaining} 次）`
                      : '开始设置 MFA'
                  }}
                </button>
              </template>
              <template v-else-if="securityStatus?.enabled === true">
                <p>剩余未使用恢复码：{{ securityStatus.recoveryCodesRemaining }}</p>
                <fieldset class="pc-security-methods">
                  <legend>停用验证方式</legend>
                  <label
                    ><input v-model="disableMode" type="radio" value="totp" />密码 + 动态码</label
                  >
                  <label
                    ><input v-model="disableMode" type="radio" value="recovery" />单枚恢复码</label
                  >
                </fieldset>
                <div v-if="disableMode === 'totp'" class="pc-security-fields">
                  <label for="mfa-disable-password">当前密码</label>
                  <input
                    id="mfa-disable-password"
                    v-model="disableForm.password"
                    type="password"
                    autocomplete="current-password"
                  />
                  <label for="mfa-disable-code">6 位动态码</label>
                  <input
                    id="mfa-disable-code"
                    v-model="disableForm.code"
                    inputmode="numeric"
                    autocomplete="one-time-code"
                    pattern="[0-9]{6}"
                  />
                </div>
                <div v-else class="pc-security-fields">
                  <label for="mfa-disable-recovery">恢复码</label>
                  <input
                    id="mfa-disable-recovery"
                    v-model="disableForm.recovery_code"
                    autocomplete="off"
                    pattern="[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}"
                  />
                </div>
                <button
                  type="button"
                  class="pc-security-button is-danger"
                  :disabled="securityLoading"
                  @click="disableMfa"
                >
                  停用 MFA
                </button>
              </template>
              <p v-else>状态契约不可用时不会开放 MFA 变更操作。</p>

              <div
                v-if="enrollment"
                class="pc-enrollment"
                role="region"
                aria-label="待确认 MFA 密钥"
              >
                <p>请将以下一次性密钥或 otpauth URI 添加到验证器。服务端仅保存 Fernet 加密密文。</p>
                <strong>一次性密钥</strong>
                <code>{{ enrollment.secret }}</code>
                <strong>otpauth URI</strong>
                <code>{{ enrollment.otpauth_uri }}</code>
                <label for="mfa-confirm-code">验证器生成的 6 位动态码</label>
                <input
                  id="mfa-confirm-code"
                  v-model="mfaConfirmCode"
                  inputmode="numeric"
                  autocomplete="one-time-code"
                  pattern="[0-9]{6}"
                />
                <button
                  type="button"
                  class="pc-security-button"
                  :disabled="securityLoading"
                  @click="confirmMfaEnrollment"
                >
                  确认并启用
                </button>
              </div>

              <div v-if="oneTimeRecoveryCodes.length" class="pc-recovery-codes" role="alert">
                <h4>恢复码仅显示这一次</h4>
                <p>请立即离线保存。页面关闭或刷新后无法重新读取这些明文恢复码。</p>
                <ul>
                  <li v-for="code in oneTimeRecoveryCodes" :key="code">
                    <code>{{ code }}</code>
                  </li>
                </ul>
              </div>
            </section>

            <section class="pc-security-card" aria-labelledby="session-settings-title">
              <div class="pc-security-card-heading">
                <h3 id="session-settings-title">可撤销会话</h3>
                <button
                  type="button"
                  class="pc-security-button"
                  :disabled="securityLoading"
                  @click="revokeOtherSessions"
                >
                  撤销其他活动会话
                </button>
              </div>
              <p>
                仅列出由真实登录流程签发并记录的 tracked session；旧 untracked token
                不冒充为可撤销会话。
              </p>
              <ul class="pc-session-list">
                <li v-for="session in securitySessions" :key="session.sessionId">
                  <div>
                    <strong
                      >{{ session.current ? '当前会话' : '其他会话' }} ·
                      {{ session.status }}</strong
                    >
                    <span>{{ session.issuedAt }} → {{ session.expiresAt }}</span>
                    <small>last_seen：{{ session.lastSeenStatus }}</small>
                  </div>
                  <button
                    v-if="session.status === 'active'"
                    type="button"
                    class="pc-security-button is-danger"
                    :disabled="securityLoading"
                    @click="revokeSession(session)"
                  >
                    撤销
                  </button>
                </li>
              </ul>
            </section>

            <details class="pc-security-card">
              <summary>脱敏安全审计（最近 {{ securityAudit.length }} 条）</summary>
              <ul class="pc-audit-list">
                <li v-for="event in securityAudit" :key="event.eventId">
                  <strong>{{ event.action }}</strong>
                  <span>{{ event.timestamp }} · version {{ event.sequence }}</span>
                  <small>reason SHA-256：{{ event.reasonSha256 }}</small>
                </li>
              </ul>
            </details>
          </section>

          <!-- API Config Form -->
          <el-form
            v-if="activeTab === 'api-config'"
            :model="apiConfig"
            label-width="0"
            class="pc-form pc-api-form"
          >
            <div class="pc-api-head">
              <div>
                <span class="pc-api-kicker">AI ROUTING</span>
                <h2 class="pc-api-title">数据助手 API</h2>
              </div>
              <span class="pc-api-status">保存后立即用于下一次对话</span>
            </div>

            <div class="pc-provider-grid">
              <button
                v-for="opt in providerOptions"
                :key="opt.value"
                type="button"
                class="pc-provider-card"
                :class="{ 'is-selected': apiConfig.active_provider === opt.value }"
                @click="handleProviderChange(opt.value)"
              >
                <span class="pc-provider-name">{{ opt.label }}</span>
                <span class="pc-provider-badge">{{ opt.badge }}</span>
              </button>
            </div>

            <section class="pc-api-template">
              <div class="pc-api-template-main">
                <div class="pc-api-template-title-row">
                  <h3 class="pc-api-template-title">{{ getTemplate().title }}</h3>
                  <button
                    type="button"
                    class="pc-template-apply"
                    @click="
                      applyProviderTemplate(apiConfig.active_provider, {
                        preserveKey: true,
                        force: true,
                      })
                    "
                  >
                    应用模板
                  </button>
                </div>
                <p class="pc-api-template-desc">{{ getTemplate().desc }}</p>
                <div class="pc-api-note">{{ getTemplate().note }}</div>
              </div>
              <div v-if="apiConfig.active_provider === 'deepseek'" class="pc-endpoint-table">
                <div class="pc-endpoint-row">
                  <span>base_url (OpenAI)</span>
                  <code>https://api.deepseek.com</code>
                </div>
                <div class="pc-endpoint-row">
                  <span>base_url (Anthropic)</span>
                  <code>https://api.deepseek.com/anthropic</code>
                </div>
              </div>
            </section>

            <div class="pc-field">
              <label class="pc-field-label">{{ getTemplate().keyLabel }}</label>
              <el-input
                v-model="apiDraft.api_key"
                type="password"
                show-password
                :placeholder="getTemplate().keyPlaceholder"
                class="pc-input"
                autocomplete="off"
              />
              <span v-if="hasStoredApiKey(getTemplate().keyName)" class="pc-field-help">
                已保存密钥，出于安全不会回显。留空会继续保留。
                <el-button link type="danger" @click="clearStoredApiKey(getTemplate().keyName)"
                  >移除</el-button
                >
              </span>
              <span
                v-else-if="apiKeysToClear.includes(getTemplate().keyName)"
                class="pc-field-help"
              >
                保存后将移除该密钥。
                <el-button link @click="keepStoredApiKey(getTemplate().keyName)">撤销</el-button>
              </span>
            </div>

            <div class="pc-api-row">
              <div class="pc-field">
                <label class="pc-field-label">默认模型</label>
                <el-select
                  v-if="getTemplate().models.length"
                  v-model="apiConfig.default_model"
                  placeholder="选择模型"
                  class="pc-input"
                  filterable
                  allow-create
                >
                  <el-option
                    v-for="model in getTemplate().models"
                    :key="model"
                    :label="model"
                    :value="model"
                  />
                </el-select>
                <el-input
                  v-else
                  v-model="apiConfig.default_model"
                  placeholder="输入模型名"
                  class="pc-input"
                />
              </div>

              <div class="pc-field">
                <label class="pc-field-label">API 端点地址</label>
                <el-input
                  v-model="apiConfig.base_url"
                  :placeholder="getTemplate().basePlaceholder"
                  class="pc-input"
                />
              </div>
            </div>

            <div v-if="apiConfig.active_provider === 'deepseek'" class="pc-code-sample">
              <div class="pc-code-title">DeepSeek OpenAI SDK 示例</div>
              <pre><code>{{ getTemplate().code }}</code></pre>
            </div>

            <section class="pc-image-api-panel">
              <div class="pc-image-api-head">
                <div>
                  <span class="pc-api-kicker">IMAGE TOOL</span>
                  <h3 class="pc-image-api-title">图片生成 API</h3>
                </div>
                <span class="pc-image-api-mode">公共账号优先，个人配置兜底</span>
              </div>
              <p class="pc-image-api-desc">
                留空时仅使用平台公共图片账号；填写个人 Key
                后，公共账号额度不足、失效或限流时会自动重试个人配置。
              </p>

              <div class="pc-api-row">
                <div class="pc-field">
                  <label class="pc-field-label">图片服务</label>
                  <el-select
                    v-model="imageApiDraft.backend"
                    class="pc-input"
                    @change="handleImageBackendChange"
                  >
                    <el-option
                      v-for="opt in imageBackendOptions"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                    />
                  </el-select>
                </div>

                <div class="pc-field">
                  <label class="pc-field-label">图片模型</label>
                  <el-input
                    v-model="imageApiDraft.model"
                    :placeholder="getImageBackendTemplate().model"
                    class="pc-input"
                  />
                </div>
              </div>

              <div class="pc-field">
                <label class="pc-field-label">图片 API Key（可选）</label>
                <el-input
                  v-model="imageApiDraft.api_key"
                  type="password"
                  show-password
                  placeholder="留空使用公共图片账号"
                  class="pc-input"
                  autocomplete="off"
                />
                <span v-if="hasStoredApiKey('image.api_key')" class="pc-field-help">
                  已保存图片密钥，出于安全不会回显。留空会继续保留。
                  <el-button link type="danger" @click="clearStoredApiKey('image.api_key')"
                    >移除</el-button
                  >
                </span>
                <span v-else-if="apiKeysToClear.includes('image.api_key')" class="pc-field-help">
                  保存后将移除图片密钥。
                  <el-button link @click="keepStoredApiKey('image.api_key')">撤销</el-button>
                </span>
              </div>

              <div class="pc-field">
                <label class="pc-field-label">{{ getImageBackendTemplate().baseLabel }}</label>
                <el-input
                  v-model="imageApiDraft.base_url"
                  :placeholder="getImageBackendTemplate().basePlaceholder"
                  class="pc-input"
                />
                <span class="pc-field-help">
                  {{ getImageBackendTemplate().help }}
                </span>
              </div>
            </section>

            <button
              type="button"
              class="pc-advanced-toggle"
              @click="showRawApiJson = !showRawApiJson"
            >
              {{ showRawApiJson ? '收起高级 JSON' : '显示高级 JSON' }}
            </button>

            <div v-if="showRawApiJson" class="pc-field">
              <label class="pc-field-label">高级 API Key 增量 JSON</label>
              <el-input
                v-model="advancedApiKeysDraft"
                type="textarea"
                :rows="4"
                placeholder='{"deepseek":"sk-xxx","image":{"backend":"openai","api_key":"sk-xxx","base_url":"https://img-cn.65535.space/v1","model":"gpt-image-2"}}'
                class="pc-input"
              />
              <span class="pc-field-help">仅提交本次更新，服务端不会回显或覆盖未填写的密钥。</span>
            </div>

            <div v-if="apiConfig.active_provider === 'deepseek'" class="pc-field pc-field--compact">
              <label class="pc-field-label">兼容说明</label>
              <span class="pc-field-help"
                >数据助手使用 OpenAI 兼容地址；需要 Anthropic SDK 时请在对应工具中使用
                <code>https://api.deepseek.com/anthropic</code>。</span
              >
            </div>

            <div class="pc-actions">
              <el-button type="primary" :loading="savingApi" @click="handleSaveApi" class="pc-btn">
                <span class="pc-btn-text">保存 API 配置</span>
                <span class="pc-btn-shimmer" aria-hidden="true"></span>
              </el-button>
              <span class="pc-actions-hint">无需重启，下一次打开数据助手或发送消息即生效</span>
            </div>
          </el-form>

          <div
            v-if="activeTab === 'assistant-memory'"
            class="pc-form pc-memory-panel"
            v-loading="memoryLoading"
          >
            <div class="pc-api-head">
              <div>
                <span class="pc-api-kicker">HERMES MEMORY</span>
                <h2 class="pc-api-title">助手长期记忆</h2>
              </div>
              <span class="pc-api-status"
                >更新于 {{ formatMemoryTime(assistantMemory.updated_at) }}</span
              >
            </div>

            <section class="pc-memory-card">
              <div class="pc-memory-card-head">
                <span>当前保存的用户印象</span>
                <strong>{{ assistantMemory.memory_summary ? '已启用' : '暂无记忆' }}</strong>
              </div>
              <pre v-if="assistantMemory.memory_summary" class="pc-memory-text">{{
                assistantMemory.memory_summary
              }}</pre>
              <div v-else class="pc-memory-empty">
                Hermes
                暂未形成长期记忆。后续对话中，系统只会保存稳定偏好、常用工作方式和长期背景摘要。
              </div>
            </section>

            <div class="pc-memory-notes">
              <span>记忆按登录账号隔离，用于让数据助手理解你的偏好和长期上下文。</span>
              <span>清空记忆不会删除历史会话，也不会影响 API 配置。</span>
            </div>

            <div class="pc-actions">
              <el-button
                type="primary"
                :loading="clearingMemory"
                :disabled="!assistantMemory.memory_summary"
                @click="clearAssistantMemory"
                class="pc-btn pc-btn--danger"
              >
                <span class="pc-btn-text">清空助手记忆</span>
                <span class="pc-btn-shimmer" aria-hidden="true"></span>
              </el-button>
              <el-button @click="loadAssistantMemory">刷新</el-button>
              <span class="pc-actions-hint">当前仅显示你自己的 Hermes 记忆</span>
            </div>
          </div>

          <div
            v-if="activeTab === 'privacy'"
            class="pc-form pc-privacy-panel"
            v-loading="privacyLoading"
          >
            <div class="pc-api-head">
              <div>
                <span class="pc-api-kicker">PRIVACY RIGHTS</span>
                <h2 class="pc-api-title">个人数据访问与删除申请</h2>
              </div>
              <span class="pc-api-status">本人账号范围</span>
            </div>

            <section class="pc-privacy-card" aria-labelledby="personal-export-title">
              <h3 id="personal-export-title">导出当前可访问的数据</h3>
              <p>
                JSON
                导出包含账号资料、搜索记录、收藏、助手会话和助手记忆，以及可安全证明归属的工作区文件元数据、文件哈希与下载路径、定时任务和已引用报告元数据、当前项目权限下的项目元数据与本人提交内容；不包含密码哈希、重置令牌或
                API Key
                值。工作区或报告正文、敏感任务上下文、其他项目成员私密字段及无法安全验证的范围会明确列为
                unavailable。
              </p>
              <el-button
                type="primary"
                class="pc-btn"
                :loading="exportingPersonalData"
                @click="exportPersonalData"
              >
                下载个人数据 JSON
              </el-button>
            </section>

            <section
              class="pc-privacy-card pc-deletion-plan"
              aria-labelledby="deletion-impact-title"
              :aria-busy="deletionPlanLoading"
            >
              <div class="pc-deletion-plan-head">
                <div>
                  <h3 id="deletion-impact-title">账号删除影响预检（只读）</h3>
                  <p>
                    仅统计当前能安全证明属于本人的数据范围并生成受阻执行计划；不会登记申请，也不会删除、匿名化或改写任何数据。
                  </p>
                </div>
                <strong class="pc-deletion-plan-state">执行受阻</strong>
              </div>

              <p
                v-if="deletionPlanError"
                class="pc-deletion-plan-error"
                role="alert"
                aria-live="assertive"
              >
                {{ deletionPlanError }}。未能证明的范围不会按 0 条处理。
              </p>
              <p
                v-else-if="deletionPlanLoading"
                class="pc-memory-empty"
                role="status"
                aria-live="polite"
              >
                正在执行只读影响预检…
              </p>
              <div v-else-if="deletionPlan" class="pc-deletion-plan-result" aria-live="polite">
                <p class="pc-deletion-plan-boundary" role="status">
                  deletion_performed=false ·
                  execution_state=blocked。保留期限、法律依据、可恢复检查点和人工授权全部完成前，本计划不可执行。
                </p>
                <ul class="pc-deletion-plan-summary" aria-label="处置分类汇总">
                  <li
                    v-for="summary in deletionPlanSummary"
                    :key="summary.disposition"
                    :class="`is-${summary.disposition}`"
                  >
                    <strong>{{ summary.label }}</strong>
                    <span>{{ summary.scopes }} 个范围 · {{ summary.records }} 条精确记录</span>
                    <small v-if="summary.unavailable">
                      {{ summary.unavailable }} 个范围计数不可用
                    </small>
                  </li>
                </ul>

                <details class="pc-deletion-plan-details">
                  <summary>查看范围分类明细</summary>
                  <ul>
                    <li v-for="item in deletionPlanItems" :key="item.scope">
                      <span>{{ item.scope }}</span>
                      <strong>{{
                        deletionDispositionLabels[item.disposition] || '需要复核'
                      }}</strong>
                      <small>
                        {{
                          item.count_status === 'exact' ? `${item.record_count} 条` : '计数不可用'
                        }}
                      </small>
                    </li>
                  </ul>
                </details>

                <details class="pc-deletion-plan-details">
                  <summary>查看外部执行阻塞项</summary>
                  <ul>
                    <li v-for="blocker in deletionPlanBlockers" :key="blocker.code">
                      <span>{{ blocker.category }}</span>
                      <strong>未完成</strong>
                      <small>{{ blocker.required_authority }}</small>
                    </li>
                  </ul>
                </details>
              </div>
              <el-button
                class="pc-deletion-plan-refresh"
                :loading="deletionPlanLoading"
                @click="loadDeletionImpactPlan"
              >
                刷新只读预检
              </el-button>
            </section>

            <section
              class="pc-privacy-card pc-privacy-card--warning"
              aria-labelledby="deletion-request-title"
            >
              <h3 id="deletion-request-title">登记账号删除申请</h3>
              <p>
                当前流程只安全登记和撤销申请，不会声称已经删除。工作区、运行中的定时任务、研究项目权限和法定保留范围完成核验前，状态会保持“待人工执行”。
              </p>
              <div class="pc-field">
                <label class="pc-field-label" for="privacy-password">当前密码</label>
                <el-input
                  id="privacy-password"
                  v-model="deletionForm.password"
                  type="password"
                  show-password
                  autocomplete="current-password"
                  class="pc-input"
                />
              </div>
              <div class="pc-field">
                <label class="pc-field-label" for="privacy-acknowledgement">
                  输入确认短语 REQUEST ACCOUNT DELETION
                </label>
                <el-input
                  id="privacy-acknowledgement"
                  v-model="deletionForm.acknowledgement"
                  autocomplete="off"
                  placeholder="REQUEST ACCOUNT DELETION"
                  class="pc-input"
                />
              </div>
              <el-button
                type="primary"
                class="pc-btn pc-btn--danger"
                :loading="creatingDeletionRequest"
                :disabled="
                  !deletionForm.password ||
                  deletionForm.acknowledgement !== 'REQUEST ACCOUNT DELETION'
                "
                @click="createDeletionRequest"
              >
                登记删除申请
              </el-button>
            </section>

            <section class="pc-privacy-card" aria-labelledby="deletion-history-title">
              <div class="pc-memory-card-head">
                <h3 id="deletion-history-title">删除申请记录</h3>
                <el-button size="small" @click="loadDeletionRequests">刷新</el-button>
              </div>
              <p v-if="!deletionRequests.length" class="pc-memory-empty" role="status">
                当前没有删除申请。
              </p>
              <ul v-else class="pc-privacy-requests" aria-live="polite">
                <li v-for="item in deletionRequests" :key="item.request_id">
                  <div>
                    <strong>{{ item.status === 'cancelled' ? '已撤销' : '待人工执行' }}</strong>
                    <span>{{ item.requested_at }}</span>
                    <small
                      >执行状态：{{
                        item.execution_status === 'not_executed'
                          ? '尚未执行'
                          : item.execution_status
                      }}</small
                    >
                  </div>
                  <el-button
                    v-if="item.status === 'pending_manual_execution'"
                    :loading="cancellingDeletionRequestId === item.request_id"
                    @click="cancelDeletionRequest(item.request_id)"
                  >
                    撤销申请
                  </el-button>
                </li>
              </ul>
            </section>
          </div>

          <DisplayPreferencesPanel v-if="activeTab === 'display'" />
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* ============================================
   DESIGN TOKENS
   ============================================ */
.pc-root {
  --pc-bg: #f9f8fb;
  --pc-surface: #ffffff;
  --pc-surface-warm: #fdfcfd;
  --pc-border: #e8e5f0;
  --pc-border-light: #f0edf6;
  --pc-text: #1a1824;
  --pc-text-secondary: #5c5870;
  --pc-text-muted: #6f6a80;
  --pc-accent-soft: rgba(91, 114, 223, 0.08);
  --pc-accent-glow: rgba(91, 114, 223, 0.18);
  --pc-shadow-sm: 0 1px 2px rgba(26, 24, 36, 0.04);
  --pc-shadow: 0 1px 3px rgba(26, 24, 36, 0.04), 0 4px 16px rgba(26, 24, 36, 0.05);
  --pc-shadow-lg: 0 2px 6px rgba(26, 24, 36, 0.03), 0 8px 32px rgba(26, 24, 36, 0.07);
  --pc-radius-sm: 8px;
  --pc-radius: 14px;
  --pc-radius-lg: 20px;
  --pc-transition: 0.22s cubic-bezier(0.33, 1, 0.68, 1);

  width: 100%;
  max-width: 1040px;
  margin: 0 auto;
  animation: pc-fade-up 0.55s cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes pc-fade-up {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ============================================
   HEADER
   ============================================ */
.pc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
  padding: 32px 36px;
  background: var(--pc-surface);
  border-radius: var(--pc-radius-lg);
  border: 1px solid var(--pc-border);
  box-shadow: var(--pc-shadow);
  position: relative;
  overflow: hidden;
}

.pc-header-body {
  position: relative;
  z-index: 1;
}

.pc-header-overline {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--uc-accent, #5b72df);
  margin-bottom: 8px;
}

.pc-header-title {
  margin: 0;
  font-size: 1.85rem;
  font-weight: 750;
  color: var(--pc-text);
  letter-spacing: -0.02em;
  line-height: 1.15;
}

.pc-header-desc {
  margin: 6px 0 0;
  font-size: 0.92rem;
  color: var(--pc-text-muted);
  font-weight: 460;
}

/* Decorative rings */
.pc-header-visual {
  position: relative;
  width: 100px;
  height: 100px;
  flex-shrink: 0;
  opacity: 0.5;
}

.pc-header-ring {
  position: absolute;
  border-radius: 50%;
  border: 1.5px solid var(--pc-border);
}

.pc-header-ring--1 {
  width: 90px;
  height: 90px;
  top: 5px;
  left: 5px;
}

.pc-header-ring--2 {
  width: 64px;
  height: 64px;
  top: 18px;
  left: 18px;
  border-color: var(--uc-accent, #5b72df);
  opacity: 0.35;
}

.pc-header-dot {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--uc-accent, #5b72df);
  top: 28px;
  right: 16px;
}

/* ============================================
   LAYOUT
   ============================================ */
.pc-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 24px;
  align-items: start;
}

/* ============================================
   CARD BASE
   ============================================ */
.pc-card {
  background: var(--pc-surface);
  border-radius: var(--pc-radius-lg);
  border: 1px solid var(--pc-border);
  box-shadow: var(--pc-shadow);
  position: relative;
  overflow: hidden;
}

/* Grain texture overlay */
.pc-card-grain {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  opacity: 0.025;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
}

/* ============================================
   IDENTITY CARD (LEFT)
   ============================================ */
.pc-card--identity {
  padding: 0;
  animation: pc-fade-up 0.55s cubic-bezier(0.22, 1, 0.36, 1) 0.08s both;
}

.pc-avatar-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 36px 24px 20px;
  position: relative;
  z-index: 1;
}

/* Avatar with progress ring */
.pc-avatar-outer {
  position: relative;
  width: 96px;
  height: 96px;
  margin-bottom: 16px;
}

.pc-avatar-inner {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  background: linear-gradient(142deg, #eef1ff 0%, #dce2ff 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.pc-avatar-init {
  font-size: 2rem;
  font-weight: 700;
  color: var(--uc-accent, #5b72df);
  letter-spacing: -0.02em;
  line-height: 1;
}

.pc-avatar-progress {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.pc-avatar-arc {
  animation: pc-arc-draw 1.2s cubic-bezier(0.22, 1, 0.36, 1) 0.3s both;
}

@keyframes pc-arc-draw {
  from {
    stroke-dashoffset: 289;
  }
  to {
    stroke-dashoffset: 72;
  }
}

.pc-avatar-info {
  text-align: center;
}

.pc-avatar-name {
  display: block;
  font-size: 1.1rem;
  font-weight: 680;
  color: var(--pc-text);
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.pc-avatar-handle {
  display: block;
  font-size: 0.84rem;
  color: var(--pc-text-muted);
  margin-top: 2px;
  font-weight: 440;
}

.pc-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  padding: 5px 12px;
  background: #f0faf4;
  border-radius: 100px;
  border: 1px solid #d4ede0;
}

.pc-badge-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
  animation: pc-pulse-dot 3s ease-in-out infinite;
}

@keyframes pc-pulse-dot {
  0%,
  100% {
    box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
  }
  50% {
    box-shadow: 0 0 0 7px rgba(34, 197, 94, 0.06);
  }
}

.pc-badge-text {
  font-size: 0.78rem;
  font-weight: 600;
  color: #15803d;
  letter-spacing: 0.01em;
}

/* Divider */
.pc-divider {
  margin: 0 24px;
  border: none;
  border-top: 1px solid var(--pc-border-light);
  position: relative;
  z-index: 1;
}

/* Meta info list */
.pc-meta {
  margin: 0;
  padding: 16px 20px 12px;
  position: relative;
  z-index: 1;
}

.pc-meta-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 11px 12px;
  border-radius: var(--pc-radius-sm);
  transition: background var(--pc-transition);
}

.pc-meta-row:hover {
  background: var(--pc-accent-soft);
}

.pc-meta-key {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 0.84rem;
  font-weight: 540;
  color: var(--pc-text-secondary);
  margin: 0;
  flex-shrink: 0;
}

.pc-meta-icon {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  flex-shrink: 0;
}

.pc-meta-icon--email {
  background: #8b5cf6;
}
.pc-meta-icon--phone {
  background: #06b6d4;
}
.pc-meta-icon--date {
  background: #f59e0b;
}

.pc-meta-val {
  margin: 0;
  font-size: 0.85rem;
  font-weight: 560;
  color: var(--pc-text);
  text-align: right;
  word-break: break-all;
}

.pc-meta-val--date {
  font-size: 0.8rem;
  color: var(--pc-text-muted);
  font-weight: 460;
}

/* Bottom accent bar */
.pc-card-accent {
  height: 3px;
  background: linear-gradient(90deg, var(--uc-accent, #5b72df) 0%, #818cf8 50%, transparent 100%);
  position: relative;
  z-index: 1;
}

/* ============================================
   SETTINGS CARD (RIGHT)
   ============================================ */
.pc-card--settings {
  padding: 0;
  animation: pc-fade-up 0.55s cubic-bezier(0.22, 1, 0.36, 1) 0.14s both;
}

/* ---- Tabs ---- */
.pc-tabs {
  display: flex;
  gap: 0;
  padding: 6px 6px 0;
  position: relative;
  border-bottom: 1px solid var(--pc-border-light);
}

.pc-tab {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 15px 12px 17px;
  border: none;
  background: transparent;
  cursor: pointer;
  position: relative;
  z-index: 1;
  transition: color var(--pc-transition);
  font-family: inherit;
}

.pc-tab-num {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--pc-text-muted);
  transition: color var(--pc-transition);
}

.pc-tab-label {
  font-size: 0.9rem;
  font-weight: 620;
  color: var(--pc-text-muted);
  transition: color var(--pc-transition);
}

.pc-tab:hover .pc-tab-num,
.pc-tab:hover .pc-tab-label {
  color: var(--pc-text-secondary);
}

.pc-tab.is-active .pc-tab-num {
  color: var(--uc-accent, #5b72df);
}

.pc-tab.is-active .pc-tab-label {
  color: var(--pc-text);
}

/* Sliding thumb */
.pc-tab-track {
  position: absolute;
  bottom: 0;
  left: 6px;
  right: 6px;
  height: 2.5px;
}

.pc-tab-thumb {
  height: 100%;
  width: calc(100% / var(--pc-tab-count, 5));
  background: var(--uc-accent, #5b72df);
  border-radius: 2px;
  transform: translateX(calc(var(--pc-tab-index, 0) * 100%));
  transition: transform 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}

/* ---- Form Stage ---- */
.pc-form-stage {
  padding: 32px 36px 36px;
  min-height: 300px;
}

.pc-field {
  margin-bottom: 22px;
}

.pc-field:last-of-type {
  margin-bottom: 28px;
}

.pc-field-label {
  display: block;
  margin-bottom: 7px;
  font-size: 0.82rem;
  font-weight: 620;
  color: var(--pc-text-secondary);
  letter-spacing: 0.01em;
}

.pc-field-help {
  display: block;
  margin-top: 6px;
  font-size: 0.76rem;
  line-height: 1.6;
  color: var(--pc-text-muted);
}

.pc-field-help code {
  padding: 1px 5px;
  border-radius: 5px;
  background: #f1f5f9;
  color: #334155;
  font-size: 0.74rem;
}

.pc-field--compact {
  margin-top: -6px;
  margin-bottom: 18px;
}

.pc-api-form {
  --api-ink: #172033;
  --api-line: #dfe6f3;
  --api-soft: #f5f8fc;
  --api-blue: #2563eb;
  --api-teal: #0f766e;
}

.pc-api-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.pc-api-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--api-teal);
  font-size: 0.66rem;
  font-weight: 750;
  letter-spacing: 0.14em;
}

.pc-api-title {
  margin: 0;
  color: var(--api-ink);
  font-size: 1.08rem;
  line-height: 1.25;
  font-weight: 740;
}

.pc-api-status {
  flex-shrink: 0;
  padding: 5px 10px;
  border: 1px solid #cde7df;
  border-radius: 999px;
  background: #f0fdfa;
  color: #0f766e;
  font-size: 0.74rem;
  font-weight: 650;
}

.pc-provider-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 18px;
}

.pc-provider-card {
  min-width: 0;
  height: 58px;
  padding: 8px 10px;
  border: 1px solid var(--api-line);
  border-radius: 8px;
  background: #fff;
  color: var(--pc-text-secondary);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 3px;
  font-family: inherit;
  transition:
    border-color var(--pc-transition),
    background var(--pc-transition),
    box-shadow var(--pc-transition);
}

.pc-provider-card:hover {
  border-color: #b8c7df;
  background: var(--api-soft);
}

.pc-provider-card.is-selected {
  border-color: rgba(37, 99, 235, 0.55);
  background: #eff6ff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}

.pc-provider-name {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--api-ink);
  font-size: 0.86rem;
  font-weight: 700;
  white-space: nowrap;
}

.pc-provider-badge {
  color: #64748b;
  font-size: 0.68rem;
  font-weight: 650;
}

.pc-api-template {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  margin-bottom: 20px;
  padding: 16px;
  border: 1px solid var(--api-line);
  border-radius: 8px;
  background: linear-gradient(180deg, #fbfdff 0%, #f7faff 100%);
}

.pc-api-template-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pc-api-template-title {
  margin: 0;
  color: var(--api-ink);
  font-size: 0.98rem;
  line-height: 1.3;
  font-weight: 730;
}

.pc-template-apply,
.pc-advanced-toggle {
  border: 1px solid var(--api-line);
  border-radius: 7px;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.78rem;
  font-weight: 650;
  transition:
    background var(--pc-transition),
    border-color var(--pc-transition),
    color var(--pc-transition);
}

.pc-template-apply {
  flex-shrink: 0;
  padding: 6px 10px;
}

.pc-template-apply:hover,
.pc-advanced-toggle:hover {
  border-color: rgba(37, 99, 235, 0.35);
  background: #eff6ff;
  color: var(--api-blue);
}

.pc-api-template-desc {
  margin: 8px 0 0;
  color: var(--pc-text-secondary);
  font-size: 0.84rem;
  line-height: 1.6;
}

.pc-api-note {
  margin-top: 10px;
  padding-left: 10px;
  border-left: 3px solid #99f6e4;
  color: #475569;
  font-size: 0.78rem;
  line-height: 1.55;
}

.pc-endpoint-table {
  display: grid;
  gap: 6px;
}

.pc-endpoint-row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid #e6edf7;
}

.pc-endpoint-row span {
  color: #64748b;
  font-size: 0.74rem;
  font-weight: 650;
}

.pc-endpoint-row code {
  min-width: 0;
  color: #0f172a;
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}

.pc-api-row {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.35fr);
  gap: 14px;
}

.pc-code-sample {
  margin: -2px 0 18px;
  border: 1px solid #d8e1ee;
  border-radius: 8px;
  background: #101827;
  overflow: hidden;
}

.pc-code-title {
  padding: 9px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.22);
  color: #cbd5e1;
  font-size: 0.76rem;
  font-weight: 700;
}

.pc-code-sample pre {
  margin: 0;
  padding: 12px;
  max-height: 220px;
  overflow: auto;
  color: #dbeafe;
  font-size: 0.76rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.pc-image-api-panel {
  margin: 0 0 18px;
  padding: 16px;
  border: 1px solid var(--api-line);
  border-radius: 8px;
  background: #fbfdff;
}

.pc-image-api-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 8px;
}

.pc-image-api-title {
  margin: 0;
  color: var(--api-ink);
  font-size: 0.98rem;
  line-height: 1.3;
  font-weight: 730;
}

.pc-image-api-mode {
  flex-shrink: 0;
  padding: 5px 9px;
  border-radius: 7px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.72rem;
  font-weight: 700;
}

.pc-image-api-desc {
  margin: 0 0 14px;
  color: #64748b;
  font-size: 0.8rem;
  line-height: 1.6;
}

.pc-advanced-toggle {
  margin: 0 0 16px;
  padding: 7px 11px;
}

.pc-memory-panel {
  --api-ink: #172033;
  --api-line: #dfe6f3;
  --api-teal: #0f766e;
}

.pc-memory-card {
  margin-bottom: 16px;
  border: 1px solid #dfe6f3;
  border-radius: 8px;
  background: #fbfdff;
  overflow: hidden;
}

.pc-memory-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #e8eef8;
  color: #475569;
  font-size: 0.78rem;
  font-weight: 700;
}

.pc-memory-card-head strong {
  flex-shrink: 0;
  color: #0f766e;
  font-size: 0.74rem;
}

.pc-memory-text {
  margin: 0;
  padding: 14px;
  max-height: 320px;
  overflow: auto;
  color: #1f2937;
  font-family: inherit;
  font-size: 0.86rem;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #fff;
}

.pc-memory-empty {
  padding: 22px 16px;
  color: #64748b;
  font-size: 0.86rem;
  line-height: 1.7;
  background: #fff;
}

.pc-memory-notes {
  display: grid;
  gap: 6px;
  margin-bottom: 18px;
  color: #64748b;
  font-size: 0.78rem;
  line-height: 1.55;
}

.pc-privacy-panel {
  --api-ink: #172033;
  --api-line: #dfe6f3;
}

.pc-privacy-card {
  display: grid;
  gap: 12px;
  margin-bottom: 16px;
  padding: 18px;
  border: 1px solid #dfe6f3;
  border-radius: 10px;
  background: #fbfdff;
}

.pc-privacy-card--warning {
  border-color: #fed7aa;
  background: #fffaf5;
}

.pc-privacy-card h3 {
  margin: 0;
  color: #1f2937;
  font-size: 0.94rem;
}

.pc-privacy-card p {
  margin: 0;
  color: #64748b;
  font-size: 0.82rem;
  line-height: 1.7;
}

.pc-deletion-plan {
  border-color: #c7d2fe;
  background: #f8faff;
}

.pc-deletion-plan-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.pc-deletion-plan-head > div {
  display: grid;
  gap: 8px;
}

.pc-deletion-plan-state {
  flex: 0 0 auto;
  border-radius: 999px;
  padding: 7px 11px;
  color: #9a3412;
  font-size: 0.74rem;
  background: #ffedd5;
}

.pc-deletion-plan-result {
  display: grid;
  gap: 12px;
}

.pc-deletion-plan-boundary {
  border-left: 3px solid #f59e0b;
  padding: 9px 11px;
  color: #78350f !important;
  background: #fffbeb;
}

.pc-deletion-plan-error {
  border-radius: 8px;
  padding: 10px 12px;
  color: #991b1b !important;
  background: #fef2f2;
}

.pc-deletion-plan-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pc-deletion-plan-summary li {
  display: grid;
  gap: 4px;
  border: 1px solid #dfe6f3;
  border-radius: 8px;
  padding: 10px 11px;
  background: #fff;
}

.pc-deletion-plan-summary strong {
  color: #334155;
  font-size: 0.78rem;
}

.pc-deletion-plan-summary span,
.pc-deletion-plan-summary small {
  color: #64748b;
  font-size: 0.72rem;
  line-height: 1.45;
}

.pc-deletion-plan-summary .is-unavailable {
  border-color: #fed7aa;
  background: #fffaf5;
}

.pc-deletion-plan-details {
  border: 1px solid #dfe6f3;
  border-radius: 8px;
  background: #fff;
}

.pc-deletion-plan-details summary {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 12px;
  color: #334155;
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
}

.pc-deletion-plan-details summary:focus-visible {
  border-radius: 8px;
  outline: 3px solid #818cf8;
  outline-offset: 2px;
}

.pc-deletion-plan-details ul {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  border-top: 1px solid #e8eef8;
  list-style: none;
}

.pc-deletion-plan-details li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid #eef2f7;
  color: #475569;
  font-size: 0.72rem;
}

.pc-deletion-plan-details li:last-child {
  border-bottom: 0;
}

.pc-deletion-plan-details li span {
  overflow-wrap: anywhere;
}

.pc-deletion-plan-details li strong {
  color: #334155;
}

.pc-deletion-plan-details li small {
  color: #64748b;
}

.pc-deletion-plan-refresh {
  justify-self: start;
  min-width: 44px;
  min-height: 44px;
}

.pc-privacy-requests {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pc-privacy-requests li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid #e8eef8;
  border-radius: 8px;
  background: #fff;
}

.pc-privacy-requests li div {
  display: grid;
  gap: 3px;
}

.pc-privacy-requests strong {
  color: #9a3412;
  font-size: 0.82rem;
}

.pc-privacy-requests span,
.pc-privacy-requests small {
  color: #64748b;
  font-size: 0.74rem;
}

.pc-security-panel {
  display: grid;
  gap: 16px;
}

.pc-security-heading,
.pc-security-card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.pc-security-heading h2,
.pc-security-card h3,
.pc-security-card h4 {
  margin: 0;
  color: var(--pc-text);
}

.pc-security-heading > strong {
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 0.78rem;
}

.pc-security-heading > strong.is-enabled {
  color: #166534;
  background: #dcfce7;
}

.pc-security-heading > strong.is-disabled {
  color: #9a3412;
  background: #ffedd5;
}

.pc-security-heading > strong.is-unknown {
  color: #475569;
  background: #e2e8f0;
}

.pc-security-boundary,
.pc-security-card p {
  margin: 0;
  color: var(--pc-text-secondary);
  font-size: 0.82rem;
  line-height: 1.7;
}

.pc-security-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin: 0;
  padding: 0;
  color: var(--pc-text-secondary);
  font-size: 0.78rem;
  list-style: none;
}

.pc-security-notice {
  margin: 0;
  border-radius: 9px;
  padding: 11px 13px;
  color: #166534;
  background: #ecfdf5;
}

.pc-security-notice.is-error {
  color: #991b1b;
  background: #fef2f2;
}

.pc-security-notice:focus-visible {
  outline: 3px solid currentColor;
  outline-offset: 3px;
}

.pc-security-card,
.pc-enrollment,
.pc-recovery-codes {
  display: grid;
  gap: 12px;
  border: 1px solid #dfe6f3;
  border-radius: 10px;
  padding: 16px;
  background: #fbfdff;
}

.pc-enrollment,
.pc-recovery-codes {
  background: #fff;
}

.pc-recovery-codes {
  border-color: #f59e0b;
  background: #fffbeb;
}

.pc-recovery-codes ul {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pc-security-methods {
  display: flex;
  gap: 18px;
  margin: 0;
  border: 1px solid #dfe6f3;
  border-radius: 9px;
  padding: 10px 12px;
}

.pc-security-methods label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.pc-security-fields {
  display: grid;
  gap: 7px;
}

.pc-security-fields input,
.pc-enrollment input {
  min-height: 44px;
  box-sizing: border-box;
  border: 1px solid var(--pc-border);
  border-radius: 8px;
  padding: 9px 12px;
  color: var(--pc-text);
  background: #fff;
  font: inherit;
}

.pc-enrollment code,
.pc-recovery-codes code {
  overflow-wrap: anywhere;
  border-radius: 7px;
  padding: 9px;
  color: #1e3a5f;
  background: #eef5ff;
}

.pc-security-button {
  min-height: 44px;
  border: 0;
  border-radius: 8px;
  padding: 9px 14px;
  color: #fff;
  background: var(--uc-accent, #5b72df);
  font: inherit;
  font-weight: 650;
  cursor: pointer;
}

.pc-security-button.is-danger {
  background: #b91c1c;
}

.pc-security-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.pc-security-button:focus-visible,
.pc-security-fields input:focus-visible,
.pc-enrollment input:focus-visible {
  outline: 3px solid rgba(91, 114, 223, 0.35);
  outline-offset: 2px;
}

.pc-session-list,
.pc-audit-list {
  display: grid;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.pc-session-list li,
.pc-audit-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid #e8eef8;
  padding-top: 10px;
}

.pc-session-list li div,
.pc-audit-list li {
  min-width: 0;
}

.pc-session-list strong,
.pc-session-list span,
.pc-session-list small,
.pc-audit-list strong,
.pc-audit-list span,
.pc-audit-list small {
  display: block;
  overflow-wrap: anywhere;
}

.pc-session-list span,
.pc-session-list small,
.pc-audit-list span,
.pc-audit-list small {
  color: var(--pc-text-muted);
  font-size: 0.72rem;
}

/* ---- Input overrides ---- */
.pc-input :deep(.el-input__wrapper) {
  background: var(--pc-surface-warm);
  border-radius: var(--pc-radius-sm);
  padding: 6px 14px;
  box-shadow: 0 0 0 1px var(--pc-border) inset;
  transition:
    box-shadow var(--pc-transition),
    background var(--pc-transition);
}

.pc-input :deep(.el-input__wrapper:hover) {
  background: #f6f4fa;
  box-shadow: 0 0 0 1px var(--pc-text-muted) inset;
}

.pc-input :deep(.el-input__wrapper.is-focus) {
  background: var(--pc-surface);
  box-shadow:
    0 0 0 1px var(--uc-accent, #5b72df) inset,
    0 0 0 4px var(--pc-accent-soft);
}

.pc-input :deep(.el-input__inner) {
  font-size: 0.9rem;
  font-weight: 460;
  color: var(--pc-text);
  font-family: inherit;
}

.pc-input :deep(.el-input__inner::placeholder) {
  color: #c4bfd2;
  font-weight: 420;
}

/* Password reveal icon */
.pc-input :deep(.el-input__suffix) {
  color: var(--pc-text-muted);
}

.pc-input :deep(.el-input__suffix:hover) {
  color: var(--pc-text-secondary);
}

/* ---- Actions ---- */
.pc-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-top: 4px;
}

.pc-actions-hint {
  font-size: 0.78rem;
  color: var(--pc-text-muted);
  font-weight: 440;
}

/* ---- Button ---- */
.pc-btn {
  --el-button-bg-color: var(--uc-accent, #5b72df) !important;
  --el-button-border-color: transparent !important;
  --el-button-hover-bg-color: #4f62d4 !important;
  --el-button-hover-border-color: transparent !important;
  --el-button-active-bg-color: #4558c4 !important;

  padding: 10px 26px !important;
  border-radius: var(--pc-radius-sm) !important;
  font-size: 0.88rem !important;
  font-weight: 620 !important;
  letter-spacing: 0.01em;
  position: relative;
  overflow: hidden;
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease;
  box-shadow:
    0 2px 8px rgba(91, 114, 223, 0.28),
    0 1px 2px rgba(91, 114, 223, 0.12);
}

.pc-btn:hover {
  transform: translateY(-1px);
  box-shadow:
    0 4px 14px rgba(91, 114, 223, 0.34),
    0 2px 4px rgba(91, 114, 223, 0.16);
}

.pc-btn:active {
  transform: translateY(0) scale(0.985);
}

.pc-btn .pc-btn-shimmer {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    105deg,
    transparent 30%,
    rgba(255, 255, 255, 0.12) 50%,
    transparent 70%
  );
  transform: translateX(-100%);
  transition: transform 0.55s ease;
}

.pc-btn:hover .pc-btn-shimmer {
  transform: translateX(100%);
}

/* Override Element Plus loading spinner */
.pc-btn :deep(.el-icon) {
  color: rgba(255, 255, 255, 0.85);
}

.pc-btn--danger {
  --el-button-bg-color: #dc2626 !important;
  --el-button-hover-bg-color: #b91c1c !important;
  --el-button-active-bg-color: #991b1b !important;
  box-shadow:
    0 2px 8px rgba(220, 38, 38, 0.22),
    0 1px 2px rgba(220, 38, 38, 0.1);
}

/* ============================================
   RESPONSIVE
   ============================================ */
@media (max-width: 768px) {
  .pc-header {
    padding: 24px 20px;
    flex-direction: column;
    align-items: flex-start;
    gap: 0;
  }

  .pc-header-visual {
    display: none;
  }

  .pc-layout {
    grid-template-columns: 1fr;
  }

  .pc-card--identity {
    order: -1;
  }

  .pc-avatar-block {
    padding: 28px 20px 16px;
  }

  .pc-form-stage {
    padding: 24px 20px 28px;
  }

  .pc-tab {
    flex-direction: column;
    gap: 2px;
    padding: 12px 4px 14px;
  }

  .pc-tab-label {
    font-size: 0.78rem;
  }

  .pc-api-head {
    flex-direction: column;
    gap: 10px;
  }

  .pc-api-status {
    align-self: flex-start;
  }

  .pc-image-api-head {
    flex-direction: column;
    gap: 8px;
  }

  .pc-provider-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pc-api-row,
  .pc-endpoint-row {
    grid-template-columns: 1fr;
  }

  .pc-endpoint-row {
    gap: 4px;
  }

  .pc-actions {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }

  .pc-security-heading,
  .pc-security-card-heading,
  .pc-session-list li {
    align-items: stretch;
    flex-direction: column;
  }

  .pc-deletion-plan-head,
  .pc-deletion-plan-details li {
    align-items: stretch;
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .pc-deletion-plan-state {
    align-self: flex-start;
  }

  .pc-recovery-codes ul {
    grid-template-columns: 1fr;
  }
}

@media (min-width: 769px) and (max-width: 1200px) {
  .pc-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
