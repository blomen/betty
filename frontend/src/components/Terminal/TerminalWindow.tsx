import { lazy, Suspense, useState, useCallback, useEffect } from 'react';
import { Sidebar, type TabName, type CategoryName } from './Sidebar';
import { TabBar, TABS_BY_CATEGORY, DEFAULT_TAB, TAB_COLORS } from './TabBar';
import { usePersistedState } from '@/hooks/usePersistedState';
// All pages lazy-loaded for fast startup
const ValuePage = lazy(() => import('./pages/ValuePage').then(m => ({ default: m.ValuePage })));
const PlayPage = lazy(() => import('./pages/PlayPage').then(m => ({ default: m.PlayPage })));
const DutchPage = lazy(() => import('./pages/DutchPage').then(m => ({ default: m.DutchPage })));
const ReversePage = lazy(() => import('./pages/ReversePage').then(m => ({ default: m.ReversePage })));
const PolymarketPage = lazy(() => import('./pages/PolymarketPage').then(m => ({ default: m.PolymarketPage })));
const BankrollPage = lazy(() => import('./pages/BankrollPage').then(m => ({ default: m.BankrollPage })));
const WelcomePage = lazy(() => import('./pages/WelcomePage').then(m => ({ default: m.WelcomePage })));
const BetsPage = lazy(() => import('./pages/BetsPage').then(m => ({ default: m.BetsPage })));
const ProfilePage = lazy(() => import('./pages/ProfilePage').then(m => ({ default: m.ProfilePage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })));
const TradingBankrollPage = lazy(() => import('./pages/TradingBankrollPage').then(m => ({ default: m.TradingBankrollPage })));
const TradingContainer = lazy(() => import('./pages/TradingContainer').then(m => ({ default: m.TradingContainer })));
const TradingStatsPage = lazy(() => import('./pages/TradingStatsPage').then(m => ({ default: m.TradingStatsPage })));
import { api } from '@/services/api';
import { ErrorNotificationBar, ConnectionErrorBar } from './ErrorNotificationBar';
import { BetMirrorToast } from './BetMirrorToast';

/** Pages that stay mounted once visited — hidden via CSS on tab switch for instant switching. */
const KEEP_ALIVE_PAGES: Record<string, React.LazyExoticComponent<React.ComponentType>> = {
  value: ValuePage,
  play: PlayPage,
  dutch: DutchPage,
  reverse: ReversePage,
  polymarket: PolymarketPage,
  stats: BetsPage,
  bankroll: BankrollPage,
};

export function TerminalWindow() {
  const [activeCategory, setActiveCategory] = usePersistedState<CategoryName>('bbq_activeCategory', 'sports');
  const [activeTab, setActiveTab] = usePersistedState<TabName>('bbq_activeTab', 'value');
  const [isProfileActive, setIsProfileActive] = useState(false);
  const [isSettingsActive, setIsSettingsActive] = useState(false);
  const [showWelcome, setShowWelcome] = useState(true);
  const [welcomeChecked, setWelcomeChecked] = useState(false);
  // All keep-alive pages and trading mount eagerly so they're ready on tab switch
  const tradingMounted = true;
  const mountedPages = new Set(Object.keys(KEEP_ALIVE_PAGES));

  // On mount: check if we should skip the welcome page
  useEffect(() => {
    const checkSession = async () => {
      // If localStorage says we already selected a profile this session, skip welcome
      if (localStorage.getItem('bbq_session_active') === '1') {
        setShowWelcome(false);
        setWelcomeChecked(true);
        return;
      }

      // Check if there's an active profile — short timeout so UI isn't blocked by slow backend
      try {
        const { active } = await api.getProfiles(undefined, 2000);
        if (active) {
          localStorage.setItem('bbq_session_active', '1');
          setShowWelcome(false);
          setWelcomeChecked(true);
          return;
        }
      } catch {
        // API not ready or timed out — skip welcome so dev/offline mode still works
        setShowWelcome(false);
      }
      setWelcomeChecked(true);
    };
    checkSession();
  }, []);

  const handleProfileSelected = useCallback(() => {
    setShowWelcome(false);
  }, []);

  const handleCategoryChange = useCallback((category: CategoryName) => {
    setActiveCategory(category);
    setActiveTab(DEFAULT_TAB[category]);
    setIsProfileActive(false);
    setIsSettingsActive(false);
  }, []);

  const handleTabChange = useCallback((tab: TabName) => {
    setActiveTab(tab);
    setIsProfileActive(false);
    setIsSettingsActive(false);
  }, []);

  const handleProfileClick = useCallback(() => {
    setIsProfileActive(true);
    setIsSettingsActive(false);
    setActiveTab('profiles');
  }, []);

  const handleSettingsClick = useCallback(() => {
    setIsSettingsActive(true);
    setIsProfileActive(false);
    setActiveTab('settings');
  }, []);

  const isTradingTab = activeTab === 'tradingL1' || activeTab === 'tradingVectors';


  const renderPage = () => {
    // Trading tabs and keep-alive pages handled separately (kept alive below)
    if (isTradingTab) return null;
    if (activeTab in KEEP_ALIVE_PAGES) return null;
    switch (activeTab) {
      case 'profiles':
        return <ProfilePage />;
      case 'settings':
        return <SettingsPage />;
      case 'tradingBankroll':
        return <TradingBankrollPage />;
      case 'tradingStats':
        return <TradingStatsPage />;
      default:
        return null;
    }
  };

  // Don't render anything until we've checked whether to show welcome
  if (!welcomeChecked) {
    return <div className="h-full bg-bg" />;
  }

  // Welcome page — full screen, no sidebar/tabs
  if (showWelcome) {
    return (
      <Suspense fallback={<div className="h-full bg-bg" />}>
        <WelcomePage onProfileSelected={handleProfileSelected} />
      </Suspense>
    );
  }

  const tabs = TABS_BY_CATEGORY[activeCategory] || [];
  const isOverlay = isProfileActive || isSettingsActive;

  return (
    <div className="flex h-full bg-bg">
      <Sidebar
        activeCategory={activeCategory}
        onCategoryChange={handleCategoryChange}
        onProfileClick={handleProfileClick}
        isProfileActive={isProfileActive}
        onSettingsClick={handleSettingsClick}
        isSettingsActive={isSettingsActive}
      />
      <div className="flex-1 flex flex-col min-w-0">
        {!isOverlay && (
          <TabBar tabs={tabs} activeTab={activeTab} onTabChange={handleTabChange} />
        )}
        <ConnectionErrorBar />
        <ErrorNotificationBar />
        <BetMirrorToast />
        <div className="flex-1 flex flex-col min-h-0 p-4 overflow-hidden" style={{ '--tab-accent': TAB_COLORS[activeTab] || '#737373' } as React.CSSProperties}>
          <Suspense fallback={<div className="p-4 text-muted text-sm animate-blink">█</div>}>
            {/* TradingContainer stays mounted once visited — hidden via CSS when on other tabs */}
            {tradingMounted && (
              <div className={`flex-1 flex flex-col min-h-0 ${isTradingTab ? '' : 'hidden'}`}>
                <TradingContainer activeSubTab={(isTradingTab ? activeTab : 'tradingL1') as 'tradingL1' | 'tradingVectors'} />
              </div>
            )}
            {/* Keep-alive pages: mounted once visited, hidden via CSS when inactive */}
            {Array.from(mountedPages).map(tabName => {
              const PageComponent = KEEP_ALIVE_PAGES[tabName];
              if (!PageComponent) return null;
              const isActive = activeTab === tabName && !isTradingTab && !isOverlay;
              return (
                <div key={tabName} className={`flex-1 flex flex-col min-h-0 ${isActive ? '' : 'hidden'}`}>
                  <PageComponent />
                </div>
              );
            })}
            {/* Non-keep-alive pages render normally (profiles, settings, trading sub-pages) */}
            {!isTradingTab && !(activeTab in KEEP_ALIVE_PAGES) && (
              isOverlay ? (
                renderPage()
              ) : tabs.length > 0 ? (
                renderPage()
              ) : (
                <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
                  Coming soon.
                </div>
              )
            )}
          </Suspense>
        </div>
      </div>
    </div>
  );
}
