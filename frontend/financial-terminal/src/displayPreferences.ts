import {
  DISPLAY_PREFERENCES_EVENT,
  applyDisplayPreferences,
  loadDisplayPreferences,
  normalizeDisplayPreferences,
  resolveDisplayPreferencesStorageKey,
} from '@globemind/shared/display-preferences'

type PreferenceMessage = {
  type?: string
  preferences?: unknown
}

function resolveStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

const storage = resolveStorage()
let activeStorageKey = resolveDisplayPreferencesStorageKey(storage)

function apply(value: unknown) {
  applyDisplayPreferences(normalizeDisplayPreferences(value), document.documentElement)
}

function handleMessage(event: MessageEvent<PreferenceMessage>) {
  if (event.source !== window.parent || event.data?.type !== DISPLAY_PREFERENCES_EVENT) return
  apply(event.data.preferences)
}

function handleStorage(event: StorageEvent) {
  if (event.storageArea && event.storageArea !== storage) return
  if (event.key === 'current_user') {
    activeStorageKey = resolveDisplayPreferencesStorageKey(storage)
    apply(loadDisplayPreferences(storage, activeStorageKey))
    return
  }
  if (event.key === activeStorageKey) {
    apply(loadDisplayPreferences(storage, activeStorageKey))
  }
}

export function initializeDisplayPreferences() {
  apply(loadDisplayPreferences(storage, activeStorageKey))
  window.addEventListener('message', handleMessage)
  window.addEventListener('storage', handleStorage)
}
