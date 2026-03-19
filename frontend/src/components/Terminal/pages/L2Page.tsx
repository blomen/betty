import { useMemo, useState, useEffect } from 'react';
import { LevelTable } from './LevelTable';
import { ContextSidebar } from './ContextSidebar';
import { NearbyLevelStrip } from './NearbyLevelStrip';
import { GaugeBar } from './GaugeBar';
import { FootprintChart } from './FootprintChart';
import { TickTape } from './TickTape';
import { TradeActionBar } from './TradeActionBar';
import { PositionManager } from './PositionManager';
import {
  orderflowToGauges, structureToGauges, mlToGauges,
  featureOrderflowToGauges, featureTemporalToGauges, featureSessionToGauges,
  featureMacroToGauges, featureCandleToGauges, featureLevelToGauges,
} from './gaugeHelpers';
import { getMlHealth } from '@/services/api';
import type {
  ExpandedSession, MonitoredLevel, PositionRow, BattleScreenData,
  PricePosition, StreamTickEvent, MlPrediction, MlFeatureSnapshot, MlHealth,
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
  latestPrediction: MlPrediction | null;
  latestFeatures: MlFeatureSnapshot | null;
}

function predictionColor(predicted: string): string {
  const p = predicted.toLowerCase();
  if (p.includes('continuation') || p.includes('breakout') || p.includes('trend')) return 'text-emerald-400';
  if (p.includes('reversal') || p.includes('rejection') || p.includes('fail')) return 'text-red-400';
  return 'text-zinc-400';
}

function MlPredictionPanel({ prediction }: { prediction: MlPrediction | null }) {
  if (!prediction) {
    return (
      <div className="border border-border bg-panel p-2">
        <div className="text-zinc-500 text-[10px] uppercase tracking-wider mb-1">ML Prediction</div>
        <div className="text-zinc-600 text-xs font-mono">No prediction yet</div>
      </div>
    );
  }

  const isUncertain = prediction.confidence < 0.45;
  const pct = Math.round(prediction.confidence * 100);
  const color = isUncertain ? 'text-zinc-400' : predictionColor(prediction.predicted);
  const barColor = isUncertain ? 'bg-zinc-600' : prediction.predicted.toLowerCase().includes('reversal') || prediction.predicted.toLowerCase().includes('rejection') || prediction.predicted.toLowerCase().includes('fail') ? 'bg-red-500' : prediction.predicted.toLowerCase().includes('continuation') || prediction.predicted.toLowerCase().includes('breakout') || prediction.predicted.toLowerCase().includes('trend') ? 'bg-emerald-500' : 'bg-zinc-500';

  // Sort probabilities descending, show top 4
  const sortedProbs = Object.entries(prediction.probabilities)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);
  const maxProb = sortedProbs[0]?.[1] ?? 1;

  return (
    <div className="border border-border bg-panel p-2 space-y-1.5">
      <div className="text-zinc-500 text-[10px] uppercase tracking-wider">ML Prediction</div>

      {/* Level + outcome */}
      <div className="flex items-baseline gap-2">
        <span className="text-zinc-500 text-[10px] font-mono">{prediction.level}</span>
        <span className={`text-xs font-mono font-semibold ${color}`}>{prediction.predicted}</span>
        {isUncertain && <span className="text-zinc-600 text-[10px]">(uncertain)</span>}
      </div>

      {/* Confidence bar */}
      <div>
        <div className="flex justify-between items-baseline mb-0.5">
          <span className="text-zinc-600 text-[10px]">Confidence</span>
          <span className={`text-[10px] font-mono ${isUncertain ? 'text-zinc-500' : 'text-zinc-300'}`}>{pct}%</span>
        </div>
        <div className="h-1 bg-zinc-800 rounded-sm overflow-hidden">
          <div className={`h-full ${barColor} transition-all duration-300`} style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* Probability distribution */}
      <div className="space-y-0.5">
        {sortedProbs.map(([label, prob]) => (
          <div key={label} className="flex items-center gap-1.5">
            <span className="text-[9px] font-mono text-zinc-500 w-24 truncate">{label}</span>
            <div className="flex-1 h-1 bg-zinc-800 rounded-sm overflow-hidden">
              <div
                className="h-full bg-zinc-600 transition-all duration-300"
                style={{ width: `${Math.round((prob / maxProb) * 100)}%` }}
              />
            </div>
            <span className="text-[9px] font-mono text-zinc-500 w-7 text-right">{Math.round(prob * 100)}%</span>
          </div>
        ))}
      </div>

      {/* Top features (optional) */}
      {prediction.top_features && prediction.top_features.length > 0 && (
        <div className="border-t border-zinc-800 pt-1 space-y-0.5">
          {prediction.top_features.slice(0, 3).map(f => (
            <div key={f.name} className="flex justify-between items-baseline">
              <span className="text-[9px] font-mono text-zinc-600 truncate max-w-[80px]">{f.name}</span>
              <span className={`text-[9px] font-mono ${f.contribution > 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                {f.contribution > 0 ? '+' : ''}{f.contribution.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelHealthPanel() {
  const [health, setHealth] = useState<MlHealth | null>(null);

  useEffect(() => {
    const load = () => getMlHealth().then(setHealth).catch(() => {});
    load();
    const iv = setInterval(load, 60_000);
    return () => clearInterval(iv);
  }, []);

  if (!health || !health.model_loaded) return null;

  const totalSamples = Object.values(health.class_distribution).reduce((a, b) => a + b, 0);
  const maxCount = Math.max(...Object.values(health.class_distribution), 1);

  return (
    <div className="border border-border bg-panel p-2 space-y-1">
      <div className="text-zinc-500 text-[10px] uppercase tracking-wider">Model Health</div>
      <div className="text-[10px] font-mono text-zinc-400">
        {totalSamples} samples | acc: {health.recent_accuracy.last_50 ? Math.round(health.recent_accuracy.last_50 * 100) + '%' : '--'}
        {health.validation_score != null && ` | val: ${Math.round(health.validation_score * 100)}%`}
      </div>

      {/* Class distribution bars */}
      <div className="space-y-0.5">
        {Object.entries(health.class_distribution).map(([cls, cnt]) => (
          <div key={cls} className="flex items-center gap-1">
            <span className="text-[9px] font-mono text-zinc-500 w-20 truncate">{cls}</span>
            <div className="flex-1 h-1 bg-zinc-800 rounded-sm overflow-hidden">
              <div className="h-full bg-zinc-600" style={{ width: `${(cnt / maxCount) * 100}%` }} />
            </div>
            <span className="text-[9px] font-mono text-zinc-600 w-6 text-right">{cnt}</span>
          </div>
        ))}
      </div>

      {/* Top features */}
      {health.top_features.length > 0 && (
        <div className="border-t border-zinc-800 pt-1 space-y-0.5">
          <div className="text-zinc-600 text-[9px]">Feature Importance</div>
          {health.top_features.slice(0, 5).map((f) => (
            <div key={f.name} className="flex items-center gap-1">
              <span className="text-[9px] font-mono text-zinc-500 w-20 truncate">{f.name}</span>
              <div className="flex-1 h-1 bg-zinc-800 rounded-sm overflow-hidden">
                <div className="h-full bg-cyan-600/60" style={{ width: `${f.importance * 100 / (health.top_features[0]?.importance || 1) * 100}%` }} />
              </div>
              <span className="text-[9px] font-mono text-cyan-600 w-6 text-right">{Math.round(f.importance * 100)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CollapsibleSection({ title, count, defaultOpen, children }: {
  title: string; count: number; defaultOpen: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between text-zinc-500 text-[10px] uppercase tracking-wider py-1 hover:text-zinc-300"
      >
        <span>{title} ({count})</span>
        <span>{open ? '▼' : '▸'}</span>
      </button>
      {open && <div className="space-y-0.5">{children}</div>}
    </div>
  );
}

export function L2Page({
  session, levels, currentPrice, connected, pricePos, onLevelClick,
  activeBattle, lastBattle, onDismissBattle, onTakeTrade,
  positions, onScale, onClose, lastTick, latestPrediction, latestFeatures,
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

  const ofGauges = battle?.orderflow ? orderflowToGauges(battle.orderflow) : [];
  const structGauges = battle ? structureToGauges(battle.structure) : [];
  const mlGauges = battle ? mlToGauges(battle.ml, battle.macro, battle.confluence) : [];
  const hasGauges = ofGauges.length > 0 || structGauges.length > 0 || mlGauges.length > 0;

  // Feature-based gauges from ml_features SSE
  const f = latestFeatures?.features ?? {};
  const ofGaugesFromFeatures = featureOrderflowToGauges(f);
  const temporalGauges = featureTemporalToGauges(f);
  const sessionGauges = featureSessionToGauges(f);
  const macroGauges = featureMacroToGauges(f);
  const candleGauges = featureCandleToGauges(f);
  const levelGauges = featureLevelToGauges(f);

  // Importance map from latestPrediction.top_features (SHAP contributions)
  const importanceMap = useMemo(() => {
    const map: Record<string, number> = {};
    if (latestPrediction?.top_features) {
      const labelMap: Record<string, string> = {
        delta: 'DELTA',
        cvd: 'CVD',
        vsa_absorption: 'ABSORB',
        stacked_imbalance_count: 'IMBAL',
        big_trades_count: 'BIG',
        trapped_traders: 'TRAPPED',
        stop_run_detected: 'STOP RUN',
        passive_active_ratio: 'PA RATIO',
        delta_slope_5m: 'Δ SLP 5M',
        delta_slope_10m: 'Δ SLP 10M',
        cvd_acceleration: 'CVD ACCEL',
        volume_roc_5m: 'VOL ROC',
        tick_roc_5m: 'TICK ROC',
        spread_compression: 'SPREAD',
        price_velocity: 'PX VEL',
        absorption_building: 'ABSORB CT',
        imbalance_trend: 'IMBAL Δ',
        market_type: 'MKT TYPE',
        opening_type: 'OPEN TYPE',
        ib_range: 'IB RANGE',
        ib_range_vs_aspr: 'IB/ASPR',
        aspr_percentile: 'ASPR %',
        rotation_factor: 'ROT FACTR',
        value_migration: 'VAL MIGR',
        distance_from_vah: 'vs VAH',
        distance_from_val: 'vs VAL',
        distance_from_poc: 'vs POC',
        price_in_va: 'IN VA',
        session_elapsed_pct: 'ELAPSED',
        minutes_since_open: 'MIN OPEN',
        developing_poc_direction: 'DEV POC',
        prior_touch_count: 'TOUCHES',
        vix_level: 'VIX',
        vix_change: 'VIX CHG',
        macro_regime: 'REGIME',
        regime_score: 'REG SCORE',
        macro_bias: 'BIAS',
        last_3_candles_direction: 'LAST 3',
        recent_doji: 'DOJI',
        consecutive_same_direction: 'CONSEC',
        highest_volume_candle_position: 'HI VOL',
        range_expansion: 'RANGE EXP',
        level_type: 'LVL TYPE',
        level_category: 'LVL CAT',
        level_strength: 'STRENGTH',
        level_confluence: 'CONFLNCE',
        approach_direction: 'APPROACH',
        distance_from_vwap: 'DIST VWAP',
      };
      for (const feat of latestPrediction.top_features) {
        const label = labelMap[feat.name] || feat.name.toUpperCase();
        map[label] = Math.abs(feat.contribution);
      }
    }
    return map;
  }, [latestPrediction]);

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header */}
      <div className="flex items-center gap-3 px-1">
        <span className={`inline-block w-2 h-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-red-500'}`} />
        <span className="text-xs text-muted font-mono">L2 ANALYSIS</span>
        {currentPrice != null && (
          <span className="text-sm font-mono font-bold text-text">
            NQ {currentPrice.toFixed(2)}
          </span>
        )}
        {battle && (
          <span className="flex items-center gap-2 ml-2 text-xs">
            {isStale && <span className="px-1.5 py-0.5 bg-zinc-800 text-zinc-500 text-[10px] uppercase">Stale</span>}
            <span className="text-amber-400 font-bold">⚔</span>
            <span className="text-white font-mono">{battle.level}</span>
            <span className="text-zinc-400">@ {battle.level_price.toLocaleString()}</span>
            {battle.confluence.length > 0 && (
              <span className="text-amber-400">+{battle.confluence.length}</span>
            )}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {pricePos?.vwap_deviation_sd != null && (
            <span className={`text-xs font-mono ${
              Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
              Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-amber-400' : 'text-zinc-500'
            }`}>
              VWAP {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
            </span>
          )}
          {activeBattle && (
            <button onClick={onDismissBattle}
              className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200">
              DISMISS
            </button>
          )}
        </div>
      </div>

      {/* Nearby Level Strip */}
      <NearbyLevelStrip above={nearbyLevels.above} below={nearbyLevels.below} />

      {/* 3-column grid: Levels | Footprint + Gauges | Context */}
      <div className="flex-1 grid grid-cols-[3fr_4fr_3fr] gap-3 min-h-0">
        {/* Left — Level Table */}
        <div className="border border-border bg-panel overflow-y-auto min-h-0">
          <LevelTable
            levels={levels}
            currentPrice={currentPrice}
            connected={connected}
            pricePos={pricePos}
            onLevelClick={onLevelClick}
          />
        </div>

        {/* Center — Footprint + Feature Gauges */}
        <div className="flex flex-col gap-2 min-h-0">
          {/* Footprint Chart — shrinks to give gauges room */}
          <div className="border border-border bg-panel min-h-[120px] max-h-[200px] overflow-hidden flex-shrink-0">
            <FootprintChart period={300} limit={8} refreshMs={10_000} />
          </div>

          {/* Feature Gauge Panel — takes remaining space, scrollable */}
          <div className={`border border-border bg-panel p-2 flex-1 overflow-y-auto min-h-0 ${isStale ? 'opacity-60' : ''}`}>
            {latestFeatures ? (
              <div className="space-y-1">
                <CollapsibleSection title="ORDERFLOW" count={ofGaugesFromFeatures.length} defaultOpen={true}>
                  {ofGaugesFromFeatures.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
                <CollapsibleSection title="TEMPORAL" count={temporalGauges.length} defaultOpen={true}>
                  {temporalGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
                <CollapsibleSection title="CANDLE" count={candleGauges.length} defaultOpen={true}>
                  {candleGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
                <CollapsibleSection title="SESSION" count={sessionGauges.length} defaultOpen={false}>
                  {sessionGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
                <CollapsibleSection title="MACRO" count={macroGauges.length} defaultOpen={false}>
                  {macroGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
                <CollapsibleSection title="LEVEL" count={levelGauges.length} defaultOpen={false}>
                  {levelGauges.map(g => <GaugeBar key={g.label} {...g} importance={importanceMap[g.label]} />)}
                </CollapsibleSection>
              </div>
            ) : hasGauges ? (
              /* Existing battle-based gauges as fallback */
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {ofGauges.length > 0 && (
                  <div>
                    <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">Orderflow</div>
                    <div className="space-y-0.5">{ofGauges.map(g => <GaugeBar key={g.label} {...g} />)}</div>
                  </div>
                )}
                {structGauges.length > 0 && (
                  <div>
                    <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">Structure</div>
                    <div className="space-y-0.5">{structGauges.map(g => <GaugeBar key={g.label} {...g} />)}</div>
                  </div>
                )}
                {mlGauges.length > 0 && (
                  <div className="col-span-2">
                    <div className="text-zinc-500 text-[10px] mb-1 uppercase tracking-wider">ML &amp; Context</div>
                    <div className="space-y-0.5">{mlGauges.map(g => <GaugeBar key={g.label} {...g} />)}</div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-zinc-600 text-xs text-center py-4">
                Approach a level to see ML features
              </div>
            )}
          </div>
        </div>

        {/* Right — Time & Sales + ML Prediction + Model Health + Context */}
        <div className="flex flex-col gap-2 min-h-0">
          <div className="border border-border bg-panel min-h-0 flex-1 overflow-hidden">
            <TickTape lastTick={lastTick} />
          </div>
          <div className="flex-shrink-0">
            <MlPredictionPanel prediction={latestPrediction} />
          </div>
          <div className="flex-shrink-0">
            <ModelHealthPanel />
          </div>
          <div className="border border-border bg-panel min-h-0 flex-shrink-0 overflow-y-auto max-h-[40%]">
            <ContextSidebar session={session} />
          </div>
        </div>
      </div>

      {/* Bottom panel — Trade Execution (collapsible) */}
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
