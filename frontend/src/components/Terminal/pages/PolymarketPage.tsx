import { useState, useEffect, useDeferredValue, useMemo, useRef, Fragment, memo, useCallback } from 'react';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { api } from '@/services/api';
import { useBetMutations } from '@/hooks/useBetMutations';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor, displayTeamName, MAX_TTK_HOURS } from '@/utils/formatters';
import { resolveOutcome as resolveOutcomeBase, SPORT_DURATION, DEFAULT_DURATION } from '@/utils/betting';
import { useTableSort } from '@/hooks/useTableSort';
import { SortableHeader } from '../SortableHeader';
import { SearchInput, relativeTime } from '../FilterBar';
import { MyBetsSection } from '../MyBetsSection';
import { ManualBetForm } from '../ManualBetForm';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useToast, ToastContainer } from '../Toast';
import type { PolymarketValueBet, PolymarketRewardMarket, Bet, Provider } from '@/types';

const polyBetFilter = (b: Bet) => b.bet_type === 'polymarket' || (b.bet_type == null && b.provider === 'polymarket');

/** Count Polymarket bets that need manual settlement. */
function countManualSettleBets(bets: Bet[]): number {
  const now = Date.now();

  return bets.filter(polyBetFilter).filter(b => {
    // Only count bets on finished events that need manual settlement
    const startMs = b.start_time ? new Date(b.start_time).getTime() : null;
    if (!startMs || startMs > now) return false;  // Upcoming — not settleable yet

    const isFinished = b.match_status === 'finished' ||
      (b.match_status !== 'live' && now > startMs + (SPORT_DURATION[b.sport ?? ''] ?? DEFAULT_DURATION));

    if (!isFinished) return false;  // Still playing — not settleable yet

    // If predicted result exists, it's pre-filled in the settle UI — don't count as needing attention
    if (b.predicted_result) return false;

    return true;  // Finished but no predicted result → needs manual settle
  }).length;
}

type PolyTab = 'value' | 'rewards' | 'mybets' | 'manual';

const polyProviderFilter = (p: Provider) => p.id === 'polymarket';

// ──────────────────── PolyRow ────────────────────

interface PolyRowProps {
  vb: PolymarketValueBet;
  idx: number;
  isSelected: boolean;
  isPending: boolean;
  isPlacing: boolean;
  onSelect: (idx: number) => void;
  onStartPlace: (vb: PolymarketValueBet) => void;
  onConfirmPlace: () => void;
  onCancelPending: () => void;
  polyName: (vb: PolymarketValueBet, side: 'home' | 'away') => string;
  resolveOutcome: (vb: PolymarketValueBet) => string;
  getOddsKey: (vb: PolymarketValueBet) => string;
  oddsToCents: (odds: number) => number;
  // Parent-level overrides (bet placement kept in parent)
  oddsOverride: Record<string, number>;
  stakeOverride: Record<string, number>;
  onOddsOverride: (key: string, value: number) => void;
  onOddsClear: (key: string) => void;
  onStakeOverride: (key: string, value: number) => void;
  onStakeClear: (key: string) => void;
  pendingActualCents: number;
}

const PolyRow = memo(function PolyRow({
  vb,
  idx,
  isSelected,
  isPending,
  isPlacing,
  onSelect,
  onStartPlace,
  onConfirmPlace,
  onCancelPending,
  polyName,
  resolveOutcome,
  getOddsKey,
  oddsToCents,
  oddsOverride,
  stakeOverride,
  onOddsOverride,
  onOddsClear,
  onStakeOverride,
  onStakeClear,
  pendingActualCents,
}: PolyRowProps) {
  const [editingOdds, setEditingOdds] = useState(false);
  const [editingStake, setEditingStake] = useState(false);

  const oddsKey = getOddsKey(vb);
  const effectiveOdds = oddsOverride[oddsKey] ?? vb.polymarket_odds;
  const priceCents = oddsToCents(effectiveOdds);
  const fairCents = vb.fair_price_cents ?? oddsToCents(vb.fair_odds);
  // Use backend pre-computed edge (includes 2% fee adjustment) unless user overrode odds
  const isOddsOverridden = oddsKey in oddsOverride;
  const edgePct = isOddsOverridden && vb.fair_odds > 1
    ? ((0.98 * effectiveOdds + 0.02) / vb.fair_odds - 1) * 100
    : vb.edge_pct;
  const stakeUsdc = stakeOverride[oddsKey] ?? vb.final_stake_usdc ?? 0;
  const shares = priceCents > 0 ? stakeUsdc / (priceCents / 100) : 0;
  const payoutUsdc = shares * 1.0;
  const profitUsdc = payoutUsdc - stakeUsdc;
  void profitUsdc; // computed but not shown inline

  const hasStake = stakeUsdc > 0;
  const isOverridden = oddsKey in oddsOverride;
  const isSkipped = !!vb.skip_reason;

  // Flash detection: flash-up when odds improve, flash-down when they worsen
  const prevOddsRef = useRef<number | null>(null);
  const [flashClass, setFlashClass] = useState('');
  useEffect(() => {
    const prev = prevOddsRef.current;
    if (prev !== null && prev !== vb.polymarket_odds) {
      const cls = vb.polymarket_odds > prev ? 'flash-up' : 'flash-down';
      setFlashClass(cls);
      const timer = setTimeout(() => setFlashClass(''), 1500);
      prevOddsRef.current = vb.polymarket_odds;
      return () => clearTimeout(timer);
    }
    prevOddsRef.current = vb.polymarket_odds;
  }, [vb.polymarket_odds]);

  const ttk = getTTKFromNow(vb.start_time);

  return (
    <Fragment>
      <tr
        className={`cursor-pointer ${isSkipped ? 'opacity-50' : ''} ${isSelected ? 'expanded' : ''} ${flashClass}`}
        onClick={() => !isSkipped && onSelect(idx)}
      >
        <td>
          <div className="flex items-center gap-2 min-w-0 group/copy">
            {vb.event_slug ? (
              <a href={`https://polymarket.com/event/${vb.event_slug}`} target="_blank" rel="noopener noreferrer" className="text-text text-sm truncate hover:text-tabPolymarket transition-colors" onClick={e => e.stopPropagation()}>{polyName(vb, 'home')} vs {polyName(vb, 'away')}</a>
            ) : (
              <span className="text-text text-sm truncate">{polyName(vb, 'home')} vs {polyName(vb, 'away')}</span>
            )}
            <button
              title="Copy event"
              className="text-muted hover:text-text transition-colors opacity-0 group-hover/copy:opacity-100 flex-shrink-0"
              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(polyName(vb, 'home')); }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            </button>
            {isSkipped && <span className="text-[9px] px-1 py-0.5 bg-muted/15 text-muted">{vb.skip_reason}</span>}
          </div>
          <div className="text-muted2 text-[11px]">
            {vb.sport} · {formatDateTime(vb.start_time)}
          </div>
        </td>
        <td className="text-right text-text text-xs">{resolveOutcome(vb)}</td>
        <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
          {editingOdds ? (
            <input
              type="number" step="1" min="1" max="99" autoFocus
              defaultValue={priceCents}
              className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
              onBlur={(e) => { const cents = parseInt(e.target.value); if (!isNaN(cents) && cents >= 1 && cents <= 99) onOddsOverride(oddsKey, 100 / cents); setEditingOdds(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditingOdds(false); }}
            />
          ) : (
            <span
              onClick={() => setEditingOdds(true)}
              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${isOverridden ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'text-text border-transparent'}`}
              title="Click to adjust price"
            >
              {effectiveOdds.toFixed(2)} <span className="text-muted text-xs font-normal">({priceCents}¢)</span>
            </span>
          )}
          {isOverridden && <button onClick={() => onOddsClear(oddsKey)} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
        </td>
        <td className="text-right text-muted text-sm">
          {vb.fair_odds.toFixed(2)} <span className="text-xs">({fairCents}¢)</span>
        </td>
        <td className="text-right text-muted text-sm">
          {vb.fair_odds > 1 ? `${(100 / vb.fair_odds).toFixed(0)}%` : '-'}
        </td>
        <td className="text-right">
          <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>
        </td>
        <td className="text-right text-sm font-medium" onClick={(e) => e.stopPropagation()}>
          {editingStake ? (
            <input
              type="number" step="0.01" autoFocus
              defaultValue={stakeUsdc.toFixed(2)}
              className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
              onBlur={(e) => { const val = parseFloat(e.target.value); if (!isNaN(val) && val > 0) onStakeOverride(oddsKey, val); setEditingStake(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditingStake(false); }}
            />
          ) : (
            <span
              onClick={() => setEditingStake(true)}
              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${oddsKey in stakeOverride ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'text-text border-transparent'}`}
              title="Click to adjust stake"
            >
              {hasStake ? `$${stakeUsdc.toFixed(2)}` : '-'}
            </span>
          )}
          {oddsKey in stakeOverride && <button onClick={() => onStakeClear(oddsKey)} className="text-muted2 hover:text-text text-[10px] ml-0.5" title="Reset">x</button>}
        </td>
        <td className={`text-right font-semibold text-sm ${edgePct > 0 ? 'text-success' : 'text-error'}`}>{edgePct > 0 ? '+' : ''}{edgePct.toFixed(1)}%</td>
        {(() => { const rt = relativeTime(vb.provider_last_checked ?? vb.updated_at); return <td className={`text-right text-sm ${rt.className}`}>{rt.text}</td>; })()}
      </tr>

      {isSelected && !isSkipped && (
        <tr key={`${vb.event_id}-${vb.outcome}-exp`}>
          <td colSpan={9} className="!p-0" onClick={e => e.stopPropagation()}>
            <div className="px-3 py-2 bg-panel flex items-center gap-2">
              {isPending ? (
                <>
                  <button onClick={onConfirmPlace} disabled={isPlacing || pendingActualCents < 1} className="px-4 py-1.5 bg-success text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap">{isPlacing ? '...' : 'Confirm'}</button>
                  <button onClick={onCancelPending} className="px-2 py-1.5 text-xs text-muted hover:text-text">Cancel</button>
                  <span className="text-muted text-xs">{pendingActualCents}¢</span>
                </>
              ) : (
                <button onClick={() => onStartPlace(vb)} disabled={!hasStake || isPlacing} className="px-4 py-1.5 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap">{isPlacing ? '...' : 'Place Bet'}</button>
              )}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
});

// ──────────────────── PolymarketPage ────────────────────

export function PolymarketPage({ providers = [] }: { providers?: Provider[] }) {
  const queryClient = useQueryClient();
  const { placeBet } = useBetMutations();
  const [activeTab, setActiveTab] = usePersistedState<PolyTab>('bbq_poly_tab', 'value');

  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const { toasts, addToast, dismissToast } = useToast();

  // Parent-level override state (kept here for bet placement logic)
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [stakeOverride, setStakeOverride] = useState<Record<string, number>>({});

  // Two-step bet flow (uniform with ValuePage)
  const [pendingBet, setPendingBet] = useState<{
    vb: PolymarketValueBet;
    actualCents: number;
  } | null>(null);

  // Track placed market+outcome combos for immediate removal from list
  const [placedKeys, setPlacedKeys] = usePersistedState<Set<string>>('bbq_poly_placedKeys', new Set());
  const [myBetsCount, setMyBetsCount] = useState<number | null>(null);
  const [searchInput, setSearchInput] = usePersistedState('bbq_poly_search', '');
  const search = useDeferredValue(searchInput);

  // Rewards state
  const [rewardsSearchInput, setRewardsSearchInput] = usePersistedState('bbq_poly_rewardsSearch', '');
  const rewardsSearch = useDeferredValue(rewardsSearchInput);

  // ──────────────────── Value Bets ────────────────────

  const { data: polyData, isLoading } = useQuery({
    queryKey: ['opportunities', 'polymarket'],
    queryFn: () => api.getPolymarketValue(undefined, undefined, 200),
    placeholderData: keepPreviousData,
  });
  const valueBets = polyData?.value_bets ?? [];

  const { data: betsData } = useQuery({
    queryKey: ['bets', 'pending'],
    queryFn: () => api.getBets('pending', 500),
    staleTime: 10_000,
  });

  // Sync placedKeys and myBetsCount from bets query data
  useEffect(() => {
    if (!betsData?.bets) return;
    const keys = new Set<string>();
    for (const b of betsData.bets) {
      if (b.event_id) {
        keys.add(`${b.event_id}|${b.market}|${b.outcome}|${b.point ?? ''}`);
      }
    }
    setPlacedKeys(prev => {
      const merged = new Set(prev);
      for (const k of keys) merged.add(k);
      return merged;
    });
    setMyBetsCount(countManualSettleBets(betsData.bets));
  }, [betsData]);

  const { data: rewardsData, isLoading: rewardsLoading } = useQuery({
    queryKey: ['polymarket-rewards'],
    queryFn: () => api.getPolymarketRewards(0, undefined, 100),
    enabled: activeTab === 'rewards',
  });
  const rewards = rewardsData?.rewards ?? [];

  const handleSelectOpp = useCallback((idx: number) => {
    setSelectedOpp(prev => prev === idx ? null : idx);
    setPendingBet(null);
  }, []);

  const handleCancelPending = useCallback(() => setPendingBet(null), []);

  const getOddsKey = useCallback((vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.outcome}|${vb.market}|${vb.point ?? ''}`, []);

  const getPlacedKey = (vb: PolymarketValueBet) =>
    `${vb.event_id}|${vb.market}|${vb.outcome}|${vb.point ?? ''}`;

  // Convert decimal odds to price in cents (1/odds * 100)
  const oddsToCents = useCallback((odds: number) => odds > 0 ? Math.round(1 / odds * 100) : 0, []);

  const getEffectiveOdds = useCallback((vb: PolymarketValueBet) =>
    oddsOverride[getOddsKey(vb)] ?? vb.polymarket_odds, [oddsOverride, getOddsKey]);

  // Two-step bet: step 1 — start
  const startPlaceBet = useCallback((vb: PolymarketValueBet) => {
    const oddsKey = getOddsKey(vb);
    const stakeUsdc = stakeOverride[oddsKey] ?? vb.final_stake_usdc;
    if (!stakeUsdc || stakeUsdc <= 0) return;
    const odds = getEffectiveOdds(vb);
    setPendingBet({ vb, actualCents: oddsToCents(odds) });
  }, [getOddsKey, stakeOverride, getEffectiveOdds, oddsToCents]);

  // Two-step bet: step 2 — confirm
  const confirmPlaceBet = useCallback(async () => {
    if (!pendingBet) return;
    const { vb, actualCents } = pendingBet;
    const oddsKey = getOddsKey(vb);
    const stakeUsdc = stakeOverride[oddsKey] ?? vb.final_stake_usdc;
    if (!stakeUsdc || stakeUsdc <= 0 || actualCents < 1) return;
    const actualOdds = 100 / actualCents;
    setIsPlacing(true);

    try {
      await placeBet.mutateAsync({
        event_id: vb.event_id,
        provider_id: 'polymarket',
        market: vb.market,
        outcome: vb.outcome,
        odds: actualOdds,
        stake: stakeUsdc,  // Send USD (native to Polymarket)
        is_bonus: false,
        utility_score: vb.edge_pct != null ? vb.edge_pct / 100 : undefined,
        selection_probability: vb.fair_odds > 1 ? 1 / vb.fair_odds : undefined,
        bet_type: 'polymarket',
      });
      const outcomeLabel = resolveOutcome(vb);
      const confirmedStake = vb.final_stake_usdc ?? 0;
      addToast(`Recorded: $${confirmedStake.toFixed(2)} on ${outcomeLabel} @ ${actualCents}¢ (Polymarket)`, 'success');

      setPlacedKeys(prev => new Set(prev).add(getPlacedKey(vb)));
      setMyBetsCount(prev => (prev ?? 0) + 1);
      setPendingBet(null);
      setSelectedOpp(null);
      queryClient.invalidateQueries({ queryKey: ['opportunities', 'polymarket'] });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to record bet';
      addToast(msg, 'error');
    } finally {
      setIsPlacing(false);
    }
  }, [pendingBet, getOddsKey, stakeOverride, queryClient, placeBet]);

  // Prefer Polymarket's own team names (e.g., "Bulls") over canonical display names ("Chicago Bulls")
  const polyName = useCallback((vb: PolymarketValueBet | PolymarketRewardMarket, side: 'home' | 'away') =>
    side === 'home'
      ? displayTeamName(vb.home_team, vb.poly_home ?? vb.display_home)
      : displayTeamName(vb.away_team, vb.poly_away ?? vb.display_away), []);

  const resolveOutcome = useCallback((vb: PolymarketValueBet): string =>
    resolveOutcomeBase(vb.outcome ?? '?', {
      ...vb,
      display_home: vb.poly_home ?? vb.display_home,
      display_away: vb.poly_away ?? vb.display_away,
    }, 'point' in vb ? vb.point : null, true), []);

  const handleOddsOverride = useCallback((key: string, value: number) =>
    setOddsOverride(prev => ({ ...prev, [key]: value })), []);

  const handleOddsClear = useCallback((key: string) =>
    setOddsOverride(prev => { const next = { ...prev }; delete next[key]; return next; }), []);

  const handleStakeOverride = useCallback((key: string, value: number) =>
    setStakeOverride(prev => ({ ...prev, [key]: value })), []);

  const handleStakeClear = useCallback((key: string) =>
    setStakeOverride(prev => { const next = { ...prev }; delete next[key]; return next; }), []);

  // Remove started/imminent events and placed bets
  const activeValueBets = useMemo(() => {
    let result = valueBets.filter(vb => {
      const ttk = getTTKFromNow(vb.start_time);
      if (ttk !== null && (ttk <= 1 / 60 || ttk > MAX_TTK_HOURS)) return false;
      if (placedKeys.has(getPlacedKey(vb))) return false;
      return true;
    });
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(vb =>
        (vb.home_team?.toLowerCase().includes(q)) ||
        (vb.away_team?.toLowerCase().includes(q)) ||
        (vb.display_home?.toLowerCase().includes(q)) ||
        (vb.display_away?.toLowerCase().includes(q)) ||
        (vb.poly_home?.toLowerCase().includes(q)) ||
        (vb.poly_away?.toLowerCase().includes(q)) ||
        (vb.sport?.toLowerCase().includes(q)) ||
        (vb.league?.toLowerCase().includes(q)) ||
        (vb.outcome?.toLowerCase().includes(q))
      );
    }
    return result;
  }, [valueBets, placedKeys, search]);

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
    useTableSort<PolymarketValueBet, PolySortCol>(activeValueBets, polySortExtractors, { column: 'edge', direction: 'desc' }, 'bbq_poly_sort');

  // ──────────────────── Virtualization ────────────────────

  const tableContainerRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: sortedBets.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: (idx) => {
      // Expanded row adds ~48px for the action bar
      return selectedOpp === idx ? 100 : 52;
    },
    overscan: 10,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();
  const totalSize = rowVirtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom = virtualItems.length > 0 ? totalSize - virtualItems[virtualItems.length - 1].end : 0;

  // ──────────────────── Rewards ────────────────────

  const refetchRewards = () => queryClient.invalidateQueries({ queryKey: ['polymarket-rewards'] });

  const filteredRewards = useMemo(() => {
    let result = rewards.filter(r => {
      const ttk = getTTKFromNow(r.start_time);
      if (ttk !== null && ttk <= 0) return false;
      return true;
    });
    if (rewardsSearch.trim()) {
      const q = rewardsSearch.trim().toLowerCase();
      result = result.filter(r =>
        (r.home_team?.toLowerCase().includes(q)) ||
        (r.away_team?.toLowerCase().includes(q)) ||
        (r.display_home?.toLowerCase().includes(q)) ||
        (r.display_away?.toLowerCase().includes(q)) ||
        (r.poly_home?.toLowerCase().includes(q)) ||
        (r.poly_away?.toLowerCase().includes(q)) ||
        (r.sport?.toLowerCase().includes(q)) ||
        (r.league?.toLowerCase().includes(q))
      );
    }
    return result;
  }, [rewards, rewardsSearch]);

  type RewardSortCol = 'comp' | 'spread' | 'min' | 'ttk';
  const rewardSortExtractors = useMemo(() => ({
    comp:   (r: PolymarketRewardMarket) => r.competitive,
    spread: (r: PolymarketRewardMarket) => r.rewards_max_spread,
    min:    (r: PolymarketRewardMarket) => r.rewards_min_size,
    ttk:    (r: PolymarketRewardMarket) => getTTKFromNow(r.start_time) ?? 99999,
  }), []);
  const { sorted: sortedRewards, sort: rewardSort, toggle: toggleRewardSort } =
    useTableSort<PolymarketRewardMarket, RewardSortCol>(filteredRewards, rewardSortExtractors, { column: 'comp', direction: 'asc' }, 'bbq_poly_rewardSort');

  const compBadge = (comp: number) => {
    // 0-1 scale: lower = less competition = better for rewards
    if (comp < 0.3) return <span className="text-[10px] px-1.5 py-0.5 bg-success/15 text-success">{(comp * 100).toFixed(0)}%</span>;
    if (comp < 0.7) return <span className="text-[10px] px-1.5 py-0.5 bg-warning/15 text-warning">{(comp * 100).toFixed(0)}%</span>;
    return <span className="text-[10px] px-1.5 py-0.5 bg-error/15 text-error">{(comp * 100).toFixed(0)}%</span>;
  };

  // ──────────────────── Render ────────────────────

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="polymarket" color={TAB_COLORS.polymarket} size={16} />
          Polymarket
        </h2>
        {activeTab === 'value' && (
          <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search event, sport..." accentColor="tabPolymarket" />
        )}
        {activeTab === 'rewards' && (
          <SearchInput value={rewardsSearchInput} onChange={setRewardsSearchInput} placeholder="Search rewards..." accentColor="tabPolymarket" />
        )}
      </div>

      {/* Tab Selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'value' as PolyTab, label: 'Value Bets', count: sortedBets.length },
          { id: 'rewards' as PolyTab, label: 'Rewards', count: rewardsData ? sortedRewards.length : null },
          { id: 'mybets' as PolyTab, label: 'My Bets', count: myBetsCount },
          { id: 'manual' as PolyTab, label: 'Manual', count: null },
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
        <MyBetsSection filter={polyBetFilter} colorKey="polymarket" persistKey="poly" />
      )}

      {/* ═══════════════ MANUAL TAB ═══════════════ */}
      {activeTab === 'manual' && (
        <ManualBetForm providers={providers} providerFilter={polyProviderFilter} accentColor="tabPolymarket" betType="polymarket" onSuccess={(msg) => { addToast(msg, 'success'); setActiveTab('mybets'); }} onError={(msg) => addToast(msg, 'error')} />
      )}

      {/* ═══════════════ REWARDS TAB ═══════════════ */}
      {activeTab === 'rewards' && <>
        <div className="flex justify-end">
          <button
            onClick={refetchRewards}
            disabled={rewardsLoading}
            className="px-2 py-1 text-xs text-muted hover:text-text border border-border hover:border-tabPolymarket/50 transition-colors disabled:opacity-50"
          >
            {rewardsLoading ? '...' : '↻'}
          </button>
        </div>

        {rewardsLoading && rewards.length === 0 ? (
          <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading rewards...</div>
        ) : sortedRewards.length === 0 ? (
          <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No Polymarket reward markets matched to Pinnacle.</div>
        ) : (
          <div className="border-l-2 border-tabPolymarket">
          <table className="sq">
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Event</th>
                <SortableHeader column="comp" label="Comp" sort={rewardSort} onToggle={toggleRewardSort} />
                <SortableHeader column="spread" label="Spread" sort={rewardSort} onToggle={toggleRewardSort} />
                <SortableHeader column="min" label="Min$" sort={rewardSort} onToggle={toggleRewardSort} />
                <th className="text-right">Poly Prices</th>
                <th className="text-right">Best Hedge</th>
                <SortableHeader column="ttk" label="TTK" sort={rewardSort} onToggle={toggleRewardSort} />
              </tr>
            </thead>
            <tbody>
              {sortedRewards.map(r => {
                const outcomes = Object.keys(r.poly_prices).length > 0 ? Object.keys(r.poly_prices) : Object.keys(r.pinnacle_fair_odds);
                return (
                  <tr key={r.event_id}>
                    <td>
                      <div className="flex items-center gap-2 min-w-0">
                        {r.event_slug ? (
                          <a href={r.polymarket_url ?? `https://polymarket.com/event/${r.event_slug}`} target="_blank" rel="noopener noreferrer" className="text-text text-sm truncate hover:text-tabPolymarket transition-colors">
                            {polyName(r, 'home')} vs {polyName(r, 'away')}
                          </a>
                        ) : (
                          <span className="text-text text-sm truncate">{polyName(r, 'home')} vs {polyName(r, 'away')}</span>
                        )}
                      </div>
                      <div className="text-muted2 text-[11px]">
                        {r.sport} · {formatDateTime(r.start_time)}
                      </div>
                    </td>
                    <td className="text-right">{compBadge(r.competitive)}</td>
                    <td className="text-right text-sm text-muted">{r.rewards_max_spread}¢</td>
                    <td className="text-right text-sm text-muted">{r.rewards_min_size}</td>
                    <td className="text-right text-sm">
                      <div className="flex flex-col items-end gap-0.5">
                        {outcomes.map(o => {
                          const price = r.poly_prices[o];
                          return price != null ? (
                            <span key={o} className="text-muted">
                              <span className="text-muted2 text-[10px]">{o === 'home' ? 'H' : o === 'away' ? 'A' : 'D'}</span>{' '}
                              {Math.round(price * 100)}¢
                            </span>
                          ) : null;
                        })}
                      </div>
                    </td>
                    <td className="text-right text-sm">
                      <div className="flex flex-col items-end gap-0.5">
                        {outcomes.map(o => {
                          const hedge = r.best_hedge_odds[o];
                          const fair = r.pinnacle_fair_odds[o];
                          return (
                            <span key={o} className="text-muted">
                              {hedge ? (
                                <><span className="text-text">{hedge.odds.toFixed(2)}</span> <span className="text-muted2 text-[10px]">{hedge.provider}</span></>
                              ) : fair ? (
                                <span className="text-muted2">{fair.toFixed(2)} <span className="text-[10px]">pin</span></span>
                              ) : '-'}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="text-right">
                      {(() => { const ttk = getTTKFromNow(r.start_time); return <span className={`text-sm ${getTTKColor(ttk)}`}>{formatTTKLabel(ttk)}</span>; })()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </>}

      {/* ═══════════════ VALUE BETS TAB ═══════════════ */}
      {activeTab === 'value' && <>
        {/* Feedback toasts */}
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />

        {isLoading && valueBets.length === 0 ? (
          <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading...</div>
        ) : sortedBets.length === 0 ? (
          <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No Polymarket value bets found. Run extraction first.</div>
        ) : (
          <div className="border-l-2 border-tabPolymarket flex-1 min-h-0 relative">
            <div
              ref={tableContainerRef}
              className="absolute inset-0 overflow-y-auto"
            >
              <table className="sq" style={{ width: '100%' }}>
                <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
                  <tr>
                    <th style={{ width: '35%' }}>Event</th>
                    <th className="text-right">Outcome</th>
                    <SortableHeader column="odds" label="Odds" sort={polySort} onToggle={togglePolySort} />
                    <SortableHeader column="fair" label="Fair" sort={polySort} onToggle={togglePolySort} />
                    <SortableHeader column="prob" label="Prob" sort={polySort} onToggle={togglePolySort} />
                    <SortableHeader column="ttk" label="TTK" sort={polySort} onToggle={togglePolySort} />
                    <SortableHeader column="stake" label="Stake" sort={polySort} onToggle={togglePolySort} />
                    <SortableHeader column="edge" label="Edge" sort={polySort} onToggle={togglePolySort} />
                    <th className="text-right">Upd</th>
                  </tr>
                </thead>
                <tbody>
                  {paddingTop > 0 && (
                    <tr><td colSpan={9} style={{ height: paddingTop, padding: 0 }} /></tr>
                  )}
                  {virtualItems.map(virtualRow => {
                    const vb = sortedBets[virtualRow.index];
                    const idx = virtualRow.index;
                    return (
                      <PolyRow
                        key={`${vb.event_id}-${vb.market}-${vb.outcome}-${idx}`}
                        vb={vb}
                        idx={idx}
                        isSelected={selectedOpp === idx}
                        isPending={pendingBet?.vb === vb}
                        isPlacing={isPlacing}
                        onSelect={handleSelectOpp}
                        onStartPlace={startPlaceBet}
                        onConfirmPlace={confirmPlaceBet}
                        onCancelPending={handleCancelPending}
                        polyName={polyName}
                        resolveOutcome={resolveOutcome}
                        getOddsKey={getOddsKey}
                        oddsToCents={oddsToCents}
                        oddsOverride={oddsOverride}
                        stakeOverride={stakeOverride}
                        onOddsOverride={handleOddsOverride}
                        onOddsClear={handleOddsClear}
                        onStakeOverride={handleStakeOverride}
                        onStakeClear={handleStakeClear}
                        pendingActualCents={pendingBet?.vb === vb ? pendingBet.actualCents : 0}
                      />
                    );
                  })}
                  {paddingBottom > 0 && (
                    <tr><td colSpan={9} style={{ height: paddingBottom, padding: 0 }} /></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </>}
    </div>
  );
}
