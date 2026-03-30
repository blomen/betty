import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/services/api';
import { useMarketStream } from '@/hooks/useMarketStream';
import { useLevelMonitor } from '@/hooks/useLevelMonitor';
import { useSound } from '@/hooks/useSound';
import { L1Page } from './L1Page';
import { VectorsPage } from './VectorsPage';
import type { ExpandedSession, MonitoredLevel, PositionRow, BattleScreenData, OrderflowSnapshot } from '@/types/market';

// ---- Demo data for testing when backend is offline ----
const DEMO_PRICE = 24927.50;

const DEMO_ORDERFLOW: OrderflowSnapshot = {
  long: {
    delta: 1250, delta_aligned: true, delta_divergence: false, delta_unwind: false,
    cvd: 3200, cvd_trend: 'rising',
    vsa_absorption: true, tick_vol_accelerating: true,
    trapped_traders: true, passive_active_ratio: 2.3,
    big_trades_count: 7, big_trades_net_delta: 1800,
    stop_run_detected: false,
    imbalance_ratio_max: 4.2, stacked_imbalance_count: 3, stacked_imbalance_direction: 'buy',
  },
  short: {
    delta: -800, delta_aligned: false, delta_divergence: true, delta_unwind: false,
    cvd: -1500, cvd_trend: 'falling',
    vsa_absorption: false, tick_vol_accelerating: false,
    trapped_traders: false, passive_active_ratio: 1.1,
    big_trades_count: 3, big_trades_net_delta: -600,
    stop_run_detected: false,
    imbalance_ratio_max: 2.1, stacked_imbalance_count: 1, stacked_imbalance_direction: 'sell',
  },
};

const DEMO_LEVELS: MonitoredLevel[] = [
  { name: 'VWAP +2SD', price: 25085.00, category: 'band', status: 'watching', distance_ticks: -630, cluster: [] },
  { name: 'ONH', price: 24995.75, category: 'overnight', status: 'watching', distance_ticks: -273, cluster: [] },
  { name: 'VWAP +1SD', price: 24978.25, category: 'band', status: 'watching', distance_ticks: -203, cluster: [] },
  { name: 'PDH', price: 24960.50, category: 'prior', status: 'watching', distance_ticks: -132, cluster: ['Weekly VAH'] },
  { name: 'VAH', price: 24945.00, category: 'session', status: 'approaching', distance_ticks: -70, cluster: [] },
  { name: 'POC', price: 24930.25, category: 'session', status: 'at_level', distance_ticks: -11, cluster: ['Dev POC'] },
  { name: 'VWAP', price: 24912.50, category: 'band', status: 'watching', distance_ticks: 60, cluster: [] },
  { name: 'VAL', price: 24898.00, category: 'session', status: 'watching', distance_ticks: 118, cluster: [] },
  { name: 'PDL', price: 24870.25, category: 'prior', status: 'watching', distance_ticks: 229, cluster: ['Naked POC'] },
  { name: 'ONL', price: 24842.00, category: 'overnight', status: 'watching', distance_ticks: 342, cluster: [] },
  { name: 'VWAP -1SD', price: 24825.75, category: 'band', status: 'watching', distance_ticks: 407, cluster: [] },
  { name: 'Swing Low', price: 24728.50, category: 'structure', status: 'watching', distance_ticks: 796, cluster: [] },
  { name: 'VWAP -2SD', price: 24685.00, category: 'band', status: 'watching', distance_ticks: 970, cluster: [] },
];

const DEMO_SESSION: ExpandedSession = {
  session: {
    date: new Date().toISOString().slice(0, 10),
    symbol: 'NQ',
    market_type: 'trending_up',
    opening_type: 'OD',
    ib_high: 24955.50,
    ib_low: 24890.25,
    distribution_type: 'p_shape',
    poor_high: false,
    poor_low: true,
    vwap: 24912.50,
    swing_structure: 'up',
    single_prints: [24935, 24940],
  } as any,
  macro: {
    regime: 'risk_on',
    vix: 16.8,
    dxy: 99.42,
  } as any,
  structure: {} as any,
  profiles: {
    session: { poc: 24930.25, vah: 24945.00, val: 24898.00 },
    developing_poc: 24932.00,
    developing_poc_direction: 'up',
  } as any,
  levels: [],
  price_position: {
    last_price: DEMO_PRICE,
    vwap_deviation_sd: 0.35,
  } as any,
  ml_day_type: 'trend_day',
  ml_day_type_confidence: 72,
};

const DEMO_BATTLE: BattleScreenData = {
  level: 'POC',
  level_price: 24930.25,
  category: 'session',
  price: DEMO_PRICE,
  confluence: ['Dev POC', 'Prior day close'],
  orderflow: DEMO_ORDERFLOW,
  structure: DEMO_SESSION.session,
  ml: { day_type: 'trend_day', day_type_confidence: 72 },
  macro: DEMO_SESSION.macro,
  suggested_entry: 24929.00,
  suggested_stop: 24918.50,
  targets: [
    { name: 'T1 (1R)', price: 24939.50 },
    { name: 'T2 (2R)', price: 24950.00 },
    { name: 'T3 (3R)', price: 24960.50 },
  ],
};

// ---------------------------------------------------

interface Props {
  activeSubTab: 'tradingL1' | 'tradingVectors';
}

export function TradingContainer({ activeSubTab }: Props) {
  const [session, setSession] = useState<ExpandedSession | null>(DEMO_SESSION);
  const [positions] = useState<PositionRow[]>([]);
  const [loading, setLoading] = useState(false);

  const { lastTick, book, lastCandle, connected, esRef } = useMarketStream();
  const { levels, activeBattle, battleActive, latestPrediction, latestFeatures, dqnInference, dismissBattle, switchBattleLevel, seedLevels } = useLevelMonitor(esRef, session);
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

      // Use live data if session has real content, otherwise demo fallback
      if (sessionRes && (sessionRes as any).status !== 'no_data' && sessionRes.session) {
        setSession(sessionRes);
      } else {
        // Fetch real VP data so sidebar doesn't show stale demo values
        const [dVP, wVP, mVP] = await Promise.all([
          api.getVolumeProfile('NQ', 'session').catch(() => null),
          api.getVolumeProfile('NQ', 'weekly').catch(() => null),
          api.getVolumeProfile('NQ', 'monthly').catch(() => null),
        ]);
        const realProfiles: any = { ...DEMO_SESSION.profiles };
        if (dVP && dVP.poc) realProfiles.session = { poc: dVP.poc, vah: dVP.vah, val: dVP.val };
        if (wVP && wVP.poc) realProfiles.weekly = { poc: wVP.poc, vah: wVP.vah, val: wVP.val };
        if (mVP && mVP.poc) realProfiles.monthly = { poc: mVP.poc, vah: mVP.vah, val: mVP.val };
        setSession({ ...DEMO_SESSION, profiles: realProfiles });
        seedLevels(DEMO_LEVELS);
        lastBattleRef.current = DEMO_BATTLE;
        return;
      }

      if (liveRes?.levels?.length) {
        seedLevels(liveRes.levels);
      } else {
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
      <div className={`flex flex-col flex-1 min-h-0 ${activeSubTab === 'tradingL1' ? '' : 'hidden'}`}>
        <L1Page
          lastTick={lastTick}
          book={book}
          lastCandle={lastCandle}
          connected={connected}
          session={session}
        />
      </div>
      <div className={`flex flex-col flex-1 min-h-0 ${activeSubTab === 'tradingVectors' ? '' : 'hidden'}`}>
        <VectorsPage
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
