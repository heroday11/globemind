import { fileURLToPath, URL } from 'node:url'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, rmSync } from 'node:fs'
import path from 'node:path'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import legacy from '@vitejs/plugin-legacy'
import vueDevTools from 'vite-plugin-vue-devtools'

import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

import { mockApiPlugin } from './scripts/mock-api-plugin.mjs'

export const PRODUCTION_PUBLIC_EXCLUDES = Object.freeze(['design-review'])
export const PRODUCTION_PUBLIC_ACTIVE_DOCUMENT_ROOTS = Object.freeze(['datasets/expert-skills'])

export function isUnapprovedActiveDocument(fileName) {
  return /\.(?:html?|m?js)$/i.test(String(fileName || ''))
}

function removeUnapprovedActiveDocuments(directory) {
  if (!existsSync(directory)) return
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      removeUnapprovedActiveDocuments(target)
    } else if (isUnapprovedActiveDocument(entry.name)) {
      rmSync(target, { force: true })
    }
  }
}

export function hardenIndexHtml(html) {
  // Rocket Loader rewrites both Vite's modern feature probes and the
  // nomodule fallback. On older browsers that can prevent either entry
  // point from ever running, so keep every generated bootstrap script
  // under Vite's own control.
  const cloudflareSafeHtml = html.replace(
    /<script(?![^>]*\bdata-cfasync\s*=)([^>]*)>/gi,
    '<script data-cfasync="false"$1>',
  )
  const inlineScriptHashes = Array.from(
    cloudflareSafeHtml.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi),
    (match) => `'sha256-${createHash('sha256').update(match[1], 'utf8').digest('base64')}'`,
  )
  const scriptPolicy = ["'self'", ...new Set(inlineScriptHashes)].join(' ')
  const policy = `script-src ${scriptPolicy}; object-src 'none'; base-uri 'self'`
  const cspMetaPattern = /<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(["'])Content-Security-Policy\1)[^>]*>/gi
  const withoutPreviousPolicy = cloudflareSafeHtml.replace(cspMetaPattern, '')
  if (
    !/<head(?:\s[^>]*)?>/i.test(withoutPreviousPolicy) ||
    !/<\/head\s*>/i.test(withoutPreviousPolicy)
  ) {
    throw new Error('cannot enforce Content-Security-Policy without a document head')
  }
  return withoutPreviousPolicy.replace(
    /<head([^>]*)>/i,
    `<head$1>\n    <meta http-equiv="Content-Security-Policy" content="${policy}">`,
  )
}

export function removeExcludedPublicArtifactsPlugin(root, outputDirectory) {
  const outputRoot = path.resolve(root, outputDirectory)
  return {
    name: 'remove-development-public-artifacts',
    apply: 'build',
    closeBundle() {
      for (const relative of PRODUCTION_PUBLIC_EXCLUDES) {
        const target = path.resolve(outputRoot, relative)
        const relation = path.relative(outputRoot, target)
        if (!relation || relation.startsWith('..') || path.isAbsolute(relation)) {
          throw new Error(`refusing to remove public artifact outside build output: ${relative}`)
        }
        rmSync(target, { recursive: true, force: true })
      }
      for (const relative of PRODUCTION_PUBLIC_ACTIVE_DOCUMENT_ROOTS) {
        const target = path.resolve(outputRoot, relative)
        const relation = path.relative(outputRoot, target)
        if (!relation || relation.startsWith('..') || path.isAbsolute(relation)) {
          throw new Error(`refusing to prune active documents outside build output: ${relative}`)
        }
        removeUnapprovedActiveDocuments(target)
      }
    },
  }
}

function cloudflareSafeIndexPlugin() {
  return {
    name: 'cloudflare-safe-index',
    transformIndexHtml: {
      order: 'post',
      handler: hardenIndexHtml,
    },
  }
}

function manualChunks(id) {
  const normalizedId = id.replaceAll('\\', '/')
  if (
    normalizedId.endsWith('/frontend/shared/displayPreferences.js') ||
    normalizedId.endsWith('/src/composables/useDisplayPreferences.js') ||
    normalizedId.endsWith('/src/utils/auth.js')
  ) {
    return 'app-runtime'
  }
  if (normalizedId.endsWith('/src/components/NewUserGuide.vue')) return 'onboarding'
  if (id.includes('node_modules/echarts')) return 'vendor-echarts'
  if (id.includes('node_modules/three')) return 'vendor-three'
  if (id.includes('node_modules/element-plus')) return 'vendor-element'
  if (id.includes('node_modules/vue')) return 'vendor-vue'
  if (id.includes('node_modules/@vue')) return 'vendor-vue'
  if (id.includes('node_modules/pinia')) return 'vendor-vue'
  if (id.includes('node_modules/vue-router')) return 'vendor-vue'
}

function envTruthy(v) {
  const s = String(v ?? '')
    .trim()
    .toLowerCase()
  return s === '1' || s === 'true' || s === 'yes'
}

export default defineConfig(({ mode }) => {
  const root = fileURLToPath(new URL('.', import.meta.url))
  const env = loadEnv(mode, root, '')
  const envDev = loadEnv('development', root, '')
  const useApiMock = envTruthy(env.VITE_USE_API_MOCK) || envTruthy(envDev.VITE_USE_API_MOCK)
  const apiProxyTarget =
    env.VITE_API_PROXY_TARGET ||
    env.VITE_API_BASE_URL ||
    envDev.VITE_API_PROXY_TARGET ||
    envDev.VITE_API_BASE_URL
  const vllmProxyTarget =
    env.VITE_VLLM_PROXY_TARGET ||
    env.VITE_VLLM_ORIGIN ||
    envDev.VITE_VLLM_PROXY_TARGET ||
    envDev.VITE_VLLM_ORIGIN
  const outputDirectory = process.env.VITE_OUT_DIR || env.VITE_OUT_DIR || 'dist'

  const devProxy = {}
  if (apiProxyTarget && !useApiMock) {
    devProxy['/api'] = {
      target: apiProxyTarget,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, '/api'),
      timeout: 300000,
      proxyTimeout: 300000,
      configure: (proxy) => {
        proxy.on('proxyReq', (proxyReq, req) => {
          const u = req.url || ''
          if (u.includes('stream') || u.includes('event-stream')) {
            proxyReq.setHeader('Accept', 'text/event-stream')
            proxyReq.setHeader('Cache-Control', 'no-cache')
          }
        })
      },
    }
  } else if (mode === 'development' && !useApiMock) {
    console.warn(
      '[vite] 未设置 VITE_API_PROXY_TARGET / VITE_API_BASE_URL，/api 代理未启用。请在 .env.development 中配置（见 .env.example）。',
    )
  }
  if (useApiMock && mode === 'development') {
    console.warn('[vite] VITE_USE_API_MOCK 已开启：/api 由本地假数据中间件响应，不会转发到后端。')
  }
  if (vllmProxyTarget) {
    devProxy['/llm'] = {
      target: vllmProxyTarget,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/llm/, ''),
    }
  }

  return {
    publicDir: envTruthy(env.VITE_SKIP_PUBLIC_COPY) ? false : 'public',
    plugins: [
      ...(useApiMock && mode === 'development' ? [mockApiPlugin()] : []),
      cloudflareSafeIndexPlugin(),
      removeExcludedPublicArtifactsPlugin(root, outputDirectory),
      legacy({
        targets: ['Chrome >= 49', 'Edge >= 15', 'Firefox >= 52', 'Safari >= 10.1'],
        additionalLegacyPolyfills: [
          'whatwg-fetch',
          'abortcontroller-polyfill/dist/abortcontroller-polyfill-only',
        ],
        modernPolyfills: false,
        renderLegacyChunks: true,
      }),
      vue({
        script: {
          defineModel: true,
        },
      }),
      ...(mode === 'development' ? [vueDevTools()] : []),
      AutoImport({
        dts: mode === 'development' ? 'auto-imports.d.ts' : false,
        resolvers: [ElementPlusResolver()],
      }),
      Components({
        dts: mode === 'development' ? 'components.d.ts' : false,
        resolvers: [ElementPlusResolver()],
      }),
    ],
    server: {
      // 默认仅 127.0.0.1；设为 true 等同 0.0.0.0，局域网其它设备可访问本机 dev server
      host: true,
      port: 5173,
      allowedHosts: true,
      // 局域网：在 .env.development.local 设置 VITE_DEV_PUBLIC_HOST=你的局域网IP
      // origin 让模块、资源与客户端逻辑中的「站点根」与浏览器地址栏一致，避免仍生成 127.0.0.1:5173
      ...(env.VITE_DEV_PUBLIC_HOST
        ? {
            origin: `http://${env.VITE_DEV_PUBLIC_HOST}:5173`,
            hmr: {
              host: env.VITE_DEV_PUBLIC_HOST,
              port: 5173,
              clientPort: 5173,
            },
          }
        : {
            hmr: true,
          }),
      proxy: devProxy,
    },
    // 预览构建产物时同样转发 /api，便于局域网用「生产包」联调本机后端
    preview: {
      host: true,
      port: 4173,
      proxy: devProxy,
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    build: {
      outDir: outputDirectory,
      emptyOutDir: true,
      rollupOptions: {
        output: {
          manualChunks,
        },
      },
    },
  }
})
