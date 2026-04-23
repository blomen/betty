import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import { api } from '@/services/api';
import { useBetMutations } from '@/hooks/useBetMutations';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
import { resolveOutcome as resolveOutcomeBase, fmtAmount, SPORT_DURATION, DEFAULT_DURATION } from '@/utils/betting';
import { ProviderName } from './ProviderName';
import { SearchInput } from './FilterBar';
import { TAB_COLORS } from './TabBar';
import { usePersistedState } from '@/hooks/usePersistedState';
import type { Bet } from '@/types';

type BetCategory = 'upcoming' | 'live' | 'ft';

interface MyBetsSectionProps {
  /** Filter function to select only bets relevant to this page */
  filter: (bet: Bet) => boolean;
  /** Color key from TAB_COLORS (e.g. 'value', 'success', 'reverse') */
  colorKey: string;
  /** Unique prefix for localStorage persistence (e.g. 'value', 'arb') */
  persistKey?: string;
}

export function MyBetsSection({ filter, colorKey, persistKey }: MyBetsSectionProps) {
  const pk = persistKey ? `bbq_mybets_${persistKey}` : null;
  const [bets, setBets] = useState<Bet[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeCategory, setActiveCategory] = usePersistedState<BetCategory>(pk ? `${pk}_category` : '', 'upcoming');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [settling, setSettling] = useState<number | null>(null);
  // Settlement selection: pre-filled from predicted_result, editable before confirm
  const [settleSelection, setSettleSelection] = useState<Record<number, string>>({});
  // Inline cell editing (click odds/stake to edit directly in the table)
  const [inlineEdit, setInlineEdit] = useState<{ id: number; field: 'odds' | 'stake' } | null>(null);
  const [inlineValue, setInlineValue] = useState('');
  const [search, setSearch] = usePersistedState(pk ? `${pk}_search` : '', '');
  // Cashout state
  const [cashoutBetId, setCashoutBetId] = useState<number | null>(null);
  const [cashoutAmount, setCashoutAmount] = useState('');

  const { editBet } = useBetMutations();
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

  // Pre-fill settle selections from predicted_result when bets change
  useEffect(() => {
    const initial: Record<number, string> = {};
    for (const b of bets) {
      if (b.predicted_result && !settleSelection[b.id]) {
        initial[b.id] = b.predicted_result;
      }
    }
    if (Object.keys(initial).length > 0) {
      setSettleSelection(prev => ({ ...initial, ...prev }));
    }
  }, [bets]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSettle = async (bet: Bet, result: 'won' | 'lost' | 'void') => {
    setSettling(bet.id);
    try {
      await editBet.mutateAsync({ betId: bet.id, data: { result } });
      setExpandedId(null);
      await fetchBets();
    } catch (err) {
      console.error('Settle failed:', err);
    } finally {
      setSettling(null);
    }
  };

  const startInlineEdit = (bet: Bet, field: 'odds' | 'stake') => {
    setInlineEdit({ id: bet.id, field });
    const isUsd = bet.currency === 'USD' || bet.currency === 'USDC';
    setInlineValue(field === 'odds' ? bet.odds.toFixed(2) : bet.stake.toFixed(isUsd ? 2 : 0));
  };

  const cancelInlineEdit = () => {
    setInlineEdit(null);
    setInlineValue('');
  };

  const saveInlineEdit = async () => {
    if (!inlineEdit) return;
    const val = parseFloat(inlineValue);
    if (isNaN(val) || val <= 0) { cancelInlineEdit(); return; }
    const bet = bets.find(b => b.id === inlineEdit.id);
    if (!bet) { cancelInlineEdit(); return; }
    const changes: { stake?: number; odds?: number } = {};
    if (inlineEdit.field === 'odds' && Math.abs(val - bet.odds) > 0.001) changes.odds = val;
    if (inlineEdit.field === 'stake' && Math.abs(val - bet.stake) > 0.5) changes.stake = val;
    if (Object.keys(changes).length === 0) { cancelInlineEdit(); return; }
    try {
      await editBet.mutateAsync({ betId: inlineEdit.id, data: changes });
      cancelInlineEdit();
      fetchBets();
    } catch (err) {
      console.error('Inline edit failed:', err);
    }
  };

  /** Get odds value — live from edit input if editing, else from bet */
  const getEditOdds = (b: Bet) => {
    if (inlineEdit?.id === b.id && inlineEdit.field === 'odds') {
      const v = parseFloat(inlineValue);
      return isNaN(v) || v <= 0 ? b.odds : v;
    }
    return b.odds;
  };

  /** Get stake value — live from edit input if editing, else from bet */
  const getEditStake = (b: Bet) => {
    if (inlineEdit?.id === b.id && inlineEdit.field === 'stake') {
      const v = parseFloat(inlineValue);
      return isNaN(v) || v <= 0 ? b.stake : v;
    }
    return b.stake;
  };

  const categorized = useMemo(() => {
    const now = Date.now();
    const upcoming: Bet[] = [];
    const live: Bet[] = [];
    const ft: Bet[] = [];

    for (const b of bets) {
      const startMs = b.start_time ? new Date(b.start_time).getTime() : null;

      if (!startMs || startMs > now) {
        // Bets with no start_time: use placed_at + 3h as proxy for "game finished"
        if (!startMs && b.placed_at) {
          const placedMs = new Date(b.placed_at).getTime();
          if (now - placedMs > 3 * 3600000) {
            ft.push(b);
            continue;
          }
        }
        upcoming.push(b);
      } else if (b.match_status === 'finished') {
        ft.push(b);
      } else if (b.match_status === 'live') {
        live.push(b);
      } else {
        // No explicit status — use sport duration heuristic
        // Bets without an event (manual/boost) default to 2.5h (football, most common)
        const fallback = b.event_id ? DEFAULT_DURATION : 2.5 * 3600000;
        const duration = SPORT_DURATION[b.sport ?? ''] ?? fallback;
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

  const activeBets = useMemo(() => {
    let result = categorized[activeCategory];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(b =>
        (b.home_team?.toLowerCase().includes(q)) ||
        (b.away_team?.toLowerCase().includes(q)) ||
        (b.display_home?.toLowerCase().includes(q)) ||
        (b.display_away?.toLowerCase().includes(q)) ||
        b.provider.toLowerCase().includes(q) ||
        (b.sport?.toLowerCase().includes(q)) ||
        (b.league?.toLowerCase().includes(q))
      );
    }
    return result;
  }, [categorized, activeCategory, search]);

  // ── Raise: add initial stake again at current odds ──
  const handleRaise = async (bet: Bet) => {
    const currentOdds = bet.current_odds ?? bet.odds;
    const raiseAmount = bet.stake; // add current stake again
    const newStake = bet.stake + raiseAmount;
    const newAvgOdds = (bet.stake * bet.odds + raiseAmount * currentOdds) / newStake;
    try {
      await editBet.mutateAsync({ betId: bet.id, data: { stake: newStake, odds: parseFloat(newAvgOdds.toFixed(2)) } });
      fetchBets();
    } catch (err) {
      console.error('Raise failed:', err);
    }
  };

  const startCashout = (bet: Bet) => {
    setCashoutBetId(bet.id);
    setCashoutAmount('');
  };

  const cancelCashout = () => {
    setCashoutBetId(null);
    setCashoutAmount('');
  };

  const confirmCashout = async (betId: number) => {
    const amount = parseFloat(cashoutAmount);
    if (isNaN(amount) || amount < 0) return;
    try {
      await editBet.mutateAsync({ betId, data: { result: 'void', payout: amount } });
      cancelCashout();
      setExpandedId(null);
      fetchBets();
    } catch (err) {
      console.error('Cashout failed:', err);
    }
  };

  const resolveOutcome = (b: Bet): string =>
    resolveOutcomeBase(b.outcome ?? '', b, b.point);

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
      {/* Category tabs + search */}
      <div className="flex items-center gap-1 border-b border-border">
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
        <div className="ml-auto">
          <SearchInput value={search} onChange={setSearch} placeholder="Search bet..." accentColor={colorKey} />
        </div>
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
                <th style={{ width: '35%' }}>Event</th>
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
                {activeCategory === 'ft' && <th className="text-right">Close</th>}
                {activeCategory === 'ft' && <th className="text-right">CLV</th>}
                {activeCategory === 'ft' && <th className="text-right">Prob</th>}
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
                const colCount = activeCategory === 'upcoming' ? 9 : activeCategory === 'ft' ? 12 : activeCategory === 'live' ? 9 : 8;
                const isEditingOdds = inlineEdit?.id === b.id && inlineEdit.field === 'odds';
                const dynOdds = getEditOdds(b);
                const dynStake = getEditStake(b);
                const edgePct = b.edge_pct ?? b.placed_edge_pct ?? null;

                // Live odds tracking for upcoming bets
                const fairOdds = b.fair_odds ?? b.fair_odds_at_placement ?? null;

                // FT tab: use placement values (current odds are meaningless post-match)
                const ftFairOdds = b.fair_odds_at_placement ?? fairOdds;
                const ftEdgePct = b.placed_edge_pct ?? edgePct;
                const liveOdds = b.current_odds ?? b.odds;
                const liveEdge = fairOdds != null && fairOdds > 1 ? (liveOdds / fairOdds - 1) * 100 : null;
                const placedEdge = edgePct;
                const edgeDirection = liveEdge != null && placedEdge != null
                  ? (liveEdge > placedEdge + 0.5 ? 'up' : liveEdge < placedEdge - 0.5 ? 'down' : null)
                  : null;

                return (
                  <Fragment key={b.id}>
                    <tr
                      className={`cursor-pointer ${b.market === 'boost' ? 'bg-tabValue/[0.03] hover:bg-tabValue/[0.07]' : ''} ${isExpanded ? 'expanded' : ''}`}
                      onClick={() => { if (!inlineEdit) setExpandedId(isExpanded ? null : b.id); }}
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
                        <div className="text-text text-sm">
                          {b.market === 'boost' && <span className="text-tabValue mr-0.5">⚡</span>}
                          {b.market === 'boost' && b.home_team ? eventLabel(b) : b.market === 'boost' ? (b.boost_title || b.outcome || `Bet #${b.id}`) : eventLabel(b)}
                        </div>
                        <div className="text-muted2 text-[10px]">
                          {b.market === 'boost' && b.boost_title && b.home_team ? <>{b.boost_title} · </> : null}
                          {b.sport}{b.market && b.market !== '1x2' && b.market !== 'moneyline' && b.market !== 'boost' ? ` · ${b.market}` : ''}
                          {b.start_time ? ` · ${formatDateTime(b.start_time)}` : b.placed_at ? ` · ${formatDateTime(b.placed_at)}` : ''}
                        </div>
                      </td>
                      <td className="text-right text-text text-sm">
                        {b.market === 'boost' ? (
                          <span className="text-muted">boost</span>
                        ) : (
                          <>
                            {resolveOutcome(b)}
                            {b.point != null && <span className="text-muted2 text-[10px] ml-0.5">{b.point > 0 ? '+' : ''}{b.point}</span>}
                          </>
                        )}
                      </td>
                      <td className="text-right text-muted text-sm"><ProviderName name={b.provider} /></td>

                      {/* Odds column — click to edit inline */}
                      <td className="text-right" onClick={e => e.stopPropagation()}>
                        {isEditingOdds ? (
                          <input
                            type="number"
                            step="0.01"
                            className="w-16 px-1 py-0 bg-bg border border-accent/50 text-text text-sm text-right font-medium outline-none"
                            value={inlineValue}
                            onChange={e => setInlineValue(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') saveInlineEdit(); if (e.key === 'Escape') cancelInlineEdit(); }}
                            onBlur={() => saveInlineEdit()}
                            autoFocus
                          />
                        ) : (
                          <div className="flex flex-col items-end">
                            <span
                              className="text-sm font-medium text-text cursor-text hover:text-accent transition-colors"
                              onClick={() => startInlineEdit(b, 'odds')}
                            >{b.odds.toFixed(2)}</span>
                            {activeCategory === 'upcoming' && b.current_odds != null && Math.abs(b.current_odds - b.odds) > 0.005 && (
                              <span className={`text-[9px] ${b.current_odds > b.odds ? 'text-success' : 'text-error'}`}>
                                now {b.current_odds.toFixed(2)}
                              </span>
                            )}
                          </div>
                        )}
                      </td>

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

                      {/* Edge / CLV column — dynamic when editing odds */}
                      {(() => {
                        // Compute dynamic edge when editing odds
                        const effectiveFair = activeCategory === 'ft' ? ftFairOdds : fairOdds;
                        const dynEdge = isEditingOdds && effectiveFair && effectiveFair > 1
                          ? (dynOdds / effectiveFair - 1) * 100
                          : null;

                        if (activeCategory === 'live') return (
                          <td className="text-right text-sm font-medium">
                            {b.clv_pct != null ? (
                              <span className={b.clv_pct >= 0 ? 'text-success' : 'text-error'}>{b.clv_pct >= 0 ? '+' : ''}{b.clv_pct.toFixed(1)}%</span>
                            ) : <span className="text-muted">-</span>}
                          </td>
                        );

                        if (activeCategory === 'upcoming') {
                          const displayEdge = dynEdge ?? (liveEdge != null ? liveEdge : placedEdge);
                          return (
                            <td className="text-right text-sm font-medium">
                              {displayEdge != null ? (
                                <span className={`${displayEdge >= 0 ? 'text-success' : 'text-error'} ${isEditingOdds ? 'transition-colors' : ''}`}>
                                  {displayEdge >= 0 ? '+' : ''}{displayEdge.toFixed(1)}%
                                  {!isEditingOdds && edgeDirection === 'up' && <span className="text-[9px] text-success ml-0.5">&#9650;</span>}
                                  {!isEditingOdds && edgeDirection === 'down' && <span className="text-[9px] text-error ml-0.5">&#9660;</span>}
                                </span>
                              ) : <span className="text-muted">-</span>}
                            </td>
                          );
                        }

                        if (activeCategory === 'ft') {
                          const displayEdge = dynEdge ?? ftEdgePct;
                          return (
                            <td className="text-right">
                              <span className={`text-sm font-medium ${displayEdge != null && displayEdge >= 0 ? 'text-success' : 'text-error'}`}>
                                {displayEdge != null ? `${displayEdge >= 0 ? '+' : ''}${displayEdge.toFixed(1)}%` : '-'}
                              </span>
                            </td>
                          );
                        }

                        const displayEdge = dynEdge ?? edgePct;
                        return (
                          <td className={`text-right text-sm font-medium ${displayEdge != null && displayEdge >= 0 ? 'text-success' : 'text-error'}`}>
                            {displayEdge != null ? `${displayEdge >= 0 ? '+' : ''}${displayEdge.toFixed(1)}%` : '-'}
                          </td>
                        );
                      })()}

                      {/* Close / CLV / Prob — Settle tab only */}
                      {activeCategory === 'ft' && (
                        <td className="text-right">
                          {b.closing_odds != null ? (
                            <span className={`text-sm ${b.closing_odds < b.odds ? 'text-success' : b.closing_odds > b.odds ? 'text-error' : 'text-text'}`}>
                              {b.closing_odds.toFixed(2)}
                            </span>
                          ) : <span className="text-sm text-muted">-</span>}
                        </td>
                      )}
                      {activeCategory === 'ft' && (
                        <td className="text-right">
                          {b.clv_pct != null ? (
                            <span className={`text-sm font-medium ${b.clv_pct >= 0 ? 'text-success' : 'text-error'}`}>
                              {b.clv_pct >= 0 ? '+' : ''}{b.clv_pct.toFixed(1)}%
                            </span>
                          ) : <span className="text-sm text-muted">-</span>}
                        </td>
                      )}
                      {activeCategory === 'ft' && (
                        <td className="text-right">
                          {b.selection_probability != null ? (
                            <span className="text-sm text-text">{(b.selection_probability * 100).toFixed(0)}%</span>
                          ) : <span className="text-sm text-muted">-</span>}
                        </td>
                      )}

                      {/* Stake column — click to edit inline */}
                      <td className="text-right" onClick={e => e.stopPropagation()}>
                        {inlineEdit?.id === b.id && inlineEdit.field === 'stake' ? (
                          <input
                            type="number"
                            step="1"
                            className="w-16 px-1 py-0 bg-bg border border-accent/50 text-text text-sm text-right font-medium outline-none"
                            value={inlineValue}
                            onChange={e => setInlineValue(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') saveInlineEdit(); if (e.key === 'Escape') cancelInlineEdit(); }}
                            onBlur={() => saveInlineEdit()}
                            autoFocus
                          />
                        ) : (
                          <span className="inline-flex items-center gap-1 justify-end">
                            <span
                              className="text-text text-sm font-medium cursor-text hover:text-accent transition-colors"
                              onClick={() => startInlineEdit(b, 'stake')}
                            >{fmtAmount(b.stake, b.currency)}</span>
                            {b.is_bonus && <span className="text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>}
                            {activeCategory === 'upcoming' && b.current_odds != null && b.current_odds > b.odds && (
                              <button
                                className="text-[9px] px-1 py-0 bg-success/20 text-success hover:bg-success/35 transition-colors font-bold"
                                onClick={() => handleRaise(b)}
                                title={`Raise +${fmtAmount(b.stake, b.currency)} at ${b.current_odds!.toFixed(2)} odds`}
                              >r</button>
                            )}
                          </span>
                        )}
                      </td>

                      {/* Return column — dynamic when editing odds/stake */}
                      <td className="text-right text-sm font-medium text-text">
                        {fmtAmount(dynStake * dynOdds, b.currency)}
                      </td>

                      {/* Last column: Score (live), Settle (ft), or nothing (upcoming) */}
                      {activeCategory === 'ft' ? (
                        <td className="text-right" onClick={e => e.stopPropagation()}>
                          {(() => {
                            const sel = settleSelection[b.id] || '';
                            const options: Array<{ val: string; label: string; color: string; bg: string }> = [
                              { val: 'won', label: 'W', color: 'text-success', bg: 'bg-success' },
                              { val: 'lost', label: 'L', color: 'text-error', bg: 'bg-error' },
                              { val: 'void', label: 'V', color: 'text-muted', bg: 'bg-muted' },
                            ];
                            return (
                              <span className="inline-flex gap-0.5 items-center justify-end">
                                {b.home_score != null && b.away_score != null && (
                                  <span className="text-[10px] text-muted mr-1">{b.home_score}-{b.away_score}</span>
                                )}
                                {options.map(o => (
                                  <button
                                    key={o.val}
                                    className={`text-[10px] px-1.5 py-0.5 transition-colors ${
                                      sel === o.val
                                        ? `${o.bg}/30 ${o.color} font-bold ring-1 ring-current`
                                        : `${o.bg}/10 ${o.color}/50 hover:${o.bg}/20`
                                    }`}
                                    onClick={() => setSettleSelection(prev => ({ ...prev, [b.id]: o.val }))}
                                  >{o.label}</button>
                                ))}
                                <button
                                  className={`text-[10px] px-2 py-0.5 ml-0.5 transition-colors ${
                                    sel && settling === null
                                      ? 'bg-accent/20 text-accent hover:bg-accent/35 font-bold'
                                      : 'bg-border/30 text-muted cursor-not-allowed'
                                  }`}
                                  disabled={!sel || settling !== null}
                                  onClick={() => sel && settling === null && handleSettle(b, sel as 'won' | 'lost' | 'void')}
                                >{settling === b.id ? '...' : 'OK'}</button>
                              </span>
                            );
                          })()}
                        </td>
                      ) : activeCategory === 'live' ? (
                        <td className="text-right">
                          <div className="flex flex-col items-end">
                            {b.home_score != null && b.away_score != null ? (
                              <span className="text-sm font-medium text-warning">{b.home_score}-{b.away_score}</span>
                            ) : (
                              <span className="text-sm text-warning">LIVE</span>
                            )}
                            {b.match_period != null && (
                              <span className="text-[9px] text-muted">{b.match_period}</span>
                            )}
                            {b.match_period == null && b.match_minute != null && (
                              <span className="text-[9px] text-muted">{b.match_minute}'</span>
                            )}
                          </div>
                        </td>
                      ) : null}
                    </tr>
                    {isExpanded && (
                      <tr key={`${b.id}-x`}>
                        <td colSpan={colCount} className="!p-0" onClick={e => e.stopPropagation()}>
                          <div className="px-3 py-2 bg-panel space-y-2">
                            {b.market === 'boost' && b.outcome && (
                              <div className="text-xs text-muted2">
                                <span className="uppercase tracking-wider text-muted">Condition: </span>
                                <span className="text-text">{b.outcome}</span>
                              </div>
                            )}
                            {cashoutBetId === b.id ? (
                              <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1">
                                  <span className="text-muted2 uppercase tracking-wider">Cashout Amount:</span>
                                  <input
                                    type="number"
                                    step="1"
                                    className="w-24 px-1.5 py-0.5 bg-bg border border-border text-text text-sm"
                                    value={cashoutAmount}
                                    onChange={e => setCashoutAmount(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') confirmCashout(b.id); if (e.key === 'Escape') cancelCashout(); }}
                                    placeholder={fmtAmount(b.stake, b.currency)}
                                    autoFocus
                                  />
                                  <span className="text-muted2">{b.currency === 'USD' || b.currency === 'USDC' ? '$' : 'kr'}</span>
                                </div>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                  onClick={() => confirmCashout(b.id)}
                                >Confirm</button>
                                <button
                                  className="text-[10px] px-2 py-0.5 bg-muted/15 text-muted hover:bg-muted/30 transition-colors"
                                  onClick={cancelCashout}
                                >Cancel</button>
                              </div>
                            ) : (
                              <div className="flex items-center gap-2 text-xs">
                                <button
                                  className="text-[10px] px-1.5 py-0.5 bg-warning/15 text-warning hover:bg-warning/30 transition-colors"
                                  onClick={() => startCashout(b)}
                                >Cashout</button>
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
