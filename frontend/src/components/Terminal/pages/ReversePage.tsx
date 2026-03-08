import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import { api } from '@/services/api';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
import { useRefreshOnExtraction, useExtractionFreshness, useTiersProgress } from '@/hooks/useExtractionStatus';
import { useMultiSort } from '@/hooks/useMultiSort';
import { MultiSortableHeader } from '../MultiSortableHeader';
import { FilterBar, MultiSelectDropdown, FreshnessIndicator, SearchInput } from '../FilterBar';
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
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);
  const [stakeOverride, setStakeOverride] = useState<Record<string, number>>({});
  const [editingStake, setEditingStake] = useState<string | null>(null);

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
  const [search, setSearch] = useState('');
  const [selectedLeagues, setSelectedLeagues] = useState<Set<string>>(new Set());

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

  const tiersProgress = useTiersProgress();
  const anyExtracting = tiersProgress?.any_running ?? false;
  useEffect(() => {
    if (!anyExtracting) return;
    const id = setInterval(fetchData, 30_000);
    return () => clearInterval(id);
  }, [anyExtracting, fetchData]);

  const availableLeagues = useMemo(() => {
    const set = new Set<string>();
    for (const opp of opportunities) {
      if (opp.league) set.add(opp.league);
    }
    return Array.from(set).sort();
  }, [opportunities]);

  const toggleLeague = (l: string) => {
    setSelectedLeagues(prev => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l); else next.add(l);
      return next;
    });
  };

  const filtered = useMemo(() => {
    let result = opportunities
      .filter(o => { const ttk = getTTKFromNow(o.starts_at); return ttk === null || ttk > 1 / 60; })
      .filter(o => !placedKeys.has(`${o.event_id}|${o.market}|${o.outcome1}|${o.point ?? ''}`));
    if (selectedLeagues.size > 0) {
      result = result.filter(o => o.league != null && selectedLeagues.has(o.league));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(o =>
        (o.home_team?.toLowerCase().includes(q)) ||
        (o.away_team?.toLowerCase().includes(q)) ||
        (o.display_home?.toLowerCase().includes(q)) ||
        (o.display_away?.toLowerCase().includes(q)) ||
        (o.sport?.toLowerCase().includes(q)) ||
        (o.league?.toLowerCase().includes(q))
      );
    }
    return result;
  }, [opportunities, placedKeys, selectedLeagues, search]);

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

  const marketLabel = (market: string): string => {
    if (market === 'moneyline') return 'ML';
    return market.toUpperCase();
  };

  const resolveOutcome = (opp: Opportunity): string => {
    const outcome = opp.outcome1;
    const point = opp.point != null ? ` ${opp.point}` : '';
    const tag = ` [${marketLabel(opp.market)}]`;
    if (outcome === 'home') return `${displayTeamName(opp.home_team, opp.display_home)}${point}${tag}`;
    if (outcome === 'away') return `${displayTeamName(opp.away_team, opp.display_away)}${point}${tag}`;
    if (outcome === 'draw') return `Draw${tag}`;
    if (outcome === 'over') return `Over${point}${tag}`;
    if (outcome === 'under') return `Under${point}${tag}`;
    return `${outcome}${tag}`;
  };

  const getOddsKey = (opp: Opportunity) => `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}`;

  // Enter "awaiting confirm" state for two-step bet recording
  const startPlaceBet = (opp: Opportunity) => {
    const key = getOddsKey(opp);
    const stake = stakeOverride[key] ?? opp.final_stake;
    if (!stake || stake <= 0) return;
    const odds = oddsOverride[key] ?? opp.odds1;
    setBetError(null);
    setBetSuccess(null);
    setPendingBet({ oppId: opp.id, opp, actualOdds: odds, navUrl: null, windowName: 'bbq_pinnacle' });
  };

  // Step 2: Confirm bet with actual odds
  const confirmPlaceBet = async () => {
    if (!pendingBet) return;
    const { opp, actualOdds } = pendingBet;
    const key = getOddsKey(opp);
    const stake = stakeOverride[key] ?? opp.final_stake;
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
    <div className="space-y-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="reverse" color={TAB_COLORS.reverse} size={16} />
          Reverse
        </h2>
        {activeTab === 'reverse' && (
          <SearchInput value={search} onChange={setSearch} placeholder="Search event, sport..." accentColor="tabReverse" />
        )}
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
        {availableLeagues.length > 0 && (
          <MultiSelectDropdown
            label="League"
            options={availableLeagues}
            selected={selectedLeagues}
            onToggle={toggleLeague}
            onClear={() => setSelectedLeagues(new Set())}
            accentColor="tabReverse"
          />
        )}
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
              <th style={{ width: '35%' }}>Event</th>
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
              const isSkipped = !!opp.skip_reason;
              const oppKey = getOddsKey(opp);
              const effOdds = oddsOverride[oppKey] ?? opp.odds1;
              const effStake = stakeOverride[oppKey] ?? opp.final_stake;
              const hasStake = effStake != null && effStake > 0;
              const isOddsOver = oppKey in oddsOverride;
              const isStakeOver = oppKey in stakeOverride;
              const dynEdge = opp.fair_odds && opp.fair_odds > 1
                ? (effOdds / opp.fair_odds - 1) * 100
                : opp.edge_pct ?? 0;

              return (
                <Fragment key={opp.id}>
                  <tr
                    className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => { if (!isSkipped) { setSelectedRow(isSelected ? null : idx); setPendingBet(null); setEditingOdds(null); setEditingStake(null); } }}
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
                    <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
                      {editingOdds === oppKey ? (
                        <input
                          type="number" step="0.01" autoFocus
                          defaultValue={effOdds.toFixed(2)}
                          className="w-16 bg-bg border border-tabReverse/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabReverse"
                          onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val >= 1.01) setOddsOverride(prev => ({ ...prev, [oppKey]: val })); setEditingOdds(null); }}
                          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setEditingOdds(null); }}
                        />
                      ) : (
                        <span
                          onClick={() => setEditingOdds(oppKey)}
                          className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabReverse/50 transition-colors ${isOddsOver ? 'text-tabReverse font-medium border-tabReverse/30' : 'text-text border-transparent'}`}
                          title="Click to adjust odds"
                        >
                          {effOdds.toFixed(2)}
                        </span>
                      )}
                      {isOddsOver && <button onClick={() => setOddsOverride(prev => { const next = { ...prev }; delete next[oppKey]; return next; })} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
                    </td>
                    <td className="text-right text-muted text-sm">{opp.fair_odds?.toFixed(2) || '-'}</td>
                    <td className="text-right text-muted text-sm">
                      {opp.fair_odds && opp.fair_odds > 1 ? `${(100 / opp.fair_odds).toFixed(0)}%` : '-'}
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(opp.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
                      {editingStake === oppKey ? (
                        <input
                          type="number" step="1" autoFocus
                          defaultValue={effStake?.toFixed(0) ?? '0'}
                          className="w-16 bg-bg border border-tabReverse/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabReverse"
                          onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val > 0) setStakeOverride(prev => ({ ...prev, [oppKey]: val })); setEditingStake(null); }}
                          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setEditingStake(null); }}
                        />
                      ) : (
                        <span
                          onClick={() => setEditingStake(oppKey)}
                          className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabReverse/50 transition-colors ${isStakeOver ? 'text-tabReverse font-medium border-tabReverse/30' : 'text-text border-transparent'}`}
                          title="Click to adjust stake"
                        >
                          {hasStake ? `${effStake!.toFixed(0)} kr` : '-'}
                        </span>
                      )}
                      {isStakeOver && <button onClick={() => setStakeOverride(prev => { const next = { ...prev }; delete next[oppKey]; return next; })} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
                    </td>
                    <td className={`text-right font-semibold text-sm ${dynEdge > 0 ? 'text-success' : 'text-error'}`}>{dynEdge > 0 ? '+' : ''}{dynEdge.toFixed(1)}%</td>
                  </tr>

                  {isSelected && !isSkipped && (
                    <tr key={`${opp.id}-expanded`}>
                      <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                        <div className="px-3 py-2 bg-panel flex items-center gap-2">
                          {pendingBet?.oppId === opp.id ? (
                            <>
                              <span className="text-muted text-xs">@ {pendingBet.actualOdds.toFixed(2)}</span>
                              <button onClick={confirmPlaceBet} disabled={isPlacing || pendingBet.actualOdds < 1.01} className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap">{isPlacing ? '...' : 'Confirm'}</button>
                              <button onClick={() => setPendingBet(null)} className="px-2 py-1.5 text-xs text-muted hover:text-text">Cancel</button>
                            </>
                          ) : (
                            <button onClick={() => startPlaceBet(opp)} disabled={!hasStake || isPlacing} className="px-4 py-1.5 text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap bg-tabReverse">{isPlacing ? '...' : 'Place Bet'}</button>
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
