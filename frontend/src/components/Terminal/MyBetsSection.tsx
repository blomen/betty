import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import { api } from '@/services/api';
import { formatProviderName, formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
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
      const res = await api.getBets('pending', 500);
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

    // Typical sport durations (ms) — used when Pinnacle hasn't set match_status
    const SPORT_DURATION: Record<string, number> = {
      football: 2.5 * 3600000,
      basketball: 3 * 3600000,
      ice_hockey: 3 * 3600000,
      tennis: 4 * 3600000,
      esports: 4 * 3600000,
      handball: 2.5 * 3600000,
      mma: 3 * 3600000,
    };
    const DEFAULT_DURATION = 3 * 3600000;

    for (const b of bets) {
      const startMs = b.start_time ? new Date(b.start_time).getTime() : null;

      if (!startMs || startMs > now) {
        upcoming.push(b);
      } else if (b.match_status === 'finished') {
        ft.push(b);
      } else if (b.match_status === 'live') {
        live.push(b);
      } else {
        // No explicit status — use sport duration heuristic
        const duration = SPORT_DURATION[b.sport ?? ''] ?? DEFAULT_DURATION;
        if (now < startMs + duration) {
          live.push(b); // Within typical game duration → probably playing
        } else {
          ft.push(b);   // Past duration → probably finished
        }
      }
    }

    // Sort: upcoming by start_time asc, live/ft by start_time desc
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
      const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
      const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
      return tb - ta;
    });

    return { upcoming, live, ft };
  }, [bets]);

  const categories: { id: BetCategory; label: string; count: number }[] = [
    { id: 'upcoming', label: 'Upcoming', count: categorized.upcoming.length },
    { id: 'live', label: 'Live', count: categorized.live.length },
    { id: 'ft', label: 'Settle', count: categorized.ft.length },
  ];

  const activeBets = categorized[activeCategory];

  // ── 2x/3x stake multiplier when odds drifted higher ──
  const handleMultiplyStake = async (bet: Bet, multiplier: number) => {
    const currentOdds = bet.current_odds ?? bet.odds;
    const additionalStake = bet.stake * (multiplier - 1);
    const newStake = bet.stake + additionalStake;
    const newAvgOdds = (bet.stake * bet.odds + additionalStake * currentOdds) / newStake;
    try {
      await api.editBet(bet.id, {
        stake: newStake,
        odds: parseFloat(newAvgOdds.toFixed(2)),
      });
      fetchBets();
    } catch (err) {
      console.error(`${multiplier}x stake failed:`, err);
    }
  };

  const resolveOutcome = (b: Bet): string => {
    const outcome = b.outcome ?? '';
    const point = b.point != null ? ` ${b.point}` : '';
    if (outcome === 'home') return displayTeamName(b.home_team, b.display_home);
    if (outcome === 'away') return displayTeamName(b.away_team, b.display_away);
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${point}`;
    if (outcome === 'under') return `Under${point}`;
    return outcome;
  };

  const eventLabel = (b: Bet): string => {
    if (b.home_team && b.away_team) return `${displayTeamName(b.home_team, b.display_home)} vs ${displayTeamName(b.away_team, b.display_away)}`;
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
          No {activeCategory === 'ft' ? 'bets to settle' : activeCategory} bets.
        </div>
      ) : (
        <div className="border-l-2" style={{ borderColor: color }}>
          <table className="sq">
            <thead>
              <tr>
                {activeCategory === 'upcoming' && <th className="text-left">TTK</th>}
                <th>Event</th>
                <th className="text-right">Outcome</th>
                <th className="text-right">Provider</th>
                <th className="text-right">Odds</th>
                {activeCategory === 'live' ? (
                  <th className="text-right">Current</th>
                ) : (
                  <th className="text-right">Fair</th>
                )}
                {activeCategory === 'live' ? (
                  <th className="text-right">CLV</th>
                ) : (
                  <th className="text-right">Edge</th>
                )}
                <th className="text-right">Stake</th>
                <th className="text-right">Return</th>
                {activeCategory === 'live' ? (
                  <th className="text-right">Score</th>
                ) : activeCategory === 'ft' ? (
                  <th className="text-right">Settle</th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {activeBets.map(b => {
                const isExpanded = expandedId === b.id;
                const isEditing = editingId === b.id;
                const colCount = activeCategory === 'upcoming' ? 9 : activeCategory === 'ft' || activeCategory === 'live' ? 9 : 8;
                const edgePct = b.edge_pct ?? b.placed_edge_pct ?? null;

                // Live odds tracking for upcoming bets
                const fairOdds = b.fair_odds ?? b.fair_odds_at_placement ?? null;

                // FT tab: use placement values (current odds are meaningless post-match)
                const ftFairOdds = b.fair_odds_at_placement ?? fairOdds;
                const ftEdgePct = b.placed_edge_pct ?? edgePct;
                const liveOdds = b.current_odds ?? b.odds;
                const liveEdge = fairOdds != null && fairOdds > 1 ? (liveOdds / fairOdds - 1) * 100 : null;
                const placedEdge = edgePct;
                const edgeIncreasing = liveEdge != null && placedEdge != null && liveEdge > placedEdge;

                return (
                  <Fragment key={b.id}>
                    <tr
                      className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => { if (!isEditing) setExpandedId(isExpanded ? null : b.id); }}
                    >
                      {/* TTK column for upcoming */}
                      {activeCategory === 'upcoming' && (() => {
                        const ttk = getTTKFromNow(b.start_time);
                        return (
                          <td className="whitespace-nowrap">
                            <span className={`text-[10px] ${getTTKColor(ttk)}`}>
                              {formatTTKLabel(ttk)}
                            </span>
                          </td>
                        );
                      })()}
                      <td>
                        <div className="text-text text-sm">{eventLabel(b)}</div>
                        <div className="text-muted2 text-[10px]">
                          {b.sport}{b.market && b.market !== '1x2' && b.market !== 'moneyline' && b.market !== 'boost' ? ` · ${b.market}` : ''}
                          {b.start_time ? ` · ${formatDateTime(b.start_time)}` : b.placed_at ? ` · ${formatDateTime(b.placed_at)}` : ''}
                        </div>
                      </td>
                      <td className="text-right text-text text-sm">
                        {resolveOutcome(b)}
                        {b.point != null && <span className="text-muted2 text-[10px] ml-0.5">{b.point > 0 ? '+' : ''}{b.point}</span>}
                      </td>
                      <td className="text-right text-muted text-sm">{formatProviderName(b.provider)}</td>

                      {/* Odds column — upcoming shows current vs placed */}
                      {activeCategory === 'upcoming' ? (
                        <td className="text-right">
                          <div className="flex flex-col items-end">
                            <span className="text-sm font-medium text-text">{b.odds.toFixed(2)}</span>
                            {b.current_odds != null && Math.abs(b.current_odds - b.odds) > 0.005 && (
                              <span className={`text-[9px] ${b.current_odds > b.odds ? 'text-success' : 'text-error'}`}>
                                now {b.current_odds.toFixed(2)}
                              </span>
                            )}
                          </div>
                        </td>
                      ) : (
                        <td className="text-right text-text text-sm font-medium">{b.odds.toFixed(2)}</td>
                      )}

                      {activeCategory === 'live' ? (
                        <td className="text-right text-sm">
                          {b.current_odds != null ? (
                            <span className={b.current_odds > b.odds ? 'text-success' : b.current_odds < b.odds ? 'text-error' : 'text-text'}>{b.current_odds.toFixed(2)}</span>
                          ) : <span className="text-muted">-</span>}
                        </td>
                      ) : (
                        <td className="text-right text-sm text-muted">
                          {(activeCategory === 'ft' ? ftFairOdds : fairOdds)?.toFixed(2) ?? '-'}
                        </td>
                      )}

                      {/* Edge / CLV column — upcoming shows live edge with arrow */}
                      {activeCategory === 'live' ? (
                        <td className="text-right text-sm font-medium">
                          {b.clv_pct != null ? (
                            <span className={b.clv_pct >= 0 ? 'text-success' : 'text-error'}>{b.clv_pct >= 0 ? '+' : ''}{b.clv_pct.toFixed(1)}%</span>
                          ) : <span className="text-muted">-</span>}
                        </td>
                      ) : activeCategory === 'upcoming' ? (
                        <td className="text-right text-sm font-medium">
                          {(() => {
                            const displayEdge = liveEdge != null ? liveEdge : placedEdge;
                            if (displayEdge != null) return (
                              <span className={displayEdge >= 0 ? 'text-success' : 'text-error'}>
                                {displayEdge >= 0 ? '+' : ''}{displayEdge.toFixed(1)}%
                                {edgeIncreasing && <span className="text-[9px] text-success ml-0.5">&#8593;</span>}
                              </span>
                            );
                            return <span className="text-muted">-</span>;
                          })()}
                        </td>
                      ) : activeCategory === 'ft' ? (
                        <td className="text-right">
                          <div className="flex flex-col items-end">
                            <span className="text-sm font-medium" style={{ color }}>
                              {ftEdgePct != null ? `${ftEdgePct >= 0 ? '+' : ''}${ftEdgePct.toFixed(1)}%` : '-'}
                            </span>
                            {b.clv_pct != null && (
                              <span className={`text-[9px] ${b.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                                CLV {b.clv_pct >= 0 ? '+' : ''}{b.clv_pct.toFixed(1)}%
                              </span>
                            )}
                          </div>
                        </td>
                      ) : (
                        <td className="text-right text-sm font-medium" style={{ color }}>
                          {edgePct != null ? `${edgePct >= 0 ? '+' : ''}${edgePct.toFixed(1)}%` : '-'}
                        </td>
                      )}

                      {/* Stake column — upcoming shows 2x/3x buttons */}
                      {activeCategory === 'upcoming' ? (
                        <td className="text-right" onClick={e => e.stopPropagation()}>
                          <span className="inline-flex items-center gap-1 justify-end">
                            <span className="text-text text-sm font-medium">{b.stake.toFixed(0)} kr</span>
                            {b.is_bonus && <span className="text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>}
                            {b.current_odds != null && b.current_odds > b.odds && (<>
                              <button
                                className="text-[9px] px-1 py-0 bg-success/20 text-success hover:bg-success/35 transition-colors font-bold"
                                onClick={() => handleMultiplyStake(b, 2)}
                                title={`Double to ${b.stake * 2} kr at ${b.current_odds!.toFixed(2)} odds`}
                              >2x</button>
                              <button
                                className="text-[9px] px-1 py-0 bg-success/20 text-success hover:bg-success/35 transition-colors font-bold"
                                onClick={() => handleMultiplyStake(b, 3)}
                                title={`Triple to ${b.stake * 3} kr at ${b.current_odds!.toFixed(2)} odds`}
                              >3x</button>
                            </>)}
                          </span>
                        </td>
                      ) : (
                        <td className="text-right text-text text-sm font-medium">
                          {b.stake.toFixed(0)} kr
                          {b.is_bonus && <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>}
                        </td>
                      )}

                      {/* Return column — always shown */}
                      <td className="text-right text-sm font-medium" style={{ color }}>
                        {(b.stake * b.odds).toFixed(0)} kr
                      </td>

                      {/* Last column: Score (live), Settle (ft), or nothing (upcoming) */}
                      {activeCategory === 'ft' ? (
                        <td className="text-right" onClick={e => e.stopPropagation()}>
                          <span className="inline-flex gap-1 items-center justify-end">
                            {b.home_score != null && b.away_score != null && (
                              <span className="text-[10px] text-muted mr-1">{b.home_score}-{b.away_score}</span>
                            )}
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-success/15 text-success hover:bg-success/30 transition-colors disabled:opacity-50"
                              onClick={() => handleSettle(b, 'won')}
                              disabled={settling === b.id}
                            >W</button>
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-error/15 text-error hover:bg-error/30 transition-colors disabled:opacity-50"
                              onClick={() => handleSettle(b, 'lost')}
                              disabled={settling === b.id}
                            >L</button>
                            <button
                              className="text-[10px] px-1.5 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors disabled:opacity-50"
                              onClick={() => handleSettle(b, 'void')}
                              disabled={settling === b.id}
                            >V</button>
                          </span>
                        </td>
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
                            {/* Edit button */}
                            {!isEditing && (
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
