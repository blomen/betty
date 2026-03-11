import { lazy, Suspense, useState, useCallback, useEffect } from 'react';
import type { BettingContext } from '@/types';
import { Sidebar, type TabName, type CategoryName } from './Sidebar';
import { TabBar, TABS_BY_CATEGORY, DEFAULT_TAB } from './TabBar';
// Eager: core pages always in main bundle
import {
  ValuePage,
  DutchPage,
  ReversePage,
  PolymarketPage,
  BankrollPage,
  WelcomePage,
} from './pages';
// Lazy: secondary/heavy pages split into separate chunks
const BetsPage = lazy(() => import('./pages/BetsPage').then(m => ({ default: m.BetsPage })));
const ProfilePage = lazy(() => import('./pages/ProfilePage').then(m => ({ default: m.ProfilePage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })));
const TradingBankrollPage = lazy(() => import('./pages/TradingBankrollPage').then(m => ({ default: m.TradingBankrollPage })));
const TradingTodayPage = lazy(() => import('./pages/TradingTodayPage').then(m => ({ default: m.TradingTodayPage })));
const TradingBuilderPage = lazy(() => import('./pages/TradingBuilderPage').then(m => ({ default: m.TradingBuilderPage })));
const TradingTradesPage = lazy(() => import('./pages/TradingTradesPage').then(m => ({ default: m.TradingTradesPage })));
const TradingJournalPage = lazy(() => import('./pages/TradingJournalPage').then(m => ({ default: m.TradingJournalPage })));
import { api } from '@/services/api';
import { ErrorNotificationBar, ConnectionErrorBar } from './ErrorNotificationBar';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
}

export function TerminalWindow({ context, onRefresh }: TerminalWindowProps) {
  const [activeCategory, setActiveCategory] = useState<CategoryName>('sports');
  const [activeTab, setActiveTab] = useState<TabName>('value');
  const [isProfileActive, setIsProfileActive] = useState(false);
  const [isSettingsActive, setIsSettingsActive] = useState(false);
  const [showWelcome, setShowWelcome] = useState(true);
  const [welcomeChecked, setWelcomeChecked] = useState(false);

  // On mount: check if we should skip the welcome page
  useEffect(() => {
    const checkSession = async () => {
      // If sessionStorage says we already selected a profile this session, skip welcome
      if (sessionStorage.getItem('bbq_session_active') === '1') {
        setShowWelcome(false);
        setWelcomeChecked(true);
        return;
      }

      // Check if there's an active profile
      try {
        const { active } = await api.getProfiles();
        if (active) {
          sessionStorage.setItem('bbq_session_active', '1');
          setShowWelcome(false);
          setWelcomeChecked(true);
          return;
        }
      } catch {
        // API not ready yet — show welcome
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

  const renderPage = () => {
    switch (activeTab) {
      case 'value':
        return <ValuePage providers={context.providers} />;
      case 'dutch':
        return <DutchPage providers={context.providers} />;
      case 'reverse':
        return <ReversePage />;
      case 'polymarket':
        return <PolymarketPage />;
      case 'stats':
        return <BetsPage />;
      case 'bankroll':
        return <BankrollPage providers={context.providers} onRefresh={onRefresh} />;
      case 'profiles':
        return <ProfilePage onRefresh={onRefresh} />;
      case 'settings':
        return <SettingsPage />;
      case 'tradingBankroll':
        return <TradingBankrollPage />;
      case 'tradingToday':
        return <TradingTodayPage />;
      case 'tradingBuilder':
        return <TradingBuilderPage />;
      case 'tradingTrades':
        return <TradingTradesPage />;
      case 'tradingJournal':
        return <TradingJournalPage />;
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
    return <WelcomePage onProfileSelected={handleProfileSelected} />;
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
        <div className="flex-1 overflow-y-auto p-3">
          <Suspense fallback={<div className="p-4 text-muted text-sm">Loading...</div>}>
            {isOverlay ? (
              renderPage()
            ) : tabs.length > 0 ? (
              renderPage()
            ) : (
              <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
                Coming soon.
              </div>
            )}
          </Suspense>
        </div>
      </div>
    </div>
  );
}
