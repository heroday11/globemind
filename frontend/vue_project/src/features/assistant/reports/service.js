import {
  REPORT_OUTPUT_FORMATS,
  REPORT_WORKSPACE_NAME,
  buildPptMasterRequestMarkdown,
  buildReportFilename,
  selectedReportFormat,
} from './model.js'
import {
  normalizeAssistantWorkspaceDto,
  normalizeAssistantWorkspaceList,
} from '../workspace/model.js'

function responseMessage(response, fallback) {
  return response?.error || response?.detail || response?.message || fallback
}

function duplicateWorkspace(value) {
  const message = value?.payload?.error || value?.error || value?.detail || value?.message || ''
  return String(message).includes('同名工作区')
}

export function createAssistantReportArchiveService(
  api,
  {
    workspaceName = REPORT_WORKSPACE_NAME,
    hasToken = () => true,
    getWorkspaces = () => [],
    setWorkspaces = () => {},
    formatDateTime,
    now = Date.now,
  } = {},
) {
  if (!api || typeof api !== 'object') {
    throw new TypeError('Assistant report archive service requires an API facade')
  }

  function publishWorkspaces(rows) {
    const workspaces = normalizeAssistantWorkspaceList(rows)
    setWorkspaces(workspaces)
    return workspaces
  }

  async function refreshWorkspaces() {
    const response = await api.listWorkspaces()
    if (!response?.ok || !Array.isArray(response.data)) return []
    return publishWorkspaces(response.data)
  }

  async function ensureWorkspace() {
    if (!hasToken()) {
      throw new Error('请先登录后生成报告，报告文件需要保存到用户目录。')
    }
    const currentWorkspaces = getWorkspaces()
    let workspaces = Array.isArray(currentWorkspaces) ? currentWorkspaces : []
    let workspace = workspaces.find((item) => item.name === workspaceName)
    if (workspace) return workspace

    workspaces = await refreshWorkspaces()
    workspace = workspaces.find((item) => item.name === workspaceName)
    if (workspace) return workspace

    let response
    try {
      response = await api.createWorkspace({
        name: workspaceName,
        desc: '智能体报告固定存放目录',
      })
    } catch (error) {
      if (!duplicateWorkspace(error)) throw error
      workspaces = await refreshWorkspaces()
      workspace = workspaces.find((item) => item.name === workspaceName)
      if (workspace) return workspace
      throw error
    }
    if (response?.ok) {
      workspace = normalizeAssistantWorkspaceDto(response.data)
      publishWorkspaces([
        workspace,
        ...workspaces.filter((item) => item.name !== workspaceName),
      ])
      return workspace
    }
    if (duplicateWorkspace(response)) {
      workspaces = await refreshWorkspaces()
      workspace = workspaces.find((item) => item.name === workspaceName)
      if (workspace) return workspace
    }
    throw new Error(responseMessage(response, '创建 report 工作区失败'))
  }

  async function saveReport(content, topic, createdAt, formatKey) {
    await ensureWorkspace()
    const format = selectedReportFormat(formatKey)
    if (format.disabled) {
      throw new Error(`${format.label} 生成链路尚未接入，请先选择 Word 或 Markdown。`)
    }
    if (format.key === 'pptx') {
      throw new Error('PPT 需要先保存 Word 源稿，再交给 ppt-master 生成。')
    }
    let filename = buildReportFilename(topic, createdAt, format.extension || 'md')
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const response = format.key === 'docx'
        ? await api.exportWorkspaceDocx(workspaceName, { filename, title: topic, content })
        : await api.createWorkspaceFile(workspaceName, { filename, content })
      if (response?.ok) return filename
      if (!String(response?.error || '').includes('文件已存在')) {
        throw new Error(responseMessage(response, '保存报告文件失败'))
      }
      filename = buildReportFilename(
        `${topic}-${now()}`,
        createdAt,
        format.extension || 'md',
      )
    }
    throw new Error('保存报告文件失败')
  }

  async function saveTextFile(filename, content) {
    await ensureWorkspace()
    let target = filename
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const response = await api.createWorkspaceFile(workspaceName, {
        filename: target,
        content,
      })
      if (response?.ok) return target
      if (!String(response?.error || '').includes('文件已存在')) {
        throw new Error(responseMessage(response, '保存 ppt-master 任务文件失败'))
      }
      target = filename.replace(/\.md$/i, `-${now()}.md`)
    }
    throw new Error('保存 ppt-master 任务文件失败')
  }

  async function savePptMasterPackage(content, topic, createdAt) {
    const wordFilename = await saveReport(content, topic, createdAt, 'docx')
    const requestFilename = buildReportFilename(`${topic}-ppt-master任务`, createdAt, 'md')
    const requestContent = buildPptMasterRequestMarkdown({
      topic,
      createdAt,
      wordFilename,
      content,
      workspaceName,
      formatDateTime,
    })
    const pptRequestFileName = await saveTextFile(requestFilename, requestContent)
    return { wordFilename, pptRequestFileName }
  }

  return Object.freeze({
    ensureWorkspace,
    savePptMasterPackage,
    saveReport,
    saveTextFile,
    supportedFormats: REPORT_OUTPUT_FORMATS,
  })
}
