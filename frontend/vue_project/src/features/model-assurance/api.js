import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'
import { getToken } from '../../utils/auth.js'

import { sanitizeModelAssuranceResponse } from './model.js'
import { sanitizeModelOutputSurfaceInventory } from './surfaces.js'

export const MODEL_ASSURANCE_ENDPOINTS = Object.freeze({
  status: `${API_PREFIX}/model-assurance/status`,
  surfaces: `${API_PREFIX}/model-assurance/surfaces`,
  evaluations: `${API_PREFIX}/model-assurance/evaluations`,
})

export class ModelAssuranceApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', detail = null, cause } = {}) {
    super(message, { cause })
    this.name = 'ModelAssuranceApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function responsePayload(response) {
  const contentType = String(response?.headers?.get?.('content-type') || '')
  if (!contentType || contentType.includes('application/json')) {
    try {
      return await response.json()
    } catch (cause) {
      throw new ModelAssuranceApiError('模型保障服务返回了无效 JSON', {
        status: Number(response?.status || 0),
        code: 'INVALID_JSON_RESPONSE',
        cause,
      })
    }
  }
  const detail = await response.text().catch(() => '')
  return detail ? { detail } : {}
}

function errorMessage(payload, fallback) {
  const detail = payload?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (detail && typeof detail === 'object') {
    return String(detail.message || detail.code || fallback)
  }
  return String(payload?.error || fallback)
}

function validEvaluationId(value) {
  return /^eval\.[a-z0-9][a-z0-9_.-]{1,119}$/.test(value)
}

export function createModelAssuranceApi({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  getAccessToken = getToken,
  apiOrigin = resolveApiOrigin(),
  endpoints = MODEL_ASSURANCE_ENDPOINTS,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Model assurance API requires fetch')

  async function request(
    path,
    {
      method = 'GET',
      body,
      signal,
      expectedStatus = 200,
      fallback = '模型保障请求失败',
    } = {},
  ) {
    const token = getAccessToken?.() || ''
    if (!token) {
      throw new ModelAssuranceApiError('请先登录后查看模型保障账本', {
        status: 401,
        code: 'AUTH_REQUIRED',
      })
    }
    const headers = {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let response
    try {
      response = await fetchImpl(`${apiOrigin}${path}`, {
        method,
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
    } catch (cause) {
      if (cause?.name === 'AbortError') throw cause
      throw new ModelAssuranceApiError(cause?.message || fallback, {
        code: 'NETWORK_ERROR',
        cause,
      })
    }
    const payload = await responsePayload(response)
    if (!response.ok || response.status !== expectedStatus) {
      throw new ModelAssuranceApiError(errorMessage(payload, fallback), {
        status: Number(response.status || 0),
        code: response.status === 401
          ? 'AUTH_REQUIRED'
          : response.status === 403
            ? 'ADMIN_REQUIRED'
            : 'REQUEST_FAILED',
        detail: payload?.detail ?? payload,
      })
    }
    return payload
  }

  return Object.freeze({
    async fetchSurfaces({ signal } = {}) {
      const payload = await request(endpoints.surfaces, {
        signal,
        fallback: '读取模型输出面清单失败',
      })
      return sanitizeModelOutputSurfaceInventory(payload)
    },

    async fetchStatus({ signal } = {}) {
      const payload = await request(endpoints.status, {
        signal,
        fallback: '读取模型保障状态失败',
      })
      return sanitizeModelAssuranceResponse('status', payload)
    },

    async listEvaluations({ limit = 100, modelId = '', signal } = {}) {
      if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
        throw new TypeError('Model assurance list limit must be in [1, 500]')
      }
      const query = new URLSearchParams({ limit: String(limit) })
      if (modelId) {
        if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$/.test(modelId)) {
          throw new TypeError('Model assurance model id is invalid')
        }
        query.set('model_id', modelId)
      }
      const payload = await request(`${endpoints.evaluations}?${query}`, {
        signal,
        fallback: '读取模型评测列表失败',
      })
      return sanitizeModelAssuranceResponse('list', payload)
    },

    async getEvaluation(evaluationId, { signal } = {}) {
      if (!validEvaluationId(evaluationId)) throw new TypeError('Model assurance evaluation id is invalid')
      const payload = await request(
        `${endpoints.evaluations}/${encodeURIComponent(evaluationId)}`,
        { signal, fallback: '读取模型评测详情失败' },
      )
      return sanitizeModelAssuranceResponse('detail', payload)
    },

    async submitEvaluation(manifest, { signal } = {}) {
      if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
        throw new TypeError('A parsed model assurance manifest is required')
      }
      const payload = await request(endpoints.evaluations, {
        method: 'POST',
        body: manifest,
        signal,
        expectedStatus: 201,
        fallback: '提交模型评测清单失败',
      })
      return sanitizeModelAssuranceResponse('detail', payload)
    },
  })
}

export const modelAssuranceApi = createModelAssuranceApi()
