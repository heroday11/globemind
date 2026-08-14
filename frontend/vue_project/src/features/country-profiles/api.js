import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'

import { sanitizeCountryProfileCatalog } from './model.js'

export const COUNTRY_PROFILE_CATALOG_ENDPOINT = (
  `${API_PREFIX}/authoritative-data/country-profiles/catalog`
)
export const COUNTRY_PROFILE_CATALOG_MAX_BYTES = 256 * 1024
const GENERIC_FAILURE = '国家档案目录暂不可用，请稍后重试。'

export class CountryProfileCatalogApiError extends Error {
  constructor({ status = 0, code = 'REQUEST_FAILED' } = {}) {
    super(GENERIC_FAILURE)
    this.name = 'CountryProfileCatalogApiError'
    this.status = status
    this.code = code
  }
}

function apiError(response, code) {
  return new CountryProfileCatalogApiError({
    status: Number(response?.status || 0),
    code,
  })
}

function mediaType(response) {
  return String(response?.headers?.get?.('content-type') || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase()
}

function assertJsonMediaType(response) {
  const type = mediaType(response)
  if (type !== 'application/json' && !type.endsWith('+json')) {
    throw apiError(response, 'INVALID_RESPONSE_TYPE')
  }
}

function declaredLength(response) {
  const raw = String(response?.headers?.get?.('content-length') || '').trim()
  if (!raw) return null
  if (!/^\d{1,12}$/.test(raw)) throw apiError(response, 'INVALID_RESPONSE_LENGTH')
  const value = Number(raw)
  if (!Number.isSafeInteger(value)) throw apiError(response, 'INVALID_RESPONSE_LENGTH')
  return value
}

async function boundedResponseText(response) {
  const length = declaredLength(response)
  if (length !== null && length > COUNTRY_PROFILE_CATALOG_MAX_BYTES) {
    throw apiError(response, 'RESPONSE_TOO_LARGE')
  }

  const reader = response?.body?.getReader?.()
  if (reader) {
    const chunks = []
    let total = 0
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (!(value instanceof Uint8Array)) throw apiError(response, 'INVALID_RESPONSE_BODY')
        total += value.byteLength
        if (total > COUNTRY_PROFILE_CATALOG_MAX_BYTES) {
          await reader.cancel().catch(() => {})
          throw apiError(response, 'RESPONSE_TOO_LARGE')
        }
        chunks.push(value)
      }
    } finally {
      reader.releaseLock?.()
    }
    const joined = new Uint8Array(total)
    let offset = 0
    for (const chunk of chunks) {
      joined.set(chunk, offset)
      offset += chunk.byteLength
    }
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(joined)
    } catch {
      throw apiError(response, 'INVALID_RESPONSE_ENCODING')
    }
  }

  if (typeof response?.text !== 'function') throw apiError(response, 'INVALID_RESPONSE_BODY')
  const text = await response.text()
  if (new TextEncoder().encode(text).byteLength > COUNTRY_PROFILE_CATALOG_MAX_BYTES) {
    throw apiError(response, 'RESPONSE_TOO_LARGE')
  }
  return text
}

function assertNoDuplicateJsonKeys(source) {
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
    throw new Error('unterminated string')
  }

  function scanValue(depth = 0) {
    nodes += 1
    if (depth > 40 || nodes > 20_000) throw new Error('JSON tree exceeds bounds')
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
        if (keys.has(key)) throw new Error('duplicate JSON key')
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

async function strictPayload(response) {
  assertJsonMediaType(response)
  let source
  try {
    source = await boundedResponseText(response)
  } catch (error) {
    if (error instanceof CountryProfileCatalogApiError) throw error
    throw apiError(response, 'INVALID_RESPONSE_BODY')
  }
  let payload
  try {
    payload = JSON.parse(source)
    assertNoDuplicateJsonKeys(source)
  } catch {
    throw apiError(response, 'INVALID_JSON_RESPONSE')
  }
  try {
    return sanitizeCountryProfileCatalog(payload)
  } catch {
    throw apiError(response, 'INVALID_RESPONSE_CONTRACT')
  }
}

export function createCountryProfileCatalogApi({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  apiOrigin = resolveApiOrigin(),
  endpoint = COUNTRY_PROFILE_CATALOG_ENDPOINT,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Country profile catalog API requires fetch')

  return Object.freeze({
    async fetchCatalog({ signal } = {}) {
      let response
      try {
        response = await fetchImpl(`${apiOrigin}${endpoint}`, {
          method: 'GET',
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          cache: 'no-store',
          signal,
        })
      } catch (error) {
        if (error?.name === 'AbortError' || signal?.aborted) throw error
        throw new CountryProfileCatalogApiError({ code: 'NETWORK_ERROR' })
      }
      if (response?.ok !== true) throw apiError(response, 'REQUEST_FAILED')
      return strictPayload(response)
    },
  })
}

export const countryProfileCatalogApi = createCountryProfileCatalogApi()
