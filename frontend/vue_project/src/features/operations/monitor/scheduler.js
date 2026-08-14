import {
  DEFAULT_FAST_REFRESH_INTERVAL_MS,
  FULL_REFRESH_INTERVAL_MS,
} from './model.js'

export function createPipelineMonitorRefreshScheduler({
  refreshFull,
  refreshFast,
  fullIntervalMs = FULL_REFRESH_INTERVAL_MS,
  setIntervalImpl = globalThis.setInterval.bind(globalThis),
  clearIntervalImpl = globalThis.clearInterval.bind(globalThis),
} = {}) {
  if (typeof refreshFull !== 'function' || typeof refreshFast !== 'function') {
    throw new TypeError('Pipeline monitor scheduler requires refresh callbacks')
  }

  let fullTimer = null
  let fastTimer = null

  function stop() {
    if (fullTimer !== null) clearIntervalImpl(fullTimer)
    if (fastTimer !== null) clearIntervalImpl(fastTimer)
    fullTimer = null
    fastTimer = null
  }

  function sync({ enabled, fastIntervalMs } = {}) {
    stop()
    if (!enabled) return
    const parsedFastInterval = Number(fastIntervalMs)
    const normalizedFastInterval = parsedFastInterval > 0
      ? parsedFastInterval
      : DEFAULT_FAST_REFRESH_INTERVAL_MS
    fullTimer = setIntervalImpl(refreshFull, fullIntervalMs)
    fastTimer = setIntervalImpl(refreshFast, normalizedFastInterval)
  }

  return Object.freeze({ stop, sync })
}
