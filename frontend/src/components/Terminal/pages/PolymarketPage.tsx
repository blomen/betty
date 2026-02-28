import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { openProviderWindow } from '@/utils/providerWindow';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { useRecorder } from '@/contexts/RecorderContext';
import { SortableHeader } from '../SortableHeader';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { PolymarketValueBet } from '@/types';

export function PolymarketPage() {
  const { startAutoRecord, stopAutoRecord } = useRecorder();
  const [valueBets, setValueBets] = useState<PolymarketValueBet[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);

  // Odds editing state (uniform with ValuePage)
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  // Two-step placement: tracks which bet is awaiting confirm after browser opened
  const [pendingBet, setPendingBet] = useState<{
    idx: number;
    vb: PolymarketValueBet;
    actualOdds: number;
    navUrl: string | null;
    windowName: string;
  } | null>(null);

  // Track placed event+provider combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = useState<Set<string>>(new Set());

  // Load placed bets from DB on mount to filter out already-bet events
  useEffect(() => {
    api.getBets('pending', 500).then(({ bets }) => {
      const keys = new Set<string>();
      for (const b of bets) {
        if (b.event_id && b.provider === 'polymarket') keys.add(`${b.event_id}|polymarket`);
      }
      if (keys.size > 0) setPlacedKeys(keys);
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const valueRes = await api.getPolymarketValue(3, undefined, 50);
      setValueBets(valueRes.value_bets);
    } catch (err) {
      console.error('Failed to fetch Polymarket data:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  const handleSelectOpp = (idx: number) => {
    setSelectedOpp(selectedOpp === idx ? null : idx);
    setPendingBet(null);
  };

  const getOddsKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.outcome}|${vb.market}|${vb.point ?? ''}`;

  const getEffectiveOdds = (vb: PolymarketValueBet) =>
    oddsOverride[getOddsKey(vb)] ?? vb.polymarket_odds;

  const getPlacedKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|polymarket`;

  // Convert decimal odds to price in cents (1/odds * 100)
  const oddsToCents = (odds: number) => odds > 0 ? Math.round(1 / odds * 100) : 0;

  // Recalculate edge/stake/shares when price is overridden
  const getEffectiveMetrics = (vb: PolymarketValueBet) => {
    const effectiveOdds = getEffectiveOdds(vb);
    const priceCents = oddsToCents(effectiveOdds);
    const fairCents = vb.fair_price_cents ?? oddsToCents(vb.fair_odds);
    // edge = (odds / fair_odds - 1) * 100
    const edgePct = vb.fair_odds > 1 ? (effectiveOdds / vb.fair_odds - 1) * 100 : vb.edge_pct;
    // Approximate stake scaling: scale proportionally to edge change
    const edgeRatio = vb.edge_pct > 0 ? edgePct / vb.edge_pct : 1;
    const stakeUsdc = (vb.final_stake_usdc ?? 0) * Math.max(0, edgeRatio);
    const shares = priceCents > 0 ? stakeUsdc / (priceCents / 100) : 0;
    const payoutUsdc = shares * 1.0;
    const profitUsdc = payoutUsdc - stakeUsdc;
    return { priceCents, fairCents, edgePct, stakeUsdc, shares, payoutUsdc, profitUsdc };
  };

  // Step 1: Navigate browser to Polymarket, enter "awaiting confirm" state
  const startPlaceBet = async (vb: PolymarketValueBet, idx: number) => {
    const stake = vb.final_stake;
    if (!stake || stake <= 0) return;
    const odds = getEffectiveOdds(vb);
    setIsPlacing(true);
    setBetError(null);
    setBetSuccess(null);

    try {
      let navUrl: string | null = null;
      let windowName = 'bbq_polymarket';
      try {
        const nav = await api.navigateToEvent({
          provider_id: 'polymarket',
          home_team: vb.home_team,
          away_team: vb.away_team,
          event_id: vb.event_id,
        });
        navUrl = nav.url;
        windowName = nav.window_name;
      } catch {
        // Navigation is best-effort
      }
      setPendingBet({ idx, vb, actualOdds: odds, navUrl, windowName });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to navigate';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  // Step 2: Confirm bet with actual odds
  const confirmPlaceBet = async () => {
    if (!pendingBet) return;
    const { vb, actualOdds } = pendingBet;
    const stake = vb.final_stake;
    if (!stake || stake <= 0) return;
    setIsPlacing(true);
    setBetError(null);

    try {
      await api.createBet({
        event_id: vb.event_id,
        provider_id: 'polymarket',
        market: vb.market,
        outcome: vb.outcome,
        odds: actualOdds,
        stake,
        is_bonus: false,
        utility_score: vb.edge_pct != null ? vb.edge_pct / 100 : undefined,
        selection_probability: vb.fair_odds > 1 ? 1 / vb.fair_odds : undefined,
      });
      stopAutoRecord();
      const outcomeLabel = resolveOutcome(vb);
      const stakeUsdc = vb.final_stake_usdc ?? (stake / (vb.exchange_rate_sek ?? 10.5));
      setBetSuccess(`Recorded: $${stakeUsdc.toFixed(2)} on ${outcomeLabel} @ ${oddsToCents(actualOdds)}¢ (Polymarket)`);
      setTimeout(() => setBetSuccess(null), 5000);

      // Remove from list immediately
      setPlacedKeys(prev => new Set(prev).add(getPlacedKey(vb)));
      setPendingBet(null);
      setSelectedOpp(null);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  const resolveOutcome = (vb: PolymarketValueBet): string => {
    const point = vb.point != null ? ` ${vb.point}` : '';
    if (vb.outcome === 'home' && vb.home_team) return vb.home_team;
    if (vb.outcome === 'away' && vb.away_team) return vb.away_team;
    if (vb.outcome === 'draw') return 'Draw';
    if (vb.outcome === 'over') return `Over${point}`;
    if (vb.outcome === 'under') return `Under${point}`;
    return vb.outcome;
  };

  // Remove started/imminent events and placed bets
  const activeValueBets = useMemo(() =>
    valueBets.filter(vb => {
      const ttk = getTTKFromNow(vb.start_time);
      if (ttk !== null && ttk <= 1 / 60) return false;
      if (placedKeys.has(getPlacedKey(vb))) return false;
      return true;
    }),
  [valueBets, placedKeys]);

  type PolySortCol = 'price' | 'fair' | 'stake' | 'shares' | 'edge' | 'ttk';
  const polySortExtractors = useMemo(() => ({
    price:  (vb: PolymarketValueBet) => vb.price_cents ?? oddsToCents(vb.polymarket_odds),
    fair:   (vb: PolymarketValueBet) => vb.fair_price_cents ?? oddsToCents(vb.fair_odds),
    stake:  (vb: PolymarketValueBet) => vb.final_stake_usdc ?? 0,
    shares: (vb: PolymarketValueBet) => vb.shares ?? 0,
    edge:   (vb: PolymarketValueBet) => vb.edge_pct,
    ttk:    (vb: PolymarketValueBet) => getTTKFromNow(vb.start_time) ?? 99999,
  }), []);
  const { sorted: sortedBets, sort: polySort, toggle: togglePolySort } =
    useTableSort<PolymarketValueBet, PolySortCol>(activeValueBets, polySortExtractors, { column: 'edge', direction: 'desc' });

  return (
    <div className="space-y-3">
      {/* Header — uniform with ValuePage */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="polymarket" color={TAB_COLORS.polymarket} size={16} />
          Polymarket
          <span className="text-muted text-sm font-normal ml-1">({sortedBets.length})</span>
        </h2>
      </div>

      {/* Feedback toasts */}
      {betSuccess && (
        <div className="px-3 py-2 bg-success/10 border border-success/30 text-success text-xs flex items-center justify-between">
          <span>{betSuccess}</span>
          <button onClick={() => setBetSuccess(null)} className="text-success/60 hover:text-success ml-2">x</button>
        </div>
      )}
      {betError && (
        <div className="px-3 py-2 bg-error/10 border border-error/30 text-error text-xs flex items-center justify-between">
          <span>{betError}</span>
          <button onClick={() => setBetError(null)} className="text-error/60 hover:text-error ml-2">x</button>
        </div>
      )}

      {isLoading && valueBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
      ) : sortedBets.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No Polymarket value bets found. Run extraction first.</div>
      ) : (
        <div className="border-l-2 border-tabPolymarket">
        <table className="sq">
          <thead>
            <tr>
              <th>Event</th>
              <th className="text-right">Outcome</th>
              <SortableHeader column="price" label="Price" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="fair" label="Fair" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="ttk" label="TTK" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="stake" label="Stake" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="shares" label="Shares" sort={polySort} onToggle={togglePolySort} />
              <SortableHeader column="edge" label="Edge" sort={polySort} onToggle={togglePolySort} />
            </tr>
          </thead>
          <tbody>
            {sortedBets.map((vb, idx) => {
              const isSelected = selectedOpp === idx;
              const isSkipped = !!vb.skip_reason;
              const m = getEffectiveMetrics(vb);
              const hasStake = m.stakeUsdc > 0;
              const oddsKey = getOddsKey(vb);
              const isOverridden = oddsKey in oddsOverride;

              return (
                <>
                  <tr
                    key={`${vb.event_id}-${vb.outcome}`}
                    className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => !isSkipped && handleSelectOpp(idx)}
                  >
                    <td>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">{vb.home_team} vs {vb.away_team}</span>
                        {isSkipped && <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{vb.skip_reason}</span>}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {vb.sport}{vb.market && vb.market !== '1x2' && vb.market !== 'moneyline' ? ` · ${vb.market}` : ''}{vb.league ? ` · ${vb.league}` : ''}{' · '}{formatDateTime(vb.start_time)}
                      </div>
                    </td>
                    <td className="text-right text-text text-sm">{resolveOutcome(vb)}</td>
                    <td className={`text-right text-sm font-medium ${isOverridden ? 'text-tabPolymarket' : 'text-text'}`}>{m.priceCents}¢</td>
                    <td className="text-right text-muted text-sm">{m.fairCents}¢</td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(vb.start_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium text-text">
                      {hasStake ? `$${m.stakeUsdc.toFixed(2)}` : '-'}
                    </td>
                    <td className="text-right text-sm text-muted">
                      {m.shares > 0 ? Math.round(m.shares) : '-'}
                    </td>
                    <td className="text-right text-tabPolymarket font-semibold text-sm">+{m.edgePct.toFixed(1)}%</td>
                  </tr>
                  {isSelected && !isSkipped && (() => {
                    const isPending = pendingBet?.idx === idx;
                    const pendingCents = isPending ? oddsToCents(pendingBet!.actualOdds) : 0;

                    return (
                    <tr key={`${vb.event_id}-${vb.outcome}-exp`}>
                      <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                        {/* Top row: Kelly, Price (editable), Payout, Profit, Market */}
                        <div className="px-3 py-2 bg-panel border-b border-border flex items-center gap-6 text-xs text-muted">
                          {vb.kelly_fraction != null && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Kelly: </span>
                              <span className="text-text">{(vb.kelly_fraction * 100).toFixed(1)}%</span>
                            </div>
                          )}
                          <div className="flex items-center gap-1">
                            <span className="text-muted2 uppercase tracking-wider">Price: </span>
                            {editingOdds === oddsKey ? (
                              <input
                                type="number"
                                step="1"
                                min="1"
                                max="99"
                                autoFocus
                                defaultValue={m.priceCents}
                                className="w-12 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
                                onBlur={(e) => {
                                  const cents = parseFloat(e.target.value);
                                  if (!isNaN(cents) && cents >= 1 && cents <= 99) {
                                    const newOdds = 100 / cents;
                                    setOddsOverride(prev => ({ ...prev, [oddsKey]: newOdds }));
                                  }
                                  setEditingOdds(null);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    (e.target as HTMLInputElement).blur();
                                  } else if (e.key === 'Escape') {
                                    setEditingOdds(null);
                                  }
                                }}
                              />
                            ) : (
                              <span
                                onClick={() => setEditingOdds(oddsKey)}
                                className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${isOverridden ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'text-text border-transparent'}`}
                                title="Click to adjust price in cents"
                              >
                                {m.priceCents}¢
                              </span>
                            )}
                            {isOverridden && (
                              <button
                                onClick={() => setOddsOverride(prev => {
                                  const next = { ...prev };
                                  delete next[oddsKey];
                                  return next;
                                })}
                                className="text-muted2 hover:text-text text-[10px] ml-0.5"
                                title="Reset to original"
                              >
                                x
                              </button>
                            )}
                          </div>
                          {hasStake && (
                            <>
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Shares: </span>
                                <span className="text-text">{Math.round(m.shares)}</span>
                              </div>
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Payout: </span>
                                <span className="text-text">${m.payoutUsdc.toFixed(2)}</span>
                                <span className="text-tabPolymarket text-xs ml-1">(+${m.profitUsdc.toFixed(2)})</span>
                              </div>
                            </>
                          )}
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Market: </span>
                            <span className="text-text">{vb.market}</span>
                          </div>
                          {vb.point != null && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Line: </span>
                              <span className="text-text">{vb.point}</span>
                            </div>
                          )}
                        </div>
                        {/* Bottom row: Two-step bet flow */}
                        <div className="px-3 py-2 bg-panel flex items-center gap-2">
                          {isPending ? (
                            <>
                              <button
                                onClick={() => { startAutoRecord('polymarket', 'place_bet'); openProviderWindow(pendingBet!.navUrl, pendingBet!.windowName); }}
                                className="px-2 py-1.5 text-xs text-tabPolymarket hover:text-text transition-colors"
                                title={pendingBet!.navUrl ?? 'Open Polymarket'}
                              >
                                Go&thinsp;&#8599;
                              </button>
                              <span className="text-muted text-xs">Price:</span>
                              <input
                                type="number"
                                step="1"
                                min="1"
                                max="99"
                                autoFocus
                                value={pendingCents}
                                onChange={(e) => {
                                  const cents = parseInt(e.target.value, 10);
                                  if (!isNaN(cents) && cents >= 1 && cents <= 99) {
                                    setPendingBet(prev => prev ? { ...prev, actualOdds: 100 / cents } : null);
                                  }
                                }}
                                className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-2 py-1.5 text-right focus:outline-none focus:border-tabPolymarket"
                                onKeyDown={(e) => { if (e.key === 'Enter') confirmPlaceBet(); if (e.key === 'Escape') setPendingBet(null); }}
                              />
                              <span className="text-muted text-xs">¢</span>
                              <button
                                onClick={confirmPlaceBet}
                                disabled={isPlacing || pendingBet!.actualOdds < 1.01}
                                className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                              >
                                {isPlacing ? '...' : 'Confirm'}
                              </button>
                              <button
                                onClick={() => { stopAutoRecord(); setPendingBet(null); }}
                                className="px-2 py-1.5 text-xs text-muted hover:text-text"
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={() => startPlaceBet(vb, idx)}
                              disabled={!hasStake || isPlacing}
                              className="px-4 py-1.5 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                            >
                              {isPlacing ? '...' : 'Place Bet'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    );
                  })()}
                </>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
