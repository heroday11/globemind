import type { CrosshairLegend } from './MainCandlestickChart'

export default function CrosshairTooltip({ data }: { data: CrosshairLegend }) {
  if (!data) return null
  return (
    <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-[min(420px,92vw)] rounded border border-terminal-border bg-[#161a1e]/95 px-4 py-3 font-mono text-sm leading-snug shadow-lg shadow-black/40 backdrop-blur-sm">
      <div className="mb-2 text-sm text-slate-300">{data.timeLabel}</div>
      <div className="grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 text-[15px] text-slate-200">
        <span className="text-slate-500">基线</span>
        <span>{data.open.toFixed(2)}</span>
        <span className="text-slate-500">峰值</span>
        <span className="text-up">{data.high.toFixed(2)}</span>
        <span className="text-slate-500">谷值</span>
        <span className="text-down">{data.low.toFixed(2)}</span>
        <span className="text-slate-500">指数</span>
        <span className="text-white">{data.close.toFixed(2)}</span>
        <span className="text-slate-500">信号量</span>
        <span>{data.volume >= 1e6 ? `${(data.volume / 1e6).toFixed(2)}M` : `${(data.volume / 1e3).toFixed(0)}K`}</span>
      </div>
      <div className="mt-2 border-t border-terminal-border pt-2 text-sm text-slate-500">
        世界状态指数 {data.close.toFixed(2)}
      </div>
    </div>
  )
}
