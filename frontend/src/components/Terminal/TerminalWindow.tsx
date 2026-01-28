import { useEffect, useRef, useMemo, useCallback, useState } from 'react';
import type { BettingContext, Profile, ProfileCreate, ProfileUpdate, Message } from '@/types';
import { useChat } from '@/hooks/useChat';
import { useBankroll } from '@/hooks/useBankroll';
import { useExtraction } from '@/hooks/useExtraction';
import { TerminalHeader } from './TerminalHeader';
import { TerminalInput } from './TerminalInput';
import { ChatMessage } from './ChatMessage';
import { WelcomeMessage } from './WelcomeMessage';
import { ExtractionProgressMessage } from './ExtractionProgressMessage';
import { createCommandRegistry, formatCommandHelp } from '@/utils/commands';
import {
  formatBankrollTable,
  formatOpportunitiesList,
  formatBetsTable,
  formatStatsReport,
} from '@/utils/formatters';
import {
  parseOpportunityFilters,
  parseBetFilters,
  parseSettleBetArgs,
  parsePlaceBetArgs,
  parseProfileCommand,
} from '@/utils/commandParsers';
import { api } from '@/services/api';
import type { OpportunityWithEvent } from '@/types';

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
  const { messages, setMessages, isLoading, sendMessage, stopGeneration, clearMessages } =
    useChat(context);
  const { exposure } = useBankroll(30000);
  const { runExtraction } = useExtraction();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Track last displayed opportunities for bet placement
  const [lastOpportunities, setLastOpportunities] = useState<OpportunityWithEvent[]>([]);

  // Track extraction state
  const [_isExtracting, setIsExtracting] = useState(false);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Command handlers that send messages to LLM
  const handleRunExtraction = useCallback(async (providers?: string) => {
    // Default to all providers if none specified
    const providerList = providers || context.providers.map(p => p.id).join(',');

    try {
      // Create progress message
      const generateId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      const progressMessage: Message = {
        id: generateId(),
        role: 'assistant',
        content: '', // Will be filled by ExtractionProgressMessage component
        timestamp: new Date(),
        isStreaming: true,
        isExtraction: true,
      };

      // Add progress message to chat
      setMessages((prev: Message[]) => [...prev, progressMessage]);

      // Start extraction (this triggers the background job)
      // Extract ALL sports from sports.json
      await runExtraction(providerList);

      // Mark extraction as running
      setIsExtracting(true);

    } catch (err) {
      sendMessage(`**[!] Failed to start extraction**\n\n` +
        `Error: ${err instanceof Error ? err.message : 'Unknown error'}\n\n` +
        `Make sure the backend is running on port 8000.`);
    }
  }, [runExtraction, sendMessage, context.providers, setMessages]);

  const handleExtractionComplete = useCallback(async () => {
    setIsExtracting(false);

    try {
      const status = await api.getExtractionStatus();

      // Send ONLY final message (1 message)
      if (status.events === 0 && status.odds === 0) {
        sendMessage(
          `**[!] EXTRACTION COMPLETE - No new data**\n\n` +
          `**Possible Reasons:**\n` +
          `- No live events currently available\n` +
          `- Provider APIs may be down or in maintenance\n` +
          `- Geographical restrictions\n` +
          `- Off-season for major sports\n\n` +
          `**Existing Data:**\n` +
          `- ${context.events.length} events in database\n` +
          `- ${context.opportunities.length} opportunities detected\n\n` +
          `Try again in 15-30 minutes or use existing data with \`/opportunities\``
        );
      } else {
        sendMessage(
          `**[+] EXTRACTION COMPLETE!**\n\n` +
          `**Results:**\n` +
          `- Events: ${status.events}\n` +
          `- Odds: ${status.odds}\n` +
          `- Completed: ${status.last_run || 'just now'}\n\n` +
          `**Next Steps:**\n` +
          `- \`/opportunities\` - View all opportunities\n` +
          `- \`/arb\` - Arbitrage opportunities only\n` +
          `- \`/value\` - Value bets only\n` +
          `- \`/stats\` - Updated statistics`
        );
      }

      onRefresh();
    } catch (err) {
      sendMessage(`Error checking extraction status: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage, onRefresh, context.events.length, context.opportunities.length]);

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

  // New command handlers that display formatted output in chat
  const handleShowBankroll = useCallback(async () => {
    try {
      const exposure = await api.getBankrollExposure();
      const formatted = formatBankrollTable(exposure);
      sendMessage(formatted);
    } catch (err) {
      sendMessage(
        `Error loading bankroll: ${err instanceof Error ? err.message : 'Unknown error'}`
      );
    }
  }, [sendMessage]);

  const handleShowOpportunities = useCallback(
    async (args: string) => {
      try {
        const filters = parseOpportunityFilters(args);
        const result = await api.getOpportunities(
          filters.type,
          true,
          undefined,
          undefined,
          undefined,
          filters.market,
          filters.sport,
          filters.minValue
        );

        // Fetch event details for each opportunity
        const opportunitiesWithEvents: OpportunityWithEvent[] = await Promise.all(
          result.opportunities.map(async (opp) => {
            try {
              const event = await api.getEvent(opp.event_id);
              return { ...opp, event };
            } catch {
              return opp;
            }
          })
        );

        // Store for bet placement
        setLastOpportunities(opportunitiesWithEvents);

        const formatted = formatOpportunitiesList(opportunitiesWithEvents, result.count);
        sendMessage(formatted);
      } catch (err) {
        sendMessage(
          `Error loading opportunities: ${err instanceof Error ? err.message : 'Unknown error'}`
        );
      }
    },
    [sendMessage]
  );

  const handleShowBets = useCallback(
    async (args: string) => {
      try {
        const filters = parseBetFilters(args);
        const result = await api.getBets(filters.status, filters.limit);
        const formatted = formatBetsTable(result.bets, filters.status);
        sendMessage(formatted);
      } catch (err) {
        sendMessage(
          `Error loading bets: ${err instanceof Error ? err.message : 'Unknown error'}`
        );
      }
    },
    [sendMessage]
  );

  const handleShowStats = useCallback(async () => {
    try {
      const stats = await api.getBankrollStats();
      const formatted = formatStatsReport(stats);
      sendMessage(formatted);
    } catch (err) {
      sendMessage(
        `Error loading stats: ${err instanceof Error ? err.message : 'Unknown error'}`
      );
    }
  }, [sendMessage]);

  const handleSettleBet = useCallback(
    async (args: string) => {
      const parsed = parseSettleBetArgs(args);
      if (!parsed) {
        sendMessage(
          'Invalid syntax. Usage: /settle-bet <id> won/lost/void\nExample: /settle-bet 123 won'
        );
        return;
      }

      try {
        // Fetch bet details first
        const betsResult = await api.getBets(undefined, 100);
        const bet = betsResult.bets.find((b) => b.id === parsed.betId);

        if (!bet) {
          sendMessage(`Bet #${parsed.betId} not found.`);
          return;
        }

        if (bet.result !== 'pending') {
          sendMessage(`Bet #${parsed.betId} is already settled as ${bet.result}.`);
          return;
        }

        // Calculate payout
        const payout =
          parsed.result === 'won'
            ? bet.stake * bet.odds
            : parsed.result === 'void'
            ? bet.stake
            : 0;

        // Settle the bet
        const result = await api.settleBet(parsed.betId, {
          result: parsed.result,
          payout,
        });

        sendMessage(
          `Bet #${parsed.betId} settled as ${parsed.result}.\nProfit: $${result.profit.toFixed(2)}\n\nRun /bankroll to see updated balance.`
        );
      } catch (err) {
        sendMessage(
          `Error settling bet: ${err instanceof Error ? err.message : 'Unknown error'}`
        );
      }
    },
    [sendMessage]
  );

  const handlePlaceBet = useCallback(
    async (args: string) => {
      const parsed = parsePlaceBetArgs(args);
      if (!parsed || !parsed.opportunityNumber || !parsed.stake) {
        sendMessage(
          'Invalid syntax. Usage: /place-bet <opportunity#> <stake> [provider]\nExample: /place-bet 3 100\nExample: /place-bet 5 50 unibet'
        );
        return;
      }

      // Check if opportunities were loaded
      if (lastOpportunities.length === 0) {
        sendMessage(
          'No opportunities loaded. Please run /opportunities first to see available bets.'
        );
        return;
      }

      // Find the opportunity by display number
      const oppIndex = parsed.opportunityNumber - 1;
      if (oppIndex < 0 || oppIndex >= lastOpportunities.length) {
        sendMessage(
          `Opportunity #${parsed.opportunityNumber} not found. Only ${lastOpportunities.length} opportunities are loaded. Run /opportunities to refresh.`
        );
        return;
      }

      const opportunity = lastOpportunities[oppIndex];

      // Determine which provider to use
      let providerId = parsed.provider || opportunity.provider1;
      let odds = opportunity.odds1;
      let outcome = opportunity.outcome1;

      // If user specified provider2 or different provider
      if (parsed.provider && parsed.provider.toLowerCase() === opportunity.provider2?.toLowerCase()) {
        providerId = opportunity.provider2;
        odds = opportunity.odds2 || 0;
        outcome = opportunity.outcome2 || '';
      }

      // Check provider balance
      const providerExposure = exposure.providers.find(
        (p) => p.provider_id.toLowerCase() === providerId.toLowerCase()
      );

      if (!providerExposure) {
        sendMessage(`Provider "${providerId}" not found in your bankroll.`);
        return;
      }

      if (!parsed.isBonus && parsed.stake > providerExposure.available) {
        sendMessage(
          `Insufficient balance on ${providerId}.\nAvailable: $${providerExposure.available.toFixed(2)}\nRequested: $${parsed.stake.toFixed(2)}`
        );
        return;
      }

      const eventName = opportunity.event
        ? `${opportunity.event.home_team} vs ${opportunity.event.away_team}`
        : opportunity.event_id;

      try {
        // Create the bet
        await api.createBet({
          event_id: opportunity.event_id,
          provider_id: providerId,
          market: opportunity.market,
          outcome,
          odds,
          stake: parsed.stake,
          is_bonus: parsed.isBonus || false,
        });

        const potentialReturn = parsed.stake * odds;
        const potentialProfit = potentialReturn - parsed.stake;
        const typeLabel = opportunity.type === 'arbitrage' ? 'ARB' : opportunity.type === 'value' ? 'VALUE' : 'BONUS';
        const value = opportunity.type === 'arbitrage' ? opportunity.profit_pct : opportunity.edge_pct;

        sendMessage(
          `[+] Bet placed successfully!\n\n**Opportunity #${parsed.opportunityNumber}** (${typeLabel} - ${value?.toFixed(2)}%)\n${eventName}\n\n**Bet Details:**\n- Provider: ${providerId}\n- Market: ${opportunity.market}\n- Outcome: ${outcome}\n- Odds: ${odds.toFixed(2)}\n- Stake: $${parsed.stake.toFixed(2)}${parsed.isBonus ? ' (BONUS)' : ''}\n- Potential Return: $${potentialReturn.toFixed(2)}\n- Potential Profit: $${potentialProfit.toFixed(2)}\n\n**IMPORTANT:** Go to ${providerId} and manually place this bet now.\n\nRun /bets to see your pending bets, or /bankroll to see updated balance.`
        );

        // Refresh data after bet placement
        onRefresh();
      } catch (err) {
        sendMessage(
          `Error placing bet: ${err instanceof Error ? err.message : 'Unknown error'}`
        );
      }
    },
    [sendMessage, lastOpportunities, exposure, onRefresh]
  );

  const handleProfileCommand = useCallback(
    async (args: string) => {
      const parsed = parseProfileCommand(args);

      try {
        switch (parsed.action) {
          case 'list': {
            if (profilesState.profiles.length === 0) {
              sendMessage('No profiles found. Use /profile create <name> to create a profile.');
              return;
            }

            const profileList = profilesState.profiles
              .map((p) => {
                const active = p.is_active ? '[*]' : '[ ]';
                const name = p.name.padEnd(15);
                const bankroll = `$${p.bankroll.toFixed(0)}`.padStart(8);
                const kelly = p.kelly_fraction.toFixed(2).padStart(4);
                const minEdge = `${p.min_edge_pct.toFixed(1)}%`.padStart(5);
                return `${active} ${name} | Bankroll: ${bankroll} | Kelly: ${kelly} | Min Edge: ${minEdge}`;
              })
              .join('\n');

            const activeProfile = profilesState.activeProfile;
            sendMessage(
              `**PROFILES:**\n\n\`\`\`\n${profileList}\n\`\`\`\n\n**Active:** ${activeProfile?.name || 'None'}\n\nCommands:\n- /profile switch <name> - Switch to a profile\n- /profile create <name> - Create new profile\n- /profile delete <name> - Delete profile`
            );
            break;
          }

          case 'switch': {
            if (!parsed.name) {
              sendMessage('Usage: /profile switch <name>\nExample: /profile switch aggressive');
              return;
            }

            const profile = profilesState.profiles.find(
              (p) => p.name.toLowerCase() === parsed.name!.toLowerCase()
            );

            if (!profile) {
              sendMessage(
                `Profile "${parsed.name}" not found.\n\nRun /profile list to see available profiles.`
              );
              return;
            }

            if (profile.is_active) {
              sendMessage(`Profile "${parsed.name}" is already active.`);
              return;
            }

            await profilesState.activateProfile(profile.id);
            sendMessage(
              `[+] Switched to profile: **${parsed.name}**\n\n- Bankroll: $${profile.bankroll.toFixed(2)}\n- Kelly Fraction: ${profile.kelly_fraction}\n- Min Edge: ${profile.min_edge_pct}%\n\nData refreshing...`
            );
            onRefresh();
            break;
          }

          case 'create': {
            if (!parsed.name) {
              sendMessage('Usage: /profile create <name>\nExample: /profile create aggressive');
              return;
            }

            // Check if profile already exists
            const existing = profilesState.profiles.find(
              (p) => p.name.toLowerCase() === parsed.name!.toLowerCase()
            );

            if (existing) {
              sendMessage(`Profile "${parsed.name}" already exists. Use /profile switch ${parsed.name} to activate it.`);
              return;
            }

            const newProfile = await profilesState.createProfile({
              name: parsed.name,
            });

            sendMessage(
              `[+] Profile "${parsed.name}" created successfully!\n\n**Default Settings:**\n- Bankroll: $${newProfile.bankroll.toFixed(2)}\n- Kelly Fraction: ${newProfile.kelly_fraction}\n- Min Edge: ${newProfile.min_edge_pct}%\n\nUse /profile switch ${parsed.name} to activate it.`
            );
            break;
          }

          case 'delete': {
            if (!parsed.name) {
              sendMessage('Usage: /profile delete <name>\nExample: /profile delete test');
              return;
            }

            const profile = profilesState.profiles.find(
              (p) => p.name.toLowerCase() === parsed.name!.toLowerCase()
            );

            if (!profile) {
              sendMessage(`Profile "${parsed.name}" not found.`);
              return;
            }

            if (profile.is_active) {
              sendMessage(
                `Cannot delete active profile "${parsed.name}".\n\nSwitch to another profile first with /profile switch <name>`
              );
              return;
            }

            await profilesState.deleteProfile(profile.id);
            sendMessage(`[+] Profile "${parsed.name}" deleted successfully.`);
            break;
          }

          case 'set': {
            if (!parsed.setting || !parsed.value) {
              sendMessage(
                'Usage: /profile set <setting> <value>\n\nAvailable settings:\n- kelly_fraction - Kelly fraction (0.0-1.0)\n- min_edge_pct - Minimum edge percentage\n- bankroll - Total bankroll amount\n\nExample: /profile set kelly_fraction 0.25'
              );
              return;
            }

            if (!profilesState.activeProfile) {
              sendMessage('No active profile. Use /profile switch <name> first.');
              return;
            }

            const validSettings = ['kelly_fraction', 'min_edge_pct', 'bankroll'];
            if (!validSettings.includes(parsed.setting)) {
              sendMessage(
                `Invalid setting "${parsed.setting}".\n\nValid settings: ${validSettings.join(', ')}`
              );
              return;
            }

            const numValue = parseFloat(parsed.value);
            if (isNaN(numValue)) {
              sendMessage(`Invalid value "${parsed.value}". Must be a number.`);
              return;
            }

            const updateData: Partial<Profile> = {
              [parsed.setting]: numValue,
            };

            await profilesState.updateProfile(profilesState.activeProfile.id, updateData);
            sendMessage(
              `[+] Profile setting updated: ${parsed.setting} = ${numValue}\n\nRun /profile list to see updated values.`
            );
            onRefresh();
            break;
          }
        }
      } catch (err) {
        sendMessage(
          `Error: ${err instanceof Error ? err.message : 'Unknown error'}\n\nRun /profile list to see available profiles.`
        );
      }
    },
    [profilesState, sendMessage, onRefresh]
  );

  // Create command registry
  const commandRegistry = useMemo(
    () =>
      createCommandRegistry({
        onShowOpportunities: handleShowOpportunities,
        onShowBets: handleShowBets,
        onShowBalanceBreakdown: handleShowBankroll,
        onShowStats: handleShowStats,
        onRefresh,
        onClear: clearMessages,
        onRunExtraction: handleRunExtraction,
        onShowProviders: handleShowProviders,
        onShowHealth: handleShowHealth,
        onSettleBet: handleSettleBet,
        onPlaceBet: handlePlaceBet,
        onProfileCommand: handleProfileCommand,
      }),
    [
      handleShowOpportunities,
      handleShowBets,
      handleShowBankroll,
      handleShowStats,
      onRefresh,
      clearMessages,
      handleRunExtraction,
      handleShowProviders,
      handleShowHealth,
      handleSettleBet,
      handlePlaceBet,
      handleProfileCommand,
    ]
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

      // Special handling for /extract with optional args
      if (command === 'extract') {
        handleRunExtraction(args || undefined);
        return;
      }

      // Special handling for commands that accept arguments
      if (command === 'opportunities' || command === 'arb' || command === 'value') {
        handleShowOpportunities(args || (command === 'arb' ? '--type arb' : command === 'value' ? '--type value' : ''));
        return;
      }

      if (command === 'bets') {
        handleShowBets(args);
        return;
      }

      if (command === 'settle-bet') {
        if (!args) {
          sendMessage('Usage: /settle-bet <id> won/lost/void\nExample: /settle-bet 123 won');
          return;
        }
        handleSettleBet(args);
        return;
      }

      if (command === 'place-bet') {
        if (!args) {
          sendMessage('Usage: /place-bet <opportunity#> <stake> [provider]\nExample: /place-bet 3 100');
          return;
        }
        handlePlaceBet(args);
        return;
      }

      if (command === 'profile') {
        handleProfileCommand(args);
        return;
      }

      // UI commands that don't need LLM messages
      const silentCommands = ['bankroll', 'balance', 'stats'];

      // Execute the command
      try {
        cmd.execute();

        // Send feedback message for non-silent commands
        if (!silentCommands.includes(command)) {
          const messages: Record<string, string> = {
            clear: 'Chat history cleared.',
            refresh: 'Refreshing all data...',
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
    [commandRegistry, sendMessage, handleRunExtraction, handleShowOpportunities, handleShowBets, handleSettleBet]
  );

  // Keyboard shortcuts - now execute slash commands instead of opening modals
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+O: Run /opportunities command
      if (e.ctrlKey && e.key === 'o' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        handleCommand('opportunities', '');
      }
      // Ctrl+B: Run /bets command
      if (e.ctrlKey && e.key === 'b' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        handleCommand('bets', '');
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
  }, [clearMessages, onRefresh, handleCommand]);

  return (
    <div className="flex flex-col h-full bg-terminal-bg">
      {/* Header */}
      <TerminalHeader
        context={context}
        exposure={exposure}
        isLoading={isContextLoading}
        activeProfile={profilesState.activeProfile}
      />

      {/* Messages area - centered with dotted grid */}
      <div className="flex-1 overflow-y-auto dotted-grid">
        <div className="max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <WelcomeMessage
              context={context}
              exposure={exposure}
              activeProfile={profilesState.activeProfile}
            />
          ) : (
            <div className="pb-4">
              {messages.map((message) =>
                message.isExtraction ? (
                  <div key={message.id} className="border-b border-terminal-border/30 py-3 px-4">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="font-bold text-terminal-accent">[*]</span>
                      <span className="text-xs font-medium uppercase tracking-wide text-terminal-accent">
                        oddopp
                      </span>
                    </div>
                    <div className="pl-6">
                      <ExtractionProgressMessage onComplete={handleExtractionComplete} />
                    </div>
                  </div>
                ) : (
                  <ChatMessage key={message.id} message={message} />
                )
              )}
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
    </div>
  );
}
