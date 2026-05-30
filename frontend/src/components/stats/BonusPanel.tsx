import { useQuery } from '@tanstack/react-query';
import type { AnalyticsBucket } from '@/services/api/bets';
import { api } from '@/services/api';
import { ProviderName } from '@/components/ProviderName';
import { BonusArbTracker } from '@/components/BonusArbTracker';

const UNLIMITED = new Set(['pinnacle', 'cloudbet', 'kalshi', 'polymarket']);

export function BonusPanel({ profileId, byProvider }: { profileId: number; byProvider: Record<string, AnalyticsBucket> }) {
  const { data: bonus } = useQuery({
    queryKey: ['profile', 'bonus-statuses', profileId],
    queryFn: () => api.getProfileBonusStatuses(profileId),
    staleTime: 60_000,
  });

  const providers = Object.entries(byProvider)
    .filter(([, v]) => v != null)
    .sort(([, a], [, b]) => (b?.profit ?? 0) - (a?.profit ?? 0));
  const sharpPnl = providers.filter(([pid]) => UNLIMITED.has(pid)).reduce((s, [, v]) => s + (v?.profit ?? 0), 0);

  return (
    <div className="space-y-2">
      <div className="border border-border bg-panel2 overflow-hidden">
        <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border flex justify-between">
          <span>Per-provider bonus + value captured</span>
          <span className={sharpPnl >= 0 ? 'text-success' : 'text-error'}>
            Sharp-side P/L: {sharpPnl >= 0 ? '+' : ''}{sharpPnl.toFixed(0)} kr
          </span>
        </div>
        <table className="w-full text-[11px] font-mono">
          <thead className="bg-bg/50">
            <tr>
              <th className="px-2 py-1 text-left">provider</th>
              <th className="px-2 py-1 text-left">bonus</th>
              <th className="px-2 py-1 text-right">wager%</th>
              <th className="px-2 py-1 text-right">days</th>
              <th className="px-2 py-1 text-right">staked</th>
              <th className="px-2 py-1 text-right">value</th>
              <th className="px-2 py-1 text-right">ROI%</th>
            </tr>
          </thead>
          <tbody>
            {providers.map(([pid, v]) => {
              const b = bonus?.[pid];
              return v && (
                <tr key={pid} className="border-t border-border/50 hover:bg-bg/30">
                  <td className="px-2 py-1"><ProviderName name={pid} /></td>
                  <td className="px-2 py-1 text-muted">{b ? `${b.bonus_type ?? '-'} · ${b.status}` : '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{b && b.progress_pct != null ? `${b.progress_pct.toFixed(0)}%` : '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{b?.days_remaining ?? '-'}</td>
                  <td className="px-2 py-1 text-right text-muted">{v.staked.toFixed(0)}</td>
                  <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                    {v.profit >= 0 ? '+' : ''}{v.profit.toFixed(0)}
                  </td>
                  <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                    {v.roi_pct?.toFixed(1) ?? '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <BonusArbTracker />
    </div>
  );
}
