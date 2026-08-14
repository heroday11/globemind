import { useEffect } from 'react'
import { IconAlertTriangle } from './Icons'

export type ToastItem = {
  id: string
  title: string
  subtitle: string
}

const DISMISS_MS = 6000

export default function AlertToastStack({
  items,
  onDismiss,
}: {
  items: ToastItem[]
  onDismiss: (id: string) => void
}) {
  return (
    <div
      className="pointer-events-none fixed right-4 top-[4.25rem] z-[200] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2"
      aria-live="polite"
    >
      {items.map((t) => (
        <ToastCard key={t.id} item={t} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: (id: string) => void }) {
  useEffect(() => {
    const id = window.setTimeout(() => onDismiss(item.id), DISMISS_MS)
    return () => window.clearTimeout(id)
  }, [item.id, onDismiss])

  return (
    <div
      className="pointer-events-auto rounded-lg border border-rose-500/35 bg-[#14171c]/95 px-4 py-3 shadow-xl shadow-black/50 backdrop-blur-md transition-opacity duration-300"
      role="alert"
    >
      <div className="flex gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-rose-500/15 text-rose-400">
          <IconAlertTriangle className="h-6 w-6" />
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <div className="text-sm font-semibold tracking-tight text-rose-100">{item.title}</div>
          <p className="mt-1 text-sm leading-snug text-slate-400">{item.subtitle}</p>
        </div>
        <button
          type="button"
          onClick={() => onDismiss(item.id)}
          className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-slate-500 transition-colors hover:bg-white/10 hover:text-slate-300"
        >
          关闭
        </button>
      </div>
      <div className="mt-2 h-0.5 overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full origin-left bg-gradient-to-r from-rose-500/80 to-amber-500/50"
          style={{
            animation: `alertToastBar ${DISMISS_MS}ms linear forwards`,
          }}
        />
      </div>
    </div>
  )
}
