import type { TabName, CategoryName } from './Sidebar';

interface Tab {
  name: TabName;
  label: string;
  color: string;
}

const SPORTS_TABS: Tab[] = [
  { name: 'home',       label: 'Home',      color: '#64748B' },
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
  sports: 'home',
  stocks: 'tradingBankroll',
};

// Color map for use in pages — matches SPORTS_TABS colors
export const TAB_COLORS: Record<string, string> = {
  home: '#64748B',
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
    // Sports sidebar — soccer ball
    case 'sports':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <polygon points="12,6 14.5,9 13,12 11,12 9.5,9" strokeWidth="1.2"/>
          <line x1="12" y1="3" x2="12" y2="6"/>
          <line x1="9.5" y1="9" x2="5" y2="7.5"/>
          <line x1="14.5" y1="9" x2="19" y2="7.5"/>
          <line x1="13" y1="12" x2="16.5" y2="15"/>
          <line x1="11" y1="12" x2="7.5" y2="15"/>
        </svg>
      );
    // Stocks sidebar — line chart with uptrend
    case 'stocks':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3,18 8,13 12,15 21,6"/>
          <polyline points="16,6 21,6 21,11"/>
          <line x1="3" y1="21" x2="21" y2="21"/>
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
