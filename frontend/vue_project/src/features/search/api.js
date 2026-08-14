import { API_PREFIX } from '../../config/api.js'
import { formatApiErrorDetail } from '../../utils/apiError.js'

export class SearchApiError extends Error {
  constructor(message, { operation = 'unknown', status = 0, payload = null, cause } = {}) {
    super(message, { cause })
    this.name = 'SearchApiError'
    this.operation = operation
    this.status = status
    this.payload = payload
  }
}

export function createSearchEndpoints(apiPrefix = API_PREFIX) {
  return Object.freeze({
    news: `${apiPrefix}/dashboard/news`,
    search: `${apiPrefix}/dashboard/search`,
    stats: `${apiPrefix}/dashboard/stats`,
    searchOptions: `${apiPrefix}/dashboard/search/options`,
    favorites: `${apiPrefix}/user/favorites`,
    favoriteToggle: `${apiPrefix}/user/favorites/toggle`,
    favoriteRemove: `${apiPrefix}/user/favorites/remove`,
    searchHistory: `${apiPrefix}/user/search-history`,
    searchSnapshots: `${apiPrefix}/search-snapshots`,
    searchSnapshot: (id) => `${apiPrefix}/search-snapshots/${encodeURIComponent(id)}`,
    searchSnapshotReplay: (id) => `${apiPrefix}/search-snapshots/${encodeURIComponent(id)}/replay`,
    v11Children: (id, level) => `${apiPrefix}/dashboard/search/v11-clusters/${encodeURIComponent(id)}/children?level=${encodeURIComponent(level)}&page=1&page_size=50`,
  })
}

export const SEARCH_API_ENDPOINTS = createSearchEndpoints()

export function buildSearchAuthHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function buildNewsListUrl(endpoint, origin, {
  page,
  size,
  sortBy,
  sortOrder,
  favoriteScopeTopic,
}) {
  const url = new URL(endpoint, origin)
  url.searchParams.set('page', String(page))
  url.searchParams.set('size', String(size))
  const canonicalSortBy = sortBy === 'pub_time' ? 'published_at' : sortBy
  if (canonicalSortBy === 'published_at') url.searchParams.set('sort_by', canonicalSortBy)
  url.searchParams.set('sort_order', ['asc', 'desc'].includes(sortOrder) ? sortOrder : 'desc')
  url.searchParams.set('favorite_scope_topic', favoriteScopeTopic || '')
  return url
}

export function createSearchApi({
  fetchImpl = globalThis.fetch,
  apiPrefix = API_PREFIX,
  getAccessToken = () => globalThis.localStorage?.getItem('access_token') || '',
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Search API requires fetch')
  const endpoints = createSearchEndpoints(apiPrefix)

  function authHeaders() {
    return buildSearchAuthHeaders(getAccessToken())
  }

  async function requestJson(operation, url, options, fallbackMessage) {
    let response
    try {
      response = await fetchImpl(url, options)
    } catch (cause) {
      if (cause?.name === 'AbortError' || options?.signal?.aborted) throw cause
      throw new SearchApiError(`${fallbackMessage}，请检查网络连接`, { operation, cause })
    }

    let payload
    try {
      payload = await response.json()
    } catch (cause) {
      if (response.ok) {
        throw new SearchApiError(`${fallbackMessage}：响应数据格式错误`, {
          operation,
          status: response.status,
          cause,
        })
      }
      payload = {}
    }

    if (!response.ok) {
      const formatted = formatApiErrorDetail(payload)
      const detail = formatted === '请求失败' ? '' : formatted
      const statusText = response.statusText ? ` ${response.statusText}` : ''
      throw new SearchApiError(
        detail || `${fallbackMessage}: ${response.status}${statusText}`,
        { operation, status: response.status, payload },
      )
    }
    return payload
  }

  return Object.freeze({
    endpoints,

    async listNews(query, { signal, origin = globalThis.location?.origin } = {}) {
      const url = buildNewsListUrl(endpoints.news, origin, query)
      return requestJson('list-news', url, {
        headers: { ...authHeaders() },
        signal,
      }, '加载数据失败')
    },

    async search(params, { signal } = {}) {
      return requestJson('search', endpoints.search, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify(params),
        signal,
      }, '搜索失败')
    },

    async captureSearchSnapshot(receipt, expectedPreviousSnapshotId = null, { signal } = {}) {
      return requestJson('capture-search-snapshot', endpoints.searchSnapshots, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify({
          receipt,
          expected_previous_snapshot_id: expectedPreviousSnapshotId,
        }),
        signal,
      }, '保存查询快照失败')
    },

    async listSearchSnapshots(limit = 100, { signal } = {}) {
      const boundedLimit = Number.isInteger(limit) && limit >= 1 && limit <= 100 ? limit : 100
      return requestJson(
        'list-search-snapshots',
        `${endpoints.searchSnapshots}?limit=${boundedLimit}`,
        { headers: { ...authHeaders() }, signal },
        '加载查询快照失败',
      )
    },

    async getSearchSnapshot(id, { signal } = {}) {
      return requestJson('get-search-snapshot', endpoints.searchSnapshot(id), {
        headers: { ...authHeaders() },
        signal,
      }, '加载查询快照失败')
    },

    async replaySearchSnapshot(id, { signal } = {}) {
      return requestJson('replay-search-snapshot', endpoints.searchSnapshotReplay(id), {
        headers: { ...authHeaders() },
        signal,
      }, '读取冻结结果 ID 失败')
    },

    async getSearchOptions({ signal } = {}) {
      return requestJson('search-options', endpoints.searchOptions, { signal }, '加载搜索选项失败')
    },

    async getStats({ signal } = {}) {
      return requestJson('stats', endpoints.stats, { signal }, '加载统计信息失败')
    },

    async getV11Children(id, level, { signal } = {}) {
      return requestJson('v11-children', endpoints.v11Children(id, level), {
        headers: { ...authHeaders() },
        signal,
      }, '加载层级数据失败')
    },

    async listFavorites({ signal } = {}) {
      return requestJson('list-favorites', endpoints.favorites, {
        headers: { ...authHeaders() },
        signal,
      }, '加载收藏失败')
    },

    async toggleFavorite(params, { signal } = {}) {
      return requestJson('toggle-favorite', endpoints.favoriteToggle, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify(params),
        signal,
      }, '收藏同步失败')
    },

    async removeFavorite(params, { signal } = {}) {
      return requestJson('remove-favorite', endpoints.favoriteRemove, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
        },
        body: JSON.stringify(params),
        signal,
      }, '移除收藏失败')
    },

    async recordHistory(keyword, { signal } = {}) {
      if (!getAccessToken()) return { status: 'skipped' }
      try {
        const response = await fetchImpl(endpoints.searchHistory, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(),
          },
          body: JSON.stringify({ keyword: keyword.slice(0, 255) }),
          signal,
        })
        if (!response.ok && response.status !== 401) {
          const payload = await response.json().catch(() => ({}))
          return { status: 'warning', message: formatApiErrorDetail(payload) }
        }
        return { status: 'ok' }
      } catch (error) {
        return { status: 'error', error }
      }
    },
  })
}

export const searchApi = createSearchApi()
