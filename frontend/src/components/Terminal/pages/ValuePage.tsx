import { useState, useEffect, useDeferredValue, useMemo, useRef, Fragment, memo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { SpecialItem, StakePreviewResult } from '@/services/api';
import { formatProviderName, formatProviderWithPlatform, formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName, MAX_TTK_HOURS } from '@/utils/formatters';
import { resolveOutcome } from '@/utils/betting';
import { ProviderName } from '../ProviderName';
import { useExtractionFreshness } from '@/hooks/useExtractionStatus';
import { useMultiSort } from '@/hooks/useMultiSort';
import { useTableSort } from '@/hooks/useTableSort';
import { MultiSortableHeader } from '../MultiSortableHeader';
import { SortableHeader } from '../SortableHeader';
import { FilterBar, MultiSelectDropdown, FreshnessIndicator, SearchInput } from '../FilterBar';
import { BonusPopup } from '../BonusPopup';
import { MyBetsSection } from '../MyBetsSection';
import { ManualBetForm } from '../ManualBetForm';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { Opportunity, Provider, Bet } from '@/types';

const softProviderFilter = (p: Provider) => p.id !== 'polymarket' && p.id !== 'pinnacle';

type ValueTab = 'value' | 'boosts' | 'mybets' | 'manual';

const softBetFilter = (b: Bet) =>
  b.bet_type === 'value' || b.bet_type === 'boost' || b.bet_type === 'manual' ||
  (b.bet_type == null && b.provider !== 'pinnacle' && b.provider !== 'polymarket');

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


interface OpportunityRowProps {
  group: GroupedOpp;
  idx: number;
  isExpanded: boolean;
  onToggle: (idx: number) => void;
  placedKeys: Set<string>;
  balanceMap: Map<string, number>;
  selectedBetProvider: number;
  providerDropdownOpen: boolean;
  providerDropdownRef: React.RefObject<HTMLDivElement | null>;
  onProviderDropdownToggle: (groupKey: string) => void;
  onProviderSelect: (groupKey: string, index: number) => void;
  onPlaceBet: (opp: Opportunity, effectiveOdds: number, effectiveStake: number | null) => void;
  pendingBet: { groupKey: string; opp: Opportunity; actualOdds: number; useFreebet: boolean; navUrl: string | null; windowName: string; effectiveStake: number | null } | null;
  isPlacing: boolean;
  onConfirmBet: () => void;
  onCancelBet: () => void;
}

const OpportunityRow = memo(function OpportunityRow({
  group,
  idx,
  isExpanded,
  onToggle,
  balanceMap,
  selectedBetProvider: selIdx,
  providerDropdownOpen,
  providerDropdownRef,
  onProviderDropdownToggle,
  onProviderSelect,
  onPlaceBet,
  pendingBet,
  isPlacing,
  onConfirmBet,
  onCancelBet,
}: OpportunityRowProps) {
  const { rep, opps, providers: groupProviders } = group;

  // Local edit state
  const [localOddsOverride, setLocalOddsOverride] = useState<number | null>(null);
  const [editingOdds, setEditingOdds] = useState(false);
  const [localStakeOverride, setLocalStakeOverride] = useState<number | null>(null);
  const [editingStake, setEditingStake] = useState(false);

  // Flash detection
  const prevOdds = useRef(rep.odds1);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);
  useEffect(() => {
    if (rep.odds1 !== prevOdds.current) {
      setFlash(rep.odds1 > prevOdds.current ? 'up' : 'down');
      prevOdds.current = rep.odds1;
      const timer = setTimeout(() => setFlash(null), 1500);
      return () => clearTimeout(timer);
    }
  }, [rep.odds1]);

  const isSkipped = opps.every(o => !!o.skip_reason);
  const providerCount = groupProviders.length;
  const effectiveOdds = localOddsOverride ?? rep.odds1;
  const effectiveStake = localStakeOverride ?? rep.final_stake;
  const hasStake = effectiveStake != null && effectiveStake > 0;
  const isOddsOverridden = localOddsOverride !== null;
  const isStakeOverridden = localStakeOverride !== null;
  const dynamicEdge = rep.fair_odds && rep.fair_odds > 1
    ? (effectiveOdds / rep.fair_odds - 1) * 100
    : rep.edge_pct ?? 0;

  const hasBalance = (ids: string[]) => ids.some(id => (balanceMap.get(id) ?? 0) > 0);

  const selOpp = opps[selIdx] || opps[0];
  const isPending = pendingBet?.groupKey === group.key;

  const getDotClass = (opp: any) => {
    if (opp.is_daily_capped) return 'bg-error';
    if ((opp.allocation_score ?? 0) > 50) return 'bg-tabValue';
    if ((balanceMap.get(opp.provider1) ?? 0) > 0) return 'bg-success';
    return 'bg-muted/40';
  };

  const effStake = localStakeOverride ?? selOpp.final_stake;
  const oppHasStake = effStake != null && effStake > 0;
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

  return (
    <Fragment key={group.key}>
      <tr
        className={`cursor-pointer group ${isSkipped ? 'opacity-50' : ''} ${isExpanded ? 'expanded' : ''}`}
        onClick={() => !isSkipped && onToggle(idx)}
      >
        <td>
          <div className="flex items-center gap-1 min-w-0">
            <span className="text-text text-sm truncate">{displayTeamName(rep.home_team, rep.display_home ?? rep.prov_home)} vs {displayTeamName(rep.away_team, rep.display_away ?? rep.prov_away)}</span>
            <button
              title="Copy event"
              className="text-muted hover:text-text transition-colors opacity-0 group-hover:opacity-100 flex-shrink-0"
              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(displayTeamName(rep.home_team, rep.display_home ?? rep.prov_home)); }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            </button>
            {isSkipped && (
              <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{rep.skip_reason}</span>
            )}
          </div>
          <div className="text-muted2 text-[11px]">
            {rep.sport}{rep.league ? ` · ${rep.league}` : ''}{rep.market && rep.market !== '1x2' && rep.market !== 'moneyline' ? ` · ${rep.market}` : ''} · {formatDateTime(rep.starts_at)}
          </div>
        </td>
        <td className="text-right text-sm min-w-0">
          <span className="inline-flex items-center gap-1.5 justify-end">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${opps.every((o: any) => o.is_daily_capped) ? 'bg-error' : (rep as any).allocation_score > 50 ? 'bg-tabValue' : hasBalance(groupProviders) ? 'bg-success' : 'bg-error'}`} />
            {providerCount <= 3 ? (
              <span className="text-text truncate">{groupProviders.map((p, i) => <Fragment key={p}>{i > 0 && ', '}<ProviderName name={p} /></Fragment>)}</span>
            ) : (
              <span className="text-text truncate">
                <ProviderName name={groupProviders[0]} />
                <span className="text-muted ml-1">+{providerCount - 1}</span>
              </span>
            )}
          </span>
        </td>
        <td className="text-right text-text text-sm">{resolveOutcome(rep.outcome1, rep, rep.point, true)}</td>
        <td className={`text-right text-sm font-medium ${flash ? `flash-${flash}` : ''}`} onClick={(e) => e.stopPropagation()}>
          {editingOdds ? (
            <input
              type="number" step="0.01" autoFocus
              defaultValue={effectiveOdds.toFixed(2)}
              className="w-16 bg-bg border border-tabValue/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabValue"
              onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val >= 1.01) setLocalOddsOverride(val); setEditingOdds(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setEditingOdds(false); }}
            />
          ) : (
            <span
              onClick={() => setEditingOdds(true)}
              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabValue/50 transition-colors ${isOddsOverridden ? 'text-tabValue font-medium border-tabValue/30' : 'text-text border-transparent'}`}
              title="Click to adjust odds"
            >
              {effectiveOdds.toFixed(2)}
            </span>
          )}
          {isOddsOverridden && <button onClick={() => { setLocalOddsOverride(null); setEditingOdds(false); }} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
        </td>
        <td className="text-right text-muted text-sm">{rep.fair_odds?.toFixed(2) || '-'}</td>
        <td className="text-right text-muted text-sm">
          {rep.fair_odds && rep.fair_odds > 1 ? `${(100 / rep.fair_odds).toFixed(0)}%` : '-'}
        </td>
        <td className="text-right">
          {(() => { const ttk = getTTKFromNow(rep.starts_at); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
        </td>
        <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
          {editingStake ? (
            <input
              type="number" step="1" autoFocus
              defaultValue={effectiveStake?.toFixed(0) ?? '0'}
              className="w-16 bg-bg border border-tabValue/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabValue"
              onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val > 0) setLocalStakeOverride(val); setEditingStake(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setEditingStake(false); }}
            />
          ) : (
            <span
              onClick={() => setEditingStake(true)}
              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabValue/50 transition-colors ${isStakeOverridden ? 'text-tabValue font-medium border-tabValue/30' : 'text-text border-transparent'}`}
              title="Click to adjust stake"
            >
              {hasStake ? `${effectiveStake!.toFixed(0)} kr` : '-'}
            </span>
          )}
          {isStakeOverridden && <button onClick={() => { setLocalStakeOverride(null); setEditingStake(false); }} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
          {rep.bonus_status === 'trigger_needed' && <span className="ml-1 text-[9px] px-1 py-0.5 bg-warning/20 text-warning">TRG</span>}
          {rep.bonus_status === 'freebet_available' && <span className="ml-1 text-[9px] px-1 py-0.5 bg-accent/20 text-accent">FREE</span>}
        </td>
        <td className={`text-right font-semibold text-sm ${dynamicEdge > 0 ? 'text-success' : 'text-error'}`}>{dynamicEdge > 0 ? '+' : ''}{dynamicEdge.toFixed(1)}%</td>
      </tr>

      {isExpanded && !isSkipped && (
        <tr key={`${group.key}-expanded`}>
          <td colSpan={10} className="!p-0" onClick={e => e.stopPropagation()}>
            <div className="px-3 py-2 bg-panel">
              <div className="flex items-center gap-2">
              {isPending ? (
                <>
                  <button
                    onClick={onConfirmBet}
                    disabled={isPlacing || pendingBet!.actualOdds < 1.01}
                    className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                  >
                    {isPlacing ? '...' : 'Confirm'}
                  </button>
                  <span className="text-muted text-xs">@ {pendingBet!.actualOdds.toFixed(2)}</span>
                  <button
                    onClick={onCancelBet}
                    className="px-2 py-1.5 text-xs text-muted hover:text-text"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <div className="relative" ref={providerDropdownOpen ? providerDropdownRef : undefined}>
                    <button
                      type="button"
                      onClick={() => onProviderDropdownToggle(group.key)}
                      className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabValue/50 cursor-pointer flex items-center gap-1.5 min-w-[120px]"
                    >
                      <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${getDotClass(selOpp)}`} />
                      <span className="truncate">
                        <ProviderName name={selOpp.provider1} />
                        {oppHasStake ? ` ${effStake!.toFixed(0)} kr` : ''}
                        {(selOpp as any).is_daily_capped ? ' [CAP]' : selOpp.bonus_status === 'trigger_needed' ? ' [TRG]' : selOpp.bonus_status === 'freebet_available' ? ' [FREE]' : selOpp.skip_reason ? ` (${selOpp.skip_reason})` : ''}
                      </span>
                      <svg className="w-3 h-3 ml-auto flex-shrink-0 text-muted" viewBox="0 0 12 12" fill="none"><path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    </button>
                    {providerDropdownOpen && (
                      <div className="absolute left-0 top-full mt-0.5 z-50 bg-bg border border-border shadow-lg max-h-48 overflow-y-auto min-w-[160px]">
                        {opps.map((opp, i) => {
                          const oppStake = localStakeOverride ?? opp.final_stake;
                          const s = oppStake != null && oppStake > 0 ? ` ${oppStake.toFixed(0)} kr` : '';
                          const tag = (opp as any).is_daily_capped ? ' [CAP]'
                            : opp.bonus_status === 'trigger_needed' ? ' [TRG]'
                            : opp.bonus_status === 'freebet_available' ? ' [FREE]'
                            : opp.skip_reason ? ` (${opp.skip_reason})`
                            : '';
                          return (
                            <button
                              key={opp.id}
                              type="button"
                              onClick={() => {
                                onProviderSelect(group.key, i);
                              }}
                              className={`w-full text-left px-2 py-1.5 text-xs flex items-center gap-1.5 hover:bg-panel cursor-pointer ${i === selIdx ? 'bg-panel text-text' : 'text-muted'}`}
                            >
                              <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${getDotClass(opp)}`} />
                              <span className="truncate">
                                <ProviderName name={opp.provider1} />{s}{tag}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => onPlaceBet(selOpp, effectiveOdds, localStakeOverride)}
                    disabled={isDisabled}
                    className={`px-4 py-1.5 ${btnColor} text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap`}
                  >
                    {btnLabel}
                  </button>
                </>
              )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
});

interface ValuePageProps {
  providers?: Provider[];
}

export function ValuePage({ providers = [] }: ValuePageProps) {
  const freshness = useExtractionFreshness();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<ValueTab>('value');

  const [selectedGroup, setSelectedGroup] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);

  const [freebetPopup, setFreebetPopup] = useState<{
    opp: Opportunity;
    freebetAmount: number;
    effectiveOdds: number;
    effectiveStake: number | null;
  } | null>(null);

  const [selectedProviders, setSelectedProviders] = useState<Set<string>>(new Set());
  const [selectedLeagues, setSelectedLeagues] = useState<Set<string>>(new Set());
  const [searchInput, setSearchInput] = useState('');
  const search = useDeferredValue(searchInput);
  const [boostSearchInput, setBoostSearchInput] = useState('');
  const boostSearch = useDeferredValue(boostSearchInput);
  const [betError, setBetError] = useState<string | null>(null);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
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
    effectiveStake: number | null;
  } | null>(null);

  // --- Boosts state ---
  const [boostFilters, setBoostFilters] = useState<{ providers: string[] } | null>(null);
  const [boostExpandedIdx, setBoostExpandedIdx] = useState<number | null>(null);
  const [boostStakePreview, setBoostStakePreview] = useState<StakePreviewResult | null>(null);
  const [isLoadingBoostPreview, setIsLoadingBoostPreview] = useState(false);
  const [boostSelectedProviders, setBoostSelectedProviders] = useState<Set<string>>(new Set());
  const [boostSelectedBetProvider, setBoostSelectedBetProvider] = useState<Record<string, number>>({});
  const [boostOddsOverride, setBoostOddsOverride] = useState<Record<string, number>>({});
  const [boostEditingOdds, setBoostEditingOdds] = useState<string | null>(null);
  const [boostStakeOverride, setBoostStakeOverride] = useState<Record<string, number>>({});
  const [boostEditingStake, setBoostEditingStake] = useState<string | null>(null);
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

  const { data: opportunitiesData, isLoading } = useQuery({
    queryKey: ['opportunities', 'value'],
    queryFn: () => api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3),
    refetchInterval: 30_000,
  });
  const opportunities = opportunitiesData?.opportunities ?? [];

  const { data: specialsData } = useQuery({
    queryKey: ['specials'],
    queryFn: () => api.getSpecials({}),
    staleTime: 60_000,
  });
  const specials = specialsData?.specials ?? [];
  // Sync boostFilters from query data
  useEffect(() => {
    if (specialsData?.filters) {
      setBoostFilters({ providers: specialsData.filters.providers });
    }
  }, [specialsData]);

  const { data: betsData } = useQuery({
    queryKey: ['bets', 'pending'],
    queryFn: () => api.getBets('pending', 500),
    staleTime: 60_000,
  });
  const pendingBets = betsData?.bets ?? [];

  // Sync placed-bet keys from DB (merge with in-session keys)
  useEffect(() => {
    if (!pendingBets.length) return;
    const keys = new Set<string>();
    const bKeys = new Set<string>();
    for (const b of pendingBets) {
      if (b.market === 'boost' && b.outcome) {
        bKeys.add(b.outcome);
      } else if (b.event_id) {
        keys.add(`${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`);
      }
    }
    setPlacedKeys(prev => {
      const merged = new Set(prev);
      for (const k of keys) merged.add(k);
      return merged;
    });
    setBoostPlacedKeys(prev => {
      const merged = new Set(prev);
      for (const k of bKeys) merged.add(k);
      return merged;
    });
    setMyBetsCount(pendingBets.filter(softBetFilter).length);
  }, [pendingBets]);

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

  const availableLeagues = useMemo(() => {
    const set = new Set<string>();
    for (const opp of opportunities) {
      if (opp.league) set.add(opp.league);
    }
    return Array.from(set).sort();
  }, [opportunities]);

  const balanceMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of providers) m.set(p.id, p.balance);
    return m;
  }, [providers]);

  const hasBalance = (providerIds: string[]) =>
    providerIds.some(id => (balanceMap.get(id) ?? 0) > 0);

  const grouped = useMemo(() => {
    let result = opportunities;
    // Remove started/imminent events and events > 7 days out
    result = result.filter(o => {
      const ttk = getTTKFromNow(o.starts_at);
      return ttk === null || (ttk > 1 / 60 && ttk <= MAX_TTK_HOURS);
    });
    // Remove placed market+outcome+point combos (same bet at any provider)
    if (placedKeys.size > 0) {
      result = result.filter(o => !placedKeys.has(`${o.event_id}|${o.market}|${o.outcome1}|${o.point ?? ''}`));
    }
    if (selectedProviders.size > 0) {
      result = result.filter(o => selectedProviders.has(o.provider1));
    }
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
        (o.prov_home?.toLowerCase().includes(q)) ||
        (o.prov_away?.toLowerCase().includes(q)) ||
        (o.provider1?.toLowerCase().includes(q)) ||
        (o.sport?.toLowerCase().includes(q)) ||
        (o.league?.toLowerCase().includes(q))
      );
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
      // Sort providers within group: highest allocation score first
      opps.sort((a, b) => ((b as any).allocation_score ?? -1) - ((a as any).allocation_score ?? -1));
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
  }, [opportunities, selectedProviders, selectedLeagues, placedKeys, search]);

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

  const valueScrollRef = useRef<HTMLDivElement>(null);

  const valueVirtualizer = useVirtualizer({
    count: sortedGroups.length,
    getScrollElement: () => valueScrollRef.current,
    estimateSize: (index) => selectedGroup === index ? 100 : 52,
    overscan: 10,
  });

  const toggleProvider = (p: string) => {
    setSelectedProviders(prev => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p); else next.add(p);
      return next;
    });
  };

  const toggleLeague = (l: string) => {
    setSelectedLeagues(prev => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l); else next.add(l);
      return next;
    });
  };

  // --- Boosts grouping & sorting ---
  const boostNonExpired = useMemo(() => specials.filter(s => {
    if (s.event_time) {
      try {
        const diff = new Date(s.event_time).getTime() - Date.now();
        if (diff <= 0) return false;                          // Already started
        if (diff > MAX_TTK_HOURS * 3600000) return false;    // > 7 days out
      } catch { /* keep */ }
    }
    if (!s.expires_at) return true;
    try { return new Date(s.expires_at).getTime() > Date.now(); } catch { return true; }
  }), [specials]);

  const boostGrouped = useMemo(() => {
    const groups: GroupedSpecial[] = [];
    for (const s of boostNonExpired) {
      const allProviders = [s.provider, ...(s.shared_providers || [])];
      const key = `${s.provider}-${s.title}-${s.boosted_odds}-${s.event || ''}`;
      if (boostPlacedKeys.has(key) || boostPlacedKeys.has(s.title)) continue;
      groups.push({ key, rep: s, providers: allProviders });
    }
    return groups;
  }, [boostNonExpired, boostPlacedKeys]);

  const boostActiveGroups = useMemo(() => {
    let result = boostGrouped;
    if (boostSelectedProviders.size > 0) {
      result = result.filter(g =>
        g.providers.some(p => boostSelectedProviders.has(p.toLowerCase()))
      );
    }
    if (boostSearch.trim()) {
      const q = boostSearch.trim().toLowerCase();
      result = result.filter(g =>
        g.rep.title.toLowerCase().includes(q) ||
        (g.rep.event && g.rep.event.toLowerCase().includes(q)) ||
        g.providers.some(p => p.toLowerCase().includes(q))
      );
    }
    return result;
  }, [boostGrouped, boostSelectedProviders, boostSearch]);

  type BoostSortCol = 'odds' | 'fair' | 'edge' | 'aiProb' | 'aiEdge' | 'ttk' | 'stake';
  const boostSortExtractors = useMemo(() => ({
    odds: (g: GroupedSpecial) => g.rep.boosted_odds ?? 0,
    fair: (g: GroupedSpecial) => g.rep.llm_fair_odds ?? 0,
    aiProb: (g: GroupedSpecial) => g.rep.llm_probability ?? 0,
    aiEdge: (g: GroupedSpecial) => g.rep.llm_edge_pct ?? -999,
    stake: (g: GroupedSpecial) => g.rep.recommended_stake ?? 0,
    edge: (g: GroupedSpecial) => g.rep.edge_pct ?? g.rep.boost_pct ?? 0,
    ttk: (g: GroupedSpecial) => getTTKFromNow(g.rep.event_time) ?? 99999,
  }), []);
  const { sorted: sortedBoosts, sort: boostSort, toggle: toggleBoostSort } =
    useTableSort<GroupedSpecial, BoostSortCol>(boostActiveGroups, boostSortExtractors, { column: 'aiEdge', direction: 'desc' });

  const toggleBoostProvider = (p: string) => {
    setBoostSelectedProviders(prev => { const next = new Set(prev); const key = p.toLowerCase(); if (next.has(key)) next.delete(key); else next.add(key); return next; });
    setBoostExpandedIdx(null);
  };

  const handleBoostRowClick = async (idx: number, group: GroupedSpecial) => {
    if (boostExpandedIdx === idx) { setBoostExpandedIdx(null); setBoostStakePreview(null); setBoostPendingBet(null); return; }
    setBoostExpandedIdx(idx); setBoostStakePreview(null); setBoostPendingBet(null); setBoostEditingOdds(null); setBoostEditingStake(null);
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
    if (!special.boosted_odds) return;
    const overriddenStake = boostStakeOverride[groupKey];
    let stake = overriddenStake ?? (boostStakePreview ? Math.min(boostStakePreview.recommended_stake, special.max_stake ?? Infinity) : (special.recommended_stake ?? 0));
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
        fair_odds_at_placement: special.llm_fair_odds ?? undefined,
        boost_event: special.event ?? undefined,
        boost_title: special.llm_title ?? special.title,
        bet_type: 'boost',
        start_time: special.event_time ?? undefined,
      });
      setBetSuccess(`Recorded: ${stake.toFixed(0)} kr on ${special.title} @ ${actualOdds.toFixed(2)} (${formatProviderName(providerId)})`);
      setTimeout(() => setBetSuccess(null), 5000);
      setBoostPlacedKeys(prev => { const next = new Set(prev); next.add(groupKey); next.add(special.title); return next; });
      setMyBetsCount(prev => (prev ?? 0) + 1);
      setBoostPendingBet(null); setBoostExpandedIdx(null); setBoostStakePreview(null);
      queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
    } catch (err) {
      setBetError(err instanceof Error ? err.message : 'Failed to place bet');
      setTimeout(() => setBetError(null), 5000);
    } finally { setIsPlacing(false); }
  };

  const handleSelectGroup = (idx: number) => {
    setSelectedGroup(selectedGroup === idx ? null : idx);
    setPendingBet(null);
  };

  const handlePlaceBetClick = (opp: Opportunity, effectiveOdds: number, effectiveStake: number | null) => {
    const stake = effectiveStake ?? opp.final_stake;
    if (!stake || stake <= 0) return;

    if (opp.bonus_status === 'freebet_available') {
      // Show popup to choose freebet vs balance
      setFreebetPopup({ opp, freebetAmount: opp.bonus_amount ?? stake, effectiveOdds, effectiveStake });
    } else {
      // Trigger or normal bet — start two-step flow
      startPlaceBet(opp, false, effectiveOdds, effectiveStake);
    }
  };

  // Enter "awaiting confirm" state for two-step bet recording
  const startPlaceBet = (opp: Opportunity, useFreebet: boolean, effectiveOdds: number, effectiveStake: number | null) => {
    setFreebetPopup(null);
    setBetError(null);
    setBetSuccess(null);
    const groupKey = `${opp.event_id}|${opp.outcome1}|${opp.market}|${opp.point ?? ''}|${opp.odds1}`;
    setPendingBet({ groupKey, opp, actualOdds: effectiveOdds, useFreebet, navUrl: null, windowName: `bbq_${opp.provider1}`, effectiveStake });
  };

  // Step 2: Confirm bet with actual odds
  const confirmPlaceBet = async () => {
    if (!pendingBet) return;
    const { opp, actualOdds, useFreebet, effectiveStake } = pendingBet;
    const stake = effectiveStake ?? opp.final_stake;
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
        bet_type: 'value',
      });
      const outcomeLabel = resolveOutcome(opp.outcome1, opp, opp.point);
      const type = useFreebet ? 'Freebet' : opp.bonus_status === 'trigger_needed' ? 'Trigger' : 'Bet';
      setBetSuccess(`${type}: ${stake.toFixed(0)} kr on ${outcomeLabel} @ ${actualOdds.toFixed(2)} (${formatProviderName(opp.provider1)})`);
      setTimeout(() => { setBetSuccess(null); setBetError(null); }, 5000);

      // Remove from list immediately (same market+outcome+point hidden across all providers)
      setPlacedKeys(prev => new Set(prev).add(`${opp.event_id}|${opp.market}|${opp.outcome1}|${opp.point ?? ''}`));
      setMyBetsCount(prev => (prev ?? 0) + 1);
      setPendingBet(null);
      setSelectedGroup(null);
      queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
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
          <TabIcon name="value" color={TAB_COLORS.value} size={16} />
          Soft
        </h2>
        {activeTab === 'value' && (
          <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search event, provider..." accentColor="tabValue" />
        )}
        {activeTab === 'boosts' && (
          <SearchInput value={boostSearchInput} onChange={setBoostSearchInput} placeholder="Search boost, provider..." accentColor="tabValue" />
        )}
      </div>

      {/* Sub-tab selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'value' as ValueTab, label: 'Value Bets', count: filteredCount, activeClass: 'border-tabValue text-tabValue' },
          { id: 'boosts' as ValueTab, label: 'Boosts', count: sortedBoosts.length, activeClass: 'border-tabValue text-tabValue' },
          { id: 'mybets' as ValueTab, label: 'My Bets', count: myBetsCount, activeClass: 'border-tabValue text-tabValue' },
          { id: 'manual' as ValueTab, label: 'Manual', count: null, activeClass: 'border-tabValue text-tabValue' },
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

      {/* MyBets tab — all soft provider bets (value + boosts + manual) */}
      {activeTab === 'mybets' && (
        <MyBetsSection filter={softBetFilter} colorKey="value" />
      )}

      {/* Manual bet entry tab */}
      {activeTab === 'manual' && (
        <ManualBetForm providers={providers} providerFilter={softProviderFilter} onSuccess={(msg) => { setBetSuccess(msg); setActiveTab('mybets'); }} onError={setBetError} />
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

      <FilterBar>
        {boostFilters && boostFilters.providers.length > 0 && (
          <MultiSelectDropdown label="Provider" options={boostFilters.providers} selected={boostSelectedProviders} onToggle={toggleBoostProvider} onClear={() => { setBoostSelectedProviders(new Set()); setBoostExpandedIdx(null); }} format={formatProviderWithPlatform} accentColor="tabValue" />
        )}
        <FreshnessIndicator tiers={[['boosts', freshness.boosts]]} />
      </FilterBar>

      {sortedBoosts.length === 0 ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          No active boosts. Boosts are scraped automatically every hour.
        </div>
      ) : (
        <div className="border-l-2 border-tabValue">
        <table className="sq">
          <thead>
            <tr>
              <th>Boost</th>
              <th className="text-right">Providers</th>
              <SortableHeader column="odds" label="Odds" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="fair" label="Fair" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="aiProb" label="Prob" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="ttk" label="TTK" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="stake" label="Stake" sort={boostSort} onToggle={toggleBoostSort} />
              <SortableHeader column="aiEdge" label="Edge" sort={boostSort} onToggle={toggleBoostSort} />
            </tr>
          </thead>
          <tbody>
            {sortedBoosts.map((group, idx) => {
              const s = group.rep;
              const isExpanded = boostExpandedIdx === idx;
              const providerCount = group.providers.length;

              const boostEffOdds = boostOddsOverride[group.key] ?? s.boosted_odds ?? 0;
              const boostEffStake = boostStakeOverride[group.key] ?? s.recommended_stake ?? 0;
              const isBoostOddsOverridden = group.key in boostOddsOverride;
              const isBoostStakeOverridden = group.key in boostStakeOverride;
              const boostFairOdds = s.llm_fair_odds ?? (s.fair_odds ?? null);
              const boostDynEdge = boostFairOdds != null && boostFairOdds > 1
                ? (boostEffOdds / boostFairOdds - 1) * 100
                : s.llm_edge_pct ?? s.edge_pct ?? s.boost_pct ?? null;

              return (
                <Fragment key={group.key}>
                  <tr className={`cursor-pointer group bg-tabValue/[0.03] hover:bg-tabValue/[0.07] ${isExpanded ? 'expanded' : ''}`} onClick={() => handleBoostRowClick(idx, group)}>
                    <td>
                      <div className="flex items-center gap-1 min-w-0">
                        <span className="text-text text-sm truncate" title={s.llm_title ? s.title : undefined}>{s.llm_title || s.title}</span>
                        <button
                          title="Copy event"
                          className="text-muted hover:text-text transition-colors opacity-0 group-hover:opacity-100 flex-shrink-0"
                          onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText((s.event || '').split(/\s+vs\s+/i)[0] || s.llm_title || s.title); }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                        </button>
                      </div>
                      <div className="text-muted2 text-[11px] truncate">
                        {s.event || ''}{s.sport && s.sport !== 'unknown' ? ` · ${s.sport.replace(/_/g, ' ')}` : ''}
                        {s.event_time ? ` · ${formatDateTime(s.event_time)}` : ''}
                      </div>
                    </td>
                    <td className="text-right text-sm min-w-0">
                      <span className="inline-flex items-center gap-1.5 justify-end">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${hasBalance(group.providers) ? 'bg-success' : 'bg-error'}`} />
                        {providerCount <= 3 ? (
                          <span className="text-text truncate">{group.providers.map((p, i) => <Fragment key={p}>{i > 0 && ', '}<ProviderName name={p} /></Fragment>)}</span>
                        ) : (
                          <span className="text-text truncate">
                            <ProviderName name={group.providers[0]} />
                            <span className="text-muted ml-1">+{providerCount - 1}</span>
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="text-right text-sm" onClick={(e) => e.stopPropagation()}>
                      {s.original_odds != null && <><span className="text-muted2">{s.original_odds.toFixed(2)}</span><span className="text-muted2 mx-0.5">&rarr;</span></>}
                      {boostEditingOdds === group.key ? (
                        <input
                          type="number" step="0.01" autoFocus
                          defaultValue={boostEffOdds.toFixed(2)}
                          className="w-16 bg-bg border border-tabValue/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabValue"
                          onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val >= 1.01) setBoostOddsOverride(prev => ({ ...prev, [group.key]: val })); setBoostEditingOdds(null); }}
                          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setBoostEditingOdds(null); }}
                        />
                      ) : (
                        <span
                          onClick={() => setBoostEditingOdds(group.key)}
                          className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabValue/50 transition-colors ${isBoostOddsOverridden ? 'text-tabValue font-medium border-tabValue/30' : 'text-success font-medium border-transparent'}`}
                          title="Click to adjust odds"
                        >
                          {boostEffOdds.toFixed(2)}
                        </span>
                      )}
                      {isBoostOddsOverridden && <button onClick={() => { setBoostOddsOverride(prev => { const next = { ...prev }; delete next[group.key]; return next; }); setBoostEditingOdds(null); }} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
                      {!isBoostOddsOverridden && s.boost_pct != null && <div className="text-muted2 text-[10px]">+{s.boost_pct.toFixed(0)}%</div>}
                    </td>
                    <td className="text-right text-muted text-sm">{s.llm_fair_odds != null ? s.llm_fair_odds.toFixed(2) : '-'}</td>
                    <td className="text-right text-muted text-sm">
                      {s.llm_probability != null ? `${(s.llm_probability * 100).toFixed(0)}%` : '-'}
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(s.event_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                    <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
                      {boostEditingStake === group.key ? (
                        <input
                          type="number" step="1" autoFocus
                          defaultValue={boostEffStake.toFixed(0)}
                          className="w-16 bg-bg border border-tabValue/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabValue"
                          onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val > 0) setBoostStakeOverride(prev => ({ ...prev, [group.key]: val })); setBoostEditingStake(null); }}
                          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); else if (e.key === 'Escape') setBoostEditingStake(null); }}
                        />
                      ) : (
                        <span
                          onClick={() => setBoostEditingStake(group.key)}
                          className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabValue/50 transition-colors ${isBoostStakeOverridden ? 'text-tabValue font-medium border-tabValue/30' : 'text-text border-transparent'}`}
                          title="Click to adjust stake"
                        >
                          {boostEffStake > 0 ? `${Math.round(boostEffStake)} kr` : '-'}
                        </span>
                      )}
                      {isBoostStakeOverridden && <button onClick={() => { setBoostStakeOverride(prev => { const next = { ...prev }; delete next[group.key]; return next; }); setBoostEditingStake(null); }} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
                    </td>
                    <td className="text-right font-semibold text-sm">
                      {boostDynEdge != null ? (
                        <span className="text-text">
                          {boostDynEdge > 0 ? '+' : ''}{boostDynEdge.toFixed(1)}%
                        </span>
                      ) : <span className="text-muted2">-</span>}
                    </td>
                  </tr>
                  {isExpanded && (() => {
                    const bSelIdx = boostSelectedBetProvider[group.key] ?? 0;
                    const bSelProvider = group.providers[bSelIdx] || group.providers[0];
                    const bIsPending = boostPendingBet?.groupKey === group.key;
                    const bEffStake = boostStakeOverride[group.key] ?? (boostStakePreview ? Math.min(boostStakePreview.recommended_stake, s.max_stake ?? Infinity) : (s.recommended_stake ?? 0));

                    return (
                    <tr key={`${group.key}-exp`}>
                      <td colSpan={8} className="!p-0" onClick={e => e.stopPropagation()}>
                        {isLoadingBoostPreview ? <div className="px-3 py-2 bg-panel text-muted text-sm">Loading...</div> : (
                        <div className="px-3 py-2 bg-panel">
                          {s.llm_reasoning && (
                            <div className="text-muted2 text-[10px] leading-relaxed mb-2">
                              <span className="uppercase tracking-wider">{s.llm_confidence || 'low'}: </span>
                              {s.llm_reasoning.split('\n').filter((l: string) => l.trim()).map((line: string, i: number) => (
                                <span key={i}>{i > 0 && ' · '}{line.replace(/^-\s*/, '').trim()}</span>
                              ))}
                            </div>
                          )}
                          <div className="flex items-center gap-2">
                            {betError && <span className="text-error text-xs max-w-[200px] truncate">{betError}</span>}
                            {(boostStakePreview?.skip_reason) ? (
                              <span className="text-muted text-xs bg-border px-2 py-1">{boostStakePreview.skip_reason}</span>
                            ) : bIsPending ? (
                              <>
                                <button onClick={confirmBoostPlaceBet} disabled={isPlacing || boostPendingBet!.actualOdds < 1.01} className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap">{isPlacing ? '...' : 'Confirm'}</button>
                                <span className="text-muted text-xs">@ {boostPendingBet!.actualOdds.toFixed(2)}</span>
                                <button onClick={() => setBoostPendingBet(null)} className="px-2 py-1.5 text-xs text-muted hover:text-text">Cancel</button>
                              </>
                            ) : (
                              <>
                                <div className="relative">
                                  <select
                                    value={bSelIdx}
                                    onChange={(e) => setBoostSelectedBetProvider(prev => ({ ...prev, [group.key]: parseInt(e.target.value) }))}
                                    className="bg-bg border border-border text-text text-xs px-2 py-1.5 focus:outline-none focus:border-tabValue/50 cursor-pointer min-w-[120px]"
                                  >
                                    {group.providers.map((pid, i) => (
                                      <option key={pid} value={i}>{formatProviderName(pid)}{bEffStake > 0 ? ` ${bEffStake.toFixed(0)} kr` : ''}</option>
                                    ))}
                                  </select>
                                </div>
                                <button
                                  onClick={() => startBoostPlaceBet(s, bSelProvider, group.key)}
                                  disabled={bEffStake <= 0 || isPlacing}
                                  className="px-4 py-1.5 bg-tabValue text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
                                >
                                  {isPlacing ? '...' : 'Place Bet'}
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                        )}
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
            format={formatProviderWithPlatform}
            accentColor="tabValue"
          />
        )}
        {availableLeagues.length > 0 && (
          <MultiSelectDropdown
            label="League"
            options={availableLeagues}
            selected={selectedLeagues}
            onToggle={toggleLeague}
            onClear={() => setSelectedLeagues(new Set())}
            accentColor="tabValue"
          />
        )}
        <FreshnessIndicator tiers={[['soft', freshness.soft], ['sharp', freshness.sharp]]} />
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
        <div ref={valueScrollRef} className="overflow-auto" style={{ maxHeight: 'calc(100vh - 280px)' }}>
        <table className="sq w-full">
          <thead className="sticky top-0 z-10 bg-panel">
            <tr>
              <th style={{ width: '35%' }}>Event</th>
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
          <tbody style={{
            paddingTop: valueVirtualizer.getVirtualItems()[0]?.start ?? 0,
            paddingBottom: (() => { const items = valueVirtualizer.getVirtualItems(); return valueVirtualizer.getTotalSize() - (items[items.length - 1]?.end ?? 0); })(),
          }}>
            {valueVirtualizer.getVirtualItems().map((virtualRow) => {
              const group = sortedGroups[virtualRow.index];
              const idx = virtualRow.index;
              return (
                <OpportunityRow
                  key={group.key}
                  group={group}
                  idx={idx}
                  isExpanded={selectedGroup === idx}
                  onToggle={handleSelectGroup}
                  placedKeys={placedKeys}
                  balanceMap={balanceMap}
                  selectedBetProvider={selectedBetProvider[group.key] ?? 0}
                  providerDropdownOpen={providerDropdownOpen === group.key}
                  providerDropdownRef={providerDropdownRef}
                  onProviderDropdownToggle={(key) => setProviderDropdownOpen(prev => prev === key ? null : key)}
                  onProviderSelect={(key, i) => { setSelectedBetProvider(prev => ({ ...prev, [key]: i })); setProviderDropdownOpen(null); }}
                  onPlaceBet={handlePlaceBetClick}
                  pendingBet={pendingBet?.groupKey === group.key ? pendingBet : null}
                  isPlacing={isPlacing}
                  onConfirmBet={confirmPlaceBet}
                  onCancelBet={() => setPendingBet(null)}
                />
              );
            })}
          </tbody>
        </table>
        </div>
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
                  <td className="text-right text-text">{displayTeamName(freebetPopup.opp.home_team, freebetPopup.opp.display_home ?? freebetPopup.opp.prov_home)} vs {displayTeamName(freebetPopup.opp.away_team, freebetPopup.opp.display_away ?? freebetPopup.opp.prov_away)}</td>
                </tr>
                <tr>
                  <td className="text-muted">Stake</td>
                  <td className="text-right text-text">{(freebetPopup.effectiveStake ?? freebetPopup.opp.final_stake)?.toFixed(0)} kr @ {freebetPopup.effectiveOdds.toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => startPlaceBet(freebetPopup.opp, true, freebetPopup.effectiveOdds, freebetPopup.effectiveStake)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-accent text-bg hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                <div>Use Freebet</div>
                <div className="text-[10px] opacity-70">no deduction</div>
              </button>
              <button
                onClick={() => startPlaceBet(freebetPopup.opp, false, freebetPopup.effectiveOdds, freebetPopup.effectiveStake)}
                disabled={isPlacing}
                className="flex-1 px-3 py-2 text-xs font-medium bg-panel border border-border text-muted hover:text-text disabled:opacity-50 transition-colors"
              >
                <div>Use Balance</div>
                <div className="text-[10px] opacity-70">deduct {(freebetPopup.effectiveStake ?? freebetPopup.opp.final_stake)?.toFixed(0)} kr</div>
              </button>
            </div>
          </div>
        </BonusPopup>
      )}
    </div>
  );
}


