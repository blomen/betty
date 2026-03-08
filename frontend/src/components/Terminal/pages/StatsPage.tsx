import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { CLVChart } from './BetsPage';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { BankrollStats, Bet } from '@/types';

export function StatsPage() {
  const [stats, setStats] = useState<BankrollStats | null>(null);
  const [bets, setBets] = useState<Bet[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statsData, betsData] = await Promise.all([
        api.getBankrollStats(),
        api.getBets(undefined, 500),
      ]);
      setStats(statsData);
      setBets(betsData.bets);
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
                {stats.total_deposited > 0 && (
                  <div className="text-muted text-sm font-normal">{stats.total_deposited.toFixed(0)} kr deposited</div>
                )}
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
    </div>
  );
}
