import { useState, useEffect, useRef } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type Time,
} from 'lightweight-charts'
import { api } from '@/hooks/useStocksApi'
import type { BrokerTrade } from '@/types/stocks'

export function StatsPage() {
  const [trades, setTrades] = useState<BrokerTrade[]>([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)
  const [sortKey, setSortKey] = useState<'ts' | 'pnl_dollars' | 'pnl_r'>('ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  useEffect(() => {
    setLoading(true)
    api.getBrokerTrades(days)
      .then(d => setTrades((d.trades ?? []).filter(t => t.closed_at != null)))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [days])

  const stats = computeStats(trades)

  const sorted = [...trades].sort((a, b) => {
    let va: number, vb: number
    if (sortKey === 'ts') {
      va = new Date(a.ts).getTime(); vb = new Date(b.ts).getTime()
    } else {
      va = a[sortKey] ?? 0; vb = b[sortKey] ?? 0
    }
    return sortDir === 'asc' ? va - vb : vb - va
  })

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const arrow = (key: typeof sortKey) => sortKey === key ? (sortDir === 'asc' ? ' \u2191' : ' \u2193') : ''

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3 overflow-y-auto">
      {/* Period selector */}
      <div className="flex items-center gap-1">
        {[7, 30, 90].map(d => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`px-2 py-1 text-[10px] font-mono uppercase border ${
              days === d ? 'border-blue-500 text-blue-400 bg-blue-950' : 'border-zinc-800 text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {d}d
          </button>
        ))}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-6 gap-2">
        <SummaryCard label="Trades" value={String(stats.tradeCount)} color="#3b82f6" />
        <SummaryCard label="Win Rate" value={stats.tradeCount > 0 ? `${stats.winRate.toFixed(1)}%` : '—'} color={stats.winRate >= 50 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Profit Factor" value={stats.tradeCount > 0 ? stats.profitFactor === Infinity ? '—' : stats.profitFactor.toFixed(2) : '—'} color={stats.profitFactor >= 1 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Net P&L" value={`$${stats.totalPnL.toFixed(2)}`} color={stats.totalPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Avg Win" value={stats.avgWin > 0 ? `$${stats.avgWin.toFixed(2)}` : '—'} color="#4ade80" />
        <SummaryCard label="Avg Loss" value={stats.avgLoss > 0 ? `-$${stats.avgLoss.toFixed(2)}` : '—'} color="#ef4444" />
      </div>

      {/* Second row: R-based stats */}
      <div className="grid grid-cols-6 gap-2">
        <SummaryCard label="Total R" value={`${stats.totalR.toFixed(2)}R`} color={stats.totalR >= 0 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Avg R/Trade" value={stats.tradeCount > 0 ? `${stats.avgR.toFixed(2)}R` : '—'} color={stats.avgR >= 0 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Best Trade" value={stats.bestTrade != null ? `$${stats.bestTrade.toFixed(2)}` : '—'} color="#4ade80" />
        <SummaryCard label="Worst Trade" value={stats.worstTrade != null ? `$${stats.worstTrade.toFixed(2)}` : '—'} color="#ef4444" />
        <SummaryCard label="Avg Confidence" value={stats.avgConfidence > 0 ? `${(stats.avgConfidence * 100).toFixed(0)}%` : '—'} color="#8b5cf6" />
        <SummaryCard label="Long / Short" value={`${stats.longCount} / ${stats.shortCount}`} color="#f59e0b" />
      </div>

      {/* Equity curve */}
      <div className="border border-zinc-800 bg-zinc-950 h-[200px]">
        <EquityCurve trades={trades} />
      </div>

      {/* Trade history */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px]">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Trade History
        </h3>
        {loading ? (
          <div className="text-xs font-mono text-zinc-600 text-center py-8">Loading trades...</div>
        ) : (
          <div className="overflow-y-auto max-h-[400px]">
            <table className="sq w-full">
              <thead>
                <tr>
                  <th className="cursor-pointer" onClick={() => toggleSort('ts')}>Time{arrow('ts')}</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Stop</th>
                  <th className="cursor-pointer" onClick={() => toggleSort('pnl_dollars')}>P&L ${arrow('pnl_dollars')}</th>
                  <th className="cursor-pointer" onClick={() => toggleSort('pnl_r')}>P&L R{arrow('pnl_r')}</th>
                  <th>Signal</th>
                  <th>Conf</th>
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 ? (
                  <tr><td colSpan={9} className="text-center text-zinc-600">No trades yet</td></tr>
                ) : (
                  sorted.map(t => {
                    const pnl = t.pnl_dollars ?? 0
                    const r = t.pnl_r ?? 0
                    return (
                      <tr key={t.id}>
                        <td className="text-zinc-500">{new Date(t.ts).toLocaleString()}</td>
                        <td className={t.side === 'long' ? 'text-emerald-400' : 'text-red-400'}>
                          {t.side === 'long' ? 'Long' : 'Short'}
                        </td>
                        <td>{t.entry_price.toFixed(2)}</td>
                        <td>{t.exit_price?.toFixed(2) ?? '—'}</td>
                        <td className="text-zinc-500">{t.stop_price?.toFixed(2) ?? '—'}</td>
                        <td className={pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          ${pnl.toFixed(2)}
                        </td>
                        <td className={r >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {r.toFixed(2)}R
                        </td>
                        <td className="text-zinc-500 text-[10px]">
                          {t.signal_action?.replace('enter_', '') ?? '—'}
                        </td>
                        <td className="text-zinc-500 text-[10px]">
                          {t.signal_confidence ? `${(t.signal_confidence * 100).toFixed(0)}%` : '—'}
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function SummaryCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
    </div>
  )
}

function EquityCurve({ trades }: { trades: BrokerTrade[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || trades.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9AA0A6',
        fontSize: 10,
        fontFamily: 'monospace',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
      handleScroll: { vertTouchDrag: false },
    })

    const series = chart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
    })

    const sorted = [...trades].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
    const data: Array<{ time: Time; value: number }> = []
    let cumPnL = 0

    for (const t of sorted) {
      cumPnL += t.pnl_dollars ?? 0
      const ts = Math.floor(new Date(t.closed_at ?? t.ts).getTime() / 1000) as Time
      data.push({ time: ts, value: cumPnL })
    }

    if (data.length > 0) {
      series.setData(data)
      chart.timeScale().fitContent()
    }

    chartRef.current = chart

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [trades])

  if (trades.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center text-xs font-mono text-zinc-600">
        No trades for equity curve
      </div>
    )
  }

  return <div ref={containerRef} className="w-full h-full" />
}

function computeStats(trades: BrokerTrade[]) {
  if (trades.length === 0) {
    return { winRate: 0, profitFactor: 0, totalPnL: 0, tradeCount: 0, totalR: 0, avgR: 0, avgWin: 0, avgLoss: 0, bestTrade: null as number | null, worstTrade: null as number | null, avgConfidence: 0, longCount: 0, shortCount: 0 }
  }

  let wins = 0, losses = 0, grossProfit = 0, grossLoss = 0, totalPnL = 0, totalR = 0
  let bestTrade: number | null = null, worstTrade: number | null = null
  let confSum = 0, confCount = 0, longCount = 0, shortCount = 0

  for (const t of trades) {
    const pnl = t.pnl_dollars ?? 0
    totalPnL += pnl
    totalR += t.pnl_r ?? 0

    if (pnl >= 0) { wins++; grossProfit += pnl }
    else { losses++; grossLoss += Math.abs(pnl) }

    if (bestTrade === null || pnl > bestTrade) bestTrade = pnl
    if (worstTrade === null || pnl < worstTrade) worstTrade = pnl

    if (t.signal_confidence != null && t.signal_confidence > 0) {
      confSum += t.signal_confidence; confCount++
    }

    if (t.side === 'long') longCount++
    else shortCount++
  }

  const tradeCount = wins + losses
  const winRate = tradeCount > 0 ? (wins / tradeCount) * 100 : 0
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0
  const avgR = tradeCount > 0 ? totalR / tradeCount : 0
  const avgWin = wins > 0 ? grossProfit / wins : 0
  const avgLoss = losses > 0 ? grossLoss / losses : 0
  const avgConfidence = confCount > 0 ? confSum / confCount : 0

  return { winRate, profitFactor, totalPnL, tradeCount, totalR, avgR, avgWin, avgLoss, bestTrade, worstTrade, avgConfidence, longCount, shortCount }
}
