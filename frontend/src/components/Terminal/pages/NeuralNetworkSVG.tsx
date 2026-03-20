// NeuralNetworkSVG.tsx — DQN 107→128→128→64→3 visualization
import { useMemo } from 'react';
import {
  DQN_INPUTS, DQN_SEGMENTS, HIDDEN_LAYERS, ACTION_NAMES, ACTION_COLORS,
  getSegmentColor,
} from './dqnConfig';
import type { DQNInferenceEvent } from '@/types/market';

interface Props {
  dqnInference: DQNInferenceEvent | null;
}

// Layout constants
const INPUT_X = 180;
const LAYER1_X = 540;
const LAYER2_X = 780;
const LAYER3_X = 1000;
const OUTPUT_X = 1260;
const NODE_R = 4;
const HIDDEN_DOT_R = 3;
const OUTPUT_R = 16;
const ROW_H = 10;       // tight for 107 nodes
const SEGMENT_GAP = 8;
const TOP_PAD = 35;

// Layer display colors
const LAYER_COLORS = ['#06b6d4', '#8b5cf6', '#a78bfa'] as const;

// How many representative dots to show per hidden layer
const HIDDEN_SAMPLES = 40;

export function NeuralNetworkSVG({ dqnInference }: Props) {
  // ── Static: input node positions ──
  const { inputNodePositions, totalHeight } = useMemo(() => {
    const positions: number[] = [];
    let y = TOP_PAD;
    let lastSeg = '';
    for (const def of DQN_INPUTS) {
      if (def.segment !== lastSeg) {
        if (lastSeg) y += SEGMENT_GAP;
        lastSeg = def.segment;
      }
      positions.push(y);
      y += ROW_H;
    }
    return { inputNodePositions: positions, totalHeight: y + 40 };
  }, []);

  // ── Static: segment label positions ──
  const segmentLabels = useMemo(() => {
    return DQN_SEGMENTS.map(seg => {
      const startIdx = seg.start;
      const endIdx = seg.end - 1;
      const yStart = inputNodePositions[startIdx] ?? TOP_PAD;
      const yEnd = inputNodePositions[endIdx] ?? TOP_PAD;
      return { seg, yStart, yEnd, yMid: (yStart + yEnd) / 2 };
    });
  }, [inputNodePositions]);

  // ── Static: hidden layer dot positions ──
  const hiddenLayerDots = useMemo(() => {
    return HIDDEN_LAYERS.map((size, layerIdx) => {
      const step = Math.max(1, Math.floor(size / HIDDEN_SAMPLES));
      const sampledIndices: number[] = [];
      for (let i = 0; i < size; i += step) {
        sampledIndices.push(i);
        if (sampledIndices.length >= HIDDEN_SAMPLES) break;
      }
      const count = sampledIndices.length;
      const spread = totalHeight * 0.7;
      const yStart = totalHeight * 0.15;
      return sampledIndices.map((srcIdx, j) => ({
        srcIdx,
        y: yStart + (j / Math.max(count - 1, 1)) * spread,
        layerIdx,
      }));
    });
  }, [totalHeight]);

  // ── Static: output node positions ──
  const outputPositions = useMemo(() => {
    const count = ACTION_NAMES.length;
    const spread = 120;
    const center = totalHeight / 2;
    return ACTION_NAMES.map((name, i) => ({
      name,
      color: ACTION_COLORS[i],
      y: center + (i - (count - 1) / 2) * spread,
    }));
  }, [totalHeight]);

  // ── Dynamic: node brightnesses ──
  const inputBrightnesses = useMemo(() => {
    return DQN_INPUTS.map(def => {
      const val = dqnInference?.inputs[def.index] ?? 0;
      return Math.max(0.15, Math.min(1, Math.abs(val)));
    });
  }, [dqnInference]);

  const hiddenBrightnesses = useMemo(() => {
    return HIDDEN_LAYERS.map((_, layerIdx) => {
      const actKey = (['layer1', 'layer2', 'layer3'] as const)[layerIdx];
      const acts = dqnInference?.activations[actKey] ?? [];
      return hiddenLayerDots[layerIdx].map(dot => {
        const val = acts[dot.srcIdx] ?? 0;
        return Math.max(0.1, Math.min(1, Math.abs(val)));
      });
    });
  }, [dqnInference, hiddenLayerDots]);

  const winnerIdx = useMemo(() => {
    if (!dqnInference) return -1;
    const qv = dqnInference.q_values;
    return qv.indexOf(Math.max(...qv));
  }, [dqnInference]);

  // ── Dynamic: connection lines ──
  const connectionLines = useMemo(() => {
    if (!dqnInference) return [];
    const lines: Array<{
      x1: number; y1: number; x2: number; y2: number;
      color: string; strokeWidth: number; opacity: number;
    }> = [];

    const layer1Ys = hiddenLayerDots[0].map(d => d.y);
    const layer2Ys = hiddenLayerDots[1].map(d => d.y);
    const layer3Ys = hiddenLayerDots[2].map(d => d.y);

    // input → layer1
    for (const conn of dqnInference.connections.input_l1) {
      const fromY = inputNodePositions[conn.from_idx];
      if (fromY == null) continue;
      const toSlot = hiddenLayerDots[0].findIndex(d => d.srcIdx === conn.to_idx);
      if (toSlot === -1) continue;
      const toY = layer1Ys[toSlot];
      lines.push({
        x1: INPUT_X + NODE_R, y1: fromY,
        x2: LAYER1_X - HIDDEN_DOT_R, y2: toY,
        color: conn.sign === 1 ? LAYER_COLORS[0] : '#ef4444',
        strokeWidth: Math.max(0.4, conn.strength * 3),
        opacity: Math.max(0.05, Math.min(0.8, conn.strength)),
      });
    }

    // layer1 → layer2
    for (const conn of dqnInference.connections.l1_l2) {
      const fromSlot = hiddenLayerDots[0].findIndex(d => d.srcIdx === conn.from_idx);
      if (fromSlot === -1) continue;
      const toSlot = hiddenLayerDots[1].findIndex(d => d.srcIdx === conn.to_idx);
      if (toSlot === -1) continue;
      lines.push({
        x1: LAYER1_X + HIDDEN_DOT_R, y1: layer1Ys[fromSlot],
        x2: LAYER2_X - HIDDEN_DOT_R, y2: layer2Ys[toSlot],
        color: conn.sign === 1 ? LAYER_COLORS[1] : '#ef4444',
        strokeWidth: Math.max(0.4, conn.strength * 3),
        opacity: Math.max(0.05, Math.min(0.8, conn.strength)),
      });
    }

    // layer2 → layer3
    for (const conn of dqnInference.connections.l2_l3) {
      const fromSlot = hiddenLayerDots[1].findIndex(d => d.srcIdx === conn.from_idx);
      if (fromSlot === -1) continue;
      const toSlot = hiddenLayerDots[2].findIndex(d => d.srcIdx === conn.to_idx);
      if (toSlot === -1) continue;
      lines.push({
        x1: LAYER2_X + HIDDEN_DOT_R, y1: layer2Ys[fromSlot],
        x2: LAYER3_X - HIDDEN_DOT_R, y2: layer3Ys[toSlot],
        color: conn.sign === 1 ? LAYER_COLORS[2] : '#ef4444',
        strokeWidth: Math.max(0.4, conn.strength * 3),
        opacity: Math.max(0.05, Math.min(0.8, conn.strength)),
      });
    }

    // layer3 → output
    for (const conn of dqnInference.connections.l3_output) {
      const fromSlot = hiddenLayerDots[2].findIndex(d => d.srcIdx === conn.from_idx);
      if (fromSlot === -1) continue;
      const outNode = outputPositions[conn.to_idx];
      if (!outNode) continue;
      lines.push({
        x1: LAYER3_X + HIDDEN_DOT_R, y1: layer3Ys[fromSlot],
        x2: OUTPUT_X - OUTPUT_R, y2: outNode.y,
        color: conn.sign === 1 ? outNode.color : '#ef4444',
        strokeWidth: Math.max(0.4, conn.strength * 3),
        opacity: Math.max(0.05, Math.min(0.8, conn.strength)),
      });
    }

    return lines;
  }, [dqnInference, inputNodePositions, hiddenLayerDots, outputPositions]);

  // Status label
  const statusLabel = useMemo(() => {
    if (!dqnInference) return 'WAITING FOR LEVEL';
    if (dqnInference.trigger === 'approaching') return `APPROACHING ${dqnInference.level}`;
    return `AT LEVEL ${dqnInference.level}`;
  }, [dqnInference]);

  // Hidden layer X positions
  const layerXs = [LAYER1_X, LAYER2_X, LAYER3_X];

  return (
    <svg
      viewBox={`0 0 1400 ${totalHeight}`}
      className="w-full"
      preserveAspectRatio="xMidYMin meet"
    >
      <defs>
        <filter id="nn-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* ── Connection lines ── */}
      {connectionLines.map((c, i) => (
        <line
          key={`conn-${i}`}
          x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2}
          stroke={c.color}
          strokeWidth={c.strokeWidth}
          opacity={c.opacity}
          className="transition-all duration-300"
        />
      ))}

      {/* ── Segment group bars + labels (left margin) ── */}
      {segmentLabels.map(({ seg, yStart, yEnd, yMid }) => (
        <g key={`seg-${seg.name}`}>
          <line
            x1={8} y1={yStart} x2={8} y2={yEnd}
            stroke={seg.color} strokeWidth="2" opacity="0.5"
          />
          <text
            x={14} y={yMid + 4}
            fill={seg.color}
            fontSize="8"
            fontFamily="monospace"
            fontWeight="bold"
          >
            {seg.name}
          </text>
        </g>
      ))}

      {/* ── Input nodes (107) ── */}
      {DQN_INPUTS.map((def, i) => {
        const y = inputNodePositions[i];
        const brightness = inputBrightnesses[i];
        const color = getSegmentColor(def.segment);
        return (
          <g key={`inp-${def.index}`}>
            <circle
              cx={INPUT_X} cy={y} r={NODE_R}
              fill={color} opacity={brightness}
              filter={brightness > 0.7 ? 'url(#nn-glow)' : undefined}
              className="transition-all duration-300"
            />
            <text
              x={INPUT_X + NODE_R + 4} y={y + 3}
              fill={brightness > 0.4 ? color : '#444'}
              fontSize="7"
              fontFamily="monospace"
            >
              {def.label}
            </text>
          </g>
        );
      })}

      {/* ── Hidden layers ── */}
      {HIDDEN_LAYERS.map((size, layerIdx) => {
        const lx = layerXs[layerIdx];
        const color = LAYER_COLORS[layerIdx];
        const dots = hiddenLayerDots[layerIdx];
        const brightnesses = hiddenBrightnesses[layerIdx];
        const ys = dots.map(d => d.y);
        const yMin = Math.min(...ys) - 12;
        const yMax = Math.max(...ys) + 12;
        return (
          <g key={`layer-${layerIdx}`}>
            {/* Bounding rect */}
            <rect
              x={lx - 10} y={yMin}
              width={20} height={yMax - yMin}
              rx={4}
              fill={color} opacity={0.04}
              stroke={color} strokeOpacity={0.12} strokeWidth={0.5}
            />
            {/* Layer label */}
            <text
              x={lx} y={yMin - 6}
              fill={color} fontSize="9" fontFamily="monospace"
              textAnchor="middle" fontWeight="bold"
            >
              L{layerIdx + 1} {size}
            </text>
            {/* Dots */}
            {dots.map((dot, j) => (
              <circle
                key={`h-${layerIdx}-${j}`}
                cx={lx} cy={dot.y} r={HIDDEN_DOT_R}
                fill={color}
                opacity={brightnesses[j] ?? 0.15}
                filter={(brightnesses[j] ?? 0) > 0.7 ? 'url(#nn-glow)' : undefined}
                className="transition-all duration-300"
              />
            ))}
          </g>
        );
      })}

      {/* ── Output nodes (3 Q-values) ── */}
      <text
        x={OUTPUT_X} y={outputPositions[0].y - OUTPUT_R - 20}
        fill="#555" fontSize="9" fontFamily="monospace" textAnchor="middle"
      >
        Q-VALUES
      </text>
      {outputPositions.map((o, i) => {
        const qVal = dqnInference?.q_values[i] ?? 0;
        const isWinner = i === winnerIdx;
        const bright = isWinner ? 1.0 : 0.25;
        return (
          <g key={`out-${o.name}`}>
            <circle
              cx={OUTPUT_X} cy={o.y} r={OUTPUT_R}
              fill={o.color} opacity={bright}
              filter={isWinner ? 'url(#nn-glow)' : undefined}
              className="transition-all duration-300"
            />
            {/* Action name above */}
            <text
              x={OUTPUT_X + OUTPUT_R + 10} y={o.y - 4}
              fill={o.color} fontSize="11" fontFamily="monospace" fontWeight="bold"
              opacity={bright}
            >
              {o.name}
            </text>
            {/* Q-value below */}
            <text
              x={OUTPUT_X + OUTPUT_R + 10} y={o.y + 10}
              fill={bright > 0.4 ? o.color : '#555'}
              fontSize="10" fontFamily="monospace"
            >
              {qVal.toFixed(3)}
            </text>
          </g>
        );
      })}

      {/* ── Status watermark ── */}
      <text
        x={12} y={totalHeight - 10}
        fill="#444" fontSize="10" fontFamily="monospace"
      >
        {statusLabel}
      </text>

      {/* ── Architecture label ── */}
      <text
        x={700} y={totalHeight - 10}
        fill="#333" fontSize="9" fontFamily="monospace" textAnchor="middle"
      >
        DQN: 107 → 128 (ReLU) → 128 (ReLU) → 64 (ReLU) → 3 Q-values
      </text>
    </svg>
  );
}
