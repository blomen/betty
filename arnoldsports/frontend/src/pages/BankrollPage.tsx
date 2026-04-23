import { useState, useMemo } from 'react';
import { Card } from '@/components/Card';
import { DepositAllocator } from '@/components/DepositAllocator';
import { SortableHeader } from '@/components/SortableHeader';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { ProviderName } from '@/components/ProviderName';
import { useTableSort } from '@/hooks/useTableSort';
import type { AllocationEnvelope, ProviderExposure } from '@/types';
import { useBankrollQuery } from '@/hooks/useBankrollQuery';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast, ToastContainer } from '@/components/Toast';

type BankrollSortCol = 'provider' | 'balance' | 'deposit' | 'bets' | 'ev';

export function BankrollPage() {
  const queryClient = useQueryClient();
  const { exposure, setBalance, isLoading } = useBankrollQuery();
  const { data: providersData } = useQuery({ queryKey: ['providers'], queryFn: () => api.getProviders() });
  const providers = providersData?.providers ?? [];
  const { toasts, addToast, dismissToast } = useToast();

  // Fetch play batch for Overview stats (deployed bets + EV)
  const { data: batchData } = useQuery({
    queryKey: ['opportunities', 'play', 'batch'],
    queryFn: () => api.getPlayBatch(),
    staleTime: 30_000,
  });

  // Fetch allocation envelope (recommended deposits per provider)
  const { data: allocation } = useQuery<AllocationEnvelope>({
    queryKey: ['bankroll', 'allocate', null],
    queryFn: () => api.allocate(null),
    staleTime: 30_000,
  });
  const recommendedTotal = allocation?.recommended_total ?? 0;
  const recommendedEv = allocation?.deposits.reduce((sum, d) => sum + d.expected_ev, 0) ?? 0;

  const depositByProvider = useMemo(() => {
    const map = new Map<string, { amount: number; unlocks: string; ev: number; currency: string }>();
    for (const d of allocation?.deposits ?? []) {
      map.set(d.provider_id, {
        amount: d.amount,
        unlocks: d.unlocks,
        ev: d.expected_ev,
        currency: d.provider_id === 'polymarket' ? 'USDC' : 'SEK',
      });
    }
    return map;
  }, [allocation]);

  const batchTotal = batchData?.summary?.total_bets ?? 0;
  const batchEV = batchData?.summary?.total_expected_profit ?? 0;

  // Set balance inline state
  const [settingProvider, setSettingProvider] = useState<string | null>(null);
  const [setBalanceInput, setSetBalanceInput] = useState('');

  const getProviderBonus = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId);
    if (!provider?.bonus) return null;
    return {
      bonus: provider.bonus,
      status: provider.bonus_status,
      hasUnclaimedBonus: !provider.bonus_status || provider.bonus_status === 'available',
    };
  };

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

  // Sort extractors including deposit column
  const bankrollSortExtractors: Record<BankrollSortCol, (p: ProviderExposure) => number> = {
    provider: (p) => {
      const n = formatProviderName(p.provider_name).toLowerCase();
      return (n.charCodeAt(0) || 0) * 10000 + (n.charCodeAt(1) || 0) * 100 + (n.charCodeAt(2) || 0);
    },
    balance: (p) => p.total_balance,
    deposit: (p) => depositByProvider.get(p.provider_id)?.amount ?? 0,
    bets: (p) => {
      const u = depositByProvider.get(p.provider_id)?.unlocks;
      if (!u) return 0;
      const match = u.match(/^(\d+)/);
      return match ? parseInt(match[1], 10) : 0;
    },
    ev: (p) => depositByProvider.get(p.provider_id)?.ev ?? 0,
  };

  // Hide signal-only providers (no balance, no bets, not playable)
  const SIGNAL_ONLY = new Set(['consensus', 'stake', 'marathon']);
  const providerList = useMemo(() => {
    const list = exposure?.providers ?? [];
    return list.filter(p =>
      !SIGNAL_ONLY.has(p.provider_id) &&
      (p.total_balance > 0 || depositByProvider.has(p.provider_id) || p.pending_exposure > 0)
    );
  }, [exposure, depositByProvider]);
  const { sorted: tableSorted, sort: provSort, toggle: toggleProvSort } =
    useTableSort<ProviderExposure, BankrollSortCol>(providerList, bankrollSortExtractors, { column: 'deposit', direction: 'desc' }, 'bbq_bankroll_sort');

  // Default sort: providers needing deposits first, then by deposit amount desc
  const sortedProviders = useMemo(() => {
    if (provSort.column !== null) return tableSorted;
    return [...tableSorted].sort((a, b) => {
      const da = depositByProvider.get(a.provider_id);
      const db = depositByProvider.get(b.provider_id);
      // Providers needing deposits first
      if (da && !db) return -1;
      if (!da && db) return 1;
      if (da && db) return db.ev - da.ev;
      // Then by balance desc
      return b.total_balance - a.total_balance;
    });
  }, [tableSorted, provSort.column, depositByProvider]);

  if (isLoading) {
    return (
      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 bg-tabBankroll" />
          Bankroll
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabBankroll" />
        Bankroll
      </h2>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Overview with deposit summary */}
      {exposure && (
        <div className="border-l-2 border-tabBankroll">
          <Card title="Overview">
            <div className="grid grid-cols-2 gap-4 text-xs">
              <div>
                <div className="text-muted mb-1">DEPLOYED</div>
                <div className="text-text text-xl font-semibold">{exposure.total_balance.toFixed(0)} kr</div>
                <div className="text-muted">{batchTotal} bets · +{batchEV.toFixed(0)} kr EV</div>
              </div>
              <div>
                <div className="text-muted mb-1">RECOMMENDED DEPOSIT</div>
                <div className="text-tabBankroll text-xl font-semibold">
                  {recommendedTotal > 0 ? `${recommendedTotal.toFixed(0)} kr` : 'Fully funded'}
                </div>
                {recommendedTotal > 0 && (
                  <div className="text-muted">→ +{recommendedEv.toFixed(0)} kr EV</div>
                )}
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* Deposit Allocator */}
      {exposure && (
        <div className="border-l-2 border-tabBankroll">
          <DepositAllocator />
        </div>
      )}

      {/* Provider Balances with deposit recommendations */}
      {exposure && (
        <div className="border-l-2 border-tabBankroll">
          <Card title="Provider Balances">
            <table className="sq">
              <thead>
                <tr>
                  <SortableHeader column="provider" label="Provider" sort={provSort} onToggle={toggleProvSort} align="left" />
                  <SortableHeader column="balance" label="Balance" sort={provSort} onToggle={toggleProvSort} />
                  <SortableHeader column="deposit" label="Deposit" sort={provSort} onToggle={toggleProvSort} />
                  <SortableHeader column="bets" label="Unlocks" sort={provSort} onToggle={toggleProvSort} />
                  <SortableHeader column="ev" label="EV" sort={provSort} onToggle={toggleProvSort} />
                  <th className="text-right"></th>
                </tr>
              </thead>
              <tbody>
                {sortedProviders.map(provider => {
                  const dep = depositByProvider.get(provider.provider_id);
                  const bonus = getProviderBonus(provider.provider_id);
                  return (
                    <tr key={provider.provider_id}>
                      <td className="text-text">
                        <ProviderName name={provider.provider_name} />
                        {bonus?.hasUnclaimedBonus && bonus.bonus && (
                          <button
                            className="ml-1.5 inline-flex items-center justify-center w-3.5 h-3.5 text-[9px] font-bold bg-tabBonus/20 text-tabBonus hover:bg-tabBonus/40 transition-colors cursor-pointer"
                            title={`Click to mark as claimed · ${bonus.bonus.type === 'freebet'
                              ? `Freebet ${bonus.bonus.amount} kr`
                              : `Bonus deposit up to ${bonus.bonus.amount} kr`}`}
                            onClick={async (e) => {
                              e.stopPropagation();
                              try {
                                await api.claimBonus(provider.provider_id);
                                queryClient.invalidateQueries({ queryKey: ['providers'] });
                                queryClient.invalidateQueries({ queryKey: ['bankroll'] });
                              } catch (err) {
                                addToast(err instanceof Error ? err.message : 'Failed to claim bonus', 'error');
                              }
                            }}
                          >
                            {bonus.bonus.type === 'freebet' ? 'f' : 'd'}
                          </button>
                        )}
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
                        {dep ? (
                          <span className="text-tabBankroll font-medium">
                            {dep.currency === 'USDC' ? `$${dep.amount.toFixed(0)}` : `${dep.amount.toFixed(0)} kr`}
                          </span>
                        ) : (
                          <span className="text-muted2">—</span>
                        )}
                      </td>
                      <td className="text-right">
                        {dep && dep.unlocks ? (
                          <span className="text-text">{dep.unlocks}</span>
                        ) : (
                          <span className="text-muted2">—</span>
                        )}
                      </td>
                      <td className="text-right">
                        {dep && dep.ev > 0 ? (
                          <span className="text-success">+{dep.ev.toFixed(0)} kr</span>
                        ) : (
                          <span className="text-muted2">—</span>
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
          </Card>
        </div>
      )}
    </div>
  );
}
