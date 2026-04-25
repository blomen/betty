import { useEffect, useState } from 'react'

type ProviderHealth = {
  provider_id: string
  status: 'ok' | 'warning' | 'critical' | 'down'
  age_minutes: number | null
  warn_minutes: number
  crit_minutes: number
  interval_minutes: number
  is_sharp: boolean
}

type Health = {
  status: 'ok' | 'warning' | 'critical' | 'error'
  providers?: ProviderHealth[]
  issues?: string[]
}

const COLORS = {
  ok: 'text-emerald-400',
  warning: 'text-amber-400',
  critical: 'text-red-400',
  down: 'text-red-500',
  error: 'text-zinc-500',
} as const

function fmtAge(min: number | null): string {
  if (min === null) return '—'
  if (min < 1) return '<1m'
  if (min < 60) return `${Math.round(min)}m`
  return `${(min / 60).toFixed(1)}h`
}

export function ExtractionHealth() {
  const [health, setHealth] = useState<Health | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const r = await fetch('/api/health/extraction')
        if (!r.ok) return
        const d = await r.json()
        if (!cancelled) setHealth(d)
      } catch { /* ignore */ }
    }
    load()
    const id = setInterval(load, 30_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  if (!health) return null
  const status = health.status
  const color = COLORS[status as keyof typeof COLORS] ?? COLORS.error
  const providers = health.providers ?? []
  const bad = providers.filter(p => p.status !== 'ok')
  const sharp = providers.find(p => p.is_sharp)

  // Compact label: "OK" / "3 stale" / "Pinnacle DOWN"
  const label = (() => {
    if (status === 'error') return 'health err'
    if (sharp && (sharp.status === 'critical' || sharp.status === 'down')) {
      return `Pinnacle ${sharp.status === 'down' ? 'DOWN' : `${fmtAge(sharp.age_minutes)} stale`}`
    }
    if (bad.length === 0) return 'extraction ok'
    return `${bad.length} stale`
  })()

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`text-[11px] font-mono mr-3 ${color} hover:opacity-80`}
        title="Click for per-provider extraction health"
      >
        ● {label}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-zinc-900 border border-zinc-700 rounded shadow-xl p-2 min-w-[280px] max-h-[400px] overflow-y-auto">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
              Extraction health
            </span>
            <button onClick={() => setOpen(false)} className="text-zinc-600 hover:text-zinc-400 text-xs">✕</button>
          </div>
          <table className="w-full text-[11px] font-mono">
            <tbody>
              {providers.map(p => (
                <tr key={p.provider_id} className="border-b border-zinc-800/40 last:border-b-0">
                  <td className={`py-0.5 pr-2 ${COLORS[p.status]}`}>●</td>
                  <td className={`py-0.5 pr-2 ${p.is_sharp ? 'text-orange-400 font-semibold' : 'text-zinc-300'}`}>
                    {p.provider_id}
                  </td>
                  <td className="py-0.5 pr-2 text-zinc-500 text-right">{fmtAge(p.age_minutes)}</td>
                  <td className="py-0.5 text-zinc-700 text-right text-[10px]">
                    /{p.warn_minutes}m
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
