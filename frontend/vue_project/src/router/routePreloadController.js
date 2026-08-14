const DEFAULT_DELAY_MS = 120

export function createRoutePreloadController(
  loaders,
  {
    delay = DEFAULT_DELAY_MS,
    setTimer = globalThis.setTimeout,
    clearTimer = globalThis.clearTimeout,
  } = {},
) {
  const loadedOrLoading = new Set()
  const scheduled = new Map()
  let active = true

  function cancelScheduled(path) {
    if (!scheduled.has(path)) return
    clearTimer(scheduled.get(path))
    scheduled.delete(path)
  }

  async function loadNow(path) {
    cancelScheduled(path)
    const loader = loaders?.[path]
    if (!active || typeof loader !== 'function' || loadedOrLoading.has(path)) return false
    loadedOrLoading.add(path)
    try {
      await loader()
      return true
    } catch {
      loadedOrLoading.delete(path)
      return false
    }
  }

  function schedule(path) {
    const loader = loaders?.[path]
    if (
      !active ||
      typeof loader !== 'function' ||
      loadedOrLoading.has(path) ||
      scheduled.has(path)
    ) {
      return false
    }
    const timer = setTimer(() => {
      scheduled.delete(path)
      void loadNow(path)
    }, delay)
    scheduled.set(path, timer)
    return true
  }

  function dispose() {
    if (!active) return
    active = false
    for (const timer of scheduled.values()) clearTimer(timer)
    scheduled.clear()
  }

  return Object.freeze({ loadNow, schedule, dispose })
}
