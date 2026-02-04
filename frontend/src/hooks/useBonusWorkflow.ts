/**
 * useBonusWorkflow - Manages bonus arbitrage workflow state
 *
 * Extracts bonus workflow logic from TerminalWindow to reduce complexity.
 */
import { useState, useCallback } from 'react';
import type { BonusArbOpportunity, BonusWorkflowState, BonusDropdownOption, Provider } from '@/types';
import { api } from '@/services/api';
import { formatBonusArbitrage, formatProviderName } from '@/utils/formatters';

interface UseBonusWorkflowProps {
  providers: Provider[];
  sendMessage: (msg: string) => void;
  onRefresh: () => void;
}

export function useBonusWorkflow({ providers, sendMessage, onRefresh }: UseBonusWorkflowProps) {
  const [workflow, setWorkflow] = useState<BonusWorkflowState>({ step: 'idle' });
  const [bonusArbs, setBonusArbs] = useState<BonusArbOpportunity[]>([]);
  const [options, setOptions] = useState<BonusDropdownOption[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [manualStakeInput, setManualStakeInput] = useState('');

  // Start bonus workflow
  const start = useCallback(() => {
    const softProviders = providers.filter(
      (p) => p.is_enabled && !['pinnacle', 'polymarket'].includes(p.id)
    );

    if (softProviders.length === 0) {
      sendMessage(
        '**No soft providers configured.**\n\n' +
        'Bonus arbitrage requires at least one soft provider with balance.\n' +
        'Run `/extract leovegas` or similar to add provider data.'
      );
      return;
    }

    const providerOptions: BonusDropdownOption[] = softProviders.map((p) => ({
      id: p.id,
      label: formatProviderName(p.name || p.id),
      sublabel: `$${p.balance.toFixed(2)} balance`,
      type: 'provider' as const,
    }));

    setOptions(providerOptions);
    setWorkflow({ step: 'select-provider' });
    setSelectedIndex(0);
    sendMessage(
      '**BONUS ARBITRAGE SCANNER**\n\n' +
      'Select anchor provider from the dropdown below.\n' +
      'Use arrow keys to navigate, Enter to select.'
    );
  }, [providers, sendMessage]);

  // Cancel workflow
  const cancel = useCallback(() => {
    setWorkflow({ step: 'idle' });
    setOptions([]);
    setBonusArbs([]);
    setSelectedIndex(0);
    setManualStakeInput('');
  }, []);

  // Place bonus bet (record in database) - records all legs
  const placeBonusBet = useCallback(async () => {
    const oppIndex = (workflow.selectedOpp || 1) - 1;
    const opp = bonusArbs[oppIndex];

    if (!opp || opp.legs.length === 0) {
      sendMessage('Invalid bet configuration.');
      cancel();
      return;
    }

    const eventName = opp.home_team && opp.away_team
      ? `${opp.home_team} vs ${opp.away_team}`
      : 'Unknown Event';

    try {
      const betIds: number[] = [];
      // Record each leg as a separate bet
      for (const leg of opp.legs) {
        const result = await api.createBet({
          event_id: opp.event_id,
          provider_id: leg.provider,
          market: opp.market,
          outcome: leg.outcome,
          odds: leg.odds,
          stake: leg.stake,
          is_bonus: leg.is_anchor,
          bonus_type: leg.bonus_type || undefined,
        });
        betIds.push(result.bet_id);
      }

      const anchorLeg = opp.legs.find(l => l.is_anchor);
      const hedgeLegs = opp.legs.filter(l => !l.is_anchor);

      let msg = `**BETS RECORDED** ${eventName}\n`;
      msg += `├─ Bet IDs: #${betIds.join(', #')}\n`;
      if (anchorLeg) {
        msg += `├─ ANCHOR: ${anchorLeg.outcome} @ ${anchorLeg.odds.toFixed(2)} - $${anchorLeg.stake.toFixed(2)} (${anchorLeg.provider})\n`;
      }
      hedgeLegs.forEach((leg, i) => {
        const prefix = i === hedgeLegs.length - 1 ? '└─' : '├─';
        msg += `${prefix} HEDGE: ${leg.outcome} @ ${leg.odds.toFixed(2)} - $${leg.stake.toFixed(2)} (${leg.provider})\n`;
      });
      msg += `\nUse \`/bets\` to settle when result is in.`;

      sendMessage(msg);
      onRefresh();
    } catch (err) {
      sendMessage(`Error recording bet: ${err instanceof Error ? err.message : 'Unknown'}`);
    }

    cancel();
  }, [workflow, bonusArbs, sendMessage, onRefresh, cancel]);

  // Handle manual stake submission (kept for compatibility, not used in true arb flow)
  const submitManualStake = useCallback((_stakeStr: string) => {
    // In true arb flow, stakes are pre-calculated - this is a no-op
    sendMessage('Stakes are pre-calculated for arbitrage opportunities.');
  }, [sendMessage]);

  // Handle selection at each step
  const select = useCallback(async (option: BonusDropdownOption) => {
    // Handle cancel/back
    if (option.id === 'cancel') {
      const prevSteps: Record<string, BonusWorkflowState['step']> = {
        'select-opportunity': 'select-provider',
        'confirm': 'select-opportunity',
      };
      const prevStep = prevSteps[workflow.step];
      if (prevStep === 'select-provider') {
        start();
      } else if (prevStep === 'select-opportunity') {
        // Go back to opportunity selection - re-trigger provider scan
        const providerId = workflow.anchorProvider;
        if (providerId) {
          setWorkflow((prev) => ({ ...prev, step: 'select-provider' }));
          // Re-select provider to refresh opportunities
          start();
        } else {
          cancel();
        }
      } else {
        cancel();
      }
      return;
    }

    switch (workflow.step) {
      case 'select-provider': {
        const providerId = option.id as string;
        sendMessage(`Scanning **${option.label}** for bonus arbitrage...`);

        try {
          const result = await api.getBonusArbitrage(providerId);

          // Filter out suspect arbs (>7% profit likely data errors)
          const verifiedOpps = result.opportunities.filter(o => o.quality !== 'suspect');
          setBonusArbs(verifiedOpps);

          if (verifiedOpps.length === 0) {
            sendMessage(
              `**No arbitrage found for ${option.label}.**\n\n` +
              `Make sure both Pinnacle and ${option.label} have data.\n` +
              `Run \`/extract pinnacle ${providerId}\` to refresh.`
            );
            cancel();
            return;
          }

          sendMessage(formatBonusArbitrage(
            verifiedOpps,
            providerId,
            result.total_bankroll,
            result.anchor_balance
          ));

          const oppOptions: BonusDropdownOption[] = verifiedOpps.slice(0, 20).map((opp, idx) => {
            const profitSign = opp.profit_pct >= 0 ? '+' : '';
            const totalStake = opp.legs.reduce((sum, l) => sum + l.stake, 0);
            return {
              id: idx + 1,
              label: `[${idx + 1}] ${profitSign}${opp.profit_pct.toFixed(1)}% ${opp.home_team || 'TBD'} vs ${opp.away_team || 'TBD'}`,
              sublabel: `$${totalStake.toFixed(0)} total investment`,
              type: 'opportunity' as const,
            };
          });

          setOptions(oppOptions);
          setSelectedIndex(0);
          setWorkflow({
            step: 'select-opportunity',
            anchorProvider: providerId,
            totalBankroll: result.total_bankroll,
            anchorBalance: result.anchor_balance,
          });
        } catch (err) {
          sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`);
          cancel();
        }
        break;
      }

      case 'select-opportunity': {
        const oppIndex = (option.id as number) - 1;
        const opp = bonusArbs[oppIndex];
        if (!opp) return;

        const eventName = opp.home_team && opp.away_team
          ? `${opp.home_team} vs ${opp.away_team}`
          : 'Unknown Event';

        const anchorLeg = opp.legs.find(l => l.is_anchor);
        const hedgeLegs = opp.legs.filter(l => !l.is_anchor);
        const totalInvestment = opp.legs.reduce((sum, l) => sum + l.stake, 0);
        const profitSign = opp.profit_pct >= 0 ? '+' : '';

        let msg = `**#${option.id}** ${eventName} (${profitSign}${opp.profit_pct.toFixed(1)}%)\n`;
        if (anchorLeg) {
          msg += `├─ ANCHOR: ${anchorLeg.outcome} @ ${anchorLeg.odds.toFixed(2)} - $${anchorLeg.stake.toFixed(2)} (${anchorLeg.provider})\n`;
        }
        hedgeLegs.forEach((leg, i) => {
          const prefix = i === hedgeLegs.length - 1 ? '└─' : '├─';
          msg += `${prefix} HEDGE: ${leg.outcome} @ ${leg.odds.toFixed(2)} - $${leg.stake.toFixed(2)} (${leg.provider})\n`;
        });
        msg += `\nTotal: $${totalInvestment.toFixed(2)} → $${(totalInvestment + opp.profit_amount).toFixed(2)} (${profitSign}$${opp.profit_amount.toFixed(2)})`;

        sendMessage(msg);

        // For arbitrage, stakes are pre-calculated, so go directly to confirm
        setOptions([
          { id: 'confirm', label: '[PLACE BETS]', sublabel: `Invest $${totalInvestment.toFixed(0)}`, type: 'action' as const },
          { id: 'cancel', label: '[Back]', sublabel: 'Select different', type: 'action' as const },
        ]);
        setSelectedIndex(0);
        setWorkflow((prev) => ({ ...prev, step: 'confirm', selectedOpp: option.id as number }));
        break;
      }

      case 'confirm': {
        if (option.id === 'confirm') {
          await placeBonusBet();
        }
        break;
      }
    }
  }, [workflow, bonusArbs, sendMessage, start, cancel, placeBonusBet]);

  return {
    workflow,
    options,
    selectedIndex,
    setSelectedIndex,
    start,
    cancel,
    select,
    isActive: workflow.step !== 'idle',
    // Manual stake input
    manualStakeInput,
    setManualStakeInput,
    submitManualStake,
    isManualStakeMode: workflow.step === 'manual-stake',
  };
}
