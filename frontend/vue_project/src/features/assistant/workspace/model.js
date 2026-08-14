export const TEXT_PREVIEW_EXTENSIONS = Object.freeze([
  '.txt',
  '.md',
  '.json',
  '.yaml',
  '.yml',
  '.csv',
  '.xml',
  '.log',
  '.py',
  '.js',
  '.ts',
  '.jsx',
  '.tsx',
  '.html',
  '.css',
  '.scss',
  '.less',
  '.sql',
  '.sh',
  '.ini',
  '.cfg',
  '.conf',
  '.toml',
])

export function normalizeAssistantWorkspaceDto(row) {
  const value = row && typeof row === 'object' ? row : {}
  return {
    ...value,
    name: String(value.name || ''),
    desc: String(value.desc || value.description || ''),
    pinned: Boolean(value.pinned),
    file_count: Number(value.file_count || value.files_count || 0),
  }
}

export function normalizeAssistantWorkspaceList(rows) {
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantWorkspaceDto)
}

export function normalizeWorkspaceFileList(rows) {
  return Array.isArray(rows) ? rows : []
}

export function workspaceChildPath(currentPath, childName) {
  return currentPath ? `${currentPath}/${childName}` : childName
}

export function workspaceParentPath(currentPath) {
  if (!currentPath) return ''
  const slashIndex = currentPath.lastIndexOf('/')
  return slashIndex === -1 ? '' : currentPath.slice(0, slashIndex)
}

export function workspaceFilePath(currentPath, fileOrName) {
  const name = typeof fileOrName === 'object' ? fileOrName?.name : fileOrName
  return workspaceChildPath(currentPath, name)
}

export function toggleWorkspaceFileSelection(selectedNames, filename) {
  const next = new Set(selectedNames)
  if (next.has(filename)) next.delete(filename)
  else next.add(filename)
  return next
}

export function toggleAllWorkspaceFiles(selectedNames, files) {
  const rows = Array.isArray(files) ? files : []
  if (selectedNames.size === rows.length) return new Set()
  return new Set(rows.map((file) => file.name))
}

export function selectedWorkspaceFilePaths(selectedNames, currentPath) {
  return [...selectedNames].map((name) => workspaceFilePath(currentPath, name))
}

export function isTextPreviewFile(filename) {
  const dotIndex = filename.lastIndexOf('.')
  if (dotIndex === -1) return false
  const extension = filename.slice(dotIndex).toLowerCase()
  return TEXT_PREVIEW_EXTENSIONS.includes(extension)
}

export function workspaceDownloadFilename(fullPath) {
  return String(fullPath).split('/').pop() || 'download'
}

export function workspaceArchiveFilename(workspaceName) {
  return `${workspaceName}.zip`
}

export function workspaceFilePreviewFromResponse(response) {
  if (response.ok) {
    const content = response.data.content
    return { content, originalContent: content }
  }
  const content = `// 无法读取文件: ${response.error}`
  return { content, originalContent: content }
}

export function workspaceFilePreviewFromError(error) {
  const content = `// 读取失败: ${error.message}`
  return { content, originalContent: content }
}
