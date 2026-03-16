import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import { CandleChart } from './CandleChart';
import { GaugeStrip } from './GaugeStrip';
import type { ExpandedSession, IndicatorsResponse, TradingSignal, ScanCondition, CandleData } from '@/types/market';

// ─── Constants ───────────────────────────────────────────────────────────────

const SETUP_COLORS: Record<string, string> = {
  spring: 'bg-emerald-600', sfp: 'bg-blue-600', poor_extreme: 'bg-purple-600',
  ib_break: 'bg-orange-600', rule_80: 'bg-cyan-600', fakeout: 'bg-red-600',
  break_from_balance: 'bg-amber-600', double_distribution: 'bg-pink-600',
  news_directional: 'bg-indigo-600',
};

// ─── Types ───────────────────────────────────────────────────────────────────

export interface LadderLevel {
  price: number;
  label: string;
  category: string;
  zone?: boolean;
  priceHigh?: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function classifyLevel(type: string): string {
  const t = type.toLowerCase();
  if (t === 'vwap') return 'vwap';
  if (t.includes('sd')) return 'sd';
  if (t.includes('poc')) return 'poc';
  if (t.includes('vah')) return 'vah';
  if (t.includes('val')) return 'val';
  if (t.includes('ib')) return 'ib';
  if (t.includes('pdh') || t === 'weekly_high' || t === 'monthly_high') return 'pdh';
  if (t.includes('pdl') || t === 'weekly_low' || t === 'monthly_low') return 'pdl';
  if (t.includes('swing') || t.includes('hh') || t.includes('hl') || t.includes('lh') || t.includes('ll')) return 'swing';
  if (t.includes('order_block') || t.includes('ob')) return 'ob';
  if (t.includes('fvg')) return 'fvg';
  if (t.includes('overnight')) return 'overnight';
  if (t.includes('naked')) return 'naked';
  if (t.includes('tokyo') || t.includes('london')) return 'session';
  return 'default';
}

/** Aggregate all levels from session data into a unified sorted list */
function buildLadder(session: ExpandedSession | null): LadderLevel[] {
  if (!session) return [];
  const levels: LadderLevel[] = [];
  const s = session.session;
  const profiles = session.profiles;
  const structure = session.structure;

  // VWAP bands
  if (s?.vwap != null) levels.push({ price: s.vwap, label: 'VWAP', category: 'vwap' });
  if (s?.vwap_1sd_upper != null) levels.push({ price: s.vwap_1sd_upper, label: '+1 SD', category: 'sd' });
  if (s?.vwap_1sd_lower != null) levels.push({ price: s.vwap_1sd_lower, label: '-1 SD', category: 'sd' });
  if (s?.vwap_2sd_upper != null) levels.push({ price: s.vwap_2sd_upper, label: '+2 SD', category: 'sd' });
  if (s?.vwap_2sd_lower != null) levels.push({ price: s.vwap_2sd_lower, label: '-2 SD', category: 'sd' });
  if (s?.vwap_3sd_upper != null) levels.push({ price: s.vwap_3sd_upper, label: '+3 SD', category: 'sd' });
  if (s?.vwap_3sd_lower != null) levels.push({ price: s.vwap_3sd_lower, label: '-3 SD', category: 'sd' });

  // IB
  if (s?.ib_high != null) levels.push({ price: s.ib_high, label: 'IB High', category: 'ib' });
  if (s?.ib_low != null) levels.push({ price: s.ib_low, label: 'IB Low', category: 'ib' });

  // Session VP
  if (s?.poc != null) levels.push({ price: s.poc, label: 'Session POC', category: 'poc' });
  if (s?.vah != null) levels.push({ price: s.vah, label: 'Session VAH', category: 'vah' });
  if (s?.val != null) levels.push({ price: s.val, label: 'Session VAL', category: 'val' });

  // TPO
  if (s?.tpo_poc != null && s.tpo_poc !== s.poc) levels.push({ price: s.tpo_poc, label: 'TPO POC', category: 'poc' });

  // Overnight
  if (s?.overnight_high != null) levels.push({ price: s.overnight_high, label: 'ON High', category: 'overnight' });
  if (s?.overnight_low != null) levels.push({ price: s.overnight_low, label: 'ON Low', category: 'overnight' });

  // Multi-TF profiles
  if (profiles?.weekly?.poc != null) levels.push({ price: profiles.weekly.poc, label: 'Wkly POC', category: 'poc' });
  if (profiles?.leg?.poc != null) levels.push({ price: profiles.leg.poc, label: 'Leg POC', category: 'poc' });
  if (profiles?.macro?.poc != null) levels.push({ price: profiles.macro.poc, label: 'Macro POC', category: 'poc' });
  if (profiles?.developing_poc != null) {
    const dir = profiles.developing_poc_direction === 'up' ? ' ↑' : profiles.developing_poc_direction === 'down' ? ' ↓' : '';
    levels.push({ price: profiles.developing_poc, label: `Dev POC${dir}`, category: 'poc' });
  }
  // Naked POCs
  if (profiles?.naked_pocs) {
    for (const np of profiles.naked_pocs) {
      levels.push({ price: np.price, label: `Naked POC`, category: 'naked' });
    }
  }

  // Swing structure
  if (structure?.swing_high != null) levels.push({ price: structure.swing_high, label: 'Swing High', category: 'swing' });
  if (structure?.swing_low != null) levels.push({ price: structure.swing_low, label: 'Swing Low', category: 'swing' });
  if (structure?.last_hh != null) levels.push({ price: structure.last_hh, label: 'HH', category: 'swing' });
  if (structure?.last_hl != null) levels.push({ price: structure.last_hl, label: 'HL', category: 'swing' });
  if (structure?.last_lh != null) levels.push({ price: structure.last_lh, label: 'LH', category: 'swing' });
  if (structure?.last_ll != null) levels.push({ price: structure.last_ll, label: 'LL', category: 'swing' });

  // Structural levels from backend (PDH, PDL, OB, FVG, Tokyo, London, etc.)
  for (const lvl of session.levels ?? []) {
    const cat = classifyLevel(lvl.type);
    const isZone = lvl.price_low !== lvl.price_high;
    levels.push({
      price: isZone ? (lvl.price_low + lvl.price_high) / 2 : lvl.price_low,
      label: lvl.type.replace(/_/g, ' '),
      category: cat,
      zone: isZone,
      priceHigh: isZone ? lvl.price_high : undefined,
    });
  }

  // Deduplicate levels at same price with same label
  const seen = new Set<string>();
  return levels.filter(l => {
    const key = `${l.price.toFixed(2)}|${l.label}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => b.price - a.price);
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function SignalRow({ sig, expanded, onToggle, onTakeTrade }: {
  sig: TradingSignal;
  expanded: boolean;
  onToggle: () => void;
  onTakeTrade: (sig: TradingSignal, price: string) => void;
}) {
  const [taking, setTaking] = useState(false);
  const [entryPrice, setEntryPrice] = useState(sig.suggested_entry?.toFixed(2) || '');

  const rrCalc = sig.suggested_entry && sig.suggested_stop && sig.suggested_target
    ? Math.abs(sig.suggested_target - sig.suggested_entry) / Math.abs(sig.suggested_entry - sig.suggested_stop)
    : null;
  const rr = sig.rr_tp1 ?? rrCalc;
  const conditions: ScanCondition[] = typeof sig.conditions === 'string'
    ? JSON.parse(sig.conditions) : sig.conditions;

  return (
    <div className="border-b border-zinc-800/50 last:border-0">
      <button onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-zinc-800/30 transition-colors">
        {/* Score */}
        <div className="w-7 flex-shrink-0">
          <div className={`text-xs font-mono font-bold ${
            sig.score >= 80 ? 'text-green-400' : sig.score >= 70 ? 'text-tabTradingScanner' : 'text-yellow-400'
          }`}>{sig.score.toFixed(0)}</div>
        </div>

        {/* Setup name + direction */}
        <div className="flex-1 min-w-0 flex items-center gap-1.5">
          <span className="text-[11px] text-text font-medium truncate">{sig.setup_name}</span>
          <span className={`text-[9px] px-1 py-0.5 rounded ${
            sig.direction === 'long' ? 'bg-green-500/15 text-green-400 border border-green-500/30' :
            'bg-red-500/15 text-red-400 border border-red-500/30'
          }`}>{sig.direction.toUpperCase()}</span>
          {sig.setup_category && (
            <span className={`px-1 py-0.5 rounded text-[9px] text-white ${SETUP_COLORS[sig.setup_category] || 'bg-zinc-600'}`}>
              {sig.setup_category}
            </span>
          )}
        </div>

        {/* E/S/T */}
        <div className="flex gap-2 text-[10px] text-muted flex-shrink-0 font-mono">
          {sig.suggested_entry && <span>E <span className="text-text">{sig.suggested_entry.toFixed(0)}</span></span>}
          {sig.suggested_stop && <span>S <span className="text-red-400">{sig.suggested_stop.toFixed(0)}</span></span>}
          {sig.suggested_target && <span>T <span className="text-green-400">{sig.suggested_target.toFixed(0)}</span></span>}
          {rr != null && (
            <span className={rr >= 2 ? 'text-green-400' : rr >= 1.5 ? 'text-yellow-400' : 'text-zinc-400'}>
              {rr.toFixed(1)}R
            </span>
          )}
        </div>

        <span className={`text-zinc-500 text-xs transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 space-y-2 bg-zinc-900/30">
          {/* Conditions */}
          <div className="grid grid-cols-2 gap-1">
            {Array.isArray(conditions) && conditions.map((c, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[10px]">
                <div className="w-6 text-right font-mono text-zinc-500">{Math.round(c.score * 100)}%</div>
                <div className="w-12 bg-zinc-800 rounded-full h-1">
                  <div className="h-1 rounded-full" style={{
                    width: `${c.score * 100}%`,
                    backgroundColor: c.score >= 0.7 ? '#4CAF50' : c.score >= 0.4 ? '#FF9800' : '#EF5350'
                  }} />
                </div>
                <span className={c.is_auto ? 'text-zinc-300' : 'text-zinc-500 italic'}>{c.name}</span>
              </div>
            ))}
          </div>

          {/* Take trade */}
          {taking ? (
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-zinc-500">Fill:</span>
              <input type="number" step="0.25" value={entryPrice}
                onChange={e => setEntryPrice(e.target.value)}
                placeholder={sig.suggested_entry?.toFixed(2) || ''}
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs font-mono text-text w-24"
                autoFocus />
              <button onClick={() => { onTakeTrade(sig, entryPrice); setTaking(false); }}
                disabled={!entryPrice}
                className="text-[10px] px-2.5 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40">
                Confirm
              </button>
              <button onClick={() => setTaking(false)}
                className="text-[10px] px-2 py-1 text-zinc-500 hover:text-zinc-300">Cancel</button>
            </div>
          ) : (
            <button onClick={() => setTaking(true)}
              className="text-[10px] px-3 py-1 bg-tabTradingScanner text-black rounded hover:bg-tabTradingScanner/80 font-medium">
              Take Trade
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main Component ──────────────────────────────────────────────────────────

export function TradingIntradayPage() {
  const [session, setSession] = useState<ExpandedSession | null>(null);
  const [indicators, setIndicators] = useState<IndicatorsResponse | null>(null);
  const [signals, setSignals] = useState<TradingSignal[]>([]);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [candleError, setCandleError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const { lastTick, lastCandle, connected } = useMarketStream();
  const prevConnected = useRef(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [sessionRes, signalsRes, indicRes, candleRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getMarketSignals().catch(() => ({ signals: [] })),
        api.getIndicators().catch(() => null),
        api.getCandles().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
      if (indicRes) setIndicators(indicRes);
      if (candleRes) {
        setCandles(candleRes.candles);
        setCandleError(false);
      } else {
        setCandleError(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh session/signals/indicators every 30s (NOT candles — those update via SSE)
  useEffect(() => {
    const refresh = async () => {
      const [sessionRes, signalsRes, indicRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getMarketSignals().catch(() => ({ signals: [] })),
        api.getIndicators().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
      if (indicRes) setIndicators(indicRes);
    };
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, []);

  // Re-fetch candles on SSE reconnect to resync after missed ticks
  useEffect(() => {
    if (connected && !prevConnected.current) {
      api.getCandles().then(res => {
        if (res) { setCandles(res.candles); setCandleError(false); }
      }).catch(() => {});
    }
    prevConnected.current = connected;
  }, [connected]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await api.triggerMarketCompute();
      const [sessionRes, signalsRes, indicRes, candleRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.triggerMarketScan(70).catch(() => ({ signals: [] })),
        api.getIndicators().catch(() => null),
        api.getCandles().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
      if (indicRes) setIndicators(indicRes);
      if (candleRes) { setCandles(candleRes.candles); setCandleError(false); }
      setLastRefresh(new Date());
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleAnchorUpdate = async (field: 'vp_leg_start' | 'vp_ongoing_macro_start', value: string) => {
    await api.updateVPAnchors({ [field]: value });
    const res = await api.getExpandedSession().catch(() => null);
    if (res) setSession(res);
  };

  const handleTakeTrade = async (signal: TradingSignal, priceStr: string) => {
    const price = parseFloat(priceStr);
    if (!price || !signal) return;
    try {
      await api.createTrade({
        instrument: session?.session?.symbol || 'NQ',
        direction: signal.direction,
        setup_type: signal.setup_type,
        entry_price: price,
        stop_price: signal.suggested_stop || 0,
        targets: signal.suggested_target ? [{ price: signal.suggested_target }] : [],
        contracts: 1,
        notes: `Scanner signal: ${signal.setup_name} (score: ${signal.score})`,
      });
    } catch (err) {
      console.error('Failed to create trade:', err);
    }
  };

  // Build the unified level ladder
  const ladderLevels = useMemo(() => buildLadder(session), [session]);
  const currentPrice = lastTick?.price ?? session?.price_position?.last_price ?? null;
  const pricePos = session?.price_position;

  // Derive signal levels for chart overlay
  const signalLevels = useMemo(() => {
    if (expandedSignal == null) return null;
    const sig = signals.find(s => s.id === expandedSignal);
    if (!sig) return null;
    return {
      entry: sig.suggested_entry ?? undefined,
      stop: sig.suggested_stop ?? undefined,
      target: sig.suggested_target ?? undefined,
    };
  }, [expandedSignal, signals]);

  if (loading) return <div className="text-zinc-500 text-sm p-4">Loading scanner...</div>;

  return (
    <div className="flex flex-col h-full">

      {/* ─── Header ─── */}
      <div className="flex items-center gap-3 flex-wrap border-b border-zinc-800 pb-2 mb-2 px-1">
        <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={16} />
        <span className="text-sm font-semibold text-text">Intraday</span>

        {currentPrice != null && (
          <span className="font-mono text-sm text-tabTradingScanner font-bold">
            NQ {currentPrice.toFixed(2)}
          </span>
        )}

        <div className="flex items-center gap-1 text-[10px]">
          <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
          <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
        </div>

        {pricePos?.vwap_deviation_sd != null && (
          <span className={`text-[10px] font-mono ${
            Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
            Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-yellow-400' : 'text-zinc-400'
          }`}>
            {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
          </span>
        )}

        <div className="flex-1" />

        {/* VP Anchor date pickers */}
        {session?.profiles && (
          <div className="flex gap-2 text-[10px]">
            <div className="flex items-center gap-1">
              <span className="text-zinc-500">Leg:</span>
              <input type="date"
                className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
                defaultValue={session.profiles.leg?.anchor ?? ''}
                onBlur={e => handleAnchorUpdate('vp_leg_start', e.target.value)} />
            </div>
            <div className="flex items-center gap-1">
              <span className="text-zinc-500">Macro:</span>
              <input type="date"
                className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
                defaultValue={session.profiles.macro?.anchor ?? ''}
                onBlur={e => handleAnchorUpdate('vp_ongoing_macro_start', e.target.value)} />
            </div>
          </div>
        )}

        <span className="text-[9px] text-zinc-600 font-mono">
          5m{lastRefresh && ` · ${lastRefresh.toLocaleTimeString()}`}
        </span>
        <button onClick={handleRefresh} disabled={isRefreshing}
          className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40 transition-colors">
          {isRefreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* ─── Chart (dominant, ~75% height) ─── */}
      <div className="flex-1 min-h-0 border border-zinc-800 rounded bg-zinc-900/30">
        {candles.length > 0 ? (
          <CandleChart
            candles={candles}
            levels={ladderLevels}
            signalLevels={signalLevels}
            lastCandle={lastCandle}
          />
        ) : candleError ? (
          <div className="flex flex-col items-center justify-center h-full gap-2">
            <span className="text-zinc-500 text-sm">Failed to load candles</span>
            <button onClick={handleRefresh} className="text-[10px] px-3 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800">
              Retry
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
            No candle data. Click Refresh to load.
          </div>
        )}
      </div>

      {/* ─── Bottom: Gauge Strip + Signals ─── */}
      <div className="flex gap-2 mt-2 min-h-[120px] max-h-[200px]">

        {/* Gauges */}
        <div className="flex-shrink-0">
          <GaugeStrip
            session={session?.session}
            orderflow={indicators?.orderflow}
            macro={session?.macro}
          />
        </div>

        {/* Signals */}
        <div className="flex-1 min-w-0 border border-zinc-800 rounded bg-zinc-900/30 overflow-y-auto">
          <div className="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-3 py-1 flex items-center justify-between">
            <span className="text-[10px] font-semibold text-text">
              Signals <span className="text-tabTradingScanner">{signals.length}</span>
            </span>
          </div>
          {signals.length === 0 ? (
            <div className="px-3 py-2 text-center text-zinc-600 text-[10px]">
              No signals above threshold (70)
            </div>
          ) : (
            signals.map(sig => (
              <SignalRow
                key={sig.id}
                sig={sig}
                expanded={expandedSignal === sig.id}
                onToggle={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                onTakeTrade={handleTakeTrade}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
