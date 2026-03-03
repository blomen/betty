import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react';
import { api } from '@/services/api';
import type { SpecialItem, StakePreviewResult } from '@/services/api';
import { formatProviderName, formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useMultiSort } from '@/hooks/useMultiSort';
import { useTableSort } from '@/hooks/useTableSort';
import { MultiSortableHeader } from '../MultiSortableHeader';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, MultiSelectDropdown } from '../FilterBar';
import { BonusPopup } from '../BonusPopup';
import { MyBetsSection } from '../MyBetsSection';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Opportunity, Provider, Bet } from '@/types';

type ValueTab = 'value' | 'boosts' | 'mybets';

const softBetFilter = (b: Bet) =>
  b.provider !== 'pinnacle' && b.provider !== 'polymarket';

interface GroupedOpp {
  key: string;
  rep: Opportunity;
  opps: Opportunity[];
  providers: string[];
}

interface GroupedSpecial {
  key: string;
  rep: SpecialItem;
  providers: string[];
}

// LLM confidence colors
const LLM_CONFIDENCE_COLOR: Record<string, string> = {
  high: 'text-success',
  medium: 'text-sky-400',
  low: 'text-warning',
};

interface ValuePageProps {
  providers: Provider[];
}

export function ValuePage({ providers }: ValuePageProps) {
  const [activeTab, setActiveTab] = useState<ValueTab>('value');
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
  const [providerDropdownOpen, setProviderDropdownOpen] = useState<string | null>(null);
  const providerDropdownRef = useRef<HTMLDivElement>(null);

  // Close provider dropdown on outside click
  useEffect(() => {
    if (!providerDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (providerDropdownRef.current && !providerDropdownRef.current.contains(e.target as Node)) {
        setProviderDropdownOpen(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [providerDropdownOpen]);

  // Two-step placement: Place → enter actual odds → Confirm
  const [pendingBet, setPendingBet] = useState<{
    groupKey: string;
    opp: Opportunity;
    actualOdds: number;
    useFreebet: boolean;
    navUrl: string | null;
    windowName: string;
  } | null>(null);

  // --- Boosts state ---
  const [specials, setSpecials] = useState<SpecialItem[]>([]);
  const [boostFilters, setBoostFilters] = useState<{ providers: string[] } | null>(null);
  const [boostExpandedIdx, setBoostExpandedIdx] = useState<number | null>(null);
  const [boostStakePreview, setBoostStakePreview] = useState<StakePreviewResult | null>(null);
  const [isLoadingBoostPreview, setIsLoadingBoostPreview] = useState(false);
  const [boostSelectedProviders, setBoostSelectedProviders] = useState<Set<string>>(new Set());
  const [boostSelectedBetProvider, setBoostSelectedBetProvider] = useState<Record<string, number>>({});
  const [boostOddsOverride, setBoostOddsOverride] = useState<Record<string, number>>({});
  const [boostEditingOdds, setBoostEditingOdds] = useState<string | null>(null);
  const [boostPendingBet, setBoostPendingBet] = useState<{
    groupKey: string;
    special: SpecialItem;
    providerId: string;
    actualOdds: number;
    stake: number;
  } | null>(null);
  const [boostPlacedKeys, setBoostPlacedKeys] = useState<Set<string>>(new Set());

  // Track placed event+provider combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = useState<Set<string>>(new Set());
  const [myBetsCount, setMyBetsCount] = useState<number | null>(null);

  // Load placed bets from DB on mount to filter out already-bet market+outcome+point combos
  useEffect(() => {
    api.getBets('pending', 500).then(({ bets }) => {
      const keys = new Set<string>();
      const bKeys = new Set<string>();
      for (const b of bets) {
        if (b.market === 'boost' && b.outcome) {
          bKeys.add(b.outcome);
        } else if (b.event_id) {
          keys.add(`${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`);
        }
      }
      if (keys.size > 0) setPlacedKeys(keys);
      if (bKeys.size > 0) setBoostPlacedKeys(bKeys);
      setMyBetsCount(bets.filter(softBetFilter).length);
    }).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [res, boostRes] = await Promise.all([
        api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3),
        api.getSpecials({}).catch(() => null),
      ]);
      setOpportunities(res.opportunities);
      if (boostRes) {
        setSpecials(boostRes.specials || []);
        if (boostRes.filters) setBoostFilters({ providers: boostRes.filters.providers });
      }
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

  const balanceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of providers) m.set(p.id, p.balance);
    return m;
  }, [providers]);

  const hasBalance = (providerIds: string[]) =>
    providerIds.some(id => (balanceMap.get(id) ?? 0) > 0);

  const grouped = useMemo(() => {
    let result = opportunities;
    // Remove started/imminent events (less than 1 min to kickoff)
    result = result.filter(o => {
      const ttk = getTTKFromNow(o.starts_at);
      return ttk === null || ttk > 1 / 60;
    });
    // Remove placed market+outcome+point combos (same bet at any provider)
    if (placedKeys.size > 0) {
      result = result.filter(o => !placedKeys.has(`${o.event_id}|${o.market}|${o.outcome1}|${o.point ?? ''}`));
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

  // --- Boosts grouping & sorting ---
  const boostNonExpired = useMemo(() => specials.filter(s => {
    if (s.event_time) { try { if (new Date(s.event_time).getTime() <= Date.now()) return false; } catch { /* keep */ } }
    if (!s.expires_at) return true;
    try { return new Date(s.expires_at).getTime() > Date.now(); } catch { return true; }
  }), [specials]);

  const boostGrouped = useMemo(() => {
    const groups: GroupedSpecial[] = [];
    for (const s of boostNonExpired) {
      const allProviders = [s.provider, ...(s.shared_providers || [])];
      const key = `${s.provider}-${s.title}-${s.boosted_odds}`;
      if (boostPlacedKeys.has(key) || boostPlacedKeys.has(s.title)) continue;
      groups.push({ key, rep: s, providers: allProviders });
    }
    return groups;
  }, [boostNonExpired, boostPlacedKeys]);

  const boostActiveGroups = useMemo(() => {
    if (boostSelectedProviders.size === 0) return boostGrouped;
    return boostGrouped.filter(g =>
      g.providers.some(p => boostSelectedProviders.has(p.toLowerCase()))
    );
  }, [boostGrouped, boostSelectedProviders]);

  type BoostSortCol = 'odds' | 'edge' | 'aiProb' | 'aiEdge' | 'ttk' | 'max';
  const boostSortExtractors = useMemo(() => ({
    odds: (g: GroupedSpecial) => g.rep.boosted_odds ?? 0,
    aiProb: (g: GroupedSpecial) => g.rep.llm_probability ?? 0,
    aiEdge: (g: GroupedSpecial) => g.rep.llm_edge_pct ?? -999,
    max: (g: GroupedSpecial) => g.rep.max_stake ?? 0,
    edge: (g: GroupedSpecial) => g.rep.edge_pct ?? g.rep.boost_pct ?? 0,
    ttk: (g: GroupedSpecial) => getTTKFromNow(g.rep.event_time) ?? 99999,
  }), []);
  const { sorted: sortedBoosts, sort: boostSort, toggle: toggleBoostSort } =
    useTableSort<GroupedSpecial, BoostSortCol>(boostActiveGroups, boostSortExtractors, { column: 'edge', direction: 'desc' });

  const toggleBoostProvider = (p: string) => {
    setBoostSelectedProviders(prev => { const next = new Set(prev); const key = p.toLowerCase(); if (next.has(key)) next.delete(key); else next.add(key); return next; });
    setBoostExpandedIdx(null);
  };

  const handleBoostRowClick = async (idx: number, group: GroupedSpecial) => {
    if (boostExpandedIdx === idx) { setBoostExpandedIdx(null); setBoostStakePreview(null); setBoostPendingBet(null); return; }
    setBoostExpandedIdx(idx); setBoostStakePreview(null); setBoostPendingBet(null);
    const s = group.rep;
    // Use LLM edge if available, otherwise boost edge
    const edgeForStake = s.llm_edge_pct ?? s.edge_pct;
    if (!s.boosted_odds || edgeForStake == null) return;
    setIsLoadingBoostPreview(true);
    try { const preview = await api.getBoostStakePreview({ edge_pct: edgeForStake, odds: s.boosted_odds, provider_id: s.provider }); setBoostStakePreview(preview); }
    catch (err) { console.error('Failed to load stake preview:', err); }
    finally { setIsLoadingBoostPreview(false); }
  };

  const startBoostPlaceBet = (special: SpecialItem, providerId: string, groupKey: string) => {
    if (!boostStakePreview || !special.boosted_odds) return;
    let stake = boostStakePreview.recommended_stake;
    if (special.max_stake != null && stake > special.max_stake) stake = special.max_stake;
    if (stake <= 0) return;
    const odds = boostOddsOverride[groupKey] ?? special.boosted_odds;
    setBetError(null); setBetSuccess(null);
    setBoostPendingBet({ groupKey, special, providerId, actualOdds: odds, stake });
  };

  const confirmBoostPlaceBet = async () => {
    if (!boostPendingBet) return;
    const { special, providerId, actualOdds, stake, groupKey } = boostPendingBet;
    setIsPlacing(true); setBetError(null);
    try {
      await api.createBet({
        provider_id: providerId,
        market: 'boost',
        outcome: special.title,
        odds: actualOdds,
        stake,
        is_bonus: false,
        utility_score: (special.llm_edge_pct ?? special.edge_pct) != null ? (special.llm_edge_pct ?? special.edge_pct)! / 100 : undefined,
        selection_probability: special.llm_probability ?? undefined,
      });
      setBetSuccess(`Recorded: ${stake.toFixed(0)} kr on ${special.title} @ ${actualOdds.toFixed(2)} (${formatProviderName(providerId)})`);
      setTimeout(() => setBetSuccess(null), 5000);
      setBoostPlacedKeys(prev => { const next = new Set(prev); next.add(groupKey); next.add(special.title); return next; });
      setBoostPendingBet(null); setBoostExpandedIdx(null); setBoostStakePreview(null);
      fetchData();
    } catch (err) {
      setBetError(err instanceof Error ? err.message : 'Failed to place bet');
      setTimeout(() => setBetError(null), 5000);
    } finally { setIsPlacing(false); }
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

  // Enter "awaiting confirm" state for two-step bet recording
  const startPlaceBet = (opp: Opportunity, useFreebet: boolean) => {
    const odds = getEffectiveOdds(opp);
    setFreebetPopup(null);
    setBetError(null);
    setBetSuccess(null);
    const groupKey = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}|${opp.odds1}`;
    setPendingBet({ groupKey, opp, actualOdds: odds, useFreebet, navUrl: null, windowName: `bbq_${opp.provider1}` });
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
        point: opp.point,
        is_bonus: useFreebet,
        bonus_type: useFreebet ? 'freebet' : undefined,
        utility_score: placedEdge,
        selection_probability: opp.fair_odds != null && opp.fair_odds > 1 ? 1 / opp.fair_odds : undefined,
      });
      const outcomeLabel = resolveOutcome(opp.outcome1, opp, opp.point);
      const type = useFreebet ? 'Freebet' : opp.bonus_status === 'trigger_needed' ? 'Trigger' : 'Bet';
      setBetSuccess(`${type}: ${stake.toFixed(0)} kr on ${outcomeLabel} @ ${actualOdds.toFixed(2)} (${formatProviderName(opp.provider1)})`);
      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 5000);

      // Remove from list immediately (same market+outcome+point hidden across all providers)
      setPlacedKeys(prev => new Set(prev).add(`${opp.event_id}|${opp.market}|${opp.outcome1}|${opp.point ?? ''}`));
      setPendingBet(null);
      setSelectedGroup(null);
      fetchData();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      setBetError(msg);
      setTimeout(() => setBetError(null), 5000);
    } finally {
      setIsPlacing(false);
    }
  };

  const resolveOutcome = (outcome: string, opp: Opportunity, point?: number | null): string => {
    const p = point != null ? ` ${point}` : '';
    if (outcome === 'home') return `${displayTeamName(opp.home_team, opp.display_home)}${p}`;
    if (outcome === 'away') return `${displayTeamName(opp.away_team, opp.display_away)}${p}`;
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
        </h2>
      </div>

      {/* Sub-tab selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'value' as ValueTab, label: 'Value Bets', count: filteredCount, activeClass: 'border-tabValue text-tabValue' },
          { id: 'boosts' as ValueTab, label: 'Boosts', count: sortedBoosts.length, activeClass: 'border-tabBonus text-tabBonus' },
          { id: 'mybets' as ValueTab, label: 'My Bets', count: myBetsCount, activeClass: 'border-tabValue text-tabValue' },
        ]).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
              activeTab === tab.id
                ? tab.activeClass
                : 'border-transparent text-muted hover:text-text'
            }`}
          >
            {tab.label}
            {tab.count != null && <span className="ml-1 text-muted">({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* MyBets tab — all soft provider bets (value + boosts) */}
      {activeTab === 'mybets' && (
        <MyBetsSection filter={softBetFilter} colorKey="value" />
      )}

      {/* Boosts tab */}
      {activeTab === 'boosts' && <>
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

      {boostFilters && boostFilters.providers.length > 0 && (
        <FilterBar>
          <MultiSelectDropdown label="Provider" options={boostFilters.providers} selected={boostSelectedProviders} onToggle={toggleBoostProvider} onClear={() => { setBoostSelectedProviders(new Set()); setBoostExpandedIdx(null); }} format={formatProviderName} accentColor="tabBonus" />
        </FilterBar>
      )}

      {sortedBoosts.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          No active boosts. Boosts are scraped automatically every 2 hours.
        </div>
      ) : (
        <div className="border-l-2 border-tabBonus">
        <table className="sq">
          <thead>
            <tr>
              <th>Boost</th>
              <th className="text-right">Providers</th>
              <SortableHeader column="odds" label="Odds" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="edge" label="Boost" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="aiProb" label="AI Prob" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="aiEdge" label="AI Edge" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="ttk" label="TTK" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="max" label="Max" sort={boostSort} onToggle={toggleBoostSort} />
            </tr>
          </thead>
          <tbody>
            {sortedBoosts.map((group, idx) => {
              const s = group.rep;
              const isExpanded = boostExpandedIdx === idx;
              const providerCount = group.providers.length;
              const hasLLM = s.llm_probability != null;

              return (
                <Fragment key={group.key}>
                  <tr className={`cursor-pointer ${isExpanded ? 'expanded' : ''}`} onClick={() => handleBoostRowClick(idx, group)}>
                    <td>
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-text text-sm truncate">{s.title}</span>
                        {hasLLM && (
                          <span className="text-[9px] px-1 py-0.5 bg-sky-500/15 text-sky-400">AI</span>
                        )}
                      </div>
                      <div className="text-muted2 text-[11px] truncate">
                        {s.event || ''}{s.sport && s.sport !== 'unknown' ? ` · ${s.sport.replace(/_/g, ' ')}` : ''}{s.league ? ` · ${s.league}` : ''}
                        {s.event_time && isFutureDate(s.event_time) ? ` · ${formatEventTime(s.event_time)}` : s.expires_at ? ` · ${formatTimeRemaining(s.expires_at)}` : ''}
                      </div>
                    </td>
                    <td className="text-right text-sm min-w-0">
                      <span className="inline-flex items-center gap-1.5 justify-end">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasBalance(group.providers) ? 'bg-success' : 'bg-error'}`} />
                        {providerCount <= 3 ? (
                          <span className="text-text truncate">{group.providers.map(formatProviderName).join(', ')}</span>
                        ) : (
                          <span className="text-text truncate">
                            {formatProviderName(group.providers[0])}
                            <span className="text-muted ml-1">+{providerCount - 1}</span>
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {s.original_odds != null && (<><span className="text-muted line-through text-xs">{s.original_odds.toFixed(2)}</span><span className="text-muted text-xs">&rarr;</span></>)}
                        <span className="text-success font-bold text-sm">{s.boosted_odds != null ? s.boosted_odds.toFixed(2) : '-'}</span>
                      </div>
                    </td>
                    <td className="text-right">
                      {(s.edge_pct ?? s.boost_pct) != null ? (
                        <span className="font-semibold text-sm text-tabBonus">
                          +{(s.edge_pct ?? s.boost_pct)!.toFixed(0)}%
                        </span>
                      ) : <span className="text-muted2 text-sm">-</span>}
                    </td>
                    <td className="text-right text-sm">
                      {s.llm_probability != null ? (
                        <span className={LLM_CONFIDENCE_COLOR[s.llm_confidence ?? 'low'] ?? 'text-muted'}>
                          {(s.llm_probability * 100).toFixed(0)}%
                        </span>
                      ) : <span className="text-muted2">-</span>}
                    </td>
                    <td className="text-right">
                      {s.llm_edge_pct != null ? (
                        <span className={`font-semibold text-sm ${s.llm_edge_pct > 0 ? 'text-sky-400' : 'text-error'}`}>
                          {s.llm_edge_pct > 0 ? '+' : ''}{s.llm_edge_pct.toFixed(1)}%
                        </span>
                      ) : <span className="text-muted2 text-sm">-</span>}
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(s.event_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-muted text-sm">{s.max_stake != null ? `${s.max_stake.toFixed(0)} kr` : '-'}</td>
                  </tr>
                  {isExpanded && (
                    <tr key={`${group.key}-exp`}>
                      <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                        <BoostExpandedRow
                          special={s}
                          groupKey={group.key}
                          providers={group.providers}
                          stakePreview={boostStakePreview}
                          isLoadingPreview={isLoadingBoostPreview}
                          isPlacing={isPlacing}
                          pendingBet={boostPendingBet?.groupKey === group.key ? boostPendingBet : null}
                          selectedProviderIdx={boostSelectedBetProvider[group.key] ?? 0}
                          onSelectProvider={(i) => setBoostSelectedBetProvider(prev => ({ ...prev, [group.key]: i }))}
                          onStartPlaceBet={(pid) => startBoostPlaceBet(s, pid, group.key)}
                          onConfirmBet={confirmBoostPlaceBet}
                          onCancelPending={() => setBoostPendingBet(null)}
                          onUpdatePendingOdds={(val) => setBoostPendingBet(prev => prev ? { ...prev, actualOdds: val } : null)}
                          oddsOverride={boostOddsOverride[group.key] ?? null}
                          editingOdds={boostEditingOdds === group.key}
                          onEditOdds={() => setBoostEditingOdds(group.key)}
                          onSetOdds={(val) => { setBoostOddsOverride(prev => ({ ...prev, [group.key]: val })); setBoostEditingOdds(null); }}
                          onResetOdds={() => { setBoostOddsOverride(prev => { const next = { ...prev }; delete next[group.key]; return next; }); setBoostEditingOdds(null); }}
                          onCancelEdit={() => setBoostEditingOdds(null)}
                          betError={betError}
                        />
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
                <Fragment key={group.key}>
                  <tr
                    className={`cursor-pointer group ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''}`}
                    onClick={() => !isSkipped && handleSelectGroup(idx)}
                  >
                    <td>
                      <div className="flex items-center gap-1 min-w-0">
                        <span className="text-text text-sm truncate">{displayTeamName(rep.home_team, rep.display_home)} vs {displayTeamName(rep.away_team, rep.display_away)}</span>
                        <button
                          title="Copy event"
                          className="text-muted hover:text-text transition-colors opacity-0 group-hover:opacity-100 flex-shrink-0"
                          onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(rep.home_team, rep.display_home)); }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                        </button>
                        {isSkipped && (
                          <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{rep.skip_reason}</span>
                        )}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {rep.sport}{rep.market && rep.market !== '1x2' && rep.market !== 'moneyline' ? ` · ${rep.market}` : ''} · {formatDateTime(rep.starts_at)}
                      </div>
                    </td>
                    <td className="text-right text-sm min-w-0">
                      <span className="inline-flex items-center gap-1.5 justify-end">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasBalance(groupProviders) ? 'bg-success' : 'bg-error'}`} />
                        {providerCount <= 3 ? (
                          <span className="text-text truncate">{groupProviders.map(formatProviderName).join(', ')}</span>
                        ) : (
                          <span className="text-text truncate">
                            {formatProviderName(groupProviders[0])}
                            <span className="text-muted ml-1">+{providerCount - 1}</span>
                          </span>
                        )}
                      </span>
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
                                  onClick={() => setPendingBet(null)}
                                  className="px-2 py-1.5 text-xs text-muted hover:text-text"
                                >
                                  Cancel
                                </button>
                              </>
                            ) : (
                              <>
                                <div className="relative" ref={providerDropdownOpen === group.key ? providerDropdownRef : undefined}>
                                  <button
                                    type="button"
                                    onClick={() => setProviderDropdownOpen(prev => prev === group.key ? null : group.key)}
                                    className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabValue/50 cursor-pointer flex items-center gap-1.5 min-w-[120px]"
                                  >
                                    <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${(balanceMap.get(selOpp.provider1) ?? 0) > 0 ? 'bg-success' : 'bg-muted/40'}`} />
                                    <span className="truncate">
                                      {formatProviderName(selOpp.provider1)}
                                      {selOpp.final_stake != null && selOpp.final_stake > 0 ? ` ${selOpp.final_stake.toFixed(0)} kr` : ''}
                                      {selOpp.bonus_status === 'trigger_needed' ? ' [TRG]' : selOpp.bonus_status === 'freebet_available' ? ' [FREE]' : selOpp.skip_reason ? ` (${selOpp.skip_reason})` : ''}
                                    </span>
                                    <svg className="w-3 h-3 ml-auto flex-shrink-0 text-muted" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                                  </button>
                                  {providerDropdownOpen === group.key && (
                                    <div className="absolute left-0 top-full mt-0.5 z-50 bg-bg border border-border shadow-lg max-h-48 overflow-y-auto min-w-[160px]">
                                      {opps.map((opp, i) => {
                                        const s = opp.final_stake != null && opp.final_stake > 0 ? ` ${opp.final_stake.toFixed(0)} kr` : '';
                                        const tag = opp.bonus_status === 'trigger_needed' ? ' [TRG]'
                                          : opp.bonus_status === 'freebet_available' ? ' [FREE]'
                                          : opp.skip_reason ? ` (${opp.skip_reason})`
                                          : '';
                                        const hasBal = (balanceMap.get(opp.provider1) ?? 0) > 0;
                                        return (
                                          <button
                                            key={opp.id}
                                            type="button"
                                            onClick={() => {
                                              setSelectedBetProvider(prev => ({ ...prev, [group.key]: i }));
                                              setProviderDropdownOpen(null);
                                            }}
                                            className={`w-full text-left px-2 py-1.5 text-xs flex items-center gap-1.5 hover:bg-panel cursor-pointer ${i === selIdx ? 'bg-panel text-text' : 'text-muted'}`}
                                          >
                                            <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasBal ? 'bg-success' : 'bg-muted/40'}`} />
                                            {formatProviderName(opp.provider1)}{s}{tag}
                                          </button>
                                        );
                                      })}
                                    </div>
                                  )}
                                </div>
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
                </Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      </>}

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
                  <td className="text-right text-text">{displayTeamName(freebetPopup.opp.home_team, freebetPopup.opp.display_home)} vs {displayTeamName(freebetPopup.opp.away_team, freebetPopup.opp.display_away)}</td>
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
  if (outcome === 'home') return `${displayTeamName(opp.home_team, opp.display_home)}${point}`;
  if (outcome === 'away') return `${displayTeamName(opp.away_team, opp.display_away)}${point}`;
  if (outcome === 'draw') return 'Draw';
  if (outcome === 'over') return `Over${point}`;
  if (outcome === 'under') return `Under${point}`;
  return outcome;
}


// --- Boost helpers ---

function formatEventTime(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();
    if (diffMs <= 0) return 'started';
    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHrs / 24);
    if (diffHrs < 24) { if (diffMin < 60) return `in ${diffMin}m`; const remMin = diffMin % 60; return remMin > 0 ? `in ${diffHrs}h ${remMin}m` : `in ${diffHrs}h`; }
    if (diffDays < 7) return date.toLocaleDateString('sv-SE', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    return date.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

function formatTimeRemaining(isoString: string): string {
  try {
    const date = new Date(isoString);
    const diffMs = date.getTime() - Date.now();
    if (diffMs <= 0) return 'expired';
    const diffMin = Math.floor(diffMs / 60000);
    const diffHrs = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHrs / 24);
    if (diffMin < 60) return `${diffMin}m left`;
    if (diffHrs < 24) { const remMin = diffMin % 60; return remMin > 0 ? `${diffHrs}h ${remMin}m` : `${diffHrs}h left`; }
    if (diffDays < 7) return `${diffDays}d ${diffHrs % 24}h`;
    return `${diffDays}d left`;
  } catch { return ''; }
}

function isFutureDate(isoString: string): boolean {
  try { return new Date(isoString).getTime() > Date.now(); } catch { return false; }
}


// --- Boost Expanded Row ---

function BoostExpandedRow({ special, groupKey, providers, stakePreview, isLoadingPreview, isPlacing, pendingBet, selectedProviderIdx, onSelectProvider, onStartPlaceBet, onConfirmBet, onCancelPending, onUpdatePendingOdds, oddsOverride, editingOdds, onEditOdds, onSetOdds, onResetOdds, onCancelEdit, betError }: {
  special: SpecialItem;
  groupKey: string;
  providers: string[];
  stakePreview: StakePreviewResult | null;
  isLoadingPreview: boolean;
  isPlacing: boolean;
  pendingBet: { groupKey: string; special: SpecialItem; providerId: string; actualOdds: number; stake: number } | null;
  selectedProviderIdx: number;
  onSelectProvider: (idx: number) => void;
  onStartPlaceBet: (providerId: string) => void;
  onConfirmBet: () => void;
  onCancelPending: () => void;
  onUpdatePendingOdds: (val: number) => void;
  oddsOverride: number | null;
  editingOdds: boolean;
  onEditOdds: () => void;
  onSetOdds: (val: number) => void;
  onResetOdds: () => void;
  onCancelEdit: () => void;
  betError: string | null;
}) {
  void groupKey;
  const stake = stakePreview ? Math.min(stakePreview.recommended_stake, special.max_stake ?? Infinity) : 0;
  const effectiveOdds = oddsOverride ?? special.boosted_odds ?? 0;
  const oddsChanged = oddsOverride != null;
  const potentialReturn = stake * effectiveOdds;
  const potentialProfit = potentialReturn - stake;

  return (
    <div className="px-3 py-2 bg-panel">
      {isLoadingPreview ? (<div className="text-muted text-sm">Calculating stake...</div>) : stakePreview ? (
        <div className="space-y-2">
          <div className="flex items-center gap-6 text-xs text-muted flex-wrap">
            <div><span className="text-muted2 uppercase tracking-wider">Kelly: </span><span className="text-text">{(stakePreview.kelly_fraction * 100).toFixed(1)}%</span></div>
            <div className="flex items-center gap-1">
              <span className="text-muted2 uppercase tracking-wider">Odds: </span>
              {editingOdds ? (
                <input
                  type="number" step="0.01" autoFocus defaultValue={effectiveOdds.toFixed(2)}
                  className="w-16 bg-bg border border-tabBonus/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabBonus"
                  onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val >= 1.01) onSetOdds(val); else onCancelEdit(); }}
                  onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') onCancelEdit(); }}
                />
              ) : (
                <span onClick={onEditOdds} className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabBonus/50 transition-colors ${oddsChanged ? 'text-tabBonus font-medium border-tabBonus/30' : 'text-text border-transparent'}`} title="Click to adjust odds">
                  {effectiveOdds.toFixed(2)}
                </span>
              )}
              {oddsChanged && <button onClick={onResetOdds} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
            </div>
            <div><span className="text-muted2 uppercase tracking-wider">Stake: </span><span className="text-text font-medium">{stake.toFixed(0)} kr</span>{stakePreview.was_capped_single && <span className="text-warning text-[10px] ml-1">capped</span>}{special.max_stake != null && stakePreview.recommended_stake > special.max_stake && <span className="text-warning text-[10px] ml-1">max</span>}</div>
            <div><span className="text-muted2 uppercase tracking-wider">Return: </span><span className="text-text">{potentialReturn.toFixed(0)} kr</span><span className="text-success text-xs ml-1">(+{potentialProfit.toFixed(0)})</span></div>
            <div><span className="text-muted2 uppercase tracking-wider">Bankroll: </span><span className="text-text">{stakePreview.bankroll.toFixed(0)} kr</span></div>
            {special.boost_pct != null && <div><span className="text-muted2 uppercase tracking-wider">Boost: </span><span className="text-tabBonus">{special.boost_pct > 0 ? '+' : ''}{special.boost_pct.toFixed(0)}%</span></div>}
            {special.llm_fair_odds != null && <div><span className="text-muted2 uppercase tracking-wider">AI Fair: </span><span className="text-sky-400">{special.llm_fair_odds.toFixed(2)}</span></div>}
            {!stakePreview.bonus_cleared && <div><span className="text-warning uppercase tracking-wider text-[10px]">Bonus active </span><span className="text-warning text-xs">min odds {stakePreview.min_odds_applied.toFixed(2)}</span></div>}
            {special.llm_reasoning && (
              <div className="text-sky-400/70 text-[10px]">
                <span className="uppercase tracking-wider">AI ({special.llm_confidence || 'low'}): </span>
                <span className="text-sky-400/50 normal-case">{special.llm_reasoning.split('\n').filter((l: string) => l.trim()).map((line: string, i: number) => <span key={i}>{i > 0 && ' · '}{line.replace(/^-\s*/, '')}</span>)}</span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {betError && <span className="text-error text-xs max-w-[200px] truncate">{betError}</span>}
            {stakePreview.skip_reason ? (
              <span className="text-muted text-xs bg-border px-2 py-1">{stakePreview.skip_reason}</span>
            ) : pendingBet ? (
              <>
                <span className="text-muted text-xs">Odds:</span>
                <input
                  type="number" step="0.01" autoFocus value={pendingBet.actualOdds}
                  onChange={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val)) onUpdatePendingOdds(val); }}
                  className="w-20 bg-bg border border-tabBonus/50 text-text text-xs px-2 py-1.5 text-right focus:outline-none focus:border-tabBonus"
                  onKeyDown={(e) => { if (e.key === 'Enter') onConfirmBet(); if (e.key === 'Escape') onCancelPending(); }}
                />
                <button onClick={onConfirmBet} disabled={isPlacing || pendingBet.actualOdds < 1.01} className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap">
                  {isPlacing ? '...' : 'Confirm'}
                </button>
                <button onClick={onCancelPending} className="px-2 py-1.5 text-xs text-muted hover:text-text">Cancel</button>
              </>
            ) : (
              <>
                <select
                  value={selectedProviderIdx}
                  onChange={(e) => onSelectProvider(Number(e.target.value))}
                  className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabBonus/50 cursor-pointer"
                >
                  {providers.map((pid, i) => (
                    <option key={pid} value={i}>{formatProviderName(pid)} {stake > 0 ? `${stake.toFixed(0)} kr` : ''}</option>
                  ))}
                </select>
                <button
                  onClick={() => onStartPlaceBet(providers[selectedProviderIdx] || providers[0])}
                  disabled={stake <= 0 || isPlacing}
                  className="px-4 py-1.5 bg-tabBonus text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity whitespace-nowrap"
                >
                  {isPlacing ? '...' : 'Place Bet'}
                </button>
              </>
            )}
          </div>
        </div>
      ) : (<div className="text-muted text-sm">No preview available — missing boost data</div>)}
    </div>
  );
}
