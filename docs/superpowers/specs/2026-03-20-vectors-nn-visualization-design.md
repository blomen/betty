# Vectors Neural Network Visualization

## Summary

Replace the gauge-bar grid in VectorsPage with a Trackmania-style neural network visualization. Every feature the ML model reads is displayed as a firing neuron, with connections flowing through hidden layers to prediction outputs. Live data pulses through the network in real-time.

## Inspiration

Yosh's Trackmania neural network overlay — inputs on left, hidden layers in middle, outputs on right. Node brightness = activation strength. Connection thickness = weight contribution.

## Layout

- **Input layer (left)**: Every individual feature as its own node, grouped vertically by category (Book, Orderflow, Temporal, etc.) with group labels. Each node shows: name + raw value.
- **Hidden layers (middle)**: Decorative nodes representing model internals. Cyan (layer 1) and purple (layer 2). Brightness driven by aggregate input activation.
- **Output layer (right)**: Prediction classes (Continuation, Reversal, Rejection) with probability percentages. Winning class glows brightest.
- **Connections**: Lines between layers. Thickness = SHAP feature contribution. Color follows signal direction (green/red). Opacity fades for weak connections.

## Visual Encoding

- **Node color**: Green = bullish/positive, Red = bearish/negative, Amber = neutral/elevated
- **Node brightness**: Per-feature normalization to [0,1] using a range lookup table. Ranges are approximate and will be tuned as vectors are built out.
- **Glow effect**: SVG filter for high-activation nodes (>0.7 normalized)
- **Animation**: CSS transitions on opacity/stroke changes (~300ms). No rAF loops.

## Data Sources

Uses existing props already wired into VectorsPage:
- `latestFeatures.features` — raw feature dict from SSE `ml_features` event
- `latestPrediction` — prediction + SHAP top_features for connection weights
- `book` — bid/ask/spread for book group nodes
- `lastTick` — cvd, delta for orderflow nodes

## Component Structure

- `NeuralNetworkSVG` — single SVG component replacing the gauge grid
- Takes features dict + prediction + book as props
- Feature list and grouping defined in a config array (easy to add/remove/reorder as vectors evolve)
- Everything else in VectorsPage (header, NearbyLevelStrip, PredictionBar, TradeActionBar, PositionManager) unchanged

## Flexibility

The feature set is not finalized. The component should:
- Read features from a config array that maps `featureKey → { label, group, range, colorLogic }`
- Automatically lay out nodes based on how many features exist
- Gracefully handle missing features (dim/hidden node)
- Be easy to add new vectors without touching layout code

## Out of Scope (for now)

- Semantic interpretation layer (divergence, confirmation states)
- Interactive hover detail cards
- Feature history/sparklines
- Training/retraining from the UI
