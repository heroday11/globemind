import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { IconActivity } from './Icons'

const nav = [
  { to: '/', label: '世界状态' },
  { to: '/numerical-alert', label: '阈值预警' },
]

export default function TerminalHeader() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  return (
    <header className="border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur md:px-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:gap-5">
          <div className="flex min-w-0 items-center gap-3">
            <div className="rounded-2xl bg-slate-950 p-2 text-white shadow-[0_14px_28px_rgba(15,23,42,0.18)]">
              <IconActivity className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-slate-900">GlobeMind 世界状态终端</div>
              <div className="truncate text-[11px] text-slate-500">全球指标与风险信号</div>
            </div>
          </div>

          <nav className="flex w-fit items-center gap-1 rounded-full border border-slate-200 bg-slate-50 p-1">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-semibold transition-colors sm:text-sm ${
                    isActive ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-900'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="font-mono text-xs text-slate-500">
          {now.toISOString().replace('T', ' ').slice(0, 19)} UTC
        </div>
      </div>
    </header>
  )
}
