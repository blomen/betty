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
  const [capitalPlan, setCapitalPlan] = useState<any>(null)
  const [pendingByProvider, setPendingByProvider] = useState<Record<string, any[]>>({})
  const [placedToday, setPlacedToday] = useState<Record<string, number>>({})
  const [error, setError] = useState<string | null>(null)
  const [navigating, setNavigating] = useState<string | null>(null)
  const mirror = useMirrorStream()
  const [loopRunning, setLoopRunning] = useState(false)
  const [currentBetReady, setCurrentBetReady] = useState<any>(null)
  const [toasts, setToasts] = useState<SettleToast[]>([])
  const [confirmedSettlements, setConfirmedSettlements] = useState<any[]>([])
  const [settleWaiting, setSettleWaiting] = useState(false)
  // Persist selection across tab switches
  const [selectedProvider, setSelectedProvider] = useState<string | null>(
    () => localStorage.getItem('firevsports_selected_provider')
  )
  const selectProvider = async (pid: string | null) => {
    if (loopRunning) {
      api.stopPlayLoop()
      setLoopRunning(false)
      setCurrentBetReady(null)
      setToasts([])
      setConfirmedSettlements([])
      setSettleWaiting(false)
      setLoopStatus(null)
    }
    setSelectedProvider(pid)
    if (pid) localStorage.setItem('firevsports_selected_provider', pid)
    else localStorage.removeItem('firevsports_selected_provider')

    if (pid) {
      const provBets = bets.filter(b => b.provider_id === pid)
      const hasPending = (pendingByProvider[pid] ?? []).length > 0
      if (provBets.length === 0 && !hasPending) return
      try { await api.openTab(pid) } catch { /* */ }
      setLoopRunning(true)
      await api.startPlayLoop(provBets, providerBalances)
    }
  }
  const [loopStatus, setLoopStatus] = useState<string | null>(null)
  const [providerLive, setProviderLive] = useState<any>(null)

  useEffect(() => {
    if (!selectedProvider) { setProviderLive(null); return }
    let active = true
    const poll = async () => {
      try {
        const state = await api.getProviderState(selectedProvider)
        if (active) setProviderLive(state)
      } catch { /* */ }
    }
    poll()
    const iv = setInterval(poll, 3000)
    return () => { active = false; clearInterval(iv) }
  }, [selectedProvider])

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
      setCapitalPlan(result.capital_plan ?? null)
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
    if (type === 'settling_pending') setLoopStatus(`Scanning pending bets on ${data.provider_id}...`)
    if (type === 'settling_done') {
      if (data.settlements?.length > 0) {
        // Create individual toasts for each settlement
        const newToasts: SettleToast[] = data.settlements.map((s: any) => {
          const bet = data.pending_bets?.find((b: any) => b.bet_id === s.bet_id || b.id === s.bet_id)
          const payout = s.payout ?? 0
          const stake = bet?.stake ?? 0
          const eventLabel = bet
            ? (bet.home_team && bet.away_team ? `${bet.home_team} v ${bet.away_team}` : bet.event_id?.split(':').slice(1, 3).join(' v ') ?? `Bet #${s.bet_id}`)
            : `Bet #${s.bet_id}`
          return {
            id: `settle-${s.bet_id}-${Date.now()}`,
            bet_id: s.bet_id,
            provider_id: data.provider_id,
            event_label: eventLabel,
            outcome: bet?.outcome ?? bet?.market ?? '',
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
        setLoopStatus(data.pending_count > 0 ? `${data.pending_count} pending — all still open` : null)
      }
    }
    if (type === 'settlements_confirmed') { setToasts([]); setSettleWaiting(false); setLoopStatus(null); load() }
    if (type === 'provider_skipped') setLoopStatus(`Skipped ${data.provider_id}: ${data.reason}`)
    if (type === 'bet_navigated') setLoopStatus(`Navigating to bet...`)
    if (type === 'bet_ready') { setCurrentBetReady(data.bet ?? data); setLoopStatus(null) }
    if (type === 'bet_placed') {
      setCurrentBetReady(null)
      setLoopStatus(`Bet placed (${data.placed_today ?? '?'}/${data.daily_cap ?? 10})`)
      if (data.bet?.provider_id) {
        setPlacedToday(prev => ({ ...prev, [data.bet.provider_id]: data.placed_today ?? (prev[data.bet.provider_id] ?? 0) + 1 }))
      }
      load() // refresh pending list so bet appears on the left
    }
    if (type === 'bet_skipped' || type === 'bet_failed') { setCurrentBetReady(null); setLoopStatus(null) }
    if (type === 'provider_complete') setLoopStatus(`${data.provider_id} done`)
    if (type === 'play_complete' || type === 'play_stopped') {
      setLoopRunning(false)
      setCurrentBetReady(null)
      setToasts([])
      setSettleWaiting(false)
      setLoopStatus(null)
    }
  }, [mirror.lastEvent])

  const handleStopLoop = () => {
    api.stopPlayLoop()
    setLoopRunning(false)
    setCurrentBetReady(null)
    setToasts([])
    setSettleWaiting(false)
    setLoopStatus(null)
    selectProvider(null)
  }
  const handlePlace = () => api.placeCurrent()
  const handleSkip = () => api.skipCurrent()

  // Toast confirm/reject handlers
  const handleToastConfirm = (toast: SettleToast) => {
    setConfirmedSettlements(prev => [...prev, { bet_id: toast.bet_id, result: toast.result, payout: toast.payout }])
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }
  const handleToastReject = (toast: SettleToast) => {
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }

  // When all toasts are resolved, confirm with server
  useEffect(() => {
    if (!settleWaiting) return
    if (toasts.length > 0) return
    // All toasts dismissed — send confirmed ones to server, signal loop to continue
    if (confirmedSettlements.length > 0) {
      api.confirmSettlements()
    } else {
      // All rejected — still need to unblock the loop
      api.confirmSettlements()
    }
    setSettleWaiting(false)
    setConfirmedSettlements([])
  }, [toasts, settleWaiting, confirmedSettlements])

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
  const bets = batch.filter(b => b.edge_pct > 0 && (balances[b.provider_id] ?? 0) > 0)

  // Group by cluster, then by provider within cluster
  const byCluster: Record<string, Record<string, BatchBet[]>> = {}
  for (const b of bets) {
    const cluster = b.cluster || b.provider_id
    if (!byCluster[cluster]) byCluster[cluster] = {}
    if (!byCluster[cluster][b.provider_id]) byCluster[cluster][b.provider_id] = []
    byCluster[cluster][b.provider_id].push(b)
  }
  for (const pid of Object.keys(pendingByProvider)) {
    const found = Object.values(byCluster).some(c => pid in c)
    if (!found) {
      if (!byCluster[pid]) byCluster[pid] = {}
      byCluster[pid][pid] = []
    }
  }
  for (const cluster of Object.values(byCluster))
    for (const pid in cluster) cluster[pid].sort((a, b) => b.edge_pct - a.edge_pct)

  const clusterIds = Object.keys(byCluster).sort((a, b) => {
    const evA = Object.values(byCluster[a]).flat().reduce((s, x) => s + x.expected_profit, 0)
    const evB = Object.values(byCluster[b]).flat().reduce((s, x) => s + x.expected_profit, 0)
    return evB - evA
  })
  const totalEv = summary?.total_expected_profit ?? bets.reduce((s, b) => s + b.expected_profit, 0)
  const totalPending = Object.values(pendingByProvider).reduce((s, arr) => s + arr.length, 0)

  const fmtStake = (b: BatchBet) => b.tier === 'polymarket' ? `$${b.stake.toFixed(1)}` : `${Math.round(b.stake)} kr`
  const fmtEv = (b: BatchBet) => b.tier === 'polymarket' ? `+$${b.expected_profit.toFixed(2)}` : `+${b.expected_profit.toFixed(0)} kr`
  const fmtBal = (pid: string, tier: string) => {
    const bal = balances[pid]
    if (bal == null) return ''
    return tier === 'polymarket' ? `$${bal.toFixed(1)}` : `${Math.round(bal)} kr`
  }

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
      {/* Header bar */}
      <div className="flex items-center gap-4 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900 text-xs">
        <span className="text-zinc-200 font-mono">{bets.length}</span>
        <span className="text-zinc-400">bets</span>
        {totalPending > 0 && <>
          <span className="text-amber-400 font-mono">{totalPending}</span>
          <span className="text-zinc-500">pending</span>
        </>}
        <span className="text-green-400 font-mono">+{totalEv.toFixed(0)} kr EV</span>
        <div className="ml-auto flex items-center gap-2">
          {mirror.connected && <span className="w-1.5 h-1.5 rounded-full bg-green-500" />}
          {selectedProvider && (
            <span className="text-[10px] text-amber-400 uppercase">{selectedProvider}</span>
          )}
          {loopRunning && (
            <button onClick={handleStopLoop}
              className="px-2 py-0.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded">
              Stop
            </button>
          )}
        </div>
        {loopStatus && <span className="text-xs text-amber-400">{loopStatus}</span>}
        {error && <span className="text-red-400">{error}</span>}
        {!error && batch.length === 0 && <span className="text-zinc-500">Loading...</span>}
      </div>

      {/* Provider live status strip */}
      {selectedProvider && providerLive && (
        <div className={`flex items-center gap-3 px-3 py-1.5 border-b text-xs ${
          providerLive.logged_in ? 'bg-green-900/20 border-green-800/50' : 'bg-amber-900/20 border-amber-800/50'
        }`}>
          <span className={`w-2 h-2 rounded-full ${providerLive.logged_in ? 'bg-green-500' : providerLive.found ? 'bg-amber-500 animate-pulse' : 'bg-zinc-600'}`} />
          <span className={`uppercase font-medium ${providerLive.logged_in ? 'text-green-400' : 'text-amber-400'}`}>{selectedProvider}</span>
          {providerLive.found ? (
            <>
              <span className="text-zinc-500 truncate max-w-[200px]">{providerLive.url}</span>
              {providerLive.logged_in ? (
                <span className="text-green-400">logged in{providerLive.balance != null ? ` · bal ${Math.round(providerLive.balance)} kr` : ''}</span>
              ) : (
                <span className="text-amber-400">waiting for login...</span>
              )}
            </>
          ) : (
            <span className="text-zinc-600">no tab open{providerLive.domain ? ` (${providerLive.domain})` : ''}</span>
          )}
        </div>
      )}

      {/* Bet ready bar */}
      {currentBetReady && (
        <div className="flex items-center gap-3 px-3 py-2 bg-amber-900/30 border-b border-amber-700/50">
          <span className="text-xs text-amber-400 font-medium">
            Ready: {currentBetReady.display_home} v {currentBetReady.display_away} — {currentBetReady.outcome} @ {currentBetReady.odds}
          </span>
          <span className="text-xs text-green-400">+{currentBetReady.edge_pct?.toFixed(1)}%</span>
          <div className="ml-auto flex gap-2">
            <button onClick={handlePlace}
              className="px-3 py-1 text-xs bg-green-700 hover:bg-green-600 text-white rounded font-semibold">
              Place
            </button>
            <button onClick={handleSkip}
              className="px-3 py-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded">
              Skip
            </button>
          </div>
        </div>
      )}

      {/* Main content */}
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
              <div className="flex items-center gap-3 px-3 py-1 bg-panel2/30 border-b border-zinc-800">
                <span className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider">{clusterId}</span>
                <span className="text-[10px] text-zinc-600">{allClusterBets.length} bets · {providerIds.length} providers</span>
                <span className="text-[10px] text-green-400 ml-auto">+{clusterEv.toFixed(0)} kr EV</span>
              </div>

              {providerIds.map(pid => {
                const provBets = clusterProviders[pid]
                const provEv = provBets.reduce((s, b) => s + b.expected_profit, 0)
                const bal = fmtBal(pid, provBets[0]?.tier || 'soft')
                const provPending = pendingByProvider[pid] ?? []
                const placed = placedToday[pid] ?? 0
                const dailyCap = 10
                const atCap = placed >= dailyCap
                const hasPending = provPending.length > 0
                const totalStakePending = provPending.reduce((s, p) => s + (p.stake ?? 0), 0)

                return (
                  <div key={pid} className="border-b border-zinc-800">
                    {/* Provider header */}
                    <div
                      onClick={() => selectProvider(selectedProvider === pid ? null : pid)}
                      className={`flex items-center gap-2 px-3 pl-6 py-1 border-b cursor-pointer transition-colors ${
                        selectedProvider === pid
                          ? providerLive?.logged_in && providerLive?.provider_id === pid
                            ? 'bg-green-900/30 border-green-700/50 border-l-2 border-l-green-500'
                            : 'bg-amber-900/30 border-amber-700/50 border-l-2 border-l-amber-500'
                          : 'bg-zinc-900/50 border-zinc-800 hover:bg-zinc-800/60'
                      }`}
                    >
                      <span className={`text-xs font-semibold uppercase ${
                        selectedProvider === pid
                          ? providerLive?.logged_in && providerLive?.provider_id === pid ? 'text-green-400' : 'text-amber-400'
                          : 'text-zinc-300'
                      }`}>{pid}</span>
                      <span className={`text-[10px] font-mono ${atCap ? 'text-red-400' : placed > 0 ? 'text-amber-400' : 'text-zinc-600'}`}>
                        {placed}/{dailyCap}
                      </span>
                      {hasPending && (
                        <span className="text-xs text-amber-400">{provPending.length} pending</span>
                      )}
                      <span className="text-xs text-zinc-500">{provBets.length} bets</span>
                      <span className="text-xs text-success">bal {bal}</span>
                      <span className="text-xs text-green-400 ml-auto">+{provEv.toFixed(0)} kr</span>
                    </div>

                    {/* Two-panel layout: pending (left) | bets (right) */}
                    <div className={`flex ${hasPending ? '' : ''}`}>
                      {/* Left: Pending bets */}
                      {hasPending && (
                        <div className="border-r border-zinc-800 bg-zinc-950/50" style={{ width: '280px', minWidth: '280px' }}>
                          <div className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800/50 bg-amber-900/10">
                            <span className="text-[10px] text-amber-500 uppercase font-medium">Pending</span>
                            <span className="text-[10px] text-zinc-600">{provPending.length} · {Math.round(totalStakePending)} kr</span>
                          </div>
                          {provPending.map((p: any) => {
                            const eventLabel = p.home_team && p.away_team
                              ? `${p.home_team} v ${p.away_team}`
                              : p.event_id?.split(':').slice(1, 3).join(' v ') ?? p.event_id
                            return (
                              <div key={`pending-${p.id}`}
                                className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800/30 text-xs">
                                <div className="flex-1 min-w-0">
                                  <div className="text-amber-300/70 truncate text-[11px]">{eventLabel}</div>
                                  <div className="flex gap-2 text-[10px]">
                                    <span className="text-amber-400/60">{p.outcome ?? p.market}</span>
                                    <span className="text-zinc-500 font-mono">@ {(p.odds ?? 0).toFixed(2)}</span>
                                    <span className="text-amber-300/50 font-mono">{Math.round(p.stake ?? 0)} kr</span>
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}

                      {/* Right: Bets to place */}
                      <div className="flex-1 min-w-0">
                        {provBets.length > 0 ? (
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
                                    } ${currentBetReady?.event_id === b.event_id && currentBetReady?.outcome === b.outcome ? 'bg-amber-900/20 border-l-2 border-amber-500' : ''}`}
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
                        ) : (
                          <div className="px-3 py-2 text-zinc-600 text-xs">No new bets</div>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>

      {/* Settlement toast stack — fixed bottom-right */}
      {toasts.length > 0 && (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
          {toasts.map(toast => (
            <div key={toast.id}
              className={`rounded border shadow-lg text-xs animate-in slide-in-from-right ${
                toast.result === 'won'
                  ? 'bg-green-950 border-green-700/60'
                  : toast.result === 'lost'
                    ? 'bg-red-950 border-red-700/60'
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
