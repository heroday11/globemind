import {
  isTranslationLanguage,
  isTranslationSourceText,
  normalizeMachineTranslationResponse,
  sha256Text,
} from './model.js'

const MAX_RESPONSE_BYTES = 64 * 1024

function translationError(code) {
  const error = new Error(code)
  error.code = code
  return error
}

function assertStrictJsonTree(source) {
  let index = 0
  let nodes = 0

  function skipWhitespace() {
    while (' \n\r\t'.includes(source[index])) index += 1
  }

  function scanString({ decode = false } = {}) {
    const start = index
    index += 1
    while (index < source.length) {
      if (source[index] === '\\') index += 2
      else if (source[index] === '"') {
        index += 1
        return decode ? JSON.parse(source.slice(start, index)) : undefined
      } else index += 1
    }
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }

  function scanValue(depth = 0) {
    nodes += 1
    if (depth > 40 || nodes > 20_000) {
      throw translationError('TRANSLATION_RESPONSE_INVALID')
    }
    skipWhitespace()
    if (source[index] === '{') {
      index += 1
      skipWhitespace()
      const keys = new Set()
      if (source[index] === '}') {
        index += 1
        return
      }
      while (index < source.length) {
        skipWhitespace()
        const key = scanString({ decode: true })
        if (keys.has(key)) throw translationError('TRANSLATION_RESPONSE_INVALID')
        keys.add(key)
        skipWhitespace()
        index += 1
        scanValue(depth + 1)
        skipWhitespace()
        if (source[index] === '}') {
          index += 1
          return
        }
        index += 1
      }
      return
    }
    if (source[index] === '[') {
      index += 1
      skipWhitespace()
      if (source[index] === ']') {
        index += 1
        return
      }
      while (index < source.length) {
        scanValue(depth + 1)
        skipWhitespace()
        if (source[index] === ']') {
          index += 1
          return
        }
        index += 1
      }
      return
    }
    if (source[index] === '"') {
      scanString()
      return
    }
    while (index < source.length && !' \n\r\t,]}'.includes(source[index])) index += 1
  }

  scanValue()
}

async function readBoundedResponseUnsafe(response) {
  const contentType = String(response?.headers?.get?.('content-type') || '')
  const mediaType = contentType.split(';', 1)[0].trim().toLowerCase()
  if (mediaType !== 'application/json' && !mediaType.endsWith('+json')) {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }

  const contentLength = response.headers.get('content-length')
  if (contentLength !== null) {
    if (!/^\d+$/.test(contentLength.trim()) || Number(contentLength) > MAX_RESPONSE_BYTES) {
      throw translationError('TRANSLATION_RESPONSE_INVALID')
    }
  }

  const chunks = []
  let byteLength = 0
  if (response.body?.getReader) {
    const reader = response.body.getReader()
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!(value instanceof Uint8Array)) {
          throw translationError('TRANSLATION_RESPONSE_INVALID')
        }
        byteLength += value.byteLength
        if (byteLength > MAX_RESPONSE_BYTES) {
          await reader.cancel()
          throw translationError('TRANSLATION_RESPONSE_INVALID')
        }
        chunks.push(value)
      }
    } finally {
      reader.releaseLock()
    }
  } else {
    const value = new Uint8Array(await response.arrayBuffer())
    byteLength = value.byteLength
    if (byteLength > MAX_RESPONSE_BYTES) {
      throw translationError('TRANSLATION_RESPONSE_INVALID')
    }
    chunks.push(value)
  }
  if (!byteLength) throw translationError('TRANSLATION_RESPONSE_INVALID')

  const bytes = new Uint8Array(byteLength)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    const source = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    const parsed = JSON.parse(source)
    assertStrictJsonTree(source)
    return parsed
  } catch {
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }
}

async function readBoundedResponse(response) {
  try {
    return await readBoundedResponseUnsafe(response)
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    if (error?.code === 'TRANSLATION_RESPONSE_INVALID') throw error
    throw translationError('TRANSLATION_RESPONSE_INVALID')
  }
}

export async function requestMachineTranslation({
  apiPrefix,
  fetchImpl = fetch,
  sourceText,
  sourceLanguage = 'und',
  targetLanguage = 'zh-Hans',
  token,
  signal,
}) {
  if (!token) throw translationError('TRANSLATION_AUTH_REQUIRED')
  if (
    !isTranslationSourceText(sourceText)
    || !isTranslationLanguage(sourceLanguage)
    || targetLanguage !== 'zh-Hans'
  ) {
    throw translationError('TRANSLATION_WORKLOAD_LIMIT_EXCEEDED')
  }

  const sourceTextSha256 = await sha256Text(sourceText)
  let response
  try {
    response = await fetchImpl(`${apiPrefix}/dashboard/news/translate-paragraph`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        text: sourceText,
        source_language: sourceLanguage,
        target_language: targetLanguage,
      }),
      signal,
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw translationError('TRANSLATION_PROVIDER_UNAVAILABLE')
  }
  if (!response?.ok) throw translationError('TRANSLATION_PROVIDER_UNAVAILABLE')

  const payload = await readBoundedResponse(response)
  return normalizeMachineTranslationResponse(payload, {
    sourceText,
    sourceTextSha256,
    sourceLanguage,
    targetLanguage,
  })
}
