# ML Intelligence Page Redesign

**Date:** 2026-04-14  
**Status:** Approved

## Goal

Replace the current DQN tab with a unified ML intelligence page showing all three model tiers — DQN, GBT, and Specialists — with real per-dimension neuron activations in the network visualization.

## Layout (Pipeline, top-to-bottom)

```
┌─────────────────────────────────────────────────────────┐
│ DECISION STRIP  action | conf | EV | stop | size | model│
├──────────────────────────┬──────────────┬───────────────┤
│ DQN NETWORK (60%)        │ SPECIALISTS  │ GBT           │
│ input cols → layers → Q  │ bar blocks   │ bar blocks    │
├──────────────────────────┴──────────────┴───────────────┤
│ FEATURE HEATMAP  (14 segment strip, existing component) │
└─────────────────────────────────────────────────────────┘
```

## Components

### 1. Decision Strip
Single horizontal bar. Always visible.
- Action (large, color-coded green/red/grey)
- Model badge: `SPECIALISTS+DQN`, `GBT+DQN`, `DQN`, etc.
- LIVE badge (when `inputs` present) or SIM badge
- Stats: conf %, P(cont), P(rev), EV cont, EV rev, stop ticks, sizing signal
- Trigger info: level name + price, right-aligned

### 2. NeuralNetworkSVG (refactored)

**Input layer — always real when signal fired:**
- 14 segment columns, one per `DQN_SEGMENTS` entry
- Each column: N dots stacked vertically (N = min(seg.size, 16), evenly sampled)
- Dot brightness (opacity) = `abs(inputs[dim]) / maxAbs`
- Dot color = segment color; red tint when value is negative
- Segment name label below column
- No simulation for inputs — if no `dqnInference`, dots are all dim grey

**Hidden layers — real when `activations.layer1` present:**
- 4 layers: 256→256→128→64, show 14 sampled neurons each
- Brightness = `abs(activation[i])` normalized within layer
- When activations absent: grey dots at low opacity (clearly "offline")
- Small "DQN OFFLINE" label when in this state

**Output:**
- 3 nodes: CONT (green), REV (red), SKIP (grey)
- Size proportional to Q-value magnitude
- Winner highlighted
- Q-values printed next to nodes

**Connections:**
- Drawn from real `connections` data when available
- Removed entirely (not simulated) when unavailable

**Status line:**
- Bottom-left: `LIVE INPUTS` or `NO SIGNAL`
- Bottom-right: `DQN ACTIVE` or `DQN OFFLINE`

### 3. Specialists Panel
Four blocks stacked vertically:
- CONT specialist: bar for P(success) + EV label
- REV specialist: bar for P(success) + EV label  
- STOP specialist: bar (scaled to 40t max) + tick value
- SIZING: bar 0–1 + Kelly fraction label

Show `---` placeholders when no data.

### 4. GBT Panel
Five bar rows:
- Direction: cont prob, rev prob
- Forecast: exp return, breakeven %
- Stop: tick value (scalar, not bar)

Show `---` when no data.

### 5. Feature Heatmap Strip
Existing `SegmentHeatmap` component, unchanged. Shows when `dqnInference?.inputs` present.

## Key Implementation Details

- **`hasRealInputs`** = `!!dqnInference?.inputs?.length` — always true when signal fired
- **`hasRealActivations`** = `!!dqnInference?.activations?.layer1?.length` — only when DQN loaded
- Input neurons use `hasRealInputs`; hidden neurons use `hasRealActivations`
- Remove `simTick` simulation timer entirely from input layer
- Keep sim timer only for hidden layers when offline (gentle pulse so they look "waiting" not "dead")
- `maxAbs` for input normalization: `Math.max(...inputs.map(Math.abs), 0.001)`
- Negative input values: red tint on dot (`mix(segmentColor, #ef4444, 0.5)`)

## Files Changed

- `firevstocks/frontend/src/pages/NeuralNetworkSVG.tsx` — full refactor
- `firevstocks/frontend/src/pages/DQNPage.tsx` — layout simplification, add Specialists+GBT panels inline
