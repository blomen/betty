"""SHAP-based feature importance for level touch predictions."""
import logging
import numpy as np

logger = logging.getLogger(__name__)

_explainer = None
_background_data = None


def init_explainer(model, X_train_sample: np.ndarray):
    """Initialize SHAP TreeExplainer with training data background."""
    global _explainer, _background_data
    try:
        import shap
        _background_data = X_train_sample[:100]  # subsample for speed
        _explainer = shap.TreeExplainer(model, _background_data)
        logger.info("SHAP explainer initialized")
    except ImportError:
        logger.warning("shap package not installed — feature explanations disabled")
    except Exception:
        logger.exception("Failed to initialize SHAP explainer")


def explain_prediction(
    features_encoded: np.ndarray,
    feature_names: list[str],
    predicted_class: int,
    top_n: int = 5,
) -> list[dict]:
    """Get top N contributing features for a prediction.

    Args:
        features_encoded: Encoded feature array (from _encode_features).
        feature_names: Ordered feature name list.
        predicted_class: Index of predicted class.
        top_n: Number of top features to return.

    Returns:
        List of {"name": str, "contribution": float} sorted by abs contribution.
        Empty list if explainer not initialized.
    """
    if _explainer is None:
        return []

    try:
        shap_values = _explainer.shap_values(features_encoded.reshape(1, -1))

        # For multiclass, shap_values is list of arrays per class
        if isinstance(shap_values, list):
            class_shap = shap_values[predicted_class][0]
        else:
            class_shap = shap_values[0]

        # Get top N by absolute SHAP value
        abs_vals = np.abs(class_shap)
        top_indices = np.argsort(abs_vals)[-top_n:][::-1]

        return [
            {
                "name": feature_names[i] if i < len(feature_names) else f"feature_{i}",
                "contribution": round(float(class_shap[i]), 4),
            }
            for i in top_indices
        ]
    except Exception:
        logger.exception("SHAP explanation failed")
        return []
