import numpy as np
import pytest

from src.rl.signal.gbt_multitask import MultiTaskGBT


def test_multitask_gbt_trains_three_separate_models(tmp_path):
    rng = np.random.default_rng(0)
    n, d = 200, 313
    X = rng.standard_normal((n, d)).astype(np.float32)
    # Synthetic targets
    magnitudes = rng.uniform(-2, 4, n)
    win_outcomes = (rng.uniform(0, 1, n) < 0.6).astype(int)
    durations = rng.uniform(1, 20, n)

    mtgbt = MultiTaskGBT()
    mtgbt.train(X, magnitudes=magnitudes, win_outcomes=win_outcomes, durations=durations)

    obs = rng.standard_normal(d).astype(np.float32)
    out = mtgbt.predict(obs)
    assert -10 < out["magnitude_R"] < 10
    assert 0 <= out["win_probability"] <= 1
    assert out["duration_bars"] > 0


def test_multitask_gbt_save_and_load(tmp_path):
    rng = np.random.default_rng(1)
    n, d = 100, 313
    X = rng.standard_normal((n, d)).astype(np.float32)
    mtgbt = MultiTaskGBT()
    mtgbt.train(
        X,
        magnitudes=rng.uniform(-2, 4, n),
        win_outcomes=(rng.uniform(0, 1, n) < 0.5).astype(int),
        durations=rng.uniform(1, 20, n),
    )

    path = tmp_path / "mtgbt.joblib"
    mtgbt.save(path)

    mtgbt2 = MultiTaskGBT.load(path)
    obs = rng.standard_normal(d).astype(np.float32)
    o1 = mtgbt.predict(obs)
    o2 = mtgbt2.predict(obs)
    assert o1["magnitude_R"] == pytest.approx(o2["magnitude_R"])


def test_multitask_gbt_returns_zeros_when_untrained():
    mtgbt = MultiTaskGBT()
    obs = np.zeros(313, dtype=np.float32)
    out = mtgbt.predict(obs)
    assert out["magnitude_R"] == 0.0
    assert out["win_probability"] == 0.5
    assert out["duration_bars"] == 5.0
