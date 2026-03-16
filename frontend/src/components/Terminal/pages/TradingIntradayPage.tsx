import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import { useLevelMonitor } from '@/hooks/useLevelMonitor';
import { useSound } from '@/hooks/useSound';
import { LevelMonitorTable } from './LevelMonitorTable';
import { BattleScreen } from './BattleScreen';
import { PositionManager } from './PositionManager';
import type { ExpandedSession, PositionRow } from '@/types/market';

export function TradingIntradayPage() {
  const [session, setSession] = useState<ExpandedSession | null>(null);
  const [positions] = useState<PositionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const { lastTick, connected, esRef } = useMarketStream();
  const { levels, activeBattle, battleActive, dismissBattle, switchBattleLevel } = useLevelMonitor(esRef, session);
  const { unlock, play } = useSound();
  const prevBattle = useRef(false);

  // Play sound when battle screen activates
  useEffect(() => {
    if (battleActive && !prevBattle.current) {
      play('at_level');
    }
    prevBattle.current = battleActive;
  }, [battleActive, play]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [sessionRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getLiveLevels().catch(() => null),
      ]);
      if (sessionRes) setSession(sessionRes);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh session every 30s
  useEffect(() => {
    const refresh = async () => {
      const sessionRes = await api.getExpandedSession().catch(() => null);
      if (sessionRes) setSession(sessionRes);
    };
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await api.triggerMarketCompute();
      const sessionRes = await api.getExpandedSession().catch(() => null);
      if (sessionRes) setSession(sessionRes);
      setLastRefresh(new Date());
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleTakeTrade = async (
    direction: 'long' | 'short',
    entry: number,
    stop: number,
    targets: { name: string; price: number }[],
  ) => {
    try {
      await api.createTrade({
        instrument: 'NQ',
        direction,
        setup_type: activeBattle?.level || 'manual',
        entry_price: entry,
        stop_price: stop,
        targets: targets.map((t) => ({ price: t.price, contracts: 1, label: t.name })),
        contracts: 2,
        notes: `Entry at ${activeBattle?.level} level`,
      });
    } catch (err) {
      console.error('Failed to create trade:', err);
    }
  };

  const handleScale = async (tradeId: number, pct: number) => {
    try {
      await api.scalePosition(tradeId, pct);
    } catch (err) {
      console.error('Failed to scale position:', err);
    }
  };

  const handleClose = async (tradeId: number) => {
    try {
      await api.closePosition(tradeId);
    } catch (err) {
      console.error('Failed to close position:', err);
    }
  };

  const currentPrice = lastTick?.price ?? session?.price_position?.last_price ?? null;
  const pricePos = session?.price_position;

  if (loading) return <div className="text-zinc-500 text-sm p-4">Loading level monitor...</div>;

  return (
    <div className="flex flex-col h-full gap-2" onClick={unlock}>

      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap border-b border-zinc-800 pb-2 px-1">
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
          {lastRefresh && `${lastRefresh.toLocaleTimeString()}`}
        </span>
        <button onClick={handleRefresh} disabled={isRefreshing}
          className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40 transition-colors">
          {isRefreshing ? 'Computing...' : 'Refresh'}
        </button>
      </div>

      {/* Level Monitor Table */}
      <div className="border border-zinc-800 rounded bg-zinc-900/30 overflow-y-auto"
        style={{ maxHeight: battleActive ? '140px' : '400px' }}>
        <LevelMonitorTable
          levels={levels}
          currentPrice={currentPrice}
          onLevelClick={switchBattleLevel}
          compact={battleActive}
        />
      </div>

      {/* Battle Screen */}
      {activeBattle && (
        <BattleScreen
          data={activeBattle}
          onTrade={handleTakeTrade}
          onDismiss={dismissBattle}
        />
      )}

      {/* Position Manager */}
      {positions.length > 0 && (
        <div className="border border-zinc-800 rounded bg-zinc-900/30 p-2">
          <PositionManager
            positions={positions}
            onScale={handleScale}
            onClose={handleClose}
            onHold={() => {}}
            onUpdateStop={() => {}}
          />
        </div>
      )}

      {/* Empty state when no levels loaded */}
      {levels.length === 0 && !battleActive && (
        <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
          No levels loaded. Click Refresh to compute session.
        </div>
      )}
    </div>
  );
}
