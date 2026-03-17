import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { CLVChart } from './BetsPage';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { BankrollStats, Bet, BonusProgressEntry, Provider, ProviderLimit } from '@/types';
import { ProviderName } from '../ProviderName';

export function StatsPage() {
  const queryClient = useQueryClient();
  const [limitForm, setLimitForm] = useState<{
    editingLimitId?: number;
    providerId: string;
    limitType: string;
    limitLevel: number;
    notes: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [mlTraining, setMlTraining] = useState(false);

  const { data: statsData, isLoading: statsLoading } = useQuery({
    queryKey: ['bankroll-stats'],
    queryFn: () => api.getBankrollStats(),
  });
  const stats: BankrollStats | null = statsData ?? null;

  const { data: betsData, isLoading: betsLoading } = useQuery({
    queryKey: ['bets', 'all'],
    queryFn: () => api.getBets(undefined, 500),
  });
  const bets: Bet[] = betsData?.bets ?? [];

  const { data: limitsData } = useQuery({
    queryKey: ['limits'],
    queryFn: () => api.getLimits(),
  });
  const limits: ProviderLimit[] = limitsData ?? [];

  const { data: providersData } = useQuery({
    queryKey: ['providers'],
    queryFn: () => api.getProviders(),
  });
  const providers: Provider[] = providersData?.providers ?? [];

  const { data: bonusStatus } = useQuery({
    queryKey: ['bankroll-status'],
    queryFn: () => api.getBankrollStatus().catch(() => null),
    staleTime: 60_000,
  });
  const activeBonuses: [string, BonusProgressEntry][] = bonusStatus
    ? Object.entries(bonusStatus.bonus_progress).filter(
        ([, b]) => ['trigger_needed', 'freebet_available', 'in_progress'].includes(b.status)
      )
    : [];

  const { data: extractionData } = useQuery({
    queryKey: ['extraction-analytics'],
    queryFn: () => api.getExtractionAnalytics().catch(() => null),
    staleTime: 300_000,
  });

  const { data: recommendationsData } = useQuery({
    queryKey: ['extraction-recommendations'],
    queryFn: () => api.getExtractionRecommendations().catch(() => []),
    staleTime: 300_000,
  });
  const recommendations: any[] = recommendationsData ?? [];

  const { data: mlStatus } = useQuery({
    queryKey: ['ml-status'],
    queryFn: () => api.getMlStatus().catch(() => null),
    staleTime: 300_000,
  });

  const isLoading = statsLoading || betsLoading;

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
        const prov = providers.find(p => p.id === providerId);

        return {
          providerId,
          totalBets: provBets.length,
          totalStake,
          totalProfit,
          roi: totalStake > 0 ? (totalProfit / totalStake) * 100 : 0,
          winRate: settled.length > 0 ? wins / settled.length : null,
          avgClv,
          limits: provLimits,
          limitRisk: prov?.limit_risk || 'low',
        };
      })
      .sort((a, b) => b.totalBets - a.totalBets);
  })();

  const handleSubmitLimit = async () => {
    if (!limitForm) return;
    setSaving(true);
    try {
      if (limitForm.editingLimitId) {
        await api.updateLimit(limitForm.editingLimitId, {
          limit_level: limitForm.limitLevel,
          notes: limitForm.notes || undefined,
        });
      } else {
        await api.createLimit({
          provider_id: limitForm.providerId,
          limit_type: limitForm.limitType,
          limit_level: limitForm.limitLevel,
          notes: limitForm.notes || undefined,
        });
      }
      setLimitForm(null);
      queryClient.invalidateQueries({ queryKey: ['limits'] });
    } catch (err) {
      console.error('Failed to save limit:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteLimit = async (id: number) => {
    try {
      await api.deleteLimit(id);
      queryClient.invalidateQueries({ queryKey: ['limits'] });
    } catch (err) {
      console.error('Failed to delete limit:', err);
    }
  };

  const LIMIT_LEVEL_LABELS: Record<number, string> = {
    1: 'Minor', 2: 'Moderate', 3: 'Severe', 4: 'Gutted', 5: 'Closed',
  };

  const RISK_COLORS: Record<string, string> = {
    low: 'text-success',
    medium: 'text-warning',
    high: 'text-orange-400',
    instant: 'text-error',
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
              <td className="text-right text-muted">
                {stats.win_rate.toFixed(1)}% win rate
              </td>
              <td className="text-right">
                <div className="flex flex-col items-end gap-0.5">
                  <span className="text-muted text-xs">{stats.total_staked.toFixed(0)} kr staked</span>
                  {stats.total_deposited > 0 && (
                    <span className="text-muted text-xs">{stats.net_deposited.toFixed(0)} kr net deposited</span>
                  )}
                </div>
              </td>
            </tr>
            {(stats.freebet_profit > 0 || stats.bonus_profit > 0) && (
              <tr>
                <td colSpan={3}>
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-muted">Profit breakdown:</span>
                    <span className="text-text">{stats.bet_profit.toFixed(0)} kr bets</span>
                    {stats.freebet_profit > 0 && (
                      <span className="text-accent">+{stats.freebet_profit.toFixed(0)} kr freebets</span>
                    )}
                    {stats.bonus_profit > 0 && (
                      <span className="text-tabBonus">+{stats.bonus_profit.toFixed(0)} kr bonuses</span>
                    )}
                  </div>
                </td>
              </tr>
            )}
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

      {/* Active Wagering Window */}
      {activeBonuses.length > 0 && (
        <div className="border-l-2 border-tabBonus">
          <table className="sq">
            <thead>
              <tr>
                <th>Wagering</th>
                <th>Type</th>
                <th className="text-right">Progress</th>
                <th className="text-right">Remaining</th>
                <th className="text-right">Kr/wk</th>
                <th className="text-right">Deadline</th>
                <th className="text-right">ETA</th>
              </tr>
            </thead>
            <tbody>
              {activeBonuses.map(([providerId, bonus]) => {
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;
                const remaining = bonus.wagering_requirement - bonus.wagered_amount;
                const hasProgress = (bonus.status === 'in_progress' || (bonus.status === 'trigger_needed' && bonus.bonus_type === 'bonusdeposit')) && bonus.wagering_requirement > 0;
                const estDays = bonus.prognosis?.est_weeks != null ? Math.round(bonus.prognosis.est_weeks * 7) : null;
                const requiredPerWk = bonus.prognosis?.required_weekly_wagering ?? null;

                return (
                  <tr key={providerId}>
                    <td className="text-text text-sm font-medium"><ProviderName name={providerId} /></td>
                    <td>
                      <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                        bonus.status === 'freebet_available' ? 'bg-success/15 text-success' :
                        'bg-tabBonus/15 text-tabBonus'
                      }`}>
                        {bonus.bonus_type === 'freebet' ? 'FREEBET'
                          : bonus.status === 'trigger_needed' ? 'TRIGGER'
                          : 'WAGER'}
                      </span>
                    </td>
                    <td className="text-right">
                      {hasProgress ? (
                        <div className="flex items-center gap-2 justify-end">
                          <div className="w-16 h-1.5 bg-bg overflow-hidden">
                            <div
                              className={`h-full ${urgent ? 'bg-error' : warning ? 'bg-amber-400' : 'bg-tabBonus'}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-muted2">{pct.toFixed(0)}%</span>
                        </div>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right text-sm text-text">
                      {hasProgress ? `${remaining.toFixed(0)} kr` : '-'}
                    </td>
                    <td className="text-right">
                      {requiredPerWk != null && requiredPerWk > 0 ? (
                        <span className="text-sm text-text">{requiredPerWk.toFixed(0)}</span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right">
                      {days !== null ? (
                        <span className={`text-sm ${urgent ? 'text-error font-medium' : warning ? 'text-amber-400' : 'text-muted'}`}>
                          {days}d
                        </span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                    <td className="text-right">
                      {estDays !== null ? (
                        <span className={`text-sm ${days !== null && estDays <= days ? 'text-success' : 'text-error'}`}>
                          ~{estDays}d
                        </span>
                      ) : <span className="text-muted text-sm">-</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Provider Stats */}
      {providerStats.length > 0 && (
        <div className="border-l-2 border-tabStats">
          <table className="sq">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Risk</th>
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
                  <td className={`text-xs ${RISK_COLORS[ps.limitRisk] || 'text-muted'}`}>
                    {ps.limitRisk}
                  </td>
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
                      <span className="text-error text-xs flex gap-1 justify-end">
                        {ps.limits.map(l => (
                          <button
                            key={l.id}
                            onClick={() => setLimitForm({
                              editingLimitId: l.id,
                              providerId: ps.providerId,
                              limitType: l.limit_type,
                              limitLevel: l.limit_level,
                              notes: l.notes || '',
                            })}
                            className="hover:underline cursor-pointer"
                            title={`${l.limit_type} — ${l.notes || 'click to edit'}\nSnapshot: ${l.betting_snapshot?.total_bets ?? 0} bets`}
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

      {(extractionData?.provider_roi?.length ?? 0) > 0 && (
        <>
          <h3 className="text-sm font-bold mt-4 mb-2 text-[var(--text-primary)]">
            Extraction Provider ROI
          </h3>
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Provider</th>
                <th className="text-right">Opps</th>
                <th className="text-right">Edge%</th>
                <th className="text-right">Bets</th>
                <th className="text-right">Win%</th>
                <th className="text-right">P&L</th>
              </tr>
            </thead>
            <tbody>
              {extractionData!.provider_roi!.map((r: any) => (
                <tr key={r.provider_id}>
                  <td>{r.provider_id}</td>
                  <td className="text-right">{r.total_opportunities}</td>
                  <td className="text-right">{r.avg_edge.toFixed(1)}%</td>
                  <td className="text-right">{r.total_bets}</td>
                  <td className="text-right">
                    {r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : '-'}
                  </td>
                  <td className={`text-right ${r.net_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {r.net_pnl >= 0 ? '+' : ''}{r.net_pnl.toFixed(0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {recommendations.length > 0 && (
        <>
          <h3 className="text-sm font-bold mt-4 mb-2 text-[var(--text-primary)]">
            Recommendations
          </h3>
          <div className="space-y-1">
            {recommendations.map((r: any) => (
              <div key={r.id} className={`text-xs px-2 py-1 rounded ${
                r.severity === 'critical' ? 'bg-red-900/30 text-red-300' :
                r.severity === 'warning' ? 'bg-yellow-900/30 text-yellow-300' :
                'bg-blue-900/30 text-blue-300'
              }`}>
                <span className="font-mono mr-2">
                  {r.severity === 'critical' ? '!' : r.severity === 'warning' ? '~' : '+'}
                </span>
                {r.message}
              </div>
            ))}
          </div>
        </>
      )}

      {/* ML Models */}
      {mlStatus && Object.keys(mlStatus).length > 0 && (
        <>
          <h3 className="text-sm font-bold mt-4 mb-2 text-[var(--text-primary)] flex items-center gap-2">
            ML Models
            <button
              onClick={async () => {
                setMlTraining(true);
                try {
                  await api.triggerMlTraining();
                  queryClient.invalidateQueries({ queryKey: ['ml-status'] });
                } catch (e) {
                  console.error('ML training failed:', e);
                } finally {
                  setMlTraining(false);
                }
              }}
              disabled={mlTraining}
              className="text-xs px-2 py-0.5 bg-tabStats/20 text-tabStats hover:bg-tabStats/30 rounded"
            >
              {mlTraining ? 'Training...' : 'Train'}
            </button>
          </h3>
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Model</th>
                <th className="text-center">Status</th>
                <th className="text-right">Min Samples</th>
                <th className="text-center">Data Ready</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(mlStatus).map(([name, info]) => (
                <tr key={name}>
                  <td className="text-text font-mono text-xs">{name}</td>
                  <td className="text-center">
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      info.loaded ? 'bg-success/20 text-success' : 'bg-panel2 text-muted'
                    }`}>
                      {info.loaded ? 'loaded' : 'idle'}
                    </span>
                  </td>
                  <td className="text-right text-muted text-xs">{info.min_samples}</td>
                  <td className="text-center">
                    <span className={info.data_ready ? 'text-success' : 'text-muted'}>
                      {info.data_ready ? 'yes' : 'no'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Limit Form Modal */}
      {limitForm && (
        <div className="border-l-2 border-error p-3 space-y-2">
          <div className="text-sm text-text font-medium">
            {limitForm.editingLimitId ? `Edit limit for ${limitForm.providerId}` : `Mark ${limitForm.providerId} as limited`}
          </div>
          <div className="flex gap-2 items-center flex-wrap">
            <select
              value={limitForm.limitType}
              onChange={e => setLimitForm({ ...limitForm, limitType: e.target.value })}
              disabled={!!limitForm.editingLimitId}
              className="bg-panel2 text-text text-xs px-2 py-1 rounded border border-border disabled:opacity-50"
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
              onClick={handleSubmitLimit}
              disabled={saving}
              className="text-xs px-3 py-1 bg-error/20 text-error hover:bg-error/30 rounded"
            >
              {saving ? 'Saving...' : limitForm.editingLimitId ? 'Update' : 'Confirm'}
            </button>
            {limitForm.editingLimitId && (
              <button
                onClick={() => { handleDeleteLimit(limitForm.editingLimitId!); setLimitForm(null); }}
                className="text-xs px-2 py-1 text-error hover:text-error/80"
              >
                Delete
              </button>
            )}
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
