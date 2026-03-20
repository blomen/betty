import { useState, useEffect, useRef } from 'react';
import { TabIcon } from './TabBar';
import { api } from '../../services/api';

interface MirrorProvider {
  id: string;
  name: string;
  running: boolean;
}

export type TabName = 'value' | 'dutch' | 'reverse' | 'polymarket' | 'stats' | 'bankroll' | 'profiles' | 'settings' | 'tradingL1' | 'tradingL2' | 'tradingBankroll' | 'tradingStats';
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
          ? 'border-2 border-text text-text shadow-[0_0_12px_rgba(212,212,212,0.15)] bg-white/[0.03]'
          : 'border-2 border-transparent text-muted hover:border-muted hover:text-text'
      }`}
      title={title}
    >
      {children}
    </button>
  );
}

function MirrorButton() {
  const [providers, setProviders] = useState<MirrorProvider[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const anyRunning = providers.some(p => p.running);

  const refresh = () => {
    api.getMirrorProviders()
      .then((r: { providers: MirrorProvider[] }) => setProviders(r.providers))
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10_000);
    return () => clearInterval(id);
  }, []);

  // Close menu on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = async (providerId: string, isRunning: boolean) => {
    setLoading(providerId);
    try {
      if (isRunning) {
        await api.stopMirror(providerId);
      } else {
        await api.startMirror(providerId);
      }
      refresh();
    } catch (err) {
      console.error('[mirror]', err);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(!open)}
        className={`w-12 h-12 flex items-center justify-center mb-1 border-2 transition ${
          anyRunning
            ? 'border-success text-success shadow-[0_0_12px_rgba(76,175,80,0.25)] bg-success/5'
            : 'border-transparent text-muted hover:border-muted hover:text-text'
        }`}
        title="Mirror"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          {anyRunning ? (
            <>
              <line x1="10" y1="15" x2="10" y2="9" />
              <line x1="14" y1="15" x2="14" y2="9" />
            </>
          ) : (
            <polygon points="10,8 16,12 10,16" fill="currentColor" stroke="none" />
          )}
        </svg>
      </button>

      {open && (
        <div className="absolute left-14 bottom-0 z-50 border-2 border-border bg-panel min-w-[180px] py-1 shadow-lg">
          <div className="px-3 py-1 text-[10px] text-muted uppercase tracking-wider">Mirror</div>
          {providers.map(p => (
            <button
              key={p.id}
              onClick={() => toggle(p.id, p.running)}
              disabled={loading === p.id}
              className={`w-full px-3 py-1.5 text-xs font-mono flex items-center gap-2 hover:bg-white/5 ${
                loading === p.id ? 'opacity-50' : ''
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${p.running ? 'bg-success' : 'bg-muted/30'}`} />
              <span className={p.running ? 'text-text' : 'text-muted'}>{p.name}</span>
              <span className="ml-auto text-[10px] text-muted">
                {loading === p.id ? '...' : p.running ? 'stop' : 'start'}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
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
