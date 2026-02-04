import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { BankrollExposure, Profile } from '@/types';

interface BankrollPageProps {
  onRefresh: () => void;
}

export function BankrollPage({ onRefresh }: BankrollPageProps) {
  const [exposure, setExposure] = useState<BankrollExposure | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [adjustingProvider, setAdjustingProvider] = useState<string | null>(null);
  const [adjustAmount, setAdjustAmount] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [exposureData, profileData] = await Promise.all([
        api.getBankrollExposure(),
        api.getActiveProfile().catch(() => null),
      ]);
      setExposure(exposureData);
      setProfile(profileData);
    } catch (err) {
      console.error('Failed to fetch bankroll data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleAdjust = async (providerId: string, isDeposit: boolean) => {
    const amount = parseFloat(adjustAmount);
    if (isNaN(amount) || amount <= 0) return;

    try {
      await api.adjustBalance(providerId, isDeposit ? amount : -amount);
      setAdjustingProvider(null);
      setAdjustAmount('');
      fetchData();
      onRefresh();
    } catch (err) {
      console.error('Failed to adjust balance:', err);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBankroll" />
          Bankroll
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabBankroll" />
        Bankroll
      </h2>

      {/* Overview */}
      {exposure && (
        <Card title="Overview">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-muted text-sm">Total Balance</div>
              <div className="text-text text-xl font-semibold">{exposure.total_balance.toFixed(0)} kr</div>
            </div>
            <div>
              <div className="text-muted text-sm">Available</div>
              <div className="text-success text-xl font-semibold">{exposure.total_available.toFixed(0)} kr</div>
            </div>
            <div>
              <div className="text-muted text-sm">Pending</div>
              <div className="text-tabBets text-xl font-semibold">{exposure.total_pending.toFixed(0)} kr</div>
            </div>
            <div>
              <div className="text-muted text-sm">Utilization</div>
              <div className="text-text text-xl font-semibold">
                {exposure.total_balance > 0
                  ? ((exposure.total_pending / exposure.total_balance) * 100).toFixed(1)
                  : 0}%
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Profile Settings */}
      {profile && (
        <Card title="Active Profile">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-muted">Profile</div>
              <div className="text-text font-medium">{profile.name}</div>
            </div>
            <div>
              <div className="text-muted">Kelly Fraction</div>
              <div className="text-text">{(profile.kelly_fraction * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="text-muted">Min Edge</div>
              <div className="text-text">{profile.min_edge_pct}%</div>
            </div>
            <div>
              <div className="text-muted">Max Stake</div>
              <div className="text-text">{profile.max_stake_pct}%</div>
            </div>
          </div>
        </Card>
      )}

      {/* Provider Balances */}
      {exposure && (
        <Card title="Provider Balances">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left text-xs">
                  <th className="pb-2 pr-4">Provider</th>
                  <th className="pb-2 pr-4 text-right">Balance</th>
                  <th className="pb-2 pr-4 text-right">Pending</th>
                  <th className="pb-2 pr-4 text-right">Available</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {exposure.providers.map(provider => (
                  <tr key={provider.provider_id} className="border-t border-border">
                    <td className="py-3 pr-4 text-text">{formatProviderName(provider.provider_name)}</td>
                    <td className="py-3 pr-4 text-right text-text">{provider.total_balance.toFixed(0)} kr</td>
                    <td className="py-3 pr-4 text-right text-muted">
                      {provider.pending_exposure.toFixed(0)} kr
                      {provider.pending_bets_count > 0 && (
                        <span className="text-xs ml-1">({provider.pending_bets_count})</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-right text-success">{provider.available.toFixed(0)} kr</td>
                    <td className="py-3">
                      {adjustingProvider === provider.provider_id ? (
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            value={adjustAmount}
                            onChange={(e) => setAdjustAmount(e.target.value)}
                            placeholder="Amount"
                            className="w-20 px-2 py-1 bg-panel2 border border-border rounded text-text text-xs"
                            autoFocus
                          />
                          <button
                            onClick={() => handleAdjust(provider.provider_id, true)}
                            className="px-2 py-1 text-xs bg-success/20 text-success rounded hover:bg-success/30"
                          >
                            +
                          </button>
                          <button
                            onClick={() => handleAdjust(provider.provider_id, false)}
                            className="px-2 py-1 text-xs bg-error/20 text-error rounded hover:bg-error/30"
                          >
                            -
                          </button>
                          <button
                            onClick={() => { setAdjustingProvider(null); setAdjustAmount(''); }}
                            className="px-2 py-1 text-xs text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setAdjustingProvider(provider.provider_id)}
                          className="px-2 py-1 text-xs text-tabBankroll hover:opacity-80"
                        >
                          Adjust
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
