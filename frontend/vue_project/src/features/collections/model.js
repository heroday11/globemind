const MAX_SERVER_RECORDS = 500
const MAX_BROWSER_RECORDS = 500
const MAX_TOPIC_LENGTH = 255
const MAX_TITLE_LENGTH = 500

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function requirePositiveNewsId(value) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > 2_147_483_647) {
    throw new TypeError('favorite news id is invalid')
  }
  return value
}

function normalizeTopic(value) {
  if (typeof value !== 'string') throw new TypeError('favorite topic is invalid')
  const topic = value.trim()
  const hasControlCharacter = [...topic].some((character) => {
    const codePoint = character.codePointAt(0)
    return codePoint < 32 || codePoint === 127
  })
  if (topic.length > MAX_TOPIC_LENGTH || hasControlCharacter) {
    throw new TypeError('favorite topic is invalid')
  }
  return topic
}

function requireCount(value) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new TypeError('favorite count is unavailable')
  }
  return value
}

export function normalizeServerFavorites(payload) {
  if (!isRecord(payload) || payload.schema_version !== 'user-favorites-v2') {
    throw new TypeError('favorite response contract is unavailable')
  }
  if (!Array.isArray(payload.items) || payload.items.length > MAX_SERVER_RECORDS) {
    throw new TypeError('favorite response items are unavailable or unbounded')
  }
  if (!Array.isArray(payload.news_ids) || !isRecord(payload.counts)) {
    throw new TypeError('favorite response summary is unavailable')
  }

  const exactKeys = new Set()
  const favoriteIds = new Set()
  const favoriteRecords = []
  let warningRecords = 0
  for (const raw of payload.items) {
    if (!isRecord(raw)) throw new TypeError('favorite response item is invalid')
    const newsId = requirePositiveNewsId(raw.news_id)
    const topic = normalizeTopic(raw.topic)
    if (raw.kind !== 'favorite' && raw.kind !== 'warning') {
      throw new TypeError('favorite response kind is invalid')
    }
    const key = `${newsId}\u0000${topic}\u0000${raw.kind}`
    if (exactKeys.has(key)) throw new TypeError('favorite response contains duplicates')
    exactKeys.add(key)
    if (raw.kind === 'warning') {
      warningRecords += 1
      continue
    }
    favoriteIds.add(newsId)
    favoriteRecords.push({ id: newsId, topic })
  }

  const projectedIds = payload.news_ids.map(requirePositiveNewsId)
  if (new Set(projectedIds).size !== projectedIds.length) {
    throw new TypeError('favorite response projection contains duplicates')
  }
  const expectedIds = [...favoriteIds].sort((left, right) => left - right)
  const actualIds = [...projectedIds].sort((left, right) => left - right)
  if (JSON.stringify(expectedIds) !== JSON.stringify(actualIds)) {
    throw new TypeError('favorite response projection is inconsistent')
  }

  const counts = {
    favoriteRecords: requireCount(payload.counts.favorite_records),
    warningRecords: requireCount(payload.counts.warning_records),
    invalidRecords: requireCount(payload.counts.invalid_records),
    distinctFavoriteNews: requireCount(payload.counts.distinct_favorite_news),
  }
  if (
    counts.favoriteRecords !== favoriteRecords.length
    || counts.warningRecords !== warningRecords
    || counts.distinctFavoriteNews !== favoriteIds.size
  ) {
    throw new TypeError('favorite response counts are inconsistent')
  }

  return Object.freeze({
    records: Object.freeze(favoriteRecords),
    counts: Object.freeze(counts),
  })
}

export function collectBrowserFavorites(map) {
  if (!isRecord(map)) {
    return Object.freeze({
      records: Object.freeze([]),
      invalidRecords: 0,
      truncated: false,
    })
  }
  const records = []
  let invalidRecords = 0
  let truncated = false
  for (const [rawTopic, entries] of Object.entries(map)) {
    if (!Array.isArray(entries)) continue
    let topic
    try {
      topic = normalizeTopic(rawTopic) || '新闻分析主题'
    } catch {
      invalidRecords += entries.length
      continue
    }
    for (const entry of entries) {
      if (records.length >= MAX_BROWSER_RECORDS) {
        truncated = true
        break
      }
      if (!isRecord(entry)) {
        invalidRecords += 1
        continue
      }
      try {
        const id = requirePositiveNewsId(entry.id)
        const title = typeof entry.title === 'string'
          ? entry.title.trim().slice(0, MAX_TITLE_LENGTH)
          : ''
        records.push({ id, title, topic })
      } catch {
        // Malformed browser-owned entries are ignored, never coerced into IDs.
        invalidRecords += 1
      }
    }
    if (truncated) break
  }
  return Object.freeze({
    records: Object.freeze(records),
    invalidRecords,
    truncated,
  })
}

export function mergeFavoriteSources(serverRecords, browserRecords, { limit = 80 } = {}) {
  if (!Array.isArray(serverRecords) || !Array.isArray(browserRecords)) {
    throw new TypeError('favorite sources must be arrays')
  }
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) {
    throw new RangeError('favorite display limit is invalid')
  }

  const byId = new Map()
  function upsert(raw, source) {
    if (!isRecord(raw)) throw new TypeError('favorite source record is invalid')
    const id = requirePositiveNewsId(raw.id)
    const topic = normalizeTopic(raw.topic)
    const previous = byId.get(id) || {
      id,
      title: '',
      topics: [],
      account: false,
      browser: false,
    }
    if (topic && !previous.topics.includes(topic)) previous.topics.push(topic)
    if (!previous.title && typeof raw.title === 'string') {
      previous.title = raw.title.trim().slice(0, MAX_TITLE_LENGTH)
    }
    previous[source] = true
    byId.set(id, previous)
  }

  for (const record of serverRecords) upsert(record, 'account')
  for (const record of browserRecords) upsert(record, 'browser')

  const allRows = [...byId.values()].map((row) => {
    let source = 'browser'
    let sourceLabel = '本机临时收藏'
    if (row.account && row.browser) {
      source = 'account+browser'
      sourceLabel = '账号与本机均有记录'
    } else if (row.account) {
      source = 'account'
      sourceLabel = '账号收藏'
    }
    return Object.freeze({
      id: row.id,
      title: row.title,
      topics: Object.freeze([...row.topics]),
      topic: row.topics.join(' · '),
      source,
      sourceLabel,
    })
  })
  return Object.freeze({
    rows: Object.freeze(allRows.slice(0, limit)),
    total: allRows.length,
    truncated: allRows.length > limit,
  })
}

export const COLLECTION_LIMITS = Object.freeze({
  browserRecords: MAX_BROWSER_RECORDS,
  serverRecords: MAX_SERVER_RECORDS,
})
