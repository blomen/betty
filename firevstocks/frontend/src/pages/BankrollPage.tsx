import { useState, useEffect } from 'react'
import { api } from '@/hooks/useApi'
import type { Position, Account, ModelStatus } from '@/types'

interface Props {
  positions: Position[]
  lastPrice: number | null
}

const MAX_LOSS = 4500
const NQ_POINT_VALUE = 20

export function BankrollPage({ positions, lastPrice }: Props) {
  const [account, setAccount] = useState<Account | null>(null)
  const [model, setModel] = useState<ModelStatus | null>(null)

  useEffect(() => {
    const fetch = () => {
      api.getAccountInfo().then(setAccount).catch(() => {})
      api.getModelStatus().then(setModel).catch(() => {})
    }
    fetch()
    const iv = setInterval(fetch, 10_000)
    return () => clearInterval(iv)
  }, [])

  const sessionPnL = model?.session_pnl ?? 0

  const unrealizedPnL = positions.reduce((sum, pos) => {
    if (!lastPrice) return sum
    const side = typeof pos.side === 'number' ? (pos.side === 0 ? 'long' : 'short') : pos.side
    const pnlPoints = side === 'long' ? lastPrice - pos.price : pos.price - lastPrice
    return sum + pnlPoints * NQ_POINT_VALUE * pos.size
  }, 0)

  const totalPnL = sessionPnL + unrealizedPnL
  const drawdownUsed = Math.max(0, -totalPnL)
  const drawdownPct = Math.min(drawdownUsed / MAX_LOSS * 100, 100)
  const drawdownColor = drawdownPct > 75 ? '#ef4444' : drawdownPct > 50 ? '#f59e0b' : '#4ade80'

  const isLive = model?.relay_connected && model?.stream_running
  const isHalted = model?.halted

  return (
    <div className="flex flex-col gap-3 overflow-y-auto flex-1">
      {/* Model status banner */}
      <div className={`flex items-center gap-3 px-3 py-2 border ${
        isHalted ? 'border-red-800 bg-red-950' :
        isLive ? 'border-emerald-800 bg-emerald-950' :
        'border-zinc-800 bg-zinc-900'
      }`}>
        <span className={`text-xs font-mono font-bold uppercase tracking-wider ${
          isHalted ? 'text-red-400' : isLive ? 'text-emerald-400' : 'text-zinc-500'
        }`}>
          {isHalted ? `HALTED — ${model?.halt_reason}` : isLive ? 'MODEL LIVE' : 'OFFLINE'}
        </span>
        <div className="flex-1" />
        {model && (
          <div className="flex items-center gap-4 text-[10px] font-mono text-zinc-500">
            {model.is_flat === false && model.position_side && (
              <span className={model.position_side === 'long' ? 'text-emerald-400' : 'text-red-400'}>
                {model.position_side.toUpperCase()} {model.position_size ?? 1}ct
                {model.entry_price ? ` @ ${model.entry_price.toFixed(2)}` : ''}
              </span>
            )}
            {model.is_flat && (
              <span className="text-zinc-600">FLAT</span>
            )}
          </div>
        )}
      </div>

      {/* Account */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Balance" value={account?.balance != null ? `$${Number(account.balance).toLocaleString()}` : '—'} color="#ec4899" />
        <StatCard label="Buying Power" value={account?.buyingPower != null ? `$${Number(account.buyingPower).toLocaleString()}` : '—'} color="#8b5cf6" />
        <StatCard
          label="Account"
          value={account?.id ? `#${account.id}` : '—'}
          color="#3b82f6"
          sub={account?.canTrade === false ? 'Disabled' : 'Active'}
        />
      </div>

      {/* Drawdown tracker */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <div className="flex justify-between items-center mb-2">
          <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">
            Drawdown vs Max Loss (${MAX_LOSS.toLocaleString()})
          </span>
          <span className="text-sm font-mono font-bold" style={{ color: drawdownColor }}>
            ${drawdownUsed.toFixed(0)} / ${MAX_LOSS.toLocaleString()}
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

      {/* Session P&L */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Session P&L" value={`$${sessionPnL.toFixed(2)}`} color={sessionPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Unrealized" value={`$${unrealizedPnL.toFixed(2)}`} color={unrealizedPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Trailing DD" value={`$${(model?.trailing_dd ?? 0).toFixed(2)}`} color={(model?.trailing_dd ?? 0) > 200 ? '#ef4444' : '#4ade80'} />
      </div>

      {/* Open positions */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1">
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
              <th>Stop</th>
              <th>Unreal. P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr><td colSpan={6} className="text-center text-zinc-600">Flat</td></tr>
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
                    <td className="text-zinc-500">{model?.stop_price ? model.stop_price.toFixed(2) : '—'}</td>
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
