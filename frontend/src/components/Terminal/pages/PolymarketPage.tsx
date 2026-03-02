import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { formatDateTime, getTTKFromNow, formatTTKLabel, getTTKColor } from '@/utils/formatters';
import { useRefreshOnExtraction } from '@/hooks/useExtractionStatus';
import { useTableSort } from '@/hooks/useTableSort';
import { useRecorder } from '@/contexts/RecorderContext';
import { SortableHeader } from '../SortableHeader';
import { TabIcon, TAB_COLORS } from '../TabBar';
import type { PolymarketValueBet, PolyPortfolio, PolyMyBetsResponse, PolyMyBet } from '@/types';

type PolyTab = 'value' | 'portfolio' | 'mybets';

export function PolymarketPage() {
  const { startAutoRecord, stopAutoRecord, navigateCdp } = useRecorder();
  const [activeTab, setActiveTab] = useState<PolyTab>('value');

  // Wallet state
  const [walletAddress, setWalletAddress] = useState<string | null>(null);
  const [walletInput, setWalletInput] = useState('');
  const [isEditingWallet, setIsEditingWallet] = useState(false);
  const [walletError, setWalletError] = useState<string | null>(null);

  // Portfolio state
  const [portfolio, setPortfolio] = useState<PolyPortfolio | null>(null);
  const [portfolioLoading, setPortfolioLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  // MyBets state
  const [myBetsData, setMyBetsData] = useState<PolyMyBetsResponse | null>(null);
  const [myBetsLoading, setMyBetsLoading] = useState(false);

  // Value bets state
  const [valueBets, setValueBets] = useState<PolymarketValueBet[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [selectedOpp, setSelectedOpp] = useState<number | null>(null);
  const [isPlacing, setIsPlacing] = useState(false);
  const [betSuccess, setBetSuccess] = useState<string | null>(null);
  const [betError, setBetError] = useState<string | null>(null);

  // Odds editing state (uniform with ValuePage)
  const [oddsOverride, setOddsOverride] = useState<Record<string, number>>({});
  const [editingOdds, setEditingOdds] = useState<string | null>(null);

  // Stake editing state (USDC override, bypasses balance constraints)
  const [stakeOverride, setStakeOverride] = useState<Record<string, number>>({});
  const [editingStake, setEditingStake] = useState<string | null>(null);

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

  // ──────────────────── Wallet ────────────────────

  const fetchWallet = useCallback(async () => {
    try {
      const res = await api.getPolymarketWallet();
      setWalletAddress(res.wallet_address);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchWallet(); }, [fetchWallet]);

  const saveWallet = async () => {
    const addr = walletInput.trim();
    if (!/^0x[a-fA-F0-9]{40}$/.test(addr)) {
      setWalletError('Invalid address — must be 0x + 40 hex chars');
      return;
    }
    try {
      await api.setPolymarketWallet(addr);
      setWalletAddress(addr);
      setIsEditingWallet(false);
      setWalletError(null);
    } catch (err) {
      setWalletError(err instanceof Error ? err.message : 'Failed to save wallet');
    }
  };

  // ──────────────────── Portfolio ────────────────────

  const fetchPortfolio = useCallback(async () => {
    setPortfolioLoading(true);
    try {
      const res = await api.getPolymarketPortfolio();
      setPortfolio(res);
      if (res.wallet) setWalletAddress(res.wallet);
    } catch (err) {
      console.error('Portfolio fetch failed:', err);
    } finally {
      setPortfolioLoading(false);
    }
  }, []);

  const triggerSync = async () => {
    setIsSyncing(true);
    setSyncMessage(null);
    try {
      const res = await api.syncPolymarket();
      if (res.error) {
        setSyncMessage(`Sync error: ${res.error}`);
      } else {
        const parts = [];
        if (res.settled_count > 0) parts.push(`${res.settled_count} settled`);
        if (res.balance_updated && res.balance != null) parts.push(`balance: $${res.balance.toFixed(2)}`);
        if (res.pending_remaining > 0) parts.push(`${res.pending_remaining} pending`);
        setSyncMessage(parts.length > 0 ? parts.join(', ') : 'Up to date');
      }
      // Refresh portfolio after sync
      fetchPortfolio();
      setTimeout(() => setSyncMessage(null), 8000);
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : 'Sync failed');
    } finally {
      setIsSyncing(false);
    }
  };

  // ──────────────────── MyBets ────────────────────

  const fetchMyBets = useCallback(async () => {
    setMyBetsLoading(true);
    try {
      const res = await api.getPolymarketMyBets(undefined, 100);
      setMyBetsData(res);
    } catch (err) {
      console.error('MyBets fetch failed:', err);
    } finally {
      setMyBetsLoading(false);
    }
  }, []);

  // ──────────────────── Value Bets ────────────────────

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

  // Fetch data based on active tab
  useEffect(() => {
    if (activeTab === 'value') fetchValueData();
    else if (activeTab === 'portfolio') fetchPortfolio();
    else if (activeTab === 'mybets') fetchMyBets();
  }, [activeTab, fetchValueData, fetchPortfolio, fetchMyBets]);

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
    `${vb.event_id}|polymarket`;

  // Convert decimal odds to price in cents (1/odds * 100)
  const oddsToCents = (odds: number) => odds > 0 ? Math.round(1 / odds * 100) : 0;

  // Recalculate edge/stake/shares when price or stake is overridden
  const getEffectiveMetrics = (vb: PolymarketValueBet) => {
    const effectiveOdds = getEffectiveOdds(vb);
    const priceCents = oddsToCents(effectiveOdds);
    const fairCents = vb.fair_price_cents ?? oddsToCents(vb.fair_odds);
    const edgePct = vb.fair_odds > 1 ? (effectiveOdds / vb.fair_odds - 1) * 100 : vb.edge_pct;
    const edgeRatio = vb.edge_pct > 0 ? edgePct / vb.edge_pct : 1;
    const key = getOddsKey(vb);
    const stakeUsdc = key in stakeOverride
      ? stakeOverride[key]
      : (vb.final_stake_usdc ?? 0) * Math.max(0, edgeRatio);
    const shares = priceCents > 0 ? stakeUsdc / (priceCents / 100) : 0;
    const payoutUsdc = shares * 1.0;
    const profitUsdc = payoutUsdc - stakeUsdc;
    const stakeOverridden = key in stakeOverride;
    return { priceCents, fairCents, edgePct, stakeUsdc, shares, payoutUsdc, profitUsdc, stakeOverridden };
  };

  // Get effective stake in SEK (uses override if set)
  const getEffectiveStake = (vb: PolymarketValueBet) => {
    const key = getOddsKey(vb);
    if (key in stakeOverride) {
      const rate = vb.exchange_rate_sek ?? 10.5;
      return stakeOverride[key] * rate;
    }
    return vb.final_stake;
  };

  // Step 1: Fill bet slip via CDP
  const startPlaceBet = async (vb: PolymarketValueBet, idx: number) => {
    const stake = getEffectiveStake(vb);
    if (!stake || stake <= 0) return;
    const odds = getEffectiveOdds(vb);
    setIsPlacing(true);
    setBetError(null);
    setBetSuccess(null);

    try {
      let navUrl: string | null = null;
      let windowName = 'bbq_polymarket';
      let slipOdds = odds;

      try {
        const slip = await api.fillSlip({
          provider_id: 'polymarket',
          event_id: vb.event_id,
          market: vb.market,
          outcome: vb.outcome,
          point: vb.point,
          stake,
          expected_odds: odds,
          home_team: vb.home_team,
          away_team: vb.away_team,
          provider_meta: vb.provider_meta,
        });
        navUrl = slip.url;
        if (slip.actual_odds) slipOdds = slip.actual_odds;

        // Post-login sync: update wallet + refresh stakes if balance synced
        const slipAny = slip as Record<string, unknown>;
        if (slipAny.wallet_address && typeof slipAny.wallet_address === 'string') {
          setWalletAddress(slipAny.wallet_address as string);
        }
        if (slipAny.balance_updated) {
          // Balance synced — refresh value bets to recalculate stakes
          fetchValueData();
          const bal = typeof slipAny.balance === 'number' ? `$${(slipAny.balance as number).toFixed(2)}` : '';
          setBetSuccess(`Synced ${bal} — place bet on Polymarket and confirm below.`);
          setTimeout(() => setBetSuccess(null), 8000);
        } else if (slip.status === 'ready' || slip.status === 'navigated_only') {
          setBetSuccess('Opened on Polymarket — place bet and confirm below.');
          setTimeout(() => setBetSuccess(null), 8000);
        }
      } catch {
        try {
          const nav = await api.navigateToEvent({
            provider_id: 'polymarket',
            provider_meta: vb.provider_meta,
            home_team: vb.home_team,
            away_team: vb.away_team,
            event_id: vb.event_id,
          });
          navUrl = nav.url;
          windowName = nav.window_name;
        } catch { /* best-effort */ }
      }

      setPendingBet({ idx, vb, actualOdds: slipOdds, navUrl, windowName });
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
    const stake = getEffectiveStake(vb);
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

      setPlacedKeys(prev => new Set(prev).add(getPlacedKey(vb)));
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

  const resolveOutcome = (vb: PolymarketValueBet | PolyMyBet): string => {
    const point = 'point' in vb && vb.point != null ? ` ${vb.point}` : '';
    const home = 'home_team' in vb ? vb.home_team : null;
    const away = 'away_team' in vb ? vb.away_team : null;
    if (vb.outcome === 'home' && home) return home;
    if (vb.outcome === 'away' && away) return away;
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

  // ──────────────────── Helpers ────────────────────

  const truncAddr = (addr: string) => `${addr.slice(0, 6)}...${addr.slice(-4)}`;
  const pnlColor = (v: number) => v > 0.01 ? 'text-success' : v < -0.01 ? 'text-error' : 'text-muted';
  const pnlSign = (v: number) => v > 0 ? '+' : '';
  const resultBadge = (r: string) => {
    if (r === 'won') return 'text-success';
    if (r === 'lost') return 'text-error';
    if (r === 'void') return 'text-muted';
    return 'text-warning';
  };

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="polymarket" color={TAB_COLORS.polymarket} size={16} />
          Polymarket
        </h2>
      </div>

      {/* Wallet Status Bar */}
      <div className="px-3 py-2 bg-panel border border-border flex items-center gap-3 text-xs">
        <span className="text-muted2 uppercase tracking-wider">Wallet:</span>
        {isEditingWallet ? (
          <>
            <input
              type="text"
              value={walletInput}
              onChange={(e) => setWalletInput(e.target.value)}
              placeholder="0x..."
              className="flex-1 max-w-xs bg-bg border border-tabPolymarket/50 text-text text-xs px-2 py-1 focus:outline-none focus:border-tabPolymarket font-mono"
              onKeyDown={(e) => { if (e.key === 'Enter') saveWallet(); if (e.key === 'Escape') setIsEditingWallet(false); }}
              autoFocus
            />
            <button onClick={saveWallet} className="px-2 py-1 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90">Save</button>
            <button onClick={() => { setIsEditingWallet(false); setWalletError(null); }} className="text-muted hover:text-text text-xs">Cancel</button>
            {walletError && <span className="text-error text-xs">{walletError}</span>}
          </>
        ) : walletAddress ? (
          <>
            <span className="font-mono text-text">{truncAddr(walletAddress)}</span>
            <button
              onClick={() => { setWalletInput(walletAddress); setIsEditingWallet(true); }}
              className="text-muted2 hover:text-text text-[10px]"
            >
              edit
            </button>
          </>
        ) : (
          <>
            <span className="text-muted">No wallet detected</span>
            <button
              onClick={() => { setWalletInput(''); setIsEditingWallet(true); }}
              className="px-2 py-1 border border-tabPolymarket/40 text-tabPolymarket text-xs hover:bg-tabPolymarket/10"
            >
              Set manually
            </button>
            <span className="text-muted2">or log into polymarket.com in Chrome to auto-detect</span>
          </>
        )}
      </div>

      {/* Tab Selector */}
      <div className="flex gap-1 border-b border-border">
        {([
          { id: 'value' as PolyTab, label: 'Value Bets', count: sortedBets.length },
          { id: 'portfolio' as PolyTab, label: 'Portfolio' },
          { id: 'mybets' as PolyTab, label: 'My Bets', count: myBetsData?.stats.total_bets },
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
      {syncMessage && (
        <div className={`px-3 py-2 text-xs border ${syncMessage.includes('error') || syncMessage.includes('failed') ? 'bg-error/10 border-error/30 text-error' : 'bg-success/10 border-success/30 text-success'} flex items-center justify-between`}>
          <span>Sync: {syncMessage}</span>
          <button onClick={() => setSyncMessage(null)} className="ml-2 opacity-60 hover:opacity-100">x</button>
        </div>
      )}

      {/* ═══════════════ PORTFOLIO TAB ═══════════════ */}
      {activeTab === 'portfolio' && (
        <div className="space-y-3">
          {/* Summary Cards */}
          {portfolioLoading && !portfolio ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading portfolio...</div>
          ) : !walletAddress ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Set a wallet address to view portfolio.</div>
          ) : portfolio ? (
            <>
              <div className="grid grid-cols-5 gap-2">
                {[
                  {
                    label: 'Total Value',
                    value: `$${portfolio.total_value_usdc.toFixed(2)}`,
                    sub: `${portfolio.total_value_sek.toFixed(0)} SEK`,
                  },
                  {
                    label: 'Cash',
                    value: `$${(portfolio.cash_balance ?? 0).toFixed(2)}`,
                    sub: portfolio.position_value > 0 ? `+$${portfolio.position_value.toFixed(2)} in positions` : portfolio.redeemable_value > 0 ? `+$${portfolio.redeemable_value.toFixed(2)} redeemable` : undefined,
                  },
                  { label: 'Open', value: String(portfolio.open_count), sub: `${portfolio.closed_count} closed` },
                  { label: 'Unrealized', value: `${pnlSign(portfolio.unrealized_pnl)}$${Math.abs(portfolio.unrealized_pnl).toFixed(2)}`, color: pnlColor(portfolio.unrealized_pnl) },
                  { label: 'Realized', value: `${pnlSign(portfolio.realized_pnl)}$${Math.abs(portfolio.realized_pnl).toFixed(2)}`, color: pnlColor(portfolio.realized_pnl) },
                ].map(card => (
                  <div key={card.label} className="bg-panel border border-border px-3 py-2">
                    <div className="text-muted2 text-[10px] uppercase tracking-wider">{card.label}</div>
                    <div className={`text-sm font-semibold ${card.color ?? 'text-text'}`}>{card.value}</div>
                    {card.sub && <div className="text-muted2 text-[10px]">{card.sub}</div>}
                  </div>
                ))}
              </div>

              {/* Sync Button */}
              <div className="flex items-center gap-2">
                <button
                  onClick={triggerSync}
                  disabled={isSyncing}
                  className="px-3 py-1.5 bg-tabPolymarket text-bg text-xs font-medium hover:opacity-90 disabled:opacity-50"
                >
                  {isSyncing ? 'Syncing...' : 'Sync Account'}
                </button>
                <button
                  onClick={fetchPortfolio}
                  disabled={portfolioLoading}
                  className="px-3 py-1.5 border border-border text-muted text-xs hover:text-text disabled:opacity-50"
                >
                  Refresh
                </button>
              </div>

              {/* Open Positions */}
              {portfolio.positions.length > 0 && (
                <>
                  <h3 className="text-xs text-muted2 uppercase tracking-wider">Open Positions ({portfolio.positions.length})</h3>
                  <div className="border-l-2 border-tabPolymarket">
                    <table className="sq">
                      <thead>
                        <tr>
                          <th>Event</th>
                          <th className="text-right">Outcome</th>
                          <th className="text-right">Shares</th>
                          <th className="text-right">Avg</th>
                          <th className="text-right">Current</th>
                          <th className="text-right">Value</th>
                          <th className="text-right">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {portfolio.positions.map(p => (
                          <tr key={`${p.condition_id}-${p.outcome}`}>
                            <td>
                              <span className="text-text text-sm">{p.title}</span>
                              {p.redeemable && <span className="ml-1 text-[9px] px-1 py-0.5 bg-success/15 text-success">REDEEMABLE</span>}
                            </td>
                            <td className="text-right text-text text-sm">{p.outcome}</td>
                            <td className="text-right text-text text-sm">{p.size.toFixed(1)}</td>
                            <td className="text-right text-muted text-sm">{Math.round(p.avg_price * 100)}¢</td>
                            <td className="text-right text-text text-sm">{Math.round(p.cur_price * 100)}¢</td>
                            <td className="text-right text-text text-sm font-medium">${p.current_value.toFixed(2)}</td>
                            <td className={`text-right text-sm font-medium ${pnlColor(p.cash_pnl)}`}>
                              {pnlSign(p.cash_pnl)}${Math.abs(p.cash_pnl).toFixed(2)}
                              <span className="text-muted2 text-[10px] ml-1">({pnlSign(p.percent_pnl)}{Math.abs(p.percent_pnl).toFixed(0)}%)</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {/* Closed Positions */}
              {portfolio.closed_positions.length > 0 && (
                <>
                  <h3 className="text-xs text-muted2 uppercase tracking-wider">Closed Positions ({portfolio.closed_positions.length})</h3>
                  <div className="border-l-2 border-muted/30">
                    <table className="sq">
                      <thead>
                        <tr>
                          <th>Event</th>
                          <th className="text-right">Outcome</th>
                          <th className="text-right">Shares</th>
                          <th className="text-right">Avg</th>
                          <th className="text-right">Final</th>
                          <th className="text-right">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {portfolio.closed_positions.map(c => (
                          <tr key={`${c.condition_id}-${c.outcome}`} className="opacity-70">
                            <td>
                              <span className="text-text text-sm">{c.title}</span>
                              {c.end_date && <div className="text-muted2 text-[10px]">{c.end_date}</div>}
                            </td>
                            <td className="text-right text-text text-sm">{c.outcome}</td>
                            <td className="text-right text-muted text-sm">{c.total_bought.toFixed(1)}</td>
                            <td className="text-right text-muted text-sm">{Math.round(c.avg_price * 100)}¢</td>
                            <td className="text-right text-muted text-sm">{Math.round(c.cur_price * 100)}¢</td>
                            <td className={`text-right text-sm font-medium ${pnlColor(c.realized_pnl)}`}>
                              {pnlSign(c.realized_pnl)}${Math.abs(c.realized_pnl).toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {portfolio.positions.length === 0 && portfolio.closed_positions.length === 0 && (
                <div className="text-muted text-sm py-4 text-center">No positions found for this wallet.</div>
              )}
            </>
          ) : null}
        </div>
      )}

      {/* ═══════════════ MY BETS TAB ═══════════════ */}
      {activeTab === 'mybets' && (
        <div className="space-y-3">
          {myBetsLoading && !myBetsData ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">Loading bets...</div>
          ) : myBetsData ? (
            <>
              {/* Stats Summary */}
              <div className="grid grid-cols-5 gap-2">
                {[
                  { label: 'Bets', value: String(myBetsData.stats.total_bets), sub: `${myBetsData.stats.pending} pending` },
                  { label: 'Win Rate', value: `${myBetsData.stats.win_rate.toFixed(0)}%`, sub: `${myBetsData.stats.wins}W ${myBetsData.stats.losses}L` },
                  { label: 'P&L', value: `${pnlSign(myBetsData.stats.total_profit_usdc)}$${Math.abs(myBetsData.stats.total_profit_usdc).toFixed(2)}`, color: pnlColor(myBetsData.stats.total_profit_usdc) },
                  { label: 'ROI', value: `${pnlSign(myBetsData.stats.roi_pct)}${Math.abs(myBetsData.stats.roi_pct).toFixed(1)}%`, color: pnlColor(myBetsData.stats.roi_pct) },
                  { label: 'Avg Edge', value: `${myBetsData.stats.avg_edge.toFixed(1)}%` },
                ].map(card => (
                  <div key={card.label} className="bg-panel border border-border px-3 py-2">
                    <div className="text-muted2 text-[10px] uppercase tracking-wider">{card.label}</div>
                    <div className={`text-sm font-semibold ${card.color ?? 'text-text'}`}>{card.value}</div>
                    {card.sub && <div className="text-muted2 text-[10px]">{card.sub}</div>}
                  </div>
                ))}
              </div>

              {/* Bets Table */}
              {myBetsData.bets.length > 0 ? (
                <div className="border-l-2 border-tabPolymarket">
                  <table className="sq">
                    <thead>
                      <tr>
                        <th>Event</th>
                        <th className="text-right">Outcome</th>
                        <th className="text-right">Price</th>
                        <th className="text-right">Stake</th>
                        <th className="text-right">Edge</th>
                        <th className="text-right">Result</th>
                        <th className="text-right">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {myBetsData.bets.map(b => {
                        const priceCents = b.odds > 0 ? Math.round(1 / b.odds * 100) : 0;
                        return (
                          <tr key={b.id}>
                            <td>
                              <div className="text-text text-sm">{b.home_team && b.away_team ? `${b.home_team} vs ${b.away_team}` : `Bet #${b.id}`}</div>
                              <div className="text-muted2 text-[10px]">
                                {b.sport}{b.placed_at ? ` · ${formatDateTime(b.placed_at)}` : ''}
                              </div>
                            </td>
                            <td className="text-right text-text text-sm">{resolveOutcome(b)}</td>
                            <td className="text-right text-text text-sm">{priceCents}¢</td>
                            <td className="text-right text-text text-sm font-medium">${b.stake_usdc.toFixed(2)}</td>
                            <td className="text-right text-tabPolymarket text-sm">
                              {b.edge_pct != null ? `+${b.edge_pct.toFixed(1)}%` : '-'}
                            </td>
                            <td className={`text-right text-sm font-medium ${resultBadge(b.result)}`}>
                              {b.result}
                            </td>
                            <td className={`text-right text-sm font-medium ${pnlColor(b.profit_usdc)}`}>
                              {b.result === 'pending' ? '-' : `${pnlSign(b.profit_usdc)}$${Math.abs(b.profit_usdc).toFixed(2)}`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-muted text-sm py-4 text-center">No Polymarket bets placed yet.</div>
              )}
            </>
          ) : (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">No data available.</div>
          )}
        </div>
      )}

      {/* ═══════════════ VALUE BETS TAB ═══════════════ */}
      {activeTab === 'value' && (
        <>
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
                        <td className="text-right text-sm font-medium text-text" onClick={e => e.stopPropagation()}>
                          {editingStake === oddsKey ? (
                            <input
                              type="number"
                              step="0.5"
                              min="0.01"
                              autoFocus
                              defaultValue={m.stakeUsdc.toFixed(2)}
                              className="w-16 bg-bg border border-tabPolymarket/50 text-text text-xs px-1 py-0.5 text-right focus:outline-none focus:border-tabPolymarket"
                              onBlur={(e) => {
                                const val = parseFloat(e.target.value);
                                if (!isNaN(val) && val > 0) {
                                  setStakeOverride(prev => ({ ...prev, [oddsKey]: val }));
                                }
                                setEditingStake(null);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                if (e.key === 'Escape') setEditingStake(null);
                              }}
                            />
                          ) : (
                            <span
                              onClick={() => setEditingStake(oddsKey)}
                              className={`cursor-pointer px-1 py-0.5 border border-dashed hover:border-tabPolymarket/50 transition-colors ${m.stakeOverridden ? 'text-tabPolymarket font-medium border-tabPolymarket/30' : 'border-transparent'}`}
                              title="Click to edit stake"
                            >
                              {m.stakeUsdc > 0 ? `$${m.stakeUsdc.toFixed(2)}` : '-'}
                            </span>
                          )}
                          {m.stakeOverridden && (
                            <button
                              onClick={() => setStakeOverride(prev => { const next = { ...prev }; delete next[oddsKey]; return next; })}
                              className="text-muted2 hover:text-text text-[10px] ml-0.5"
                              title="Reset to calculated"
                            >
                              x
                            </button>
                          )}
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
                                    onClick={() => { startAutoRecord('polymarket', 'place_bet'); navigateCdp(pendingBet!.navUrl); }}
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
                                  disabled={(!hasStake && !m.stakeOverridden) || isPlacing}
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
        </>
      )}
    </div>
  );
}
