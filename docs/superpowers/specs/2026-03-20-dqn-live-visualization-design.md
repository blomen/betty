# DQN Live Neural Network Visualization

## Summary

Replace the decorative neural network SVG in VectorsPage with a true visualization of the DQN trading agent (107 → 128 → 128 → 64 → 3). All 107 input neurons shown individually, real hidden layer activations, real Q-values (LONG/SHORT/SKIP). Fires in real-time on level approach and level touch via SSE.

## Inspiration

Yosh's Trackmania AI neural network overlay — every input visible, connections lighting up based on actual model weights, outputs showing the agent's decision in real-time.

## Current State

- **DQN model** (`backend/src/rl/`) exists for training/eval only — not served live
- **Live inference** uses a LightGBM classifier (`ml_prediction` SSE event), not the DQN
- **Frontend** (`NeuralNetworkSVG.tsx`) shows a decorative diagram with fake hidden layers and wrong outputs (continuation/reversal/rejection instead of LONG/SHORT/SKIP)
- **Level detection** already works live via `LevelMonitor` — emits `level_approaching`, `level_touched`, `level_rejected` SSE events
- **Feature extraction** already runs on touch via `extract_level_touch_features()` — but this produces the LightGBM feature dict, not the 107-dim DQN observation vector

## Architecture

### Data Flow

```
LevelMonitor detects approach/touch
    ↓
build_observation(state) → 107-dim float32 vector
    ↓
DQNetwork.forward_with_activations(obs)
    → captures post-ReLU output at each layer
    ↓
Extract top-100 connections per layer transition by |weight × activation|
    ↓
SSE event: dqn_inference {
  inputs[107], layer1[128], layer2[128], layer3[64],
  q_values[3], action, connections[~300]
}
    ↓
Frontend NeuralNetworkSVG renders all nodes + connections with real values
```

### Trigger Timing

- **`level_approaching`** — DQN inference runs, emitted every ~2.5s while price is within 15 ticks of a level (piggybacks on existing `orderflow_update` interval)
- **`level_touched`** — DQN inference runs immediately when price hits within 5 ticks
- **Between events** — frontend holds last state, nodes stay at last brightness
- **No level nearby** — all nodes dim, structure visible, "WAITING FOR LEVEL" label

## Backend Changes

### 1. DQNetwork.forward_with_activations()

New method on `backend/src/rl/agent/network.py`:

```python
def forward_with_activations(self, x: Tensor) -> dict:
    """Forward pass capturing all intermediate activations.

    Returns dict with:
        inputs: Tensor[107] — raw observation
        layer1: Tensor[128] — post-ReLU layer 1
        layer2: Tensor[128] — post-ReLU layer 2
        layer3: Tensor[64] — post-ReLU layer 3
        q_values: Tensor[3] — final output (LONG, SHORT, SKIP)
    """
```

Implementation: manually step through `self.net` layers, capturing output after each ReLU.

### 2. Top-N Connection Extraction

New utility function in `backend/src/rl/agent/network.py`:

```python
def extract_top_connections(self, activations: dict, top_n: int = 100) -> dict:
    """Extract strongest connections per layer transition.

    For each layer pair, computes signal = |weight[i,j] * activation[i]|
    and returns top_n connections sorted by signal strength.

    Returns dict with keys: input_l1, l1_l2, l2_l3, l3_output
    Each value is a list of {from_idx, to_idx, strength, sign}
    """
```

### 3. DQN Inference Service

New module `backend/src/rl/live_inference.py`:

- Singleton service, loaded at startup if `dqn_latest.pt` exists
- `infer(state: dict) -> dict` — builds observation, runs forward_with_activations, extracts connections, returns full payload
- Thread-safe (inference is fast, ~1ms for a 107→3 MLP)
- If no model file found, returns `None` (frontend shows "NO MODEL LOADED")

### 4. SSE Event Integration

Modify `backend/src/market_data/level_monitor.py`:

- On `level_approaching` and `level_touched`, after existing ML feature extraction:
  - Build the RL observation vector from current market state
  - Call `live_inference.infer(state)`
  - Emit `dqn_inference` SSE event with full payload

### 5. SSE Event Payload

```json
{
  "type": "dqn_inference",
  "trigger": "approaching | touched",
  "level": "vwap",
  "level_price": 24418.0,
  "inputs": [0.0, 0.0, ..., 0.8, ...],
  "activations": {
    "layer1": [0.0, 0.42, ...],
    "layer2": [0.0, 0.31, ...],
    "layer3": [0.0, 0.65, ...]
  },
  "q_values": [1.42, -0.31, 0.0],
  "action": "LONG",
  "epsilon": 0.05,
  "connections": {
    "input_l1": [{"from": 42, "to": 7, "strength": 0.83, "sign": 1}, ...],
    "l1_l2": [...],
    "l2_l3": [...],
    "l3_output": [...]
  },
  "timestamp": 1774028000.0
}
```

### 6. Observation State Assembly

The DQN's `build_observation()` requires a `state` dict with specific keys (level_type, price, candles, vwap_bands, volume_profile, tpo_profile, session_levels, all_levels, orderflow_signals, macro, session_context).

Most of this data is already available in `LevelMonitor` at touch time:
- `price` — from current tick
- `candles` — from CandleFlow 1m buffer
- `level_type` — from the MonitoredLevel being touched
- `vwap_bands`, `session_levels`, `all_levels` — from expanded session
- `orderflow_signals` — from latest orderflow computation
- `macro` — from macro context fetch
- `volume_profile`, `tpo_profile` — from session data (may need wiring)
- `session_context` — from session state

A new `build_rl_state()` helper will assemble this dict from LevelMonitor's available data.

## Frontend Changes

### 1. NeuralNetworkSVG Rewrite

Complete rewrite of `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx`.

**Layout (SVG viewBox ~1400 x 1200):**

| Column | X Position | Content |
|--------|-----------|---------|
| Labels | 8-90 | Group names + vertical bars |
| Inputs | ~100 | 107 nodes, grouped by segment |
| Layer 1 | ~540 | 128 neurons in bounding box |
| Layer 2 | ~800 | 128 neurons in bounding box |
| Layer 3 | ~1035 | 64 neurons in bounding box |
| Output | ~1260 | 3 Q-value nodes (LONG/SHORT/SKIP) |

**Input nodes (107):**
- Each node: small circle + monospace label + live value
- Grouped vertically by segment with colored group labels
- Node brightness = `clamp(|activation| / segment_max, 0.15, 1.0)`
- Node color = segment color
- Glow filter on high-activation nodes (>0.7)
- One-hot segments (level type): only the active bit glows, rest dim

**Hidden layers (128, 128, 64):**
- Rendered as dense columns of small dots inside a subtle bounding box
- Show ~40 evenly-spaced representative dots per layer (not all 128 literally — would be unreadable)
- Each dot's brightness = actual neuron activation (sampled at that position in the layer)
- Layer label + neuron count at bottom
- Distinct colors per layer: cyan (L1), purple (L2), lighter purple (L3)

**Output nodes (3):**
- Large circles: LONG (emerald), SHORT (red), SKIP (zinc)
- Show Q-value number below label
- Winning action (argmax Q) glows brightest with glow filter
- Losing actions dimmed

**Connections (~300 total):**
- Only top-100 per layer transition rendered (from backend payload)
- Line from source node position to target node position
- `strokeWidth = clamp(strength * 4, 0.4, 3.0)`
- `opacity = clamp(strength, 0.05, 0.8)`
- Color: positive contribution = layer color, negative = red
- CSS `transition: all 300ms` for smooth updates

### 2. Input Node Config

New file `frontend/src/components/Terminal/pages/dqnConfig.ts`:

Maps each of the 107 observation indices to display properties:

```typescript
interface DQNInputDef {
  index: number;      // position in the 107-dim vector
  label: string;      // short display label
  segment: string;    // group name
  segmentColor: string; // hex color for the segment
}
```

Segments and their indices (matching `build_observation()` exactly):

| Index | Segment | Count | Color |
|-------|---------|-------|-------|
| 0-25 | LEVEL TYPE | 26 | #06b6d4 (cyan) |
| 26-40 | ORDERFLOW | 15 | #10b981 (emerald) |
| 41-63 | STRUCTURE | 23 | #8b5cf6 (violet) |
| 64-76 | TPO | 13 | #f59e0b (amber) |
| 77-91 | CANDLES | 15 | #ec4899 (pink) |
| 92-96 | CONFLUENCE | 5 | #14b8a6 (teal) |
| 97-106 | MACRO | 10 | #ef4444 (red) |

Individual labels for each index derived from the feature extraction code (e.g., index 26 = "delta_pct", index 27 = "delta_norm", etc.).

### 3. Visualization State Machine

```
IDLE → APPROACHING → AT_LEVEL → IDLE
         ↓               ↓
       (updates       (updates
       every 2.5s)    immediately)
```

- **IDLE**: Structure visible, all nodes at 0.15 opacity, "WAITING FOR LEVEL" watermark
- **APPROACHING**: Nodes light up with real activations, connections visible, Q-values updating
- **AT_LEVEL**: Full brightness, action recommendation prominent, glow on winning Q-value
- **Transition back to IDLE**: Fade over ~2s after `level_rejected` or timeout

### 4. VectorsPage Integration

- Remove old `latestPrediction` / `latestFeatures` props from NeuralNetworkSVG
- Add new `dqnInference` prop (the full SSE payload or null)
- Parse `dqn_inference` SSE events in the market stream handler (same place that handles `ml_prediction`)
- Add TypeScript types for the DQN inference payload

### 5. No-Model Fallback

If the DQN model is not loaded (no `dqn_latest.pt`):
- Backend never emits `dqn_inference` events
- Frontend shows the full architecture structure with all nodes dim
- Watermark: "DQN MODEL NOT LOADED — train with: python -m src.app rl train"
- The visualization still looks good as a reference diagram

## TypeScript Types

```typescript
interface DQNConnection {
  from: number;
  to: number;
  strength: number;
  sign: 1 | -1;
}

interface DQNInferenceEvent {
  type: 'dqn_inference';
  trigger: 'approaching' | 'touched';
  level: string;
  level_price: number;
  inputs: number[];           // 107
  activations: {
    layer1: number[];         // 128
    layer2: number[];         // 128
    layer3: number[];         // 64
  };
  q_values: number[];         // 3: [LONG, SHORT, SKIP]
  action: 'LONG' | 'SHORT' | 'SKIP';
  epsilon: number;
  connections: {
    input_l1: DQNConnection[];
    l1_l2: DQNConnection[];
    l2_l3: DQNConnection[];
    l3_output: DQNConnection[];
  };
  timestamp: number;
}
```

## Performance Considerations

- **Backend inference**: ~1ms per forward pass for 107→3 MLP — negligible
- **Connection extraction**: ~2ms for top-100 per layer — negligible
- **SSE payload size**: ~15KB per event (300 connections + 107+128+128+64+3 activations)
- **Frontend rendering**: ~300 SVG elements (107 input circles + labels, ~120 hidden dots, 3 output circles, ~300 connection lines) — well within browser SVG performance
- **Update rate**: Max every 2.5s — no performance concern
- **CSS transitions**: 300ms on opacity/stroke changes, no requestAnimationFrame loops

## Out of Scope

- Interactive hover cards showing feature details
- Training/retraining from the UI
- Historical replay of past inferences
- Multiple model comparison
- Weight visualization heatmaps
- Gradient flow visualization
