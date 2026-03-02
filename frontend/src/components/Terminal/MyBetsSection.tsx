import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName, formatDateTime } from '@/utils/formatters';
import { TAB_COLORS } from './TabBar';
import type { Bet } from '@/types';

type BetCategory = 'upcoming' | 'live' | 'ft';

interface MyBetsSectionProps {
  /** Filter function to select only bets relevant to this page */
  filter: (bet: Bet) => boolean;
  /** Color key from TAB_COLORS (e.g. 'value', 'success', 'reverse') */
  colorKey: string;
}

export function MyBetsSection({ filter, colorKey }: MyBetsSectionProps) {
  const [bets, setBets] = useState<Bet[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState<BetCategory>('upcoming');

  const color = TAB_COLORS[colorKey] ?? '#64748B';

  const fetchBets = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getBets(undefined, 500);
      setBets(res.bets.filter(filter));
    } catch (err) {
      console.error('MyBets fetch failed:', err);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { fetchBets(); }, [fetchBets]);

  const categorized = useMemo(() => {
    const now = Date.now();
    const upcoming: Bet[] = [];
    const live: Bet[] = [];
    const ft: Bet[] = [];

    for (const b of bets) {
      if (b.result !== 'pending') {
        ft.push(b);
      } else if (b.start_time && new Date(b.start_time).getTime() <= now) {
        live.push(b);
      } else {
        upcoming.push(b);
      }
    }

    // Sort: upcoming by start_time asc, live by start_time desc, ft by placed_at desc
    upcoming.sort((a, b) => {
      const ta = a.start_time ? new Date(a.start_time).getTime() : Infinity;
      const tb = b.start_time ? new Date(b.start_time).getTime() : Infinity;
      return ta - tb;
    });
    live.sort((a, b) => {
      const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
      const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
      return tb - ta;
    });
    ft.sort((a, b) => {
      const ta = a.placed_at ? new Date(a.placed_at).getTime() : 0;
      const tb = b.placed_at ? new Date(b.placed_at).getTime() : 0;
      return tb - ta;
    });

    return { upcoming, live, ft };
  }, [bets]);

  const categories: { id: BetCategory; label: string; count: number }[] = [
    { id: 'upcoming', label: 'Upcoming', count: categorized.upcoming.length },
    { id: 'live', label: 'Live', count: categorized.live.length },
    { id: 'ft', label: 'FT', count: categorized.ft.length },
  ];

  const activeBets = categorized[activeCategory];

  const pnlColor = (v: number) => v > 0.01 ? 'text-success' : v < -0.01 ? 'text-error' : 'text-muted';
  const pnlSign = (v: number) => v > 0 ? '+' : '';

  const resultBadge = (r: string) => {
    if (r === 'won') return 'text-success';
    if (r === 'lost') return 'text-error';
    if (r === 'void') return 'text-muted';
    return 'text-warning';
  };

  const resolveOutcome = (b: Bet): string => {
    const outcome = b.outcome ?? '';
    const point = b.point != null ? ` ${b.point}` : '';
    if (outcome === 'home' && b.home_team) return b.home_team;
    if (outcome === 'away' && b.away_team) return b.away_team;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${point}`;
    if (outcome === 'under') return `Under${point}`;
    return outcome;
  };

  const eventLabel = (b: Bet): string => {
    if (b.home_team && b.away_team) return `${b.home_team} vs ${b.away_team}`;
    if (b.outcome) return b.outcome;
    return `Bet #${b.id}`;
  };

  if (loading && bets.length === 0) {
    return <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading bets...</div>;
  }

  return (
    <div className="space-y-3">
      {/* Category tabs */}
      <div className="flex gap-1 border-b border-border">
        {categories.map(cat => (
          <button
            key={cat.id}
            onClick={() => setActiveCategory(cat.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
              activeCategory === cat.id
                ? ''
                : 'border-transparent text-muted hover:text-text'
            }`}
            style={activeCategory === cat.id ? { borderBottomColor: color, color } : undefined}
          >
            {cat.label}
            <span className="ml-1 text-muted">({cat.count})</span>
          </button>
        ))}
      </div>

      {/* Bets table */}
      {activeBets.length === 0 ? (
        <div className="text-muted text-sm py-4 text-center">
          No {activeCategory === 'ft' ? 'settled' : activeCategory} bets.
        </div>
      ) : (
        <div className="border-l-2" style={{ borderColor: color }}>
          <table className="sq">
            <thead>
              <tr>
                <th>Event</th>
                <th className="text-right">Outcome</th>
                <th className="text-right">Provider</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Stake</th>
                {activeCategory === 'ft' ? (
                  <>
                    <th className="text-right">Result</th>
                    <th className="text-right">P&L</th>
                  </>
                ) : activeCategory === 'live' ? (
                  <th className="text-right">Score</th>
                ) : (
                  <th className="text-right">Edge</th>
                )}
              </tr>
            </thead>
            <tbody>
              {activeBets.map(b => (
                <tr key={b.id}>
                  <td>
                    <div className="text-text text-sm">{eventLabel(b)}</div>
                    <div className="text-muted2 text-[10px]">
                      {b.sport}{b.market && b.market !== '1x2' && b.market !== 'moneyline' && b.market !== 'boost' ? ` · ${b.market}` : ''}
                      {b.start_time ? ` · ${formatDateTime(b.start_time)}` : b.placed_at ? ` · ${formatDateTime(b.placed_at)}` : ''}
                    </div>
                  </td>
                  <td className="text-right text-text text-sm">{resolveOutcome(b)}</td>
                  <td className="text-right text-muted text-sm">{formatProviderName(b.provider)}</td>
                  <td className="text-right text-text text-sm font-medium">{b.odds.toFixed(2)}</td>
                  <td className="text-right text-text text-sm font-medium">
                    {b.stake.toFixed(0)} kr
                    {b.is_bonus && <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>}
                  </td>
                  {activeCategory === 'ft' ? (
                    <>
                      <td className={`text-right text-sm font-medium ${resultBadge(b.result)}`}>
                        {b.result}
                      </td>
                      <td className={`text-right text-sm font-medium ${pnlColor(b.profit)}`}>
                        {`${pnlSign(b.profit)}${b.profit.toFixed(0)} kr`}
                      </td>
                    </>
                  ) : activeCategory === 'live' ? (
                    <td className="text-right text-sm text-warning">
                      {b.home_score != null && b.away_score != null
                        ? `${b.home_score}-${b.away_score}`
                        : b.match_minute != null
                          ? `${b.match_minute}'`
                          : 'LIVE'}
                    </td>
                  ) : (
                    <td className="text-right text-sm font-medium" style={{ color }}>
                      {b.edge_pct != null ? `+${b.edge_pct.toFixed(1)}%` : b.placed_edge_pct != null ? `+${(b.placed_edge_pct * 100).toFixed(1)}%` : '-'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
