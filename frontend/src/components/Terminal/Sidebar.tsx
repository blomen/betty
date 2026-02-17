export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'specials' | 'profiles';
export type CategoryName = 'sports' | 'stocks';

interface SidebarProps {
  activeCategory: CategoryName;
  onCategoryChange: (category: CategoryName) => void;
  onProfileClick: () => void;
  isProfileActive: boolean;
}

export function Sidebar({ activeCategory, onCategoryChange, onProfileClick, isProfileActive }: SidebarProps) {
  return (
    <div className="w-14 bg-panel border-r border-border flex flex-col items-center py-3 flex-shrink-0">
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
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polygon points="12,7 14.5,8.8 13.5,11.5 10.5,11.5 9.5,8.8" />
            <line x1="12" y1="2" x2="12" y2="7" />
            <line x1="14.5" y1="8.8" x2="21" y2="6.5" />
            <line x1="13.5" y1="11.5" x2="19.5" y2="16" />
            <line x1="10.5" y1="11.5" x2="4.5" y2="16" />
            <line x1="9.5" y1="8.8" x2="3" y2="6.5" />
          </svg>
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
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
            <polyline points="16 7 22 7 22 13" />
          </svg>
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
