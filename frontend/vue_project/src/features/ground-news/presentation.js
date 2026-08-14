import { storyRelationLabel } from '../../governance/storyRelations.js'

export const familyFilters = [
  { label: '全部', value: '' },
  { label: '外交', value: 'diplomacy' },
  { label: '安全', value: 'military_security' },
  { label: '经贸', value: 'economic_trade' },
  { label: '政治', value: 'domestic_politics' },
  { label: '法律', value: 'law_policy' },
]

export const sortOptions = [
  { label: '媒体关注', value: 'importance' },
  { label: '最新', value: 'recent' },
  { label: '多来源', value: 'coverage' },
]

export const dateRangeOptions = [
  { label: '30 天', value: 30 },
  { label: '120 天', value: 120 },
  { label: '1 年', value: 365 },
  { label: '全部', value: 0 },
]

export const minArticleOptions = [
  { label: '2+', value: 2 },
  { label: '5+', value: 5 },
  { label: '10+', value: 10 },
]

export const minSourceOptions = [
  { label: '1+', value: 1 },
  { label: '2+', value: 2 },
  { label: '3+', value: 3 },
]

export const qualityOptions = [
  { label: 'Ready', value: 'ready' },
  { label: '可用', value: 'usable' },
  { label: '全部', value: 'all' },
]

export const groundNewsTabs = [
  { key: 'coverage', label: '报道切面' },
  { key: 'timeline', label: '时间线' },
  { key: 'sources', label: '来源目录' },
  { key: 'l2', label: '关联线索' },
]

export const biasModel = [
  { key: 'left', label: '目录：偏左', color: '#2f7dd1' },
  { key: 'center', label: '目录：中间', color: '#2ca66f' },
  { key: 'right', label: '目录：偏右', color: '#d77b2a' },
  { key: 'state_aligned', label: '目录：国家关联', color: '#7a6a55' },
  { key: 'unknown', label: '目录：未评级', color: '#aab3bd' },
]

export const sourceColumnsModel = [
  { key: 'left', label: '第三方目录：偏左', color: '#2f7dd1' },
  { key: 'center', label: '第三方目录：中间', color: '#2ca66f' },
  { key: 'right', label: '第三方目录：偏右', color: '#d77b2a' },
  { key: 'state_aligned', label: '第三方目录：国家关联', color: '#7a6a55' },
  { key: 'unknown', label: '第三方目录：未评级', color: '#aab3bd' },
]

export function newsDetailPath(newsId) {
  return `/data-service/news/${encodeURIComponent(newsId)}`
}

export function sourcePath(domain) {
  if (!domain) return '/data-service/ground-news'
  return `/data-service/ground-news-source/${encodeURIComponent(domain)}`
}

export function coverImageUrl(story) {
  return story?.cover?.image_url || story?.cover_url || ''
}

export function coverCredit(story) {
  return story?.cover?.credit || story?.cover_credit || '来源见详情'
}

export function coverTheme(story) {
  const themes = {
    diplomacy: 'diplomacy',
    military_security: 'security',
    economic_trade: 'markets',
    technology_industry: 'technology',
    domestic_politics: 'civic',
    civil_unrest: 'civic',
    law_policy: 'policy',
    public_development: 'public',
    human_rights_migration: 'border',
    disaster_environment: 'climate',
  }
  return themes[story?.event_family] || 'global'
}

export function coverLabel(story) {
  if (coverImageUrl(story)) return '新闻图片'
  return familyLabel(story?.event_family)
}

export function coverShort(story) {
  const text = story?.location || story?.initiator || story?.target || familyLabel(story?.event_family)
  return String(text || 'Global').slice(0, 20)
}

export function normalizeLeaning(value) {
  const text = String(value || '')
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
  if (text.includes('state') || text.includes('government')) return 'state_aligned'
  if (text.includes('left')) return 'left'
  if (text.includes('right')) return 'right'
  if (
    text.includes('center')
    || text.includes('centre')
    || text.includes('least')
    || text.includes('mixed')
    || text.includes('neutral')
  ) return 'center'
  return 'unknown'
}

export function normalizeBiasBuckets(value) {
  const normalized = {}
  for (const [key, raw] of Object.entries(value || {})) {
    const leaning = normalizeLeaning(key)
    normalized[leaning] = (normalized[leaning] || 0) + Number(raw || 0)
  }
  const rows = biasModel.map((bucket) => ({
    ...bucket,
    value: Math.max(0, Number(normalized[bucket.key] || 0)),
  }))
  const total = rows.reduce((sum, row) => sum + row.value, 0)
  if (!total) return rows.map((row) => ({ ...row, value: row.key === 'unknown' ? 100 : 0 }))
  return rows.map((row) => ({ ...row, value: (row.value / total) * 100 }))
}

export function listBiasBuckets(story) {
  return normalizeBiasBuckets(
    story?.political_group_pct_reviewed_known_sources || {},
  ).filter((row) => row.value > 0)
}

export function topEntries(value, limit) {
  return Object.entries(value || {})
    .map(([key, raw]) => ({ key, value: Number(raw) || 0 }))
    .sort((left, right) => right.value - left.value)
    .slice(0, limit)
}

export function formatRange(start, end) {
  if (!start && !end) return '无日期'
  if (start && end && String(start).slice(0, 10) !== String(end).slice(0, 10)) {
    return `${String(start).slice(0, 10)} -> ${String(end).slice(0, 10)}`
  }
  return String(start || end).slice(0, 10)
}

export function formatDate(value) {
  if (!value) return '无日期'
  return String(value).slice(0, 10)
}

export function formatPct(value) {
  const numberValue = Number(value || 0)
  return numberValue >= 10 ? numberValue.toFixed(0) : numberValue.toFixed(1)
}

export function qualityPct(value) {
  void value
  return '质量指标未知'
}

export function sourceTypeLabel(value) {
  const labels = {
    state_media: '国家媒体',
    wire_service: '通讯社',
    business_media: '商业财经',
    global_major_media: '全球主流',
    public_broadcaster: '公共广播',
    national_major_media: '全国主流',
    regional_major_media: '地区媒体',
    independent_media: '独立媒体',
  }
  return labels[value] || codeLabel(value)
}

export function angleLabel(value) {
  const labels = {
    main_event: '核心事件',
    context_update: '背景进展',
    market_reaction: '市场反应',
    outcome_reaction: '后续反应',
    analysis_context: '分析解读',
    preview_planning: '事前铺垫',
    official_update: '官方更新',
    video_clip: '视频片段',
  }
  return labels[value] || codeLabel(value, '其他')
}

export function familyLabel(value) {
  const labels = {
    diplomacy: '外交',
    military_security: '安全',
    economic_trade: '经贸',
    technology_industry: '科技产业',
    domestic_politics: '国内政治',
    law_policy: '法律政策',
    human_rights_migration: '人权 / 移民',
    public_development: '公共发展',
    disaster_environment: '灾害环境',
  }
  return labels[value] || codeLabel(value)
}

export function familyGroupLabel(value) {
  const labels = {
    diplomacy: '外交',
    military_security: '安全',
    economic_security: '经济安全',
    domestic_politics: '国内政治',
    technology_industry: '科技产业',
    public_development: '公共发展',
  }
  return labels[value] || familyLabel(value)
}

export function domainLabel(value) {
  const labels = {
    political: '政治相关',
    geopolitical: '地缘政治',
    economic: '经济相关',
    technology: '科技相关',
    security: '安全相关',
  }
  return labels[value] || codeLabel(value)
}

export function eventActionLabel(value) {
  const labels = {
    meeting_visit: '会晤 / 访问',
    agreement_signed: '签署协议',
    sanction_export_control: '制裁 / 出口管制',
    military_attack: '军事攻击',
    ceasefire_negotiation: '停火谈判',
    policy_announcement: '政策发布',
    legal_ruling: '法律裁决',
    protest_demonstration: '抗议示威',
    election_campaign: '选举 / 竞选',
    appointment_resignation: '任命 / 辞职',
  }
  return labels[value] || codeLabel(value, '动作未知')
}

export function toneLabel(value) {
  const labels = {
    neutral: '中性',
    positive: '正面',
    negative: '负面',
    mixed: '混合',
    escalating: '升级',
    deescalating: '降温',
  }
  return labels[value] || codeLabel(value)
}

export function credibilityLabel(value) {
  const labels = {
    high: '目录标签：高',
    medium: '目录标签：中',
    low: '目录标签：低',
    unknown: '目录标签：未知',
  }
  return labels[value] || codeLabel(value)
}

export function ownershipLabel(value) {
  const labels = {
    public: '公共机构',
    private: '私营',
    state: '国有',
    government: '政府',
    wire_service: '通讯社',
    nonprofit: '非营利',
    unknown: '所有权未知',
  }
  return labels[value] || codeLabel(value)
}

export function biasGroupLabel(value) {
  const labels = {
    left: '左翼 / 偏左',
    center: '中间 / 低偏见',
    right: '右翼 / 偏右',
    state_aligned: '国家立场',
    unknown: '未评级',
  }
  return labels[normalizeLeaning(value)] || '未评级'
}

export function blindspotLevelLabel(value) {
  void value
  return 'Blindspot 状态未知'
}

export function statusLabel(value) {
  const labels = {
    ready: '目录处理完成（非质量通过）',
    not_built: '未构建',
    single_source: '单一信源',
    low_source_count: '信源偏少',
    missing_political_ratings: '缺少立场评级',
  }
  return labels[value] || codeLabel(value)
}

export function qualityLabel(value) {
  void value
  return '链质量未知'
}

export function edgeTypeLabel(value) {
  return storyRelationLabel(value)
}

export function riskFlagLabel(value) {
  const labels = {
    long_time_span: '时间跨度偏长',
    mixed_actions: '动作类型混合',
    low_similarity: '相似度偏低',
    sparse_chain: '链条偏稀疏',
    weak_edges: '边关系偏弱',
  }
  return labels[value] || codeLabel(value)
}

export function codeLabel(value, fallback = '未知') {
  if (!value) return fallback
  return String(value).replaceAll('_', ' / ')
}
