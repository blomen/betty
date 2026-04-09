import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'

interface BatchBet {
  rank: number
  tier: string
  provider_id: string
  event_id: string
  market: string
  outcome: string
  point: number | null
  odds: number
  fair_odds: number
  edge_pct: number
  stake: number
  expected_profit: number
  display_home: string
  display_away: string
  sport: string
  league?: string
  start_time?: string
  odds_age_minutes?: number
  cluster?: string
  funded?: boolean
  skip_reason?: string
}

export default function PlayPage() {
  const [batch, setBatch] = useState<BatchBet[]>([])
  const [summary, setSummary] = useState<any>(null)
  const [providerBalances, setProviderBalances] = useState<Record<string, number>>({})
  const [error, setError] = useState<string | null>(null)
  const [navigating, setNavigating] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const result = await api.getPlayBatch()
      setBatch(result.batch ?? [])
      setSummary(result.summary ?? null)
      setProviderBalances(result.provider_balances ?? {})
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

  const handleNavigate = async (b: BatchBet) => {
    const key = `${b.event_id}:${b.market}:${b.outcome}`
    setNavigating(key)
    try {
      await api.navigateBet({
        provider_id: b.provider_id,
        event_id: b.event_id,
        market: b.market,
        outcome: b.outcome,
        point: b.point,
        odds: b.odds,
        fair_odds: b.fair_odds,
        stake: b.stake,
        display_home: b.display_home,
        display_away: b.display_away,
      })
    } catch { /* mirror may not be running */ }
    finally { setNavigating(null) }
  }

  const balances = providerBalances
  const bets = batch.filter(b => b.edge_pct > 0)

  // Group by cluster, then by provider within cluster
  const byCluster: Record<string, Record<string, BatchBet[]>> = {}
  for (const b of bets) {
    const cluster = b.cluster || b.provider_id
    if (!byCluster[cluster]) byCluster[cluster] = {}
    if (!byCluster[cluster][b.provider_id]) byCluster[cluster][b.provider_id] = []
    byCluster[cluster][b.provider_id].push(b)
  }
  // Sort bets within each provider by edge desc
  for (const cluster of Object.values(byCluster))
    for (const pid in cluster) cluster[pid].sort((a, b) => b.edge_pct - a.edge_pct)

  // Sort clusters by total EV desc
  const clusterIds = Object.keys(byCluster).sort((a, b) => {
    const evA = Object.values(byCluster[a]).flat().reduce((s, x) => s + x.expected_profit, 0)
    const evB = Object.values(byCluster[b]).flat().reduce((s, x) => s + x.expected_profit, 0)
    return evB - evA
  })
  const totalEv = summary?.total_expected_profit ?? bets.reduce((s, b) => s + b.expected_profit, 0)

  const fmtStake = (b: BatchBet) => b.tier === 'polymarket' ? `$${b.stake.toFixed(1)}` : `${Math.round(b.stake)} kr`
  const fmtEv = (b: BatchBet) => b.tier === 'polymarket' ? `+$${b.expected_profit.toFixed(2)}` : `+${b.expected_profit.toFixed(0)} kr`
  const fmtBal = (pid: string, tier: string) => {
    const bal = balances[pid]
    if (bal == null) return ''
    return tier === 'polymarket' ? `$${bal.toFixed(1)}` : `${Math.round(bal)} kr`
  }

  // Resolve outcome to readable team/label
  const resolveOutcome = (b: BatchBet) => {
    if (b.outcome === 'home') return b.display_home || 'Home'
    if (b.outcome === 'away') return b.display_away || 'Away'
    if (b.outcome === 'draw') return 'Draw'
    if (b.outcome === 'over' && b.point != null) return `Over ${b.point}`
    if (b.outcome === 'under' && b.point != null) return `Under ${b.point}`
    if (b.point != null) return `${b.outcome} ${b.point}`
    return b.outcome
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900 text-xs">
        <span className="text-zinc-200 font-mono">{bets.length}</span>
        <span className="text-zinc-400">bets</span>
        <span className="text-green-400 font-mono">+{totalEv.toFixed(0)} kr EV</span>
        {error && <span className="text-red-400 ml-auto">{error}</span>}
        {!error && batch.length === 0 && <span className="text-zinc-500 ml-auto">Loading...</span>}
      </div>

      <div className="flex-1 overflow-y-auto">
        {clusterIds.length === 0 && batch.length > 0 && (
          <div className="p-4 text-zinc-500 text-xs">No positive-edge bets available.</div>
        )}

        {clusterIds.map(clusterId => {
          const clusterProviders = byCluster[clusterId]
          const allClusterBets = Object.values(clusterProviders).flat()
          const clusterEv = allClusterBets.reduce((s, b) => s + b.expected_profit, 0)
          const providerIds = Object.keys(clusterProviders).sort((a, b) => (balances[b] ?? 0) - (balances[a] ?? 0))

          return (
            <div key={clusterId}>
              {/* Cluster header */}
              <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-zinc-800">
                <span className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider">{clusterId}</span>
                <span className="text-[10px] text-zinc-600">{allClusterBets.length} bets · {providerIds.length} providers</span>
                <span className="text-[10px] text-green-400 ml-auto">+{clusterEv.toFixed(0)} kr EV</span>
              </div>

              {providerIds.map(pid => {
                const provBets = clusterProviders[pid]
                const provEv = provBets.reduce((s, b) => s + b.expected_profit, 0)
                const bal = fmtBal(pid, provBets[0]?.tier || 'soft')
                return (
                  <div key={pid} className="border-b border-zinc-800">
                    <div className="flex items-center gap-2 px-3 pl-6 py-1 bg-zinc-900/50 border-b border-zinc-800">
                      <span className="text-xs font-semibold text-zinc-300 uppercase">{pid}</span>
                      <span className="text-xs text-zinc-500">{provBets.length} bets</span>
                      {bal && <span className="text-xs text-success">bal {bal}</span>}
                      <span className="text-xs text-green-400 ml-auto">+{provEv.toFixed(0)} kr</span>
                    </div>

                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-zinc-500 border-b border-zinc-800">
                          <th className="text-left px-3 py-1 font-normal">Event</th>
                          <th className="text-left px-3 py-1 font-normal">Bet On</th>
                          <th className="text-right px-3 py-1 font-normal">Odds</th>
                          <th className="text-right px-3 py-1 font-normal">Fair</th>
                          <th className="text-right px-3 py-1 font-normal">Edge</th>
                          <th className="text-right px-3 py-1 font-normal">Stake</th>
                          <th className="text-right px-3 py-1 font-normal">EV</th>
                        </tr>
                      </thead>
                      <tbody>
                        {provBets.map(b => {
                          const key = `${b.event_id}:${b.market}:${b.outcome}:${b.provider_id}`
                          return (
                            <tr
                              key={key}
                              onClick={() => handleNavigate(b)}
                              className={`border-b border-zinc-800/50 cursor-pointer hover:bg-zinc-800/60 transition-colors ${
                                navigating === key ? 'opacity-50' : ''
                              }`}
                            >
                              <td className="px-3 py-1.5 text-zinc-200 max-w-[200px] truncate">
                                {b.display_home} v {b.display_away}
                              </td>
                              <td className="px-3 py-1.5 text-amber-400 font-medium">{resolveOutcome(b)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-zinc-200">{b.odds.toFixed(2)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-zinc-500">{b.fair_odds.toFixed(2)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-green-400">+{b.edge_pct.toFixed(1)}%</td>
                              <td className="px-3 py-1.5 text-right font-mono text-zinc-300">{fmtStake(b)}</td>
                              <td className="px-3 py-1.5 text-right font-mono text-green-400">{fmtEv(b)}</td>
                            </tr>
                    )
                  })}
                </tbody>
              </table>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
