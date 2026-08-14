const HEARTBEAT_ENDPOINT = '/api/ops/heartbeat'
const HEARTBEAT_CLIENT_KEY = 'globemind_ops_client_id'
const HEARTBEAT_INTERVAL_MS = 30_000
const HEARTBEAT_BURST_DELAY_MS = 250
const HEARTBEAT_MAX_RETRY_AFTER_MS = 5 * 60_000

function defaultStorage() {
  try {
    return globalThis.localStorage
  } catch {
    return null
  }
}

function defaultEventTarget() {
  try {
    return globalThis.document
  } catch {
    return null
  }
}

function defaultFetch() {
  return typeof globalThis.fetch === 'function' ? globalThis.fetch.bind(globalThis) : null
}

function normalizeDelay(value, fallback, minimum = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(minimum, parsed) : fallback
}

function normalizeClientId(value) {
  const normalized = typeof value === 'string' ? value.trim() : ''
  return normalized.length >= 8
    && normalized.length <= 128
    && /^[A-Za-z0-9_.:-]+$/.test(normalized)
    ? normalized
    : ''
}

function createClientId({ randomUUID, now, random }) {
  try {
    const generated = normalizeClientId(randomUUID?.())
    if (generated) return generated
  } catch {
    // Fall through to a locally generated identifier.
  }
  return `client-${Math.trunc(now())}-${random().toString(16).slice(2)}`.slice(0, 128)
}

function getHeartbeatClientId({ storage, randomUUID, now, random }) {
  try {
    const stored = normalizeClientId(storage?.getItem?.(HEARTBEAT_CLIENT_KEY))
    if (stored) return stored
  } catch {
    // Storage can be unavailable in privacy-restricted browsing contexts.
  }

  const generated = createClientId({ randomUUID, now, random })
  try {
    storage?.setItem?.(HEARTBEAT_CLIENT_KEY, generated)
  } catch {
    // A stable in-memory value is still retained by the controller.
  }
  return generated
}

function retryAfterMs(response, now, fallbackMs, maximumMs) {
  let raw = ''
  try {
    raw = response?.headers?.get?.('retry-after')?.trim() || ''
  } catch {
    return fallbackMs
  }

  const seconds = Number(raw)
  if (raw && Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(maximumMs, seconds * 1000)
  }

  const timestamp = Date.parse(raw)
  if (raw && Number.isFinite(timestamp)) {
    return Math.min(maximumMs, Math.max(0, timestamp - now()))
  }
  return fallbackMs
}

function safeValue(getter, fallback) {
  try {
    const value = getter()
    return typeof value === 'string' && value ? value : fallback
  } catch {
    return fallback
  }
}

function normalizeHeartbeatPath(value) {
  const raw = typeof value === 'string' ? value.trim() : ''
  const path = raw.split(/[?#]/, 1)[0]
  if (!path.startsWith('/')) return '/'
  const printablePath = [...path]
    .filter((character) => {
      const codePoint = character.codePointAt(0)
      return codePoint > 31 && codePoint !== 127
    })
    .join('')
  return printablePath.slice(0, 256) || '/'
}

function normalizeVisibility(value) {
  return ['visible', 'hidden', 'prerender'].includes(value) ? value : 'visible'
}

export function createOperationsHeartbeat(options = {}) {
  const fetchImpl = Object.hasOwn(options, 'fetchImpl') ? options.fetchImpl : defaultFetch()
  const storage = Object.hasOwn(options, 'storage') ? options.storage : defaultStorage()
  const eventTarget = Object.hasOwn(options, 'eventTarget')
    ? options.eventTarget
    : defaultEventTarget()
  const setTimeoutImpl = options.setTimeoutImpl || globalThis.setTimeout.bind(globalThis)
  const clearTimeoutImpl = options.clearTimeoutImpl || globalThis.clearTimeout.bind(globalThis)
  const setIntervalImpl = options.setIntervalImpl || globalThis.setInterval.bind(globalThis)
  const clearIntervalImpl = options.clearIntervalImpl || globalThis.clearInterval.bind(globalThis)
  const now = options.now || Date.now
  const random = options.random || Math.random
  const randomUUID = Object.hasOwn(options, 'randomUUID')
    ? options.randomUUID
    : globalThis.crypto?.randomUUID?.bind(globalThis.crypto)
  const endpoint = options.endpoint || HEARTBEAT_ENDPOINT
  const intervalMs = normalizeDelay(options.intervalMs, HEARTBEAT_INTERVAL_MS, 1)
  const burstDelayMs = normalizeDelay(options.burstDelayMs, HEARTBEAT_BURST_DELAY_MS)
  const maximumRetryAfterMs = normalizeDelay(
    options.maximumRetryAfterMs,
    HEARTBEAT_MAX_RETRY_AFTER_MS,
    1,
  )
  const getPath = options.getPath || (() => '/')
  const getVisibility =
    options.getVisibility || (() => eventTarget?.visibilityState || 'visible')

  let running = false
  let generation = 0
  let pending = false
  let pendingTimer = null
  let intervalTimer = null
  let activeRequest = null
  let backoffUntil = 0
  let clientId = ''

  function clearPendingTimer() {
    if (pendingTimer === null) return
    clearTimeoutImpl(pendingTimer)
    pendingTimer = null
  }

  function schedulePending(delayMs = burstDelayMs) {
    if (!running || !pending) return
    clearPendingTimer()
    const backoffRemaining = Math.max(0, backoffUntil - now())
    pendingTimer = setTimeoutImpl(flushPending, Math.max(delayMs, backoffRemaining))
  }

  function payload() {
    if (!clientId) {
      clientId = getHeartbeatClientId({ storage, randomUUID, now, random })
    }
    return {
      client_id: clientId,
      path: normalizeHeartbeatPath(safeValue(getPath, '/')),
      visibility: normalizeVisibility(safeValue(getVisibility, 'visible')),
    }
  }

  function finishRequest(token, requestGeneration) {
    if (activeRequest === token) activeRequest = null
    if (running && generation === requestGeneration && pending) schedulePending()
  }

  function sendPending() {
    if (typeof fetchImpl !== 'function') return
    const requestGeneration = generation
    const token = {}
    activeRequest = token

    let request
    try {
      request = fetchImpl(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
        keepalive: true,
      })
    } catch {
      finishRequest(token, requestGeneration)
      return
    }

    Promise.resolve(request)
      .then((response) => {
        if (!running || generation !== requestGeneration || response?.status !== 429) return
        const delayMs = retryAfterMs(
          response,
          now,
          intervalMs,
          maximumRetryAfterMs,
        )
        backoffUntil = Math.max(backoffUntil, now() + delayMs)
        pending = true
      })
      .catch(() => {})
      .finally(() => finishRequest(token, requestGeneration))
  }

  function flushPending() {
    pendingTimer = null
    if (!running || !pending) return
    if (now() < backoffUntil) {
      schedulePending(0)
      return
    }
    if (activeRequest) return
    pending = false
    sendPending()
  }

  function queueHeartbeat() {
    if (!running) return
    pending = true
    schedulePending()
  }

  function handleVisibilityChange() {
    queueHeartbeat()
  }

  function start() {
    if (running) return
    running = true
    generation += 1
    backoffUntil = 0
    eventTarget?.addEventListener?.('visibilitychange', handleVisibilityChange)
    intervalTimer = setIntervalImpl(queueHeartbeat, intervalMs)
    queueHeartbeat()
  }

  function stop() {
    if (!running) return
    running = false
    generation += 1
    pending = false
    backoffUntil = 0
    clearPendingTimer()
    if (intervalTimer !== null) {
      clearIntervalImpl(intervalTimer)
      intervalTimer = null
    }
    eventTarget?.removeEventListener?.('visibilitychange', handleVisibilityChange)
    activeRequest = null
  }

  return Object.freeze({
    notifyRouteChange: queueHeartbeat,
    start,
    stop,
  })
}

export {
  HEARTBEAT_BURST_DELAY_MS,
  HEARTBEAT_CLIENT_KEY,
  HEARTBEAT_ENDPOINT,
  HEARTBEAT_INTERVAL_MS,
  normalizeHeartbeatPath,
}
