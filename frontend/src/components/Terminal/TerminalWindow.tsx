import { useState, useCallback } from 'react';
import type { BettingContext } from '@/types';
import { Sidebar, type TabName } from './Sidebar';
import {
  ValuePage,
  PolymarketPage,
  BetsPage,
  BankrollPage,
  SpecialsPage,
  ProfilePage,
  StatsPage,
} from './pages';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
}

export function TerminalWindow({ context, onRefresh }: TerminalWindowProps) {
  const [activeTab, setActiveTab] = useState<TabName>('value');

  const handleTabChange = useCallback((tab: TabName) => {
    setActiveTab(tab);
  }, []);

  const renderPage = () => {
    switch (activeTab) {
      case 'value':
        return <ValuePage />;
      case 'polymarket':
        return <PolymarketPage />;
      case 'bets':
        return <BetsPage />;
      case 'bankroll':
        return <BankrollPage providers={context.providers} onRefresh={onRefresh} />;
      case 'specials':
        return <SpecialsPage />;
      case 'profiles':
        return <ProfilePage onRefresh={onRefresh} />;
      case 'stats':
        return <StatsPage />;
      default:
        return null;
    }
  };

  return (
    <div className="flex h-full bg-bg">
      <Sidebar activeTab={activeTab} onTabChange={handleTabChange} />
      <div className="flex-1 overflow-y-auto p-6">
        {renderPage()}
      </div>
    </div>
  );
}
