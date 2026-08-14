const TOKEN_KEY = 'access_token'
const USER_KEY = 'current_user'
const AUTH_CHANGED_EVENT = 'auth-changed'

function sanitizeUserForStorage(value) {
  if (Array.isArray(value)) return value.map(sanitizeUserForStorage)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => {
        const normalized = key.toLowerCase()
        return !(
          normalized === 'api_key' ||
          normalized === 'api_keys' ||
          normalized === 'password' ||
          normalized === 'secret' ||
          normalized === 'token' ||
          normalized === 'access_token' ||
          normalized.endsWith('_api_key') ||
          normalized.endsWith('_password') ||
          normalized.endsWith('_secret')
        )
      })
      .map(([key, child]) => [key, sanitizeUserForStorage(child)])
  )
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (!token) return
  localStorage.setItem(TOKEN_KEY, token)
  window.dispatchEvent(new CustomEvent(AUTH_CHANGED_EVENT))
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  window.dispatchEvent(new CustomEvent(AUTH_CHANGED_EVENT))
}

export function setCurrentUser(user) {
  if (!user) {
    localStorage.removeItem(USER_KEY)
  } else {
    localStorage.setItem(USER_KEY, JSON.stringify(sanitizeUserForStorage(user)))
  }
  window.dispatchEvent(new CustomEvent(AUTH_CHANGED_EVENT))
}

export function getCurrentUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    const sanitized = sanitizeUserForStorage(parsed)
    const next = JSON.stringify(sanitized)
    // Remove full API keys left behind by older frontend builds.
    if (next !== raw) localStorage.setItem(USER_KEY, next)
    return sanitized
  } catch {
    localStorage.removeItem(USER_KEY)
    return null
  }
}

export function isLoggedIn() {
  return Boolean(getToken())
}

export function getAuthChangedEventName() {
  return AUTH_CHANGED_EVENT
}
