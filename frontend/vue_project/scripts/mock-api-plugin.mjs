/**
 * Vite 开发服务器：拦截 /api/* 返回静态样本（无 FastAPI 时联调 data-search 等页面）。
 * 通过 VITE_USE_API_MOCK=true 启用；见 .env.example。
 */

import { randomBytes } from 'node:crypto'

import {
  buildSearchResponse,
  getNewsById,
  getNewsByIdsParam,
  getNewsListResponse,
  getSearchOptionsPayload,
  getStatsPayload,
} from './mock-api-data.mjs'
import {
  getFavoritesListResponse,
  mockFavoriteRemove,
  mockFavoriteToggle,
} from './mock-user-favorites.mjs'

function sendJson(res, status, obj) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.end(JSON.stringify(obj))
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.on('data', (c) => {
      raw += c
      if (raw.length > 2_000_000) {
        reject(new Error('body too large'))
        req.destroy()
      }
    })
    req.on('end', () => {
      if (!raw) return resolve({})
      try {
        resolve(JSON.parse(raw))
      } catch (e) {
        reject(e)
      }
    })
    req.on('error', reject)
  })
}

/**
 * @returns {Promise<boolean>} 是否已处理（true 则不应再 next 到代理）
 */
async function tryHandle(req, res) {
  const raw = req.url || ''
  if (!raw.startsWith('/api')) return false

  const url = new URL(raw, 'http://localhost')
  let pathname = url.pathname
  if (pathname.length > 1 && pathname.endsWith('/')) pathname = pathname.slice(0, -1)
  const method = (req.method || 'GET').toUpperCase()

  if (method === 'POST' && pathname === '/api/auth/login') {
    const body = await readJsonBody(req)
    const username = String(body?.username ?? '').trim()
    if (!username) {
      sendJson(res, 400, { detail: '用户名不能为空' })
      return true
    }
    const token = randomBytes(32).toString('hex')
    const user = {
      id: 1,
      username,
      full_name: username,
      email: username.includes('@') ? username : `${username}@local.dev`,
      phone: '',
      created_at: null,
      updated_at: null,
      is_active: true,
      last_login_at: null,
      role: 'user',
      avatar_url: '',
    }
    sendJson(res, 200, { access_token: token, token_type: 'bearer', user })
    return true
  }

  if (method === 'POST' && pathname === '/api/auth/register') {
    await readJsonBody(req)
    sendJson(res, 200, { ok: true, message: '注册成功' })
    return true
  }

  if (method === 'POST' && pathname === '/api/auth/forgot-password') {
    await readJsonBody(req)
    sendJson(res, 200, { message: '如该邮箱已注册，将收到重置说明（当前为离线环境）' })
    return true
  }

  if (method === 'POST' && pathname === '/api/auth/reset-password') {
    await readJsonBody(req)
    sendJson(res, 200, { message: '密码已更新' })
    return true
  }

  if (method === 'GET' && pathname === '/api/dashboard/search/options') {
    sendJson(res, 200, getSearchOptionsPayload())
    return true
  }

  if (method === 'GET' && pathname === '/api/dashboard/stats') {
    sendJson(res, 200, getStatsPayload())
    return true
  }

  if (method === 'GET' && pathname === '/api/dashboard/news/by-ids') {
    const ids = url.searchParams.get('ids') || ''
    sendJson(res, 200, { data: getNewsByIdsParam(ids) })
    return true
  }

  if (method === 'GET' && pathname === '/api/dashboard/news') {
    sendJson(res, 200, getNewsListResponse(pathname + url.search))
    return true
  }

  const newsDetailMatch = pathname.match(/^\/api\/dashboard\/news\/(\d+)$/)
  if (method === 'GET' && newsDetailMatch) {
    const item = getNewsById(newsDetailMatch[1])
    if (!item) {
      sendJson(res, 404, { detail: '新闻不存在' })
      return true
    }
    sendJson(res, 200, item)
    return true
  }

  const articleCompat = pathname.match(/^\/api\/article\/(\d+)$/)
  if (method === 'GET' && articleCompat) {
    const item = getNewsById(articleCompat[1])
    if (!item) {
      sendJson(res, 404, { detail: '新闻不存在' })
      return true
    }
    sendJson(res, 200, item)
    return true
  }

  if (method === 'POST' && pathname === '/api/dashboard/search') {
    const body = await readJsonBody(req)
    sendJson(res, 200, buildSearchResponse(body))
    return true
  }

  if (method === 'POST' && pathname === '/api/user/search-history') {
    sendJson(res, 200, { ok: true })
    return true
  }

  if (method === 'GET' && pathname === '/api/user/favorites') {
    sendJson(res, 200, getFavoritesListResponse())
    return true
  }

  if (method === 'POST' && pathname === '/api/user/favorites/toggle') {
    const body = await readJsonBody(req)
    const out = mockFavoriteToggle(body)
    sendJson(res, out.status, out.json)
    return true
  }

  if (method === 'POST' && pathname === '/api/user/favorites/remove') {
    const body = await readJsonBody(req)
    const out = mockFavoriteRemove(body)
    sendJson(res, out.status, out.json)
    return true
  }

  // 详情页曾尝试的分析接口：返回空 items，由前端兜底展示基础字段
  const analysisPath = pathname.match(/^\/api\/dashboard\/news\/(\d+)\/analysis$/)
  if (method === 'GET' && analysisPath) {
    sendJson(res, 200, { items: [] })
    return true
  }
  const analysisPath2 = pathname.match(/^\/api\/dashboard\/news-analysis\/(\d+)$/)
  if (method === 'GET' && analysisPath2) {
    sendJson(res, 200, { items: [] })
    return true
  }

  if (method === 'POST' && pathname === '/api/ai/analyze') {
    sendJson(res, 503, { detail: '该服务暂不可用，请稍后重试。' })
    return true
  }

  sendJson(res, 404, { detail: `接口不存在：${method} ${pathname}` })
  return true
}

export function mockApiPlugin() {
  return {
    name: 'globemind-dev-api-sample',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const u = req.url || ''
        if (!u.startsWith('/api')) return next()
        tryHandle(req, res)
          .then((handled) => {
            if (!handled) next()
          })
          .catch((err) => {
            console.error('[api-sample]', err)
            sendJson(res, 500, { detail: String(err?.message || err) })
          })
      })
    },
  }
}
