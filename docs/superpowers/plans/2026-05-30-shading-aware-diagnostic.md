# Shading-Aware Edge Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conservative per-outcome shading-RISK diagnostic (low/elevated/high), surface it as a badge, and record it + an odds bucket on each opp snapshot so the Shadow-CLV surface can show whether high-shading-risk bets realize worse CLV per odds bucket — with ZERO edge/stake change.

**Architecture:** A pure `compute_shading` classifier (consensus_lean spine + 2-way-only favorite-longshot flag) writes a `"shading"` key into the existing `Opportunity.annotations` pipe (no schema change). The existing `opp_snapshots` table (from sub-project #1) freezes `shading_risk` + `odds_bucket` at detection; the existing `/api/opp-snapshots/stats` endpoint gains a `shading_clv_breakdown` section. Frontend gets a badge + a small table.

**Tech Stack:** Python 3.12 / SQLAlchemy / FastAPI / pytest (backend); React 19 / TS / vitest (frontend). SQLite in tests.

**Spec:** `docs/superpowers/specs/2026-05-30-shading-aware-diagnostic-design.md`

**Scope:** Diagnostic + CLV-bucket validation ONLY. No live edge/stake change (a stake throttle is a deferred, data-gated future plan). Premise note: Pinnacle barely shades; FLB is a devig-method artifact already handled by power-devig on 1x2 — hence the FLB flag fires on 2-way markets only.

**Deploy note:** Backend rebuild (analysis + analyzer + models + snapshot service + endpoint); frontend via `betty.bat`. Migration = 2 nullable columns (safe). Zero betting-path change — shadow-safe.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/src/analysis/shading.py` | `ShadingSignal` dataclass + tunable constants + `compute_shading` pure classifier | Create |
| `backend/tests/analysis/test_shading.py` | Unit tests for `compute_shading` | Create |
| `backend/src/pipeline/analyzer.py` | Add `"shading"` to the annotations dict (~407-418) | Modify |
| `backend/src/db/models.py` | 2 nullable `OppSnapshot` cols + migration tuples | Modify |
| `backend/src/services/opp_snapshot_service.py` | Freeze `shading_risk` + `odds_bucket` at detection | Modify |
| `backend/tests/services/test_opp_snapshot_shading.py` | Snapshot freeze tests | Create |
| `backend/src/api/routes/opp_snapshots.py` | `shading_clv_breakdown` section | Modify |
| `backend/tests/api/test_opp_snapshots_shading.py` | Endpoint test | Create |
| `frontend/src/services/api/oppSnapshots.ts` | Type for the breakdown rows | Modify |
| `frontend/src/pages/PlayPage.tsx` | `shade` badge in `renderAnnotationBadges` | Modify |
| `frontend/src/pages/StatsPage.tsx` | Render `shading_clv_breakdown` table | Modify |

---

## Task 1: `compute_shading` pure classifier

**Files:**
- Create: `backend/src/analysis/shading.py`
- Test: `backend/tests/analysis/test_shading.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/analysis/test_shading.py`:

```python
"""Tests for the shading-risk classifier (analysis/shading.py)."""

from src.analysis.shading import ShadingSignal, compute_shading


def _lean(lean: str, divergence_pp: float) -> dict:
    return {"lean": lean, "divergence_pp": divergence_pp}


def test_none_when_no_consensus_lean():
    assert compute_shading(0.5, "moneyline", None) is None


def test_stale_outlier_high_divergence_is_high():
    sig = compute_shading(0.55, "moneyline", _lean("stale_outlier", 6.0))
    assert sig is not None
    assert sig.risk == "high"
    assert sig.divergence_pp == 6.0


def test_stale_outlier_moderate_divergence_is_elevated():
    sig = compute_shading(0.55, "moneyline", _lean("stale_outlier", 2.5))
    assert sig.risk == "elevated"


def test_sharp_value_lean_is_low():
    sig = compute_shading(0.55, "moneyline", _lean("sharp_value", -5.0))
    assert sig.risk == "low"


def test_market_lag_lean_is_low():
    sig = compute_shading(0.55, "moneyline", _lean("market_lag", 0.5))
    assert sig.risk == "low"


def test_flb_flag_fires_on_two_way_heavy_favorite():
    sig = compute_shading(0.90, "moneyline", _lean("market_lag", 0.0))
    assert sig.flb_contrib is True
    assert sig.risk == "elevated"  # FLB lifts low -> elevated, never to high alone


def test_flb_flag_fires_on_two_way_longshot():
    sig = compute_shading(0.05, "total", _lean("market_lag", 0.0))
    assert sig.flb_contrib is True


def test_flb_flag_never_fires_on_1x2():
    # 1x2 uses power devig already → FLB neutralized → no flb contribution.
    sig = compute_shading(0.90, "1x2", _lean("market_lag", 0.0))
    assert sig.flb_contrib is False
    assert sig.risk == "low"


def test_favorite_side_flag():
    assert compute_shading(0.70, "moneyline", _lean("market_lag", 0.0)).favorite_side is True
    assert compute_shading(0.30, "moneyline", _lean("market_lag", 0.0)).favorite_side is False


def test_to_dict_shape():
    d = compute_shading(0.90, "moneyline", _lean("stale_outlier", 6.0)).to_dict()
    assert set(d) == {"risk", "favorite_side", "fav_prob", "divergence_pp", "flb_contrib", "reason"}
    assert d["risk"] == "high"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/analysis/test_shading.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.analysis.shading'`.

- [ ] **Step 3: Implement the classifier**

Create `backend/src/analysis/shading.py`:

```python
"""Per-outcome shading-RISK diagnostic.

Conservative, READ-ONLY signal. Never mutates edge or stake — it only labels how
likely a value bet's "edge" is a shading/devig artifact rather than real value,
so realized CLV can be sliced by (odds_bucket x shading_risk) and a live
correction considered LATER, from data.

Grounded in verified research (workflow understand-shading-gap, 2026-05-30):
  - Pinnacle barely shades toward the public → we do NOT build a Pinnacle
    un-shading offset (would add error).
  - The residual favorite-longshot bias is a devig-METHOD artifact, already
    neutralized on 1x2 by power devig (devig.get_fair_odds_for_outcome). So the
    FLB flag fires on 2-way markets only.
  - Over-correction is the dominant risk → this stays diagnostic; thresholds are
    starting HYPOTHESES to backtest on Betty's own CLV-by-bucket data, NOT laws.

The spine is the existing consensus_lean signal (soft-consensus vs Pinnacle): a
`stale_outlier` lean means the soft books price the outcome MORE likely than
Pinnacle, i.e. the Pinnacle price we're beating may itself be shaded/stale on
this side — the cleanest available shading proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Tunable thresholds (BACKTEST HYPOTHESES, not established constants) ──
# Two-way markets where multiplicative devig leaves favorite-longshot bias
# un-neutralized (1x2 uses power devig, so it's excluded).
TWO_WAY_MARKETS: frozenset[str] = frozenset({"moneyline", "spread", "total"})
# A devigged fair prob at/above this (favorite) or at/below its complement
# (longshot) is "extreme" enough to flag FLB on a 2-way market.
SHADING_FAV_EXTREME_PROB: float = 0.80
# consensus_lean divergence (pp) past which a stale_outlier lean is "elevated".
SHADING_ELEVATED_PP: float = 2.0
# ... and past which it is "high".
SHADING_HIGH_PP: float = 4.0


@dataclass(frozen=True)
class ShadingSignal:
    """Read-only shading-risk label for one outcome of a value bet."""

    risk: str                     # "low" | "elevated" | "high"
    favorite_side: bool           # is this outcome the market favorite?
    fav_prob: float               # the outcome's devigged fair probability
    divergence_pp: float | None   # consensus_lean divergence (the spine)
    flb_contrib: bool             # favorite-longshot flag fired (2-way only)
    reason: str                   # human-readable "why"

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "favorite_side": self.favorite_side,
            "fav_prob": round(self.fav_prob, 4),
            "divergence_pp": (round(self.divergence_pp, 2) if self.divergence_pp is not None else None),
            "flb_contrib": self.flb_contrib,
            "reason": self.reason,
        }


def compute_shading(
    fair_probability: float,
    market: str,
    consensus_lean: dict | None,
    *,
    fav_extreme_prob: float = SHADING_FAV_EXTREME_PROB,
    elevated_divergence_pp: float = SHADING_ELEVATED_PP,
    high_divergence_pp: float = SHADING_HIGH_PP,
) -> ShadingSignal | None:
    """Classify shading risk for one outcome. Returns None if no consensus_lean.

    Args:
        fair_probability: Pinnacle devigged fair prob for this outcome (0..1).
        market: market key ("moneyline"/"spread"/"total"/"1x2").
        consensus_lean: ConsensusLean.to_dict() ({"lean","divergence_pp",...}) or None.
    """
    if not consensus_lean:
        return None

    lean = consensus_lean.get("lean")
    divergence_pp = consensus_lean.get("divergence_pp")

    # ── Spine: consensus_lean ──
    # stale_outlier = softs say MORE likely than Pinnacle → Pinnacle price may be
    # shaded/stale on this side (the side we're taking value on). Tier by magnitude.
    spine = "low"
    reasons: list[str] = []
    if lean == "stale_outlier" and isinstance(divergence_pp, (int, float)):
        adiv = abs(divergence_pp)
        if adiv >= high_divergence_pp:
            spine = "high"
            reasons.append(f"soft consensus diverges {divergence_pp:+.1f}pp (stale-outlier, high)")
        elif adiv >= elevated_divergence_pp:
            spine = "elevated"
            reasons.append(f"soft consensus diverges {divergence_pp:+.1f}pp (stale-outlier)")

    # ── FLB flag: 2-way markets only (1x2 already power-devigged) ──
    flb_contrib = False
    if market in TWO_WAY_MARKETS and (
        fair_probability >= fav_extreme_prob or fair_probability <= (1.0 - fav_extreme_prob)
    ):
        flb_contrib = True
        side = "favorite" if fair_probability >= 0.5 else "longshot"
        reasons.append(f"extreme {side} on 2-way market (FLB-prone devig)")

    # ── Combine: FLB can lift low→elevated, never alone to high ──
    risk = spine
    if flb_contrib and risk == "low":
        risk = "elevated"

    if not reasons:
        reasons.append("no elevated shading signals")

    return ShadingSignal(
        risk=risk,
        favorite_side=fair_probability >= 0.5,
        fav_prob=fair_probability,
        divergence_pp=divergence_pp if isinstance(divergence_pp, (int, float)) else None,
        flb_contrib=flb_contrib,
        reason="; ".join(reasons),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/analysis/test_shading.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/shading.py backend/tests/analysis/test_shading.py
git commit -m "feat(analysis): compute_shading risk classifier (consensus-lean spine + 2-way FLB)"
```

---

## Task 2: Wire shading into the annotations pipe

**Files:**
- Modify: `backend/src/pipeline/analyzer.py` (imports near top; the annotations block ~407-418)

- [ ] **Step 1: Add the import**

In `backend/src/pipeline/analyzer.py`, near the other `from ..analysis...` imports (search for `from ..analysis.consensus_lean import compute_consensus_lean`), add:

```python
from ..analysis.shading import compute_shading
```

- [ ] **Step 2: Compute + attach the annotation**

In `analyzer.py`, the existing block (~407-418) is:

```python
            lean_obj = compute_consensus_lean(
                odds_snapshot=vb.odds_snapshot,
                sharp_fair_probability=vb.fair_probability,
                bet_provider=vb.provider,
            )
            annotations: dict | None = None
            if key_info or steam_sig or lean_obj:
                annotations = {
                    "key_number": key_info.to_dict() if key_info else None,
                    "steam_signal": steam_sig,
                    "consensus_lean": lean_obj.to_dict() if lean_obj else None,
                }
```

Replace it with (adds the shading computation + key; shading reads the same `lean_obj`):

```python
            lean_obj = compute_consensus_lean(
                odds_snapshot=vb.odds_snapshot,
                sharp_fair_probability=vb.fair_probability,
                bet_provider=vb.provider,
            )
            lean_dict = lean_obj.to_dict() if lean_obj else None
            shading_obj = compute_shading(
                fair_probability=vb.fair_probability,
                market=clean_market,
                consensus_lean=lean_dict,
            )
            annotations: dict | None = None
            if key_info or steam_sig or lean_obj or shading_obj:
                annotations = {
                    "key_number": key_info.to_dict() if key_info else None,
                    "steam_signal": steam_sig,
                    "consensus_lean": lean_dict,
                    "shading": shading_obj.to_dict() if shading_obj else None,
                }
```

NOTE: confirm the market variable name in this scope is `clean_market` (used by the steam lookup just above at ~402). If it's named differently, use the real one — it must be the normalized market key ("moneyline"/"spread"/"total"/"1x2") that matches `TWO_WAY_MARKETS`.

- [ ] **Step 3: Verify analyzer imports cleanly**

Run: `cd backend && python -c "from src.pipeline.analyzer import OpportunityAnalyzer" 2>&1 | tail -3` (use the real class/symbol name if different — the goal is to confirm no import error). Then run the analysis tests to ensure nothing broke: `cd backend && python -m pytest tests/analysis/test_shading.py -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/pipeline/analyzer.py
git commit -m "feat(analyzer): attach shading annotation alongside consensus_lean"
```

---

## Task 3: Freeze shading_risk + odds_bucket on OppSnapshot

**Files:**
- Modify: `backend/src/db/models.py` (`OppSnapshot` class — after `blended_clv_pct` ~line 964; migration `additions` list — after the blend tuples ~line 1925)
- Modify: `backend/src/services/opp_snapshot_service.py` (`upsert_from_opportunity` — add fields)
- Test: `backend/tests/services/test_opp_snapshot_shading.py` (create)

- [ ] **Step 1: Add columns to the model**

In `backend/src/db/models.py`, in `class OppSnapshot`, after `blended_clv_pct = Column(Float, nullable=True)` (~line 964), add:

```python
    # ---- Shading diagnostic (sub-project #4). Frozen at detection. ----
    shading_risk = Column(String, nullable=True)   # "low" | "elevated" | "high"
    odds_bucket = Column(String, nullable=True)     # from patterns._odds_range(odds1)
```

- [ ] **Step 2: Add migration tuples**

In `_run_pg_migrations`, append to the `additions` list after the blend tuples (after `("opp_snapshots", "blended_clv_pct", "DOUBLE PRECISION")` ~line 1925):

```python
        # 2026-05-30 — shading diagnostic (sub-project #4) on opp_snapshots.
        # Nullable; frozen at detection. Diagnostic only (no edge/stake change).
        ("opp_snapshots", "shading_risk", "VARCHAR"),
        ("opp_snapshots", "odds_bucket", "VARCHAR"),
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/services/test_opp_snapshot_shading.py`:

```python
"""Freeze of shading_risk + odds_bucket in OppSnapshotService."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Base, Event, Opportunity, OppSnapshot, Provider
from src.services.opp_snapshot_service import OppSnapshotService


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Provider(id="betsson", name="betsson"))
    s.add(Event(id="evt1", sport="soccer_epl", home_team="A", away_team="B",
                start_time=datetime.now(UTC) + timedelta(hours=2)))
    s.commit()
    yield s
    s.close()


def _opp(**over):
    base = dict(
        event_id="evt1", type="value", market="moneyline", outcome1="home",
        provider1_id="betsson", odds1=3.0, odds2=2.8, edge_pct=5.0, scope="ft",
        annotations={"shading": {"risk": "high"}},
    )
    base.update(over)
    return Opportunity(**base)


def test_freezes_shading_risk_and_bucket(session):
    snap = OppSnapshotService(session).upsert_from_opportunity(_opp(odds1=3.0))
    assert snap.shading_risk == "high"
    assert snap.odds_bucket == "2.5-4.0"  # patterns._odds_range(3.0)


def test_bucket_low_odds(session):
    snap = OppSnapshotService(session).upsert_from_opportunity(_opp(odds1=1.4, outcome1="away"))
    assert snap.odds_bucket == "<1.5"


def test_null_when_no_shading_annotation(session):
    snap = OppSnapshotService(session).upsert_from_opportunity(
        _opp(outcome1="draw", annotations={"steam_signal": None})
    )
    assert snap.shading_risk is None
    assert snap.odds_bucket == "2.5-4.0"  # bucket always set from odds1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_opp_snapshot_shading.py -v`
Expected: FAIL — `shading_risk`/`odds_bucket` are None (freeze not implemented).

- [ ] **Step 5: Implement the freeze**

In `backend/src/services/opp_snapshot_service.py`, add the import near the top (with the other `from ..analysis...`/model imports):

```python
from ..analysis.patterns import _odds_range
```

In `upsert_from_opportunity`, after the blend block (after `blend_sources = blend.sources if blend else None`, ~line 85) and before the `snap = OppSnapshot(...)` constructor, add:

```python
        # Shading diagnostic (sub-project #4) — frozen at detection from the
        # opportunity's annotation. odds_bucket always set from odds1 so every
        # snapshot is sliceable, even when no shading annotation exists.
        shading_ann = (opp.annotations or {}).get("shading") if isinstance(opp.annotations, dict) else None
        shading_risk = shading_ann.get("risk") if isinstance(shading_ann, dict) else None
        odds_bucket = _odds_range(opp.odds1) if opp.odds1 and opp.odds1 > 1.0 else None
```

Then add these two kwargs to the `OppSnapshot(...)` constructor (after `blend_sources=blend_sources,` ~line 107):

```python
            shading_risk=shading_risk,
            odds_bucket=odds_bucket,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_opp_snapshot_shading.py -v`
Expected: PASS (3 tests). Also: `cd backend && python -m pytest tests/services/ -k opp_snapshot -q` → no regression in the #1 snapshot tests.

- [ ] **Step 7: Commit**

```bash
git add backend/src/db/models.py backend/src/services/opp_snapshot_service.py backend/tests/services/test_opp_snapshot_shading.py
git commit -m "feat(snapshot): freeze shading_risk + odds_bucket at detection"
```

---

## Task 4: `shading_clv_breakdown` in the stats endpoint

**Files:**
- Modify: `backend/src/api/routes/opp_snapshots.py` (add a section to `get_stats`)
- Test: `backend/tests/api/test_opp_snapshots_shading.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_opp_snapshots_shading.py`. First inspect a sibling test in `backend/tests/api/` (e.g. `test_opp_snapshots_blend.py` from #1) and COPY its exact app-import + DB-override fixture pattern. Skeleton:

```python
"""shading_clv_breakdown section of /api/opp-snapshots/stats."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import app  # match the sibling test's real import
from src.api.deps import get_db
from src.db.models import Base, Event, OppSnapshot, Provider


@pytest.fixture
def client():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    now = datetime.now(UTC)
    s.add(Provider(id="betsson", name="betsson"))
    s.add(Event(id="e1", sport="soccer_epl", home_team="A", away_team="B", start_time=now - timedelta(hours=1)))
    # 4 high-risk snapshots in the 2.5-4.0 bucket, CLV computed.
    for i in range(4):
        s.add(OppSnapshot(
            event_id="e1", type="value", market="moneyline", outcome1=f"o{i}", scope="ft",
            provider1_id="betsson", odds1_at_detection=3.0,
            first_detected_at=now - timedelta(hours=2), last_detected_at=now - timedelta(hours=2),
            clv_computed_at=now, pinnacle_clv_pct=-2.0, shading_risk="high", odds_bucket="2.5-4.0",
        ))
    s.commit()
    app.dependency_overrides[get_db] = lambda: s
    yield TestClient(app)
    app.dependency_overrides.clear()
    s.close()


def test_stats_includes_shading_breakdown(client):
    data = client.get("/api/opp-snapshots/stats?days=30").json()
    assert "shading_clv_breakdown" in data
    rows = data["shading_clv_breakdown"]
    assert len(rows) == 1
    row = rows[0]
    assert row["odds_bucket"] == "2.5-4.0"
    assert row["shading_risk"] == "high"
    assert row["n"] == 4
    assert row["mean_pinnacle_clv_pct"] == pytest.approx(-2.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/api/test_opp_snapshots_shading.py -v`
Expected: FAIL — `KeyError: 'shading_clv_breakdown'`.

- [ ] **Step 3: Implement the section**

In `backend/src/api/routes/opp_snapshots.py`, inside `get_stats`, before the final `return {...}` add (uses the existing `cutoff` + `func` already imported in #1's version of this file):

```python
    # ---- Shading-risk x odds-bucket CLV breakdown (over-correction detector) ----
    shading_rows = (
        db.query(
            OppSnapshot.odds_bucket.label("odds_bucket"),
            OppSnapshot.shading_risk.label("shading_risk"),
            func.count().label("n"),
            func.avg(OppSnapshot.pinnacle_clv_pct).label("mean_pin"),
        )
        .filter(
            OppSnapshot.clv_computed_at.isnot(None),
            OppSnapshot.first_detected_at > cutoff,
            OppSnapshot.pinnacle_clv_pct.isnot(None),
            OppSnapshot.shading_risk.isnot(None),
            OppSnapshot.odds_bucket.isnot(None),
        )
        .group_by(OppSnapshot.odds_bucket, OppSnapshot.shading_risk)
        .having(func.count() >= 3)
        .order_by(OppSnapshot.odds_bucket, OppSnapshot.shading_risk)
        .all()
    )
    shading_clv_breakdown = [
        {
            "odds_bucket": r.odds_bucket,
            "shading_risk": r.shading_risk,
            "n": int(r.n),
            "mean_pinnacle_clv_pct": float(r.mean_pin) if r.mean_pin is not None else None,
        }
        for r in shading_rows
    ]
```

Add `"shading_clv_breakdown": shading_clv_breakdown,` to the returned dict (alongside `summary`/`history`/`breakdown`/`sport_blend_comparison`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/api/test_opp_snapshots_shading.py -v`
Expected: PASS. Regression: `cd backend && python -m pytest tests/ -k opp_snapshot -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/opp_snapshots.py backend/tests/api/test_opp_snapshots_shading.py
git commit -m "feat(api): shading-risk x odds-bucket CLV breakdown in opp-snapshots stats"
```

---

## Task 5: Frontend — shading badge + breakdown table

**Files:**
- Modify: `frontend/src/services/api/oppSnapshots.ts` (type + field)
- Modify: `frontend/src/pages/PlayPage.tsx` (`renderAnnotationBadges` ~2699-2735)
- Modify: `frontend/src/pages/StatsPage.tsx` (Shadow CLV view)

- [ ] **Step 1: Add the API type**

In `frontend/src/services/api/oppSnapshots.ts`, add:

```typescript
export interface ShadingClvRow {
  odds_bucket: string;
  shading_risk: string;
  n: number;
  mean_pinnacle_clv_pct: number | null;
}
```

And add `shading_clv_breakdown: ShadingClvRow[];` to the `OppSnapshotStats` interface.

- [ ] **Step 2: Add the shade badge in PlayPage**

In `frontend/src/pages/PlayPage.tsx`, inside `renderAnnotationBadges` (after the `steam` pill block ~line 2735), add:

```tsx
        {(() => {
          const sh = ann.shading as { risk?: string; reason?: string } | null | undefined;
          if (!sh?.risk || sh.risk === 'low') return null;
          const cls = sh.risk === 'high'
            ? 'bg-red-900/30 border-red-600/40 text-red-300'
            : 'bg-amber-900/30 border-amber-600/40 text-amber-300';
          return (
            <span className={`px-1 py-0.5 rounded border ${cls}`} title={`Shading risk ${sh.risk}: ${sh.reason ?? ''}`}>
              shade {sh.risk === 'high' ? '!!' : '!'}
            </span>
          );
        })()}
```

(Match the surrounding pill style — the `steam`/`sharp`/`stale` pills use the same `px-1 py-0.5 rounded border` + color-token convention; reuse it.)

- [ ] **Step 3: Render the breakdown table in StatsPage**

In `frontend/src/pages/StatsPage.tsx`, import `ShadingClvRow` from `@/services/api/oppSnapshots` (add to the existing type import). Add a component near `SportBlendTable` (from #1):

```tsx
function ShadingClvTable({ rows }: { rows: ShadingClvRow[] }) {
  if (!rows.length) {
    return <div className="text-muted text-xs p-3">No shading-vs-CLV data yet — accumulating.</div>;
  }
  const fmt = (v: number | null) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);
  return (
    <div className="mt-4">
      <h3 className="text-[10px] text-muted uppercase tracking-wider font-semibold mb-1">
        Realized CLV by odds bucket × shading risk
      </h3>
      <table className="w-full text-xs border border-border">
        <thead className="bg-panel2 border-b border-border text-muted">
          <tr>
            <th className="px-2 py-1 text-left">Odds bucket</th>
            <th className="px-2 py-1 text-left">Shading risk</th>
            <th className="px-2 py-1 text-right">n</th>
            <th className="px-2 py-1 text-right">Mean Pinnacle CLV</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.odds_bucket}|${r.shading_risk}`} className="border-b border-border/40">
              <td className="px-2 py-1 text-left">{r.odds_bucket}</td>
              <td className="px-2 py-1 text-left">{r.shading_risk}</td>
              <td className="px-2 py-1 text-right tabular-nums">{r.n}</td>
              <td className={`px-2 py-1 text-right tabular-nums ${(r.mean_pinnacle_clv_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {fmt(r.mean_pinnacle_clv_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

Render it in the Shadow CLV view after the existing `<SportBlendTable .../>`:

```tsx
      <ShadingClvTable rows={data.shading_clv_breakdown} />
```

(Match the real class tokens used by the sibling `SportBlendTable`/`BreakdownTable` in this file — grep for `border-border`/`bg-panel2`/`text-muted` to confirm they exist; adapt if the file uses different tokens.)

- [ ] **Step 4: Verify build**

Run: `cd frontend && npx tsc --noEmit` → no new errors. `cd frontend && npm run test 2>&1 | tail -5` (if vitest present) or skip. The PostToolUse hook runs eslint --fix.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/api/oppSnapshots.ts frontend/src/pages/PlayPage.tsx frontend/src/pages/StatsPage.tsx
git commit -m "feat(stats-ui): shading badge + odds-bucket×risk CLV table"
```

---

## Task 6: Finish (PR) + deploy (user-gated)

**Files:** none (operational).

- [ ] **Step 1: Run the full feature test set**

Run: `cd backend && python -m pytest tests/analysis/test_shading.py tests/services/test_opp_snapshot_shading.py tests/api/test_opp_snapshots_shading.py -q`
Expected: all pass. Confirm no NEW failures vs the ~24 known pre-existing env/DB failures on main (compare changed-file blast radius only).

- [ ] **Step 2: Push + open PR against main**

CI runs ruff + frontend-typecheck. NOTE: `backend-tests` CI is RED on main from a pre-existing unrelated `tests/mirror/...` collection error — not this PR.

- [ ] **Step 3: Deploy (user-gated)**

Backend rebuild via `server-deploy.sh rebuild backend`; verify `\d opp_snapshots | grep -E 'shading_risk|odds_bucket'` and that `shading_risk` populates after a scan. Frontend via `betty.bat`.

---

## Self-Review

**Spec coverage:**
- Component 1 (`compute_shading` + `ShadingSignal` + tunable constants, lean-spine + 2-way-only FLB) → Task 1 ✓
- Component 2 (annotation wiring, reads same lean_obj) → Task 2 ✓
- Component 3 (frontend badge) → Task 5 Step 2 ✓
- Component 4 (OppSnapshot cols + migration + freeze + stats breakdown + table) → Tasks 3, 4, 5 ✓
- Edge cases: None consensus_lean → None (Task 1 test); 1x2 no FLB (Task 1 test); old rows nullable (Task 3 migration); bucket reuses `patterns._odds_range` (Task 3 import); risk=low → no badge (Task 5 guard); zero edge/stake mutation (classifier is pure, no calculate_stake touch) ✓

**Placeholder scan:** No TBD/TODO. Two content-anchored confirmations (the `clean_market` var name in analyzer Task 2; the app-import/fixture pattern in Task 4) are explicit "verify against the real symbol/sibling test" directions, not vague requirements.

**Type consistency:** `ShadingSignal` fields + `to_dict` keys (`risk`/`favorite_side`/`fav_prob`/`divergence_pp`/`flb_contrib`/`reason`) defined in Task 1, asserted in Task 1 test, consumed in Task 2 (`.to_dict()`), frozen in Task 3 (`["risk"]`), surfaced in Task 5 badge. `shading_risk`/`odds_bucket` columns (Task 3) → endpoint group-by (Task 4) → `ShadingClvRow` (`odds_bucket`/`shading_risk`/`n`/`mean_pinnacle_clv_pct`, Task 5) consistent. `_odds_range` buckets ("<1.5"/"1.5-2.5"/"2.5-4.0"/"4.0+") identical between Task 3 freeze and Task 4 test assertions.
