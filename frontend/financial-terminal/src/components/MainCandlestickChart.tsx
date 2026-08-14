import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { OhlcBar } from '../types'
import type { PriceAlertLine } from '../lib/priceAlerts'

const UP = '#26a69a'
const DOWN = '#ef5350'
const VISIBLE = 96
/** 成交量带占主图区（价+量）高度的比例，贴近参考图 ~22–25% */
const VOLUME_HEIGHT_SHARE = 0.24
const MIN_VOLUME_PX = 68

export type CrosshairLegend = {
  timeLabel: string
  open: number
  high: number
  low: number
  close: number
  volume: number
} | null

function fmtTime(t: number): string {
  return new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

/** 底部时间轴刻度（UTC，月/日 时:分） */
function fmtAxisTime(t: number): string {
  const d = new Date(t * 1000)
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mi = String(d.getUTCMinutes()).padStart(2, '0')
  return `${mm}/${dd} ${hh}:${mi}`
}

function timeTickIndices(barCount: number, plotW: number): number[] {
  const minGapPx = 56
  const cap = Math.max(3, Math.min(12, Math.floor(plotW / minGapPx)))
  const step = Math.max(1, Math.ceil(barCount / cap))
  const out: number[] = []
  for (let i = 0; i < barCount; i += step) out.push(i)
  const last = barCount - 1
  if (barCount > 0 && out[out.length - 1] !== last) out.push(last)
  return [...new Set(out)].sort((a, b) => a - b)
}

/** 按可视高度自动决定档位数，刻度步长取「整齐」价位 */
function niceStep(range: number, targetSteps: number): number {
  if (!(range > 0) || !Number.isFinite(range)) return 0.01
  const rough = range / Math.max(2, targetSteps - 1)
  const exp = Math.floor(Math.log10(rough))
  const f = rough / 10 ** exp
  const nf = f <= 1.5 ? 1 : f <= 3 ? 2 : f <= 7 ? 5 : 10
  return nf * 10 ** exp
}

function buildPriceTicks(minP: number, maxP: number, priceH: number): number[] {
  const range = maxP - minP
  const approx = Math.max(5, Math.min(10, Math.floor(priceH / 38)))
  const step = niceStep(range, approx)
  if (!(step > 0)) return [minP, maxP]
  const ticks: number[] = []
  let v = Math.floor(minP / step) * step
  const end = maxP + step * 2
  while (v <= end) {
    if (v >= minP - 1e-9 && v <= maxP + 1e-9) {
      const rounded = Number.parseFloat(v.toPrecision(12))
      ticks.push(rounded)
    }
    v += step
  }
  if (ticks.length < 2) return [minP, (minP + maxP) / 2, maxP]
  return ticks
}

type Props = {
  bars: OhlcBar[]
  ma20: { time: number; value: number }[]
  ma50: { time: number; value: number }[]
  ma200: { time: number; value: number }[]
  onCrosshair: (p: CrosshairLegend) => void
  alertLines: PriceAlertLine[]
  onAddAlertLine?: (price: number) => void
  lightMode?: boolean
}

const lightColors = {
  bg: '#f1f5f9', grid: '#e2e8f0', text: '#64748b', textBright: '#475569',
  volUp: 'rgba(0,0,0,0.7)', volUpStroke: 'rgba(0,0,0,0.55)',
  volDown: 'rgba(0,0,0,0.55)', volDownStroke: 'rgba(0,0,0,0.4)',
  hoverBg: 'rgba(0,0,0,0.04)', sepLine: '#cbd5e1', alertLine: '#94a3b8',
  volBorder: '#cbd5e1', legend: '#64748b', ma200: '#2563eb', ma50: '#d97706', ma20: '#ea580c',
}
const darkColors = {
  bg: '#0B0E11', grid: '#1a1f26', text: '#8b949e', textBright: '#f1f5f9',
  volUp: 'rgba(255,255,255,0.88)', volUpStroke: 'rgba(255,255,255,0.72)',
  volDown: 'rgba(255,255,255,0.5)', volDownStroke: 'rgba(255,255,255,0.35)',
  hoverBg: 'rgba(255,255,255,0.045)', sepLine: '#2a2f36', alertLine: '#5c6370',
  volBorder: '#3d4450', legend: '#64748b', ma200: '#42a5f5', ma50: '#ffd54f', ma20: '#fb923c',
}

export default function MainCandlestickChart({
  bars,
  ma20,
  ma50,
  ma200,
  onCrosshair,
  alertLines,
  onAddAlertLine,
  lightMode,
}: Props) {
  const C = lightMode ? lightColors : darkColors
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 520 })
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [hoverPrice, setHoverPrice] = useState<number | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.height > 0) setSize({ w: Math.floor(r.width), h: Math.floor(r.height) })
    })
    ro.observe(el)
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.height > 0) setSize({ w: Math.floor(r.width), h: Math.floor(r.height) })
    return () => ro.disconnect()
  }, [])

  const visible = useMemo(() => bars.slice(-VISIBLE), [bars])

  const { minP, maxP, maxVol, left, top, priceH, volTop, volH, plotW, slotW } =
    useMemo(() => {
      const W = size.w
      const H = size.h
      const left = 8
      const right = 58
      const top = 8
      /** 底部：时间轴 + 图例说明 */
      const bottom = 46
      const inner = Math.max(80, H - top - bottom)
      const volH = Math.max(MIN_VOLUME_PX, Math.round(inner * VOLUME_HEIGHT_SHARE))
      const gap = 4
      const priceH = Math.max(100, inner - volH - gap)
      const volTop = top + priceH + gap
      const plotW = W - left - right
      const n = Math.max(visible.length, 1)
      const slotW = plotW / n
      let minP = Infinity
      let maxP = -Infinity
      let maxVol = 1
      for (const b of visible) {
        minP = Math.min(minP, b.low)
        maxP = Math.max(maxP, b.high)
        maxVol = Math.max(maxVol, b.volume)
      }
      const pad = (maxP - minP) * 0.035 || 0.08
      return {
        minP: minP - pad,
        maxP: maxP + pad,
        maxVol,
        left,
        right,
        top,
        priceH,
        volTop,
        volH,
        bottom,
        plotW,
        slotW,
      }
    }, [size, visible])

  const yPrice = useCallback(
    (p: number) => top + ((maxP - p) / (maxP - minP || 1)) * priceH,
    [top, priceH, maxP, minP],
  )

  const priceTicks = useMemo(() => {
    const values = buildPriceTicks(minP, maxP, priceH)
    return values.map((p) => ({ p, y: yPrice(p), label: p.toFixed(2) }))
  }, [minP, maxP, priceH, yPrice])

  const ma20Map = useMemo(() => new Map(ma20.map((m) => [m.time, m.value])), [ma20])
  const ma50Map = useMemo(() => new Map(ma50.map((m) => [m.time, m.value])), [ma50])
  const ma200Map = useMemo(() => new Map(ma200.map((m) => [m.time, m.value])), [ma200])

  function buildMaPoints(maMap: Map<number, number>): string {
    const pts: string[] = []
    for (let i = 0; i < visible.length; i++) {
      const v = maMap.get(visible[i].time)
      if (v === undefined) continue
      const cx = left + i * slotW + slotW / 2
      const y = yPrice(v)
      pts.push(`${cx},${y}`)
    }
    return pts.join(' ')
  }

  const ma20Points = useMemo(() => buildMaPoints(ma20Map), [visible, ma20Map, left, slotW, yPrice])
  const ma50Points = useMemo(() => buildMaPoints(ma50Map), [visible, ma50Map, left, slotW, yPrice])
  const ma200Points = useMemo(() => buildMaPoints(ma200Map), [visible, ma200Map, left, slotW, yPrice])

  const verticalGridXs = useMemo(() => {
    const cols = 8
    const xs: number[] = []
    for (let i = 1; i < cols; i++) xs.push(left + (plotW * i) / cols)
    return xs
  }, [left, plotW])

  const chartBottomY = volTop + volH
  const timeTicks = useMemo(() => {
    const n = visible.length
    if (n < 1) return [] as { i: number; x: number; label: string }[]
    const idxs = timeTickIndices(n, plotW)
    return idxs.map((i) => ({
      i,
      x: left + i * slotW + slotW / 2,
      label: fmtAxisTime(visible[i].time),
    }))
  }, [visible, plotW, left, slotW])

  const watermarkFont = Math.min(plotW * 0.085, priceH * 0.38, 56)
  const wmX = left + plotW / 2
  const wmY = top + priceH / 2

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget
    const rect = svg.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const x = mx - left
    if (x < 0 || x > plotW) {
      setHoverIdx(null)
      setHoverPrice(null)
      onCrosshair(null)
      return
    }
    const idx = Math.min(visible.length - 1, Math.max(0, Math.floor(x / slotW)))
    setHoverIdx(idx)
    const b = visible[idx]
    onCrosshair({
      timeLabel: fmtTime(b.time),
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
      volume: b.volume,
    })

    if (my >= top && my <= volTop) {
      const raw = maxP - ((my - top) / (priceH || 1)) * (maxP - minP)
      setHoverPrice(raw)
    } else {
      setHoverPrice(null)
    }
  }

  const handleLeave = () => {
    setHoverIdx(null)
    setHoverPrice(null)
    onCrosshair(null)
  }

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!onAddAlertLine) return
    const svg = e.currentTarget
    const rect = svg.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const x = mx - left
    if (x < 0 || x > plotW) return
    if (my < top || my > volTop) return
    const raw = maxP - ((my - top) / (priceH || 1)) * (maxP - minP)
    onAddAlertLine(Number(raw.toPrecision(8)))
  }

  const cxCross =
    hoverIdx !== null ? left + hoverIdx * slotW + slotW / 2 : null

  const hoverLineY =
    hoverPrice !== null
      ? Math.min(volTop - 1, Math.max(top + 1, yPrice(hoverPrice)))
      : null

  const volPad = 3

  return (
    <div ref={wrapRef} className="h-full min-h-0 w-full flex-1 basis-0">
      <svg
        width={size.w}
        height={size.h}
        className="block cursor-crosshair touch-none"
        onMouseMove={handleMove}
        onMouseLeave={handleLeave}
        onClick={handleClick}
      >
        <rect width={size.w} height={size.h} fill={lightMode ? "#f1f5f9" : "#0B0E11"} />

        {verticalGridXs.map((gx) => (
          <line
            key={gx}
            x1={gx}
            y1={top}
            x2={gx}
            y2={chartBottomY}
            stroke={lightMode ? "#e2e8f0" : "#1a1f26"}
            strokeWidth={1}
          />
        ))}

        {priceTicks.map((t) => (
          <g key={`${t.p}-${t.y}`}>
            <line
              x1={left}
              y1={t.y}
              x2={left + plotW}
              y2={t.y}
              stroke={C.grid}
              strokeWidth={1}
            />
            <text x={left + plotW + 5} y={t.y + 4} fill={C.text} fontSize={10} fontFamily="monospace">
              {t.label}
            </text>
          </g>
        ))}

        <text
          x={wmX}
          y={wmY}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={C.hoverBg}
          fontSize={watermarkFont}
          fontWeight={700}
          style={{ fontFamily: 'system-ui, Segoe UI, sans-serif', userSelect: 'none' }}
        >
          世界状态
        </text>

        {visible.map((b, i) => {
          const cx = left + i * slotW + slotW / 2
          const bodyW = Math.max(2, slotW * 0.55)
          const yH = yPrice(b.high)
          const yL = yPrice(b.low)
          const yO = yPrice(b.open)
          const yC = yPrice(b.close)
          const up = b.close >= b.open
          const col = up ? UP : DOWN
          const yTop = Math.min(yO, yC)
          const yBot = Math.max(yO, yC)
          const bodyH = Math.max(1, yBot - yTop)
          return (
            <g key={`${b.time}-${i}`}>
              <line x1={cx} y1={yH} x2={cx} y2={yL} stroke={col} strokeWidth={1} />
              <rect
                x={cx - bodyW / 2}
                y={yTop}
                width={bodyW}
                height={bodyH}
                fill={col}
                stroke={col}
                strokeWidth={0.5}
              />
            </g>
          )
        })}

        {ma200Points ? (
          <polyline fill="none" stroke={C.ma200} strokeWidth={1.35} points={ma200Points} />
        ) : null}
        {ma50Points ? (
          <polyline fill="none" stroke={C.ma50} strokeWidth={1.25} points={ma50Points} />
        ) : null}
        {ma20Points ? (
          <polyline fill="none" stroke={C.ma20} strokeWidth={1} points={ma20Points} />
        ) : null}

        {alertLines.map((al) => {
          const y = Math.min(volTop - 1, Math.max(top + 1, yPrice(al.price)))
          return (
            <g key={al.id}>
              <line
                x1={left}
                y1={y}
                x2={left + plotW}
                y2={y}
                stroke={C.volUp}
                strokeWidth={1}
                strokeDasharray="7 5"
              />
              <text
                x={left + plotW + 4}
                y={y + 4}
                fill={C.textBright}
                fontSize={10}
                fontFamily="monospace"
              >
                {al.price.toFixed(2)}
              </text>
            </g>
          )
        })}

        {hoverLineY !== null ? (
          <g>
            <line
              x1={left}
              y1={hoverLineY}
              x2={left + plotW}
              y2={hoverLineY}
              stroke={C.volUpStroke}
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            {hoverPrice !== null ? (
              <text
                x={left + 2}
                y={hoverLineY - 5}
                fill={C.volUp}
                fontSize={10}
                fontFamily="monospace"
              >
                {hoverPrice.toFixed(2)} · 点击添加风险阈值
              </text>
            ) : null}
          </g>
        ) : null}

        <line x1={left} y1={volTop} x2={left + plotW} y2={volTop} stroke={C.sepLine} strokeWidth={1} />

        {visible.map((b, i) => {
          const hRatio = b.volume / maxVol
          const barW = Math.max(1, slotW * 0.5)
          const cx = left + i * slotW + slotW / 2
          const innerVolH = volH - volPad * 2
          const vh = hRatio * innerVolH
          const y0 = volTop + volH - volPad - vh
          const up = b.close >= b.open
          return (
            <rect
              key={`v-${b.time}-${i}`}
              x={cx - barW / 2}
              y={y0}
              width={barW}
              height={Math.max(1, vh)}
              fill={up ? 'rgba(38,166,154,0.62)' : 'rgba(239,83,80,0.62)'}
            />
          )
        })}

        <line
          x1={left}
          y1={chartBottomY}
          x2={left + plotW}
          y2={chartBottomY}
          stroke={C.sepLine}
          strokeWidth={1}
        />
        {timeTicks.map((tt) => (
          <g key={`t-${tt.i}`}>
            <line
              x1={tt.x}
              y1={chartBottomY}
              x2={tt.x}
              y2={chartBottomY + 5}
              stroke={C.alertLine}
              strokeWidth={1}
            />
            <text
              x={tt.x}
              y={chartBottomY + 18}
              textAnchor="middle"
              fill={C.text}
              fontSize={11}
              fontFamily="monospace"
            >
              {tt.label}
            </text>
          </g>
        ))}

        {cxCross !== null ? (
          <line
            x1={cxCross}
            y1={top}
            x2={cxCross}
            y2={chartBottomY + 22}
            stroke={C.volBorder}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
        ) : null}

        <text x={left} y={size.h - 6} fill={C.legend} fontSize={9} fontFamily="monospace">
          MA20 短周期 · MA50 中周期 · MA200 长周期 — 点击固定风险阈值；十字准星查看世界状态 OHLC
        </text>
      </svg>
    </div>
  )
}
