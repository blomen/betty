import { useState, useEffect } from 'react'
import { api } from '@/hooks/useApi'
import type { Position, Account, Fill, ExitEvent } from '@/types'

interface Props {
  positions: Position[]
  fills: Fill[]
  exits: ExitEvent[]
  lastPrice: number | null
}

const MAX_LOSS = 4500
const NQ_POINT_VALUE = 20

export function BankrollPage({ positions, fills, exits, lastPrice }: Props) {
  const [account, setAccount] = useState<Account | null>(null)

  useEffect(() => {
    const fetch = () => { api.getAccountInfo().then(setAccount).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 30_000)
    return () => clearInterval(iv)
  }, [])

  const closedPnL = computeClosedPnL(fills, exits)

  const unrealizedPnL = positions.reduce((sum, pos) => {
    if (!lastPrice) return sum
    const entryPrice = pos.price
    const side = typeof pos.side === 'number' ? (pos.side === 0 ? 'long' : 'short') : pos.side
    const pnlPoints = side === 'long' ? lastPrice - entryPrice : entryPrice - lastPrice
    return sum + pnlPoints * NQ_POINT_VALUE * pos.size
  }, 0)

  const totalPnL = closedPnL + unrealizedPnL
  const drawdownUsed = Math.max(0, -totalPnL)
  const drawdownPct = Math.min(drawdownUsed / MAX_LOSS * 100, 100)
  const drawdownColor = drawdownPct > 75 ? '#ef4444' : drawdownPct > 50 ? '#f59e0b' : '#4ade80'

  return (
    <div className="flex flex-col gap-3 overflow-y-auto flex-1">
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Balance" value={account?.balance != null ? `$${Number(account.balance).toLocaleString()}` : '—'} color="#ec4899" />
        <StatCard label="Buying Power" value={account?.buyingPower != null ? `$${Number(account.buyingPower).toLocaleString()}` : '—'} color="#8b5cf6" />
        <StatCard label="Account" value={account?.id ? `#${account.id}` : '—'} color="#3b82f6" sub="Practice" />
      </div>

      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <div className="flex justify-between items-center mb-2">
          <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">
            Drawdown vs Max Loss ($4,500)
          </span>
          <span className="text-sm font-mono font-bold" style={{ color: drawdownColor }}>
            ${drawdownUsed.toFixed(0)} / ${MAX_LOSS}
          </span>
        </div>
        <div className="h-3 bg-zinc-800">
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${drawdownPct}%`, backgroundColor: drawdownColor }}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-[10px] font-mono text-zinc-600">{drawdownPct.toFixed(1)}% used</span>
          <span className="text-[10px] font-mono text-zinc-600">${(MAX_LOSS - drawdownUsed).toFixed(0)} remaining</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Closed P&L" value={`$${closedPnL.toFixed(2)}`} color={closedPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Unrealized P&L" value={`$${unrealizedPnL.toFixed(2)}`} color={unrealizedPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Total P&L" value={`$${totalPnL.toFixed(2)}`} color={totalPnL >= 0 ? '#4ade80' : '#ef4444'} />
      </div>

      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px]">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Open Positions ({positions.length})
        </h3>
        <table className="sq w-full">
          <thead>
            <tr>
              <th>Side</th>
              <th>Contracts</th>
              <th>Entry</th>
              <th>Current</th>
              <th>Unreal. P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr><td colSpan={5} className="text-center text-zinc-600">Flat — no open positions</td></tr>
            ) : (
              positions.map((pos, i) => {
                const side = typeof pos.side === 'number' ? (pos.side === 0 ? 'Long' : 'Short') : pos.side
                const pnlPoints = lastPrice
                  ? (side === 'Long' ? lastPrice - pos.price : pos.price - lastPrice)
                  : 0
                const pnl = pnlPoints * NQ_POINT_VALUE * pos.size
                return (
                  <tr key={i}>
                    <td className={side === 'Long' ? 'text-emerald-400' : 'text-red-400'}>{side}</td>
                    <td>{pos.size}</td>
                    <td>{pos.price.toFixed(2)}</td>
                    <td>{lastPrice?.toFixed(2) ?? '—'}</td>
                    <td className={pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                      ${pnl.toFixed(2)}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatCard({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px] font-mono text-zinc-600 mt-0.5">{sub}</div>}
    </div>
  )
}

function computeClosedPnL(fills: Fill[], exits: ExitEvent[]): number {
  let pnl = 0
  const exitsCopy = [...exits]
  for (const fill of fills) {
    const exit = exitsCopy.shift()
    if (!exit) break
    const side = fill.side
    const pnlPoints = side === 'long' ? exit.price - fill.price : fill.price - exit.price
    pnl += pnlPoints * 20 * fill.size
  }
  return pnl
}
