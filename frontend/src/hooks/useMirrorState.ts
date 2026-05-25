/**
 * useMirrorState — read authoritative provider + runner state from the server DB.
 *
 * Phase 2 of the platform rebuild (2026-05-08). Replaces the stale-state-prone
 * polling against `/mirror/play/status` with a single source of truth: the
 * server's `mirror_provider_state` and `mirror_runner_state` tables. The local
 * mirror writes to those tables on every state change (via
 * `arnold/mirror/state_writer.py`), and this hook reads them on mount + every
 * 5s thereafter.
 *
 * Why this matters:
 *  - Survives `arnold.bat` restart (state persists in DB, not in-memory)
 *  - Survives browser hard-refresh (frontend reads DB instead of waiting for SSE)
 *  - Survives SSH tunnel wedges (eventually-consistent recovery once the
 *    tunnel comes back, vs the previous all-or-nothing failure mode)
 *
 * Until the server-side endpoints are deployed (`/api/mirror/state`), the
 * fetch will 502/404; the hook gracefully treats those as empty and the
 * existing `/mirror/play/status` polling in PlayPage.tsx continues to work
 * as the fallback. After deploy, this becomes the primary state source.
 */
import { useEffect, useState } from 'react'

export interface MirrorProviderState {
  provider_id: string
  logged_in: boolean
  balance: number | null
  balance_currency: string | null
  tab_url: string | null
  tab_open: boolean
  updated_at: string | null
}

export interface MirrorRunnerState {
  provider_id: string
  state: string | null
  mode: string | null
  current_arb_group_id: string | null
  current_opp_id: number | null
  last_idle_reason: string | null
  updated_at: string | null
}

export interface MirrorStateBundle {
  providers: Record<string, MirrorProviderState>
  runners: Record<string, MirrorRunnerState>
  loading: boolean
  error: string | null
  lastFetched: string | null
}

const POLL_INTERVAL_MS = 5000

const EMPTY: MirrorStateBundle = {
  providers: {},
  runners: {},
  loading: true,
  error: null,
  lastFetched: null,
}

export function useMirrorState(): MirrorStateBundle {
  const [bundle, setBundle] = useState<MirrorStateBundle>(EMPTY)

  useEffect(() => {
    let cancelled = false
    const fetchOnce = async () => {
      try {
        const r = await fetch('/api/mirror/state')
        if (!r.ok) {
          if (cancelled) return
          // Pre-deploy: endpoint doesn't exist yet → 404. Mark error but
          // keep last-known state visible (PlayPage's existing /play/status
          // polling fallback still works for live data).
          setBundle(prev => ({ ...prev, loading: false, error: `state ${r.status}` }))
          return
        }
        const data = await r.json()
        if (cancelled) return
        const providers: Record<string, MirrorProviderState> = {}
        for (const p of data.providers ?? []) {
          providers[p.provider_id] = p
        }
        const runners: Record<string, MirrorRunnerState> = {}
        for (const r of data.runners ?? []) {
          runners[r.provider_id] = r
        }
        setBundle({
          providers,
          runners,
          loading: false,
          error: null,
          lastFetched: new Date().toISOString(),
        })
      } catch (e: any) {
        if (cancelled) return
        // Network blip / tunnel wedge — keep last-known and try again next tick.
        setBundle(prev => ({ ...prev, loading: false, error: String(e?.message ?? e) }))
      }
    }
    fetchOnce()
    const id = setInterval(fetchOnce, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return bundle
}
