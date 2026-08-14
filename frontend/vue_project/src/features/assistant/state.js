export function safeParseJson(raw, fallback) {
  try {
    const parsed = JSON.parse(raw)
    return parsed ?? fallback
  } catch {
    return fallback
  }
}

export function normalizeFolderName(name, defaultName = '默认工作夹') {
  return String(name || '').trim() || defaultName
}

export function displayFolderName(
  name,
  { defaultName = '默认工作夹', legacyName = '新闻分析主题' } = {},
) {
  const normalized = normalizeFolderName(name, defaultName)
  return normalized === legacyName ? defaultName : normalized
}

export function formatAssistantDateTime(value) {
  if (!value) return '暂无更新'
  try {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return String(value)
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return String(value)
  }
}

export function formatConversationTime(timestamp, now = Date.now()) {
  if (!timestamp) return ''
  const diff = now - timestamp
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
  return new Date(timestamp).toLocaleDateString('zh-CN')
}

export function parseSseDataBlocks(buffer) {
  const events = []
  let rest = buffer
  let separator
  while ((separator = rest.indexOf('\n\n')) >= 0) {
    const raw = rest.slice(0, separator)
    rest = rest.slice(separator + 2)
    const dataLines = []
    for (const line of raw.split('\n')) {
      if (line.startsWith('data:')) {
        dataLines.push(line.startsWith('data: ') ? line.slice(6) : line.slice(5).trimStart())
      }
    }
    const data = dataLines.join('\n').trim()
    if (!data) continue
    try {
      events.push(JSON.parse(data))
    } catch {
      // Ignore non-JSON SSE frames.
    }
  }
  return { rest, events }
}

export function createRequestAbortScope(
  timeoutMs,
  {
    AbortControllerImpl = globalThis.AbortController,
    setTimeoutImpl = globalThis.setTimeout,
    clearTimeoutImpl = globalThis.clearTimeout,
  } = {},
) {
  const controller = new AbortControllerImpl()
  const timeoutId = setTimeoutImpl(() => controller.abort(), timeoutMs)
  return {
    controller,
    signal: controller.signal,
    abort() {
      controller.abort()
    },
    dispose() {
      clearTimeoutImpl(timeoutId)
    },
  }
}

export function isAssistantAbortError(error, signal) {
  return error?.name === 'AbortError' || Boolean(signal?.aborted)
}

export function normalizePageActionQuery(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .join(' ')
  }
  if (value == null) return ''
  return String(value).replace(/\s+/g, ' ').trim()
}

export function pickSearchQueryFromPayload(payload) {
  const keys = ['query', 'keyword', 'keywords', 'q', 'topic', 'search_query', 'query_preview']
  const bags = [
    payload?.input,
    payload?.invoke,
    payload?.args,
    payload?.arguments,
    payload?.result?.params,
    payload?.result?.request,
    payload?.result,
    payload,
  ]
  for (const bag of bags) {
    if (!bag || typeof bag !== 'object') continue
    for (const key of keys) {
      const query = normalizePageActionQuery(bag[key])
      if (query) return query
    }
  }
  return ''
}
