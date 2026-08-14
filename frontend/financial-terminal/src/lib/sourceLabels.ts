import type { DashboardData, DataSourceStatus, FreshnessStatus, TrustReason, TrustStatus } from '../types'

const SOURCE_LABELS: Record<string, string> = {
  Composite: '综合模型',
  'MFA/UN': '外交部/联合国',
  'IEA/EIA': '能源机构',
  'GDELT / ACLED': '新闻事件/冲突数据',
  'EIA / IEA / 航运': '能源/航运',
  'IMF / World Bank': '国际货币基金/世界银行',
  'Trade/AIS': '贸易/航运',
  'Patent/Policy': '专利/政策',
  'Media/Social': '媒体/社媒',
  'GDELT/OpenSky/WB': '新闻/航空/宏观',
  'GDELT/OpenSky/USGS/EIA/World Bank': '新闻/航空/地震/宏观',
  'GDELT/OpenSky/USGS/EIA/World Bank/NOAA/EONET': '新闻/航空/地震/宏观/空间天气',
  'Ground News/GDELT/OpenSky/USGS/EIA/World Bank/NOAA/EONET': '事件图谱/新闻/航空/地震/宏观',
  'Ground News/GDELT/OpenSky/USGS/EIA/World Bank/NOAA/EONET/GDACS/EPSS/UN Sanctions': '事件图谱/新闻/航空/地震/灾害/漏洞/制裁',
  'Ground News/GDELT': '事件图谱/全球新闻',
  'Ground News/GDELT/USGS/World Bank': '事件图谱/安全新闻/地震/军费',
  'Ground News/GDELT/USGS/World Bank/UN Sanctions': '事件图谱/安全新闻/地震/军费/制裁',
  'Ground News/EIA/GDELT': '事件图谱/能源/全球新闻',
  'Ground News/OpenSky/GDELT': '事件图谱/航空/供应链新闻',
  'Ground News/OpenAlex/NVD/CISA/GDELT': '事件图谱/论文/漏洞/安全事件',
  'Ground News/OpenAlex/NVD/CISA/EPSS/GDELT': '事件图谱/论文/漏洞/利用概率/安全事件',
  'Ground News/USGS/OpenAQ/NASA FIRMS/GDELT': '事件图谱/地震/空气/灾害',
  'Ground News/USGS/OpenAQ/NASA FIRMS/GDELT/GDACS': '事件图谱/地震/空气/灾害告警',
  GDELT: '全球新闻事件',
  'GDELT/EIA': '新闻/能源',
  'OpenSky/GDELT': '航空/新闻',
  'OpenAlex/NVD': '论文/漏洞',
  'OpenAlex/NVD/CISA/GDELT': '论文/漏洞/安全事件',
  'GDELT/OpenAQ': '新闻/环境',
  'USGS/OpenAQ/NASA FIRMS/GDELT': '地震/空气/灾害事件',
  'GDELT 2.1 DOC': '全球新闻事件流',
  'Ground News 本地事件图谱': '本地事件图谱',
  'World Bank GDP growth': '世界银行：全球经济增速',
  'World Bank GDP deflator': '世界银行：价格压力',
  'OpenAlex technology': '开放论文库：科技热度',
  'OpenAlex：AI 与半导体': '开放论文库：AI 与半导体',
  'OpenAlex：量子计算': '开放论文库：量子计算',
  'OpenAlex：生物技术': '开放论文库：生物技术',
  'OpenAlex：气候能源': '开放论文库：气候能源',
  'OpenSky Network': '开放航空运行数据',
  'USGS Earthquake': '美国地调局：地震监测',
  USGS: '全球地震监测',
  'NVD CVE': '美国漏洞库',
  'CISA KEV': '已知被利用漏洞',
  'EIA Open Data': '美国能源数据',
  OpenAQ: '开放空气质量',
  'NASA FIRMS': '美国航天局卫星火点',
  'NASA EONET': '美国航天局自然事件',
  'GDACS Disaster Alerts': '全球灾害告警协调系统',
  GDACS: '全球灾害告警',
  'FIRST EPSS': '漏洞利用概率',
  'UN Security Council Sanctions': '联合国安理会制裁清单',
  'NOAA SWPC': '美国海洋大气局空间天气',
  'NOAA SWPC Kp': '美国海洋大气局 Kp 指数',
  'GDELT 24h': '全球新闻 24 小时',
  'GDELT + USGS': '新闻事件/地震监测',
  'World Bank': '世界银行',
  OpenSky: '开放航空数据',
  'OpenAlex 30d': '开放论文近 30 日',
  NVD: '漏洞库',
  EIA: '能源数据',
  'Front-end mock': '本地模拟数据',
  'Live API fallback': '接口回退数据',
}

export function sourceLabel(value?: string | null): string {
  if (!value) return '未知来源'
  return SOURCE_LABELS[value] || value
}

export function sourceDetailLabel(value?: string | null): string {
  if (!value) return '等待数据'
  return value
    .replace('connected', '已连接')
    .replace('missing EIA_API_KEY', '未配置能源数据 key')
    .replace('missing OPENAQ_API_KEY', '未配置空气质量 key')
    .replace('missing NASA_FIRMS_MAP_KEY', '未配置卫星火点 key')
    .replace('front-end generated mock data', '本地生成的模拟数据')
    .replace('live API unavailable', '真实接口暂不可用')
    .replace(/Client error '?429 Too Many Requests'?.*/i, '外部接口限流，等待下次同步')
    .replace('Client error 429 Too Many Requests', '外部接口限流，暂缓同步')
    .replace('ConnectTimeout', '连接超时，等待下次同步')
    .replace('TimeoutError', '同步超时，等待下次同步')
    .replace('invalid JSON: Queries containing OR\'d terms must be surrounded by ().', '外部查询语法被拒绝，已降级等待修正')
    .replace('dashboard is loading', '正在同步数据')
    .replace('latest global news/event articles', '最新全球新闻事件')
    .replace('global VIIRS fires 24h', '近 24 小时全球卫星火点')
    .replace('open natural events', '个开放自然事件')
    .replace(/^(\d+) open natural events$/, '$1 个开放自然事件')
    .replace(/^(\d+) clusters\/7d, (\d+) raw articles\/24h, latest (.+)$/i, '近 7 日 $1 个事件簇，24 小时 $2 条原始新闻，最新 $3')
    .replace(/^(\d+) alerts\/24h, (\d+) high impact, latest (.+)$/i, '24 小时 $1 个灾害告警，$2 个高影响，最新 $3')
    .replace(/^(\d+) alerts\/24h, (\d+) high impact$/i, '24 小时 $1 个灾害告警，$2 个高影响')
    .replace(/^(\d+) CVEs >=0\.95 EPSS, (\d+) >=0\.99, latest (.+)$/i, '$1 个 CVE 利用概率 >=0.95，$2 个 >=0.99，最新 $3')
    .replace(/^(\d+) entries, (\d+) updated 30d, generated (.+)$/i, '$1 个制裁对象，近 30 日更新 $2 个，生成于 $3')
    .replace('7d published CVEs', '近 7 日新增漏洞')
    .replace('added in 30d', '近 30 日新增')
    .replace('30d works matching artificial intelligence OR semiconductor', '近 30 日 AI/半导体论文')
    .replace('airborne states', '架飞行中')
    .replace('max magnitude', '最高震级')
}

export function statusLabel(status?: DataSourceStatus['status']): string {
  if (status === 'live') return '可用'
  if (status === 'degraded') return '降级'
  if (status === 'disabled') return '未启用'
  if (status === 'mock') return '模拟'
  if (status === 'unavailable') return '不可用'
  return '未知'
}

export function freshnessLabel(status?: FreshnessStatus): string {
  if (status === 'live') return '新鲜度达标'
  if (status === 'delayed') return '延迟数据'
  if (status === 'stale') return '历史快照'
  if (status === 'offline') return '数据离线'
  if (status === 'mock') return '模拟数据'
  return '等待新鲜度判定'
}

export function trustLabel(status?: TrustStatus): string {
  if (status === 'trusted') return '门禁允许计算'
  if (status === 'limited') return '受限计算'
  if (status === 'unavailable') return '不可计算'
  if (status === 'mock') return '模拟演示'
  return '等待门禁判定'
}

export function trustReasonLabel(reason?: TrustReason): string {
  if (!reason) return '数据暂未满足计算门禁。'
  const labels: Record<string, string> = {
    STALE_DASHBOARD_CACHE: '聚合缓存已过期',
    CRITICAL_INPUT_MISSING: '关键输入缺失',
    CRITICAL_INPUT_STALE: '关键输入已过期',
    INSUFFICIENT_SOURCE_COVERAGE: '有效来源覆盖不足',
    DASHBOARD_REQUEST_FAILED: '聚合接口请求失败',
    ALERT_REQUEST_FAILED: '预警接口请求失败',
    EXPLICIT_MOCK_MODE: '当前为显式模拟模式',
    LIVE_API_UNAVAILABLE: '真实接口不可用',
  }
  return labels[reason.code] || reason.message || reason.code
}

export function cadenceLabel(value?: string | null): string {
  if (!value) return '按源更新'
  const map: Record<string, string> = {
    'near-real-time': '高频更新',
    continuous: '持续',
    annual: '年度',
    daily: '每日',
    local: '本地',
    live: '持续更新',
    '5m': '5 分钟',
    '10m': '10 分钟',
    '15m': '15 分钟',
    '30m': '30 分钟',
    '12h': '12 小时',
    '1h': '1 小时',
    '2h': '2 小时',
    '1d': '每日',
    '3d': '3 天',
    '1w': '每周',
    '5m-annual': '5 分钟/年度混合',
    '15m-daily': '15 分钟/每日混合',
    '15m-annual': '15 分钟/年度混合',
  }
  return map[value] || value
}

export function modeLabel(mode?: DashboardData['mode']): string {
  if (mode === 'live') return '数据可用'
  if (mode === 'delayed') return '延迟数据'
  if (mode === 'historical') return '历史快照'
  if (mode === 'unavailable') return '不可计算'
  if (mode === 'mock-fallback') return '接口回退'
  if (mode === 'mock') return '模拟数据'
  return '同步中'
}
