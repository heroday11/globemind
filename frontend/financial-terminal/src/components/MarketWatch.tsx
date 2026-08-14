import { memo } from 'react'
import type { WatchRow } from '../types'
import type { PriceAlertLine } from '../lib/priceAlerts'
import { IconAlertTriangle } from './Icons'
import { cadenceLabel, sourceLabel, statusLabel } from '../lib/sourceLabels'

type Props = {
  rows: WatchRow[]
  alertLines: PriceAlertLine[]
  onRemoveAlert: (id: string) => void
  lastClose: number | null
  activeLabel?: string
  onOpenRow?: (row: WatchRow) => void
}

const CATEGORY_LABELS: Record<string, string> = {
  politics: '政治',
  economy: '经济',
  science: '科技',
  energy: '能源',
  security: '安全',
  society: '社会',
  logistics: '物流',
}

const CATEGORY_STYLES: Record<string, { accent: string; badge: string; active: string }> = {
  politics: {
    accent: 'bg-sky-300',
    badge: 'bg-sky-500/10 text-sky-300',
    active: 'border-sky-400/20 bg-sky-400/[0.07]',
  },
  economy: {
    accent: 'bg-emerald-300',
    badge: 'bg-emerald-500/10 text-emerald-300',
    active: 'border-emerald-400/20 bg-emerald-400/[0.06]',
  },
  science: {
    accent: 'bg-violet-300',
    badge: 'bg-violet-500/10 text-violet-300',
    active: 'border-violet-400/20 bg-violet-400/[0.07]',
  },
  energy: {
    accent: 'bg-amber-200',
    badge: 'bg-amber-500/10 text-amber-200',
    active: 'border-amber-300/20 bg-amber-300/[0.06]',
  },
  security: {
    accent: 'bg-rose-300',
    badge: 'bg-rose-500/10 text-rose-300',
    active: 'border-rose-400/20 bg-rose-400/[0.07]',
  },
  society: {
    accent: 'bg-cyan-300',
    badge: 'bg-cyan-500/10 text-cyan-300',
    active: 'border-cyan-400/20 bg-cyan-400/[0.06]',
  },
  logistics: {
    accent: 'bg-teal-300',
    badge: 'bg-teal-500/10 text-teal-300',
    active: 'border-teal-400/20 bg-teal-400/[0.06]',
  },
}

function statusDot(status?: WatchRow['status']) {
  if (status === 'live') return 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]'
  if (status === 'degraded') return 'bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.35)]'
  if (status === 'disabled') return 'bg-slate-500'
  if (status === 'mock') return 'bg-blue-300 shadow-[0_0_8px_rgba(147,197,253,0.35)]'
  return 'bg-slate-600'
}

const MarketWatch = memo(function MarketWatch({ rows, alertLines, onRemoveAlert, lastClose, activeLabel = '全部指标', onOpenRow }: Props) {
  return (
    <aside className="flex min-h-0 w-full shrink-0 flex-col border-t border-terminal-border bg-terminal-panel lg:h-auto lg:w-64 lg:self-stretch lg:border-l lg:border-t-0 xl:w-72">
      <div className="shrink-0 border-b border-terminal-border px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <IconAlertTriangle className="h-4 w-4 text-amber-500/90" />
          阈值监控
        </div>
        <p className="mt-1 text-[11px] leading-snug text-slate-600">
          悬停主图出现虚线，点击设定世界状态风险阈值。
        </p>
      </div>
      <div className="max-h-[42%] shrink-0 overflow-auto border-b border-terminal-border p-2">
        {alertLines.length === 0 ? (
          <div className="rounded border border-dashed border-white/10 bg-black/20 px-3 py-4 text-center text-xs text-slate-500">
            暂无自定义阈值。悬停主图后点击任意价位即可建立监控线。
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {alertLines.map((al) => {
              const delta = lastClose !== null ? lastClose - al.price : null
              return (
                <li
                  key={al.id}
                  className="rounded-lg border border-rose-500/20 bg-[#151a1f] px-3 py-2.5 shadow-sm shadow-black/20"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-rose-200/95">
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" />
                        {al.label}
                      </div>
                      <div className="mt-1 font-mono text-[11px] text-slate-500">{al.instrument}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onRemoveAlert(al.id)}
                      className="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium text-slate-500 hover:bg-white/10 hover:text-slate-300"
                    >
                      移除
                    </button>
                  </div>
                  <div className="mt-2 flex items-baseline justify-between gap-2 border-t border-white/5 pt-2">
                    <span className="text-[11px] text-slate-500">风险阈值</span>
                    <span className="font-mono text-sm font-semibold text-white">{al.price.toFixed(2)}</span>
                  </div>
                  {delta !== null ? (
                    <div className="mt-1 text-right font-mono text-[11px] text-slate-500">
                      当前偏离{' '}
                      <span className={delta >= 0 ? 'text-up' : 'text-down'}>
                        {delta >= 0 ? '+' : ''}
                        {delta.toFixed(2)}
                      </span>
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="shrink-0 border-b border-terminal-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        指标列表 · {activeLabel}
      </div>
      <ul className="min-h-0 flex-1 overflow-auto overscroll-contain p-2">
        {rows.map((r) => {
          const up = r.change_pct >= 0
          const active = Math.abs(r.change_pct) >= 0.5
          const style = r.category ? CATEGORY_STYLES[r.category] : undefined
          return (
            <li
              key={r.symbol}
              className={`relative mb-1 overflow-hidden rounded border transition-all ${
                active
                  ? style?.active || 'border-white/10 bg-white/[0.08]'
                  : 'border-transparent hover:border-terminal-border hover:bg-white/[0.03]'
              }`}
            >
              <span className={`absolute left-0 top-0 h-full w-1 ${style?.accent || 'bg-slate-500'}`} />
              <button
                type="button"
                onClick={() => onOpenRow?.(r)}
                className="flex w-full items-start justify-between gap-2 px-2 py-2 pl-3 text-left transition-transform hover:translate-x-0.5 focus:outline-none focus:ring-1 focus:ring-cyan-400/30"
              >
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <div className="font-mono text-sm font-semibold text-slate-100">{r.symbol}</div>
                    {r.category ? (
                      <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${style?.badge || 'bg-slate-500/10 text-slate-300'}`}>
                        {CATEGORY_LABELS[r.category] || r.category}
                      </span>
                    ) : null}
                  </div>
                  <div className="truncate text-[11px] text-slate-500">{r.label}</div>
                  <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-slate-600">
                    <span className={`h-1.5 w-1.5 rounded-full ${statusDot(r.status)}`} />
                    {r.status ? <span>{statusLabel(r.status)}</span> : null}
                    {r.region ? <span>{r.region}</span> : null}
                    {r.source ? <span className="truncate">· {sourceLabel(r.source)}</span> : null}
                    {r.cadence ? <span>· {cadenceLabel(r.cadence)}</span> : null}
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-sm text-white">
                    {r.price < 200 ? r.price.toFixed(2) : r.price.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </div>
                  <div className={`font-mono text-xs font-medium ${up ? 'text-up' : 'text-down'}`}>
                    {up ? '+' : ''}
                    {r.change_pct.toFixed(2)}%
                  </div>
                </div>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
})

export default MarketWatch
