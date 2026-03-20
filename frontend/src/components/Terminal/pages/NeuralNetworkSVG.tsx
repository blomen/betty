// NeuralNetworkSVG.tsx
import { useMemo } from 'react';
import {
  NN_FEATURES, normalizeValue, formatValue,
  type NNColor,
} from './nnConfig';
import type {
  MlPrediction, MlFeatureSnapshot, StreamBookEvent,
} from '@/types/market';

interface Props {
  features: MlFeatureSnapshot | null;
  prediction: MlPrediction | null;
  book: StreamBookEvent | null;
}

// Layout constants
const INPUT_X = 55;
const HIDDEN1_X = 330;
const HIDDEN2_X = 490;
const OUTPUT_X = 650;
const NODE_R = 6;
const HIDDEN_R = 7;
const OUTPUT_R = 10;
const ROW_H = 18;       // vertical spacing between nodes
const GROUP_GAP = 10;    // extra gap between groups
const TOP_PAD = 25;

// Color maps
const FILL_MAP: Record<NNColor, string> = {
  green: '#10b981', red: '#ef4444', amber: '#f59e0b', dim: '#52525b',
};

// Output classes we expect
const OUTPUT_CLASSES = ['continuation', 'reversal', 'rejection'];
const OUTPUT_COLORS = ['#10b981', '#ef4444', '#f59e0b'];

export function NeuralNetworkSVG({ features, prediction, book }: Props) {
  // Resolve raw feature values from props
  const featureValues = useMemo(() => {
    const f = features?.features ?? {};
    const vals: Record<string, number> = {};
    for (const def of NN_FEATURES) {
      if (def.key.startsWith('book.')) {
        const bk = def.key.split('.')[1];
        vals[def.key] = book ? Number((book as any)[bk]) || 0 : 0;
      } else {
        const raw = f[def.key];
        vals[def.key] = typeof raw === 'number' ? raw : typeof raw === 'boolean' ? (raw ? 1 : 0) : 0;
      }
    }
    return vals;
  }, [features, book]);

  // SHAP importance map
  const importanceMap = useMemo(() => {
    const map: Record<string, number> = {};
    if (prediction?.top_features) {
      for (const feat of prediction.top_features) {
        map[feat.name] = Math.abs(feat.contribution);
      }
    }
    return map;
  }, [prediction]);

  // Compute input node positions (grouped)
  const inputNodes = useMemo(() => {
    const nodes: Array<{ def: typeof NN_FEATURES[0]; y: number }> = [];
    let y = TOP_PAD;
    let lastGroup = '';
    for (const def of NN_FEATURES) {
      if (def.group !== lastGroup) {
        if (lastGroup) y += GROUP_GAP;
        lastGroup = def.group;
      }
      nodes.push({ def, y });
      y += ROW_H;
    }
    return nodes;
  }, []);

  const totalHeight = inputNodes.length > 0
    ? inputNodes[inputNodes.length - 1].y + 40
    : 400;

  // Hidden layer positions
  const hidden1Count = 6;
  const hidden2Count = 4;
  const h1Nodes = Array.from({ length: hidden1Count }, (_, i) => ({
    y: TOP_PAD + 30 + i * ((totalHeight - 80) / (hidden1Count - 1)),
  }));
  const h2Nodes = Array.from({ length: hidden2Count }, (_, i) => ({
    y: TOP_PAD + 50 + i * ((totalHeight - 120) / (hidden2Count - 1)),
  }));

  // Output positions
  const outputProbs = OUTPUT_CLASSES.map(cls => {
    const prob = prediction?.probabilities?.[cls] ?? 0;
    return { cls, prob };
  });
  const outYStart = totalHeight / 2 - (OUTPUT_CLASSES.length - 1) * 30;
  const outputNodes = outputProbs.map((o, i) => ({
    ...o,
    y: outYStart + i * 60,
    color: OUTPUT_COLORS[i],
  }));

  // Group label positions
  const groupLabels = useMemo(() => {
    const labels: Array<{ group: string; y: number; yEnd: number }> = [];
    let currentGroup = '';
    let startY = 0;
    for (const node of inputNodes) {
      if (node.def.group !== currentGroup) {
        if (currentGroup) {
          labels[labels.length - 1].yEnd = node.y - GROUP_GAP;
        }
        currentGroup = node.def.group;
        startY = node.y;
        labels.push({ group: currentGroup, y: startY, yEnd: startY });
      }
    }
    if (labels.length > 0) {
      labels[labels.length - 1].yEnd = inputNodes[inputNodes.length - 1].y;
    }
    return labels;
  }, [inputNodes]);

  // Deterministic connection mapping (input → hidden1)
  // Each input connects to 2 hidden1 nodes based on index
  const connections1 = useMemo(() => {
    const conns: Array<{ x1: number; y1: number; x2: number; y2: number; opacity: number; color: string }> = [];
    for (let i = 0; i < inputNodes.length; i++) {
      const node = inputNodes[i];
      const rawVal = featureValues[node.def.key] ?? 0;
      const norm = normalizeValue(rawVal, node.def.range);
      const color = FILL_MAP[node.def.colorFn(rawVal)];
      // connect to 2 hidden nodes
      const h1a = i % hidden1Count;
      const h1b = (i + 1) % hidden1Count;
      const imp = importanceMap[node.def.key] ?? norm * 0.3;
      conns.push({
        x1: INPUT_X + NODE_R, y1: node.y,
        x2: HIDDEN1_X - HIDDEN_R, y2: h1Nodes[h1a].y,
        opacity: Math.max(0.08, Math.min(0.8, imp)),
        color,
      });
      if (norm > 0.2) {
        conns.push({
          x1: INPUT_X + NODE_R, y1: node.y,
          x2: HIDDEN1_X - HIDDEN_R, y2: h1Nodes[h1b].y,
          opacity: Math.max(0.05, Math.min(0.5, imp * 0.6)),
          color,
        });
      }
    }
    return conns;
  }, [inputNodes, featureValues, importanceMap, h1Nodes]);

  // Hidden1 → Hidden2
  const connections2 = useMemo(() => {
    const conns: Array<{ x1: number; y1: number; x2: number; y2: number; opacity: number }> = [];
    for (let i = 0; i < hidden1Count; i++) {
      for (let j = 0; j < hidden2Count; j++) {
        const strength = 0.2 + 0.6 * Math.abs(Math.sin(i * 3 + j * 7));
        conns.push({
          x1: HIDDEN1_X + HIDDEN_R, y1: h1Nodes[i].y,
          x2: HIDDEN2_X - HIDDEN_R, y2: h2Nodes[j].y,
          opacity: strength * 0.6,
        });
      }
    }
    return conns;
  }, [h1Nodes, h2Nodes]);

  // Hidden2 → Output
  const connections3 = useMemo(() => {
    const conns: Array<{ x1: number; y1: number; x2: number; y2: number; opacity: number; color: string }> = [];
    for (let i = 0; i < hidden2Count; i++) {
      for (let j = 0; j < outputNodes.length; j++) {
        conns.push({
          x1: HIDDEN2_X + HIDDEN_R, y1: h2Nodes[i].y,
          x2: OUTPUT_X - OUTPUT_R, y2: outputNodes[j].y,
          opacity: outputNodes[j].prob * 0.9,
          color: outputNodes[j].color,
        });
      }
    }
    return conns;
  }, [h2Nodes, outputNodes]);

  const svgWidth = 780;

  return (
    <svg
      viewBox={`0 0 ${svgWidth} ${totalHeight}`}
      className="w-full h-full"
      style={{ minHeight: 0 }}
    >
      <defs>
        <filter id="nn-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* ── Connections (behind nodes) ── */}
      {connections1.map((c, i) => (
        <line key={`c1-${i}`} x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2}
          stroke={c.color} opacity={c.opacity}
          strokeWidth={Math.max(0.4, c.opacity * 2.5)}
          className="transition-all duration-300" />
      ))}
      {connections2.map((c, i) => (
        <line key={`c2-${i}`} x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2}
          stroke="#06b6d4" opacity={c.opacity}
          strokeWidth={Math.max(0.3, c.opacity * 1.8)}
          className="transition-all duration-300" />
      ))}
      {connections3.map((c, i) => (
        <line key={`c3-${i}`} x1={c.x1} y1={c.y1} x2={c.x2} y2={c.y2}
          stroke={c.color} opacity={c.opacity}
          strokeWidth={Math.max(0.3, c.opacity * 2.5)}
          className="transition-all duration-300" />
      ))}

      {/* ── Group labels ── */}
      {groupLabels.map(g => (
        <g key={g.group}>
          <text x="5" y={g.y - 4} fill="#444" fontSize="7" fontFamily="monospace" fontWeight="bold">
            {g.group}
          </text>
          <line x1="5" y1={g.y} x2="5" y2={g.yEnd} stroke="#27272a" strokeWidth="1" />
        </g>
      ))}

      {/* ── Input nodes ── */}
      {inputNodes.map(({ def, y }) => {
        const rawVal = featureValues[def.key] ?? 0;
        const norm = normalizeValue(rawVal, def.range);
        const color = FILL_MAP[def.colorFn(rawVal)];
        const bright = Math.max(0.15, Math.min(1, norm));
        const displayVal = formatValue(rawVal || (features?.features?.[def.key] ?? null));

        return (
          <g key={def.key}>
            <circle cx={INPUT_X} cy={y} r={NODE_R}
              fill={color} opacity={bright}
              filter={bright > 0.7 ? 'url(#nn-glow)' : undefined}
              className="transition-all duration-300" />
            <text x={INPUT_X + NODE_R + 4} y={y + 3}
              fill={bright > 0.4 ? color : '#555'}
              fontSize="6.5" fontFamily="monospace">
              {def.label} {displayVal}
            </text>
          </g>
        );
      })}

      {/* ── Hidden layer 1 ── */}
      <text x={HIDDEN1_X - 15} y={TOP_PAD} fill="#333" fontSize="7" fontFamily="monospace">
        HIDDEN 1
      </text>
      {h1Nodes.map((n, i) => (
        <circle key={`h1-${i}`} cx={HIDDEN1_X} cy={n.y} r={HIDDEN_R}
          fill="#06b6d4" opacity={0.3 + Math.random() * 0.5}
          className="transition-all duration-500" />
      ))}

      {/* ── Hidden layer 2 ── */}
      <text x={HIDDEN2_X - 15} y={TOP_PAD} fill="#333" fontSize="7" fontFamily="monospace">
        HIDDEN 2
      </text>
      {h2Nodes.map((n, i) => (
        <circle key={`h2-${i}`} cx={HIDDEN2_X} cy={n.y} r={HIDDEN_R}
          fill="#8b5cf6" opacity={0.3 + Math.random() * 0.5}
          className="transition-all duration-500" />
      ))}

      {/* ── Output nodes ── */}
      <text x={OUTPUT_X - 10} y={TOP_PAD} fill="#333" fontSize="7" fontFamily="monospace">
        OUTPUT
      </text>
      {outputNodes.map(o => {
        const bright = Math.max(0.15, o.prob);
        return (
          <g key={o.cls}>
            <circle cx={OUTPUT_X} cy={o.y} r={OUTPUT_R}
              fill={o.color} opacity={bright}
              filter={bright > 0.5 ? 'url(#nn-glow)' : undefined}
              className="transition-all duration-300" />
            <text x={OUTPUT_X + OUTPUT_R + 6} y={o.y - 4}
              fill={o.color} fontSize="8" fontFamily="monospace" fontWeight="bold">
              {o.cls.toUpperCase()}
            </text>
            <text x={OUTPUT_X + OUTPUT_R + 6} y={o.y + 8}
              fill={bright > 0.3 ? o.color : '#555'}
              fontSize="10" fontFamily="monospace" fontWeight="bold">
              {Math.round(o.prob * 100)}%
            </text>
          </g>
        );
      })}

      {/* ── Legend ── */}
      <g transform={`translate(${svgWidth - 170}, ${totalHeight - 55})`}>
        <rect x="0" y="0" width="160" height="50" rx="4" fill="#111" stroke="#27272a" />
        <circle cx="12" cy="12" r="4" fill="#10b981" opacity="0.9" />
        <text x="22" y="15" fill="#666" fontSize="6" fontFamily="monospace">Bullish / positive</text>
        <circle cx="12" cy="26" r="4" fill="#ef4444" opacity="0.7" />
        <text x="22" y="29" fill="#666" fontSize="6" fontFamily="monospace">Bearish / negative</text>
        <circle cx="12" cy="40" r="4" fill="#f59e0b" opacity="0.6" />
        <text x="22" y="43" fill="#666" fontSize="6" fontFamily="monospace">Elevated / neutral</text>
      </g>
    </svg>
  );
}
