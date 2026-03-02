import type { TabName, CategoryName } from './Sidebar';

interface Tab {
  name: TabName;
  label: string;
  color: string;
}

const SPORTS_TABS: Tab[] = [
  { name: 'monitor',    label: 'Monitor',   color: '#64748B' },
  { name: 'value',      label: 'Soft',      color: '#FF9800' },
  { name: 'dutch',      label: 'Dutch',     color: '#10b981' },
  { name: 'reverse',    label: 'Reverse',   color: '#EF5350' },
  { name: 'polymarket', label: 'Poly',      color: '#A855F7' },
  { name: 'stats',      label: 'Stats',     color: '#4FC3F7' },
  { name: 'bankroll',   label: 'Bankroll',  color: '#EC4899' },
  { name: 'specials',   label: 'Specials',   color: '#A78BFA' },
];

const STOCKS_TABS: Tab[] = [
  { name: 'tradingBankroll', label: 'Bankroll', color: '#EC4899' },
  { name: 'tradingToday',    label: 'Today',    color: '#FACC15' },
  { name: 'tradingBuilder',  label: 'Builder',  color: '#22C55E' },
  { name: 'tradingTrades',   label: 'Trades',   color: '#4FC3F7' },
  { name: 'tradingJournal',  label: 'Journal',  color: '#A78BFA' },
];

export const TABS_BY_CATEGORY: Record<CategoryName, Tab[]> = {
  sports: SPORTS_TABS,
  stocks: STOCKS_TABS,
};

export const DEFAULT_TAB: Record<CategoryName, TabName> = {
  sports: 'monitor',
  stocks: 'tradingBankroll',
};

// Color map for use in pages — matches SPORTS_TABS colors
export const TAB_COLORS: Record<string, string> = {
  monitor: '#64748B',
  value: '#FF9800',
  dutch: '#10b981',
  reverse: '#EF5350',
  polymarket: '#A855F7',
  stats: '#4FC3F7',
  bankroll: '#EC4899',
  specials: '#A78BFA',
  bets: '#4FC3F7',
  profiles: '#A78BFA',
  success: '#10b981',
  tradingBankroll: '#EC4899',
  tradingToday: '#FACC15',
  tradingBuilder: '#22C55E',
  tradingTrades: '#4FC3F7',
  tradingJournal: '#A78BFA',
};

export function TabIcon({ name, color, size = 16 }: { name: string; color: string; size?: number }) {
  const w = size;
  const h = size;
  const v = '0 0 24 24';

  switch (name) {
    // Sports sidebar — trophy
    case 'sports':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 3h12v6a6 6 0 01-12 0V3z"/>
          <path d="M6 5H4a1 1 0 00-1 1v1a3 3 0 003 3"/>
          <path d="M18 5h2a1 1 0 011 1v1a3 3 0 01-3 3"/>
          <line x1="12" y1="15" x2="12" y2="18"/>
          <path d="M8 21h8l-1-3H9z"/>
        </svg>
      );
    // Stocks sidebar — candlesticks
    case 'stocks':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <line x1="7" y1="4" x2="7" y2="20"/>
          <rect x="5" y="8" width="4" height="7" rx="0.5" fill="currentColor" fillOpacity="0.15"/>
          <line x1="17" y1="3" x2="17" y2="17"/>
          <rect x="15" y="6" width="4" height="6" rx="0.5" fill="currentColor" fillOpacity="0.15"/>
        </svg>
      );
    // App logo — BBQ chicken
    case 'app':
      return (
        <svg width={w} height={h} viewBox="0 0 32 32" fill="none">
          <path d="M30.5,3H29V1.51a1.5,1.5,0,1,0-3,0V3L21,8l3,3,5-5h1.5a1.5,1.5,0,0,0,0-3Z" fill="#ffe6c1"/>
          <path d="M26,12,20,6,5,14.13A9.5,9.5,0,1,0,17.87,27Z" fill="#dc562c"/>
          <path d="M14.7,24a1,1,0,0,1-.71-.29L8.29,18a1,1,0,0,1,1.41-1.41l5.7,5.7A1,1,0,0,1,14.7,24Z" fill="#8f2c0e"/>
          <path d="M19,19.8a1,1,0,0,1-.71-.29l-5.8-5.8a1,1,0,0,1,1.41-1.41l5.8,5.8A1,1,0,0,1,19,19.8Z" fill="#8f2c0e"/>
          <path d="M10.6,28.2a1,1,0,0,1-.71-.29l-5.8-5.8a1,1,0,0,1,1.41-1.41l5.8,5.8a1,1,0,0,1-.71,1.71Z" fill="#8f2c0e"/>
        </svg>
      );
    // All tab icons — thin colored ring
    default:
      return (
        <svg width={w} height={h} viewBox={v} fill="none">
          <circle cx="12" cy="12" r="5" stroke={color} strokeWidth="1.5"/>
        </svg>
      );
  }
}

interface TabBarProps {
  tabs: Tab[];
  activeTab: TabName;
  onTabChange: (tab: TabName) => void;
}

export function TabBar({ tabs, activeTab, onTabChange }: TabBarProps) {
  if (tabs.length === 0) return null;

  return (
    <div className="flex items-center gap-0 border-b border-border bg-panel px-2 flex-shrink-0">
      {tabs.map(tab => {
        const isActive = activeTab === tab.name;
        return (
          <button
            key={tab.name}
            onClick={() => onTabChange(tab.name)}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-sm font-mono
              transition-colors duration-150 outline-none border-b-2 -mb-px
              ${isActive
                ? 'text-text'
                : 'text-muted hover:text-text border-b-transparent'
              }
            `}
            style={isActive ? { borderBottomColor: tab.color } : undefined}
          >
            <TabIcon name={tab.name} color={tab.color} />
            <span>{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}
