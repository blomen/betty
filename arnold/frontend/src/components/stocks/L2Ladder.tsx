import type { DepthSnapshot } from '@/types/stocks'

interface Props {
  depth: DepthSnapshot | null
  lastPrice: number | null
  autonomous?: boolean
}

export function L2Ladder({ depth, lastPrice, autonomous = false }: Props) {
  if (!depth || (!depth.bids.length && !depth.asks.length)) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">L2 Depth</div>
        {autonomous ? (
          <div className="text-zinc-400 leading-tight">
            Unavailable in autonomous mode — the server owns the TopstepX
            session, so GatewayDepth doesn't reach this client.
          </div>
        ) : (
          <div className="text-zinc-400">No depth feed</div>
        )}
      </div>
    )
  }

  const maxSize = Math.max(
    1,
    ...depth.bids.map(l => l.size),
    ...depth.asks.map(l => l.size),
  )
  const totalBid = depth.bids.reduce((a, l) => a + l.size, 0)
  const totalAsk = depth.asks.reduce((a, l) => a + l.size, 0)
  const imbalance = totalBid + totalAsk > 0 ? (totalBid - totalAsk) / (totalBid + totalAsk) : 0

  const renderRow = (l: { price: number; size: number }, side: 'bid' | 'ask') => {
    const pct = Math.round((l.size / maxSize) * 100)
    const barColor = side === 'bid' ? 'bg-emerald-900/60' : 'bg-red-900/60'
    const textColor = side === 'bid' ? 'text-emerald-400' : 'text-red-400'
    return (
      <div key={`${side}-${l.price}`} className="relative flex justify-between px-1 py-0.5">
        <span className={`absolute inset-y-0 ${side === 'bid' ? 'right-0' : 'left-0'} ${barColor}`} style={{ width: `${pct}%` }} />
        <span className={`relative ${textColor}`}>{l.size}</span>
        <span className="relative text-zinc-300">{l.price.toFixed(2)}</span>
      </div>
    )
  }

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">L2 Depth</span>
        <span className={imbalance >= 0 ? 'text-emerald-400' : 'text-red-400'}>
          {(imbalance * 100).toFixed(0)}%
        </span>
      </div>
      <div>
        {[...depth.asks].reverse().map(l => renderRow(l, 'ask'))}
        {lastPrice !== null && (
          <div className="border-y border-zinc-700 px-1 py-0.5 text-center text-zinc-200 bg-zinc-800/50">
            {lastPrice.toFixed(2)}
          </div>
        )}
        {depth.bids.map(l => renderRow(l, 'bid'))}
      </div>
    </div>
  )
}
