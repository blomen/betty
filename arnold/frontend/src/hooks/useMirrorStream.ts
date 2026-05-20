import { useState, useEffect, useRef, useCallback } from 'react';

type MirrorEvent = { type: string; data: any };

export function useMirrorStream() {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<MirrorEvent | null>(null);
  const [events, setEvents] = useState<MirrorEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource('/mirror/stream');
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    const eventTypes = [
      'play_started', 'provider_activated', 'provider_skipped', 'provider_complete',
      'login_waiting', 'login_detected',
      'settling_pending', 'settling_done',
      'bet_navigated', 'bet_ready', 'bet_placed', 'bet_skipped', 'bet_failed',
      'play_complete', 'play_stopped',
      'pending_started', 'history_synced', 'settlements_detected', 'settlements_confirmed',
      'balance_updated', 'balance_intercepted', 'history_intercepted', 'bet_intercepted',
      'pending_stopped', 'live_price', 'stake_limited', 'opp_expired',
      'arb_bet_ready', 'arb_hedge_placing', 'arb_hedge_placed',
      'arb_hedge_failed', 'arb_unhedged', 'arb_complete',
      'arb_legs_loaded', 'arb_alignment', 'arb_anchor_placed', 'arb_anchor_rejected',
      'bet_reconciled',
      // Per-leg events from /mirror/arb/navigate-opp (user-pick path) — without
      // these the frontend would silently drop every live odds tick.
      'arb_leg_started', 'arb_leg_navigated', 'arb_leg_prepped',
      'arb_leg_synced', 'arb_leg_odds', 'arb_leg_failed', 'arb_leg_event_closed',
      'arb_dethroned', 'arb_runner_idle',
      // Passive live-odds push from interceptor — fires on every Altenar
      // GetOddsStates WS update without requiring a user click. Lets the arb
      // page reflect site-live odds as soon as the bookmaker pushes them.
      'live_provider_odds',
      // Manual-mode placement recorder — fires after play_loop's fallback
      // POSTs an intercepted bet to /api/bets. Frontend toasts on this and
      // the bet shows up in the PENDING list on the next 5s poll.
      'bet_recorded', 'bet_record_failed',
      // Manual-mode skipped — placement intercepted but response didn't
      // expose actual stake (bookmaker may have stake-limited). User must
      // navigate to provider history page so reactive sync recovers the
      // correct amount.
      'bet_record_deferred',
      'provider_opening', 'provider_ready', 'provider_running',
      'login_detected',
      // Detected when the user manually browses a counter tab to a matchup
      // page. Frontend uses it to auto-pick the matching arb opp without
      // round-tripping through /mirror/arb/navigate-opp.
      'provider_manual_nav',
    ];

    for (const type of eventTypes) {
      es.addEventListener(type, (e: MessageEvent) => {
        const evt: MirrorEvent = { type, data: JSON.parse(e.data) };
        setLastEvent(evt);
        setEvents(prev => [...prev.slice(-99), evt]);
      });
    }

    return () => { es.close(); esRef.current = null; setConnected(false); };
  }, []);

  const clearEvents = useCallback(() => setEvents([]), []);
  return { connected, lastEvent, events, clearEvents };
}
