import type { AnalyticsBucket } from '@/services/api/bets';
import { LANE_ORDER } from './lanes';

export function StrategySplit({ byStrategy }: { byStrategy: Record<string, AnalyticsBucket> }) {
  const rows = LANE_ORDER.map((lane) => [lane, byStrategy[lane]] as const).filter(([, v]) => v != null);
  if (rows.length === 0) return null;
  return (
    <div className="border border-border bg-panel2 overflow-hidden">
      <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border">
        Strategy split
      </div>
      <table className="w-full text-[11px] font-mono">
        <thead className="bg-bg/50">
          <tr>
            <th className="px-2 py-1 text-left">lane</th>
            <th className="px-2 py-1 text-right">n</th>
            <th className="px-2 py-1 text-right">win%</th>
            <th className="px-2 py-1 text-right">staked</th>
            <th className="px-2 py-1 text-right">profit</th>
            <th className="px-2 py-1 text-right">ROI%</th>
            <th className="px-2 py-1 text-right">CLV%</th>
            <th className="px-2 py-1 text-right">beat%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([lane, v]) => v && (
            <tr key={lane} className="border-t border-border/50 hover:bg-bg/30">
              <td className="px-2 py-1">{lane}</td>
              <td className="px-2 py-1 text-right">{v.n}</td>
              <td className="px-2 py-1 text-right">{v.win_pct ?? '-'}</td>
              <td className="px-2 py-1 text-right text-muted">{v.staked.toFixed(0)}</td>
              <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                {v.profit >= 0 ? '+' : ''}{v.profit.toFixed(0)}
              </td>
              <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {v.roi_pct?.toFixed(1) ?? '-'}
              </td>
              <td className={`px-2 py-1 text-right ${(v.avg_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {v.avg_clv_pct?.toFixed(1) ?? '-'}
              </td>
              <td className="px-2 py-1 text-right text-muted">{v.clv_positive_pct?.toFixed(0) ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
