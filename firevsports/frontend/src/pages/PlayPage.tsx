import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'
import { useMirrorStream } from '../hooks/useMirrorStream'

// Unlimited providers — value-bet flow (Section B). All other providers route through arbitrage (Section A).
const UNLIMITED_PROVIDERS = new Set(['pinnacle', 'polymarket', 'cloudbet'])

// Provider is "drained" when balance falls below this threshold (SEK).
// Keep small — we always play the remaining balance down, threshold just avoids
// residual-micro-balance bugs (1-2 SEK stuck from rounded stakes, refunds).
const DRAIN_THRESHOLD_SEK = 1

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
  const [activeProviders, setActiveProviders] = useState<Set<string>>(new Set())
  const [loopStatus, setLoopStatus] = useState<string | null>(null)
  const [loopProviderStatus, setLoopProviderStatus] = useState<Record<string, any> | null>(null)
  const [placementToast, setPlacementToast] = useState<{ bet: any; count: number; cap: number } | null>(null)
  const [detectedSettlements, setDetectedSettlements] = useState<Record<number, { result: string; payout: number; match_method: string }>>({})
  const [livePrices, setLivePrices] = useState<Record<string, { odds: number; edge: number | null }>>({})
  const [stakeCaps, setStakeCaps] = useState<Record<string, number>>({})
  const [dutchHedgeStatus, setDutchHedgeStatus] = useState<Record<string, {
    status: 'placing' | 'placed' | 'failed' | 'unhedged'
    counter_provider?: string
    outcome?: string
    actual_odds?: number
    actual_stake?: number
    reason?: string
  }>>({})
  const [dutchCounterPlan, setDutchCounterPlan] = useState<any[] | null>(null)
  const [dutchProfitPct, setDutchProfitPct] = useState<number | null>(null)
  const [dutchGroupId, setDutchGroupId] = useState<string | null>(null)
  // Per-anchor arb opps: { provider_id: [top 10 opps anchored on that provider] }
  const [oppsByProvider, setOppsByProvider] = useState<Record<string, any[]>>({})
  const [arbLoading, setArbLoading] = useState(false)
  // Raw single-leg edges (value opps vs Pinnacle fair), includes negative edge
  const [rawEdges, setRawEdges] = useState<any[]>([])
  const [rawEdgesLoading, setRawEdgesLoading] = useState(false)

  const startSkin = async (pid: string) => {
    // Deselect — click active provider to remove it
    if (activeProviders.has(pid)) {
      setActiveProviders(prev => {
        const next = new Set(prev)
        next.delete(pid)
        // If last provider removed, stop everything
        if (next.size === 0 && loopRunning) {
          api.stopPlayLoop()
          setLoopRunning(false)
          setCurrentBetReady(null)
          setLoopStatus(null)
          setLoopProviderStatus(null)
        }
        return next
      })
      return
    }
    // Add provider — open tab and start/add to loop
    setActiveProviders(prev => new Set(prev).add(pid))
    try { await api.startMirror() } catch { /* */ }
    try { await api.openTab(pid) } catch { /* */ }
    // Collect bets from all active clusters (current + new)
    const allPids = [...activeProviders, pid]
    const selectedClusters = new Set(allPids.map(p => providerToCluster[p] || p))
    const allBets = bets.filter(b => selectedClusters.has(b.cluster || b.provider_id))
    setLoopRunning(true)
    await api.startPlayLoop(allBets, providerBalances, allPids)
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

  // Fetch top 10 arb opps per funded soft provider (anchor-centric).
  // Counter pool excludes drained providers (balance < threshold).
  const loadArbOpps = useCallback(async () => {
    const soft = Array.from(
      new Set(batch.map(b => b.provider_id).filter(pid => !UNLIMITED_PROVIDERS.has(pid)))
    )
    const funded = soft.filter(pid => (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK)
    if (funded.length === 0) {
      setOppsByProvider({})
      return
    }
    // Non-drained counter pool: funded soft books + all unlimited providers
    const pool = [...funded, ...Array.from(UNLIMITED_PROVIDERS)]
    try {
      setArbLoading(true)
      const results = await Promise.all(
        funded.map(async anchor => {
          const counters = pool.filter(p => p !== anchor)
          try {
            const res = await api.getArbOpps([anchor], counters, 10)
            const opps = ((res?.opportunities ?? []) as any[])
              .sort((a, b) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0))
            return [anchor, opps] as const
          } catch {
            return [anchor, [] as any[]] as const
          }
        })
      )
      setOppsByProvider(Object.fromEntries(results))
    } finally {
      setArbLoading(false)
    }
  }, [batch, providerBalances])

  const loadRawEdges = useCallback(async () => {
    try {
      setRawEdgesLoading(true)
      const res = await api.getRawEdges(20)
      const opps = ((res?.opportunities ?? []) as any[])
        .sort((a, b) => (b.edge_pct ?? -999) - (a.edge_pct ?? -999))
      setRawEdges(opps)
    } catch {
      /* swallow */
    } finally {
      setRawEdgesLoading(false)
    }
  }, [])

  useEffect(() => {
    loadArbOpps()
    const id = setInterval(loadArbOpps, 30_000)
    return () => clearInterval(id)
  }, [loadArbOpps])

  useEffect(() => {
    loadRawEdges()
    const id = setInterval(loadRawEdges, 30_000)
    return () => clearInterval(id)
  }, [loadRawEdges])

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
    if (type === 'settlements_detected') {
      const setts = data.settlements ?? []
      const map: Record<number, { result: string; payout: number; match_method: string }> = {}
      for (const s of setts) map[s.bet_id] = { result: s.result, payout: s.payout ?? 0, match_method: s.match_method ?? '' }
      setDetectedSettlements(prev => ({ ...prev, ...map }))
    }
    if (type === 'settlements_confirmed') { setToasts([]); setSettleWaiting(false); setDetectedSettlements({}); setLoopStatus(null); load() }
    if (type === 'provider_skipped') setLoopStatus(`Skipped ${data.provider_id}: ${data.reason}`)
    if (type === 'bet_ready') {
      const bet = data.bet ?? data
      setCurrentBetReady({ ...bet, prep_ok: data.prep_ok, live_odds: data.live_odds, live_edge: data.live_edge, prep_reason: data.prep_reason })
      if (data.live_odds != null && bet.event_id) {
        const key = `${bet.event_id}:${bet.market}:${bet.outcome}`
        setLivePrices(prev => ({ ...prev, [key]: { odds: data.live_odds, edge: data.live_edge } }))
      }
      setLoopStatus(null)
    }
    if (type === 'bet_placed') {
      setCurrentBetReady(null)
      const bet = data.bet
      const pid = bet?.provider_id || ''
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
        const bpid = bet.provider_id || ''
        setPendingByProvider(prev => ({
          ...prev,
          [bpid]: [...(prev[bpid] ?? []), {
            id: Date.now(),
            event_id: bet.event_id,
            provider_id: bpid,
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
    if (type === 'live_price') {
      const key = `${data.event_id}:${data.market}:${data.outcome}`
      setLivePrices(prev => ({ ...prev, [key]: { odds: data.live_odds, edge: data.live_edge } }))
    }
    if (type === 'stake_limited') {
      const pid = data.provider_id
      const cap = data.cap ?? data.actual_stake
      if (pid && cap) setStakeCaps(prev => ({ ...prev, [pid]: cap }))
    }
    if (type === 'bet_skipped' || type === 'bet_failed') { setCurrentBetReady(null); setLoopStatus(null); setDutchCounterPlan(null); setDutchProfitPct(null); setDutchGroupId(null); setDutchHedgeStatus({}) }
    if (type === 'dutch_bet_ready') {
      const bet = data.bet ?? data
      setCurrentBetReady({ ...bet, prep_ok: data.prep_ok, live_odds: data.live_odds, live_edge: data.live_edge })
      setDutchCounterPlan(data.counter_plan ?? null)
      setDutchProfitPct(data.guaranteed_profit_pct ?? null)
      setDutchGroupId(data.dutch_group_id ?? null)
      setDutchHedgeStatus({})
      setLoopStatus(null)
    }
    if (type === 'dutch_hedge_placing') {
      setDutchHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'placing', counter_provider: data.counter_provider, outcome: data.outcome } }))
    }
    if (type === 'dutch_hedge_placed') {
      setDutchHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'placed', counter_provider: data.counter_provider, outcome: data.outcome, actual_odds: data.actual_odds, actual_stake: data.actual_stake } }))
    }
    if (type === 'dutch_hedge_failed') {
      setDutchHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'failed', counter_provider: data.counter_provider, outcome: data.outcome, reason: data.reason } }))
    }
    if (type === 'dutch_unhedged') {
      setDutchHedgeStatus(prev => ({ ...prev, __unhedged: { status: 'unhedged', outcome: data.outcome, reason: 'All fallbacks exhausted' } }))
    }
    if (type === 'dutch_complete') {
      setDutchProfitPct(data.guaranteed_profit_pct ?? dutchProfitPct)
      setTimeout(() => { setDutchCounterPlan(null); setDutchProfitPct(null); setDutchGroupId(null); setDutchHedgeStatus({}); setCurrentBetReady(null) }, 5000)
      loadArbOpps()
    }
    if (type === 'provider_complete') {
      setLoopProviderStatus(prev => {
        if (!prev) return prev
        const next = { ...prev }
        delete next[data.provider_id]
        return Object.keys(next).length > 0 ? next : null
      })
    }
    if (type === 'play_complete' || type === 'play_stopped') {
      setLoopRunning(false)
      setCurrentBetReady(null)
      setLoopProviderStatus(null)
      setToasts([])
      setSettleWaiting(false)
      setLoopStatus(null)
      setDutchCounterPlan(null)
      setDutchProfitPct(null)
      setDutchGroupId(null)
      setDutchHedgeStatus({})
    }
    // Update per-provider status from individual events
    if (type === 'provider_opening' || type === 'login_waiting' || type === 'login_detected' ||
        type === 'settling_pending' || type === 'settling_done' ||
        type === 'bet_ready' || type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed') {
      const epid = data.provider_id || data.bet?.provider_id
      if (epid) {
        setLoopProviderStatus(prev => ({
          ...prev,
          [epid]: {
            state: type === 'bet_ready' ? 'ready' :
                   type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed' ? 'navigating' :
                   type.includes('login') ? 'login_waiting' :
                   type.includes('settl') ? 'settling' : 'opening',
            current_bet: data.bet || null,
          }
        }))
      }
    }
  }, [mirror.lastEvent])

  const handlePlace = () => api.placeCurrent()

  const handleToastConfirm = (toast: SettleToast) => {
    setConfirmedSettlements(prev => [...prev, { bet_id: toast.bet_id, result: toast.result, payout: toast.payout }])
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }
  const handleToastReject = (toast: SettleToast) => {
    setToasts(prev => prev.filter(t => t.id !== toast.id))
  }

  const handleConfirmSettlements = async () => {
    const batch = Object.entries(detectedSettlements).map(([betId, s]) => ({
      bet_id: Number(betId),
      result: s.result,
    }))
    if (batch.length === 0) return
    try {
      await api.settleBatch(batch)
      setDetectedSettlements({})
      load()
    } catch (e: any) {
      console.error('settle failed', e)
    }
  }

  const handleDismissSettlement = (betId: number) => {
    setDetectedSettlements(prev => {
      const next = { ...prev }
      delete next[betId]
      return next
    })
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
    if (!UNLIMITED_PROVIDERS.has(b.provider_id)) return false
    if (b.edge_pct <= 0) return false
    const h = getTtkHours(b)
    if (h != null && h > ttkFilter) return false
    return true
  })

  // Soft providers seen in the current batch — drive the arb activation bar.
  // Derived from batch data so the UI follows whatever the backend returns.
  const softProviders = Array.from(
    new Set(batch.map(b => b.provider_id).filter(pid => !UNLIMITED_PROVIDERS.has(pid)))
  ).sort()

  // Unlimited providers behave as their own standalone "cluster" — no sibling dedup needed.
  const CLUSTER_MEMBERS: Record<string, string[]> = {}
  const providerToCluster: Record<string, string> = {}
  for (const [cluster, members] of Object.entries(CLUSTER_MEMBERS)) {
    for (const pid of members) providerToCluster[pid] = cluster
  }
  // Override with actual batch data (in case cluster names differ)
  for (const b of batch) providerToCluster[b.provider_id] = b.cluster || b.provider_id

  // Group by cluster — flat list, no provider sub-grouping.
  // Section B clusters are UNLIMITED-only; soft providers are handled in the arb section.
  const byCluster: Record<string, BatchBet[]> = {}
  for (const b of bets) {
    const cluster = b.cluster || b.provider_id
    if (!byCluster[cluster]) byCluster[cluster] = []
    byCluster[cluster].push(b)
  }
  // Ensure clusters exist for UNLIMITED providers with balance or pending (even if no bets)
  for (const pid of Object.keys(providerBalances)) {
    if (!UNLIMITED_PROVIDERS.has(pid)) continue
    const cluster = providerToCluster[pid] || pid
    if (!byCluster[cluster]) byCluster[cluster] = []
  }
  for (const pid of Object.keys(pendingByProvider)) {
    if (!UNLIMITED_PROVIDERS.has(pid)) continue
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

      {/* Dutch arb card */}
      {currentBetReady && dutchCounterPlan && (
        <div className="border-b border-purple-700/50 bg-purple-900/10 px-3 py-2">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="px-1.5 py-0.5 text-[10px] font-bold bg-purple-900/50 text-purple-400 border border-purple-700/50 rounded">DUTCH ARB</span>
            {dutchProfitPct != null && (
              <span className="text-xs font-mono font-semibold text-green-400">+{dutchProfitPct.toFixed(2)}% guaranteed profit</span>
            )}
            {dutchGroupId && <span className="text-[10px] text-zinc-600 ml-auto font-mono">{dutchGroupId}</span>}
          </div>
          <div className="flex items-center gap-2 text-xs mb-1.5">
            <span className="text-zinc-400">Anchor:</span>
            <span className="text-zinc-200">{currentBetReady.display_home} v {currentBetReady.display_away}</span>
            <span className="text-amber-400 font-medium">{resolveOutcome(currentBetReady)}</span>
            <span className="font-mono text-zinc-200">@ {(currentBetReady.live_odds ?? currentBetReady.odds)?.toFixed(2)}</span>
            <span className="text-zinc-500 uppercase text-[10px]">{currentBetReady.provider_id}</span>
          </div>
          <div className="space-y-0.5">
            {dutchCounterPlan.map((leg: any, i: number) => (
              <div key={i}>
                <div className="text-[10px] text-zinc-500 mb-0.5">Counter: {leg.outcome}</div>
                {leg.providers?.map((p: any, j: number) => {
                  const hedge = dutchHedgeStatus[p.provider]
                  return (
                    <div key={j} className="flex items-center gap-2 pl-3 text-[10px]">
                      <span className="text-zinc-400 uppercase w-16">{p.provider}</span>
                      <span className="font-mono text-zinc-300">@ {p.odds?.toFixed(2)}</span>
                      <span className="font-mono text-zinc-500">{(p.stake_pct * 100).toFixed(0)}%</span>
                      {hedge?.status === 'placing' && <span className="text-amber-400 animate-pulse">Placing...</span>}
                      {hedge?.status === 'placed' && (
                        <span className="text-green-400 font-semibold">
                          HEDGED @ {hedge.actual_odds?.toFixed(2)} · {Math.round(hedge.actual_stake ?? 0)} kr
                        </span>
                      )}
                      {hedge?.status === 'failed' && <span className="text-red-400">Failed: {hedge.reason}</span>}
                      {!hedge && <span className="text-zinc-600">Waiting</span>}
                    </div>
                  )
                })}
              </div>
            ))}
            {dutchHedgeStatus.__unhedged && (
              <div className="flex items-center gap-2 pl-3 text-[10px] mt-1">
                <span className="text-red-400 font-semibold">UNHEDGED — all fallbacks exhausted</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Per-provider status rows */}
      {loopRunning && loopProviderStatus && Object.keys(loopProviderStatus).length > 0 && (
        <div className="border-b border-zinc-800">
          {Object.entries(loopProviderStatus).map(([pid, status]: [string, any]) => (
            <div key={pid} className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800/50 bg-zinc-900/30">
              <span className="text-[10px] font-semibold text-amber-400 uppercase w-20">{pid}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                status.state === 'ready' ? 'bg-green-900/40 text-green-400' :
                status.state === 'navigating' ? 'bg-blue-900/40 text-blue-400' :
                status.state === 'placing' ? 'bg-amber-900/40 text-amber-400' :
                status.state === 'settling' ? 'bg-purple-900/40 text-purple-400' :
                'bg-zinc-800 text-zinc-500'
              }`}>{status.state}</span>
              {status.current_bet && (
                <>
                  <span className="text-[10px] text-zinc-300 truncate">
                    {status.current_bet.display_home} v {status.current_bet.display_away}
                  </span>
                  <span className="text-[10px] text-amber-400 font-medium">{resolveOutcome(status.current_bet)}</span>
                  <span className="text-[10px] font-mono text-zinc-200">@ {(status.current_bet.live_odds ?? status.current_bet.odds)?.toFixed(2)}</span>
                  {status.current_bet.edge_pct != null && <span className="text-[10px] text-green-400">+{status.current_bet.edge_pct?.toFixed(1)}%</span>}
                  <span className="text-[10px] font-mono text-zinc-500">{Math.round(status.current_bet.stake ?? 0)} kr</span>
                </>
              )}
              {status.state === 'ready' && (
                <button onClick={() => api.skipCurrent(pid)} className="text-[10px] text-zinc-500 hover:text-zinc-300 ml-auto">Skip</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        {/* SECTION A — Per-provider Arb Opportunities (soft books, arb-only) */}
        {(() => {
          const fundedSoft = softProviders.filter(
            pid => (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK
          )
          const drainedSoft = softProviders.filter(
            pid => (providerBalances[pid] ?? 0) < DRAIN_THRESHOLD_SEK
          )
          const totalOpps = Object.values(oppsByProvider).reduce((n, arr) => n + arr.length, 0)

          return (
            <div className="border-b border-zinc-800 pb-2 mb-2">
              <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800">
                <h3 className="text-[10px] font-bold text-purple-400 uppercase tracking-wider">
                  Arb Opportunities
                </h3>
                <span className="text-[10px] text-zinc-500 font-mono">{totalOpps}</span>
                {arbLoading && <span className="text-[10px] text-zinc-600">loading…</span>}
                <span className="text-[10px] text-zinc-600 ml-auto">
                  top 10 per funded provider · drained excluded
                </span>
              </div>

              {/* Drained (blacklisted) providers — shown but not scanned */}
              {drainedSoft.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 px-3 py-1 border-b border-zinc-800/50 bg-zinc-900/20">
                  <span className="text-[10px] text-zinc-600 uppercase tracking-wider">Drained:</span>
                  {drainedSoft.map(pid => (
                    <span
                      key={pid}
                      className="px-1.5 py-0.5 text-[10px] rounded text-zinc-600 line-through bg-zinc-900/50 border border-zinc-800"
                      title={`Balance ${(providerBalances[pid] ?? 0).toFixed(2)} SEK < ${DRAIN_THRESHOLD_SEK} — excluded from counter pool`}
                    >
                      {pid}
                    </span>
                  ))}
                </div>
              )}

              {/* Per-provider arb cards */}
              {fundedSoft.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-zinc-600">
                  No funded soft books. Fund a provider (balance ≥ {DRAIN_THRESHOLD_SEK} SEK) to see arb opps.
                </div>
              ) : (
                <div className="flex flex-col">
                  {fundedSoft.map(pid => {
                    const bal = providerBalances[pid] ?? 0
                    const pending = pendingByProvider[pid]?.length ?? 0
                    const placed = placedToday[pid] ?? 0
                    const isSkinActive = activeProviders.has(pid)
                    const atCap = placed >= 10
                    const opps = oppsByProvider[pid] ?? []
                    return (
                      <div key={pid} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Header bar with activate button */}
                        <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/30">
                          <button
                            onClick={() => startSkin(pid)}
                            className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                              isSkinActive
                                ? 'bg-purple-700/50 text-purple-200 border border-purple-600/50'
                                : 'text-zinc-300 hover:bg-zinc-700/50 border border-zinc-700/50 cursor-pointer'
                            }`}
                          >
                            <span className="uppercase font-semibold">{pid}</span>
                            <span className="ml-1 text-zinc-500">{Math.round(bal)}</span>
                          </button>
                          <span className={`text-[10px] ${atCap ? 'text-red-400' : placed > 0 ? 'text-amber-400' : 'text-zinc-500'}`}>
                            {placed}/10
                          </span>
                          {pending > 0 && <span className="text-[10px] text-amber-400">{pending}p pending</span>}
                          {stakeCaps[pid] && (
                            <span className="px-1 py-px text-[8px] font-bold bg-orange-900/50 text-orange-400 border border-orange-700/50 rounded">
                              ≤{Math.round(stakeCaps[pid])}
                            </span>
                          )}
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {opps.length} arb{opps.length === 1 ? '' : 's'}
                          </span>
                        </div>

                        {/* Per-provider arb table */}
                        {opps.length === 0 ? (
                          <div className="px-6 py-2 text-[10px] text-zinc-600">
                            {arbLoading ? 'Scanning…' : 'No arbs for this provider right now.'}
                          </div>
                        ) : (
                          <table className="w-full text-xs">
                            <tbody>
                              {opps.map((opp: any, i: number) => {
                                const anchor = opp.anchor ?? {}
                                const counterLegs = opp.counter_plan ?? opp.counter_legs ?? opp.legs ?? []
                                const profitPct = opp.guaranteed_profit_pct ?? 0
                                const eventLabel = opp.display_home && opp.display_away
                                  ? `${opp.display_home} v ${opp.display_away}`
                                  : opp.event_id
                                // Anchor may be inline on opp.legs as the leg with provider === pid
                                const anchorLeg = anchor.provider || anchor.provider_id
                                  ? anchor
                                  : (opp.legs ?? []).find((l: any) => (l.provider ?? l.provider_id) === pid) ?? {}
                                const anchorOutcome = anchorLeg.outcome
                                  ? (anchorLeg.point != null ? `${anchorLeg.outcome} ${anchorLeg.point}` : anchorLeg.outcome)
                                  : '—'
                                const counters = (counterLegs as any[]).filter(
                                  (l: any) => (l.provider ?? l.provider_id) !== pid
                                )
                                return (
                                  <tr key={`arb-${pid}-${i}`} className="border-b border-zinc-800/30 hover:bg-zinc-800/40">
                                    <td className={`pl-6 pr-2 py-1 font-mono font-semibold text-right w-[60px] ${profitPct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                      {profitPct >= 0 ? '+' : ''}{profitPct.toFixed(2)}%
                                    </td>
                                    <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate text-[11px]">{eventLabel}</td>
                                    <td className="px-2 py-1 text-zinc-500 text-[10px] uppercase">{opp.market ?? ''}</td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <span className="text-amber-400">{anchorOutcome}</span>{' '}
                                      <span className="font-mono text-zinc-200">@ {Number(anchorLeg.odds ?? 0).toFixed(2)}</span>
                                    </td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <div className="flex flex-col gap-0.5">
                                        {counters.map((leg: any, li: number) => {
                                          const legOutcome = leg.outcome
                                            ? (leg.point != null ? `${leg.outcome} ${leg.point}` : leg.outcome)
                                            : '—'
                                          return (
                                            <div key={li} className="flex items-center gap-1">
                                              <span className="text-amber-400/80">{legOutcome}</span>
                                              <span className="text-zinc-500 uppercase text-[10px]">{leg.provider ?? leg.provider_id}</span>
                                              <span className="font-mono text-zinc-300">@ {Number(leg.odds ?? 0).toFixed(2)}</span>
                                            </div>
                                          )
                                        })}
                                      </div>
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })()}

        {/* SECTION B — Value bets (unlimited providers only) */}
        {clusterIds.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800">
            <h3 className="text-[10px] font-bold text-amber-400 uppercase tracking-wider">Value Bets</h3>
            <span className="text-[10px] text-zinc-500 font-mono">{bets.length}</span>
          </div>
        )}
        {clusterIds.length === 0 && batch.length > 0 && (
          <div className="p-4 text-zinc-500 text-xs">No positive-edge value bets (Pinnacle / Polymarket / Cloudbet).</div>
        )}

        {clusterIds.map(clusterId => {
          const cb = byCluster[clusterId] || []
          const stats = clusterStats(clusterId)
          const isActive = stats.providers.some(p => activeProviders.has(p))

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
                    const isSkinActive = activeProviders.has(pid)
                    const uncapped = ['pinnacle', 'polymarket', 'cloudbet'].includes(pid)
                    const atCap = !uncapped && placed >= 10
                    const disabled = bal <= 0 && pending === 0 && !uncapped
                    return (
                      <button key={pid}
                        onClick={() => !disabled && startSkin(pid)}
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
                        {stakeCaps[pid] && <span className="ml-1 px-1 py-px text-[8px] font-bold bg-orange-900/50 text-orange-400 border border-orange-700/50 rounded" title={`Provider limit: max ${Math.round(stakeCaps[pid])} kr per bet`}>≤{Math.round(stakeCaps[pid])}</span>}
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
                const clusterSettled = clusterPending.filter((p: any) => detectedSettlements[p.bet_id ?? p.id])
                const clusterPnl = clusterSettled.reduce((s: number, p: any) => {
                  const det = detectedSettlements[p.bet_id ?? p.id]
                  return s + ((det?.payout ?? 0) - (p.stake ?? 0))
                }, 0)
                return (
                  <div className="border-b border-zinc-800 bg-amber-900/5">
                    <div className="flex items-center gap-2 px-3 py-0.5 border-b border-zinc-800/30">
                      <span className="text-[10px] text-amber-500 uppercase font-medium">Pending</span>
                      <span className="text-[10px] text-zinc-600">{clusterPending.length} bets · {Math.round(clusterPending.reduce((s: number, p: any) => s + (p.stake ?? 0), 0))} kr</span>
                      {clusterSettled.length > 0 && (
                        <>
                          <span className={`text-[10px] font-mono font-semibold ${clusterPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {clusterPnl >= 0 ? '+' : ''}{Math.round(clusterPnl)} kr
                          </span>
                          <button onClick={handleConfirmSettlements}
                            className="ml-auto px-2 py-0.5 text-[10px] font-semibold bg-green-900/40 text-green-400 border border-green-700/50 rounded hover:bg-green-900/60 transition-colors">
                            Confirm {clusterSettled.length}
                          </button>
                        </>
                      )}
                    </div>
                    {clusterPending.map((p: any) => {
                      const betId = p.bet_id ?? p.id
                      const det = detectedSettlements[betId]
                      const eventLabel = p.home_team && p.away_team
                        ? `${p.home_team} v ${p.away_team}`
                        : p.event_id?.split(':').slice(1, 3).join(' v ') ?? p.event_id
                      const profit = det ? (det.payout - (p.stake ?? 0)) : 0
                      return (
                        <div key={`pending-${p.id}`} className={`flex items-center gap-2 px-3 pl-6 py-0.5 border-b border-zinc-800/20 text-xs ${
                          det ? (det.result === 'won' ? 'bg-green-900/10' : det.result === 'lost' ? 'bg-red-900/10' : 'bg-zinc-800/20') : ''
                        }`}>
                          <span className="text-[10px] text-zinc-600 uppercase w-[80px]">{p._pid}</span>
                          <span className={`truncate flex-1 ${det ? 'text-zinc-400' : 'text-amber-300/70'}`}>{eventLabel}</span>
                          <span className="text-amber-400/60 text-[10px]">{p.outcome ?? p.market}</span>
                          <span className="text-zinc-500 font-mono text-[10px]">@ {(p.odds ?? 0).toFixed(2)}</span>
                          <span className="text-amber-300/50 font-mono text-[10px]">{Math.round(p.stake ?? 0)} kr</span>
                          {det && (
                            <>
                              <span className={`text-[10px] font-semibold uppercase px-1 rounded ${
                                det.result === 'won' ? 'text-green-400 bg-green-900/30' :
                                det.result === 'lost' ? 'text-red-400 bg-red-900/30' :
                                'text-zinc-400 bg-zinc-800'
                              }`}>{det.result}</span>
                              <span className={`text-[10px] font-mono font-semibold ${profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                {profit >= 0 ? '+' : ''}{Math.round(profit)} kr
                              </span>
                              <button onClick={() => handleDismissSettlement(betId)}
                                className="text-zinc-600 hover:text-zinc-400 text-[10px]">✕</button>
                            </>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )
              })()}

              {/* Bet rows */}
              <table className="w-full text-xs">
                <tbody>
                  {[...cb].sort((a, b) => {
                    const aEdge = livePrices[`${a.event_id}:${a.market}:${a.outcome}`]?.edge ?? a.edge_pct
                    const bEdge = livePrices[`${b.event_id}:${b.market}:${b.outcome}`]?.edge ?? b.edge_pct
                    return bEdge - aEdge
                  }).map(b => {
                    const key = `${b.event_id}:${b.market}:${b.outcome}:${b.provider_id}`
                    const liveKey = `${b.event_id}:${b.market}:${b.outcome}`
                    const live = livePrices[liveKey]
                    const isCurrent = currentBetReady?.event_id === b.event_id && currentBetReady?.outcome === b.outcome
                    const displayOdds = live?.odds ?? b.odds
                    const displayEdge = live?.edge ?? b.edge_pct
                    const oddsChanged = live && Math.abs(live.odds - b.odds) >= 0.01
                    return (
                      <tr key={key}
                        className={`border-b border-zinc-800/30 hover:bg-zinc-800/40 transition-colors ${
                          isCurrent ? 'bg-amber-900/20' : ''
                        }`}
                      >
                        <td className="pl-6 pr-2 py-1 text-[10px] text-zinc-500 uppercase w-[80px]">{b.cluster && b.cluster !== b.provider_id ? b.cluster.replace('_main', '').replace('_group', '').replace('gecko_', '') : b.provider_id}</td>
                        <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate">{b.display_home} v {b.display_away}</td>
                        <td className="px-2 py-1 text-amber-400 font-medium">{resolveOutcome(b)}</td>
                        <td className={`px-2 py-1 text-right font-mono ${oddsChanged ? (live!.odds > b.odds ? 'text-green-400' : 'text-red-400') : 'text-zinc-200'}`}>
                          {displayOdds.toFixed(2)}
                          {oddsChanged && <span className="text-zinc-600 text-[9px] ml-0.5">({b.odds.toFixed(2)})</span>}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-500">{b.fair_odds.toFixed(2)}</td>
                        <td className={`px-2 py-1 text-right font-mono ${displayEdge >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {displayEdge >= 0 ? '+' : ''}{displayEdge.toFixed(1)}%
                        </td>
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

        {/* SECTION C — Top 20 Raw Edges (single-leg, includes negative) */}
        <div className="border-t border-zinc-800 mt-2">
          <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800">
            <h3 className="text-[10px] font-bold text-cyan-400 uppercase tracking-wider">Top 20 Edges (raw)</h3>
            <span className="text-[10px] text-zinc-500 font-mono">{rawEdges.length}</span>
            {rawEdgesLoading && <span className="text-[10px] text-zinc-600">loading…</span>}
            <span className="text-[10px] text-zinc-600 ml-auto">single-leg vs Pinnacle fair · negative included</span>
          </div>
          {rawEdges.length === 0 ? (
            <div className="px-3 py-3 text-[11px] text-zinc-600">
              {rawEdgesLoading ? 'Loading…' : 'No edges available.'}
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-zinc-500 uppercase tracking-wider">
                  <th className="pl-6 pr-2 py-1 text-right w-[60px]">Edge</th>
                  <th className="px-2 py-1 text-left">Event</th>
                  <th className="px-2 py-1 text-left">Market</th>
                  <th className="px-2 py-1 text-left">Outcome</th>
                  <th className="px-2 py-1 text-left">Provider</th>
                  <th className="px-2 py-1 text-right">Odds</th>
                  <th className="px-2 py-1 text-right">Fair</th>
                </tr>
              </thead>
              <tbody>
                {rawEdges.map((opp: any, i: number) => {
                  const edge = opp.edge_pct ?? 0
                  const eventLabel = opp.display_home && opp.display_away
                    ? `${opp.display_home} v ${opp.display_away}`
                    : opp.event_id
                  const outcome = opp.outcome1
                    ? (opp.point != null ? `${opp.outcome1} ${opp.point}` : opp.outcome1)
                    : '—'
                  return (
                    <tr key={`edge-${opp.id ?? i}`} className="border-b border-zinc-800/30 hover:bg-zinc-800/40">
                      <td className={`pl-6 pr-2 py-1 font-mono font-semibold text-right ${edge >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                      </td>
                      <td className="px-2 py-1 text-zinc-200 max-w-[240px] truncate text-[11px]">{eventLabel}</td>
                      <td className="px-2 py-1 text-zinc-500 text-[10px] uppercase">{opp.market ?? ''}</td>
                      <td className="px-2 py-1 text-amber-400 text-[11px]">{outcome}</td>
                      <td className="px-2 py-1 text-zinc-500 uppercase text-[10px]">{opp.provider1 ?? ''}</td>
                      <td className="px-2 py-1 text-right font-mono text-zinc-200">{Number(opp.odds1 ?? 0).toFixed(2)}</td>
                      <td className="px-2 py-1 text-right font-mono text-zinc-500">{Number(opp.fair_odds ?? 0).toFixed(2)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
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
