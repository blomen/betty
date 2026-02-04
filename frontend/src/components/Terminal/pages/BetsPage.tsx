import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { formatProviderName } from '@/utils/formatters';
import type { Bet } from '@/types';

type BetStatus = 'pending' | 'won' | 'lost' | 'void' | undefined;

export function BetsPage() {
  const [bets, setBets] = useState<Bet[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<BetStatus>(undefined);

  const fetchBets = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await api.getBets(statusFilter, 100);
      setBets(response.bets);
    } catch (err) {
      console.error('Failed to fetch bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter]);

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

  const statusOptions: { value: BetStatus; label: string }[] = [
    { value: undefined, label: 'All' },
    { value: 'pending', label: 'Pending' },
    { value: 'won', label: 'Won' },
    { value: 'lost', label: 'Lost' },
    { value: 'void', label: 'Void' },
  ];

  // Summary stats
  const stats = {
    total: bets.length,
    pending: bets.filter(b => b.result === 'pending').length,
    totalStaked: bets.reduce((sum, b) => sum + b.stake, 0),
    totalProfit: bets.reduce((sum, b) => sum + b.profit, 0),
  };

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabBets" />
        Bets
      </h2>

      {/* Summary */}
      <Card title="Summary">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <div className="text-muted">Total Bets</div>
            <div className="text-text text-lg font-medium">{stats.total}</div>
          </div>
          <div>
            <div className="text-muted">Pending</div>
            <div className="text-tabBets text-lg font-medium">{stats.pending}</div>
          </div>
          <div>
            <div className="text-muted">Total Staked</div>
            <div className="text-text text-lg font-medium">{stats.totalStaked.toFixed(0)} kr</div>
          </div>
          <div>
            <div className="text-muted">Profit/Loss</div>
            <div className={`text-lg font-medium ${stats.totalProfit >= 0 ? 'text-success' : 'text-error'}`}>
              {stats.totalProfit >= 0 ? '+' : ''}{stats.totalProfit.toFixed(2)} kr
            </div>
          </div>
        </div>
      </Card>

      {/* Filters */}
      <Card title="Filter">
        <div className="flex gap-2">
          {statusOptions.map(opt => (
            <button
              key={opt.label}
              onClick={() => setStatusFilter(opt.value)}
              className={`
                px-3 py-1 rounded text-sm transition-colors
                ${statusFilter === opt.value
                  ? 'bg-tabBets text-bg'
                  : 'bg-panel2 text-muted hover:text-text'
                }
              `}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </Card>

      {/* Bets Table */}
      <Card title={`Bets (${bets.length})`}>
        {isLoading ? (
          <div className="text-muted text-sm py-4 text-center">Loading...</div>
        ) : bets.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">No bets found.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left text-xs">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Provider</th>
                  <th className="pb-2 pr-4">Outcome</th>
                  <th className="pb-2 pr-4 text-right">Odds</th>
                  <th className="pb-2 pr-4 text-right">Stake</th>
                  <th className="pb-2 pr-4 text-right">Profit</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {bets.map(bet => (
                  <tr key={bet.id} className="border-t border-border">
                    <td className="py-3 pr-4 text-muted text-xs">{formatDate(bet.placed_at)}</td>
                    <td className="py-3 pr-4 text-text">{formatProviderName(bet.provider)}</td>
                    <td className="py-3 pr-4 text-text capitalize">{bet.outcome || '-'}</td>
                    <td className="py-3 pr-4 text-right text-text">{bet.odds.toFixed(2)}</td>
                    <td className="py-3 pr-4 text-right text-text">{bet.stake.toFixed(2)} kr</td>
                    <td className="py-3 pr-4 text-right">
                      <span className={bet.profit >= 0 ? 'text-success' : 'text-error'}>
                        {bet.profit >= 0 ? '+' : ''}{bet.profit.toFixed(2)} kr
                      </span>
                    </td>
                    <td className={`py-3 pr-4 capitalize ${getStatusColor(bet.result)}`}>
                      {bet.result}
                    </td>
                    <td className="py-3">
                      {bet.result === 'pending' && (
                        <div className="flex gap-1">
                          <button
                            onClick={() => settleBet(bet.id, 'won')}
                            className="px-2 py-1 text-xs bg-success/20 text-success rounded hover:bg-success/30"
                          >
                            Won
                          </button>
                          <button
                            onClick={() => settleBet(bet.id, 'lost')}
                            className="px-2 py-1 text-xs bg-error/20 text-error rounded hover:bg-error/30"
                          >
                            Lost
                          </button>
                          <button
                            onClick={() => settleBet(bet.id, 'void')}
                            className="px-2 py-1 text-xs bg-panel2 text-muted rounded hover:bg-border"
                          >
                            Void
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
