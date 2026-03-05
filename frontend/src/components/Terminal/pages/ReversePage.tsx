import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
import { useRefreshOnExtraction, useExtractionFreshness } from '@/hooks/useExtractionStatus';
import { useMultiSort } from '@/hooks/useMultiSort';
import { MultiSortableHeader } from '../MultiSortableHeader';
import { FilterBar, FreshnessIndicator } from '../FilterBar';
import { MyBetsSection } from '../MyBetsSection';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Opportunity, Bet } from '@/types';

type ReverseTab = 'reverse' | 'mybets';

const reverseBetFilter = (b: Bet) => b.provider === 'pinnacle';

export function ReversePage() {
  const freshness = useExtractionFreshness();
  const [activeTab, setActiveTab] = useState<ReverseTab>('reverse');
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);

  // Two-step placement: tracks which row is awaiting confirm
  const [pendingBet, setPendingBet] = useState<{
    oppId: number;
    opp: Opportunity;
    actualOdds: number;
    navUrl: string | null;
    windowName: string;
  } | null>(null);

  // Track placed market+outcome+point combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = useState<Set<string>>(new Set());
  const [myBetsCount, setMyBetsCount] = useState<number | null>(null);

  // Load placed bets from DB on mount to filter out already-bet market+outcome+point combos
  useEffect(() => {
    api.getBets('pending', 500).then(({ bets }) => {
      const keys = new Set<string>();
      for (const b of bets) {
        if (b.event_id && b.provider === 'pinnacle') {
          keys.add(`${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`);
        }
      }
      if (keys.size > 0) setPlacedKeys(keys);
      setMyBetsCount(bets.filter(reverseBetFilter).length);
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await api.getOpportunities('reverse_value', true, undefined, undefined, undefined, undefined, undefined, 3);
      setOpportunities(res.opportunities);
    } catch (err) {
      console.error('Failed to fetch reverse value bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  const filtered = useMemo(() => {
    return opportunities
      .filter(o => { const ttk = getTTKFromNow(o.starts_at); return ttk === null || ttk > 1 / 60; })
      .filter(o => !placedKeys.has(`${o.event_id}|${o.market}|${o.outcome1}|${o.point ?? ''}`));
  }, [opportunities, placedKeys]);

  type ReverseSortCol = 'odds' | 'consensus' | 'prob' | 'ttk' | 'stake' | 'edge';
  const reverseSortExtractors = useMemo(() => ({
    odds:      (o: Opportunity) => o.odds1 ?? 0,
    consensus: (o: Opportunity) => o.fair_odds ?? 0,
    prob:      (o: Opportunity) => o.fair_odds && o.fair_odds > 1 ? 100 / o.fair_odds : 0,
    ttk:       (o: Opportunity) => getTTKFromNow(o.starts_at) ?? 99999,
    stake:     (o: Opportunity) => o.final_stake ?? 0,
    edge:      (o: Opportunity) => o.edge_pct ?? 0,
  }), []);
  const { sorted, sort: reverseSort, toggle: toggleReverseSort } =
    useMultiSort<Opportunity, ReverseSortCol>(filtered, reverseSortExtractors, { column: 'edge', direction: 'desc' });

  const resolveOutcome = (opp: Opportunity): string => {
    const outcome = opp.outcome1;
    const point = opp.point != null ? ` ${opp.point}` : '';
    if (outcome === 'home') return `${displayTeamName(opp.home_team, opp.display_home)}${point}`;
    if (outcome === 'away') return `${displayTeamName(opp.away_team, opp.display_away)}${point}`;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${point}`;
    if (outcome === 'under') return `Under${point}`;
    return outcome;
  };

  // Enter "awaiting confirm" state for two-step bet recording
  const startPlaceBet = (opp: Opportunity) => {
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;
    setBetError(null);
    setBetSuccess(null);
    setPendingBet({ oppId: opp.id, opp, actualOdds: opp.odds1, navUrl: null, windowName: 'bbq_pinnacle' });
  };

  // Step 2: Confirm bet with actual odds
  const confirmPlaceBet = async () => {
    if (!pendingBet) return;
    const { opp, actualOdds } = pendingBet;
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;
    setIsPlacing(true);
    setBetError(null);

    try {
      await api.createBet({
        event_id: opp.event_id,
        provider_id: 'pinnacle',
        market: opp.market,
        outcome: opp.outcome1,
        odds: actualOdds,
        stake,
        point: opp.point,
        utility_score: opp.edge_pct != null ? opp.edge_pct / 100 : undefined,
        selection_probability: opp.fair_odds != null && opp.fair_odds > 1 ? 1 / opp.fair_odds : undefined,
      });

      const outcomeLabel = resolveOutcome(opp);
      setBetSuccess(`Placed: ${stake.toFixed(0)} kr on ${outcomeLabel} @ ${actualOdds.toFixed(2)} (Pinnacle)`);
      setTimeout(() => setBetSuccess(null), 5000);

      // Remove from list immediately
      setPlacedKeys(prev => new Set(prev).add(`${opp.event_id}|${opp.market}|${opp.outcome1}|${opp.point ?? ''}`));
      setPendingBet(null);
      setSelectedRow(null);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="reverse" color={TAB_COLORS.reverse} size={16} />
          Reverse
        </h2>
      </div>

      {/* Sub-tab selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'reverse' as ReverseTab, label: 'Reverse Bets', count: sorted.length },
          { id: 'mybets' as ReverseTab, label: 'My Bets', count: myBetsCount },
        ]).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
              activeTab === tab.id
                ? 'border-tabReverse text-tabReverse'
                : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {tab.label}
            {tab.count != null && <span className="ml-1 text-muted">({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* MyBets tab */}
      {activeTab === 'mybets' && (
        <MyBetsSection filter={reverseBetFilter} colorKey="reverse" />
      )}

      {activeTab === 'reverse' && <>
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
        <FreshnessIndicator tiers={[['soft', freshness.soft], ['sharp', freshness.sharp]]} />
      </FilterBar>

      {/* Table */}
      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : sorted.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          No reverse value bets found. Run extraction first.
        </div>
      ) : (
        <div className="border-l-2 border-tabReverse">
        <table className="sq">
          <thead>
            <tr>
              <th>Event</th>
              <th className="text-right">Outcome</th>
              <MultiSortableHeader column="odds" label="Pin Odds" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
              <MultiSortableHeader column="consensus" label="Consensus" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
              <MultiSortableHeader column="prob" label="Prob" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
              <MultiSortableHeader column="ttk" label="TTK" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
              <MultiSortableHeader column="stake" label="Stake" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
              <MultiSortableHeader column="edge" label="Edge" sort={reverseSort} onToggle={toggleReverseSort} align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((opp, idx) => {
              const isSelected = selectedRow === idx;
              const hasStake = opp.final_stake != null && opp.final_stake > 0;
              const isSkipped = !!opp.skip_reason;

              return (
                <>
                  <tr
                    key={opp.id}
                    className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => { if (!isSkipped) { setSelectedRow(isSelected ? null : idx); setPendingBet(null); } }}
                  >
                    <td>
                      <div className="flex items-center gap-2 min-w-0 group/copy">
                        <span className="text-text text-sm truncate">{displayTeamName(opp.home_team, opp.display_home)} vs {displayTeamName(opp.away_team, opp.display_away)}</span>
                        <button
                          title="Copy event"
                          className="text-muted hover:text-text transition-colors opacity-0 group-hover/copy:opacity-100 flex-shrink-0"
                          onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(opp.home_team, opp.display_home)); }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                        </button>
                        {isSkipped && <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{opp.skip_reason}</span>}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {opp.sport}{opp.market && opp.market !== '1x2' && opp.market !== 'moneyline' ? ` · ${opp.market}` : ''} · {formatDateTime(opp.starts_at)}
                      </div>
                    </td>
                    <td className="text-right text-text text-sm">{resolveOutcome(opp)}</td>
                    <td className="text-right text-text text-sm font-medium">{opp.odds1.toFixed(2)}</td>
                    <td className="text-right text-muted text-sm">{opp.fair_odds?.toFixed(2) || '-'}</td>
                    <td className="text-right text-muted text-sm">
                      {opp.fair_odds && opp.fair_odds > 1 ? `${(100 / opp.fair_odds).toFixed(0)}%` : '-'}
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(opp.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium">
                      {hasStake ? (
                        <span className="text-text">{opp.final_stake!.toFixed(0)} kr</span>
                      ) : '-'}
                    </td>
                    <td className={`text-right font-semibold text-sm ${(opp.edge_pct ?? 0) > 0 ? 'text-success' : 'text-error'}`}>{(opp.edge_pct ?? 0) > 0 ? '+' : ''}{opp.edge_pct?.toFixed(1)}%</td>
                  </tr>

                  {isSelected && !isSkipped && (
                    <tr key={`${opp.id}-expanded`}>
                      <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                        <div className="px-3 py-2 bg-panel border-b border-border flex items-center gap-6 text-xs text-muted">
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Kelly: </span>
                            <span className="text-text">{opp.kelly_fraction != null ? `${(opp.kelly_fraction * 100).toFixed(1)}%` : '-'}</span>
                          </div>
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Market: </span>
                            <span className="text-text">{opp.market}</span>
                          </div>
                          {hasStake && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Return: </span>
                              <span className="text-text">{(opp.final_stake! * opp.odds1).toFixed(0)} kr</span>
                              <span className="text-tabReverse text-xs ml-1">(+{(opp.final_stake! * opp.odds1 - opp.final_stake!).toFixed(0)})</span>
                            </div>
                          )}
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Place on: </span>
                            <span className="text-text">Pinnacle</span>
                          </div>
                        </div>
                        <div className="px-3 py-2 bg-panel flex items-center gap-2">
                          {pendingBet?.oppId === opp.id ? (
                            <>
                              <span className="text-muted text-xs">Odds:</span>
                              <input
                                type="number"
                                step="0.01"
                                autoFocus
                                value={pendingBet.actualOdds}
                                onChange={(e) => {
                                  const val = parseFloat(e.target.value);
                                  if (!isNaN(val)) {
                                    setPendingBet(prev => prev ? { ...prev, actualOdds: val } : null);
                                  }
                                }}
                                className="w-20 bg-bg border border-tabReverse/50 text-text text-xs px-2 py-1.5 text-right focus:outline-none focus:border-tabReverse"
                                onKeyDown={(e) => { if (e.key === 'Enter') confirmPlaceBet(); if (e.key === 'Escape') setPendingBet(null); }}
                              />
                              <button
                                onClick={confirmPlaceBet}
                                disabled={isPlacing || pendingBet.actualOdds < 1.01}
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
                              onClick={() => startPlaceBet(opp)}
                              disabled={!hasStake || isPlacing}
                              className="px-4 py-1.5 text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap bg-tabReverse"
                            >
                              {isPlacing ? '...' : 'Place Bet'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
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
