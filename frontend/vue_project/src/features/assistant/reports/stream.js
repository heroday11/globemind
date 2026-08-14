import { createRequestAbortScope, parseSseDataBlocks } from '../state.js'

export class ReportStreamError extends Error {
  constructor(message, { code = 'stream_error', cause } = {}) {
    super(message, { cause })
    this.name = 'ReportStreamError'
    this.code = code
  }
}

export function reportStreamEventError(event, fallback = '报告生成流式错误') {
  const detail = event?.detail
  if (typeof detail === 'string') return detail
  if (detail) return JSON.stringify(detail)
  return event?.msg || fallback
}

export function createReportStreamController(
  api,
  {
    timeoutMs = 300000,
    createAbortScope = createRequestAbortScope,
    parseEvents = parseSseDataBlocks,
    TextDecoderImpl = globalThis.TextDecoder,
  } = {},
) {
  if (!api || typeof api.openAssistantStream !== 'function') {
    throw new TypeError('Report stream controller requires the assistant stream API')
  }

  let activeRequest = null
  let requestSequence = 0
  let disposed = false

  function abort() {
    if (!activeRequest) return false
    activeRequest.scope.abort()
    return true
  }

  async function run({ requestBody, onEvent = () => {} }) {
    if (disposed) {
      throw new ReportStreamError('报告生成控制器已释放', { code: 'disposed' })
    }
    abort()
    const requestId = requestSequence + 1
    requestSequence = requestId
    const scope = createAbortScope(timeoutMs)
    activeRequest = { id: requestId, scope }
    let buffer = ''
    let streamedReply = ''
    let finalReply = ''
    let streamError = ''
    let reader = null

    const isCurrent = () => activeRequest?.id === requestId && !scope.signal.aborted
    const consume = (events) => {
      for (const event of events) {
        if (!isCurrent()) break
        if (event.step === 'text_delta' && event.text) streamedReply += event.text
        if (event.step === 'done' && event.reply != null) finalReply = String(event.reply)
        if (event.step === 'error') streamError = reportStreamEventError(event)
        onEvent(event)
      }
    }

    try {
      const response = await api.openAssistantStream(requestBody, {
        signal: scope.signal,
        fallbackMessage: '报告生成失败',
      })
      if (!response.body?.getReader) throw new Error('浏览器不支持流式读取')
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
        throw new ReportStreamError('报告生成已停止', { code: 'aborted' })
      }
      buffer += decoder.decode()
      if (buffer.trim()) consume(parseEvents(`${buffer}\n\n`).events)
      if (streamError) throw new ReportStreamError(streamError)
      return String(finalReply || streamedReply).trim()
    } catch (error) {
      if (scope.signal.aborted || activeRequest?.id !== requestId) {
        if (error instanceof ReportStreamError && error.code === 'aborted') throw error
        throw new ReportStreamError('报告生成已停止', { code: 'aborted', cause: error })
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
