import { useState, useEffect, useCallback } from 'react'
import { api } from '@/hooks/useStocksApi'
import type { Position, Account, ModelStatus, Order, Quote } from '@/types/stocks'

interface Props {
  positions: Position[]
  lastPrice: number | null
  quote: Quote | null
}

const MAX_LOSS = 4500
const NQ_POINT_VALUE = 20

export function BankrollPage({ positions, lastPrice, quote }: Props) {
  const [account, setAccount] = useState<Account | null>(null)
  const [model, setModel] = useState<ModelStatus | null>(null)
  const [orders, setOrders] = useState<Order[]>([])
  const [flattenPending, setFlattenPending] = useState(false)

  useEffect(() => {
    const poll = () => {
      api.getAccountInfo().then(setAccount).catch(() => {})
      api.getModelStatus().then(setModel).catch(() => {})
      api.getOrders().then(d => setOrders(d.orders ?? [])).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 10_000)
    return () => clearInterval(iv)
  }, [])

  const handleFlatten = useCallback(async () => {
    if (!confirm('FLATTEN — Close all positions and cancel all orders?')) return
    setFlattenPending(true)
    try {
      await api.flatten()
      // Refresh immediately
      api.getModelStatus().then(setModel).catch(() => {})
      api.getOrders().then(d => setOrders(d.orders ?? [])).catch(() => {})
    } catch {}
    setFlattenPending(false)
  }, [])

  const handleCancelOrder = useCallback(async (orderId: number) => {
    try {
      await api.cancelOrder(orderId)
      api.getOrders().then(d => setOrders(d.orders ?? [])).catch(() => {})
    } catch {}
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
  const hasPosition = model?.is_flat === false

  return (
    <div className="flex flex-col gap-3 overflow-y-auto flex-1">
      {/* Status banner + flatten button */}
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
        {/* Quote display */}
        {quote && (
          <div className="flex items-center gap-2 text-[10px] font-mono">
            <span className="text-emerald-400">{quote.bid.toFixed(2)}</span>
            <span className="text-zinc-600">/</span>
            <span className="text-red-400">{quote.ask.toFixed(2)}</span>
            <span className="text-zinc-700">({((quote.ask - quote.bid) / 0.25).toFixed(0)}t)</span>
          </div>
        )}
        {model && (
          <div className="flex items-center gap-4 text-[10px] font-mono text-zinc-500">
            {hasPosition && model.position_side && (
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
        <button
          onClick={handleFlatten}
          disabled={flattenPending || (!hasPosition && orders.length === 0)}
          className={`px-3 py-1 text-[10px] font-mono font-bold uppercase border ${
            hasPosition
              ? 'border-red-600 text-red-400 bg-red-950 hover:bg-red-900'
              : 'border-zinc-700 text-zinc-600 cursor-not-allowed'
          }`}
        >
          {flattenPending ? 'FLATTENING...' : 'FLATTEN ALL'}
        </button>
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

      <div className="grid grid-cols-2 gap-2 flex-1 min-h-0">
        {/* Open positions */}
        <div className="border border-zinc-800 bg-zinc-900">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
            Open Positions ({positions.length})
          </h3>
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Side</th>
                <th>Ct</th>
                <th>Entry</th>
                <th>Current</th>
                <th>Stop</th>
                <th>P&L</th>
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

        {/* Open orders */}
        <div className="border border-zinc-800 bg-zinc-900">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
            Open Orders ({orders.length})
          </h3>
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Type</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orders.length === 0 ? (
                <tr><td colSpan={5} className="text-center text-zinc-600">No open orders</td></tr>
              ) : (
                orders.map((o, i) => {
                  const oid = o.orderId ?? o.id ?? 0
                  const action = o.action ?? '?'
                  const isBuy = action === 'Buy' || action === 'buy'
                  const price = o.stopPrice ?? o.limitPrice ?? o.price ?? 0
                  return (
                    <tr key={oid || i}>
                      <td className="text-zinc-400">{o.type ?? 'Stop'}</td>
                      <td className={isBuy ? 'text-emerald-400' : 'text-red-400'}>{action}</td>
                      <td>{o.size ?? 1}</td>
                      <td>{typeof price === 'number' ? price.toFixed(2) : '—'}</td>
                      <td>
                        {oid > 0 && (
                          <button
                            onClick={() => handleCancelOrder(oid)}
                            className="text-[9px] font-mono text-red-500 hover:text-red-300 px-1"
                          >
                            CANCEL
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
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
