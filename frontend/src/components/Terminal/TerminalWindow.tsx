import { lazy, Suspense, useState, useCallback, useEffect } from 'react';
import { Sidebar, type TabName, type CategoryName } from './Sidebar';
import { TabBar, TABS_BY_CATEGORY, DEFAULT_TAB, TAB_COLORS } from './TabBar';
import { usePersistedState } from '@/hooks/usePersistedState';
// All pages lazy-loaded for fast startup
const ValuePage = lazy(() => import('./pages/ValuePage').then(m => ({ default: m.ValuePage })));
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

export function TerminalWindow() {
  const [activeCategory, setActiveCategory] = usePersistedState<CategoryName>('bbq_activeCategory', 'sports');
  const [activeTab, setActiveTab] = usePersistedState<TabName>('bbq_activeTab', 'value');
  const [isProfileActive, setIsProfileActive] = useState(false);
  const [isSettingsActive, setIsSettingsActive] = useState(false);
  const [showWelcome, setShowWelcome] = useState(true);
  const [welcomeChecked, setWelcomeChecked] = useState(false);
  // Track if trading page has been visited — once mounted, keep alive
  const [tradingMounted, setTradingMounted] = useState(false);

  // On mount: check if we should skip the welcome page
  useEffect(() => {
    const checkSession = async () => {
      // If sessionStorage says we already selected a profile this session, skip welcome
      if (localStorage.getItem('bbq_session_active') === '1') {
        setShowWelcome(false);
        setWelcomeChecked(true);
        return;
      }

      // Check if there's an active profile
      try {
        const { active } = await api.getProfiles();
        if (active) {
          localStorage.setItem('bbq_session_active', '1');
          setShowWelcome(false);
          setWelcomeChecked(true);
          return;
        }
      } catch {
        // API not ready — skip welcome so dev/offline mode still works
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

  // Once user visits trading, keep TradingContainer mounted forever
  useEffect(() => {
    if (isTradingTab && !tradingMounted) setTradingMounted(true);
  }, [isTradingTab, tradingMounted]);

  const renderPage = () => {
    // Trading tabs handled separately (kept alive below)
    if (isTradingTab) return null;
    switch (activeTab) {
      case 'value':
        return <ValuePage />;
      case 'dutch':
        return <DutchPage />;
      case 'reverse':
        return <ReversePage />;
      case 'polymarket':
        return <PolymarketPage />;
      case 'stats':
        return <BetsPage />;
      case 'bankroll':
        return <BankrollPage />;
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
            {/* Non-trading pages render normally */}
            {!isTradingTab && (
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
