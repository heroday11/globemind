export const CHAT_STREAM_ERROR_FALLBACK = 'CC 流式错误'

export function createChatStreamMessage(overrides = {}) {
  return {
    role: 'assistant',
    text: '',
    thinking: '',
    showDetailChip: false,
    storedFullReply: '',
    finishReason: '',
    truncated: false,
    detailIndex: 1,
    toolCalls: [],
    ...overrides,
  }
}

export function createChatStreamState({
  message = createChatStreamMessage(),
  fullReply = '',
  error = '',
  newsHits = [],
  clusterHits = [],
} = {}) {
  return {
    message: {
      ...message,
      text: String(message?.text || ''),
      thinking: String(message?.thinking || ''),
      storedFullReply: String(message?.storedFullReply || ''),
      finishReason: String(message?.finishReason || ''),
      truncated: Boolean(message?.truncated),
      toolCalls: Array.isArray(message?.toolCalls) ? [...message.toolCalls] : [],
      sources: Array.isArray(message?.sources) ? [...message.sources] : [],
    },
    fullReply: String(fullReply || ''),
    error: String(error || ''),
    newsHits: Array.isArray(newsHits) ? newsHits : [],
    clusterHits: Array.isArray(clusterHits) ? clusterHits : [],
  }
}

export function chatStreamEventError(event, fallback = CHAT_STREAM_ERROR_FALLBACK) {
  const detail = event?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return event?.msg || fallback
}

export function reduceChatStreamEvent(
  state,
  event,
  {
    normalizeWebSourceGroups = () => [],
    sourceGroupsFromToolResult = () => [],
    mergeSourceGroups = (existing, incoming) => [...existing, ...incoming],
  } = {},
) {
  const current = createChatStreamState(state)
  const next = {
    ...current,
    message: { ...current.message },
  }
  const effects = {
    contextUpdated: false,
    pageActionPhase: '',
    sessionId: null,
    shouldScroll: false,
  }

  if (event?.step === 'context') {
    next.newsHits = Array.isArray(event.news_hits) ? event.news_hits : []
    next.clusterHits = Array.isArray(event.cluster_hits) ? event.cluster_hits : []
    effects.contextUpdated = true
    if (Array.isArray(event.web_sources) && event.web_sources.length) {
      next.message.sources = mergeSourceGroups(
        current.message.sources,
        normalizeWebSourceGroups(event.web_sources),
      )
    }
    if (event.session_id != null) effects.sessionId = event.session_id
  } else if (event?.step === 'text_delta' && event.text) {
    const delta = String(event.text)
    next.fullReply += delta
    next.message.text += delta
    effects.shouldScroll = true
  } else if (event?.step === 'thinking_delta' && event.text) {
    next.message.thinking += String(event.text)
  } else if (event?.step === 'tool_executing') {
    next.message.toolCalls = [
      ...current.message.toolCalls,
      {
        type: 'tool_executing',
        name: event.tool || 'tool',
        input: event.input || {},
        invoke: event.invoke || {},
      },
    ]
    effects.pageActionPhase = 'executing'
    effects.shouldScroll = true
  } else if (event?.step === 'tool_finished') {
    const toolCalls = current.message.toolCalls.map((tool) => ({ ...tool }))
    const index = toolCalls.map((tool) => tool.name).lastIndexOf(event.tool)
    if (index >= 0 && toolCalls[index].type === 'tool_executing') {
      toolCalls[index] = {
        ...toolCalls[index],
        type: 'tool_finished',
        result: event.result || {},
      }
    } else {
      toolCalls.push({
        type: 'tool_finished',
        name: event.tool || '',
        result: event.result || {},
        invoke: {},
        input: {},
      })
    }
    next.message.toolCalls = toolCalls
    const groups = sourceGroupsFromToolResult(event.tool, event.result)
    if (groups.length) {
      next.message.sources = mergeSourceGroups(current.message.sources, groups)
    }
    effects.pageActionPhase = 'finished'
  } else if (event?.step === 'done' && event.reply != null) {
    const finalReply = String(event.reply)
    next.fullReply = finalReply
    next.message.storedFullReply = finalReply
    if (finalReply.length >= next.message.text.length) next.message.text = finalReply
    next.message.finishReason = String(event.finish_reason || '')
    next.message.truncated = Boolean(event.truncated || event.finish_reason === 'length')
  } else if (event?.step === 'error') {
    next.error = chatStreamEventError(event)
  }

  return { effects, state: next }
}

export function finalizeChatStreamState(state) {
  const next = createChatStreamState(state)
  if (next.error) {
    next.message.text = next.message.text
      ? `${next.message.text}\n\n【错误】${next.error}`
      : `【错误】${next.error}`
    next.message.storedFullReply = next.message.text
  } else if (!next.message.storedFullReply && next.fullReply) {
    next.message.storedFullReply = next.fullReply
  }
  return next
}
