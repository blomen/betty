"""M6: Temporal Pattern Recognizer -- 1D-CNN on candle sequences.

Predicts reversal/continuation from last 20 candles of orderflow.
Input: (20, N_FEATURES) candle sequence.
Output: {direction, probability, confidence}.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 500
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# Features per candle (must match candle_features.snapshot_candles output)
CANDLE_FEATURE_NAMES = [
    "delta", "delta_pct", "cvd", "volume", "volume_ratio",
    "spread_ticks", "body_ratio", "close_position", "tick_count",
    "passive_active_ratio", "vwap_distance_ticks", "poc_distance_ticks",
    "imbalance_ratio_max", "stacked_imbalance_count",
    "big_trades_count", "big_trades_net_delta",
]
N_FEATURES = len(CANDLE_FEATURE_NAMES)
SEQ_LEN = 20

# Target classes
CLASSES = ["reversal_long", "reversal_short", "continuation_long", "continuation_short", "chop"]
N_CLASSES = len(CLASSES)


class TemporalPatternModel:
    def train(self, data) -> dict | None:
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            logger.warning("torch not installed -- M6 disabled")
            return None

        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            candles = features.get("candle_sequence")
            if not candles or len(candles) < SEQ_LEN:
                continue
            seq = _encode_candle_sequence(candles[-SEQ_LEN:])
            if seq is None:
                continue
            label = _get_label(row.outcome, row.outcome_binary)
            if label is None:
                continue
            X_list.append(seq)
            y_list.append(label)

        if len(X_list) < MIN_SAMPLES:
            logger.info(f"M6: insufficient data ({len(X_list)} < {MIN_SAMPLES})")
            return None

        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.int64)

        # Z-score normalize per feature across each window
        mean = X.mean(axis=1, keepdims=True)
        std = X.std(axis=1, keepdims=True) + 1e-8
        X = (X - mean) / std

        X_tensor = torch.tensor(X).permute(0, 2, 1)  # (batch, features, seq_len)
        y_tensor = torch.tensor(y)

        # Train/val split (last 20% for validation, time-ordered)
        split = int(len(X_tensor) * 0.8)
        X_train, X_val = X_tensor[:split], X_tensor[split:]
        y_train, y_val = y_tensor[:split], y_tensor[split:]

        model = _CandleCNN(N_FEATURES, N_CLASSES)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        criterion = nn.CrossEntropyLoss()

        best_val_acc = 0.0
        for epoch in range(50):
            model.train()
            optimizer.zero_grad()
            out = model(X_train)
            loss = criterion(out, y_train)
            loss.backward()
            optimizer.step()

            model.eval()
            with torch.no_grad():
                val_out = model(X_val)
                val_preds = val_out.argmax(dim=1)
                val_acc = (val_preds == y_val).float().mean().item()
                if val_acc > best_val_acc:
                    best_val_acc = val_acc

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "temporal_pattern_latest.pt"
        torch.save(model.state_dict(), path)

        return {
            "file_path": str(path),
            "training_data_count": len(X_list),
            "validation_score": best_val_acc,
            "baseline_metric": 1.0 / N_CLASSES,  # random baseline
        }

    def predict(self, candle_sequence: list[dict]) -> dict | None:
        """Predict pattern from candle sequence."""
        try:
            import torch
        except ImportError:
            return None

        if not candle_sequence or len(candle_sequence) < SEQ_LEN:
            return None

        seq = _encode_candle_sequence(candle_sequence[-SEQ_LEN:])
        if seq is None:
            return None

        # Z-score normalize
        mean = seq.mean(axis=0, keepdims=True)
        std = seq.std(axis=0, keepdims=True) + 1e-8
        seq = (seq - mean) / std

        X = torch.tensor(seq, dtype=torch.float32).unsqueeze(0).permute(0, 2, 1)
        return X  # Actual inference done by Predictor with loaded model


class _CandleCNN(object):
    """Minimal 1D-CNN for candle pattern recognition.

    Architecture: Conv1d -> ReLU -> Conv1d -> ReLU -> AdaptiveAvgPool -> FC -> Softmax
    Designed for <10ms inference on 20-candle sequences.
    """
    def __new__(cls, n_features, n_classes):
        import torch.nn as nn

        class CandleCNNModule(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv1d(n_features, 32, kernel_size=3, padding=1)
                self.conv2 = nn.Conv1d(32, 64, kernel_size=3, padding=1)
                self.pool = nn.AdaptiveAvgPool1d(1)
                self.fc = nn.Linear(64, n_classes)
                self.relu = nn.ReLU()

            def forward(self, x):
                x = self.relu(self.conv1(x))
                x = self.relu(self.conv2(x))
                x = self.pool(x).squeeze(-1)
                return self.fc(x)

        return CandleCNNModule()


def _encode_candle_sequence(candles: list[dict]) -> np.ndarray | None:
    """Encode list of candle dicts to (seq_len, n_features) array."""
    rows = []
    for c in candles:
        row = []
        for name in CANDLE_FEATURE_NAMES:
            val = c.get(name)
            row.append(float(val) if val is not None else 0.0)
        rows.append(row)
    return np.array(rows, dtype=np.float32)


def _get_label(outcome, outcome_binary) -> int | None:
    """Map R-multiple outcome to class label."""
    if outcome is None:
        return None
    if outcome > 0.5:
        return 0  # reversal_long (or continuation_long depending on context)
    elif outcome < -0.5:
        return 1  # reversal_short
    elif outcome > 0:
        return 2  # continuation_long (mild positive)
    elif outcome < 0:
        return 3  # continuation_short (mild negative)
    else:
        return 4  # chop
