import { API_PREFIX } from '../../config/api.js'
import { getToken } from '../../utils/auth.js'

export class AssistantApiError extends Error {
  constructor(message, { status = 0, payload = null, cause } = {}) {
    super(message, { cause })
    this.name = 'AssistantApiError'
    this.status = status
    this.payload = payload
  }
}

export function assistantErrorText(error, fallback = '操作失败') {
  return error?.message || String(error || fallback)
}

export function buildAssistantAuthHeaders(token, extra = {}) {
  const headers = { ...extra }
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function readResponsePayload(response) {
  const contentType = String(response.headers?.get?.('content-type') || '')
  if (contentType.includes('application/json')) {
    return response.json().catch(() => ({}))
  }
  const text = await response.text().catch(() => '')
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    return { detail: text }
  }
}

function payloadError(payload, fallback) {
  return payload?.detail || payload?.error || payload?.message || fallback
}

export function createAssistantApi({
  fetchImpl = globalThis.fetch,
  apiPrefix = API_PREFIX,
  getAccessToken = getToken,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Assistant API requires fetch')

  async function request(
    path,
    {
      method = 'GET',
      headers = {},
      body,
      signal,
      responseType = 'json',
      auth = true,
      absolute = false,
      fallbackMessage = '请求失败',
    } = {},
  ) {
    const token = auth ? getAccessToken() : ''
    const nextHeaders = buildAssistantAuthHeaders(token, headers)
    let nextBody = body
    if (
      body !== undefined &&
      body !== null &&
      typeof body !== 'string' &&
      !(typeof FormData !== 'undefined' && body instanceof FormData) &&
      !(typeof Blob !== 'undefined' && body instanceof Blob)
    ) {
      nextBody = JSON.stringify(body)
      if (!Object.keys(nextHeaders).some((key) => key.toLowerCase() === 'content-type')) {
        nextHeaders['Content-Type'] = 'application/json'
      }
    }

    let response
    try {
      response = await fetchImpl(absolute ? path : `${apiPrefix}${path}`, {
        method,
        headers: nextHeaders,
        credentials: 'same-origin',
        body: nextBody,
        signal,
      })
    } catch (error) {
      if (error?.name === 'AbortError') throw error
      throw new AssistantApiError(assistantErrorText(error, fallbackMessage), { cause: error })
    }

    if (responseType === 'response') {
      if (!response.ok) {
        const payload = await readResponsePayload(response)
        throw new AssistantApiError(payloadError(payload, fallbackMessage), {
          status: response.status,
          payload,
        })
      }
      return response
    }

    const payload = await readResponsePayload(response)
    if (!response.ok) {
      throw new AssistantApiError(payloadError(payload, fallbackMessage), {
        status: response.status,
        payload,
      })
    }
    return payload
  }

  return Object.freeze({
    listSchedules() {
      return request('/assistant/schedules', { fallbackMessage: '读取定时任务失败' })
    },
    saveSchedule(id, payload) {
      return request(`/assistant/schedules${id ? `/${encodeURIComponent(id)}` : ''}`, {
        method: id ? 'PUT' : 'POST',
        body: payload,
        fallbackMessage: '保存定时任务失败',
      })
    },
    runSchedule(id) {
      return request(`/assistant/schedules/${encodeURIComponent(id)}/run`, {
        method: 'POST',
        fallbackMessage: '运行定时任务失败',
      })
    },
    deleteSchedule(id) {
      return request(`/assistant/schedules/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        fallbackMessage: '删除定时任务失败',
      })
    },
    listWorkspaces() {
      return request('/workspaces', { fallbackMessage: '获取工作区失败' })
    },
    createWorkspace(payload) {
      return request('/workspaces', {
        method: 'POST',
        body: payload,
        fallbackMessage: '创建工作区失败',
      })
    },
    updateWorkspace(name, payload) {
      return request(`/workspaces/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: payload,
        fallbackMessage: '更新工作区失败',
      })
    },
    deleteWorkspace(name) {
      return request(`/workspaces/${encodeURIComponent(name)}`, {
        method: 'DELETE',
        fallbackMessage: '删除工作区失败',
      })
    },
    listWorkspaceFiles(name, subpath = '') {
      const query = subpath ? `?subpath=${encodeURIComponent(subpath)}` : ''
      return request(`/workspaces/${encodeURIComponent(name)}/files${query}`, {
        fallbackMessage: '获取文件列表失败',
      })
    },
    readWorkspaceFile(name, filename) {
      return request(
        `/workspaces/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}/read`,
        { fallbackMessage: '读取文件失败' },
      )
    },
    updateWorkspaceFile(name, filename, payload) {
      return request(`/workspaces/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}`, {
        method: 'PUT',
        body: payload,
        fallbackMessage: '保存文件失败',
      })
    },
    createWorkspaceFile(name, payload) {
      return request(`/workspaces/${encodeURIComponent(name)}/files`, {
        method: 'POST',
        body: payload,
        fallbackMessage: '创建文件失败',
      })
    },
    uploadWorkspaceFiles(name, formData) {
      return request(`/workspaces/${encodeURIComponent(name)}/upload`, {
        method: 'POST',
        body: formData,
        fallbackMessage: '上传文件失败',
      })
    },
    exportWorkspaceDocx(name, payload) {
      return request(`/workspaces/${encodeURIComponent(name)}/files/export-docx`, {
        method: 'POST',
        body: payload,
        fallbackMessage: '导出 Word 失败',
      })
    },
    downloadWorkspaceFile(name, filename) {
      return request(
        `/workspaces/${encodeURIComponent(name)}/files/${encodeURIComponent(filename)}/download`,
        { responseType: 'response', fallbackMessage: '下载失败' },
      )
    },
    downloadWorkspaceZip(name, filenames) {
      return request(`/workspaces/${encodeURIComponent(name)}/download-zip`, {
        method: 'POST',
        body: { filenames },
        responseType: 'response',
        fallbackMessage: '下载失败',
      })
    },
    readKbFile(filename, category) {
      return request(
        `/kb2/files/${encodeURIComponent(filename)}/read?category=${encodeURIComponent(category)}`,
        { fallbackMessage: '读取知识库文件失败' },
      )
    },
    getSkillCatalog(url = '/datasets/expert-skills/catalog.json') {
      return request(url, { absolute: true, auth: false, fallbackMessage: '获取 Skill 目录失败' })
    },
    listKbCategories() {
      return request('/kb2/categories', { fallbackMessage: '获取知识库分类失败' })
    },
    listKbFiles(category) {
      return request(`/kb2/files?category=${encodeURIComponent(category)}`, {
        fallbackMessage: '获取知识库文件失败',
      })
    },
    listDirectory(path) {
      return request(path, { fallbackMessage: '加载站点与成员配置失败' })
    },
    listSessions() {
      return request('/assistant/sessions', { fallbackMessage: '获取会话失败' })
    },
    createSession(title = '新会话') {
      return request('/assistant/sessions', {
        method: 'POST',
        body: { title },
        fallbackMessage: '创建会话失败',
      })
    },
    deleteSession(id) {
      return request(`/assistant/sessions/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        fallbackMessage: '删除会话失败',
      })
    },
    listSessionMessages(id) {
      return request(`/assistant/sessions/${encodeURIComponent(id)}/messages`, {
        fallbackMessage: '加载会话消息失败',
      })
    },
    openAssistantStream(payload, { signal, fallbackMessage = '数据助手请求失败' } = {}) {
      return request('/assistant/cc/stream', {
        method: 'POST',
        headers: { Accept: 'text/event-stream' },
        body: payload,
        signal,
        responseType: 'response',
        fallbackMessage,
      })
    },
  })
}

export const assistantApi = createAssistantApi()
