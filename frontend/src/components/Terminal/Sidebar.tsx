import { TabIcon } from './TabBar';

export type TabName = 'monitor' | 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'specials' | 'profiles' | 'tradingBankroll' | 'tradingToday' | 'tradingBuilder' | 'tradingTrades' | 'tradingJournal';
export type CategoryName = 'sports' | 'stocks';

interface SidebarProps {
  activeCategory: CategoryName;
  onCategoryChange: (category: CategoryName) => void;
  onProfileClick: () => void;
  isProfileActive: boolean;
}

export function Sidebar({ activeCategory, onCategoryChange, onProfileClick, isProfileActive }: SidebarProps) {
  return (
    <div className="w-14 bg-panel border-r border-border flex flex-col items-center py-3 flex-shrink-0 relative">
      {/* Categories */}
      <nav className="flex flex-col gap-1">
        <button
          onClick={() => onCategoryChange('sports')}
          className={`w-10 h-10 flex items-center justify-center rounded transition-colors ${
            activeCategory === 'sports' && !isProfileActive
              ? 'bg-panel2 text-text'
              : 'text-muted hover:bg-panel2 hover:text-text'
          }`}
          title="Sports"
        >
          <TabIcon name="sports" color="currentColor" size={20} />
        </button>
        <button
          onClick={() => onCategoryChange('stocks')}
          className={`w-10 h-10 flex items-center justify-center rounded transition-colors ${
            activeCategory === 'stocks' && !isProfileActive
              ? 'bg-panel2 text-text'
              : 'text-muted hover:bg-panel2 hover:text-text'
          }`}
          title="Stocks"
        >
          <TabIcon name="stocks" color="currentColor" size={20} />
        </button>
      </nav>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Profile */}
      <button
        onClick={onProfileClick}
        className={`w-10 h-10 flex items-center justify-center rounded transition-colors ${
          isProfileActive
            ? 'bg-panel2 text-text'
            : 'text-muted hover:bg-panel2 hover:text-text'
        }`}
        title="Profiles"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      </button>
    </div>
  );
}
