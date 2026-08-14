import axios from 'axios'

import { getToken } from '../../../utils/auth.js'

export const PIPELINE_MONITOR_ENDPOINTS = Object.freeze({
  snapshot: '/api/ops/pipeline-monitor',
  fastSnapshot: '/api/ops/pipeline-monitor/fast',
  runtimeCatalog: '/api/ops/runtime-catalog',
})

export function buildPipelineMonitorAuthHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function createPipelineMonitorApi({
  httpClient = axios,
  getAccessToken = getToken,
  endpoints = PIPELINE_MONITOR_ENDPOINTS,
} = {}) {
  if (typeof httpClient?.get !== 'function') {
    throw new TypeError('Pipeline monitor API requires an HTTP client')
  }

  function accessToken() {
    return getAccessToken?.() || ''
  }

  function requestHeaders() {
    return buildPipelineMonitorAuthHeaders(accessToken())
  }

  return Object.freeze({
    endpoints,

    hasAccessToken() {
      return Boolean(accessToken())
    },

    async fetchSnapshot({ fresh = false, signal } = {}) {
      const response = await httpClient.get(endpoints.snapshot, {
        params: fresh ? { fresh: true } : {},
        headers: requestHeaders(),
        signal,
      })
      return response.data
    },

    async fetchFastSnapshot({ signal } = {}) {
      const response = await httpClient.get(endpoints.fastSnapshot, {
        headers: requestHeaders(),
        signal,
      })
      return response.data
    },

    async fetchRuntimeCatalog({ signal } = {}) {
      const response = await httpClient.get(endpoints.runtimeCatalog, {
        headers: requestHeaders(),
        signal,
      })
      return response.data
    },
  })
}

export const pipelineMonitorApi = createPipelineMonitorApi()
