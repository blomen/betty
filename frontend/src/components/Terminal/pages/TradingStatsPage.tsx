import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Trade, TradingAnalytics } from '@/types/trading';

type FilterState = {
  setup_type: string;
  direction: string;
  result: string;
};

export function TradingStatsPage() {
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null);
  const [filters, setFilters] = useState<FilterState>({
    setup_type: 'all', direction: 'all', result: 'all',
  });

  const { data: analyticsData, isLoading: analyticsLoading } = useQuery({
    queryKey: ['trading-analytics'],
    queryFn: () => api.getTradingAnalytics({}).catch(() => null),
  });
  const analytics: TradingAnalytics | null = analyticsData ?? null;

  const { data: tradesData, isLoading: tradesLoading } = useQuery({
    queryKey: ['trading-trades'],
    queryFn: () => api.getTrades({}).catch(() => []),
  });
  const trades: Trade[] = Array.isArray(tradesData) ? tradesData : [];

  const { data: accountsData } = useQuery({
    queryKey: ['trading-accounts'],
    queryFn: () => api.getTradingAccounts().catch(() => ({ accounts: [] })),
  });
  const activeAccountId = accountsData?.accounts?.[0]?.id ?? null;

  const isLoading = analyticsLoading || tradesLoading;

  const filteredTrades = trades.filter(t => {
    if (filters.setup_type !== 'all' && t.setup_type !== filters.setup_type) return false;
    if (filters.direction !== 'all' && t.direction !== filters.direction) return false;
    if (filters.result === 'win' && (t.realized_pnl ?? 0) <= 0) return false;
    if (filters.result === 'loss' && (t.realized_pnl ?? 0) >= 0) return false;
    return true;
  });

  if (isLoading) return <div className="text-muted text-sm">Loading stats...</div>;

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="flex items-center gap-2">
        <TabIcon name="tradingStats" color={TAB_COLORS.tradingStats} size={18} />
        <span className="text-sm font-semibold text-text">Trade Stats</span>
      </div>

      {analytics && (
        <div className="grid grid-cols-5 gap-2">
          <StatCard label="Trades" value={String(analytics.total)} />
          <StatCard label="Win Rate" value={`${((analytics.win_rate ?? 0) * 100).toFixed(0)}%`}
            color={(analytics.win_rate ?? 0) >= 0.5 ? 'text-success' : 'text-error'} />
          <StatCard label="P&L" value={`${(analytics.total_pnl ?? 0) >= 0 ? '+' : ''}${(analytics.total_pnl ?? 0).toFixed(0)}`}
            color={(analytics.total_pnl ?? 0) >= 0 ? 'text-success' : 'text-error'} />
          <StatCard label="Avg R" value={analytics.avg_r?.toFixed(2) || '\u2014'}
            color={(analytics.avg_r ?? 0) >= 0 ? 'text-success' : 'text-error'} />
          <StatCard label="Profit Factor" value={analytics.profit_factor != null ? String(typeof analytics.profit_factor === 'number' ? analytics.profit_factor.toFixed(2) : analytics.profit_factor) : '\u2014'}
            color={(typeof analytics.profit_factor === 'number' ? analytics.profit_factor : 0) >= 1 ? 'text-success' : 'text-error'} />
        </div>
      )}

      <div className="flex items-center gap-3 text-xs">
        <select value={filters.direction} onChange={e => setFilters(f => ({ ...f, direction: e.target.value }))}
          className="bg-panel2 border border-border rounded px-2 py-1 text-text">
          <option value="all">All Directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select value={filters.result} onChange={e => setFilters(f => ({ ...f, result: e.target.value }))}
          className="bg-panel2 border border-border rounded px-2 py-1 text-text">
          <option value="all">All Results</option>
          <option value="win">Wins</option>
          <option value="loss">Losses</option>
        </select>
        <span className="text-muted ml-auto">{filteredTrades.length} trades</span>
      </div>

      <div className="border border-border bg-panel rounded">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted">
              <th className="text-left px-3 py-2">Date</th>
              <th className="text-left px-3 py-2">Setup</th>
              <th className="text-left px-3 py-2">Dir</th>
              <th className="text-right px-3 py-2">Entry</th>
              <th className="text-right px-3 py-2">Exit</th>
              <th className="text-right px-3 py-2">P&L</th>
              <th className="text-right px-3 py-2">R</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filteredTrades.length === 0 ? (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-muted">No trades yet.</td></tr>
            ) : filteredTrades.map(t => (
              <tr key={t.id}
                onClick={() => setExpandedTrade(expandedTrade === t.id ? null : t.id)}
                className="hover:bg-panel2/50 cursor-pointer transition-colors">
                <td className="px-3 py-2 text-muted font-mono">
                  {t.opened_at ? new Date(t.opened_at).toLocaleDateString() : '\u2014'}
                </td>
                <td className="px-3 py-2 text-text">{t.setup_type}</td>
                <td className="px-3 py-2">
                  <span className={t.direction === 'long' ? 'text-success' : 'text-error'}>
                    {t.direction.toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-text">
                  {t.entry_price != null ? t.entry_price.toFixed(2) : '\u2014'}
                </td>
                <td className="px-3 py-2 text-right font-mono text-text">
                  {t.state === 'closed' && t.realized_pnl != null && t.entry_price != null
                    ? (t.entry_price + t.realized_pnl / t.contracts).toFixed(2) : '\u2014'}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  (t.realized_pnl ?? 0) > 0 ? 'text-success' : (t.realized_pnl ?? 0) < 0 ? 'text-error' : 'text-muted'
                }`}>
                  {t.realized_pnl != null ? `${t.realized_pnl >= 0 ? '+' : ''}${t.realized_pnl.toFixed(0)}` : '\u2014'}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  (t.r_multiple ?? 0) > 0 ? 'text-success' : (t.r_multiple ?? 0) < 0 ? 'text-error' : 'text-muted'
                }`}>
                  {t.r_multiple != null ? `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple.toFixed(2)}R` : '\u2014'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {activeAccountId != null && (
        <PostmortemSection accountId={activeAccountId} />
      )}
    </div>
  );
}

function PostmortemSection({ accountId }: { accountId: number }) {
  const [summary, setSummary] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [patterns, setPatterns] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function fetch() {
      try {
        const [sumRes, patRes] = await Promise.all([
          api.getPostmortemTradesSummary(accountId).catch(() => ({ summary: [], total: 0 })),
          api.getPostmortemTradesPatterns(accountId).catch(() => ({ patterns: [] })),
        ]);
        if (cancelled) return;
        setSummary(sumRes.summary ?? []);
        setTotal(sumRes.total ?? 0);
        setPatterns(patRes.patterns ?? []);
      } catch {
        // swallow
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    fetch();
    return () => { cancelled = true; };
  }, [accountId]);

  if (!loaded) return null;
  if (total === 0 && patterns.length === 0) return null;

  // Compute summary card values
  const totalLosses = summary.filter(s => s.classification && s.classification !== 'expected_loss' ? false : true)
    .reduce((n, s) => n + (s.count ?? 0), 0) || total;
  const expectedLossCount = summary.find(s => s.classification === 'expected_loss')?.count ?? 0;
  const stopIssueCount = summary.find(s => s.classification === 'stop_too_wide')?.count ?? 0;
  const psychCorrelated = patterns.find(
    (p: any) => p.message && (p.message.toLowerCase().includes('psych') || p.message.toLowerCase().includes('routine'))
  );
  const psychPct = psychCorrelated?.value != null
    ? `${Number(psychCorrelated.value).toFixed(0)}%`
    : '\u2014';

  const expectedLossPct = totalLosses > 0 ? ((expectedLossCount / totalLosses) * 100).toFixed(0) : '0';
  const stopIssuePct = totalLosses > 0 ? ((stopIssueCount / totalLosses) * 100).toFixed(0) : '0';

  function severityIcon(severity: string) {
    if (severity === 'high' || severity === 'critical') return <span className="text-error">&#x25BC;</span>;
    if (severity === 'medium' || severity === 'warning') return <span className="text-[#a855f7]">&#x25CF;</span>;
    return <span className="text-success">&#x25B2;</span>;
  }

  return (
    <div className="space-y-3">
      <div className="text-[10px] uppercase text-muted tracking-wider">Postmortem</div>

      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Closed Trades" value={String(total)} />
        <StatCard label="% Expected Losses" value={`${expectedLossPct}%`}
          color={Number(expectedLossPct) >= 50 ? 'text-success' : 'text-error'} />
        <StatCard label="% Stop Issues" value={`${stopIssuePct}%`}
          color={Number(stopIssuePct) <= 10 ? 'text-success' : 'text-error'} />
        <StatCard label="Psych Correlated" value={psychPct} />
      </div>

      <div className="border-2 border-border bg-panel rounded p-3">
        <div className="text-[10px] uppercase text-muted tracking-wider mb-2">Pattern Insights</div>
        {patterns.length === 0 ? (
          <div className="text-xs text-muted">Not enough data</div>
        ) : (
          <div className="space-y-1.5">
            {patterns.map((p: any, i: number) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className="mt-0.5 shrink-0">{severityIcon(p.severity ?? 'low')}</span>
                <span className="text-text font-mono">{p.message ?? p.pattern ?? '\u2014'}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, color = 'text-text' }: { label: string; value: string; color?: string }) {
  return (
    <div className="border border-border bg-panel rounded p-2.5 text-center">
      <div className="text-[10px] text-muted mb-0.5">{label}</div>
      <div className={`text-sm font-mono font-semibold ${color}`}>{value}</div>
    </div>
  );
}
