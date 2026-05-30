import { useState } from 'react';
import type { api } from '@/services/api';

type Analytics = Awaited<ReturnType<typeof api.getAnalytics>>;

export function EdgeAnalytics({ analytics }: { analytics: Analytics }) {
  const [collapsed, setCollapsed] = useState(true);

  return (
    <div>
      <div className="flex items-center gap-2 w-full">
        <button
          className="flex items-center gap-2 text-left cursor-pointer group flex-1"
          onClick={() => setCollapsed(c => !c)}
        >
          <span className={`text-[10px] text-muted2 transition-transform ${collapsed ? '' : 'rotate-90'}`}>▶</span>
          <h3 className="text-xs text-muted uppercase tracking-wider font-semibold group-hover:text-text transition-colors">
            Realized vs Displayed Edge
          </h3>
        </button>
      </div>

      {!collapsed && (
        <div className="grid grid-cols-2 gap-2 mt-2">
          {/* Per-sport */}
          <div className="border border-border bg-panel2 overflow-hidden">
            <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border">By Sport</div>
            <table className="w-full text-[11px] font-mono">
              <thead className="bg-bg/50">
                <tr>
                  <th className="px-2 py-1 text-left">sport</th>
                  <th className="px-2 py-1 text-right">n</th>
                  <th className="px-2 py-1 text-right">W</th>
                  <th className="px-2 py-1 text-right">win%</th>
                  <th className="px-2 py-1 text-right">implied%</th>
                  <th className="px-2 py-1 text-right">edge%</th>
                  <th className="px-2 py-1 text-right">CLV%</th>
                  <th className="px-2 py-1 text-right">ROI%</th>
                  <th className="px-2 py-1 text-right">profit</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(analytics.by_sport)
                  .filter(([, v]) => v != null)
                  .sort(([, a], [, b]) => (b?.n ?? 0) - (a?.n ?? 0))
                  .map(([sport, v]) => v && (
                    <tr key={sport} className="border-t border-border/50 hover:bg-bg/30">
                      <td className="px-2 py-1">{sport}</td>
                      <td className="px-2 py-1 text-right">{v.n}</td>
                      <td className="px-2 py-1 text-right">{v.won}</td>
                      <td className="px-2 py-1 text-right">{v.win_pct ?? '-'}</td>
                      <td className="px-2 py-1 text-right text-muted">{v.implied_pct ?? '-'}</td>
                      <td className="px-2 py-1 text-right text-muted">{v.avg_displayed_edge_pct?.toFixed(1) ?? '-'}</td>
                      <td className={`px-2 py-1 text-right ${(v.avg_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.avg_clv_pct?.toFixed(1) ?? '-'}
                      </td>
                      <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.roi_pct?.toFixed(1) ?? '-'}
                      </td>
                      <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.profit.toFixed(2)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {/* Per-edge-bucket */}
          <div className="border border-border bg-panel2 overflow-hidden">
            <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border">By Edge Bucket</div>
            <table className="w-full text-[11px] font-mono">
              <thead className="bg-bg/50">
                <tr>
                  <th className="px-2 py-1 text-left">bucket</th>
                  <th className="px-2 py-1 text-right">n</th>
                  <th className="px-2 py-1 text-right">W</th>
                  <th className="px-2 py-1 text-right">win%</th>
                  <th className="px-2 py-1 text-right">implied%</th>
                  <th className="px-2 py-1 text-right">edge%</th>
                  <th className="px-2 py-1 text-right">CLV%</th>
                  <th className="px-2 py-1 text-right">ROI%</th>
                  <th className="px-2 py-1 text-right">profit</th>
                </tr>
              </thead>
              <tbody>
                {['0-2%', '2-5%', '5-10%', '10-20%', '20%+']
                  .map(b => [b, analytics.by_edge_bucket[b]] as const)
                  .filter(([, v]) => v != null)
                  .map(([b, v]) => v && (
                    <tr key={b} className="border-t border-border/50 hover:bg-bg/30">
                      <td className="px-2 py-1">{b}</td>
                      <td className="px-2 py-1 text-right">{v.n}</td>
                      <td className="px-2 py-1 text-right">{v.won}</td>
                      <td className="px-2 py-1 text-right">{v.win_pct ?? '-'}</td>
                      <td className="px-2 py-1 text-right text-muted">{v.implied_pct ?? '-'}</td>
                      <td className="px-2 py-1 text-right text-muted">{v.avg_displayed_edge_pct?.toFixed(1) ?? '-'}</td>
                      <td className={`px-2 py-1 text-right ${(v.avg_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.avg_clv_pct?.toFixed(1) ?? '-'}
                      </td>
                      <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.roi_pct?.toFixed(1) ?? '-'}
                      </td>
                      <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                        {v.profit.toFixed(2)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {/* Per-sport × market with CLV confidence multiplier */}
          {analytics.by_sport_and_market && Object.keys(analytics.by_sport_and_market).length > 0 && (
            <div className="border border-border bg-panel2 overflow-hidden col-span-full">
              <div className="px-2 py-1 text-[10px] text-muted uppercase tracking-wider bg-bg border-b border-border flex justify-between">
                <span>By Sport × Market — Kelly Confidence</span>
                <span className={analytics.bucket_confidence_enabled ? 'text-success' : 'text-muted2'}>
                  multiplier {analytics.bucket_confidence_enabled ? 'LIVE' : 'preview (set BUCKET_CONFIDENCE_ENABLED=1)'}
                </span>
              </div>
              <table className="w-full text-[11px] font-mono">
                <thead className="bg-bg/50">
                  <tr>
                    <th className="px-2 py-1 text-left">sport</th>
                    <th className="px-2 py-1 text-left">market</th>
                    <th className="px-2 py-1 text-right">n</th>
                    <th className="px-2 py-1 text-right">win%</th>
                    <th className="px-2 py-1 text-right">edge%</th>
                    <th className="px-2 py-1 text-right">CLV%</th>
                    <th className="px-2 py-1 text-right">ROI%</th>
                    <th className="px-2 py-1 text-right">profit</th>
                    <th className="px-2 py-1 text-right">×Kelly</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(analytics.by_sport_and_market)
                    .filter(([, v]) => v != null)
                    .sort(([, a], [, b]) => (b?.n ?? 0) - (a?.n ?? 0))
                    .map(([key, v]) => {
                      if (!v) return null;
                      const [sport, market] = key.split('|');
                      const mult = v.confidence_multiplier ?? 1.0;
                      const multColor = mult >= 1.0 ? 'text-success' : mult >= 0.5 ? 'text-warning' : 'text-error';
                      return (
                        <tr key={key} className="border-t border-border/50 hover:bg-bg/30">
                          <td className="px-2 py-1">{sport}</td>
                          <td className="px-2 py-1 text-muted">{market}</td>
                          <td className="px-2 py-1 text-right">{v.n}</td>
                          <td className="px-2 py-1 text-right">{v.win_pct ?? '-'}</td>
                          <td className="px-2 py-1 text-right text-muted">{v.avg_displayed_edge_pct?.toFixed(1) ?? '-'}</td>
                          <td className={`px-2 py-1 text-right ${(v.avg_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                            {v.avg_clv_pct?.toFixed(2) ?? '-'}
                          </td>
                          <td className={`px-2 py-1 text-right ${(v.roi_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                            {v.roi_pct?.toFixed(1) ?? '-'}
                          </td>
                          <td className={`px-2 py-1 text-right ${v.profit >= 0 ? 'text-success' : 'text-error'}`}>
                            {v.profit.toFixed(2)}
                          </td>
                          <td className={`px-2 py-1 text-right font-semibold ${multColor}`}>
                            {mult.toFixed(2)}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!collapsed && analytics.overall && (
        <div className="mt-1 text-[10px] text-muted px-2">
          CLV-ROI gap &gt; 10pp on any sport = likely event-mismatch issue. n &lt; 30 = noise.
          Kelly multiplier deflates stakes in buckets with negative historical CLV (≥100 bets); preview-only until env flag is set.
        </div>
      )}
    </div>
  );
}
