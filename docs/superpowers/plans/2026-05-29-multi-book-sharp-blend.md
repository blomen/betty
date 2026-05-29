# Multi-Book Sharp Blend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a per-sport weighted-harmonic blended sharp fair line (pinnacle + cloudbet + kalshi + polymarket) and shadow-record its CLV alongside the existing Pinnacle-only fair line, so we can prove per-sport whether the blend beats Pinnacle before trusting it for stakes.

**Architecture:** A pure `compute_blended_sharp_fair()` math function in `devig.py`; a thin `analysis/sharp_blend.py` orchestration layer that reads per-sport weights from `providers.yaml` and builds the blend from DB odds rows; new nullable `OppSnapshot` columns frozen at detection and backfilled at close inside `OppSnapshotService`; and a per-sport blended-vs-Pinnacle comparison in the `/api/opp-snapshots/stats` endpoint + Stats > Shadow CLV sub-tab. **The scanner's edge math is NOT touched** — the blend is shadow-only this phase. The edge-flip (using the blend for stakes) is a documented follow-up plan, gated by the shadow data this phase collects.

**Tech Stack:** Python 3.12 / SQLAlchemy / FastAPI / pytest (backend); React 19 / TypeScript / Vite (frontend). Postgres in prod, SQLite in tests.

**Scope boundary (read first):**
- IN: blend math, config, shadow capture (detection freeze + closing backfill), comparison surface.
- OUT (separate follow-up plan): wiring `use_blended_fair_for_edge` into the scanner to use the blend for stake-driving edge. That flip is config-gated and only justified after this phase's data shows a per-sport win. Do not implement it here.

**Deploy note:** Tasks 1–6 touch `backend/` → require `server-deploy.sh rebuild backend`. Task 7 is frontend → ships via `betty.bat`. The whole phase is stake-safe (no edge-path change). Do not deploy mid-plan; deploy once at the end.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/src/analysis/devig.py` | Pure blend math `compute_blended_sharp_fair` + `BlendedFair` dataclass + `_devig_market_for_outcome` helper | Modify |
| `backend/src/analysis/sharp_blend.py` | Orchestration: per-sport weight resolution + build blend from DB odds rows | Create |
| `backend/src/config/providers.yaml` | `sharp_blend` config block | Modify |
| `backend/src/config/loader.py` | Capture + expose `sharp_blend` block | Modify |
| `backend/src/db/models.py` | 5 nullable `OppSnapshot` columns + migration tuples | Modify |
| `backend/src/services/opp_snapshot_service.py` | Freeze blended fair at detection; backfill blended closing + CLV | Modify |
| `backend/src/api/routes/opp_snapshots.py` | Per-sport blended-vs-Pinnacle comparison section | Modify |
| `frontend/src/services/api/oppSnapshots.ts` | Types + fetch for the comparison section | Modify |
| `frontend/src/pages/StatsPage.tsx` | Render per-sport blended-vs-Pinnacle table in `ShadowCLVView` | Modify |
| `backend/tests/analysis/test_sharp_blend.py` | Unit tests for blend math + orchestration | Create |
| `backend/tests/services/test_opp_snapshot_blend.py` | Tests for detection freeze + closing backfill | Create |
| `backend/tests/api/test_opp_snapshots_blend.py` | Test for comparison endpoint section | Create |

---

## Task 1: Blend math — `compute_blended_sharp_fair`

**Files:**
- Modify: `backend/src/analysis/devig.py` (append after `compute_consensus_fair_odds`, ends at line 279)
- Test: `backend/tests/analysis/test_sharp_blend.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/analysis/test_sharp_blend.py`:

```python
"""Tests for the multi-book sharp blend math (analysis/devig.py)."""

import pytest

from src.analysis.devig import (
    BlendedFair,
    compute_blended_sharp_fair,
    get_fair_odds_for_outcome,
)

MEMBERS = ["pinnacle", "cloudbet", "kalshi", "polymarket"]
LIQ_GATED = {"kalshi", "polymarket"}


def _market(rows):
    """rows: {outcome: [(provider, odds, depth_usd), ...]} -> odds_by_outcome dict."""
    return {
        out: [{"provider": p, "odds": o, "depth_usd": d} for (p, o, d) in lst]
        for out, lst in rows.items()
    }


def test_only_pinnacle_returns_pinnacle_fair_parity():
    # Blend with only Pinnacle present must equal the single-source devig.
    obo = _market({"home": [("pinnacle", 1.91, None)], "away": [("pinnacle", 1.91, None)]})
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights={"pinnacle": 1.0, "cloudbet": 0.6, "max_dev_pct": 8},
        liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result is not None
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]
    expected = get_fair_odds_for_outcome("home", {"home": 1.91, "away": 1.91})
    assert result.fair_odds == pytest.approx(expected, rel=1e-9)


def test_weighted_harmonic_mean_two_sources():
    # Pinnacle fair home ~2.0 (1.91/1.91), Cloudbet fair home ~2.5 (1.80/3.00 devig).
    # Weighted harmonic mean of fair odds = inverse of weighted-mean probability.
    obo = _market({
        "home": [("pinnacle", 1.91, None), ("cloudbet", 1.80, None)],
        "away": [("pinnacle", 1.91, None), ("cloudbet", 2.20, None)],
    })
    weights = {"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 50}  # loose leash
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights=weights, liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result is not None
    assert result.n_sources == 2
    pin = get_fair_odds_for_outcome("home", {"home": 1.91, "away": 1.91})
    cb = get_fair_odds_for_outcome("home", {"home": 1.80, "away": 2.20})
    expected = 2.0 / (1.0 / pin + 1.0 / cb)  # equal-weight harmonic mean of odds
    assert result.fair_odds == pytest.approx(expected, rel=1e-9)
    assert result.clamped is False


def test_liquidity_gate_drops_thin_prediction_market():
    # Kalshi has depth below the gate -> excluded; only pinnacle qualifies.
    obo = _market({
        "home": [("pinnacle", 1.91, None), ("kalshi", 1.50, 100.0)],
        "away": [("pinnacle", 1.91, None), ("kalshi", 2.50, 100.0)],
    })
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights={"pinnacle": 1.0, "kalshi": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]


def test_liquidity_gate_admits_deep_prediction_market():
    obo = _market({
        "home": [("pinnacle", 1.91, None), ("kalshi", 1.80, 5000.0)],
        "away": [("pinnacle", 1.91, None), ("kalshi", 2.20, 5000.0)],
    })
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights={"pinnacle": 1.0, "kalshi": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result.n_sources == 2
    assert "kalshi" in result.sources


def test_guardrail_clamps_outlier_blend_toward_pinnacle():
    # Cloudbet wildly off -> blend would deviate far from Pinnacle; clamp to +/-4%.
    obo = _market({
        "home": [("pinnacle", 2.00, None), ("cloudbet", 5.00, None)],
        "away": [("pinnacle", 2.00, None), ("cloudbet", 1.25, None)],
    })
    weights = {"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 4}
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights=weights, liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    pin = get_fair_odds_for_outcome("home", {"home": 2.00, "away": 2.00})  # ~2.0
    assert result.clamped is True
    assert result.fair_odds <= pin * 1.04 + 1e-9
    assert result.fair_odds >= pin * 0.96 - 1e-9


def test_no_members_returns_none():
    obo = _market({"home": [("betsson", 1.95, None)], "away": [("betsson", 1.95, None)]})
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights={"pinnacle": 1.0, "max_dev_pct": 8},
        liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result is None


def test_incomplete_member_market_skipped():
    # Cloudbet only has 'home', not 'away' -> can't devig -> skipped.
    obo = _market({
        "home": [("pinnacle", 1.91, None), ("cloudbet", 1.80, None)],
        "away": [("pinnacle", 1.91, None)],
    })
    result = compute_blended_sharp_fair(
        outcome="home", odds_by_outcome=obo, members=MEMBERS,
        weights={"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED, liquidity_min_usd=500,
    )
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py -v`
Expected: FAIL with `ImportError: cannot import name 'BlendedFair'` / `compute_blended_sharp_fair`.

- [ ] **Step 3: Implement the blend math**

Append to `backend/src/analysis/devig.py` (after line 279). Add `from dataclasses import dataclass` to the imports at the top of the file (currently only `import logging`):

```python
from dataclasses import dataclass


@dataclass
class BlendedFair:
    """Result of a multi-book sharp blend for one outcome.

    fair_odds: the blended fair decimal odds (post-guardrail).
    pinnacle_fair: Pinnacle's own devigged fair for the outcome (None if absent).
    n_sources: number of sharp members that contributed.
    sources: sorted list of contributing provider ids.
    clamped: True if the guardrail pulled the blend back toward Pinnacle.
    """

    fair_odds: float
    pinnacle_fair: float | None
    n_sources: int
    sources: list[str]
    clamped: bool = False


def _devig_market_for_outcome(
    outcome: str, all_outcomes: list[str], p_market: dict[str, float]
) -> float | None:
    """Devig ONE provider's complete market and return its fair odds for `outcome`.

    Power method for 3-way (1x2), multiplicative for 2-way — identical selection
    to compute_consensus_fair_odds. Returns None on invalid odds.
    """
    n = len(all_outcomes)
    p_odds = [p_market[o] for o in all_outcomes]
    if any(o <= 1 for o in p_odds):
        return None
    if n >= 3:
        fair_list = devig_power(p_odds)
        return fair_list[all_outcomes.index(outcome)]
    margin = sum(1.0 / o for o in p_odds) - 1
    return p_market[outcome] * (1 + margin)


def compute_blended_sharp_fair(
    outcome: str,
    odds_by_outcome: dict[str, list[dict]],
    members: list[str],
    weights: dict[str, float],
    liquidity_gated: set[str],
    liquidity_min_usd: float,
    min_sources: int = 1,
) -> BlendedFair | None:
    """Weighted-harmonic blend of devigged fair odds across sharp members.

    Args:
        outcome: outcome to price ("home"/"away"/"draw"/etc).
        odds_by_outcome: {outcome: [{"provider","odds","depth_usd"(optional)}, ...]}.
        members: eligible blend providers (must include "pinnacle").
        weights: {provider_id: weight, ..., "max_dev_pct": float}. Providers with
            weight <= 0 or absent contribute nothing.
        liquidity_gated: providers (kalshi/polymarket) that must clear depth gate.
        liquidity_min_usd: minimum depth_usd for a gated provider to contribute.
        min_sources: minimum qualifying members for a multi-source blend.

    Returns:
        BlendedFair, or None if no member qualifies / market malformed.

    Guarantees: if only Pinnacle qualifies, returns Pinnacle's fair unchanged —
    the blend is never strictly worse than Pinnacle-only.
    """
    all_outcomes = list(odds_by_outcome.keys())
    if len(all_outcomes) < 2:
        return None

    # Build per-provider complete markets + capture depth on the priced outcome.
    provider_markets: dict[str, dict[str, float]] = {}
    provider_depth: dict[str, float] = {}
    for out, plist in odds_by_outcome.items():
        for p in plist:
            pid = p["provider"]
            if pid not in members:
                continue
            provider_markets.setdefault(pid, {})[out] = p["odds"]
            if out == outcome and p.get("depth_usd") is not None:
                provider_depth[pid] = p["depth_usd"]

    member_fairs: dict[str, float] = {}
    for pid, p_market in provider_markets.items():
        if len(p_market) != len(all_outcomes):
            continue  # incomplete market — can't devig
        if pid in liquidity_gated:
            depth = provider_depth.get(pid)
            if depth is None or depth < liquidity_min_usd:
                continue  # thin/unknown prediction-market depth — fail safe
        fair = _devig_market_for_outcome(outcome, all_outcomes, p_market)
        if fair is None or fair <= 1:
            continue
        member_fairs[pid] = fair

    if not member_fairs:
        return None

    pinnacle_fair = member_fairs.get("pinnacle")
    non_pinnacle = {k: v for k, v in member_fairs.items() if k != "pinnacle"}

    # Only Pinnacle qualified → return it unchanged (never worse than today).
    if not non_pinnacle:
        if pinnacle_fair is None:
            return None
        return BlendedFair(
            fair_odds=pinnacle_fair, pinnacle_fair=pinnacle_fair,
            n_sources=1, sources=["pinnacle"],
        )

    if len(member_fairs) < min_sources:
        return None

    # Weighted harmonic mean of fair odds == inverse of weighted-mean probability.
    weight_sum = 0.0
    inv_sum = 0.0
    for pid, fair in member_fairs.items():
        w = weights.get(pid, 0.0)
        if w <= 0:
            continue
        weight_sum += w
        inv_sum += w / fair
    if inv_sum <= 0:
        return None
    blended = weight_sum / inv_sum

    # Guardrail: clamp blend within +/- max_dev_pct of Pinnacle's fair.
    clamped = False
    max_dev = weights.get("max_dev_pct")
    if pinnacle_fair is not None and max_dev:
        lo = pinnacle_fair * (1 - max_dev / 100.0)
        hi = pinnacle_fair * (1 + max_dev / 100.0)
        if blended < lo:
            blended, clamped = lo, True
        elif blended > hi:
            blended, clamped = hi, True

    return BlendedFair(
        fair_odds=blended,
        pinnacle_fair=pinnacle_fair,
        n_sources=len(member_fairs),
        sources=sorted(member_fairs.keys()),
        clamped=clamped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/devig.py backend/tests/analysis/test_sharp_blend.py
git commit -m "feat(analysis): compute_blended_sharp_fair multi-book blend math"
```

---

## Task 2: Config — `sharp_blend` block + loader accessor

**Files:**
- Modify: `backend/src/config/providers.yaml` (add top-level `sharp_blend` block)
- Modify: `backend/src/config/loader.py` (capture block at `_load_providers`, ~line 305-343; add accessor near other getters ~line 359)
- Test: extend `backend/tests/analysis/test_sharp_blend.py`

- [ ] **Step 1: Add the config block**

Add to `backend/src/config/providers.yaml` as a new top-level key (place it just above the `providers:` map). The per-sport keys MUST match the values stored in `events.sport` — verify against the DB with `SELECT DISTINCT sport FROM events;` and adjust keys if they differ; unmatched sports fall back to `default`:

```yaml
sharp_blend:
  members: [pinnacle, cloudbet, kalshi, polymarket]
  liquidity_gated: [kalshi, polymarket]
  liquidity_min_usd: 500
  per_sport:
    default:
      pinnacle: 1.0
      cloudbet: 0.6
      kalshi: 0.5
      polymarket: 0.5
      max_dev_pct: 8
    americanfootball_nfl:
      pinnacle: 1.0
      cloudbet: 0.2
      kalshi: 0.3
      polymarket: 0.3
      max_dev_pct: 4
    soccer_epl:
      pinnacle: 1.0
      cloudbet: 0.3
      kalshi: 0.2
      polymarket: 0.2
      max_dev_pct: 4
```

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/analysis/test_sharp_blend.py`:

```python
def test_loader_exposes_sharp_blend_block():
    from src.config.loader import load_config

    cfg = load_config()
    blend = cfg.get_sharp_blend()
    assert "pinnacle" in blend["members"]
    assert set(blend["liquidity_gated"]) == {"kalshi", "polymarket"}
    assert blend["liquidity_min_usd"] == 500
    assert "default" in blend["per_sport"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py::test_loader_exposes_sharp_blend_block -v`
Expected: FAIL with `AttributeError: 'ConfigLoader' object has no attribute 'get_sharp_blend'`.

- [ ] **Step 4: Capture + expose the block**

In `backend/src/config/loader.py`, inside `_load_providers` (after the line `config = yaml.safe_load(f)`, ~line 312), add:

```python
        # Multi-book sharp blend config (see analysis/sharp_blend.py). Stored raw
        # — validation is light because keys are sport-dependent. Defaults to an
        # empty/Pinnacle-only blend if the block is missing.
        self._sharp_blend = config.get("sharp_blend", {}) or {}
```

In the same file, ensure `self._sharp_blend` is initialised in `__init__` (find `def __init__` and add `self._sharp_blend = {}` alongside the other instance attributes). Then add this method next to `get_orchestrator_config` (~line 367):

```python
    def get_sharp_blend(self) -> dict:
        """Return the raw sharp_blend config block (empty dict if unset)."""
        return self._sharp_blend
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py::test_loader_exposes_sharp_blend_block -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/config/providers.yaml backend/src/config/loader.py backend/tests/analysis/test_sharp_blend.py
git commit -m "feat(config): sharp_blend block + loader accessor"
```

---

## Task 3: Orchestration — `analysis/sharp_blend.py`

**Files:**
- Create: `backend/src/analysis/sharp_blend.py`
- Test: extend `backend/tests/analysis/test_sharp_blend.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/analysis/test_sharp_blend.py`:

```python
def test_resolve_weights_falls_back_to_default():
    from src.analysis.sharp_blend import resolve_weights

    w = resolve_weights("some_unknown_sport")
    assert w["pinnacle"] == 1.0
    assert "max_dev_pct" in w


def test_blended_fair_from_rows():
    from src.analysis.sharp_blend import blended_fair_from_rows

    # rows mimic Odds: objects with provider_id, outcome, odds, depth_usd.
    class Row:
        def __init__(self, provider_id, outcome, odds, depth_usd=None):
            self.provider_id = provider_id
            self.outcome = outcome
            self.odds = odds
            self.depth_usd = depth_usd

    rows = [
        Row("pinnacle", "home", 1.91), Row("pinnacle", "away", 1.91),
        Row("cloudbet", "home", 1.80), Row("cloudbet", "away", 2.20),
        Row("betsson", "home", 1.95), Row("betsson", "away", 1.95),  # non-member ignored
    ]
    result = blended_fair_from_rows(outcome="home", rows=rows, sport="soccer_epl")
    assert result is not None
    assert "pinnacle" in result.sources
    assert "cloudbet" in result.sources
    assert "betsson" not in result.sources
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py -k "resolve_weights or from_rows" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.analysis.sharp_blend'`.

- [ ] **Step 3: Implement the orchestration module**

Create `backend/src/analysis/sharp_blend.py`:

```python
"""Multi-book sharp-blend orchestration.

Bridges the pure blend math (analysis/devig.compute_blended_sharp_fair) to
config (providers.yaml `sharp_blend`) and DB odds rows. Shadow-only this phase:
nothing here feeds the scanner's edge math — see
docs/superpowers/specs/2026-05-29-multi-book-sharp-blend-design.md.
"""

from __future__ import annotations

from ..config.loader import load_config
from .devig import BlendedFair, compute_blended_sharp_fair

# Sensible fallback if the config block is missing entirely.
_DEFAULT_WEIGHTS = {"pinnacle": 1.0, "max_dev_pct": 8}


def _blend_config() -> dict:
    return load_config().get_sharp_blend()


def get_members() -> list[str]:
    """Eligible blend providers. Always includes pinnacle."""
    members = list(_blend_config().get("members", []))
    if "pinnacle" not in members:
        members = ["pinnacle", *members]
    return members


def resolve_weights(sport: str | None) -> dict:
    """Per-sport member weights merged over `default`. Falls back when missing."""
    cfg = _blend_config()
    per_sport = cfg.get("per_sport", {})
    default = per_sport.get("default", _DEFAULT_WEIGHTS)
    if sport and sport in per_sport:
        merged = dict(default)
        merged.update(per_sport[sport])
        return merged
    return dict(default)


def blended_fair_from_rows(outcome: str, rows: list, sport: str | None) -> BlendedFair | None:
    """Build odds_by_outcome from Odds-like rows and compute the blend.

    `rows` must all belong to ONE (event, market, point, scope) group across the
    blend members; the caller is responsible for that filtering. Each row needs
    `.provider_id`, `.outcome`, `.odds`, and optionally `.depth_usd`.
    """
    cfg = _blend_config()
    members = get_members()
    liquidity_gated = set(cfg.get("liquidity_gated", []))
    liquidity_min_usd = float(cfg.get("liquidity_min_usd", 0) or 0)

    odds_by_outcome: dict[str, list[dict]] = {}
    for r in rows:
        if r.provider_id not in members:
            continue
        if r.odds is None or r.odds <= 1:
            continue
        odds_by_outcome.setdefault(r.outcome, []).append(
            {
                "provider": r.provider_id,
                "odds": r.odds,
                "depth_usd": getattr(r, "depth_usd", None),
            }
        )

    if outcome not in odds_by_outcome:
        return None

    return compute_blended_sharp_fair(
        outcome=outcome,
        odds_by_outcome=odds_by_outcome,
        members=members,
        weights=resolve_weights(sport),
        liquidity_gated=liquidity_gated,
        liquidity_min_usd=liquidity_min_usd,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/sharp_blend.py backend/tests/analysis/test_sharp_blend.py
git commit -m "feat(analysis): sharp_blend orchestration (weights + build from rows)"
```

---

## Task 4: OppSnapshot columns + migration

**Files:**
- Modify: `backend/src/db/models.py` (OppSnapshot class ~line 946-957; `_run_pg_migrations` additions list ~line 1881-1911)
- Test: extend in Task 5 (the columns are exercised by the service tests)

- [ ] **Step 1: Add columns to the model**

In `backend/src/db/models.py`, inside `class OppSnapshot`, after the line `clv_computed_at = Column(DateTime, nullable=True)` (line 957), add:

```python
    # ---- Multi-book sharp blend (shadow). Frozen at detection / backfilled at close. ----
    blended_fair1_at_detection = Column(Float, nullable=True)
    blend_n_sources_at_detection = Column(Integer, nullable=True)
    blend_sources = Column(JSON, nullable=True)  # list[str] of contributing providers
    blended_closing_fair = Column(Float, nullable=True)
    blended_clv_pct = Column(Float, nullable=True)
```

- [ ] **Step 2: Add migration tuples**

In `_run_pg_migrations`, append to the `additions` list (after the `odds.max_stake` entry, line 1910):

```python
        # 2026-05-29 — multi-book sharp blend shadow columns on opp_snapshots.
        # All nullable; frozen at detection / backfilled at close by
        # OppSnapshotService. Edge math unaffected (shadow only).
        ("opp_snapshots", "blended_fair1_at_detection", "DOUBLE PRECISION"),
        ("opp_snapshots", "blend_n_sources_at_detection", "INTEGER"),
        ("opp_snapshots", "blend_sources", "JSON"),
        ("opp_snapshots", "blended_closing_fair", "DOUBLE PRECISION"),
        ("opp_snapshots", "blended_clv_pct", "DOUBLE PRECISION"),
```

- [ ] **Step 3: Verify model imports cleanly**

Run: `cd backend && python -c "from src.db.models import OppSnapshot; print([c.name for c in OppSnapshot.__table__.columns if 'blend' in c.name])"`
Expected: `['blended_fair1_at_detection', 'blend_n_sources_at_detection', 'blend_sources', 'blended_closing_fair', 'blended_clv_pct']`

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(db): OppSnapshot multi-book blend shadow columns + pg migration"
```

---

## Task 5: Freeze blend at detection + backfill at close

**Files:**
- Modify: `backend/src/services/opp_snapshot_service.py` (`upsert_from_opportunity` ~line 73-94; `compute_closing_clv` ~line 164-168; add a private helper)
- Test: `backend/tests/services/test_opp_snapshot_blend.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_opp_snapshot_blend.py`:

```python
"""Shadow-blend freeze + backfill in OppSnapshotService."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Base, Event, Odds, Opportunity, OppSnapshot, Provider
from src.services.opp_snapshot_service import OppSnapshotService


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for pid in ("pinnacle", "cloudbet", "betsson"):
        s.add(Provider(id=pid, name=pid))
    s.commit()
    yield s
    s.close()


def _event(session, start_offset_min):
    ev = Event(
        id="evt1", sport="soccer_epl", home_team="A", away_team="B",
        start_time=datetime.now(UTC) + timedelta(minutes=start_offset_min),
    )
    session.add(ev)
    session.commit()
    return ev


def _odds(session, provider, outcome, odds, market="1x2"):
    session.add(Odds(event_id="evt1", provider_id=provider, market=market,
                     outcome=outcome, odds=odds, scope="ft"))


def test_detection_freezes_blended_fair(session):
    _event(session, start_offset_min=120)  # not started
    _odds(session, "pinnacle", "home", 1.91); _odds(session, "pinnacle", "away", 1.91)
    _odds(session, "cloudbet", "home", 1.80); _odds(session, "cloudbet", "away", 2.20)
    session.commit()

    opp = Opportunity(
        event_id="evt1", type="value", market="1x2", outcome1="home",
        provider1_id="betsson", odds1=2.10, odds2=2.00, edge_pct=5.0, scope="ft",
    )
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)
    assert snap.blended_fair1_at_detection is not None
    assert snap.blend_n_sources_at_detection == 2
    assert set(snap.blend_sources) == {"pinnacle", "cloudbet"}


def test_detection_only_pinnacle_records_single_source(session):
    _event(session, start_offset_min=120)
    _odds(session, "pinnacle", "home", 1.91); _odds(session, "pinnacle", "away", 1.91)
    session.commit()
    opp = Opportunity(
        event_id="evt1", type="value", market="1x2", outcome1="home",
        provider1_id="betsson", odds1=2.10, odds2=2.00, edge_pct=5.0, scope="ft",
    )
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)
    assert snap.blend_n_sources_at_detection == 1
    assert snap.blend_sources == ["pinnacle"]


def test_closing_backfill_computes_blended_clv(session):
    _event(session, start_offset_min=-10)  # already started
    _odds(session, "pinnacle", "home", 2.00); _odds(session, "pinnacle", "away", 2.00)
    _odds(session, "cloudbet", "home", 2.00); _odds(session, "cloudbet", "away", 2.00)
    session.commit()
    snap = OppSnapshot(
        event_id="evt1", type="value", market="1x2", outcome1="home", scope="ft",
        provider1_id="betsson", odds1_at_detection=2.20,
        blended_fair1_at_detection=2.05,
        first_detected_at=datetime.now(UTC) - timedelta(hours=2),
        last_detected_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(snap); session.commit()

    result = OppSnapshotService(session).compute_closing_clv()
    session.refresh(snap)
    assert result["processed"] == 1
    # Closing blended fair ~2.0 (50/50 devigged); CLV = (2.20/2.00 - 1)*100 = 10%.
    assert snap.blended_closing_fair == pytest.approx(2.0, rel=1e-6)
    assert snap.blended_clv_pct == pytest.approx(10.0, rel=1e-3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_opp_snapshot_blend.py -v`
Expected: FAIL — `blended_fair1_at_detection`/`blend_n_sources_at_detection` are None (freeze not implemented).

- [ ] **Step 3: Implement detection freeze**

In `backend/src/services/opp_snapshot_service.py`, add the import at top (after the existing model import on line 11):

```python
from ..analysis.sharp_blend import blended_fair_from_rows
```

Add this private helper to the class (after `_pinnacle_closing_fair`, end of file):

```python
    def _blend_member_rows(self, snap_or_opp, market, point, scope):
        """All Odds rows (any outcome) for the event/market/point/scope across
        every provider — sharp_blend filters to members itself. Returns list[Odds]."""
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == snap_or_opp.event_id,
            Odds.market == market,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        return q.all()

    def _event_sport(self, event_id: str) -> str | None:
        ev = self.db.query(Event).filter(Event.id == event_id).first()
        return ev.sport if ev else None
```

In `upsert_from_opportunity`, after computing `fair_odds1` (line 65) and before building `snap` (line 73), add:

```python
        # Multi-book sharp blend (shadow). Computed from the same current Odds
        # rows the scanner saw this cycle — detection-time by construction.
        blend = blended_fair_from_rows(
            outcome=opp.outcome1,
            rows=self._blend_member_rows(opp, opp.market, opp.point, opp.scope),
            sport=self._event_sport(opp.event_id),
        )
        blended_fair1 = blend.fair_odds if blend else None
        blend_n_sources = blend.n_sources if blend else None
        blend_sources = blend.sources if blend else None
```

Then add these three kwargs to the `OppSnapshot(...)` constructor (inside the call, after `time_to_start_minutes_at_detection=ttk,`):

```python
            blended_fair1_at_detection=blended_fair1,
            blend_n_sources_at_detection=blend_n_sources,
            blend_sources=blend_sources,
```

- [ ] **Step 4: Implement closing backfill**

In `compute_closing_clv`, after the Pinnacle closing-fair block (after line 168, the `did_update = True` that follows `snap.pinnacle_clv_pct = ...`), add:

```python
            # ---- Blended sharp closing fair (shadow) ----
            blend = blended_fair_from_rows(
                outcome=snap.outcome1,
                rows=self._blend_member_rows(snap, snap.market, snap.point, snap.scope),
                sport=self._event_sport(snap.event_id),
            )
            if blend is not None and blend.fair_odds > 1.0:
                snap.blended_closing_fair = blend.fair_odds
                snap.blended_clv_pct = round(
                    (snap.odds1_at_detection / blend.fair_odds - 1) * 100, 2
                )
                did_update = True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_opp_snapshot_blend.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full snapshot + analysis suites for regressions**

Run: `cd backend && python -m pytest tests/analysis/test_sharp_blend.py tests/services/test_opp_snapshot_blend.py -v`
Expected: PASS. Also run any existing opp-snapshot test: `python -m pytest tests/ -k opp_snapshot -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/opp_snapshot_service.py backend/tests/services/test_opp_snapshot_blend.py
git commit -m "feat(snapshot): freeze blended fair at detection + backfill blended CLV"
```

---

## Task 6: Stats endpoint — per-sport blended-vs-Pinnacle comparison

**Files:**
- Modify: `backend/src/api/routes/opp_snapshots.py` (add a section to `get_stats`)
- Test: `backend/tests/api/test_opp_snapshots_blend.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_opp_snapshots_blend.py`:

```python
"""Per-sport blended-vs-Pinnacle comparison section in /api/opp-snapshots/stats."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_db
from src.api.main import app  # adjust if app factory differs
from src.db.models import Base, Event, OppSnapshot


@pytest.fixture
def client():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    now = datetime.now(UTC)
    s.add(Event(id="e1", sport="soccer_epl", home_team="A", away_team="B",
                start_time=now - timedelta(hours=1)))
    # 4 snapshots: blended CLV beats pinnacle on soccer_epl.
    for i in range(4):
        s.add(OppSnapshot(
            event_id="e1", type="value", market="1x2", outcome1="home", scope="ft",
            provider1_id="betsson", odds1_at_detection=2.1,
            first_detected_at=now - timedelta(hours=2),
            last_detected_at=now - timedelta(hours=2),
            clv_computed_at=now, pinnacle_clv_pct=1.0, blended_clv_pct=3.0,
        ))
    s.commit()

    def _override():
        try:
            yield s
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()
    s.close()


def test_stats_includes_sport_blend_comparison(client):
    resp = client.get("/api/opp-snapshots/stats?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert "sport_blend_comparison" in data
    rows = data["sport_blend_comparison"]
    assert len(rows) == 1
    row = rows[0]
    assert row["sport"] == "soccer_epl"
    assert row["n"] == 4
    assert row["mean_pinnacle_clv_pct"] == pytest.approx(1.0)
    assert row["mean_blended_clv_pct"] == pytest.approx(3.0)
    assert row["delta"] == pytest.approx(2.0)
```

Note: confirm the FastAPI app import path (`from src.api.main import app`) by checking an existing API test in `backend/tests/api/`; use whatever import/fixture pattern they use (there may be a shared `client` fixture in `tests/conftest.py` or `tests/api/conftest.py`). If a shared client fixture exists, drop the local one and reuse it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_opp_snapshots_blend.py -v`
Expected: FAIL — `KeyError: 'sport_blend_comparison'`.

- [ ] **Step 3: Implement the comparison section**

In `backend/src/api/routes/opp_snapshots.py`, add the `Event` import (line 10 currently imports only `OppSnapshot`):

```python
from ...db.models import Event, OppSnapshot
```

Before the final `return {...}` (line 102), add:

```python
    # ---- Per-sport blended-vs-Pinnacle comparison (drives flip decisions) ----
    # Only rows where BOTH CLV values exist, so the delta is apples-to-apples.
    blend_base = (
        db.query(
            Event.sport.label("sport"),
            func.count().label("n"),
            func.avg(OppSnapshot.pinnacle_clv_pct).label("mean_pin"),
            func.avg(OppSnapshot.blended_clv_pct).label("mean_blend"),
        )
        .join(Event, Event.id == OppSnapshot.event_id)
        .filter(
            OppSnapshot.clv_computed_at.isnot(None),
            OppSnapshot.first_detected_at > cutoff,
            OppSnapshot.pinnacle_clv_pct.isnot(None),
            OppSnapshot.blended_clv_pct.isnot(None),
        )
        .group_by(Event.sport)
        .having(func.count() >= 3)
        .order_by(func.count().desc())
        .all()
    )
    sport_blend_comparison = [
        {
            "sport": row.sport,
            "n": int(row.n),
            "mean_pinnacle_clv_pct": float(row.mean_pin) if row.mean_pin is not None else None,
            "mean_blended_clv_pct": float(row.mean_blend) if row.mean_blend is not None else None,
            "delta": (
                float(row.mean_blend) - float(row.mean_pin)
                if row.mean_blend is not None and row.mean_pin is not None
                else None
            ),
        }
        for row in blend_base
    ]
```

Change the final return to include the new section:

```python
    return {
        "summary": summary,
        "history": history,
        "breakdown": breakdown,
        "sport_blend_comparison": sport_blend_comparison,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_opp_snapshots_blend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/opp_snapshots.py backend/tests/api/test_opp_snapshots_blend.py
git commit -m "feat(api): per-sport blended-vs-pinnacle CLV comparison in opp-snapshots stats"
```

---

## Task 7: Frontend — render the per-sport comparison

**Files:**
- Modify: `frontend/src/services/api/oppSnapshots.ts` (add type + field)
- Modify: `frontend/src/pages/StatsPage.tsx` (`ShadowCLVView` ~line 1163-1182; add a table component)

- [ ] **Step 1: Add the type**

In `frontend/src/services/api/oppSnapshots.ts`, add the interface and extend `OppSnapshotStats`:

```typescript
export interface SportBlendComparisonRow {
  sport: string;
  n: number;
  mean_pinnacle_clv_pct: number | null;
  mean_blended_clv_pct: number | null;
  delta: number | null;
}

export interface OppSnapshotStats {
  summary: OppSnapshotSummary;
  history: OppSnapshotHistoryPoint[];
  breakdown: OppSnapshotBreakdownRow[];
  sport_blend_comparison: SportBlendComparisonRow[];
}
```

- [ ] **Step 2: Render the comparison table**

In `frontend/src/pages/StatsPage.tsx`, import the new type (add `SportBlendComparisonRow` to the existing `import type { ... } from '@/services/api/oppSnapshots'` on line 12).

Add this component near the other shadow components (e.g. after `BreakdownTable`):

```tsx
function SportBlendTable({ rows }: { rows: SportBlendComparisonRow[] }) {
  if (!rows.length) {
    return (
      <div className="text-muted text-xs p-3">
        No blended-vs-Pinnacle data yet — accumulating shadow CLV.
      </div>
    );
  }
  const fmt = (v: number | null) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  return (
    <div className="mt-4">
      <h3 className="text-xs font-semibold text-muted mb-1">
        Blended sharp line vs Pinnacle (per sport)
      </h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted">
            <th className="px-2 py-1 text-left">Sport</th>
            <th className="px-2 py-1 text-right">n</th>
            <th className="px-2 py-1 text-right">Pinnacle CLV</th>
            <th className="px-2 py-1 text-right">Blended CLV</th>
            <th className="px-2 py-1 text-right">Δ (blend − pin)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sport} className="border-t border-base-300">
              <td className="px-2 py-1 text-left">{r.sport}</td>
              <td className="px-2 py-1 text-right tabular-nums">{r.n}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.mean_pinnacle_clv_pct)}</td>
              <td className="px-2 py-1 text-right tabular-nums">{fmt(r.mean_blended_clv_pct)}</td>
              <td
                className={`px-2 py-1 text-right tabular-nums ${
                  (r.delta ?? 0) > 0 ? 'text-success' : (r.delta ?? 0) < 0 ? 'text-error' : ''
                }`}
              >
                {fmt(r.delta)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

In `ShadowCLVView`, render it after `<BreakdownTable rows={data.breakdown} />` (line 1180):

```tsx
      <SportBlendTable rows={data.sport_blend_comparison} />
```

- [ ] **Step 3: Verify the frontend builds + lints**

Run: `cd frontend && npm run lint && npx tsc --noEmit`
Expected: no errors. (The PostToolUse hook also runs `eslint --fix` on save.)

- [ ] **Step 4: Visually verify (optional but recommended)**

Start the local client (`local\betty.bat`), open Stats > Shadow CLV. The new table renders the empty-state message until shadow data accumulates post-deploy. Capture a screenshot if desired.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/api/oppSnapshots.ts frontend/src/pages/StatsPage.tsx
git commit -m "feat(stats-ui): per-sport blended-vs-pinnacle CLV table in Shadow CLV"
```

---

## Task 8: Deploy + verify shadow collection

**Files:** none (operational).

- [ ] **Step 1: Confirm clean fast-forward + no other agent mid-deploy**

```bash
git fetch && git log HEAD..origin/main --oneline
ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh'"
```
Expected: no surprising remote commits; no running deploy script.

- [ ] **Step 2: Merge the branch to main and push** (only when the user approves)

The work is on branch `design/multi-book-sharp-blend`. Fast-forward `main` and push per the user's normal flow.

- [ ] **Step 3: Deploy backend** (Tasks 1–6 are backend)

```bash
ssh root@148.251.40.251 "bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend"
```
Expected: deploy completes, `/health` responds.

- [ ] **Step 4: Verify the migration applied**

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c \"\\d opp_snapshots\" | grep blend"
```
Expected: the 5 `blend*` columns listed.

- [ ] **Step 5: Verify shadow data starts populating** (after a scan + an event close cycle)

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose exec -T postgres psql -U betty -d betty -c \"SELECT count(*) FILTER (WHERE blended_fair1_at_detection IS NOT NULL) AS frozen, count(*) FILTER (WHERE blended_clv_pct IS NOT NULL) AS backfilled FROM opp_snapshots WHERE first_detected_at > now() - interval '1 day';\""
```
Expected: `frozen` climbs after the next scan; `backfilled` climbs after events start closing.

- [ ] **Step 6: Wait for data, then decide flips**

Once a sport reaches the evidence bar (target n ≥ 200, sustained positive `delta` in the Shadow CLV sub-tab), schedule the follow-up flip plan (wiring `use_blended_fair_for_edge` into the scanner). Do NOT flip before the data supports it.

---

## Self-Review

**Spec coverage:**
- Component 1 (blend function) → Task 1 ✓
- Component 2 (config) → Task 2 ✓
- Component 3 (shadow capture) → Tasks 3 (orchestration), 4 (columns), 5 (detection freeze) ✓
- Component 4 (closing backfill) → Task 5 ✓
- Component 5 (comparison surface) → Tasks 6 (endpoint) + 7 (frontend) ✓
- Component 6 (rollout/flip) → intentionally OUT of this plan; documented as follow-up in Task 8 Step 6 and the scope boundary. ✓ (matches spec's shadow→validate→flip phasing)
- Error handling (insufficient sources, Pinnacle absent, liquidity gate null depth, incomplete market) → covered by Task 1 tests + the fallback logic. ✓
- Currency non-applicability → noted in blend math docstring. ✓

**Type consistency:** `BlendedFair` fields (`fair_odds`, `pinnacle_fair`, `n_sources`, `sources`, `clamped`) used consistently in Tasks 1/3/5. Config keys (`members`, `liquidity_gated`, `liquidity_min_usd`, `per_sport`, `max_dev_pct`) consistent across Tasks 2/3. Endpoint key `sport_blend_comparison` + row fields (`sport`, `n`, `mean_pinnacle_clv_pct`, `mean_blended_clv_pct`, `delta`) consistent across Tasks 6/7.

**Placeholder scan:** No TBD/TODO. Two flagged verification points (Event.sport values vs config keys in Task 2; FastAPI app import path in Task 6) are explicit instructions to check existing code, not vague requirements — the engineer is told exactly what to verify and the fallback behavior.

**Open verification (do during execution, not blocking):**
- Task 2: confirm `events.sport` actual values (`SELECT DISTINCT sport FROM events;`) so per-sport keys match; unmatched → `default` (safe).
- Task 6: confirm the API test harness import/fixture pattern from a neighboring `tests/api/` test.
