/**
 * 开发模式下内存中的用户收藏/预警（与 FastAPI user_favorite 列表语义一致）。
 * 进程重启后清空；与 localStorage 中的本地收藏由前端合并展示。
 */

/** @type {{ news_id: number, topic: string, kind: 'favorite' | 'warning' }[]} */
let favoriteRows = []

function normTopic(t) {
  const s = String(t ?? '').trim()
  return s || '新闻分析主题'
}

function normKind(k) {
  return String(k || 'favorite').toLowerCase() === 'warning' ? 'warning' : 'favorite'
}

export function getFavoritesListResponse() {
  const items = favoriteRows.map((r) => ({
    news_id: r.news_id,
    topic: r.topic,
    kind: r.kind,
    created_at: new Date().toISOString().slice(0, 19).replace('T', ' '),
  }))
  const news_ids = [...new Set(favoriteRows.map((r) => r.news_id))].sort((a, b) => a - b)
  return { items, news_ids }
}

export function mockFavoriteToggle(body) {
  const news_id = Number(body?.news_id)
  if (!Number.isFinite(news_id)) {
    return { status: 400, json: { detail: 'news_id 不能为空' } }
  }
  const topic = normTopic(body?.topic)
  const kind = normKind(body?.kind)
  const idx = favoriteRows.findIndex(
    (r) => r.news_id === news_id && r.topic === topic && r.kind === kind,
  )
  if (idx >= 0) {
    favoriteRows.splice(idx, 1)
    return { status: 200, json: { favorited: false } }
  }
  favoriteRows.push({ news_id, topic, kind })
  return { status: 200, json: { favorited: true } }
}

export function mockFavoriteRemove(body) {
  const news_id = Number(body?.news_id)
  if (!Number.isFinite(news_id)) {
    return { status: 400, json: { detail: 'news_id 不能为空' } }
  }
  const topic = normTopic(body?.topic)
  const kind = normKind(body?.kind)
  favoriteRows = favoriteRows.filter(
    (r) => !(r.news_id === news_id && r.topic === topic && r.kind === kind),
  )
  return { status: 200, json: { ok: true } }
}
