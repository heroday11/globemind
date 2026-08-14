import { formatDateYmd, formatIndexValue } from './presentation.js'

export const PRESET_DAYS_MAP = Object.freeze({
  '7d': 7,
  '30d': 30,
  '90d': 90,
  '365d': 365,
})

function finiteOptionalNumber(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function trimTrendData(trend) {
  const dates = Array.isArray(trend?.dates) ? trend.dates : []
  const values = Array.isArray(trend?.values)
    ? trend.values.map(finiteOptionalNumber)
    : []
  if (!dates.length) return { dates: [], values: [] }
  const zeroThreshold = 0.01
  let start = 0
  while (
    start < values.length
    && (values[start] === null || Math.abs(values[start]) < zeroThreshold)
  ) start += 1
  let end = values.length - 1
  while (
    end >= 0
    && (values[end] === null || Math.abs(values[end]) < zeroThreshold)
  ) end -= 1
  if (start > end) return { dates: [], values: [] }
  return { dates: dates.slice(start, end + 1), values: values.slice(start, end + 1) }
}

export function findAnomalyPoints(data) {
  const dates = data?.dates || []
  const values = (data?.values || []).map(finiteOptionalNumber)
  if (dates.length < 4 || values.length < 4) return []
  const differences = values.slice(1).map((value, index) => (
    value === null || values[index] === null ? null : value - values[index]
  ))
  const absolute = differences.map(Math.abs).filter(Number.isFinite)
  if (!absolute.length) return []
  const average = absolute.reduce((sum, value) => sum + value, 0) / absolute.length
  const variance = absolute.reduce((sum, value) => sum + (value - average) ** 2, 0) / absolute.length
  const threshold = Math.max(6, average + Math.sqrt(variance) * 1.25)
  return differences
    .map((delta, index) => (delta === null ? null : {
        date: dates[index + 1],
        index: index + 1,
        value: values[index + 1],
        delta,
      }))
    .filter(Boolean)
    .filter((point) => Math.abs(point.delta) >= threshold)
    .sort((left, right) => Math.abs(right.delta) - Math.abs(left.delta))
    .slice(0, 8)
}

export function normalizeResultDate(value) {
  if (!value) return ''
  const raw = String(value).trim()
  const direct = raw.match(/^(\d{4}-\d{2}-\d{2})/)
  if (direct) return direct[1]
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? '' : formatDateYmd(parsed)
}

export function buildSearchChartMarkPoints(searchResults, data) {
  if (!searchResults?.length || !data?.dates?.length || !data?.values?.length) return []
  const values = data.values.map(finiteOptionalNumber)
  const counts = new Map()
  for (const item of searchResults) {
    const date = normalizeResultDate(item.pub_time || item.published_at || item.created_at)
    if (date) counts.set(date, (counts.get(date) || 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([date, count]) => {
      const index = data.dates.indexOf(date)
      if (index < 0 || values[index] === null) return null
      return {
        name: '搜索命中',
        coord: [index, values[index]],
        value: values[index],
        date,
        count,
        searchHit: true,
        symbol: 'circle',
        symbolSize: Math.min(44, 24 + count * 4),
        itemStyle: { color: '#f97316', borderColor: '#fff', borderWidth: 2 },
      }
    })
    .filter(Boolean)
}

export function selectDatePoint(date, data, anomalyPoints = []) {
  const dates = data?.dates || []
  const values = (data?.values || []).map(finiteOptionalNumber)
  const index = dates.indexOf(date)
  const value = index >= 0 ? values[index] : null
  const previous = index > 0 ? values[index - 1] : null
  return {
    date,
    value,
    delta: value !== null && previous !== null ? value - previous : null,
    anomaly: anomalyPoints.some((point) => point.date === date),
  }
}

export function currentRangeDays(timeRange, customDays) {
  if (timeRange === 'custom') {
    const days = Number(customDays)
    return Number.isFinite(days) && days > 0 ? Math.min(365, Math.floor(days)) : 30
  }
  return PRESET_DAYS_MAP[String(timeRange)] || 30
}

export function rangeIndexes(dates, { customDays, endDate, timeRange } = {}) {
  if (!dates?.length) return { startIndex: 0, endIndex: 0, total: 0 }
  const safeEnd = endDate && dates.includes(endDate) ? endDate : dates[dates.length - 1]
  const endIndex = Math.max(0, dates.indexOf(safeEnd))
  const startIndex = Math.max(0, endIndex - currentRangeDays(timeRange, customDays) + 1)
  return { startIndex, endIndex, total: dates.length - 1 }
}

export function dataZoomRange(indexes) {
  if (!indexes || indexes.total <= 0) return { start: 0, end: 100 }
  return {
    start: (indexes.startIndex / indexes.total) * 100,
    end: (indexes.endIndex / indexes.total) * 100,
  }
}

export function yAxisRange(values, startPercent, endPercent) {
  if (!values?.length) return { min: -10, max: 10 }
  const total = values.length - 1
  const startIndex = Math.max(0, Math.floor((startPercent / 100) * total))
  const endIndex = Math.min(total, Math.ceil((endPercent / 100) * total))
  const visible = values
    .slice(startIndex, endIndex + 1)
    .map(finiteOptionalNumber)
    .filter((value) => value !== null)
  if (!visible.length) return { min: -10, max: 10 }
  const minimum = Math.min(...visible)
  const maximum = Math.max(...visible)
  const padding = Math.max((maximum - minimum) * 0.2, 8)
  return { min: Math.floor(minimum - padding), max: Math.ceil(maximum + padding) }
}

export function sparklinePoints(values, width = 120, height = 36) {
  if (!values || values.length < 2) return ''
  const normalized = values.map(finiteOptionalNumber)
  if (normalized.some((value) => value === null)) return ''
  const minimum = Math.min(...normalized)
  const maximum = Math.max(...normalized)
  const range = maximum - minimum || 1
  return normalized
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width
      const y = height - ((value - minimum) / range) * height * 0.85 - height * 0.075
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

export function resolveChartPointDate(params, data) {
  if (params?.componentType !== 'markPoint') return params?.name || ''
  const raw = params?.data?.coord?.[0]
  if (typeof raw === 'number') return data?.dates?.[raw] || ''
  if (params?.data?.type === 'max' || params?.data?.type === 'min') {
    const values = (data?.values || []).map(finiteOptionalNumber)
    const available = values.filter((value) => value !== null)
    if (!available.length) return ''
    const target = params.data.type === 'max' ? Math.max(...available) : Math.min(...available)
    return data?.dates?.[values.indexOf(target)] || ''
  }
  return String(raw || '')
}

export function buildSentimentChartOption({
  anomalyPoints = [],
  data = { dates: [], values: [] },
  end = 100,
  searchMarkPoints = [],
  start = 0,
  yRange = { min: -10, max: 10 },
} = {}) {
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#1e293b' } },
      valueFormatter: (value) => (value != null ? Number(value).toFixed(1) : ''),
    },
    grid: { left: '3%', right: '4%', bottom: 78, top: 52, containLabel: true },
    yAxis: {
      type: 'value',
      min: yRange.min,
      max: yRange.max,
      splitLine: { lineStyle: { color: 'rgba(91,114,223,0.12)' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      data: data.dates,
      axisLine: {
        onZero: true,
        symbol: ['none', 'arrow'],
        symbolSize: [10, 15],
        lineStyle: { color: '#7a8ccf', width: 1.5 },
      },
      axisLabel: { color: '#60709b' },
    },
    dataZoom: [
      {
        type: 'slider',
        show: data.dates.length > 1,
        start,
        end,
        bottom: 14,
        height: 34,
        z: 8,
        filterMode: 'none',
        showDetail: true,
        brushSelect: false,
        textStyle: { color: '#60709b' },
        borderColor: 'rgba(91, 114, 223, 0.2)',
        fillerColor: 'rgba(91, 114, 223, 0.18)',
        dataBackground: {
          lineStyle: { color: '#a8b5e0', width: 1 },
          areaStyle: { color: '#eaf0ff', opacity: 1 },
        },
        selectedDataBackground: {
          lineStyle: { color: '#5b72df', width: 2 },
          areaStyle: { color: '#5b72df', opacity: 0.22 },
        },
        handleIcon:
          'path://M10.7,11.9v-1.3H9.3v1.3c-4.9,0.3-8.8,4.4-8.8,9.4c0,5,3.9,9.1,8.8,9.4v1.3h1.3v-1.3c4.9-0.3,8.8-4.4,8.8-9.4C19.5,16.3,15.6,12.2,10.7,11.9z M13.3,24.4H6.7V23h6.6V24.4z M13.3,19.6H6.7v-1.4h6.6V19.6z',
        handleSize: '80%',
        handleStyle: {
          color: '#5b72df',
          shadowBlur: 4,
          shadowColor: 'rgba(91,114,223,0.35)',
          borderWidth: 0,
        },
      },
      {
        type: 'inside',
        start,
        end,
        zoomOnMouseWheel: false,
        moveOnMouseWheel: true,
      },
    ],
    visualMap: {
      show: false,
      pieces: [
        { gt: 0, lte: 99999, color: '#5b72df' },
        { gte: -99999, lte: 0, color: '#ef4444' },
      ],
    },
    series: [
      {
        name: '加权目标立场指数',
        type: 'line',
        smooth: 0.3,
        symbolSize: 6,
        showSymbol: false,
        animation: false,
        emphasis: { focus: 'series', showSymbol: true },
        lineStyle: { width: 3 },
        areaStyle: { opacity: 0.15 },
        markPoint: {
          symbol: 'pin',
          symbolSize: 50,
          label: {
            color: '#fff',
            fontSize: 10,
            formatter: (params) => {
              if (params.data?.searchHit) return String(params.data.count || 1)
              if (params.data?.delta != null) return `Δ${formatIndexValue(params.data.delta)}`
              return params.value != null ? Number(params.value).toFixed(1) : ''
            },
          },
          tooltip: {
            formatter: (params) => {
              if (params.data?.searchHit) {
                return `${params.data.date}<br/>当前页搜索命中 ${params.data.count} 条<br/>加权目标立场指数 ${formatIndexValue(params.value)} 指数点`
              }
              return `${params.name}<br/>加权目标立场指数 ${formatIndexValue(params.value)} 指数点`
            },
          },
          data: [
            { type: 'max', name: '支持立场极值', itemStyle: { color: '#5b72df' } },
            { type: 'min', name: '批评立场极值', itemStyle: { color: '#ef4444' } },
            ...anomalyPoints.map((point) => ({
              name: point.delta >= 0 ? '异常上行' : '异常下行',
              coord: [point.index, point.value],
              value: point.value,
              delta: point.delta,
              itemStyle: { color: point.delta >= 0 ? '#2563eb' : '#dc2626' },
            })),
            ...searchMarkPoints,
          ],
        },
        data: data.values,
      },
    ],
  }
}
