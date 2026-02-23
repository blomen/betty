import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Trade, TradingAnalytics } from '@/types/trading';

export function TradingJournalPage() {
  const [unreviewed, setUnreviewed] = useState<Trade[]>([]);
  const [analytics, setAnalytics] = useState<TradingAnalytics | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Review form state
  const [reviewingId, setReviewingId] = useState<number | null>(null);
  const [thesisRecap, setThesisRecap] = useState('');
  const [followedRules, setFollowedRules] = useState<boolean | null>(null);
  const [whatToImprove, setWhatToImprove] = useState('');
  const [grade, setGrade] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [unrevRes, analyticsRes] = await Promise.all([
        api.getUnreviewedTrades(),
        api.getTradingAnalytics(),
      ]);
      setUnreviewed(unrevRes.trades);
      setAnalytics(analyticsRes);
    } catch (err) {
      console.error('Failed to fetch journal data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleStartReview = (trade: Trade) => {
    setReviewingId(trade.id);
    setThesisRecap('');
    setFollowedRules(null);
    setWhatToImprove('');
    setGrade(null);
    setSuccessMsg('');
  };

  const handleSubmitReview = async () => {
    if (!reviewingId || grade === null) return;
    setSubmitting(true);
    try {
      await api.submitTradeReview(reviewingId, {
        thesis_recap: thesisRecap || null,
        followed_rules: followedRules,
        what_to_improve: whatToImprove || null,
        grade,
      });
      setSuccessMsg(`Review submitted for trade #${reviewingId}`);
      setReviewingId(null);
      fetchData();
    } catch (err) {
      console.error('Failed to submit review:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleExportCsv = () => {
    const url = api.getTradingExportUrl();
    window.open(url, '_blank');
  };

  const pnlColor = (v: number | null | undefined) => v == null ? 'text-muted' : v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-muted';
  const pct = (v: number) => `${(v * 100).toFixed(0)}%`;

  if (isLoading) return <div className="text-muted text-sm">Loading journal...</div>;

  const a = analytics;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="tradingJournal" color={TAB_COLORS.tradingJournal} />
          Journal
        </h2>
        <button onClick={handleExportCsv} className="text-xs px-3 py-1.5 border border-border text-muted hover:text-text rounded">
          Export CSV
        </button>
      </div>

      {successMsg && (
        <div className="bg-success/10 border border-success text-success text-sm p-3 rounded">{successMsg}</div>
      )}

      {/* Unreviewed trades */}
      {unreviewed.length > 0 && (
        <div className="border border-warning/50 bg-warning/5 rounded p-4">
          <h3 className="text-sm font-semibold text-warning mb-3">Pending Review ({unreviewed.length})</h3>
          <div className="space-y-2">
            {unreviewed.map(trade => (
              <div key={trade.id} className="flex items-center justify-between bg-panel rounded px-3 py-2 border border-border">
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-muted font-mono">#{trade.id}</span>
                  <span className="text-text">{trade.instrument}</span>
                  <span className={trade.direction === 'long' ? 'text-success' : 'text-error'}>{trade.direction.toUpperCase()}</span>
                  <span className="text-muted">{trade.setup_type}</span>
                  <span className={`font-mono ${pnlColor(trade.realized_pnl)}`}>
                    {trade.realized_pnl != null ? `$${trade.realized_pnl.toFixed(2)}` : '—'}
                  </span>
                  <span className={`font-mono ${pnlColor(trade.r_multiple)}`}>
                    {trade.r_multiple != null ? `${trade.r_multiple.toFixed(1)}R` : '—'}
                  </span>
                </div>
                <button
                  onClick={() => handleStartReview(trade)}
                  className="text-xs px-3 py-1 bg-tabTradingJournal/20 text-tabTradingJournal rounded hover:bg-tabTradingJournal/30"
                >
                  Write Review
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Review form */}
      {reviewingId !== null && (
        <div className="border border-tabTradingJournal bg-panel rounded p-4 space-y-3">
          <h3 className="text-sm font-semibold text-tabTradingJournal">Review Trade #{reviewingId}</h3>

          <div>
            <label className="text-xs text-muted block mb-1">Thesis Recap</label>
            <textarea
              value={thesisRecap}
              onChange={e => setThesisRecap(e.target.value)}
              placeholder="What was your thesis? What happened?"
              className="bg-panel2 border border-border rounded px-3 py-2 text-sm text-text w-full font-mono resize-none h-16"
            />
          </div>

          <div className="flex items-center gap-4">
            <div>
              <label className="text-xs text-muted block mb-1">Followed Rules?</label>
              <div className="flex gap-1">
                {([true, false] as const).map(val => (
                  <button
                    key={String(val)}
                    onClick={() => setFollowedRules(val)}
                    className={`text-xs px-4 py-1.5 rounded border transition-colors ${
                      followedRules === val
                        ? val ? 'bg-success/20 border-success text-success' : 'bg-error/20 border-error text-error'
                        : 'border-border text-muted hover:text-text'
                    }`}
                  >
                    {val ? 'Yes' : 'No'}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-xs text-muted block mb-1">Grade</label>
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map(n => (
                  <button
                    key={n}
                    onClick={() => setGrade(n)}
                    className={`w-8 h-8 text-xs rounded border transition-colors ${
                      grade === n
                        ? 'bg-tabTradingJournal/20 border-tabTradingJournal text-tabTradingJournal'
                        : 'border-border text-muted hover:text-text'
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div>
            <label className="text-xs text-muted block mb-1">What to Improve</label>
            <textarea
              value={whatToImprove}
              onChange={e => setWhatToImprove(e.target.value)}
              placeholder="What would you do differently?"
              className="bg-panel2 border border-border rounded px-3 py-2 text-sm text-text w-full font-mono resize-none h-12"
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleSubmitReview}
              disabled={submitting || grade === null}
              className="text-sm px-4 py-2 bg-tabTradingJournal/20 border border-tabTradingJournal text-tabTradingJournal rounded hover:bg-tabTradingJournal/30 disabled:opacity-40"
            >
              {submitting ? 'Submitting...' : 'Submit Review'}
            </button>
            <button onClick={() => setReviewingId(null)} className="text-sm px-4 py-2 border border-border text-muted rounded hover:text-text">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Analytics Dashboard */}
      {a && a.total > 0 && (
        <>
          {/* Key Metrics */}
          <div className="border border-border bg-panel rounded p-4">
            <h3 className="text-sm font-semibold text-text mb-3">Performance Overview</h3>
            <div className="grid grid-cols-4 gap-px bg-border rounded overflow-hidden mb-3">
              {[
                { label: 'Total Trades', value: a.total.toString() },
                { label: 'Win Rate', value: pct(a.win_rate ?? 0), color: 'text-success' },
                { label: 'Profit Factor', value: typeof a.profit_factor === 'string' ? a.profit_factor : (a.profit_factor ?? 0).toFixed(2), color: (typeof a.profit_factor === 'number' && a.profit_factor > 1) ? 'text-success' : 'text-error' },
                { label: 'Expectancy', value: `$${(a.expectancy ?? 0).toFixed(2)}`, color: pnlColor(a.expectancy) },
              ].map(item => (
                <div key={item.label} className="bg-panel p-3 text-center">
                  <div className="text-xs text-muted mb-1">{item.label}</div>
                  <div className={`text-sm font-mono font-semibold ${item.color || 'text-text'}`}>{item.value}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-6 gap-px bg-border rounded overflow-hidden mb-3">
              {[
                { label: 'Total P&L', value: `$${(a.total_pnl ?? 0).toFixed(2)}`, color: pnlColor(a.total_pnl) },
                { label: 'Avg Win', value: `$${(a.avg_win ?? 0).toFixed(2)}`, color: 'text-success' },
                { label: 'Avg Loss', value: `$${(a.avg_loss ?? 0).toFixed(2)}`, color: 'text-error' },
                { label: 'Avg R', value: `${(a.avg_r ?? 0).toFixed(1)}R`, color: pnlColor(a.avg_r) },
                { label: 'Best R', value: `${(a.max_r ?? 0).toFixed(1)}R`, color: 'text-success' },
                { label: 'Worst R', value: `${(a.min_r ?? 0).toFixed(1)}R`, color: 'text-error' },
              ].map(item => (
                <div key={item.label} className="bg-panel p-2.5 text-center">
                  <div className="text-[10px] text-muted mb-0.5">{item.label}</div>
                  <div className={`text-xs font-mono ${item.color || 'text-text'}`}>{item.value}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-6 gap-px bg-border rounded overflow-hidden">
              {[
                { label: 'Largest Win', value: `$${(a.largest_win ?? 0).toFixed(2)}`, color: 'text-success' },
                { label: 'Largest Loss', value: `$${(a.largest_loss ?? 0).toFixed(2)}`, color: 'text-error' },
                { label: 'Win Streak', value: (a.max_win_streak ?? 0).toString(), color: 'text-success' },
                { label: 'Loss Streak', value: (a.max_loss_streak ?? 0).toString(), color: 'text-error' },
                { label: 'Commission', value: `$${(a.total_commission ?? 0).toFixed(2)}`, color: 'text-muted' },
                { label: 'Avg Grade', value: (a.avg_grade ?? 0).toFixed(1), color: 'text-text' },
              ].map(item => (
                <div key={item.label} className="bg-panel p-2.5 text-center">
                  <div className="text-[10px] text-muted mb-0.5">{item.label}</div>
                  <div className={`text-xs font-mono ${item.color || 'text-text'}`}>{item.value}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Equity Curve */}
          {a.equity_curve && a.equity_curve.length > 1 && (
            <div className="border border-border bg-panel rounded p-4">
              <h3 className="text-sm font-semibold text-text mb-3">Equity Curve</h3>
              <div className="h-32 flex items-end gap-px relative">
                {(() => {
                  const pts = a.equity_curve!;
                  const maxAbs = Math.max(...pts.map(p => Math.abs(p.cumulative_pnl)), 1);
                  return pts.map((pt, i) => {
                    const pctHeight = Math.abs(pt.cumulative_pnl) / maxAbs * 50;
                    const isPositive = pt.cumulative_pnl >= 0;
                    return (
                      <div key={i} className="flex-1 relative group" style={{ height: '100%' }}>
                        <div
                          className={`absolute left-0 right-0 ${isPositive ? 'bg-success/60' : 'bg-error/60'}`}
                          style={{
                            bottom: isPositive ? '50%' : `${50 - pctHeight}%`,
                            height: `${Math.max(pctHeight, 1)}%`,
                          }}
                        />
                        <div className="absolute bottom-full left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 bg-panel2 border border-border rounded px-2 py-1 text-[10px] text-text font-mono whitespace-nowrap z-10 pointer-events-none mb-1">
                          #{pt.trade_id}: ${pt.pnl.toFixed(2)} (total: ${pt.cumulative_pnl.toFixed(2)})
                        </div>
                      </div>
                    );
                  });
                })()}
                {/* Zero line */}
                <div className="absolute left-0 right-0 border-t border-muted/30" style={{ top: '50%' }} />
              </div>
            </div>
          )}

          {/* Direction Stats */}
          {a.by_direction && (
            <div className="grid grid-cols-2 gap-3">
              {Object.entries(a.by_direction).map(([dir, d]) => (
                <div key={dir} className="border border-border bg-panel rounded p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className={`text-sm font-semibold ${dir === 'long' ? 'text-success' : 'text-error'}`}>{dir.toUpperCase()}</span>
                    <span className="text-xs text-muted">{d.count} trades</span>
                  </div>
                  <div className="flex gap-4 text-xs">
                    <span className="text-muted">Win%: <span className="text-text">{d.count > 0 ? pct(d.wins / d.count) : '—'}</span></span>
                    <span className="text-muted">P&L: <span className={`font-mono ${pnlColor(d.total_pnl)}`}>${d.total_pnl.toFixed(2)}</span></span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* By Setup */}
          {a.by_setup && Object.keys(a.by_setup).length > 0 && (
            <div className="border border-border bg-panel rounded p-4">
              <h3 className="text-sm font-semibold text-text mb-3">By Setup</h3>
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-panel2 text-xs text-muted border-b border-border">
                      <th className="text-left px-3 py-2">Setup</th>
                      <th className="text-right px-3 py-2">Trades</th>
                      <th className="text-right px-3 py-2">Win%</th>
                      <th className="text-right px-3 py-2">Avg R</th>
                      <th className="text-right px-3 py-2">Expectancy</th>
                      <th className="text-right px-3 py-2">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(a.by_setup)
                      .sort((x, y) => y[1].total_pnl - x[1].total_pnl)
                      .map(([setup, d]) => (
                        <tr key={setup} className="border-b border-border">
                          <td className="px-3 py-2 text-text">{setup}</td>
                          <td className="px-3 py-2 text-right text-muted font-mono">{d.count}</td>
                          <td className="px-3 py-2 text-right text-success font-mono">{pct(d.win_rate)}</td>
                          <td className={`px-3 py-2 text-right font-mono ${pnlColor(d.avg_r)}`}>{d.avg_r.toFixed(1)}R</td>
                          <td className={`px-3 py-2 text-right font-mono ${pnlColor(d.expectancy)}`}>${d.expectancy.toFixed(2)}</td>
                          <td className={`px-3 py-2 text-right font-mono ${pnlColor(d.total_pnl)}`}>${d.total_pnl.toFixed(2)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* By Instrument */}
          {a.by_instrument && Object.keys(a.by_instrument).length > 0 && (
            <div className="border border-border bg-panel rounded p-4">
              <h3 className="text-sm font-semibold text-text mb-3">By Instrument</h3>
              <div className="border border-border rounded overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-panel2 text-xs text-muted border-b border-border">
                      <th className="text-left px-3 py-2">Instrument</th>
                      <th className="text-right px-3 py-2">Trades</th>
                      <th className="text-right px-3 py-2">Win%</th>
                      <th className="text-right px-3 py-2">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(a.by_instrument)
                      .sort((x, y) => y[1].total_pnl - x[1].total_pnl)
                      .map(([inst, d]) => (
                        <tr key={inst} className="border-b border-border">
                          <td className="px-3 py-2 text-text">{inst}</td>
                          <td className="px-3 py-2 text-right text-muted font-mono">{d.count}</td>
                          <td className="px-3 py-2 text-right text-success font-mono">{pct(d.win_rate)}</td>
                          <td className={`px-3 py-2 text-right font-mono ${pnlColor(d.total_pnl)}`}>${d.total_pnl.toFixed(2)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {(!a || a.total === 0) && unreviewed.length === 0 && (
        <div className="text-muted text-xs py-8 text-center border border-border bg-panel rounded">
          No closed trades yet. Complete some trades to see analytics here.
        </div>
      )}
    </div>
  );
}
