import { useCallback } from 'react';

export type TabName = 'extract' | 'arb' | 'value' | 'bets' | 'bankroll' | 'profiles' | 'stats';

interface Tab {
  name: TabName;
  label: string;
  color: string;
}

const tabs: Tab[] = [
  { name: 'extract', label: 'Extract', color: '#60a5fa' },
  { name: 'arb', label: 'Arb', color: '#22c55e' },
  { name: 'value', label: 'Value', color: '#f59e0b' },
  { name: 'bets', label: 'Bets', color: '#22d3d8' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats', label: 'Stats', color: '#94a3b8' },
  { name: 'profiles', label: 'Profiles', color: '#8b5cf6' },
];

interface SidebarProps {
  activeTab: TabName;
  onTabChange: (tab: TabName) => void;
}

export function Sidebar({ activeTab, onTabChange }: SidebarProps) {
  const handleTabClick = useCallback((tab: TabName) => {
    onTabChange(tab);
  }, [onTabChange]);

  return (
    <div className="w-44 bg-panel border-r border-border flex flex-col py-4">
      <div className="px-4 mb-4">
        <span className="text-text font-semibold text-sm tracking-wide">OddOpp</span>
      </div>
      <nav className="flex flex-col gap-1 px-2">
        {tabs.map((tab, idx) => {
          const isActive = activeTab === tab.name;
          return (
            <button
              key={tab.name}
              onClick={() => handleTabClick(tab.name)}
              className={`
                flex items-center gap-3 px-3 py-2 rounded-md text-sm font-mono
                transition-colors duration-150 outline-none text-left
                ${isActive
                  ? 'bg-panel2 text-text'
                  : 'text-muted hover:bg-panel2 hover:text-text'
                }
              `}
            >
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: tab.color }}
              />
              <span className="text-muted2 text-xs mr-1">{idx + 1}</span>
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
