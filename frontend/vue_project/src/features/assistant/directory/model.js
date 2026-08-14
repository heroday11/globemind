export function formatDirectoryCount(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return '—'
  const text = typeof value === 'string' ? value.trim() : value
  if (text === '') return '—'
  const number = Number(text)
  return Number.isSafeInteger(number) && number >= 0 ? number.toLocaleString('en-US') : '—'
}

export function directoryLoadErrorText(error) {
  if (error?.status === 401 || error?.status === 403) {
    return '登录状态无效或无权读取站点与成员目录。'
  }
  return '站点与成员目录加载失败。'
}

export function siteDirectoryStatus(value) {
  if (value === 'active') return { label: '配置为启用', tone: 'configured' }
  if (value === 'paused') return { label: '配置为暂停', tone: 'paused' }
  if (value === 'error') return { label: '目录标记异常', tone: 'error' }
  return { label: '未登记状态', tone: 'unknown' }
}

export function formatDirectoryTimestamp(value) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?$/.test(text)) {
    return '—'
  }
  const candidate = text.includes('T') ? text : `${text.replace(' ', 'T')}Z`
  return Number.isFinite(Date.parse(candidate)) ? text.slice(0, 16).replace('T', ' ') : '—'
}

export function memberPresenceLabel(value) {
  if (value === true) return '目录标记：在线'
  if (value === false) return '目录标记：离线'
  return '目录标记：未知'
}
