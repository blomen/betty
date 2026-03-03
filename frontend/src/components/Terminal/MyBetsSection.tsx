import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
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
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [settling, setSettling] = useState<number | null>(null);
  // Inline edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editStake, setEditStake] = useState('');
  const [editOdds, setEditOdds] = useState('');
  const [editResult, setEditResult] = useState('');

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

  const handleSettle = async (bet: Bet, result: 'won' | 'lost' | 'void') => {
    setSettling(bet.id);
    try {
      await api.editBet(bet.id, { result });
      setExpandedId(null);
      fetchBets();
    } catch (err) {
      console.error('Settle failed:', err);
    } finally {
      setSettling(null);
    }
  };

  const startEditing = (bet: Bet) => {
    setEditingId(bet.id);
    setEditStake(bet.stake.toFixed(0));
    setEditOdds(bet.odds.toFixed(2));
    setEditResult(bet.result);
  };

  const cancelEditing = () => {
    setEditingId(null);
    setEditStake('');
    setEditOdds('');
    setEditResult('');
  };

  const saveEdit = async (betId: number) => {
    const original = bets.find(b => b.id === betId);
    if (!original) return;
    const changes: { stake?: number; odds?: number; result?: string } = {};
    const newStake = parseFloat(editStake);
    const newOdds = parseFloat(editOdds);
    if (!isNaN(newStake) && newStake !== original.stake) changes.stake = newStake;
    if (!isNaN(newOdds) && newOdds !== original.odds) changes.odds = newOdds;
    if (editResult && editResult !== original.result) changes.result = editResult;
    if (Object.keys(changes).length === 0) { cancelEditing(); return; }
    try {
      await api.editBet(betId, changes);
      cancelEditing();
      fetchBets();
    } catch (err) {
      console.error('Edit bet failed:', err);
    }
  };

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
                {activeCategory === 'ft' ? (
                  <th className="text-right">Close</th>
                ) : activeCategory === 'live' ? (
                  <th className="text-right">Current</th>
                ) : (
                  <th className="text-right">Fair</th>
                )}
                {activeCategory === 'ft' || activeCategory === 'live' ? (
                  <th className="text-right">CLV</th>
                ) : (
                  <th className="text-right">Edge</th>
                )}
                <th className="text-right">Stake</th>
                {activeCategory === 'ft' ? (
                  <>
                    <th className="text-right">Result</th>
                    <th className="text-right">P&L</th>
                  </>
                ) : activeCategory === 'live' ? (
                  <th className="text-right">Score</th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {activeBets.map(b => {
                const isExpanded = expandedId === b.id;
                const isEditing = editingId === b.id;
                const colCount = activeCategory === 'ft' ? 9 : activeCategory === 'live' ? 8 : 7;
                const edgePct = b.edge_pct ?? (b.placed_edge_pct != null ? b.placed_edge_pct * 100 : null);
                return (
                  <Fragment key={b.id}>
                    <tr
                      className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => { if (!isEditing) setExpandedId(isExpanded ? null : b.id); }}
                    >
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
                      {activeCategory === 'ft' ? (
                        <td className="text-right text-sm">
                          {b.closing_odds != null ? (
                            <span className={b.closing_odds > b.odds ? 'text-success' : b.closing_odds < b.odds ? 'text-error' : 'text-text'}>{b.closing_odds.toFixed(2)}</span>
                          ) : <span className="text-muted">-</span>}
                        </td>
                      ) : activeCategory === 'live' ? (
                        <td className="text-right text-sm">
                          {b.current_odds != null ? (
                            <span className={b.current_odds > b.odds ? 'text-success' : b.current_odds < b.odds ? 'text-error' : 'text-text'}>{b.current_odds.toFixed(2)}</span>
                          ) : <span className="text-muted">-</span>}
                        </td>
                      ) : (
                        <td className="text-right text-sm text-muted">
                          {b.fair_odds_at_placement != null ? b.fair_odds_at_placement.toFixed(2) : b.fair_odds != null ? b.fair_odds.toFixed(2) : '-'}
                        </td>
                      )}
                      {activeCategory === 'ft' || activeCategory === 'live' ? (
                        <td className="text-right text-sm font-medium">
                          {b.clv_pct != null ? (
                            <span className={b.clv_pct >= 0 ? 'text-success' : 'text-error'}>{b.clv_pct >= 0 ? '+' : ''}{b.clv_pct.toFixed(1)}%</span>
                          ) : <span className="text-muted">-</span>}
                        </td>
                      ) : (
                        <td className="text-right text-sm font-medium" style={{ color }}>
                          {edgePct != null ? `${edgePct >= 0 ? '+' : ''}${edgePct.toFixed(1)}%` : '-'}
                        </td>
                      )}
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
                      ) : null}
                    </tr>
                    {isExpanded && (
                      <tr key={`${b.id}-x`}>
                        <td colSpan={colCount} className="!p-0" onClick={e => e.stopPropagation()}>
                          <div className="px-3 py-2 bg-panel">
                            {/* Settle buttons for pending bets */}
                            {b.result === 'pending' && !isEditing && (
                              <div className="flex items-center gap-2">
                                <span className="text-[10px] text-muted2 uppercase tracking-wider mr-1">Settle:</span>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors disabled:opacity-50"
                                  onClick={() => handleSettle(b, 'won')}
                                  disabled={settling === b.id}
                                >Won</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-error/15 text-error hover:bg-error/30 transition-colors disabled:opacity-50"
                                  onClick={() => handleSettle(b, 'lost')}
                                  disabled={settling === b.id}
                                >Lost</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors disabled:opacity-50"
                                  onClick={() => handleSettle(b, 'void')}
                                  disabled={settling === b.id}
                                >Void</button>
                                <button
                                  className="text-[10px] px-1.5 py-0.5 bg-accent/15 text-accent hover:bg-accent/30 transition-colors ml-auto"
                                  onClick={() => startEditing(b)}
                                >Edit</button>
                              </div>
                            )}

                            {/* Edit button for settled bets */}
                            {b.result !== 'pending' && !isEditing && (
                              <div className="flex items-center">
                                <button
                                  className="text-[10px] px-1.5 py-0.5 bg-accent/15 text-accent hover:bg-accent/30 transition-colors ml-auto"
                                  onClick={() => startEditing(b)}
                                >Edit</button>
                              </div>
                            )}

                            {/* Inline edit form */}
                            {isEditing && (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Stake:</span>
                                  <input
                                    type="number"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editStake}
                                    onChange={e => setEditStake(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(b.id); if (e.key === 'Escape') cancelEditing(); }}
                                    autoFocus
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Odds:</span>
                                  <input
                                    type="number"
                                    step="0.01"
                                    className="w-20 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editOdds}
                                    onChange={e => setEditOdds(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(b.id); if (e.key === 'Escape') cancelEditing(); }}
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Result:</span>
                                  <select
                                    className="px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={editResult}
                                    onChange={e => setEditResult(e.target.value)}
                                  >
                                    <option value="pending">pending</option>
                                    <option value="won">won</option>
                                    <option value="lost">lost</option>
                                    <option value="void">void</option>
                                  </select>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors"
                                  onClick={() => saveEdit(b.id)}
                                >Save</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelEditing}
                                >Cancel</button>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
