import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { FilterBar, SingleSelectPills, MultiSelectDropdown } from '../FilterBar';
import type { Bet, BankrollStats, BonusProgressEntry } from '@/types';

function BankrollChart({ bets, currentBankroll }: { bets: Bet[]; currentBankroll: number }) {
  const data = useMemo(() => {
    // Only settled bets, sorted by date
    const settled = bets
      .filter(b => b.result !== 'pending')
      .sort((a, b) => new Date(a.placed_at).getTime() - new Date(b.placed_at).getTime());

    if (settled.length === 0) return [];

    // Starting bankroll = current - total profit from settled bets
    const totalProfit = settled.reduce((sum, b) => sum + b.profit, 0);
    const startBankroll = currentBankroll - totalProfit;

    let cumulative = startBankroll;
    const points = [{ date: new Date(settled[0].placed_at), value: startBankroll }];
    for (const bet of settled) {
      cumulative += bet.profit;
      points.push({ date: new Date(bet.placed_at), value: cumulative });
    }
    return points;
  }, [bets, currentBankroll]);

  if (data.length < 2) return null;

  const W = 600;
  const H = 140;
  const PX = 40; // left padding for labels
  const PR = 12; // right padding
  const PY = 16;

  const minVal = Math.min(...data.map(d => d.value));
  const maxVal = Math.max(...data.map(d => d.value));
  const range = maxVal - minVal || 1;
  const minDate = data[0].date.getTime();
  const maxDate = data[data.length - 1].date.getTime();
  const dateRange = maxDate - minDate || 1;

  const x = (d: Date) => PX + (d.getTime() - minDate) / dateRange * (W - PX - PR);
  const y = (v: number) => PY + (1 - (v - minVal) / range) * (H - PY * 2);

  const pathD = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');

  const lastVal = data[data.length - 1].value;
  const firstVal = data[0].value;
  const isUp = lastVal >= firstVal;
  const stroke = isUp ? '#10b981' : '#ef4444';

  // Y-axis labels
  const yLabels = [minVal, (minVal + maxVal) / 2, maxVal].map(v => ({
    value: v,
    label: `${(v / 1000).toFixed(1)}k`,
    yPos: y(v),
  }));

  return (
    <div className="border border-border bg-panel">
      <div className="px-3 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] text-muted uppercase tracking-wider">Bankroll</span>
        <span className={`text-sm font-semibold ${isUp ? 'text-success' : 'text-error'}`}>
          {lastVal.toFixed(0)} kr
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none">
        {/* Grid lines */}
        {yLabels.map((l, i) => (
          <line key={i} x1={PX} y1={l.yPos} x2={W - PR} y2={l.yPos} stroke="currentColor" className="text-border" strokeWidth="0.5" />
        ))}
        {/* Y labels */}
        {yLabels.map((l, i) => (
          <text key={`t${i}`} x={PX - 4} y={l.yPos + 3} textAnchor="end" fill="currentColor" className="text-muted2" fontSize="9">{l.label}</text>
        ))}
        {/* Area fill */}
        <path
          d={`${pathD} L${x(data[data.length - 1].date).toFixed(1)},${(H - PY).toFixed(1)} L${x(data[0].date).toFixed(1)},${(H - PY).toFixed(1)} Z`}
          fill={stroke}
          fillOpacity="0.08"
        />
        {/* Line */}
        <path d={pathD} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
        {/* Endpoint dot */}
        <circle cx={x(data[data.length - 1].date)} cy={y(lastVal)} r="2.5" fill={stroke} />
      </svg>
    </div>
  );
}

export function BetsPage() {
  const [bets, setBets] = useState<Bet[]>([]);
  const [bankrollStats, setBankrollStats] = useState<BankrollStats | null>(null);
  const [currentBankroll, setCurrentBankroll] = useState<number>(0);
  const [isLoading, setIsLoading] = useState(true);
  const [activeBonuses, setActiveBonuses] = useState<[string, BonusProgressEntry][]>([]);

  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const fetchBets = useCallback(async () => {
    setIsLoading(true);
    try {
      const [response, bankroll] = await Promise.all([
        api.getBets(undefined, 500),
        api.getBankroll(),
      ]);
      setBets(response.bets);
      setCurrentBankroll(bankroll.total);
    } catch (err) {
      console.error('Failed to fetch bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const statsData = await api.getBankrollStats();
      setBankrollStats(statsData);
    } catch {
      // Stats are supplementary
    }
  }, []);

  const fetchBonuses = useCallback(async () => {
    try {
      const status = await api.getBankrollStatus();
      const active = Object.entries(status.bonus_progress).filter(
        ([, b]) => ['trigger_needed', 'freebet_available', 'in_progress'].includes(b.status)
      );
      setActiveBonuses(active);
    } catch {
      // Silently ignore — bonus section is supplementary
    }
  }, []);

  useEffect(() => { fetchBets(); fetchStats(); fetchBonuses(); }, [fetchBets, fetchStats, fetchBonuses]);

  const settleBet = async (betId: number, result: 'won' | 'lost' | 'void') => {
    const bet = bets.find(b => b.id === betId);
    if (!bet) return;
    const payout = result === 'won' ? bet.stake * bet.odds : 0;
    try {
      await api.settleBet(betId, { result, payout });
      fetchBets();
      fetchStats();
    } catch (err) {
      console.error('Failed to settle bet:', err);
    }
  };

  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const bet of bets) { if (bet.provider) set.add(bet.provider); }
    return Array.from(set).sort();
  }, [bets]);

  const statusOptions = ['pending', 'won', 'lost', 'void'];

  const filtered = useMemo(() => {
    let result = bets;
    if (statusFilter) result = result.filter(b => b.result === statusFilter);
    if (selectedProviders.size > 0) result = result.filter(b => selectedProviders.has(b.provider));
    return result;
  }, [bets, statusFilter, selectedProviders]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
    setExpandedIdx(null);
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const getStatusColor = (result: Bet['result']) => {
    switch (result) {
      case 'won': return 'text-success';
      case 'lost': return 'text-error';
      case 'void': return 'text-muted';
      default: return 'text-accent';
    }
  };

  const resolveOutcome = (bet: Bet): string => {
    const outcome = bet.outcome || '-';
    if (outcome === 'home' && bet.home_team) return bet.home_team;
    if (outcome === 'away' && bet.away_team) return bet.away_team;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return 'Over';
    if (outcome === 'under') return 'Under';
    return outcome;
  };

  const handleBonusAction = async (providerId: string, action: 'trigger_settled' | 'freebet_used') => {
    try {
      await api.bonusTransition(providerId, action);
      fetchBonuses();
      fetchStats();
    } catch (err) {
      console.error('Bonus transition failed:', err);
    }
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 bg-tabBets" />
        Bets
      </h2>

      {/* Stats Summary */}
      {bankrollStats && (
        <div className="border-l-2 border-tabBets">
          <div className="grid grid-cols-4 gap-px bg-border border border-border">
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Bets</div>
              <div className="text-text text-lg font-semibold">{bankrollStats.total_bets}</div>
              <div className="flex items-center gap-2 text-[10px]">
                <span className="text-success">{bankrollStats.wins}W</span>
                <span className="text-error">{bankrollStats.losses}L</span>
                <span className="text-muted">{bankrollStats.voids}V</span>
              </div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Win Rate</div>
              <div className="text-text text-lg font-semibold">{bankrollStats.win_rate.toFixed(1)}%</div>
              <div className="text-[10px] text-muted">{bankrollStats.total_staked.toFixed(0)} kr staked</div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">ROI</div>
              <div className={`text-lg font-semibold ${bankrollStats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
                {bankrollStats.roi_pct >= 0 ? '+' : ''}{bankrollStats.roi_pct.toFixed(1)}%
              </div>
            </div>
            <div className="bg-panel2 px-3 py-2.5">
              <div className="text-[10px] text-muted uppercase tracking-wider mb-0.5">Profit</div>
              <div className={`text-lg font-semibold ${bankrollStats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
                {bankrollStats.total_profit >= 0 ? '+' : ''}{bankrollStats.total_profit.toFixed(0)} kr
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Bankroll Chart */}
      {bets.length > 0 && currentBankroll > 0 && (
        <BankrollChart bets={bets} currentBankroll={currentBankroll} />
      )}

      {/* Active Bonuses */}
      {activeBonuses.length > 0 && (
        <div className="border-l-2 border-tabBonus">
          <div className="border border-border">
            <div className="px-3 py-2 border-b border-border bg-panel">
              <h3 className="text-muted font-semibold text-xs uppercase tracking-wider">Active Bonuses</h3>
            </div>
            <div className="divide-y divide-border">
              {activeBonuses.map(([providerId, bonus]) => {
                const pct = Math.min(100, bonus.progress_pct);
                const days = bonus.days_remaining;
                const urgent = days !== null && days <= 10;
                const warning = days !== null && days > 10 && days <= 30;

                return (
                  <div key={providerId} className="px-3 py-2.5 space-y-1.5">
                    {/* Header: provider + status + action */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-text text-sm font-medium">{formatProviderName(providerId)}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 font-medium ${
                          bonus.status === 'trigger_needed' ? 'bg-amber-400/15 text-amber-400' :
                          bonus.status === 'freebet_available' ? 'bg-success/15 text-success' :
                          'bg-tabBonus/15 text-tabBonus'
                        }`}>
                          {bonus.status === 'trigger_needed' ? 'TRIGGER NEEDED' :
                           bonus.status === 'freebet_available' ? 'FREEBET READY' :
                           `${pct.toFixed(0)}%`}
                        </span>
                        {days !== null && (
                          <span className={`text-[10px] font-mono ${urgent ? 'text-error' : warning ? 'text-amber-400' : 'text-muted'}`}>
                            {days}d left
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {bonus.status === 'trigger_needed' && (
                          <button
                            onClick={() => handleBonusAction(providerId, 'trigger_settled')}
                            className="px-2 py-1 text-[10px] font-medium bg-amber-400/20 text-amber-400 hover:bg-amber-400/30 transition-colors"
                          >
                            Mark Settled
                          </button>
                        )}
                        {bonus.status === 'freebet_available' && (
                          <button
                            onClick={() => handleBonusAction(providerId, 'freebet_used')}
                            className="px-2 py-1 text-[10px] font-medium bg-success/20 text-success hover:bg-success/30 transition-colors"
                          >
                            Mark Used
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Action text */}
                    <div className="text-xs text-muted">{bonus.action_needed}</div>

                    {/* Progress bar for in_progress wagering */}
                    {bonus.status === 'in_progress' && bonus.wagering_requirement > 0 && (
                      <div className="space-y-1">
                        <div className="h-1.5 bg-panel overflow-hidden">
                          <div
                            className={`h-full transition-all duration-500 ${
                              urgent ? 'bg-error' : warning ? 'bg-amber-400' : 'bg-tabBonus'
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <div className="flex items-center justify-between text-[10px] text-muted2">
                          <span>{bonus.wagered_amount.toFixed(0)} / {bonus.wagering_requirement.toFixed(0)} kr</span>
                          <span>{(bonus.wagering_requirement - bonus.wagered_amount).toFixed(0)} kr remaining</span>
                        </div>
                      </div>
                    )}

                    {/* Pace info */}
                    {bonus.prognosis && bonus.status === 'in_progress' && (() => {
                      const p = bonus.prognosis;
                      const betsPlaced = p.bets_per_week;
                      const needBetsWk = p.required_weekly_wagering > 0 && p.avg_stake > 0
                        ? Math.ceil(p.required_weekly_wagering / p.avg_stake)
                        : null;
                      const estDays = p.est_weeks !== null ? Math.round(p.est_weeks * 7) : null;
                      const onTrack = p.required_weekly_wagering > 0 && p.weekly_wagering >= p.required_weekly_wagering;

                      return (
                        <div className="flex items-center gap-3 text-[10px]">
                          {needBetsWk !== null ? (
                            <span className={onTrack ? 'text-success' : 'text-amber-400'}>
                              {Math.round(betsPlaced)}/{needBetsWk} bets/wk
                            </span>
                          ) : betsPlaced > 0 ? (
                            <span className="text-muted2">{Math.round(betsPlaced)} bets/wk</span>
                          ) : (
                            <span className="text-muted2">No qualifying bets yet</span>
                          )}
                          {p.avg_stake > 0 && (
                            <span className="text-muted2">{p.avg_stake} kr avg</span>
                          )}
                          {estDays !== null && (
                            <span className="text-muted2">~{estDays}d to clear</span>
                          )}
                          {p.required_weekly_wagering > 0 && !onTrack && (
                            <span className="text-amber-400">need {p.required_weekly_wagering} kr/wk</span>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <FilterBar>
        <SingleSelectPills
          label="Status"
          options={statusOptions}
          active={statusFilter}
          onSelect={(v) => { setStatusFilter(v); setExpandedIdx(null); }}
          format={(v) => v.charAt(0).toUpperCase() + v.slice(1)}
          accentColor="tabBets"
        />
        {availableProviders.length > 1 && (
          <>
            <div className="w-px h-5 bg-border/50" />
            <MultiSelectDropdown
              label="Provider"
              options={availableProviders}
              selected={selectedProviders}
              onToggle={toggleProvider}
              onClear={() => { setSelectedProviders(new Set()); setExpandedIdx(null); }}
              format={formatProviderName}
              accentColor="tabBets"
            />
          </>
        )}
      </FilterBar>

      {/* Bet Table */}
      {isLoading && bets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : filtered.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {bets.length === 0 ? 'No bets found.' : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="border-l-2 border-tabBets">
        <table className="sq">
          <thead>
            <tr>
              <th>Date</th>
              <th>Provider</th>
              <th>Outcome</th>
              <th className="text-right">Odds</th>
              <th className="text-right">Stake</th>
              <th className="text-right">Profit</th>
              <th className="text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((bet, idx) => {
              const isExpanded = expandedIdx === idx;
              return (
                <>
                  <tr
                    key={bet.id}
                    className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                    onClick={() => setExpandedIdx(isExpanded ? null : idx)}
                  >
                    <td className="text-muted text-[11px] whitespace-nowrap">{formatDate(bet.placed_at)}</td>
                    <td className="text-text text-sm">{formatProviderName(bet.provider)}</td>
                    <td className="text-text text-sm">{resolveOutcome(bet)}</td>
                    <td className="text-right text-text text-sm font-medium">{bet.odds.toFixed(2)}</td>
                    <td className="text-right text-text text-sm">{bet.stake.toFixed(0)} kr</td>
                    <td className="text-right">
                      <span className={`text-sm font-medium ${bet.profit >= 0 ? 'text-success' : 'text-error'}`}>
                        {bet.profit >= 0 ? '+' : ''}{bet.profit.toFixed(0)} kr
                      </span>
                    </td>
                    <td className="text-right">
                      <span className={`text-sm capitalize ${getStatusColor(bet.result)}`}>{bet.result}</span>
                    </td>
                  </tr>
                  {isExpanded && bet.result === 'pending' && (
                    <tr key={`${bet.id}-expanded`}>
                      <td colSpan={7} className="!p-0" onClick={e => e.stopPropagation()}>
                        <div className="px-3 py-2 bg-panel flex items-center justify-between gap-6">
                          <div className="flex items-center gap-6 text-xs text-muted">
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Market: </span>
                              <span className="text-text">{bet.market || '-'}</span>
                            </div>
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Potential: </span>
                              <span className="text-text">{(bet.stake * bet.odds).toFixed(0)} kr</span>
                              <span className="text-accent text-xs ml-1">(+{(bet.stake * bet.odds - bet.stake).toFixed(0)})</span>
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <button onClick={() => settleBet(bet.id, 'won')} className="px-3 py-1.5 text-xs font-medium bg-success/20 text-success hover:bg-success/30 transition-colors">Won</button>
                            <button onClick={() => settleBet(bet.id, 'lost')} className="px-3 py-1.5 text-xs font-medium bg-error/20 text-error hover:bg-error/30 transition-colors">Lost</button>
                            <button onClick={() => settleBet(bet.id, 'void')} className="px-3 py-1.5 text-xs font-medium bg-panel2 text-muted hover:bg-border transition-colors">Void</button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
