import type { Signal, Fill, ExitEvent } from '@/types/stocks'

interface Props {
  signals: Signal[]
  fills: Fill[]
  exits: ExitEvent[]
}

interface Row {
  ts: number
  kind: 'signal' | 'fill' | 'exit'
  text: string
  color: string
}

export function EventLog({ signals, fills, exits }: Props) {
  const rows: Row[] = []
  for (const s of signals.slice(-30)) {
    rows.push({
      ts: s.ts ?? 0,
      kind: 'signal',
      text: `${s.action} conf=${s.confidence?.toFixed(2) ?? '—'} zone=${s.zone ?? '—'}`,
      color: 'text-zinc-300',
    })
  }
  for (const f of fills.slice(-30)) {
    rows.push({
      ts: f.ts,
      kind: 'fill',
      text: `${f.side} ${f.size}@${f.price.toFixed(2)}`,
      color: f.side === 'long' ? 'text-emerald-400' : 'text-red-400',
    })
  }
  for (const e of exits.slice(-30)) {
    rows.push({
      ts: e.ts,
      kind: 'exit',
      text: `exit @${e.price.toFixed(2)}${e.was_stop ? ' (STOP)' : ''}`,
      color: e.was_stop ? 'text-red-400' : 'text-zinc-400',
    })
  }
  rows.sort((a, b) => b.ts - a.ts)

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="text-zinc-500 uppercase tracking-wider mb-2">Recent Events</div>
      <div className="max-h-64 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="text-zinc-500">none</div>
        ) : (
          rows.slice(0, 50).map((r, i) => {
            const time = r.ts ? new Date(r.ts * 1000).toLocaleTimeString() : ''
            return (
              <div key={i} className="flex gap-2 py-0.5">
                <span className="text-zinc-600 w-20 shrink-0">{time}</span>
                <span className="text-zinc-500 w-12 shrink-0 uppercase">{r.kind}</span>
                <span className={`${r.color} truncate`}>{r.text}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
