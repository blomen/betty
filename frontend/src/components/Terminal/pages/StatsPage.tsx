import { useState, useEffect, useCallback } from 'react';
import { Card } from './Card';
import { api } from '@/services/api';
import type { BankrollStats, MetricsRun } from '@/types';

export function StatsPage() {
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [metricsHistory, setMetricsHistory] = useState<MetricsRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statsData, metricsData] = await Promise.all([
        api.getBankrollStats(),
        api.getMetricsHistory(10),
      ]);
      setStats(statsData);
      setMetricsHistory(metricsData.history);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const formatDuration = (ms: number) => {
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    if (minutes > 0) {
      return `${minutes}m ${seconds % 60}s`;
    }
    return `${seconds}s`;
  };

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

      {/* Extraction History */}
      <Card title="Recent Extractions">
        {metricsHistory.length === 0 ? (
          <div className="text-muted text-sm py-4 text-center">No extraction history.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-left text-xs">
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Duration</th>
                  <th className="pb-2 pr-4 text-right">Providers</th>
                  <th className="pb-2 pr-4 text-right">Events</th>
                  <th className="pb-2 text-right">Odds</th>
                </tr>
              </thead>
              <tbody>
                {metricsHistory.map(run => {
                  const providerCount = Object.keys(run.providers).length;
                  const totalEvents = Object.values(run.providers).reduce((sum, p) => sum + p.events_extracted, 0);
                  const totalOdds = Object.values(run.providers).reduce((sum, p) => sum + p.odds_extracted, 0);
                  const successCount = Object.values(run.providers).filter(p => p.success).length;

                  return (
                    <tr key={run.run_id} className="border-t border-border">
                      <td className="py-3 pr-4 text-text">{formatDate(run.started_at)}</td>
                      <td className="py-3 pr-4 text-muted">{formatDuration(run.total_duration_ms)}</td>
                      <td className="py-3 pr-4 text-right">
                        <span className="text-success">{successCount}</span>
                        <span className="text-muted">/{providerCount}</span>
                      </td>
                      <td className="py-3 pr-4 text-right text-text">{totalEvents}</td>
                      <td className="py-3 text-right text-text">{totalOdds}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
