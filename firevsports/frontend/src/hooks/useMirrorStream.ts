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
      'pending_stopped', 'live_price',
      'dutch_bet_ready', 'dutch_hedge_placing', 'dutch_hedge_placed',
      'dutch_hedge_failed', 'dutch_unhedged', 'dutch_complete',
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
