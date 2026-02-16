import type { TabName, CategoryName } from './Sidebar';

interface Tab {
  name: TabName;
  label: string;
  color: string;
}

const SPORTS_TABS: Tab[] = [
  { name: 'value',      label: 'Soft',      color: '#FF9800' },
  { name: 'dutch',      label: 'Dutch',     color: '#10b981' },
  { name: 'polymarket', label: 'Poly',      color: '#A855F7' },
  { name: 'stats',      label: 'Stats',     color: '#4FC3F7' },
  { name: 'bankroll',   label: 'Bankroll',  color: '#EC4899' },
  { name: 'specials',   label: 'Oddsboost', color: '#A78BFA' },
];

const STOCKS_TABS: Tab[] = [];

export const TABS_BY_CATEGORY: Record<CategoryName, Tab[]> = {
  sports: SPORTS_TABS,
  stocks: STOCKS_TABS,
};

export const DEFAULT_TAB: Record<CategoryName, TabName> = {
  sports: 'value',
  stocks: 'value',
};

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
                ? 'text-text border-b-accent'
                : 'text-muted hover:text-text border-b-transparent'
              }
            `}
          >
            <span
              className="w-1.5 h-1.5 flex-shrink-0 rounded-full"
              style={{ backgroundColor: tab.color }}
            />
            <span>{tab.label}</span>
          </button>
        );
      })}
    </div>
  );
}
