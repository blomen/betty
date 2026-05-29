import { useCallback, useEffect, useRef, useState } from 'react'
import { devigMultiplicative, devigPower } from '../utils/devig'

type RefreshState = 'idle' | 'refreshing' | 'fresh' | 'stale' | 'unsupported'

const SUPPORTED_PROVIDERS = new Set(['pinnacle'])

export interface UseSharpRefreshArgs {
  eventKey: string
  baselineProviderId: string | null
  matchupId: string | null
  market: string
  point: number | null
  outcome: string
  eventId: string
}

export interface UseSharpRefreshResult {
  state: RefreshState
  freshFair: Record<string, number> | null
  freshRaw: Record<string, number> | null
  freshAt: number | null
  refresh: () => Promise<void>
}

interface InflightEntry {
  promise: Promise<void>
}

const inflight = new Map<string, InflightEntry>()

function selectPinnacleMarket(
  markets: any[],
  market: string,
  point: number | null,
  outcome: string,
): any | null {
  if (!Array.isArray(markets)) return null
  const period = 0
  if (market === 'moneyline') {
    return markets.find(m => m?.key === `s;${period};m` && m?.period === period) ?? null
  }
  if (market === 'spread' && point != null) {
    // Pinnacle keys spreads home-perspective. Away point flips sign.
    const lookupPoint = outcome === 'away' ? -point : point
    return findByPointMatch(markets, `s;${period};s`, period, lookupPoint)
  }
  if (market === 'total' && point != null) {
    return findByPointMatch(markets, `s;${period};ou`, period, point)
  }
  return null
}

function findByPointMatch(
  markets: any[],
  prefix: string,
  period: number,
  target: number,
): any | null {
  for (const m of markets) {
    if (m?.period !== period) continue
    const key: string = m?.key ?? ''
    if (!key.startsWith(prefix + ';')) continue
    const suffix = key.slice(prefix.length + 1)
    const parsed = Number.parseFloat(suffix)
    if (!Number.isFinite(parsed)) continue
    if (Math.abs(parsed - target) < 0.01) return m
  }
  return null
}

export function useSharpRefresh(args: UseSharpRefreshArgs): UseSharpRefreshResult {
  const {
    eventKey, baselineProviderId, matchupId, market, point, outcome, eventId,
  } = args
  const [state, setState] = useState<RefreshState>('idle')
  const [freshFair, setFreshFair] = useState<Record<string, number> | null>(null)
  const [freshRaw, setFreshRaw] = useState<Record<string, number> | null>(null)
  const [freshAt, setFreshAt] = useState<number | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  const refresh = useCallback(async () => {
    if (!baselineProviderId || !SUPPORTED_PROVIDERS.has(baselineProviderId) || !matchupId) {
      if (mountedRef.current) setState('unsupported')
      return
    }
    const existing = inflight.get(eventKey)
    if (existing) {
      try { await existing.promise } catch { /* ignore */ }
      return
    }
    const promise = (async () => {
      if (mountedRef.current) setState('refreshing')
      let res: Response
      try {
        res = await fetch('/mirror/sharp/refresh-event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider_id: baselineProviderId,
            matchup_id: matchupId,
            event_id: eventId,
            market,
            point,
            outcome,
          }),
          signal: AbortSignal.timeout(10_000),
        })
      } catch {
        if (mountedRef.current) setState('stale')
        return
      }
      let body: any
      try {
        body = await res.json()
      } catch {
        if (mountedRef.current) setState('stale')
        return
      }
      if (!res.ok || body?.error) {
        if (mountedRef.current) setState('stale')
        return
      }
      const m = selectPinnacleMarket(body.markets ?? [], market, point, outcome)
      if (!m) {
        if (mountedRef.current) setState('stale')
        return
      }
      const raw: Record<string, number> = {}
      for (const p of m.prices ?? []) {
        if (typeof p?.decimal === 'number' && p?.designation) {
          raw[p.designation] = p.decimal
        }
      }
      const outcomes = Object.keys(raw)
      const oddsList = outcomes.map(o => raw[o])
      const fairList = outcomes.length >= 3 ? devigPower(oddsList) : devigMultiplicative(oddsList)
      const fair: Record<string, number> = {}
      outcomes.forEach((o, i) => { fair[o] = fairList[i] })
      if (mountedRef.current) {
        setFreshRaw(raw)
        setFreshFair(fair)
        setFreshAt(Date.now())
        setState('fresh')
      }
    })()
    inflight.set(eventKey, { promise })
    try { await promise } finally { inflight.delete(eventKey) }
  }, [baselineProviderId, matchupId, eventKey, market, point, outcome, eventId])

  return { state, freshFair, freshRaw, freshAt, refresh }
}
