import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'
import { useMirrorStream } from '../hooks/useMirrorStream'

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

interface SettleToast {
  id: string
  bet_id: number
  provider_id: string
  event_label: string
  outcome: string
  market: string
  odds: number
  stake: number
  result: string
  payout: number
  profit: number
}

export default function PlayPage() {
  const [batch, setBatch] = useState<BatchBet[]>([])
  const [summary, setSummary] = useState<any>(null)
  const [providerBalances, setProviderBalances] = useState<Record<string, number>>({})
  const [pendingByProvider, setPendingByProvider] = useState<Record<string, any[]>>({})
  const [placedToday, setPlacedToday] = useState<Record<string, number>>({})
  const [ttkFilter, setTtkFilter] = useState<number>(24)
  const [error, setError] = useState<string | null>(null)
  const mirror = useMirrorStream()
  const [loopRunning, setLoopRunning] = useState(false)
  const [currentBetReady, setCurrentBetReady] = useState<any>(null)
  const [toasts, setToasts] = useState<SettleToast[]>([])
  const [confirmedSettlements, setConfirmedSettlements] = useState<any[]>([])
  const [settleWaiting, setSettleWaiting] = useState(false)
  const [activeCluster, setActiveCluster] = useState<string | null>(null)
  const [activeSkin, setActiveSkin] = useState<string | null>(null)
  const [loopStatus, setLoopStatus] = useState<string | null>(null)
  const [placementToast, setPlacementToast] = useState<{ bet: any; count: number; cap: number } | null>(null)

  const startSkin = async (pid: string, clusterId: string) => {
    if (loopRunning) {
      api.stopPlayLoop()
      setLoopRunning(false)
      setCurrentBetReady(null)
      setToasts([])
      setConfirmedSettlements([])
      setSettleWaiting(false)
      setLoopStatus(null)
    }
    if (activeCluster === clusterId && activeSkin === pid) {
      setActiveCluster(null)
      setActiveSkin(null)
      return
    }
    setActiveCluster(clusterId)
    setActiveSkin(pid)
    // Ensure mirror browser is running, then open provider tab
    try { await api.startMirror() } catch { /* */ }
    try { await api.openTab(pid) } catch { /* */ }
    const clusterBets = bets.filter(b => (b.cluster || b.provider_id) === clusterId)
    setLoopRunning(true)
    await api.startPlayLoop(clusterBets, providerBalances, pid)
  }

  const load = useCallback(async () => {
    try {
      const [result, pendingResult] = await Promise.all([
        api.getPlayBatch(),
        api.getPendingBets().catch(() => ({ providers: [] })),
      ])
      setBatch(result.batch ?? [])
      setSummary(result.summary ?? null)
      setProviderBalances(result.provider_balances ?? {})
      setPlacedToday(result.placed_today ?? {})
      const grouped: Record<string, any[]> = {}
      for (const p of pendingResult.providers ?? [])
        if (p.bets?.length) grouped[p.provider_id] = p.bets
      setPendingByProvider(grouped)
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

  // SSE event handler
  useEffect(() => {
    if (!mirror.lastEvent) return
    const { type, data } = mirror.lastEvent
    if (type === 'login_waiting') setLoopStatus(`Waiting for login on ${data.provider_id}... (${Math.round(data.elapsed)}/${data.timeout}s)`)
    if (type === 'login_detected') setLoopStatus(`Logged in to ${data.provider_id}`)
    if (type === 'settling_pending') setLoopStatus(`Scanning pending on ${data.provider_id}...`)
    if (type === 'settling_done') {
      if (data.settlements?.length > 0) {
        const newToasts: SettleToast[] = data.settlements.map((s: any) => {
          const bet = data.pending_bets?.find((b: any) => b.bet_id === s.bet_id || b.id === s.bet_id)
          const payout = s.payout ?? 0
          const stake = bet?.stake ?? 0
          const eventLabel = bet
            ? (bet.home_team && bet.away_team ? `${bet.home_team} v ${bet.away_team}` : `Bet #${s.bet_id}`)
            : `Bet #${s.bet_id}`
          return {
            id: `settle-${s.bet_id}-${Date.now()}`,
            bet_id: s.bet_id,
            provider_id: data.provider_id,
            event_label: eventLabel,
            outcome: bet?.outcome ?? '',
            market: bet?.market ?? '',
            odds: bet?.odds ?? 0,
            stake,
            result: s.result,
            payout,
            profit: payout - stake,
          }
        })
        setToasts(newToasts)
        setConfirmedSettlements([])
        setSettleWaiting(true)
        setLoopStatus(null)
      } else {
        setLoopStatus(data.pending_count > 0 ? `${data.pending_count} pending — all open` : null)
      }
    }
    if (type === 'settlements_confirmed') { setToasts([]); setSettleWaiting(false); setLoopStatus(null); load() }
    if (type === 'provider_skipped') setLoopStatus(`Skipped ${data.provider_id}: ${data.reason}`)
    if (type === 'bet_ready') {
      const bet = data.bet ?? data
      setCurrentBetReady({ ...bet, prep_ok: data.prep_ok, live_odds: data.live_odds, live_edge: data.live_edge, prep_reason: data.prep_reason })
      setLoopStatus(null)
    }
    if (type === 'bet_placed') {
      setCurrentBetReady(null)
      const bet = data.bet
      const pid = bet?.provider_id || activeSkin || ''
      const currentPending = (pendingByProvider[pid] ?? []).length + 1
      const placedCount = data.placed_today ?? 1
      setPlacementToast({ bet, count: currentPending, cap: 10 })
      setTimeout(() => setPlacementToast(null), 4000)
      if (bet?.provider_id) {
        setPlacedToday(prev => ({ ...prev, [bet.provider_id]: placedCount }))
      }
      // Optimistic: remove from batch, add to pending
      if (bet?.event_id) {
        setBatch(prev => prev.filter(b =>
          !(b.event_id === bet.event_id && b.market === bet.market && b.outcome === bet.outcome)
        ))
        const pid = bet.provider_id || activeSkin || ''
        setPendingByProvider(prev => ({
          ...prev,
          [pid]: [...(prev[pid] ?? []), {
            id: Date.now(),
            event_id: bet.event_id,
            provider_id: pid,
            market: bet.market,
            outcome: bet.outcome,
            point: bet.point,
            odds: data.actual_odds ?? bet.odds,
            stake: data.actual_stake ?? bet.stake,
            home_team: bet.display_home,
            away_team: bet.display_away,
          }],
        }))
      }
      setTimeout(load, 3000) // Delayed refresh — let server record the bet first
    }
    if (type === 'bet_skipped' || type === 'bet_failed') { setCurrentBetReady(null); setLoopStatus(null) }
    if (type === 'provider_complete') setLoopStatus(`${data.provider_id} done — next skin`)
    if (type === 'play_complete' || type === 'play_stopped') {
      setLoopRunning(false)
      setCurrentBetReady(null)
      setActiveCluster(null)
      setActiveSkin(null)
      setToasts([])
      setSettleWaiting(false)
      setLoopStatus(null)
    }
  }, [mirror.lastEvent])

  const handlePlace = () => api.placeCurrent()
  const handleSkip = () => api.skipCurrent()

  const handleToastConfirm = (toast: SettleToast) => {
    setConfirmedSettlements(prev => [...prev, { bet_id: toast.bet_id, result: toast.result, payout: toast.payout }])
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }
  const handleToastReject = (toast: SettleToast) => {
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }

  useEffect(() => {
    if (!settleWaiting) return
    if (toasts.length > 0) return
    api.confirmSettlements(confirmedSettlements.length > 0 ? confirmedSettlements : [])
    setSettleWaiting(false)
    setConfirmedSettlements([])
  }, [toasts, settleWaiting, confirmedSettlements])

  const getTtkHours = (b: BatchBet) => {
    if (!b.start_time) return null
    const diff = new Date(b.start_time).getTime() - Date.now()
    return diff > 0 ? diff / 3600000 : 0
  }
  const fmtTtk = (b: BatchBet) => {
    const h = getTtkHours(b)
    if (h == null) return '—'
    if (h < 1) return `${Math.round(h * 60)}m`
    if (h < 48) return `${Math.round(h)}h`
    return `${Math.round(h / 24)}d`
  }

  const bets = batch.filter(b => {
    if (b.edge_pct <= 0) return false
    const h = getTtkHours(b)
    if (h != null && h > ttkFilter) return false
    return true
  })

  // Static cluster membership — mirrors PLATFORM_GROUPS from constants.py
  const CLUSTER_MEMBERS: Record<string, string[]> = {
    kambi: ['unibet', 'leovegas', 'expekt', 'betmgm', 'speedybet', 'x3000', 'goldenbull', '1x2'],
    spectate: ['888sport', 'mrgreen'],
    altenar_main: ['betinia', 'campobet', 'lodur', 'quickcasino', 'swiper', 'dbet'],
    gecko_betsson: ['betsson', 'nordicbet', 'betsafe', 'spelklubben'],
    comeon_group: ['comeon', 'lyllo', 'hajper', 'snabbare'],
  }
  const providerToCluster: Record<string, string> = {}
  for (const [cluster, members] of Object.entries(CLUSTER_MEMBERS)) {
    for (const pid of members) providerToCluster[pid] = cluster
  }
  // Override with actual batch data (in case cluster names differ)
  for (const b of batch) providerToCluster[b.provider_id] = b.cluster || b.provider_id

  // Group by cluster — flat list, no provider sub-grouping
  const byCluster: Record<string, BatchBet[]> = {}
  for (const b of bets) {
    const cluster = b.cluster || b.provider_id
    if (!byCluster[cluster]) byCluster[cluster] = []
    byCluster[cluster].push(b)
  }
  // Ensure clusters exist for ALL providers with balance or pending (even if no bets)
  for (const pid of Object.keys(providerBalances)) {
    const cluster = providerToCluster[pid] || pid
    if (!byCluster[cluster]) byCluster[cluster] = []
  }
  for (const pid of Object.keys(pendingByProvider)) {
    const cluster = providerToCluster[pid] || pid
    if (!byCluster[cluster]) byCluster[cluster] = []
  }
  for (const cluster in byCluster) byCluster[cluster].sort((a, b) => b.edge_pct - a.edge_pct)

  // Cluster-level stats
  const clusterStats = (clusterId: string) => {
    const cb = byCluster[clusterId] || []
    const providers = new Set(cb.map(b => b.provider_id))
    // Add ALL members from static cluster mapping
    if (CLUSTER_MEMBERS[clusterId]) {
      for (const pid of CLUSTER_MEMBERS[clusterId]) providers.add(pid)
    }
    // Add providers with pending/balance in this cluster
    for (const pid of Object.keys(pendingByProvider)) {
      if ((providerToCluster[pid] || pid) === clusterId) providers.add(pid)
    }
    for (const pid of Object.keys(providerBalances)) {
      if ((providerToCluster[pid] || pid) === clusterId) providers.add(pid)
    }
    const totalBal = [...providers].reduce((s, p) => s + (providerBalances[p] ?? 0), 0)
    const ev = cb.reduce((s, b) => s + b.expected_profit, 0)
    const pending = [...providers].reduce((s, p) => s + (pendingByProvider[p]?.length ?? 0), 0)
    const placed = [...providers].reduce((s, p) => s + (placedToday[p] ?? 0), 0)
    return { providers: [...providers], ev, totalBal, pending, placed, betCount: cb.length }
  }

  const clusterIds = Object.keys(byCluster).sort((a, b) => {
    const evA = (byCluster[a] || []).reduce((s, x) => s + x.expected_profit, 0)
    const evB = (byCluster[b] || []).reduce((s, x) => s + x.expected_profit, 0)
    return evB - evA
  })
  const totalEv = summary?.total_expected_profit ?? bets.reduce((s, b) => s + b.expected_profit, 0)
  const totalPending = Object.values(pendingByProvider).reduce((s, arr) => s + arr.length, 0)

  const fmtStake = (b: BatchBet) => b.tier === 'polymarket' ? `$${b.stake.toFixed(1)}` : `${Math.round(b.stake)} kr`
  const fmtEv = (b: BatchBet) => b.tier === 'polymarket' ? `+$${b.expected_profit.toFixed(2)}` : `+${b.expected_profit.toFixed(0)} kr`

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
      {/* Header */}
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900 text-xs">
        <span className="text-zinc-200 font-mono">{bets.length}</span>
        <span className="text-zinc-400">bets</span>
        {totalPending > 0 && <>
          <span className="text-amber-400 font-mono">{totalPending}</span>
          <span className="text-zinc-500">pending</span>
        </>}
        <span className="text-green-400 font-mono">+{totalEv.toFixed(0)} kr EV</span>
        <div className="flex items-center gap-0.5 ml-2">
          {([12, 24, 48, 168] as const).map(h => (
            <button key={h} onClick={() => setTtkFilter(h)}
              className={`px-1.5 py-0.5 text-[10px] font-mono rounded ${
                ttkFilter === h ? 'bg-zinc-700 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
              }`}>
              {h <= 48 ? `${h}h` : '1W'}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {mirror.connected && <span className="w-1.5 h-1.5 rounded-full bg-green-500" />}
          {loopStatus && <span className="text-amber-400">{loopStatus}</span>}
        </div>
        {error && <span className="text-red-400">{error}</span>}
        {!error && batch.length === 0 && <span className="text-zinc-500">Loading...</span>}
      </div>

      {/* Bet ready bar — placement auto-detected via interceptor */}
      {currentBetReady && (
        <div className="flex items-center gap-3 px-3 py-2 border-b bg-amber-900/20 border-amber-700/50">
          <span className="text-xs text-zinc-500 uppercase">{currentBetReady.provider_id}</span>
          <span className="text-xs text-zinc-200 font-medium truncate">
            {currentBetReady.display_home} v {currentBetReady.display_away}
          </span>
          <span className="text-xs text-amber-400 font-medium">{resolveOutcome(currentBetReady)}</span>
          <span className="text-xs font-mono text-zinc-200">
            @ {(currentBetReady.live_odds ?? currentBetReady.odds)?.toFixed(2)}
          </span>
          <span className="text-xs text-green-400">+{(currentBetReady.live_edge ?? currentBetReady.edge_pct)?.toFixed(1)}%</span>
          <span className="text-xs font-mono text-zinc-400">{Math.round(currentBetReady.stake ?? 0)} kr</span>
          <div className="ml-auto flex gap-2">
            <button onClick={handleSkip}
              className="px-3 py-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded">
              Skip
            </button>
          </div>
        </div>
      )}

      {/* Placement toast */}
      {placementToast && (
        <div className="flex items-center gap-3 px-3 py-2 border-b bg-green-900/30 border-green-700/50 animate-pulse">
          <span className="text-xs text-green-400 font-semibold">{placementToast.count}/{placementToast.cap}</span>
          <span className="text-xs text-zinc-200 truncate">
            {placementToast.bet?.display_home} v {placementToast.bet?.display_away}
          </span>
          <span className="text-xs text-green-400">{placementToast.bet?.outcome}</span>
          <span className="text-xs font-mono text-zinc-300">@ {placementToast.bet?.odds?.toFixed(2)}</span>
          <span className="text-xs font-mono text-zinc-400">{Math.round(placementToast.bet?.stake ?? 0)} kr</span>
        </div>
      )}

      {/* Main content — flat list per cluster */}
      <div className="flex-1 overflow-y-auto">
        {clusterIds.length === 0 && batch.length > 0 && (
          <div className="p-4 text-zinc-500 text-xs">No positive-edge bets available.</div>
        )}

        {clusterIds.map(clusterId => {
          const cb = byCluster[clusterId] || []
          const stats = clusterStats(clusterId)
          const isActive = activeCluster === clusterId

          return (
            <div key={clusterId}>
              {/* Cluster header with skin tabs */}
              <div className={`flex items-center gap-2 px-3 py-1.5 border-b ${
                isActive ? 'bg-amber-900/20 border-amber-700/50' : 'bg-zinc-900/50 border-zinc-800'
              }`}>
                <span className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider">{clusterId}</span>
                {/* Skin tabs — sorted by balance desc */}
                <div className="flex items-center gap-1">
                  {stats.providers.sort((a, b) => (providerBalances[b] ?? 0) - (providerBalances[a] ?? 0)).map(pid => {
                    const bal = providerBalances[pid] ?? 0
                    const placed = placedToday[pid] ?? 0
                    const pending = pendingByProvider[pid]?.length ?? 0
                    const isSkinActive = activeSkin === pid
                    const uncapped = ['pinnacle', 'polymarket', 'cloudbet'].includes(pid)
                    const atCap = !uncapped && placed >= 10
                    const disabled = bal <= 0 && pending === 0
                    return (
                      <button key={pid}
                        onClick={() => !disabled && startSkin(pid, clusterId)}
                        disabled={disabled}
                        className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                          disabled
                            ? 'text-zinc-700 border border-zinc-800/30 cursor-not-allowed opacity-40'
                            : isSkinActive
                              ? 'bg-amber-700/50 text-amber-300 border border-amber-600/50'
                              : 'text-zinc-300 hover:bg-zinc-700/50 border border-zinc-700/50 cursor-pointer'
                        }`}
                      >
                        <span className="uppercase font-semibold">{pid}</span>
                        {bal > 0 && <span className="ml-1 text-zinc-500">{Math.round(bal)}</span>}
                        {!uncapped && <span className={`ml-1 ${atCap ? 'text-red-400' : placed > 0 ? 'text-amber-400' : 'text-zinc-600'}`}>{placed}/10</span>}
                        {pending > 0 && <span className="ml-1 text-amber-400">{pending}p</span>}
                      </button>
                    )
                  })}
                </div>
                <span className="text-[10px] text-zinc-500 ml-auto">{stats.betCount} bets</span>
                <span className="text-[10px] text-green-400">+{stats.ev.toFixed(0)} kr</span>
              </div>

              {/* Pending bets for this cluster */}
              {(() => {
                const clusterPending = stats.providers.flatMap(pid =>
                  (pendingByProvider[pid] ?? []).map((p: any) => ({ ...p, _pid: pid }))
                )
                if (clusterPending.length === 0) return null
                return (
                  <div className="border-b border-zinc-800 bg-amber-900/5">
                    <div className="flex items-center gap-2 px-3 py-0.5 border-b border-zinc-800/30">
                      <span className="text-[10px] text-amber-500 uppercase font-medium">Pending</span>
                      <span className="text-[10px] text-zinc-600">{clusterPending.length} bets · {Math.round(clusterPending.reduce((s: number, p: any) => s + (p.stake ?? 0), 0))} kr</span>
                    </div>
                    {clusterPending.map((p: any) => {
                      const eventLabel = p.home_team && p.away_team
                        ? `${p.home_team} v ${p.away_team}`
                        : p.event_id?.split(':').slice(1, 3).join(' v ') ?? p.event_id
                      return (
                        <div key={`pending-${p.id}`} className="flex items-center gap-2 px-3 pl-6 py-0.5 border-b border-zinc-800/20 text-xs">
                          <span className="text-[10px] text-zinc-600 uppercase w-[80px]">{p._pid}</span>
                          <span className="text-amber-300/70 truncate flex-1">{eventLabel}</span>
                          <span className="text-amber-400/60 text-[10px]">{p.outcome ?? p.market}</span>
                          <span className="text-zinc-500 font-mono text-[10px]">@ {(p.odds ?? 0).toFixed(2)}</span>
                          <span className="text-amber-300/50 font-mono text-[10px]">{Math.round(p.stake ?? 0)} kr</span>
                        </div>
                      )
                    })}
                  </div>
                )
              })()}

              {/* Bet rows */}
              <table className="w-full text-xs">
                <tbody>
                  {cb.map(b => {
                    const key = `${b.event_id}:${b.market}:${b.outcome}:${b.provider_id}`
                    const isCurrent = currentBetReady?.event_id === b.event_id && currentBetReady?.outcome === b.outcome
                    return (
                      <tr key={key}
                        className={`border-b border-zinc-800/30 hover:bg-zinc-800/40 transition-colors ${
                          isCurrent ? 'bg-amber-900/20' : ''
                        }`}
                      >
                        <td className="pl-6 pr-2 py-1 text-[10px] text-zinc-500 uppercase w-[80px]">{b.cluster && b.cluster !== b.provider_id ? b.cluster.replace('_main', '').replace('_group', '').replace('gecko_', '') : b.provider_id}</td>
                        <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate">{b.display_home} v {b.display_away}</td>
                        <td className="px-2 py-1 text-amber-400 font-medium">{resolveOutcome(b)}</td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-200">{b.odds.toFixed(2)}</td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-500">{b.fair_odds.toFixed(2)}</td>
                        <td className="px-2 py-1 text-right font-mono text-green-400">+{b.edge_pct.toFixed(1)}%</td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-300">{fmtStake(b)}</td>
                        <td className="px-2 py-1 text-right font-mono text-green-400">{fmtEv(b)}</td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-500">{fmtTtk(b)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
        })}
      </div>

      {/* Settlement toasts */}
      {toasts.length > 0 && (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
          {toasts.map(toast => (
            <div key={toast.id}
              className={`rounded border shadow-lg text-xs ${
                toast.result === 'won' ? 'bg-green-950 border-green-700/60'
                  : toast.result === 'lost' ? 'bg-red-950 border-red-700/60'
                    : 'bg-zinc-900 border-zinc-700'
              }`}>
              <div className="px-3 py-2">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`font-semibold uppercase text-[10px] ${
                    toast.result === 'won' ? 'text-green-400' : toast.result === 'lost' ? 'text-red-400' : 'text-zinc-400'
                  }`}>{toast.result}</span>
                  <span className="text-zinc-500 text-[10px]">{toast.provider_id}</span>
                </div>
                <div className="text-zinc-200 mb-1 truncate">{toast.event_label}</div>
                <div className="flex items-center gap-3 text-[11px]">
                  <span className="text-zinc-400">{toast.outcome}</span>
                  <span className="text-zinc-500 font-mono">@ {toast.odds.toFixed(2)}</span>
                  <span className="text-zinc-400 font-mono">{Math.round(toast.stake)} kr</span>
                  <span className={`font-mono font-semibold ml-auto ${toast.profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {toast.profit >= 0 ? '+' : ''}{Math.round(toast.profit)} kr
                  </span>
                </div>
              </div>
              <div className="flex border-t border-zinc-800">
                <button onClick={() => handleToastConfirm(toast)}
                  className="flex-1 px-3 py-1.5 text-[11px] font-semibold text-green-400 hover:bg-green-900/30 transition-colors">
                  Confirm
                </button>
                <button onClick={() => handleToastReject(toast)}
                  className="flex-1 px-3 py-1.5 text-[11px] text-zinc-500 hover:bg-zinc-800/50 transition-colors border-l border-zinc-800">
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
