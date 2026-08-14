import { useEffect, useMemo, useRef, useState } from 'react'
import type { SeriesPoint } from '../types'
import type { PriceAlertLine } from '../lib/priceAlerts'

type Props = {
  points: SeriesPoint[]
  unit?: string
  label: string
  thresholds: PriceAlertLine[]
  historical?: boolean
  statusMessage?: string
  onAddThreshold?: (value: number) => void
}

function fmtAxisTime(time: number) {
  const dt = new Date(time * 1000)
  const month = `${dt.getUTCMonth() + 1}`.padStart(2, '0')
  const day = `${dt.getUTCDate()}`.padStart(2, '0')
  const hour = `${dt.getUTCHours()}`.padStart(2, '0')
  return `${month}/${day} ${hour}:00`
}

function niceStep(range: number, targetSteps: number) {
  if (!(range > 0)) return 1
  const rough = range / Math.max(2, targetSteps - 1)
  const exp = Math.floor(Math.log10(rough))
  const base = rough / 10 ** exp
  const nice = base <= 1.5 ? 1 : base <= 3 ? 2 : base <= 7 ? 5 : 10
  return nice * 10 ** exp
}

export default function SignalHistoryChart({ points, unit, label, thresholds, historical = false, statusMessage, onAddThreshold }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 900, height: 420 })
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)

  useEffect(() => {
    const element = wrapRef.current
    if (!element) return
    const observer = new ResizeObserver(() => {
      const rect = element.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0) {
        setSize({ width: Math.floor(rect.width), height: Math.floor(rect.height) })
      }
    })
    observer.observe(element)
    const rect = element.getBoundingClientRect()
    if (rect.width > 0 && rect.height > 0) {
      setSize({ width: Math.floor(rect.width), height: Math.floor(rect.height) })
    }
    return () => observer.disconnect()
  }, [])

  const visiblePoints = useMemo(
    () => historical ? [] : points.slice(-48),
    [historical, points],
  )
  const layout = useMemo(() => {
    const left = 18
    const right = 54
    const top = 18
    const bottom = 42
    const plotWidth = Math.max(120, size.width - left - right)
    const plotHeight = Math.max(160, size.height - top - bottom)
    const values = visiblePoints.map((point) => point.value)
    const minValue = Math.min(...values, ...thresholds.map((line) => line.price))
    const maxValue = Math.max(...values, ...thresholds.map((line) => line.price))
    const safeMin = Number.isFinite(minValue) ? minValue : 0
    const safeMax = Number.isFinite(maxValue) ? maxValue : 1
    const pad = (safeMax - safeMin || 1) * 0.16
    return {
      left,
      right,
      top,
      bottom,
      plotWidth,
      plotHeight,
      minValue: safeMin - pad,
      maxValue: safeMax + pad,
    }
  }, [size, visiblePoints, thresholds])

  const pointMeta = useMemo(() => {
    const { left, plotWidth, top, plotHeight, minValue, maxValue } = layout
    const slot = visiblePoints.length > 1 ? plotWidth / (visiblePoints.length - 1) : plotWidth
    return visiblePoints.map((point, index) => {
      const x = left + slot * index
      const y = top + ((maxValue - point.value) / Math.max(maxValue - minValue, 1)) * plotHeight
      return { ...point, x, y }
    })
  }, [layout, visiblePoints])

  const linePath = useMemo(() => {
    if (!pointMeta.length) return ''
    return pointMeta.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
  }, [pointMeta])

  const areaPath = useMemo(() => {
    if (!pointMeta.length) return ''
    const first = pointMeta[0]
    const last = pointMeta[pointMeta.length - 1]
    const bottomY = layout.top + layout.plotHeight
    return `${linePath} L ${last.x} ${bottomY} L ${first.x} ${bottomY} Z`
  }, [layout.plotHeight, layout.top, linePath, pointMeta])

  const ticks = useMemo(() => {
    const step = niceStep(layout.maxValue - layout.minValue, 5)
    const start = Math.floor(layout.minValue / step) * step
    const out: { value: number; y: number }[] = []
    for (let value = start; value <= layout.maxValue + step; value += step) {
      if (value < layout.minValue - step * 0.1) continue
      const y = layout.top + ((layout.maxValue - value) / Math.max(layout.maxValue - layout.minValue, 1)) * layout.plotHeight
      out.push({ value, y })
    }
    return out
  }, [layout])

  const timeTicks = useMemo(() => {
    const maxLabels = Math.max(3, Math.min(6, Math.floor(layout.plotWidth / 140)))
    const step = Math.max(1, Math.ceil(pointMeta.length / maxLabels))
    return pointMeta.filter((_, index) => index % step === 0 || index === pointMeta.length - 1)
  }, [layout.plotWidth, pointMeta])

  const hovered = hoverIndex === null ? null : pointMeta[hoverIndex] || null

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!pointMeta.length) return
    const rect = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - rect.left
    let nearest = 0
    let distance = Infinity
    for (let index = 0; index < pointMeta.length; index += 1) {
      const delta = Math.abs(pointMeta[index].x - x)
      if (delta < distance) {
        distance = delta
        nearest = index
      }
    }
    setHoverIndex(nearest)
  }

  const handleClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!onAddThreshold || historical) return
    const rect = event.currentTarget.getBoundingClientRect()
    const y = event.clientY - rect.top
    if (y < layout.top || y > layout.top + layout.plotHeight) return
    const value = layout.maxValue - ((y - layout.top) / Math.max(layout.plotHeight, 1)) * (layout.maxValue - layout.minValue)
    onAddThreshold(Number(value.toFixed(2)))
  }

  if (pointMeta.length < 2) {
    return (
      <div ref={wrapRef} className="relative h-full min-h-[24rem] rounded-xl border border-slate-200 bg-white">
        <div className="flex h-full flex-col items-center justify-center px-6 text-center">
          <div className="text-sm font-semibold text-slate-700">{label}</div>
          <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
            {historical
              ? statusMessage || '当前数据不满足可信计算门槛，复合指数当前值与历史精确序列均已隐藏。'
              : '当前指标只有一个可用快照或历史样本不足。系统会继续累积真实观测，不用伪造波形填充主图。'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div ref={wrapRef} className="relative h-full min-h-[30rem] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_24px_60px_rgba(15,23,42,0.06)]">
      {historical ? (
        <div className="pointer-events-none absolute left-4 top-4 z-10 max-w-md rounded-lg border border-amber-200 bg-amber-50/95 px-3 py-2 text-xs font-semibold text-amber-800 shadow-sm">
          {statusMessage || '历史样本，仅供回溯；当前值和阈值评估不可用。'}
        </div>
      ) : null}
      <svg
        className="h-full w-full"
        viewBox={`0 0 ${size.width} ${size.height}`}
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
        onClick={handleClick}
      >
        <defs>
          <linearGradient id="signal-area-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#5f9cf6" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#5f9cf6" stopOpacity="0.03" />
          </linearGradient>
        </defs>

        {ticks.map((tick) => (
          <g key={tick.value}>
            <line x1={layout.left} x2={size.width - layout.right} y1={tick.y} y2={tick.y} stroke="#e7eef5" strokeDasharray="3 6" />
            <text x={size.width - layout.right + 8} y={tick.y + 4} fontSize="11" fill="#7c8ba1">
              {tick.value.toFixed(tick.value >= 100 ? 0 : 2)}
            </text>
          </g>
        ))}

        {timeTicks.map((tick) => (
          <text key={tick.time} x={tick.x} y={size.height - 14} fontSize="11" textAnchor="middle" fill="#7c8ba1">
            {fmtAxisTime(tick.time)}
          </text>
        ))}

        {thresholds.map((line) => {
          const y = layout.top + ((layout.maxValue - line.price) / Math.max(layout.maxValue - layout.minValue, 1)) * layout.plotHeight
          return (
            <g key={line.id}>
              <line x1={layout.left} x2={size.width - layout.right} y1={y} y2={y} stroke="#d66f6f" strokeDasharray="5 6" />
              <text x={layout.left + 10} y={y - 6} fontSize="11" fill="#a94444">
                {line.label} · {line.price.toFixed(2)}
              </text>
            </g>
          )
        })}

        <path d={areaPath} fill="url(#signal-area-fill)" />
        <path d={linePath} fill="none" stroke="#2563eb" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

        {pointMeta.map((point, index) => (
          <circle
            key={`${point.time}-${point.value}`}
            cx={point.x}
            cy={point.y}
            r={hoverIndex === index ? 4.6 : 2.6}
            fill={hoverIndex === index ? '#1d4ed8' : '#60a5fa'}
            opacity={hoverIndex === null || hoverIndex === index ? 1 : 0.55}
          />
        ))}

        {hovered ? (
          <>
            <line x1={hovered.x} x2={hovered.x} y1={layout.top} y2={layout.top + layout.plotHeight} stroke="#93a8c4" strokeDasharray="4 6" />
            <circle cx={hovered.x} cy={hovered.y} r="6" fill="#ffffff" stroke="#1d4ed8" strokeWidth="2" />
          </>
        ) : null}
      </svg>

      {hovered ? (
        <div className="pointer-events-none absolute left-4 top-4 rounded-xl border border-slate-800/70 bg-slate-950/95 px-4 py-3 text-sm text-slate-100 shadow-2xl shadow-slate-900/25">
          <div className="text-xs text-slate-400">{new Date(hovered.time * 1000).toISOString().replace('T', ' ').slice(0, 19)} UTC</div>
          <div className="mt-1 font-mono text-lg font-semibold">
            {hovered.value.toFixed(hovered.value >= 100 ? 0 : 2)}{unit ? ` ${unit}` : ''}
          </div>
          <div className="mt-1 text-xs text-slate-400">{label}</div>
        </div>
      ) : null}
    </div>
  )
}
