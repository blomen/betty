import { useState, useCallback } from 'react';
import type { BettingContext } from '@/types';
import { Sidebar, type TabName, type CategoryName } from './Sidebar';
import { TabBar, TABS_BY_CATEGORY, DEFAULT_TAB } from './TabBar';
import { ExtractionProgressBar } from './ExtractionProgressBar';
import {
  ValuePage,
  DutchPage,
  ReversePage,
  PolymarketPage,
  BetsPage,
  BankrollPage,
  SpecialsPage,
  ProfilePage,
} from './pages';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
}

export function TerminalWindow({ context, onRefresh }: TerminalWindowProps) {
  const [activeCategory, setActiveCategory] = useState<CategoryName>('sports');
  const [activeTab, setActiveTab] = useState<TabName>('value');
  const [isProfileActive, setIsProfileActive] = useState(false);

  const handleCategoryChange = useCallback((category: CategoryName) => {
    setActiveCategory(category);
    setActiveTab(DEFAULT_TAB[category]);
    setIsProfileActive(false);
  }, []);

  const handleTabChange = useCallback((tab: TabName) => {
    setActiveTab(tab);
    setIsProfileActive(false);
  }, []);

  const handleProfileClick = useCallback(() => {
    setIsProfileActive(true);
    setActiveTab('profiles');
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
      case 'specials':
        return <SpecialsPage />;
      case 'profiles':
        return <ProfilePage onRefresh={onRefresh} />;
      default:
        return null;
    }
  };

  const tabs = TABS_BY_CATEGORY[activeCategory] || [];

  return (
    <div className="flex h-full bg-bg">
      <Sidebar
        activeCategory={activeCategory}
        onCategoryChange={handleCategoryChange}
        onProfileClick={handleProfileClick}
        isProfileActive={isProfileActive}
      />
      <div className="flex-1 flex flex-col min-w-0">
        {!isProfileActive && <TabBar tabs={tabs} activeTab={activeTab} onTabChange={handleTabChange} />}
        <div className="flex-1 overflow-y-auto p-4">
          <ExtractionProgressBar />
          {isProfileActive ? (
            <ProfilePage onRefresh={onRefresh} />
          ) : tabs.length > 0 ? (
            renderPage()
          ) : (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              Coming soon.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
