/**
 * 报告中心：合并 API 与本地收藏/预警，与 report-center 页面逻辑一致。
 */
import { API_PREFIX } from '@/config/api'
import { getFavoritesMap, getWarningsMap } from '@/utils/reportFavorites'

const API_ENDPOINTS = {
  favorites: `${API_PREFIX}/user/favorites`,
  newsByIds: `${API_PREFIX}/dashboard/news/by-ids`,
}

function getAuthHeaders() {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function fetchNewsByIds(ids) {
  const uniq = [...new Set(ids.map((x) => Number(x)).filter((n) => Number.isFinite(n)))].slice(0, 500)
  if (uniq.length === 0) return new Map()
  const byId = new Map()
  const chunk = 120
  for (let i = 0; i < uniq.length; i += chunk) {
    const part = uniq.slice(i, i + chunk)
    try {
      const res = await fetch(`${API_ENDPOINTS.newsByIds}?ids=${part.join(',')}`, {
        headers: { ...getAuthHeaders() },
      })
      if (!res.ok) continue
      const result = await res.json()
      const list = result.data || []
      for (const n of list) {
        const id = Number(n.id)
        if (Number.isFinite(id)) byId.set(id, n)
      }
    } catch {
      /* ignore chunk */
    }
  }
  return byId
}

function synthesizeItemsFromApiResponse(data) {
  let items = Array.isArray(data.items) ? [...data.items] : []
  if (items.length > 0) return items
  const rawIds = data.news_ids
  if (!Array.isArray(rawIds) || rawIds.length === 0) return items
  const favMap = getFavoritesMap()
  const warnMap = getWarningsMap()
  const seen = new Set()
  for (const nidRaw of rawIds) {
    const nid = Number(nidRaw)
    if (!Number.isFinite(nid)) continue
    const key = (topic, kind) => `${nid}|${topic}|${kind}`
    let assigned = false
    for (const [topic, list] of Object.entries(favMap)) {
      if (Array.isArray(list) && list.some((x) => Number(x?.id) === nid)) {
        const k = key(topic, 'favorite')
        if (!seen.has(k)) {
          seen.add(k)
          items.push({ news_id: nid, topic, kind: 'favorite' })
        }
        assigned = true
        break
      }
    }
    if (assigned) continue
    for (const [topic, list] of Object.entries(warnMap)) {
      if (Array.isArray(list) && list.some((x) => Number(x?.id) === nid)) {
        const k = key(topic, 'warning')
        if (!seen.has(k)) {
          seen.add(k)
          items.push({ news_id: nid, topic, kind: 'warning' })
        }
        assigned = true
        break
      }
    }
    if (!assigned) {
      const k = key('新闻分析主题', 'favorite')
      if (!seen.has(k)) {
        seen.add(k)
        items.push({ news_id: nid, topic: '新闻分析主题', kind: 'favorite' })
      }
    }
  }
  return items
}

function mergeFavoriteGroups(apiGroups, localGroups) {
  const topicMap = new Map()
  function absorb(groups) {
    for (const g of groups || []) {
      const t = g.topic || '未命名主题'
      if (!topicMap.has(t)) topicMap.set(t, new Map())
      const m = topicMap.get(t)
      for (const it of g.items || []) {
        const kind = String(
          it.__kind || (it.__tagType === 'warning' ? 'warning' : 'favorite'),
        ).toLowerCase()
        const id = Number(it.id)
        if (!Number.isFinite(id)) continue
        const mapKey = `${id}|${kind}`
        const prev = m.get(mapKey)
        const placeholder =
          !it.title ||
          it.title === '无标题' ||
          String(it.title).includes('列表中暂无') ||
          String(it.title).includes('加载中')
        if (!prev) {
          m.set(mapKey, it)
        } else {
          const prevPh =
            !prev.title ||
            prev.title === '无标题' ||
            String(prev.title).includes('列表中暂无') ||
            String(prev.title).includes('加载中')
          const merged =
            prevPh && !placeholder ? { ...prev, ...it } : !placeholder ? { ...it, ...prev } : { ...prev, ...it }
          m.set(mapKey, {
            ...merged,
            id,
            __topic: t,
            __kind: kind,
            __tagType: kind === 'warning' ? 'warning' : 'favorite',
          })
        }
      }
    }
  }
  absorb(apiGroups)
  absorb(localGroups)
  return Array.from(topicMap.entries())
    .map(([topic, im]) => ({ topic, items: Array.from(im.values()) }))
    .filter((g) => g.items.length)
}

function buildGroupsFromApiItems(items, newsById) {
  const byTopic = {}
  for (const it of items) {
    const nid = Number(it.news_id)
    if (!Number.isFinite(nid)) continue
    const tp =
      it.topic !== undefined && it.topic !== null && String(it.topic).trim() !== ''
        ? String(it.topic).trim()
        : '新闻分析主题'
    const kind = String(it.kind || 'favorite').toLowerCase() === 'warning' ? 'warning' : 'favorite'
    if (!byTopic[tp]) byTopic[tp] = []
    byTopic[tp].push({ news_id: nid, kind })
  }
  return Object.entries(byTopic).map(([topic, rows]) => ({
    topic,
    items: rows.map((r) => {
      const row = newsById.get(r.news_id)
      const base = row
        ? {
            ...row,
            id: Number(row.id),
            __topic: topic,
            __kind: r.kind,
            __tagType: r.kind === 'warning' ? 'warning' : 'favorite',
          }
        : {
            id: r.news_id,
            title: '（列表中暂无详情，可点进详情页）',
            abstract: '',
            source: '',
            pub_time: null,
            request_url: '#',
            __topic: topic,
            __kind: r.kind,
            __tagType: r.kind === 'warning' ? 'warning' : 'favorite',
          }
      return base
    }),
  }))
}

function buildGroupsFromLocalOnly() {
  const favMap = getFavoritesMap()
  const warnMap = getWarningsMap()
  const topics = new Set([...Object.keys(favMap), ...Object.keys(warnMap)])
  const groups = []
  for (const topic of topics) {
    const items = []
    const favList = Array.isArray(favMap[topic]) ? favMap[topic] : []
    const warnList = Array.isArray(warnMap[topic]) ? warnMap[topic] : []
    for (const x of favList) {
      const id = Number(x?.id)
      if (!Number.isFinite(id)) continue
      items.push({
        ...x,
        id,
        __topic: topic,
        __kind: 'favorite',
        __tagType: 'favorite',
      })
    }
    for (const x of warnList) {
      const id = Number(x?.id)
      if (!Number.isFinite(id)) continue
      items.push({
        ...x,
        id,
        __topic: topic,
        __kind: 'warning',
        __tagType: 'warning',
      })
    }
    if (items.length) groups.push({ topic, items })
  }
  return groups
}

/**
 * @returns {Promise<{ groups: Array<{ topic: string, items: any[] }>, errorMessage: string, infoMessage: string }>}
 */
export async function loadMergedFavoriteGroups() {
  let infoMessage = ''
  try {
    const token = localStorage.getItem('access_token')
    if (!token) {
      const groups = buildGroupsFromLocalOnly()
      if (groups.length === 0) {
        infoMessage = '登录后可与数据库同步；当前侧边栏收藏会保存在本机'
      }
      return { groups, errorMessage: '', infoMessage }
    }

    const res = await fetch(API_ENDPOINTS.favorites, { headers: { ...getAuthHeaders() } })
    if (!res.ok) {
      throw new Error(`获取收藏失败: ${res.status}`)
    }
    const data = await res.json()
    const items = synthesizeItemsFromApiResponse(data)
    const allIds = [...new Set(items.map((it) => Number(it.news_id)).filter((n) => Number.isFinite(n)))]
    const newsById = await fetchNewsByIds(allIds)
    const apiGroups = buildGroupsFromApiItems(items, newsById)
    const localGroups = buildGroupsFromLocalOnly()
    return { groups: mergeFavoriteGroups(apiGroups, localGroups), errorMessage: '', infoMessage: '' }
  } catch (error) {
    console.error('报告中心数据加载失败:', error)
    return {
      groups: buildGroupsFromLocalOnly(),
      errorMessage: error.message || '加载失败，请稍后重试',
      infoMessage: '',
    }
  }
}

/**
 * 按主题从合并后的收藏/预警中取条目（需先请求 loadMergedFavoriteGroups）。
 */
export async function loadItemsForReportTopic(topic) {
  const t = String(topic || '').trim()
  if (!t) return { items: [], errorMessage: '', infoMessage: '' }
  const { groups, errorMessage, infoMessage } = await loadMergedFavoriteGroups()
  const g = groups.find((x) => x.topic === t)
  return { items: g?.items ?? [], errorMessage, infoMessage }
}
