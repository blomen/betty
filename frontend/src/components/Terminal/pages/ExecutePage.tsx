import { useMemo } from 'react';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { NearbyLevelStrip } from './NearbyLevelStrip';
import { TradeActionBar } from './TradeActionBar';
import { GaugeBar } from './GaugeBar';
import { PositionManager } from './PositionManager';
import { orderflowToGauges, structureToGauges, mlToGauges } from './gaugeHelpers';
import type { MonitoredLevel, PositionRow, BattleScreenData } from '@/types/market';

interface Props {
  activeBattle: BattleScreenData | null;
  lastBattle: BattleScreenData | null;
  battleActive: boolean;
  levels: MonitoredLevel[];
  currentPrice: number | null;
  connected: boolean;
  onDismissBattle: () => void;
  onTakeTrade: (direction: 'long' | 'short', entry: number, stop: number, targets: { name: string; price: number }[]) => void;
  positions: PositionRow[];
  onScale: (tradeId: number, pct: number) => void;
  onClose: (tradeId: number) => void;
}

export function ExecutePage({
  activeBattle, lastBattle, levels, currentPrice, connected,
  onDismissBattle, onTakeTrade,
  positions, onScale, onClose,
}: Props) {
  const cp = currentPrice ?? 0;
  const aboveLevels = levels.filter(l => l.price > cp).sort((a, b) => a.price - b.price);
  const belowLevels = levels.filter(l => l.price <= cp).sort((a, b) => b.price - a.price);

  const nearbyLevels = useMemo(() => ({
    above: aboveLevels.slice(0, 3).map(l => ({ name: l.name, price: l.price })),
    below: belowLevels.slice(0, 3).map(l => ({ name: l.name, price: l.price })),
  }), [aboveLevels, belowLevels]);

  // Use active battle, fall back to stale last battle
  const battle = activeBattle ?? lastBattle;
  const isStale = !activeBattle && !!lastBattle;

  const ofGauges = battle?.orderflow ? orderflowToGauges(battle.orderflow) : [];
  const structGauges = battle ? structureToGauges(battle.structure) : [];
  const mlGauges = battle ? mlToGauges(battle.ml, battle.macro, battle.confluence) : [];

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header — same pattern as sports pages */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="tradingExecute" color={TAB_COLORS.tradingExecute} size={16} />
          Execute
          {battle && (
            <span className="flex items-center gap-2 ml-2 text-xs font-normal">
              {isStale && (
                <span className="px-1.5 py-0.5 bg-zinc-800 text-zinc-500 text-[10px] uppercase">Stale</span>
              )}
              <span className="text-cyan-400 font-bold">⚔</span>
              <span className="text-white font-mono">{battle.level}</span>
              <span className="text-zinc-400">@ {battle.level_price.toLocaleString()}</span>
              {battle.confluence.length > 0 && (
                <span className="text-amber-400">+{battle.confluence.length} confluence</span>
              )}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-[10px]">
            <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
            <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
          </div>
          {currentPrice != null && (
            <span className="text-xs font-mono text-zinc-300">
              NQ {currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          )}
          {activeBattle && (
            <button onClick={onDismissBattle}
              className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors">
              DISMISS
            </button>
          )}
        </div>
      </div>

      {/* Battle gauges or empty state */}
      {battle ? (
        <div className={`flex flex-col flex-1 min-h-0 ${isStale ? 'opacity-60' : ''}`}>
          {/* Gauge grid */}
          <div className="grid grid-cols-2 gap-4 flex-1 overflow-y-auto p-3">
            {/* Left: Orderflow gauges */}
            <div>
              <div className="text-zinc-500 text-[10px] mb-2 uppercase tracking-wider">Orderflow</div>
              <div className="space-y-1">
                {ofGauges.map(g => <GaugeBar key={g.label} {...g} />)}
              </div>
            </div>
            {/* Right: Structure + ML gauges */}
            <div>
              <div className="text-zinc-500 text-[10px] mb-2 uppercase tracking-wider">Structure</div>
              <div className="space-y-1">
                {structGauges.map(g => <GaugeBar key={g.label} {...g} />)}
              </div>
              <div className="text-zinc-500 text-[10px] mb-2 mt-3 uppercase tracking-wider">ML & Context</div>
              <div className="space-y-1">
                {mlGauges.map(g => <GaugeBar key={g.label} {...g} />)}
              </div>
            </div>
          </div>

          {/* Nearby level strip */}
          <NearbyLevelStrip above={nearbyLevels.above} below={nearbyLevels.below} />

          {/* Trade action bar — only when live battle */}
          {activeBattle && <TradeActionBar battle={activeBattle} onTrade={onTakeTrade} />}
        </div>
      ) : (
        /* Empty state — no battle and no stale data */
        <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 gap-4">
          <div className="text-lg font-mono">Watching for level trigger</div>
          {currentPrice != null && (
            <div className="text-sm font-mono text-cyan-400">NQ {currentPrice.toFixed(2)}</div>
          )}
          <div className="flex gap-8 text-xs">
            <div>
              <div className="text-zinc-600 mb-1">Above</div>
              {aboveLevels.slice(0, 3).map(l => (
                <div key={l.name} className="text-zinc-400">{l.name} @ {l.price.toFixed(2)}</div>
              ))}
            </div>
            <div>
              <div className="text-zinc-600 mb-1">Below</div>
              {belowLevels.slice(0, 3).map(l => (
                <div key={l.name} className="text-zinc-400">{l.name} @ {l.price.toFixed(2)}</div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Position Manager — always at bottom when active */}
      {positions.length > 0 && (
        <div className="flex-shrink-0 border border-zinc-800 bg-zinc-900/30 p-2">
          <PositionManager
            positions={positions}
            onScale={onScale}
            onClose={onClose}
            onHold={() => {}}
            onUpdateStop={() => {}}
          />
        </div>
      )}
    </div>
  );
}
