(() => {
  const READY_TYPE = 'globemind:globe-ready'
  const ERROR_TYPE = 'globemind:globe-error'
  const HEALTH_TIMEOUT_MS = 10_000
  const REQUIRED_PAINTED_FRAMES = 3
  const attempt = new URLSearchParams(window.location.search).get('healthAttempt') || '0'
  const startedAt = Date.now()
  const canvasDrawCounts = new WeakMap()
  const drawMethodRestorers = []
  let finished = false
  let loaderObserver = null

  function instrumentDrawMethods(Context) {
    const prototype = Context?.prototype
    if (!prototype) return

    for (const methodName of [
      'drawArrays',
      'drawElements',
      'drawArraysInstanced',
      'drawElementsInstanced',
    ]) {
      const original = prototype[methodName]
      if (typeof original !== 'function' || original.__globemindHealthWrapped) continue

      function trackedDraw(...args) {
        const canvas = this.canvas
        const result = Reflect.apply(original, this, args)
        const elementCount = methodName.startsWith('drawArrays') ? args[2] : args[1]
        const instanceCount = methodName.endsWith('Instanced')
          ? args[methodName.startsWith('drawArrays') ? 3 : 4]
          : 1
        if (canvas && Number(elementCount) > 0 && Number(instanceCount) > 0) {
          canvasDrawCounts.set(canvas, (canvasDrawCounts.get(canvas) || 0) + 1)
        }
        return result
      }
      trackedDraw.__globemindHealthWrapped = true

      try {
        prototype[methodName] = trackedDraw
        if (prototype[methodName] === trackedDraw) {
          drawMethodRestorers.push(() => {
            if (prototype[methodName] === trackedDraw) prototype[methodName] = original
          })
        }
      } catch {
        // A locked-down browser may expose non-writable WebGL prototypes. The
        // parent timeout remains the safe fallback instead of claiming ready.
      }
    }
  }

  // This script intentionally runs before the module bundle so the first real
  // WebGL draw calls are observable. Merely obtaining a context is not proof
  // that the bundled globe rendered anything.
  instrumentDrawMethods(globalThis.WebGLRenderingContext)
  instrumentDrawMethods(globalThis.WebGL2RenderingContext)

  function cleanup() {
    loaderObserver?.disconnect()
    while (drawMethodRestorers.length) {
      try {
        drawMethodRestorers.pop()()
      } catch {
        // Prototype restoration is best-effort; the readiness result must still
        // reach the parent if another script locked a method after startup.
      }
    }
  }

  function notify(type) {
    if (finished) return
    finished = true
    cleanup()
    window.parent.postMessage({ type, attempt }, window.location.origin)
  }

  function startHealthCheck() {
    if (finished) return
    const loader = document.querySelector('.loader-container')
    const canvas = document.querySelector('canvas')
    let loaderWasLoaded = false
    let loaderReturnedToBusy = false
    let loaderReady = false
    let lastLoaderState = false
    let paintedFrames = 0
    let lastDrawCount = 0

    function recordLoaderState(loaded) {
      if (loaded === lastLoaderState) return
      if (loaded) {
        if (loaderWasLoaded && loaderReturnedToBusy) {
          loaderReady = true
          paintedFrames = 0
          lastDrawCount = canvas ? (canvasDrawCounts.get(canvas) || 0) : 0
        }
        loaderWasLoaded = true
      } else if (loaderWasLoaded) {
        loaderReturnedToBusy = true
        paintedFrames = 0
      }
      lastLoaderState = loaded
    }

    if (loader) {
      recordLoaderState(loader.classList.contains('loaded'))
      loaderObserver = new MutationObserver((records) => {
        for (const record of records) {
          recordLoaderState(String(record.oldValue || '').split(/\s+/).includes('loaded'))
        }
        recordLoaderState(loader.classList.contains('loaded'))
      })
      loaderObserver.observe(loader, {
        attributeFilter: ['class'],
        attributeOldValue: true,
        attributes: true,
      })
    }

    function checkCanvas() {
      if (finished) return
      const renderer = loaderReady && canvas
        ? canvas.getContext('webgl2') || canvas.getContext('webgl')
        : null
      const drawCount = canvas ? (canvasDrawCounts.get(canvas) || 0) : 0
      const loaderIsSettled = Boolean(loader?.classList.contains('loaded'))
      const drewSinceLastCheck = drawCount > lastDrawCount

      if (
        loaderReady
        && loaderIsSettled
        && renderer
        && canvas.width > 0
        && canvas.height > 0
        && drewSinceLastCheck
      ) {
        paintedFrames += 1
        if (paintedFrames >= REQUIRED_PAINTED_FRAMES) {
          notify(READY_TYPE)
          return
        }
      } else {
        paintedFrames = 0
      }
      lastDrawCount = drawCount

      if (Date.now() - startedAt >= HEALTH_TIMEOUT_MS) {
        notify(ERROR_TYPE)
        return
      }
      window.requestAnimationFrame(checkCanvas)
    }

    window.requestAnimationFrame(checkCanvas)
  }

  window.addEventListener('error', () => notify(ERROR_TYPE), { once: true })
  window.addEventListener('unhandledrejection', () => notify(ERROR_TYPE), { once: true })

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startHealthCheck, { once: true })
  } else {
    startHealthCheck()
  }
})()
