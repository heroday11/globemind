import {
  isTextPreviewFile,
  selectedWorkspaceFilePaths,
  toggleAllWorkspaceFiles,
  toggleWorkspaceFileSelection,
  workspaceChildPath,
  workspaceFilePath,
  workspaceFilePreviewFromError,
  workspaceFilePreviewFromResponse,
  workspaceParentPath,
} from './model.js'

export function createAssistantWorkspaceController({
  state,
  service,
  hasToken,
  showError,
  errorText,
  confirmDeleteWorkspace,
  confirmCloseDirtyPreview,
  focusCreateFileInput,
  triggerDownload,
}) {
  const {
    workspaces,
    workspaceLoading,
    workspaceError,
    currentWorkspace,
    currentFilePath,
    workspaceFiles,
    selectedFileNames,
    filesLoading,
    createDialogVisible,
    newWorkspaceName,
    newWorkspaceDescription,
    creatingWorkspace,
    createFileDialogVisible,
    newFileName,
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
  } = state

  async function togglePinWorkspace(workspace) {
    try {
      await service.togglePinned(workspaces.value, workspace)
    } catch (error) {
      showError(errorText(error), '操作失败')
    }
  }

  async function createWorkspace() {
    if (!hasToken()) {
      showError('请先登录后创建工作区')
      return
    }
    const name = newWorkspaceName.value.trim()
    if (!name) return
    creatingWorkspace.value = true
    try {
      const workspace = await service.create(name, newWorkspaceDescription.value.trim())
      workspaces.value.unshift(workspace)
      createDialogVisible.value = false
      newWorkspaceName.value = ''
      newWorkspaceDescription.value = ''
    } catch (error) {
      showError(errorText(error), '创建工作区失败')
    } finally {
      creatingWorkspace.value = false
    }
  }

  async function fetchWorkspaces() {
    workspaceError.value = ''
    if (!hasToken()) {
      workspaces.value = []
      workspaceError.value = '请先登录后查看和管理工作区。'
      return
    }
    workspaceLoading.value = true
    try {
      workspaces.value = await service.list()
    } catch (error) {
      workspaceError.value = errorText(error, '获取工作区失败')
      console.error('获取工作区失败', error)
    } finally {
      workspaceLoading.value = false
    }
  }

  async function deleteWorkspace(workspace) {
    if (!(await confirmDeleteWorkspace(workspace))) return
    try {
      await service.remove(workspace.name)
      workspaces.value = workspaces.value.filter((item) => item.name !== workspace.name)
    } catch (error) {
      showError(errorText(error), '删除失败')
    }
  }

  async function loadWorkspaceFiles(name, subpath = '') {
    if (!hasToken()) {
      showError('请先登录后查看工作区文件')
      return
    }
    filesLoading.value = true
    try {
      workspaceFiles.value = await service.listFiles(name, subpath)
    } catch (error) {
      showError(errorText(error), '获取文件列表失败')
      console.error('获取文件列表失败', error)
    } finally {
      filesLoading.value = false
    }
  }

  async function enterWorkspace(workspace) {
    currentWorkspace.value = workspace
    currentFilePath.value = ''
    selectedFileNames.value = new Set()
    await loadWorkspaceFiles(workspace.name, '')
  }

  function leaveWorkspace() {
    currentWorkspace.value = null
    currentFilePath.value = ''
    workspaceFiles.value = []
    selectedFileNames.value = new Set()
  }

  function enterWorkspaceFolder(folderName) {
    const nextPath = workspaceChildPath(currentFilePath.value, folderName)
    currentFilePath.value = nextPath
    selectedFileNames.value = new Set()
    loadWorkspaceFiles(currentWorkspace.value.name, nextPath)
  }

  function goBackToParentFolder() {
    if (!currentFilePath.value) return
    currentFilePath.value = workspaceParentPath(currentFilePath.value)
    selectedFileNames.value = new Set()
    loadWorkspaceFiles(currentWorkspace.value.name, currentFilePath.value)
  }

  function fileFullPath(file) {
    return workspaceFilePath(currentFilePath.value, file)
  }

  function toggleFileSelection(filename) {
    selectedFileNames.value = toggleWorkspaceFileSelection(selectedFileNames.value, filename)
  }

  function selectAllFiles() {
    selectedFileNames.value = toggleAllWorkspaceFiles(
      selectedFileNames.value,
      workspaceFiles.value,
    )
  }

  async function downloadWorkspaceFile(fullPath) {
    if (!currentWorkspace.value || !fullPath) return
    try {
      const download = await service.downloadFile(currentWorkspace.value.name, fullPath)
      triggerDownload(download.blob, download.filename)
    } catch (error) {
      showError(errorText(error), '下载失败')
    }
  }

  async function downloadSelected() {
    const fullPaths = selectedWorkspaceFilePaths(
      selectedFileNames.value,
      currentFilePath.value,
    )
    if (!fullPaths.length) return
    try {
      const download = await service.downloadSelection(currentWorkspace.value.name, fullPaths)
      if (download) triggerDownload(download.blob, download.filename)
    } catch (error) {
      showError(errorText(error), '下载失败')
    }
  }

  async function openFilePreview(file) {
    if (file.is_dir || !isTextPreviewFile(file.name)) return
    filePreviewVisible.value = true
    filePreviewTitle.value = file.name
    filePreviewFullPath.value = fileFullPath(file)
    filePreviewContent.value = ''
    fileOriginalContent.value = ''
    filePreviewLoading.value = true
    try {
      const response = await service.readFile(
        currentWorkspace.value.name,
        filePreviewFullPath.value,
      )
      const preview = workspaceFilePreviewFromResponse(response)
      filePreviewContent.value = preview.content
      fileOriginalContent.value = preview.originalContent
    } catch (error) {
      const preview = workspaceFilePreviewFromError(error)
      filePreviewContent.value = preview.content
      fileOriginalContent.value = preview.originalContent
    } finally {
      filePreviewLoading.value = false
    }
  }

  async function closeFilePreview() {
    if (
      filePreviewContent.value !== fileOriginalContent.value
      && !(await confirmCloseDirtyPreview())
    ) {
      return
    }
    filePreviewVisible.value = false
    filePreviewTitle.value = ''
    filePreviewFullPath.value = ''
    filePreviewContent.value = ''
    fileOriginalContent.value = ''
  }

  async function saveFileContent() {
    const name = currentWorkspace.value?.name
    const fullPath = filePreviewFullPath.value
    if (!name || !fullPath) return
    fileSaving.value = true
    try {
      await service.updateFile(name, fullPath, filePreviewContent.value)
      fileOriginalContent.value = filePreviewContent.value
    } catch (error) {
      showError(errorText(error), '保存失败')
    } finally {
      fileSaving.value = false
    }
  }

  async function createWorkspaceFile() {
    if (!hasToken()) {
      showError('请先登录后创建文件')
      return
    }
    const name = currentWorkspace.value?.name
    const rawName = newFileName.value.trim()
    if (!name || !rawName) return
    const filename = workspaceFilePath(currentFilePath.value, rawName)
    creatingFile.value = true
    try {
      await service.createFile(name, filename)
      await loadWorkspaceFiles(name, currentFilePath.value)
      createFileDialogVisible.value = false
      newFileName.value = ''
    } catch (error) {
      showError(errorText(error), '创建文件失败')
    } finally {
      creatingFile.value = false
    }
  }

  function openCreateFileDialog() {
    newFileName.value = ''
    createFileDialogVisible.value = true
    focusCreateFileInput()
  }

  function openUploadDialog() {
    if (!hasToken()) {
      showError('请先登录后上传文件')
      return
    }
    if (!currentWorkspace.value) {
      showError('请先进入一个工作区')
      return
    }
    if (currentFilePath.value) {
      showError('当前仅支持上传到工作区根目录，请返回根目录后上传')
      return
    }
    uploadInputEl.value?.click()
  }

  async function uploadWorkspaceFiles(event) {
    const input = event?.target
    const files = Array.from(input?.files || []).filter(Boolean)
    if (!files.length) return
    const name = currentWorkspace.value?.name
    if (!name) return
    uploadingFiles.value = true
    try {
      await service.uploadFiles(name, files)
      await loadWorkspaceFiles(name, currentFilePath.value)
    } catch (error) {
      showError(errorText(error), '上传文件失败')
    } finally {
      uploadingFiles.value = false
      if (input) input.value = ''
    }
  }

  return Object.freeze({
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
  })
}
