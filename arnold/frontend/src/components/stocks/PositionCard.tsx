import type { Position, ModelStatus } from '@/types/stocks'

interface Props {
  positions: Position[]
  modelStatus: ModelStatus | null
  lastPrice: number | null
}

export function PositionCard({ positions, modelStatus, lastPrice }: Props) {
  const pos = positions[0] ?? null
  const flat = !pos || pos.size === 0
  const ms = modelStatus

  if (flat) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Position</div>
        <div className="text-zinc-400">Flat</div>
        {ms?.session_pnl !== undefined && (
          <div className="text-zinc-500 mt-1">Session PnL: ${ms.session_pnl.toFixed(2)}</div>
        )}
      </div>
    )
  }

  const sideStr = typeof pos.side === 'string' ? pos.side : (pos.side === 0 ? 'long' : 'short')
  const entry = ms?.entry_price ?? pos.price
  const stop = ms?.stop_price
  const isLong = sideStr === 'long'
  const dir = isLong ? 1 : -1
  const unrealized = lastPrice !== null && entry ? (lastPrice - entry) * dir * pos.size * 20 : 0
  const rMult = stop && entry && lastPrice !== null ? ((lastPrice - entry) * dir) / Math.abs(entry - stop) : 0

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Position</span>
        <span className={`px-2 py-0.5 text-[10px] uppercase rounded ${isLong ? 'bg-emerald-900/50 text-emerald-400' : 'bg-red-900/50 text-red-400'}`}>
          {sideStr} × {pos.size}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-300">
        <span className="text-zinc-500">Entry</span><span>{entry?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Stop</span><span>{stop?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Last</span><span>{lastPrice?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Unrealized</span>
        <span className={unrealized >= 0 ? 'text-emerald-400' : 'text-red-400'}>${unrealized.toFixed(2)}</span>
        <span className="text-zinc-500">R-multiple</span>
        <span className={rMult >= 0 ? 'text-emerald-400' : 'text-red-400'}>{rMult.toFixed(2)}R</span>
      </div>
    </div>
  )
}
