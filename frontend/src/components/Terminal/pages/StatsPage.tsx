import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { CLVChart } from './BetsPage';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { BankrollStats, Bet, ProviderLimit } from '@/types';

export function StatsPage() {
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [bets, setBets] = useState<Bet[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [limits, setLimits] = useState<ProviderLimit[]>([]);
  const [limitForm, setLimitForm] = useState<{
    providerId: string;
    limitType: string;
    limitLevel: number;
    notes: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statsData, betsData, limitsData] = await Promise.all([
        api.getBankrollStats(),
        api.getBets(undefined, 500),
        api.getLimits(),
      ]);
      setStats(statsData);
      setBets(betsData.bets);
      setLimits(limitsData);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useRefreshOnExtraction(fetchData);

  // Compute per-provider stats from bets
  const providerStats = (() => {
    const grouped: Record<string, Bet[]> = {};
    for (const bet of bets) {
      const pid = bet.provider;
      if (!grouped[pid]) grouped[pid] = [];
      grouped[pid].push(bet);
    }

    return Object.entries(grouped)
      .map(([providerId, provBets]) => {
        const totalStake = provBets.reduce((s, b) => s + b.stake, 0);
        const totalProfit = provBets.reduce((s, b) => s + b.profit, 0);
        const settled = provBets.filter(b => b.result === 'won' || b.result === 'lost');
        const wins = settled.filter(b => b.result === 'won').length;
        const clvBets = provBets.filter(b => b.clv_pct != null);
        const avgClv = clvBets.length > 0
          ? clvBets.reduce((s, b) => s + (b.clv_pct ?? 0), 0) / clvBets.length
          : null;
        const provLimits = limits.filter(l => l.provider_id === providerId);

        return {
          providerId,
          totalBets: provBets.length,
          totalStake,
          totalProfit,
          roi: totalStake > 0 ? (totalProfit / totalStake) * 100 : 0,
          winRate: settled.length > 0 ? wins / settled.length : null,
          avgClv,
          limits: provLimits,
        };
      })
      .sort((a, b) => b.totalBets - a.totalBets);
  })();

  const handleMarkLimited = async () => {
    if (!limitForm) return;
    setSaving(true);
    try {
      await api.createLimit({
        provider_id: limitForm.providerId,
        limit_type: limitForm.limitType,
        limit_level: limitForm.limitLevel,
        notes: limitForm.notes || undefined,
      });
      setLimitForm(null);
      fetchData();
    } catch (err) {
      console.error('Failed to create limit:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteLimit = async (id: number) => {
    try {
      await api.deleteLimit(id);
      fetchData();
    } catch (err) {
      console.error('Failed to delete limit:', err);
    }
  };

  const LIMIT_LEVEL_LABELS: Record<number, string> = {
    1: 'Minor', 2: 'Moderate', 3: 'Severe', 4: 'Gutted', 5: 'Closed',
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="stats" color={TAB_COLORS.stats} size={16} />
          Statistics
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabStats" />
        Statistics
      </h2>

      {/* Betting Stats */}
      {stats && (
        <div className="border-l-2 border-tabStats">
        <table className="sq">
          <thead>
            <tr>
              <th>Total Bets</th>
              <th className="text-right">ROI</th>
              <th className="text-right">Total Profit</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="text-text text-2xl font-semibold">{stats.total_bets}</td>
              <td className={`text-right text-2xl font-semibold ${stats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.roi_pct >= 0 ? '+' : ''}{stats.roi_pct.toFixed(1)}%
              </td>
              <td className={`text-right text-2xl font-semibold ${stats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.total_profit >= 0 ? '+' : ''}{stats.total_profit.toFixed(0)} kr
              </td>
            </tr>
            <tr>
              <td>
                <div className="flex items-center gap-4">
                  <span className="text-success font-medium">{stats.wins} W</span>
                  <span className="text-error font-medium">{stats.losses} L</span>
                  <span className="text-muted font-medium">{stats.voids} V</span>
                </div>
              </td>
              <td colSpan={2}></td>
            </tr>
          </tbody>
        </table>
        </div>
      )}

      {/* CLV Stats */}
      {stats && stats.clv_count > 0 && (
        <div className="border-l-2 border-tabStats">
          <table className="sq">
            <thead>
              <tr>
                <th>Avg CLV</th>
                <th className="text-right">+CLV Rate</th>
                <th className="text-right">CLV Bets</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={`text-2xl font-semibold ${stats.avg_clv >= 0 ? 'text-success' : 'text-error'}`}>
                  {stats.avg_clv >= 0 ? '+' : ''}{stats.avg_clv.toFixed(1)}%
                </td>
                <td className="text-right text-text text-2xl font-semibold">
                  {stats.clv_positive_pct.toFixed(0)}%
                </td>
                <td className="text-right text-muted text-2xl font-semibold">
                  {stats.clv_count}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* CLV Trend Chart */}
      <CLVChart bets={bets} />

      {/* Provider Stats */}
      {providerStats.length > 0 && (
        <div className="border-l-2 border-tabStats">
          <table className="sq">
            <thead>
              <tr>
                <th>Provider</th>
                <th className="text-right">Bets</th>
                <th className="text-right">Stake</th>
                <th className="text-right">Profit</th>
                <th className="text-right">ROI</th>
                <th className="text-right">CLV</th>
                <th className="text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {providerStats.map(ps => (
                <tr key={ps.providerId}>
                  <td className="text-text">{ps.providerId}</td>
                  <td className="text-right text-muted">{ps.totalBets}</td>
                  <td className="text-right text-muted">{ps.totalStake.toFixed(0)}</td>
                  <td className={`text-right ${ps.totalProfit >= 0 ? 'text-success' : 'text-error'}`}>
                    {ps.totalProfit >= 0 ? '+' : ''}{ps.totalProfit.toFixed(0)}
                  </td>
                  <td className={`text-right ${ps.roi >= 0 ? 'text-success' : 'text-error'}`}>
                    {ps.roi >= 0 ? '+' : ''}{ps.roi.toFixed(1)}%
                  </td>
                  <td className="text-right text-muted">
                    {ps.avgClv != null ? `${ps.avgClv >= 0 ? '+' : ''}${ps.avgClv.toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right">
                    {ps.limits.length > 0 ? (
                      <span className="text-error text-xs">
                        {ps.limits.map(l => (
                          <button
                            key={l.id}
                            onClick={() => handleDeleteLimit(l.id)}
                            className="hover:line-through cursor-pointer"
                            title={`${l.limit_type} — ${l.notes || 'click to remove'}\nSnapshot: ${l.betting_snapshot?.total_bets ?? 0} bets`}
                          >
                            {LIMIT_LEVEL_LABELS[l.limit_level] || l.limit_level}/5
                          </button>
                        ))}
                      </span>
                    ) : (
                      <button
                        onClick={() => setLimitForm({
                          providerId: ps.providerId,
                          limitType: 'stake_limited',
                          limitLevel: 3,
                          notes: '',
                        })}
                        className="text-xs text-muted hover:text-text"
                      >
                        Mark Limited
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Limit Form Modal */}
      {limitForm && (
        <div className="border-l-2 border-error p-3 space-y-2">
          <div className="text-sm text-text font-medium">
            Mark {limitForm.providerId} as limited
          </div>
          <div className="flex gap-2 items-center flex-wrap">
            <select
              value={limitForm.limitType}
              onChange={e => setLimitForm({ ...limitForm, limitType: e.target.value })}
              className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border"
            >
              <option value="stake_limited">Stake Limited</option>
              <option value="market_restricted">Market Restricted</option>
              <option value="odds_restricted">Odds Restricted</option>
              <option value="fully_banned">Fully Banned</option>
            </select>
            <select
              value={limitForm.limitLevel}
              onChange={e => setLimitForm({ ...limitForm, limitLevel: Number(e.target.value) })}
              className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border"
            >
              {[1, 2, 3, 4, 5].map(n => (
                <option key={n} value={n}>{n} — {LIMIT_LEVEL_LABELS[n]}</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Notes (optional)"
              value={limitForm.notes}
              onChange={e => setLimitForm({ ...limitForm, notes: e.target.value })}
              className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border flex-1 min-w-[150px]"
            />
            <button
              onClick={handleMarkLimited}
              disabled={saving}
              className="text-xs px-3 py-1 bg-error/20 text-error hover:bg-error/30 rounded"
            >
              {saving ? 'Saving...' : 'Confirm'}
            </button>
            <button
              onClick={() => setLimitForm(null)}
              className="text-xs px-2 py-1 text-muted hover:text-text"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
