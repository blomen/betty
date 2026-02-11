/**
 * useDropdownWorkflow - Manages value/bets workflow state
 *
 * Handles multi-step workflows for opportunity selection and bet settlement.
 * Stakes are auto-calculated by the backend using risk management settings.
 */
import { useState, useCallback } from 'react';
import type {
  DropdownWorkflowState,
  DropdownOption,
  OpportunityWithEvent,
  BankrollExposure,
  Bet,
  EventWithBets,
} from '@/types';
import { api } from '@/services/api';
import { formatOpportunitiesList, formatBetsTable, formatProviderName, joinLines, outcomeToTeam } from '@/utils/formatters';

interface UseDropdownWorkflowProps {
  exposure: BankrollExposure;
  sendMessage: (msg: string) => void;
  onRefresh: () => void;
}

export function useDropdownWorkflow({
  // exposure is available for future use if needed
  sendMessage,
  onRefresh,
}: UseDropdownWorkflowProps) {
  const [workflow, setWorkflow] = useState<DropdownWorkflowState>({ type: 'idle', step: 'idle' });
  const [options, setOptions] = useState<DropdownOption[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [lastOpportunities, setLastOpportunities] = useState<OpportunityWithEvent[]>([]);
  const [lastBets, setLastBets] = useState<Bet[]>([]);

  // Cancel workflow
  const cancel = useCallback(() => {
    setWorkflow({ type: 'idle', step: 'idle' });
    setOptions([]);
    setSelectedIndex(0);
  }, []);

  // Back button option (added to all option arrays)
  const backOption: DropdownOption = {
    id: 'back',
    label: '← Back',
    sublabel: 'Previous step',
    type: 'action' as const,
  };

  // Helper to add back button to options
  const withBack = (opts: DropdownOption[]): DropdownOption[] => [...opts, backOption];

  // Place value bet (record in database) - uses pre-calculated stake from API
  const placeValueBet = useCallback(async () => {
    const oppIndex = (workflow.selectedOpp || 1) - 1;
    const opp = workflow.opportunities?.[oppIndex];
    if (!opp) return;

    // Use final_stake from API response
    const stake = opp.final_stake;
    if (!stake || stake <= 0) {
      sendMessage('No stake calculated for this opportunity. Check profile settings.');
      cancel();
      return;
    }

    const homeTeam = opp.home_team || opp.event?.home_team;
    const awayTeam = opp.away_team || opp.event?.away_team;
    const betOn = outcomeToTeam(opp.outcome1, homeTeam, awayTeam);

    try {
      const result = await api.createBet({
        event_id: opp.event_id,
        provider_id: opp.provider1,
        market: opp.market,
        outcome: opp.outcome1,
        odds: opp.odds1,
        stake,
        is_bonus: false,
      });

      const potentialReturn = stake * opp.odds1;
      const potentialProfit = potentialReturn - stake;

      sendMessage(
        `**BET #${result.bet_id} RECORDED**\n` +
        '```\n' +
        `${betOn} | ${opp.provider1} | ${opp.odds1.toFixed(2)} | $${stake.toFixed(0)} | +$${potentialProfit.toFixed(0)}\n` +
        '```\n' +
        `Use \`/bets\` to settle when result is in.`
      );

      onRefresh();
      cancel();
    } catch (err) {
      sendMessage(`Error recording bet: ${err instanceof Error ? err.message : 'Unknown'}`);
      cancel();
    }
  }, [workflow, sendMessage, onRefresh, cancel]);

  // Start value workflow
  const startValue = useCallback(async () => {
    sendMessage('**VALUE BET SCANNER** - Loading opportunities...');

    try {
      const result = await api.getOpportunities('value', true);

      if (result.opportunities.length === 0) {
        sendMessage('**No value bets found.**\n\nRun `/extract pinnacle <provider>` to refresh.');
        return;
      }

      const opportunitiesWithEvents: OpportunityWithEvent[] = await Promise.all(
        result.opportunities.slice(0, 20).map(async (opp) => {
          try {
            const event = await api.getEvent(opp.event_id);
            return { ...opp, event };
          } catch {
            return opp;
          }
        })
      );

      setLastOpportunities(opportunitiesWithEvents);
      sendMessage(formatOpportunitiesList(opportunitiesWithEvents, result.count));

      const opts: DropdownOption[] = opportunitiesWithEvents
        .filter(opp => !opp.skip_reason)  // Filter out skipped opportunities
        .map((opp, idx) => {
          const stake = opp.final_stake || 0;
          return {
            id: idx + 1,
            label: `[${idx + 1}] +${opp.edge_pct?.toFixed(1)}% ${opp.event?.home_team || opp.home_team || 'Unknown'} vs ${opp.event?.away_team || opp.away_team || ''}`,
            sublabel: `${opp.provider1} @ ${opp.odds1.toFixed(2)} | $${stake.toFixed(0)}`,
            type: 'opportunity' as const,
          };
        });

      setOptions(withBack(opts));
      setSelectedIndex(0);
      setWorkflow({ type: 'value', step: 'select-opportunity', opportunities: opportunitiesWithEvents });
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage]);

  // Start bets workflow - group pending bets by event
  const startBets = useCallback(async () => {
    try {
      const result = await api.getBets(undefined, 100);

      if (result.bets.length === 0) {
        sendMessage('No bets found.');
        return;
      }

      setLastBets(result.bets);

      // Filter pending bets and group by event_id
      const pendingBets = result.bets.filter((b) => b.result === 'pending');

      if (pendingBets.length === 0) {
        sendMessage(formatBetsTable(result.bets));
        sendMessage('No pending bets to settle.');
        return;
      }

      // Group pending bets by event_id
      const eventMap = new Map<string, Bet[]>();
      for (const bet of pendingBets) {
        const eventId = bet.event_id || 'unknown';
        const existing = eventMap.get(eventId) || [];
        existing.push(bet);
        eventMap.set(eventId, existing);
      }

      // Fetch event details for each event
      const eventsWithBets: EventWithBets[] = [];
      for (const [eventId, bets] of eventMap) {
        let homeTeam = 'Unknown';
        let awayTeam = 'Unknown';
        let sport = '';

        if (eventId !== 'unknown') {
          try {
            const event = await api.getEvent(eventId);
            homeTeam = event.home_team;
            awayTeam = event.away_team;
            sport = event.sport;
          } catch {
            // Use bet info if event not found
            homeTeam = bets[0]?.outcome || 'Unknown';
          }
        }

        const totalStake = bets.reduce((sum, b) => sum + b.stake, 0);
        eventsWithBets.push({
          event_id: eventId,
          home_team: homeTeam,
          away_team: awayTeam,
          sport,
          bets,
          total_stake: totalStake,
        });
      }

      // Show summary
      const summary = joinLines(eventsWithBets.map((e, idx) =>
        `[${idx + 1}] ${e.home_team} vs ${e.away_team} - ${e.bets.length} bet${e.bets.length > 1 ? 's' : ''} ($${e.total_stake.toFixed(0)})`
      ));

      sendMessage(`**PENDING BETS** (${pendingBets.length} bets on ${eventsWithBets.length} events)\n\n${summary}\n\nSelect event to enter result:`);

      const opts: DropdownOption[] = eventsWithBets.map((e, idx) => ({
        id: e.event_id,
        label: `[${idx + 1}] ${e.home_team} vs ${e.away_team}`,
        sublabel: `${e.bets.length} bet${e.bets.length > 1 ? 's' : ''} - $${e.total_stake.toFixed(0)}`,
        type: 'opportunity' as const,
      }));

      setOptions(withBack(opts));
      setSelectedIndex(0);
      setWorkflow({ type: 'bets', step: 'select-event', bets: pendingBets, eventsWithBets });
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage]);

  // Handle selection
  const select = useCallback(async (option: DropdownOption) => {
    if (option.id === 'cancel') {
      cancel();
      return;
    }

    // Handle back navigation
    if (option.id === 'back') {
      switch (workflow.step) {
        case 'confirm':
          // Go back to opportunity selection
          if (workflow.type === 'value' && workflow.opportunities) {
            const opts: DropdownOption[] = workflow.opportunities
              .filter(opp => !opp.skip_reason)
              .map((opp, idx) => {
                const stake = opp.final_stake || 0;
                return {
                  id: idx + 1,
                  label: `[${idx + 1}] +${opp.edge_pct?.toFixed(1)}% ${opp.event?.home_team || opp.home_team || 'Unknown'} vs ${opp.event?.away_team || opp.away_team || ''}`,
                  sublabel: `${opp.provider1} @ ${opp.odds1.toFixed(2)} | $${stake.toFixed(0)}`,
                  type: 'opportunity' as const,
                };
              });
            setOptions(withBack(opts));
            setSelectedIndex(0);
            setWorkflow((prev) => ({ ...prev, step: 'select-opportunity', selectedOpp: undefined }));
          }
          return;
        case 'select-event-outcome':
          // Go back to event selection
          if (workflow.eventsWithBets) {
            const opts: DropdownOption[] = workflow.eventsWithBets.map((e) => ({
              id: e.event_id,
              label: `${e.home_team} vs ${e.away_team}`,
              sublabel: `${e.bets.length} bet${e.bets.length > 1 ? 's' : ''} - $${e.total_stake.toFixed(0)}`,
              type: 'action' as const,
            }));
            setOptions(withBack(opts));
            setSelectedIndex(0);
            setWorkflow((prev) => ({ ...prev, step: 'select-event', selectedEventId: undefined }));
          }
          return;
        default:
          // For first-level steps (select-opportunity, select-event, select-provider), cancel workflow
          cancel();
          return;
      }
    }

    switch (workflow.type) {
      case 'value': {
        if (workflow.step === 'select-opportunity') {
          const oppIndex = (option.id as number) - 1;
          const opp = workflow.opportunities?.[oppIndex];
          if (!opp) return;

          const homeTeam = opp.home_team || opp.event?.home_team;
          const awayTeam = opp.away_team || opp.event?.away_team;
          const betOn = outcomeToTeam(opp.outcome1, homeTeam, awayTeam);
          const stake = opp.final_stake || 0;
          const potentialReturn = stake * opp.odds1;
          const potentialProfit = potentialReturn - stake;

          sendMessage(
            `**#${option.id}** +${opp.edge_pct?.toFixed(1)}% edge\n` +
            '```\n' +
            `Bet on: ${betOn} | ${opp.provider1} @ ${opp.odds1.toFixed(2)}\n` +
            `Stake: $${stake.toFixed(0)} | Return: $${potentialReturn.toFixed(0)} (+$${potentialProfit.toFixed(0)})\n` +
            '```\n' +
            `Place bet manually, then confirm to record.`
          );

          // Show confirm options directly (no stake selection needed)
          const confirmOpts: DropdownOption[] = [
            { id: 'confirm', label: '[CONFIRM]', sublabel: 'Record bet', type: 'action' as const },
          ];

          setOptions(withBack(confirmOpts));
          setSelectedIndex(0);
          setWorkflow((prev) => ({
            ...prev,
            step: 'confirm',
            selectedOpp: option.id as number,
            selectedProvider: opp.provider1,
          }));
        } else if (workflow.step === 'confirm') {
          if (option.id === 'confirm') {
            await placeValueBet();
          }
        }
        break;
      }

      case 'bets': {
        if (workflow.step === 'select-event') {
          const eventId = option.id as string;
          const eventWithBets = workflow.eventsWithBets?.find((e) => e.event_id === eventId);
          if (!eventWithBets) return;

          const eventName = `${eventWithBets.home_team} vs ${eventWithBets.away_team}`;

          // Show bet details
          const betDetails = joinLines(eventWithBets.bets.map((b) =>
            `  #${b.id} ${formatProviderName(b.provider)} @ ${b.odds.toFixed(2)} - $${b.stake.toFixed(0)}`
          ));

          sendMessage(`**${eventName}**\n${betDetails}\n\nWho won the match?`);

          // Always show all outcome options so user can settle bets correctly
          // If home wins, bets on away/draw lose. If away wins, bets on home/draw lose.
          const outcomeOpts: DropdownOption[] = [
            {
              id: 'home',
              label: `${eventWithBets.home_team} won`,
              sublabel: 'Home win',
              type: 'action' as const,
            },
            {
              id: 'away',
              label: `${eventWithBets.away_team} won`,
              sublabel: 'Away win',
              type: 'action' as const,
            },
            {
              id: 'draw',
              label: 'Draw',
              sublabel: 'Match drawn',
              type: 'action' as const,
            },
            {
              id: 'void',
              label: 'Void event',
              sublabel: 'Refund all bets',
              type: 'action' as const,
            },
          ];

          setOptions(withBack(outcomeOpts));
          setSelectedIndex(0);
          setWorkflow((prev) => ({ ...prev, step: 'select-event-outcome', selectedEventId: eventId }));
        } else if (workflow.step === 'select-event-outcome') {
          const outcome = option.id as 'home' | 'away' | 'draw' | 'void';
          const eventId = workflow.selectedEventId;
          const eventWithBets = workflow.eventsWithBets?.find((e) => e.event_id === eventId);
          if (!eventWithBets) return;

          const eventName = `${eventWithBets.home_team} vs ${eventWithBets.away_team}`;

          try {
            let totalProfit = 0;
            const results: string[] = [];

            for (const bet of eventWithBets.bets) {
              const betOutcome = bet.outcome?.toLowerCase();
              let betResult: 'won' | 'lost' | 'void';
              let payout: number;

              if (outcome === 'void') {
                betResult = 'void';
                payout = bet.stake;
              } else {
                // Determine if bet won based on outcome
                const betWon =
                  (outcome === 'home' && (betOutcome === 'home' || betOutcome === '1')) ||
                  (outcome === 'away' && (betOutcome === 'away' || betOutcome === '2')) ||
                  (outcome === 'draw' && (betOutcome === 'draw' || betOutcome === 'x'));

                betResult = betWon ? 'won' : 'lost';
                payout = betWon ? bet.stake * bet.odds : 0;
              }

              const settled = await api.settleBet(bet.id, { result: betResult, payout });
              totalProfit += settled.profit;

              const symbol = betResult === 'won' ? '+' : betResult === 'lost' ? '-' : '~';
              const profitStr = settled.profit >= 0 ? `+$${settled.profit.toFixed(0)}` : `-$${Math.abs(settled.profit).toFixed(0)}`;
              results.push(`[${symbol}] #${bet.id} ${formatProviderName(bet.provider)}: ${betResult.toUpperCase()} (${profitStr})`);
            }

            const totalStr = totalProfit >= 0 ? `+$${totalProfit.toFixed(0)}` : `-$${Math.abs(totalProfit).toFixed(0)}`;
            const outcomeLabel = outcome === 'home' ? eventWithBets.home_team :
                                 outcome === 'away' ? eventWithBets.away_team :
                                 outcome === 'draw' ? 'Draw' : 'Void';

            sendMessage(
              `**${eventName}** → ${outcomeLabel}\n\n` +
              joinLines(results) +
              `\n\n**Total: ${totalStr}**`
            );

            onRefresh();
          } catch (err) {
            sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
          }

          cancel();
        }
        break;
      }
    }
  }, [workflow, sendMessage, onRefresh, cancel, placeValueBet]);

  return {
    workflow,
    options,
    selectedIndex,
    setSelectedIndex,
    lastOpportunities,
    lastBets,
    startValue,
    startBets,
    cancel,
    select,
    isActive: workflow.type !== 'idle',
    // Manual stake input - no longer needed but keep for backward compatibility
    manualStakeInput: '',
    setManualStakeInput: () => {},
    submitManualStake: () => {},
    isManualStakeMode: false,
  };
}
