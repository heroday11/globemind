export function isSentimentAbortError(error, signal) {
  return error?.name === 'AbortError' || Boolean(signal?.aborted)
}

export function createLatestSentimentRequest({
  AbortControllerImpl = globalThis.AbortController,
} = {}) {
  if (typeof AbortControllerImpl !== 'function') {
    throw new TypeError('Latest sentiment request requires AbortController')
  }
  let sequence = 0
  let active = null

  return Object.freeze({
    async run(executor) {
      if (typeof executor !== 'function') throw new TypeError('Request executor must be a function')
      active?.controller.abort()
      const id = ++sequence
      const controller = new AbortControllerImpl()
      active = { controller, id }
      try {
        const value = await executor(controller.signal)
        if (id !== sequence) return { latest: false, status: 'stale' }
        return { latest: true, status: 'success', value }
      } catch (error) {
        if (id !== sequence) return { error, latest: false, status: 'stale' }
        if (isSentimentAbortError(error, controller.signal)) {
          return { error, latest: true, status: 'aborted' }
        }
        return { error, latest: true, status: 'error' }
      } finally {
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
