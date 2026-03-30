import { useState, useEffect } from 'react';
import { TabIcon } from './TabBar';
import { api } from '../../services/api';

export type TabName = 'value' | 'play' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingL1' | 'tradingVectors' | 'tradingBankroll' | 'tradingStats';
export type CategoryName = 'sports' | 'stocks';

interface SidebarProps {
  activeCategory: CategoryName;
  onCategoryChange: (category: CategoryName) => void;
  onProfileClick: () => void;
  isProfileActive: boolean;
  onSettingsClick: () => void;
  isSettingsActive: boolean;
}

function SidebarButton({
  isActive,
  onClick,
  title,
  children,
}: {
  isActive: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-12 h-12 flex items-center justify-center transition-shadow ${
        isActive
          ? 'border-2 border-text text-text bg-white/[0.03]'
          : 'border-2 border-transparent text-muted hover:border-muted hover:text-text'
      }`}
      title={title}
    >
      {children}
    </button>
  );
}

function MirrorButton() {
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    api.getMirrorStatus()
      .then(r => setRunning(r.running))
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, []);

  const toggle = async () => {
    setLoading(true);
    try {
      if (running) {
        await api.stopMirror();
      } else {
        await api.startMirror();
      }
      refresh();
    } catch (err) {
      console.error('[mirror]', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={toggle}
      disabled={loading}
      className={`w-12 h-12 flex items-center justify-center mb-1 border-2 transition ${
        running
          ? 'border-success text-success bg-success/5'
          : 'border-transparent text-muted hover:border-muted hover:text-text'
      } ${loading ? 'opacity-50' : ''}`}
      title={running ? 'Stop Mirror' : 'Start Mirror'}
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        {running ? (
          <>
            <line x1="10" y1="15" x2="10" y2="9" />
            <line x1="14" y1="15" x2="14" y2="9" />
          </>
        ) : (
          <polygon points="10,8 16,12 10,16" fill="currentColor" stroke="none" />
        )}
      </svg>
    </button>
  );
}

export function Sidebar({ activeCategory, onCategoryChange, onProfileClick, isProfileActive, onSettingsClick, isSettingsActive }: SidebarProps) {
  const isOverlay = isProfileActive || isSettingsActive;

  return (
    <div className="w-16 bg-panel border-r-2 border-border flex flex-col items-center py-4 flex-shrink-0">
      {/* Logo */}
      <div className="mb-4">
        <TabIcon name="app" color="currentColor" size={24} />
      </div>

      {/* Categories */}
      <nav className="flex flex-col gap-1">
        <SidebarButton
          isActive={activeCategory === 'sports' && !isOverlay}
          onClick={() => onCategoryChange('sports')}
          title="Sports"
        >
          <TabIcon name="sports" color="currentColor" size={20} />
        </SidebarButton>
        <SidebarButton
          isActive={activeCategory === 'stocks' && !isOverlay}
          onClick={() => onCategoryChange('stocks')}
          title="Stocks"
        >
          <TabIcon name="stocks" color="currentColor" size={20} />
        </SidebarButton>
      </nav>

      {/* Separator */}
      <div className="flex-1 flex items-center justify-center">
        <span className="text-muted2 text-[10px] select-none">──</span>
      </div>

      {/* Mirror toggle */}
      <MirrorButton />

      {/* Settings */}
      <SidebarButton
        isActive={isSettingsActive}
        onClick={onSettingsClick}
        title="Settings"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
      </SidebarButton>

      {/* Profile */}
      <SidebarButton
        isActive={isProfileActive}
        onClick={onProfileClick}
        title="Profiles"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      </SidebarButton>
    </div>
  );
}
