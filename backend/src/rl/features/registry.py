"""Feature registry + versioning for the RL observation vector.

Every feature segment is declared here with (name, version, dim). The registry
is the single source of truth for the obs schema. When we retrain, we save the
registry state alongside the model. When we load a model for inference, we
verify the current code's registry matches.

This enables:
- Safe retraining without dim-mismatch crashes
- A/B comparison across model versions with different feature sets
- Incremental feature additions (bump version, add to registry)
- Graceful fail-fast when loading an older model with different obs schema

Live inference always uses the CURRENT registry. Historical models either
match (load cleanly) or fail fast with a clear message naming which feature
version is missing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FeatureSchema:
    """Declaration of one feature segment in the observation vector.

    name: stable identifier (e.g. "reaction", "orderflow")
    version: bumped when the feature semantics change (dim may or may not change)
    dim: number of float values this segment contributes to the obs vector
    description: human-readable note for comparison reports
    """

    name: str
    version: int
    dim: int
    description: str = ""


# ---------------------------------------------------------------------------
# The authoritative list — mirror the order in observation.py concat.
# When adding a new feature:
#   1. Append a new entry here (never reorder existing)
#   2. Bump the version of the schema in BASE_OBSERVATION_SCHEMA
#   3. The compat check will then distinguish "missing new feature" from
#      "broken old feature"
# ---------------------------------------------------------------------------

BASE_OBSERVATION_SCHEMA_VERSION = 8  # v8 (2026-05-18): orderflow 25→27 (+vsa_aligned, +stop_run_aligned)

BASE_OBSERVATION_SCHEMA: list[FeatureSchema] = [
    FeatureSchema("level_composition", 1, 31, "multi-hot (zone) or one-hot (legacy) level types"),
    FeatureSchema(
        "orderflow",
        3,
        27,
        "candle-level orderflow + 4 Tier C pattern dims + 2 approach-aligned dims (2026-05-18)",
    ),
    FeatureSchema("structure", 1, 64, "Dow Theory + session + PDH/PDL + swings"),
    FeatureSchema("tpo", 1, 38, "per-session TPO profile"),
    FeatureSchema("candles", 1, 15, "last 5 candles × 3 (delta, vol, body_ratio)"),
    FeatureSchema("zone_features", 1, 4, "zone hierarchy + member_count + strength"),
    FeatureSchema("zone_confluence", 1, 5, "zone-level FVG + single-print overlap"),
    FeatureSchema("macro", 1, 11, "VIX, DXY, yields, COT, news proximity"),
    FeatureSchema("exchange_stats", 1, 5, "CME daily OI + settlement + cleared/block vol"),
    FeatureSchema("setup_flags", 1, 14, "binary detector flags per setup"),
    FeatureSchema("amt_static", 1, 20, "Dalton day type + opening type + VA migration"),
    FeatureSchema("amt_dynamics", 1, 20, "real-time IB extensions + acceptance/rejection"),
    FeatureSchema("micro", 1, 20, "tick-level approach features (pre-touch)"),
    FeatureSchema("approach_dir", 1, 1, "+1 up / -1 down"),
    FeatureSchema("execution", 1, 7, "Fabio-style timing/auction rules"),
    FeatureSchema("session_cvd", 1, 2, "RTH-session cumulative delta ratio + sign"),
    FeatureSchema("hvn_lvn", 1, 2, "signed distance to nearest HVN/LVN"),
    FeatureSchema("big_trades_abs", 1, 2, "absolute ≥25-contract trade activity"),
    FeatureSchema("of_alignment", 1, 3, "of_score × zone_strength AND gate"),
    # Phase 3a additions (v4 schema)
    FeatureSchema(
        "reaction",
        1,
        8,
        "post-touch market reaction (velocity, aggression, rejection_speed, vol_spike, tape_compression, delta_alignment, opposing_momentum, linearity)",
    ),
    FeatureSchema(
        "patterns",
        1,
        5,
        "Fabio-style pattern detectors (pin_bar, absorption_wall, imbalance_cluster, delta_divergence, trapped_breakout)",
    ),
    FeatureSchema("zone_quality", 1, 1, "unified level quality scalar (hierarchy × members)"),
    FeatureSchema("zone_memory", 1, 3, "touch_count + last_result + time_since_last"),
    # v6 — cross-zone narrative for stacked-zone scenarios:
    FeatureSchema(
        "prev_zone",
        1,
        5,
        "signed dist to prev different zone, prev outcome (-1/0/+1), age, valid flag, stack density (zones within 5pt)",
    ),
]


AUGMENTED_SCHEMA: list[FeatureSchema] = [
    FeatureSchema("gbt_forecast", 1, 8, "TriggerGBT 8-dim forecast"),
    FeatureSchema("position_state", 1, 8, "pos_side + unrealized_R + time_in_trade + session P&L"),
    # Phase 3c session-memory addition (augmented 318 → 324):
    # stateful regime-awareness features computed chronologically per session.
    # Teaches the heads to recognize hostile regimes from recent session context
    # instead of relying on hard rules in live_inference.
    FeatureSchema(
        "session_memory",
        1,
        6,
        "rolling_5 win_rate + avg_R + DD_from_peak + consec_loss + trade_count + R_vol",
    ),
]


# ---------------------------------------------------------------------------
# Trigger observation schema (Phase 3b: 144 → 118 → 122)
# ---------------------------------------------------------------------------

TRIGGER_OBSERVATION_SCHEMA_VERSION = 4  # v4 (2026-05-18): orderflow 25→27 (+vsa_aligned, +stop_run_aligned)

TRIGGER_OBSERVATION_SCHEMA: list[FeatureSchema] = [
    FeatureSchema("structural_passthrough", 1, 10, "10 structural dims carried over from base obs"),
    FeatureSchema("micro", 1, 20, "tick-level approach features"),
    FeatureSchema(
        "orderflow", 3, 27, "candle-level orderflow + 4 Tier C pattern dims + 2 approach-aligned dims (2026-05-18)"
    ),
    FeatureSchema("candles", 1, 15, "last 5 candles × 3 features"),
    FeatureSchema("zone_features", 1, 4, "zone hierarchy + member_count + strength"),
    FeatureSchema("zone_confluence", 1, 5, "zone-level FVG + single-print overlap"),
    FeatureSchema("zone_composition", 1, 31, "level composition multi-hot"),
    FeatureSchema("approach_dir", 1, 1, "+1 up / -1 down"),
    FeatureSchema("trigger_gbt_forecast", 1, 8, "TriggerGBT 8-dim forecast"),
    FeatureSchema("exec_passthrough", 1, 3, "trades_today + time_to_close + session_pnl"),
]


def base_observation_dim() -> int:
    """Total dim of the base observation (pre-augmentation)."""
    return sum(s.dim for s in BASE_OBSERVATION_SCHEMA)


def augmented_observation_dim() -> int:
    """Total dim including GBT forecast + position state."""
    return base_observation_dim() + sum(s.dim for s in AUGMENTED_SCHEMA)


def trigger_observation_dim() -> int:
    """Total dim of the trigger observation."""
    return sum(s.dim for s in TRIGGER_OBSERVATION_SCHEMA)


def schema_hash(schema: list[FeatureSchema]) -> str:
    """Stable content hash of a schema list — used for compatibility checks."""
    key = "|".join(f"{s.name}:v{s.version}:{s.dim}" for s in schema)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def current_schema_hash() -> str:
    return schema_hash(BASE_OBSERVATION_SCHEMA + AUGMENTED_SCHEMA)


def current_trigger_schema_hash() -> str:
    return schema_hash(TRIGGER_OBSERVATION_SCHEMA)


def schema_to_dict(schema: list[FeatureSchema]) -> list[dict]:
    return [{"name": s.name, "version": s.version, "dim": s.dim, "description": s.description} for s in schema]


def schema_from_dict(raw: list[dict]) -> list[FeatureSchema]:
    return [
        FeatureSchema(name=d["name"], version=d["version"], dim=d["dim"], description=d.get("description", ""))
        for d in raw
    ]


def save_schema(path: Path | str) -> None:
    """Write current feature schema + augmented schema to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "base_version": BASE_OBSERVATION_SCHEMA_VERSION,
        "base_schema": schema_to_dict(BASE_OBSERVATION_SCHEMA),
        "augmented_schema": schema_to_dict(AUGMENTED_SCHEMA),
        "trigger_version": TRIGGER_OBSERVATION_SCHEMA_VERSION,
        "trigger_schema": schema_to_dict(TRIGGER_OBSERVATION_SCHEMA),
        "base_dim": base_observation_dim(),
        "augmented_dim": augmented_observation_dim(),
        "trigger_dim": trigger_observation_dim(),
        "schema_hash": current_schema_hash(),
        "trigger_schema_hash": current_trigger_schema_hash(),
    }
    path.write_text(json.dumps(payload, indent=2))


def load_schema(path: Path | str) -> dict:
    """Load a saved schema JSON. Returns dict with base_schema list."""
    return json.loads(Path(path).read_text())


def check_compatibility(saved_schema_path: Path | str) -> tuple[bool, str]:
    """Verify a saved model's feature schema matches the current code.

    Returns (ok, message). If ok=False, message describes what mismatches.
    """
    try:
        saved = load_schema(saved_schema_path)
    except FileNotFoundError:
        return False, f"no feature_schema.json at {saved_schema_path}"

    saved_hash = saved.get("schema_hash")
    if saved_hash == current_schema_hash():
        return True, "schemas match"

    current_by_name = {s.name: s for s in BASE_OBSERVATION_SCHEMA + AUGMENTED_SCHEMA}
    saved_list = schema_from_dict(saved.get("base_schema", []) + saved.get("augmented_schema", []))
    saved_by_name = {s.name: s for s in saved_list}

    diffs = []
    for name, cur in current_by_name.items():
        if name not in saved_by_name:
            diffs.append(f"+{name}:v{cur.version}:{cur.dim}d (added since saved model)")
        elif saved_by_name[name].version != cur.version:
            diffs.append(
                f"~{name}: saved v{saved_by_name[name].version}/{saved_by_name[name].dim}d "
                f"→ current v{cur.version}/{cur.dim}d"
            )
        elif saved_by_name[name].dim != cur.dim:
            diffs.append(
                f"~{name}: saved {saved_by_name[name].dim}d → current {cur.dim}d (dim changed without version bump!)"
            )

    for name, old in saved_by_name.items():
        if name not in current_by_name:
            diffs.append(f"-{name}:v{old.version}:{old.dim}d (removed since saved model)")

    summary = (
        "; ".join(diffs)
        if diffs
        else "hashes differ but no named feature diffs (did BASE_OBSERVATION_SCHEMA_VERSION bump?)"
    )
    return False, summary
