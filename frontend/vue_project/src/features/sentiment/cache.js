export const OPINION_CACHE_TTL_MS = 2 * 60 * 1000
export const OPINION_REFRESH_INTERVAL_MS = 10 * 60 * 1000
export const TREND_CACHE_KEY = 'globemind:sentiment-analysis:trend:v5'
export const OVERVIEW_CACHE_KEY = 'globemind:sentiment-analysis:overview:v5'

export function createSentimentSnapshotCache({
  now = Date.now,
  storage = globalThis.localStorage,
  ttlMs = OPINION_CACHE_TTL_MS,
} = {}) {
  return Object.freeze({
    read(key) {
      if (!storage) return null
      try {
        const parsed = JSON.parse(storage.getItem(key) || 'null')
        if (!parsed?.savedAt || now() - parsed.savedAt > ttlMs) {
          storage.removeItem?.(key)
          return null
        }
        return sanitizeOpinionPayload(parsed.payload, { now: now() })
      } catch {
        return null
      }
    },

    write(key, payload) {
      if (!storage) return false
      try {
        const savedAt = now()
        const sanitized = sanitizeOpinionPayload(payload, { now: savedAt })
        storage.setItem(key, JSON.stringify({ payload: sanitized, savedAt }))
        return true
      } catch {
        return false
      }
    },

    remove(key) {
      if (!storage) return false
      try {
        storage.removeItem?.(key)
        return true
      } catch {
        return false
      }
    },
  })
}
import { sanitizeOpinionPayload } from './presentation.js'
