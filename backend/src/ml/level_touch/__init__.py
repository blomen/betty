"""Level touch ML classifier module."""

# Shared state for last ML prediction, written by LevelMonitor, read by API
_last_ml_prediction: dict | None = None


def set_last_prediction(prediction: dict):
    global _last_ml_prediction
    _last_ml_prediction = prediction


def get_last_prediction() -> dict | None:
    return _last_ml_prediction
