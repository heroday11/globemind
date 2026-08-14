import { useEffect, useMemo, useState } from 'react'
import TerminalHeader from '../components/TerminalHeader'
import SignalHistoryChart from '../components/SignalHistoryChart'
import DetailModal, { type DetailTarget } from '../components/DetailModal'
import { useMarketData } from '../hooks/useMarketData'
import { createPriceAlertLine, type PriceAlertLine } from '../lib/priceAlerts'
import { financialTrendDisclosurePresentation } from '../lib/trust'
import {
  cadenceLabel,
  freshnessLabel,
  modeLabel,
  sourceDetailLabel,
  sourceLabel,
  statusLabel,
  trustLabel,
  trustReasonLabel,
} from '../lib/sourceLabels'
import type { AlertRule, DataSourceStatus, IndexCard, MetricCategory, MetricSeries, WatchRow } from '../types'

type FilterId = 'all' | MetricCategory

const CATEGORY_TABS: { id: FilterId; label: string; hint: string }[] = [
  { id: 'all', label: '全部信号', hint: '总览' },
  { id: 'politics', label: '政治外交', hint: '新闻与协调' },
  { id: 'security', label: '安全冲突', hint: '冲突与抗议' },
  { id: 'economy', label: '经济宏观', hint: '长期基线' },
  { id: 'energy', label: '能源资源', hint: '油气与供应' },
  { id: 'logistics', label: '物流交通', hint: '航空与链路' },
  { id: 'science', label: '科技安全', hint: '论文与漏洞' },
  { id: 'society', label: '社会环境', hint: '灾害与空气' },
]

const CATEGORY_ACCENT: Record<MetricCategory, string> = {
  politics: 'bg-sky-500',
  security: 'bg-rose-500',
  economy: 'bg-emerald-500',
  energy: 'bg-amber-400',
  logistics: 'bg-cyan-500',
  science: 'bg-violet-500',
  society: 'bg-teal-500',
}

function categoryLabel(id?: FilterId | MetricCategory) {
  return CATEGORY_TABS.find((item) => item.id === id)?.label || '全部信号'
}

function formatMetricValue(metric?: MetricSeries | null) {
  if (!metric) return '—'
  if (metric.availability === 'not_computable' || metric.latest === null) {
    return metric.kind === 'index' ? '不可计算' : '暂无观测'
  }
  const decimals = metric.latest >= 100 ? 0 : metric.unit === '%' || metric.unit === 'Mw' ? 2 : 2
  const value = metric.latest.toLocaleString('zh-CN', { maximumFractionDigits: decimals, minimumFractionDigits: decimals })
  return metric.unit ? `${value} ${metric.unit}` : value
}

function statusTone(status?: DataSourceStatus['status']) {
  if (status === 'live') return 'bg-emerald-500'
  if (status === 'degraded') return 'bg-amber-400'
  if (status === 'disabled') return 'bg-slate-400'
  if (status === 'mock') return 'bg-blue-400'
  if (status === 'unavailable') return 'bg-rose-400'
  return 'bg-slate-300'
}

function relatedMetricIds(selectedMetric: MetricSeries | null, watchlist: WatchRow[]) {
  if (!selectedMetric?.category) return watchlist.slice(0, 4).map((row) => row.metric_id || row.symbol)
  return watchlist
    .filter((row) => row.category === selectedMetric.category && (row.metric_id || row.symbol) !== selectedMetric.id)
    .slice(0, 4)
    .map((row) => row.metric_id || row.symbol)
}

function hasSignalValue(metric?: MetricSeries | null) {
  if (!metric) return false
  if (metric.kind === 'index') return true
  if (metric.status === 'disabled') return false
  if (Math.abs(metric.latest || 0) > 0.0001) return true
  return metric.points.some((point) => Math.abs(point.value || 0) > 0.0001)
}

function hasMovement(values?: number[]) {
  if (!values || values.length < 2) return false
  const rounded = values.map((value) => Number(value || 0).toFixed(4))
  return new Set(rounded).size > 1
}

function isDisplayIndex(index: IndexCard, metric?: MetricSeries | null) {
  if (index.id === 'wsi') return true
  if (metric?.availability === 'not_computable') return true
  if (metric?.status === 'disabled') return false
  const values = metric?.points.map((point) => point.value) || index.spark
  if (metric?.status === 'degraded' && !hasMovement(values)) return false
  return hasMovement(values) || Math.abs(index.change_pct || 0) > 0.01
}

function isPrimaryRow(row: WatchRow, metric?: MetricSeries | null) {
  if (!metric) return row.status !== 'disabled' && Math.abs(row.price || 0) > 0.0001
  if (metric.status === 'disabled') return false
  if (metric.status === 'degraded' && !hasSignalValue(metric)) return false
  return hasSignalValue(metric)
}

function dataQuality(metric?: MetricSeries | null) {
  if (!metric) return '等待'
  if (metric.availability === 'not_computable' || metric.status === 'unavailable') return '不可计算'
  if (metric.status === 'disabled') return '待配置'
  if (!hasSignalValue(metric)) return '无观测'
  if (metric.status === 'degraded') return '降级'
  return '可用'
}

function isNearRealtimeCadence(cadence?: string) {
  const value = (cadence || '').toLowerCase()
  return ['near-real-time', '15m', '30m', '5m', '2h', '3h'].some((token) => value.includes(token))
}

function cacheLabel(cache?: string | null) {
  if (cache === 'stale') return '过期缓存（仅作历史展示）'
  if (cache === 'hit') return '后端缓存快照'
  if (cache === 'shared') return '共享缓存快照'
  if (cache === 'coalesced') return '聚合快照'
  if (cache === 'miss') return '新生成快照'
  return '缓存状态待确认'
}

function ruleSeverityClass(rule: AlertRule) {
  if (rule.severity === 'high') return 'border-rose-200 bg-rose-50 text-rose-700'
  if (rule.severity === 'medium') return 'border-amber-200 bg-amber-50 text-amber-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

function severityLabel(rule: AlertRule) {
  if (rule.severity === 'high') return '高'
  if (rule.severity === 'medium') return '中'
  return '低'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

export default function TerminalDashboard() {
  const { indices, watchlist, series, defaultMetricId, sources, alertRules, coverage, trust, mode, lastUpdated, cacheState, loading, error } = useMarketData()
  const [activeCategory, setActiveCategory] = useState<FilterId>('all')
  const [selectedMetricId, setSelectedMetricId] = useState<string | null>(null)
  const [signalQuery, setSignalQuery] = useState('')
  const [detailTarget, setDetailTarget] = useState<DetailTarget | null>(null)
  const [thresholdMap, setThresholdMap] = useState<Record<string, PriceAlertLine[]>>({})

  const seriesMap = useMemo(() => new Map(series.map((metric) => [metric.id, metric])), [series])
  const visibleIndices = useMemo(
    () => indices.filter((index) => isDisplayIndex(index, seriesMap.get(index.metric_id || `IDX-${index.id.toUpperCase()}`))),
    [indices, seriesMap],
  )
  const primaryWatchlist = useMemo(
    () => watchlist.filter((row) => isPrimaryRow(row, seriesMap.get(row.metric_id || row.symbol))),
    [seriesMap, watchlist],
  )
  const inactiveWatchlist = useMemo(
    () => watchlist.filter((row) => !isPrimaryRow(row, seriesMap.get(row.metric_id || row.symbol))),
    [seriesMap, watchlist],
  )
  const metricRows = useMemo(
    () => primaryWatchlist.filter((row) => activeCategory === 'all' || row.category === activeCategory),
    [activeCategory, primaryWatchlist],
  )
  const filteredMetricRows = useMemo(() => {
    const query = signalQuery.trim().toLowerCase()
    if (!query) return metricRows
    return metricRows.filter((row) => {
      const text = [
        row.label,
        row.symbol,
        row.metric_id,
        row.region,
        row.source,
        row.description,
        categoryLabel(row.category),
      ].filter(Boolean).join(' ').toLowerCase()
      return text.includes(query)
    })
  }, [metricRows, signalQuery])
  const categoryCounts = useMemo(() => {
    const counts = { all: primaryWatchlist.length } as Record<FilterId, number>
    CATEGORY_TABS.forEach((tab) => {
      if (tab.id === 'all') return
      counts[tab.id] = primaryWatchlist.filter((row) => row.category === tab.id).length
    })
    return counts
  }, [primaryWatchlist])
  const visibleCategoryTabs = useMemo(
    () => CATEGORY_TABS.filter((tab) => tab.id === 'all' || (categoryCounts[tab.id] || 0) > 0),
    [categoryCounts],
  )

  useEffect(() => {
    if (!series.length) return
    if (activeCategory !== 'all') {
      const withinCategory = metricRows.some((row) => (row.metric_id || row.symbol) === selectedMetricId)
      if (!withinCategory) {
        setSelectedMetricId(metricRows[0]?.metric_id || metricRows[0]?.symbol || null)
      }
      return
    }
    if (!selectedMetricId || !seriesMap.has(selectedMetricId)) {
      setSelectedMetricId(defaultMetricId || metricRows[0]?.metric_id || metricRows[0]?.symbol || series[0]?.id || null)
    }
  }, [activeCategory, defaultMetricId, metricRows, selectedMetricId, series, seriesMap])

  useEffect(() => {
    if (activeCategory !== 'all' && (categoryCounts[activeCategory] || 0) === 0) {
      setActiveCategory('all')
    }
  }, [activeCategory, categoryCounts])

  const selectedMetric = selectedMetricId ? seriesMap.get(selectedMetricId) || null : null
  const selectedWatchRow = useMemo(
    () => primaryWatchlist.find((row) => (row.metric_id || row.symbol) === selectedMetricId) || null,
    [primaryWatchlist, selectedMetricId],
  )
  const currentThresholds = selectedMetric ? thresholdMap[selectedMetric.id] || [] : []
  const relatedIds = useMemo(() => relatedMetricIds(selectedMetric, primaryWatchlist), [selectedMetric, primaryWatchlist])
  const relatedMetrics = useMemo(() => relatedIds.map((id) => seriesMap.get(id)).filter(Boolean) as MetricSeries[], [relatedIds, seriesMap])
  const liveSources = sources.filter((source) => source.status === 'live').length
  const degradedSources = sources.filter((source) => source.status === 'degraded').length
  const disabledSources = sources.filter((source) => source.status === 'disabled').length
  const politicsSecuritySignals = coverage?.politics_security_series ?? series.filter((metric) => metric.category === 'politics' || metric.category === 'security').length
  const nearRealtimeSignals = coverage?.near_realtime_series ?? series.filter((metric) => isNearRealtimeCadence(metric.cadence)).length
  const groundNewsSource = sources.find((source) => source.id === 'ground-news-local')
  const gdeltSource = sources.find((source) => source.id === 'gdelt')
  const activeSourceCount = coverage?.usable_sources ?? coverage?.live_sources ?? liveSources
  const totalSourceCount = trust?.source_total ?? coverage?.sources_total ?? sources.length
  const selectedCategoryName = selectedMetric?.id === 'IDX-WSI' ? '全部信号' : categoryLabel(selectedMetric?.category)
  const dashboardUnavailable = Boolean(
    !loading && trust && !trust.computable && trust.trust_status !== 'mock',
  )
  const selectedUnavailable = Boolean(
    selectedMetric?.kind === 'index'
      && (selectedMetric.availability === 'not_computable' || selectedMetric.latest === null || dashboardUnavailable),
  )
  const alertsEnabled = Boolean(trust?.alerts_enabled || trust?.trust_status === 'mock')
  const dataAsOf = trust?.data_as_of || coverage?.ground_news_latest_story_date || null
  const primaryTrustReason = trust?.unavailable_reasons?.[0]
  const coveragePercent = trust ? `${(trust.coverage_ratio * 100).toFixed(0)}%` : '待判定'
  const methodCard = asRecord(trust?.composite_method_card)
  const frequencyAlignment = asRecord(methodCard?.frequency_alignment)
  const methodApproval = methodCard?.approval_status === 'not_approved' ? '未批准' : '待确认'
  const dimensionalStatus = methodCard?.input_units_status === 'not_dimensionally_validated'
    ? '量纲未验证'
    : '量纲状态待确认'
  const selectedTrendDisclosure = financialTrendDisclosurePresentation(
    selectedMetric?.trend_disclosure,
  )
  const selectedChange = selectedTrendDisclosure.preciseChangeAllowed
    ? selectedMetric?.change_pct
    : null

  const handleSelectMetric = (metricId?: string) => {
    if (!metricId) return
    const metric = seriesMap.get(metricId)
    if (metricId === 'IDX-WSI') {
      setActiveCategory('all')
    } else if (metric?.category) {
      setActiveCategory(metric.category)
    }
    setSelectedMetricId(metricId)
  }

  const addThreshold = (value: number) => {
    if (!selectedMetric || !alertsEnabled || selectedUnavailable) return
    setThresholdMap((prev) => {
      const current = prev[selectedMetric.id] || []
      return {
        ...prev,
        [selectedMetric.id]: [...current, createPriceAlertLine(value, selectedMetric.label, current.length + 1)],
      }
    })
  }

  const removeThreshold = (thresholdId: string) => {
    if (!selectedMetric) return
    setThresholdMap((prev) => ({
      ...prev,
      [selectedMetric.id]: (prev[selectedMetric.id] || []).filter((line) => line.id !== thresholdId),
    }))
  }

  return (
    <div
      className="flex min-h-screen flex-col overflow-visible bg-[#f4f8fb] text-slate-900 xl:h-full xl:min-h-0 xl:overflow-x-hidden xl:overflow-y-auto"
      style={{ backgroundImage: 'linear-gradient(135deg, rgba(238,244,251,0.96), rgba(246,250,252,0.98))' }}
    >
      <DetailModal target={detailTarget} onClose={() => setDetailTarget(null)} />
      <TerminalHeader />

      <div className="flex flex-1 flex-col overflow-visible xl:min-h-0">
        <section className="shrink-0 border-b border-slate-200 bg-white/85 px-4 py-2 backdrop-blur md:px-5">
          <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h1 className="mr-1 text-base font-semibold tracking-tight text-slate-950">世界状态信号库</h1>
              <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${dashboardUnavailable ? 'bg-rose-50 text-rose-700' : trust?.freshness_status === 'delayed' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'}`}>
                {loading ? '同步中' : trust ? `${trustLabel(trust.trust_status)} · ${freshnessLabel(trust.freshness_status)}` : modeLabel(mode)}
              </span>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
                {primaryWatchlist.length} 个可用信号
              </span>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
                政治/安全 {politicsSecuritySignals}
              </span>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
                高频更新 {nearRealtimeSignals}
              </span>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-600">
                {activeSourceCount}/{totalSourceCount} 源可用
              </span>
            </div>
            <IndexTape indices={visibleIndices} activeMetricId={selectedMetricId} onSelectMetric={handleSelectMetric} />
          </div>
          {!loading ? (
            <div
              role="status"
              className={`mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-xs ${dashboardUnavailable ? 'border-rose-200 bg-rose-50 text-rose-800' : trust?.freshness_status === 'delayed' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-600'}`}
            >
              <span className="font-semibold">{trust ? trustLabel(trust.trust_status) : modeLabel(mode)}</span>
              <span>{trust ? freshnessLabel(trust.freshness_status) : '等待新鲜度判定'}</span>
              <span>数据截止 {dataAsOf ? dataAsOf.slice(0, 19).replace('T', ' ') + ' UTC' : '未知'}</span>
              <span>有效覆盖 {coveragePercent}</span>
              <span>方法 {trust?.method_version || '待确认'}</span>
              {primaryTrustReason ? <span className="font-semibold">原因：{trustReasonLabel(primaryTrustReason)}</span> : null}
            </div>
          ) : null}
          {error ? <p className="mt-2 text-sm font-medium text-rose-600">{error}</p> : null}
        </section>

        <section className="grid flex-1 gap-3 overflow-visible p-3 md:p-4 xl:min-h-[48rem] xl:grid-cols-[minmax(0,1fr)_20rem]">
          <main className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_14px_32px_rgba(15,23,42,0.06)] xl:min-h-[44rem]">
            <div className="shrink-0 border-b border-slate-200 bg-gradient-to-r from-white via-sky-50/60 to-white px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusTone(selectedMetric?.status)}`} />
                    <span className="text-[10px] font-semibold uppercase text-slate-400">
                      {selectedMetric?.kind === 'index' ? '综合指数' : '基础指标'} · {selectedCategoryName}
                    </span>
                    {selectedMetric?.freshness_status ? (
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
                        {freshnessLabel(selectedMetric.freshness_status)}
                      </span>
                    ) : null}
                  </div>
                  <h2 className="mt-1 truncate text-2xl font-semibold tracking-tight text-slate-950">
                    {selectedMetric?.label || '等待数据'}
                  </h2>
                  <p className="mt-1 max-w-3xl truncate text-sm text-slate-600">
                    {selectedMetric?.description || '选择右侧指标后，在这里查看它自己的真实历史。'}
                  </p>
                  {selectedUnavailable ? (
                    <p className="mt-1 text-xs font-semibold text-rose-700">
                      当前复合指数不可计算；精确值和复合历史序列已隐藏。
                    </p>
                  ) : null}
                </div>

                <div className="flex shrink-0 items-end gap-5 border-l border-slate-200 pl-5">
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-slate-400">当前值</div>
                    <div className="mt-1 font-mono text-3xl font-semibold text-slate-950">{formatMetricValue(selectedMetric)}</div>
                  </div>
                  {!selectedUnavailable && selectedChange !== null && selectedChange !== undefined ? (
                    <div className={`pb-1 font-mono text-sm font-semibold ${selectedChange >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                      {selectedChange >= 0 ? '+' : ''}
                      {selectedChange.toFixed(2)}%
                    </div>
                  ) : (
                    <div className="pb-1 text-xs font-semibold text-slate-500">变化不可用</div>
                  )}
                  {selectedWatchRow ? (
                    <button
                      type="button"
                      onClick={() => setDetailTarget({ kind: 'indicator', data: selectedWatchRow })}
                      className="mb-0.5 rounded border border-slate-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 transition-colors hover:border-slate-400 hover:text-slate-950"
                    >
                      详情
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">
                {visibleCategoryTabs.map((tab) => {
                  const count = categoryCounts[tab.id] || 0
                  const active = tab.id === activeCategory
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setActiveCategory(tab.id)}
                      className={`shrink-0 rounded border px-2.5 py-1.5 text-left transition-all ${
                        active
                          ? 'border-slate-950 bg-slate-950 text-white'
                          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-950'
                      }`}
                    >
                      <span className="text-xs font-semibold">{tab.label}</span>
                      <span className={`ml-2 font-mono text-[11px] ${active ? 'text-slate-300' : 'text-slate-400'}`}>{count}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="grid flex-1 gap-3 overflow-visible p-3 xl:min-h-[34rem] xl:grid-rows-[minmax(30rem,1fr)_auto]">
              <SignalHistoryChart
                label={selectedMetric?.label || '世界状态'}
                points={selectedMetric?.points || []}
                unit={selectedMetric?.unit}
                thresholds={alertsEnabled && !selectedUnavailable ? currentThresholds : []}
                historical={selectedUnavailable || trust?.freshness_status === 'stale'}
                statusMessage={selectedUnavailable
                  ? '当前指数不可计算，精确值和复合历史序列已隐藏。'
                  : !selectedTrendDisclosure.preciseChangeAllowed
                    ? '历史序列仅供回溯；基期和不确定性方法未建立，不显示变化百分比。'
                    : undefined}
                onAddThreshold={alertsEnabled && !selectedUnavailable ? addThreshold : undefined}
              />

              <div className="grid gap-3 border-t border-slate-100 pt-2 lg:grid-cols-[minmax(0,1fr)_20rem]">
                <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-7">
                  <MetaCell label="来源" value={sourceLabel(selectedMetric?.source)} />
                  <MetaCell label="更新频率" value={cadenceLabel(selectedMetric?.cadence)} />
                  <MetaCell label="数据截止" value={selectedMetric?.data_as_of?.slice(0, 10) || dataAsOf?.slice(0, 10) || '未知'} />
                  <MetaCell label="有效覆盖" value={selectedMetric?.coverage_ratio !== undefined ? `${(selectedMetric.coverage_ratio * 100).toFixed(0)}%` : coveragePercent} />
                  <MetaCell label="方法版本" value={selectedMetric?.method_version || trust?.method_version || '待确认'} />
                  <MetaCell label="输入/输出单位" value={selectedMetric?.unit || '未声明'} />
                  <MetaCell label="地区" value={selectedMetric?.region || '全球'} />
                </div>
                <div className="min-w-0 lg:border-l lg:border-slate-200 lg:pl-3">
                  <div className="text-[10px] font-semibold uppercase text-slate-400">阈值线</div>
                  <div className="mt-1 flex min-w-0 gap-1.5 overflow-x-auto pb-1">
                    {!alertsEnabled || selectedUnavailable ? (
                      <span className="text-xs font-semibold text-amber-700">可信门禁已暂停阈值评估</span>
                    ) : currentThresholds.length === 0 ? (
                      <span className="text-xs text-slate-500">点击图表添加监控线</span>
                    ) : (
                      currentThresholds.map((line) => (
                        <button
                          key={line.id}
                          type="button"
                          onClick={() => removeThreshold(line.id)}
                          className="shrink-0 rounded border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-600 hover:border-slate-400"
                          title="点击移除阈值线"
                        >
                          {line.label} · {line.price.toFixed(2)}
                        </button>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          </main>

          <aside className="grid gap-3 overflow-visible xl:min-h-0 xl:grid-rows-[minmax(30rem,1fr)_auto_auto]">
            <section className="flex min-h-[30rem] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_12px_28px_rgba(15,23,42,0.05)]">
              <div className="shrink-0 border-b border-slate-200 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-[10px] font-semibold uppercase text-slate-400">信号目录</div>
                    <div className="mt-0.5 text-xs text-slate-500">{filteredMetricRows.length} / {primaryWatchlist.length} 个可用信号</div>
                  </div>
                  <span className="rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-500">
                    {categoryLabel(activeCategory)}
                  </span>
                </div>
                <label className="mt-2 block">
                  <span className="sr-only">搜索信号</span>
                  <input
                    value={signalQuery}
                    onChange={(event) => setSignalQuery(event.target.value)}
                    placeholder="搜索国家、来源或指标"
                    className="h-8 w-full rounded border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 outline-none placeholder:text-slate-400 focus:border-slate-400"
                  />
                </label>
              </div>

              <ul className="min-h-0 flex-1 overflow-auto">
                {filteredMetricRows.length === 0 ? (
                  <li className="px-3 py-4 text-sm text-slate-500">没有匹配的信号。</li>
                ) : null}
                {filteredMetricRows.map((row) => {
                  const metricId = row.metric_id || row.symbol
                  const metric = seriesMap.get(metricId)
                  const active = selectedMetricId === metricId
                  return (
                    <li key={row.symbol} className="border-b border-slate-100 last:border-b-0">
                      <button
                        type="button"
                        onClick={() => setSelectedMetricId(metricId)}
                        className={`relative flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left transition-colors ${
                          active ? 'bg-slate-950 text-white' : 'hover:bg-slate-50'
                        }`}
                      >
                        {row.category ? <span className={`absolute left-0 top-0 h-full w-1 ${CATEGORY_ACCENT[row.category]}`} /> : null}
                        <div className="min-w-0 pl-1">
                          <div className={`truncate text-sm font-semibold ${active ? 'text-white' : 'text-slate-900'}`}>{row.label}</div>
                          <div className={`mt-1 truncate text-[11px] ${active ? 'text-slate-300' : 'text-slate-500'}`}>
                            {row.source ? sourceLabel(row.source) : row.symbol} · {row.region || '全球'}
                          </div>
                          <div className={`mt-1 flex items-center gap-1.5 text-[10px] ${active ? 'text-slate-400' : 'text-slate-500'}`}>
                            <span className={`h-1.5 w-1.5 rounded-full ${statusTone(metric?.status || row.status)}`} />
                            <span>{dashboardUnavailable ? '历史快照' : dataQuality(metric)}</span>
                            <span>·</span>
                            <span>{cadenceLabel(metric?.cadence || row.cadence)}</span>
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className={`font-mono text-sm font-semibold ${active ? 'text-white' : 'text-slate-900'}`}>
                            {row.price.toLocaleString('zh-CN', { maximumFractionDigits: row.price >= 100 ? 0 : 2 })}
                          </div>
                          <div className={`mt-1 font-mono text-xs font-semibold ${row.change_pct >= 0 ? (active ? 'text-emerald-300' : 'text-emerald-600') : active ? 'text-rose-300' : 'text-rose-600'}`}>
                            {row.change_pct >= 0 ? '+' : ''}
                            {row.change_pct.toFixed(2)}%
                          </div>
                        </div>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-3 shadow-[0_12px_28px_rgba(15,23,42,0.05)]">
              <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-2">
                <div className="text-[10px] font-semibold uppercase text-slate-400">数据状态</div>
                <div className="text-[10px] text-slate-500">{loading ? '同步中' : `${liveSources} 可用 · ${degradedSources} 延迟 · ${disabledSources} 离线`}</div>
              </div>
              <div className="mt-2 divide-y divide-slate-100">
                <StatusLine
                  label="可信门禁"
                  value={trust ? `${trustLabel(trust.trust_status)} · ${freshnessLabel(trust.freshness_status)}` : '等待判定'}
                  detail={`覆盖 ${coveragePercent} · 截止 ${dataAsOf?.slice(0, 10) || '未知'}`}
                />
                <StatusLine
                  label="本地事件图谱"
                  value={(coverage?.ground_news_records ?? groundNewsSource?.records ?? 0) > 0 ? `${(coverage?.ground_news_records ?? groundNewsSource?.records ?? 0).toLocaleString('zh-CN')} 记录` : '无可用记录'}
                  detail={coverage?.ground_news_latest_story_date ? `最新 ${coverage.ground_news_latest_story_date}` : sourceDetailLabel(groundNewsSource?.detail || '')}
                />
                <StatusLine
                  label="外部新闻流"
                  value={statusLabel(gdeltSource?.status || 'degraded')}
                  detail={sourceDetailLabel(gdeltSource?.detail || 'GDELT 状态待同步')}
                />
                <StatusLine
                  label="未进入主目录"
                  value={`${inactiveWatchlist.length} 个`}
                  detail="禁用、全零或观测不足"
                />
                <StatusLine
                  label="计算版本"
                  value={trust?.method_version || '待确认'}
                  detail={trust?.model_version || '模型版本待确认'}
                />
                <StatusLine
                  label="方法治理"
                  value={`方法卡${methodApproval} · ${dimensionalStatus}`}
                  detail="公式仅部分从原型代码提取；基线未建立，阈值未批准"
                />
                <StatusLine
                  label="短样本趋势"
                  value={selectedTrendDisclosure.baseline || '基期未建立'}
                  detail={selectedTrendDisclosure.sampleSize}
                />
                <StatusLine
                  label="趋势不确定性"
                  value={selectedTrendDisclosure.uncertainty || '不确定性不可计算（方法未建立）'}
                  detail={selectedTrendDisclosure.outliers}
                />
                <StatusLine
                  label="混合频率对齐"
                  value={frequencyAlignment?.status === 'not_approved' ? '实现存在，规则未批准' : '状态待确认'}
                  detail={frequencyAlignment?.interpolation === 'linear_by_array_position_not_observation_timestamp'
                    ? '当前按数组位置线性插值，不是按观测时间戳对齐'
                    : '插值规则待确认'}
                />
                <StatusLine
                  label="缺失与修订"
                  value="治理规则未建立"
                  detail="当前空输入零填充、单值重复；修订与历史重算政策未建立"
                />
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-3 shadow-[0_12px_28px_rgba(15,23,42,0.05)]">
              <div className="grid gap-3">
                <div>
                  <div className="text-[10px] font-semibold uppercase text-slate-400">关联指标</div>
                  <div className="mt-2 space-y-1">
                    {relatedMetrics.length === 0 ? (
                      <div className="text-xs text-slate-500">当前类目没有更多关联指标。</div>
                    ) : (
                      relatedMetrics.slice(0, 3).map((metric) => (
                        <button
                          key={metric.id}
                          type="button"
                          onClick={() => setSelectedMetricId(metric.id)}
                          className="flex w-full items-center justify-between gap-3 border-b border-slate-100 py-1.5 text-left last:border-b-0 hover:text-slate-950"
                        >
                          <span className="truncate text-xs font-semibold text-slate-700">{metric.label}</span>
                          {metric.change_pct === null ? (
                            <span className="shrink-0 text-[11px] font-semibold text-slate-500">不可用</span>
                          ) : (
                            <span className={`shrink-0 font-mono text-[11px] font-semibold ${metric.change_pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                              {metric.change_pct >= 0 ? '+' : ''}
                              {metric.change_pct.toFixed(2)}%
                            </span>
                          )}
                        </button>
                      ))
                    )}
                  </div>
                </div>

                <div className="border-t border-slate-100 pt-3">
                  <div className="text-[10px] font-semibold uppercase text-slate-400">系统预警</div>
                  <div className="mt-2 space-y-1">
                    {!alertsEnabled ? (
                      <div className="text-xs font-semibold text-amber-700">数据不满足门槛，未生成阈值告警。</div>
                    ) : alertRules.length === 0 ? (
                      <div className="text-xs text-slate-500">当前没有系统告警。</div>
                    ) : alertRules.slice(0, 3).map((rule) => (
                      <div key={rule.id} className="flex items-center justify-between gap-3 border-b border-slate-100 py-1.5 last:border-b-0">
                        <span className="truncate text-xs font-semibold text-slate-700">{rule.metric}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${ruleSeverityClass(rule)}`}>
                          {severityLabel(rule)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </section>
          </aside>
        </section>

        <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-2 text-[11px] text-slate-500 md:px-5">
          <span>{cacheLabel(cacheState)}</span>
          <span>{dataAsOf ? `数据截止 ${dataAsOf.slice(0, 19).replace('T', ' ')} UTC` : lastUpdated ? `快照生成 ${lastUpdated.slice(0, 19).replace('T', ' ')} UTC` : '等待时间戳'}</span>
        </footer>
      </div>
    </div>
  )
}

function IndexTape({
  indices,
  activeMetricId,
  onSelectMetric,
}: {
  indices: IndexCard[]
  activeMetricId?: string | null
  onSelectMetric?: (metricId?: string) => void
}) {
  if (!indices.length) return null
  return (
    <div className="flex max-w-full gap-1.5 overflow-x-auto pb-0.5 xl:max-w-[46rem]">
      {indices.slice(0, 6).map((index) => {
        const active = index.metric_id === activeMetricId
        const available = index.availability !== 'not_computable' && index.value !== null && index.change_pct !== null
        const up = available && index.change_pct! >= 0
        return (
          <button
            key={index.id}
            type="button"
            onClick={() => onSelectMetric?.(index.metric_id)}
            className={`flex shrink-0 items-center gap-2 rounded border px-2.5 py-1.5 text-left transition-colors ${
              active ? 'border-slate-950 bg-slate-950 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
            }`}
          >
            <span className="max-w-[7rem] truncate text-[11px] font-semibold">{index.name}</span>
            {available ? (
              <>
                <span className="font-mono text-[11px]">{index.value!.toFixed(index.value! >= 100 ? 0 : 2)}</span>
                <span className={`font-mono text-[11px] font-semibold ${up ? (active ? 'text-emerald-300' : 'text-emerald-600') : active ? 'text-rose-300' : 'text-rose-600'}`}>
                  {up ? '+' : ''}
                  {index.change_pct!.toFixed(2)}%
                </span>
              </>
            ) : (
              <span className={`text-[11px] font-semibold ${active ? 'text-amber-200' : 'text-amber-700'}`}>不可计算</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-l border-slate-200 pl-3 first:border-l-0 first:pl-0">
      <div className="text-[10px] font-semibold uppercase text-slate-400">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-slate-800">{value}</div>
    </div>
  )
}

function StatusLine({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 py-2">
      <div className="text-[11px] font-semibold text-slate-500">{label}</div>
      <div className="min-w-0">
        <div className="truncate text-xs font-semibold text-slate-800">{value}</div>
        <div className="mt-0.5 truncate text-[11px] text-slate-500">{detail || '等待同步'}</div>
      </div>
    </div>
  )
}
