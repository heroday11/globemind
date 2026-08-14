export {
  normalizeBriefingScheduleDto,
  normalizeBriefingScheduleList,
} from './briefing/model.js'
export {
  normalizeAssistantWorkspaceDto,
  normalizeAssistantWorkspaceList,
} from './workspace/model.js'

export function normalizeAssistantProviderDto(value) {
  if (value && typeof value === 'object') {
    const rawId = String(value.id || value.provider || value.name || 'default').trim()
    const id = rawId.toLowerCase()
    return {
      id: id || 'default',
      label: String(value.label || value.display_name || value.name || rawId || '默认助手'),
      model: String(value.model || value.model_name || ''),
      status: String(value.status || 'configured'),
    }
  }
  const id = String(value || 'default').trim().toLowerCase() || 'default'
  return {
    id,
    label: id === 'default' ? '默认助手' : String(value),
    model: '',
    status: 'configured',
  }
}

export function normalizeAssistantSessionDto(row, now = Date.now()) {
  const value = row && typeof row === 'object' ? row : {}
  const updatedAt = value.updated_at || value.updatedAt || ''
  const parsedAt = updatedAt ? new Date(updatedAt).getTime() : Number(value.at || now)
  return {
    ...value,
    id: String(value.id || ''),
    title: String(value.title || '会话'),
    at: Number.isFinite(parsedAt) ? parsedAt : now,
    updated_at: updatedAt,
    provider_info: normalizeAssistantProviderDto(value.provider || value.backend),
  }
}

export function normalizeAssistantMessageDto(row) {
  const value = row && typeof row === 'object' ? row : {}
  return {
    ...value,
    id: value.id ?? null,
    role: value.role === 'assistant' ? 'assistant' : 'user',
    content: String(value.content || ''),
    extra: value.extra && typeof value.extra === 'object' ? value.extra : {},
  }
}

export function normalizeAssistantSessionList(rows, now = Date.now()) {
  return (Array.isArray(rows) ? rows : []).map((row) => normalizeAssistantSessionDto(row, now))
}
