import { TabIcon } from './TabBar';

export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingIntraday' | 'tradingBankroll' | 'tradingStats';
export type CategoryName = 'sports' | 'stocks';

interface SidebarProps {
  activeCategory: CategoryName;
  onCategoryChange: (category: CategoryName) => void;
  onProfileClick: () => void;
  isProfileActive: boolean;
  onSettingsClick: () => void;
  isSettingsActive: boolean;
}

export function Sidebar({ activeCategory, onCategoryChange, onProfileClick, isProfileActive, onSettingsClick, isSettingsActive }: SidebarProps) {
  const isOverlay = isProfileActive || isSettingsActive;

  return (
    <div className="w-14 bg-panel border-r border-border flex flex-col items-center py-3 flex-shrink-0 relative">
      {/* Categories */}
      <nav className="flex flex-col gap-1">
        <button
          onClick={() => onCategoryChange('sports')}
          className={`w-10 h-10 flex items-center justify-center rounded transition-colors ${
            activeCategory === 'sports' && !isOverlay
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
            activeCategory === 'stocks' && !isOverlay
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

      {/* Settings */}
      <button
        onClick={onSettingsClick}
        className={`w-10 h-10 flex items-center justify-center rounded transition-colors ${
          isSettingsActive
            ? 'bg-panel2 text-text'
            : 'text-muted hover:bg-panel2 hover:text-text'
        }`}
        title="Settings"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </button>

      {/* Profile */}
      <button
        onClick={onProfileClick}
        className={`w-10 h-10 flex items-center justify-center rounded transition-colors mt-1 ${
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
