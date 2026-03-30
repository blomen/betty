import { useState, useEffect, useRef, useCallback } from 'react';
import { NeuralNetworkSVG } from './NeuralNetworkSVG';
import { DQN_SEGMENTS, HIDDEN_LAYERS } from './dqnConfig';
import type {
  ExpandedSession, MonitoredLevel, PositionRow, BattleScreenData,
  PricePosition, StreamTickEvent, StreamBookEvent, MlPrediction,
  MlFeatureSnapshot, DQNInferenceEvent, DQNConnection,
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
  dqnInference: DQNInferenceEvent | null;
}

// ── Demo inference generator ──
function generateDemoInference(battle: BattleScreenData): DQNInferenceEvent {
  const totalInputs = DQN_SEGMENTS[DQN_SEGMENTS.length - 1].end;
  const rng = () => Math.random();
  const randn = () => (rng() + rng() + rng() - 1.5) * 0.8;

  const inputs: number[] = [];
  for (let i = 0; i < totalInputs; i++) {
    const seg = DQN_SEGMENTS.find(s => i >= s.start && i < s.end);
    const boost = seg && (seg.name === 'LEVEL TYPE' || seg.name === 'ORDERFLOW') ? 1.5 : 0.7;
    inputs.push(Math.max(-1, Math.min(1, randn() * boost)));
  }

  const makeActs = (size: number) =>
    Array.from({ length: size }, () => Math.max(0, randn() * 0.8));

  const layer1 = makeActs(HIDDEN_LAYERS[0]); // 256
  const layer2 = makeActs(HIDDEN_LAYERS[1]); // 256
  const layer3 = makeActs(HIDDEN_LAYERS[2]); // 128
  const layer4 = makeActs(HIDDEN_LAYERS[3]); // 64

  const raw = [randn() * 0.5, randn() * 0.5, randn() * 0.3];
  const winIdx = Math.floor(rng() * 2);
  raw[winIdx] += 0.4;
  const actions = ['CONTINUATION', 'REVERSAL', 'SKIP'] as const;

  const makeConns = (fromSize: number, toSize: number, count: number): DQNConnection[] => {
    const conns: DQNConnection[] = [];
    for (let c = 0; c < count; c++) {
      conns.push({
        from_idx: Math.floor(rng() * fromSize),
        to_idx: Math.floor(rng() * toSize),
        strength: 0.1 + rng() * 0.8,
        sign: rng() > 0.3 ? 1 : -1,
      });
    }
    return conns;
  };

  return {
    type: 'dqn_inference',
    trigger: 'touched',
    level: battle.level,
    level_price: battle.level_price,
    inputs,
    activations: { layer1, layer2, layer3, layer4 },
    q_values: raw,
    action: actions[winIdx],
    connections: {
      input_l1: makeConns(totalInputs, HIDDEN_LAYERS[0], 40),
      l1_l2: makeConns(HIDDEN_LAYERS[0], HIDDEN_LAYERS[1], 30),
      l2_l3: makeConns(HIDDEN_LAYERS[1], HIDDEN_LAYERS[2], 25),
      l3_l4: makeConns(HIDDEN_LAYERS[2], HIDDEN_LAYERS[3], 20),
      l4_output: makeConns(HIDDEN_LAYERS[3], 3, 15),
    },
    timestamp: Date.now(),
  };
}

function useDemoInference(
  realInference: DQNInferenceEvent | null,
  battle: BattleScreenData | null,
): DQNInferenceEvent | null {
  const [demo, setDemo] = useState<DQNInferenceEvent | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const refresh = useCallback(() => {
    if (battle) setDemo(generateDemoInference(battle));
  }, [battle]);

  useEffect(() => {
    if (realInference) {
      clearInterval(intervalRef.current);
      setDemo(null);
      return;
    }
    if (battle) {
      refresh();
      intervalRef.current = setInterval(refresh, 2000);
      return () => clearInterval(intervalRef.current);
    } else {
      setDemo(null);
    }
  }, [realInference, battle, refresh]);

  return realInference ?? demo;
}

export function VectorsPage({
  session: _session, levels: _levels, currentPrice: _currentPrice, connected: _connected,
  pricePos: _pricePos, onLevelClick: _onLevelClick,
  activeBattle, lastBattle, onDismissBattle: _onDismissBattle, onTakeTrade: _onTakeTrade,
  positions: _positions, onScale: _onScale, onClose: _onClose,
  lastTick: _lastTick, book: _book, latestPrediction: _latestPrediction,
  latestFeatures: _latestFeatures, dqnInference,
}: Props) {
  const effectiveInference = useDemoInference(dqnInference, activeBattle ?? lastBattle);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex-1 min-h-0">
        <NeuralNetworkSVG dqnInference={effectiveInference} />
      </div>
    </div>
  );
}
