import axios from 'axios'

import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'
import { L2_RUN_ID, L3_RUN_ID } from './config.js'

function defaultApiRoot() {
  return `${resolveApiOrigin()}${API_PREFIX}`
}

function requireMode(mode) {
  if (mode !== 'l2' && mode !== 'l3') {
    throw new TypeError(`Unsupported story graph mode: ${mode}`)
  }
  return mode
}

export function buildStoryGraphListRequest(mode) {
  const selectedMode = requireMode(mode)
  if (selectedMode === 'l3') {
    return {
      path: '/story-graph/l3-macro/list',
      params: { page_size: 100, min_chains: 8, run_id: L3_RUN_ID },
    }
  }
  return {
    path: '/story-graph/l2-chain/list',
    params: { page_size: 100, min_segments: 2, run_id: L2_RUN_ID },
  }
}

export function buildStoryGraphWorkspaceRequest(mode, storyId) {
  const selectedMode = requireMode(mode)
  const resource = selectedMode === 'l3' ? 'l3-macro' : 'l2-chain'
  return {
    path: `/story-graph/${resource}/${encodeURIComponent(storyId)}`,
    params:
      selectedMode === 'l3'
        ? { run_id: L3_RUN_ID, max_nodes: 56 }
        : { run_id: L2_RUN_ID },
  }
}

export function buildStoryGraphEvidenceParams(payload, { currentL2RunId } = {}) {
  const params = { limit: 60 }
  if (payload?.kind === 'l3-chain' && payload.l2ChainId) {
    params.chain_id = payload.l2ChainId
    params.run_id = payload.l2RunId || currentL2RunId || L2_RUN_ID
  } else if (payload?.kind === 'l2-segment' && payload.segmentId) {
    params.segment_id = payload.segmentId
  } else {
    const clusterId = payload?.originalClusterId || payload?.l1ClusterId
    if (clusterId) params.cluster_id = clusterId
  }
  return params
}

export function hasStoryGraphEvidenceTarget(params) {
  return Boolean(params?.cluster_id || params?.segment_id || params?.chain_id)
}

export function createStoryGraphApi({
  client = axios,
  apiRoot = defaultApiRoot(),
  assistantApiRoot = API_PREFIX,
} = {}) {
  return Object.freeze({
    async listStories(mode, { signal } = {}) {
      const request = buildStoryGraphListRequest(mode)
      const response = await client.get(`${apiRoot}${request.path}`, {
        params: request.params,
        signal,
      })
      return response.data
    },

    async getWorkspace(mode, storyId, { signal } = {}) {
      const request = buildStoryGraphWorkspaceRequest(mode, storyId)
      const response = await client.get(`${apiRoot}${request.path}`, {
        params: request.params,
        signal,
      })
      return response.data
    },

    async getEvidence(params, { signal } = {}) {
      const response = await client.get(`${apiRoot}/story-graph/evidence`, { params, signal })
      return response.data
    },

    async getAssistantSessionMessages(sessionId, { token, signal } = {}) {
      const headers = { Accept: 'application/json' }
      if (token) headers.Authorization = `Bearer ${token}`
      const response = await client.get(
        `${assistantApiRoot}/assistant/sessions/${encodeURIComponent(sessionId)}/messages`,
        {
          headers,
          signal,
          withCredentials: true,
        },
      )
      return response.data
    },
  })
}

export const storyGraphApi = createStoryGraphApi()
