/**
 * useBonusWorkflow - Manages bonus arbitrage workflow state
 *
 * Extracts bonus workflow logic from TerminalWindow to reduce complexity.
 */
import { useState, useCallback } from 'react';
import type { BonusArb, BonusWorkflowState, BonusDropdownOption, Provider } from '@/types';
import { api } from '@/services/api';
import { formatBonusArbitrage, formatProviderName } from '@/utils/formatters';

interface UseBonusWorkflowProps {
  providers: Provider[];
  sendMessage: (msg: string) => void;
  onRefresh: () => void;
}

export function useBonusWorkflow({ providers, sendMessage, onRefresh }: UseBonusWorkflowProps) {
  const [workflow, setWorkflow] = useState<BonusWorkflowState>({ step: 'idle' });
  const [bonusArbs, setBonusArbs] = useState<BonusArb[]>([]);
  const [options, setOptions] = useState<BonusDropdownOption[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [manualStakeInput, setManualStakeInput] = useState('');
  const [selectedStake, setSelectedStake] = useState(0);

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
    setSelectedStake(0);
  }, []);

  // Show confirmation with stake
  const showConfirmation = useCallback((stake: number) => {
    const oppIndex = (workflow.selectedOpp || 1) - 1;
    const opp = bonusArbs[oppIndex];
    if (!opp) return;

    setSelectedStake(stake);

    const eventName = opp.home_team && opp.away_team
      ? `${opp.home_team} vs ${opp.away_team}`
      : 'Unknown Event';

    const potentialReturn = stake * opp.anchor_odds;
    const potentialProfit = potentialReturn - stake;

    sendMessage(
      `**BONUS BET** ${eventName}\n` +
      `├─ ${opp.anchor_provider}: ${opp.outcome} @ ${opp.anchor_odds.toFixed(2)}\n` +
      `├─ Stake: $${stake.toFixed(2)} (BONUS)\n` +
      `├─ Potential return: $${potentialReturn.toFixed(2)} (+$${potentialProfit.toFixed(2)})\n` +
      `└─ Edge: +${opp.edge_pct.toFixed(1)}%\n\n` +
      `Place bet manually on site, then confirm to record.`
    );

    setOptions([
      { id: 'confirm', label: '[CONFIRM]', sublabel: 'Record bet', type: 'action' as const },
    ]);
    setSelectedIndex(0);
    setWorkflow((prev) => ({ ...prev, step: 'confirm', suggestedStake: stake }));
  }, [workflow, bonusArbs, sendMessage]);

  // Place bonus bet (record in database)
  const placeBonusBet = useCallback(async () => {
    const oppIndex = (workflow.selectedOpp || 1) - 1;
    const opp = bonusArbs[oppIndex];
    const stake = selectedStake || workflow.suggestedStake || 0;

    if (!opp || stake <= 0) {
      sendMessage('Invalid bet configuration.');
      cancel();
      return;
    }

    try {
      const result = await api.createBet({
        event_id: opp.event_id,
        provider_id: opp.anchor_provider,
        market: opp.market,
        outcome: opp.outcome,
        odds: opp.anchor_odds,
        stake,
        is_bonus: true,
      });

      const eventName = opp.home_team && opp.away_team
        ? `${opp.home_team} vs ${opp.away_team}`
        : 'Unknown Event';

      sendMessage(
        `**BET RECORDED** ${eventName}\n` +
        `├─ Bet ID: #${result.bet_id}\n` +
        `├─ ${opp.anchor_provider}: ${opp.outcome} @ ${opp.anchor_odds.toFixed(2)} - $${stake.toFixed(2)} (BONUS)\n` +
        `└─ Use \`/bets\` to settle when result is in.`
      );

      onRefresh();
    } catch (err) {
      sendMessage(`Error recording bet: ${err instanceof Error ? err.message : 'Unknown'}`);
    }

    cancel();
  }, [workflow, bonusArbs, selectedStake, sendMessage, onRefresh, cancel]);

  // Handle manual stake submission
  const submitManualStake = useCallback((stakeStr: string) => {
    const stake = parseFloat(stakeStr);
    if (isNaN(stake) || stake <= 0) {
      sendMessage('Invalid stake amount. Enter a positive number.');
      return;
    }

    setManualStakeInput('');
    showConfirmation(stake);
  }, [sendMessage, showConfirmation]);

  // Handle selection at each step
  const select = useCallback(async (option: BonusDropdownOption) => {
    // Handle cancel/back
    if (option.id === 'cancel') {
      const prevSteps: Record<string, BonusWorkflowState['step']> = {
        'select-opportunity': 'select-provider',
        'select-stake': 'select-opportunity',
        'confirm': 'select-stake',
      };
      const prevStep = prevSteps[workflow.step];
      if (prevStep === 'select-provider') {
        start();
      } else if (prevStep) {
        setWorkflow((prev) => ({ ...prev, step: prevStep }));
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
          setBonusArbs(result.opportunities);

          if (result.opportunities.length === 0) {
            sendMessage(
              `**No arbitrage found for ${option.label}.**\n\n` +
              `Make sure both Pinnacle and ${option.label} have data.\n` +
              `Run \`/extract pinnacle ${providerId}\` to refresh.`
            );
            cancel();
            return;
          }

          sendMessage(formatBonusArbitrage(
            result.opportunities,
            providerId,
            result.total_bankroll,
            result.anchor_balance
          ));

          const oppOptions: BonusDropdownOption[] = result.opportunities.slice(0, 20).map((opp, idx) => ({
            id: idx + 1,
            label: `[${idx + 1}] +${opp.edge_pct.toFixed(1)}% ${opp.home_team || 'TBD'} vs ${opp.away_team || 'TBD'}`,
            sublabel: `$${opp.suggested_stake.toFixed(0)} suggested`,
            type: 'opportunity' as const,
          }));

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

        sendMessage(
          `**#${option.id}** ${eventName} (+${opp.edge_pct.toFixed(1)}%)\n` +
          `├─ ${opp.anchor_provider}: ${opp.outcome} @ ${opp.anchor_odds.toFixed(2)}\n` +
          `├─ Fair odds: ${opp.fair_odds.toFixed(2)} (from Pinnacle)\n` +
          `└─ Suggested: $${opp.suggested_stake.toFixed(0)} (Kelly)`
        );

        const conservative = Math.max(opp.suggested_stake / 2, 1);
        const stakeOptions: BonusDropdownOption[] = [
          { id: opp.suggested_stake, label: `$${opp.suggested_stake.toFixed(0)}`, sublabel: 'Kelly', type: 'stake' as const },
          { id: opp.max_stake, label: `$${opp.max_stake.toFixed(0)}`, sublabel: 'max 5%', type: 'stake' as const },
          { id: conservative, label: `$${conservative.toFixed(0)}`, sublabel: 'conservative', type: 'stake' as const },
          { id: 'manual', label: '[manual]', sublabel: 'enter amount', type: 'action' as const },
        ];

        setOptions(stakeOptions);
        setSelectedIndex(0);
        setWorkflow((prev) => ({ ...prev, step: 'select-stake', selectedOpp: option.id as number }));
        break;
      }

      case 'select-stake': {
        if (option.id === 'manual') {
          // Switch to manual input mode
          setWorkflow((prev) => ({ ...prev, step: 'manual-stake' }));
          setOptions([]);
          return;
        }

        const stake = option.id as number;
        showConfirmation(stake);
        break;
      }

      case 'manual-stake': {
        // Handled by submitManualStake
        break;
      }

      case 'confirm': {
        if (option.id === 'confirm') {
          await placeBonusBet();
        }
        break;
      }
    }
  }, [workflow, bonusArbs, sendMessage, onRefresh, start, cancel]);

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
