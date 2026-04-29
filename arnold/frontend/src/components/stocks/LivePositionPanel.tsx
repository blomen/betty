import { useEffect, useRef, useState } from 'react'
import type { Fill, ExitEvent, ModelStatus, Position, Quote } from '@/types/stocks'

interface Props {
  positions: Position[]
  lastPrice: number | null
  fills: Fill[]
  exits: ExitEvent[]
  quote?: Quote | null
  modelStatus: ModelStatus | null
}

const TICK_SIZE = 0.25
// NQ futures: $5 per tick per contract.
const NQ_DOLLARS_PER_TICK = 5

function fmtCurrency(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const sign = v < 0 ? '-' : v > 0 ? '+' : ''
  return `${sign}$${Math.abs(v).toFixed(2)}`
}

function fmtAge(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3600_000) {
    const m = Math.floor(ms / 60_000)
    const s = Math.round((ms % 60_000) / 1000)
    return `${m}m ${s}s`
  }
  const h = Math.floor(ms / 3600_000)
  const m = Math.round((ms % 3600_000) / 60_000)
  return `${h}h ${m}m`
}

function PnLPill({ value }: { value: number | null }) {
  if (value === null || !Number.isFinite(value)) {
    return <span className="text-zinc-500 tabular-nums">—</span>
  }
  const cls = value > 0 ? 'text-emerald-300' : value < 0 ? 'text-red-300' : 'text-zinc-300'
  return <span className={`tabular-nums font-semibold ${cls}`}>{fmtCurrency(value)}</span>
}

export function LivePositionPanel({
  positions,
  lastPrice,
  fills,
  exits,
  quote,
  modelStatus,
}: Props) {
  const model = modelStatus
  const [now, setNow] = useState(Date.now())
  const [flash, setFlash] = useState<'fill' | 'exit' | null>(null)
  const lastFillRef = useRef<number>(0)
  const lastExitRef = useRef<number>(0)

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(tick)
  }, [])

  // Flash on new fill/exit
  useEffect(() => {
    if (fills.length === 0) return
    const last = fills[fills.length - 1]
    const ts = (last.ts ?? 0) * 1000
    if (ts > lastFillRef.current) {
      lastFillRef.current = ts
      setFlash('fill')
      const t = setTimeout(() => setFlash(null), 1800)
      return () => clearTimeout(t)
    }
  }, [fills])

  useEffect(() => {
    if (exits.length === 0) return
    const last = exits[exits.length - 1]
    const ts = (last.ts ?? 0) * 1000
    if (ts > lastExitRef.current) {
      lastExitRef.current = ts
      setFlash('exit')
      const t = setTimeout(() => setFlash(null), 1800)
      return () => clearTimeout(t)
    }
  }, [exits])

  // Prefer model-status (server-side broker tracker) over the positions array
  // because tracker has entry+stop already wired. Fall back to positions[0]
  // when model-status hasn't arrived yet.
  const isFlat = model?.is_flat ?? positions.length === 0
  const side = model?.position_side ?? (positions[0]?.side as string | undefined) ?? null
  const size = model?.position_size ?? positions[0]?.size ?? 0
  const entry = model?.entry_price ?? positions[0]?.price ?? null
  const stop = model?.stop_price ?? null

  const sideStr = (side ?? '').toString().toLowerCase()
  const isLong = sideStr === 'long' || sideStr === 'buy' || sideStr === '1'
  const isShort = sideStr === 'short' || sideStr === 'sell' || sideStr === '2'
  const sideLabel = isFlat ? 'FLAT' : isLong ? 'LONG' : isShort ? 'SHORT' : (side ?? '—').toString().toUpperCase()
  const sideColor = isLong ? 'text-emerald-400' : isShort ? 'text-red-400' : 'text-zinc-400'
  const dotColor = isLong
    ? 'bg-emerald-500'
    : isShort
      ? 'bg-red-500'
      : 'bg-zinc-600'

  // Live unrealized PnL — recomputed on every lastPrice update.
  let unrealized: number | null = null
  let unrealizedTicks: number | null = null
  let rMultiple: number | null = null
  let stopDistTicks: number | null = null
  let stopDistDollars: number | null = null
  if (!isFlat && entry !== null && lastPrice !== null) {
    const dir = isLong ? 1 : isShort ? -1 : 0
    if (dir !== 0) {
      const movePts = (lastPrice - entry) * dir
      unrealizedTicks = movePts / TICK_SIZE
      unrealized = unrealizedTicks * NQ_DOLLARS_PER_TICK * size
    }
    if (stop !== null) {
      stopDistTicks = (Math.abs(lastPrice - stop)) / TICK_SIZE
      stopDistDollars = stopDistTicks * NQ_DOLLARS_PER_TICK * size
      // R-multiple = unrealizedTicks / risk_ticks where risk_ticks is the
      // INITIAL stop distance from entry. Without entry-time stop history we
      // approximate using entry→stop as risk (this is what the tracker does).
      const riskTicks = Math.abs(entry - stop) / TICK_SIZE
      if (riskTicks > 0 && unrealizedTicks !== null) {
        rMultiple = unrealizedTicks / riskTicks
      }
    }
  }

  // Position age (best-effort: time since last opening fill of this side)
  let posAgeMs: number | null = null
  if (!isFlat && fills.length > 0) {
    for (let i = fills.length - 1; i >= 0; i--) {
      const f = fills[i]
      const fSide = (f.side ?? '').toString().toLowerCase()
      if ((isLong && (fSide === 'long' || fSide === 'buy' || fSide === '1')) ||
          (isShort && (fSide === 'short' || fSide === 'sell' || fSide === '2'))) {
        posAgeMs = now - (f.ts ?? 0) * 1000
        break
      }
    }
  }

  const halted = model?.halted ?? false
  const haltReason = model?.halt_reason
  const sessionPnL = model?.session_pnl ?? null
  const peakEquity = model?.peak_equity ?? null
  const trailingDd = model?.trailing_dd ?? null
  const consecutiveStops = model?.consecutive_stops ?? 0
  const tradeCount = model?.trade_count ?? 0

  const flashClass =
    flash === 'fill'
      ? 'ring-2 ring-emerald-500/70'
      : flash === 'exit'
        ? 'ring-2 ring-red-500/70'
        : ''

  // Flat / waiting: render a slim strip
  if (isFlat) {
    return (
      <div
        className={`rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs font-mono flex items-center gap-3 transition-shadow ${flashClass}`}
      >
        <span className={`inline-block h-2 w-2 rounded-full ${dotColor}`} />
        <span className="text-zinc-200 font-semibold">FLAT</span>
        <span className="text-zinc-500">last</span>
        <span className="text-zinc-200 tabular-nums">{lastPrice?.toFixed(2) ?? '—'}</span>
        {quote && quote.bid && quote.ask && (
          <>
            <span className="text-zinc-700">·</span>
            <span className="text-zinc-500">bid</span>
            <span className="text-zinc-300 tabular-nums">{quote.bid.toFixed(2)}</span>
            <span className="text-zinc-500">ask</span>
            <span className="text-zinc-300 tabular-nums">{quote.ask.toFixed(2)}</span>
            <span className="text-zinc-500 text-[10px]">
              ({Math.round((quote.ask - quote.bid) / TICK_SIZE)}t)
            </span>
          </>
        )}
        <span className="text-zinc-700">·</span>
        <span className="text-zinc-500">session</span>
        <PnLPill value={sessionPnL} />
        {peakEquity !== null && trailingDd !== null && (
          <>
            <span className="text-zinc-700">·</span>
            <span className="text-zinc-500">trail dd</span>
            <span className="text-zinc-200 tabular-nums">{fmtCurrency(trailingDd)}</span>
          </>
        )}
        {tradeCount > 0 && (
          <>
            <span className="text-zinc-700">·</span>
            <span className="text-zinc-500">trades {tradeCount}</span>
          </>
        )}
        {halted && (
          <span className="ml-auto px-2 py-0.5 rounded bg-red-900/50 text-red-300 text-[10px] uppercase tracking-wider">
            halted{haltReason ? ` · ${haltReason}` : ''}
          </span>
        )}
      </div>
    )
  }

  // In position: full live panel
  return (
    <div
      className={`rounded border-2 ${isLong ? 'border-emerald-700/60' : 'border-red-700/60'} bg-zinc-900 p-3 font-mono text-xs transition-shadow ${flashClass}`}
    >
      <div className="flex items-center gap-3 mb-3">
        <span className={`inline-block h-3 w-3 rounded-full ${dotColor} animate-pulse`} />
        <span className={`text-lg font-bold ${sideColor}`}>{sideLabel}</span>
        <span className="text-zinc-500">×</span>
        <span className="text-zinc-200 text-lg">{size}</span>
        {posAgeMs !== null && (
          <span className="text-zinc-500 ml-2">held {fmtAge(posAgeMs)}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-zinc-500 text-[10px] uppercase tracking-wider">unrealized</span>
          <span className="text-lg">
            <PnLPill value={unrealized} />
          </span>
          {rMultiple !== null && (
            <span
              className={`text-[11px] tabular-nums px-2 py-0.5 rounded ${rMultiple >= 0 ? 'bg-emerald-900/40 text-emerald-300' : 'bg-red-900/40 text-red-300'}`}
            >
              {rMultiple >= 0 ? '+' : ''}{rMultiple.toFixed(2)}R
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">Entry</div>
          <div className="text-zinc-200 tabular-nums text-base">
            {entry !== null ? entry.toFixed(2) : '—'}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">Last</div>
          <div className="text-zinc-200 tabular-nums text-base">
            {lastPrice !== null ? lastPrice.toFixed(2) : '—'}
          </div>
          {unrealizedTicks !== null && (
            <div className={`text-[10px] tabular-nums ${unrealizedTicks > 0 ? 'text-emerald-400' : unrealizedTicks < 0 ? 'text-red-400' : 'text-zinc-500'}`}>
              {unrealizedTicks > 0 ? '+' : ''}{unrealizedTicks.toFixed(1)}t
            </div>
          )}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">Stop</div>
          <div className="text-zinc-200 tabular-nums text-base">
            {stop !== null ? stop.toFixed(2) : '—'}
          </div>
          {stopDistTicks !== null && stopDistDollars !== null && (
            <div className="text-[10px] tabular-nums text-zinc-500">
              {stopDistTicks.toFixed(1)}t · ${stopDistDollars.toFixed(0)}
            </div>
          )}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">Session</div>
          <div className="text-base">
            <PnLPill value={sessionPnL} />
          </div>
          {trailingDd !== null && (
            <div className="text-[10px] tabular-nums text-zinc-500">
              trail dd {fmtCurrency(trailingDd)}
            </div>
          )}
        </div>
      </div>

      {(consecutiveStops > 0 || halted) && (
        <div className="mt-2 pt-2 border-t border-zinc-800 flex items-center gap-3 text-[10px]">
          {consecutiveStops > 0 && (
            <span className="text-amber-400">
              {consecutiveStops} consec stop{consecutiveStops === 1 ? '' : 's'}
            </span>
          )}
          {halted && (
            <span className="px-2 py-0.5 rounded bg-red-900/50 text-red-300 uppercase tracking-wider">
              halted{haltReason ? ` · ${haltReason}` : ''}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
