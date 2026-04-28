import { useEffect, useRef, useState, useCallback } from 'react'
import type { Signal, Zone, Fill, ExitEvent, Quote, Position, DQNInferenceEvent, DepthSnapshot, ObservationSchema } from '@/types/stocks'

export type InferenceTrigger = 'approaching' | 'touched' | 'zone_entry'

export interface DashboardState {
  connected: boolean
  relayConnected: boolean
  streamRunning: boolean
  lastPrice: number | null
  tickCount: number
  signals: Signal[]
  zones: Zone[]
  fills: Fill[]
  exits: ExitEvent[]
  positions: Position[]
  quote: Quote | null
  depth: DepthSnapshot | null
  // True when STOCKS_AUTONOMOUS=true — server owns TopstepX, local app
  // never sees GatewayDepth. L2Ladder uses this to render an explanatory
  // empty state instead of "No depth feed" which looks broken.
  autonomous: boolean
  dqnInference: DQNInferenceEvent | null
  dqnInferenceAt: number | null  // Date.now() when last inference arrived
  /** Latest inference per trigger phase. Lets the lifecycle header show
   *  approaching → touched → zone_entry continuity even if the events
   *  arrive close together. */
  dqnByTrigger: Partial<Record<InferenceTrigger, { event: DQNInferenceEvent; at: number }>>
  /** Schema for the dqn_inference.inputs[] vector (fetched once on connect). */
  observationSchema: ObservationSchema | null
}

export interface TickEvent {
  price: number
  ts: number
  tick_count: number
}

const MAX_SIGNALS = 100
const MAX_FILLS = 200
const RECONNECT_MS = 2000

export function useDashboardWS() {
  const [state, setState] = useState<DashboardState>({
    connected: false,
    relayConnected: false,
    streamRunning: false,
    lastPrice: null,
    tickCount: 0,
    signals: [],
    zones: [],
    fills: [],
    exits: [],
    positions: [],
    quote: null,
    depth: null,
    autonomous: false,
    dqnInference: null,
    dqnInferenceAt: null,
    dqnByTrigger: {},
    observationSchema: null,
  })

  const [lastTick, setLastTick] = useState<TickEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | undefined>(undefined)
  const bootIdRef = useRef<string | null>(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/stocks/ws/dashboard`)
    wsRef.current = ws

    ws.onopen = () => {
      setState(s => ({ ...s, connected: true }))
      // Seed zones + depth from the REST state snapshot so a freshly-opened
      // page doesn't need to wait for the next broadcast (zones only push on
      // 1m candle close; depth pushes ~5Hz but a brand-new client misses
      // anything before connect). Best-effort — ignore failures.
      fetch('/stocks/api/state')
        .then(r => (r.ok ? r.json() : null))
        .then(snap => {
          if (!snap) return
          setState(s => {
            const next = { ...s }
            if (Array.isArray(snap.zones) && snap.zones.length > 0 && s.zones.length === 0) {
              next.zones = snap.zones
            }
            if (snap.depth && (snap.depth.bids?.length || snap.depth.asks?.length) && !s.depth) {
              next.depth = snap.depth
            }
            if (typeof snap.autonomous === 'boolean') {
              next.autonomous = snap.autonomous
            }
            return next
          })
        })
        .catch(() => { /* ignore */ })
      // Schema is invariant across runs of a model version — fetch once on
      // connect and cache. The DimsBreakdownCard slices inputs[] using this.
      fetch('/stocks/api/observation-schema')
        .then(r => (r.ok ? r.json() : null))
        .then((schema: ObservationSchema | null) => {
          if (schema && Array.isArray(schema.segments)) {
            setState(s => ({ ...s, observationSchema: schema }))
          }
        })
        .catch(() => { /* ignore */ })
    }

    ws.onclose = () => {
      setState(s => ({ ...s, connected: false }))
      wsRef.current = null
      reconnectTimer.current = setTimeout(connect, RECONNECT_MS)
    }

    ws.onerror = () => {
      ws.close()
    }

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      switch (msg.type) {
        case 'boot':
          if (bootIdRef.current !== null && bootIdRef.current !== msg.boot_id) {
            // Server restarted — hard reload to pick up new assets
            window.location.reload()
            return
          }
          bootIdRef.current = msg.boot_id
          break
        case 'tick':
          setLastTick({ price: msg.price, ts: msg.ts, tick_count: msg.tick_count })
          setState(s => ({ ...s, lastPrice: msg.price, tickCount: msg.tick_count }))
          break
        case 'signal':
          setState(s => ({
            ...s,
            signals: [...s.signals.slice(-(MAX_SIGNALS - 1)), msg as Signal],
          }))
          break
        case 'zones':
          setState(s => ({ ...s, zones: msg.zones }))
          break
        case 'quote':
          setState(s => ({ ...s, quote: msg as Quote }))
          break
        case 'depth':
          setState(s => ({
            ...s,
            depth: { bids: msg.bids, asks: msg.asks, ts: msg.ts },
          }))
          break
        case 'positions':
          setState(s => ({ ...s, positions: msg.positions }))
          break
        case 'status':
          setState(s => ({
            ...s,
            relayConnected: msg.relay_connected,
            streamRunning: msg.stream_running,
          }))
          break
        case 'dqn_inference': {
          const ev = msg as DQNInferenceEvent
          const now = Date.now()
          const trigger = (ev.trigger ?? 'zone_entry') as InferenceTrigger
          setState(s => ({
            ...s,
            dqnInference: ev,
            dqnInferenceAt: now,
            dqnByTrigger: { ...s.dqnByTrigger, [trigger]: { event: ev, at: now } },
          }))
          break
        }
        case 'fill':
          setState(s => ({
            ...s,
            fills: [...s.fills.slice(-(MAX_FILLS - 1)), msg as Fill],
          }))
          break
        case 'exit':
          setState(s => ({
            ...s,
            exits: [...s.exits.slice(-(MAX_FILLS - 1)), msg as ExitEvent],
          }))
          break
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { state, lastTick }
}
