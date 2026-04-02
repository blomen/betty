import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/services/api';
import { useMarketStream } from '@/hooks/useMarketStream';
import { useLevelMonitor } from '@/hooks/useLevelMonitor';
import { useSound } from '@/hooks/useSound';
import { ChartPage } from './ChartPage';
import { DqnPage } from './DqnPage';
import type { ExpandedSession, MonitoredLevel, PositionRow, BattleScreenData } from '@/types/market';


interface Props {
  activeSubTab: 'tradingChart' | 'tradingDqn';
}

export function TradingContainer({ activeSubTab }: Props) {
  const [session, setSession] = useState<ExpandedSession | null>(null);
  const [positions] = useState<PositionRow[]>([]);
  const [loading, setLoading] = useState(false);

  const { lastTick, book, lastCandle, statistics, connected, esRef, connectionId } = useMarketStream();
  const { levels, activeBattle, battleActive, latestPrediction, latestFeatures, dqnInference, dismissBattle, switchBattleLevel, seedLevels } = useLevelMonitor(esRef, session, connectionId);
  const { unlock, play } = useSound();
  const prevBattle = useRef(false);
  const lastBattleRef = useRef<BattleScreenData | null>(null);

  // Cache battle data before dismiss for stale display
  useEffect(() => {
    if (activeBattle) lastBattleRef.current = activeBattle;
  }, [activeBattle]);

  // Play sound when battle activates
  useEffect(() => {
    if (battleActive && !prevBattle.current) {
      play('at_level');
    }
    prevBattle.current = battleActive;
  }, [battleActive, play]);

  const fetchData = useCallback(async () => {
    try {
      const [sessionRes, liveRes] = await Promise.all([
        api.getExpandedSession().catch(() => null),
        api.getLiveLevels().catch(() => null),
      ]);

      if (sessionRes && (sessionRes as any).status !== 'no_data' && sessionRes.session) {
        setSession(sessionRes);
      }

      if (liveRes?.levels?.length) {
        seedLevels(liveRes.levels);
      } else if (sessionRes?.session) {
        const built: MonitoredLevel[] = [];
        const lastPrice = sessionRes.price_position?.last_price ?? 0;
        const TICK = 0.25;
        for (const lv of sessionRes.levels ?? []) {
          const price = lv.price_low || 0;
          if (!price) continue;
          const name = lv.type || 'unknown';
          const cat = name.toLowerCase().includes('vwap') || name.toLowerCase().includes('sd') ? 'band'
            : ['pdh', 'pdl'].includes(name.toLowerCase()) ? 'prior'
            : name.toLowerCase().includes('overnight') || ['on_high', 'on_low'].includes(name.toLowerCase()) ? 'overnight'
            : name.toLowerCase().match(/swing|naked|ob|fvg/) ? 'structure'
            : 'session';
          built.push({ name, price, category: cat, status: 'watching', distance_ticks: lastPrice ? (lastPrice - price) / TICK : 0, cluster: [] });
        }
        const s = sessionRes.session;
        const bands: [string, string][] = [
          ['VWAP', 'vwap'], ['VWAP +1SD', 'vwap_1sd_upper'], ['VWAP -1SD', 'vwap_1sd_lower'],
          ['VWAP +2SD', 'vwap_2sd_upper'], ['VWAP -2SD', 'vwap_2sd_lower'],
          ['VWAP +3SD', 'vwap_3sd_upper'], ['VWAP -3SD', 'vwap_3sd_lower'],
        ];
        for (const [bName, key] of bands) {
          const val = (s as any)?.[key];
          if (val != null && !built.some(l => l.name.toLowerCase() === bName.toLowerCase())) {
            built.push({ name: bName, price: val, category: 'band', status: 'watching', distance_ticks: lastPrice ? (lastPrice - val) / TICK : 0, cluster: [] });
          }
        }
        if (built.length) seedLevels(built);
      }
    } finally {
      setLoading(false);
    }
  }, [seedLevels]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh session every 60s
  useEffect(() => {
    const refresh = async () => {
      const sessionRes = await api.getExpandedSession().catch(() => null);
      if (sessionRes) setSession(sessionRes);
    };
    const interval = setInterval(refresh, 60_000);
    return () => clearInterval(interval);
  }, []);

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
    try { await api.scalePosition(tradeId, pct); } catch (err) { console.error('Failed to scale:', err); }
  };
  const handleClose = async (tradeId: number) => {
    try { await api.closePosition(tradeId); } catch (err) { console.error('Failed to close:', err); }
  };

  const handleLevelClick = (levelName: string) => {
    switchBattleLevel(levelName);
  };

  const currentPrice = lastTick?.price ?? session?.price_position?.last_price ?? null;
  const pricePos = session?.price_position;

  if (loading) return <div className="text-zinc-500 text-sm p-4">Loading level monitor...</div>;

  return (
    <div className="flex flex-col flex-1 min-h-0" onClick={unlock}>
      <div className={`flex flex-col flex-1 min-h-0 ${activeSubTab === 'tradingChart' ? '' : 'hidden'}`}>
        <ChartPage
          lastTick={lastTick}
          book={book}
          lastCandle={lastCandle}
          connected={connected}
          session={session}
          statistics={statistics}
        />
      </div>
      <div className={`flex flex-col flex-1 min-h-0 ${activeSubTab === 'tradingDqn' ? '' : 'hidden'}`}>
        <DqnPage
          session={session}
          levels={levels}
          currentPrice={currentPrice}
          connected={connected}
          pricePos={pricePos}
          onLevelClick={handleLevelClick}
          activeBattle={activeBattle}
          lastBattle={lastBattleRef.current}
          battleActive={battleActive}
          onDismissBattle={dismissBattle}
          onTakeTrade={handleTakeTrade}
          positions={positions}
          onScale={handleScale}
          onClose={handleClose}
          lastTick={lastTick}
          book={book}
          latestPrediction={latestPrediction}
          latestFeatures={latestFeatures}
          dqnInference={dqnInference}
        />
      </div>
    </div>
  );
}
