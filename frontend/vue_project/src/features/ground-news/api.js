import axios from 'axios'

import { API_PREFIX, resolveApiOrigin } from '../../config/api.js'
import { normalizeGroundNewsTimeline } from './timeline.js'

function defaultApiRoot() {
  return `${resolveApiOrigin()}${API_PREFIX}`
}

export function buildGroundNewsListParams({
  pageSize = 24,
  minArticles,
  minSources,
  dateDays,
  sort,
  quality,
  includeFirstDetail = true,
  query,
  eventFamily,
} = {}) {
  return {
    page_size: pageSize,
    min_articles: minArticles,
    min_sources: minSources,
    date_days: dateDays,
    sort,
    quality,
    include_first_detail: includeFirstDetail,
    q: query || undefined,
    event_family: eventFamily || undefined,
  }
}

export function createGroundNewsApi({ client = axios, apiRoot = defaultApiRoot() } = {}) {
  const storyGraphRoot = `${apiRoot}/story-graph`
  const groundNewsRoot = `${storyGraphRoot}/ground-news`

  return Object.freeze({
    async listStories(filters) {
      const response = await client.get(`${groundNewsRoot}/list`, {
        params: buildGroundNewsListParams(filters),
      })
      return response.data
    },

    async getStory(clusterId) {
      const response = await client.get(`${groundNewsRoot}/${encodeURIComponent(clusterId)}`)
      return response.data
    },

    async getHome({ candidateLimit = 260, minArticles = 2 } = {}) {
      const response = await client.get(`${groundNewsRoot}/home`, {
        params: { candidate_limit: candidateLimit, min_articles: minArticles },
      })
      return response.data
    },

    async getBlindspots({ pageSize = 80 } = {}) {
      const response = await client.get(`${groundNewsRoot}/blindspots`, {
        params: { page_size: pageSize },
      })
      return response.data
    },

    async getTopic(topic, { pageSize = 80 } = {}) {
      const response = await client.get(`${groundNewsRoot}/topic/${encodeURIComponent(topic)}`, {
        params: { page_size: pageSize },
      })
      return response.data
    },

    async getSource(domain) {
      const response = await client.get(`${groundNewsRoot}/source/${encodeURIComponent(domain)}`)
      return response.data
    },

    async getTimeline(chainId) {
      const response = await client.get(`${groundNewsRoot}/timeline/${encodeURIComponent(chainId)}`)
      return normalizeGroundNewsTimeline(response.data)
    },

    async getL2Chain(chainId) {
      const response = await client.get(
        `${storyGraphRoot}/l2-chain/${encodeURIComponent(chainId)}`,
      )
      return normalizeGroundNewsTimeline(response.data)
    },
  })
}

export const groundNewsApi = createGroundNewsApi()
