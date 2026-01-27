import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import type { BettingContext, OpportunityWithEvent, Bet, Profile, ProfileCreate, ProfileUpdate } from '@/types';
import { useChat } from '@/hooks/useChat';
import { useBankroll } from '@/hooks/useBankroll';
import { useExtraction } from '@/hooks/useExtraction';
import { TerminalHeader } from './TerminalHeader';
import { TerminalInput } from './TerminalInput';
import { ChatMessage } from './ChatMessage';
import { WelcomeMessage } from './WelcomeMessage';
import { BalanceBreakdownModal } from './BalanceBreakdownModal';
import { OpportunitiesOverlay } from './OpportunitiesOverlay';
import { BetPlacementModal } from './BetPlacementModal';
import { BetsPanel } from './BetsPanel';
import { SettleBetModal } from './SettleBetModal';
import { createCommandRegistry, formatCommandHelp } from '@/utils/commands';
import { api } from '@/services/api';

interface ProfilesState {
  profiles: Profile[];
  activeProfile: Profile | null;
  isLoading: boolean;
  error: string | null;
  createProfile: (data: ProfileCreate) => Promise<Profile>;
  updateProfile: (id: number, data: ProfileUpdate) => Promise<Profile>;
  activateProfile: (id: number) => Promise<Profile>;
  deleteProfile: (id: number) => Promise<void>;
}

interface TerminalWindowProps {
  context: BettingContext;
  onRefresh: () => void;
  isContextLoading: boolean;
  profilesState: ProfilesState;
}

export function TerminalWindow({
  context,
  onRefresh,
  isContextLoading,
  profilesState,
}: TerminalWindowProps) {
  const { messages, isLoading, sendMessage, stopGeneration, clearMessages } =
    useChat(context);
  const { exposure } = useBankroll(30000);
  const { runExtraction } = useExtraction();
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

  // Command handlers that send messages to LLM
  const handleRunExtraction = useCallback(async (providers?: string) => {
    const providerList = providers || 'unibet,leovegas,casumo';

    // Send initial message to LLM
    sendMessage(`Starting extraction for providers: ${providerList}`);

    try {
      // Start extraction (this triggers the background job)
      await runExtraction(providerList, 'football', 5);

      // Show immediate feedback
      setTimeout(() => {
        sendMessage(`Extraction started successfully. Monitoring progress...`);
      }, 500);

      // Poll for progress updates
      let attempts = 0;
      const maxAttempts = 60; // 60 seconds max
      let lastEventCount = 0;
      let lastOddsCount = 0;

      const pollInterval = setInterval(async () => {
        attempts++;
        try {
          const extractionStatus = await api.getExtractionStatus();

          // Show progress updates when counts change
          if (extractionStatus.events !== lastEventCount || extractionStatus.odds !== lastOddsCount) {
            lastEventCount = extractionStatus.events;
            lastOddsCount = extractionStatus.odds;

            if (extractionStatus.running) {
              sendMessage(`Extraction in progress... Found ${extractionStatus.events} events, ${extractionStatus.odds} odds so far.`);
            }
          }

          if (!extractionStatus.running) {
            clearInterval(pollInterval);
            sendMessage(`Extraction completed successfully!\n\n**Results:**\n- Events extracted: ${extractionStatus.events}\n- Odds collected: ${extractionStatus.odds}\n- Last run: ${extractionStatus.last_run || 'just now'}\n\nYou can now use /opportunities to see arbitrage and value bets.`);
          } else if (attempts >= maxAttempts) {
            clearInterval(pollInterval);
            sendMessage(`Extraction is taking longer than expected. It may still be running in the background. Check /providers for status.`);
          }
        } catch (err) {
          console.error('Error polling extraction status:', err);
        }
      }, 2000); // Poll every 2 seconds
    } catch (err) {
      sendMessage(`Failed to start extraction: ${err instanceof Error ? err.message : 'Unknown error'}. Make sure the backend is running.`);
    }
  }, [runExtraction, sendMessage]);

  const handleShowProviders = useCallback(() => {
    const providerList = context.providers.map((p) => `- **${p.id}**: ${p.name} (${p.is_enabled ? 'enabled' : 'disabled'})`).join('\n');
    sendMessage(`Here are the current providers:\n\n${providerList}\n\nYou can run extraction with /extractall or /extract [providers]`);
  }, [context.providers, sendMessage]);

  const handleShowHealth = useCallback(async () => {
    try {
      const response = await fetch('/health');
      const health = await response.json();
      sendMessage(`System health check:\n\n**Status:** ${health.status}\n**Time:** ${health.time}\n\nAll systems operational!`);
    } catch (err) {
      sendMessage(`Failed to check system health. The server may be down.`);
    }
  }, [sendMessage]);

  // Create command registry
  const commandRegistry = useMemo(
    () =>
      createCommandRegistry({
        onShowOpportunities: () => setShowOpportunities(true),
        onShowBets: () => setShowBets(true),
        onShowBalanceBreakdown: () => setShowBalanceBreakdown(true),
        onRefresh,
        onClear: clearMessages,
        onRunExtraction: handleRunExtraction,
        onShowProviders: handleShowProviders,
        onShowHealth: handleShowHealth,
      }),
    [onRefresh, clearMessages, handleRunExtraction, handleShowProviders, handleShowHealth]
  );

  const commands = useMemo(() => Object.values(commandRegistry), [commandRegistry]);

  // Handle command execution with LLM context
  const handleCommand = useCallback(
    (command: string, args: string) => {
      const cmd = commandRegistry[command];
      if (!cmd) {
        sendMessage(`Unknown command: /${command}. Type /help for available commands.`);
        return;
      }

      // Special handling for /help and /commands
      if (command === 'help' || command === 'commands') {
        const helpText = formatCommandHelp(commandRegistry);
        sendMessage(helpText);
        return;
      }

      // Special handling for /extract with args
      if (command === 'extract' && args) {
        handleRunExtraction(args);
        return;
      }

      // UI commands that don't need LLM messages
      const silentCommands = ['opportunities', 'arb', 'value', 'bets', 'bankroll', 'balance'];

      // Execute the command
      try {
        cmd.execute();

        // Send feedback message for non-silent commands
        if (!silentCommands.includes(command)) {
          const messages: Record<string, string> = {
            clear: 'Chat history cleared.',
            refresh: 'Refreshing all data...',
            extractall: 'Starting extraction on all providers...',
          };
          const message = messages[command];
          if (message) {
            sendMessage(message);
          }
        }
      } catch (err) {
        sendMessage(`Error executing /${command}: ${err instanceof Error ? err.message : 'Unknown error'}`);
      }
    },
    [commandRegistry, sendMessage, handleRunExtraction]
  );

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
        profilesState={profilesState}
      />

      {/* Messages area - centered */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <WelcomeMessage
              context={context}
              exposure={exposure}
              activeProfile={profilesState.activeProfile}
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
          onCommand={handleCommand}
          commands={commands}
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
