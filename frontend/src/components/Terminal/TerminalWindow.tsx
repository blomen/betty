import { useEffect, useRef, useState } from 'react';
import type { BettingContext, OpportunityWithEvent, Bet } from '@/types';
import { useChat } from '@/hooks/useChat';
import { useBankroll } from '@/hooks/useBankroll';
import { TerminalHeader } from './TerminalHeader';
import { TerminalInput } from './TerminalInput';
import { ChatMessage } from './ChatMessage';
import { WelcomeMessage } from './WelcomeMessage';
import { BalanceBreakdownModal } from './BalanceBreakdownModal';
import { OpportunitiesOverlay } from './OpportunitiesOverlay';
import { BetPlacementModal } from './BetPlacementModal';
import { BetsPanel } from './BetsPanel';
import { SettleBetModal } from './SettleBetModal';

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
  isContextLoading: boolean;
}

export function TerminalWindow({
  context,
  onRefresh,
  isContextLoading,
}: TerminalWindowProps) {
  const { messages, isLoading, sendMessage, stopGeneration, clearMessages } =
    useChat(context);
  const { exposure } = useBankroll(30000);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Modal states
  const [showBalanceBreakdown, setShowBalanceBreakdown] = useState(false);
  const [showOpportunities, setShowOpportunities] = useState(false);
  const [showBets, setShowBets] = useState(false);
  const [selectedOpportunity, setSelectedOpportunity] = useState<OpportunityWithEvent | null>(
    null
  );
  const [selectedBetToSettle, setSelectedBetToSettle] = useState<Bet | null>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+O: Open opportunities
      if (e.ctrlKey && e.key === 'o' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        setShowOpportunities(true);
      }
      // Ctrl+B: Open bets
      if (e.ctrlKey && e.key === 'b' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        setShowBets(true);
      }
      // Ctrl+L: Clear chat
      if (e.ctrlKey && e.key === 'l' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        clearMessages();
      }
      // F5: Refresh
      if (e.key === 'F5') {
        e.preventDefault();
        onRefresh();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [clearMessages, onRefresh]);

  const handleSelectOpportunity = (opportunity: OpportunityWithEvent) => {
    setSelectedOpportunity(opportunity);
    setShowOpportunities(false);
  };

  const handleCloseBetPlacement = () => {
    setSelectedOpportunity(null);
  };

  const handleSettleBet = (bet: Bet) => {
    setSelectedBetToSettle(bet);
  };

  const handleCloseSettleBet = () => {
    setSelectedBetToSettle(null);
  };

  return (
    <div className="flex flex-col h-full bg-terminal-bg">
      {/* Header */}
      <TerminalHeader
        context={context}
        exposure={exposure}
        isLoading={isContextLoading}
        onClear={clearMessages}
        onRefresh={onRefresh}
        onShowBalanceBreakdown={() => setShowBalanceBreakdown(true)}
      />

      {/* Messages area - centered */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <WelcomeMessage
              context={context}
              exposure={exposure}
              onShowOpportunities={() => setShowOpportunities(true)}
              onShowBets={() => setShowBets(true)}
            />
          ) : (
            <div className="pb-4">
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Input - centered */}
      <div className="max-w-4xl mx-auto w-full">
        <TerminalInput
          onSend={sendMessage}
          onStop={stopGeneration}
          isLoading={isLoading}
        />
      </div>

      {/* Modals and Overlays */}
      <BalanceBreakdownModal
        exposure={exposure}
        isOpen={showBalanceBreakdown}
        onClose={() => setShowBalanceBreakdown(false)}
      />

      <OpportunitiesOverlay
        isOpen={showOpportunities}
        onClose={() => setShowOpportunities(false)}
        onSelectOpportunity={handleSelectOpportunity}
      />

      <BetPlacementModal
        opportunity={selectedOpportunity}
        isOpen={selectedOpportunity !== null}
        onClose={handleCloseBetPlacement}
      />

      <BetsPanel
        isOpen={showBets}
        onClose={() => setShowBets(false)}
        onSettleBet={handleSettleBet}
      />

      <SettleBetModal
        bet={selectedBetToSettle}
        isOpen={selectedBetToSettle !== null}
        onClose={handleCloseSettleBet}
      />
    </div>
  );
}
