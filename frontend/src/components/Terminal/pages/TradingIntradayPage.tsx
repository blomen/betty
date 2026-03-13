import { useState, useEffect, useCallback } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import type { ExpandedSession, IndicatorsResponse, TradingSignal, ScanCondition } from '@/types/market';

const SETUP_COLORS: Record<string, string> = {
  spring: 'bg-emerald-600', sfp: 'bg-blue-600', poor_extreme: 'bg-purple-600',
  ib_break: 'bg-orange-600', rule_80: 'bg-cyan-600', fakeout: 'bg-red-600',
  break_from_balance: 'bg-amber-600', double_distribution: 'bg-pink-600',
  news_directional: 'bg-indigo-600',
};

// --- Helper components ---

const Pill = ({ label, value, color = 'zinc' }: { label: string; value?: string | number | null; color?: string }) => {
  const colors: Record<string, string> = {
    green: 'text-green-400', red: 'text-red-400', yellow: 'text-yellow-400',
    cyan: 'text-cyan-400', purple: 'text-purple-400', zinc: 'text-zinc-300',
    blue: 'text-blue-400',
  };
  return (
    <div className="flex items-center gap-1">
      <span className="text-zinc-500 text-[10px] uppercase">{label}</span>
      <span className={`${colors[color] ?? colors.zinc} text-[11px] font-mono`}>{value ?? 'N/A'}</span>
    </div>
  );
};

const Sep = () => <div className="w-px h-3.5 bg-zinc-700" />;

function Badge({ label, value, color = 'text-tabTradingScanner' }: { label: string; value: string; color?: string }) {
  return (
    <span className="text-muted">
      {label} <span className={color}>{value}</span>
    </span>
  );
}

// --- Main component ---

export function TradingIntradayPage() {
  const [session, setSession] = useState<ExpandedSession | null>(null);
  const [indicators, setIndicators] = useState<IndicatorsResponse | null>(null);
  const [signals, setSignals] = useState<TradingSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [threshold, setThreshold] = useState(70);
  const [isComputing, setIsComputing] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [takingTrade, setTakingTrade] = useState<number | null>(null);
  const [entryPrice, setEntryPrice] = useState('');
  const [showLevels, setShowLevels] = useState(false);
  const [levels, setLevels] = useState<any[]>([]);

  const { lastTick, connected } = useMarketStream();

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [sessionRes, signalsRes, indicRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getMarketSignals().catch(() => ({ signals: [] })),
        api.getIndicators().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
      if (indicRes) setIndicators(indicRes);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh indicators every 30s
  useEffect(() => {
    const refreshIndicators = async () => {
      const res = await api.getIndicators().catch(() => null);
      if (res) setIndicators(res);
    };
    const interval = setInterval(refreshIndicators, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleCompute = async () => {
    setIsComputing(true);
    try {
      await api.triggerMarketCompute();
      const [sessionRes, indicRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getIndicators().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      if (indicRes) setIndicators(indicRes);
    } finally {
      setIsComputing(false);
    }
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      const res = await api.triggerMarketScan(threshold);
      setSignals(res.signals || []);
      setLastScan(new Date().toLocaleTimeString());
    } finally {
      setIsScanning(false);
    }
  };

  const handleAnchorUpdate = async (field: 'vp_leg_start' | 'vp_ongoing_macro_start', value: string) => {
    await api.updateVPAnchors({ [field]: value });
    const res = await api.getExpandedSession().catch(() => null);
    if (res) setSession(res);
  };

  const handleTakeTrade = async (signal: TradingSignal) => {
    const price = parseFloat(entryPrice);
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
      setTakingTrade(null);
      setEntryPrice('');
    } catch (err) {
      console.error('Failed to create trade:', err);
    }
  };

  const loadLevels = async () => {
    const res = await fetch(`/api/trading/market/levels?symbol=NQ`);
    const data = await res.json();
    setLevels(data);
    setShowLevels(true);
  };

  const toggleLevels = () => {
    if (!showLevels && levels.length === 0) {
      loadLevels();
    } else {
      setShowLevels(v => !v);
    }
  };

  if (loading) return <div className="text-muted text-sm">Loading scanner...</div>;

  const s = session?.session;
  const macro = session?.macro;
  const structure = session?.structure;
  const profiles = session?.profiles;
  const structuralLevels = session?.levels ?? [];
  const pricePos = session?.price_position;
  const of = indicators?.orderflow;

  return (
    <div className="space-y-2 max-w-5xl">

      {/* Section 1: Header Bar */}
      <div className="flex items-center gap-3 flex-wrap border-b border-border pb-2">
        <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={18} />
        <span className="text-sm font-semibold text-text">Intraday</span>
        {pricePos?.last_price != null && (
          <span className="font-mono text-xs text-tabTradingScanner border border-tabTradingScanner/30 px-2 py-0.5 rounded">
            NQ {pricePos.last_price.toFixed(2)}
          </span>
        )}
        <div className="flex items-center gap-1 text-[10px]">
          <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
          <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
        </div>
        <div className="flex-1" />
        <button
          onClick={handleCompute}
          disabled={isComputing}
          className="text-xs px-3 py-1 border border-tabTradingScanner/50 text-tabTradingScanner rounded hover:bg-tabTradingScanner/10 disabled:opacity-40"
        >
          {isComputing ? 'Computing...' : 'Compute'}
        </button>
        <button
          onClick={handleScan}
          disabled={isScanning}
          className="text-xs px-3 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40"
        >
          {isScanning ? 'Scanning...' : 'Scan'}
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

      {/* Section 2: Macro Context Strip */}
      {macro && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="flex flex-wrap gap-3 items-center">
            <Pill label="Regime" value={macro.regime?.replace('_', ' ')}
              color={macro.regime === 'risk_on' ? 'green' : macro.regime === 'risk_off' ? 'red' : 'yellow'} />
            <Sep />
            <Pill label="Score" value={macro.regime_score?.toFixed(0)} color="zinc" />
            <Sep />
            <Pill label="VIX" value={macro.vix?.toFixed(1)}
              color={(macro.vix ?? 20) < 18 ? 'green' : (macro.vix ?? 20) > 25 ? 'red' : 'yellow'} />
            {macro.vix_change_pct != null && (
              <span className={`text-[10px] font-mono ${macro.vix_change_pct > 0 ? 'text-red-400' : 'text-green-400'}`}>
                {macro.vix_change_pct > 0 ? '+' : ''}{macro.vix_change_pct.toFixed(1)}%
              </span>
            )}
            <Sep />
            <Pill label="DXY" value={macro.dxy?.toFixed(1)} />
            <Sep />
            <Pill label="10Y" value={macro.us10y != null ? `${macro.us10y.toFixed(2)}%` : undefined} />
            {macro.yield_curve_spread != null && (
              <>
                <Sep />
                <Pill label="2s10s" value={`${macro.yield_curve_spread > 0 ? '+' : ''}${macro.yield_curve_spread.toFixed(0)}bp`}
                  color={macro.yield_curve_spread > 0 ? 'green' : 'red'} />
              </>
            )}
            {macro.cot_net_position != null && (
              <>
                <Sep />
                <Pill label="COT"
                  value={`${macro.cot_net_position > 0 ? '+' : ''}${macro.cot_net_position.toLocaleString()}`}
                  color={macro.cot_net_position > 0 ? 'green' : 'red'} />
              </>
            )}
            {macro.gex != null && (
              <>
                <Sep />
                <Pill label="GEX" value={macro.gex > 0 ? `+${(macro.gex / 1e9).toFixed(1)}B` : `${(macro.gex / 1e9).toFixed(1)}B`}
                  color={macro.gex > 0 ? 'green' : 'red'} />
              </>
            )}
            {macro.put_call_ratio != null && (
              <>
                <Sep />
                <Pill label="P/C" value={macro.put_call_ratio.toFixed(2)}
                  color={macro.put_call_ratio > 1 ? 'red' : 'green'} />
              </>
            )}
          </div>
        </div>
      )}

      {/* Section 3: Session Profile */}
      {s && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="flex flex-wrap gap-3 items-center">
            {s.market_type && <Pill label="Type" value={s.market_type} color="cyan" />}
            {session?.ml_day_type && (
              <>
                <Sep />
                <Pill label="ML Day" value={session.ml_day_type}
                  color="purple" />
                {session.ml_day_type_confidence != null && (
                  <span className="text-[10px] text-zinc-500 font-mono">
                    {(session.ml_day_type_confidence * 100).toFixed(0)}%
                  </span>
                )}
              </>
            )}
            {s.opening_type && (
              <>
                <Sep />
                <Pill label="Open" value={s.opening_type} />
              </>
            )}
            {s.ib_high != null && s.ib_low != null && (
              <>
                <Sep />
                <Pill label="IB" value={`${s.ib_low.toFixed(0)}-${s.ib_high.toFixed(0)}`} color="cyan" />
                {s.ib_range != null && <span className="text-zinc-500 text-[10px] font-mono">({s.ib_range.toFixed(0)}pts)</span>}
              </>
            )}
            {s.rotation_factor != null && (
              <>
                <Sep />
                <Pill label="RF" value={s.rotation_factor.toString()}
                  color={s.rotation_factor > 0 ? 'green' : s.rotation_factor < 0 ? 'red' : 'zinc'} />
              </>
            )}
            {s.aspr != null && (
              <>
                <Sep />
                <Pill label="ASPR" value={s.aspr.toFixed(2)} />
                {s.aspr_percentile != null && (
                  <span className="text-zinc-500 text-[10px] font-mono">({(s.aspr_percentile * 100).toFixed(0)}%ile)</span>
                )}
              </>
            )}
            {s.distribution_type && (
              <>
                <Sep />
                <Pill label="Dist" value={s.distribution_type} />
              </>
            )}
            {s.value_migration && (
              <>
                <Sep />
                <Pill label="Migration" value={s.value_migration}
                  color={s.value_migration === 'up' ? 'green' : s.value_migration === 'down' ? 'red' : 'zinc'} />
              </>
            )}
            {(s.poor_high || s.poor_low) && (
              <>
                <Sep />
                <span className="text-orange-400 text-[10px] font-mono">
                  {[s.poor_high && 'PoorH', s.poor_low && 'PoorL'].filter(Boolean).join(' ')}
                </span>
              </>
            )}
            {s.single_prints && s.single_prints.length > 0 && (
              <>
                <Sep />
                <Pill label="SinglePrints" value={s.single_prints.length.toString()} color="yellow" />
              </>
            )}
          </div>
          {/* TPO row */}
          {(s.tpo_poc != null || s.tpo_vah != null || s.tpo_val != null) && (
            <div className="flex gap-3 items-center mt-1.5 pt-1.5 border-t border-zinc-800">
              <span className="text-zinc-500 text-[10px] uppercase">TPO</span>
              {s.tpo_val != null && <Pill label="VAL" value={s.tpo_val.toFixed(2)} />}
              {s.tpo_poc != null && <Pill label="POC" value={s.tpo_poc.toFixed(2)} color="yellow" />}
              {s.tpo_vah != null && <Pill label="VAH" value={s.tpo_vah.toFixed(2)} />}
            </div>
          )}
          {/* Overnight row */}
          {(s.overnight_high != null || s.overnight_low != null) && (
            <div className="flex gap-3 items-center mt-1.5 pt-1.5 border-t border-zinc-800">
              <span className="text-zinc-500 text-[10px] uppercase">Overnight</span>
              {s.overnight_low != null && <Pill label="Low" value={s.overnight_low.toFixed(2)} color="red" />}
              {s.overnight_high != null && <Pill label="High" value={s.overnight_high.toFixed(2)} color="green" />}
            </div>
          )}
        </div>
      )}

      {/* Section 4: Price Structure */}
      {structure && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="flex flex-wrap gap-3 items-center">
            <Pill label="Structure"
              value={structure.structure?.replace('_', ' ')}
              color={structure.structure === 'uptrend' ? 'green' : structure.structure === 'downtrend' ? 'red' : 'yellow'} />
            {structure.last_hh != null && <><Sep /><Pill label="HH" value={structure.last_hh.toFixed(2)} color="green" /></>}
            {structure.last_hl != null && <><Sep /><Pill label="HL" value={structure.last_hl.toFixed(2)} color="green" /></>}
            {structure.last_lh != null && <><Sep /><Pill label="LH" value={structure.last_lh.toFixed(2)} color="red" /></>}
            {structure.last_ll != null && <><Sep /><Pill label="LL" value={structure.last_ll.toFixed(2)} color="red" /></>}
            {structure.swing_high != null && <><Sep /><Pill label="SwingH" value={structure.swing_high.toFixed(2)} color="green" /></>}
            {structure.swing_low != null && <><Sep /><Pill label="SwingL" value={structure.swing_low.toFixed(2)} color="red" /></>}
          </div>
        </div>
      )}

      {/* Section 5: Multi-TF Volume Profiles */}
      {profiles && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="text-zinc-500 text-[10px] uppercase mb-1.5">Volume Profiles</div>
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-zinc-500 text-[10px]">
                <th className="text-left font-normal pb-1">Profile</th>
                <th className="text-left font-normal pb-1">Anchor</th>
                <th className="text-right font-mono font-normal pb-1">VAL</th>
                <th className="text-right font-mono font-normal pb-1">POC</th>
                <th className="text-right font-mono font-normal pb-1">VAH</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {profiles.session && (
                <tr>
                  <td className="py-0.5 text-zinc-300">Session</td>
                  <td className="py-0.5 text-zinc-500">today</td>
                  <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.session.val?.toFixed(2) ?? '—'}</td>
                  <td className="py-0.5 text-right font-mono text-yellow-400">{profiles.session.poc?.toFixed(2) ?? '—'}</td>
                  <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.session.vah?.toFixed(2) ?? '—'}</td>
                </tr>
              )}
              {profiles.weekly && (
                <tr>
                  <td className="py-0.5 text-zinc-300">Weekly</td>
                  <td className="py-0.5 text-zinc-500">{profiles.weekly.anchor ?? 'week'}</td>
                  <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.weekly.val?.toFixed(2) ?? '—'}</td>
                  <td className="py-0.5 text-right font-mono text-yellow-400">{profiles.weekly.poc?.toFixed(2) ?? '—'}</td>
                  <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.weekly.vah?.toFixed(2) ?? '—'}</td>
                </tr>
              )}
              <tr>
                <td className="py-0.5 text-zinc-300">Leg</td>
                <td className="py-0.5">
                  <input
                    type="date"
                    className="bg-zinc-800 border border-zinc-700 rounded px-1 text-[10px] text-zinc-300 w-24"
                    defaultValue={profiles.leg?.anchor ?? ''}
                    onBlur={e => handleAnchorUpdate('vp_leg_start', e.target.value)}
                  />
                </td>
                <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.leg?.val?.toFixed(2) ?? '—'}</td>
                <td className="py-0.5 text-right font-mono text-yellow-400">{profiles.leg?.poc?.toFixed(2) ?? '—'}</td>
                <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.leg?.vah?.toFixed(2) ?? '—'}</td>
              </tr>
              <tr>
                <td className="py-0.5 text-zinc-300">Macro</td>
                <td className="py-0.5">
                  <input
                    type="date"
                    className="bg-zinc-800 border border-zinc-700 rounded px-1 text-[10px] text-zinc-300 w-24"
                    defaultValue={profiles.macro?.anchor ?? ''}
                    onBlur={e => handleAnchorUpdate('vp_ongoing_macro_start', e.target.value)}
                  />
                </td>
                <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.macro?.val?.toFixed(2) ?? '—'}</td>
                <td className="py-0.5 text-right font-mono text-yellow-400">{profiles.macro?.poc?.toFixed(2) ?? '—'}</td>
                <td className="py-0.5 text-right font-mono text-zinc-300">{profiles.macro?.vah?.toFixed(2) ?? '—'}</td>
              </tr>
            </tbody>
          </table>
          <div className="flex gap-3 mt-1.5 pt-1.5 border-t border-zinc-800 flex-wrap items-center">
            {profiles.developing_poc != null && (
              <Pill label="Dev POC"
                value={`${profiles.developing_poc.toFixed(2)} ${profiles.developing_poc_direction === 'up' ? '▲' : profiles.developing_poc_direction === 'down' ? '▼' : '—'}`}
                color={profiles.developing_poc_direction === 'up' ? 'green' : profiles.developing_poc_direction === 'down' ? 'red' : 'zinc'} />
            )}
            {profiles.naked_pocs.length > 0 && (
              <>
                <Sep />
                <span className="text-zinc-500 text-[10px] uppercase">Naked POCs:</span>
                {profiles.naked_pocs.slice(0, 4).map((np, i) => (
                  <span key={i} className="font-mono text-[11px] text-orange-400">{np.price.toFixed(2)}</span>
                ))}
                {profiles.naked_pocs.length > 4 && <span className="text-zinc-500 text-[10px]">+{profiles.naked_pocs.length - 4}</span>}
              </>
            )}
          </div>
        </div>
      )}

      {/* Section 6: VWAP Bands */}
      {s && s.vwap != null && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="text-zinc-500 text-[10px] uppercase mb-1.5">VWAP Bands</div>
          <div className="flex flex-wrap gap-2 items-center">
            {s.vwap_3sd_upper != null && <Pill label="+3SD" value={s.vwap_3sd_upper.toFixed(2)} color="red" />}
            {s.vwap_2sd_upper != null && <Pill label="+2SD" value={s.vwap_2sd_upper.toFixed(2)} color="red" />}
            {s.vwap_1sd_upper != null && <Pill label="+1SD" value={s.vwap_1sd_upper.toFixed(2)} color="yellow" />}
            <div className="flex items-center gap-1">
              <span className="text-zinc-500 text-[10px] uppercase">VWAP</span>
              <span className="text-blue-400 text-[11px] font-mono font-bold">{s.vwap.toFixed(2)}</span>
            </div>
            {s.vwap_1sd_lower != null && <Pill label="-1SD" value={s.vwap_1sd_lower.toFixed(2)} color="yellow" />}
            {s.vwap_2sd_lower != null && <Pill label="-2SD" value={s.vwap_2sd_lower.toFixed(2)} color="green" />}
            {s.vwap_3sd_lower != null && <Pill label="-3SD" value={s.vwap_3sd_lower.toFixed(2)} color="green" />}
            {pricePos?.vwap_deviation_sd != null && (
              <>
                <Sep />
                <Pill label="Dev"
                  value={`${pricePos.vwap_deviation_sd > 0 ? '+' : ''}${pricePos.vwap_deviation_sd.toFixed(2)} SD`}
                  color={Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'red' : Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'yellow' : 'zinc'} />
              </>
            )}
          </div>
        </div>
      )}

      {/* Section 7: Structural Levels */}
      {structuralLevels.length > 0 && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="text-zinc-500 text-[10px] uppercase mb-1.5">Key Levels</div>
          <div className="flex flex-wrap gap-1.5">
            {structuralLevels.map((lvl, i) => {
              const typeLC = lvl.type?.toLowerCase() ?? '';
              const color =
                typeLC.includes('pdh') ? 'border-green-600 text-green-400' :
                typeLC.includes('pdl') ? 'border-red-600 text-red-400' :
                typeLC.includes('ib') ? 'border-cyan-600 text-cyan-400' :
                typeLC.includes('vp') || typeLC.includes('poc') || typeLC.includes('vah') || typeLC.includes('val') ? 'border-yellow-600 text-yellow-400' :
                typeLC.includes('vwap') ? 'border-blue-600 text-blue-400' :
                'border-zinc-600 text-zinc-300';
              const priceStr = lvl.price_low === lvl.price_high
                ? lvl.price_low.toFixed(2)
                : `${lvl.price_low.toFixed(2)}-${lvl.price_high.toFixed(2)}`;
              return (
                <span key={i} className={`border rounded px-1.5 py-0.5 text-[10px] font-mono ${color}`}>
                  {lvl.type} {priceStr}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Section 8: Orderflow Indicators */}
      {of && (
        <div className="bg-zinc-900/50 border border-zinc-800 rounded p-2 text-xs">
          <div className="text-zinc-500 text-[10px] uppercase mb-1.5">
            Orderflow {connected ? <span className="text-green-400">● Live</span> : <span className="text-red-400">● Offline</span>}
          </div>
          <div className="flex flex-wrap gap-3 items-center">
            <Pill label="Delta"
              value={of.delta != null ? `${of.delta > 0 ? '+' : ''}${of.delta.toLocaleString()}` : undefined}
              color={(of.delta ?? 0) > 0 ? 'green' : 'red'} />
            <Sep />
            <Pill label="CVD"
              value={of.cvd != null ? of.cvd.toLocaleString() : undefined}
              color={of.cvd_trend === 'rising' ? 'green' : of.cvd_trend === 'falling' ? 'red' : 'zinc'} />
            <Sep />
            <Pill label="ImbalMax" value={of.imbalance_ratio_max?.toFixed(2)} />
            {of.stacked_imbalance_count > 0 && (
              <>
                <Sep />
                <Pill label="StackedImb"
                  value={`${of.stacked_imbalance_count} ${of.stacked_imbalance_direction}`}
                  color={of.stacked_imbalance_direction === 'buy' ? 'green' : of.stacked_imbalance_direction === 'sell' ? 'red' : 'zinc'} />
              </>
            )}
            {of.passive_active_ratio != null && (
              <>
                <Sep />
                <Pill label="P/A" value={of.passive_active_ratio.toFixed(2)} />
              </>
            )}
            {of.big_trades_count > 0 && (
              <>
                <Sep />
                <Pill label="BigTrades"
                  value={`${of.big_trades_count} (${of.big_trades_net_delta > 0 ? '+' : ''}${of.big_trades_net_delta.toLocaleString()})`}
                  color={of.big_trades_net_delta > 0 ? 'green' : 'red'} />
              </>
            )}
          </div>
          <div className="flex gap-3 mt-1.5 text-[10px] flex-wrap">
            <span className={of.delta_aligned ? 'text-green-400' : 'text-zinc-500'}>
              {of.delta_aligned ? '✓' : '✗'} Delta Aligned
            </span>
            <span className={of.vsa_absorption ? 'text-green-400' : 'text-zinc-500'}>
              {of.vsa_absorption ? '✓' : '✗'} VSA Absorption
            </span>
            <span className={of.delta_divergence ? 'text-orange-400' : 'text-zinc-500'}>
              {of.delta_divergence ? '✓' : '✗'} Delta Divergence
            </span>
            <span className={of.delta_unwind ? 'text-yellow-400' : 'text-zinc-500'}>
              {of.delta_unwind ? '✓' : '✗'} Delta Unwind
            </span>
            <span className={of.tick_vol_accelerating ? 'text-cyan-400' : 'text-zinc-500'}>
              {of.tick_vol_accelerating ? '✓' : '✗'} Tick Vol Accel
            </span>
            <span className={of.trapped_traders ? 'text-orange-400' : 'text-zinc-500'}>
              {of.trapped_traders ? '✓' : '✗'} Trapped Traders
            </span>
            <span className={of.stop_run_detected ? 'text-red-400' : 'text-zinc-500'}>
              {of.stop_run_detected ? '✓' : '✗'} Stop Run
            </span>
          </div>
          {/* Live stream data */}
          {lastTick && (
            <div className="flex gap-3 mt-1.5 pt-1.5 border-t border-zinc-800 text-[10px] font-mono">
              <Badge label="Price" value={lastTick.price?.toFixed(2) ?? '—'} color="text-text" />
              <Badge label="Delta 1m"
                value={`${(lastTick.delta_1m ?? 0) > 0 ? '+' : ''}${lastTick.delta_1m ?? 0}`}
                color={(lastTick.delta_1m ?? 0) > 0 ? 'text-green-400' : 'text-red-400'} />
              <Badge label="CVD" value={String(lastTick.cvd ?? 0)} color="text-zinc-300" />
            </div>
          )}
        </div>
      )}

      {/* Section 9: Signal Table */}
      <div className="border border-border bg-panel rounded">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <h3 className="text-sm font-semibold text-text">
            Signals ({signals.length})
          </h3>
          {signals.length === 0 && (
            <span className="text-xs text-muted">Scan to find opportunities</span>
          )}
        </div>

        {signals.length === 0 ? (
          <div className="p-4 text-center text-muted text-sm">
            No signals above threshold. Click Scan to search.
          </div>
        ) : (
          <div className="divide-y divide-border">
            {signals.map(sig => {
              const rrCalc = sig.suggested_entry && sig.suggested_stop && sig.suggested_target
                ? Math.abs(sig.suggested_target - sig.suggested_entry) / Math.abs(sig.suggested_entry - sig.suggested_stop)
                : null;
              const rr = sig.rr_tp1 ?? rrCalc;
              const conditions: ScanCondition[] = typeof sig.conditions === 'string'
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

                      {/* L2 Orderflow Panel */}
                      <div className="bg-zinc-900 p-3 rounded mt-2">
                        <div className="text-xs text-zinc-400 mb-2">
                          L2 Orderflow <span className={connected ? 'text-green-400' : 'text-red-400'}>{connected ? '● Live' : '● Offline'}</span>
                        </div>
                        <div className="flex gap-4 text-xs">
                          <div>Delta: <span className={(lastTick?.delta_1m ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
                            {lastTick?.delta_1m ?? 0}</span></div>
                          <div>CVD: <span className="text-zinc-200">{lastTick?.cvd ?? 0}</span></div>
                          <div>Last: <span className="text-zinc-200">{lastTick?.price?.toFixed(2) ?? '—'}</span></div>
                        </div>
                        {(() => {
                          try {
                            const condRaw = typeof sig.conditions === 'string' ? JSON.parse(sig.conditions) : null;
                            const sigOf = condRaw?.orderflow;
                            if (!sigOf) return null;
                            return (
                              <div className="flex gap-3 mt-2 text-xs">
                                <span>{sigOf.delta_aligned ? '✓' : '✗'} Delta</span>
                                <span>{sigOf.vsa_absorption ? '✓' : '✗'} VSA</span>
                                <span>{sigOf.delta_divergence ? '✓' : '✗'} Divergence</span>
                                <span>{sigOf.tick_vol_accelerating ? '✓' : '✗'} Tick Vol</span>
                                <span>{sigOf.trapped_traders ? '✓' : '✗'} Trapped</span>
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

      {/* Section 10: Level Map (collapsible) */}
      <div>
        <button onClick={toggleLevels} className="text-xs text-zinc-400 hover:text-zinc-200">
          {showLevels ? '▼' : '▶'} Level Map ({levels.length} levels)
        </button>
        {showLevels && levels.length > 0 && (
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            {['vp', 'vwap', 'session', 'order_block', 'fvg', 'single_print'].map(type => {
              const filtered = levels.filter((l: any) => l.level_type?.includes(type));
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
