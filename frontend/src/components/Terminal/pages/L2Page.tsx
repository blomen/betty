import { useMemo, useState, useEffect } from 'react';
import { GaugeBar } from './GaugeBar';
import { NearbyLevelStrip } from './NearbyLevelStrip';
import { TradeActionBar } from './TradeActionBar';
import { PositionManager } from './PositionManager';
import {
  featureOrderflowToGauges, featureTemporalToGauges, featureSessionToGauges,
  featureMacroToGauges, featureCandleToGauges, featureLevelToGauges,
  featureBookToGauges,
} from './gaugeHelpers';
import { getMlHealth } from '@/services/api';
import type {
  ExpandedSession, MonitoredLevel, PositionRow, BattleScreenData,
  PricePosition, StreamTickEvent, StreamBookEvent, MlPrediction,
  MlFeatureSnapshot, MlHealth,
} from '@/types/market';

interface Props {
  session: ExpandedSession | null;
  levels: MonitoredLevel[];
  currentPrice: number | null;
  connected: boolean;
  pricePos: PricePosition | undefined;
  onLevelClick: (levelName: string) => void;
  activeBattle: BattleScreenData | null;
  lastBattle: BattleScreenData | null;
  battleActive: boolean;
  onDismissBattle: () => void;
  onTakeTrade: (direction: 'long' | 'short', entry: number, stop: number, targets: { name: string; price: number }[]) => void;
  positions: PositionRow[];
  onScale: (tradeId: number, pct: number) => void;
  onClose: (tradeId: number) => void;
  lastTick: StreamTickEvent | null;
  book: StreamBookEvent | null;
  latestPrediction: MlPrediction | null;
  latestFeatures: MlFeatureSnapshot | null;
}

// --- Compact prediction bar (inline, no separate panel) ---
function PredictionBar({ prediction }: { prediction: MlPrediction | null }) {
  if (!prediction) return null;
  const pct = Math.round(prediction.confidence * 100);
  const p = prediction.predicted.toLowerCase();
  const isCont = p.includes('continuation') || p.includes('breakout');
  const isRev = p.includes('reversal') || p.includes('rejection');
  const color = isCont ? 'text-emerald-400' : isRev ? 'text-red-400' : 'text-zinc-400';
  const barColor = isCont ? 'bg-emerald-500' : isRev ? 'bg-red-500' : 'bg-zinc-600';

  return (
    <div className="flex items-center gap-2 text-xs font-mono">
      <span className="text-zinc-500">ML:</span>
      <span className={`font-semibold ${color}`}>{prediction.predicted}</span>
      <div className="w-16 h-1.5 bg-zinc-800 rounded-sm overflow-hidden">
        <div className={`h-full ${barColor} transition-all duration-300`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-zinc-500">{pct}%</span>
      {prediction.probabilities && (
        <span className="text-zinc-600 text-[10px]">
          {Object.entries(prediction.probabilities)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3)
            .map(([k, v]) => `${k.replace('_', ' ')}:${Math.round(v * 100)}%`)
            .join(' ')}
        </span>
      )}
    </div>
  );
}

// --- Model health strip ---
function ModelHealthStrip() {
  const [health, setHealth] = useState<MlHealth | null>(null);

  useEffect(() => {
    const load = () => getMlHealth().then(setHealth).catch(() => {});
    load();
    const iv = setInterval(load, 60_000);
    return () => clearInterval(iv);
  }, []);

  if (!health || !health.model_loaded) return null;

  const total = Object.values(health.class_distribution).reduce((a, b) => a + b, 0);
  const acc = health.recent_accuracy.last_50;

  return (
    <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-500">
      <span>MODEL: {total} samples</span>
      {acc != null && <span>acc: <span className={acc > 0.4 ? 'text-emerald-500' : 'text-zinc-400'}>{Math.round(acc * 100)}%</span></span>}
      {health.top_features.slice(0, 3).map(f => (
        <span key={f.name} className="text-cyan-600">{f.name}:{Math.round(f.importance * 100)}%</span>
      ))}
    </div>
  );
}

// --- Collapsible section ---
function Section({ title, count, open: defaultOpen, children }: {
  title: string; count: number; open: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between text-zinc-500 text-[10px] uppercase tracking-wider py-0.5 hover:text-zinc-300 border-b border-zinc-800/50"
      >
        <span>{title} <span className="text-zinc-600">({count})</span></span>
        <span className="text-zinc-600">{open ? '▼' : '▸'}</span>
      </button>
      {open && <div className="space-y-px py-0.5">{children}</div>}
    </div>
  );
}

export function L2Page({
  session, levels, currentPrice, connected, pricePos, onLevelClick,
  activeBattle, lastBattle, onDismissBattle, onTakeTrade,
  positions, onScale, onClose, lastTick, book, latestPrediction, latestFeatures,
}: Props) {
  const cp = currentPrice ?? 0;
  const battle = activeBattle ?? lastBattle;
  const isStale = !activeBattle && !!lastBattle;

  const nearbyLevels = useMemo(() => {
    const above = levels.filter(l => l.price > cp).sort((a, b) => a.price - b.price);
    const below = levels.filter(l => l.price <= cp).sort((a, b) => b.price - a.price);
    return {
      above: above.slice(0, 3).map(l => ({ name: l.name, price: l.price })),
      below: below.slice(0, 3).map(l => ({ name: l.name, price: l.price })),
    };
  }, [levels, cp]);

  // Build all gauge groups from features
  const f = latestFeatures?.features ?? {};
  const bookGauges = featureBookToGauges(book, f);
  const ofGauges = featureOrderflowToGauges(f);
  const temporalGauges = featureTemporalToGauges(f);
  const candleGauges = featureCandleToGauges(f);
  const sessionGauges = featureSessionToGauges(f);
  const macroGauges = featureMacroToGauges(f);
  const levelGauges = featureLevelToGauges(f);
  const hasData = latestFeatures != null || book != null;

  // Importance highlights from SHAP
  const importanceMap = useMemo(() => {
    const map: Record<string, number> = {};
    if (latestPrediction?.top_features) {
      const labelMap: Record<string, string> = {
        delta: 'DELTA', cvd: 'CVD', vsa_absorption: 'ABSORB',
        stacked_imbalance_count: 'IMBAL', big_trades_count: 'BIG',
        trapped_traders: 'TRAPPED', stop_run_detected: 'STOP RUN',
        passive_active_ratio: 'PA RATIO', delta_slope_5m: 'Δ SLP 5M',
        delta_slope_10m: 'Δ SLP 10M', cvd_acceleration: 'CVD ACCEL',
        volume_roc_5m: 'VOL ROC', spread_compression: 'SPREAD',
        price_velocity: 'PX VEL', absorption_building: 'ABSORB CT',
        imbalance_trend: 'IMBAL Δ', market_type: 'MKT TYPE',
        opening_type: 'OPEN TYPE', ib_range: 'IB RANGE',
        vix_level: 'VIX', regime_score: 'REG SCORE',
        level_type: 'LVL TYPE', level_strength: 'STRENGTH',
        level_confluence: 'CONFLNCE', delta_aligned: 'Δ ALIGN',
        delta_divergence: 'Δ DIVERG', last_candle_delta: 'LAST Δ',
        last_candle_body_ratio: 'BODY',
      };
      for (const feat of latestPrediction.top_features) {
        const label = labelMap[feat.name] || feat.name.toUpperCase().replace(/_/g, ' ');
        map[label] = Math.abs(feat.contribution);
      }
    }
    return map;
  }, [latestPrediction]);

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-1">
      {/* Header: price + battle + prediction + model health */}
      <div className="flex items-center gap-3 px-1 flex-wrap">
        <span className={`inline-block w-2 h-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-red-500'}`} />
        <span className="text-xs text-muted font-mono">L2 ZOOM</span>
        {currentPrice != null && (
          <span className="text-sm font-mono font-bold text-text">
            NQ {currentPrice.toFixed(2)}
          </span>
        )}
        {book && (
          <span className="text-[10px] font-mono text-zinc-500">
            <span className="text-emerald-500">{book.bid_size}</span>
            <span className="text-zinc-600 mx-0.5">×</span>
            <span className="text-red-400">{book.ask_size}</span>
            <span className="text-zinc-600 ml-1">spd:{book.spread.toFixed(2)}</span>
          </span>
        )}
        {lastTick && (
          <span className="text-[10px] font-mono text-zinc-500">
            cvd:<span className={lastTick.cvd > 0 ? 'text-emerald-500' : 'text-red-400'}>{lastTick.cvd}</span>
            {' '}Δ1m:<span className={lastTick.delta_1m > 0 ? 'text-emerald-500' : 'text-red-400'}>{lastTick.delta_1m}</span>
          </span>
        )}
        {battle && (
          <span className="flex items-center gap-1 text-xs">
            <span className="text-amber-400 font-bold">⚔</span>
            <span className="text-white font-mono">{battle.level}</span>
            <span className="text-zinc-500">@ {battle.level_price.toLocaleString()}</span>
            {battle.confluence.length > 0 && <span className="text-amber-400">+{battle.confluence.length}</span>}
            {isStale && <span className="text-zinc-600 text-[10px]">(stale)</span>}
          </span>
        )}
        {pricePos?.vwap_deviation_sd != null && (
          <span className={`text-[10px] font-mono ml-auto ${
            Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
            Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-amber-400' : 'text-zinc-500'
          }`}>
            VWAP {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)}σ
          </span>
        )}
      </div>

      {/* Level strip + prediction + model health */}
      <NearbyLevelStrip above={nearbyLevels.above} below={nearbyLevels.below} />
      <div className="flex items-center gap-4 px-1">
        <PredictionBar prediction={latestPrediction} />
        <ModelHealthStrip />
      </div>

      {/* FULL SCREEN GAUGE GRID */}
      <div className={`flex-1 overflow-y-auto min-h-0 border border-border bg-panel p-2 ${isStale ? 'opacity-60' : ''}`}>
        {hasData ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-0">
            {/* Left column */}
            <div className="space-y-1">
              <Section title="BOOK & VOLUME" count={bookGauges.length} open={true}>
                {bookGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
              <Section title="ORDERFLOW" count={ofGauges.length} open={true}>
                {ofGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
              <Section title="TEMPORAL" count={temporalGauges.length} open={true}>
                {temporalGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
            </div>
            {/* Right column */}
            <div className="space-y-1">
              <Section title="CANDLE" count={candleGauges.length} open={true}>
                {candleGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
              <Section title="SESSION" count={sessionGauges.length} open={true}>
                {sessionGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
              <Section title="MACRO" count={macroGauges.length} open={true}>
                {macroGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
              <Section title="LEVEL" count={levelGauges.length} open={true}>
                {levelGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
              </Section>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-zinc-600 text-sm font-mono">
            Approach a level to see all ML features
          </div>
        )}
      </div>

      {/* Trade execution bar */}
      {(activeBattle || positions.length > 0) && (
        <div className="flex-shrink-0 border border-amber-500/30 bg-zinc-900/50 p-2 space-y-2">
          {activeBattle && <TradeActionBar battle={activeBattle} onTrade={onTakeTrade} />}
          {positions.length > 0 && (
            <PositionManager positions={positions} onScale={onScale} onClose={onClose} onHold={() => {}} onUpdateStop={() => {}} />
          )}
        </div>
      )}
    </div>
  );
}
