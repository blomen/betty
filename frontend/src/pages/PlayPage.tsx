import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api } from '../hooks/useApi'
import { useMirrorStream } from '../hooks/useMirrorStream'
import { useMirrorState } from '../hooks/useMirrorState'
import { migratedLocalStorageGet } from '../utils/localStorageMigration'

// Unlimited providers — value-bet flow (Section B) + arb counter pool. All other providers route through arbitrage (Section A).
// `rainbet` is intentionally excluded: KYC blocks Swedish residents (Sumsub
// flow doesn't list Sweden as an issuing country), so it's not playable.
// Server-side extraction still runs and its odds participate in fair-odds
// consensus for the scanner — but the local UI doesn't show it as a target.
const UNLIMITED_PROVIDERS = new Set(['pinnacle', 'polymarket', 'cloudbet', 'kalshi'])

// Provider is "drained" when balance falls below this threshold (SEK).
// Below this, no meaningful bet can be placed after odds rounding and
// provider-side minimum stakes (typically 5-10 kr), so the residue is
// not actionable.
const DRAIN_THRESHOLD_SEK = 10

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
  'vbet', '10bet', 'tipwin', 'coolbet', 'bethard',
]
// Active soft anchors are now dynamic — any soft provider that has either a
// balance above DRAIN_THRESHOLD_SEK, an unclaimed bonus, or pending bets
// renders as a row in the arb section. Replaces the prior `SOFT_FOCUS` static
// allowlist that only included betinia. See `isQualifiedSoft` helper inside
// the component for the predicate.
const PROVIDER_TO_SOFT_CLUSTER: Record<string, string> = {}
for (const [c, members] of Object.entries(SOFT_CLUSTER_MEMBERS)) {
  for (const m of members) PROVIDER_TO_SOFT_CLUSTER[m] = c
}
// Resolve a soft provider's cluster (fallback to pid for standalones).
const resolveSoftCluster = (pid: string): string => PROVIDER_TO_SOFT_CLUSTER[pid] ?? pid

// Card states are derived from observable truth (tab + login presence in the
// mirror) — NOT from the runner's internal FSM. Three states only:
//   no_tab            — no provider tab in Chromium → click opens it
//   tab_open_not_in   — tab exists but the user hasn't logged in → red badge
//   tab_open_logged   — tab exists + login detected → green badge
// Runner status (running / standby / etc.) is shown separately in the
// status row below the card, not encoded in the card color.
type CardState = 'no_tab' | 'tab_open_not_in' | 'tab_open_logged'

function deriveCardState(tabOpen: boolean, loggedIn: boolean): CardState {
  if (!tabOpen) return 'no_tab'
  if (!loggedIn) return 'tab_open_not_in'
  return 'tab_open_logged'
}

const CARD_STATE_CLASSES: Record<CardState, string> = {
  no_tab: 'text-zinc-300 hover:bg-zinc-700/50 border border-zinc-700/50',
  tab_open_not_in: 'bg-red-500/45 text-red-100 border border-red-400/70',
  tab_open_logged: 'bg-emerald-600/50 text-emerald-100 border border-emerald-500/70',
}

type ProviderBalanceInfo = {
  balance: number
  bonus_trigger?: number
  bonus_currency?: string
  // Native-currency balance (USDC for polymarket, SEK for everyone else).
  // Stored separately from `balance` because `balance` is normalised to SEK
  // for cross-provider sorting/comparison — the cluster header needs the
  // native-currency value to render correctly (e.g. "$107.35" not "$1127.17").
  balance_native?: number
  currency?: string
}
type ProviderBalanceLike = number | ProviderBalanceInfo
const getBalance = (b: ProviderBalanceLike | undefined): number =>
  typeof b === 'number' ? b : (b?.balance ?? 0)
const getTrigger = (b: ProviderBalanceLike | undefined): { amount: number; currency: string } | null => {
  if (b == null || typeof b === 'number') return null
  return b.bonus_trigger != null && b.bonus_trigger > 0
    ? { amount: b.bonus_trigger, currency: b.bonus_currency ?? 'SEK' }
    : null
}
// Providers whose balances + slip stakes are denominated in USD (USDC for
// polymarket, USD for kalshi). Everything else is SEK. The /api/bankroll
// `balance` field is normalised to SEK already (see PlayPage load()), so we
// compute stakes in SEK and convert just for native-currency display.
const USD_PROVIDERS = new Set(['polymarket', 'kalshi'])
const SEK_PER_USD = 10.5

// Providers that display prices in native cents (Polymarket: $0.62 ↔ 62¢,
// Kalshi: 62¢ buy). The decimal odds we store are derived from that native
// price (price = 1 / decimal_odds). Surface the native cents alongside the
// decimal so the user can compare what the bookmaker tab actually shows.
const CENT_PRICE_PROVIDERS = new Set(['polymarket', 'kalshi'])

const formatLegStake = (pid: string, sek: number): string => {
  if (sek <= 0) return ''
  if (USD_PROVIDERS.has(pid)) return `$${(sek / SEK_PER_USD).toFixed(1)}`
  return `${Math.round(sek)} kr`
}

// Build the odds display string. For decimal-only providers it's just "@ 1.61".
// For cent-priced providers we append the native cents the site displays, so
// the user can sanity-check against the bookmaker tab: "@ 1.61 (62¢)".
const formatOddsDisplay = (pid: string, odds: number): string => {
  if (!isFinite(odds) || odds <= 1) return `@ ${odds.toFixed(2)}`
  const base = `@ ${odds.toFixed(2)}`
  if (CENT_PRICE_PROVIDERS.has(pid)) {
    const cents = Math.round(100 / odds)
    return `${base} (${cents}¢)`
  }
  return base
}

// Compute planned arb stakes in SEK. Mirrors arb_runner.py drain strategy:
// anchor stake = full anchor balance (capped only by stakeCap if the runner
// has learned a site-max from a previous limit response). Counter stakes are
// sized so each leg pays the same on win — sharp side has unlimited liquidity
// (pinnacle/cloudbet) or auto-funds (polymarket/kalshi), so we DON'T cap the
// soft side by counter balance — that would shrink the soft stake unnecessarily.
function computeArbStakes(
  anchorPid: string,
  anchorOdds: number,
  counters: any[],
  balances: Record<string, ProviderBalanceLike>,
  stakeCaps: Record<string, number>,
  overridePayout?: number,
): { anchorSek: number; counterSekByLeg: number[]; payout: number } {
  // Payout-driven math: ALL leg stakes derive from a single target payout K.
  //   stake_i = K / odds_i
  // Default K = balance × anchor_odds (max-drain on the soft anchor). When
  // the user edits ANY leg's stake input, the input value × that leg's odds
  // becomes the new K, and every other leg's stake auto-recomputes from K /
  // its odds. Keeps the arb invariant (equal payout regardless of outcome)
  // symmetric — input on soft side, sharp side, or any specific hedge leg
  // works the same way.
  const balance = getBalance(balances[anchorPid])
  let anchorStake = balance
  const cap = stakeCaps[anchorPid]
  if (cap && cap > 0) anchorStake = Math.min(anchorStake, cap)
  if (anchorStake < 0 || !isFinite(anchorStake)) anchorStake = 0
  let totalPayout = anchorStake * (anchorOdds || 0)
  if (overridePayout != null && isFinite(overridePayout) && overridePayout > 0) {
    totalPayout = overridePayout
    if (anchorOdds > 0) {
      anchorStake = Math.min(totalPayout / anchorOdds, balance)
      // After clamp to balance, the payout we can ACTUALLY achieve drops to
      // anchorStake × anchorOdds. Recompute so the counter stakes match the
      // realisable payout, not the requested one. Otherwise the displayed
      // counter stakes would be larger than what the anchor's payout can
      // actually cover, breaking the equal-payout invariant.
      totalPayout = anchorStake * anchorOdds
    }
  }
  // Index per LEG, not per provider — 3-way arbs often have multiple legs on
  // the same sportsbook (e.g. PINNACLE home + PINNACLE away in a 1x2 with
  // BETINIA on draw). Keying by provider_id collides them and overwrites the
  // first leg's stake with the second.
  const counterSekByLeg: number[] = counters.map((leg: any) => {
    const codds = Number(leg.odds ?? 0)
    return codds > 0 ? totalPayout / codds : 0
  })
  return { anchorSek: anchorStake, counterSekByLeg, payout: totalPayout }
}

function BalanceCell({ pid, balances }: { pid: string; balances: Record<string, ProviderBalanceLike> }) {
  const balance = getBalance(balances[pid])
  const trigger = getTrigger(balances[pid])
  return (
    <span>
      <span>{balance.toFixed(2)} kr</span>
      {trigger && balance < 1 && (
        <span className="ml-2 text-xs text-orange-400/80" title="Deposit to unlock provider bonus">
          · deposit {trigger.amount.toFixed(0)} {trigger.currency.toLowerCase()}
        </span>
      )}
    </span>
  )
}

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
  // Provider-specific routing data (event_slug, matchup_id, token_id, etc.).
  // Needed for navigate-to-event when the URL template requires a slug.
  provider_meta?: Record<string, unknown>
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

type ArbLeg = {
  provider_id: string
  current_odds: number
  planned_odds: number
  drift_pct: number
  current_stake: number
  slip_state: 'loading' | 'green' | 'red'
  placed?: boolean
  failed_reason?: string
}

export default function PlayPage() {
  const [batch, setBatch] = useState<BatchBet[]>([])
  const [summary, setSummary] = useState<any>(null)
  const [providerBalances, setProviderBalances] = useState<Record<string, ProviderBalanceLike>>({})
  const [pendingByProvider, setPendingByProvider] = useState<Record<string, any[]>>({})
  const [placedToday, setPlacedToday] = useState<Record<string, number>>({})
  const [ttkFilter, setTtkFilter] = useState<number>(168)
  const [error, setError] = useState<string | null>(null)
  // Mirror error into a ref so the polling tick can read the latest value
  // without re-creating the effect (avoids restarting the timer chain on
  // every state flip).
  const errorRef = useRef<string | null>(null)
  useEffect(() => { errorRef.current = error }, [error])
  const mirror = useMirrorStream()
  // Phase 2 platform-rebuild recovery floor: read authoritative provider/runner
  // state from server DB. Survives SSE drops, browser refresh, betty.bat
  // restart. Merged into loopProviderStatus by the effect below — SSE still
  // drives sub-5s updates between the 5s polls.
  const mirrorState = useMirrorState()
  const [loopRunning, setLoopRunning] = useState(false)
  const [currentBetReady, setCurrentBetReady] = useState<any>(null)
  const [toasts, setToasts] = useState<SettleToast[]>([])
  const [confirmedSettlements, setConfirmedSettlements] = useState<any[]>([])
  const [settleWaiting, setSettleWaiting] = useState(false)
  const [activeProviders, setActiveProviders] = useState<Set<string>>(new Set())
  const [loggedInProviders, setLoggedInProviders] = useState<Set<string>>(new Set())
  // Mirror has the provider's tab open but workflow.check_login hasn't passed yet —
  // amber state ("waiting for you to log in"). Distinct from isSkinActive (runner active).
  const [tabOpenProviders, setTabOpenProviders] = useState<Set<string>>(new Set())
  const [loopStatus, setLoopStatus] = useState<string | null>(null)
  const [loopProviderStatus, setLoopProviderStatus] = useState<Record<string, any> | null>(null)
  // Per-provider status line — rendered under the provider's own row instead
  // of in the global header banner. Provider-scoped SSE events (login_waiting,
  // settling_pending, provider_skipped, bet_navigating, etc.) write here
  // keyed by data.provider_id so each provider only sees its own status.
  const [providerStatus, setProviderStatus] = useState<Record<string, string | null>>({})
  const setProviderStatusFor = useCallback((pid: string | undefined | null, msg: string | null) => {
    if (!pid) return
    setProviderStatus(prev => {
      if (prev[pid] === msg) return prev
      return { ...prev, [pid]: msg }
    })
  }, [])
  const [placementToast, setPlacementToast] = useState<{ bet: any; count: number; cap: number } | null>(null)
  // Event IDs whose pages came back "Detta evenemang är avslutat" — filter
  // them out of arb rows so the runner doesn't re-pick a dead event.
  // Persist drained event_ids in localStorage with a 24h TTL — events
  // naturally roll over within hours, but a refresh shouldn't resurrect a
  // closed event we already auto-skipped past. TTL prevents the set from
  // growing unbounded over weeks of use. We track per-eid timestamps in a
  // ref so re-saves preserve the original drain time (otherwise every save
  // would reset the TTL clock to "now").
  const DRAINED_EVENTS_KEY = 'betty:drainedEventIds:v1'
  const DRAINED_EVENTS_KEY_LEGACY = 'arnold:drainedEventIds:v1'
  const DRAINED_EVENTS_TTL_MS = 24 * 60 * 60 * 1000
  const drainedTimestampsRef = useRef<Record<string, number>>({})
  const [drainedEventIds, setDrainedEventIds] = useState<Set<string>>(() => {
    try {
      const raw = migratedLocalStorageGet(DRAINED_EVENTS_KEY, DRAINED_EVENTS_KEY_LEGACY)
      if (!raw) return new Set()
      const parsed = JSON.parse(raw) as { eid: string; ts: number }[]
      const now = Date.now()
      const fresh = parsed.filter(e => now - e.ts < DRAINED_EVENTS_TTL_MS)
      const timestamps: Record<string, number> = {}
      for (const e of fresh) timestamps[e.eid] = e.ts
      drainedTimestampsRef.current = timestamps
      return new Set(fresh.map(e => e.eid))
    } catch {
      return new Set()
    }
  })
  useEffect(() => {
    try {
      const now = Date.now()
      const map = drainedTimestampsRef.current
      // Stamp newly-added eids; leave existing timestamps alone so TTL
      // measures from the ORIGINAL drain, not the latest re-save.
      for (const eid of drainedEventIds) {
        if (!(eid in map)) map[eid] = now
      }
      // Drop timestamps for eids no longer in the Set (defensive cleanup).
      for (const eid of Object.keys(map)) {
        if (!drainedEventIds.has(eid)) delete map[eid]
      }
      const payload = Array.from(drainedEventIds).map(eid => ({
        eid,
        ts: map[eid] ?? now,
      }))
      localStorage.setItem(DRAINED_EVENTS_KEY, JSON.stringify(payload))
    } catch { /* localStorage full / disabled */ }
  }, [drainedEventIds])
  // (event_id) → set of leg-keys (`${pid}|${outcome}|${point}`) for legs the
  // user is currently navigating. Drives the per-cell amber pulse. Keyed by
  // full leg identity (not just pid) because a 1X2 arb has 3 hedge legs all
  // on the same counter provider — keying by pid alone would pulse all three
  // when only one was clicked.
  const [pickingLegs, setPickingLegs] = useState<Record<string, Set<string>>>({})
  // Per-event manual stake override stored as TARGET PAYOUT (K). Each leg's
  // stake = K / leg_odds. When the user types into ANY leg's stake input,
  // we read the new value × that leg's odds = new K, and every other leg
  // auto-recomputes. Symmetric: input on soft, sharp, or any specific hedge
  // leg all drive the same payout math. Cleared when user hits ×.
  const [payoutOverridesByEid, setPayoutOverridesByEid] = useState<Record<string, number>>({})
  const setPayoutOverride = (eid: string, payout: number | null) => {
    setPayoutOverridesByEid(prev => {
      const next = { ...prev }
      if (payout == null || !isFinite(payout) || payout <= 0) delete next[eid]
      else next[eid] = payout
      return next
    })
  }
  // Helper: given a desired stake for a specific leg, compute the implied
  // payout (stake × leg_odds) and store it as the override. Used by the
  // per-leg stake inputs.
  const setStakeForLeg = (eid: string, legOdds: number, newStake: number) => {
    if (!isFinite(newStake) || newStake <= 0 || !isFinite(legOdds) || legOdds <= 0) {
      setPayoutOverride(eid, null)
      return
    }
    setPayoutOverride(eid, newStake * legOdds)
  }
  // (event_id) → set of leg-keys (same shape as pickingLegs) for legs that
  // have been synced (nav ok + not closed + odds streaming). When ALL legs
  // of an opp are in this set, the row is "all green" and ready to place.
  const [syncedLegs, setSyncedLegs] = useState<Record<string, Set<string>>>({})
  // Build a leg-identity key. Outcome + point uniquely identifies a leg
  // within (event_id, provider_id), so this is what the picking/synced
  // sets store.
  const legKey = (pid: string, outcome: string | null | undefined, point: number | null | undefined): string =>
    `${pid}|${outcome ?? ''}|${point ?? ''}`
  // Provider → most recently user-picked event_id. Drives the per-provider
  // arb widget below the cluster row, follows the row the user clicks.
  const [pickedEventByProvider, setPickedEventByProvider] = useState<Record<string, string>>({})

  // Live-odds override map: key = `eid|provider|outcome|point`, value = last
  // streamed odds + timestamp. Persisted to localStorage so refresh / API
  // re-scan doesn't snap back to stale 60-s-cached values. TTL is short on
  // purpose: an override older than a few minutes is almost certainly stale
  // vs the live market AND vs the next API scan, so applying it just
  // distorts edges (a 6h TTL caused -25% phantom arbs on Pol Martin Tiffon
  // 2026-05-12 — the API said -1% but the overlay overrode with values
  // polled from a previous session). applyOverrides re-checks `ts` so an
  // entry that survives the initial load filter still gets dropped once
  // it ages past the TTL during a session.
  // v3: bumped 2026-05-26 to invalidate any spread overrides that may have
  // been written during the intermediate fix (which still had the leg.point /
  // opp.point ambiguity) — the current fix passes opp.point directly.
  const LIVE_ODDS_KEY = 'betty:liveLegOdds:v3'
  const LIVE_ODDS_KEY_LEGACY = 'arnold:liveLegOdds:v1'
  const LIVE_ODDS_TTL_MS = 90 * 1000
  type LiveOddsEntry = { odds: number; ts: number }
  // Key for live-odds overrides. Resolves soft cluster so QUICKCASINO and
  // BETINIA collapse onto the same key — cluster siblings share odds (and
  // the scanner picks whichever has the best @ extraction time), so the
  // override stays correct regardless of which sibling provided the leg
  // emitted by the scanner. Unlimited providers (pinnacle/polymarket/etc.)
  // pass through unchanged because PROVIDER_TO_SOFT_CLUSTER doesn't map them.
  const legOddsKey = (eid: string, provider: string, outcome: string, point: number | null | undefined): string =>
    `${eid}|${PROVIDER_TO_SOFT_CLUSTER[provider] ?? provider}|${outcome ?? ''}|${point ?? ''}`
  // Helper: apply persisted live-leg overrides on top of an opps map and
  // recompute guaranteed_profit_pct from the merged odds. Used both at
  // hydration time (so the cached opps don't render with stale @41.00 when
  // the live override has @1.78) and from inside loadArbOpps after API fetch.
  const applyLiveOverridesToClusterMap = (
    map: Record<string, any[]>,
    overrides: Record<string, LiveOddsEntry>,
  ): Record<string, any[]> => {
    const out: Record<string, any[]> = {}
    const now = Date.now()
    for (const [cluster, opps] of Object.entries(map)) {
      out[cluster] = opps.map((o: any) => {
        const eid = o.event_id
        if (!eid) return o
        let touched = false
        const legs = (o.legs ?? []).map((l: any) => {
          const k = legOddsKey(eid, l.provider ?? l.provider_id ?? '', l.outcome, l.point)
          const ov = overrides[k]
          // Reject stale overlays — see applyOverrides note.
          if (ov && ov.odds > 0 && ov.odds !== l.odds && now - ov.ts < LIVE_ODDS_TTL_MS) {
            touched = true
            return { ...l, odds: ov.odds }
          }
          return l
        })
        if (!touched) return o
        let invSum = 0
        for (const l of legs) {
          const x = Number(l.odds ?? 0)
          if (x <= 0) { invSum = 0; break }
          invSum += 1 / x
        }
        const newProfit = invSum > 0 ? (1 / invSum - 1) * 100 : (o.guaranteed_profit_pct ?? 0)
        return { ...o, legs, guaranteed_profit_pct: newProfit, profit_pct: newProfit }
      })
      // Re-sort (guaranteed_profit_pct may have shifted)
      out[cluster] = [...out[cluster]].sort(
        (a, b) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0),
      )
    }
    return out
  }
  // Counter-pool filter: which UNLIMITED providers may be used as the hedge
  // leg against the soft anchor. Defaults to all four. Toggling chips in
  // the arb-page header narrows the search (e.g. play only BETINIA-vs-
  // Pinnacle arbs). Persisted to localStorage. Must always contain at
  // least one entry — UI enforces a minimum-of-one click rule.
  const COUNTER_FILTER_KEY = 'betty:counterPoolFilter:v1'
  const COUNTER_FILTER_KEY_LEGACY = 'arnold:counterPoolFilter:v1'
  const [enabledCounters, setEnabledCounters] = useState<Set<string>>(() => {
    try {
      const raw = migratedLocalStorageGet(COUNTER_FILTER_KEY, COUNTER_FILTER_KEY_LEGACY)
      if (!raw) return new Set(UNLIMITED_PROVIDERS)
      const arr = JSON.parse(raw) as string[]
      const valid = arr.filter(p => UNLIMITED_PROVIDERS.has(p))
      return valid.length > 0 ? new Set(valid) : new Set(UNLIMITED_PROVIDERS)
    } catch {
      return new Set(UNLIMITED_PROVIDERS)
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(COUNTER_FILTER_KEY, JSON.stringify([...enabledCounters]))
    } catch { /* full / disabled */ }
  }, [enabledCounters])
  const toggleCounter = (pid: string) => {
    setEnabledCounters(prev => {
      const next = new Set(prev)
      if (next.has(pid)) {
        // Enforce min-of-one: don't allow deselecting the last enabled counter.
        if (next.size <= 1) return prev
        next.delete(pid)
      } else {
        next.add(pid)
      }
      return next
    })
  }

  const [liveLegOdds, setLiveLegOdds] = useState<Record<string, LiveOddsEntry>>(() => {
    try {
      const raw = migratedLocalStorageGet(LIVE_ODDS_KEY, LIVE_ODDS_KEY_LEGACY)
      if (!raw) return {}
      const parsed = JSON.parse(raw) as Record<string, LiveOddsEntry>
      const now = Date.now()
      const fresh: Record<string, LiveOddsEntry> = {}
      for (const [k, v] of Object.entries(parsed)) {
        if (v && typeof v.odds === 'number' && typeof v.ts === 'number' && now - v.ts < LIVE_ODDS_TTL_MS) {
          fresh[k] = v
        }
      }
      return fresh
    } catch {
      return {}
    }
  })
  // Ref mirror of liveLegOdds so loadArbOpps (a useCallback with stable
  // deps) can read the latest overrides without listing them as a dep
  // (which would re-fire the scan on every 1Hz odds tick).
  const liveLegOddsRef = useRef(liveLegOdds)
  useEffect(() => {
    liveLegOddsRef.current = liveLegOdds
    try {
      localStorage.setItem(LIVE_ODDS_KEY, JSON.stringify(liveLegOdds))
    } catch { /* full / disabled */ }
  }, [liveLegOdds])
  // Per-provider record of which leg the user picked when they clicked an arb
  // cell. The arb_leg_odds SSE event tells us provider_id + live_odds but NOT
  // outcome/point — so we MUST remember it from navigate-time. Without this
  // the override gets keyed against whichever leg `find()` matched first
  // (often the wrong outcome in over/under or 3-way markets).
  type PickedLegMeta = { eid: string; outcome: string; point: number | null | undefined }
  const pickedLegMetaByProvider = useRef<Record<string, PickedLegMeta>>({})
  // Global single-leg lock — drops every click while any leg is mid-process.
  // useRef holds the in-flight set for the synchronous busy check; useState
  // mirrors busy=true/false so the UI can dim every other row's cursor.
  const navInFlight = useRef<Set<string>>(new Set())
  const [legBusy, setLegBusy] = useState<string | null>(null)
  const [detectedSettlements, setDetectedSettlements] = useState<Record<number, { result: string; payout: number; match_method: string }>>({})
  const [livePrices, setLivePrices] = useState<Record<string, { odds: number; edge: number | null }>>({})
  const [stakeCaps, setStakeCaps] = useState<Record<string, number>>({})
  // Per-leg arb alignment from arb_legs_loaded + arb_alignment events
  const [arbLegs, setArbLegs] = useState<ArbLeg[] | null>(null)
  const [arbAllGreen, setArbAllGreen] = useState<boolean>(false)
  const [arbProfitPct, setArbProfitPct] = useState<number | null>(null)
  const [arbGroupId, setArbGroupId] = useState<string | null>(null)
  const [arbDethroneToast, setArbDethroneToast] = useState<string | null>(null)
  // Toast emitted on every `bet_recorded` / `bet_record_failed` SSE so the
  // user gets immediate confirmation that the placement made it to the DB.
  // Without this it took ~5s (next /pending-bets poll) before the row showed
  // up in PENDING — long enough that the user wasn't sure if their click
  // actually landed.
  const [betRecordedToasts, setBetRecordedToasts] = useState<
    {
      id: string
      // 'ok' = green emerald (bet recorded immediately)
      // 'info' = amber (deferred — waiting for reactive history sync to catch
      //   the accepted stake; common when bookmaker stake-limits)
      // 'fail' = red (genuine failure)
      kind: 'ok' | 'info' | 'fail'
      pid: string
      bet_id?: number
      odds?: number
      stake?: number
      reason?: string
    }[]
  >([])
  // Per-cluster arb opps: { cluster_key: [top 10 opps for that cluster's funded siblings] }
  // Siblings share odds, so one fetch per cluster suffices. Each sibling renders its
  // own card using the cluster's opp list (differing only in balance / cap / active state).
  //
  // Persisted to localStorage so a refresh shows the last live-updated state
  // INSTANTLY (no flash of empty + 1-3min wait for the next scan). The next
  // loadArbOpps run replaces it with fresh API data + applyOverrides re-applies
  // any saved live-leg odds on top.
  const OPPS_CACHE_KEY = 'betty:oppsByCluster:v1'
  const OPPS_CACHE_KEY_LEGACY = 'arnold:oppsByCluster:v1'
  const OPPS_CACHE_TTL_MS = 6 * 60 * 60 * 1000
  const [oppsByCluster, setOppsByCluster] = useState<Record<string, any[]>>(() => {
    try {
      const raw = migratedLocalStorageGet(OPPS_CACHE_KEY, OPPS_CACHE_KEY_LEGACY)
      if (!raw) return {}
      const parsed = JSON.parse(raw) as { ts: number; data: Record<string, any[]> }
      if (!parsed?.ts || Date.now() - parsed.ts >= OPPS_CACHE_TTL_MS) return {}
      const cached = parsed.data ?? {}
      // Apply any persisted live-leg overrides on top of the cached opps so
      // a refresh shows the corrected odds immediately. Without this, the
      // cached opp data (which can be a stale @41.00 from extraction lag)
      // wins over the live @1.78 override until the next API scan completes.
      return applyLiveOverridesToClusterMap(cached, liveLegOdds)
    } catch {
      return {}
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(OPPS_CACHE_KEY, JSON.stringify({ ts: Date.now(), data: oppsByCluster }))
    } catch { /* full / disabled */ }
  }, [oppsByCluster])
  const [arbLoading, setArbLoading] = useState(false)
  // Stale-arb verifier state: one cluster at a time. While running, button
  // disables and shows progress. Persists `cluster_id` so the user can see
  // which one is being checked; null = idle.
  const [verifyingCluster, setVerifyingCluster] = useState<string | null>(null)
  const [verifyProgress, setVerifyProgress] = useState<{ done: number; total: number } | null>(null)
  // Sub-tab switcher within the Play page
  const [subTab, setSubTab] = useState<'arb' | 'value'>('value')
  // Tick state so the TTK column re-renders every 30s without depending on
  // unrelated state changes. Cheap — single setState per tick, all consumers
  // already pure functions of the iso string.
  const [, setTtkTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTtkTick(t => t + 1), 30_000)
    return () => clearInterval(id)
  }, [])

  const startSkin = async (pid: string) => {
    // No-op if already active — clicking again should NOT toggle off.
    // Once selected, the runner auto-progresses (login → settle → bet loop)
    // without further user interaction. To stop, the user closes betty.
    if (activeProviders.has(pid)) return
    // Add provider — open tab and start/add to loop
    setActiveProviders(prev => new Set(prev).add(pid))
    try { await api.startMirror() } catch { /* */ }
    try { await api.openTab(pid) } catch { /* */ }
    // Collect bets from all active clusters (current + new)
    const allPids = [...activeProviders, pid]
    const selectedClusters = new Set(allPids.map(p => providerToCluster[p] || p))
    const allBets = bets.filter(b => selectedClusters.has(b.cluster || b.provider_id))
    setLoopRunning(true)
    const numericBalances = Object.fromEntries(Object.entries(providerBalances).map(([k, v]) => [k, getBalance(v)]))
    await api.startPlayLoop(allBets, numericBalances, allPids)
  }

  // Click a Section B value-bet row → navigate the provider's tab to the event
  // page (same pattern as Section A arb-row click, but single-leg). Lets the
  // user see live odds on the bookmaker site and place manually OR via the
  // auto-runner. The /mirror/navigate endpoint just calls
  // workflow.navigate_to_event — no slip-stream / runner side-effects.
  const handleValueBetClick = async (b: BatchBet) => {
    // Route through the same /mirror/arb/navigate-opp endpoint as Section A.
    // For a value bet this is a degenerate 1-leg opp — backend resolves the
    // single leg as the anchor, then nav + prep + slip-stream runs the same
    // way. Gives us the same UX guarantees: row turns green on sync, the
    // per-provider widget shows the picked event, status messages flow.
    const leg = {
      provider: b.provider_id,
      outcome: b.outcome,
      point: b.point,
      odds: b.odds,
      fair_odds: b.fair_odds,
      stake: b.stake,
      provider_meta: b.provider_meta,
    }
    const oppShape = {
      event_id: b.event_id,
      market: b.market,
      sport: b.sport,
      starts_at: b.start_time,
      display_home: b.display_home,
      display_away: b.display_away,
      legs: [leg],
      _picked_leg: leg,
    }
    const lk = legKey(b.provider_id, b.outcome, b.point)
    setPickedEventByProvider(prev => (prev[b.provider_id] === b.event_id ? prev : { ...prev, [b.provider_id]: b.event_id }))
    setProviderStatusFor(b.provider_id, `navigating: ${b.outcome} @ ${b.odds.toFixed(2)}`)
    try {
      const r: any = await api.navigateOpp(b.provider_id, oppShape)
      if (r?.status === 'synced' || r?.status === 'nav_only') {
        setSyncedLegs(prev => {
          const cur = prev[b.event_id] ?? new Set<string>()
          if (cur.has(lk)) return prev
          return { ...prev, [b.event_id]: new Set([...cur, lk]) }
        })
        const msg = r.status === 'synced'
          ? `synced @ ${r.planned_odds?.toFixed?.(2) ?? '?'} — click Place on tab`
          : `tab on event — click outcome + Place`
        setProviderStatusFor(b.provider_id, msg)
      } else if (r?.reason) {
        setProviderStatusFor(b.provider_id, `nav: ${r.reason}`)
      }
    } catch (e) {
      console.warn(`[value-bet-click] navigate failed for ${b.provider_id}:`, e)
      setProviderStatusFor(b.provider_id, 'nav failed — see console')
    }
  }

  const handleCardClick = async (pid: string) => {
    // Manual-control workflow — NO auto-runner. Click only ensures the
    // mirror is up + the provider tab is open. User then clicks individual
    // arb cells to navigate to specific events. The auto-runner that the
    // old card-click spawned would process opps autonomously and was the
    // source of "navigated auto without me clicking" haywire.
    //
    // Bet placement still works: the browser's interceptor catches the
    // /placewidget XHR and POSTs to /api/bets via the fallback path in
    // play_loop.on_bet_intercepted (no runner needed).
    try { await api.startMirror() } catch { /* mirror already up */ }
    try { await api.openTab(pid) } catch (e) { console.warn(`[card-click] openTab failed for ${pid}:`, e) }
    setActiveProviders(prev => prev.has(pid) ? prev : new Set(prev).add(pid))
  }

  const load = useCallback(async () => {
    try {
      const [result, pendingResult, bankrollResult] = await Promise.all([
        api.getPlayBatch(),
        api.getPendingBets().catch(() => ({ providers: [] })),
        api.getBankrollSummary().catch(() => ({ providers: [] })),
      ])
      setBatch(result.batch ?? [])
      setSummary(result.summary ?? null)
      // Build balance map: start from numeric batch balances then overlay bonus info from /api/bankroll
      const balanceMap: Record<string, ProviderBalanceLike> = {}
      for (const [pid, num] of Object.entries(result.provider_balances ?? {})) {
        balanceMap[pid] = num as number
      }
      for (const p of bankrollResult.providers ?? []) {
        balanceMap[p.id] = {
          balance: p.balance_sek ?? p.balance ?? 0,
          balance_native: p.balance ?? 0,
          currency: p.currency ?? 'SEK',
          bonus_trigger: p.bonus_trigger_amount ?? undefined,
          bonus_currency: p.bonus_currency ?? undefined,
        }
      }
      setProviderBalances(balanceMap)
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
    // Adaptive polling: when load() succeeds, poll every 10s; when it fails
    // (server 502/504, tunnel hiccup), poll every 3s so the UI auto-recovers
    // as soon as the server comes back instead of waiting up to 10s. The
    // banner stays visible for those few seconds, then clears automatically
    // on the first successful tick.
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    const tick = async () => {
      await load()
      if (cancelled) return
      // If error is set, retry sooner; else normal cadence.
      const interval = errorRef.current ? 3_000 : 10_000
      timer = setTimeout(tick, interval)
    }
    timer = setTimeout(tick, 10_000)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [load])

  // Fetch top 10 arb opps per cluster of funded soft providers.
  // One fetch per cluster (siblings share odds). Counter pool excludes same-cluster
  // providers (zero edge) and drained providers (no balance to place anchor leg).
  //
  // Bottleneck note: arb-workflow is a 1-3min server-side scan with a 60s cache.
  // We polled at 30s with deps that retriggered on every balance tick — net effect
  // was perpetual cache miss + concurrent scans hammering the DB pool. Fixes:
  //   1. Stable dep `fundedClustersKey` so balance ticks don't re-fire the scan.
  //   2. 5min poll interval (matches server cache + extraction cadence).
  //   3. AbortController cancels in-flight scans on dep change so stale results
  //      don't overwrite fresh ones.
  //   4. Sequential cluster iteration (not Promise.all) so one slow scan doesn't
  //      block the DB pool for all clusters.

  // Stable derived key from the SET of funded soft clusters. Re-computed on
  // every render but its STRING identity only changes when membership shifts —
  // useCallback below depends on it (not on providerBalances) so we don't
  // re-create loadArbOpps on every balance tick.
  const fundedClustersKey = useMemo(() => {
    const reps: string[] = []
    const seen = new Set<string>([
      ...Object.keys(providerBalances),
      ...Object.keys(pendingByProvider),
    ])
    for (const pid of seen) {
      if (UNLIMITED_PROVIDERS.has(pid)) continue
      const bal = getBalance(providerBalances[pid])
      const trig = getTrigger(providerBalances[pid])
      const pending = pendingByProvider[pid]?.length ?? 0
      if (bal >= DRAIN_THRESHOLD_SEK || (trig?.amount ?? 0) > 0 || pending > 0) {
        reps.push(resolveSoftCluster(pid))
      }
    }
    return [...new Set(reps)].sort().join('|')
  }, [providerBalances, pendingByProvider])

  // Snapshot the latest providerBalances inside a ref so the (stable) loadArbOpps
  // can read up-to-date values without listing providerBalances as a dep.
  const balancesRef = useRef(providerBalances)
  balancesRef.current = providerBalances
  // Same ref trick for pendingByProvider so the stable loadArbOpps can check
  // pending-bet presence without re-firing on every pending refresh.
  const pendingRef = useRef<Record<string, any[]>>({})
  pendingRef.current = pendingByProvider

  // Same trick for enabledCounters — read latest via ref inside the scan.
  // Stable dep key (sorted comma-joined) drives the re-scan trigger.
  const enabledCountersRef = useRef(enabledCounters)
  enabledCountersRef.current = enabledCounters
  const enabledCountersKey = useMemo(
    () => [...enabledCounters].sort().join(','),
    [enabledCounters],
  )

  const arbAbortRef = useRef<AbortController | null>(null)

  // Map an opp's leg-shape (market + outcome + point) to the Pinnacle market
  // key/designation. Lets us look up live odds in the refresh-matchup
  // response without re-implementing the slug logic per call site.
  const pinnacleMarketKeyPrefix = (market: string): string | null => {
    if (market === 'moneyline' || market === '1x2') return 's;'  // matches s;0;m or s;6;m
    if (market === 'spread') return 's;'                          // matches s;0;s;<pt> or s;6;s;<pt>
    if (market === 'total') return 's;'                           // matches s;0;ou;<pt> or s;6;ou;<pt>
    return null
  }
  const pinnacleMarketTypeChar = (market: string): string | null => {
    if (market === 'moneyline' || market === '1x2') return 'm'
    if (market === 'spread') return 's'
    if (market === 'total') return 'ou'
    return null
  }
  // For a given opp + leg, find the live decimal odds in a refresh-matchup
  // response (the `markets` array). Picks the right period (3-way regulation
  // for hockey 1x2 vs full-game ML) and the right designation.
  const pickLiveOddsFromMarkets = (
    market: string,
    outcome: string,
    point: number | null | undefined,
    legsCount: number,  // total legs in opp; 3 = 3-way 1x2, 2 = 2-way ML
    markets: any[],
  ): number | null => {
    const typeChar = pinnacleMarketTypeChar(market)
    if (!typeChar) return null
    // For 1x2/moneyline: 3-way means we want s;6;m (regulation, has draw)
    // 2-way means s;0;m (full game). Pick by outcome count.
    const wantThreeWay = market === '1x2' || (market === 'moneyline' && legsCount === 3)
    const candidates = (markets ?? []).filter((m: any) => {
      const k = (m.key ?? '') as string
      if (typeChar === 'm') {
        // moneyline / 1x2: key is "s;<period>;m"
        if (!/^s;\d+;m$/.test(k)) return false
        const has3 = (m.prices ?? []).length === 3
        return wantThreeWay ? has3 : !has3
      }
      // spread / total: key is "s;<period>;s;<pt>" or "s;<period>;ou;<pt>"
      const re = new RegExp(`^s;\\d+;${typeChar};`)
      if (!re.test(k)) return false
      // Match by Pinnacle market LINE (home-perspective). Pinnacle keys one
      // market `s;0;s;1.5` carrying home@+1.5 AND away@-1.5; picking by
      // designation below selects the correct side. Callers MUST pass the
      // line (opp.point), not the leg's team-perspective point — otherwise
      // an away spread leg (leg.point=-1.5) would match the wrong market
      // (`s;0;s;-1.5`, a different line) and read the wrong price.
      if (point != null) {
        const parts = k.split(';')
        const lastPart = parseFloat(parts[parts.length - 1])
        if (Math.abs(lastPart - point) > 0.01) return false
      }
      return true
    })
    // Prefer period=0 unless we want 3-way 1x2 in which case period=6 wins.
    candidates.sort((a: any, b: any) => {
      if (wantThreeWay) {
        const aPref = a.period === 6 ? 0 : 1
        const bPref = b.period === 6 ? 0 : 1
        if (aPref !== bPref) return aPref - bPref
      }
      return (a.period ?? 99) - (b.period ?? 99)
    })
    const m = candidates[0]
    if (!m) return null
    const designation = outcome  // Pinnacle uses identical labels: home/away/draw/over/under
    const price = (m.prices ?? []).find((p: any) => p.designation === designation)
    return price?.decimal ?? null
  }
  // Targeted refresh of Pinnacle odds for the picked opp. Fired in parallel
  // with the BETINIA navigate so by the time the user looks at the row again
  // the Pinnacle leg odds reflect live truth, not 2-5min-stale extraction.
  const refreshPinnacleMatchup = useCallback(async (opp: any, matchupId: string) => {
    let res
    try {
      res = await (await fetch(`/mirror/pinnacle/refresh-matchup/${matchupId}`, { signal: AbortSignal.timeout(10_000) })).json()
    } catch (e) {
      console.warn('[refresh-matchup] fetch failed:', e)
      return
    }
    if (res?.error || !Array.isArray(res?.markets)) {
      console.warn('[refresh-matchup] no markets:', res?.error)
      return
    }
    const eid = opp.event_id
    const market = opp.market as string
    const point = opp.point
    const legsCount = (opp.legs ?? []).length
    const ts = Date.now()
    // Build {legKey: live_odds} overrides for every Pinnacle leg.
    // LINE lookup uses opp.point (Pinnacle market keys are home-perspective:
    // `s;0;s;1.5` holds both home@+1.5 and away@-1.5). Override KEY uses
    // leg.point (team-perspective) so storage/lookup match the leg dict.
    const overrides: Record<string, number> = {}
    for (const leg of (opp.legs ?? [])) {
      const lp = (leg.provider ?? leg.provider_id) as string
      if (lp !== 'pinnacle') continue
      const live = pickLiveOddsFromMarkets(market, leg.outcome, point, legsCount, res.markets)
      if (live == null || live <= 0) continue
      overrides[legOddsKey(eid, lp, leg.outcome, leg.point ?? point)] = live
    }
    if (Object.keys(overrides).length === 0) return
    // Persist for cross-refresh continuity.
    setLiveLegOdds(prev => {
      const next = { ...prev }
      let changed = false
      for (const [k, v] of Object.entries(overrides)) {
        if (next[k]?.odds === v) continue
        next[k] = { odds: v, ts }
        changed = true
      }
      return changed ? next : prev
    })
    // Apply IMMEDIATELY to the row so the user sees the recomputed profit %
    // without waiting for the next API scan. Mutates legs.odds for matching
    // (eid, provider, outcome, point), recomputes profit, re-sorts.
    setOppsByCluster(prev => {
      const next: Record<string, any[]> = {}
      let mutated = false
      for (const [cluster, opps] of Object.entries(prev)) {
        const updated = opps.map((o: any) => {
          if (o.event_id !== eid) return o
          let touched = false
          const newLegs = (o.legs ?? []).map((l: any) => {
            const lk = legOddsKey(eid, l.provider ?? l.provider_id ?? '', l.outcome, l.point ?? point)
            const live = overrides[lk]
            if (live != null && live !== l.odds) {
              touched = true
              return { ...l, odds: live }
            }
            return l
          })
          if (!touched) return o
          mutated = true
          let invSum = 0
          for (const l of newLegs) {
            const x = Number(l.odds ?? 0)
            if (x <= 0) { invSum = 0; break }
            invSum += 1 / x
          }
          const newProfit = invSum > 0 ? (1 / invSum - 1) * 100 : (o.guaranteed_profit_pct ?? 0)
          return { ...o, legs: newLegs, guaranteed_profit_pct: newProfit, profit_pct: newProfit }
        })
        next[cluster] = [...updated].sort(
          (a: any, b: any) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0),
        )
      }
      return mutated ? next : prev
    })
  }, [])

  // Iterate over a cluster's currently-displayed top-N arbs, refreshing
  // Pinnacle odds for each in series. After completion the list is sorted
  // by *real* current profitability (refreshPinnacleMatchup mutates the
  // opp's legs.odds and recomputes guaranteed_profit_pct, then re-sorts).
  // Single-active: only one cluster can verify at a time. Subsequent
  // clicks while running are dropped.
  const verifyArbsInCluster = useCallback(async (clusterId: string) => {
    if (verifyingCluster) return
    const opps = oppsByCluster[clusterId] ?? []
    // Only Pinnacle-side arbs need the targeted refresh. Filter + dedupe by
    // matchup_id so we don't fetch the same matchup twice (3-leg arbs share
    // one Pinnacle matchup_id across home/away legs).
    const seen = new Set<string>()
    const queue: Array<{ opp: any; matchupId: string }> = []
    for (const o of opps.slice(0, 20)) {
      const pinnLegs = (o.legs ?? []).filter((l: any) => (l.provider ?? l.provider_id) === 'pinnacle')
      if (pinnLegs.length === 0) continue
      const mid = pinnLegs[0]?.provider_meta?.matchup_id
      if (!mid || seen.has(mid)) continue
      seen.add(mid)
      queue.push({ opp: o, matchupId: mid })
    }
    if (queue.length === 0) return
    setVerifyingCluster(clusterId)
    setVerifyProgress({ done: 0, total: queue.length })
    try {
      for (let i = 0; i < queue.length; i++) {
        const { opp, matchupId } = queue[i]
        try {
          await refreshPinnacleMatchup(opp, matchupId)
        } catch (e) {
          console.warn('[verify-arbs] failed', matchupId, e)
        }
        setVerifyProgress({ done: i + 1, total: queue.length })
      }
    } finally {
      setVerifyingCluster(null)
      // Keep the final progress visible briefly, then clear.
      setTimeout(() => setVerifyProgress(null), 2000)
    }
  }, [oppsByCluster, refreshPinnacleMatchup, verifyingCluster])

  const loadArbOpps = useCallback(async () => {
    // Cancel any in-flight scan so a stale result can't overwrite fresh state.
    if (arbAbortRef.current) arbAbortRef.current.abort()
    const controller = new AbortController()
    arbAbortRef.current = controller

    const balances = balancesRef.current
    // Group all known providers (including unlimited) by cluster. Unlimited
    // providers are kept so polymarket↔pinnacle / kalshi↔pinnacle arbs surface
    // as their own cluster cards — letting the user spot/play unlimited-only
    // arbs (mostly esports per-map markets where Pinnacle's lines lag Polymarket).
    const softByCluster: Record<string, string[]> = {}
    for (const pid of Object.keys(balances)) {
      const cluster = resolveSoftCluster(pid)
      ;(softByCluster[cluster] ??= []).push(pid)
    }

    // Pick one representative per cluster — the qualifying member with the
    // highest balance (or first with bonus/pending if no positive balance).
    // "Qualifying" = balance ≥ threshold OR unclaimed bonus OR pending bets.
    // Stays dynamic: any soft that meets one of those criteria scans/renders.
    const isQualifyingMember = (pid: string): boolean => {
      const bal = getBalance(balances[pid])
      const trig = getTrigger(balances[pid])
      const pending = pendingRef.current[pid]?.length ?? 0
      return bal >= DRAIN_THRESHOLD_SEK || (trig?.amount ?? 0) > 0 || pending > 0
    }
    const reps: Array<{ cluster: string; rep: string }> = []
    for (const [cluster, members] of Object.entries(softByCluster)) {
      const qualifying = members.filter(isQualifyingMember)
      if (qualifying.length === 0) continue
      // Prefer the highest-balance qualifying member as the rep — it's the
      // most likely candidate for actually placing arbs (vs a bonus-only
      // sibling with 0 balance).
      qualifying.sort((a, b) => getBalance(balances[b]) - getBalance(balances[a]))
      reps.push({ cluster, rep: qualifying[0] })
    }

    if (reps.length === 0) {
      // No funded clusters → either balances haven't loaded yet, or every soft
      // is drained. Leave cached oppsByCluster intact rather than wiping —
      // when the bankroll fetch completes, fundedClustersKey re-fires and we
      // scan properly. Wiping here used to clobber the localStorage-restored
      // state on every initial mount.
      if (Object.keys(balances).length === 0) return
      setOppsByCluster({})
      return
    }

    // Counter pool: ONLY sharp/unlimited providers (cloudbet, polymarket,
    // pinnacle, kalshi). Soft↔soft arbs are intentionally excluded — we
    // only hedge a soft anchor against the sharp/unlimited side. Matches
    // the drain-to-unlimited goal and avoids opps where both legs are
    // caps-prone soft books (which the runner would also reject via
    // is_valid_arb_shape, so generating them is just UI noise).
    // Honour the user's counter-pool filter (chip toggles in the arb-page
    // header). enabledCountersRef.current is the latest set; reading via
    // ref so the scan callback doesn't have to list it as a dep.
    const pool = Array.from(UNLIMITED_PROVIDERS).filter(p => enabledCountersRef.current.has(p))

    // Apply persisted live-odds overrides on top of fresh API results so a
    // refresh / re-scan doesn't snap back to the 60-s cached values that
    // pre-date our live drift. Recomputes profit from the merged odds.
    const applyOverrides = (opps: any[]): any[] => {
      const overrides = liveLegOddsRef.current
      const now = Date.now()
      return opps.map(o => {
        const eid = o.event_id
        if (!eid) return o
        let touched = false
        const legs = (o.legs ?? []).map((l: any) => {
          const k = legOddsKey(eid, l.provider ?? l.provider_id ?? '', l.outcome, l.point)
          const ov = overrides[k]
          // Only apply if the overlay is fresh — stale entries silently
          // distort the API edge (see Pol Martin Tiffon incident: 6h-old
          // overlay made a -1% arb look like -25%, click reverted to truth).
          if (ov && ov.odds > 0 && ov.odds !== l.odds && now - ov.ts < LIVE_ODDS_TTL_MS) {
            touched = true
            return { ...l, odds: ov.odds }
          }
          return l
        })
        if (!touched) return o
        let invSum = 0
        for (const l of legs) {
          const x = Number(l.odds ?? 0)
          if (x <= 0) { invSum = 0; break }
          invSum += 1 / x
        }
        const newProfit = invSum > 0 ? (1 / invSum - 1) * 100 : (o.guaranteed_profit_pct ?? 0)
        return { ...o, legs, guaranteed_profit_pct: newProfit, profit_pct: newProfit }
      })
    }

    try {
      setArbLoading(true)
      // Sequential — each scan is 1-3min server-side. Running them serially
      // keeps DB connection pool happy and avoids piling up concurrent slow
      // queries that starve the FastAPI event loop.
      const results: Array<readonly [string, any[]]> = []
      for (const { cluster, rep } of reps) {
        if (controller.signal.aborted) return
        const counters = pool.filter(p => resolveSoftCluster(p) !== cluster)
        try {
          const res = await api.getArbOpps([rep], counters, 20)
          if (controller.signal.aborted) return
          const opps = applyOverrides(((res?.opportunities ?? []) as any[])).sort(
            (a, b) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0),
          )
          results.push([cluster, opps] as const)
          // Push partial results as each cluster lands so the UI fills in
          // progressively rather than all-or-nothing after the slowest scan.
          setOppsByCluster(prev => ({ ...prev, [cluster]: opps }))
        } catch {
          results.push([cluster, [] as any[]] as const)
        }
      }
      if (!controller.signal.aborted) {
        setOppsByCluster(Object.fromEntries(results))
      }
    } finally {
      if (!controller.signal.aborted) setArbLoading(false)
    }
    // Note: providerBalances is read via balancesRef.current — NOT in the deps
    // list — so balance ticks don't re-trigger the scan. fundedClustersKey
    // changes only when the SET of funded clusters changes. enabledCountersKey
    // re-fires the scan when the user toggles which unlimited providers are
    // allowed in the counter pool.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fundedClustersKey, enabledCountersKey])

  useEffect(() => {
    loadArbOpps()
    // 5min interval matches the 60s server cache + extraction cadence. Polling
    // faster than this just thrashes the cache (every cache miss = 1-3min scan).
    const id = setInterval(loadArbOpps, 300_000)
    return () => {
      clearInterval(id)
      if (arbAbortRef.current) arbAbortRef.current.abort()
    }
  }, [loadArbOpps])

  // Continuously poll login state for every UNLIMITED provider — whenever one is
  // logged in with a positive balance AND the batch has positive-edge bets, auto-
  // activate (start the play loop). Green = logged in + runner active. Manual
  // Place/Skip still gates each bet.
  useEffect(() => {
    let cancelled = false
    const missCount: Record<string, number> = {}
    const activated: Set<string> = new Set()
    // Per-tick caches so we don't fetch /mirror/play/status and
    // /api/opportunities/play/batch ONCE PER PROVIDER. Previous code did
    // 4 redundant batch fetches per 5s tick (one per unlimited provider)
    // even though the response is identical for all of them.
    const check = async () => {
      let tickStatus: any = null
      let tickBatch: any = null
      const getStatus = async () => {
        if (tickStatus !== null) return tickStatus
        try {
          tickStatus = await (await fetch('/mirror/play/status')).json()
        } catch { tickStatus = {} }
        return tickStatus
      }
      const getBatch = async () => {
        if (tickBatch !== null) return tickBatch
        try {
          tickBatch = await (await fetch('/api/opportunities/play/batch', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
          })).json()
        } catch { tickBatch = { batch: [], provider_balances: {} } }
        return tickBatch
      }

      for (const pid of UNLIMITED_PROVIDERS) {
        try {
          const r = await fetch(`/mirror/browser/provider/${pid}`)
          const d = await r.json()
          if (cancelled) return
          // Polling is AUTHORITATIVE for green/amber. Mirror backend reads
          // the live page state every call (localStorage + DOM signals);
          // if it says logged_in=false, we trust it and clear the set.
          // SSE events still add immediately on login_detected so we don't
          // wait 10s for the green flip — but the next poll reconciles
          // any stale additions (e.g. balance_intercepted with balance=0
          // from an unauthenticated /currency endpoint).
          setLoggedInProviders(prev => {
            const has = prev.has(pid)
            if (d.logged_in && !has) return new Set(prev).add(pid)
            if (!d.logged_in && has) {
              const n = new Set(prev); n.delete(pid); return n
            }
            return prev
          })
          if (d.logged_in) {
            missCount[pid] = 0
            if ((d.balance ?? 0) > 0) {
              try {
                const ps = await getStatus()
                const runnerState = ps?.providers?.[pid]?.state
                if (runnerState && runnerState !== 'idle' && runnerState !== 'none') {
                  activated.add(pid)
                  continue
                }
                const bData = await getBatch()
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
            // loggedInProviders already reconciled at the top of the loop;
            // here we only track activation gating which uses 2-miss
            // debounce to avoid runner thrashing on transient blips.
            if (missCount[pid] >= 2) activated.delete(pid)
          }
        } catch { /* swallow — retry next tick */ }
      }
    }
    check()
    // 10s tick (was 5s) — login state rarely changes that fast, and this
    // loop hits ~6 endpoints per tick (1 per unlimited provider + 1 batch +
    // 1 status). Halving cadence cuts polling load to ~3 req/s steady-state.
    const id = setInterval(check, 10000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Poll login state for soft providers we know about (have balance or pending).
  // Recovers green-state after a page reload AND auto-activates the runner once
  // funded + logged in (parallel to the unlimited effect above). Auto-activation
  // gated on DRAIN_THRESHOLD so we don't spawn runners on drained providers.
  useEffect(() => {
    // Universe: every soft provider we know about (so a freshly-opened tab
    // gets polled immediately, before any balance/pending exists). Cluster
    // siblings + standalones + anyone we have balance/pending data for.
    const softPids = new Set<string>()
    for (const members of Object.values(SOFT_CLUSTER_MEMBERS)) {
      for (const pid of members) softPids.add(pid)
    }
    for (const pid of SOFT_STANDALONES) softPids.add(pid)
    // Include UNLIMITED providers (polymarket, pinnacle, cloudbet, kalshi) too —
    // their loggedInProviders state used to depend ENTIRELY on SSE events
    // (login_detected / balance_intercepted), so a missed/race'd event left
    // the green badge stuck off even when the balance was clearly visible.
    // The /mirror/browser/provider/{pid} endpoint reads the same browser
    // interceptor state, so polling here is a safe fallback.
    for (const pid of Object.keys(providerBalances)) softPids.add(pid)
    for (const pid of Object.keys(pendingByProvider)) softPids.add(pid)
    if (softPids.size === 0) return
    let cancelled = false
    const missCount: Record<string, number> = {}
    const activated: Set<string> = new Set()
    const check = async () => {
      for (const pid of softPids) {
        try {
          const r = await fetch(`/mirror/browser/provider/${pid}`)
          const d = await r.json()
          if (cancelled) return
          // Track tab-open separately from logged-in so the row can show
          // amber ("tab open, awaiting login") vs green ("logged in").
          if (d.found) {
            setTabOpenProviders(prev => prev.has(pid) ? prev : new Set(prev).add(pid))
          } else {
            setTabOpenProviders(prev => {
              if (!prev.has(pid)) return prev
              const n = new Set(prev); n.delete(pid); return n
            })
          }
          // Polling is AUTHORITATIVE — see UNLIMITED loop above for rationale.
          setLoggedInProviders(prev => {
            const has = prev.has(pid)
            if (d.logged_in && !has) return new Set(prev).add(pid)
            if (!d.logged_in && has) {
              const n = new Set(prev); n.delete(pid); return n
            }
            return prev
          })
          if (d.logged_in) {
            missCount[pid] = 0
          } else {
            missCount[pid] = (missCount[pid] || 0) + 1
            if (missCount[pid] >= 2) activated.delete(pid)
          }
        } catch { /* swallow */ }
      }
    }
    check()
    // 10s (was 5s) — soft providers don't auto-activate so this is purely a
    // login-state badge poll; halving cadence cuts ~20 sequential per-provider
    // fetches per tick down to one batch every 10s.
    const id = setInterval(check, 10000)
    return () => { cancelled = true; clearInterval(id) }
  }, [providerBalances, pendingByProvider])

  // One-shot seed on mount: pull /mirror/play/status so the chip reflects
  // the runner's current state if the runner is already past the events
  // we'd have caught via SSE. /mirror/stream is pure live fan-out (no
  // replay), so a page reload mid-session leaves loopProviderStatus
  // empty and the chip stuck on the default 'tab_open' / red pill even
  // though the backend has already advanced to ready_to_run / running.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const ps = await api.getPlayStatus()
        if (cancelled || !ps) return
        const providers = ps.providers ?? {}
        const seeded: Record<string, any> = {}
        for (const [pid, s] of Object.entries<any>(providers)) {
          if (s?.state && s.state !== 'idle' && s.state !== 'none') {
            seeded[pid] = { state: s.state, current_bet: s.current_bet ?? null }
          }
        }
        if (Object.keys(seeded).length > 0) {
          setLoopProviderStatus(prev => ({ ...(prev ?? {}), ...seeded }))
          setActiveProviders(prev => {
            const next = new Set(prev)
            for (const pid of Object.keys(seeded)) next.add(pid)
            return next
          })
        }
        if (ps.state === 'running') setLoopRunning(true)
      } catch { /* ignore — SSE will catch up next event */ }
    })()
    return () => { cancelled = true }
  }, [])

  // Phase 2 recovery floor — fold mirror DB state into loopProviderStatus.
  // Runs every time mirrorState refreshes (5s default). DB state is
  // authoritative when SSE drops events; SSE still wins between polls
  // because its updates land seconds before the next mirrorState tick.
  useEffect(() => {
    const runners = mirrorState.runners
    if (!runners || Object.keys(runners).length === 0) return
    // DB state can be stale from a prior betty.bat run that died without
    // writing state='idle'. Activity (i.e. activeProviders membership) must
    // come from the live in-memory runner via /mirror/play/status — NOT from
    // DB. Otherwise a previous session's "ready_to_run" leaks into a fresh
    // launch where no runner exists. We ONLY update loopProviderStatus here,
    // and only for providers the user already activated this session.
    const seeded: Record<string, any> = {}
    for (const [pid, r] of Object.entries(runners)) {
      const s = r.state
      if (!s || s === 'idle' || s === 'none') continue
      if (!activeProviders.has(pid)) continue
      seeded[pid] = { state: s, current_bet: null }
    }
    if (Object.keys(seeded).length > 0) {
      setLoopProviderStatus(prev => {
        const merged: Record<string, any> = { ...(prev ?? {}) }
        for (const [pid, v] of Object.entries(seeded)) {
          // SSE-driven entry is authoritative if it has a current_bet (live
          // update). Otherwise DB state wins — this unsticks the "Log in to
          // continue" red pill when SSE missed the provider_ready event.
          const existing = merged[pid]
          if (!existing || !existing.current_bet) merged[pid] = v
        }
        return merged
      })
    }
  }, [mirrorState.lastFetched, activeProviders])

  // SSE event handler
  useEffect(() => {
    if (!mirror.lastEvent) return
    const { type, data } = mirror.lastEvent
    if (type === 'login_waiting') {
      setProviderStatusFor(
        data.provider_id,
        `waiting for login (${Math.round(data.elapsed)}/${data.timeout}s)`,
      )
    }
    if (type === 'login_detected') {
      setProviderStatusFor(data.provider_id, null)
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
    if (type === 'settling_pending') setProviderStatusFor(data.provider_id, 'scanning pending…')
    if (type === 'settling_done') {
      // Clear the "scanning pending…" status set by settling_pending — the
      // sync is done so the badge shouldn't linger. Without this the cyan
      // badge stayed on indefinitely if the play runner cached an old
      // settling state. Reactive sync now fires settling_done in a finally
      // block so the badge always clears.
      setProviderStatus(prev => {
        if (!prev[data.provider_id]) return prev
        const next = { ...prev }; delete next[data.provider_id]; return next
      })
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
    if (type === 'provider_skipped') setProviderStatusFor(data.provider_id, `skipped: ${data.reason}`)
    if (type === 'arb_runner_idle') {
      const reason = data?.reason || 'idle'
      const skips = data?.details?.skip_counts
      const tail = skips && Object.keys(skips).length
        ? ` (${Object.entries(skips).map(([k, v]) => `${k}×${v}`).join(', ')})`
        : ''
      setProviderStatusFor(data.provider_id, `idle: ${reason}${tail}`)
    }
    if (type === 'provider_complete') {
      setProviderStatusFor(data.provider_id, data?.reason ? `done — ${data.reason}` : 'done')
    }
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
    if (type === 'bet_skipped' || type === 'bet_failed') {
      setCurrentBetReady(null)
      // Show a brief reason in loopStatus so the user can SEE the runner is
      // alive and what's failing — without this, a stream of hard-fails on
      // stale event slugs looks identical to a stuck runner.
      const bet = data?.bet || {}
      const home = bet.display_home || bet.home_team || ''
      const away = bet.display_away || bet.away_team || ''
      const evt = home && away ? `${home} v ${away}` : (bet.event_name || '?')
      const reason = data?.reason || 'unknown'
      const pid = data?.provider_id || bet.provider_id
      if (pid) setProviderStatusFor(pid, `skipped: ${evt} — ${reason}`)
      else setLoopStatus(`Skipped: ${evt} — ${reason}`)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    if (type === 'bet_navigating') {
      const bet = data?.bet || {}
      const home = bet.display_home || bet.home_team || ''
      const away = bet.display_away || bet.away_team || ''
      const evt = home && away ? `${home} v ${away}` : (bet.event_name || '?')
      const skipped = data?.skipped_so_far ?? 0
      const fails = data?.consecutive_hard_fails ?? 0
      const tail = fails > 0 ? ` (${fails} hard-fails)` : (skipped > 0 ? ` (${skipped} skipped)` : '')
      setProviderStatusFor(data?.provider_id, `navigating: ${evt}${tail}`)
    }
    if (type === 'runner_stale_intel') {
      setProviderStatusFor(
        data?.provider_id,
        `⚠️ ${data?.consecutive_hard_fails ?? '?'} hard-fails — ${data?.hint || 'cached event intel may be stale'}`,
      )
    }
    if (type === 'arb_legs_loaded') {
      setArbGroupId(data.arb_group_id ?? null)
      setArbLegs(
        (data.legs ?? []).map((l: any) => ({
          provider_id: l.provider_id,
          current_odds: l.planned_odds ?? 0,
          planned_odds: l.planned_odds ?? 0,
          drift_pct: 0,
          current_stake: l.planned_stake ?? 0,
          slip_state: 'loading',
        }))
      )
      setArbAllGreen(false)
      setArbProfitPct(null)
    }
    if (type === 'arb_leg_event_closed') {
      const eid = data.event_id
      const pid = data.provider_id
      if (eid) setDrainedEventIds(prev => prev.has(eid) ? prev : new Set([...prev, eid]))
      if (pid) setProviderStatusFor(pid, `event finished — auto-skipping`)
    }
    if (type === 'opp_expired') {
      // Provider page shows "event avslutat" / "event has ended" banner —
      // Betinia removed the event but our cached opp lingers. Drain so the
      // row disappears from the value-bet + arb tables immediately.
      const eid = data.event_id
      const pid = data.provider_id
      if (eid) setDrainedEventIds(prev => prev.has(eid) ? prev : new Set([...prev, eid]))
      const reason = data.reason || 'event ended on provider'
      if (pid) setProviderStatusFor(pid, `event expired — auto-removing (${reason})`)
    }
    if (type === 'provider_manual_nav') {
      // User browsed the counter tab to a matchup page on their own.
      // Match the URL's team slugs against open opps and auto-pick the
      // first hit so the DUTCH ARB widget shows the right event without
      // the user having to click anything in the betty UI.
      const pid = data.provider_id
      const homeSlug = (data.home_slug ?? '') as string
      const awaySlug = (data.away_slug ?? '') as string
      if (!pid || !homeSlug || !awaySlug) return
      const slugify = (s: string) =>
        s.toLowerCase().trim().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '').replace(/-+/g, '-').replace(/^-|-$/g, '')
      const matchOpp = (o: any): boolean => {
        const h = slugify(o.display_home || o.home_team || '')
        const a = slugify(o.display_away || o.away_team || '')
        if (!h || !a) return false
        // Pinnacle's URL is always (home)-vs-(away). Ours could be either
        // order if the canonical home/away differs — check both.
        return (h === homeSlug && a === awaySlug) || (h === awaySlug && a === homeSlug)
      }
      let matched: { eid: string; opp: any } | null = null
      for (const opps of Object.values(oppsByCluster)) {
        const found = (opps as any[]).find(matchOpp)
        if (found) { matched = { eid: found.event_id, opp: found }; break }
      }
      if (!matched) return  // no open arb for this matchup — ignore silently
      const eid = matched.eid
      setPickedEventByProvider(prev => prev[pid] === eid ? prev : { ...prev, [pid]: eid })
      // Sync ONLY the leg the user already clicked in betty UI (recorded
      // at navigate-time in pickedLegMetaByProvider). Greening every leg
      // of the opp lit up all 3 sibling legs in 1X2 arbs which contradicts
      // the workflow: user picks soft anchor first → checks ONE specific
      // sharp leg → places. Manual nav alone (no prior betty click) just
      // sets pickedEventByProvider without auto-syncing any leg.
      const picked = pickedLegMetaByProvider.current[pid]
      if (picked && picked.eid === eid) {
        const lk = legKey(pid, picked.outcome, picked.point)
        setSyncedLegs(prev => {
          const cur = prev[eid] ?? new Set<string>()
          if (cur.has(lk)) return prev
          const next = new Set(cur); next.add(lk)
          return { ...prev, [eid]: next }
        })
      }
      setProviderStatusFor(pid, `manual nav detected — pick outcome + Place`)
    }
    if (type === 'arb_leg_synced' && data.user_picked) {
      const eid = data.event_id
      const pid = data.provider_id
      if (eid && pid) {
        // The SSE event tells us provider_id but not which specific leg
        // outcome/point — recover that from pickedLegMetaByProvider, set at
        // navigate-time. Without this the synced set is keyed by pid only,
        // which collides for 1X2 arbs with multiple legs on one provider.
        const picked = pickedLegMetaByProvider.current[pid]
        const lk = picked && picked.eid === eid
          ? legKey(pid, picked.outcome, picked.point)
          : null
        if (lk) {
          setSyncedLegs(prev => {
            const cur = prev[eid] ?? new Set<string>()
            if (cur.has(lk)) return prev
            const next = new Set(cur); next.add(lk)
            return { ...prev, [eid]: next }
          })
        }
      }
    }
    if (type === 'arb_leg_failed' && data.event_id && data.provider_id) {
      // Drop from syncedLegs so the cell goes back to neutral. Same picked-
      // meta lookup as arb_leg_synced.
      const eid = data.event_id
      const pid = data.provider_id
      const picked = pickedLegMetaByProvider.current[pid]
      const lk = picked && picked.eid === eid ? legKey(pid, picked.outcome, picked.point) : null
      if (lk) {
        setSyncedLegs(prev => {
          const cur = prev[eid]
          if (!cur || !cur.has(lk)) return prev
          const next = new Set(cur); next.delete(lk)
          return { ...prev, [eid]: next }
        })
      }
    }
    if (type === 'arb_leg_odds') {
      // Update the picked event's leg odds with the streamed live value,
      // recalc the arb's guaranteed_profit_pct, and re-sort the cluster's
      // opp list so the row moves to its correct rank. NO auto-navigate on
      // dethrone — the list just visually updates, user picks again if they
      // want the new top. (Auto-renav was racing with in-flight navigates
      // and event_closed auto-skip → cascading chaos.)
      const pid = data.provider_id
      const live = Number(data.live_odds ?? 0)
      const eid = data.event_id
      if (!pid || live <= 0 || !eid) return
      console.log(`[arb_leg_odds] received pid=${pid} eid=${eid} live=${live}`)
      // The poll task tells us provider_id + live_odds but NOT outcome/point.
      // Use the picked-leg meta we stashed at navigate-time so we update the
      // RIGHT leg (avoids over/under cross-pollination when both opps for
      // this event share the same provider).
      const picked = pickedLegMetaByProvider.current[pid]
      const pickedOutcome = picked && picked.eid === eid ? picked.outcome : null
      const pickedPoint = picked && picked.eid === eid ? picked.point : null
      // Cluster-shared opps: scanner often picks a sibling's leg (e.g.
      // QUICKCASINO @ 1.62 beats BETINIA @ 1.60) but the poll task streams
      // updates keyed on `pid` (the funded cluster member the user navigated
      // on). Accept ANY leg from the same soft cluster — otherwise the
      // sibling-anchored leg in opp.legs never gets the live update and the
      // UI stays at the stale extraction-time odds.
      const pickedCluster = resolveSoftCluster(pid)
      const legMatchesPicked = (l: any): boolean => {
        const lpid = (l.provider ?? l.provider_id ?? '') as string
        if (lpid !== pid && resolveSoftCluster(lpid) !== pickedCluster) return false
        if (pickedOutcome == null) return true  // no meta — fall back to old loose match
        if ((l.outcome ?? '') !== pickedOutcome) return false
        if ((l.point ?? null) !== pickedPoint) return false
        return true
      }
      const ts = Date.now()
      setOppsByCluster(prev => {
        // First pass: persist the override against the exact picked leg.
        if (pickedOutcome != null) {
          const k = legOddsKey(eid, pid, pickedOutcome, pickedPoint)
          setLiveLegOdds(om => om[k]?.odds === live ? om : { ...om, [k]: { odds: live, ts } })
        } else {
          // No picked-leg meta (shouldn't happen post-navigate, but guard
          // anyway) — best-effort key against the first matching leg.
          for (const opps of Object.values(prev)) {
            const o = opps.find((x: any) => x.event_id === eid)
            if (!o) continue
            const matchedLeg = (o.legs ?? []).find((l: any) => (l.provider ?? l.provider_id) === pid)
            if (matchedLeg) {
              const k = legOddsKey(eid, pid, matchedLeg.outcome, matchedLeg.point)
              setLiveLegOdds(om => om[k]?.odds === live ? om : { ...om, [k]: { odds: live, ts } })
            }
            break
          }
        }
        const next: Record<string, any[]> = {}
        let totalMatches = 0
        for (const [cluster, opps] of Object.entries(prev)) {
          let mutated = false
          const updated = opps.map(o => {
            if (o.event_id !== eid) return o
            totalMatches++
            const legs = (o.legs ?? []).map((l: any) =>
              legMatchesPicked(l) ? { ...l, odds: live } : l,
            )
            // Recalc guaranteed_profit_pct using arb math:
            //   profit% = (1 / Σ(1/odds_i) − 1) × 100
            let invSum = 0
            for (const l of legs) {
              const o2 = Number(l.odds ?? 0)
              if (o2 <= 0) { invSum = 0; break }
              invSum += 1 / o2
            }
            const newProfit = invSum > 0 ? (1 / invSum - 1) * 100 : (o.guaranteed_profit_pct ?? 0)
            mutated = true
            return { ...o, legs, guaranteed_profit_pct: newProfit, profit_pct: newProfit }
          })
          if (!mutated) {
            next[cluster] = opps
            continue
          }
          const sorted = [...updated]
            .filter(o => !drainedEventIds.has(o.event_id))
            .sort((a, b) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0))
          const drainedPart = updated.filter(o => drainedEventIds.has(o.event_id))
          next[cluster] = [...sorted, ...drainedPart]
        }
        if (totalMatches === 0) console.log(`[arb_leg_odds] no opp matched eid=${eid} (clusters=${Object.keys(prev).join(',')})`)
        return next
      })
    }
    // Passive live-odds push from the mirror's network interceptor. Fires
    // ~every 5s while ANY tab is open on an Altenar tenant (BETINIA / Quick-
    // casino / Campobet / Lodur / Swiper / Dbet) — the bookmaker's Get-
    // OddsStates WS push broadcasts `{outcome_id: price}` updates which we
    // correlate against each leg's `provider_meta.outcome_id`. No user click
    // required — the UI tracks site-live odds the moment they change. Updates
    // every cluster sibling at the same key because Altenar tenants share an
    // odds engine.
    if (type === 'live_provider_odds') {
      const pid = data.provider_id as string
      const updates = (data.updates ?? {}) as Record<string, number>
      if (!pid || !updates || Object.keys(updates).length === 0) return
      const cluster = resolveSoftCluster(pid)
      const ts = Date.now()
      setOppsByCluster(prev => {
        const next: Record<string, any[]> = {}
        let totalMatches = 0
        for (const [c, opps] of Object.entries(prev)) {
          if (c !== cluster) {
            next[c] = opps
            continue
          }
          let mutated = false
          const updated = opps.map(o => {
            let legMutated = false
            const legs = (o.legs ?? []).map((l: any) => {
              // Only act on legs belonging to this soft cluster — sharp /
              // unlimited counter legs (pinnacle / polymarket / etc.) carry
              // their own outcome_ids that would never collide, but the
              // cluster filter is cheap insurance against future ID reuse.
              const lpid = (l.provider ?? l.provider_id ?? '') as string
              if (resolveSoftCluster(lpid) !== cluster) return l
              const oid = (l.provider_meta || {}).outcome_id
              if (!oid) return l
              const newOdds = updates[String(oid)]
              if (newOdds == null || newOdds <= 0 || newOdds === l.odds) return l
              // Drift guard: reject suspicious updates that change the price by
              // more than 35% from the current leg odds. A real market move at
              // that magnitude is rare and would arrive over multiple ticks; a
              // single tick of that size almost always means the outcome_id
              // matched a row from the wrong market (e.g., ML outcome_id update
              // landing on a spread leg because of a meta-lookup miss). Better
              // to keep the slightly-stale backend price than corrupt the arb.
              const currentOdds = Number(l.odds ?? 0)
              if (currentOdds > 0) {
                const driftRatio = Math.abs(newOdds - currentOdds) / currentOdds
                if (driftRatio > 0.35) {
                  console.warn(`[live_provider_odds] reject ${pid} outcome_id=${oid} drift ${(driftRatio * 100).toFixed(1)}% (${currentOdds} → ${newOdds}) — likely outcome_id mismatch`)
                  return l
                }
              }
              legMutated = true
              // Persist override so loadArbOpps reapplies it after the next
              // 5-min server re-scan (server odds may briefly snap back).
              const k = legOddsKey(o.event_id, lpid, l.outcome, l.point)
              setLiveLegOdds(om => om[k]?.odds === newOdds ? om : { ...om, [k]: { odds: newOdds, ts } })
              return { ...l, odds: newOdds }
            })
            if (!legMutated) return o
            mutated = true
            totalMatches += 1
            let invSum = 0
            for (const l of legs) {
              const x = Number(l.odds ?? 0)
              if (x <= 0) { invSum = 0; break }
              invSum += 1 / x
            }
            const newProfit = invSum > 0 ? (1 / invSum - 1) * 100 : (o.guaranteed_profit_pct ?? 0)
            return { ...o, legs, guaranteed_profit_pct: newProfit, profit_pct: newProfit }
          })
          if (!mutated) {
            next[c] = opps
            continue
          }
          const sorted = [...updated]
            .filter((o: any) => !drainedEventIds.has(o.event_id))
            .sort((a: any, b: any) => (b.guaranteed_profit_pct ?? 0) - (a.guaranteed_profit_pct ?? 0))
          const drainedPart = updated.filter((o: any) => drainedEventIds.has(o.event_id))
          next[c] = [...sorted, ...drainedPart]
        }
        if (totalMatches > 0) console.log(`[live_provider_odds] ${pid} updated ${totalMatches} opps via ${Object.keys(updates).length} odd changes`)
        return next
      })
    }
    if (type === 'bet_recorded') {
      const id = `${data.provider_id}-${data.bet_id}-${Date.now()}`
      setBetRecordedToasts(prev => [
        ...prev,
        { id, kind: 'ok', pid: data.provider_id, bet_id: data.bet_id, odds: data.odds, stake: data.stake },
      ])
      setTimeout(() => setBetRecordedToasts(prev => prev.filter(t => t.id !== id)), 6000)
      load()
      return
    }
    if (type === 'bet_record_failed') {
      const id = `${data.provider_id}-fail-${Date.now()}`
      setBetRecordedToasts(prev => [
        ...prev,
        { id, kind: 'fail', pid: data.provider_id, reason: data.reason ?? 'unknown' },
      ])
      setTimeout(() => setBetRecordedToasts(prev => prev.filter(t => t.id !== id)), 8000)
      return
    }
    if (type === 'bet_record_deferred') {
      // Amber info toast (NOT red fail) — bookmaker accepted the bet but the
      // response didn't carry the actual_stake (common when stake-limited).
      // The reactive history sync will pick it up with the correct amount
      // once the user lands on the provider's history page. Auto-cleared
      // when bet_recorded for the same provider fires next.
      const id = `${data.provider_id}-defer-${Date.now()}`
      setBetRecordedToasts(prev => [
        ...prev,
        { id, kind: 'info', pid: data.provider_id, reason: `Open ${data.provider_id?.toUpperCase()} history to record actual stake` },
      ])
      setTimeout(() => setBetRecordedToasts(prev => prev.filter(t => t.id !== id)), 9000)
      return
    }
    if (type === 'arb_alignment') {
      setArbAllGreen(!!data.all_green)
      setArbProfitPct(data.current_profit_pct ?? data.profit_pct ?? null)
      setArbLegs(prev => {
        if (!prev) return prev
        const incoming: Record<string, any> = {}
        for (const l of (data.legs ?? [])) incoming[l.provider_id] = l
        return prev.map(leg => {
          const update = incoming[leg.provider_id]
          if (!update) return leg
          return {
            ...leg,
            current_odds: update.current_odds ?? leg.current_odds,
            planned_odds: update.planned_odds ?? leg.planned_odds,
            drift_pct: update.drift_pct ?? leg.drift_pct,
            current_stake: update.current_stake ?? leg.current_stake,
            slip_state: update.slip_state ?? leg.slip_state,
          }
        })
      })
    }
    if (type === 'arb_anchor_placed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.provider_id ? { ...l, placed: true, current_stake: data.actual_stake ?? l.current_stake, current_odds: data.actual_odds ?? l.current_odds } : l) : prev)
    }
    if (type === 'arb_anchor_rejected') {
      setArbDethroneToast(`Anchor rejected: ${data.reason ?? 'unknown'}`)
      setTimeout(() => setArbDethroneToast(null), 4000)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    if (type === 'arb_hedge_placed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.counter_provider ? { ...l, placed: true, current_stake: data.actual_stake ?? l.current_stake, current_odds: data.actual_odds ?? l.current_odds } : l) : prev)
    }
    if (type === 'arb_hedge_failed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.counter_provider ? { ...l, failed_reason: data.reason ?? 'failed', slip_state: 'red' } : l) : prev)
    }
    if (type === 'arb_dethroned') {
      const diff = data.new_profit != null && data.old_profit != null
        ? data.new_profit - data.old_profit
        : null
      const delta = diff != null
        ? `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}pp`
        : ''
      setArbDethroneToast(`Switched to higher-edge opp ${delta}`)
      setTimeout(() => setArbDethroneToast(null), 3500)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    if (type === 'arb_complete') {
      setTimeout(() => {
        setArbLegs(null)
        setArbAllGreen(false)
        setArbProfitPct(null)
        setArbGroupId(null)
        setCurrentBetReady(null)
      }, 5000)
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
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    // Update per-provider status from individual events
    if (type === 'provider_opening' || type === 'login_waiting' || type === 'login_detected' ||
        type === 'settling_pending' || type === 'settling_done' ||
        type === 'provider_ready' || type === 'provider_running' ||
        type === 'bet_ready' || type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed') {
      const epid = data.provider_id || data.bet?.provider_id
      if (epid) {
        // bet_skipped with reason='paused' means the runner is unwinding to
        // the gate after the user clicked Pause — don't stamp a 'navigating'
        // state that would flap the card green between pause and the
        // following provider_ready event.
        if (type === 'bet_skipped' && data.reason === 'paused') return
        // settling_done is a transient signal — the runner immediately fires
        // provider_running / bet_ready next. Don't stamp 'settling' on
        // settling_done (would leave the card stuck in "Logged in · syncing"
        // when the no-pending fast-path emits settling_done without a prior
        // settling_pending). Let the next event set the state.
        if (type === 'settling_done') return
        setLoopProviderStatus(prev => ({
          ...prev,
          [epid]: {
            state: type === 'provider_ready' ? 'ready_to_run' :
                   type === 'provider_running' ? 'running' :
                   type === 'bet_ready' ? 'ready' :
                   type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed' ? 'navigating' :
                   type === 'login_waiting' ? 'login_waiting' :
                   type === 'login_detected' ? 'settling' :
                   type === 'settling_pending' ? 'settling' : 'opening',
            current_bet: data.bet || null,
          }
        }))
      }
    }
  }, [mirror.lastEvent])

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

  // TTK = time-to-kickoff. Generic over any ISO start time so we can render
  // it on value bets (uses `start_time`), arb opps (uses `starts_at`) and
  // pending rows alike. Negative values (event already started) flow back
  // as -<m>m / -<h>h so the UI can display "started 8m ago".
  const fmtTtkFromIso = (iso: string | null | undefined): string => {
    if (!iso) return '—'
    const t = new Date(iso).getTime()
    if (Number.isNaN(t)) return '—'
    const diffMs = t - Date.now()
    const past = diffMs < 0
    const m = Math.abs(diffMs) / 60000
    const h = m / 60
    const d = h / 24
    let s: string
    if (m < 60) s = `${Math.round(m)}m`
    else if (h < 48) s = `${Math.round(h)}h`
    else s = `${Math.round(d)}d`
    return past ? `-${s}` : s
  }
  const ttkClass = (iso: string | null | undefined): string => {
    if (!iso) return 'text-zinc-600'
    const diffMs = new Date(iso).getTime() - Date.now()
    if (Number.isNaN(diffMs)) return 'text-zinc-600'
    if (diffMs < 0) return 'text-red-400'         // already started — pre-match arb is dead
    if (diffMs < 15 * 60_000) return 'text-amber-400'  // <15m — urgent, place now
    if (diffMs < 60 * 60_000) return 'text-yellow-300' // <1h
    return 'text-zinc-400'
  }
  const getTtkHours = (b: BatchBet) => {
    if (!b.start_time) return null
    const diff = new Date(b.start_time).getTime() - Date.now()
    return diff > 0 ? diff / 3600000 : 0
  }
  const fmtTtk = (b: BatchBet) => fmtTtkFromIso(b.start_time)

  // Blacklist: hide value bets where (event, market) is already placed.
  // Two-tier matching, same as Section A:
  //   1. exact event_id|market match (scanner-stamped bets)
  //   2. fuzzy event_name + market via bigram-Dice ≥ 0.55 — covers
  //      Polymarket positions which record with event_id="" market=""
  //      (the positions API doesn't carry them). 1x2 ↔ moneyline
  //      normalised; market="" treated as wildcard (Polymarket positions
  //      blacklist any market on the matched event, which is what we want
  //      since the user can only buy one outcome per Polymarket market).
  // Other markets on the same event stay visible — playing ML doesn't hide
  // an O/U row, but it does hide the ML row even if event_id is empty.
  const valueBetBlacklist = useMemo(() => {
    const stop = new Set(['vs', 'v', 'and', 'the', 'fc', 'cf', 'sc', 'fk', 'ec', 'esports', 'counter', 'strike'])
    const normalise = (s: string): string =>
      (s || '')
        .toLowerCase()
        .replace(/[.,:;!?'"`()/\\-]/g, ' ')
        .split(/\s+/)
        .filter(t => t.length >= 2 && !stop.has(t))
        .join(' ')
    const toBigrams = (s: string): Set<string> => {
      const out = new Set<string>()
      const clean = s.replace(/\s+/g, '')
      for (let i = 0; i < clean.length - 1; i++) out.add(clean.slice(i, i + 2))
      return out
    }
    const exactKeys = new Set<string>()
    const fuzzy: { bigrams: Set<string>; market: string }[] = []
    for (const provBets of Object.values(pendingByProvider)) {
      for (const b of provBets as any[]) {
        const mk = b?.market === '1x2' ? 'moneyline' : (b?.market ?? '')
        const eid = b?.event_id
        if (eid && mk) exactKeys.add(`${eid}|${mk}`)
        const name = b?.event_name || (b?.home_team && b?.away_team ? `${b.home_team} v ${b.away_team}` : '')
        if (name) {
          const norm = normalise(name)
          if (norm.length >= 4) fuzzy.push({ bigrams: toBigrams(norm), market: mk || '*' })
        }
      }
    }
    return {
      exactKeys,
      matchesByName: (b: BatchBet): boolean => {
        const oppName = `${b.display_home || ''} ${b.display_away || ''}`.trim()
        const norm = normalise(oppName)
        if (norm.length < 4) return false
        const oppBigrams = toBigrams(norm)
        const oppMk = b.market === '1x2' ? 'moneyline' : b.market
        for (const { bigrams, market } of fuzzy) {
          if (market !== '*' && market !== oppMk) continue
          let shared = 0
          for (const x of oppBigrams) if (bigrams.has(x)) shared++
          const dice = (2 * shared) / (oppBigrams.size + bigrams.size || 1)
          if (dice >= 0.55) return true
        }
        return false
      },
    }
  }, [pendingByProvider])

  const bets = batch.filter(b => {
    if (!UNLIMITED_PROVIDERS.has(b.provider_id)) return false
    if (b.edge_pct <= 0) return false
    const h = getTtkHours(b)
    if (h != null && h > ttkFilter) return false
    const mk = b.market === '1x2' ? 'moneyline' : b.market
    if (b.event_id && mk && valueBetBlacklist.exactKeys.has(`${b.event_id}|${mk}`)) return false
    if (valueBetBlacklist.matchesByName(b)) return false
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
  // ALWAYS surface all 4 unlimited cluster sections — even with 0 bets / 0
  // balance / 0 pending — so the user can see the system is monitoring each
  // (kalshi/cloudbet/etc.) and not silently dropped. The cluster header still
  // renders, just with empty stats.
  for (const pid of UNLIMITED_PROVIDERS) {
    const cluster = providerToCluster[pid] || pid
    if (!byCluster[cluster]) byCluster[cluster] = []
  }
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
    const totalBal = [...providers].reduce((s, p) => s + getBalance(providerBalances[p]), 0)
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

  // Polymarket + Kalshi are cents/dollar markets — odds derived from a 1-99¢
  // probability price, stakes in USD. Everyone else is decimal-odds + SEK.
  const isCentsMarket = (provider_id: string | undefined | null): boolean =>
    provider_id === 'polymarket' || provider_id === 'kalshi'
  const fmtStake = (b: BatchBet) => isCentsMarket(b.provider_id) ? `$${b.stake.toFixed(2)}` : `${Math.round(b.stake)} kr`
  const fmtEv = (b: BatchBet) => isCentsMarket(b.provider_id) ? `+$${b.expected_profit.toFixed(2)}` : `+${b.expected_profit.toFixed(0)} kr`

  // Compact diagnostic badges for a value-bet row. Reads `annotations`
  // populated by the backend analyzer; renders nothing when null/empty.
  // SHARP/LAG/STALE comes from consensus_lean, STEAM from cross-book
  // movement, KEY from NFL spread/total key-number proximity.
  const renderAnnotationBadges = (b: any) => {
    const ann = b?.annotations
    if (!ann || typeof ann !== 'object') return null
    const lean = ann.consensus_lean?.lean as ('sharp_value' | 'market_lag' | 'stale_outlier' | undefined)
    const steam = ann.steam_signal as { direction?: 'up' | 'down'; provider_count?: number } | null | undefined
    const kn = ann.key_number as { on_key?: boolean; straddles_key?: boolean; nearest_key?: number } | null | undefined
    const cl = ann.consensus_lean as { divergence_pp?: number; n_soft_books?: number } | null | undefined
    return (
      <span className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider">
        {lean === 'sharp_value' && (
          <span
            className="px-1 py-0.5 rounded border bg-green-900/30 border-green-600/40 text-green-300"
            title={`Soft consensus says outcome is LESS likely than fair (divergence ${cl?.divergence_pp?.toFixed(1)}pp across ${cl?.n_soft_books} books) — we're with the sharps, against the public.`}
          >sharp</span>
        )}
        {lean === 'stale_outlier' && (
          <span
            className="px-1 py-0.5 rounded border bg-red-900/30 border-red-600/40 text-red-300"
            title={`Soft consensus says outcome is MORE likely than fair (divergence +${cl?.divergence_pp?.toFixed(1)}pp across ${cl?.n_soft_books} books) — public has loaded this side; our book is a stale outlier likely to move against us.`}
          >stale</span>
        )}
        {lean === 'market_lag' && (
          <span
            className="px-1 py-0.5 rounded border bg-zinc-800/60 border-zinc-600/40 text-zinc-400"
            title={`Soft consensus matches sharp; this is just a single-book lag.`}
          >lag</span>
        )}
        {steam?.direction && (
          <span
            className={`px-1 py-0.5 rounded border ${
              steam.direction === 'up'
                ? 'bg-cyan-900/30 border-cyan-600/40 text-cyan-300'
                : 'bg-orange-900/30 border-orange-600/40 text-orange-300'
            }`}
            title={`Steam: ${steam.provider_count} books moved ${steam.direction === 'up' ? 'TOWARD' : 'AWAY FROM'} this outcome in the last few minutes.`}
          >steam {steam.direction === 'up' ? '▲' : '▼'} {steam.provider_count}</span>
        )}
        {(kn?.on_key || kn?.straddles_key) && (
          <span
            className="px-1 py-0.5 rounded border bg-amber-900/30 border-amber-600/40 text-amber-300"
            title={`NFL key number ${kn.nearest_key} — ${kn.on_key ? 'point sits exactly on a key' : 'half-point straddle of a key (high-leverage)'}.`}
          >key {kn.nearest_key}</span>
        )}
      </span>
    )
  }
  // Compact market label so the user can tell at a glance whether the bet is
  // on the moneyline / total / spread / 1x2. Includes the line value for
  // total/spread (e.g. "O/U 215.5"). Falls back to the raw market string for
  // anything we don't have a friendly name for.
  const fmtMarket = (b: BatchBet) => {
    const m = (b.market || '').toLowerCase()
    if (m === 'moneyline' || m === '1x2') return m === '1x2' ? '1X2' : 'ML'
    if (m === 'total') return b.point != null ? `O/U ${b.point}` : 'O/U'
    if (m === 'spread') {
      if (b.point == null) return 'SPREAD'
      const sign = b.point > 0 ? '+' : ''
      return `SPR ${sign}${b.point}`
    }
    return (b.market || '').toUpperCase() || '—'
  }
  // Polymarket prices are quoted in cents (¢) on the trading site. Show both
  // decimal and cent so the user can manually cross-check against what the
  // Polymarket Chromium tab displays before clicking Buy. Keep 2-decimal
  // precision on the cent value (no integer rounding) — Polymarket's betslip
  // shows fractional cents and we need an exact match for confirmation.
  const oddsToCents = (odds: number) => odds > 0 ? (100 / odds).toFixed(2) : '0.00'
  // `isCents` named generically because both polymarket and kalshi need the
  // cent display, not just polymarket.
  const fmtOddsWithCents = (odds: number, isCents: boolean) =>
    isCents ? `${odds.toFixed(2)} (${oddsToCents(odds)}¢)` : odds.toFixed(2)

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
        {/* Live polling diagnostic — shows backend's logged_in answer per
            unlimited provider in real time. If a chip below shows green but
            the diag here shows ✗, the frontend has stale state. */}
        <div className="ml-4 flex items-center gap-2 text-[10px] font-mono text-zinc-500">
          <span className="text-zinc-600">poll:</span>
          {Array.from(UNLIMITED_PROVIDERS).map(pid => {
            const isIn = loggedInProviders.has(pid)
            return (
              <span key={pid} className={isIn ? 'text-emerald-400' : 'text-zinc-600'}>
                {pid}={isIn ? '✓' : '✗'}
              </span>
            )
          })}
        </div>
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
          {/* Per-provider status renders under each provider row (not here).
             Global banner kept for non-scoped events only — most live status
             is now scoped per provider via providerStatus[pid]. */}
          {loopStatus && <span className="text-amber-400">{loopStatus}</span>}
        </div>
        {error && (
          <span className="text-red-400">
            {error}
            <span className="ml-2 text-amber-400 text-[10px] animate-pulse">● reconnecting…</span>
          </span>
        )}
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

      {/* DUTCH ARB / WAITING widget moved per-provider — see the per-cluster
          render below where it appears under each provider header row, scoped
          to that provider's currently-picked event_id (pickedEventByProvider). */}

      {arbDethroneToast && (
        <div className="border-b border-amber-700/50 bg-amber-900/10 px-3 py-1.5 text-xs text-amber-300">
          {arbDethroneToast}
        </div>
      )}

      {/* Floating bet-recorded confirmations (top-right). Auto-dismiss after
          ~6s. Stack vertically when multiple legs of an arb land close in time. */}
      {betRecordedToasts.length > 0 && (
        <div className="fixed top-3 right-3 z-50 flex flex-col gap-2 max-w-sm">
          {betRecordedToasts.map(t => {
            const palette =
              t.kind === 'ok'
                ? 'bg-emerald-900/80 border-emerald-600/60 text-emerald-100'
                : t.kind === 'info'
                  ? 'bg-amber-900/80 border-amber-600/60 text-amber-100'
                  : 'bg-red-900/80 border-red-600/60 text-red-100'
            const label =
              t.kind === 'ok'
                ? 'Bet recorded'
                : t.kind === 'info'
                  ? 'Awaiting history sync'
                  : 'Record failed'
            return (
              <div key={t.id} className={`px-3 py-2 rounded shadow-lg border text-xs font-mono ${palette}`}>
                <div className="font-semibold uppercase tracking-wider text-[10px]">
                  {label} · {t.pid?.toUpperCase()}
                </div>
                <div className="text-zinc-200 mt-0.5">
                  {t.kind === 'ok'
                    ? `bet #${t.bet_id} · @${(t.odds ?? 0).toFixed(2)} · ${Math.round(t.stake ?? 0)} kr · synced to DB`
                    : t.reason}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Per-provider status rows.
          Only rendered for state='ready' — the only state that needs the
          global banner because it carries current-bet context + the Skip
          button. All other states (login_waiting, settling, navigating,
          placing, running) are duplicated by the per-provider card badge
          inside each cluster, so showing them here is redundant noise.
          Polymarket is rendered inline inside its own cluster header below
          (search for "POLYMARKET inline status") — keeps the ready/Skip
          control next to the polymarket bets list. */}
      {loopRunning && loopProviderStatus && Object.keys(loopProviderStatus).length > 0 && (
        <div className="border-b border-zinc-800">
          {Object.entries(loopProviderStatus)
            .filter(([pid, status]: [string, any]) => pid !== 'polymarket' && status?.state === 'ready')
            .map(([pid, status]: [string, any]) => (
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
                  <span className="text-[10px] font-mono text-zinc-200">
                    @ {fmtOddsWithCents((status.current_bet.live_odds ?? status.current_bet.odds) ?? 0, isCentsMarket(pid))}
                  </span>
                  {status.current_bet.edge_pct != null && <span className="text-[10px] text-green-400">+{status.current_bet.edge_pct?.toFixed(1)}%</span>}
                  <span className="text-[10px] font-mono text-zinc-500">{Math.round(status.current_bet.stake ?? 0)} kr</span>
                </>
              )}
              {status.state === 'ready' && (
                <div className="ml-auto flex items-center gap-2">
                  {/* No Place button — user always pulls the trigger directly on the
                      provider's site (Playwright tab). Runner intercepts the placement
                      XHR / WebSocket frame and records to DB. */}
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
            // Keep unlimited providers — see loadArbOpps comment. Unlimited
            // ↔ unlimited arbs (mostly pinnacle+polymarket on esports) appear
            // here as their own cluster cards.
            const cluster = resolveSoftCluster(pid)
            if (!softByCluster[cluster]) softByCluster[cluster] = []
            if (!softByCluster[cluster].includes(pid)) softByCluster[cluster].push(pid)
          }

          // Qualifying-soft predicate: render a soft card if the user has
          // ANY of the three actionable signals:
          //   1. balance >= DRAIN_THRESHOLD_SEK (usable for a real stake)
          //   2. pending bets (something to settle)
          //   3. unclaimed bonus (something to claim)
          const isQualifiedSoft = (pid: string) => {
            const bal = getBalance(providerBalances[pid])
            const trig = getTrigger(providerBalances[pid])
            const pending = pendingByProvider[pid]?.length ?? 0
            return bal >= DRAIN_THRESHOLD_SEK || (trig?.amount ?? 0) > 0 || pending > 0
          }
          // Alias: kept for the per-cluster funded-count display below; same
          // predicate now (balance OR pending counts as "funded" for card-
          // visibility purposes, which matches what the user actually cares
          // about — anything they can act on).
          const isFunded = isQualifiedSoft

          // Cluster gate: render only clusters where at least one member is
          // qualified. Within a qualified cluster, only the qualified members
          // render — siblings with 0 balance/bonus/pending stay hidden.
          const clusterHasFocused = (cluster: string) =>
            (softByCluster[cluster] ?? []).some(isQualifiedSoft)
          const clusterHasFunded = clusterHasFocused
          const clusterHasQualifyingArb = (cluster: string) =>
            (oppsByCluster[cluster] ?? []).some(
              (o: any) => (o.guaranteed_profit_pct ?? 0) >= DEPOSIT_HINT_MIN_PROFIT_PCT,
            )
          const visibleClusters = Object.keys(softByCluster).filter(
            c => clusterHasFocused(c) && (clusterHasFunded(c) || clusterHasQualifyingArb(c)),
          )

          // Sort: unlimited (sharp) clusters first in a fixed order, then named
          // soft clusters in declaration order, then remaining standalones alpha.
          const sharpOrder = ['pinnacle', 'polymarket', 'kalshi', 'cloudbet']
          const namedClusters = Object.keys(SOFT_CLUSTER_MEMBERS)
          const clusterOrder = visibleClusters.sort((a, b) => {
            const as = sharpOrder.indexOf(a)
            const bs = sharpOrder.indexOf(b)
            if (as >= 0 && bs >= 0) return as - bs
            if (as >= 0) return -1
            if (bs >= 0) return 1
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
                {/* Counter-pool filter: click a chip to disable that provider as
                    a hedge counter. Last-enabled chip is sticky (min 1). Re-scans
                    on every change. Lets the user narrow to e.g. BETINIA-vs-
                    Pinnacle-only by deselecting the other 3. */}
                <span className="text-[9px] text-zinc-600 uppercase tracking-wider ml-2 mr-1">counters:</span>
                {Array.from(UNLIMITED_PROVIDERS).map(cpid => {
                  const enabled = enabledCounters.has(cpid)
                  const isLastEnabled = enabled && enabledCounters.size === 1
                  return (
                    <button
                      key={cpid}
                      onClick={() => toggleCounter(cpid)}
                      disabled={isLastEnabled}
                      className={`px-1.5 py-0.5 text-[9px] uppercase font-semibold rounded border transition-colors ${
                        enabled
                          ? 'bg-emerald-500/20 text-emerald-100 border-emerald-500/40 hover:bg-emerald-500/30'
                          : 'bg-zinc-800/40 text-zinc-500 border-zinc-700/40 hover:bg-zinc-800/70'
                      } ${isLastEnabled ? 'cursor-not-allowed opacity-80' : 'cursor-pointer'}`}
                      title={isLastEnabled
                        ? `${cpid} — must keep at least one counter enabled`
                        : enabled
                          ? `Click to exclude ${cpid} as a hedge counter`
                          : `Click to include ${cpid} as a hedge counter`}
                    >
                      {cpid}
                    </button>
                  )
                })}
                <span className="text-[10px] text-zinc-600 ml-auto">
                  top 20 per cluster · siblings share odds · drained excluded
                </span>
              </div>

              {clusterOrder.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-zinc-600">
                  No soft books configured.
                </div>
              ) : (
                <div className="flex flex-col">
                  {clusterOrder.map(cluster => {
                    // Render only qualified members (balance > threshold,
                    // unclaimed bonus, or pending bets). Siblings that are
                    // fully drained AND have no bonus/pending stay hidden so
                    // the UI shows only what the user can act on.
                    const members = (softByCluster[cluster] ?? []).filter(isQualifiedSoft)
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
                          {/* Per-member deposit hints — each cluster member with its bonus trigger */}
                          <div className="flex flex-wrap gap-3 px-3 py-2 bg-zinc-900/20 border-b border-zinc-800/30 text-[11px]">
                            {members.map(pid => {
                              // If the ONLY reason this member qualifies is a bonus
                              // trigger (no balance, no pending), expose a "mark
                              // claimed" affordance so the user can hide the card
                              // after they've taken the bonus on another account.
                              // POSTs /bankroll/claim-bonus/{pid} → bonus_status
                              // flips to "claimed" → next bankroll poll drops the
                              // provider from isQualifiedSoft.
                              const bal = getBalance(providerBalances[pid])
                              const trig = getTrigger(providerBalances[pid])
                              const pending = pendingByProvider[pid]?.length ?? 0
                              const onlyBonus =
                                (trig?.amount ?? 0) > 0 && bal < DRAIN_THRESHOLD_SEK && pending === 0
                              return (
                                <div key={pid} className="flex items-center gap-1.5">
                                  <span className="text-zinc-400 uppercase text-[10px] tracking-wider">{pid}</span>
                                  <BalanceCell pid={pid} balances={providerBalances} />
                                  {onlyBonus && (
                                    <button
                                      onClick={async (e) => {
                                        e.stopPropagation()
                                        try {
                                          const r = await fetch(`/api/bankroll/claim-bonus/${pid}`, { method: 'POST' })
                                          if (!r.ok) throw new Error(`status ${r.status}`)
                                          load()  // immediate refresh; the 5-s poll would
                                                  // catch it eventually but the user clicked
                                                  // so feedback should be snappy.
                                        } catch (err) {
                                          console.warn(`[claim-bonus] ${pid} failed`, err)
                                        }
                                      }}
                                      className="px-1.5 py-0.5 text-[9px] uppercase tracking-wider rounded bg-zinc-800 text-zinc-400 border border-zinc-700 hover:bg-zinc-700 hover:text-zinc-200 cursor-pointer"
                                      title={`Mark ${pid.toUpperCase()}'s bonus as claimed — hides this row. Reversible from Bankroll tab.`}
                                    >
                                      mark claimed
                                    </button>
                                  )}
                                </div>
                              )
                            })}
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
                                  const mkt = (opp.market ?? '').toLowerCase()
                                  // home/away: plain team name, except spread which shows the
                                  // signed handicap line so the user can verify both legs are
                                  // on the same line. per-leg point preferred; else derive
                                  // from opp.point (home = opp.point, away = its complement).
                                  if (o === 'home' || o === 'away') {
                                    const name = o === 'home' ? (opp.display_home || 'Home') : (opp.display_away || 'Away')
                                    if (mkt !== 'spread') return name
                                    let pt = leg.point
                                    if (pt == null) pt = o === 'home' ? opp.point : (opp.point != null ? -opp.point : null)
                                    return pt != null ? `${name} ${pt > 0 ? '+' : ''}${pt}` : name
                                  }
                                  if (o === 'draw') return 'Draw'
                                  const pt = leg.point ?? opp.point
                                  if (o === 'over' && pt != null) return `Over ${pt}`
                                  if (o === 'under' && pt != null) return `Under ${pt}`
                                  if (pt != null) return `${o} ${pt}`
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
                                    <td
                                      className="px-2 py-1 text-zinc-500 text-[10px] uppercase cursor-help"
                                      title={(() => {
                                        const m = (opp.market ?? '') as string
                                        const sport = (opp.sport ?? '') as string
                                        const legCount = (opp.legs ?? []).length
                                        if (m === '1x2') return `1X2 — 3-way (home/draw/away). For hockey/soccer regulation. Pinnacle: s;6;m for hockey, s;0;m for soccer.`
                                        if (m === 'moneyline') {
                                          if (legCount === 3) return `MONEYLINE — 3-way (home/draw/away). Treated like 1X2. Pinnacle: s;0;m or s;6;m.`
                                          return `MONEYLINE — 2-way (home/away), full game incl. OT/SO. Pinnacle: s;0;m.`
                                        }
                                        if (m === 'spread') return `SPREAD ${opp.point ?? ''} — ${sport === 'tennis' ? 'WARNING: tennis sets vs games unit mismatch — extractor should be skipping' : '2-way handicap'}. Pinnacle: s;<period>;s;<point>.`
                                        if (m === 'total') return `TOTAL ${opp.point ?? ''} — over/under. Pinnacle: s;<period>;ou;<point>.`
                                        return m
                                      })()}
                                    >{opp.market ?? ''}</td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">bet</span>
                                      <span className="text-green-400 font-semibold">{anchorOutcome}</span>
                                      <span className="text-zinc-600 mx-1">on</span>
                                      <span className="text-zinc-400 uppercase text-[10px]">{anchorPid}</span>
                                      <span className="font-mono text-zinc-200 ml-2">{formatOddsDisplay(anchorPid, Number(anchorLeg.odds ?? 0))}</span>
                                    </td>
                                    <td className="px-2 py-1 text-[11px]">
                                      <div className="flex flex-col gap-0.5">
                                        {counters.map((leg: any, li: number) => (
                                          <div key={li} className="flex items-center gap-1">
                                            <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">hedge</span>
                                            <span className="text-pink-400 font-semibold">{resolveLegOutcome(leg)}</span>
                                            <span className="text-zinc-600">on</span>
                                            <span className="text-zinc-400 uppercase text-[10px]">{leg.provider ?? leg.provider_id}</span>
                                            <span className="font-mono text-zinc-300 ml-2">{formatOddsDisplay(leg.provider ?? leg.provider_id, Number(leg.odds ?? 0))}</span>
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
                          {/* Verify-all button: hits the targeted refresh for every visible
                              Pinnacle-side arb in this cluster, in series. Single-active
                              across the whole UI — disabled while ANY cluster is verifying.
                              Once done, the list is auto-sorted by real-time profitability. */}
                          {(() => {
                            const isThisCluster = verifyingCluster === cluster
                            const isAnyRunning = verifyingCluster !== null
                            const hasPinnacle = (opps as any[]).some(o =>
                              (o.legs ?? []).some((l: any) => (l.provider ?? l.provider_id) === 'pinnacle'),
                            )
                            if (!hasPinnacle) return null
                            return (
                              <button
                                onClick={() => verifyArbsInCluster(cluster)}
                                disabled={isAnyRunning}
                                className={`px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider rounded border transition-colors ${
                                  isThisCluster
                                    ? 'bg-amber-700/40 text-amber-200 border-amber-600/50 cursor-wait animate-pulse'
                                    : isAnyRunning
                                      ? 'bg-zinc-800 text-zinc-600 border-zinc-700 cursor-not-allowed'
                                      : 'bg-purple-900/30 text-purple-200 border-purple-700/50 hover:bg-purple-900/50 cursor-pointer'
                                }`}
                                title="Verify each top-20 arb's live Pinnacle odds in series. Real top arbs surface; ghost arbs (stale-data positives) drop."
                              >
                                {isThisCluster && verifyProgress
                                  ? `Verifying ${verifyProgress.done}/${verifyProgress.total}…`
                                  : 'Verify all'}
                              </button>
                            )
                          })()}
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {opps.length} arb{opps.length === 1 ? '' : 's'} · siblings share odds
                          </span>
                        </div>

                        {/* One card per funded sibling — same opps, different balance/active context */}
                        {funded.map(pid => {
                          const bal = getBalance(providerBalances[pid])
                          const pending = pendingByProvider[pid]?.length ?? 0
                          const cardState = deriveCardState(tabOpenProviders.has(pid), loggedInProviders.has(pid))
                          return (
                            <div key={pid} className="border-b border-zinc-800/30 last:border-b-0">
                              {/* Provider header — activate button + state */}
                              <div className="flex items-center gap-2 px-6 py-1.5 bg-zinc-900/20">
                                <button
                                  onClick={() => handleCardClick(pid)}
                                  className={`px-2 py-0.5 text-[10px] rounded transition-colors cursor-pointer ${CARD_STATE_CLASSES[cardState]}`}
                                  title={
                                    cardState === 'no_tab' ? 'Click to open the provider site' :
                                    cardState === 'tab_open_not_in' ? 'Tab open — click to refresh if it closed silently' :
                                    'Tab open + logged in'
                                  }
                                >
                                  <span className="uppercase font-semibold">{pid}</span>
                                  {cardState === 'tab_open_not_in' && (
                                    <span className="ml-2 inline-block px-1.5 py-0.5 text-[9px] rounded bg-red-400/25 text-red-100 font-semibold">
                                      Log in to continue
                                    </span>
                                  )}
                                  {cardState === 'tab_open_logged' && (
                                    <span className="ml-2 inline-block px-1.5 py-0.5 text-[9px] rounded bg-emerald-500/25 text-emerald-100 font-semibold">
                                      Logged in
                                    </span>
                                  )}
                                  <span className="ml-1 text-green-400 font-mono">
                                    <BalanceCell pid={pid} balances={providerBalances} />
                                  </span>
                                </button>
                                {pending > 0 && <span className="text-[10px] text-amber-400">{pending}p pending</span>}
                                {stakeCaps[pid] && (
                                  <span className="px-1 py-px text-[8px] font-bold bg-orange-900/50 text-orange-400 border border-orange-700/50 rounded">
                                    ≤{Math.round(stakeCaps[pid])}
                                  </span>
                                )}
                                {/* Bonus-only provider: the only reason this card
                                    showed up is an unclaimed bonus. Surface a
                                    "mark claimed" affordance — POST flips
                                    bonus_status to "claimed" and the next bankroll
                                    poll drops the card. Reversible from Bankroll
                                    tab. Skipped when the user has real balance or
                                    pending bets (those are independent reasons to
                                    keep the card visible). */}
                                {(() => {
                                  const trig = getTrigger(providerBalances[pid])
                                  const onlyBonus =
                                    (trig?.amount ?? 0) > 0 && bal < DRAIN_THRESHOLD_SEK && pending === 0
                                  if (!onlyBonus) return null
                                  return (
                                    <button
                                      onClick={async (e) => {
                                        e.stopPropagation()
                                        try {
                                          const r = await fetch(`/api/bankroll/claim-bonus/${pid}`, { method: 'POST' })
                                          if (!r.ok) throw new Error(`status ${r.status}`)
                                          load()
                                        } catch (err) {
                                          console.warn(`[claim-bonus] ${pid} failed`, err)
                                        }
                                      }}
                                      className="px-1.5 py-0.5 text-[9px] uppercase tracking-wider rounded bg-zinc-800 text-zinc-400 border border-zinc-700 hover:bg-zinc-700 hover:text-zinc-200 cursor-pointer"
                                      title={`Mark ${pid.toUpperCase()}'s bonus as claimed — hides this row. Reversible from Bankroll tab.`}
                                    >
                                      mark claimed
                                    </button>
                                  )
                                })()}
                                {/* Counter sites — same login/balance status as the anchor.
                                    The four unlimited providers are the auto-hedge pool; rendering
                                    them inline next to the anchor lets the user see at a glance
                                    which counters are tab-open / logged-in / funded before pressing Run. */}
                                <div className="ml-auto flex items-center gap-1 flex-wrap">
                                  <span className="text-[9px] text-zinc-600 uppercase tracking-wider mr-1">counter</span>
                                  {Array.from(UNLIMITED_PROVIDERS).map(cpid => {
                                    const cBal = getBalance(providerBalances[cpid])
                                    const cBalRaw = providerBalances[cpid]
                                    const cBalDisplay = (typeof cBalRaw === 'object' && cBalRaw?.balance_native != null) ? cBalRaw.balance_native : cBal
                                    const cIsLoggedIn = loggedInProviders.has(cpid)
                                    const cIsTabOpen = tabOpenProviders.has(cpid)
                                    const native = cpid === 'polymarket' || cpid === 'kalshi' || cpid === 'cloudbet'
                                    return (
                                      <span key={cpid}
                                        className={`px-1.5 py-0.5 text-[9px] rounded border ${
                                          cIsLoggedIn
                                            ? 'bg-emerald-500/20 text-emerald-100 border-emerald-500/40'
                                            : cIsTabOpen
                                              ? 'bg-amber-500/20 text-amber-100 border-amber-500/40'
                                              : 'bg-zinc-800/40 text-zinc-500 border-zinc-700/40'
                                        }`}
                                        title={cIsLoggedIn ? 'Logged in' : cIsTabOpen ? 'Tab open — awaiting login' : 'Tab not open'}
                                      >
                                        <span className="uppercase font-semibold">{cpid}</span>
                                        {cBalDisplay > 0 && (
                                          <span className="ml-1 text-green-400 font-mono">
                                            {native ? '$' : ''}{cBalDisplay.toFixed(2)}{native ? '' : ' kr'}
                                          </span>
                                        )}
                                      </span>
                                    )
                                  })}
                                </div>
                              </div>
                              {(() => {
                                // Per-provider arb widget — DUTCH ARB / WAITING moved here from
                                // the global header. Tracks the event the user most recently
                                // picked for this provider (pickedEventByProvider[pid]) and
                                // shows: profit %, all legs (anchor + counters) with planned
                                // vs synced state, and the green ring once every leg is
                                // synced (== ready to place).
                                const pickedEid = pickedEventByProvider[pid]
                                if (!pickedEid) {
                                  if (!providerStatus[pid]) return null
                                  return (
                                    <div className="px-6 py-1 border-b border-zinc-800/30 bg-amber-950/20">
                                      <span className="text-[10px] text-amber-300 font-mono" title={providerStatus[pid] || ''}>
                                        {providerStatus[pid]}
                                      </span>
                                    </div>
                                  )
                                }
                                const pickedOpp = (opps as any[]).find((o: any) => o.event_id === pickedEid)
                                if (!pickedOpp) return null
                                const pickedLegs: any[] = pickedOpp.legs ?? pickedOpp.arb_legs ?? []
                                const synced = syncedLegs[pickedEid] ?? new Set<string>()
                                const picking = pickingLegs[pickedEid] ?? new Set<string>()
                                const allGreen = pickedLegs.length > 0 && pickedLegs.every((l: any) =>
                                  synced.has(legKey(l.provider ?? l.provider_id ?? '', l.outcome, l.point)),
                                )
                                const profit = pickedOpp.guaranteed_profit_pct ?? 0
                                const eventLabel = pickedOpp.display_home && pickedOpp.display_away
                                  ? `${pickedOpp.display_home} v ${pickedOpp.display_away}`
                                  : pickedOpp.event_id
                                // Per-leg stake breakdown for the picked opp.
                                // Anchor (this card's pid) is sized to full
                                // balance; counter legs match payout. Total
                                // stake = anchor + Σ counters; guaranteed
                                // payout is the SAME for every outcome (= the
                                // arb invariant). Profit = payout − total.
                                // Cluster-sharing: scanner often picks a sibling's leg
                                // (QUICKCASINO @ 1.62 beats BETINIA @ 1.60). Match
                                // by cluster so the soft anchor leg is found whether
                                // it's keyed on pid or any cluster sibling — without
                                // this, find() returned undefined → anchorOdds=0 →
                                // totalPayout=0 → every counter stake displayed as 0.
                                const pickedClusterForStakes = resolveSoftCluster(pid)
                                const isAnchorLeg = (l: any): boolean => {
                                  const lpid = (l.provider ?? l.provider_id ?? '') as string
                                  return lpid === pid || resolveSoftCluster(lpid) === pickedClusterForStakes
                                }
                                const anchorOddsPicked = Number(
                                  (pickedLegs.find(isAnchorLeg) ?? {}).odds ?? 0,
                                )
                                const pickedCounters = pickedLegs.filter((l: any) => !isAnchorLeg(l))
                                const payoutOverride = payoutOverridesByEid[pickedEid]
                                const pickedStakes = computeArbStakes(
                                  pid,
                                  anchorOddsPicked,
                                  pickedCounters,
                                  providerBalances,
                                  stakeCaps,
                                  payoutOverride,
                                )
                                const totalStakeSek =
                                  pickedStakes.anchorSek + pickedStakes.counterSekByLeg.reduce((s, x) => s + x, 0)
                                const guaranteedPayoutSek = pickedStakes.payout
                                const guaranteedProfitSek = guaranteedPayoutSek - totalStakeSek
                                const anchorBalance = getBalance(providerBalances[pid])
                                return (
                                  <div className="px-6 py-1.5 border-b border-zinc-800/30 bg-purple-950/15">
                                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                                      <span className="px-1.5 py-0.5 text-[9px] font-bold bg-purple-900/50 text-purple-300 border border-purple-700/50 rounded">DUTCH ARB</span>
                                      <span className={`text-[10px] font-mono font-semibold ${profit > 0 ? 'text-green-400' : 'text-red-400'}`}>
                                        {profit > 0 ? '+' : ''}{profit.toFixed(2)}%
                                      </span>
                                      <span className="text-[10px] text-zinc-400 truncate max-w-[260px]">{eventLabel}</span>
                                      <span
                                        className={`text-[10px] font-mono ${ttkClass(pickedOpp.starts_at)}`}
                                        title={pickedOpp.starts_at ? `kicks off ${new Date(pickedOpp.starts_at).toLocaleString()}` : 'no start time'}
                                      >
                                        {fmtTtkFromIso(pickedOpp.starts_at)}
                                      </span>
                                      {totalStakeSek > 0 && (
                                        <span className="text-[10px] text-zinc-500 font-mono" title="Total stake across all legs">
                                          total <span className="text-zinc-300">{Math.round(totalStakeSek)} kr</span>
                                        </span>
                                      )}
                                      {/* Editable target payout K (SEK). Each leg's stake = K / leg_odds.
                                          User can also type into any per-leg input below — does the same
                                          math from the other direction. Reset (×) restores max-balance
                                          default on the soft anchor. */}
                                      <span className="text-[10px] text-zinc-500 font-mono inline-flex items-center gap-1" title="Guaranteed payout — same regardless of outcome. Drives all leg stakes (stake_i = payout / odds_i).">
                                        payout
                                        <input
                                          type="number"
                                          min={0}
                                          step={1}
                                          value={Math.round(guaranteedPayoutSek)}
                                          onChange={(e) => {
                                            const raw = e.target.value
                                            if (raw === '') { setPayoutOverride(pickedEid, null); return }
                                            const n = Number(raw)
                                            if (isFinite(n) && n > 0) setPayoutOverride(pickedEid, n)
                                          }}
                                          className={`w-16 px-1 py-0 bg-zinc-900 border ${payoutOverride != null ? 'border-amber-500/50 text-amber-200' : 'border-zinc-700 text-zinc-300'} rounded text-[10px] font-mono text-right focus:outline-none focus:border-purple-500`}
                                        />
                                        kr
                                        {payoutOverride != null && (
                                          <button
                                            onClick={() => setPayoutOverride(pickedEid, null)}
                                            className="text-zinc-500 hover:text-zinc-200 px-0.5"
                                            title="Reset to max (anchor balance × odds)"
                                          >×</button>
                                        )}
                                      </span>
                                      {totalStakeSek > 0 && (
                                        <span className={`text-[10px] font-mono font-semibold ${guaranteedProfitSek >= 0 ? 'text-green-400' : 'text-red-400'}`} title="Guaranteed profit — same regardless of outcome">
                                          {guaranteedProfitSek >= 0 ? '+' : ''}{Math.round(guaranteedProfitSek)} kr
                                        </span>
                                      )}
                                      <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${
                                        allGreen ? 'bg-green-900/50 text-green-300' :
                                        picking.size > 0 ? 'bg-amber-900/50 text-amber-300 animate-pulse' :
                                        'bg-zinc-800 text-zinc-400'
                                      }`}>
                                        {allGreen ? 'ALL GREEN — place each tab' : picking.size > 0 ? 'CHECKING' : 'WAITING'}
                                      </span>
                                      {providerStatus[pid] && (
                                        <span className="ml-auto text-[10px] text-amber-300 font-mono truncate max-w-[260px]" title={providerStatus[pid] || ''}>
                                          {providerStatus[pid]}
                                        </span>
                                      )}
                                    </div>
                                    <div className="space-y-0.5 pl-1">
                                      {pickedLegs.map((leg: any, lidx: number) => {
                                        const rawLegPid = leg.provider ?? leg.provider_id ?? ''
                                        // Cluster-sharing: scanner often picks a sibling's leg
                                        // (e.g. QUICKCASINO @ 1.62 beats BETINIA @ 1.60) but the
                                        // user will actually place on `pid` (the funded cluster
                                        // member). Display `pid` so the widget's name matches the
                                        // tab the user clicks Place on. The underlying leg keeps
                                        // its real provider for stake-key / sync tracking.
                                        const isSiblingAnchor =
                                          rawLegPid !== pid &&
                                          clusterMemberSet.has(rawLegPid)
                                        const displayPid = isSiblingAnchor ? pid : rawLegPid
                                        const isAnchor = displayPid === pid
                                        const lk = legKey(rawLegPid, leg.outcome, leg.point)
                                        const isSynced = synced.has(lk)
                                        const isPicking = picking.has(lk)
                                        // Counter index = position within counters-only list. Anchor uses anchorSek.
                                        const counterIdx = pickedCounters.indexOf(leg)
                                        const stakeSek = isAnchor
                                          ? pickedStakes.anchorSek
                                          : (counterIdx >= 0 ? pickedStakes.counterSekByLeg[counterIdx] : 0)
                                        const oddsForLeg = Number(leg.odds ?? 0)
                                        const ifWinPayout = guaranteedPayoutSek > 0 ? guaranteedPayoutSek : (stakeSek * oddsForLeg)
                                        // USD providers (Polymarket / Kalshi) display in $ but their
                                        // balance is normalised to SEK in providerBalances. Show the
                                        // input in SEK so the math stays in one unit; the user sees
                                        // the native-currency display in the row below if they want.
                                        return (
                                          <div key={`${rawLegPid}-${lidx}`} className="flex items-center gap-2 text-[10px]">
                                            <span className={`inline-block w-2 h-2 rounded-full ${
                                              isSynced ? 'bg-green-400' : isPicking ? 'bg-amber-400 animate-pulse' : 'bg-zinc-600'
                                            }`} />
                                            <span className="text-zinc-400 uppercase w-20">{displayPid}</span>
                                            <span className="font-mono text-zinc-300 w-24">{formatOddsDisplay(displayPid, oddsForLeg)}</span>
                                            {/* Outcome → team-name. Bookmaker tabs show team names,
                                                not "home"/"away" — matching the user's mental model
                                                ("I'm betting on Lilli Tagger") is faster than asking
                                                them to translate the side label. Falls back to the
                                                raw outcome for over/under/draw / no-team markets. */}
                                            <span className="text-zinc-300 w-32 truncate" title={`outcome: ${leg.outcome ?? ''}`}>
                                              {(() => {
                                                const out = leg.outcome ?? ''
                                                if (out === 'home') return pickedOpp.display_home || 'Home'
                                                if (out === 'away') return pickedOpp.display_away || 'Away'
                                                if (out === 'draw') return 'Draw'
                                                if (out === 'over' && leg.point != null) return `Over ${leg.point}`
                                                if (out === 'under' && leg.point != null) return `Under ${leg.point}`
                                                return out
                                              })()}
                                            </span>
                                            {/* Editable per-leg stake. Typing into ANY leg sets the
                                                target payout = new_stake × leg_odds, then every other
                                                leg's stake auto-recomputes from K / its_odds. So input
                                                "80" on Pinnacle leg @ 2.03 → payout 162 → BETINIA stake
                                                = 162 / 2.20 = 74 kr. Inverse direction works too.
                                                For USD providers (Polymarket/Kalshi) the input value
                                                renders in dollars so it matches what the user enters
                                                in the provider tab. Underlying math stays in SEK —
                                                we convert on read/write. */}
                                            {(() => {
                                              const isUsd = USD_PROVIDERS.has(displayPid)
                                              const displayValue = isUsd
                                                ? Math.round((stakeSek / SEK_PER_USD) * 10) / 10
                                                : Math.round(stakeSek)
                                              return (
                                                <>
                                                  <input
                                                    type="number"
                                                    min={0}
                                                    step={isUsd ? 0.1 : 1}
                                                    value={displayValue}
                                                    onChange={(e) => {
                                                      const raw = e.target.value
                                                      if (raw === '') { setPayoutOverride(pickedEid, null); return }
                                                      const n = Number(raw)
                                                      if (!isFinite(n)) return
                                                      // Convert USD input back to SEK before pushing
                                                      // through the SEK-keyed stake math.
                                                      const nSek = isUsd ? n * SEK_PER_USD : n
                                                      setStakeForLeg(pickedEid, oddsForLeg, nSek)
                                                    }}
                                                    className={`w-20 px-1 py-0 bg-zinc-900 border ${payoutOverride != null ? 'border-amber-500/50 text-amber-200' : 'border-zinc-700 text-amber-300'} rounded font-mono text-[10px] text-right focus:outline-none focus:border-purple-500`}
                                                    title={`Stake on ${displayPid.toUpperCase()} (${isUsd ? 'USD' : 'SEK'}). Typing rebalances all legs to a new payout = stake × ${oddsForLeg.toFixed(2)}.`}
                                                  />
                                                  <span className="text-[9px] text-zinc-600">{isUsd ? '$' : 'kr'}</span>
                                                </>
                                              )
                                            })()}
                                            {ifWinPayout > 0 && (
                                              <span className="font-mono text-zinc-500 text-[9px]" title="Guaranteed payout (same for every outcome, kept in SEK as the common reference)">
                                                wins → {Math.round(ifWinPayout)} kr
                                              </span>
                                            )}
                                          </div>
                                        )
                                      })}
                                    </div>
                                  </div>
                                )
                              })()}

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
                                      // Display priority: scanner team-names → event_name fallback
                                      // (set by _record_unknown_open_bets from provider history) →
                                      // event_id colon-split → "Unknown event" so the row never
                                      // renders blank. Without the event_name fallback, manually-
                                      // placed bets (no Event row) showed up as empty rows.
                                      const eventLabel = (p.home_team && p.away_team)
                                        ? `${p.home_team} v ${p.away_team}`
                                        : (p.event_name || p.event_id?.split(':').slice(1, 3).join(' v ') || p.event_id || 'Unknown event')
                                      const profit = det ? (det.payout - (p.stake ?? 0)) : 0
                                      // Settlement readiness — event start + 3 h grace window
                                      // (covers full sport durations: tennis/basketball ~3h, soccer 2h,
                                      // MMA fights vary). After that we assume the result is decidable
                                      // and surface a "ready to settle" pill so the user knows to
                                      // navigate to history for reconciliation.
                                      const startMs = p.start_time ? new Date(p.start_time).getTime() : null
                                      const placedMs = p.placed_at ? new Date(p.placed_at).getTime() : null
                                      const nowMs = Date.now()
                                      const SETTLE_GRACE_MS = 3 * 60 * 60 * 1000
                                      const readyToSettle = startMs != null && nowMs > startMs + SETTLE_GRACE_MS
                                      const inProgress = startMs != null && nowMs > startMs && !readyToSettle
                                      const fmtTime = (ms: number | null): string => {
                                        if (ms == null) return '—'
                                        const d = new Date(ms)
                                        const today = new Date(); today.setHours(0, 0, 0, 0)
                                        const same = d >= today
                                        return same
                                          ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                          : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
                                            d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                      }
                                      return (
                                        <div key={`pending-${p.id}`} className={`flex items-center gap-2 px-6 pl-9 py-0.5 border-b border-zinc-800/20 text-xs ${
                                          det ? (det.result === 'won' ? 'bg-green-900/10' : det.result === 'lost' ? 'bg-red-900/10' : 'bg-zinc-800/20') : ''
                                        }`}>
                                          <span className={`truncate flex-1 ${det ? 'text-zinc-400' : 'text-amber-300/70'}`}>{eventLabel}</span>
                                          <span className="text-amber-400/60 text-[10px]">{p.outcome ?? p.market}</span>
                                          <span className="text-zinc-500 font-mono text-[10px]">@ {(p.odds ?? 0).toFixed(2)}</span>
                                          <span className="text-amber-300/50 font-mono text-[10px]">{Math.round(p.stake ?? 0)} kr</span>
                                          <span
                                            className="text-zinc-600 font-mono text-[10px]"
                                            title={`Placed ${p.placed_at ?? '—'}`}
                                          >
                                            placed {fmtTime(placedMs)}
                                          </span>
                                          <span
                                            className={`font-mono text-[10px] ${ttkClass(p.start_time)}`}
                                            title={p.start_time ? `Event start: ${new Date(p.start_time).toLocaleString()}` : 'no event start time on bet — settlement will not auto-flag'}
                                          >
                                            {p.start_time ? `starts ${fmtTime(startMs)} · ${fmtTtkFromIso(p.start_time)}` : 'no start time'}
                                          </span>
                                          {!det && readyToSettle && (
                                            <span
                                              className="text-[9px] px-1.5 py-0.5 rounded font-semibold bg-emerald-700/40 text-emerald-200 border border-emerald-500/40 uppercase tracking-wider"
                                              title="Event ended >3h ago — open the provider history page to recover the result"
                                            >
                                              ready to settle
                                            </span>
                                          )}
                                          {!det && inProgress && (
                                            <span
                                              className="text-[9px] px-1.5 py-0.5 rounded font-semibold bg-amber-700/30 text-amber-200 border border-amber-500/30 uppercase tracking-wider"
                                              title="Event is live — result not yet final"
                                            >
                                              live
                                            </span>
                                          )}
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
                                    {opps.filter((opp: any) => {
                                      if (drainedEventIds.has(opp.event_id)) return false
                                      // Suppress clearly-bogus profit%. Real soft-vs-sharp arbs
                                      // are typically 0-5%; >30% is a stale-extraction artifact
                                      // (DB has BETINIA Italy @41.00 vs live @1.78 etc.). The
                                      // live odds stream will correct it once the user clicks,
                                      // but the unclicked rows stay broken indefinitely. Hide
                                      // them so they can't trick the user into placing.
                                      const p = opp.guaranteed_profit_pct ?? 0
                                      if (p > 30) return false
                                      return true
                                    }).map((opp: any, i: number) => {
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
                                        const mkt = (opp.market ?? '').toLowerCase()
                                        // home/away: plain team name, except spread which shows the
                                        // signed handicap line so the user can verify both legs are
                                        // on the same line. per-leg point preferred; else derive
                                        // from opp.point (home = opp.point, away = its complement).
                                        if (o === 'home' || o === 'away') {
                                          const name = o === 'home' ? (opp.display_home || 'Home') : (opp.display_away || 'Away')
                                          if (mkt !== 'spread') return name
                                          let pt = leg.point
                                          if (pt == null) pt = o === 'home' ? opp.point : (opp.point != null ? -opp.point : null)
                                          return pt != null ? `${name} ${pt > 0 ? '+' : ''}${pt}` : name
                                        }
                                        if (o === 'draw') return 'Draw'
                                        const pt = leg.point ?? opp.point
                                        if (o === 'over' && pt != null) return `Over ${pt}`
                                        if (o === 'under' && pt != null) return `Under ${pt}`
                                        if (pt != null) return `${o} ${pt}`
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
                                      const stakes = computeArbStakes(
                                        pid,
                                        Number(anchorLeg.odds ?? 0),
                                        counters,
                                        providerBalances,
                                        stakeCaps,
                                      )
                                      const navigateLeg = async (legPid: string, leg: any, label: string, _opp: any = opp, opts?: { autoChained?: boolean }) => {
                                        const eid = _opp.event_id
                                        const lk = legKey(legPid, leg?.outcome, leg?.point)
                                        const autoChained = opts?.autoChained === true
                                        // Soft-anchor-first ordering: counter (sharp) legs can only
                                        // be picked AFTER the soft anchor for this opp is synced.
                                        // Otherwise the user clicks Pinnacle first, sees its odds,
                                        // then realises BETINIA's slip got cancelled by the time
                                        // they go back. The whole point of arb is to lock the soft
                                        // side first since that's the slower / less-liquid leg.
                                        // Auto-chained counter clicks (fired by onAnchorClick
                                        // immediately after the anchor) bypass this — we WANT both
                                        // tabs to open together when the user clicks once.
                                        const isAnchorClick = legPid === pid
                                        if (!isAnchorClick && !autoChained) {
                                          const anchorLk = legKey(
                                            anchorLeg.provider ?? anchorLeg.provider_id ?? pid,
                                            anchorLeg.outcome,
                                            anchorLeg.point,
                                          )
                                          const anchorIsSynced = (syncedLegs[eid] ?? new Set<string>()).has(anchorLk)
                                          if (!anchorIsSynced) {
                                            // Surface on the anchor card's status row — that's what the
                                            // user is looking at right above the arb table. The counter
                                            // chip (`legPid`) has no widget that renders providerStatus.
                                            const msg = `click ${pid.toUpperCase()} ${anchorOutcome} first, then ${legPid.toUpperCase()}`
                                            setProviderStatusFor(pid, msg)
                                            // Also flash a brief toast-style status on the counter for
                                            // visibility — gets overwritten by the next legitimate sync.
                                            setProviderStatusFor(legPid, `→ pick ${pid.toUpperCase()} first`)
                                            console.log(`[navigateLeg] anchor not synced — drop counter click for ${lk}/${eid}`)
                                            return
                                          }
                                        }
                                        // Anchor clicks SUPERSEDE — clicking a new soft anchor while a
                                        // previous one is still navigating cancels the old picked-event
                                        // state and starts the new one. Frontend just clears + proceeds;
                                        // the backend's navigate-opp internally stops the prior runner +
                                        // re-uses the same tab so the page goes to the new URL anyway.
                                        // Without this the user couldn't switch BETINIA events without
                                        // waiting for the prior nav to fully settle (~6s of dead clicks).
                                        // Counter clicks keep the single-leg lock — they target separate
                                        // bookmaker tabs and racing them produces real state corruption.
                                        if (isAnchorClick) {
                                          // Wipe any prior anchor-side in-flight markers for THIS provider.
                                          for (const k of Array.from(navInFlight.current)) {
                                            if (k.startsWith(legPid + '|')) navInFlight.current.delete(k)
                                          }
                                        } else {
                                          // Counter click — race protection is PER-PROVIDER, not global.
                                          // Two counter navs on the SAME tab clobber slip state, but two
                                          // navs on DIFFERENT tabs (Pinnacle + Polymarket) are fine and
                                          // happen on purpose during auto-chain (anchor click fires all
                                          // counters in parallel). Only drop if SAME-provider is busy.
                                          const sameProviderBusy = Array.from(navInFlight.current).some(
                                            k => k.startsWith(legPid + '|'),
                                          )
                                          if (sameProviderBusy) {
                                            console.log(`[navigateLeg] ${legPid} busy, dropping counter click for ${lk}/${eid}`)
                                            return
                                          }
                                        }
                                        navInFlight.current.add(lk)
                                        setLegBusy(`${lk}:${eid}`)
                                        // Anchor click that switches event for the same soft provider:
                                        // wipe the prior event's UI state so the DUTCH ARB widget
                                        // moves to the new event and the old row no longer reads as
                                        // synced/green/picking. Previously the old picked event would
                                        // stick (widget keeps showing the dead pick, sync chips stay
                                        // green for an opp the user has moved away from). Counter
                                        // clicks don't trigger this — they're already gated on the
                                        // anchor for this event being synced.
                                        if (isAnchorClick) {
                                          const prevEid = pickedEventByProvider[legPid]
                                          if (prevEid && prevEid !== eid) {
                                            setSyncedLegs(prev => {
                                              if (!(prevEid in prev)) return prev
                                              const next = { ...prev }
                                              delete next[prevEid]
                                              return next
                                            })
                                            setPickingLegs(prev => {
                                              if (!(prevEid in prev)) return prev
                                              const next = { ...prev }
                                              delete next[prevEid]
                                              return next
                                            })
                                            // Detach any counter providers that were pointed at the
                                            // OLD event — otherwise their DUTCH ARB widget (if shown
                                            // somewhere else) and their "checking…" status would
                                            // linger on a pick the user has abandoned.
                                            setPickedEventByProvider(prev => {
                                              let mutated = false
                                              const next: Record<string, string> = { ...prev }
                                              for (const [p, e] of Object.entries(prev)) {
                                                if (p !== legPid && e === prevEid) {
                                                  delete next[p]
                                                  mutated = true
                                                }
                                              }
                                              return mutated ? next : prev
                                            })
                                            // Drop the picked-leg meta for counter providers tied to
                                            // the old event. Frontend uses this to key live-odds
                                            // overrides; a stale entry would resurrect wrong odds
                                            // on the next SSE tick for the abandoned event.
                                            for (const [p, meta] of Object.entries(pickedLegMetaByProvider.current)) {
                                              if (p !== legPid && meta?.eid === prevEid) {
                                                delete pickedLegMetaByProvider.current[p]
                                              }
                                            }
                                          }
                                        }
                                        setPickedEventByProvider(prev => prev[legPid] === eid ? prev : { ...prev, [legPid]: eid })
                                        // Record exactly which leg we picked so arb_leg_odds can key
                                        // its persistence against the right outcome (not just the
                                        // first leg that happened to match this provider id).
                                        pickedLegMetaByProvider.current[legPid] = {
                                          eid,
                                          outcome: leg?.outcome ?? '',
                                          point: leg?.point ?? null,
                                        }
                                        setPickingLegs(prev => {
                                          const cur = prev[eid] ?? new Set<string>()
                                          const next = new Set(cur); next.add(lk)
                                          return { ...prev, [eid]: next }
                                        })
                                        setProviderStatusFor(legPid, `checking ${label}…`)
                                        // Supersede check: when the API call returns, verify the user
                                        // hasn't moved on to a different anchor event. If they have,
                                        // skip the success setState — otherwise the late completion
                                        // would re-add the abandoned event's sync state and resurrect
                                        // a green chip on a row the user just left.
                                        const isSuperseded = () =>
                                          isAnchorClick && pickedLegMetaByProvider.current[legPid]?.eid !== eid
                                        try {
                                          // Pass the user's manual stake override (if any) to the
                                          // backend. The override is stored as TARGET PAYOUT — we
                                          // derive the anchor stake from payout / anchor_odds (clamped
                                          // to balance). Backend uses this for bet["stake"] instead of
                                          // full balance so the placed bet matches the UI display.
                                          const payoutOverride = payoutOverridesByEid[eid]
                                          let overrideAnchorStake: number | null = null
                                          if (payoutOverride != null && payoutOverride > 0) {
                                            const ao = Number(anchorLeg.odds ?? 0)
                                            if (ao > 0) overrideAnchorStake = payoutOverride / ao
                                          }
                                          const r = await api.navigateOpp(legPid, {
                                            ..._opp,
                                            _picked_leg: leg,
                                            ...(overrideAnchorStake != null && overrideAnchorStake > 0
                                              ? { _override_stake: overrideAnchorStake }
                                              : {}),
                                          })
                                          if (isSuperseded()) {
                                            console.log(`[navigateLeg] superseded — ignoring ${lk}/${eid} response`)
                                            return  // finally still runs to remove from navInFlight
                                          }
                                          if (r?.status === 'synced' || r?.status === 'nav_only') {
                                            // Per-leg sync confirmed. Mark green:
                                            //   1. The picked leg itself.
                                            //   2. Every sibling leg on this opp that shares the SAME
                                            //      provider (same event page renders all outcomes).
                                            //   3. Every cluster-sibling leg (Altenar tenants share
                                            //      an odds engine — BETINIA, QUICKCASINO etc. all
                                            //      reflect the same odds; the scanner often emits the
                                            //      cluster-best sibling's leg, and without a cluster
                                            //      match the row's `allGreen` check stayed false even
                                            //      though the user navigated successfully).
                                            const syncedCluster = resolveSoftCluster(legPid)
                                            setSyncedLegs(prev => {
                                              const cur = prev[eid] ?? new Set<string>()
                                              const next = new Set(cur)
                                              next.add(lk)
                                              for (const l of (_opp.legs ?? [])) {
                                                const lp = (l.provider ?? l.provider_id ?? '') as string
                                                if (!lp) continue
                                                if (lp === legPid || resolveSoftCluster(lp) === syncedCluster) {
                                                  next.add(legKey(lp, l.outcome, l.point))
                                                }
                                              }
                                              if (next.size === cur.size) return prev
                                              return { ...prev, [eid]: next }
                                            })
                                            const msg = r.status === 'synced'
                                              ? `synced @ ${r.planned_odds?.toFixed?.(2) ?? '?'} — click Place on tab`
                                              : `tab on event — click outcome + Place`
                                            setProviderStatusFor(legPid, msg)
                                          } else if (r?.status === 'prep_failed') {
                                            setProviderStatusFor(legPid, `prep failed: ${r.reason ?? 'unknown'}`)
                                          } else if (r?.status === 'event_closed') {
                                            // Drain the row, STOP. No auto-pop — the previous chained
                                            // recursion was racing with arb_leg_odds re-ranks (closure
                                            // captures stale opps) and looked haywire. User clicks
                                            // the next top arb manually.
                                            setDrainedEventIds(prev => prev.has(eid) ? prev : new Set([...prev, eid]))
                                            setProviderStatusFor(legPid, `event finished — drained, click next`)
                                          } else {
                                            setProviderStatusFor(legPid, 'nav done')
                                          }
                                        } catch (e: any) {
                                          if (isSuperseded()) return
                                          setProviderStatusFor(legPid, `nav failed: ${e?.message ?? e}`)
                                        } finally {
                                          setPickingLegs(prev => {
                                            const cur = prev[eid]
                                            if (!cur || !cur.has(lk)) return prev
                                            const next = new Set(cur); next.delete(lk)
                                            return { ...prev, [eid]: next }
                                          })
                                          navInFlight.current.delete(lk)
                                          setLegBusy(prev => prev === `${lk}:${eid}` ? null : prev)
                                        }
                                      }
                                      const onRowClick = (e: React.MouseEvent) => {
                                        e.stopPropagation()
                                        // Navigate every leg whose provider IS logged in. Skip the
                                        // ones that aren't — the user plays those manually on the
                                        // bookmaker's site (the un-logged-in legs); clicking opens
                                        // the logged-in event page so the hedge math can be
                                        // referenced/placed). Previously this
                                        // path bailed entirely on !canRun, which hid the calc-side
                                        // value of seeing whatever counter tabs ARE available.
                                        if (!canRun) {
                                          setProviderStatusFor(
                                            pid,
                                            `manual play: ${missingLogins.map(p => p.toUpperCase()).join(' + ')}`,
                                          )
                                        }
                                        if (loggedInProviders.has(pid)) {
                                          navigateLeg(pid, anchorLeg, `${anchorOutcome} on ${pid}`)
                                        } else {
                                          // Manual-play branch: navigateLeg is skipped (anchor not
                                          // logged in), so pickedEventByProvider[pid] would never
                                          // get set — meaning the DUTCH ARB calculator widget
                                          // (editable payout + per-leg stakes) never renders for
                                          // manual-play rows. Pin the picked event manually
                                          // so the user still sees stake math while placing on
                                          // the bookmaker's site by hand. Mirrors the cleanup of
                                          // stale sync/picking state that navigateLeg does when
                                          // the anchor click switches events.
                                          const prevEid = pickedEventByProvider[pid]
                                          if (prevEid && prevEid !== opp.event_id) {
                                            setSyncedLegs(prev => {
                                              if (!(prevEid in prev)) return prev
                                              const next = { ...prev }
                                              delete next[prevEid]
                                              return next
                                            })
                                            setPickingLegs(prev => {
                                              if (!(prevEid in prev)) return prev
                                              const next = { ...prev }
                                              delete next[prevEid]
                                              return next
                                            })
                                          }
                                          setPickedEventByProvider(prev =>
                                            prev[pid] === opp.event_id ? prev : { ...prev, [pid]: opp.event_id },
                                          )
                                          pickedLegMetaByProvider.current[pid] = {
                                            eid: opp.event_id,
                                            outcome: anchorLeg?.outcome ?? '',
                                            point: anchorLeg?.point ?? null,
                                          }
                                        }
                                        // Auto-chain: fire navigateLeg for EVERY logged-in counter
                                        // leg. When two legs share a counter provider (e.g. 1X2 arb
                                        // with pinnacle covering both Draw and Away), we still fire
                                        // both. The per-provider busy guard inside navigateLeg
                                        // drops the 2nd same-provider call as a no-op, but the
                                        // FIRST call's success handler greens every sibling leg
                                        // sharing (provider, event) — the tab is on the event page
                                        // which renders all outcomes anyway.
                                        for (const cleg of counters) {
                                          const cpid = (cleg.provider ?? cleg.provider_id) as string
                                          if (!cpid) continue
                                          if (!loggedInProviders.has(cpid)) continue
                                          navigateLeg(cpid, cleg, `${resolveOutcome(cleg)} on ${cpid}`, opp, { autoChained: true })
                                        }
                                        // In parallel: pre-warm Pinnacle live odds via the targeted
                                        // refresh endpoint. Auto-applies as liveLegOdds overrides
                                        // so the row's profit % updates to reflect what Pinnacle is
                                        // actually offering RIGHT NOW (extraction-time data is often
                                        // 2-5 min stale and can flip a "+0.5%" arb to negative).
                                        const pinnacleLegs = (opp.legs ?? []).filter((l: any) =>
                                          (l.provider ?? l.provider_id) === 'pinnacle',
                                        )
                                        if (pinnacleLegs.length > 0) {
                                          const matchupId = pinnacleLegs[0]?.provider_meta?.matchup_id
                                          if (matchupId) {
                                            refreshPinnacleMatchup(opp, matchupId).catch(err => {
                                              console.warn('[refresh-matchup] failed', err)
                                            })
                                          }
                                        }
                                      }
                                      const rowEid = opp.event_id
                                      const eventSynced = syncedLegs[rowEid] ?? new Set<string>()
                                      const eventPicking = pickingLegs[rowEid] ?? new Set<string>()
                                      // All-green check now uses leg keys (pid|outcome|point) so 1X2
                                      // arbs with 3 hedge legs on the same provider need each
                                      // distinct outcome synced — not just one provider entry.
                                      const allLegKeys = [
                                        legKey(anchorLeg.provider ?? anchorLeg.provider_id ?? pid, anchorLeg.outcome, anchorLeg.point),
                                        ...counters.map((l: any) => legKey(l.provider ?? l.provider_id ?? '', l.outcome, l.point)),
                                      ]
                                      const allGreen = allLegKeys.length > 0 && allLegKeys.every(k => eventSynced.has(k))
                                      const anchorLegKey = legKey(anchorLeg.provider ?? anchorLeg.provider_id ?? pid, anchorLeg.outcome, anchorLeg.point)
                                      const anchorSynced = eventSynced.has(anchorLegKey)
                                      const anchorPicking = eventPicking.has(anchorLegKey)
                                      // Runnable check: the FUNDED anchor (pid) + every ENABLED
                                      // counter leg must be logged in. We use `pid` (this section's
                                      // funded soft) and NOT anchorLeg.provider because cluster
                                      // siblings share odds — the scanner often picks a sibling's
                                      // leg as the anchor (e.g. QUICKCASINO @ 2.20 beats BETINIA @
                                      // 2.10), but we'd still place on `pid` since that's the one
                                      // the user has funded. Checking the sibling's login state
                                      // would grey rows the user can perfectly well execute.
                                      //
                                      // Counters the user has disabled via the chip filter aren't
                                      // part of the placement plan, so we skip them even if they're
                                      // present in opp.legs.
                                      //
                                      // Also: if login state hasn't loaded yet (empty set on first
                                      // paint), don't grey — would flash every row off then on.
                                      const loginsKnown = loggedInProviders.size > 0
                                      const rowProviders = [
                                        pid,
                                        ...counters
                                          .filter((l: any) => {
                                            const lp = l.provider ?? l.provider_id
                                            return lp && enabledCounters.has(lp)
                                          })
                                          .map((l: any) => l.provider ?? l.provider_id),
                                      ].filter(Boolean) as string[]
                                      const missingLogins = loginsKnown
                                        ? rowProviders.filter(p => !loggedInProviders.has(p))
                                        : []
                                      const canRun = missingLogins.length === 0
                                      const cellClass = (synced: boolean, picking: boolean) =>
                                        picking
                                          ? 'bg-amber-900/40 animate-pulse'
                                          : synced
                                            ? 'bg-emerald-900/30'
                                            : ''
                                      const rowDisabledTitle = canRun
                                        ? ''
                                        : `Not logged in: ${missingLogins.map(p => p.toUpperCase()).join(', ')}. Log in to enable this arb.`
                                      return (
                                        <tr key={`arb-${pid}-${i}`}
                                            title={rowDisabledTitle}
                                            onClick={onRowClick}
                                            className={`border-b border-zinc-800/20 ${allGreen ? 'ring-1 ring-emerald-500/40' : ''} hover:bg-zinc-800/40 cursor-pointer`}>
                                          <td className={`pl-9 pr-2 py-1 font-mono font-semibold text-right w-[60px] ${profitPct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                            {allGreen && <span className="mr-1 text-emerald-300" title="All legs synced — ready to place">●</span>}
                                            {profitPct >= 0 ? '+' : ''}{profitPct.toFixed(2)}%
                                          </td>
                                          <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate text-[11px]">
                                            {eventLabel}
                                          </td>
                                          <td className={`px-2 py-1 font-mono text-[10px] w-[44px] text-right ${ttkClass(opp.starts_at)}`} title={opp.starts_at ? `kicks off ${new Date(opp.starts_at).toLocaleString()}` : 'no start time'}>
                                            {fmtTtkFromIso(opp.starts_at)}
                                          </td>
                                          <td
                                      className="px-2 py-1 text-zinc-500 text-[10px] uppercase cursor-help"
                                      title={(() => {
                                        const m = (opp.market ?? '') as string
                                        const sport = (opp.sport ?? '') as string
                                        const legCount = (opp.legs ?? []).length
                                        if (m === '1x2') return `1X2 — 3-way (home/draw/away). For hockey/soccer regulation. Pinnacle: s;6;m for hockey, s;0;m for soccer.`
                                        if (m === 'moneyline') {
                                          if (legCount === 3) return `MONEYLINE — 3-way (home/draw/away). Treated like 1X2. Pinnacle: s;0;m or s;6;m.`
                                          return `MONEYLINE — 2-way (home/away), full game incl. OT/SO. Pinnacle: s;0;m.`
                                        }
                                        if (m === 'spread') return `SPREAD ${opp.point ?? ''} — ${sport === 'tennis' ? 'WARNING: tennis sets vs games unit mismatch — extractor should be skipping' : '2-way handicap'}. Pinnacle: s;<period>;s;<point>.`
                                        if (m === 'total') return `TOTAL ${opp.point ?? ''} — over/under. Pinnacle: s;<period>;ou;<point>.`
                                        return m
                                      })()}
                                    >{opp.market ?? ''}</td>
                                          <td className="px-2 py-1 text-[11px]">
                                            <span
                                              className={`rounded px-1 -mx-1 py-0.5 inline-block ${cellClass(anchorSynced, anchorPicking)}`}
                                            >
                                              <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">bet</span>
                                              <span className="text-green-400 font-semibold">{anchorOutcome}</span>
                                              <span className="text-zinc-600 mx-1">on</span>
                                              <span className="text-zinc-400 uppercase text-[10px]">{pid}</span>
                                              <span className="font-mono text-zinc-200 ml-2">{formatOddsDisplay(pid, Number(anchorLeg.odds ?? 0))}</span>
                                              {stakes.anchorSek > 0 && (
                                                <span className="font-mono text-amber-300 ml-2">{formatLegStake(pid, stakes.anchorSek)}</span>
                                              )}
                                            </span>
                                          </td>
                                          <td className="px-2 py-1 text-[11px]">
                                            <div className="flex flex-col gap-0.5">
                                              {counters.map((leg: any, li: number) => {
                                                const cpid = leg.provider ?? leg.provider_id
                                                const cstake = stakes.counterSekByLeg[li] ?? 0
                                                const hedgeLegKey = legKey(cpid ?? '', leg.outcome, leg.point)
                                                const hedgeSynced = eventSynced.has(hedgeLegKey)
                                                const hedgePicking = eventPicking.has(hedgeLegKey)
                                                return (
                                                  <div
                                                    key={li}
                                                    className={`flex items-center gap-1 rounded px-1 -mx-1 py-0.5 ${cellClass(hedgeSynced, hedgePicking)}`}
                                                  >
                                                    <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">hedge</span>
                                                    <span className="text-pink-400 font-semibold">{resolveOutcome(leg)}</span>
                                                    <span className="text-zinc-600">on</span>
                                                    <span className="text-zinc-400 uppercase text-[10px]">{cpid}</span>
                                                    <span className="font-mono text-zinc-300 ml-2">{formatOddsDisplay(cpid, Number(leg.odds ?? 0))}</span>
                                                    {cstake > 0 && (
                                                      <span className="font-mono text-amber-300 ml-2">{formatLegStake(cpid, cstake)}</span>
                                                    )}
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
        {/* Deposit recommendation — sums current Kelly stakes per unlimited provider.
            Stakes ramp with bankroll (Kelly is bankroll-fraction), so this is the
            "fund all NOW at current Kelly" number; depositing more lets stakes grow. */}
        {bets.length > 0 && (() => {
          const stakeNativeByProvider: Record<string, number> = {}
          const stakeSekByProvider: Record<string, number> = {}
          let totalSek = 0
          for (const b of bets) {
            if (!b.stake || b.stake <= 0) continue
            const isPoly = b.tier === 'polymarket'
            const stakeSek = isPoly ? b.stake * 10.5 : b.stake
            stakeNativeByProvider[b.provider_id] = (stakeNativeByProvider[b.provider_id] || 0) + b.stake
            stakeSekByProvider[b.provider_id] = (stakeSekByProvider[b.provider_id] || 0) + stakeSek
            totalSek += stakeSek
          }
          const needNative: Record<string, number> = {}
          let additionalSek = 0
          for (const [pid, stakeSek] of Object.entries(stakeSekByProvider)) {
            const isPoly = pid === 'polymarket'
            const balRaw = providerBalances[pid]
            // Use balance_native so the gap is computed in the same currency
            // as stakeNative — for poly that's USDC, for everyone else SEK.
            // `balance` alone is SEK-normalized and would zero the poly gap.
            const balNative = typeof balRaw === 'object' && balRaw != null
              ? (balRaw.balance_native ?? balRaw.balance ?? 0)
              : (typeof balRaw === 'number' ? balRaw : 0)
            const stakeNative = stakeNativeByProvider[pid] || 0
            const gapNative = Math.max(0, stakeNative - balNative)
            if (gapNative > 0) needNative[pid] = gapNative
            additionalSek += gapNative * (isPoly ? 10.5 : 1)
          }
          if (totalSek <= 0) return null
          return (
            <div className="px-3 py-2 bg-amber-950/25 border-b border-amber-800/30 text-[11px] flex items-center gap-3 flex-wrap">
              <span className="text-amber-300 font-semibold uppercase tracking-wider">Total stake</span>
              <span className="text-amber-200 font-mono font-semibold">{Math.round(totalSek)} kr</span>
              <span className="text-zinc-500">·</span>
              {Object.entries(stakeSekByProvider).sort(([,a],[,b]) => b - a).map(([pid]) => {
                const isPoly = pid === 'polymarket'
                const stakeNative = stakeNativeByProvider[pid] || 0
                const gapNative = needNative[pid] || 0
                const unit = isPoly ? '$' : 'kr'
                return (
                  <span key={pid} className="flex items-center gap-1">
                    <span className="text-zinc-400 uppercase">{pid}</span>
                    <span className="text-amber-200/90 font-mono">
                      {isPoly ? `$${stakeNative.toFixed(0)}` : `${Math.round(stakeNative)} ${unit}`}
                    </span>
                    {gapNative > 0 && (
                      <span className="text-orange-400 font-mono" title="Additional deposit needed at this provider given current balance">
                        (+{isPoly ? `$${gapNative.toFixed(0)}` : `${Math.round(gapNative)}`})
                      </span>
                    )}
                  </span>
                )
              })}
              {additionalSek > 0 && (
                <span className="ml-auto text-orange-300 font-mono">
                  {Math.round(additionalSek)} kr to deposit
                </span>
              )}
              <span className="text-zinc-500 text-[10px]" title="Kelly stakes scale with total bankroll. Re-check this number after depositing.">
                · ramps with bankroll
              </span>
            </div>
          )
        })()}
        {clusterIds.length === 0 && batch.length > 0 && (
          <div className="p-4 text-zinc-500 text-xs">No positive-edge value bets (Pinnacle / Polymarket / Cloudbet / Kalshi / Rainbet).</div>
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
                isActive
                  ? (isLoggedIn
                      ? 'bg-green-900/20 border-green-700/50'
                      : 'bg-amber-900/20 border-amber-700/50')
                  : 'bg-zinc-900/50 border-zinc-800'
              }`}>
                <span className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider">{clusterId}</span>
                {/* Skin tabs — sorted by balance desc */}
                <div className="flex items-center gap-1">
                  {stats.providers.sort((a, b) => getBalance(providerBalances[b]) - getBalance(providerBalances[a])).map(pid => {
                    const bal = getBalance(providerBalances[pid])
                    // Native-currency display: USDC for polymarket (the SEK
                    // value would be misleading with a $ prefix), SEK for
                    // everyone else. Falls back to bal if balance_native isn't
                    // populated (e.g. transient race during initial load).
                    const balRaw = providerBalances[pid]
                    const balDisplay = (typeof balRaw === 'object' && balRaw?.balance_native != null) ? balRaw.balance_native : bal
                    const pending = pendingByProvider[pid]?.length ?? 0
                    const uncapped = ['pinnacle', 'polymarket', 'cloudbet', 'kalshi'].includes(pid)
                    const disabled = bal <= 0 && pending === 0 && !uncapped
                    const cardState = deriveCardState(tabOpenProviders.has(pid), loggedInProviders.has(pid))
                    return (
                      <button key={pid}
                        onClick={() => !disabled && handleCardClick(pid)}
                        disabled={disabled}
                        className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                          disabled
                            ? 'text-zinc-700 border border-zinc-800/30 cursor-not-allowed opacity-40'
                            : `${CARD_STATE_CLASSES[cardState]} cursor-pointer`
                        }`}
                        title={
                          cardState === 'no_tab' ? 'Click to open the provider site' :
                          cardState === 'tab_open_not_in' ? 'Tab open — click to refresh if it closed silently' :
                          'Tab open + logged in'
                        }
                      >
                        <span className="uppercase font-semibold">{pid}</span>
                        {cardState === 'tab_open_not_in' && (
                          <span className="ml-2 inline-block px-1.5 py-0.5 text-[9px] rounded bg-red-400/25 text-red-100 font-semibold">
                            Log in to continue
                          </span>
                        )}
                        {cardState === 'tab_open_logged' && (
                          <span className="ml-2 inline-block px-1.5 py-0.5 text-[9px] rounded bg-emerald-500/25 text-emerald-100 font-semibold">
                            Logged in
                          </span>
                        )}
                        {balDisplay > 0 && (
                          <span className="ml-1 text-green-400 font-mono">
                            {USD_PROVIDERS.has(pid) ? '$' : ''}{balDisplay.toFixed(2)}{!USD_PROVIDERS.has(pid) ? ' kr' : ''}
                          </span>
                        )}
                        {pending > 0 && <span className="ml-1 text-amber-400">{pending}p</span>}
                        {stakeCaps[pid] && <span className="ml-1 px-1 py-px text-[8px] font-bold bg-orange-900/50 text-orange-400 border border-orange-700/50 rounded" title={`Provider limit: max ${Math.round(stakeCaps[pid])} kr per bet`}>≤{Math.round(stakeCaps[pid])}</span>}
                        {providerStatus[pid] && (
                          <span className="ml-1 text-amber-400 text-[10px] truncate max-w-[180px]" title={providerStatus[pid] || ''}>
                            {providerStatus[pid]}
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
                <span className="text-[10px] text-zinc-500 ml-auto">{stats.betCount} bets</span>
                <span className="text-[10px] text-green-400">
                  {clusterId === 'polymarket' ? `+$${stats.ev.toFixed(2)}` : `+${stats.ev.toFixed(0)} kr`}
                </span>
              </div>

              {/* POLYMARKET inline status — moved out of the global header so the
                  ready/Skip control sits next to the polymarket bet list. */}
              {clusterId === 'polymarket' && loopRunning && loopProviderStatus?.polymarket && (() => {
                const status = loopProviderStatus.polymarket
                return (
                  <div className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800/50 bg-zinc-900/30">
                    <span className="text-[10px] font-semibold text-amber-400 uppercase w-20">polymarket</span>
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
                        <span className="text-[10px] text-cyan-400/80 font-mono uppercase">{fmtMarket(status.current_bet)}</span>
                        <span className="text-[10px] text-amber-400 font-medium">{resolveOutcome(status.current_bet)}</span>
                        <span className="text-[10px] font-mono text-zinc-200">
                          @ {fmtOddsWithCents((status.current_bet.live_odds ?? status.current_bet.odds) ?? 0, true)}
                        </span>
                        {status.current_bet.edge_pct != null && (
                          <span className="text-[10px] text-green-400">+{status.current_bet.edge_pct?.toFixed(1)}%</span>
                        )}
                        <span className="text-[10px] font-mono text-zinc-500">${status.current_bet.stake?.toFixed(2)}</span>
                      </>
                    )}
                    {status.state === 'ready' && (
                      <div className="ml-auto flex items-center gap-2">
                        <button
                          onClick={() => api.skipCurrent('polymarket')}
                          className="text-[10px] text-zinc-500 hover:text-zinc-300"
                        >Skip</button>
                      </div>
                    )}
                  </div>
                )
              })()}

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
                      <span className="text-[10px] text-zinc-600">
                        {clusterPending.length} bets · {(() => {
                          const sum = clusterPending.reduce((s: number, p: any) => s + (p.stake ?? 0), 0)
                          return clusterId === 'polymarket' ? `$${sum.toFixed(2)}` : `${Math.round(sum)} kr`
                        })()}
                      </span>
                      {clusterSettled.length > 0 && (
                        <>
                          <span className={`text-[10px] font-mono font-semibold ${clusterPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {clusterPnl >= 0 ? '+' : ''}{clusterId === 'polymarket' ? `$${clusterPnl.toFixed(2)}` : `${Math.round(clusterPnl)} kr`}
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
                      // Display priority same as soft-cluster pending row:
                      // scanner team-names → event_name fallback → event_id
                      // split → "Unknown event" so the row never renders blank.
                      const eventLabel = (p.home_team && p.away_team)
                        ? `${p.home_team} v ${p.away_team}`
                        : (p.event_name || p.event_id?.split(':').slice(1, 3).join(' v ') || p.event_id || 'Unknown event')
                      const profit = det ? (det.payout - (p.stake ?? 0)) : 0
                      // Settlement readiness — event start + 3 h grace window.
                      const startMs = p.start_time ? new Date(p.start_time).getTime() : null
                      const placedMs = p.placed_at ? new Date(p.placed_at).getTime() : null
                      const nowMs = Date.now()
                      const SETTLE_GRACE_MS = 3 * 60 * 60 * 1000
                      const readyToSettle = startMs != null && nowMs > startMs + SETTLE_GRACE_MS
                      const inProgress = startMs != null && nowMs > startMs && !readyToSettle
                      const fmtTime = (ms: number | null): string => {
                        if (ms == null) return '—'
                        const d = new Date(ms)
                        const today = new Date(); today.setHours(0, 0, 0, 0)
                        const same = d >= today
                        return same
                          ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                          : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
                            d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                      }
                      return (
                        <div key={`pending-${p.id}`} className={`flex items-center gap-2 px-3 pl-6 py-0.5 border-b border-zinc-800/20 text-xs ${
                          det ? (det.result === 'won' ? 'bg-green-900/10' : det.result === 'lost' ? 'bg-red-900/10' : 'bg-zinc-800/20') : ''
                        }`}>
                          <span className="text-[10px] text-zinc-600 uppercase w-[80px]">{p._pid}</span>
                          <span className={`truncate flex-1 ${det ? 'text-zinc-400' : 'text-amber-300/70'}`}>{eventLabel}</span>
                          <span className="text-amber-400/60 text-[10px]">{p.outcome ?? p.market}</span>
                          <span className="text-zinc-500 font-mono text-[10px]">@ {fmtOddsWithCents(p.odds ?? 0, isCentsMarket(clusterId))}</span>
                          <span className="text-amber-300/50 font-mono text-[10px]">
                            {clusterId === 'polymarket' ? `$${(p.stake ?? 0).toFixed(2)}` : `${Math.round(p.stake ?? 0)} kr`}
                          </span>
                          <span
                            className="text-zinc-600 font-mono text-[10px]"
                            title={`Placed ${p.placed_at ?? '—'}`}
                          >
                            placed {fmtTime(placedMs)}
                          </span>
                          <span
                            className={`font-mono text-[10px] ${ttkClass(p.start_time)}`}
                            title={p.start_time ? `Event start: ${new Date(p.start_time).toLocaleString()}` : 'no event start time on bet — settlement will not auto-flag'}
                          >
                            {p.start_time ? `starts ${fmtTime(startMs)} · ${fmtTtkFromIso(p.start_time)}` : 'no start time'}
                          </span>
                          {!det && readyToSettle && (
                            <span
                              className="text-[9px] px-1.5 py-0.5 rounded font-semibold bg-emerald-700/40 text-emerald-200 border border-emerald-500/40 uppercase tracking-wider"
                              title="Event ended >3h ago — open the provider history page to recover the result"
                            >
                              ready to settle
                            </span>
                          )}
                          {!det && inProgress && (
                            <span
                              className="text-[9px] px-1.5 py-0.5 rounded font-semibold bg-amber-700/30 text-amber-200 border border-amber-500/30 uppercase tracking-wider"
                              title="Event is live — result not yet final"
                            >
                              live
                            </span>
                          )}
                          {det && (
                            <>
                              <span className={`text-[10px] font-semibold uppercase px-1 rounded ${
                                det.result === 'won' ? 'text-green-400 bg-green-900/30' :
                                det.result === 'lost' ? 'text-red-400 bg-red-900/30' :
                                'text-zinc-400 bg-zinc-800'
                              }`}>{det.result}</span>
                              <span className={`text-[10px] font-mono font-semibold ${profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                {profit >= 0 ? '+' : ''}{clusterId === 'polymarket' ? `$${profit.toFixed(2)}` : `${Math.round(profit)} kr`}
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

              {/* Bet rows.
                  Filter out bets whose CURRENT (live-streamed when available)
                  edge has gone non-positive — they're no longer +EV so showing
                  them is noise. The runner's slip-stream auto-skips the active
                  one within ~1s of edge < 0; the table filter mirrors that
                  decision for sibling rows in the queue. */}
              <table className="w-full text-xs">
                <tbody>
                  {[...cb]
                    .filter(b => {
                      const liveEdge = livePrices[`${b.event_id}:${b.market}:${b.outcome}`]?.edge
                      const currentEdge = liveEdge ?? b.edge_pct
                      return currentEdge > 0
                    })
                    .sort((a, b) => {
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
                    const isSynced = (syncedLegs[b.event_id] ?? new Set<string>()).has(
                      legKey(b.provider_id, b.outcome, b.point),
                    )
                    return (
                      <tr key={key}
                        onClick={() => handleValueBetClick(b)}
                        title={`Open ${b.provider_id.toUpperCase()} event page`}
                        className={`border-b border-zinc-800/30 hover:bg-zinc-800/40 cursor-pointer transition-colors ${
                          isSynced ? 'bg-emerald-900/30 ring-1 ring-emerald-500/40' : isCurrent ? 'bg-amber-900/20' : ''
                        }`}
                      >
                        <td className="pl-6 pr-2 py-1 text-[10px] text-zinc-500 uppercase w-[80px]">{b.cluster && b.cluster !== b.provider_id ? b.cluster.replace('_main', '').replace('_group', '').replace('gecko_', '') : b.provider_id}</td>
                        <td className="px-2 py-1 text-zinc-200 max-w-[220px] truncate">{b.display_home} v {b.display_away}</td>
                        <td className="px-2 py-1 text-cyan-400/80 font-mono text-[10px] uppercase">{fmtMarket(b)}</td>
                        <td className="px-2 py-1 text-amber-400 font-medium">{resolveOutcome(b)}</td>
                        <td className={`px-2 py-1 text-right font-mono ${live ? 'text-sky-400' : 'text-zinc-200'}`}>
                          {fmtOddsWithCents(displayOdds, isCentsMarket(b.provider_id))}
                          {/* Sky color = streaming live from the provider tab.
                              Drift direction is intentionally not shown — the
                              edge column already conveys whether the live odds
                              still leave us +EV. */}
                        </td>
                        <td className="px-2 py-1 text-right font-mono text-zinc-500">{fmtOddsWithCents(b.fair_odds, isCentsMarket(b.provider_id))}</td>
                        <td className={`px-2 py-1 text-right font-mono ${displayEdge >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {displayEdge >= 0 ? '+' : ''}{displayEdge.toFixed(1)}%
                        </td>
                        <td className="px-1 py-1 text-center whitespace-nowrap">
                          {renderAnnotationBadges(b)}
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
