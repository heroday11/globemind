import { reactive, readonly } from 'vue'
import {
  DEFAULT_DISPLAY_PREFERENCES,
  DISPLAY_PREFERENCES_EVENT,
  FONT_FAMILY_OPTIONS,
  FONT_SIZE_OFFSET_MAX,
  FONT_SIZE_OFFSET_MIN,
  LINE_HEIGHT_OPTIONS,
  applyDisplayPreferences,
  loadDisplayPreferences,
  normalizeDisplayPreferences,
  resolveDisplayPreferencesStorageKey,
  saveDisplayPreferences,
} from '@globemind/shared/display-preferences'
import { getAuthChangedEventName } from '@/utils/auth'

function resolveStorage() {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

const storage = resolveStorage()
let activeStorageKey = resolveDisplayPreferencesStorageKey(storage)
const preferences = reactive(loadDisplayPreferences(storage, activeStorageKey))
let initialized = false

function replacePreferences(value) {
  const normalized = normalizeDisplayPreferences(value)
  Object.assign(preferences, normalized)
  return normalized
}

function dispatchPreferences(value) {
  if (typeof window === 'undefined' || typeof window.CustomEvent !== 'function') return
  window.dispatchEvent(
    new CustomEvent(DISPLAY_PREFERENCES_EVENT, {
      detail: { ...normalizeDisplayPreferences(value) },
    }),
  )
}

function applyAndNotify(value) {
  const normalized = replacePreferences(value)
  applyDisplayPreferences(normalized)
  dispatchPreferences(normalized)
  return normalized
}

function reloadForCurrentAccount() {
  const nextStorageKey = resolveDisplayPreferencesStorageKey(storage)
  if (nextStorageKey === activeStorageKey) return
  activeStorageKey = nextStorageKey
  applyAndNotify(loadDisplayPreferences(storage, activeStorageKey))
}

function handleStorage(event) {
  if (event.storageArea && event.storageArea !== storage) return
  if (event.key !== activeStorageKey) return
  applyAndNotify(loadDisplayPreferences(storage, activeStorageKey))
}

export function initializeDisplayPreferences() {
  applyDisplayPreferences(preferences)
  if (initialized || typeof window === 'undefined') return
  initialized = true
  window.addEventListener('storage', handleStorage)
  window.addEventListener(getAuthChangedEventName(), reloadForCurrentAccount)
}

export function updateDisplayPreferences(patch) {
  const normalized = normalizeDisplayPreferences({ ...preferences, ...patch })
  saveDisplayPreferences(normalized, storage, activeStorageKey)
  return applyAndNotify(normalized)
}

export function resetDisplayPreferences() {
  return updateDisplayPreferences(DEFAULT_DISPLAY_PREFERENCES)
}

export function getDisplayPreferencesSnapshot() {
  return normalizeDisplayPreferences(preferences)
}

export function useDisplayPreferences() {
  return {
    preferences: readonly(preferences),
    updateDisplayPreferences,
    resetDisplayPreferences,
  }
}

export {
  DEFAULT_DISPLAY_PREFERENCES,
  DISPLAY_PREFERENCES_EVENT,
  FONT_FAMILY_OPTIONS,
  FONT_SIZE_OFFSET_MAX,
  FONT_SIZE_OFFSET_MIN,
  LINE_HEIGHT_OPTIONS,
}
