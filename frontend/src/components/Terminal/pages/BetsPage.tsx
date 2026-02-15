import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import { FilterBar, SingleSelectPills, MultiSelectDropdown } from '../FilterBar';
import type { Bet } from '@/types';

export function BetsPage() {
  const [bets, setBets] = useState<Bet[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());

  // Expanded row
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const fetchBets = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getBets(undefined, 100);
      setBets(response.bets);
    } catch (err) {
      console.error('Failed to fetch bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBets();
  }, [fetchBets]);

  const settleBet = async (betId: number, result: 'won' | 'lost' | 'void') => {
    const bet = bets.find(b => b.id === betId);
    if (!bet) return;

    const payout = result === 'won' ? bet.stake * bet.odds : 0;
    try {
      await api.settleBet(betId, { result, payout });
      fetchBets();
    } catch (err) {
      console.error('Failed to settle bet:', err);
    }
  };

  // Derive available providers from data
  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    for (const bet of bets) {
      if (bet.provider) set.add(bet.provider);
    }
    return Array.from(set).sort();
  }, [bets]);

  const statusOptions = ['pending', 'won', 'lost', 'void'];

  // Apply filters
  const filtered = useMemo(() => {
    let result = bets;
    if (statusFilter) {
      result = result.filter(b => b.result === statusFilter);
    }
    if (selectedProviders.size > 0) {
      result = result.filter(b => selectedProviders.has(b.provider));
    }
    return result;
  }, [bets, statusFilter, selectedProviders]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
    setExpandedIdx(null);
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getStatusColor = (result: Bet['result']) => {
    switch (result) {
      case 'won': return 'text-success';
      case 'lost': return 'text-error';
      case 'void': return 'text-muted';
      default: return 'text-tabBets';
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

  // Summary stats
  const stats = {
    total: bets.length,
    pending: bets.filter(b => b.result === 'pending').length,
    totalStaked: bets.reduce((sum, b) => sum + b.stake, 0),
    totalProfit: bets.reduce((sum, b) => sum + b.profit, 0),
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabBets" />
          Bets
          <span className="text-muted text-sm font-normal ml-1">({filtered.length})</span>
        </h2>
        <div className="flex items-center gap-4 text-xs text-muted">
          <span>{stats.pending} pending</span>
          <span>{stats.totalStaked.toFixed(0)} kr staked</span>
          <span className={stats.totalProfit >= 0 ? 'text-success' : 'text-error'}>
            {stats.totalProfit >= 0 ? '+' : ''}{stats.totalProfit.toFixed(0)} kr
          </span>
        </div>
      </div>

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

      {/* Table */}
      {isLoading && bets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center bg-panel border border-border rounded-lg">
          {bets.length === 0 ? 'No bets found.' : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          {/* Column headers */}
          <div className="grid grid-cols-[90px_90px_1fr_65px_75px_80px_65px] gap-3 px-4 py-2 border-b border-border text-[11px] text-muted uppercase tracking-wider font-semibold">
            <div>Date</div>
            <div>Provider</div>
            <div>Outcome</div>
            <div className="text-right">Odds</div>
            <div className="text-right">Stake</div>
            <div className="text-right">Profit</div>
            <div className="text-right">Status</div>
          </div>

          {/* Rows */}
          <div className="divide-y divide-border/50">
            {filtered.map((bet, idx) => {
              const isExpanded = expandedIdx === idx;

              return (
                <div key={bet.id}>
                  {/* Main row */}
                  <div
                    className={`grid grid-cols-[90px_90px_1fr_65px_75px_80px_65px] gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm ${
                      isExpanded ? 'bg-tabBets/5' : 'hover:bg-panel2'
                    }`}
                    onClick={() => setExpandedIdx(isExpanded ? null : idx)}
                  >
                    {/* Date */}
                    <div className="flex items-center">
                      <span className="text-muted text-[11px]">{formatDate(bet.placed_at)}</span>
                    </div>

                    {/* Provider */}
                    <div className="flex items-center min-w-0">
                      <span className="text-text text-sm truncate">{formatProviderName(bet.provider)}</span>
                    </div>

                    {/* Outcome */}
                    <div className="flex items-center min-w-0">
                      <span className="text-text text-sm truncate">{resolveOutcome(bet)}</span>
                    </div>

                    {/* Odds */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm font-medium">{bet.odds.toFixed(2)}</span>
                    </div>

                    {/* Stake */}
                    <div className="flex items-center justify-end">
                      <span className="text-text text-sm">{bet.stake.toFixed(0)} kr</span>
                    </div>

                    {/* Profit */}
                    <div className="flex items-center justify-end">
                      <span className={`text-sm font-medium ${bet.profit >= 0 ? 'text-success' : 'text-error'}`}>
                        {bet.profit >= 0 ? '+' : ''}{bet.profit.toFixed(0)} kr
                      </span>
                    </div>

                    {/* Status */}
                    <div className="flex items-center justify-end">
                      <span className={`text-sm capitalize ${getStatusColor(bet.result)}`}>
                        {bet.result}
                      </span>
                    </div>
                  </div>

                  {/* Expanded: settle buttons for pending */}
                  {isExpanded && bet.result === 'pending' && (
                    <div
                      className="px-4 py-3 bg-panel2/50 border-t border-border/30"
                      onClick={e => e.stopPropagation()}
                    >
                      <div className="flex items-center justify-between gap-6">
                        <div className="flex items-center gap-6 text-sm text-muted">
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Market</span>
                            <span className="text-text">{bet.market || '-'}</span>
                          </div>
                          <div>
                            <span className="text-[10px] uppercase tracking-wider text-muted block">Potential</span>
                            <span className="text-text">{(bet.stake * bet.odds).toFixed(0)} kr</span>
                            <span className="text-tabBets text-xs ml-1">(+{(bet.stake * bet.odds - bet.stake).toFixed(0)})</span>
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => settleBet(bet.id, 'won')}
                            className="px-3 py-1.5 text-xs font-medium bg-success/20 text-success rounded hover:bg-success/30 transition-colors"
                          >
                            Won
                          </button>
                          <button
                            onClick={() => settleBet(bet.id, 'lost')}
                            className="px-3 py-1.5 text-xs font-medium bg-error/20 text-error rounded hover:bg-error/30 transition-colors"
                          >
                            Lost
                          </button>
                          <button
                            onClick={() => settleBet(bet.id, 'void')}
                            className="px-3 py-1.5 text-xs font-medium bg-panel2 text-muted rounded hover:bg-border transition-colors"
                          >
                            Void
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
