import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatProviderName, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { openProviderWindow } from '@/utils/providerWindow';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useMultiSort } from '@/hooks/useMultiSort';
import { useRecorder } from '@/contexts/RecorderContext';
import { MultiSortableHeader } from '../MultiSortableHeader';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { BonusPopup } from '../BonusPopup';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Opportunity, Provider } from '@/types';

interface GroupedOpp {
  key: string;
  rep: Opportunity;
  opps: Opportunity[];
  providers: string[];
}

interface ValuePageProps {
  providers: Provider[];
}

export function ValuePage({ providers }: ValuePageProps) {
  const { startAutoRecord, stopAutoRecord } = useRecorder();
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [selectedGroup, setSelectedGroup] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);

  const [freebetPopup, setFreebetPopup] = useState<{
    opp: Opportunity;
    freebetAmount: number;
  } | null>(null);

  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [betError, setBetError] = useState<string | null>(null);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);
  const [selectedBetProvider, setSelectedBetProvider] = useState<Record<string, number>>({});

  // Two-step placement: tracks which group is awaiting confirm after browser opened
  const [pendingBet, setPendingBet] = useState<{
    groupKey: string;
    opp: Opportunity;
    actualOdds: number;
    useFreebet: boolean;
    navUrl: string | null;
    windowName: string;
  } | null>(null);

  // Track placed event+provider combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = useState<Set<string>>(new Set());

  // Load placed bets from DB on mount to filter out already-bet event+provider combos
  useEffect(() => {
    api.getBets('pending', 500).then(({ bets }) => {
      const keys = new Set<string>();
      for (const b of bets) {
        if (b.event_id) keys.add(`${b.event_id}|${b.provider}`);
      }
      if (keys.size > 0) setPlacedKeys(keys);
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3);
      setOpportunities(res.opportunities);
    } catch (err) {
      console.error('Failed to fetch value bets:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useRefreshOnExtraction(fetchData);

  const availableProviders = useMemo(() => {
    const set = new Set<string>();
    // Include all known providers (from profiles/balances)
    for (const p of providers) {
      if (p.is_enabled) set.add(p.id);
    }
    // Also include any provider appearing in current opportunities
    for (const opp of opportunities) {
      if (opp.provider1) set.add(opp.provider1);
    }
    return Array.from(set).sort();
  }, [providers, opportunities]);

  const grouped = useMemo(() => {
    let result = opportunities;
    // Remove started/imminent events (less than 1 min to kickoff)
    result = result.filter(o => {
      const ttk = getTTKFromNow(o.starts_at);
      return ttk === null || ttk > 1 / 60;
    });
    // Remove placed event+provider combos
    if (placedKeys.size > 0) {
      result = result.filter(o => !placedKeys.has(`${o.event_id}|${o.provider1}`));
    }
    if (selectedProviders.size > 0) {
      result = result.filter(o => selectedProviders.has(o.provider1));
    }

    const map = new Map<string, Opportunity[]>();
    for (const opp of result) {
      const key = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}|${opp.odds1}`;
      const arr = map.get(key);
      if (arr) arr.push(opp);
      else map.set(key, [opp]);
    }
    const groups: GroupedOpp[] = [];
    for (const [key, opps] of map) {
      groups.push({ key, rep: opps[0], opps, providers: opps.map(o => o.provider1) });
    }
    // Bonus-first when user actively filters to those providers
    const boostBonus = selectedProviders.size > 0;
    if (boostBonus) {
      groups.sort((a, b) => {
        const aBonus = a.opps.some(o =>
          selectedProviders.has(o.provider1) &&
          (o.bonus_status === 'trigger_needed' || o.bonus_status === 'freebet_available')
        ) ? 1 : 0;
        const bBonus = b.opps.some(o =>
          selectedProviders.has(o.provider1) &&
          (o.bonus_status === 'trigger_needed' || o.bonus_status === 'freebet_available')
        ) ? 1 : 0;
        return bBonus - aBonus;
      });
    }
    return groups;
  }, [opportunities, selectedProviders, placedKeys]);

  type ValueSortCol = 'odds' | 'fair' | 'prob' | 'stake' | 'edge' | 'ttk';
  const valueSortExtractors = useMemo(() => ({
    odds:  (g: GroupedOpp) => g.rep.odds1 ?? 0,
    fair:  (g: GroupedOpp) => g.rep.fair_odds ?? 0,
    prob:  (g: GroupedOpp) => g.rep.fair_odds && g.rep.fair_odds > 1 ? 100 / g.rep.fair_odds : 0,
    stake: (g: GroupedOpp) => g.rep.final_stake ?? 0,
    edge:  (g: GroupedOpp) => g.rep.edge_pct ?? 0,
    ttk:   (g: GroupedOpp) => getTTKFromNow(g.rep.starts_at) ?? 99999,
  }), []);
  const { sorted: sortedGroups, sort: valueSort, toggle: toggleValueSort } =
    useMultiSort<GroupedOpp, ValueSortCol>(grouped, valueSortExtractors, { column: 'edge', direction: 'desc' });

  const filteredCount = useMemo(() =>
    sortedGroups.reduce((acc, g) => acc + g.opps.length, 0),
  [sortedGroups]);

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  };

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const handleSelectGroup = (idx: number) => {
    setSelectedGroup(selectedGroup === idx ? null : idx);
    setPendingBet(null);
  };

  const handlePlaceBetClick = (opp: Opportunity) => {
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;

    if (opp.bonus_status === 'freebet_available') {
      // Show popup to choose freebet vs balance
      setFreebetPopup({ opp, freebetAmount: opp.bonus_amount ?? stake });
    } else {
      // Trigger or normal bet — start two-step flow
      startPlaceBet(opp, false);
    }
  };

  const getEffectiveOdds = (opp: Opportunity) => {
    const groupKey = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}`;
    return oddsOverride[groupKey] ?? opp.odds1;
  };

  // Step 1: Navigate browser to match page, enter "awaiting confirm" state
  const startPlaceBet = async (opp: Opportunity, useFreebet: boolean) => {
    const odds = getEffectiveOdds(opp);
    setIsPlacing(true);
    setFreebetPopup(null);
    setBetError(null);
    setBetSuccess(null);

    try {
      let navUrl: string | null = null;
      let windowName = `bbq_${opp.provider1}`;
      try {
        const nav = await api.navigateToEvent({
          provider_id: opp.provider1,
          provider_meta: opp.provider_meta,
          home_team: opp.home_team,
          away_team: opp.away_team,
          event_id: opp.event_id,
        });
        navUrl = nav.url;
        windowName = nav.window_name;
      } catch {
        // Navigation is best-effort
      }

      const groupKey = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}|${opp.odds1}`;
      setPendingBet({ groupKey, opp, actualOdds: odds, useFreebet, navUrl, windowName });
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
    const { opp, actualOdds, useFreebet } = pendingBet;
    const stake = opp.final_stake;
    if (!stake || stake <= 0) return;
    setIsPlacing(true);
    setBetError(null);

    try {
      // Recalculate edge based on actual placed odds vs fair odds
      const placedEdge = opp.fair_odds != null && opp.fair_odds > 1
        ? (actualOdds / opp.fair_odds - 1)
        : (opp.edge_pct != null ? opp.edge_pct / 100 : undefined);
      await api.createBet({
        event_id: opp.event_id,
        provider_id: opp.provider1,
        market: opp.market,
        outcome: opp.outcome1,
        odds: actualOdds,
        stake,
        is_bonus: useFreebet,
        bonus_type: useFreebet ? 'freebet' : undefined,
        utility_score: placedEdge,
        selection_probability: opp.fair_odds != null && opp.fair_odds > 1 ? 1 / opp.fair_odds : undefined,
      });
      const outcomeLabel = resolveOutcome(opp.outcome1, opp.home_team, opp.away_team, opp.point);
      const type = useFreebet ? 'Freebet' : opp.bonus_status === 'trigger_needed' ? 'Trigger' : 'Bet';
      setBetSuccess(`${type}: ${stake.toFixed(0)} kr on ${outcomeLabel} @ ${actualOdds.toFixed(2)} (${formatProviderName(opp.provider1)})`);
      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 5000);

      // Remove from list immediately
      setPlacedKeys(prev => new Set(prev).add(`${opp.event_id}|${opp.provider1}`));
      setPendingBet(null);
      setSelectedGroup(null);
      stopAutoRecord();
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  const resolveOutcome = (outcome: string, home?: string, away?: string, point?: number | null): string => {
    const p = point != null ? ` ${point}` : '';
    if (outcome === 'home' && home) return home;
    if (outcome === 'away' && away) return away;
    if (outcome === 'draw') return 'Draw';
    if (outcome === 'over') return `Over${p}`;
    if (outcome === 'under') return `Under${p}`;
    return outcome;
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="value" color={TAB_COLORS.value} size={16} />
          Soft
          <span className="text-muted text-sm font-normal ml-1">({filteredCount})</span>
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

      <FilterBar>
        {availableProviders.length > 0 && (
          <MultiSelectDropdown
            label="Provider"
            options={availableProviders}
            selected={selectedProviders}
            onToggle={toggleProvider}
            onClear={() => setSelectedProviders(new Set())}
            format={formatProviderName}
            accentColor="tabValue"
          />
        )}
      </FilterBar>

      {/* Value bets table */}
      {isLoading && opportunities.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Loading...
        </div>
      ) : sortedGroups.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {opportunities.length === 0
            ? 'No value bets found. Run extraction first.'
            : 'No matches for current filters.'}
        </div>
      ) : (
        <div className="border-l-2 border-tabValue">
        <table className="sq">
          <thead>
            <tr>
              <th>Event</th>
              <th className="text-right">Providers</th>
              <th className="text-right">Outcome</th>
              <MultiSortableHeader column="odds" label="Odds" sort={valueSort} onToggle={toggleValueSort} />
              <MultiSortableHeader column="fair" label="Fair" sort={valueSort} onToggle={toggleValueSort} />
              <MultiSortableHeader column="prob" label="Prob" sort={valueSort} onToggle={toggleValueSort} />
              <MultiSortableHeader column="ttk" label="TTK" sort={valueSort} onToggle={toggleValueSort} />
              <MultiSortableHeader column="stake" label="Stake" sort={valueSort} onToggle={toggleValueSort} />
              <MultiSortableHeader column="edge" label="Edge" sort={valueSort} onToggle={toggleValueSort} />
            </tr>
          </thead>
          <tbody>
            {sortedGroups.map((group, idx) => {
              const { rep, opps, providers: groupProviders } = group;
              const isSelected = selectedGroup === idx;
              const hasStake = rep.final_stake != null && rep.final_stake > 0;
              const isSkipped = opps.every(o => !!o.skip_reason);
              const providerCount = groupProviders.length;

              return (
                <>
                  <tr
                    key={group.key}
                    className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => !isSkipped && handleSelectGroup(idx)}
                  >
                    <td>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-text text-sm truncate">{rep.home_team} vs {rep.away_team}</span>
                        {isSkipped && (
                          <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{rep.skip_reason}</span>
                        )}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {rep.sport}{rep.market && rep.market !== '1x2' && rep.market !== 'moneyline' ? ` · ${rep.market}` : ''} · {formatTime(rep.starts_at)}
                      </div>
                    </td>
                    <td className="text-right text-sm min-w-0">
                      {providerCount <= 3 ? (
                        <span className="text-text truncate">{groupProviders.map(formatProviderName).join(', ')}</span>
                      ) : (
                        <span className="text-text truncate">
                          {formatProviderName(groupProviders[0])}
                          <span className="text-muted ml-1">+{providerCount - 1}</span>
                        </span>
                      )}
                    </td>
                    <td className="text-right text-text text-sm">{resolveOutcomeName(rep)}</td>
                    <td className="text-right text-text text-sm font-medium">{rep.odds1.toFixed(2)}</td>
                    <td className="text-right text-muted text-sm">{rep.fair_odds?.toFixed(2) || '-'}</td>
                    <td className="text-right text-muted text-sm">
                      {rep.fair_odds && rep.fair_odds > 1 ? `${(100 / rep.fair_odds).toFixed(0)}%` : '-'}
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(rep.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium">
                      {hasStake ? (
                        <>
                          <span className="text-text">{rep.final_stake!.toFixed(0)} kr</span>
                          {rep.bonus_status === 'trigger_needed' && (
                            <span className="ml-1 text-[9px] px-1 py-0.5 bg-warning/20 text-warning">TRG</span>
                          )}
                          {rep.bonus_status === 'freebet_available' && (
                            <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>
                          )}
                        </>
                      ) : '-'}
                    </td>
                    <td className="text-right text-tabValue font-semibold text-sm">+{rep.edge_pct?.toFixed(1)}%</td>
                  </tr>

                  {isSelected && !isSkipped && (() => {
                    const groupOddsKey = `${rep.event_id}|${rep.outcome1}|${rep.market}|${rep.point ?? ''}`;
                    const isPendingConfirm = pendingBet?.groupKey === group.key;
                    const effectiveOdds = isPendingConfirm ? pendingBet!.actualOdds : (oddsOverride[groupOddsKey] ?? rep.odds1);
                    const oddsChanged = isPendingConfirm ? effectiveOdds !== rep.odds1 : groupOddsKey in oddsOverride;
                    return (
                    <tr key={`${group.key}-expanded`}>
                      <td colSpan={9} className="!p-0" onClick={e => e.stopPropagation()}>
                        <div className="px-3 py-2 bg-panel border-b border-border flex items-center gap-6 text-xs text-muted">
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Kelly: </span>
                            <span className="text-text">{(() => {
                              if (rep.fair_odds != null && rep.fair_odds > 1 && effectiveOdds > 1) {
                                const p = 1 / rep.fair_odds;
                                const k = (p * effectiveOdds - 1) / (effectiveOdds - 1);
                                return `${(Math.max(0, k) * 100).toFixed(1)}%`;
                              }
                              return rep.kelly_fraction != null ? `${(rep.kelly_fraction * 100).toFixed(1)}%` : '-';
                            })()}</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <span className="text-muted2 uppercase tracking-wider">Odds: </span>
                            {editingOdds === groupOddsKey ? (
                              <input
                                type="number"
                                step="0.01"
                                autoFocus
                                defaultValue={effectiveOdds.toFixed(2)}
                                className="w-16 bg-bg border border-tabValue/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabValue"
                                onBlur={(e) => {
                                  const val = parseFloat(e.target.value);
                                  if (!isNaN(val) && val >= 1.01) {
                                    setOddsOverride(prev => ({ ...prev, [groupOddsKey]: val }));
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
                                onClick={() => setEditingOdds(groupOddsKey)}
                                className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabValue/50 transition-colors ${oddsChanged ? 'text-tabValue font-medium border-tabValue/30' : 'text-text border-transparent'}`}
                                title="Click to adjust odds"
                              >
                                {effectiveOdds.toFixed(2)}
                              </span>
                            )}
                            {oddsChanged && (
                              <button
                                onClick={() => setOddsOverride(prev => {
                                  const next = { ...prev };
                                  delete next[groupOddsKey];
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
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Return: </span>
                              <span className="text-text">{(rep.final_stake! * effectiveOdds).toFixed(0)} kr</span>
                              <span className="text-tabValue text-xs ml-1">(+{(rep.final_stake! * effectiveOdds - rep.final_stake!).toFixed(0)})</span>
                            </div>
                          )}
                          <div>
                            <span className="text-muted2 uppercase tracking-wider">Market: </span>
                            <span className="text-text">{rep.market}</span>
                          </div>
                          {rep.point != null && (
                            <div>
                              <span className="text-muted2 uppercase tracking-wider">Line: </span>
                              <span className="text-text">{rep.point}</span>
                            </div>
                          )}
                        </div>
                        {(() => {
                          const selIdx = selectedBetProvider[group.key] ?? 0;
                          const selOpp = opps[selIdx] || opps[0];
                          const oppHasStake = selOpp.final_stake != null && selOpp.final_stake > 0;
                          const isTrigger = selOpp.bonus_status === 'trigger_needed';
                          const isFreebet = selOpp.bonus_status === 'freebet_available';
                          const skipReason = selOpp.skip_reason;
                          const isDisabled = !oppHasStake || isPlacing || !!skipReason;
                          const btnColor = isTrigger ? 'bg-warning' : isFreebet ? 'bg-accent' : 'bg-tabValue';
                          const btnLabel = isPlacing ? '...'
                            : skipReason === 'trigger_placed' ? 'Trigger placed'
                            : skipReason === 'no_balance' ? 'No balance'
                            : isTrigger ? 'Trigger'
                            : isFreebet ? 'Freebet'
                            : 'Place Bet';
                          const isPending = pendingBet?.groupKey === group.key;

                          return (
                          <div className="px-3 py-2 bg-panel flex items-center gap-2">
                            {isPending ? (
                              <>
                                <button
                                  onClick={() => {
                                    startAutoRecord(pendingBet!.opp.provider1, 'place_bet');
                                    openProviderWindow(pendingBet!.navUrl, pendingBet!.windowName);
                                  }}
                                  className="px-2 py-1.5 text-xs text-tabValue hover:text-text transition-colors"
                                  title={pendingBet!.navUrl ?? 'Open provider'}
                                >
                                  Go&thinsp;&#8599;
                                </button>
                                <span className="text-muted text-xs">Odds:</span>
                                <input
                                  type="number"
                                  step="0.01"
                                  autoFocus
                                  value={pendingBet!.actualOdds}
                                  onChange={(e) => {
                                    const val = parseFloat(e.target.value);
                                    if (!isNaN(val)) {
                                      setPendingBet(prev => prev ? { ...prev, actualOdds: val } : null);
                                    }
                                  }}
                                  className="w-20 bg-bg border border-tabValue/50 text-text text-xs px-2 py-1.5 text-right focus:outline-none focus:border-tabValue"
                                  onKeyDown={(e) => { if (e.key === 'Enter') confirmPlaceBet(); if (e.key === 'Escape') setPendingBet(null); }}
                                />
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
                              <>
                                <select
                                  value={selIdx}
                                  onChange={(e) => setSelectedBetProvider(prev => ({ ...prev, [group.key]: Number(e.target.value) }))}
                                  className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabValue/50 cursor-pointer"
                                >
                                  {opps.map((opp, i) => {
                                    const s = opp.final_stake != null && opp.final_stake > 0 ? ` ${opp.final_stake.toFixed(0)} kr` : '';
                                    const tag = opp.bonus_status === 'trigger_needed' ? ' [TRG]'
                                      : opp.bonus_status === 'freebet_available' ? ' [FREE]'
                                      : opp.skip_reason ? ` (${opp.skip_reason})`
                                      : '';
                                    return (
                                      <option key={opp.id} value={i}>
                                        {formatProviderName(opp.provider1)}{s}{tag}
                                      </option>
                                    );
                                  })}
                                </select>
                                <button
                                  onClick={() => handlePlaceBetClick(selOpp)}
                                  disabled={isDisabled}
                                  className={`px-4 py-1.5 ${btnColor} text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap`}
                                >
                                  {btnLabel}
                                </button>
                              </>
                            )}
                          </div>
                          );
                        })()}
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

      {/* Freebet Popup */}
      {freebetPopup && (
        <BonusPopup
          title={`Freebet Available (${freebetPopup.freebetAmount.toFixed(0)} kr)`}
          onClose={() => setFreebetPopup(null)}
        >
          <div className="space-y-3">
            <table className="sq text-xs">
              <tbody>
                <tr>
                  <td className="text-muted">Match</td>
                  <td className="text-right text-text">{freebetPopup.opp.home_team} vs {freebetPopup.opp.away_team}</td>
                </tr>
                <tr>
                  <td className="text-muted">Stake</td>
                  <td className="text-right text-text">{freebetPopup.opp.final_stake?.toFixed(0)} kr @ {getEffectiveOdds(freebetPopup.opp).toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => startPlaceBet(freebetPopup.opp, true)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-accent text-bg hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                <div>Use Freebet</div>
                <div className="text-[10px] opacity-70">no deduction</div>
              </button>
              <button
                onClick={() => startPlaceBet(freebetPopup.opp, false)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text disabled:opacity-50 transition-colors"
              >
                <div>Use Balance</div>
                <div className="text-[10px] opacity-70">deduct {freebetPopup.opp.final_stake?.toFixed(0)} kr</div>
              </button>
            </div>
          </div>
        </BonusPopup>
      )}
    </div>
  );
}


function resolveOutcomeName(opp: Opportunity): string {
  const outcome = opp.outcome1;
  const point = opp.point != null ? ` ${opp.point}` : '';
  if (outcome === 'home' && opp.home_team) return opp.home_team;
  if (outcome === 'away' && opp.away_team) return opp.away_team;
  if (outcome === 'draw') return 'Draw';
  if (outcome === 'over') return `Over${point}`;
  if (outcome === 'under') return `Under${point}`;
  return outcome;
}
