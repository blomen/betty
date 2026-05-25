import { useState, Component } from 'react'
import type { ReactNode } from 'react'
import PlayPage from './pages/PlayPage'
import { BankrollPage } from './pages/BankrollPage'
import { StatsPage } from './pages/StatsPage'
import { ProfileSelector } from './components/ProfileSelector'
import { ExtractionHealth } from './components/ExtractionHealth'

// Catch escaping render errors. Used both at root (to catch shell crashes)
// and wrapped around each tab so one tab's crash doesn't kill the others.
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

type Tab = 'play' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'play',     label: 'Sports',   color: '#22c55e' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('play')

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
          <ExtractionHealth />
          <ProfileSelector />
        </div>

        <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden">
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'play' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Play">
              <PlayPage />
            </ErrorBoundary>
          </div>

          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'bankroll' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Bankroll">
              <BankrollPage />
            </ErrorBoundary>
          </div>

          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'stats' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Stats">
              <StatsPage />
            </ErrorBoundary>
          </div>
        </div>
      </div>
    </ErrorBoundary>
  )
}
