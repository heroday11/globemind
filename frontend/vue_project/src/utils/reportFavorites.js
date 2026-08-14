const STORAGE_BY_TOPIC = 'report_center_favorites_by_topic_v1'
const LEGACY_FLAT_KEY = 'report_center_favorites_v1'

function safeParse(json, fallback) {
  try {
    const v = JSON.parse(json)
    return v ?? fallback
  } catch {
    return fallback
  }
}

function migrateLegacyFlatIfNeeded(map) {
  if (Object.keys(map).length > 0) return map
  const raw = localStorage.getItem(LEGACY_FLAT_KEY)
  const list = safeParse(raw, [])
  if (!Array.isArray(list) || list.length === 0) return map
  const topic = '新闻分析主题'
  map[topic] = list.map((x) => ({ ...x, topic }))
  localStorage.removeItem(LEGACY_FLAT_KEY)
  return map
}

export function getFavoritesMap() {
  const raw = localStorage.getItem(STORAGE_BY_TOPIC)
  let map = safeParse(raw, {})
  if (!map || typeof map !== 'object' || Array.isArray(map)) map = {}
  map = migrateLegacyFlatIfNeeded(map)
  return map
}

export function setFavoritesMap(map) {
  const next = map && typeof map === 'object' && !Array.isArray(map) ? map : {}
  localStorage.setItem(STORAGE_BY_TOPIC, JSON.stringify(next))
  window.dispatchEvent(new CustomEvent('reportFavoritesUpdated'))
}

export function getReportFavoritesForTopic(topic) {
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = getFavoritesMap()
  const list = map[t]
  return Array.isArray(list) ? list : []
}

export function setReportFavoritesForTopic(topic, list) {
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = { ...getFavoritesMap() }
  map[t] = Array.isArray(list) ? list : []
  setFavoritesMap(map)
}

/** 兼容旧 API：返回当前主题下列表（默认主题） */
export function getReportFavorites(topic) {
  const t = topic != null ? String(topic).trim() || '新闻分析主题' : '新闻分析主题'
  return getReportFavoritesForTopic(t)
}

export function upsertReportFavorite(item, topic) {
  if (!item || item.id === undefined || item.id === null) return
  const id = Number(item.id)
  if (!Number.isFinite(id)) return
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = { ...getFavoritesMap() }
  const list = Array.isArray(map[t]) ? [...map[t]] : []
  const existsIdx = list.findIndex((x) => Number(x?.id) === id)
  const nextItem = { ...item, id, topic: t }
  if (existsIdx >= 0) {
    list[existsIdx] = { ...list[existsIdx], ...nextItem }
  } else {
    list.unshift(nextItem)
  }
  map[t] = list
  setFavoritesMap(map)
}

export function removeReportFavorite(newsId, topic) {
  const id = Number(newsId)
  if (!Number.isFinite(id)) return
  const t =
    topic != null && String(topic).trim() !== ''
      ? String(topic).trim()
      : null
  const map = { ...getFavoritesMap() }
  if (t) {
    const list = Array.isArray(map[t]) ? map[t].filter((x) => Number(x?.id) !== id) : []
    map[t] = list
  } else {
    for (const key of Object.keys(map)) {
      if (!Array.isArray(map[key])) continue
      map[key] = map[key].filter((x) => Number(x?.id) !== id)
    }
  }
  setFavoritesMap(map)
}

export function hasReportFavorite(newsId, topic) {
  const id = Number(newsId)
  if (!Number.isFinite(id)) return false
  const list = getReportFavoritesForTopic(topic || '新闻分析主题')
  return list.some((x) => Number(x?.id) === id)
}

// ---------- 预警（按主题），与收藏同样结构 ----------

const WARN_STORAGE_BY_TOPIC = 'data_search_warnings_by_topic_v1'
const WARN_LEGACY_FLAT = 'data_search_warning_cards_v1'

function migrateWarnLegacy(map) {
  if (Object.keys(map).length > 0) return map
  const raw = localStorage.getItem(WARN_LEGACY_FLAT)
  const list = safeParse(raw, [])
  if (!Array.isArray(list) || list.length === 0) return map
  map['新闻分析主题'] = list.map((x) => ({ ...x, topic: '新闻分析主题' }))
  localStorage.removeItem(WARN_LEGACY_FLAT)
  return map
}

export function getWarningsMap() {
  const raw = localStorage.getItem(WARN_STORAGE_BY_TOPIC)
  let map = safeParse(raw, {})
  if (!map || typeof map !== 'object' || Array.isArray(map)) map = {}
  map = migrateWarnLegacy(map)
  return map
}

export function setWarningsMap(map) {
  const next = map && typeof map === 'object' && !Array.isArray(map) ? map : {}
  localStorage.setItem(WARN_STORAGE_BY_TOPIC, JSON.stringify(next))
  window.dispatchEvent(new CustomEvent('reportFavoritesUpdated'))
}

export function getWarningsForTopic(topic) {
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = getWarningsMap()
  const list = map[t]
  return Array.isArray(list) ? list : []
}

export function setWarningsForTopic(topic, list) {
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = { ...getWarningsMap() }
  map[t] = Array.isArray(list) ? list : []
  setWarningsMap(map)
}

export function upsertWarningCard(item, topic) {
  if (!item || item.id === undefined || item.id === null) return
  const id = Number(item.id)
  if (!Number.isFinite(id)) return
  const t = String(topic || '').trim() || '新闻分析主题'
  const map = { ...getWarningsMap() }
  const list = Array.isArray(map[t]) ? [...map[t]] : []
  const idx = list.findIndex((x) => Number(x?.id) === id)
  const row = {
    ...item,
    id,
    topic: t,
    cardType: 'warning',
  }
  if (idx >= 0) list[idx] = { ...list[idx], ...row }
  else list.unshift(row)
  map[t] = list
  setWarningsMap(map)
}

export function removeWarningCard(newsId, topic) {
  const id = Number(newsId)
  if (!Number.isFinite(id)) return
  const t =
    topic != null && String(topic).trim() !== '' ? String(topic).trim() : null
  const map = { ...getWarningsMap() }
  if (t) {
    map[t] = (Array.isArray(map[t]) ? map[t] : []).filter((x) => Number(x?.id) !== id)
  } else {
    for (const key of Object.keys(map)) {
      if (!Array.isArray(map[key])) continue
      map[key] = map[key].filter((x) => Number(x?.id) !== id)
    }
  }
  setWarningsMap(map)
}

/** 删除整个主题下本地收藏与预警（不请求服务器） */
export function deleteThemeData(topic) {
  const t = String(topic || '').trim()
  if (!t) return
  const fav = { ...getFavoritesMap() }
  delete fav[t]
  setFavoritesMap(fav)
  const w = { ...getWarningsMap() }
  delete w[t]
  setWarningsMap(w)
}
