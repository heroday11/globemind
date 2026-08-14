import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'
import { getToken } from '../../utils/auth.js'

import {
  sanitizeEntityGovernanceStatus,
  sanitizeEntityGovernanceWorkspace,
} from './model.js'

export const ENTITY_GOVERNANCE_ENDPOINTS = Object.freeze({
  status: `${API_PREFIX}/entity-governance/status`,
  catalog: `${API_PREFIX}/entity-governance/catalog`,
  relations: `${API_PREFIX}/entity-governance/relations`,
  history: `${API_PREFIX}/entity-governance/history`,
})

export class EntityGovernanceApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', detail = null, cause } = {}) {
    super(message, { cause })
    this.name = 'EntityGovernanceApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function responsePayload(response) {
  const contentType = String(response?.headers?.get?.('content-type') || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase()
  if (contentType !== 'application/json' && !contentType.endsWith('+json')) {
    throw new EntityGovernanceApiError('实体治理服务返回了非 JSON 响应', {
      status: Number(response?.status || 0),
      code: 'INVALID_RESPONSE_TYPE',
    })
  }
  try {
    return await response.json()
  } catch (cause) {
    throw new EntityGovernanceApiError('实体治理服务返回了无效 JSON', {
      status: Number(response?.status || 0),
      code: 'INVALID_JSON_RESPONSE',
      cause,
    })
  }
}

function errorMetadata(payload, fallback) {
  const detail = payload?.detail
  if (detail && typeof detail === 'object') {
    const safeCode = typeof detail.code === 'string' && /^[A-Z][A-Z0-9_]{2,199}$/.test(detail.code)
      ? detail.code
      : 'REQUEST_FAILED'
    const reasonCandidate = detail.reason_code || detail.code
    const safeReason = typeof reasonCandidate === 'string' && /^[A-Z][A-Z0-9_]{2,199}$/.test(reasonCandidate)
      ? reasonCandidate
      : fallback
    return {
      message: safeReason,
      code: safeCode,
      detail: Object.freeze({
        code: safeCode,
        reasonCode: safeReason === fallback ? null : safeReason,
        fallback: detail.fallback === 'none' ? 'none' : null,
      }),
    }
  }
  return {
    message: fallback,
    code: 'REQUEST_FAILED',
    detail: null,
  }
}

export function createEntityGovernanceApi({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  getAccessToken = getToken,
  apiOrigin = resolveApiOrigin(),
  endpoints = ENTITY_GOVERNANCE_ENDPOINTS,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Entity governance API requires fetch')

  async function request(path, { signal, fallback }) {
    const token = getAccessToken?.() || ''
    if (!token) {
      throw new EntityGovernanceApiError('请先登录后查看实体治理账本', {
        status: 401,
        code: 'AUTH_REQUIRED',
      })
    }
    let response
    try {
      response = await fetchImpl(`${apiOrigin}${path}`, {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${token}`,
        },
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
      })
    } catch (cause) {
      if (cause?.name === 'AbortError') throw cause
      throw new EntityGovernanceApiError(fallback, {
        code: 'NETWORK_ERROR',
        cause,
      })
    }
    const payload = await responsePayload(response)
    if (!response.ok) {
      const metadata = errorMetadata(payload, fallback)
      throw new EntityGovernanceApiError(metadata.message, {
        status: Number(response.status || 0),
        code: metadata.code,
        detail: metadata.detail,
      })
    }
    return payload
  }

  return Object.freeze({
    async loadWorkspace({ limit = 50, signal } = {}) {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
        throw new TypeError('Entity governance history limit must be in [1, 100]')
      }
      const statusPayload = await request(endpoints.status, {
        signal,
        fallback: '读取实体治理状态失败',
      })
      const status = sanitizeEntityGovernanceStatus(statusPayload)
      if (status.storageStatus === 'unavailable') {
        return Object.freeze({
          status,
          catalog: null,
          relations: null,
          history: null,
        })
      }
      const [catalog, relations, history] = await Promise.all([
        request(endpoints.catalog, { signal, fallback: '读取实体治理目录失败' }),
        request(endpoints.relations, { signal, fallback: '读取实体关系失败' }),
        request(`${endpoints.history}?limit=${limit}`, { signal, fallback: '读取实体治理历史失败' }),
      ])
      return sanitizeEntityGovernanceWorkspace({
        status: statusPayload,
        catalog,
        relations,
        history,
      }, { historyLimit: limit })
    },
  })
}

export const entityGovernanceApi = createEntityGovernanceApi()
