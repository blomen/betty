# DQN Live Neural Network Visualization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace decorative neural network SVG with a true DQN visualization (107→128→128→64→3) showing real-time activations and Q-values on level approach/touch.

**Architecture:** Add `forward_with_activations()` to the existing DQNetwork, create a live inference singleton that runs on level approach/touch, emit a new `dqn_inference` SSE event with full layer activations and top-N connections, and rewrite the frontend NeuralNetworkSVG to render the real architecture.

**Tech Stack:** Python 3.10+ / PyTorch / FastAPI SSE | React 19 / TypeScript / Inline SVG / Tailwind

**Spec:** `docs/superpowers/specs/2026-03-20-dqn-live-visualization-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/rl/agent/network.py` | Modify | Add `forward_with_activations()` and `extract_top_connections()` |
| `backend/src/rl/live_inference.py` | Create | Singleton service: loads model, runs inference, returns full payload |
| `backend/src/market_data/level_monitor.py` | Modify | Call live inference on approach/touch, emit `dqn_inference` SSE event |
| `backend/tests/test_dqn_activations.py` | Create | Tests for activation capture and connection extraction |
| `backend/tests/test_live_inference.py` | Create | Tests for live inference service |
| `frontend/src/types/market.ts` | Modify | Add `DQNInferenceEvent` type |
| `frontend/src/components/Terminal/pages/dqnConfig.ts` | Create | 107-index label/segment/color mapping |
| `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx` | Rewrite | True DQN architecture renderer |
| `frontend/src/hooks/useLevelMonitor.ts` | Modify | Add `dqn_inference` event listener |
| `frontend/src/components/Terminal/pages/VectorsPage.tsx` | Modify | Pass DQN inference data to NeuralNetworkSVG |

---

### Task 1: DQNetwork Activation Capture

**Files:**
- Modify: `backend/src/rl/agent/network.py`
- Create: `backend/tests/test_dqn_activations.py`

- [ ] **Step 1: Write failing test for forward_with_activations**

```python
# backend/tests/test_dqn_activations.py
import numpy as np
import torch
from src.rl.agent.network import DQNetwork
from src.rl.config import NUM_ACTIONS


def test_forward_with_activations_shapes():
    """Verify activation capture returns correct shapes for each layer."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)

    assert result["inputs"].shape == (1, 107)
    assert result["layer1"].shape == (1, 128)
    assert result["layer2"].shape == (1, 128)
    assert result["layer3"].shape == (1, 64)
    assert result["q_values"].shape == (1, NUM_ACTIONS)


def test_forward_with_activations_matches_forward():
    """Q-values from activation capture must match normal forward pass."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    normal_q = net.forward(obs)
    result = net.forward_with_activations(obs)
    torch.testing.assert_close(result["q_values"], normal_q)


def test_activations_are_non_negative():
    """Post-ReLU activations should be >= 0."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)
    assert (result["layer1"] >= 0).all()
    assert (result["layer2"] >= 0).all()
    assert (result["layer3"] >= 0).all()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_dqn_activations.py -v
```
Expected: FAIL — `DQNetwork has no attribute 'forward_with_activations'`

- [ ] **Step 3: Implement forward_with_activations**

Add to `backend/src/rl/agent/network.py` after the `forward()` method (after line 40):

```python
def forward_with_activations(self, x: Tensor) -> dict[str, Tensor]:
    """Forward pass capturing all intermediate activations.

    Returns dict with keys: inputs, layer1, layer2, layer3, q_values.
    All tensors have shape (batch, dim).
    """
    if x.ndim == 1:
        x = x.unsqueeze(0)
    inputs = x
    # Step through Sequential layers manually:
    # net[0]=Linear, net[1]=ReLU, net[2]=Linear, net[3]=ReLU,
    # net[4]=Linear, net[5]=ReLU, net[6]=Linear
    layer1 = self.net[1](self.net[0](x))      # Linear + ReLU → 128
    layer2 = self.net[3](self.net[2](layer1))  # Linear + ReLU → 128
    layer3 = self.net[5](self.net[4](layer2))  # Linear + ReLU → 64
    q_values = self.net[6](layer3)             # Linear → NUM_ACTIONS
    return {
        "inputs": inputs,
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
        "q_values": q_values,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_dqn_activations.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/network.py backend/tests/test_dqn_activations.py
git commit -m "feat(rl): add forward_with_activations to DQNetwork"
```

---

### Task 2: Top-N Connection Extraction

**Files:**
- Modify: `backend/src/rl/agent/network.py`
- Modify: `backend/tests/test_dqn_activations.py`

- [ ] **Step 1: Write failing test**

```python
# Append to backend/tests/test_dqn_activations.py

def test_extract_top_connections_structure():
    """Verify top connections have correct structure and count."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)
    conns = net.extract_top_connections(result, top_n=50)

    assert set(conns.keys()) == {"input_l1", "l1_l2", "l2_l3", "l3_output"}
    for key, conn_list in conns.items():
        assert len(conn_list) <= 50
        for c in conn_list:
            assert set(c.keys()) == {"from_idx", "to_idx", "strength", "sign"}
            assert c["sign"] in (1, -1)
            assert c["strength"] >= 0


def test_extract_top_connections_sorted_by_strength():
    """Connections should be sorted descending by strength."""
    net = DQNetwork(input_dim=107)
    obs = torch.randn(1, 107)
    result = net.forward_with_activations(obs)
    conns = net.extract_top_connections(result, top_n=20)

    for conn_list in conns.values():
        strengths = [c["strength"] for c in conn_list]
        assert strengths == sorted(strengths, reverse=True)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_dqn_activations.py::test_extract_top_connections_structure -v
```
Expected: FAIL — `DQNetwork has no attribute 'extract_top_connections'`

- [ ] **Step 3: Implement extract_top_connections**

Add to `backend/src/rl/agent/network.py` after `forward_with_activations()`:

```python
@torch.no_grad()
def extract_top_connections(
    self, activations: dict[str, Tensor], top_n: int = 100
) -> dict[str, list[dict]]:
    """Extract strongest connections per layer transition.

    Signal strength = |weight[j, i] * activation[i]| for each connection.
    Returns top_n connections per transition, sorted by strength descending.
    """
    layers = [
        ("input_l1", activations["inputs"], self.net[0]),   # Linear 107→128
        ("l1_l2",    activations["layer1"], self.net[2]),    # Linear 128→128
        ("l2_l3",    activations["layer2"], self.net[4]),    # Linear 128→64
        ("l3_output", activations["layer3"], self.net[6]),   # Linear 64→3
    ]
    result = {}
    for name, act, linear in layers:
        act_1d = act[0]  # (dim,) — first batch element
        w = linear.weight  # (out_dim, in_dim)
        # Signal matrix: |w[j,i] * act[i]| for all (i,j)
        signal = (w * act_1d.unsqueeze(0)).abs()  # (out_dim, in_dim)
        # Flatten and get top-N indices
        flat = signal.flatten()
        k = min(top_n, flat.numel())
        top_vals, top_idxs = flat.topk(k)
        out_dim = w.shape[0]
        conns = []
        for val, idx in zip(top_vals.tolist(), top_idxs.tolist()):
            j = idx // w.shape[1]  # to (output neuron)
            i = idx % w.shape[1]   # from (input neuron)
            conns.append({
                "from_idx": i,
                "to_idx": j,
                "strength": round(val, 4),
                "sign": 1 if w[j, i].item() >= 0 else -1,
            })
        result[name] = conns
    return result
```

- [ ] **Step 4: Run all tests**

```bash
cd backend && python -m pytest tests/test_dqn_activations.py -v
```
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/network.py backend/tests/test_dqn_activations.py
git commit -m "feat(rl): add top-N connection extraction to DQNetwork"
```

---

### Task 3: Live Inference Service

**Files:**
- Create: `backend/src/rl/live_inference.py`
- Create: `backend/tests/test_live_inference.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_live_inference.py
import numpy as np
from unittest.mock import patch
from src.rl.live_inference import DQNLiveInference


def test_infer_returns_none_when_no_model():
    """Without a model file, infer() returns None."""
    service = DQNLiveInference()
    assert not service.is_loaded
    assert service.infer({}) is None


def test_infer_returns_full_payload():
    """With a model loaded, infer() returns complete payload."""
    service = DQNLiveInference()
    # Manually create and assign a network (skip file loading)
    from src.rl.agent.network import DQNetwork
    service._network = DQNetwork(input_dim=107)
    service._loaded = True

    state = {
        "level_type": "vwap",
        "price": 24500.0,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "tpo_profile": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
    }
    result = service.infer(state)

    assert result is not None
    assert len(result["inputs"]) == 107
    assert len(result["activations"]["layer1"]) == 128
    assert len(result["activations"]["layer2"]) == 128
    assert len(result["activations"]["layer3"]) == 64
    assert len(result["q_values"]) == 3
    assert result["action"] in ("LONG", "SHORT", "SKIP")
    assert set(result["connections"].keys()) == {"input_l1", "l1_l2", "l2_l3", "l3_output"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_live_inference.py -v
```
Expected: FAIL — `No module named 'src.rl.live_inference'`

- [ ] **Step 3: Implement live_inference.py**

```python
# backend/src/rl/live_inference.py
"""DQN live inference service — singleton for real-time level touch inference."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch

from .agent.network import DQNetwork
from .config import Action, LevelType
from .features.observation import build_observation, OBSERVATION_DIM

log = logging.getLogger(__name__)

# Search paths for trained model checkpoint
_MODEL_SEARCH_DIRS = [
    Path("data/rl"),
    Path("backend/data/rl"),
]
_MODEL_PATTERNS = ["dqn_latest.pt", "dqn_*.pt"]


class DQNLiveInference:
    """Loads a trained DQN and runs inference with full activation capture."""

    def __init__(self) -> None:
        self._network: DQNetwork | None = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def try_load(self) -> bool:
        """Attempt to find and load the newest DQN checkpoint.

        Returns True if a model was loaded successfully.
        """
        for search_dir in _MODEL_SEARCH_DIRS:
            if not search_dir.exists():
                continue
            # Try dqn_latest.pt first, then any dqn_*.pt sorted by mtime
            latest = search_dir / "dqn_latest.pt"
            if latest.exists():
                return self._load_checkpoint(latest)
            candidates = sorted(search_dir.glob("dqn_*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                return self._load_checkpoint(candidates[0])
        log.info("No DQN checkpoint found — live visualization will show empty architecture")
        return False

    def _load_checkpoint(self, path: Path) -> bool:
        try:
            self._network = DQNetwork(input_dim=OBSERVATION_DIM)
            checkpoint = torch.load(path, weights_only=False, map_location="cpu")
            self._network.load_state_dict(checkpoint["q_network"])
            self._network.eval()
            self._loaded = True
            log.info("DQN model loaded from %s", path)
            return True
        except Exception:
            log.exception("Failed to load DQN checkpoint from %s", path)
            self._network = None
            self._loaded = False
            return False

    def infer(self, state: dict) -> dict | None:
        """Run inference on a market state dict.

        Returns None if no model is loaded.
        Returns full payload dict with inputs, activations, q_values, action, connections.
        """
        if not self._loaded or self._network is None:
            return None

        # Normalise level_type to enum if it's a string
        lt = state.get("level_type", "vwap")
        if isinstance(lt, str):
            try:
                state["level_type"] = LevelType(lt)
            except ValueError:
                state["level_type"] = LevelType.VWAP

        obs = build_observation(state)
        obs_tensor = torch.from_numpy(obs).unsqueeze(0)

        with torch.no_grad():
            activations = self._network.forward_with_activations(obs_tensor)
            connections = self._network.extract_top_connections(activations, top_n=100)

        q_vals = activations["q_values"][0].tolist()
        action_idx = int(np.argmax(q_vals))
        action_name = Action(action_idx).name  # "LONG", "SHORT", "SKIP"

        return {
            "inputs": obs.tolist(),
            "activations": {
                "layer1": activations["layer1"][0].tolist(),
                "layer2": activations["layer2"][0].tolist(),
                "layer3": activations["layer3"][0].tolist(),
            },
            "q_values": q_vals,
            "action": action_name,
            "connections": connections,
        }


# Module-level singleton
_instance: DQNLiveInference | None = None


def get_dqn_inference() -> DQNLiveInference:
    """Get the global DQN inference singleton."""
    global _instance
    if _instance is None:
        _instance = DQNLiveInference()
        _instance.try_load()
    return _instance
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/test_live_inference.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/live_inference.py backend/tests/test_live_inference.py
git commit -m "feat(rl): add DQN live inference service singleton"
```

---

### Task 4: Wire DQN Inference into LevelMonitor

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add import at top of level_monitor.py**

After existing imports, add:

```python
from src.rl.live_inference import get_dqn_inference
```

- [ ] **Step 2: Add DQN inference to _handle_ml_touch**

In `_handle_ml_touch()` (around line 468, after the existing `ml_prediction` emit), add:

```python
# --- DQN inference ---
dqn = get_dqn_inference()
if dqn.is_loaded:
    rl_state = self._build_rl_state(level_name, level_price, price)
    dqn_result = dqn.infer(rl_state)
    if dqn_result is not None:
        self._publish({
            "type": "dqn_inference",
            "trigger": "touched",
            "level": level_name,
            "level_price": level_price,
            **dqn_result,
            "timestamp": time.time(),
        })
```

- [ ] **Step 3: Add DQN inference to _on_level_approaching**

In `_on_level_approaching()` (around line 272, after the existing emit), add the same pattern but with `"trigger": "approaching"`.

- [ ] **Step 4: Add DQN inference to _emit_orderflow_update**

In `_emit_orderflow_update()` (around line 574, after emitting `orderflow_update`), add DQN inference with `"trigger": "approaching"` so the visualization updates every 2.5s while at a level.

- [ ] **Step 5: Implement _build_rl_state helper**

Add this method to the `LevelMonitor` class:

```python
def _build_rl_state(self, level_name: str, level_price: float, current_price: float) -> dict:
    """Assemble a state dict compatible with RL build_observation()."""
    from src.rl.config import LevelType

    # Map level name to LevelType enum
    level_type_map = {
        "vwap": LevelType.VWAP,
        "vwap_sd1_upper": LevelType.VWAP_SD1, "vwap_sd1_lower": LevelType.VWAP_SD1,
        "vwap_sd2_upper": LevelType.VWAP_SD2, "vwap_sd2_lower": LevelType.VWAP_SD2,
        "vwap_sd3_upper": LevelType.VWAP_SD3, "vwap_sd3_lower": LevelType.VWAP_SD3,
        "poc": LevelType.POC_SESSION, "poc_daily": LevelType.POC_DAILY,
        "poc_weekly": LevelType.POC_WEEKLY, "poc_monthly": LevelType.POC_MONTHLY,
        "vah": LevelType.VAH, "val": LevelType.VAL,
        "ib_high": LevelType.IB_HIGH, "ib_low": LevelType.IB_LOW,
        "pdh": LevelType.PDH, "pdl": LevelType.PDL,
    }
    lt = level_type_map.get(level_name.lower(), LevelType.VWAP)

    return {
        "level_type": lt,
        "price": current_price,
        "candles": list(self._candle_buffer) if hasattr(self, "_candle_buffer") else [],
        "vwap_bands": self._vwap_bands if hasattr(self, "_vwap_bands") else None,
        "volume_profile": self._volume_profile if hasattr(self, "_volume_profile") else None,
        "tpo_profile": self._tpo_profile if hasattr(self, "_tpo_profile") else None,
        "session_levels": self._session_levels if hasattr(self, "_session_levels") else None,
        "all_levels": [l.price for l in self._levels] if hasattr(self, "_levels") else [],
        "orderflow_signals": self._orderflow_signals if hasattr(self, "_orderflow_signals") else None,
        "macro": self._macro_context if hasattr(self, "_macro_context") else None,
        "session_context": self._session_context if hasattr(self, "_session_context") else None,
    }
```

Note: The exact attribute names depend on what's available in `LevelMonitor`. Read the class's `__init__` and data-setting methods to find the correct attribute names for candle buffer, VWAP bands, volume profile, etc. The important thing is that we pass whatever data is available — `build_observation()` handles None values gracefully.

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(vectors): wire DQN inference into LevelMonitor SSE events"
```

---

### Task 5: Frontend TypeScript Types

**Files:**
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add DQN types**

Append to `frontend/src/types/market.ts`:

```typescript
// --- DQN Live Inference ---

export interface DQNConnection {
  from_idx: number;
  to_idx: number;
  strength: number;
  sign: 1 | -1;
}

export interface DQNInferenceEvent {
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
  connections: {
    input_l1: DQNConnection[];
    l1_l2: DQNConnection[];
    l2_l3: DQNConnection[];
    l3_output: DQNConnection[];
  };
  timestamp: number;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types/market.ts
git commit -m "feat(vectors): add DQNInferenceEvent TypeScript types"
```

---

### Task 6: DQN Input Config (107 Labels)

**Files:**
- Create: `frontend/src/components/Terminal/pages/dqnConfig.ts`

- [ ] **Step 1: Create the config file**

This maps each of the 107 observation vector indices to a display label, segment name, and color. Derived directly from `backend/src/rl/features/observation.py` and each segment's feature extraction file.

```typescript
// dqnConfig.ts — maps each of the 107 DQN observation indices to display properties

export interface DQNInputDef {
  index: number;
  label: string;
  segment: string;
}

export interface DQNSegment {
  name: string;
  color: string;
  start: number;
  end: number;  // exclusive
}

export const DQN_SEGMENTS: DQNSegment[] = [
  { name: 'LEVEL TYPE',  color: '#06b6d4', start: 0,  end: 26 },
  { name: 'ORDERFLOW',   color: '#10b981', start: 26, end: 41 },
  { name: 'STRUCTURE',   color: '#8b5cf6', start: 41, end: 64 },
  { name: 'TPO',         color: '#f59e0b', start: 64, end: 77 },
  { name: 'CANDLES',     color: '#ec4899', start: 77, end: 92 },
  { name: 'CONFLUENCE',  color: '#14b8a6', start: 92, end: 97 },
  { name: 'MACRO',       color: '#ef4444', start: 97, end: 107 },
];

// Level type names (indices 0-25) — matches LevelType enum order in config.py
const LEVEL_TYPES = [
  'poc_session', 'poc_daily', 'poc_weekly', 'poc_monthly', 'poc_macro',
  'vah', 'val', 'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  'ib_high', 'ib_low', 'pdh', 'pdl',
  'tokyo_hl', 'london_hl', 'globex_hl', 'overnight_hl',
  'weekly_hl', 'monthly_hl',
  'naked_poc', 'single_print', 'fvg', 'order_block', 'swing_point',
];

// Orderflow feature names (indices 26-40)
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
];

// Structure feature names (indices 41-63)
const STRUCTURE = [
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'single_prints',
  'ib_range', 'poor_high', 'poor_low',
  'mkt_trend', 'mkt_range', 'mkt_neutral',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
];

// TPO feature names (indices 64-76)
const TPO = [
  'poc_dist', 'va_width', 'in_va', 'time_at_px',
  'excess_hi', 'excess_lo', 'rotation_f', 'rotation_n',
  'shape_p', 'shape_b', 'shape_d', 'shape_bal', 'reserved',
];

// Candle window feature names (indices 77-91) — 5 candles × 3 features
const CANDLES = [
  'c1 delta', 'c1 vol', 'c1 body',
  'c2 delta', 'c2 vol', 'c2 body',
  'c3 delta', 'c3 vol', 'c3 body',
  'c4 delta', 'c4 vol', 'c4 body',
  'c5 delta', 'c5 vol', 'c5 body',
];

// Confluence feature names (indices 92-96)
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
];

// Macro feature names (indices 97-106)
const MACRO = [
  'vix', 'vix_chg', 'regime', 'dxy_chg', 'gex',
  'us10y_chg', 'us2y_chg', 'yield_curve', 'news', 'news_sev',
];

// Build the full 107-element array
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 26 + i, label, segment: 'ORDERFLOW' })),
  ...STRUCTURE.map((label, i) => ({ index: 41 + i, label, segment: 'STRUCTURE' })),
  ...TPO.map((label, i) => ({ index: 64 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 77 + i, label, segment: 'CANDLES' })),
  ...CONFLUENCE.map((label, i) => ({ index: 92 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 97 + i, label, segment: 'MACRO' })),
];

/** Get segment color for a given segment name */
export function getSegmentColor(segmentName: string): string {
  return DQN_SEGMENTS.find(s => s.name === segmentName)?.color ?? '#52525b';
}

/** Hidden layer sizes (real DQN architecture) */
export const HIDDEN_LAYERS = [128, 128, 64] as const;
export const NUM_ACTIONS = 3;
export const ACTION_NAMES = ['LONG', 'SHORT', 'SKIP'] as const;
export const ACTION_COLORS = ['#10b981', '#ef4444', '#52525b'] as const;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/dqnConfig.ts
git commit -m "feat(vectors): add DQN 107-index input config"
```

---

### Task 7: Wire DQN Inference Event in useLevelMonitor

**Files:**
- Modify: `frontend/src/hooks/useLevelMonitor.ts`

- [ ] **Step 1: Add state for DQN inference**

Add to the hook's state declarations (around line 30):

```typescript
const [dqnInference, setDqnInference] = useState<DQNInferenceEvent | null>(null);
```

Import the type:
```typescript
import type { DQNInferenceEvent } from '@/types/market';
```

- [ ] **Step 2: Add event listener**

After the existing `es.addEventListener('ml_features', onMlFeatures)` (line 204), add:

```typescript
const onDqnInference = (e: MessageEvent) => {
  try { setDqnInference(JSON.parse(e.data)); } catch {}
};
es.addEventListener('dqn_inference', onDqnInference);
```

And add cleanup in the removeEventListener block.

- [ ] **Step 3: Return dqnInference from the hook**

Add `dqnInference` to the return object (around line 237).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useLevelMonitor.ts
git commit -m "feat(vectors): listen for dqn_inference SSE events"
```

---

### Task 8: Rewrite NeuralNetworkSVG

**Files:**
- Rewrite: `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx`

This is the largest task. The component renders the full 107→128→128→64→3 architecture.

- [ ] **Step 1: Write the new NeuralNetworkSVG component**

Complete rewrite. The component receives `dqnInference: DQNInferenceEvent | null` as its primary prop.

Key rendering sections:
1. **Input nodes (107)**: Iterate `DQN_INPUTS`, group by segment. Each node is a circle at `(INPUT_X, y)` with brightness = `clamp(|inputs[i]|, 0.15, 1.0)`. Color = segment color. Label to the right.
2. **Hidden layers (128, 128, 64)**: For each layer, render ~40 evenly-spaced representative dots inside a bounding rect. Brightness = `activations.layer[sampledIndex]`. Show neuron count label.
3. **Output nodes (3)**: Large circles for LONG/SHORT/SKIP. Show Q-value. Winning action (argmax) gets glow filter.
4. **Connections (~300)**: Render lines from `connections.input_l1`, `l1_l2`, `l2_l3`, `l3_output`. Map `from_idx`/`to_idx` to screen coordinates. `strokeWidth = clamp(strength * 4, 0.4, 3)`. Color: sign=1 → layer color, sign=-1 → red.
5. **State labels**: "APPROACHING [level]" or "AT LEVEL [level]" or "WAITING FOR LEVEL" watermark.
6. **No-model fallback**: If `dqnInference` is null, show architecture structure with all nodes dim.

SVG viewBox: `0 0 1400 1200` (scrollable container). `preserveAspectRatio="xMidYMin meet"`.

Layout constants:
```typescript
const INPUT_X = 180;
const LAYER1_X = 540;
const LAYER2_X = 780;
const LAYER3_X = 1000;
const OUTPUT_X = 1260;
const NODE_R = 4;
const OUTPUT_R = 16;
const ROW_H = 10;        // tight spacing for 107 nodes
const SEGMENT_GAP = 8;
const TOP_PAD = 35;
```

Use `useMemo` for:
- Input node positions (static, computed once from `DQN_INPUTS`)
- Hidden layer sample positions (static)
- Output node positions (static)

Use `useMemo` with `[dqnInference]` dependency for:
- Connection lines
- Node brightnesses

CSS transitions: `transition: all 300ms` on circle opacity and line stroke properties.

- [ ] **Step 2: Verify it compiles**

```bash
cd frontend && npx tsc --noEmit
```
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx
git commit -m "feat(vectors): rewrite NeuralNetworkSVG for true DQN architecture"
```

---

### Task 9: Wire Into VectorsPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/VectorsPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/TradingContainer.tsx` (if needed)

- [ ] **Step 1: Update VectorsPage props**

Add `dqnInference` prop to the `Props` interface:

```typescript
dqnInference: DQNInferenceEvent | null;
```

- [ ] **Step 2: Pass to NeuralNetworkSVG**

Replace the current NeuralNetworkSVG usage:

```tsx
<NeuralNetworkSVG dqnInference={dqnInference} />
```

Remove the old `features`, `prediction`, `book` props from NeuralNetworkSVG (those were for the decorative version).

- [ ] **Step 3: Thread dqnInference from TradingContainer**

In `TradingContainer.tsx`, get `dqnInference` from `useLevelMonitor()` and pass it to `VectorsPage`.

- [ ] **Step 4: Clean up old nnConfig.ts**

Delete `frontend/src/components/Terminal/pages/nnConfig.ts` — replaced by `dqnConfig.ts`.

- [ ] **Step 5: Verify it compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/VectorsPage.tsx frontend/src/components/Terminal/pages/TradingContainer.tsx
git rm frontend/src/components/Terminal/pages/nnConfig.ts
git commit -m "feat(vectors): wire DQN inference into VectorsPage"
```

---

### Task 10: Visual Verification

**Files:** None (verification only)

- [ ] **Step 1: Start backend**

```bash
cd backend && python -m src.app serve
```

Verify in logs: either "DQN model loaded from ..." or "No DQN checkpoint found ..."

- [ ] **Step 2: Start frontend**

```bash
cd frontend && npm run dev
```

- [ ] **Step 3: Open Vectors tab and verify**

Check:
- Architecture structure visible (107 input nodes grouped by segment, 3 hidden layer columns, 3 output nodes)
- If no model loaded: all nodes dim, "DQN MODEL NOT LOADED" label visible
- If model loaded: wait for level approach to see nodes light up
- Segment labels and colors match spec
- Output nodes show LONG/SHORT/SKIP labels

- [ ] **Step 4: Test with simulated data (if no live data)**

Temporarily hardcode a fake `DQNInferenceEvent` in VectorsPage to verify rendering:

```typescript
const fakeDqn: DQNInferenceEvent = {
  type: 'dqn_inference',
  trigger: 'touched',
  level: 'vwap',
  level_price: 24500,
  inputs: Array.from({ length: 107 }, () => Math.random()),
  activations: {
    layer1: Array.from({ length: 128 }, () => Math.random()),
    layer2: Array.from({ length: 128 }, () => Math.random()),
    layer3: Array.from({ length: 64 }, () => Math.random()),
  },
  q_values: [1.42, -0.31, 0.0],
  action: 'LONG',
  connections: {
    input_l1: Array.from({ length: 100 }, (_, i) => ({ from_idx: i % 107, to_idx: i % 128, strength: Math.random(), sign: (Math.random() > 0.3 ? 1 : -1) as 1 | -1 })),
    l1_l2: Array.from({ length: 100 }, (_, i) => ({ from_idx: i % 128, to_idx: i % 128, strength: Math.random() * 0.6, sign: 1 as 1 | -1 })),
    l2_l3: Array.from({ length: 100 }, (_, i) => ({ from_idx: i % 128, to_idx: i % 64, strength: Math.random() * 0.5, sign: 1 as 1 | -1 })),
    l3_output: Array.from({ length: 30 }, (_, i) => ({ from_idx: i % 64, to_idx: i % 3, strength: Math.random() * 0.8, sign: 1 as 1 | -1 })),
  },
  timestamp: Date.now() / 1000,
};
```

Verify all 107 nodes render, connections visible, Q-values shown. Remove fake data after verification.

- [ ] **Step 5: Commit any polish**

```bash
git add -A
git commit -m "fix(vectors): tune DQN visualization layout and rendering"
```

---

## Notes

- **Model file convention**: The live inference service checks for `dqn_latest.pt` first, then falls back to the most recent `dqn_*.pt` by modification time. The training CLI should be updated to copy/symlink the best checkpoint as `dqn_latest.pt`.
- **LevelMonitor attribute names**: Task 4's `_build_rl_state` uses `hasattr` guards because the exact attribute names need to be verified against the actual LevelMonitor class. Read the class to find the correct names for candle buffer, VWAP bands, etc.
- **107 labels**: The `dqnConfig.ts` label arrays are derived from the backend feature extraction code. If the observation vector changes (features added/removed), both files must be updated.
- **Hidden layer sampling**: We render ~40 representative dots per hidden layer, not all 128. The dots are evenly spaced and map to actual neurons (e.g., dot 0 → neuron 0, dot 1 → neuron 3, dot 2 → neuron 6, etc.). This keeps rendering fast while showing real activations.
- **Old code cleanup**: `nnConfig.ts` is deleted in Task 9. The old `NeuralNetworkSVG.tsx` is fully replaced in Task 8.
