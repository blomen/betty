import { useState, useEffect, useRef } from 'react'
import {
  createChart,
  ColorType,
  type IChartApi,
  type Time,
} from 'lightweight-charts'
import { api } from '@/hooks/useApi'
import type { Trade } from '@/types'

export function StatsPage() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<'timestamp' | 'price'>('timestamp')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  useEffect(() => {
    setLoading(true)
    api.getTrades()
      .then(data => {
        const list = data.trades ?? (Array.isArray(data) ? data : [])
        setTrades(list)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const { winRate, profitFactor, totalPnL, tradeCount } = computeStats(trades)

  const sorted = [...trades].sort((a, b) => {
    const va = sortKey === 'timestamp' ? a.timestamp : a.price
    const vb = sortKey === 'timestamp' ? b.timestamp : b.price
    const cmp = va < vb ? -1 : va > vb ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3 overflow-y-auto">
      <div className="grid grid-cols-4 gap-2">
        <SummaryCard label="Trades" value={String(tradeCount)} color="#3b82f6" />
        <SummaryCard label="Win Rate" value={tradeCount > 0 ? `${winRate.toFixed(1)}%` : '—'} color={winRate >= 50 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Profit Factor" value={tradeCount > 0 ? profitFactor.toFixed(2) : '—'} color={profitFactor >= 1 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Net P&L" value={`$${totalPnL.toFixed(2)}`} color={totalPnL >= 0 ? '#4ade80' : '#ef4444'} />
      </div>

      <div className="border border-zinc-800 bg-zinc-950 h-[200px]">
        <EquityCurve trades={trades} />
      </div>

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
                  <th className="cursor-pointer" onClick={() => toggleSort('timestamp')}>
                    Time {sortKey === 'timestamp' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Side</th>
                  <th className="cursor-pointer" onClick={() => toggleSort('price')}>
                    Price {sortKey === 'price' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Size</th>
                  <th>Contract</th>
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 ? (
                  <tr><td colSpan={5} className="text-center text-zinc-600">No trades yet</td></tr>
                ) : (
                  sorted.map((trade, i) => (
                    <tr key={trade.id ?? i}>
                      <td className="text-zinc-500">
                        {new Date(trade.timestamp).toLocaleString()}
                      </td>
                      <td className={trade.side === 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {trade.side === 0 ? 'Buy' : 'Sell'}
                      </td>
                      <td>{trade.price.toFixed(2)}</td>
                      <td>{trade.size}</td>
                      <td className="text-zinc-500">{trade.contractId}</td>
                    </tr>
                  ))
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

function EquityCurve({ trades }: { trades: Trade[] }) {
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

    // v4 API: chart.addLineSeries() not chart.addSeries(LineSeries, ...)
    const series = chart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
    })

    const sorted = [...trades].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    const data: Array<{ time: Time; value: number }> = []
    let cumPnL = 0
    let pendingBuy: Trade | null = null

    for (const trade of sorted) {
      if (trade.side === 0) {
        pendingBuy = trade
      } else if (pendingBuy) {
        const pnl = (trade.price - pendingBuy.price) * 20 * trade.size
        cumPnL += pnl
        const ts = Math.floor(new Date(trade.timestamp).getTime() / 1000) as Time
        data.push({ time: ts, value: cumPnL })
        pendingBuy = null
      }
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

function computeStats(trades: Trade[]) {
  if (trades.length === 0) return { winRate: 0, profitFactor: 0, totalPnL: 0, tradeCount: 0 }

  const sorted = [...trades].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  let wins = 0
  let losses = 0
  let grossProfit = 0
  let grossLoss = 0
  let totalPnL = 0
  let pendingBuy: Trade | null = null

  for (const trade of sorted) {
    if (trade.side === 0) {
      pendingBuy = trade
    } else if (pendingBuy) {
      const pnl = (trade.price - pendingBuy.price) * 20 * trade.size
      totalPnL += pnl
      if (pnl >= 0) { wins++; grossProfit += pnl }
      else { losses++; grossLoss += Math.abs(pnl) }
      pendingBuy = null
    }
  }

  const tradeCount = wins + losses
  const winRate = tradeCount > 0 ? (wins / tradeCount) * 100 : 0
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0

  return { winRate, profitFactor, totalPnL, tradeCount }
}
