// ============================================================
// Dashboard 数据 Hook — 从 API 获取（开发阶段 mock 回退）
// 通过定时轮询 API（或 mock 实时 tick）驱动行情更新
// ============================================================
import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  AlertRule,
  DashboardData,
  DataCoverage,
  DataSourceStatus,
  FinancialTrust,
  IndexCard,
  MaPoint,
  MetricSeries,
  OhlcBar,
  WatchRow,
} from '../types'
import { fetchDashboard } from '../api'
import { rollingMa } from '../lib/mockData'
import { useMock } from './useMock'

export function useMarketData() {
  const [bars, setBars] = useState<OhlcBar[]>([])
  const [indices, setIndices] = useState<IndexCard[]>([])
  const [watchlist, setWatchlist] = useState<WatchRow[]>([])
  const [series, setSeries] = useState<MetricSeries[]>([])
  const [sources, setSources] = useState<DataSourceStatus[]>([])
  const [alertRules, setAlertRules] = useState<AlertRule[]>([])
  const [coverage, setCoverage] = useState<DataCoverage | null>(null)
  const [trust, setTrust] = useState<FinancialTrust | null>(null)
  const [defaultMetricId, setDefaultMetricId] = useState<string | null>(null)
  const [mode, setMode] = useState<DashboardData['mode']>('unavailable')
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [cacheState, setCacheState] = useState<DashboardData['cache'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const mock = useMock()

  const load = useCallback(async (cancelledRef?: { current: boolean }) => {
    setLoading(true)
    try {
      const data = await fetchDashboard()
      if (cancelledRef?.current) return
      setBars(data.bars)
      setIndices(data.indices)
      setWatchlist(data.watchlist)
      setSeries(data.series || [])
      setDefaultMetricId(data.default_metric_id || null)
      setSources(data.sources || [])
      setAlertRules(data.alert_rules || [])
      setCoverage(data.coverage || null)
      setTrust(data.trust || null)
      setMode(data.mode || 'unavailable')
      setLastUpdated(data.last_updated || new Date().toISOString())
      setCacheState(data.cache || null)
      setError(data.mode === 'mock-fallback' ? '真实接口暂不可用，已使用本地回退数据。' : null)
    } catch {
      if (cancelledRef?.current) return
      setError('金融数据加载失败。')
      setBars([])
      setIndices((previous) => previous.map((index) => ({
        ...index,
        value: null,
        change_pct: null,
        spark: [],
        availability: 'not_computable',
        trust_status: 'unavailable',
        freshness_status: 'offline',
      })))
      setSeries((previous) => previous.map((metric) => metric.kind === 'index' ? {
        ...metric,
        latest: null,
        change_pct: null,
        points: [],
        availability: 'not_computable',
        trust_status: 'unavailable',
        freshness_status: 'offline',
        status: 'unavailable',
      } : metric))
      setAlertRules([])
      setMode('unavailable')
      setCacheState('stale')
      setTrust({
        schema_version: 'financial-trust-v1',
        snapshot_id: `client-dashboard-error-${Date.now()}`,
        trust_status: 'unavailable',
        freshness_status: 'offline',
        computability: 'not_computable',
        computable: false,
        data_as_of: null,
        evaluated_at: new Date().toISOString(),
        coverage_ratio: 0,
        minimum_coverage_ratio: 0.5,
        usable_sources: 0,
        source_total: 0,
        usable_source_ids: [],
        unavailable_source_ids: [],
        source_status: { offline: 1 },
        model_version: 'unknown',
        method_version: 'unknown',
        unavailable_reasons: [{ code: 'DASHBOARD_REQUEST_FAILED', message: '金融数据加载失败。' }],
        alerts_enabled: false,
      })
    } finally {
      if (!cancelledRef?.current) setLoading(false)
    }
  }, [])

  // 初始化加载；真实接口模式下周期性刷新，后端会用 TTL 缓存保护外部 API。
  useEffect(() => {
    const cancelledRef = { current: false }
    const pollMs = Number((import.meta as any).env?.VITE_FIN_DASHBOARD_POLL_MS || 60000)
    load(cancelledRef)
    if (mock) {
      return () => { cancelledRef.current = true }
    }
    const id = window.setInterval(() => load(cancelledRef), Math.max(15000, pollMs))
    return () => {
      cancelledRef.current = true
      window.clearInterval(id)
    }
  }, [load, mock])

  // Mock 模式下的实时 tick（每 2s 更新最后一根 K 线）
  const tick = useCallback(() => {
    if (!mock) return // 非 mock 模式由后端推送或轮询
    setBars((prev) => {
      if (!prev.length) return prev
      const next = [...prev]
      const last = { ...next[next.length - 1] }
      const delta = (Math.random() - 0.48) * 0.12
      const c = Math.max(60, last.close + delta)
      last.close = c
      last.high = Math.max(last.high, c, last.open)
      last.low = Math.min(last.low, c, last.open)
      last.volume = Math.max(50000, last.volume + Math.floor((Math.random() - 0.5) * 8000))
      next[next.length - 1] = last
      return next
    })
    setIndices((prev) =>
      prev.map((idx) => {
        if (idx.value === null || idx.change_pct === null) return idx
        const spark = [...idx.spark.slice(1), idx.value * (1 + (Math.random() - 0.5) * 0.0008)]
        const nv = idx.value * (1 + (Math.random() - 0.5) * 0.0006)
        const changePct = ((nv - spark[0]) / spark[0]) * 100
        return { ...idx, value: nv, spark, change_pct: changePct }
      }),
    )
    setWatchlist((prev) =>
      prev.map((w) => {
        const np = w.price * (1 + (Math.random() - 0.5) * 0.0012)
        const cp = w.change_pct + (Math.random() - 0.5) * 0.04
        return { ...w, price: np, change_pct: cp }
      }),
    )
    setSeries((prev) =>
      prev.map((metric) => {
        if (!metric.points.length || metric.latest === null) return metric
        const drift = (Math.random() - 0.5) * 0.006
        const nextValue = metric.latest * (1 + drift)
        const nextPoints = [...metric.points.slice(-23), { time: Math.floor(Date.now() / 1000), value: nextValue }]
        const first = nextPoints[0]?.value || nextValue
        return {
          ...metric,
          latest: nextValue,
          change_pct: first ? ((nextValue - first) / first) * 100 : 0,
          points: nextPoints,
        }
      }),
    )
  }, [mock])

  useEffect(() => {
    if (!mock) return
    const id = window.setInterval(tick, 2000)
    return () => window.clearInterval(id)
  }, [tick, mock])

  // MA 计算（缓存）
  const ma20 = useMemo<MaPoint[]>(() => rollingMa(bars, 20), [bars])
  const ma50 = useMemo<MaPoint[]>(() => rollingMa(bars, 50), [bars])
  const ma200 = useMemo<MaPoint[]>(() => rollingMa(bars, 200), [bars])

  return { bars, ma20, ma50, ma200, indices, watchlist, series, defaultMetricId, sources, alertRules, coverage, trust, mode, lastUpdated, cacheState, loading, error }
}
