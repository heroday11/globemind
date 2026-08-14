const FALLBACK_PATH = '/'
const MAX_REDIRECT_LENGTH = 2048
const INTERNAL_ORIGIN = 'https://globemind.invalid'
const AUTH_ENTRY_PATHS = new Set(['/login', '/register', '/forgot-password', '/reset-password'])

function hasUnsafeRedirectCharacters(value) {
  if (value.includes('\\')) return true
  return Array.from(value).some((character) => {
    const code = character.codePointAt(0)
    return code <= 31 || code === 127
  })
}

function normalizedPathname(pathname) {
  const normalized = String(pathname || '/').toLowerCase()
  return normalized === '/' ? normalized : normalized.replace(/\/+$/, '')
}

function decodedRedirect(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return null
  }
}

export function resolveSafeInternalRedirect(value, resolve) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > MAX_REDIRECT_LENGTH ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    hasUnsafeRedirectCharacters(value)
  ) {
    return FALLBACK_PATH
  }

  const decoded = decodedRedirect(value)
  if (
    decoded === null ||
    decoded.startsWith('//') ||
    hasUnsafeRedirectCharacters(decoded)
  ) {
    return FALLBACK_PATH
  }

  let parsed
  try {
    parsed = new URL(value, INTERNAL_ORIGIN)
  } catch {
    return FALLBACK_PATH
  }
  if (parsed.origin !== INTERNAL_ORIGIN || AUTH_ENTRY_PATHS.has(normalizedPathname(parsed.pathname))) {
    return FALLBACK_PATH
  }

  const target = `${parsed.pathname}${parsed.search}${parsed.hash}`
  if (typeof resolve === 'function') {
    let resolved
    try {
      resolved = resolve(target)
    } catch {
      return FALLBACK_PATH
    }
    if (
      !resolved ||
      resolved.name === 'NotFound' ||
      !Array.isArray(resolved.matched) ||
      resolved.matched.length === 0
    ) {
      return FALLBACK_PATH
    }
  }
  return target
}
