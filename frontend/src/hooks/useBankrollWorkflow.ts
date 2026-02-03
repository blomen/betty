/**
 * useBankrollWorkflow - Manages bankroll management workflow state
 *
 * Handles deposit, withdraw, settings, and reset flows.
 */
import { useState, useCallback } from 'react';
import type {
  BankrollWorkflowState,
  BankrollOption,
  BankrollExposure,
  Profile,
} from '@/types';
import { api } from '@/services/api';
import { formatBankrollTable, formatProviderName } from '@/utils/formatters';

// Settings configuration
const KELLY_OPTIONS = [
  { value: 0.1, label: '10%', sublabel: 'Conservative' },
  { value: 0.25, label: '25%', sublabel: 'Moderate' },
  { value: 0.5, label: '50%', sublabel: 'Aggressive' },
  { value: 1.0, label: '100%', sublabel: 'Full Kelly' },
];

const MAX_STAKE_OPTIONS = [
  { value: 1, label: '1%', sublabel: 'Very safe' },
  { value: 2, label: '2%', sublabel: 'Recommended' },
  { value: 5, label: '5%', sublabel: 'Moderate' },
  { value: 10, label: '10%', sublabel: 'Aggressive' },
];

const MIN_EDGE_OPTIONS = [
  { value: 1, label: '1%', sublabel: 'Low threshold' },
  { value: 2, label: '2%', sublabel: 'Recommended' },
  { value: 3, label: '3%', sublabel: 'Conservative' },
  { value: 5, label: '5%', sublabel: 'Very selective' },
];

const MIN_ARB_OPTIONS = [
  { value: 0.5, label: '0.5%', sublabel: 'Low threshold' },
  { value: 1, label: '1%', sublabel: 'Recommended' },
  { value: 2, label: '2%', sublabel: 'Conservative' },
];

interface UseBankrollWorkflowProps {
  exposure: BankrollExposure;
  profile: Profile | null;
  sendMessage: (msg: string) => void;
  onRefresh: () => void;
}

export function useBankrollWorkflow({
  exposure,
  profile,
  sendMessage,
  onRefresh,
}: UseBankrollWorkflowProps) {
  const [workflow, setWorkflow] = useState<BankrollWorkflowState>({ step: 'idle' });
  const [options, setOptions] = useState<BankrollOption[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [amountInput, setAmountInput] = useState('');
  const [confirmInput, setConfirmInput] = useState('');

  // Cancel workflow
  const cancel = useCallback(() => {
    setWorkflow({ step: 'idle' });
    setOptions([]);
    setSelectedIndex(0);
    setAmountInput('');
    setConfirmInput('');
  }, []);

  // Start bankroll workflow - shows table and action menu
  const start = useCallback(async () => {
    try {
      // Show bankroll table first
      const data = await api.getBankrollExposure();
      sendMessage(formatBankrollTable(data));

      // Show action menu
      const actionOpts: BankrollOption[] = [
        { id: 'deposit', label: '[1] Deposit', sublabel: 'Add funds', type: 'action' },
        { id: 'withdraw', label: '[2] Withdraw', sublabel: 'Remove funds', type: 'action' },
        { id: 'settings', label: '[3] Settings', sublabel: 'Kelly & limits', type: 'action' },
        { id: 'reset', label: '[4] Reset', sublabel: 'Zero all (!)', type: 'action' },
      ];

      setOptions(actionOpts);
      setSelectedIndex(0);
      setWorkflow({ step: 'select-action' });
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
    }
  }, [sendMessage]);

  // Show provider selection for deposit/withdraw
  const showProviderSelection = useCallback((action: 'deposit' | 'withdraw') => {
    if (exposure.providers.length === 0) {
      sendMessage('No providers configured.');
      cancel();
      return;
    }

    const providerOpts: BankrollOption[] = exposure.providers.map((p) => ({
      id: p.provider_id,
      label: formatProviderName(p.provider_name),
      sublabel: `$${p.total_balance.toFixed(0)}`,
      type: 'provider' as const,
    }));

    setOptions(providerOpts);
    setSelectedIndex(0);
    setWorkflow({ step: 'select-provider', action });
  }, [exposure.providers, sendMessage, cancel]);

  // Show settings menu
  const showSettingsMenu = useCallback(() => {
    if (!profile) {
      sendMessage('No active profile. Create one first.');
      cancel();
      return;
    }

    const currentSettings = `Current: Kelly ${(profile.kelly_fraction * 100).toFixed(0)}%, Max ${profile.max_stake_pct}%, Min Edge ${profile.min_edge_pct}%, Min Arb ${profile.min_arb_pct}%`;
    sendMessage(currentSettings);

    const settingOpts: BankrollOption[] = [
      { id: 'kelly_fraction', label: 'Kelly Fraction', sublabel: `${(profile.kelly_fraction * 100).toFixed(0)}%`, type: 'setting' },
      { id: 'max_stake_pct', label: 'Max Stake %', sublabel: `${profile.max_stake_pct}%`, type: 'setting' },
      { id: 'min_edge_pct', label: 'Min Edge %', sublabel: `${profile.min_edge_pct}%`, type: 'setting' },
      { id: 'min_arb_pct', label: 'Min Arb %', sublabel: `${profile.min_arb_pct}%`, type: 'setting' },
    ];

    setOptions(settingOpts);
    setSelectedIndex(0);
    setWorkflow({ step: 'select-setting', action: 'settings' });
  }, [profile, sendMessage, cancel]);

  // Show value options for selected setting
  const showValueOptions = useCallback((setting: string) => {
    let valueOpts: BankrollOption[] = [];

    switch (setting) {
      case 'kelly_fraction':
        valueOpts = KELLY_OPTIONS.map((opt) => ({
          id: opt.value,
          label: opt.label,
          sublabel: opt.sublabel,
          type: 'value' as const,
        }));
        break;
      case 'max_stake_pct':
        valueOpts = MAX_STAKE_OPTIONS.map((opt) => ({
          id: opt.value,
          label: opt.label,
          sublabel: opt.sublabel,
          type: 'value' as const,
        }));
        break;
      case 'min_edge_pct':
        valueOpts = MIN_EDGE_OPTIONS.map((opt) => ({
          id: opt.value,
          label: opt.label,
          sublabel: opt.sublabel,
          type: 'value' as const,
        }));
        break;
      case 'min_arb_pct':
        valueOpts = MIN_ARB_OPTIONS.map((opt) => ({
          id: opt.value,
          label: opt.label,
          sublabel: opt.sublabel,
          type: 'value' as const,
        }));
        break;
    }

    setOptions(valueOpts);
    setSelectedIndex(0);
    setWorkflow((prev) => ({ ...prev, step: 'select-value', selectedSetting: setting }));
  }, []);

  // Show reset confirmation
  const showResetConfirmation = useCallback(() => {
    sendMessage(
      '**WARNING**: This will reset ALL provider balances to $0.\n\n' +
      'Type RESET to confirm, or press Esc to cancel.'
    );
    setOptions([]);
    setWorkflow({ step: 'confirm-reset', action: 'reset' });
  }, [sendMessage]);

  // Submit amount for deposit/withdraw
  const submitAmount = useCallback(async () => {
    const amount = parseFloat(amountInput);
    if (isNaN(amount) || amount <= 0) {
      sendMessage('Invalid amount. Enter a positive number.');
      return;
    }

    const providerId = workflow.selectedProvider;
    if (!providerId) return;

    const provider = exposure.providers.find((p) => p.provider_id === providerId);
    const providerName = formatProviderName(provider?.provider_name || providerId);

    try {
      // For withdraw, use negative amount
      const adjustmentAmount = workflow.action === 'withdraw' ? -amount : amount;
      const result = await api.adjustBalance(providerId, adjustmentAmount);

      const action = workflow.action === 'withdraw' ? 'Withdrawn' : 'Deposited';
      sendMessage(
        `**${action}** $${amount.toFixed(2)} ${workflow.action === 'withdraw' ? 'from' : 'to'} ${providerName}\n` +
        `New balance: $${result.new_balance.toFixed(2)}`
      );

      onRefresh();
      cancel();
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
      cancel();
    }
  }, [amountInput, workflow, exposure.providers, sendMessage, onRefresh, cancel]);

  // Submit reset confirmation
  const submitResetConfirmation = useCallback(async () => {
    if (confirmInput !== 'RESET') {
      sendMessage('Type RESET exactly to confirm.');
      return;
    }

    try {
      const result = await api.resetAllBalances();
      sendMessage(`**RESET COMPLETE** - ${result.reset_count} providers zeroed.`);
      onRefresh();
      cancel();
    } catch (err) {
      sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
      cancel();
    }
  }, [confirmInput, sendMessage, onRefresh, cancel]);

  // Handle selection
  const select = useCallback(async (option: BankrollOption) => {
    switch (workflow.step) {
      case 'select-action': {
        const action = option.id as 'deposit' | 'withdraw' | 'settings' | 'reset';

        if (action === 'deposit' || action === 'withdraw') {
          showProviderSelection(action);
        } else if (action === 'settings') {
          showSettingsMenu();
        } else if (action === 'reset') {
          showResetConfirmation();
        }
        break;
      }

      case 'select-provider': {
        const providerId = option.id as string;
        const provider = exposure.providers.find((p) => p.provider_id === providerId);
        const providerName = formatProviderName(provider?.provider_name || providerId);
        const currentBalance = provider?.total_balance || 0;

        sendMessage(`${workflow.action === 'withdraw' ? 'Withdraw from' : 'Deposit to'} **${providerName}** (current: $${currentBalance.toFixed(2)})\nEnter amount:`);

        setOptions([]);
        setWorkflow((prev) => ({ ...prev, step: 'enter-amount', selectedProvider: providerId }));
        break;
      }

      case 'select-setting': {
        const setting = option.id as string;
        showValueOptions(setting);
        break;
      }

      case 'select-value': {
        const value = option.id as number;
        const setting = workflow.selectedSetting;

        if (!profile || !setting) {
          cancel();
          return;
        }

        try {
          const updateData: Record<string, number> = {};
          updateData[setting] = value;

          await api.updateProfile(profile.id, updateData);

          const settingLabel = setting.replace(/_/g, ' ').replace(/pct/g, '%');
          sendMessage(`**Updated** ${settingLabel} to ${option.label}`);

          onRefresh();
          cancel();
        } catch (err) {
          sendMessage(`Error: ${err instanceof Error ? err.message : 'Unknown'}`);
          cancel();
        }
        break;
      }
    }
  }, [workflow, exposure.providers, profile, sendMessage, onRefresh, cancel, showProviderSelection, showSettingsMenu, showResetConfirmation, showValueOptions]);

  return {
    workflow,
    options,
    selectedIndex,
    setSelectedIndex,
    start,
    cancel,
    select,
    isActive: workflow.step !== 'idle',
    // Amount input
    amountInput,
    setAmountInput,
    submitAmount,
    isAmountMode: workflow.step === 'enter-amount',
    // Reset confirmation input
    confirmInput,
    setConfirmInput,
    submitResetConfirmation,
    isResetConfirmMode: workflow.step === 'confirm-reset',
  };
}
