import { useState, useEffect } from 'react'
import { useDashboardWS } from './hooks/useDashboardWS'
import { api } from './hooks/useApi'
import type { ExpandedSession } from './types'
import { ChartPage } from './pages/ChartPage'

type Tab = 'chart' | 'dqn' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'chart',    label: 'Chart',    color: '#f59e0b' },
  { name: 'dqn',      label: 'DQN',      color: '#8b5cf6' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chart')
  const { state: ws, lastTick } = useDashboardWS()
  const [session, setSession] = useState<ExpandedSession | null>(null)

  useEffect(() => {
    const fetch = () => { api.getSession().then(setSession).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 60_000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-amber-500 mr-4">firevstocks</span>
        {TABS.map(tab => (
          <button
            key={tab.name}
            onClick={() => setActiveTab(tab.name)}
            className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider ${
              activeTab === tab.name ? 'text-zinc-950 font-bold' : 'text-zinc-500 hover:text-zinc-300'
            }`}
            style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
          >
            <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <span className={`text-xs font-mono ${ws.relayConnected ? 'text-emerald-400' : ws.connected ? 'text-yellow-400' : 'text-red-400'}`}>
          ● {ws.relayConnected ? 'Relay' : ws.connected ? 'WS only' : 'Disconnected'}
        </span>
        {ws.streamRunning && <span className="text-xs font-mono text-emerald-400 ml-1">● Stream</span>}
        {ws.lastPrice && (
          <span className="text-xs font-mono text-zinc-400 ml-2">
            NQ {ws.lastPrice.toFixed(2)}
          </span>
        )}
        {ws.tickCount > 0 && (
          <span className="text-xs font-mono text-zinc-600 ml-2">
            {ws.tickCount.toLocaleString()} ticks
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden p-2">
        {activeTab === 'chart' && (
          <ChartPage
            lastTick={lastTick}
            session={session}
            zones={ws.zones}
            signals={ws.signals}
            fills={ws.fills}
            exits={ws.exits}
          />
        )}
        {activeTab === 'dqn' && <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">DQN — {ws.signals.length} signals</div>}
        {activeTab === 'bankroll' && <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">Bankroll — {ws.positions.length} positions</div>}
        {activeTab === 'stats' && <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">Stats tab</div>}
      </div>
    </div>
  )
}
