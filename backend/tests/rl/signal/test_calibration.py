import numpy as np
import pytest

from src.rl.signal.calibration import IsotonicCalibrator


def test_calibrator_fit_then_transform():
    """Fit on (raw_probs, true_outcomes). transform() maps raw → calibrated."""
    cal = IsotonicCalibrator()
    # Synthetic data: raw_prob_cont vs whether cont actually happened
    rng = np.random.default_rng(42)
    n = 500
    raw_p_cont = rng.uniform(0, 1, size=n)
    # Outcome: cont happens with prob ~= raw_p_cont (well-calibrated)
    true_cont = (rng.uniform(0, 1, size=n) < raw_p_cont).astype(int)

    cal.fit_per_class(class_idx=0, raw_probs=raw_p_cont, true_outcomes=true_cont)
    # transform a single logit triple
    calibrated = cal.transform([0.6, 0.3, 0.1])
    assert len(calibrated) == 3
    assert all(0.0 <= p <= 1.0 for p in calibrated)


def test_calibrator_passes_through_when_unfitted():
    """Transform without fit returns the input unchanged."""
    cal = IsotonicCalibrator()
    out = cal.transform([0.5, 0.3, 0.2])
    assert out == [0.5, 0.3, 0.2]


def test_calibrator_renormalizes_to_sum_to_one():
    """After per-class transformation, probabilities may not sum to 1.
    Calibrator must renormalize."""
    cal = IsotonicCalibrator()
    rng = np.random.default_rng(0)
    for ci in range(3):
        raw = rng.uniform(0, 1, 200)
        true = (rng.uniform(0, 1, 200) < raw).astype(int)
        cal.fit_per_class(class_idx=ci, raw_probs=raw, true_outcomes=true)
    out = cal.transform([0.7, 0.5, 0.4])
    assert sum(out) == pytest.approx(1.0, abs=1e-6)


def test_calibrator_save_and_load(tmp_path):
    cal = IsotonicCalibrator()
    rng = np.random.default_rng(1)
    for ci in range(3):
        raw = rng.uniform(0, 1, 200)
        true = (rng.uniform(0, 1, 200) < raw).astype(int)
        cal.fit_per_class(class_idx=ci, raw_probs=raw, true_outcomes=true)

    path = tmp_path / "cal.joblib"
    cal.save(path)

    cal2 = IsotonicCalibrator.load(path)
    out = cal2.transform([0.6, 0.3, 0.1])
    expected = cal.transform([0.6, 0.3, 0.1])
    np.testing.assert_allclose(out, expected, atol=1e-8)


def test_brier_score_improves_after_calibration():
    """Trained calibrator should reduce Brier score on holdout."""
    from sklearn.metrics import brier_score_loss

    rng = np.random.default_rng(7)
    n = 2000
    # Miscalibrated raw probs: shifted toward 0.5 (overconfident-suppressed)
    raw_p = np.clip(rng.beta(2, 2, n), 0.05, 0.95)
    # True outcomes follow a STEEPER probability than raw suggests
    true_p = raw_p**0.5  # square-root pulls high probs higher
    true_y = (rng.uniform(0, 1, n) < true_p).astype(int)

    # Fit on half, test on half
    half = n // 2
    cal = IsotonicCalibrator()
    cal.fit_per_class(class_idx=0, raw_probs=raw_p[:half], true_outcomes=true_y[:half])

    raw_brier = brier_score_loss(true_y[half:], raw_p[half:])
    cal_p = np.array([cal.transform([rp, 0, 0])[0] for rp in raw_p[half:]])
    cal_brier = brier_score_loss(true_y[half:], cal_p)

    assert cal_brier < raw_brier, f"Calibration didn't improve: raw={raw_brier:.4f}, cal={cal_brier:.4f}"
