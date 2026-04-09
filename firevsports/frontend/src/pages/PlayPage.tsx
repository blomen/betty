import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'

interface PlayBet {
  id: string
  provider_id: string
  provider_name?: string
  event: string
  outcome: string
  odds: number
  edge_pct: number
  stake: number
  ev: number
  market?: string
  sport?: string
}

interface PlayBatch {
  bets: PlayBet[]
  total_ev?: number
}

function fmtOdds(o: number) {
  return o.toFixed(2)
}

function fmtEdge(e: number) {
  const sign = e >= 0 ? '+' : ''
  return `${sign}${e.toFixed(1)}%`
}

function fmtEv(e: number) {
  const sign = e >= 0 ? '+' : ''
  return `${sign}${e.toFixed(2)}`
}

export default function PlayPage() {
  const [data, setData] = useState<PlayBatch | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [navigating, setNavigating] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const result = await api.getPlayBatch()
      setData(result)
      setError(null)
    } catch (e: any) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10_000)
    return () => clearInterval(id)
  }, [load])

  const handleNavigate = async (bet: PlayBet) => {
    setNavigating(bet.id)
    try {
      await api.navigateBet({ bet_id: bet.id, provider_id: bet.provider_id, event: bet.event })
    } catch (e: any) {
      // ignore navigation errors silently — mirror may not be running
    } finally {
      setNavigating(null)
    }
  }

  const bets = (data?.bets ?? []).filter(b => b.edge_pct > 0)

  // Group by provider
  const grouped = bets.reduce<Record<string, PlayBet[]>>((acc, bet) => {
    const pid = bet.provider_id
    if (!acc[pid]) acc[pid] = []
    acc[pid].push(bet)
    return acc
  }, {})

  // Sort each group by edge desc
  for (const pid in grouped) {
    grouped[pid].sort((a, b) => b.edge_pct - a.edge_pct)
  }

  const providerIds = Object.keys(grouped).sort()
  const totalEv = data?.total_ev ?? bets.reduce((s, b) => s + (b.ev ?? 0), 0)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Summary header */}
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900 text-xs">
        <span className="text-zinc-400">
          <span className="text-zinc-200 font-mono">{bets.length}</span> bets
        </span>
        <span className="text-zinc-400">
          Total EV: <span className="text-green-400 font-mono">{fmtEv(totalEv)}</span>
        </span>
        {error && <span className="text-red-400 ml-auto">{error}</span>}
        {!error && !data && <span className="text-zinc-500 ml-auto">Loading...</span>}
      </div>

      <div className="flex-1 overflow-y-auto">
        {providerIds.length === 0 && data && (
          <div className="p-4 text-zinc-500 text-xs">No positive-edge bets available.</div>
        )}

        {providerIds.map(pid => {
          const provBets = grouped[pid]
          const provName = provBets[0]?.provider_name ?? pid
          return (
            <div key={pid} className="border-b border-zinc-800">
              {/* Provider header */}
              <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900 border-b border-zinc-800">
                <span className="text-xs font-semibold text-green-400 uppercase tracking-wide">{provName}</span>
                <span className="text-xs text-zinc-500">{provBets.length} bet{provBets.length !== 1 ? 's' : ''}</span>
              </div>

              {/* Bets table */}
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-zinc-500 border-b border-zinc-800">
                    <th className="text-left px-3 py-1 font-normal">Event</th>
                    <th className="text-left px-3 py-1 font-normal">Outcome</th>
                    <th className="text-right px-3 py-1 font-normal">Odds</th>
                    <th className="text-right px-3 py-1 font-normal">Edge</th>
                    <th className="text-right px-3 py-1 font-normal">Stake</th>
                    <th className="text-right px-3 py-1 font-normal">EV</th>
                  </tr>
                </thead>
                <tbody>
                  {provBets.map(bet => (
                    <tr
                      key={bet.id}
                      onClick={() => handleNavigate(bet)}
                      className={`border-b border-zinc-800/50 cursor-pointer hover:bg-zinc-800/60 transition-colors ${
                        navigating === bet.id ? 'opacity-50' : ''
                      }`}
                    >
                      <td className="px-3 py-1.5 text-zinc-200 max-w-[200px] truncate">{bet.event}</td>
                      <td className="px-3 py-1.5 text-zinc-300">{bet.outcome}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-zinc-200">{fmtOdds(bet.odds)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-green-400">{fmtEdge(bet.edge_pct)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-zinc-300">{bet.stake}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-green-400">{fmtEv(bet.ev)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        })}
      </div>
    </div>
  )
}
