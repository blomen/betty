# Vectors Neural Network Visualization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace gauge-bar grid in VectorsPage with a Trackmania-style neural network SVG showing all features as firing neurons flowing through hidden layers to prediction outputs.

**Architecture:** Single `NeuralNetworkSVG` React component using inline SVG. Feature config array drives node layout. Existing props (`latestFeatures`, `latestPrediction`, `book`, `lastTick`) provide all data — no backend changes needed.

**Tech Stack:** React 19, TypeScript, inline SVG, CSS transitions, Tailwind

**Spec:** `docs/superpowers/specs/2026-03-20-vectors-nn-visualization-design.md`

---

### Task 1: Create Neural Network Feature Config

**Files:**
- Create: `frontend/src/components/Terminal/pages/nnConfig.ts`

This is the data model that drives everything — maps feature keys to their display properties.

- [ ] **Step 1: Create the config file**

```typescript
// nnConfig.ts — defines every feature node in the neural network

export type NNColor = 'green' | 'red' | 'amber' | 'dim';

export interface NNFeatureDef {
  key: string;          // feature dict key or special key like 'book.bid_size'
  label: string;        // short display label
  group: string;        // group name for vertical clustering
  range: [number, number]; // [min, max] for visual normalization to [0,1]
  colorFn: (v: number) => NNColor; // bullish/bearish coloring
}

// Color helpers
const posneg = (v: number): NNColor => v > 0 ? 'green' : v < 0 ? 'red' : 'dim';
const high = (v: number): NNColor => v > 0.5 ? 'amber' : 'dim';
const bool = (v: number): NNColor => v ? 'amber' : 'dim';

export const NN_FEATURES: NNFeatureDef[] = [
  // BOOK
  { key: 'book.bid_size', label: 'BID', group: 'BOOK', range: [0, 2000], colorFn: (v) => v > 500 ? 'green' : 'dim' },
  { key: 'book.ask_size', label: 'ASK', group: 'BOOK', range: [0, 2000], colorFn: (v) => v > 500 ? 'red' : 'dim' },
  { key: 'book.spread', label: 'SPREAD', group: 'BOOK', range: [0, 2], colorFn: (v) => v > 0.75 ? 'amber' : 'dim' },
  { key: 'passive_active_ratio', label: 'PA RATIO', group: 'BOOK', range: [0, 4], colorFn: (v) => v > 2 ? 'amber' : 'dim' },

  // ORDERFLOW
  { key: 'delta', label: 'DELTA', group: 'FLOW', range: [-50000, 50000], colorFn: posneg },
  { key: 'cvd', label: 'CVD', group: 'FLOW', range: [-50000, 50000], colorFn: posneg },
  { key: 'vsa_absorption', label: 'ABSORB', group: 'FLOW', range: [0, 1], colorFn: bool },
  { key: 'stacked_imbalance_count', label: 'IMBAL', group: 'FLOW', range: [0, 5], colorFn: high },
  { key: 'big_trades_count', label: 'BIG', group: 'FLOW', range: [0, 10], colorFn: high },
  { key: 'trapped_traders', label: 'TRAPPED', group: 'FLOW', range: [0, 1], colorFn: bool },
  { key: 'stop_run_detected', label: 'STOP RUN', group: 'FLOW', range: [0, 1], colorFn: bool },

  // TEMPORAL
  { key: 'delta_slope_5m', label: 'Δ SLP 5M', group: 'TEMPORAL', range: [-100, 100], colorFn: posneg },
  { key: 'delta_slope_10m', label: 'Δ SLP 10M', group: 'TEMPORAL', range: [-100, 100], colorFn: posneg },
  { key: 'cvd_acceleration', label: 'CVD ACCEL', group: 'TEMPORAL', range: [-2, 2], colorFn: posneg },
  { key: 'volume_roc_5m', label: 'VOL ROC', group: 'TEMPORAL', range: [-5, 5], colorFn: posneg },
  { key: 'price_velocity', label: 'PX VEL', group: 'TEMPORAL', range: [-5, 5], colorFn: posneg },

  // CANDLE
  { key: 'last_candle_body_ratio', label: 'BODY', group: 'CANDLE', range: [0, 1], colorFn: high },
  { key: 'last_candle_delta', label: 'LAST Δ', group: 'CANDLE', range: [-5000, 5000], colorFn: posneg },

  // SESSION
  { key: 'market_type', label: 'MKT TYPE', group: 'SESSION', range: [0, 4], colorFn: high },
  { key: 'opening_type', label: 'OPEN TYPE', group: 'SESSION', range: [0, 4], colorFn: high },
  { key: 'ib_range', label: 'IB RANGE', group: 'SESSION', range: [0, 100], colorFn: high },

  // MACRO
  { key: 'vix_level', label: 'VIX', group: 'MACRO', range: [10, 80], colorFn: (v) => v > 25 ? 'amber' : 'dim' },
  { key: 'regime_score', label: 'REG SCORE', group: 'MACRO', range: [0, 1], colorFn: high },

  // LEVEL
  { key: 'level_strength', label: 'STRENGTH', group: 'LEVEL', range: [0, 1], colorFn: (v) => v > 0.5 ? 'green' : 'dim' },
  { key: 'level_confluence', label: 'CONFLNCE', group: 'LEVEL', range: [0, 5], colorFn: (v) => v >= 2 ? 'green' : 'dim' },
  { key: 'delta_aligned', label: 'Δ ALIGN', group: 'LEVEL', range: [0, 1], colorFn: (v) => v ? 'green' : 'red' },

  // APPROACH VOLUME
  { key: 'approach_vol_slope', label: 'VOL SLOPE', group: 'APPROACH', range: [-2, 2], colorFn: posneg },
  { key: 'approach_vol_ratio', label: 'VOL INTO', group: 'APPROACH', range: [0, 3], colorFn: high },
  { key: 'approach_delta_slope', label: 'Δ INTO', group: 'APPROACH', range: [-2, 2], colorFn: posneg },
];

/** Group labels in display order */
export const NN_GROUPS = ['BOOK', 'FLOW', 'TEMPORAL', 'CANDLE', 'SESSION', 'MACRO', 'LEVEL', 'APPROACH'];

/** Normalize a raw value to [0, 1] given a feature's range */
export function normalizeValue(value: number, range: [number, number]): number {
  const [min, max] = range;
  return Math.max(0, Math.min(1, (Math.abs(value) - Math.abs(min)) / (Math.abs(max) - Math.abs(min))));
}

/** Format a raw value for display next to the node */
export function formatValue(value: number | string | boolean | null | undefined): string {
  if (value == null) return '--';
  if (typeof value === 'boolean') return value ? 'YES' : '--';
  if (typeof value === 'string') return value;
  if (Math.abs(value) >= 1000) return `${value > 0 ? '+' : ''}${(value / 1000).toFixed(1)}k`;
  if (Math.abs(value) >= 1) return `${value > 0 ? '+' : ''}${value.toFixed(0)}`;
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/nnConfig.ts
git commit -m "feat(vectors): add neural network feature config"
```

---

### Task 2: Create NeuralNetworkSVG Component

**Files:**
- Create: `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx`

The core visualization — a single SVG that renders input nodes, hidden layers, output nodes, and connections.

- [ ] **Step 1: Create the component file**

```tsx
// NeuralNetworkSVG.tsx
import { useMemo } from 'react';
import {
  NN_FEATURES, NN_GROUPS, normalizeValue, formatValue,
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
```

Note: The hidden layer opacity uses `Math.random()` as placeholder — this will be replaced with actual computed activations once we have them. For now, decorative.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx
git commit -m "feat(vectors): add NeuralNetworkSVG component"
```

---

### Task 3: Wire Into VectorsPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/VectorsPage.tsx`

Replace the gauge grid section with the new NN component. Keep everything else (header, NearbyLevelStrip, PredictionBar, TradeActionBar, PositionManager).

- [ ] **Step 1: Update VectorsPage imports**

Remove: `GaugeBar`, all `gaugeHelpers` imports, `Section` component.

Add: `import { NeuralNetworkSVG } from './NeuralNetworkSVG';`

- [ ] **Step 2: Remove gauge-related code from the component body**

Remove:
- All `featureXxxToGauges()` calls (lines ~144-153)
- The `hasData` variable
- The `importanceMap` useMemo (this logic moves into NeuralNetworkSVG)
- The COT data fetch + state (can be added back later as a vector)
- The `Section` component definition

- [ ] **Step 3: Replace the gauge grid JSX**

Replace the `<div className="flex-1 overflow-y-auto ...">` block (the gauge grid, lines ~238-282) with:

```tsx
<div className={`flex-1 overflow-y-auto min-h-0 border border-border bg-panel p-2 ${isStale ? 'opacity-60' : ''}`}>
  <NeuralNetworkSVG
    features={latestFeatures}
    prediction={latestPrediction}
    book={book}
  />
</div>
```

- [ ] **Step 4: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/VectorsPage.tsx
git commit -m "feat(vectors): replace gauge grid with neural network visualization"
```

---

### Task 4: Visual Verify & Polish

**Files:**
- Modify: `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx`
- Modify: `frontend/src/components/Terminal/pages/nnConfig.ts`

- [ ] **Step 1: Start dev server and check the page**

Run: `cd frontend && npm run dev`

Open the Vectors tab. Verify:
- Input nodes render with group labels
- Hidden layer nodes visible
- Output nodes show prediction percentages (or 0% if no prediction data)
- Connections visible between layers
- Empty state shows dim nodes (no crash when features are null)

- [ ] **Step 2: Tune layout if needed**

Adjust `ROW_H`, `GROUP_GAP`, `TOP_PAD` constants, or SVG viewBox if the layout is too cramped/sparse. Tune `range` values in nnConfig if nodes are all maxed out or all dim.

- [ ] **Step 3: Commit any polish**

```bash
git add frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx frontend/src/components/Terminal/pages/nnConfig.ts
git commit -m "fix(vectors): tune NN layout and normalization ranges"
```

---

## Notes

- **Feature list will evolve.** Adding a new vector = adding one entry to `NN_FEATURES` in nnConfig.ts. The layout auto-adjusts.
- **Hidden layers are decorative** until we expose actual LightGBM internals. They represent the concept.
- **GaugeBar and gaugeHelpers are NOT deleted** — other code may reference them. They're just no longer imported by VectorsPage.
- **No tests** — this is a visual SVG component with no business logic worth unit testing. Visual verification via dev server.
