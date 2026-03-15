import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import type { ExpandedSession, IndicatorsResponse, TradingSignal, ScanCondition } from '@/types/market';

// ─── Constants ───────────────────────────────────────────────────────────────

const SETUP_COLORS: Record<string, string> = {
  spring: 'bg-emerald-600', sfp: 'bg-blue-600', poor_extreme: 'bg-purple-600',
  ib_break: 'bg-orange-600', rule_80: 'bg-cyan-600', fakeout: 'bg-red-600',
  break_from_balance: 'bg-amber-600', double_distribution: 'bg-pink-600',
  news_directional: 'bg-indigo-600',
};

const LEVEL_COLORS: Record<string, { border: string; text: string; bg: string }> = {
  vwap:    { border: 'border-blue-500/60',   text: 'text-blue-400',   bg: 'bg-blue-500/10' },
  sd:      { border: 'border-blue-500/30',   text: 'text-blue-300/70', bg: 'bg-blue-500/5' },
  poc:     { border: 'border-yellow-500/60', text: 'text-yellow-400', bg: 'bg-yellow-500/10' },
  vah:     { border: 'border-yellow-500/30', text: 'text-yellow-300/70', bg: 'bg-yellow-500/5' },
  val:     { border: 'border-yellow-500/30', text: 'text-yellow-300/70', bg: 'bg-yellow-500/5' },
  ib:      { border: 'border-cyan-500/60',   text: 'text-cyan-400',   bg: 'bg-cyan-500/10' },
  pdh:     { border: 'border-green-500/60',  text: 'text-green-400',  bg: 'bg-green-500/10' },
  pdl:     { border: 'border-red-500/60',    text: 'text-red-400',    bg: 'bg-red-500/10' },
  swing:   { border: 'border-purple-500/60', text: 'text-purple-400', bg: 'bg-purple-500/10' },
  ob:      { border: 'border-orange-500/60', text: 'text-orange-400', bg: 'bg-orange-500/10' },
  fvg:     { border: 'border-amber-500/60',  text: 'text-amber-400',  bg: 'bg-amber-500/10' },
  session: { border: 'border-zinc-500/60',   text: 'text-zinc-300',   bg: 'bg-zinc-500/10' },
  overnight: { border: 'border-zinc-600/60', text: 'text-zinc-400',   bg: 'bg-zinc-500/5' },
  naked:   { border: 'border-orange-600/60', text: 'text-orange-300', bg: 'bg-orange-500/10' },
  default: { border: 'border-zinc-600/40',   text: 'text-zinc-400',   bg: 'bg-zinc-500/5' },
};

// ─── Types ───────────────────────────────────────────────────────────────────

interface LadderLevel {
  price: number;
  label: string;
  category: string;  // key into LEVEL_COLORS
  zone?: boolean;    // true for range levels (OB, FVG)
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

function getColors(cat: string) {
  return LEVEL_COLORS[cat] ?? LEVEL_COLORS.default;
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

function PriceLadder({ levels, currentPrice }: { levels: LadderLevel[]; currentPrice: number | null }) {
  if (levels.length === 0 && currentPrice == null) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        No session data. Click Compute to load.
      </div>
    );
  }

  // Insert price marker into sorted levels
  const priceVal = currentPrice ?? 0;
  const allItems: (LadderLevel | { isPrice: true; price: number })[] = [];
  let priceInserted = false;

  for (const lvl of levels) {
    if (!priceInserted && currentPrice != null && lvl.price <= priceVal) {
      allItems.push({ isPrice: true, price: priceVal });
      priceInserted = true;
    }
    allItems.push(lvl);
  }
  if (!priceInserted && currentPrice != null) {
    allItems.push({ isPrice: true, price: priceVal });
  }

  return (
    <div className="space-y-0 font-mono text-[11px]">
      {allItems.map((item, i) => {
        if ('isPrice' in item) {
          return (
            <div key="price" className="relative flex items-center gap-2 py-1.5 my-0.5">
              <div className="absolute inset-0 bg-tabTradingScanner/10 border-y border-tabTradingScanner/40 rounded" />
              <div className="relative flex items-center gap-2 w-full px-2">
                <span className="text-tabTradingScanner font-bold text-xs">▶</span>
                <span className="text-tabTradingScanner font-bold text-sm tracking-wide">
                  {item.price.toFixed(2)}
                </span>
                <div className="flex-1 border-t border-tabTradingScanner/50 border-dashed" />
                <span className="text-tabTradingScanner font-bold text-[10px] uppercase">Price</span>
              </div>
            </div>
          );
        }

        const colors = getColors(item.category);
        const dist = currentPrice != null ? item.price - priceVal : null;
        const isAbove = dist != null && dist > 0;
        const isClose = dist != null && Math.abs(dist) < (priceVal * 0.001); // within 0.1%

        return (
          <div
            key={`${i}-${item.label}`}
            className={`flex items-center gap-1.5 px-2 py-[3px] rounded-sm transition-colors hover:bg-zinc-800/50
              ${isClose ? 'bg-zinc-800/30' : ''}`}
          >
            {/* Price */}
            <span className={`w-[72px] text-right tabular-nums ${colors.text}`}>
              {item.zone && item.priceHigh != null
                ? `${item.price.toFixed(0)}`
                : item.price.toFixed(2)}
            </span>

            {/* Visual bar */}
            <div className="w-3 flex justify-center">
              <div className={`w-1.5 h-1.5 rounded-full ${item.zone ? 'w-3 h-1 rounded' : ''} ${
                item.category === 'vwap' ? 'bg-blue-400' :
                item.category === 'poc' ? 'bg-yellow-400' :
                item.category === 'ib' ? 'bg-cyan-400' :
                item.category === 'pdh' ? 'bg-green-400' :
                item.category === 'pdl' ? 'bg-red-400' :
                item.category === 'swing' ? 'bg-purple-400' :
                item.category === 'ob' ? 'bg-orange-400' :
                item.category === 'fvg' ? 'bg-amber-400' :
                item.category === 'naked' ? 'bg-orange-300' :
                'bg-zinc-500'
              }`} />
            </div>

            {/* Label */}
            <span className={`flex-1 truncate ${colors.text} text-[10px]`}>
              {item.label}
              {item.zone && item.priceHigh != null && (
                <span className="text-zinc-500 ml-1">
                  ({item.price.toFixed(0)}-{item.priceHigh.toFixed(0)})
                </span>
              )}
            </span>

            {/* Distance from price */}
            {dist != null && (
              <span className={`w-12 text-right tabular-nums text-[10px] ${
                isAbove ? 'text-green-500/60' : 'text-red-500/60'
              }`}>
                {isAbove ? '+' : ''}{dist.toFixed(0)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function OrderflowPanel({ of, connected, lastTick }: {
  of: IndicatorsResponse['orderflow'] | undefined;
  connected: boolean;
  lastTick: any;
}) {
  if (!of) {
    return (
      <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 text-zinc-500 text-[10px] py-2">
        Orderflow {connected ? <span className="text-green-400">● Live</span> : <span className="text-red-400">● Off</span>}
        <div className="mt-1 text-zinc-600">Waiting for data...</div>
      </div>
    );
  }

  const SIGNAL_ACTIVE_CLASSES: Record<string, string> = {
    green:  'text-green-400 bg-green-500/10 border border-green-500/30',
    orange: 'text-orange-400 bg-orange-500/10 border border-orange-500/30',
    yellow: 'text-yellow-400 bg-yellow-500/10 border border-yellow-500/30',
    cyan:   'text-cyan-400 bg-cyan-500/10 border border-cyan-500/30',
    red:    'text-red-400 bg-red-500/10 border border-red-500/30',
  };

  const signals = [
    { key: 'delta_aligned', label: 'Delta', active: of.delta_aligned, color: 'green' },
    { key: 'vsa', label: 'VSA', active: of.vsa_absorption, color: 'green' },
    { key: 'divergence', label: 'Diverg', active: of.delta_divergence, color: 'orange' },
    { key: 'unwind', label: 'Unwind', active: of.delta_unwind, color: 'yellow' },
    { key: 'tickvol', label: 'TickVol', active: of.tick_vol_accelerating, color: 'cyan' },
    { key: 'trapped', label: 'Trapped', active: of.trapped_traders, color: 'orange' },
    { key: 'stoprun', label: 'StopRun', active: of.stop_run_detected, color: 'red' },
  ];

  return (
    <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 space-y-1.5">
      <div className="flex items-center gap-2 text-[10px]">
        <span className="text-zinc-500 uppercase">Orderflow</span>
        <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
      </div>

      {/* Delta / CVD / Imbalance row */}
      <div className="flex gap-3 text-[11px] font-mono">
        <div>
          <span className="text-zinc-500 text-[9px]">Δ </span>
          <span className={(of.delta ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
            {of.delta != null ? `${of.delta > 0 ? '+' : ''}${of.delta.toLocaleString()}` : 'N/A'}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 text-[9px]">CVD </span>
          <span className={of.cvd_trend === 'rising' ? 'text-green-400' : of.cvd_trend === 'falling' ? 'text-red-400' : 'text-zinc-300'}>
            {of.cvd != null ? of.cvd.toLocaleString() : 'N/A'}
          </span>
        </div>
        {of.big_trades_count > 0 && (
          <div>
            <span className="text-zinc-500 text-[9px]">Big </span>
            <span className={of.big_trades_net_delta > 0 ? 'text-green-400' : 'text-red-400'}>
              x{of.big_trades_count}
            </span>
          </div>
        )}
      </div>

      {/* Signal flags */}
      <div className="flex flex-wrap gap-x-1.5 gap-y-0.5">
        {signals.map(s => (
          <span key={s.key} className={`text-[9px] px-1 py-0.5 rounded ${
            s.active ? SIGNAL_ACTIVE_CLASSES[s.color] : 'text-zinc-600'
          }`}>
            {s.active ? '✓' : '✗'} {s.label}
          </span>
        ))}
      </div>

      {/* Live tick */}
      {lastTick && (
        <div className="flex gap-2 text-[10px] font-mono pt-1 border-t border-zinc-800">
          <span className="text-zinc-400">{lastTick.price?.toFixed(2)}</span>
          <span className={(lastTick.delta_1m ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
            Δ1m {(lastTick.delta_1m ?? 0) > 0 ? '+' : ''}{lastTick.delta_1m ?? 0}
          </span>
        </div>
      )}
    </div>
  );
}

function ContextStrip({ session }: { session: ExpandedSession | null }) {
  const s = session?.session;
  const structure = session?.structure;
  const macro = session?.macro;
  if (!s) return null;

  return (
    <div className="flex items-start gap-0 text-[10px] font-mono overflow-x-auto">
      {/* Group 1: Macro */}
      {macro && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5 pr-3 border-r border-zinc-800 mr-3 min-w-0">
          <span className="text-zinc-500 uppercase text-[10px] w-full">Macro</span>
          <span className={macro.regime === 'risk_on' ? 'text-green-400' : macro.regime === 'risk_off' ? 'text-red-400' : 'text-yellow-400'}>
            {macro.regime?.replace('_', ' ')}
          </span>
          {macro.vix != null && (
            <span className={macro.vix < 18 ? 'text-green-400' : macro.vix > 25 ? 'text-red-400' : 'text-yellow-400'}>
              VIX {macro.vix.toFixed(1)}
              {macro.vix_change_pct != null && (
                <span className="text-zinc-500 text-[9px] ml-0.5">
                  {macro.vix_change_pct > 0 ? '+' : ''}{macro.vix_change_pct.toFixed(1)}%
                </span>
              )}
            </span>
          )}
          {macro.dxy != null && <span className="text-zinc-300">DXY {macro.dxy.toFixed(1)}</span>}
          {macro.us10y != null && <span className="text-zinc-300">10Y {macro.us10y.toFixed(2)}%</span>}
          {macro.yield_curve_spread != null && (
            <span className={macro.yield_curve_spread > 0 ? 'text-green-400' : 'text-red-400'}>
              2s10s {macro.yield_curve_spread > 0 ? '+' : ''}{macro.yield_curve_spread.toFixed(0)}bp
            </span>
          )}
        </div>
      )}

      {/* Group 2: Session */}
      <div className="flex flex-wrap gap-x-2 gap-y-0.5 pr-3 border-r border-zinc-800 mr-3 min-w-0">
        <span className="text-zinc-500 uppercase text-[10px] w-full">Session</span>
        {s.market_type && <span className="text-cyan-400">{s.market_type}</span>}
        {s.opening_type && <span className="text-zinc-300">{s.opening_type}</span>}
        {s.ib_range != null && <span className="text-cyan-300">IB {s.ib_range.toFixed(0)}pt</span>}
        {s.rotation_factor != null && (
          <span className={s.rotation_factor > 0 ? 'text-green-400' : s.rotation_factor < 0 ? 'text-red-400' : 'text-zinc-400'}>
            RF {s.rotation_factor > 0 ? '+' : ''}{s.rotation_factor}
          </span>
        )}
        {s.aspr != null && (
          <span className="text-zinc-300">
            ASPR {s.aspr.toFixed(1)}
            {s.aspr_percentile != null && <span className="text-zinc-500"> P{(s.aspr_percentile * 100).toFixed(0)}</span>}
          </span>
        )}
        {s.distribution_type && <span className="text-zinc-400">{s.distribution_type}</span>}
        {s.value_migration && (
          <span className={s.value_migration === 'up' ? 'text-green-400' : s.value_migration === 'down' ? 'text-red-400' : 'text-zinc-400'}>
            Val {s.value_migration}
          </span>
        )}
        {(s.poor_high || s.poor_low) && (
          <span className="text-orange-400">
            {[s.poor_high && 'PoorH', s.poor_low && 'PoorL'].filter(Boolean).join(' ')}
          </span>
        )}
        {s.single_prints && s.single_prints.length > 0 && (
          <span className="text-yellow-400">SP x{s.single_prints.length}</span>
        )}
      </div>

      {/* Group 3: Structure */}
      {structure && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5 min-w-0">
          <span className="text-zinc-500 uppercase text-[10px] w-full">Structure</span>
          <span className={
            structure.structure === 'uptrend' ? 'text-green-400' :
            structure.structure === 'downtrend' ? 'text-red-400' : 'text-yellow-400'
          }>
            {structure.structure === 'uptrend' ? 'HH/HL ↑' :
             structure.structure === 'downtrend' ? 'LH/LL ↓' : 'Ranging ↔'}
          </span>
          {session?.ml_day_type && (
            <span className="text-purple-400">
              ML: {session.ml_day_type}
              {session.ml_day_type_confidence != null && (
                <span className="text-zinc-500 ml-0.5">{(session.ml_day_type_confidence * 100).toFixed(0)}%</span>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function VolumeProfilesPanel({ session, onAnchorUpdate }: {
  session: ExpandedSession | null;
  onAnchorUpdate: (field: 'vp_leg_start' | 'vp_ongoing_macro_start', value: string) => void;
}) {
  const profiles = session?.profiles;
  if (!profiles) return (
    <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 flex items-center justify-center text-zinc-600 text-[10px]">No profile data</div>
  );

  return (
    <div className="flex flex-col min-h-0">
      <div className="text-[10px] text-zinc-500 uppercase mb-1.5">Volume Profiles</div>
      <div className="space-y-0.5 text-[10px] font-mono flex-1">
        {profiles.session && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Sess</span>
            <span className="text-yellow-400">{profiles.session.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.session.val?.toFixed(0)}-{profiles.session.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.weekly && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Wkly</span>
            <span className="text-yellow-400">{profiles.weekly.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.weekly.val?.toFixed(0)}-{profiles.weekly.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.leg && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Leg</span>
            <span className="text-yellow-400">{profiles.leg.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.leg.val?.toFixed(0)}-{profiles.leg.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.macro && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Macro</span>
            <span className="text-yellow-400">{profiles.macro.poc?.toFixed(0)}</span>
            <span className="text-zinc-500">{profiles.macro.val?.toFixed(0)}-{profiles.macro.vah?.toFixed(0)}</span>
          </div>
        )}
        {profiles.developing_poc != null && (
          <div className="flex gap-2">
            <span className="text-zinc-500 w-10">Dev</span>
            <span className="text-yellow-400">
              {profiles.developing_poc.toFixed(0)}
              {profiles.developing_poc_direction === 'up' ? ' ↑' : profiles.developing_poc_direction === 'down' ? ' ↓' : ''}
            </span>
          </div>
        )}
      </div>

      <div className="mt-auto pt-2 border-t border-zinc-800">
        <div className="flex gap-3 text-[10px]">
        <div className="flex items-center gap-1">
          <span className="text-zinc-500">Leg:</span>
          <input type="date"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
            defaultValue={profiles.leg?.anchor ?? ''}
            onBlur={e => onAnchorUpdate('vp_leg_start', e.target.value)} />
        </div>
        <div className="flex items-center gap-1">
          <span className="text-zinc-500">Macro:</span>
          <input type="date"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
            defaultValue={profiles.macro?.anchor ?? ''}
            onBlur={e => onAnchorUpdate('vp_ongoing_macro_start', e.target.value)} />
        </div>
        </div>
      </div>
    </div>
  );
}

function SignalRow({ sig, expanded, onToggle, onTakeTrade, connected, lastTick }: {
  sig: TradingSignal;
  expanded: boolean;
  onToggle: () => void;
  onTakeTrade: (sig: TradingSignal, price: string) => void;
  connected: boolean;
  lastTick: any;
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
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-zinc-800/30 transition-colors">
        {/* Score */}
        <div className="w-8 flex-shrink-0">
          <div className={`text-sm font-mono font-bold ${
            sig.score >= 80 ? 'text-green-400' : sig.score >= 70 ? 'text-tabTradingScanner' : 'text-yellow-400'
          }`}>{sig.score.toFixed(0)}</div>
        </div>

        {/* Setup name + direction */}
        <div className="flex-1 min-w-0 flex items-center gap-1.5">
          <span className="text-xs text-text font-medium truncate">{sig.setup_name}</span>
          <span className={`text-[10px] px-1 py-0.5 rounded ${
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
        <div className="px-3 pb-3 space-y-2 bg-zinc-900/30">
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

          {/* Live orderflow */}
          <div className="flex gap-3 text-[10px] font-mono bg-zinc-900/50 rounded px-2 py-1.5">
            <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
            <span className="text-zinc-400">Price {lastTick?.price?.toFixed(2) ?? '—'}</span>
            <span className={(lastTick?.delta_1m ?? 0) > 0 ? 'text-green-400' : 'text-red-400'}>
              Δ1m {(lastTick?.delta_1m ?? 0) > 0 ? '+' : ''}{lastTick?.delta_1m ?? 0}
            </span>
            <span className="text-zinc-400">CVD {lastTick?.cvd ?? 0}</span>
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
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

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

  // Auto-refresh all data every 30s
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

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await api.triggerMarketCompute();
      const [sessionRes, signalsRes, indicRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.triggerMarketScan(70).catch(() => ({ signals: [] })),
        api.getIndicators().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
      setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
      if (indicRes) setIndicators(indicRes);
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

  if (loading) return <div className="text-zinc-500 text-sm p-4">Loading scanner...</div>;

  return (
    <div className="flex flex-col h-full max-w-6xl">

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

        <span className="text-[9px] text-zinc-600 font-mono">
          auto 5m{lastRefresh && ` · ${lastRefresh.toLocaleTimeString()}`}
        </span>
        <button onClick={handleRefresh} disabled={isRefreshing}
          className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40 transition-colors">
          {isRefreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* ─── Two-column layout ─── */}
      <div className="flex gap-3 flex-1 min-h-0 px-1">

        {/* LEFT: Price Ladder */}
        <div className="w-[340px] flex-shrink-0 border border-zinc-800 rounded bg-zinc-900/30 overflow-y-auto">
          <div className="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-2 py-1.5 flex items-center justify-between">
            <span className="text-[10px] text-zinc-500 uppercase">Price Ladder</span>
            <span className="text-[10px] text-zinc-600">{ladderLevels.length} levels</span>
          </div>
          <div className="p-1">
            <PriceLadder levels={ladderLevels} currentPrice={currentPrice} />
          </div>
        </div>

        {/* RIGHT: Info Panels + Signals */}
        <div className="flex-1 min-w-0 grid grid-rows-[auto_minmax(160px,2fr)_minmax(200px,3fr)] gap-2 overflow-hidden">

          {/* Row 1: ContextStrip */}
          <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5">
            <ContextStrip session={session} />
          </div>

          {/* Row 2: OrderflowPanel + VolumeProfilesPanel */}
          <div className="grid grid-cols-2 gap-2 min-h-0">
            <OrderflowPanel of={indicators?.orderflow} connected={connected} lastTick={lastTick} />
            <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2.5 overflow-y-auto">
              <VolumeProfilesPanel session={session} onAnchorUpdate={handleAnchorUpdate} />
            </div>
          </div>

          {/* Row 3: Signals */}
          <div className="border border-zinc-800 rounded bg-zinc-900/30 flex-1 min-h-0 flex flex-col overflow-y-auto">
            <div className="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-3 py-1.5 flex items-center justify-between">
              <span className="text-xs font-semibold text-text">
                Signals <span className="text-tabTradingScanner">{signals.length}</span>
              </span>
              {signals.length === 0 && (
                <span className="text-[10px] text-zinc-600">Auto-scanning every 5 min (thr 70)</span>
              )}
            </div>

            {signals.length === 0 ? (
              <div className="p-4 text-center text-zinc-600 text-xs">
                No signals above threshold (70). Auto-scanning every 5 min.
              </div>
            ) : (
              <div className="overflow-y-auto">
                {signals.map(sig => (
                  <SignalRow
                    key={sig.id}
                    sig={sig}
                    expanded={expandedSignal === sig.id}
                    onToggle={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                    onTakeTrade={handleTakeTrade}
                    connected={connected}
                    lastTick={lastTick}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
