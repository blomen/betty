import { useEffect, useRef, useState, useCallback } from 'react'
import type { Signal, Zone, Fill, ExitEvent, Quote, Position, DQNInferenceEvent } from '@/types'

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
  dqnInference: DQNInferenceEvent | null
  dqnInferenceAt: number | null  // Date.now() when last inference arrived
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
    dqnInference: null,
    dqnInferenceAt: null,
  })

  const [lastTick, setLastTick] = useState<TickEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | undefined>(undefined)
  const bootIdRef = useRef<string | null>(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/dashboard`)
    wsRef.current = ws

    ws.onopen = () => {
      setState(s => ({ ...s, connected: true }))
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
        case 'dqn_inference':
          setState(s => ({ ...s, dqnInference: msg as DQNInferenceEvent, dqnInferenceAt: Date.now() }))
          break
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
