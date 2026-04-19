import { useState } from 'react'
import PlayPage from './pages/PlayPage'
import { BankrollPage } from './pages/BankrollPage'
import { StatsPage } from './pages/StatsPage'
import { ProfileSelector } from './components/ProfileSelector'

type Tab = 'play' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'play',     label: 'Play',     color: '#22c55e' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('play')

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-orange-500 mr-4">FirevSports</span>
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
        <div className="ml-auto">
          <ProfileSelector />
        </div>
      </div>
      <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden">
        {activeTab === 'play' && <PlayPage />}
        {activeTab === 'bankroll' && <BankrollPage />}
        {activeTab === 'stats' && <StatsPage />}
      </div>
    </div>
  )
}
