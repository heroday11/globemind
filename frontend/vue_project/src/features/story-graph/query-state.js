export function createLatestRequestGate() {
  let generation = 0
  return Object.freeze({
    begin() {
      generation += 1
      return generation
    },
    invalidate() {
      generation += 1
      return generation
    },
    isCurrent(token) {
      return token === generation
    },
  })
}

function modeItemLabel(mode) {
  return mode === 'l2' ? '走势链' : '大事件'
}

export function buildStoryGraphQueryStatus(kind, {
  count = 0,
  mode = 'l3',
  query = '',
  storyId = '',
} = {}) {
  const itemLabel = modeItemLabel(mode)
  const normalizedQuery = String(query || '').trim()
  const normalizedCount = Math.max(0, Number(count) || 0)
  const messages = {
    idle: `正在准备 ${itemLabel}库。`,
    'list-loading': `正在加载 ${itemLabel}库，旧图谱已清空。`,
    'list-empty': `当前批次没有可展示的${itemLabel}，画布与统计已清空。`,
    'workspace-empty': `所选${itemLabel}没有可展示节点，未保留上一次图谱、统计或结论。`,
    'search-empty': `当前查询“${normalizedQuery}”没有匹配的${itemLabel}，未沿用上一次图谱、统计或结论。`,
    'selection-required': normalizedQuery
      ? `当前查询“${normalizedQuery}”找到 ${normalizedCount} 条，请选择一条查看；画布不会沿用查询前内容。`
      : `已加载 ${normalizedCount} 条${itemLabel}，请选择一条查看。`,
    'workspace-loading': `正在加载${itemLabel}${storyId ? ` ${storyId}` : ''}，旧图谱已清空。`,
    ready: `当前图谱对应所选${itemLabel}${storyId ? ` ${storyId}` : ''}。`,
    error: `当前${itemLabel}查询失败，画布、统计和结论已清空。请稍后重试。`,
  }
  const normalizedKind = Object.hasOwn(messages, kind) ? kind : 'idle'
  return Object.freeze({
    kind: normalizedKind,
    message: messages[normalizedKind],
    role: normalizedKind === 'error' ? 'alert' : 'status',
  })
}
