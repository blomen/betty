"""Audit #19 regression tests: merge-live must extend touch_epochs.npy.

Before the fix, live_collector + ingest-live-trades + label-zone-outcomes all
wrote obs_*.npy + rc/rr/lt/st chunks but never te_*.npy, and merge-live
extended observations without extending touch_epochs. The live block then
ran through the chronological session_memory simulator with t=0, which
collapses every live row to the session start.
"""

import numpy as np
import pytest


@pytest.fixture
def tmp_pools(tmp_path, monkeypatch):
    """Point _DATA_DIR + _EPISODES_DIR at a tmpdir so merge-live is sandboxed."""
    # cli.py imports torch at module level; skip merge-live tests when local
    # env doesn't have it (the docker container does).
    pytest.importorskip("torch")
    from src.rl import cli

    episodes = tmp_path / "episodes"
    live = tmp_path / "live_episodes"
    episodes.mkdir()
    live.mkdir()
    monkeypatch.setattr(cli, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_EPISODES_DIR", episodes)
    return episodes, live


def _seed_main(episodes_dir, n_main=3, dim=4):
    """Write a minimal main pool that merge-live's hot-start branch will load."""
    np.save(episodes_dir / "observations.npy", np.ones((n_main, dim), dtype=np.float32))
    np.save(episodes_dir / "rewards_cont.npy", np.zeros(n_main, dtype=np.float32))
    np.save(episodes_dir / "rewards_rev.npy", np.zeros(n_main, dtype=np.float32))
    np.save(episodes_dir / "level_types.npy", np.zeros(n_main, dtype=np.int32))
    np.save(episodes_dir / "stop_targets.npy", np.full(n_main, 25.0, dtype=np.float32))
    np.save(episodes_dir / "touch_epochs.npy", np.array([100.0, 200.0, 300.0], dtype=np.float64))


def _seed_live_chunk(live_dir, cid, n_live=2, dim=4, te=None):
    np.save(live_dir / f"obs_{cid}.npy", np.full((n_live, dim), 2.0, dtype=np.float32))
    np.save(live_dir / f"rc_{cid}.npy", np.zeros(n_live, dtype=np.float32))
    np.save(live_dir / f"rr_{cid}.npy", np.ones(n_live, dtype=np.float32))
    np.save(live_dir / f"lt_{cid}.npy", np.zeros(n_live, dtype=np.int32))
    np.save(live_dir / f"st_{cid}.npy", np.full(n_live, 25.0, dtype=np.float32))
    if te is not None:
        np.save(live_dir / f"te_{cid}.npy", np.asarray(te, dtype=np.float64))


def test_merge_live_extends_touch_epochs(tmp_pools):
    """Hot-start: live block's te_*.npy gets concatenated onto main touch_epochs."""
    from src.rl.cli import merge_live

    episodes, live = tmp_pools
    _seed_main(episodes, n_main=3)
    _seed_live_chunk(live, "0001", n_live=2, te=[1700.0, 1800.0])

    merge_live()

    out = np.load(episodes / "touch_epochs.npy")
    assert out.tolist() == [100.0, 200.0, 300.0, 1700.0, 1800.0]


def test_merge_live_zero_pads_when_main_lacks_touch_epochs(tmp_pools):
    """If touch_epochs.npy is missing in the main pool, _merge_aux zero-fills
    that side and still keeps the live entries — better than zeroing the
    live block (the previous behaviour)."""
    from src.rl.cli import merge_live

    episodes, live = tmp_pools
    _seed_main(episodes, n_main=3)
    (episodes / "touch_epochs.npy").unlink()
    _seed_live_chunk(live, "0001", n_live=2, te=[1700.0, 1800.0])

    merge_live()

    out = np.load(episodes / "touch_epochs.npy")
    assert out.shape == (5,)
    assert out[:3].tolist() == [0.0, 0.0, 0.0]
    assert out[3:].tolist() == [1700.0, 1800.0]


def test_merge_live_skips_touch_epochs_when_no_chunks_have_it(tmp_pools):
    """Legacy live chunks (no te_*.npy yet) must not crash — pre-existing
    main touch_epochs.npy is preserved as-is."""
    from src.rl.cli import merge_live

    episodes, live = tmp_pools
    _seed_main(episodes, n_main=3)
    _seed_live_chunk(live, "0001", n_live=2, te=None)  # no te chunk

    merge_live()

    out = np.load(episodes / "touch_epochs.npy")
    assert out.tolist() == [100.0, 200.0, 300.0]  # unchanged


def test_merge_live_cold_start_writes_touch_epochs(tmp_pools):
    """No main pool yet → live block becomes the whole pool. touch_epochs.npy
    must contain the live te_*.npy contents."""
    from src.rl.cli import merge_live

    episodes, live = tmp_pools
    _seed_live_chunk(live, "0001", n_live=2, te=[42.0, 43.0])

    merge_live()

    out = np.load(episodes / "touch_epochs.npy")
    assert out.tolist() == [42.0, 43.0]


def test_live_collector_writes_te_chunk(tmp_path):
    """live_collector._flush_to_disk must persist touch_ts as te_<idx>.npy."""
    from src.rl.live_collector import CompletedEpisode, LiveEpisodeCollector

    coll = LiveEpisodeCollector(data_dir=tmp_path)
    coll._completed.append(
        CompletedEpisode(
            observation=np.zeros(4, dtype=np.float32),
            trigger_observation=None,
            reward_continuation=0.5,
            reward_reversal=0.0,
            optimal_stop_ticks=25.0,
            level_type=0,
            touch_price=100.0,
            touch_ts=12345.6,
            breakeven_reached=True,
            levels_captured=1,
        )
    )
    coll._flush_to_disk()

    te_files = sorted(coll._live_dir.glob("te_*.npy"))
    assert len(te_files) == 1
    arr = np.load(te_files[0])
    assert arr.tolist() == [12345.6]
