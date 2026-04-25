import { useState, useEffect, useCallback } from 'react'
import { api } from '../hooks/useApi'
import { useMirrorStream } from '../hooks/useMirrorStream'

// Unlimited providers — value-bet flow (Section B). All other providers route through arbitrage (Section A).
const UNLIMITED_PROVIDERS = new Set(['pinnacle', 'polymarket', 'cloudbet', 'kalshi'])

// Provider is "drained" when balance falls below this threshold (SEK).
// Below this, no meaningful bet can be placed after odds rounding and
// provider-side minimum stakes (typically 5-10 kr), so the residue is
// not actionable.
const DRAIN_THRESHOLD_SEK = 20

// Minimum guaranteed_profit_pct an arb must show for a fully-drained
// cluster (no funded members) to be surfaced as a deposit hint. Tuned
// to clear realistic execution costs: ~0.5-1.5% on Pinnacle-hedged
// arbs, ~1.5-4% on Kalshi/Polymarket-hedged arbs (slippage + spread +
// per-contract fees).
const DEPOSIT_HINT_MIN_PROFIT_PCT = 2.5

// Soft-book cluster membership (mirrors backend mirror/play_loop.py _CLUSTER_MEMBERS).
// Cluster siblings share odds engines → arb between them has zero edge, so they
// group into one section and auto-exclude each other from counter pool.
const SOFT_CLUSTER_MEMBERS: Record<string, string[]> = {
  kambi: ['unibet', 'leovegas', 'expekt', 'betmgm', 'speedybet', 'x3000', 'goldenbull', '1x2'],
  spectate: ['888sport', 'mrgreen'],
  altenar_main: ['betinia', 'campobet', 'lodur', 'quickcasino', 'swiper', 'dbet'],
  gecko_betsson: ['betsson', 'nordicbet', 'betsafe', 'spelklubben'],
  comeon_group: ['comeon', 'lyllo', 'hajper', 'snabbare'],
}
// Standalone soft providers — their own one-provider "cluster". Listed so they
// always appear in the UI even when fully untouched (no balance / bonus / pending).
const SOFT_STANDALONES: string[] = [
  'interwetten', 'vbet', '10bet', 'tipwin', 'coolbet', 'bethard',
]
const PROVIDER_TO_SOFT_CLUSTER: Record<string, string> = {}
for (const [c, members] of Object.entries(SOFT_CLUSTER_MEMBERS)) {
  for (const m of members) PROVIDER_TO_SOFT_CLUSTER[m] = c
}
// Resolve a soft provider's cluster (fallback to pid for standalones).
const resolveSoftCluster = (pid: string): string => PROVIDER_TO_SOFT_CLUSTER[pid] ?? pid

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
  const [loggedInProviders, setLoggedInProviders] = useState<Set<string>>(new Set())
  const [loopStatus, setLoopStatus] = useState<string | null>(null)
  const [loopProviderStatus, setLoopProviderStatus] = useState<Record<string, any> | null>(null)
  const [placementToast, setPlacementToast] = useState<{ bet: any; count: number; cap: number } | null>(null)
  const [reconcileToasts, setReconcileToasts] = useState<Array<{
    id: string; provider_id: string; bet_id: number; event_name?: string;
    match_method: string; confidence?: number; changes: Record<string, any>;
  }>>([])
  const [detectedSettlements, setDetectedSettlements] = useState<Record<number, { result: string; payout: number; match_method: string }>>({})
  const [livePrices, setLivePrices] = useState<Record<string, { odds: number; edge: number | null }>>({})
  const [stakeCaps, setStakeCaps] = useState<Record<string, number>>({})
  const [arbHedgeStatus, setArbHedgeStatus] = useState<Record<string, {
    status: 'placing' | 'placed' | 'failed' | 'unhedged'
    counter_provider?: string
    outcome?: string
    actual_odds?: number
    actual_stake?: number
    reason?: string
  }>>({})
  const [arbCounterPlan, setArbCounterPlan] = useState<any[] | null>(null)
  const [arbProfitPct, setArbProfitPct] = useState<number | null>(null)
  const [arbGroupId, setArbGroupId] = useState<string | null>(null)
  // Per-cluster arb opps: { cluster_key: [top 10 opps for that cluster's funded siblings] }
  // Siblings share odds, so one fetch per cluster suffices. Each sibling renders its
  // own card using the cluster's opp list (differing only in balance / cap / active state).
  const [oppsByCluster, setOppsByCluster] = useState<Record<string, any[]>>({})
  const [arbLoading, setArbLoading] = useState(false)
  // Sub-tab switcher within the Play page
  const [subTab, setSubTab] = useState<'arb' | 'value'>('value')

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

  // Fetch top 10 arb opps per cluster of funded soft providers.
  // One fetch per cluster (siblings share odds). Counter pool excludes same-cluster
  // providers (zero edge) and drained providers (no balance to place anchor leg).
  const loadArbOpps = useCallback(async () => {
    // Group all known soft providers by cluster
    const softByCluster: Record<string, string[]> = {}
    for (const pid of Object.keys(providerBalances)) {
      if (UNLIMITED_PROVIDERS.has(pid)) continue
      const cluster = resolveSoftCluster(pid)
      ;(softByCluster[cluster] ??= []).push(pid)
    }

    // Pick one representative per cluster that has at least one funded sibling
    const reps: Array<{ cluster: string; rep: string }> = []
    const fundedAll: string[] = []
    for (const [cluster, members] of Object.entries(softByCluster)) {
      const funded = members.filter(pid => (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK)
      if (funded.length > 0) {
        reps.push({ cluster, rep: funded[0] })
        fundedAll.push(...funded)
      }
    }

    if (reps.length === 0) {
      setOppsByCluster({})
      return
    }

    // Non-drained counter pool: all funded soft books + unlimited providers
    const pool = [...fundedAll, ...Array.from(UNLIMITED_PROVIDERS)]

    try {
      setArbLoading(true)
      const results = await Promise.all(
        reps.map(async ({ cluster, rep }) => {
          // Exclude same-cluster siblings from counter pool (zero edge)
          const counters = pool.filter(p => resolveSoftCluster(p) !== cluster)
          try {
            const res = await api.getArbOpps([rep], counters, 10)
            const opps = ((res?.opportunities ?? []) as any[])
              .sort((a, b) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0))
            return [cluster, opps] as const
          } catch {
            return [cluster, [] as any[]] as const
          }
        })
      )
      setOppsByCluster(Object.fromEntries(results))
    } finally {
      setArbLoading(false)
    }
  }, [providerBalances])

  useEffect(() => {
    loadArbOpps()
    const id = setInterval(loadArbOpps, 30_000)
    return () => clearInterval(id)
  }, [loadArbOpps])

  // Continuously poll login state for every UNLIMITED provider — whenever one is
  // logged in with a positive balance AND the batch has positive-edge bets, auto-
  // activate (start the play loop). Green = logged in + runner active. Manual
  // Place/Skip still gates each bet.
  useEffect(() => {
    let cancelled = false
    const missCount: Record<string, number> = {}
    const activated: Set<string> = new Set()
    const check = async () => {
      for (const pid of UNLIMITED_PROVIDERS) {
        try {
          const r = await fetch(`/mirror/browser/provider/${pid}`)
          const d = await r.json()
          if (cancelled) return
          if (d.logged_in) {
            missCount[pid] = 0
            setLoggedInProviders(prev => prev.has(pid) ? prev : new Set(prev).add(pid))
            // Auto-activate once per page lifetime; use this effect's own `activated`
            // set rather than activeProviders so we avoid React state-closure races.
            if (!activated.has(pid) && (d.balance ?? 0) > 0) {
              // Fetch fresh batch + balances and start the loop directly — bypasses
              // startSkin which reads `bets` from a potentially stale render closure.
              try {
                const bResp = await fetch('/api/opportunities/play/batch', {
                  method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
                })
                const bData = await bResp.json()
                const full: any[] = bData.batch || []
                const myBets = full.filter((b: any) => b.provider_id === pid && (b.edge_pct ?? 0) > 0)
                if (myBets.length === 0) continue
                activated.add(pid)
                setActiveProviders(prev => prev.has(pid) ? prev : new Set(prev).add(pid))
                try { await api.startMirror() } catch {}
                try { await api.openTab(pid) } catch {}
                setLoopRunning(true)
                await api.startPlayLoop(myBets, bData.provider_balances || {}, [pid])
              } catch { /* retry next tick */ }
            }
          } else {
            missCount[pid] = (missCount[pid] || 0) + 1
            if (missCount[pid] >= 2) {
              setLoggedInProviders(prev => {
                if (!prev.has(pid)) return prev
                const n = new Set(prev); n.delete(pid); return n
              })
              activated.delete(pid)
            }
          }
        } catch { /* swallow — retry next tick */ }
      }
    }
    check()
    const id = setInterval(check, 5000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Poll login state for soft providers we know about (have balance or pending).
  // SSE login_detected/balance_intercepted only fires when events happen; this
  // effect recovers green-state after a page reload or when SSE missed events.
  // No auto-activation — soft sessions need explicit user Start due to daily caps.
  useEffect(() => {
    const softPids = new Set<string>()
    for (const pid of Object.keys(providerBalances)) {
      if (!UNLIMITED_PROVIDERS.has(pid)) softPids.add(pid)
    }
    for (const pid of Object.keys(pendingByProvider)) {
      if (!UNLIMITED_PROVIDERS.has(pid)) softPids.add(pid)
    }
    if (softPids.size === 0) return
    let cancelled = false
    const missCount: Record<string, number> = {}
    const check = async () => {
      for (const pid of softPids) {
        try {
          const r = await fetch(`/mirror/browser/provider/${pid}`)
          const d = await r.json()
          if (cancelled) return
          if (d.logged_in) {
            missCount[pid] = 0
            setLoggedInProviders(prev => prev.has(pid) ? prev : new Set(prev).add(pid))
          } else {
            missCount[pid] = (missCount[pid] || 0) + 1
            if (missCount[pid] >= 2) {
              setLoggedInProviders(prev => {
                if (!prev.has(pid)) return prev
                const n = new Set(prev); n.delete(pid); return n
              })
            }
          }
        } catch { /* swallow */ }
      }
    }
    check()
    const id = setInterval(check, 5000)
    return () => { cancelled = true; clearInterval(id) }
  }, [providerBalances, pendingByProvider])

  // SSE event handler
  useEffect(() => {
    if (!mirror.lastEvent) return
    const { type, data } = mirror.lastEvent
    if (type === 'login_waiting') setLoopStatus(`Waiting for login on ${data.provider_id}... (${Math.round(data.elapsed)}/${data.timeout}s)`)
    if (type === 'login_detected') {
      setLoopStatus(`Logged in to ${data.provider_id}`)
      setLoggedInProviders(prev => new Set(prev).add(data.provider_id))
    }
    if (type === 'balance_intercepted' && data.provider_id && data.balance != null && data.balance >= 0) {
      setLoggedInProviders(prev => new Set(prev).add(data.provider_id))
    }
    if (type === 'login_required') {
      setLoggedInProviders(prev => {
        const next = new Set(prev)
        next.delete(data.provider_id)
        return next
      })
    }
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
    if (type === 'bet_skipped' || type === 'bet_failed') { setCurrentBetReady(null); setLoopStatus(null); setArbCounterPlan(null); setArbProfitPct(null); setArbGroupId(null); setArbHedgeStatus({}) }
    if (type === 'arb_bet_ready') {
      const bet = data.bet ?? data
      setCurrentBetReady({ ...bet, prep_ok: data.prep_ok, live_odds: data.live_odds, live_edge: data.live_edge })
      setArbCounterPlan(data.counter_plan ?? null)
      setArbProfitPct(data.guaranteed_profit_pct ?? null)
      setArbGroupId(data.arb_group_id ?? null)
      setArbHedgeStatus({})
      setLoopStatus(null)
    }
    if (type === 'arb_legs_loaded') {
      setArbGroupId(data.arb_group_id ?? null)
      setArbCounterPlan(data.legs ?? null)
      setArbHedgeStatus({})
      setArbProfitPct(null)
      setLoopStatus(`Arb legs loaded — streaming odds`)
    }
    if (type === 'arb_alignment') {
      setArbProfitPct(data.profit_pct)
      // Live legs update — store per-leg current odds/stake/state
      setArbCounterPlan(data.legs ?? null)
    }
    if (type === 'arb_anchor_placed') {
      setLoopStatus(`Anchor placed @ ${data.actual_stake} on ${data.provider_id} — confirm hedges in mirror`)
    }
    if (type === 'arb_anchor_rejected') {
      setLoopStatus(`Anchor REJECTED on ${data.provider_id}: ${data.reason} — trying next opp`)
      setArbHedgeStatus({})
    }
    if (type === 'arb_hedge_placing') {
      setArbHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'placing', counter_provider: data.counter_provider, outcome: data.outcome } }))
    }
    if (type === 'arb_hedge_placed') {
      setArbHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'placed', counter_provider: data.counter_provider, outcome: data.outcome, actual_odds: data.actual_odds, actual_stake: data.actual_stake } }))
    }
    if (type === 'arb_hedge_failed') {
      setArbHedgeStatus(prev => ({ ...prev, [data.counter_provider]: { status: 'failed', counter_provider: data.counter_provider, outcome: data.outcome, reason: data.reason } }))
    }
    if (type === 'arb_unhedged') {
      setArbHedgeStatus(prev => ({ ...prev, __unhedged: { status: 'unhedged', outcome: data.outcome, reason: 'All fallbacks exhausted' } }))
    }
    if (type === 'arb_complete') {
      setArbProfitPct(data.guaranteed_profit_pct ?? arbProfitPct)
      setTimeout(() => { setArbCounterPlan(null); setArbProfitPct(null); setArbGroupId(null); setArbHedgeStatus({}); setCurrentBetReady(null) }, 5000)
      loadArbOpps()
    }
    if (type === 'bet_reconciled') {
      const id = `recon-${data.bet_id}-${Date.now()}`
      setReconcileToasts(prev => {
        // De-dupe: replace any prior toast for this bet_id (re-reconcile updates same row)
        const filtered = prev.filter(t => t.bet_id !== data.bet_id)
        return [...filtered, {
          id,
          provider_id: data.provider_id,
          bet_id: data.bet_id,
          event_name: data.event_name,
          match_method: data.match_method,
          confidence: data.confidence,
          changes: data.changes,
        }]
      })
      load()  // refresh batch + pending so UI sees the updated bet
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
      setArbCounterPlan(null)
      setArbProfitPct(null)
      setArbGroupId(null)
      setArbHedgeStatus({})
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
    return { providers: [...providers], ev, totalBal, pending, betCount: cb.length }
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
      {/* Sub-tab bar — top level, directly under main nav */}
      <div className="flex items-center gap-1 px-3 py-1 bg-zinc-900/60 border-b border-zinc-800">
        <button
          onClick={() => setSubTab('value')}
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded transition-colors ${
            subTab === 'value'
              ? 'bg-amber-700/40 text-amber-200 border border-amber-600/50'
              : 'text-zinc-500 hover:text-zinc-200 border border-transparent'
          }`}
        >
          Value Bets
        </button>
        <button
          onClick={() => setSubTab('arb')}
          className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider rounded transition-colors ${
            subTab === 'arb'
              ? 'bg-purple-700/40 text-purple-200 border border-purple-600/50'
              : 'text-zinc-500 hover:text-zinc-200 border border-transparent'
          }`}
        >
          Arbitrage
        </button>
      </div>

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
          <span className="text-xs text-green-400 font-semibold">PLACED</span>
          <span className="text-xs text-zinc-200 truncate">
            {placementToast.bet?.display_home} v {placementToast.bet?.display_away}
          </span>
          <span className="text-xs text-green-400">{placementToast.bet?.outcome}</span>
          <span className="text-xs font-mono text-zinc-300">@ {placementToast.bet?.odds?.toFixed(2)}</span>
          <span className="text-xs font-mono text-zinc-400">{Math.round(placementToast.bet?.stake ?? 0)} kr</span>
        </div>
      )}

      {/* Reconcile toasts — persist until user dismisses */}
      {reconcileToasts.length > 0 && (
        <div className="fixed top-4 right-4 z-50 space-y-2 max-w-sm">
          {reconcileToasts.length > 1 && (
            <div className="flex justify-end">
              <button
                onClick={() => setReconcileToasts([])}
                className="text-[10px] px-2 py-0.5 bg-zinc-800/90 border border-zinc-600 rounded text-zinc-300 hover:bg-zinc-700"
              >
                dismiss all ({reconcileToasts.length})
              </button>
            </div>
          )}
          {reconcileToasts.map(t => (
            <div key={t.id} className="bg-blue-900/90 border border-blue-500 rounded p-2 text-xs shadow-lg">
              <div className="flex items-start justify-between gap-2">
                <div className="text-blue-200 font-semibold uppercase">
                  {t.provider_id} · reconciled ({t.match_method} · {t.confidence != null ? Math.round(t.confidence) : '—'})
                </div>
                <button
                  onClick={() => setReconcileToasts(prev => prev.filter(x => x.id !== t.id))}
                  className="text-zinc-400 hover:text-zinc-100 leading-none px-1 -mt-0.5"
                  aria-label="Dismiss"
                  title="Dismiss"
                >
                  ×
                </button>
              </div>
              <div className="text-zinc-200 mt-1 truncate">{t.event_name ?? `Bet #${t.bet_id}`}</div>
              <div className="text-zinc-400 mt-1 space-y-0.5">
                {Object.entries(t.changes).map(([k, v]) => (
                  <div key={k} className="font-mono">
                    {k}: <span className="text-amber-300">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Arb card */}
      {currentBetReady && arbCounterPlan && (
        <div className="border-b border-purple-700/50 bg-purple-900/10 px-3 py-2">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="px-1.5 py-0.5 text-[10px] font-bold bg-purple-900/50 text-purple-400 border border-purple-700/50 rounded">DUTCH ARB</span>
            {arbProfitPct != null && (
              <span className="text-xs font-mono font-semibold text-green-400">Live profit: +{arbProfitPct.toFixed(2)}%</span>
            )}
            {arbGroupId && <span className="text-[10px] text-zinc-600 ml-auto font-mono">{arbGroupId}</span>}
          </div>
          <div className="space-y-0.5">
            {(arbCounterPlan as any[]).map((leg: any) => {
              const hedge = arbHedgeStatus[leg.provider_id]
              return (
                <div key={leg.provider_id} className="flex items-center gap-2 text-[10px]">
                  <span className="text-zinc-400 uppercase w-20">{leg.provider_id}</span>
                  <span className="font-mono text-zinc-300">@ {(leg.current_odds ?? leg.planned_odds ?? leg.odds)?.toFixed?.(2) ?? '—'}</span>
                  <span className="font-mono text-zinc-500">{(leg.current_stake ?? leg.planned_stake)?.toFixed?.(2) ?? '—'} SEK</span>
                  <span className="text-zinc-600">{leg.slip_state ?? '—'}</span>
                  {hedge?.status === 'placing' && <span className="text-amber-400 animate-pulse">Placing...</span>}
                  {hedge?.status === 'placed' && (
                    <span className="text-green-400 font-semibold">
                      HEDGED @ {hedge.actual_odds?.toFixed(2)} · {Math.round(hedge.actual_stake ?? 0)} kr
                    </span>
                  )}
                  {hedge?.status === 'failed' && <span className="text-red-400">Failed: {hedge.reason}</span>}
                </div>
              )
            })}
            {arbHedgeStatus.__unhedged && (
              <div className="flex items-center gap-2 pl-3 text-[10px] mt-1">
                <span className="text-red-400 font-semibold">UNHEDGED — all fallbacks exhausted</span>
              </div>
            )}
          </div>
          <div className="text-[10px] text-zinc-500 mt-1.5">
            {loopStatus || 'Waiting — click Place inside each mirror tab when ready'}
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
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => api.placeCurrent()}
                    className="px-2.5 py-0.5 text-[10px] font-semibold rounded bg-green-700 hover:bg-green-600 text-white transition-colors"
                  >Place</button>
                  <button
                    onClick={() => api.skipCurrent(pid)}
                    className="text-[10px] text-zinc-500 hover:text-zinc-300"
                  >Skip</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        {/* SECTION A — Per-cluster Arb Opportunities (soft books, arb-only) */}
        {subTab === 'arb' && (() => {
          // Build the soft-cluster universe: canonical siblings + standalones +
          // any provider we have current balance/pending data for. Then filter
          // to only clusters with at least one funded member, OR (drained but)
          // at least one arb opp clearing DEPOSIT_HINT_MIN_PROFIT_PCT.
          const softByCluster: Record<string, string[]> = {}
          for (const [cluster, members] of Object.entries(SOFT_CLUSTER_MEMBERS)) {
            softByCluster[cluster] = [...members]
          }
          for (const pid of SOFT_STANDALONES) {
            if (!softByCluster[pid]) softByCluster[pid] = [pid]
          }
          const allKnownPids = new Set([
            ...Object.keys(providerBalances),
            ...Object.keys(pendingByProvider),
          ])
          for (const pid of allKnownPids) {
            if (UNLIMITED_PROVIDERS.has(pid)) continue
            const cluster = resolveSoftCluster(pid)
            if (!softByCluster[cluster]) softByCluster[cluster] = []
            if (!softByCluster[cluster].includes(pid)) softByCluster[cluster].push(pid)
          }

          // Funded check (used by both visibility filter and per-cluster render)
          const isFunded = (pid: string) =>
            (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
            (pendingByProvider[pid]?.length ?? 0) > 0

          // Visibility: cluster shows if any member is funded, OR if the cluster
          // has a qualifying arb opp (>= DEPOSIT_HINT_MIN_PROFIT_PCT). Drained
          // clusters with no qualifying arb are hidden entirely.
          const clusterHasFunded = (cluster: string) =>
            (softByCluster[cluster] ?? []).some(isFunded)
          const clusterHasQualifyingArb = (cluster: string) =>
            (oppsByCluster[cluster] ?? []).some(
              (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
            )
          const visibleClusters = Object.keys(softByCluster).filter(
            c => clusterHasFunded(c) || clusterHasQualifyingArb(c),
          )

          // Stable sort: named clusters first, then standalones alphabetically
          const namedClusters = Object.keys(SOFT_CLUSTER_MEMBERS)
          const clusterOrder = visibleClusters.sort((a, b) => {
            const ai = namedClusters.indexOf(a)
            const bi = namedClusters.indexOf(b)
            if (ai >= 0 && bi >= 0) return ai - bi
            if (ai >= 0) return -1
            if (bi >= 0) return 1
            return a.localeCompare(b)
          })
          const totalOpps = Object.values(oppsByCluster).reduce((n, arr) => n + arr.length, 0)

          return (
            <div className="border-b border-zinc-800 pb-2 mb-2">
              <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800">
                <h3 className="text-[10px] font-bold text-purple-400 uppercase tracking-wider">
                  Arb Opportunities
                </h3>
                <span className="text-[10px] text-zinc-500 font-mono">{totalOpps}</span>
                {arbLoading && <span className="text-[10px] text-zinc-600">loading…</span>}
                <span className="text-[10px] text-zinc-600 ml-auto">
                  top 10 per cluster · siblings share odds · drained excluded
                </span>
              </div>

              {clusterOrder.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-zinc-600">
                  No soft books configured.
                </div>
              ) : (
                <div className="flex flex-col">
                  {clusterOrder.map(cluster => {
                    const members = softByCluster[cluster]
                    const funded = members.filter(isFunded)
                    const opps = oppsByCluster[cluster] ?? []
                    const clusterMemberSet = new Set(members)

                    // Deposit-hint mode: cluster has zero funded members but a
                    // qualifying arb survived the visibility filter. Render the
                    // cluster header + the qualifying arb rows only (no provider
                    // cards, no Place/Skip — user must deposit first).
                    if (funded.length === 0) {
                      const qualifyingOpps = opps.filter(
                        (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
                      ).slice(0, 10)
                      return (
                        <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                          <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                            <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                              {cluster}
                            </span>
                            <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-900/30 text-amber-400 border border-amber-700/40 uppercase tracking-wider">
                              deposit to play
                            </span>
                            <span className="text-[10px] text-zinc-600 ml-auto">
                              {qualifyingOpps.length} qualifying arb{qualifyingOpps.length === 1 ? '' : 's'} ≥ {DEPOSIT_HINT_MIN_PROFIT_PCT}%
                            </span>
                          </div>
                          <table className="w-full text-xs">
                            <tbody>
                              {qualifyingOpps.map((opp: any, i: number) => {
                                const counterLegs = opp.counter_plan ?? opp.counter_legs ?? opp.legs ?? []
                                const profitPct = opp.guaranteed_profit_pct ?? 0
                                const eventLabel = opp.display_home && opp.display_away
                                  ? `${opp.display_home} v ${opp.display_away}`
                                  : opp.event_id
                                const resolveLegOutcome = (leg: any): string => {
                                  const o = leg?.outcome
                                  if (!o) return '—'
                                  if (o === 'home') return opp.display_home || 'Home'
                                  if (o === 'away') return opp.display_away || 'Away'
                                  if (o === 'draw') return 'Draw'
                                  if (o === 'over' && leg.point != null) return `Over ${leg.point}`
                                  if (o === 'under' && leg.point != null) return `Under ${leg.point}`
                                  if (leg.point != null) return `${o} ${leg.point}`
                                  return o
                                }
                                const anchorLeg =
                                  (opp.legs ?? []).find((l: any) =>
                                    clusterMemberSet.has(l.provider ?? l.provider_id ?? ''),
                                  ) ?? {}
                                const anchorPid = anchorLeg.provider ?? anchorLeg.provider_id ?? cluster
                                const anchorOutcome = resolveLegOutcome(anchorLeg)
                                const counters = (counterLegs as any[]).filter((l: any) => {
                                  const lp = l.provider ?? l.provider_id ?? ''
                                  return !clusterMemberSet.has(lp)
                                })
                                return (
                                  <tr key={`hint-${cluster}-${i}`} className="border-b border-zinc-800/20 hover:bg-zinc-800/40">
                                    <td className="pl-9 pr-2 py-1 font-mono font-semibold text-right w-[60px] text-green-400">
                                      +{profitPct.toFixed(2)}%
                                    </td>
                                    <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate text-[11px]">{eventLabel}</td>
                                    <td className="px-2 py-1 text-zinc-500 text-[10px] uppercase">{opp.market ?? ''}</td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">bet</span>
                                      <span className="text-green-400 font-semibold">{anchorOutcome}</span>
                                      <span className="text-zinc-600 mx-1">on</span>
                                      <span className="text-zinc-400 uppercase text-[10px]">{anchorPid}</span>
                                      <span className="font-mono text-zinc-200 ml-2">@ {Number(anchorLeg.odds ?? 0).toFixed(2)}</span>
                                    </td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <div className="flex flex-col gap-0.5">
                                        {counters.map((leg: any, li: number) => (
                                          <div key={li} className="flex items-center gap-1">
                                            <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">hedge</span>
                                            <span className="text-pink-400 font-semibold">{resolveLegOutcome(leg)}</span>
                                            <span className="text-zinc-600">on</span>
                                            <span className="text-zinc-400 uppercase text-[10px]">{leg.provider ?? leg.provider_id}</span>
                                            <span className="font-mono text-zinc-300 ml-2">@ {Number(leg.odds ?? 0).toFixed(2)}</span>
                                          </div>
                                        ))}
                                      </div>
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                      )
                    }

                    return (
                      <div key={cluster} className="border-b border-zinc-800/50 last:border-b-0">
                        {/* Cluster header — funded mode */}
                        <div className="flex items-center gap-2 px-3 py-1 bg-zinc-900/40 border-b border-zinc-800/50 flex-wrap">
                          <span className="text-[10px] font-bold text-purple-300 uppercase tracking-wider">
                            {cluster}
                          </span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {opps.length} arb{opps.length === 1 ? '' : 's'} · siblings share odds
                          </span>
                        </div>

                        {/* One card per funded sibling — same opps, different balance/active context */}
                        {funded.map(pid => {
                          const bal = providerBalances[pid] ?? 0
                          const pending = pendingByProvider[pid]?.length ?? 0
                          const isSkinActive = activeProviders.has(pid)
                          const isLoggedIn = loggedInProviders.has(pid)
                          return (
                            <div key={pid} className="border-b border-zinc-800/30 last:border-b-0">
                              {/* Provider header — activate button + state */}
                              <div className="flex items-center gap-2 px-6 py-1.5 bg-zinc-900/20">
                                <button
                                  onClick={() => startSkin(pid)}
                                  className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                                    isLoggedIn
                                      ? 'bg-green-700/50 text-green-200 border border-green-600/50'
                                      : isSkinActive
                                        ? 'bg-purple-700/50 text-purple-200 border border-purple-600/50'
                                        : 'text-zinc-300 hover:bg-zinc-700/50 border border-zinc-700/50 cursor-pointer'
                                  }`}
                                >
                                  <span className="uppercase font-semibold">{pid}</span>
                                  <span className="ml-1 text-green-400 font-mono">{bal.toFixed(2)} kr</span>
                                </button>
                                {pending > 0 && <span className="text-[10px] text-amber-400">{pending}p pending</span>}
                                {stakeCaps[pid] && (
                                  <span className="px-1 py-px text-[8px] font-bold bg-orange-900/50 text-orange-400 border border-orange-700/50 rounded">
                                    ≤{Math.round(stakeCaps[pid])}
                                  </span>
                                )}
                              </div>

                              {/* Per-provider pending list */}
                              {(() => {
                                const providerPending = pendingByProvider[pid] ?? []
                                if (providerPending.length === 0) return null
                                const providerSettled = providerPending.filter((p: any) => detectedSettlements[p.bet_id ?? p.id])
                                const providerPnl = providerSettled.reduce((s: number, p: any) => {
                                  const det = detectedSettlements[p.bet_id ?? p.id]
                                  return s + ((det?.payout ?? 0) - (p.stake ?? 0))
                                }, 0)
                                return (
                                  <div className="bg-amber-900/5 border-b border-zinc-800/40">
                                    <div className="flex items-center gap-2 px-6 py-0.5 border-b border-zinc-800/30">
                                      <span className="text-[10px] text-amber-500 uppercase font-medium">Pending</span>
                                      <span className="text-[10px] text-zinc-600">
                                        {providerPending.length} bets · {Math.round(providerPending.reduce((s: number, p: any) => s + (p.stake ?? 0), 0))} kr
                                      </span>
                                      {providerSettled.length > 0 && (
                                        <>
                                          <span className={`text-[10px] font-mono font-semibold ${providerPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                            {providerPnl >= 0 ? '+' : ''}{Math.round(providerPnl)} kr
                                          </span>
                                          <button onClick={handleConfirmSettlements}
                                            className="ml-auto px-2 py-0.5 text-[10px] font-semibold bg-green-900/40 text-green-400 border border-green-700/50 rounded hover:bg-green-900/60 transition-colors">
                                            Confirm {providerSettled.length}
                                          </button>
                                        </>
                                      )}
                                    </div>
                                    {providerPending.map((p: any) => {
                                      const betId = p.bet_id ?? p.id
                                      const det = detectedSettlements[betId]
                                      const eventLabel = p.home_team && p.away_team
                                        ? `${p.home_team} v ${p.away_team}`
                                        : p.event_id?.split(':').slice(1, 3).join(' v ') ?? p.event_id
                                      const profit = det ? (det.payout - (p.stake ?? 0)) : 0
                                      return (
                                        <div key={`pending-${p.id}`} className={`flex items-center gap-2 px-6 pl-9 py-0.5 border-b border-zinc-800/20 text-xs ${
                                          det ? (det.result === 'won' ? 'bg-green-900/10' : det.result === 'lost' ? 'bg-red-900/10' : 'bg-zinc-800/20') : ''
                                        }`}>
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

                              {/* Arb table — same opps for all siblings, anchor shown as THIS provider */}
                              {opps.length === 0 ? (
                                <div className="px-9 py-2 text-[10px] text-zinc-600">
                                  {arbLoading ? 'Scanning…' : 'No arbs right now.'}
                                </div>
                              ) : (
                                <table className="w-full text-xs">
                                  <tbody>
                                    {opps.map((opp: any, i: number) => {
                                      const counterLegs = opp.counter_plan ?? opp.counter_legs ?? opp.legs ?? []
                                      const profitPct = opp.guaranteed_profit_pct ?? 0
                                      const eventLabel = opp.display_home && opp.display_away
                                        ? `${opp.display_home} v ${opp.display_away}`
                                        : opp.event_id
                                      // Resolve a leg's outcome code ('home'/'away'/'draw'/'over'/'under') to a
                                      // bettor-readable label using the event's team names + market point.
                                      const resolveOutcome = (leg: any): string => {
                                        const o = leg?.outcome
                                        if (!o) return '—'
                                        if (o === 'home') return opp.display_home || 'Home'
                                        if (o === 'away') return opp.display_away || 'Away'
                                        if (o === 'draw') return 'Draw'
                                        if (o === 'over' && leg.point != null) return `Over ${leg.point}`
                                        if (o === 'under' && leg.point != null) return `Under ${leg.point}`
                                        if (leg.point != null) return `${o} ${leg.point}`
                                        return o
                                      }
                                      // Anchor: prefer the leg for THIS provider, fall back to any sibling
                                      const anchorLeg =
                                        (opp.legs ?? []).find((l: any) => (l.provider ?? l.provider_id) === pid) ??
                                        (opp.legs ?? []).find((l: any) =>
                                          clusterMemberSet.has(l.provider ?? l.provider_id ?? '')
                                        ) ?? {}
                                      const anchorOutcome = resolveOutcome(anchorLeg)
                                      const counters = (counterLegs as any[]).filter((l: any) => {
                                        const lp = l.provider ?? l.provider_id ?? ''
                                        return !clusterMemberSet.has(lp)
                                      })
                                      return (
                                        <tr key={`arb-${pid}-${i}`} className="border-b border-zinc-800/20 hover:bg-zinc-800/40">
                                          <td className={`pl-9 pr-2 py-1 font-mono font-semibold text-right w-[60px] ${profitPct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                            {profitPct >= 0 ? '+' : ''}{profitPct.toFixed(2)}%
                                          </td>
                                          <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate text-[11px]">{eventLabel}</td>
                                          <td className="px-2 py-1 text-zinc-500 text-[10px] uppercase">{opp.market ?? ''}</td>
                                          <td className="px-2 py-1 text-[11px]">
                                            <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">bet</span>
                                            <span className="text-green-400 font-semibold">{anchorOutcome}</span>
                                            <span className="text-zinc-600 mx-1">on</span>
                                            <span className="text-zinc-400 uppercase text-[10px]">{pid}</span>
                                            <span className="font-mono text-zinc-200 ml-2">@ {Number(anchorLeg.odds ?? 0).toFixed(2)}</span>
                                          </td>
                                          <td className="px-2 py-1 text-[11px]">
                                            <div className="flex flex-col gap-0.5">
                                              {counters.map((leg: any, li: number) => (
                                                <div key={li} className="flex items-center gap-1">
                                                  <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">hedge</span>
                                                  <span className="text-pink-400 font-semibold">{resolveOutcome(leg)}</span>
                                                  <span className="text-zinc-600">on</span>
                                                  <span className="text-zinc-400 uppercase text-[10px]">{leg.provider ?? leg.provider_id}</span>
                                                  <span className="font-mono text-zinc-300 ml-2">@ {Number(leg.odds ?? 0).toFixed(2)}</span>
                                                </div>
                                              ))}
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
                    )
                  })}
                </div>
              )}
            </div>
          )
        })()}

        {/* SECTION B — Value bets (unlimited providers only) */}
        {subTab === 'value' && <>
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
          const isLoggedIn = stats.providers.some(p => loggedInProviders.has(p))

          return (
            <div key={clusterId}>
              {/* Cluster header with skin tabs */}
              <div className={`flex items-center gap-2 px-3 py-1.5 border-b ${
                isLoggedIn
                  ? 'bg-green-900/20 border-green-700/50'
                  : isActive
                    ? 'bg-amber-900/20 border-amber-700/50'
                    : 'bg-zinc-900/50 border-zinc-800'
              }`}>
                <span className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider">{clusterId}</span>
                {/* Skin tabs — sorted by balance desc */}
                <div className="flex items-center gap-1">
                  {stats.providers.sort((a, b) => (providerBalances[b] ?? 0) - (providerBalances[a] ?? 0)).map(pid => {
                    const bal = providerBalances[pid] ?? 0
                    const pending = pendingByProvider[pid]?.length ?? 0
                    const isSkinActive = activeProviders.has(pid)
                    const isLoggedIn = loggedInProviders.has(pid)
                    const uncapped = ['pinnacle', 'polymarket', 'cloudbet', 'kalshi'].includes(pid)
                    const disabled = bal <= 0 && pending === 0 && !uncapped
                    return (
                      <button key={pid}
                        onClick={() => !disabled && startSkin(pid)}
                        disabled={disabled}
                        className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                          disabled
                            ? 'text-zinc-700 border border-zinc-800/30 cursor-not-allowed opacity-40'
                            : isLoggedIn
                              ? 'bg-green-700/50 text-green-200 border border-green-600/50'
                              : isSkinActive
                                ? 'bg-amber-700/50 text-amber-300 border border-amber-600/50'
                                : 'text-zinc-300 hover:bg-zinc-700/50 border border-zinc-700/50 cursor-pointer'
                        }`}
                      >
                        <span className="uppercase font-semibold">{pid}</span>
                        {bal > 0 && (
                          <span className="ml-1 text-green-400 font-mono">
                            {pid === 'polymarket' ? '$' : ''}{bal.toFixed(2)}{pid !== 'polymarket' ? ' kr' : ''}
                          </span>
                        )}
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
        </>}

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
