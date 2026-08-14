const MAX_EXTERNAL_URL_LENGTH = 2048
const EXPERT_SKILL_DOCUMENT_PREFIX = '/datasets/expert-skills/'

function containsUnsafeUrlCharacter(value) {
  if (value.includes('\\')) return true
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0)
    return codePoint <= 31 || codePoint === 127
  })
}

export function safeExternalHttpUrl(value) {
  if (typeof value !== 'string') return ''
  if (!value || value.length > MAX_EXTERNAL_URL_LENGTH || value.trim() !== value) return ''
  if (containsUnsafeUrlCharacter(value)) return ''

  let parsed
  try {
    parsed = new URL(value)
  } catch {
    return ''
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) return ''
  if (!parsed.hostname || parsed.username || parsed.password) return ''
  return parsed.href
}

export function safeExpertSkillDocumentPath(value) {
  if (typeof value !== 'string') return ''
  if (!value || value.length > MAX_EXTERNAL_URL_LENGTH || value.trim() !== value) return ''
  if (containsUnsafeUrlCharacter(value) || value.includes('?') || value.includes('#')) return ''

  let decoded
  try {
    decoded = decodeURIComponent(value)
  } catch {
    return ''
  }
  if (decoded !== value || containsUnsafeUrlCharacter(decoded)) return ''
  if (!decoded.startsWith(EXPERT_SKILL_DOCUMENT_PREFIX) || !decoded.endsWith('.md')) return ''
  const relativeSegments = decoded.slice(EXPERT_SKILL_DOCUMENT_PREFIX.length).split('/')
  if (relativeSegments.some((segment) => !segment || segment === '.' || segment === '..')) return ''

  const parsed = new URL(value, 'https://globemind.invalid')
  if (parsed.origin !== 'https://globemind.invalid' || parsed.pathname !== value) return ''
  return parsed.pathname
}
