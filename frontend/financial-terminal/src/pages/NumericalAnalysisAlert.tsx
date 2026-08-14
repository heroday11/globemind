import { useEffect, useState, useCallback } from 'react'
import { NavLink } from 'react-router-dom'
import { IconAlertTriangle, IconArrowLeft, IconRadio } from '../components/Icons'
import type { Severity, AlertRule, AlertReport, AlertTriageStatus } from '../types'
import { useNumericalAlerts } from '../hooks/useNumericalAlerts'
import { freshnessLabel, trustLabel, trustReasonLabel } from '../lib/sourceLabels'

const SEVERITY_CLASSES: Record<Severity, { badge: string; cardBorder: string; cardBg: string }> = {
  high: { badge: 'bg-rose-500/15 text-rose-400 border border-rose-500/20', cardBorder: 'border-rose-500/30 bg-rose-500/5', cardBg: 'bg-rose-500/10 text-rose-400 border-rose-500/15' },
  medium: { badge: 'bg-amber-500/15 text-amber-400 border border-amber-500/20', cardBorder: 'border-amber-500/25 bg-amber-500/5', cardBg: 'bg-amber-500/10 text-amber-400 border-amber-500/15' },
  low: { badge: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/20', cardBorder: 'border-emerald-500/20 bg-emerald-500/5', cardBg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/15' },
}

function severityLabel(s: Severity) {
  return { high: '高危', medium: '注意', low: '正常' }[s]
}

function trendArrow(t: string) {
  if (t === 'up') return '↑'
  if (t === 'down') return '↓'
  return '→'
}

function triageStatusLabel(status: AlertTriageStatus): string {
  return {
    open: '待确认',
    acknowledged: '已确认',
    escalated: '已升级',
    false_positive: '误报',
    resolved: '已解决',
  }[status]
}

function buildAlertAssistantContext(rep: AlertReport, rules: AlertRule[]): { visibleMessage: string; prompt: string; mode: 'expert' } {
  const matchedRule = rules.find((rule) => rule.metric === rep.metric)
  const relatedRules = rules
    .slice(0, 12)
    .map((rule, index) => {
      const status = rule.breached ? '已突破' : rule.current >= rule.threshold * 0.82 ? '接近阈值' : '正常'
      return `${index + 1}. ${rule.metric}: 当前 ${rule.current.toFixed(2)}${rule.unit} / 阈值 ${rule.threshold}${rule.unit} / 基线 ${rule.baseline}${rule.unit} / ${severityLabel(rule.severity)} / ${status} / 趋势 ${trendArrow(rule.trend)}`
    })
    .join('\n')
  const eventTags = rep.eventTags?.length ? rep.eventTags.join('、') : '暂无'
  const visibleMessage = `分析金融预警：${rep.metric} 当前 ${rep.current.toFixed(2)}，阈值 ${rep.threshold}`
  const prompt = [
    '【金融终端阈值预警上下文】',
    `触发指标：${rep.metric}`,
    `触发时间：${rep.triggered_at}`,
    `当前值：${rep.current.toFixed(2)}`,
    `阈值：${rep.threshold}`,
    `严重级别：${severityLabel(rep.severity)}`,
    `系统消息：${rep.message}`,
    `关联事件标签：${eventTags}`,
    matchedRule
      ? `匹配规则：基线 ${matchedRule.baseline}${matchedRule.unit}，趋势 ${trendArrow(matchedRule.trend)}，状态 ${matchedRule.breached ? '已突破' : '未突破'}`
      : '匹配规则：未找到同名规则',
    '',
    '【同页监控规则快照】',
    relatedRules || '无',
    '',
    '【用户问题】',
    '请基于上述金融/世界状态指标异常生成结构化研判：先给结论，再解释可能原因、需要核查的新闻/事件线索、风险等级、未来 24-72 小时观察指标，并给出可写入报告的简报段落。',
  ].join('\n')
  return { visibleMessage, prompt, mode: 'expert' }
}

export default function NumericalAnalysisAlert() {
  const { rules, reports, trust, loading, error } = useNumericalAlerts()
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [now, setNow] = useState(new Date())
  const [navigatingId, setNavigatingId] = useState<string | null>(null)
  const [hoveredMetric, setHoveredMetric] = useState<string | null>(null)
  const [lightMode, setLightMode] = useState(() => localStorage.getItem('fin_terminal_light') === '1')

  // 定时更新当前时间显示
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const dismissReport = useCallback((reportId: string) => {
    setDismissed((prev) => new Set([...prev, reportId]))
  }, [])

  const activeReports = reports.filter((r) => !dismissed.has(r.id))
  const breachedCount = rules.filter((r) => r.breached).length
  const highCount = rules.filter((r) => r.breached && r.severity === 'high').length
  const alertsEnabled = Boolean(trust?.alerts_enabled || trust?.trust_status === 'mock')
  const dataAsOf = trust?.data_as_of

  const handleNavigate = (rep: AlertReport) => {
    if (navigatingId || !alertsEnabled) return
    setNavigatingId(rep.id)
    window.parent.postMessage({
      type: 'storeAndNavigate',
      path: '/data-assistant',
      assistantContext: buildAlertAssistantContext(rep, rules),
    }, '*')
    setTimeout(() => setNavigatingId(null), 4000)
  }

  return (
    <div className={`flex h-full min-h-0 flex-col ${lightMode ? 'bg-[#f6f8ff] text-slate-800 light-mode' : 'bg-[#0B0E11] text-slate-200'}`}>
      {/* --- Header Bar --- */}
      <header className={`flex shrink-0 items-center justify-between border-b px-4 py-3 ${lightMode ? 'border-slate-200 bg-white/80 backdrop-blur' : 'border-terminal-border bg-terminal-panel'}`}>
        <div className="flex items-center gap-4">
          <NavLink
            to="/"
            aria-label="返回世界状态终端"
            className={`flex min-h-11 items-center gap-1.5 rounded px-2 text-xs font-medium transition-colors ${lightMode ? 'text-slate-500 hover:text-slate-800' : 'text-slate-400 hover:text-slate-200'}`}
          >
            <IconArrowLeft />
            <span className="hidden sm:inline">世界状态</span>
          </NavLink>
          <div className={`h-5 w-px ${lightMode ? 'bg-slate-200' : 'bg-terminal-border'}`} />
          <div className="flex items-center gap-2.5">
            <IconRadio className={`h-5 w-5 ${alertsEnabled ? (lightMode ? 'text-emerald-600' : 'text-emerald-400') : 'text-amber-400'}`} />
            <h1 className={`text-lg font-semibold ${lightMode ? 'text-slate-900' : 'text-slate-100'}`}>阈值预警</h1>
          </div>
        </div>
        <div className="flex items-center gap-4 font-mono text-xs">
          <button
            type="button"
            aria-label={lightMode ? '切换为深色模式' : '切换为浅色模式'}
            onClick={() => { const v = !lightMode; setLightMode(v); localStorage.setItem('fin_terminal_light', v ? '1' : '0') }}
            className={`min-h-11 rounded-full px-3 text-[11px] font-semibold transition-colors ${lightMode ? 'bg-slate-200 text-slate-700 hover:bg-slate-300' : 'bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25'}`}
          >
            {lightMode ? '深色模式' : '浅色模式'}
          </button>
          <span className={`flex items-center gap-1.5 ${lightMode ? 'text-slate-500' : 'text-slate-500'}`}>
            <span className={`h-2 w-2 rounded-full ${!alertsEnabled ? 'bg-amber-400' : breachedCount > 0 ? 'bg-rose-500 animate-pulse' : 'bg-emerald-500'}`} />
            {!alertsEnabled ? '评估暂停' : breachedCount > 0 ? `${breachedCount} 项告警` : '当前无告警'}
          </span>
          <span className={`hidden sm:inline ${lightMode ? 'text-slate-400' : 'text-slate-500'}`}>{now.toLocaleTimeString('zh-CN', { hour12: false })}</span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl p-4 md:p-6 space-y-5">

          <div role="status" className={`rounded-lg border px-4 py-3 text-sm ${alertsEnabled ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-200' : 'border-amber-500/25 bg-amber-500/10 text-amber-200'}`}>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="font-semibold">{loading ? '正在读取可信状态' : trustLabel(trust?.trust_status)}</span>
              <span>{freshnessLabel(trust?.freshness_status)}</span>
              <span>数据截止 {dataAsOf ? dataAsOf.slice(0, 19).replace('T', ' ') + ' UTC' : '未知'}</span>
              <span>覆盖 {trust ? `${(trust.coverage_ratio * 100).toFixed(0)}%` : '待判定'}</span>
              <span>方法 {trust?.method_version || '待确认'}</span>
            </div>
            {!alertsEnabled ? (
              <p className="mt-1 text-xs font-semibold">阈值计算和新告警已停止。{trustReasonLabel(trust?.unavailable_reasons?.[0])}</p>
            ) : null}
            {error ? <p className="mt-1 text-xs text-rose-300">{error}</p> : null}
            <p className="mt-1 text-xs text-slate-400">
              页面仅展示隐私最小化处置聚合；管理员写操作必须经过服务端鉴权。自动 SLA、外部通知与机构事件系统尚未接入。
            </p>
          </div>

          {/* --- Stats Overview --- */}
          <div className="grid gap-3 sm:grid-cols-4">
            <StatCard
              label="监控指标"
              value={alertsEnabled ? String(rules.length) : '—'}
              sub={alertsEnabled ? '项可评估规则' : '可信门禁暂停'}
              color="slate"
            />
            <StatCard
              label="告警中"
              value={alertsEnabled ? String(breachedCount) : '—'}
              sub={!alertsEnabled ? '未生成新告警' : highCount > 0 ? `${highCount} 项高危` : '当前无触发'}
              color={!alertsEnabled ? 'amber' : breachedCount > 0 ? 'rose' : 'emerald'}
              pulse={alertsEnabled && breachedCount > 0}
            />
            <StatCard
              label="历史记录"
              value={String(reports.length)}
              sub={`最近: ${reports[0]?.triggered_at ? new Date(reports[0].triggered_at).toLocaleString('zh-CN', { hour12: false }) : '—'}`}
              color={reports.some(r => r.severity === 'high') ? 'rose' : 'amber'}
              pulse={reports.some(r => r.severity === 'high')}
            />
            <StatCard
              label="数据模式"
              value={trust?.trust_status === 'mock' ? '1.8s' : 'API'}
              sub={trust?.trust_status === 'mock' ? '显式模拟波动' : freshnessLabel(trust?.freshness_status)}
              color="slate"
            />
          </div>

          {/* --- Alert Report Cards (new breaches) --- */}
          {activeReports.length > 0 && (
            <section>
              <div className="mb-3 flex items-center gap-2">
                <span className={`flex h-2 w-2 rounded-full ${alertsEnabled ? 'bg-rose-500 animate-pulse' : 'bg-slate-500'}`} />
                <h2 className={`text-sm font-semibold ${lightMode ? 'text-slate-700' : 'text-slate-300'}`}>{alertsEnabled ? '告警报告' : '历史告警记录'}</h2>
                <span className="rounded-full bg-rose-500/15 text-rose-400 border border-rose-500/20 px-2 py-0.5 text-[10px] font-semibold">
                  {activeReports.length} 条记录
                </span>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {activeReports.slice(0, 6).map((rep) => {
                  return (
                    <div
                      key={rep.id}
                      onMouseEnter={() => setHoveredMetric(rep.metric)}
                      onMouseLeave={() => setHoveredMetric(null)}
                      className={`relative flex flex-col rounded-lg border bg-terminal-panel p-4 animate-slide-in transition-all duration-200
                        ${rep.severity === 'high'
                          ? 'border-rose-500/30 bg-rose-500/5'
                          : rep.severity === 'medium'
                            ? 'border-amber-500/25 bg-amber-500/5'
                            : 'border-emerald-500/20 bg-emerald-500/5'
                        } ${hoveredMetric === rep.metric ? 'ring-1 ring-blue-500/30 shadow-[0_0_20px_rgba(59,130,246,0.1)]' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className={`rounded-full ${SEVERITY_CLASSES[rep.severity].badge} px-2 py-0.5 text-[10px] font-bold uppercase`}>
                            {severityLabel(rep.severity)}
                          </span>
                          <h3 className="text-sm font-semibold text-slate-100">{rep.metric}</h3>
                        </div>
                        <button
                          type="button"
                          onClick={() => dismissReport(rep.id)}
                          aria-label={`从当前视图隐藏 ${rep.metric} 告警；不会改变处置状态`}
                          className="min-h-11 shrink-0 rounded px-3 text-[11px] font-medium text-slate-500 hover:bg-slate-500/10 hover:text-slate-200 transition-colors"
                          title="仅从当前视图隐藏，不会确认或解决告警"
                        >
                          从视图隐藏
                        </button>
                      </div>
                      <p className="mt-2 flex-1 text-xs text-slate-400 leading-relaxed">{rep.message}</p>
                      {rep.eventTags && rep.eventTags.length > 0 && (
                        <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                          <span className="text-[10px] text-slate-600">关联事件:</span>
                          {rep.eventTags.map((tag) => (
                            <span key={tag} className="rounded-full bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] text-slate-400">{tag}</span>
                          ))}
                        </div>
                      )}
                      <div
                        role="status"
                        aria-label={`${rep.metric} 处置状态`}
                        className="mt-3 rounded-md border border-white/10 bg-black/10 px-3 py-2"
                      >
                        {rep.triage ? (
                          <>
                            <div className="flex flex-wrap items-center gap-2 text-[11px]">
                              <span className="font-semibold text-cyan-300">
                                处置：{triageStatusLabel(rep.triage.status)}
                              </span>
                              {rep.triage.reviewed ? (
                                <span className="rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">已复盘</span>
                              ) : null}
                              {rep.triage.historical ? (
                                <span className="rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-amber-200">历史只读</span>
                              ) : null}
                            </div>
                            <p className="mt-1 text-[10px] text-slate-500">
                              {rep.triage.transition_count} 次状态流转；处置理由和操作者不会在普通页面公开。
                            </p>
                          </>
                        ) : (
                          <p className="text-[11px] font-medium text-slate-500">处置聚合状态暂不可用。</p>
                        )}
                      </div>
                      <div className="mt-3 flex items-center justify-between border-t border-white/5 pt-2">
                        <span className="font-mono text-[11px] text-slate-500">
                          当前 {rep.current.toFixed(2)} · 阈值 {rep.threshold}
                        </span>
                        <div className="flex items-center gap-2">
                          {alertsEnabled && rep.severity === 'high' && (
                            <button
                              type="button"
                              aria-label={`在数据助手中研判 ${rep.metric} 告警`}
                              disabled={navigatingId === rep.id}
                              onClick={() => handleNavigate(rep)}
                              className={`min-h-11 rounded-full px-3 text-[10px] font-semibold transition-all
                                ${navigatingId === rep.id
                                  ? 'bg-blue-500/20 text-blue-300 border border-blue-400/30'
                                  : 'bg-blue-500/10 border border-blue-500/20 text-blue-400 hover:bg-blue-500/20 hover:text-blue-300'}`}
                            >
                              {navigatingId === rep.id ? (
                                <span className="flex items-center gap-1.5">
                                  <span className="h-2 w-2 rounded-full bg-blue-400 animate-ping" />
                                  正在接入数据助手...
                                </span>
                              ) : '一键研判'}
                            </button>
                          )}
                          <span className="font-mono text-[11px] text-slate-600">{new Date(rep.triggered_at).toLocaleTimeString('zh-CN', { hour12: false })}</span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          {/* --- Live Monitoring Grid --- */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-300">阈值评估快照</h2>
            {!alertsEnabled ? (
              <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-4 py-6 text-center text-sm font-semibold text-amber-200">
                关键输入或覆盖率未达标，当前不计算阈值状态，也不生成告警。
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {rules.map((r) => {
                const pct = Math.min(100, (r.current / r.threshold) * 100)
                const overThreshold = pct >= 100
                const nearThreshold = pct >= 80 && pct < 100

                return (
                  <div
                    key={r.id}
                    onMouseEnter={() => setHoveredMetric(r.metric)}
                    onMouseLeave={() => setHoveredMetric(null)}
                    className={`relative overflow-hidden rounded-lg border bg-terminal-panel p-4 transition-all duration-300
                      ${overThreshold
                        ? 'border-rose-500/40 shadow-[0_0_20px_rgba(244,63,94,0.08)]'
                        : nearThreshold
                          ? 'border-amber-500/25'
                          : 'border-terminal-border'
                      } ${hoveredMetric === r.metric ? 'ring-1 ring-blue-500/30 shadow-[0_0_16px_rgba(59,130,246,0.12)]' : ''}`}
                  >
                    {/* Pulse ring for breached */}
                    {overThreshold && (
                      <div className="absolute inset-0 rounded-lg border-2 border-rose-500/20 animate-ping-once pointer-events-none" />
                    )}

                    <div className="flex items-start justify-between mb-2">
                      <div>
                        <h3 className="text-sm font-semibold text-slate-100">{r.metric}</h3>
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase mt-1 ${SEVERITY_CLASSES[r.severity].cardBg}`}>
                          {severityLabel(r.severity)}
                        </span>
                      </div>
                      <span className={`font-mono text-xs ${r.trend === 'up' ? 'text-rose-400' : r.trend === 'down' ? 'text-emerald-400' : 'text-slate-500'}`}>
                        {trendArrow(r.trend)} {Math.abs(r.current - r.baseline).toFixed(2)}
                      </span>
                    </div>

                    {/* Current value */}
                    <div className="flex items-baseline gap-1.5 mb-3">
                      <span className={`font-mono text-2xl font-bold tracking-tight ${overThreshold ? 'text-rose-400' : 'text-white'}`}>
                        {r.current.toFixed(2)}
                      </span>
                      <span className="text-xs text-slate-500">{r.unit}</span>
                    </div>

                    {/* Gauge bar */}
                    <div className="mb-1.5">
                      <div className="flex justify-between text-[10px] text-slate-500 mb-1">
                        <span>0</span>
                        <span className={overThreshold ? 'text-rose-400 font-semibold' : ''}>
                          阈值 {r.threshold}{r.unit}
                        </span>
                      </div>
                      <div className="h-2 w-full rounded-full bg-white/5 overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-700 ease-out ${
                            overThreshold
                              ? 'bg-gradient-to-r from-rose-500 to-rose-400 shadow-[0_0_8px_rgba(244,63,94,0.4)]'
                              : nearThreshold
                                ? 'bg-gradient-to-r from-amber-500 to-amber-400'
                                : 'bg-gradient-to-r from-emerald-500 to-emerald-400'
                          }`}
                          style={{ width: `${Math.min(pct, 110)}%` }}
                        />
                      </div>
                      {/* Threshold marker line */}
                      <div
                        className="relative h-0"
                        style={{ left: `${Math.min(pct, 100)}%` }}
                      >
                        {overThreshold && (
                          <div className="absolute -top-[14px] -translate-x-1/2">
                            <IconAlertTriangle className="h-3 w-3 text-rose-400" />
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Status text */}
                    <p className={`text-[11px] font-medium ${overThreshold ? 'text-rose-400' : nearThreshold ? 'text-amber-400' : 'text-slate-500'}`}>
                      {overThreshold ? '⚠ 突破阈值' : nearThreshold ? '● 接近警戒线' : '● 正常范围'}
                      <span className="ml-1 text-slate-600">{(pct).toFixed(0)}%</span>
                    </p>
                  </div>
                )
              })}
              </div>
            )}
          </section>

          {/* --- Alert History Log --- */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-300">规则评估状态</h2>
            <div className="overflow-hidden rounded-lg border border-terminal-border bg-terminal-panel">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-terminal-border text-[11px] uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-2.5 font-medium">指标</th>
                    <th className="px-4 py-2.5 font-medium hidden sm:table-cell">当前值</th>
                    <th className="px-4 py-2.5 font-medium hidden sm:table-cell">阈值</th>
                    <th className="px-4 py-2.5 font-medium">等级</th>
                    <th className="px-4 py-2.5 font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-sm text-slate-500">
                        {alertsEnabled ? '没有可评估规则。' : '可信门禁暂停，未把缺失值显示为 0 或正常。'}
                      </td>
                    </tr>
                  ) : null}
                  {rules.map((r) => {
                    return (
                      <tr key={r.id} onMouseEnter={() => setHoveredMetric(r.metric)} onMouseLeave={() => setHoveredMetric(null)} className={`border-b border-white/[0.04] transition-all duration-200 ${r.breached ? 'bg-rose-500/5 hover:bg-rose-500/8' : 'hover:bg-white/[0.03]'} ${hoveredMetric === r.metric ? '!bg-blue-500/10 ring-1 ring-blue-500/20' : ''}`}>
                        <td className="px-4 py-3">
                          <span className="font-medium text-slate-100">{r.metric}</span>
                        </td>
                        <td className="px-4 py-3 font-mono hidden sm:table-cell">
                          <span className={r.breached ? 'text-rose-400' : 'text-slate-100'}>
                            {r.current.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-500 hidden sm:table-cell">{r.threshold.toFixed(2)}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${SEVERITY_CLASSES[r.severity].cardBg}`}>
                            {r.severity === 'high' && <IconAlertTriangle />}
                            {severityLabel(r.severity)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${r.breached ? 'text-rose-400' : 'text-emerald-400'}`}>
                            <span className={`h-1.5 w-1.5 rounded-full ${r.breached ? 'bg-rose-500 animate-pulse' : 'bg-emerald-500'}`} />
                            {r.breached ? '告警中' : '正常'}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

        </div>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  sub,
  color,
  pulse,
}: {
  label: string
  value: string
  sub: string
  color: 'slate' | 'rose' | 'emerald' | 'amber'
  pulse?: boolean
}) {
  const colorMap = {
    slate: 'border-slate-700/70 bg-slate-800/40',
    rose: 'border-rose-500/20 bg-rose-500/8',
    emerald: 'border-emerald-500/20 bg-emerald-500/8',
    amber: 'border-amber-500/20 bg-amber-500/8',
  }
  const valueMap = {
    slate: 'text-white',
    rose: 'text-rose-300',
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
  }
  return (
    <div className={`rounded-lg border px-4 py-3 ${colorMap[color]}`}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        {pulse && <span className="h-2 w-2 rounded-full bg-rose-500 animate-pulse" />}
        <span className={`font-mono text-2xl font-semibold ${valueMap[color]}`}>{value}</span>
      </div>
      <div className="mt-0.5 text-xs text-slate-500">{sub}</div>
    </div>
  )
}
