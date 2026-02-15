import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import type { BankrollStats } from '@/types';

export function StatsPage() {
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const statsData = await api.getBankrollStats();
      setStats(statsData);
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

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-tabStats" />
          Statistics
        </h2>
        <div className="text-muted text-sm py-4 text-center">Loading...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-text flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-tabStats" />
        Statistics
      </h2>

      {/* Betting Stats */}
      {stats && (
        <Card title="Betting Performance">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <div>
              <div className="text-muted text-sm">Total Bets</div>
              <div className="text-text text-2xl font-semibold">{stats.total_bets}</div>
            </div>
            <div>
              <div className="text-muted text-sm">Win Rate</div>
              <div className="text-text text-2xl font-semibold">{stats.win_rate.toFixed(1)}%</div>
            </div>
            <div>
              <div className="text-muted text-sm">ROI</div>
              <div className={`text-2xl font-semibold ${stats.roi_pct >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.roi_pct >= 0 ? '+' : ''}{stats.roi_pct.toFixed(1)}%
              </div>
            </div>
            <div>
              <div className="text-muted text-sm">Total Profit</div>
              <div className={`text-2xl font-semibold ${stats.total_profit >= 0 ? 'text-success' : 'text-error'}`}>
                {stats.total_profit >= 0 ? '+' : ''}{stats.total_profit.toFixed(2)} kr
              </div>
            </div>
          </div>

          <div className="grid grid-cols-4 gap-4 mt-6 pt-4 border-t border-border">
            <div className="text-center">
              <div className="text-success text-lg font-medium">{stats.wins}</div>
              <div className="text-muted text-xs">Wins</div>
            </div>
            <div className="text-center">
              <div className="text-error text-lg font-medium">{stats.losses}</div>
              <div className="text-muted text-xs">Losses</div>
            </div>
            <div className="text-center">
              <div className="text-muted text-lg font-medium">{stats.voids}</div>
              <div className="text-muted text-xs">Voids</div>
            </div>
            <div className="text-center">
              <div className="text-text text-lg font-medium">{stats.total_staked.toFixed(0)} kr</div>
              <div className="text-muted text-xs">Staked</div>
            </div>
          </div>
        </Card>
      )}

    </div>
  );
}
