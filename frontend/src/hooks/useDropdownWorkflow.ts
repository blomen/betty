/**
 * useDropdownWorkflow - Manages extract/arb/value workflow state
 *
 * Handles multi-step workflows for extraction and opportunity selection.
 * Stakes are now auto-calculated by the backend using risk management settings.
 */
import { useState, useCallback } from 'react';
import type {
  DropdownWorkflowState,
  DropdownOption,
  Provider,
  OpportunityWithEvent,
  BankrollExposure,
  Bet,
  EventWithBets,
} from '@/types';
import { api } from '@/services/api';
import { formatArbitrageList, formatOpportunitiesList, formatBetsTable, formatProviderName, joinLines, outcomeToTeam } from '@/utils/formatters';

interface UseDropdownWorkflowProps {
  providers: Provider[];
  exposure: BankrollExposure;
  sendMessage: (msg: string) => void;
  onRefresh: () => void;
  onRunExtraction: (providers: string) => void;
}

export function useDropdownWorkflow({
  providers,
  // exposure is available for future use if needed
  sendMessage,
  onRefresh,
  onRunExtraction,
}: UseDropdownWorkflowProps) {
  const [workflow, setWorkflow] = useState<DropdownWorkflowState>({ type: 'idle', step: 'idle' });
  const [options, setOptions] = useState<DropdownOption[]>([]);
  const [selectedProviderIds, setSelectedProviderIds] = useState<Set<string>>(new Set());
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [lastOpportunities, setLastOpportunities] = useState<OpportunityWithEvent[]>([]);
  const [lastBets, setLastBets] = useState<Bet[]>([]);

  // Cancel workflow
  const cancel = useCallback(() => {
    setWorkflow({ type: 'idle', step: 'idle' });
    setOptions([]);
    setSelectedProviderIds(new Set());
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

  // Place all arb bets (record them in database) - uses pre-calculated stakes from API
  const placeArbBets = useCallback(async () => {
    const oppIndex = (workflow.selectedOpp || 1) - 1;
    const arb = workflow.fullArbs?.[oppIndex];
    if (!arb || !arb.legs || arb.legs.length === 0) return;

    const eventName = arb.home_team && arb.away_team
      ? `${arb.home_team} vs ${arb.away_team}`
      : 'Unknown';

    try {
      const betIds: number[] = [];

      for (const leg of arb.legs) {
        const result = await api.createBet({
          event_id: arb.event_id,
          provider_id: leg.provider,
          market: arb.market,
          outcome: leg.outcome,
          odds: leg.odds,
          stake: leg.stake,  // Pre-calculated by backend
          is_bonus: false,
        });
        betIds.push(result.bet_id);
      }

      const betIdStr = betIds.join(', #');
      sendMessage(
        `**BETS #${betIdStr} RECORDED** ${eventName}\n` +
        `Use \`/bets\` to settle when results are in.`
      );

      onRefresh();
      cancel();
    } catch (err) {
      sendMessage(`Error recording bets: ${err instanceof Error ? err.message : 'Unknown'}`);
      cancel();
    }
  }, [workflow, sendMessage, onRefresh, cancel]);

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

  // Start extract workflow
  const startExtract = useCallback(() => {
    const allProviders = providers.filter((p) => p.is_enabled);
    if (allProviders.length === 0) {
      sendMessage('**No providers configured.**');
      return;
    }

    const defaultSelected = new Set(['pinnacle']);
    setSelectedProviderIds(defaultSelected);

    const opts: DropdownOption[] = [
      ...allProviders.map((p) => ({
        id: p.id,
        label: formatProviderName(p.name || p.id),
        sublabel: ['pinnacle', 'polymarket'].includes(p.id) ? '(sharp)' : '(soft)',
        selected: defaultSelected.has(p.id),
        type: 'provider' as const,
      })),
      { id: 'run', label: '[RUN EXTRACTION]', sublabel: 'Start', type: 'action' as const },
      { id: 'cancel', label: '[cancel]', type: 'action' as const },
    ];

    setOptions(opts);
    setSelectedIndex(0);
    setWorkflow({ type: 'extract', step: 'select-provider', selectedProviders: Array.from(defaultSelected) });
    sendMessage(
      '**EXTRACTION** - Select providers\n\n' +
      'Use arrow keys to navigate, Enter to toggle selection.\n' +
      'Select [RUN EXTRACTION] when ready.'
    );
  }, [providers, sendMessage]);

  // Start arb workflow
  const startArb = useCallback(async () => {
    try {
      const result = await api.scanArbitrage(0.5, 20);

      // Filter out suspect arbs (>7% profit likely data errors)
      const verifiedArbs = result.opportunities.filter(a => a.quality !== 'suspect');

      if (verifiedArbs.length === 0) {
        sendMessage('No arbitrage found. Run `/extract pinnacle <provider>` first.');
        return;
      }

      sendMessage(formatArbitrageList(verifiedArbs));

      const opts: DropdownOption[] = verifiedArbs.map((arb, idx) => {
        const totalStake = arb.total_stake || arb.legs.reduce((sum, l) => sum + l.stake, 0);
        return {
          id: idx + 1,
          label: `[${idx + 1}] ${arb.profit_pct.toFixed(1)}% ${arb.home_team || 'Unknown'} vs ${arb.away_team || ''}`,
          sublabel: `${arb.legs.length} legs | $${totalStake.toFixed(0)}`,
          type: 'opportunity' as const,
        };
      });

      setOptions(withBack(opts));
      setSelectedIndex(0);
      setWorkflow({ type: 'arb', step: 'select-opportunity', fullArbs: verifiedArbs });
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage]);

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
          if (workflow.type === 'arb' && workflow.fullArbs) {
            const opts: DropdownOption[] = workflow.fullArbs.map((arb, idx) => {
              const totalStake = arb.total_stake || arb.legs.reduce((sum, l) => sum + l.stake, 0);
              return {
                id: idx + 1,
                label: `[${idx + 1}] ${arb.profit_pct.toFixed(1)}% ${arb.home_team || 'Unknown'} vs ${arb.away_team || ''}`,
                sublabel: `${arb.legs.length} legs | $${totalStake.toFixed(0)}`,
                type: 'opportunity' as const,
              };
            });
            setOptions(withBack(opts));
            setSelectedIndex(0);
            setWorkflow((prev) => ({ ...prev, step: 'select-opportunity', selectedOpp: undefined }));
          } else if (workflow.type === 'value' && workflow.opportunities) {
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
      case 'extract': {
        if (option.id === 'run') {
          const selected = Array.from(selectedProviderIds);
          if (selected.length === 0) {
            sendMessage('**Select at least one provider.**');
            return;
          }
          cancel();
          onRunExtraction(selected.join(','));
          return;
        }

        // Toggle provider selection
        const providerId = option.id as string;
        setSelectedProviderIds((prev) => {
          const newSet = new Set(prev);
          if (newSet.has(providerId)) {
            newSet.delete(providerId);
          } else {
            newSet.add(providerId);
          }
          setOptions((opts) =>
            opts.map((opt) =>
              opt.id === providerId ? { ...opt, selected: !opt.selected } : opt
            )
          );
          return newSet;
        });
        break;
      }

      case 'arb': {
        if (workflow.step === 'select-opportunity') {
          const oppIndex = (option.id as number) - 1;
          const arb = workflow.fullArbs?.[oppIndex];
          if (!arb) return;

          const eventName = arb.home_team && arb.away_team
            ? `${arb.home_team} vs ${arb.away_team}`
            : 'Unknown';

          // Use pre-calculated stakes from API
          const totalStake = arb.total_stake || arb.legs.reduce((sum, l) => sum + l.stake, 0);
          const guaranteedReturn = arb.legs[0]?.return || (totalStake / arb.legs.reduce((sum, l) => sum + (1 / l.odds), 0));
          const profit = guaranteedReturn - totalStake;

          // Table row format for legs with team names
          const legRows = arb.legs
            .map((l) => {
              const team = outcomeToTeam(l.outcome, arb.home_team || undefined, arb.away_team || undefined);
              const teamStr = team.length > 14 ? team.slice(0, 13) + '…' : team.padEnd(14);
              return `${teamStr} | ${l.provider.padEnd(10)} | ${l.odds.toFixed(2)} | $${l.stake.toFixed(0)}`;
            })
            .join('\n');

          sendMessage(
            `**#${option.id}** ${eventName} (+${arb.profit_pct.toFixed(2)}%)\n` +
            '```\n' +
            `Bet on         | Provider   | Odds  | Stake\n` +
            `---------------|------------|-------|------\n` +
            `${legRows}\n` +
            '```\n' +
            `**Total**: $${totalStake.toFixed(0)} → Return: $${guaranteedReturn.toFixed(0)} (+$${profit.toFixed(0)})\n\n` +
            `Place bets manually on each site, then confirm to record.`
          );

          // Show confirm options directly (no stake selection needed)
          const confirmOpts: DropdownOption[] = [
            { id: 'confirm', label: '[CONFIRM]', sublabel: 'Record bets', type: 'action' as const },
          ];

          setOptions(withBack(confirmOpts));
          setSelectedIndex(0);
          setWorkflow((prev) => ({
            ...prev,
            step: 'confirm',
            selectedOpp: option.id as number,
          }));
        } else if (workflow.step === 'confirm') {
          if (option.id === 'confirm') {
            await placeArbBets();
          }
        }
        break;
      }

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
            `Risk: ${opp.risk_level || 'unknown'}\n` +
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
  }, [workflow, selectedProviderIds, sendMessage, onRefresh, onRunExtraction, cancel, placeArbBets, placeValueBet]);

  return {
    workflow,
    options,
    selectedIndex,
    setSelectedIndex,
    lastOpportunities,
    lastBets,
    startExtract,
    startArb,
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
