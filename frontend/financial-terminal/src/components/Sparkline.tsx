type Props = { values: number[]; positive?: boolean; className?: string }

export default function Sparkline({ values, positive = true, className = '' }: Props) {
  if (values.length < 2) return <div className={`h-8 w-20 ${className}`} />
  const min = Math.min(...values)
  const max = Math.max(...values)
  const pad = 2
  const w = 72
  const h = 28
  const range = max - min || 1
  const pts = values
    .map((v, i) => {
      const x = pad + (i / (values.length - 1)) * (w - pad * 2)
      const y = h - pad - ((v - min) / range) * (h - pad * 2)
      return `${x},${y}`
    })
    .join(' ')
  const stroke = positive ? '#26a69a' : '#ef5350'
  return (
    <svg width={w} height={h} className={className} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <polyline fill="none" stroke={stroke} strokeWidth="1.5" points={pts} />
    </svg>
  )
}
