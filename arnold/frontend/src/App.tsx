import { useState, useEffect, Component } from 'react'
import type { ReactNode } from 'react'
import PlayPage from './pages/PlayPage'
import { BankrollPage as SportsBankrollPage } from './pages/BankrollPage'
import { StatsPage as SportsStatsPage } from './pages/StatsPage'
import { ChartPage } from './pages/stocks/ChartPage'
import { BankrollPage as StocksBankrollPage } from './pages/stocks/BankrollPage'
import { StatsPage as StocksStatsPage } from './pages/stocks/StatsPage'
import { ProfileSelector } from './components/ProfileSelector'
import { useDashboardWS } from './hooks/useDashboardWS'
import { api as stocksApi } from './hooks/useStocksApi'
import type { ExpandedSession } from './types/stocks'

// Catch escaping render errors. Used both at root (to catch shell crashes)
// and wrapped around each tab so one tab's crash doesn't kill the others —
// a blown-up stocks chart shouldn't take down sports placement.
class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null }
> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  reset = () => this.setState({ error: null })
  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center flex-1 min-h-0 bg-zinc-950 text-red-400 p-8 font-mono text-sm">
          <div className="text-red-500 font-bold mb-2">
            {this.props.label ? `${this.props.label} crashed` : 'React crashed'} — error details:
          </div>
          <pre className="bg-zinc-900 p-4 rounded max-w-4xl overflow-auto text-xs text-zinc-300 border border-red-800 mb-3">
            {String(this.state.error)}
            {'\n\n'}
            {(this.state.error as Error).stack}
          </pre>
          <button
            onClick={this.reset}
            className="px-3 py-1.5 text-xs font-mono uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded"
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

type Tab = 'play' | 'charts' | 'bankroll' | 'stats'
type SharedSub = 'betting' | 'trading'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'play',     label: 'Play',     color: '#22c55e' },
  { name: 'charts',   label: 'Charts',   color: '#f59e0b' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

function SubTabBar<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T
  onChange: (v: T) => void
  options: { name: T; label: string }[]
}) {
  return (
    <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-950/50">
      {options.map(opt => (
        <button
          key={opt.name}
          onClick={() => onChange(opt.name)}
          className={`px-2.5 py-1 text-[11px] font-mono uppercase tracking-wider ${
            value === opt.name ? 'text-zinc-200 border-b border-zinc-400' : 'text-zinc-500 hover:text-zinc-300'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('play')
  const [bankrollSub, setBankrollSub] = useState<SharedSub>('betting')
  const [statsSub, setStatsSub] = useState<SharedSub>('betting')

  // Keep stocks WebSocket mounted globally so ticks/signals accumulate regardless of tab
  const { state: ws, lastTick } = useDashboardWS()
  const [session, setSession] = useState<ExpandedSession | null>(null)

  useEffect(() => {
    const poll = () => { stocksApi.getSession().then(setSession).catch(() => {}) }
    poll()
    const iv = setInterval(poll, 60_000)
    return () => clearInterval(iv)
  }, [])

  return (
    <ErrorBoundary>
      <div className="flex flex-col h-screen bg-zinc-950">
        <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
          <span className="text-sm font-bold text-orange-500 mr-4">Arnold</span>
          {TABS.map(tab => (
            <button
              key={tab.name}
              onClick={() => setActiveTab(tab.name)}
              className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded ${
                activeTab === tab.name ? 'text-zinc-950 font-bold' : 'text-zinc-500 hover:text-zinc-300'
              }`}
              style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
            >
              <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
              {tab.label}
            </button>
          ))}
          <div className="flex-1" />
          {ws.lastPrice !== null && (
            <span className="text-xs font-mono text-zinc-400 mr-2">
              NQ {ws.lastPrice.toFixed(2)}
            </span>
          )}
          <span className={`text-[11px] font-mono mr-3 ${ws.relayConnected ? 'text-emerald-400' : ws.connected ? 'text-yellow-400' : 'text-zinc-600'}`}>
            ● {ws.relayConnected ? 'Relay' : ws.connected ? 'WS' : 'Offline'}
          </span>
          <ProfileSelector />
        </div>

        <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden">
          {/* Play — sports */}
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'play' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Play">
              <PlayPage />
            </ErrorBoundary>
          </div>

          {/* Charts — stocks */}
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'charts' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Chart">
              <ChartPage
                lastTick={lastTick}
                session={session}
                zones={ws.zones}
                signals={ws.signals}
                fills={ws.fills}
                exits={ws.exits}
              />
            </ErrorBoundary>
          </div>

          {/* Bankroll — shared: Betting (sportbets providers) + Trading (propfirm/hodl/crypto later) */}
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'bankroll' ? '' : 'hidden'}`}>
            <SubTabBar
              value={bankrollSub}
              onChange={setBankrollSub}
              options={[
                { name: 'betting', label: 'Sportbets' },
                { name: 'trading', label: 'Trading' },
              ]}
            />
            <div className={`flex flex-col flex-1 min-h-0 ${bankrollSub === 'betting' ? '' : 'hidden'}`}>
              <ErrorBoundary label="Sportbets bankroll">
                <SportsBankrollPage />
              </ErrorBoundary>
            </div>
            <div className={`flex flex-col flex-1 min-h-0 ${bankrollSub === 'trading' ? '' : 'hidden'}`}>
              <ErrorBoundary label="Trading bankroll">
                <StocksBankrollPage
                  positions={ws.positions}
                  lastPrice={ws.lastPrice}
                  quote={ws.quote}
                />
              </ErrorBoundary>
            </div>
          </div>

          {/* Stats — shared: Betting + Trading */}
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'stats' ? '' : 'hidden'}`}>
            <SubTabBar
              value={statsSub}
              onChange={setStatsSub}
              options={[
                { name: 'betting', label: 'Betting' },
                { name: 'trading', label: 'Trading' },
              ]}
            />
            <div className={`flex flex-col flex-1 min-h-0 ${statsSub === 'betting' ? '' : 'hidden'}`}>
              <ErrorBoundary label="Betting stats">
                <SportsStatsPage />
              </ErrorBoundary>
            </div>
            <div className={`flex flex-col flex-1 min-h-0 ${statsSub === 'trading' ? '' : 'hidden'}`}>
              <ErrorBoundary label="Trading stats">
                <StocksStatsPage />
              </ErrorBoundary>
            </div>
          </div>
        </div>
      </div>
    </ErrorBoundary>
  )
}
