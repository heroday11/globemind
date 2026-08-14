import type { DataSourceStatus, IndexCard, WatchRow } from '../types'
import { cadenceLabel, sourceDetailLabel, sourceLabel, statusLabel } from '../lib/sourceLabels'

type DetailTarget =
  | { kind: 'indicator'; data: WatchRow }
  | { kind: 'source'; data: DataSourceStatus }
  | { kind: 'index'; data: IndexCard }

type Props = {
  target: DetailTarget | null
  onClose: () => void
}

function pct(value?: number | null) {
  if (typeof value !== 'number') return '不可用'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function tone(status?: DataSourceStatus['status']) {
  if (status === 'live') return 'text-emerald-300 border-emerald-500/25 bg-emerald-500/10'
  if (status === 'degraded') return 'text-amber-300 border-amber-500/25 bg-amber-500/10'
  if (status === 'disabled') return 'text-slate-400 border-slate-500/25 bg-slate-500/10'
  if (status === 'unavailable') return 'text-rose-300 border-rose-500/25 bg-rose-500/10'
  return 'text-blue-300 border-blue-500/25 bg-blue-500/10'
}

export default function DetailModal({ target, onClose }: Props) {
  if (!target) return null

  const title =
    target.kind === 'indicator'
      ? target.data.label
      : target.kind === 'source'
        ? sourceLabel(target.data.name)
        : target.data.name
  const subtitle =
    target.kind === 'indicator'
      ? `${target.data.symbol} · ${target.data.region || '全球'}`
      : target.kind === 'source'
        ? `${statusLabel(target.data.status)} · ${cadenceLabel(target.data.cadence)}`
        : sourceLabel(target.data.source)

  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm animate-slide-in" onClick={onClose}>
      <section
        className="max-h-[88vh] w-full max-w-2xl overflow-hidden rounded-lg border border-terminal-border bg-[#101418] shadow-2xl shadow-black/50"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-terminal-border px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-semibold text-cyan-300">
              {target.kind === 'indicator' ? '指标详情' : target.kind === 'source' ? '数据源详情' : '综合指数详情'}
            </div>
            <h2 className="mt-1 truncate text-lg font-semibold text-slate-100">{title}</h2>
            <p className="mt-0.5 truncate text-xs text-slate-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-white/10 px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
          >
            关闭
          </button>
        </header>

        <div className="grid gap-3 overflow-auto p-4 md:grid-cols-3">
          {target.kind === 'indicator' ? (
            <>
              <Metric label="当前数值" value={target.data.price < 200 ? target.data.price.toFixed(2) : target.data.price.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} />
              <Metric label="当前变化" value={pct(target.data.change_pct)} valueClass={target.data.change_pct >= 0 ? 'text-up' : 'text-down'} />
              <Metric label="刷新频率" value={cadenceLabel(target.data.cadence)} />
              <Info label="数据状态" value={statusLabel(target.data.status)} className={tone(target.data.status)} />
              <Info label="来源" value={sourceLabel(target.data.source)} />
              <Info label="地区" value={target.data.region || '全球'} />
              <div className="md:col-span-3 rounded border border-white/[0.06] bg-white/[0.03] p-3">
                <div className="text-xs font-semibold text-slate-300">使用说明</div>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">
                  该指标会参与当前类目的信号判断。切换目录时，中间主图会改为所选指标自己的真实历史；点击主图可以为当前指标添加阈值线。
                </p>
              </div>
            </>
          ) : target.kind === 'source' ? (
            <>
              <Metric label="连接状态" value={statusLabel(target.data.status)} />
              <Metric label="记录数量" value={typeof target.data.records === 'number' ? target.data.records.toLocaleString('zh-CN') : '无'} />
              <Metric label="响应耗时" value={typeof target.data.latency_ms === 'number' ? `${target.data.latency_ms} ms` : '未请求'} />
              <Info label="刷新频率" value={cadenceLabel(target.data.cadence)} />
              <Info label="最近更新" value={target.data.last_updated ? target.data.last_updated.slice(0, 19).replace('T', ' ') : '未知'} />
              <Info label="状态说明" value={sourceDetailLabel(target.data.detail)} className={tone(target.data.status)} />
              <div className="md:col-span-3 rounded border border-white/[0.06] bg-white/[0.03] p-3">
                <div className="text-xs font-semibold text-slate-300">接口地址</div>
                <p className="mt-1 break-all font-mono text-[11px] leading-relaxed text-slate-500">{target.data.url || '该数据源需要先配置 key 或当前未启用。'}</p>
              </div>
            </>
          ) : (
            <>
              <Metric label="指数值" value={target.data.value === null ? '不可计算' : target.data.value.toFixed(2)} />
              <Metric label="变化" value={pct(target.data.change_pct)} valueClass={target.data.change_pct === null ? 'text-amber-300' : target.data.change_pct >= 0 ? 'text-up' : 'text-down'} />
              <Metric label="来源" value={sourceLabel(target.data.source)} />
              <div className="md:col-span-3 rounded border border-white/[0.06] bg-white/[0.03] p-3">
                <div className="text-xs font-semibold text-slate-300">近端走势</div>
                {target.data.value === null ? (
                  <p className="mt-2 text-xs font-semibold text-amber-300">可信门禁未通过，当前精确值与走势已隐藏。</p>
                ) : (
                  <div className="mt-2 grid grid-cols-12 items-end gap-1">
                    {target.data.spark.slice(-12).map((value, index) => (
                      <span
                        key={`${value}-${index}`}
                        className="rounded-sm bg-cyan-300/60"
                        style={{ height: `${Math.max(8, Math.min(42, value / Math.max(target.data.value!, 1) * 28))}px` }}
                      />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

function Metric({ label, value, valueClass = 'text-white' }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded border border-white/[0.06] bg-white/[0.03] p-3">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`mt-1 truncate font-mono text-lg font-semibold ${valueClass}`}>{value}</div>
    </div>
  )
}

function Info({ label, value, className = 'border-white/[0.06] bg-white/[0.03] text-slate-300' }: { label: string; value?: string; className?: string }) {
  return (
    <div className={`rounded border p-3 ${className}`}>
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value || '无'}</div>
    </div>
  )
}

export type { DetailTarget }
