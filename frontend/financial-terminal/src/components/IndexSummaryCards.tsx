import type { IndexCard } from '../types'
import Sparkline from './Sparkline'
import { sourceLabel } from '../lib/sourceLabels'

type Props = {
  indices: IndexCard[]
  activeMetricId?: string | null
  onSelectMetric?: (metricId?: string) => void
}

const INDEX_FAMILY_LABELS: Record<string, string> = {
  wsi: '综合态势',
  macro: '宏观经济',
  diplomacy: '外交事件',
  energy: '能源资源',
  supply: '物流链路',
  tech: '科技安全',
  society: '社会环境',
}

export default function IndexSummaryCards({ indices, activeMetricId, onSelectMetric }: Props) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
      {indices.map((index) => {
        const available = index.availability !== 'not_computable' && index.value !== null && index.change_pct !== null
        const up = available && index.change_pct! >= 0
        const active = activeMetricId && index.metric_id === activeMetricId
        return (
          <button
            key={index.id}
            type="button"
            onClick={() => onSelectMetric?.(index.metric_id)}
            className={`group flex min-h-[5.6rem] min-w-0 flex-col justify-between rounded-xl border px-3 py-2.5 text-left transition-all ${
              active
                ? 'border-blue-300 bg-blue-50 shadow-[0_12px_24px_rgba(37,99,235,0.10)]'
                : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_10px_20px_rgba(15,23,42,0.06)]'
            }`}
          >
            <div className="flex min-w-0 items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[10px] font-semibold text-slate-400">{INDEX_FAMILY_LABELS[index.id] || '综合指数'}</div>
                <div className="mt-0.5 truncate text-sm font-semibold text-slate-800">{index.name}</div>
              </div>
              {index.source ? (
                <span className="hidden max-w-[6.5rem] shrink-0 truncate rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 sm:inline">
                  {sourceLabel(index.source)}
                </span>
              ) : null}
            </div>

            <div className="mt-2 flex items-end justify-between gap-3">
              <div className="min-w-0">
                <div className="font-mono text-xl font-semibold text-slate-900">
                  {available ? index.value!.toFixed(index.value! >= 100 ? 0 : 2) : '不可计算'}
                </div>
                {available ? (
                  <div className={`mt-1 font-mono text-xs font-semibold ${up ? 'text-emerald-600' : 'text-rose-600'}`}>
                    {up ? '+' : ''}
                    {index.change_pct!.toFixed(2)}%
                  </div>
                ) : (
                  <div className="mt-1 text-xs font-semibold text-amber-700">当前值已隐藏</div>
                )}
              </div>
              {available ? <Sparkline values={index.spark} positive={up} className={`shrink-0 opacity-90 transition-transform ${active ? 'scale-105' : 'group-hover:scale-105'}`} /> : null}
            </div>
          </button>
        )
      })}
    </div>
  )
}
