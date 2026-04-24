import { useState, useEffect, useCallback } from 'react'
import { CandleChart } from './CandleChart'
import { api } from '@/hooks/useStocksApi'
import type { CandleData, ExpandedSession, Zone, Signal, Fill, ExitEvent, ModelStatus } from '@/types/stocks'
import type { TickEvent } from '@/hooks/useDashboardWS'

interface Props {
  lastTick: TickEvent | null
  session: ExpandedSession | null
  zones: Zone[]
  signals: Signal[]
  fills: Fill[]
  exits: ExitEvent[]
}

// Every individual level key. Zones already cluster these server-side with
// a hierarchy score, so we hide them all by default — the chart now shows
// zones as the single consolidated view. Kept as a constant so the
// CandleChart renderer, which still knows how to draw each key, skips them.
const INDIVIDUAL_LEVEL_KEYS = [
  'ibh', 'ibl',
  'pdh', 'pdl',
  'tokyo_h', 'tokyo_l',
  'london_h', 'london_l',
  'd_poc', 'd_vah', 'd_val',
  'w_poc', 'w_vah', 'w_val',
  'm_poc', 'm_vah', 'm_val',
  'tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val',
  'tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val',
  'tpo_ny_letters', 'tpo_ny_poc', 'tpo_ny_vah', 'tpo_ny_val',
  'daily_swing', 'weekly_swing',
]

// User-toggleable groups. The VP *histograms* (vp_session / vp_weekly /
// vp_monthly) are distribution shapes on the right axis, not level lines,
// so they stay. Everything else collapses into zones.
const LEVEL_GROUPS: Record<string, string[]> = {
  vwap: ['vwap'],
  zones: ['zones'],
  fvg: ['fvg'],
  daily_vp: ['vp_session'],
  weekly_vp: ['vp_weekly'],
  monthly_vp: ['vp_monthly'],
}

const TOGGLE_SECTIONS: Array<{ label: string; groups: Array<{ key: string; label: string; color: string }> }> = [
  {
    label: 'Overlays',
    groups: [
      { key: 'vwap', label: 'VWAP', color: '#EAB308' },
      { key: 'zones', label: 'Zones', color: '#A78BFA' },
      { key: 'fvg', label: 'FVG Confluence', color: '#10B981' },
    ],
  },
  {
    label: 'Volume Profile',
    groups: [
      { key: 'daily_vp', label: 'Daily VP', color: '#A855F7' },
      { key: 'weekly_vp', label: 'Weekly VP', color: '#EC4899' },
      { key: 'monthly_vp', label: 'Monthly VP', color: '#F59E0B' },
    ],
  },
]

export function ChartPage({ lastTick, session, zones, signals, fills, exits }: Props) {
  const [interval, setInterval_] = useState<'1m' | '5m' | '15m'>('5m')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [hiddenLevels, setHiddenLevels] = useState<Set<string>>(() => {
    // Always hide individual levels — zones replace them. User-controllable
    // toggles (VWAP, zones, FVG, VP histograms) persist via localStorage.
    const hidden = new Set<string>(INDIVIDUAL_LEVEL_KEYS)
    try {
      const saved = localStorage.getItem('arnoldstocks-hidden-levels')
      if (saved) {
        for (const k of JSON.parse(saved) as string[]) hidden.add(k)
      }
    } catch { /* ignore */ }
    return hidden
  })

  useEffect(() => {
    // Persist only the user-controlled toggles; individual-level keys are
    // hidden by default and re-added on load.
    const persisted = [...hiddenLevels].filter(k => !INDIVIDUAL_LEVEL_KEYS.includes(k))
    localStorage.setItem('arnoldstocks-hidden-levels', JSON.stringify(persisted))
  }, [hiddenLevels])

  const toggleGroup = useCallback((group: string) => {
    const keys = LEVEL_GROUPS[group]
    if (!keys) return
    setHiddenLevels(prev => {
      const next = new Set(prev)
      const allHidden = keys.every(k => next.has(k))
      keys.forEach(k => allHidden ? next.delete(k) : next.add(k))
      return next
    })
  }, [])

  const toggleAll = useCallback(() => {
    setHiddenLevels(prev => {
      const allKeys = Object.values(LEVEL_GROUPS).flat()
      const allHidden = allKeys.every(k => prev.has(k))
      return new Set(allHidden ? [] : allKeys)
    })
  }, [])

  const isGroupHidden = (group: string) => {
    const keys = LEVEL_GROUPS[group]
    return keys ? keys.every(k => hiddenLevels.has(k)) : false
  }

  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)

  useEffect(() => {
    const fetch = () => { api.getModelStatus().then(setModelStatus).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 5_000)
    return () => clearInterval(iv)
  }, [])

  const [lastCandle, setLastCandle] = useState<CandleData | null>(null)
  const candleIntervalSec = interval === '1m' ? 60 : interval === '5m' ? 300 : 900

  // Reset live candle when interval changes
  useEffect(() => {
    setLastCandle(null)
  }, [interval])

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
      {/* Status bar */}
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
        <div className="flex-1" />
        <button
          onClick={() => setSidebarOpen(v => !v)}
          className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 px-1"
        >
          {sidebarOpen ? '▶ Hide' : '◀ Show'} Indicators
        </button>
      </div>

      {/* Chart + sidebar */}
      <div className="flex flex-1 min-h-0 gap-1">
        {/* Chart */}
        <div className="flex-1 border border-zinc-800 bg-zinc-950 min-h-0 overflow-hidden">
          <CandleChart
            lastCandle={lastCandle}
            session={session}
            hiddenLevels={hiddenLevels}
            zones={zones}
            signals={signals}
            fills={fills}
            exits={exits}
            modelStatus={modelStatus}
            interval={interval}
          />
        </div>

        {/* Sidebar — level toggles */}
        {sidebarOpen && (
          <div className="w-[160px] border border-zinc-800 bg-zinc-900 overflow-y-auto flex-shrink-0">
            {/* Master toggle */}
            <div className="px-2 py-1 border-b border-zinc-800">
              <button
                onClick={toggleAll}
                className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider hover:text-zinc-300"
              >
                Toggle All
              </button>
            </div>

            {TOGGLE_SECTIONS.map(section => (
              <div key={section.label} className="border-b border-zinc-800">
                <div className="px-2 py-1 text-[9px] font-mono text-zinc-600 uppercase tracking-wider">
                  {section.label}
                </div>
                {section.groups.map(g => {
                  const hidden = isGroupHidden(g.key)
                  return (
                    <button
                      key={g.key}
                      onClick={() => toggleGroup(g.key)}
                      className={`flex items-center gap-1.5 w-full px-2 py-0.5 text-left text-[10px] font-mono hover:bg-zinc-800 transition-colors ${
                        hidden ? 'opacity-30 line-through' : ''
                      }`}
                    >
                      <span
                        className="w-2 h-2 flex-shrink-0"
                        style={{ backgroundColor: hidden ? '#3f3f46' : g.color }}
                      />
                      <span className={hidden ? 'text-zinc-600' : 'text-zinc-400'}>
                        {g.label}
                      </span>
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
