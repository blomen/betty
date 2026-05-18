"""A/B diff for two episode pools — quantifies the impact of code-level fixes.

Designed for the PROFILE-audit fix cycle (2026-05-18) but generic: takes two
directories of episode arrays (obs / trigger_obs / rewards), computes
per-dim activation deltas, distribution shifts, and reward deltas.

Use this BEFORE committing to a full retrain. If the fixes don't move dim
activations on real episodes, there's nothing to retrain for.

Pool directory layout expected (matches `rl ingest` / replay output):
  pool/
    observations.npy            # (N, 302) base obs
    trigger_observations.npy    # (N, 118) or (N, 122) trigger obs
    rewards_cont.npy            # (N,)
    rewards_rev.npy             # (N,)

Generation (run on server, per-commit A/B chain):

  # Pool 0 — pre-fix baseline (HEAD~3, before any audit fixes)
  cd /opt/arnold && git checkout <pre-fix-sha>
  docker compose exec backend python -m src.rl.cli replay-april --out /app/data/rl/episodes_pool0

  # Pool 1 — + Bug 1 (FVG/OB wiring) only
  git checkout <bug1-sha>
  docker compose exec backend python -m src.rl.cli replay-april --out /app/data/rl/episodes_pool1

  # Pool 2 — + Bug 3 (swing fallback)
  git checkout <bug3-sha>
  docker compose exec backend python -m src.rl.cli replay-april --out /app/data/rl/episodes_pool2

  # Pool 3 — + Bug 2 (TPO schema bump)
  git checkout <bug2-sha>
  docker compose exec backend python -m src.rl.cli replay-april --out /app/data/rl/episodes_pool3

  # Pairwise diffs isolate per-fix impact
  python backend/scripts/ab_diff_pools.py /app/data/rl/episodes_pool0 /app/data/rl/episodes_pool1
  python backend/scripts/ab_diff_pools.py /app/data/rl/episodes_pool1 /app/data/rl/episodes_pool2
  python backend/scripts/ab_diff_pools.py /app/data/rl/episodes_pool2 /app/data/rl/episodes_pool3

Exit 0 if the deltas land where the audit predicted (i.e. fixes work),
exit 1 if a sanity check fails (e.g. baseline trigger_obs already 122-dim).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Slots the audit predicted should shift from dead to alive
# ---------------------------------------------------------------------------

# Base obs layout (276-dim, zone mode) — see passthrough_features.py
# Zone composition starts at idx 0 and runs len(LevelType) dims
# (LevelType currently has 35 entries; verified by smoke test).
_BASE_ZONE_COMP_START = 0

# These names must match the order in `LevelType` enum (config.py)
_LEVEL_TYPE_ORDER = [
    "DAILY_POC",
    "DAILY_VAH",
    "DAILY_VAL",
    "WEEKLY_POC",
    "WEEKLY_VAH",
    "WEEKLY_VAL",
    "MONTHLY_POC",
    "MONTHLY_VAH",
    "MONTHLY_VAL",
    "VWAP",
    "VWAP_SD1",
    "VWAP_SD2",
    "VWAP_SD3",
    "PDH",
    "PDL",
    "TOKYO_HIGH",
    "TOKYO_LOW",
    "NYIB_HIGH",
    "NYIB_LOW",
    "TPOC",
    "TVAH",
    "TVAL",
    "TIBH",
    "TIBL",
    "NAKED_POC",
    "DAILY_SWING_HIGH",
    "DAILY_SWING_LOW",
    "WEEKLY_SWING_HIGH",
    "WEEKLY_SWING_LOW",
    "MONTHLY_SWING_HIGH",
    "MONTHLY_SWING_LOW",
    "FVG_BULL",
    "FVG_BEAR",
    "ORDER_BLOCK_BULL",
    "ORDER_BLOCK_BEAR",
]

# Dims the audit flagged as dead in baseline that fixes should resurrect.
_AUDIT_PREDICTED_RESURRECTIONS_BASE_OBS: dict[str, int] = {
    name: _BASE_ZONE_COMP_START + _LEVEL_TYPE_ORDER.index(name)
    for name in (
        "FVG_BULL",
        "FVG_BEAR",
        "ORDER_BLOCK_BULL",
        "ORDER_BLOCK_BEAR",
        "WEEKLY_SWING_LOW",
        "MONTHLY_SWING_HIGH",
        "MONTHLY_SWING_LOW",
    )
}

# Trigger passthrough TPO slots that grew the schema
# (positions 5, 6, 10, 11 in the new 14-dim passthrough)
_TPO_PASSTHROUGH_NEW_SLOTS = {
    5: "tpo_tokyo_opening_direction",
    6: "tpo_london_ib_range",
    10: "tpo_ny_ib_range",
    11: "tpo_ny_price_vs_ib_mid",
}


# ---------------------------------------------------------------------------
# Pool loader
# ---------------------------------------------------------------------------


class Pool:
    def __init__(self, root: Path, label: str) -> None:
        self.root = root
        self.label = label
        self.obs = np.load(root / "observations.npy")
        self.trig = np.load(root / "trigger_observations.npy")
        self.rc = np.load(root / "rewards_cont.npy")
        self.rr = np.load(root / "rewards_rev.npy")
        assert len(self.obs) == len(self.trig) == len(self.rc) == len(self.rr), f"{label}: array length mismatch"

    def n(self) -> int:
        return len(self.obs)

    def nonzero_pct(self, arr: np.ndarray, idx: int) -> float:
        return float((arr[:, idx] != 0).mean())

    def __repr__(self) -> str:
        return f"Pool({self.label}, n={self.n()}, trig_dim={self.trig.shape[1]})"


# ---------------------------------------------------------------------------
# Diff sections
# ---------------------------------------------------------------------------


def diff_episode_counts(b: Pool, t: Pool) -> None:
    print("\n" + "=" * 80)
    print("EPISODE COUNTS")
    print("=" * 80)
    delta = t.n() - b.n()
    pct = (delta / max(b.n(), 1)) * 100
    print(f"  baseline:  {b.n():>6,}")
    print(f"  treatment: {t.n():>6,}  ({delta:+,}; {pct:+.1f}%)")
    if abs(pct) > 5:
        print("  ⚠ episode count drift > 5% — replay non-determinism, or fixes affected touch detection")


def diff_resurrected_dims_base(b: Pool, t: Pool) -> int:
    """Bug 1 + 3: FVG/OB/swing composition slots in BASE obs."""
    print("\n" + "=" * 80)
    print("RESURRECTED COMPOSITION DIMS (base obs zone_composition slots)")
    print("Bug 1: FVG/OB wiring | Bug 3: swing prior_high/low fallback")
    print("=" * 80)
    print(f"\n  {'dim':<24} {'baseline %':>12} {'treatment %':>13} {'delta pp':>10} {'verdict':<20}")
    print("  " + "-" * 80)

    fails = 0
    for name, idx in _AUDIT_PREDICTED_RESURRECTIONS_BASE_OBS.items():
        b_pct = b.nonzero_pct(b.obs, idx) * 100
        t_pct = t.nonzero_pct(t.obs, idx) * 100
        delta = t_pct - b_pct
        if b_pct < 1.0 and t_pct >= 1.0:
            verdict = "RESURRECTED ✓"
        elif b_pct < 1.0 and t_pct < 1.0:
            verdict = "STILL DEAD ✗"
            fails += 1
        elif b_pct >= 1.0 and t_pct >= 1.0:
            verdict = "alive both"
        else:
            verdict = "REGRESSED ✗"
            fails += 1
        print(f"  {name:<24} {b_pct:>11.2f}% {t_pct:>12.2f}% {delta:>+9.2f} {verdict:<20}")

    if fails:
        print(f"\n  ⚠ {fails} predicted resurrection(s) failed — investigate before retraining")
    return fails


def diff_tpo_passthrough(b: Pool, t: Pool) -> int:
    """Bug 2: 4 new TPO dims in trigger passthrough."""
    print("\n" + "=" * 80)
    print("TPO PASSTHROUGH SLOTS (trigger obs, positions 5/6/10/11)")
    print("Bug 2: TPO gap — 4 highest-R-impact TPO dims added to passthrough")
    print("=" * 80)

    if b.trig.shape[1] == t.trig.shape[1]:
        print("  baseline and treatment have same trigger dim — Bug 2 not in this delta.")
        print(f"    baseline dim={b.trig.shape[1]}, treatment dim={t.trig.shape[1]}")
        print("    (this section only meaningful when comparing across the schema bump)")
        return 0

    if b.trig.shape[1] != 118 or t.trig.shape[1] != 122:
        print(f"  ✗ unexpected dims: baseline={b.trig.shape[1]}, treatment={t.trig.shape[1]}")
        return 1

    print(f"\n  baseline trig_dim={b.trig.shape[1]} (pre-fix), treatment trig_dim={t.trig.shape[1]} (post-fix)")
    print(f"\n  {'name':<32} {'treatment nonzero %':>20} {'mean':>10} {'std':>10}")
    print("  " + "-" * 75)

    fails = 0
    for slot, name in _TPO_PASSTHROUGH_NEW_SLOTS.items():
        col = t.trig[:, slot]
        nz = float((col != 0).mean()) * 100
        m = float(col.mean())
        s = float(col.std())
        flag = "" if nz > 1.0 else "  ⚠ near-dead"
        if nz <= 1.0:
            fails += 1
        print(f"  {name:<32} {nz:>19.2f}% {m:>+10.3f} {s:>10.3f}{flag}")

    if fails:
        print(f"\n  ⚠ {fails} new TPO slot(s) near-dead in treatment — extraction not connecting upstream TPO data")
    return fails


def diff_reward_distribution(b: Pool, t: Pool) -> None:
    print("\n" + "=" * 80)
    print("REWARD DISTRIBUTION (capped [-1.5, +2.5])")
    print("=" * 80)

    def stats(rc: np.ndarray, rr: np.ndarray) -> dict:
        best = np.maximum(rc, rr)
        return {
            "mean_best_R": float(best.mean()),
            "runner_rate": float((best >= 2.25).mean()) * 100,
            "stop_rate": float((np.minimum(rc, rr) <= -1.25).mean()) * 100,
            "p_cont_pos": float((rc > 0).mean()) * 100,
            "p_rev_pos": float((rr > 0).mean()) * 100,
        }

    bs = stats(b.rc, b.rr)
    ts = stats(t.rc, t.rr)

    print(f"\n  {'metric':<20} {'baseline':>12} {'treatment':>12} {'delta':>10}")
    print("  " + "-" * 60)
    for k in bs:
        delta = ts[k] - bs[k]
        unit = "%" if "rate" in k or "p_" in k else "R"
        print(f"  {k:<20} {bs[k]:>11.3f}{unit} {ts[k]:>11.3f}{unit} {delta:>+9.3f}")

    print("\n  Note: reward deltas reflect REPLAY differences (FVG/OB now in zones may")
    print("  change touch detection / zone formation). Real R impact requires retrain + eval.")


def diff_global_dim_changes(b: Pool, t: Pool) -> None:
    """Sweep all base obs dims, report any dim where nonzero% shifted >5pp."""
    print("\n" + "=" * 80)
    print("GLOBAL BASE OBS DRIFT (any dim where nonzero% shifted by > 5pp)")
    print("=" * 80)

    n_dims = min(b.obs.shape[1], t.obs.shape[1])
    drifters = []
    for i in range(n_dims):
        b_pct = float((b.obs[:, i] != 0).mean()) * 100
        t_pct = float((t.obs[:, i] != 0).mean()) * 100
        if abs(t_pct - b_pct) >= 5.0:
            drifters.append((i, b_pct, t_pct, t_pct - b_pct))

    if not drifters:
        print("  (no dims drifted > 5pp — fixes only affect the expected dim slots)")
        return

    drifters.sort(key=lambda x: -abs(x[3]))
    print(f"\n  {'idx':>5} {'baseline %':>12} {'treatment %':>13} {'delta pp':>10}")
    print("  " + "-" * 50)
    for idx, b_pct, t_pct, d in drifters[:30]:
        print(f"  {idx:>5} {b_pct:>11.2f}% {t_pct:>12.2f}% {d:>+9.2f}")
    if len(drifters) > 30:
        print(f"  ... and {len(drifters) - 30} more")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("baseline_dir", type=Path, help="pre-fix episode pool root")
    p.add_argument("treatment_dir", type=Path, help="post-fix episode pool root")
    args = p.parse_args()

    print("=" * 80)
    print(f"A/B POOL DIFF — {args.baseline_dir.name} vs {args.treatment_dir.name}")
    print("=" * 80)

    try:
        baseline = Pool(args.baseline_dir, "baseline")
        treatment = Pool(args.treatment_dir, "treatment")
    except FileNotFoundError as e:
        print(f"\n✗ Pool load failed: {e}")
        return 1

    print(f"\n  {baseline!r}")
    print(f"  {treatment!r}")

    diff_episode_counts(baseline, treatment)
    res_fails = diff_resurrected_dims_base(baseline, treatment)
    tpo_fails = diff_tpo_passthrough(baseline, treatment)
    diff_reward_distribution(baseline, treatment)
    diff_global_dim_changes(baseline, treatment)

    print("\n" + "=" * 80)
    total_fails = res_fails + tpo_fails
    if total_fails == 0:
        print("OK — fixes shift the predicted dims. Safe to escalate to retrain + model A/B.")
    else:
        print(f"FAILED — {total_fails} sanity check(s) failed. Do NOT escalate to retrain yet.")
    print("=" * 80)
    return 0 if total_fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
