import {
  normalizeAssistantWorkspaceDto,
  normalizeAssistantWorkspaceList,
  normalizeWorkspaceFileList,
  workspaceArchiveFilename,
  workspaceDownloadFilename,
} from './model.js'

function failedResponse(response, fallback, { includeDetail = false } = {}) {
  const message = includeDetail
    ? response?.detail || response?.error || fallback
    : response?.error || fallback
  return new Error(message)
}

export function createAssistantWorkspaceService(api) {
  if (!api || typeof api !== 'object') {
    throw new TypeError('Assistant workspace service requires an API facade')
  }

  async function downloadFile(name, fullPath) {
    const response = await api.downloadWorkspaceFile(name, fullPath)
    return {
      blob: await response.blob(),
      filename: workspaceDownloadFilename(fullPath),
    }
  }

  return Object.freeze({
    async list() {
      const response = await api.listWorkspaces()
      if (!response?.ok) {
        throw failedResponse(response, '获取工作区失败', { includeDetail: true })
      }
      return normalizeAssistantWorkspaceList(response.data)
    },

    async create(name, desc) {
      const response = await api.createWorkspace({ name, desc })
      if (!response?.ok) throw failedResponse(response, '创建工作区失败')
      return normalizeAssistantWorkspaceDto(response.data)
    },

    async togglePinned(workspaces, workspace) {
      const nextPinned = !workspace.pinned
      const previousPins = workspaces.map((item) => [item, Boolean(item.pinned)])
      workspaces.forEach((item) => {
        item.pinned = false
      })
      try {
        const response = await api.updateWorkspace(workspace.name, { pinned: nextPinned })
        if (!response?.ok) throw failedResponse(response, '操作失败')
        workspace.pinned = nextPinned
        workspaces.sort((left, right) => (right.pinned ? 1 : 0) - (left.pinned ? 1 : 0))
        return workspace
      } catch (error) {
        previousPins.forEach(([item, pinned]) => {
          item.pinned = pinned
        })
        throw error
      }
    },

    async remove(name) {
      const response = await api.deleteWorkspace(name)
      if (!response?.ok) throw failedResponse(response, '删除失败')
      return response
    },

    async listFiles(name, subpath = '') {
      const response = await api.listWorkspaceFiles(name, subpath)
      if (!response?.ok) {
        throw failedResponse(response, '获取文件列表失败', { includeDetail: true })
      }
      return normalizeWorkspaceFileList(response.data)
    },

    readFile(name, fullPath) {
      return api.readWorkspaceFile(name, fullPath)
    },

    async updateFile(name, fullPath, content) {
      const response = await api.updateWorkspaceFile(name, fullPath, {
        filename: fullPath,
        content,
      })
      if (!response?.ok) throw failedResponse(response, '未知错误')
      return response
    },

    async createFile(name, filename, content = '') {
      const response = await api.createWorkspaceFile(name, { filename, content })
      if (!response?.ok) throw failedResponse(response, '创建文件失败')
      return response
    },

    async uploadFiles(name, files) {
      const list = Array.from(files || []).filter(Boolean)
      if (!list.length) return []
      const formData = new FormData()
      list.forEach((file) => formData.append('files', file))
      const response = await api.uploadWorkspaceFiles(name, formData)
      if (!response?.ok) {
        throw failedResponse(response, '上传文件失败', { includeDetail: true })
      }
      return Array.isArray(response.data) ? response.data : []
    },

    downloadFile,

    async downloadSelection(name, fullPaths) {
      if (!fullPaths.length) return null
      if (fullPaths.length === 1) return downloadFile(name, fullPaths[0])
      const response = await api.downloadWorkspaceZip(name, fullPaths)
      return {
        blob: await response.blob(),
        filename: workspaceArchiveFilename(name),
      }
    },
  })
}
