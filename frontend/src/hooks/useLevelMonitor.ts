import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  MonitoredLevel, BattleScreenData,
  LevelTouchedEvent, LevelApproachingEvent, OrderflowUpdateEvent, LevelRejectedEvent,
} from '@/types/market';

interface LevelMonitorState {
  levels: MonitoredLevel[];
  activeBattle: BattleScreenData | null;
  battleActive: boolean;
}

export function useLevelMonitor(
  esRef: React.RefObject<EventSource | null>,
  sessionData: { session: any; macro: any; ml_day_type: any; ml_day_type_confidence: any } | null,
) {
  const [state, setState] = useState<LevelMonitorState>({
    levels: [],
    activeBattle: null,
    battleActive: false,
  });

  const levelStatusRef = useRef<Map<string, MonitoredLevel>>(new Map());

  const seedLevels = useCallback((levels: MonitoredLevel[]) => {
    for (const l of levels) {
      if (!levelStatusRef.current.has(l.name)) {
        levelStatusRef.current.set(l.name, l);
      }
    }
    setState(prev => ({
      ...prev,
      levels: Array.from(levelStatusRef.current.values())
        .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
    }));
  }, []);

  useEffect(() => {
    const es = esRef.current;
    if (!es) return;

    const onApproaching = (e: MessageEvent) => {
      const data: LevelApproachingEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        name: data.level,
        price: data.level_price,
        category: data.category as any,
        status: 'approaching',
        distance_ticks: data.distance_ticks,
        cluster: [],
      });
      setState(prev => ({
        ...prev,
        levels: Array.from(levelStatusRef.current.values())
          .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
      }));
    };

    const onTouched = (e: MessageEvent) => {
      const data: LevelTouchedEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        name: data.level,
        price: data.level_price,
        category: data.category as any,
        status: 'at_level',
        distance_ticks: 0,
        cluster: data.confluence,
      });

      const allLevels = Array.from(levelStatusRef.current.values());
      const above = allLevels
        .filter(l => l.price > data.level_price)
        .sort((a, b) => a.price - b.price)
        .slice(0, 3)
        .map(l => ({ name: l.name, price: l.price }));
      const below = allLevels
        .filter(l => l.price < data.level_price)
        .sort((a, b) => b.price - a.price)
        .slice(0, 3)
        .map(l => ({ name: l.name, price: l.price }));

      const battle: BattleScreenData = {
        level: data.level,
        level_price: data.level_price,
        category: data.category,
        price: data.price,
        confluence: data.confluence,
        orderflow: data.orderflow,
        structure: sessionData?.session || null,
        ml: sessionData ? {
          day_type: sessionData.ml_day_type,
          day_type_confidence: sessionData.ml_day_type_confidence,
        } : null,
        macro: sessionData?.macro || null,
        suggested_entry: data.price,
        suggested_stop: below[0]?.price || data.price - 10,
        targets: above.length ? above : below,
      };

      setState(prev => ({
        ...prev,
        levels: Array.from(levelStatusRef.current.values())
          .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
        activeBattle: battle,
        battleActive: true,
      }));
    };

    const onOrderflow = (e: MessageEvent) => {
      const data: OrderflowUpdateEvent = JSON.parse(e.data);
      setState(prev => {
        if (!prev.activeBattle) return prev;
        return {
          ...prev,
          activeBattle: {
            ...prev.activeBattle,
            orderflow: data.orderflow,
            price: data.price,
          },
        };
      });
    };

    const onRejected = (e: MessageEvent) => {
      const data: LevelRejectedEvent = JSON.parse(e.data);
      levelStatusRef.current.set(data.level, {
        ...levelStatusRef.current.get(data.level)!,
        status: 'watching',
      });
      setState(prev => {
        const newState: LevelMonitorState = {
          ...prev,
          levels: Array.from(levelStatusRef.current.values())
            .sort((a, b) => Math.abs(a.distance_ticks) - Math.abs(b.distance_ticks)),
          activeBattle: prev.activeBattle,
          battleActive: prev.battleActive,
        };
        if (prev.activeBattle?.level === data.level) {
          newState.activeBattle = null;
          newState.battleActive = false;
        }
        return newState;
      });
    };

    const onContext = (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setState(prev => {
        if (!prev.activeBattle || prev.activeBattle.level !== data.level) return prev;
        return {
          ...prev,
          activeBattle: {
            ...prev.activeBattle,
            ml: data.ml,
            macro: data.macro,
          },
        };
      });
    };

    const onPositionTarget = (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setState(prev => ({
        ...prev,
        activeBattle: {
          level: data.target_name,
          level_price: data.target_price,
          category: 'target',
          price: data.price,
          confluence: [],
          orderflow: data.orderflow,
          structure: sessionData?.session || null,
          ml: prev.activeBattle?.ml || null,
          macro: prev.activeBattle?.macro || null,
          suggested_entry: data.price,
          suggested_stop: data.price,
          targets: [],
        },
        battleActive: true,
      }));
    };

    es.addEventListener('level_approaching', onApproaching);
    es.addEventListener('level_touched', onTouched);
    es.addEventListener('orderflow_update', onOrderflow);
    es.addEventListener('level_rejected', onRejected);
    es.addEventListener('level_context', onContext);
    es.addEventListener('position_at_target', onPositionTarget);

    return () => {
      es.removeEventListener('level_approaching', onApproaching);
      es.removeEventListener('level_touched', onTouched);
      es.removeEventListener('orderflow_update', onOrderflow);
      es.removeEventListener('level_rejected', onRejected);
      es.removeEventListener('level_context', onContext);
      es.removeEventListener('position_at_target', onPositionTarget);
    };
  }, [esRef, sessionData]);

  const dismissBattle = useCallback(() => {
    setState(prev => ({ ...prev, activeBattle: null, battleActive: false }));
  }, []);

  const switchBattleLevel = useCallback((levelName: string) => {
    const level = levelStatusRef.current.get(levelName);
    if (!level || level.status !== 'at_level') return;
    setState(prev => prev.activeBattle ? {
      ...prev,
      activeBattle: { ...prev.activeBattle, level: level.name, level_price: level.price },
    } : prev);
  }, []);

  return {
    levels: state.levels,
    activeBattle: state.activeBattle,
    battleActive: state.battleActive,
    dismissBattle,
    switchBattleLevel,
    seedLevels,
  };
}
