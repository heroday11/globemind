export function isSearchAbortError(error, signal) {
  return error?.name === 'AbortError' || Boolean(signal?.aborted)
}

export const SEARCH_DATA_OPERATION_KINDS = Object.freeze({
  LIST: 'list',
  SEARCH: 'search',
})

export const SEARCH_REQUEST_TIMEOUT_MS = 8_000

export class SearchRequestTimeoutError extends Error {
  constructor(timeoutMs = SEARCH_REQUEST_TIMEOUT_MS) {
    super(`搜索超过 ${Math.round(timeoutMs / 1000)} 秒，已停止本次查询。请缩短时间范围或增加限定词`)
    this.name = 'SearchRequestTimeoutError'
    this.timeoutMs = timeoutMs
  }
}

function freezeRecord(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`)
  }
  return Object.freeze({ ...value })
}

export function createSearchListOperation({ query, origin }) {
  return Object.freeze({
    kind: SEARCH_DATA_OPERATION_KINDS.LIST,
    query: freezeRecord(query, 'Search list query'),
    origin: String(origin || ''),
  })
}

export function createSearchQueryOperation({ params, requestParams, searchType }) {
  return Object.freeze({
    kind: SEARCH_DATA_OPERATION_KINDS.SEARCH,
    params: freezeRecord(params, 'Search request payload'),
    requestParams: freezeRecord(requestParams, 'Search request snapshot'),
    searchType: String(searchType || 'news'),
  })
}

export function createSearchOperationRunner({ api, request }) {
  if (!api || typeof api.listNews !== 'function' || typeof api.search !== 'function') {
    throw new TypeError('Search operation runner requires an API facade')
  }
  if (!request || typeof request.run !== 'function') {
    throw new TypeError('Search operation runner requires a request coordinator')
  }

  let failedOperation = null

  async function execute(operation) {
    if (!operation || !Object.values(SEARCH_DATA_OPERATION_KINDS).includes(operation.kind)) {
      throw new TypeError('Unknown search data operation')
    }
    const outcome = await request.run((signal) => {
      if (operation.kind === SEARCH_DATA_OPERATION_KINDS.LIST) {
        return api.listNews(operation.query, { signal, origin: operation.origin })
      }
      return api.search(operation.params, { signal })
    })
    if (outcome.latest && ['error', 'timeout'].includes(outcome.status)) failedOperation = operation
    if (outcome.latest && outcome.status === 'success') failedOperation = null
    return { ...outcome, operation }
  }

  return Object.freeze({
    execute,
    retry() {
      if (!failedOperation) {
        return Promise.resolve({ status: 'idle', latest: true, operation: null })
      }
      return execute(failedOperation)
    },
    getFailedOperation() {
      return failedOperation
    },
  })
}

export function createLatestSearchRequest({
  AbortControllerImpl = globalThis.AbortController,
  timeoutMs = SEARCH_REQUEST_TIMEOUT_MS,
  setTimeoutImpl = globalThis.setTimeout,
  clearTimeoutImpl = globalThis.clearTimeout,
} = {}) {
  let sequence = 0
  let active = null

  return Object.freeze({
    async run(executor) {
      if (active) active.controller.abort()
      const id = ++sequence
      const controller = new AbortControllerImpl()
      active = { id, controller }
      let timedOut = false
      const timeoutId = setTimeoutImpl(() => {
        timedOut = true
        controller.abort()
      }, timeoutMs)
      try {
        const value = await executor(controller.signal)
        if (id !== sequence) return { status: 'stale', latest: false }
        return { status: 'success', latest: true, value }
      } catch (error) {
        if (id !== sequence) return { status: 'stale', latest: false, error }
        if (timedOut) {
          return {
            status: 'timeout',
            latest: true,
            error: new SearchRequestTimeoutError(timeoutMs),
          }
        }
        if (isSearchAbortError(error, controller.signal)) {
          return { status: 'aborted', latest: true, error }
        }
        return { status: 'error', latest: true, error }
      } finally {
        clearTimeoutImpl(timeoutId)
        if (active?.id === id) active = null
      }
    },

    cancel() {
      sequence += 1
      active?.controller.abort()
      active = null
    },

    isActive() {
      return active !== null
    },
  })
}
