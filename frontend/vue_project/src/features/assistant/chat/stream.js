import { createRequestAbortScope, parseSseDataBlocks } from '../state.js'

export class ChatStreamError extends Error {
  constructor(message, { code = 'stream_error', cause } = {}) {
    super(message, { cause })
    this.name = 'ChatStreamError'
    this.code = code
  }
}

export function isChatStreamAbortError(error) {
  return error instanceof ChatStreamError && error.code === 'aborted'
}

export function createChatStreamController(
  api,
  {
    timeoutMs = 240000,
    createAbortScope = createRequestAbortScope,
    parseEvents = parseSseDataBlocks,
    TextDecoderImpl = globalThis.TextDecoder,
  } = {},
) {
  if (!api || typeof api.openAssistantStream !== 'function') {
    throw new TypeError('Chat stream controller requires the assistant stream API')
  }

  let activeRequest = null
  let requestSequence = 0
  let disposed = false

  function abort() {
    if (!activeRequest) return false
    activeRequest.scope.abort()
    return true
  }

  async function run({ requestBody, onResponse = () => {}, onEvent = () => {} }) {
    if (disposed) {
      throw new ChatStreamError('聊天流控制器已释放', { code: 'disposed' })
    }
    abort()
    const requestId = requestSequence + 1
    requestSequence = requestId
    const scope = createAbortScope(timeoutMs)
    activeRequest = { id: requestId, scope }
    let buffer = ''
    let eventCount = 0
    let reader = null

    const isCurrent = () => activeRequest?.id === requestId && !scope.signal.aborted
    const consume = (events) => {
      for (const event of events) {
        if (!isCurrent()) break
        eventCount += 1
        onEvent(event)
      }
    }

    try {
      const response = await api.openAssistantStream(requestBody, { signal: scope.signal })
      if (!isCurrent()) {
        throw new ChatStreamError('聊天生成已停止', { code: 'aborted' })
      }
      onResponse(response)
      if (!response.body?.getReader) {
        throw new Error('浏览器不支持流式读取（ReadableStream）')
      }
      reader = response.body.getReader()
      const decoder = new TextDecoderImpl()
      while (isCurrent()) {
        let chunk
        try {
          chunk = await reader.read()
        } catch (error) {
          if (!isCurrent()) break
          throw error
        }
        if (chunk.done) break
        buffer += decoder.decode(chunk.value, { stream: true })
        const parsed = parseEvents(buffer)
        buffer = parsed.rest
        consume(parsed.events)
      }
      if (!isCurrent()) {
        throw new ChatStreamError('聊天生成已停止', { code: 'aborted' })
      }
      buffer += decoder.decode()
      if (buffer.trim()) consume(parseEvents(`${buffer}\n\n`).events)
      return { eventCount }
    } catch (error) {
      if (scope.signal.aborted || activeRequest?.id !== requestId) {
        if (isChatStreamAbortError(error)) throw error
        throw new ChatStreamError('聊天生成已停止', { code: 'aborted', cause: error })
      }
      throw error
    } finally {
      if (reader) {
        try {
          await reader.cancel()
        } catch {
          /* Ignore stream cancellation errors during cleanup. */
        }
      }
      scope.dispose()
      if (activeRequest?.id === requestId) activeRequest = null
    }
  }

  function dispose() {
    disposed = true
    abort()
  }

  return Object.freeze({
    abort,
    dispose,
    get isRunning() {
      return Boolean(activeRequest && !activeRequest.scope.signal.aborted)
    },
    run,
  })
}
