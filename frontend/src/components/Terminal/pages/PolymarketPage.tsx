import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import { api } from '@/services/api';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
import { useRefreshOnExtraction, useExtractionFreshness } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, FreshnessIndicator } from '../FilterBar';
import { MyBetsSection } from '../MyBetsSection';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { PolymarketValueBet, Bet } from '@/types';

const polyBetFilter = (b: Bet) => b.provider === 'polymarket';

type PolyTab = 'value' | 'mybets';

export function PolymarketPage() {
  const freshness = useExtractionFreshness();
  const [activeTab, setActiveTab] = useState<PolyTab>('value');

  // Value bets state
  const [valueBets, setValueBets] = useState<PolymarketValueBet[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);

  // Odds editing state (in expanded row only)
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  // Two-step bet flow (uniform with ValuePage)
  const [pendingBet, setPendingBet] = useState<{
    vb: PolymarketValueBet;
    actualCents: number;
  } | null>(null);

  // Track placed market+outcome combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = useState<Set<string>>(new Set());
  const [myBetsCount, setMyBetsCount] = useState<number | null>(null);

  // ──────────────────── Value Bets ────────────────────

  // Load placed bets from DB on mount to filter out already-bet market+outcome combos
  useEffect(() => {
    api.getBets('pending', 500).then(({ bets }) => {
      const keys = new Set<string>();
      for (const b of bets) {
        if (b.event_id && b.provider === 'polymarket') {
          keys.add(`${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`);
        }
      }
      if (keys.size > 0) setPlacedKeys(keys);
      setMyBetsCount(bets.filter(polyBetFilter).length);
    }).catch(() => {});
  }, []);

  const fetchValueData = useCallback(async () => {
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

  useEffect(() => { fetchValueData(); }, [fetchValueData]);

  useRefreshOnExtraction(fetchValueData);

  const handleSelectOpp = (idx: number) => {
    setSelectedOpp(selectedOpp === idx ? null : idx);
    setPendingBet(null);
  };

  const getOddsKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.outcome}|${vb.market}|${vb.point ?? ''}`;

  const getEffectiveOdds = (vb: PolymarketValueBet) =>
    oddsOverride[getOddsKey(vb)] ?? vb.polymarket_odds;

  const getPlacedKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.market}|${vb.outcome}|${vb.point ?? ''}`;

  // Convert decimal odds to price in cents (1/odds * 100)
  const oddsToCents = (odds: number) => odds > 0 ? Math.round(1 / odds * 100) : 0;

  const getMetrics = (vb: PolymarketValueBet) => {
    const effectiveOdds = getEffectiveOdds(vb);
    const priceCents = oddsToCents(effectiveOdds);
    const fairCents = vb.fair_price_cents ?? oddsToCents(vb.fair_odds);
    const edgePct = vb.fair_odds > 1 ? (effectiveOdds / vb.fair_odds - 1) * 100 : vb.edge_pct;
    const stakeUsdc = vb.final_stake_usdc ?? 0;
    const shares = priceCents > 0 ? stakeUsdc / (priceCents / 100) : 0;
    const payoutUsdc = shares * 1.0;
    const profitUsdc = payoutUsdc - stakeUsdc;
    return { priceCents, fairCents, edgePct, stakeUsdc, shares, payoutUsdc, profitUsdc };
  };

  // Two-step bet: step 1 — start
  const startPlaceBet = (vb: PolymarketValueBet) => {
    const stake = vb.final_stake;
    if (!stake || stake <= 0) return;
    const odds = getEffectiveOdds(vb);
    setBetError(null);
    setBetSuccess(null);
    setPendingBet({ vb, actualCents: oddsToCents(odds) });
  };

  // Two-step bet: step 2 — confirm
  const confirmPlaceBet = async () => {
    if (!pendingBet) return;
    const { vb, actualCents } = pendingBet;
    const stake = vb.final_stake;
    if (!stake || stake <= 0 || actualCents < 1) return;
    const actualOdds = 100 / actualCents;
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
      const outcomeLabel = resolveOutcome(vb);
      const stakeUsdc = vb.final_stake_usdc ?? 0;
      setBetSuccess(`Recorded: $${stakeUsdc.toFixed(2)} on ${outcomeLabel} @ ${actualCents}¢ (Polymarket)`);
      setTimeout(() => setBetSuccess(null), 5000);

      setPlacedKeys(prev => new Set(prev).add(getPlacedKey(vb)));
      setMyBetsCount(prev => (prev ?? 0) + 1);
      setPendingBet(null);
      setSelectedOpp(null);
      fetchValueData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  const resolveOutcome = (vb: PolymarketValueBet): string => {
    const point = 'point' in vb && vb.point != null ? ` ${vb.point}` : '';
    if (vb.outcome === 'home') return `${displayTeamName(vb.home_team, vb.display_home)}${point}`;
    if (vb.outcome === 'away') return `${displayTeamName(vb.away_team, vb.display_away)}${point}`;
    if (vb.outcome === 'draw') return 'Draw';
    if (vb.outcome === 'over') return `Over${point}`;
    if (vb.outcome === 'under') return `Under${point}`;
    return vb.outcome ?? '?';
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

  type PolySortCol = 'odds' | 'fair' | 'prob' | 'stake' | 'edge' | 'ttk';
  const polySortExtractors = useMemo(() => ({
    odds:  (vb: PolymarketValueBet) => vb.polymarket_odds,
    fair:  (vb: PolymarketValueBet) => vb.fair_odds,
    prob:  (vb: PolymarketValueBet) => vb.fair_odds > 1 ? 100 / vb.fair_odds : 0,
    stake: (vb: PolymarketValueBet) => vb.final_stake_usdc ?? 0,
    edge:  (vb: PolymarketValueBet) => vb.edge_pct,
    ttk:   (vb: PolymarketValueBet) => getTTKFromNow(vb.start_time) ?? 99999,
  }), []);
  const { sorted: sortedBets, sort: polySort, toggle: togglePolySort } =
    useTableSort<PolymarketValueBet, PolySortCol>(activeValueBets, polySortExtractors, { column: 'edge', direction: 'desc' });

  // ──────────────────── Render ────────────────────

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="polymarket" color={TAB_COLORS.polymarket} size={16} />
          Polymarket
        </h2>
      </div>

      {/* Tab Selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'value' as PolyTab, label: 'Value Bets', count: sortedBets.length },
          { id: 'mybets' as PolyTab, label: 'My Bets', count: myBetsCount },
        ]).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
              activeTab === tab.id
                ? 'border-tabPolymarket text-tabPolymarket'
                : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {tab.label}
            {tab.count != null && <span className="ml-1 text-muted">({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* ═══════════════ MY BETS TAB ═══════════════ */}
      {activeTab === 'mybets' && (
        <MyBetsSection filter={polyBetFilter} colorKey="polymarket" />
      )}

      {/* ═══════════════ VALUE BETS TAB ═══════════════ */}
      {activeTab === 'value' && <>
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

        <FilterBar>
          <FreshnessIndicator tiers={[['poly', freshness.poly], ['sharp', freshness.sharp]]} />
        </FilterBar>

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
                <SortableHeader column="odds" label="Odds" sort={polySort} onToggle={togglePolySort} />
                <SortableHeader column="fair" label="Fair" sort={polySort} onToggle={togglePolySort} />
                <SortableHeader column="prob" label="Prob" sort={polySort} onToggle={togglePolySort} />
                <SortableHeader column="ttk" label="TTK" sort={polySort} onToggle={togglePolySort} />
                <SortableHeader column="stake" label="Stake" sort={polySort} onToggle={togglePolySort} />
                <SortableHeader column="edge" label="Edge" sort={polySort} onToggle={togglePolySort} />
              </tr>
            </thead>
            <tbody>
              {sortedBets.map((vb, idx) => {
                const isSelected = selectedOpp === idx;
                const isSkipped = !!vb.skip_reason;
                const m = getMetrics(vb);
                const hasStake = m.stakeUsdc > 0;
                const oddsKey = getOddsKey(vb);
                const isOverridden = oddsKey in oddsOverride;
                const isPending = pendingBet?.vb === vb;

                return (
                  <Fragment key={`${vb.event_id}-${vb.outcome}`}>
                    <tr
                      className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                      onClick={() => !isSkipped && handleSelectOpp(idx)}
                    >
                      <td>
                        <div className="flex items-center gap-2 min-w-0 group/copy">
                          <span className="text-text text-sm truncate">{displayTeamName(vb.home_team, vb.display_home)} vs {displayTeamName(vb.away_team, vb.display_away)}</span>
                          <button
                            title="Copy event"
                            className="text-muted hover:text-text transition-colors opacity-0 group-hover/copy:opacity-100 flex-shrink-0"
                            onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(vb.home_team, vb.display_home)); }}
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                          </button>
                          {isSkipped && <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{vb.skip_reason}</span>}
                        </div>
                        <div className="text-muted2 text-[11px]">
                          {vb.sport}{vb.market && vb.market !== '1x2' && vb.market !== 'moneyline' ? ` · ${vb.market}` : ''}{vb.league ? ` · ${vb.league}` : ''}{' · '}{formatDateTime(vb.start_time)}
                        </div>
                      </td>
                      <td className="text-right text-text text-sm">{resolveOutcome(vb)}</td>
                      <td className={`text-right text-sm font-medium ${isOverridden ? 'text-tabPolymarket' : 'text-text'}`}>
                        {getEffectiveOdds(vb).toFixed(2)} <span className="text-muted text-xs font-normal">({m.priceCents}¢)</span>
                      </td>
                      <td className="text-right text-muted text-sm">
                        {vb.fair_odds.toFixed(2)} <span className="text-xs">({m.fairCents}¢)</span>
                      </td>
                      <td className="text-right text-muted text-sm">
                        {vb.fair_odds > 1 ? `${(100 / vb.fair_odds).toFixed(0)}%` : '-'}
                      </td>
                      <td className="text-right">
                        {(() => { const ttk = getTTKFromNow(vb.start_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                      </td>
                      <td className="text-right text-sm font-medium text-text">
                        {hasStake ? `$${m.stakeUsdc.toFixed(2)}` : '-'}
                      </td>
                      <td className={`text-right font-semibold text-sm ${m.edgePct > 0 ? 'text-success' : 'text-error'}`}>{m.edgePct > 0 ? '+' : ''}{m.edgePct.toFixed(1)}%</td>
                    </tr>

                    {isSelected && !isSkipped && (
                      <tr key={`${vb.event_id}-${vb.outcome}-exp`}>
                        <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                          {/* Top row: Kelly, Odds (editable), Return, Shares, Market, Line */}
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
                                  className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
                                  onBlur={(e) => {
                                    const cents = parseInt(e.target.value);
                                    if (!isNaN(cents) && cents >= 1 && cents <= 99) {
                                      setOddsOverride(prev => ({ ...prev, [oddsKey]: 100 / cents }));
                                    }
                                    setEditingOdds(null);
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                    if (e.key === 'Escape') setEditingOdds(null);
                                  }}
                                />
                              ) : (
                                <span
                                  onClick={() => setEditingOdds(oddsKey)}
                                  className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${isOverridden ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'text-text border-transparent'}`}
                                  title="Click to adjust price"
                                >
                                  {m.priceCents}¢
                                </span>
                              )}
                              {isOverridden && (
                                <button
                                  onClick={() => setOddsOverride(prev => { const next = { ...prev }; delete next[oddsKey]; return next; })}
                                  className="text-muted2 hover:text-text text-[10px] ml-0.5"
                                  title="Reset to original"
                                >
                                  x
                                </button>
                              )}
                            </div>
                            {hasStake && (
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Return: </span>
                                <span className="text-text">${m.payoutUsdc.toFixed(2)}</span>
                                <span className="text-tabPolymarket text-xs ml-1">(+${m.profitUsdc.toFixed(2)})</span>
                              </div>
                            )}
                            {m.shares > 0 && (
                              <div>
                                <span className="text-muted2 uppercase tracking-wider">Shares: </span>
                                <span className="text-text">{Math.round(m.shares)}</span>
                              </div>
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
                            {vb.event_slug && (
                              <a
                                href={`https://polymarket.com/event/${vb.event_slug}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-tabPolymarket hover:underline text-xs ml-auto"
                              >
                                Polymarket &#8599;
                              </a>
                            )}
                          </div>
                          {/* Bottom row: two-step bet flow (uniform with ValuePage) */}
                          <div className="px-3 py-2 bg-panel flex items-center gap-2">
                            {isPending ? (
                              <>
                                <span className="text-muted text-xs">{m.priceCents}¢</span>
                                <button
                                  onClick={confirmPlaceBet}
                                  disabled={isPlacing || pendingBet!.actualCents < 1}
                                  className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                                >
                                  {isPlacing ? '...' : 'Confirm'}
                                </button>
                                <button
                                  onClick={() => setPendingBet(null)}
                                  className="px-2 py-1.5 text-xs text-muted hover:text-text"
                                >
                                  Cancel
                                </button>
                              </>
                            ) : (
                              <button
                                onClick={() => startPlaceBet(vb)}
                                disabled={!hasStake || isPlacing}
                                className="px-4 py-1.5 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                              >
                                {isPlacing ? '...' : 'Place Bet'}
                              </button>
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
      </>}
    </div>
  );
}
