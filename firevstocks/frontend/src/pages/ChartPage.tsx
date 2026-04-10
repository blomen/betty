import { useState, useEffect, useCallback } from 'react'
import { CandleChart } from './CandleChart'
import type { CandleData, ExpandedSession, Zone, Signal, Fill, ExitEvent } from '@/types'
import type { TickEvent } from '@/hooks/useDashboardWS'

interface Props {
  lastTick: TickEvent | null
  session: ExpandedSession | null
  zones: Zone[]
  signals: Signal[]
  fills: Fill[]
  exits: ExitEvent[]
}

export function ChartPage({ lastTick, session, zones, signals: _signals, fills: _fills, exits: _exits }: Props) {
  const [interval, setInterval_] = useState<'1m' | '5m' | '15m'>('5m')
  const [hiddenLevels, setHiddenLevels] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('firevstocks-hidden-levels')
      return saved ? new Set(JSON.parse(saved)) : new Set()
    } catch { return new Set() }
  })

  useEffect(() => {
    localStorage.setItem('firevstocks-hidden-levels', JSON.stringify([...hiddenLevels]))
  }, [hiddenLevels])

  const toggleLevel = useCallback((key: string) => {
    setHiddenLevels(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])
  void toggleLevel // available for future use

  const [lastCandle, setLastCandle] = useState<CandleData | null>(null)
  const candleIntervalSec = interval === '1m' ? 60 : interval === '5m' ? 300 : 900

  useEffect(() => {
    if (!lastTick) return
    const { price, ts } = lastTick
    const bucketStart = Math.floor(ts / candleIntervalSec) * candleIntervalSec

    setLastCandle(prev => {
      if (prev && prev.t === bucketStart) {
        return {
          ...prev,
          h: Math.max(prev.h, price),
          l: Math.min(prev.l, price),
          c: price,
          v: prev.v + 1,
        }
      }
      return { t: bucketStart, o: price, h: price, l: price, c: price, v: 1 }
    })
  }, [lastTick, candleIntervalSec])

  const price = lastTick?.price ?? session?.price_position?.last_price ?? null

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-1">
      <div className="flex items-center gap-3 px-1">
        {price && (
          <span className="text-sm font-mono font-bold text-zinc-200">
            NQ {price.toFixed(2)}
          </span>
        )}
        <div className="flex gap-1">
          {(['1m', '5m', '15m'] as const).map(iv => (
            <button
              key={iv}
              onClick={() => setInterval_(iv)}
              className={`px-2 py-0.5 text-[10px] font-mono border ${
                interval === iv
                  ? 'border-amber-500 text-amber-500'
                  : 'border-zinc-700 text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {iv}
            </button>
          ))}
        </div>
        {lastTick && (
          <span className="text-[10px] font-mono text-zinc-600">
            {lastTick.tick_count.toLocaleString()} ticks
          </span>
        )}
      </div>
      <div className="flex-1 border border-zinc-800 bg-zinc-950 min-h-0 overflow-hidden">
        <CandleChart
          lastCandle={lastCandle}
          session={session}
          hiddenLevels={hiddenLevels}
          zones={zones}
        />
      </div>
    </div>
  )
}
