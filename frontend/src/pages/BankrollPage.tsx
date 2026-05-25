import { useState, useMemo } from 'react';
import { Card } from '@/components/Card';
import { SortableHeader } from '@/components/SortableHeader';
import { formatProviderName } from '@/utils/formatters';
import { ProviderName } from '@/components/ProviderName';
import { useTableSort } from '@/hooks/useTableSort';
import type { ProviderExposure } from '@/types';
import { useBankrollQuery } from '@/hooks/useBankrollQuery';
import { useToast, ToastContainer } from '@/components/Toast';

type BankrollSortCol = 'provider' | 'balance';

export function BankrollPage() {
  const { exposure, setBalance, isLoading } = useBankrollQuery();
  const { toasts, addToast, dismissToast } = useToast();

  // Set balance inline state
  const [settingProvider, setSettingProvider] = useState<string | null>(null);
  const [setBalanceInput, setSetBalanceInput] = useState('');

  const fmtAmount = (providerId: string, amount: number) => {
    const prov = exposure?.providers.find(p => p.provider_id === providerId);
    if (prov?.currency && prov.currency !== 'SEK') {
      const sek = amount * (prov.exchange_rate_sek ?? 1);
      return `${sek.toFixed(0)} kr`;
    }
    return `${amount.toFixed(0)} kr`;
  };

  const handleSetBalance = async (providerId: string) => {
    const balance = parseFloat(setBalanceInput);
    if (isNaN(balance) || balance < 0) return;
    try {
      const result = await setBalance.mutateAsync({ providerId, balance });
      addToast(`Balance set to ${fmtAmount(providerId, balance)} (was ${fmtAmount(providerId, result.old_balance)})`, 'success');
      setSettingProvider(null);
      setSetBalanceInput('');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Set balance failed', 'error');
    }
  };

  const bankrollSortExtractors: Record<BankrollSortCol, (p: ProviderExposure) => number> = {
    provider: (p) => {
      const n = formatProviderName(p.provider_name).toLowerCase();
      return (n.charCodeAt(0) || 0) * 10000 + (n.charCodeAt(1) || 0) * 100 + (n.charCodeAt(2) || 0);
    },
    balance: (p) => p.total_balance,
  };

  // Hide signal-only providers (no balance, no bets, not playable)
  const SIGNAL_ONLY = new Set(['consensus', 'stake', 'marathon']);
  const providerList = useMemo(() => {
    const list = exposure?.providers ?? [];
    return list.filter(p =>
      !SIGNAL_ONLY.has(p.provider_id) &&
      (p.total_balance > 0 || p.pending_exposure > 0)
    );
  }, [exposure]);
  const { sorted: sortedProviders, sort: provSort, toggle: toggleProvSort } =
    useTableSort<ProviderExposure, BankrollSortCol>(providerList, bankrollSortExtractors, { column: 'balance', direction: 'desc' }, 'bbq_bankroll_sort');

  return (
    <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabBankroll" />
        Bankroll
      </h2>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      <div className="border-l-2 border-tabBankroll">
        <Card title="Total Capital">
          {isLoading && !exposure ? (
            <div className="text-muted text-sm">Loading...</div>
          ) : (
            <div className="text-text text-3xl font-semibold">
              {(exposure?.total_balance ?? 0).toFixed(0)} kr
            </div>
          )}
        </Card>
      </div>

      <div className="border-l-2 border-tabBankroll">
        <Card title="Provider Balances">
          {isLoading && !exposure ? (
            <div className="text-muted text-sm py-4 text-center">Loading...</div>
          ) : sortedProviders.length === 0 ? (
            <div className="text-muted text-sm py-4 text-center">
              No balances set. Open the Sports tab and click <span className="text-text">Set</span> on a provider, or place a bet to populate this table.
            </div>
          ) : (
            <table className="sq">
              <thead>
                <tr>
                  <SortableHeader column="provider" label="Provider" sort={provSort} onToggle={toggleProvSort} align="left" />
                  <SortableHeader column="balance" label="Balance" sort={provSort} onToggle={toggleProvSort} />
                  <th className="text-right"></th>
                </tr>
              </thead>
              <tbody>
                {sortedProviders.map(provider => {
                  return (
                    <tr key={provider.provider_id}>
                      <td className="text-text">
                        <ProviderName name={provider.provider_name} />
                      </td>
                      <td className="text-right text-text">
                        {provider.currency && provider.currency !== 'SEK' ? (
                          <span title={`$${provider.total_balance.toFixed(2)}`}>
                            {provider.balance_sek?.toFixed(0) ?? '?'} kr
                            <span className="text-muted2 text-[10px] ml-1">(${provider.total_balance.toFixed(2)})</span>
                          </span>
                        ) : (
                          <>{provider.total_balance.toFixed(0)} kr</>
                        )}
                      </td>
                      <td className="text-right">
                        {settingProvider === provider.provider_id ? (
                          <div className="flex items-center justify-end gap-1">
                            <input
                              type="number"
                              value={setBalanceInput}
                              onChange={(e) => setSetBalanceInput(e.target.value)}
                              placeholder={provider.total_balance.toFixed(0)}
                              className="w-20 px-2 py-1 bg-panel2 border border-border text-text text-xs"
                              autoFocus
                              onKeyDown={(e) => e.key === 'Enter' && handleSetBalance(provider.provider_id)}
                            />
                            <button
                              onClick={() => handleSetBalance(provider.provider_id)}
                              className="px-2 py-1 text-xs bg-tabBankroll/20 text-tabBankroll hover:bg-tabBankroll/30"
                            >
                              =
                            </button>
                            <button
                              onClick={() => { setSettingProvider(null); setSetBalanceInput(''); }}
                              className="px-1 py-1 text-xs text-muted hover:text-text"
                            >
                              x
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setSettingProvider(provider.provider_id)}
                            className="px-2 py-1 text-xs text-tabBankroll hover:opacity-80"
                          >
                            Set
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
