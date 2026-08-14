// ============================================================
// 模拟数据生成器（仅显式 mock 或开发兜底使用，生产默认不会静默回退）
// ============================================================
import type { OhlcBar, MaPoint, IndexCard, WatchRow, AlertRule, AlertReport } from '../types'
import { randomId } from './randomId'

// --------------- Dashboard 模拟数据 ---------------

export function mockOhlcSeries(count: number, seedPrice = 64): OhlcBar[] {
  const stepSec = 3600
  const start = Math.floor(Date.now() / 1000) - count * stepSec
  const out: OhlcBar[] = []
  let prevClose = seedPrice
  for (let i = 0; i < count; i++) {
    const eventPulse = i % 37 === 0 ? 1.15 : i % 53 === 0 ? -0.8 : 0
    const drift = (Math.random() - 0.48) * 0.58 + eventPulse
    const open = prevClose
    const close = Math.max(35, Math.min(95, open + drift + (Math.random() - 0.5) * 0.42))
    const high = Math.max(open, close) + Math.random() * 0.62
    const low = Math.min(open, close) - Math.random() * 0.62
    const volume = Math.floor(90000 + Math.random() * 180000 + Math.abs(eventPulse) * 90000)
    out.push({ time: start + i * stepSec, open, high, low, close, volume })
    prevClose = close
  }
  return out
}

export function rollingMa(bars: OhlcBar[], period: number): MaPoint[] {
  if (bars.length < period) return []
  const res: MaPoint[] = []
  for (let i = period - 1; i < bars.length; i++) {
    let s = 0
    for (let j = 0; j < period; j++) s += bars[i - j].close
    res.push({ time: bars[i].time, value: s / period })
  }
  return res
}

export function mockIndices(): IndexCard[] {
  const bases = [
    { id: 'wsi', name: '世界状态综合', value: 68.4, source: 'Composite' },
    { id: 'diplomacy', name: '外交温度', value: 42.7, source: 'MFA/UN' },
    { id: 'energy', name: '能源压力', value: 73.2, source: 'IEA/EIA' },
    { id: 'supply', name: '供应链扰动', value: 61.9, source: 'Trade/AIS' },
    { id: 'tech', name: '科技政策波动', value: 56.4, source: 'Patent/Policy' },
    { id: 'society', name: '社会舆论热度', value: 79.1, source: 'Media/Social' },
  ]
  return bases.map((b) => {
    const spark: number[] = []
    let v = b.value * 0.998
    for (let i = 0; i < 24; i++) {
      v *= 1 + (Math.random() - 0.48) * 0.0025
      spark.push(v)
    }
    const changePct = ((b.value - spark[0]) / spark[0]) * 100
    return { ...b, value: b.value, change_pct: changePct, spark }
  })
}

export function mockWatchlist(): WatchRow[] {
  return [
    { symbol: 'US-CN', label: '中美科技政策摩擦', price: 82.5, change_pct: 0.16, category: 'politics', region: '全球', source: '政策文本 / 出口管制', cadence: '15m' },
    { symbol: 'ME-CR', label: '中东冲突热度', price: 74.2, change_pct: 0.24, category: 'security', region: '中东', source: 'GDELT / ACLED', cadence: '10m' },
    { symbol: 'RU-UA', label: '俄乌安全态势', price: 67.8, change_pct: 0.55, category: 'security', region: '欧洲', source: 'OSINT / 新闻', cadence: '10m' },
    { symbol: 'TW-ST', label: '台海风险温度', price: 58.3, change_pct: 0.08, category: 'security', region: '东亚', source: '公开通报 / 舆情', cadence: '15m' },
    { symbol: 'DIP-G7', label: 'G7 外交一致度', price: 63.4, change_pct: -0.18, category: 'politics', region: '多边', source: '公报 / 投票', cadence: '1h' },
    { symbol: 'UN-VOTE', label: '联合国投票阵营漂移', price: 47.9, change_pct: 0.31, category: 'politics', region: '多边', source: 'UN Digital Library', cadence: '1d' },
    { symbol: 'OIL-ST', label: '油气供应压力', price: 71.6, change_pct: 0.44, category: 'energy', region: '全球', source: 'EIA / IEA / 航运', cadence: '30m' },
    { symbol: 'GRAIN', label: '粮食价格压力', price: 52.8, change_pct: -0.06, category: 'energy', region: '全球南方', source: 'FAO / 期货', cadence: '1h' },
    { symbol: 'RARE-E', label: '关键矿产出口风险', price: 69.7, change_pct: 0.39, category: 'energy', region: '亚太', source: '海关 / 政策', cadence: '1d' },
    { symbol: 'PMI-G', label: '全球制造景气', price: 49.8, change_pct: -0.22, category: 'economy', region: '全球', source: 'PMI / OECD', cadence: '1d' },
    { symbol: 'FX-ST', label: '新兴市场汇率压力', price: 65.2, change_pct: 0.27, category: 'economy', region: 'EM', source: '央行 / 市场', cadence: '15m' },
    { symbol: 'DEBT-R', label: '主权债务风险', price: 57.1, change_pct: 0.12, category: 'economy', region: '全球南方', source: 'IMF / World Bank', cadence: '1d' },
    { symbol: 'CHIP-C', label: '半导体链路扰动', price: 76.3, change_pct: 0.48, category: 'logistics', region: '东亚', source: '企业公告 / 海关', cadence: '1h' },
    { symbol: 'PORT-C', label: '关键港口拥堵', price: 54.5, change_pct: -0.1, category: 'logistics', region: '全球', source: 'AIS / 港口数据', cadence: '30m' },
    { symbol: 'SEA-LN', label: '红海航线风险', price: 81.4, change_pct: 0.74, category: 'logistics', region: '红海', source: 'AIS / 航运报告', cadence: '10m' },
    { symbol: 'AI-REG', label: 'AI 监管变化强度', price: 59.4, change_pct: 0.22, category: 'science', region: '欧美亚', source: '监管网站 / 法案', cadence: '1d' },
    { symbol: 'PAT-COMP', label: '关键技术专利竞争', price: 62.2, change_pct: 0.09, category: 'science', region: '全球', source: '专利库 / 论文', cadence: '1w' },
    { symbol: 'BIO-SEC', label: '生物安全关注度', price: 45.8, change_pct: -0.04, category: 'science', region: '全球', source: 'WHO / 论文', cadence: '1d' },
    { symbol: 'MEDIA-H', label: '跨国媒体对抗热度', price: 72.9, change_pct: 0.61, category: 'society', region: '全球', source: '新闻 / 社媒', cadence: '10m' },
    { symbol: 'MIG-P', label: '迁移压力指数', price: 66.7, change_pct: 0.2, category: 'society', region: '欧洲 / 中东', source: 'UNHCR / 新闻', cadence: '1d' },
    { symbol: 'FOOD-R', label: '食品安全舆情风险', price: 51.3, change_pct: -0.11, category: 'society', region: '全球', source: 'FAO / 媒体', cadence: '1d' },
  ]
}

// --------------- Alert 模拟数据 ---------------

export function mockAlertRules(): AlertRule[] {
  return [
    { id: '1', metric: '媒体对抗烈度', unit: '%', current: 28.4, threshold: 25, baseline: 18, severity: 'high', breached: true, trend: 'up' },
    { id: '2', metric: '外交双边温度', unit: '', current: 0.72, threshold: 0.8, baseline: 1.0, severity: 'medium', breached: false, trend: 'down' },
    { id: '3', metric: '政策协同度', unit: '', current: 0.31, threshold: 0.45, baseline: 0.35, severity: 'low', breached: false, trend: 'flat' },
    { id: '4', metric: '地缘冲突概率', unit: '%', current: 4.2, threshold: 3.5, baseline: 2.8, severity: 'high', breached: true, trend: 'up' },
    { id: '5', metric: '供应链中断指数', unit: 'index', current: 8.6, threshold: 10, baseline: 5, severity: 'medium', breached: false, trend: 'up' },
    { id: '6', metric: '极值制裁风险', unit: '%', current: 2.1, threshold: 2.5, baseline: 1.4, severity: 'low', breached: false, trend: 'up' },
  ]
}

export function generateAlertReport(rule: AlertRule): AlertReport {
  const msgs: Record<string, string> = {
    '地缘冲突概率': `地区冲突指数 突破高危阈值，当前值 ${rule.current.toFixed(2)}% 超出警戒线 ${rule.threshold}%，建议立即研判关注。`,
    '媒体对抗烈度': `全球媒体涉华负面波动率 突破高危阈值，当前值 ${rule.current.toFixed(2)}% 超出警戒线 ${rule.threshold}%，建议立即关注。`,
  }
  const eventTagsMap: Record<string, string[]> = {
    '地缘冲突概率': ['南海联合军演', '菲律宾外交部表态'],
    '媒体对抗烈度': ['#加征关税', '#半导体限制'],
    '外交双边温度': ['美日联合声明', 'G7 公报'],
    '供应链中断指数': ['稀土出口管制', '芯片法案'],
    '极值制裁风险': ['SDN清单更新', '出口管制新规'],
  }
  return {
    id: randomId(),
    metric: rule.metric,
    current: rule.current,
    threshold: rule.threshold,
    severity: rule.severity,
    triggered_at: new Date().toISOString(),
    message: msgs[rule.metric] ||
      (rule.severity === 'high'
        ? `${rule.metric} 突破高危阈值，当前值 ${rule.current.toFixed(2)}${rule.unit} 超出警戒线 ${rule.threshold}${rule.unit}，建议立即关注。`
        : `${rule.metric} 触发预警，当前值 ${rule.current.toFixed(2)}${rule.unit} 接近/超过阈值 ${rule.threshold}${rule.unit}`),
    eventTags: eventTagsMap[rule.metric] || undefined,
  }
}
