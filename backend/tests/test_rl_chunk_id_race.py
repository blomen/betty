"""Audit #21 regression tests: live_collector chunk-id uniqueness +
merge-live cleanup must not race-delete chunks that landed mid-merge.
"""

import re

import numpy as np
import pytest


def test_live_collector_chunk_ids_are_unique(tmp_path):
    """Two consecutive flushes must produce distinct chunk_ids even if they
    happen in the same millisecond — the seq counter prevents collision."""
    from src.rl.live_collector import CompletedEpisode, LiveEpisodeCollector

    coll = LiveEpisodeCollector(data_dir=tmp_path)

    def _ep():
        return CompletedEpisode(
            observation=np.zeros(4, dtype=np.float32),
            trigger_observation=None,
            reward_continuation=0.0,
            reward_reversal=0.0,
            optimal_stop_ticks=25.0,
            level_type=0,
            touch_price=100.0,
            touch_ts=12345.0,
            breakeven_reached=False,
            levels_captured=0,
        )

    coll._completed.append(_ep())
    coll._flush_to_disk()
    coll._completed.append(_ep())
    coll._flush_to_disk()

    obs_files = sorted(coll._live_dir.glob("obs_*.npy"))
    assert len(obs_files) == 2
    assert obs_files[0].name != obs_files[1].name

    # New scheme: LV<ms-epoch><3-digit seq>
    for f in obs_files:
        assert re.match(r"^obs_LV\d{16,}\.npy$", f.name), f.name


def test_live_collector_no_collision_with_existing_gap(tmp_path):
    """The previous len(glob) scheme would collide when files had gaps.
    Verify the new scheme is immune: write a fake gap then flush, the new
    chunk does NOT overwrite the existing files."""
    from src.rl.live_collector import CompletedEpisode, LiveEpisodeCollector

    live_dir = tmp_path / "live_episodes"
    live_dir.mkdir()
    # Simulate an existing chunk with arbitrary id (legacy 4-digit fmt)
    np.save(live_dir / "obs_0000.npy", np.full((1, 4), 42.0, dtype=np.float32))
    np.save(live_dir / "obs_0002.npy", np.full((1, 4), 43.0, dtype=np.float32))

    coll = LiveEpisodeCollector(data_dir=tmp_path)
    coll._completed.append(
        CompletedEpisode(
            observation=np.zeros(4, dtype=np.float32),
            trigger_observation=None,
            reward_continuation=0.0,
            reward_reversal=0.0,
            optimal_stop_ticks=25.0,
            level_type=0,
            touch_price=100.0,
            touch_ts=12345.0,
            breakeven_reached=False,
            levels_captured=0,
        )
    )
    coll._flush_to_disk()

    # Pre-existing chunks must still be there with their original contents
    assert np.load(live_dir / "obs_0000.npy")[0, 0] == 42.0
    assert np.load(live_dir / "obs_0002.npy")[0, 0] == 43.0
    # And a new LV* chunk landed (NOT obs_0001 / obs_0002)
    new_chunks = [f for f in live_dir.glob("obs_*.npy") if f.stem.startswith("obs_LV")]
    assert len(new_chunks) == 1


def test_merge_live_cleanup_preserves_non_chunk_files(tmp_path, monkeypatch):
    """The previous cleanup `for f in live_dir.glob('*.npy'): f.unlink()`
    would also wipe any non-chunk *.npy files. The new targeted cleanup
    only deletes files matching one of the snapshotted chunk_ids."""
    pytest.importorskip("torch")
    from src.rl import cli

    episodes = tmp_path / "episodes"
    live = tmp_path / "live_episodes"
    episodes.mkdir()
    live.mkdir()
    monkeypatch.setattr(cli, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_EPISODES_DIR", episodes)

    # Two snapshotted chunks
    for cid in ("LV1700000000001", "LV1700000000002"):
        np.save(live / f"obs_{cid}.npy", np.zeros((1, 4), dtype=np.float32))
        np.save(live / f"rc_{cid}.npy", np.zeros(1, dtype=np.float32))
        np.save(live / f"rr_{cid}.npy", np.zeros(1, dtype=np.float32))
        np.save(live / f"lt_{cid}.npy", np.zeros(1, dtype=np.int32))
        np.save(live / f"st_{cid}.npy", np.full(1, 25.0, dtype=np.float32))

    # Non-chunk file (e.g. a stray scratch file or a tool's index): the
    # OLD glob('*.npy') would have deleted this; the new code shouldn't.
    np.save(live / "stray_index.npy", np.array([1, 2, 3]))
    # Non-NPY artefact — sibling files like .ingested_trade_ids
    (live / ".ingested_trade_ids").write_text("42 43 44")

    cli.merge_live()

    # Snapshotted chunks: gone
    assert not (live / "obs_LV1700000000001.npy").exists()
    assert not (live / "obs_LV1700000000002.npy").exists()
    # Non-chunk files: preserved
    assert (live / "stray_index.npy").exists()
    assert (live / ".ingested_trade_ids").exists()
