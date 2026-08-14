export function isPipelineMonitorAbortError(error, signal) {
  return (
    error?.name === 'AbortError'
    || error?.name === 'CanceledError'
    || error?.code === 'ERR_CANCELED'
    || Boolean(signal?.aborted)
  )
}

export function createLatestPipelineMonitorRequest({
  AbortControllerImpl = globalThis.AbortController,
} = {}) {
  if (typeof AbortControllerImpl !== 'function') {
    throw new TypeError('Pipeline monitor requests require AbortController')
  }

  let sequence = 0
  let active = null

  return Object.freeze({
    async run(executor) {
      active?.controller.abort()
      const id = ++sequence
      const controller = new AbortControllerImpl()
      active = { id, controller }

      try {
        const value = await executor(controller.signal)
        if (id !== sequence) return { status: 'stale', latest: false }
        return { status: 'success', latest: true, value }
      } catch (error) {
        if (id !== sequence) return { status: 'stale', latest: false, error }
        if (isPipelineMonitorAbortError(error, controller.signal)) {
          return { status: 'aborted', latest: true, error }
        }
        return { status: 'error', latest: true, error }
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

export function pipelineMonitorErrorStatus(error) {
  return Number(error?.response?.status || error?.status || 0)
}

export function pipelineMonitorErrorMessage(error, fallback) {
  return error?.response?.data?.detail || error?.message || fallback
}
