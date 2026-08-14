import { API_PREFIX } from '../../config/api.js'

export class SentimentApiError extends Error {
  constructor(message, { operation = 'unknown', status = 0, payload = null, cause } = {}) {
    super(message, { cause })
    this.name = 'SentimentApiError'
    this.operation = operation
    this.status = status
    this.payload = payload
  }
}

export function createSentimentEndpoints(apiPrefix = API_PREFIX) {
  const opinionRoot = `${apiPrefix}/opinion`
  return Object.freeze({
    overview: `${opinionRoot}/overview`,
    trend: `${opinionRoot}/china-trend`,
    topNews: `${opinionRoot}/top-news`,
    dimensions: `${opinionRoot}/dimensions`,
    quality: `${opinionRoot}/quality`,
    newsByDate: `${opinionRoot}/news-by-date`,
    feedback: `${opinionRoot}/feedback`,
    macroEventClusters: `${opinionRoot}/macro-event-clusters`,
    search: `${apiPrefix}/dashboard/search`,
    searchHistory: `${apiPrefix}/user/search-history`,
    favorites: `${apiPrefix}/user/favorites`,
    favoriteToggle: `${apiPrefix}/user/favorites/toggle`,
  })
}

export const SENTIMENT_ENDPOINTS = createSentimentEndpoints()

export function buildSentimentAuthHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function buildSentimentUrl(endpoint, query = {}) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const encoded = params.toString()
  return encoded ? `${endpoint}?${encoded}` : endpoint
}

function errorMessage(payload, fallback) {
  if (typeof payload === 'string' && payload.trim()) return payload.trim()
  const detail = payload?.detail ?? payload?.message ?? payload?.error
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => item?.msg || item?.message || String(item))
      .filter(Boolean)
      .join('；')
  }
  return fallback
}

async function readPayload(response) {
  if (typeof response?.text === 'function') {
    const text = await response.text()
    if (!text) return {}
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }
  if (typeof response?.json === 'function') return response.json()
  return {}
}

export function createSentimentApi({
  fetchImpl = globalThis.fetch,
  apiPrefix = API_PREFIX,
  getAccessToken = () => globalThis.localStorage?.getItem('access_token') || '',
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Sentiment API requires fetch')
  const endpoints = createSentimentEndpoints(apiPrefix)

  async function requestJson(operation, endpoint, {
    auth = false,
    body,
    fallbackMessage,
    headers,
    method = 'GET',
    query,
    signal,
  } = {}) {
    const requestHeaders = {
      ...headers,
      ...(auth ? buildSentimentAuthHeaders(getAccessToken()) : {}),
    }
    if (body !== undefined) requestHeaders['Content-Type'] = 'application/json'

    let response
    try {
      response = await fetchImpl(buildSentimentUrl(endpoint, query), {
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: method === 'GET' && query?._ !== undefined ? 'no-store' : undefined,
        headers: requestHeaders,
        method,
        signal,
      })
    } catch (cause) {
      if (cause?.name === 'AbortError' || signal?.aborted) throw cause
      throw new SentimentApiError(`${fallbackMessage}，请检查网络连接`, { operation, cause })
    }

    let payload
    try {
      payload = await readPayload(response)
    } catch (cause) {
      throw new SentimentApiError(`${fallbackMessage}：响应数据格式错误`, {
        operation,
        status: response?.status || 0,
        cause,
      })
    }
    if (!response?.ok) {
      throw new SentimentApiError(errorMessage(payload, fallbackMessage), {
        operation,
        status: response?.status || 0,
        payload,
      })
    }
    return payload
  }

  return Object.freeze({
    endpoints,
    isAuthenticated: () => Boolean(getAccessToken()),

    getOverview(query, { signal } = {}) {
      return requestJson('overview', endpoints.overview, {
        fallbackMessage: '获取舆情概览失败',
        headers: { 'Cache-Control': 'no-cache' },
        query,
        signal,
      })
    },

    getTrend(query, { signal } = {}) {
      return requestJson('trend', endpoints.trend, {
        fallbackMessage: '获取舆情数据失败',
        headers: { 'Cache-Control': 'no-cache' },
        query,
        signal,
      })
    },

    getTopNews(query, { signal } = {}) {
      return requestJson('top-news', endpoints.topNews, {
        fallbackMessage: '获取相关新闻失败',
        query,
        signal,
      })
    },

    getDimensions(query = { days: 30, limit: 8 }, { signal } = {}) {
      return requestJson('dimensions', endpoints.dimensions, {
        fallbackMessage: '维度数据加载失败',
        query,
        signal,
      })
    },

    getQuality({ signal } = {}) {
      return requestJson('quality', endpoints.quality, {
        fallbackMessage: '质量数据加载失败',
        signal,
      })
    },

    getNewsByDate(query, { signal } = {}) {
      return requestJson('news-by-date', endpoints.newsByDate, {
        fallbackMessage: '获取新闻失败',
        query,
        signal,
      })
    },

    submitFeedback(payload, { signal } = {}) {
      return requestJson('feedback', endpoints.feedback, {
        auth: true,
        body: payload,
        fallbackMessage: '反馈记录失败',
        method: 'POST',
        signal,
      })
    },

    getMacroEventClusters(query, { signal } = {}) {
      return requestJson('macro-event-clusters', endpoints.macroEventClusters, {
        fallbackMessage: '获取聚类失败',
        query,
        signal,
      })
    },

    search(payload, { signal } = {}) {
      return requestJson('search', endpoints.search, {
        auth: true,
        body: payload,
        fallbackMessage: '搜索失败',
        method: 'POST',
        signal,
      })
    },

    listSearchHistory({ signal } = {}) {
      return requestJson('search-history', endpoints.searchHistory, {
        auth: true,
        fallbackMessage: '搜索记录加载失败',
        signal,
      })
    },

    recordSearchHistory(keyword, { signal } = {}) {
      return requestJson('record-search-history', endpoints.searchHistory, {
        auth: true,
        body: { keyword },
        fallbackMessage: '搜索记录保存失败',
        method: 'POST',
        signal,
      })
    },

    listFavorites({ signal } = {}) {
      return requestJson('favorites', endpoints.favorites, {
        auth: true,
        fallbackMessage: '收藏加载失败',
        signal,
      })
    },

    toggleFavorite(newsId, { signal } = {}) {
      return requestJson('toggle-favorite', endpoints.favoriteToggle, {
        auth: true,
        body: { news_id: newsId },
        fallbackMessage: '收藏同步失败',
        method: 'POST',
        signal,
      })
    },
  })
}

export const sentimentApi = createSentimentApi()
