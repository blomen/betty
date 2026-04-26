import { useMemo, useState } from 'react'
import type { Zone } from '@/types/stocks'

interface Props {
  zones: Zone[]
  lastPrice: number | null
}

type SortKey = 'distance' | 'hierarchy' | 'members'

export function ZonesTable({ zones, lastPrice }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('distance')

  const rows = useMemo(() => {
    const enriched = zones.map(z => ({
      ...z,
      distance: lastPrice !== null ? Math.abs(z.price - lastPrice) : 0,
    }))
    enriched.sort((a, b) => {
      if (sortKey === 'distance') return a.distance - b.distance
      if (sortKey === 'hierarchy') return (b.hierarchy ?? 0) - (a.hierarchy ?? 0)
      return b.members - a.members
    })
    return enriched.slice(0, 20)
  }, [zones, lastPrice, sortKey])

  const ping = async (zoneKey: string) => {
    await fetch(`/stocks/api/tv-overlay/ping-zone/${encodeURIComponent(zoneKey)}`, { method: 'POST' }).catch(() => {})
  }

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Active Zones ({zones.length})</span>
        <div className="flex gap-1">
          {(['distance', 'hierarchy', 'members'] as SortKey[]).map(k => (
            <button
              key={k}
              onClick={() => setSortKey(k)}
              className={`px-2 py-0.5 text-[10px] uppercase tracking-wider rounded ${
                sortKey === k ? 'bg-zinc-700 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </div>
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-zinc-500 text-left">
            <th className="font-normal">Price</th>
            <th className="font-normal">Range</th>
            <th className="font-normal text-right">Strength</th>
            <th className="font-normal text-right">Members</th>
            <th className="font-normal text-right">Δ</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((z, i) => {
            const id = `${z.price}:${z.members}`
            const strength = z.hierarchy ?? 0
            const bar = Math.round(strength * 100)
            return (
              <tr key={i} className="text-zinc-300 hover:bg-zinc-800/50">
                <td className="py-0.5">{z.price.toFixed(2)}</td>
                <td className="py-0.5 text-zinc-500">
                  {z.lower !== undefined && z.upper !== undefined
                    ? `${z.lower.toFixed(2)}–${z.upper.toFixed(2)}`
                    : '—'}
                </td>
                <td className="py-0.5 text-right">
                  <span className="inline-block w-12 bg-zinc-800 rounded-sm overflow-hidden align-middle">
                    <span className="block h-1.5 bg-orange-400" style={{ width: `${bar}%` }} />
                  </span>
                </td>
                <td className="py-0.5 text-right">{z.members}</td>
                <td className="py-0.5 text-right text-zinc-500">
                  {lastPrice !== null ? (z.price - lastPrice).toFixed(1) : '—'}
                </td>
                <td className="py-0.5 text-right">
                  <button
                    onClick={() => ping(id)}
                    className="px-1.5 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-400 rounded"
                  >
                    Ping
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
