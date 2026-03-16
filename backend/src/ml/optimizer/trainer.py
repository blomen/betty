"""LightGBM training infrastructure with walk-forward validation.

Walk-forward: train on [0..t], test on [t+embargo..t+embargo+window].
Prevents temporal leakage by ensuring train data always precedes test data
with a purge/embargo gap.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

MIN_SAMPLES_DEFAULT = 30


def walk_forward_splits(n_samples: int, n_splits: int = 5, embargo: int = 5):
    """Generate walk-forward cross-validation splits with embargo gap."""
    test_size = n_samples // (n_splits + 1)

    for i in range(n_splits):
        train_end = test_size * (i + 1)
        test_start = train_end + embargo
        test_end = min(test_start + test_size, n_samples)

        if test_start >= n_samples or test_end <= test_start:
            continue

        train_idx = list(range(train_end))
        test_idx = list(range(test_start, test_end))
        yield train_idx, test_idx


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    task: str = "regression",
    min_samples: int = MIN_SAMPLES_DEFAULT,
    n_splits: int = 3,
    embargo: int = 5,
    feature_names: list[str] | None = None,
    num_class: int | None = None,
) -> dict | None:
    """Train a LightGBM model with walk-forward validation.

    Args:
        task: "regression", "classification" (binary), or "multiclass".
        feature_names: Optional list of feature names for importance reporting.
        num_class: Required for task="multiclass" — number of target classes.

    Returns dict with 'model', 'validation_score', 'feature_importance'
    or None if insufficient data.
    """
    if len(X) < min_samples:
        logger.info(f"Insufficient data: {len(X)} < {min_samples} min_samples")
        return None

    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm not installed — ML optimizer disabled")
        return None

    if task == "regression":
        objective, metric = "regression", "rmse"
    elif task == "multiclass":
        objective, metric = "multiclass", "multi_logloss"
    else:
        objective, metric = "binary", "binary_logloss"

    params = {
        "objective": objective,
        "metric": metric,
        "num_leaves": 15,
        "learning_rate": 0.05,
        "n_estimators": 100,
        "verbose": -1,
        "min_child_samples": 5,
    }
    if task == "multiclass":
        params["num_class"] = num_class or int(np.max(y) + 1)

    def _make_model():
        if task == "regression":
            return lgb.LGBMRegressor(**params)
        return lgb.LGBMClassifier(**params)

    # Walk-forward validation
    scores = []
    for train_idx, test_idx in walk_forward_splits(len(X), n_splits=n_splits, embargo=embargo):
        if len(train_idx) < 10 or len(test_idx) < 5:
            continue

        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = _make_model()
        model.fit(X_train, y_train)
        score = model.score(X_test, y_test)
        scores.append(score)

    # Train final model on all data
    final_model = _make_model()
    final_model.fit(X, y)

    # Use real feature names if provided
    names = feature_names if feature_names and len(feature_names) == X.shape[1] else [
        f"f{i}" for i in range(X.shape[1])
    ]

    return {
        "model": final_model,
        "validation_score": float(np.mean(scores)) if scores else None,
        "feature_importance": dict(zip(names, final_model.feature_importances_.tolist())),
    }
