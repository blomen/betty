import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import type { TradingSignal, ConfirmationState, ScanCondition } from '@/types/market';

type ConfirmationKey = 'macro' | 'span' | 'fair_value' | 'orderflow';

interface CotReport {
  report_date: string;
  net_commercial: number;
  net_non_commercial: number;
  net_non_reportable: number;
  open_interest: number;
}

const SETUP_COLORS: Record<string, string> = {
  spring: 'bg-emerald-600', sfp: 'bg-blue-600', poor_extreme: 'bg-purple-600',
  ib_break: 'bg-orange-600', rule_80: 'bg-cyan-600', fakeout: 'bg-red-600',
  break_from_balance: 'bg-amber-600', double_distribution: 'bg-pink-600',
  news_directional: 'bg-indigo-600',
};

export function TradingIntradayPage() {
  const queryClient = useQueryClient();

  const [overrides, setOverrides] = useState<Record<ConfirmationKey, boolean | null>>({
    macro: null, span: null, fair_value: null, orderflow: null,
  });
  const [showLevels, setShowLevels] = useState(false);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [threshold, setThreshold] = useState(70);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [takingTrade, setTakingTrade] = useState<number | null>(null);
  const [entryPrice, setEntryPrice] = useState('');

  const { lastTick, connected } = useMarketStream();

  // --- Data queries ---
  const { data: sessionData, isLoading: sessionLoading } = useQuery({
    queryKey: ['market-session'],
    queryFn: () => api.getMarketSession().catch(() => null),
    staleTime: Infinity,
  });
  const session = sessionData && !sessionData.status ? sessionData : null;

  const { data: signalsData } = useQuery({
    queryKey: ['market-signals'],
    queryFn: () => api.getMarketSignals().catch(() => ({ signals: [] })),
    staleTime: 60_000,
  });
  const signals: TradingSignal[] = signalsData?.signals ?? [];

  const { data: confirmRes } = useQuery({
    queryKey: ['confirmations'],
    queryFn: () => api.getConfirmations().catch(() => null),
    staleTime: 30_000,
  });
  const confirmations: ConfirmationState | null = confirmRes ?? null;

  const { data: contextData } = useQuery({
    queryKey: ['market-context'],
    queryFn: () => api.getMarketContext().catch(() => null),
    staleTime: Infinity,
  });
  const context = contextData ?? null;

  const { data: macroData } = useQuery({
    queryKey: ['macro-snapshot'],
    queryFn: () => api.getMacroSnapshot().catch(() => null),
    staleTime: 300_000,
  });
  const macro = macroData ?? null;

  const { data: cotData } = useQuery({
    queryKey: ['cot-data'],
    queryFn: () => api.getCotData(2).catch(() => null),
    staleTime: 300_000,
  });
  const cot: CotReport[] | null = cotData ?? null;

  const { data: levelsData, refetch: refetchLevels } = useQuery({
    queryKey: ['market-levels'],
    queryFn: () => api.getMarketLevels(),
    enabled: false,
  });
  const levels = levelsData ?? [];

  // --- Mutations ---
  const computeMutation = useMutation({
    mutationFn: () => api.triggerMarketCompute(),
    onSuccess: (res) => {
      if (res && !res.status) {
        queryClient.setQueryData(['market-session'], res);
      }
      queryClient.invalidateQueries({ queryKey: ['market-signals'] });
      queryClient.invalidateQueries({ queryKey: ['confirmations'] });
      queryClient.invalidateQueries({ queryKey: ['market-levels'] });
    },
  });

  const scanMutation = useMutation({
    mutationFn: (thr: number) => api.triggerMarketScan(thr),
    onSuccess: (res) => {
      queryClient.setQueryData(['market-signals'], res);
      setLastScan(new Date().toLocaleTimeString());
      queryClient.invalidateQueries({ queryKey: ['confirmations'] });
    },
  });

  const tradeMutation = useMutation({
    mutationFn: (params: any) => api.createTrade(params),
    onSuccess: () => {
      setTakingTrade(null);
      setEntryPrice('');
    },
  });

  // --- Handlers ---
  const updateGate = async (field: string, value: string) => {
    await api.updateMarketContext({ [field]: value });
    queryClient.invalidateQueries({ queryKey: ['market-context'] });
  };

  const loadLevels = () => { refetchLevels(); setShowLevels(true); };

  const toggleOverride = (key: ConfirmationKey) => {
    setOverrides(prev => {
      const autoChecked = confirmations?.[key]?.checked ?? false;
      const current = prev[key];
      if (current === null) {
        return { ...prev, [key]: !autoChecked };
      }
      return { ...prev, [key]: null };
    });
  };

  const isChecked = (key: ConfirmationKey): boolean => {
    if (overrides[key] !== null) return overrides[key]!;
    return confirmations?.[key]?.checked ?? false;
  };

  // Layer A: at least 1 context gate set (manual input)
  const layerACount = [context?.macro_bias, context?.structure, context?.day_type].filter(Boolean).length;
  const layerAReady = layerACount >= 1;

  // Layer B: at least 2/4 auto-confirmations checked (or overridden)
  const CONFIRM_KEYS: ConfirmationKey[] = ['macro', 'span', 'fair_value', 'orderflow'];
  const layerBCount = CONFIRM_KEYS.filter(isChecked).length;
  const layerBReady = layerBCount >= 2;

  // Signals show when both layers pass
  const gatesPassed = layerAReady && layerBReady;

  const handleTakeTrade = async (signal: TradingSignal) => {
    const price = parseFloat(entryPrice);
    if (!price || !signal) return;
    tradeMutation.mutate({
      instrument: session?.symbol || 'NQ',
      direction: signal.direction,
      setup_type: signal.setup_type,
      entry_price: price,
      stop_price: signal.suggested_stop || 0,
      targets: signal.suggested_target ? [{ price: signal.suggested_target }] : [],
      contracts: 1,
      notes: `Scanner signal: ${signal.setup_name} (score: ${signal.score})`,
    });
  };

  if (sessionLoading) return <div className="text-muted text-sm">Loading scanner...</div>;

  const hasSession = session && session.poc;

  return (
    <div className="space-y-3 max-w-5xl">
      {/* A. FilterBar */}
      <div className="flex items-center gap-3 flex-wrap border-b border-border pb-2">
        <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={18} />
        <span className="text-sm font-semibold text-text">Intraday</span>
        <div className="flex-1" />
        <button
          onClick={() => computeMutation.mutate()}
          disabled={computeMutation.isPending}
          className="text-xs px-3 py-1 border border-tabTradingScanner/50 text-tabTradingScanner rounded hover:bg-tabTradingScanner/10 disabled:opacity-40"
        >
          {computeMutation.isPending ? 'Computing...' : 'Compute'}
        </button>
        <button
          onClick={() => scanMutation.mutate(threshold)}
          disabled={scanMutation.isPending || !hasSession}
          className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
        >
          {scanMutation.isPending ? 'Scanning...' : 'Scan'}
        </button>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <label>Thr:</label>
          <input type="range" min={30} max={95} step={5} value={threshold}
            onChange={e => setThreshold(parseInt(e.target.value))}
            className="w-16 accent-[#06B6D4]" />
          <span className="font-mono text-text w-5">{threshold}</span>
        </div>
        {lastScan && <span className="text-[10px] text-muted">Last: {lastScan}</span>}
      </div>

      {/* Layer A: Manual Context Gates — YOU set these */}
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Layer A · You Set</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${layerAReady ? 'bg-success/15 text-success' : 'bg-zinc-700 text-zinc-400'}`}>
            {layerACount}/3 — need ≥1
          </span>
        </div>
        <div className="flex gap-2">
          {/* Gate 1: Macro (weekly) */}
          <div className={`p-2 rounded flex-1 border transition-colors ${context?.macro_bias ? 'bg-zinc-800 border-success/30' : 'bg-zinc-800/50 border-zinc-700'}`}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-zinc-400">Macro Bias</span>
              <span className="text-[9px] text-zinc-600">weekly</span>
            </div>
            <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
              value={context?.macro_bias || ''}
              onChange={e => updateGate('macro_bias', e.target.value)}>
              <option value="">—</option>
              <option value="bull">Bull</option>
              <option value="bear">Bear</option>
              <option value="neutral">Neutral</option>
            </select>
            <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white mt-1"
              value={context?.risk_mode || ''}
              onChange={e => updateGate('risk_mode', e.target.value)}>
              <option value="">Risk Mode —</option>
              <option value="risk_on">Risk On</option>
              <option value="risk_off">Risk Off</option>
              <option value="mixed">Mixed</option>
            </select>
            {/* Supporting data for macro decision */}
            {macro && (
              <div className="mt-1.5 space-y-0.5 text-[10px] text-zinc-500 font-mono">
                {macro.vix != null && (
                  <div className="flex justify-between">
                    <span>VIX</span>
                    <span className={macro.vix > 25 ? 'text-red-400' : macro.vix < 15 ? 'text-green-400' : 'text-zinc-400'}>
                      {macro.vix.toFixed(1)} {macro.vix_change_pct != null && <span className="text-zinc-600">({macro.vix_change_pct > 0 ? '+' : ''}{macro.vix_change_pct.toFixed(1)}%)</span>}
                    </span>
                  </div>
                )}
                {macro.dxy != null && (
                  <div className="flex justify-between">
                    <span>DXY</span>
                    <span className="text-zinc-400">{macro.dxy.toFixed(1)}</span>
                  </div>
                )}
                {macro.us10y != null && (
                  <div className="flex justify-between">
                    <span>10Y</span>
                    <span className="text-zinc-400">{macro.us10y.toFixed(2)}%</span>
                  </div>
                )}
                {macro.yield_curve_spread != null && (
                  <div className="flex justify-between">
                    <span>2s10s</span>
                    <span className={macro.yield_curve_spread < 0 ? 'text-red-400' : 'text-zinc-400'}>
                      {macro.yield_curve_spread > 0 ? '+' : ''}{macro.yield_curve_spread.toFixed(0)}bp
                    </span>
                  </div>
                )}
                {macro.regime && (
                  <div className="flex justify-between">
                    <span>Regime</span>
                    <span className={macro.regime === 'risk_on' ? 'text-green-400' : macro.regime === 'risk_off' ? 'text-red-400' : 'text-yellow-400'}>
                      {macro.regime.replace('_', ' ')}
                    </span>
                  </div>
                )}
                {cot && cot.length > 0 && (
                  <div className="flex justify-between">
                    <span>COT</span>
                    <span className={cot[0].net_non_commercial > 0 ? 'text-green-400' : 'text-red-400'}>
                      {cot[0].net_non_commercial > 0 ? '+' : ''}{cot[0].net_non_commercial.toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
          {/* Gate 2: Structure (daily) */}
          <div className={`p-2 rounded flex-1 border transition-colors ${context?.structure ? 'bg-zinc-800 border-success/30' : 'bg-zinc-800/50 border-zinc-700'}`}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-zinc-400">Structure</span>
              <span className="text-[9px] text-zinc-600">daily</span>
            </div>
            <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
              value={context?.structure || ''}
              onChange={e => updateGate('structure', e.target.value)}>
              <option value="">—</option>
              <option value="uptrend">Uptrend (HH/HL)</option>
              <option value="downtrend">Downtrend (LH/LL)</option>
              <option value="ranging">Ranging</option>
            </select>
            {/* Supporting data for structure decision */}
            <div className="mt-1.5 space-y-0.5 text-[10px] text-zinc-500 font-mono">
              {context?.structure_hl != null && (
                <div className="flex justify-between">
                  <span>Last HL</span>
                  <span className="text-green-400">{context.structure_hl.toFixed(0)}</span>
                </div>
              )}
              {context?.structure_lh != null && (
                <div className="flex justify-between">
                  <span>Last LH</span>
                  <span className="text-red-400">{context.structure_lh.toFixed(0)}</span>
                </div>
              )}
              {session?.value_migration && (
                <div className="flex justify-between">
                  <span>VA Migr</span>
                  <span className={session.value_migration === 'up' ? 'text-green-400' : session.value_migration === 'down' ? 'text-red-400' : 'text-zinc-400'}>
                    {session.value_migration}
                  </span>
                </div>
              )}
              {session?.market_type && (
                <div className="flex justify-between">
                  <span>Type</span>
                  <span className="text-zinc-400">{session.market_type.replace(/_/g, ' ')}</span>
                </div>
              )}
            </div>
          </div>
          {/* Gate 3: Day Type (after first 30-60 min) */}
          <div className={`p-2 rounded flex-1 border transition-colors ${context?.day_type ? 'bg-zinc-800 border-success/30' : 'bg-zinc-800/50 border-zinc-700'}`}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-zinc-400">Day Type</span>
              <span className="text-[9px] text-zinc-600">after IB</span>
            </div>
            <select className="bg-zinc-900 text-xs p-1 rounded w-full text-white"
              value={context?.day_type || ''}
              onChange={e => updateGate('day_type', e.target.value)}>
              <option value="">—</option>
              <option value="trend">Trend</option>
              <option value="normal">Normal</option>
              <option value="normal_variation">Normal Variation</option>
              <option value="neutral">Neutral</option>
              <option value="composite">Composite</option>
            </select>
            {/* M7 ML prediction (auto-hint) */}
            {confirmations?.ml_day_type && (
              <div className="mt-1 text-[9px] font-mono flex items-center gap-1">
                <span className="text-zinc-600">M7:</span>
                <span className="text-blue-400">{confirmations.ml_day_type}</span>
                {confirmations.ml_day_type_confidence != null && (
                  <span className="text-zinc-600">({confirmations.ml_day_type_confidence}%)</span>
                )}
              </div>
            )}
            {/* Supporting data for day type decision */}
            <div className="mt-1.5 space-y-0.5 text-[10px] text-zinc-500 font-mono">
              {session?.rotation_factor != null && (
                <div className="flex justify-between">
                  <span>RF</span>
                  <span className={session.rotation_factor > 70 ? 'text-green-400' : session.rotation_factor < 30 ? 'text-yellow-400' : 'text-zinc-400'}>
                    {session.rotation_factor}
                  </span>
                </div>
              )}
              {session?.aspr != null && (
                <div className="flex justify-between">
                  <span>ASPR</span>
                  <span className="text-zinc-400">
                    {session.aspr.toFixed(1)}
                    {session.aspr_percentile != null && <span className="text-zinc-600 ml-0.5">({(session.aspr_percentile * 100).toFixed(0)}%)</span>}
                  </span>
                </div>
              )}
              {session?.ib_range != null && (
                <div className="flex justify-between">
                  <span>IB Rng</span>
                  <span className="text-zinc-400">{session.ib_range.toFixed(1)}</span>
                </div>
              )}
              {session?.opening_type && (
                <div className="flex justify-between">
                  <span>Open</span>
                  <span className="text-zinc-400">{session.opening_type.replace(/_/g, ' ')}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Layer B: Auto Confirmations — system evaluates these */}
      <div className="mb-1">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500">Layer B · Auto</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${layerBReady ? 'bg-success/15 text-success' : 'bg-zinc-700 text-zinc-400'}`}>
            {layerBCount}/4 — need ≥2
          </span>
        </div>
      </div>
      <div className="grid grid-cols-4 gap-2">
        <ConfirmCard
          label="Macro"
          checked={isChecked('macro')}
          autoChecked={confirmations?.macro?.checked ?? false}
          overridden={overrides.macro !== null}
          onClick={() => toggleOverride('macro')}
          detail={confirmations?.macro?.regime === 'risk_on' ? 'RISK ON' :
                  confirmations?.macro?.regime === 'risk_off' ? 'RISK OFF' : 'MIXED'}
          subDetail={confirmations?.macro?.vix != null ? `VIX ${confirmations.macro.vix.toFixed(1)}` : undefined}
          detailColor={confirmations?.macro?.regime === 'risk_on' ? 'text-success' :
                       confirmations?.macro?.regime === 'risk_off' ? 'text-error' : 'text-yellow'}
        />
        <ConfirmCard
          label="Span"
          checked={isChecked('span')}
          autoChecked={confirmations?.span?.checked ?? false}
          overridden={overrides.span !== null}
          onClick={() => toggleOverride('span')}
          detail={confirmations?.span?.structure === 'bullish' ? 'Bullish structure' :
                  confirmations?.span?.structure === 'bearish' ? 'Bearish structure' : 'No clear structure'}
          detailColor={confirmations?.span?.checked ? 'text-success' : 'text-muted'}
        />
        <ConfirmCard
          label="Fair Value"
          checked={isChecked('fair_value')}
          autoChecked={confirmations?.fair_value?.checked ?? false}
          overridden={overrides.fair_value !== null}
          onClick={() => toggleOverride('fair_value')}
          detail={confirmations?.fair_value?.deviation_sd != null
            ? `${confirmations.fair_value.deviation_sd > 0 ? '+' : ''}${confirmations.fair_value.deviation_sd} SD`
            : confirmations?.fair_value?.price_vs_va || 'No data'}
          detailColor={confirmations?.fair_value?.checked ? 'text-tabTradingScanner' : 'text-muted'}
        />
        <ConfirmCard
          label="Orderflow"
          checked={isChecked('orderflow')}
          autoChecked={confirmations?.orderflow?.checked ?? false}
          overridden={overrides.orderflow !== null}
          onClick={() => toggleOverride('orderflow')}
          detail={confirmations?.orderflow?.delta != null
            ? `Delta ${confirmations.orderflow.delta > 0 ? '+' : ''}${confirmations.orderflow.delta.toLocaleString()}`
            : 'No data'}
          subDetail={confirmations?.orderflow?.cvd_trend && confirmations.orderflow.cvd_trend !== 'flat'
            ? `CVD ${confirmations.orderflow.cvd_trend}`
            : undefined}
          detailColor={confirmations?.orderflow?.checked ? 'text-success' : 'text-muted'}
        />
      </div>

      {/* Orderflow Signal Strip — Fabio / OrderflowHorse signals */}
      {confirmations?.orderflow?.delta != null && (
        <div className="flex gap-1.5 flex-wrap text-[10px] font-mono px-0.5">
          <OFSignal label="Delta" active={confirmations.orderflow.delta_aligned} />
          <OFSignal label="Diverge" active={confirmations.orderflow.divergence} />
          <OFSignal label="Unwind" active={confirmations.orderflow.delta_unwind} />
          <OFSignal label="VSA" active={confirmations.orderflow.vsa_absorption} />
          <OFSignal label="TickVol" active={confirmations.orderflow.tick_vol_accelerating} />
          <OFSignal label="Trapped" active={confirmations.orderflow.trapped_traders} />
          <OFSignal label="StopRun" active={confirmations.orderflow.stop_run_detected} />
          <span className={`px-1.5 py-0.5 rounded ${confirmations.orderflow.cvd_trend === 'rising' ? 'bg-green-900/40 text-green-400' : confirmations.orderflow.cvd_trend === 'falling' ? 'bg-red-900/40 text-red-400' : 'bg-zinc-800 text-zinc-500'}`}>
            CVD {confirmations.orderflow.cvd_trend}
          </span>
          {confirmations.orderflow.big_trades_count != null && confirmations.orderflow.big_trades_count > 0 && (
            <span className={`px-1.5 py-0.5 rounded ${confirmations.orderflow.big_trades_count >= 2 ? 'bg-yellow-900/40 text-yellow-400' : 'bg-zinc-800 text-zinc-500'}`}>
              BigTx {confirmations.orderflow.big_trades_count} ({(confirmations.orderflow.big_trades_net_delta ?? 0) > 0 ? '+' : ''}{confirmations.orderflow.big_trades_net_delta})
            </span>
          )}
          {confirmations.orderflow.passive_active_ratio != null && confirmations.orderflow.passive_active_ratio > 0 && (
            <span className={`px-1.5 py-0.5 rounded ${confirmations.orderflow.passive_active_ratio > 2 ? 'bg-purple-900/40 text-purple-400' : 'bg-zinc-800 text-zinc-500'}`}>
              P/A {confirmations.orderflow.passive_active_ratio.toFixed(1)}
            </span>
          )}
          {confirmations.orderflow.stacked_imbalance_count != null && confirmations.orderflow.stacked_imbalance_count >= 2 && (
            <span className={`px-1.5 py-0.5 rounded ${confirmations.orderflow.stacked_imbalance_count >= 3 ? 'bg-cyan-900/40 text-cyan-400' : 'bg-zinc-800 text-zinc-400'}`}>
              Imb×{confirmations.orderflow.stacked_imbalance_count} {confirmations.orderflow.stacked_imbalance_direction === 'buy' ? '▲' : '▼'}
            </span>
          )}
          {confirmations.orderflow.imbalance_ratio_max != null && confirmations.orderflow.imbalance_ratio_max !== 0.5 && (
            <span className={`px-1.5 py-0.5 rounded ${confirmations.orderflow.imbalance_ratio_max >= 0.65 ? 'bg-green-900/40 text-green-400' : confirmations.orderflow.imbalance_ratio_max <= 0.35 ? 'bg-red-900/40 text-red-400' : 'bg-zinc-800 text-zinc-500'}`}>
              ImbR {(confirmations.orderflow.imbalance_ratio_max * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      {/* Session Metrics Row (23b) */}
      {session && (
        <div className="flex gap-3 mb-3 text-xs flex-wrap">
          {session.rotation_factor != null && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">RF:</span>{' '}
              <span className={session.rotation_factor > 0 ? 'text-green-400' : session.rotation_factor < 0 ? 'text-red-400' : 'text-zinc-300'}>
                {session.rotation_factor}
              </span>
            </div>
          )}
          {session.aspr != null && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">ASPR:</span>{' '}
              <span className="text-zinc-200">{session.aspr.toFixed(2)}</span>
              {session.aspr_percentile != null && (
                <span className="text-zinc-500 ml-1">({(session.aspr_percentile * 100).toFixed(0)}%ile)</span>
              )}
            </div>
          )}
          {(session.ib_high != null || session.ib_low != null) && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">IB:</span>{' '}
              <span className="text-zinc-200">{session.ib_high?.toFixed(2) ?? '—'} / {session.ib_low?.toFixed(2) ?? '—'}</span>
            </div>
          )}
          {session.vwap != null && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">VWAP:</span>{' '}
              <span className="text-blue-400">{session.vwap.toFixed(2)}</span>
            </div>
          )}
          {session.poc != null && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">POC:</span>{' '}
              <span className="text-yellow-400">{session.poc.toFixed(2)}</span>
            </div>
          )}
          {session.value_migration != null && (
            <div className="bg-zinc-800 px-3 py-1.5 rounded">
              <span className="text-zinc-400">Migration:</span>{' '}
              <span className={session.value_migration === 'up' ? 'text-green-400' : session.value_migration === 'down' ? 'text-red-400' : 'text-zinc-300'}>
                {session.value_migration}
              </span>
            </div>
          )}
        </div>
      )}

      {/* C. Market State Row */}
      {hasSession && (
        <div className="flex items-center gap-3 text-xs font-mono flex-wrap px-1">
          {session.vah && session.val && <Badge label="VA" value={`${session.val.toFixed(0)}-${session.vah.toFixed(0)}`} />}
          {session.overnight_high && session.overnight_low && <Badge label="ON" value={`${session.overnight_low.toFixed(0)}-${session.overnight_high.toFixed(0)}`} color="text-muted" />}
          {session.total_delta != null && (
            <Badge label="Delta"
              value={`${session.total_delta > 0 ? '+' : ''}${session.total_delta.toLocaleString()}`}
              color={session.total_delta > 0 ? 'text-success' : 'text-error'} />
          )}
          {session.last_price && <Badge label="Price" value={session.last_price.toFixed(2)} color="text-text" />}
        </div>
      )}

      {/* D. Gated Opportunity Table */}
      <div className="border border-border bg-panel rounded">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <h3 className="text-sm font-semibold text-text">
            Opportunities ({gatesPassed ? signals.length : 0})
          </h3>
          {!gatesPassed && (
            <span className="text-xs text-muted">
              {!layerAReady && !layerBReady
                ? 'Set ≥1 context gate + need ≥2 auto confirmations'
                : !layerAReady
                ? 'Set at least 1 context gate (Layer A)'
                : `Need ${2 - layerBCount} more auto confirmation${2 - layerBCount !== 1 ? 's' : ''} (Layer B)`}
            </span>
          )}
        </div>

        {!gatesPassed ? (
          <div className="p-6 text-center text-muted text-sm">
            {!layerAReady
              ? 'Set at least 1 context gate above to unlock signals'
              : `Waiting for auto confirmations (${layerBCount}/4, need 2)...`}
          </div>
        ) : signals.length === 0 ? (
          <div className="p-4 text-center text-muted text-sm">
            {hasSession ? 'No signals above threshold.' : 'Compute session first, then scan.'}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {signals.map(sig => {
              const rrCalc = sig.suggested_entry && sig.suggested_stop && sig.suggested_target
                ? Math.abs(sig.suggested_target - sig.suggested_entry) / Math.abs(sig.suggested_entry - sig.suggested_stop)
                : null;
              const rr = sig.rr_tp1 ?? rrCalc;
              const conditions = typeof sig.conditions === 'string'
                ? (JSON.parse(sig.conditions) as ScanCondition[])
                : sig.conditions;
              return (
                <div key={sig.id}>
                  <button
                    onClick={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-panel2/50 transition-colors"
                  >
                    <div className="w-10 flex-shrink-0">
                      <div className={`text-sm font-mono font-bold ${sig.score >= 80 ? 'text-success' : sig.score >= 70 ? 'text-tabTradingScanner' : 'text-warning'}`}>
                        {sig.score.toFixed(0)}
                      </div>
                      <div className="w-full bg-panel2 rounded-full h-1 mt-0.5">
                        <div className="h-1 rounded-full" style={{
                          width: `${sig.score}%`,
                          backgroundColor: sig.score >= 80 ? '#4CAF50' : sig.score >= 70 ? '#06B6D4' : '#FF9800'
                        }} />
                      </div>
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm text-text font-medium truncate">{sig.setup_name}</span>
                        <span className={`text-xs px-1.5 py-0.5 rounded border ${
                          sig.direction === 'long' ? 'border-success/50 text-success' : 'border-error/50 text-error'
                        }`}>
                          {sig.direction.toUpperCase()}
                        </span>
                        {sig.setup_category && (
                          <span className={`px-1.5 py-0.5 rounded text-xs text-white ${SETUP_COLORS[sig.setup_category] || 'bg-zinc-600'}`}>
                            {sig.setup_category}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex gap-3 text-xs text-muted flex-shrink-0 flex-wrap">
                      {sig.suggested_entry && <span>E:<span className="font-mono text-text ml-0.5">{sig.suggested_entry.toFixed(0)}</span></span>}
                      {sig.suggested_stop && <span>S:<span className="font-mono text-error ml-0.5">{sig.suggested_stop.toFixed(0)}</span></span>}
                      {sig.suggested_target && <span>T:<span className="font-mono text-success ml-0.5">{sig.suggested_target.toFixed(0)}</span></span>}
                      {sig.suggested_target_2 && <span>T2:<span className="font-mono text-success ml-0.5">{sig.suggested_target_2.toFixed(2)}</span></span>}
                      {sig.suggested_target_3 && <span>T3:<span className="font-mono text-success ml-0.5">{sig.suggested_target_3.toFixed(2)}</span></span>}
                      {rr != null && (
                        <span>R:R <span className={`font-mono ${rr >= 2 ? 'text-green-400' : rr >= 1.5 ? 'text-yellow-400' : 'text-zinc-400'}`}>{rr.toFixed(1)}</span></span>
                      )}
                      {sig.level_touched && (
                        <span className="bg-zinc-700 px-1.5 py-0.5 rounded text-xs">{sig.level_touched}</span>
                      )}
                    </div>

                    <span className={`text-muted text-xs transition-transform ${expandedSignal === sig.id ? 'rotate-90' : ''}`}>▸</span>
                  </button>

                  {expandedSignal === sig.id && (
                    <div className="px-4 pb-3 space-y-2 bg-panel2/30">
                      {Array.isArray(conditions) && conditions.map((c, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <div className="w-7 text-right font-mono text-muted">{Math.round(c.score * 100)}%</div>
                          <div className="w-16 bg-panel2 rounded-full h-1">
                            <div className="h-1 rounded-full" style={{
                              width: `${c.score * 100}%`,
                              backgroundColor: c.score >= 0.7 ? '#4CAF50' : c.score >= 0.4 ? '#FF9800' : '#EF5350'
                            }} />
                          </div>
                          <span className={c.is_auto ? 'text-text' : 'text-muted italic'}>{c.name}</span>
                          {!c.is_auto && <span className="text-[10px] text-muted/50">(manual)</span>}
                        </div>
                      ))}

                      {/* L2 Orderflow Panel (23c) */}
                      <div className="bg-zinc-900 p-3 rounded mt-2">
                        <div className="text-xs text-zinc-400 mb-2">L2 Orderflow {connected ? '🟢' : '🔴'}</div>
                        <div className="flex gap-4 text-xs">
                          <div>Delta: <span className={(lastTick?.delta_1m ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
                            {lastTick?.delta_1m ?? 0}</span></div>
                          <div>CVD: <span className="text-zinc-200">{lastTick?.cvd ?? 0}</span></div>
                          <div>Last: <span className="text-zinc-200">{lastTick?.price?.toFixed(2) ?? '—'}</span></div>
                        </div>
                        {(() => {
                          try {
                            const condRaw = typeof sig.conditions === 'string' ? JSON.parse(sig.conditions) : null;
                            const of = condRaw?.orderflow;
                            if (!of) return null;
                            return (
                              <div className="flex gap-3 mt-2 text-xs">
                                <span>{of.delta_aligned ? '✓' : '✗'} Delta</span>
                                <span>{of.vsa_absorption ? '✓' : '✗'} VSA</span>
                                <span>{of.delta_divergence ? '✓' : '✗'} Divergence</span>
                                <span>{of.tick_vol_accelerating ? '✓' : '✗'} Tick Vol</span>
                                <span>{of.trapped_traders ? '✓' : '✗'} Trapped</span>
                              </div>
                            );
                          } catch {
                            return null;
                          }
                        })()}
                      </div>

                      {takingTrade === sig.id ? (
                        <div className="flex items-center gap-2 pt-2 border-t border-border">
                          <span className="text-xs text-muted">Fill price:</span>
                          <input
                            type="number"
                            step="0.25"
                            value={entryPrice}
                            onChange={e => setEntryPrice(e.target.value)}
                            placeholder={sig.suggested_entry?.toFixed(2) || ''}
                            className="bg-panel2 border border-border rounded px-2 py-1 text-sm font-mono text-text w-28"
                            autoFocus
                          />
                          <button
                            onClick={() => handleTakeTrade(sig)}
                            disabled={!entryPrice}
                            className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
                          >
                            Confirm
                          </button>
                          <button
                            onClick={() => { setTakingTrade(null); setEntryPrice(''); }}
                            className="text-xs px-2 py-1 text-muted hover:text-text"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="pt-2 border-t border-border">
                          <button
                            onClick={() => { setTakingTrade(sig.id); setEntryPrice(sig.suggested_entry?.toFixed(2) || ''); }}
                            className="text-xs px-4 py-1.5 bg-tabTradingScanner text-bg rounded hover:bg-tabTradingScanner/80 font-medium"
                          >
                            Take Trade
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Level Map Section (23d) */}
      <div className="mt-3">
        <button onClick={loadLevels} className="text-xs text-zinc-400 hover:text-zinc-200">
          {showLevels ? '▼' : '▶'} Level Map ({levels.length} levels)
        </button>
        {showLevels && levels.length > 0 && (
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            {['vp', 'vwap', 'session', 'order_block', 'fvg', 'single_print'].map(type => {
              const filtered = levels.filter((l: any) => l.level_type.includes(type));
              if (!filtered.length) return null;
              return (
                <div key={type} className="bg-zinc-800 p-2 rounded">
                  <div className="text-zinc-400 mb-1 uppercase">{type.replace('_', ' ')}</div>
                  {filtered.map((l: any, i: number) => (
                    <div key={i} className="flex justify-between">
                      <span className="text-zinc-300">{l.level_type}</span>
                      <span className="text-zinc-200">{l.price_low.toFixed(2)}{l.price_high !== l.price_low ? `-${l.price_high.toFixed(2)}` : ''}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function ConfirmCard({
  label, checked, autoChecked, overridden, onClick, detail, subDetail, detailColor,
}: {
  label: string;
  checked: boolean;
  autoChecked: boolean;
  overridden: boolean;
  onClick: () => void;
  detail: string;
  subDetail?: string;
  detailColor: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`border rounded p-2.5 text-left transition-colors ${
        checked
          ? 'border-tabTradingScanner/60 bg-tabTradingScanner/5'
          : 'border-border bg-panel hover:bg-panel2/50'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center text-[10px] ${
          checked ? 'border-tabTradingScanner bg-tabTradingScanner text-bg' : 'border-muted'
        }`}>
          {checked && '✓'}
        </div>
        <span className="text-xs font-medium text-text">{label}</span>
        {autoChecked && !overridden && (
          <span className="text-[9px] px-1 rounded bg-tabTradingScanner/20 text-tabTradingScanner ml-auto">auto</span>
        )}
        {overridden && (
          <span className="text-[9px] px-1 rounded bg-warning/20 text-warning ml-auto">override</span>
        )}
      </div>
      <div className={`text-xs font-mono ${detailColor}`}>{detail}</div>
      {subDetail && <div className="text-[10px] text-muted">{subDetail}</div>}
    </button>
  );
}

function Badge({ label, value, color = 'text-tabTradingScanner' }: { label: string; value: string; color?: string }) {
  return (
    <span className="text-muted">
      {label} <span className={`${color}`}>{value}</span>
    </span>
  );
}

function OFSignal({ label, active }: { label: string; active?: boolean }) {
  return (
    <span className={`px-1.5 py-0.5 rounded ${active ? 'bg-green-900/40 text-green-400' : 'bg-zinc-800 text-zinc-600'}`}>
      {active ? '✓' : '✗'} {label}
    </span>
  );
}
