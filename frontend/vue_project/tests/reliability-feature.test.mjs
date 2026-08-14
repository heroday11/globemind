import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

test('3D globe fails closed with a bounded readiness handshake and usable fallback', async () => {
  const view = await readFile(new URL('../src/views/AmazingGlobe.vue', import.meta.url), 'utf8')
  const childHealth = await readFile(new URL('../public/amazing-globe/parent-health.js', import.meta.url), 'utf8')
  const childHtml = await readFile(new URL('../public/amazing-globe/index.html', import.meta.url), 'utf8')

  assert.match(view, /LOAD_TIMEOUT_MS = 10_000/)
  assert.match(view, /globemind:globe-ready/)
  assert.match(view, /event\.origin !== window\.location\.origin/)
  assert.match(view, /event\.source !== globeFrame\.value\?\.contentWindow/)
  assert.match(view, /event\.data\?\.attempt/)
  assert.match(view, /loadState\.value !== 'loading'/)
  assert.match(view, /event\?\.currentTarget !== globeFrame\.value/)
  assert.match(view, /focusFrameAfterRetry/)
  assert.match(view, /:aria-busy="loadState === 'loading'"/)
  assert.match(view, /当前显示的是静态覆盖示意，不代表实时地理态势/)
  assert.match(view, /重新加载/)
  assert.match(view, /新闻检索/)
  assert.match(childHealth, /document\.querySelector\('canvas'\)/)
  assert.match(childHealth, /loaderReturnedToBusy/)
  assert.match(childHealth, /MutationObserver/)
  assert.match(childHealth, /getContext\('webgl2'\)/)
  assert.match(childHealth, /canvasDrawCounts/)
  assert.match(childHealth, /drewSinceLastCheck/)
  assert.match(childHealth, /REQUIRED_PAINTED_FRAMES/)
  assert.match(childHealth, /window\.parent\.postMessage/)
  assert.match(childHealth, /\{ type, attempt \}/)
  assert.match(childHtml, /<script data-cfasync="false" src="\/amazing-globe\/parent-health\.js"><\/script>/)
})

test('3D globe child never reports a default canvas as ready and correlates real draws', async () => {
  const childHealth = await readFile(
    new URL('../public/amazing-globe/parent-health.js', import.meta.url),
    'utf8',
  )
  const messages = []
  const animationFrames = []
  let mutationCallback = null
  let loaderLoaded = false
  let now = 0
  let nativeDrawCalls = 0

  const canvas = {
    width: 640,
    height: 360,
    getContext: () => renderer,
  }
  function WebGLRenderingContext() {
    this.canvas = canvas
  }
  WebGLRenderingContext.prototype.drawElements = function drawElements() {
    nativeDrawCalls += 1
  }
  const renderer = new WebGLRenderingContext()
  const loader = {
    classList: {
      contains: (name) => name === 'loaded' && loaderLoaded,
    },
  }
  class MutationObserver {
    constructor(callback) {
      mutationCallback = callback
    }

    observe() {}

    disconnect() {}
  }

  const sandbox = {
    Date: { now: () => now },
    MutationObserver,
    Reflect,
    URLSearchParams,
    WeakMap,
    WebGLRenderingContext,
    document: {
      readyState: 'complete',
      addEventListener() {},
      querySelector(selector) {
        if (selector === '.loader-container') return loader
        if (selector === 'canvas') return canvas
        return null
      },
    },
    window: {
      addEventListener() {},
      location: {
        origin: 'https://globemind.test',
        search: '?healthAttempt=7',
      },
      parent: {
        postMessage(payload, targetOrigin) {
          messages.push({ payload, targetOrigin })
        },
      },
      requestAnimationFrame(callback) {
        animationFrames.push(callback)
      },
    },
  }

  vm.runInNewContext(childHealth, sandbox, { filename: 'parent-health.js' })

  function setLoaderState(loaded, oldValue) {
    loaderLoaded = loaded
    mutationCallback([{ oldValue }])
  }

  function runFrame() {
    const callback = animationFrames.shift()
    assert.equal(typeof callback, 'function')
    now += 16
    callback()
  }

  // Match the bundled loader's initial loaded -> busy -> final loaded cycle.
  setLoaderState(true, 'loader-container')
  setLoaderState(false, 'loader-container loaded')
  setLoaderState(true, 'loader-container')

  for (let frame = 0; frame < 5; frame += 1) runFrame()
  assert.deepEqual(messages, [], 'context and dimensions alone must not claim readiness')

  for (let frame = 0; frame < 3; frame += 1) {
    renderer.drawElements(4, 3, 5123, 0)
    runFrame()
  }

  assert.equal(nativeDrawCalls, 3)
  assert.equal(messages.length, 1)
  assert.equal(messages[0].payload.type, 'globemind:globe-ready')
  assert.equal(messages[0].payload.attempt, '7')
  assert.equal(messages[0].targetOrigin, 'https://globemind.test')
})
