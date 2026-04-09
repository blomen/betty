import type { TabName, CategoryName } from './Sidebar';

interface Tab {
  name: TabName;
  label: string;
  color: string;
}

const SPORTS_TABS: Tab[] = [
  { name: 'play',       label: 'Play',      color: '#22c55e' },
  { name: 'polymarket', label: 'Poly',      color: '#A855F7' },
  { name: 'value',      label: 'Soft',      color: '#FF9800' },
  { name: 'reverse',    label: 'Pinnacle',  color: '#EF5350' },
  { name: 'dutch',      label: 'Dutch',     color: '#10b981' },
  { name: 'bankroll',   label: 'Bankroll',  color: '#EC4899' },
  { name: 'stats',      label: 'Stats',     color: '#1E88E5' },
];

const STOCKS_TABS: Tab[] = [
  { name: 'tradingChart', label: 'Chart', color: '#F97316' },
  { name: 'tradingDqn', label: 'DQN', color: '#EF4444' },
  { name: 'tradingBankroll', label: 'Bankroll', color: '#EC4899' },
  { name: 'tradingStats',    label: 'Stats',    color: '#1E88E5' },
];

const isLocal = typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');

export const TABS_BY_CATEGORY: Record<CategoryName, Tab[]> = {
  sports: isLocal ? SPORTS_TABS : SPORTS_TABS.filter(t => t.name !== 'play'),
  stocks: STOCKS_TABS,
};

export const DEFAULT_TAB: Record<CategoryName, TabName> = {
  sports: 'value',
  stocks: 'tradingChart',
};

// Color map for use in pages — matches SPORTS_TABS colors
export const TAB_COLORS: Record<string, string> = {
  play: '#22c55e',
  value: '#FF9800',
  dutch: '#10b981',
  reverse: '#EF5350',
  polymarket: '#A855F7',
  stats: '#1E88E5',
  bankroll: '#EC4899',
  specials: '#A78BFA',
  bets: '#1E88E5',
  profiles: '#A78BFA',
  settings: '#9AA0A6',
  success: '#10b981',
  tradingChart: '#06B6D4',
  tradingDqn: '#EF4444',
  tradingBankroll: '#EC4899',
  tradingStats: '#1E88E5',
};

export function TabIcon({ name, color, size = 16 }: { name: string; color: string; size?: number }) {
  const w = size;
  const h = size;
  const v = '0 0 24 24';

  switch (name) {
    // Sports sidebar — football
    case 'sports':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
          <path d="M2 12h20"/>
        </svg>
      );
    // Stocks sidebar — trending up arrow
    case 'stocks':
      return (
        <svg width={w} height={h} viewBox={v} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>
          <polyline points="17 6 23 6 23 12"/>
        </svg>
      );
    // App logo — fire
    case 'app':
      return (
        <svg width={w} height={h} viewBox="0 0 24 24" fill="none">
          <path d="M12 23c-4.97 0-8-3.03-8-7 0-2.5 1.5-5.5 3-7.5.42-.57 1.3-.26 1.26.44-.1 1.76.47 3.56 1.74 4.56 0-3 1.5-6.5 4-8.5.56-.44 1.36.06 1.2.74C14.58 8.5 16.5 5 19 3c.44-.36 1.08.06.96.62C19.2 7.3 20 10 20 13c0 5.5-3.5 10-8 10z" fill="#FF6B35"/>
          <path d="M12 23c-2.76 0-5-2.24-5-5 0-1.5.89-3.25 2-4.5.34-.38.96-.14.92.32-.08 1.06.33 2.13 1.08 2.68 0-1.8 1-3.9 2.5-5.1.37-.3.9.02.82.46-.2 1.1.46 2.15 1.68 3.14.76.62 1 1.62 1 2.5 0 3-1.76 5.5-5 5.5z" fill="#FFD93D"/>
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
    <div className="flex items-center gap-1 border-b-2 border-border bg-panel px-3 flex-shrink-0">
      {tabs.map(tab => {
        const isActive = activeTab === tab.name;
        return (
          <button
            key={tab.name}
            onClick={() => onTabChange(tab.name)}
            className={`
              flex items-center gap-1.5 px-4 py-2.5 text-xs font-mono
              uppercase tracking-wider outline-none
              ${isActive ? 'font-bold' : 'text-muted hover:text-text'}
            `}
            style={isActive ? {
              background: `linear-gradient(180deg, ${tab.color}, ${tab.color}dd)`,
              color: '#0a0e0a',
              boxShadow: `0 0 12px ${tab.color}4d, 0 2px 8px rgba(0,0,0,0.3)`,
            } : undefined}
          >
            <span style={{ color: isActive ? '#0a0e0a' : tab.color }}>●</span>
            <span>{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}
