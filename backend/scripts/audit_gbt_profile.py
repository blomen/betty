"""PROFILE group audit — TPO + Volume Profile + zone-derived dims.

PROFILE is the most R-impactful group (+0.203R) and PROFILE_TPO is the
SINGLE most impactful (+0.241R) — but they get 11% + 1.1% of GBT SHAP
respectively. Massive under-weighting opportunity.

PROFILE methodology category covers (per observation_index):
  - zone_composition (35)   — multi-hot of which level types are in the zone
  - tpo (38)                — TPO per-session POC/VAH/VAL + opening type
  - zone_features (4)       — width, member count, hierarchy, session relevance
  - zone_confluence (5)     — nearest_higher/lower_zone_dist, FVG overlap
  - hvn_lvn (2)             — HVN/LVN distance
  - zone_quality (1)        — overall zone quality score

Trigger_obs has only 40 of 85 PROFILE dims (zone_composition + zone_features
+ zone_confluence). The TPO segment (38 dims) is mostly missing from trigger.

This audit:
  1. Per-PROFILE-dim R impact + runner rate (same as OF)
  2. Identify the TOP 5 PROFILE dims for runners and stops
  3. Quantify the "hidden TPO" gap — what does GBT not see?
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")

from src.rl.agent.trigger_gbt import TriggerGBT
from src.rl.features.observation_index import _CATEGORY_SEGMENTS, _SEGMENT_OFFSETS

EP_DIR = Path("/app/data/rl/episodes")
MD_DIR = Path("/app/data/rl/models")
OUT_DIR = Path("/app/data/rl/audit_reports")

RUNNER_THRESHOLD = 2.25
STOP_THRESHOLD = -1.25


def _per_dim_metrics(values: np.ndarray, rc: np.ndarray, rr: np.ndarray, actions: np.ndarray) -> dict:
    if values.std() < 1e-9:
        return {"dead": True, "nonzero_pct": float((values != 0).mean())}
    realized = np.where(actions == 0, rc, rr)
    q1, q4 = np.percentile(values, [25, 75])
    q1_mask = values <= q1
    q4_mask = values >= q4
    if q1_mask.sum() == 0 or q4_mask.sum() == 0:
        return {"dead": True, "nonzero_pct": float((values != 0).mean())}
    rc_q4, rr_q4 = rc[q4_mask], rr[q4_mask]
    r_q1 = realized[q1_mask]
    r_q4 = realized[q4_mask]
    return {
        "dead": False,
        "nonzero_pct": float((values != 0).mean()),
        "mean_R_q1": float(r_q1.mean()),
        "mean_R_q4": float(r_q4.mean()),
        "delta_R": float(r_q4.mean() - r_q1.mean()),
        "runner_rate_q4": float((r_q4 >= RUNNER_THRESHOLD).mean()),
        "stop_rate_q4": float((r_q4 <= STOP_THRESHOLD).mean()),
        "cont_runner_q4": float((rc_q4 >= RUNNER_THRESHOLD).mean()),
        "rev_runner_q4": float((rr_q4 >= RUNNER_THRESHOLD).mean()),
    }


def main() -> None:
    print("=" * 100)
    print("PROFILE group audit — TPO + Volume Profile + zone-derived dims")
    print("=" * 100)

    trig = np.load(EP_DIR / "trigger_observations.npy")
    obs_full = np.load(EP_DIR / "observations.npy")
    rc = np.load(EP_DIR / "rewards_cont.npy")
    rr = np.load(EP_DIR / "rewards_rev.npy")
    n = len(trig)
    gbt = TriggerGBT.load(MD_DIR / "trigger_gbt_v5.joblib")
    actions, _, _ = gbt.predict_direction_batch(trig.astype(np.float32))

    # PROFILE segments per observation_index (operate on FULL obs since
    # trigger only has 40/85 dims)
    profile_segments = _CATEGORY_SEGMENTS["PROFILE"]
    profile_dims_total = sum(s["size"] for s in profile_segments)
    print(f"\nPool: {n}  |  PROFILE total dims in full obs: {profile_dims_total}")
    print(f"PROFILE segments: {[s['name'] for s in profile_segments]}")

    # Walk each segment's dims
    all_findings = []
    for seg in profile_segments:
        seg_name = seg["name"]
        labels = seg["labels"]
        start, end = _SEGMENT_OFFSETS[seg_name]
        size = end - start
        print(f"\n{'=' * 100}")
        print(f"SEGMENT: {seg_name}  ({size} dims, indices {start}-{end})")
        print(f"{'=' * 100}")
        print(
            f"\n{'idx':>4} {'label':<28} {'nz%':>6} {'ΔR':>8} {'mean_R Q4':>10} {'runner% Q4':>11} {'stop% Q4':>9} {'cont/rev run Q4':>16}"
        )
        print("-" * 95)
        seg_findings = []
        for i, label in enumerate(labels):
            full_idx = start + i
            col = obs_full[:, full_idx]
            m = _per_dim_metrics(col, rc, rr, actions)
            if m.get("dead"):
                print(f"{full_idx:>4} {label:<28} {100 * m['nonzero_pct']:>5.1f}% {'-- dead --':>40}")
                continue
            print(
                f"{full_idx:>4} {label:<28} {100 * m['nonzero_pct']:>5.1f}% {m['delta_R']:>+8.3f} {m['mean_R_q4']:>+10.3f} "
                f"{100 * m['runner_rate_q4']:>10.1f}% {100 * m['stop_rate_q4']:>8.1f}% "
                f"{100 * m['cont_runner_q4']:>6.1f}/{100 * m['rev_runner_q4']:>4.1f}"
            )
            seg_findings.append({"label": label, "segment": seg_name, "full_idx": full_idx, **m})
        all_findings.extend(seg_findings)

    # ---- LEADERBOARDS ----
    print("\n" + "=" * 100)
    print("PROFILE LEADERBOARDS")
    print("=" * 100)

    alive = [f for f in all_findings if not f.get("dead")]

    print("\n🚀 Top 10 by mean-R delta (Q4 - Q1):")
    for r in sorted(alive, key=lambda x: -x["delta_R"])[:10]:
        print(f"  {r['segment']:<18} {r['label']:<26} ΔR={r['delta_R']:+.3f}  Q4_mean={r['mean_R_q4']:+.3f}")

    print("\n⚠ Top 10 by stop-rate at Q4 (SKIP signals):")
    for r in sorted(alive, key=lambda x: -x["stop_rate_q4"])[:10]:
        print(f"  {r['segment']:<18} {r['label']:<26} stop@Q4={100 * r['stop_rate_q4']:.1f}%  ΔR={r['delta_R']:+.3f}")

    print("\n🏃 Top 10 by runner-rate at Q4:")
    for r in sorted(alive, key=lambda x: -x["runner_rate_q4"])[:10]:
        print(
            f"  {r['segment']:<18} {r['label']:<26} runner@Q4={100 * r['runner_rate_q4']:.1f}%  ΔR={r['delta_R']:+.3f}"
        )

    print("\n💀 Dead PROFILE dims (no variance or always-0):")
    dead = [f for f in all_findings if f.get("dead")]
    for f in dead:
        print(f"  {f['segment']:<18} {f['label']:<26} nonzero={100 * f['nonzero_pct']:.1f}%")

    # ---- TPO GAP — what GBT doesn't see ----
    print("\n" + "=" * 100)
    print("TPO GAP — PROFILE_TPO is +0.241R but only 3/38 dims in trigger_obs")
    print("=" * 100)
    print("\nTop TPO dims that GBT cannot see (would benefit if added to trigger_obs):")
    tpo_findings = [f for f in alive if f["segment"] == "tpo"]
    top_tpo_for_runners = sorted(tpo_findings, key=lambda x: -x["delta_R"])[:5]
    for r in top_tpo_for_runners:
        print(f"  {r['label']:<28} ΔR={r['delta_R']:+.3f}  Q4 runner%={100 * r['runner_rate_q4']:.1f}%")

    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"gbt_profile_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": n,
        "findings": all_findings,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
