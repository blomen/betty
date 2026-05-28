# Open-Position Re-Hedge Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a periodic scanner that re-evaluates every open value/arb bet against the live market, surfacing post-placement middles (when an NFL line crosses a key number after we bet) and CLV-inversion salvage candidates (when Pinnacle's fair odds drift hard against an open bet).

**Architecture:** Pure-function math layer (`local/mirror/arb_math.py`) feeds a stateless scanner (`backend/src/analysis/rehedge_scanner.py`) that reads `bets WHERE result='pending' AND start_time > now`, joins to current `odds`, runs two case classifiers, and upserts results into the existing `opportunities` table with `type='rehedge'`. A new scheduler tier (`start_rehedge_tier`) drives it on a 5-min interval. UI surfaces under Sports → Arbitrage with a dedicated rehedge sub-section. Placement reuses `arb_runner` with an `arb_group_id` linking the new leg to the original bet. **Phase 1 ships read-only** — emit candidates only, no auto-placement — so we can validate quality before wiring writes.

**Tech Stack:** Python 3.12 / SQLAlchemy / pytest / FastAPI / React 19 / TypeScript

**Reference design:** [`docs/plans/2026-05-28-open-position-rehedge-scanner.md`](../../plans/2026-05-28-open-position-rehedge-scanner.md). Survey context: [`docs/knowledge/profitable-strategies-survey.md`](../../knowledge/profitable-strategies-survey.md) §3b + roadmap gap #4b.

---

## File Structure

**Created:**
- `backend/src/analysis/rehedge_scanner.py` — stateless scan function + case classifiers + dataclasses
- `backend/tests/analysis/test_arb_math_rehedge.py` — unit tests for new sizing helpers
- `backend/tests/analysis/test_rehedge_scanner.py` — scanner integration tests against in-memory SQLite
- `frontend/src/pages/play/RehedgeSection.tsx` — UI sub-section under Sports → Arbitrage
- `frontend/src/api/rehedge.ts` — typed fetch client

**Modified:**
- `local/mirror/arb_math.py` — add `equalise_payouts`, `middle_size`, `brackets_key_number` (pure functions, no I/O)
- `backend/src/pipeline/scheduler.py` — add `start_rehedge_tier` method + `_rehedge_loop`, call from `start_all`
- `backend/src/api/routes/opportunities.py` — extend `/api/opportunities/arb-workflow` (or add `/api/opportunities/rehedge`) to surface `type='rehedge'` rows
- `frontend/src/pages/PlayPage.tsx` — render `RehedgeSection` inside arb sub-tab

**Out of scope for this plan (Phase 2, separate plan):**
- Auto-placement via `arb_runner` (Phase 1 is read-only emit; we observe for 1-2 weeks first)
- Case 3 CLV-inversion salvage (ship Case 1 first; Case 3 has higher false-positive risk and needs the observation data from Case 1 to calibrate)
- Provider-limit awareness improvements to `limit_service`

This plan ships **Phase 1: Case 1 (post-placement middle), read-only emit**. Subsequent plans will add Case 3 and placement wiring.

---

### Task 1: `equalise_payouts` helper

**Files:**
- Modify: `local/mirror/arb_math.py`
- Test: `backend/tests/analysis/test_arb_math_rehedge.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/analysis/test_arb_math_rehedge.py`:

```python
"""Unit tests for rehedge sizing helpers in local.mirror.arb_math.

Pure-function tests — no DB, no I/O. The local.mirror import path works
because the repo root is on sys.path during pytest (see backend/tests/conftest.py).
"""

import pytest

from local.mirror.arb_math import (
    brackets_key_number,
    equalise_payouts,
    middle_size,
)


class TestEqualisePayouts:
    def test_equal_odds_equal_stakes(self):
        # Same odds → same stake on each side to equalise payout.
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=2.0) == pytest.approx(100.0)

    def test_higher_b_odds_needs_smaller_b_stake(self):
        # Side A: 100 * 2.0 = 200 payout. Side B at 4.0 needs 50 to also pay 200.
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=4.0) == pytest.approx(50.0)

    def test_lower_b_odds_needs_larger_b_stake(self):
        # Side A: 100 * 3.0 = 300 payout. Side B at 1.5 needs 200 to also pay 300.
        assert equalise_payouts(stake_a_base=100.0, odds_a=3.0, odds_b=1.5) == pytest.approx(200.0)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_odds_returns_zero(self, bad):
        # Defensive: never crash the scanner on a junk odds value.
        assert equalise_payouts(stake_a_base=100.0, odds_a=bad, odds_b=2.0) == 0.0
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=bad) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestEqualisePayouts -v`
Expected: FAIL with `ImportError: cannot import name 'equalise_payouts' from 'local.mirror.arb_math'`

- [ ] **Step 3: Write minimal implementation**

Add to `local/mirror/arb_math.py`:

```python
def equalise_payouts(stake_a_base: float, odds_a: float, odds_b: float) -> float:
    """Stake for side B that makes winning-outcome payouts equal in base currency.

    Currency conversion to provider-B native currency happens at the
    placement layer, not here. Returns 0.0 on non-positive odds — the
    scanner treats that as "no candidate".
    """
    if odds_a <= 0 or odds_b <= 0:
        return 0.0
    return stake_a_base * odds_a / odds_b
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestEqualisePayouts -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add local/mirror/arb_math.py backend/tests/analysis/test_arb_math_rehedge.py
git commit -m "feat(arb_math): add equalise_payouts helper for rehedge sizing"
```

---

### Task 2: `brackets_key_number` predicate

**Files:**
- Modify: `local/mirror/arb_math.py`
- Test: `backend/tests/analysis/test_arb_math_rehedge.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_arb_math_rehedge.py`:

```python
class TestBracketsKeyNumber:
    def test_brackets_three(self):
        # We bet home -2.5; opposite side now offers away +3.5 → brackets 3.
        assert brackets_key_number(point_a=-2.5, point_b=3.5, keys=(3, 7, 6, 10, 14)) == 3

    def test_brackets_seven(self):
        # Bet home -6.5; opposite offers away +7.5 → brackets 7.
        assert brackets_key_number(point_a=-6.5, point_b=7.5, keys=(3, 7, 6, 10, 14)) == 7

    def test_total_brackets_44(self):
        # Bet over 43.5; opposite now under 44.5 → brackets 44.
        assert brackets_key_number(point_a=43.5, point_b=44.5, keys=(37, 41, 44, 47, 51)) == 44

    def test_no_bracket_same_side(self):
        # Both lines on same side of 3 — no key bracketed.
        assert brackets_key_number(point_a=-1.5, point_b=2.5, keys=(3, 7, 6, 10, 14)) is None

    def test_no_bracket_too_wide_skips_keys(self):
        # -2.5 and +10.5 brackets 3 AND 7 AND 10 — we return the closest key
        # to the midpoint (4.0) → 3.
        assert brackets_key_number(point_a=-2.5, point_b=10.5, keys=(3, 7, 6, 10, 14)) == 3

    def test_equal_points_no_bracket(self):
        # Identical lines = no straddle.
        assert brackets_key_number(point_a=-3.0, point_b=3.0, keys=(3, 7, 6, 10, 14)) is None

    def test_handles_none(self):
        # Missing points (boost bets, moneylines) → no bracket.
        assert brackets_key_number(point_a=None, point_b=3.5, keys=(3, 7)) is None
        assert brackets_key_number(point_a=-2.5, point_b=None, keys=(3, 7)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestBracketsKeyNumber -v`
Expected: FAIL with `ImportError: cannot import name 'brackets_key_number'`

- [ ] **Step 3: Write minimal implementation**

Append to `local/mirror/arb_math.py`:

```python
def brackets_key_number(
    point_a: float | None,
    point_b: float | None,
    keys: tuple[int, ...],
) -> int | None:
    """Return a key number that sits strictly between |point_a| and |point_b|.

    For spreads, the opposite side's point has the opposite sign — we
    compare absolute values to detect crossing. For totals, both points
    are positive so abs() is a no-op. If multiple keys are bracketed,
    return the one closest to the midpoint of the two lines (this gives
    the most balanced middle window).

    Returns None when either point is missing or the lines don't bracket
    any key in `keys`.
    """
    if point_a is None or point_b is None:
        return None
    a, b = abs(point_a), abs(point_b)
    lo, hi = (a, b) if a < b else (b, a)
    bracketed = [k for k in keys if lo < k < hi]
    if not bracketed:
        return None
    midpoint = (lo + hi) / 2.0
    return min(bracketed, key=lambda k: abs(k - midpoint))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestBracketsKeyNumber -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add local/mirror/arb_math.py backend/tests/analysis/test_arb_math_rehedge.py
git commit -m "feat(arb_math): add brackets_key_number predicate for middle detection"
```

---

### Task 3: `middle_size` helper

**Files:**
- Modify: `local/mirror/arb_math.py`
- Test: `backend/tests/analysis/test_arb_math_rehedge.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_arb_math_rehedge.py`:

```python
class TestMiddleSize:
    def test_target_zero_loss_equals_equalise(self):
        # With target_wing_pct=0, stake_b should equal equalise_payouts —
        # both wings produce identical payout, total stake is just refunded
        # on whichever side wins.
        stake_a, odds_a, odds_b = 100.0, 2.0, 2.0
        stake_b = middle_size(stake_a, odds_a, odds_b, target_wing_pct=0.0)
        assert stake_b == pytest.approx(equalise_payouts(stake_a, odds_a, odds_b))

    def test_target_one_percent_wing_loss(self):
        # Accept 1% loss on wings → smaller stake_b, bigger middle upside.
        stake_a, odds_a, odds_b = 100.0, 1.91, 1.91
        stake_b = middle_size(stake_a, odds_a, odds_b, target_wing_pct=0.01)
        # Equal-payout would be 100. Accepting 1% loss → slightly smaller.
        assert stake_b < 100.0
        # Verify the resulting wing-loss is ~1%.
        total = stake_a + stake_b
        a_wins_payout = stake_a * odds_a
        b_wins_payout = stake_b * odds_b
        wing_loss = total - min(a_wins_payout, b_wins_payout)
        assert wing_loss / total == pytest.approx(0.01, abs=0.001)

    def test_invalid_target_clamps_to_zero(self):
        # Negative target_wing_pct nonsensical — clamp.
        stake_a, odds_a, odds_b = 100.0, 2.0, 2.0
        assert middle_size(stake_a, odds_a, odds_b, target_wing_pct=-0.5) == pytest.approx(
            equalise_payouts(stake_a, odds_a, odds_b)
        )

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_odds_returns_zero(self, bad):
        assert middle_size(100.0, bad, 2.0, target_wing_pct=0.01) == 0.0
        assert middle_size(100.0, 2.0, bad, target_wing_pct=0.01) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestMiddleSize -v`
Expected: FAIL with `ImportError: cannot import name 'middle_size'`

- [ ] **Step 3: Write minimal implementation**

Append to `local/mirror/arb_math.py`:

```python
def middle_size(
    stake_a_base: float,
    odds_a: float,
    odds_b: float,
    target_wing_pct: float,
) -> float:
    """Stake for side B such that wing_loss / total_stake == target_wing_pct.

    Wing loss = the loss when the result does NOT land in the middle.
    Smaller stake_b → bigger wing loss but bigger middle payout.
    Larger stake_b → smaller wing loss but smaller middle payout.

    At target_wing_pct=0 this reduces to equalise_payouts (both winning
    payouts equal total stake, refunding exactly). At target_wing_pct>0
    we deliberately under-stake side B so that:
        total_stake = stake_a + stake_b
        min(stake_a * odds_a, stake_b * odds_b) = total_stake * (1 - target_wing_pct)

    Returns 0.0 on invalid inputs. Clamps negative target_wing_pct to 0.
    """
    if odds_a <= 0 or odds_b <= 0 or stake_a_base <= 0:
        return 0.0
    w = max(0.0, target_wing_pct)

    # We assume the smaller payout side is side B (under-stake B side).
    # Derivation: let S_a, S_b be the stakes, T = S_a + S_b.
    # We want: S_b * odds_b = T * (1 - w)
    #          S_b * odds_b = (S_a + S_b) * (1 - w)
    #          S_b * (odds_b - (1 - w)) = S_a * (1 - w)
    #          S_b = S_a * (1 - w) / (odds_b - (1 - w))
    denominator = odds_b - (1.0 - w)
    if denominator <= 0:
        # odds_b < 1 + w — pathological; fall back to equalise.
        return equalise_payouts(stake_a_base, odds_a, odds_b)
    return stake_a_base * (1.0 - w) / denominator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_arb_math_rehedge.py::TestMiddleSize -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add local/mirror/arb_math.py backend/tests/analysis/test_arb_math_rehedge.py
git commit -m "feat(arb_math): add middle_size helper with configurable wing loss"
```

---

### Task 4: `RehedgeCandidate` dataclass + module skeleton

**Files:**
- Create: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/analysis/test_rehedge_scanner.py`:

```python
"""Tests for rehedge_scanner — Case 1 (post-placement middle) emit logic.

Uses the shared db_session fixture (in-memory SQLite). Builds minimal
Event + Bet + Odds rows, runs the scanner, asserts on emitted candidates.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.analysis.rehedge_scanner import RehedgeCandidate, scan_open_positions
from src.db.models import Bet, Event, Odds, Provider


@pytest.fixture
def future_event(db_session):
    """A future NFL event suitable for rehedge tests."""
    # Provider rows required by FK constraints.
    for pid, cur in [("pinnacle", "USD"), ("unibet", "SEK"), ("betsson", "SEK")]:
        db_session.add(Provider(id=pid, name=pid.title(), currency=cur))
    event = Event(
        id="evt-test-1",
        sport="americanfootball_nfl",
        home_team="Patriots",
        away_team="Jets",
        start_time=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(event)
    db_session.flush()
    yield event


class TestRehedgeCandidateDataclass:
    def test_candidate_fields(self):
        # Just enforce the shape the scanner will emit.
        c = RehedgeCandidate(
            bet_id=42,
            case="post_placement_middle",
            hedge_provider="betsson",
            hedge_market="spread",
            hedge_outcome="away",
            hedge_point=3.5,
            hedge_odds=1.91,
            recommended_stake_base=95.0,
            base_currency="SEK",
            metadata={"key_number": 3, "wing_loss_pct": 0.012},
        )
        assert c.bet_id == 42
        assert c.case == "post_placement_middle"
        assert c.metadata["key_number"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestRehedgeCandidateDataclass -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.analysis.rehedge_scanner'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/analysis/rehedge_scanner.py`:

```python
"""Open-position re-hedge scanner.

Periodically scans `bets WHERE result='pending' AND start_time > now`
and looks for post-placement middles (Case 1) — NFL spreads/totals where
the line has moved through a key number since we placed.

Distinct from analysis/scanner.py (which scans current market for value
and arb from scratch). This scanner's search space is what we already own.

Phase 1: read-only — emits RehedgeCandidate objects. Upsert into
`opportunities` and placement wiring land in later tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


RehedgeCase = Literal["post_placement_middle", "clv_inversion_salvage"]


@dataclass(frozen=True)
class RehedgeCandidate:
    """A re-hedge action recommended for an open bet.

    bet_id: the open bet this candidate hedges
    case: which classifier emitted it
    hedge_*: the side we want to take to hedge
    recommended_stake_base: stake in SEK (Betty's base currency)
    metadata: case-specific context — key_number, wing_loss_pct,
        inversion_pct, etc. Surfaced to UI as-is.
    """

    bet_id: int
    case: RehedgeCase
    hedge_provider: str
    hedge_market: str
    hedge_outcome: str
    hedge_point: float | None
    hedge_odds: float
    recommended_stake_base: float
    base_currency: str
    metadata: dict = field(default_factory=dict)


def scan_open_positions(db) -> list[RehedgeCandidate]:
    """Scan all open pending bets, return emit-able rehedge candidates.

    Stateless — caller owns the session and is responsible for upserting
    the results into `opportunities`.
    """
    return []  # implemented in later tasks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestRehedgeCandidateDataclass -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/rehedge_scanner.py backend/tests/analysis/test_rehedge_scanner.py
git commit -m "feat(rehedge): scaffold RehedgeCandidate dataclass + scan_open_positions stub"
```

---

### Task 5: Bet query — filter to candidates worth scanning

**Files:**
- Modify: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_rehedge_scanner.py`:

```python
class TestQueryOpenBets:
    """The scanner must filter to bets that are actually scannable:
    pending result, future event, has event_id (boost bets excluded),
    has point (moneylines excluded for Case 1)."""

    def test_includes_open_nfl_spread(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(Bet(
            id=1, event_id=future_event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="pending",
            bet_type="value", start_time=future_event.start_time,
        ))
        db_session.flush()

        bets = _query_open_bets(db_session)
        assert [b.id for b in bets] == [1]

    def test_excludes_settled(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(Bet(
            id=1, event_id=future_event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="won",
            bet_type="value", start_time=future_event.start_time,
        ))
        db_session.flush()
        assert _query_open_bets(db_session) == []

    def test_excludes_past_events(self, db_session, future_event):
        from src.analysis.rehedge_scanner import _query_open_bets

        future_event.start_time = datetime.now(UTC) - timedelta(hours=1)
        db_session.add(Bet(
            id=1, event_id=future_event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="pending",
            bet_type="value", start_time=future_event.start_time,
        ))
        db_session.flush()
        assert _query_open_bets(db_session) == []

    def test_excludes_boost_bets_no_event(self, db_session):
        # Boost bets often lack event_id (free-text boost_event field instead).
        from src.analysis.rehedge_scanner import _query_open_bets

        db_session.add(Provider(id="unibet", name="Unibet", currency="SEK"))
        db_session.add(Bet(
            id=1, event_id=None, provider_id="unibet",
            market="moneyline", outcome="home", odds=2.5,
            stake=50.0, currency="SEK", result="pending",
            bet_type="boost", boost_event="Arsenal vs Sunderland",
        ))
        db_session.flush()
        assert _query_open_bets(db_session) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestQueryOpenBets -v`
Expected: FAIL with `ImportError: cannot import name '_query_open_bets'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/src/analysis/rehedge_scanner.py`:

```python
from datetime import UTC, datetime

from src.db.models import Bet, Event


def _query_open_bets(db) -> list[Bet]:
    """Return pending bets on future events that are scannable for rehedge.

    Filters:
    - result == 'pending'
    - event_id IS NOT NULL (excludes boost / free-text bets)
    - Event.start_time > now (excludes started/live events — out of scope)
    """
    now = datetime.now(UTC)
    return (
        db.query(Bet)
        .join(Event, Event.id == Bet.event_id)
        .filter(
            Bet.result == "pending",
            Bet.event_id.isnot(None),
            Event.start_time.isnot(None),
            Event.start_time > now,
        )
        .all()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestQueryOpenBets -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/rehedge_scanner.py backend/tests/analysis/test_rehedge_scanner.py
git commit -m "feat(rehedge): query open pending bets on future events"
```

---

### Task 6: Currency-aware opposite-outcome resolver

**Files:**
- Modify: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_rehedge_scanner.py`:

```python
class TestOppositeOutcome:
    """For spreads: opposite of 'home' is 'away' (and point flips sign).
    For totals: opposite of 'over' is 'under' (point stays the same)."""

    def test_spread_home_to_away(self):
        from src.analysis.rehedge_scanner import _opposite_outcome
        assert _opposite_outcome("spread", "home") == "away"
        assert _opposite_outcome("spread", "away") == "home"

    def test_total_over_to_under(self):
        from src.analysis.rehedge_scanner import _opposite_outcome
        assert _opposite_outcome("total", "over") == "under"
        assert _opposite_outcome("total", "under") == "over"

    def test_runline_handicap_aliases(self):
        # MLB runline and NHL puckline use home/away too.
        from src.analysis.rehedge_scanner import _opposite_outcome
        assert _opposite_outcome("runline", "home") == "away"
        assert _opposite_outcome("handicap", "away") == "home"

    def test_unknown_market_returns_none(self):
        from src.analysis.rehedge_scanner import _opposite_outcome
        assert _opposite_outcome("1x2", "home") is None  # 3-way, no clean opposite
        assert _opposite_outcome("moneyline", "home") is None  # no point/no middle


class TestPointForOppositeSide:
    def test_spread_point_flips_sign(self):
        from src.analysis.rehedge_scanner import _opposite_point
        # home -2.5 → away side prices at +2.5 in our normalised storage
        # (Betty stores both rows with the same magnitude; the outcome
        # column carries the side, NOT the sign. So opposite "point" equals
        # the original.)
        assert _opposite_point("spread", point=-2.5) == 2.5
        assert _opposite_point("spread", point=2.5) == -2.5

    def test_total_point_unchanged(self):
        from src.analysis.rehedge_scanner import _opposite_point
        assert _opposite_point("total", point=43.5) == 43.5

    def test_no_point_returns_none(self):
        from src.analysis.rehedge_scanner import _opposite_point
        assert _opposite_point("spread", point=None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestOppositeOutcome tests/analysis/test_rehedge_scanner.py::TestPointForOppositeSide -v`
Expected: FAIL with `ImportError: cannot import name '_opposite_outcome'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/src/analysis/rehedge_scanner.py`:

```python
_SPREAD_MARKETS = {"spread", "handicap", "runline", "puckline"}
_TOTAL_MARKETS = {"total", "totals", "over_under", "ou"}


def _opposite_outcome(market: str | None, outcome: str | None) -> str | None:
    """Return the symmetric opposite outcome for spread/total markets.

    Returns None for markets where no symmetric opposite exists
    (1x2 has a draw, moneyline has no point). The scanner Case 1
    requires a point, so those markets are dropped here.
    """
    if not market or not outcome:
        return None
    m = market.lower()
    o = outcome.lower()
    if m in _SPREAD_MARKETS:
        return {"home": "away", "away": "home"}.get(o)
    if m in _TOTAL_MARKETS:
        return {"over": "under", "under": "over"}.get(o)
    return None


def _opposite_point(market: str | None, point: float | None) -> float | None:
    """Return the point value used to query the opposite side.

    For spreads, the opposite side has the negated point (home -2.5
    corresponds to away +2.5; Betty stores both as separate Odds rows
    with opposite-signed points). For totals, the same point line
    applies to both over and under.
    """
    if point is None or not market:
        return None
    if market.lower() in _SPREAD_MARKETS:
        return -point
    if market.lower() in _TOTAL_MARKETS:
        return point
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestOppositeOutcome tests/analysis/test_rehedge_scanner.py::TestPointForOppositeSide -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/rehedge_scanner.py backend/tests/analysis/test_rehedge_scanner.py
git commit -m "feat(rehedge): opposite-outcome and opposite-point resolvers"
```

---

### Task 7: Case 1 classifier — post-placement middle detection

**Files:**
- Modify: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_rehedge_scanner.py`:

```python
class TestCase1PostPlacementMiddle:
    """We bet home -2.5; a different provider now offers away +3.5 at
    a price that gives a wing loss ≤ MAX_WING_LOSS_PCT. Emit candidate."""

    def _add_bet(self, db_session, event, **overrides):
        kwargs = dict(
            id=1, event_id=event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="pending",
            bet_type="value", start_time=event.start_time,
        )
        kwargs.update(overrides)
        db_session.add(Bet(**kwargs))

    def _add_odds(self, db_session, event, **overrides):
        kwargs = dict(
            event_id=event.id, provider_id="betsson",
            market="spread", outcome="away", point=3.5, odds=1.91,
            scope="ft",
        )
        kwargs.update(overrides)
        db_session.add(Odds(**kwargs))

    def test_emits_middle_when_line_crossed_key(self, db_session, future_event):
        # Bet home -2.5, opposite provider now offers away +3.5 → brackets 3.
        self._add_bet(db_session, future_event)
        self._add_odds(db_session, future_event)
        db_session.flush()

        candidates = scan_open_positions(db_session)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.bet_id == 1
        assert c.case == "post_placement_middle"
        assert c.hedge_provider == "betsson"
        assert c.hedge_outcome == "away"
        assert c.hedge_point == 3.5
        assert c.metadata["key_number"] == 3

    def test_no_emit_when_no_bracket(self, db_session, future_event):
        # Bet home -2.5; opposite offers away +2.5 — same line, no bracket.
        self._add_bet(db_session, future_event)
        self._add_odds(db_session, future_event, point=2.5)
        db_session.flush()
        assert scan_open_positions(db_session) == []

    def test_no_emit_when_non_nfl(self, db_session, future_event):
        future_event.sport = "soccer_epl"
        self._add_bet(db_session, future_event)
        self._add_odds(db_session, future_event)
        db_session.flush()
        assert scan_open_positions(db_session) == []

    def test_no_emit_when_wing_loss_too_high(self, db_session, future_event):
        # Heavily juiced opposite side → wing loss > MAX_WING_LOSS_PCT (2.5%).
        self._add_bet(db_session, future_event)
        self._add_odds(db_session, future_event, odds=1.20)  # bad price
        db_session.flush()
        assert scan_open_positions(db_session) == []

    def test_no_emit_for_moneyline_bet(self, db_session, future_event):
        self._add_bet(
            db_session, future_event,
            market="moneyline", point=None,
        )
        self._add_odds(db_session, future_event)
        db_session.flush()
        assert scan_open_positions(db_session) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestCase1PostPlacementMiddle -v`
Expected: FAIL — `scan_open_positions` currently returns `[]` always, so the first test fails on `len(candidates) == 1`.

- [ ] **Step 3: Write minimal implementation**

Replace the stub `scan_open_positions` in `backend/src/analysis/rehedge_scanner.py`:

```python
from local.mirror.arb_math import brackets_key_number, middle_size

from src.analysis.key_numbers import (
    NFL_SPREAD_KEY_NUMBERS,
    NFL_TOTAL_KEY_NUMBERS,
    is_nfl,
)
from src.config.loader import get_exchange_rate
from src.db.models import Odds

# Tuning knobs — see survey §3b "What kills it"
MAX_WING_LOSS_PCT = 0.025  # 2.5% of total stake — anything bigger means
                           # the middle bet costs more than its expected value.
TARGET_WING_LOSS_PCT = 0.01  # 1% — what we aim for when sizing


def _bet_stake_sek(bet) -> float:
    """Convert a bet's native-currency stake to SEK via the provider's rate.

    Currency conversion is the #1 hidden source of off-by-5×-10× sizing
    bugs in Betty (CLAUDE.md "first hypothesis when sizing looks off").
    `get_exchange_rate(provider_id)` returns 1.0 for SEK-denominated
    providers (Swedish softs + this user's Pinnacle account) and the
    correct multiplier (≈10) for USD/USDC providers.
    """
    rate = get_exchange_rate(bet.provider_id)
    return bet.stake * rate


def _keys_for_market(market: str) -> tuple[int, ...]:
    m = market.lower()
    if m in _SPREAD_MARKETS:
        return NFL_SPREAD_KEY_NUMBERS
    if m in _TOTAL_MARKETS:
        return NFL_TOTAL_KEY_NUMBERS
    return ()


def _classify_case1(db, bet) -> RehedgeCandidate | None:
    """Case 1: post-placement middle on NFL spreads/totals.

    Skip if: bet's event isn't NFL, bet isn't spread/total, bet has no
    point. Otherwise look across providers for an opposite-side quote at
    a point that brackets a key number. Emit the best (lowest wing-loss)
    candidate.
    """
    if not is_nfl(bet.event.sport):
        return None
    keys = _keys_for_market(bet.market or "")
    if not keys or bet.point is None:
        return None

    opp_outcome = _opposite_outcome(bet.market, bet.outcome)
    if opp_outcome is None:
        return None

    # Query all opposite-side quotes on this event/market/scope.
    candidates_q = db.query(Odds).filter(
        Odds.event_id == bet.event_id,
        Odds.market == bet.market,
        Odds.outcome == opp_outcome,
        Odds.scope == "ft",
        Odds.provider_id != bet.provider_id,  # never hedge at the same book
    ).all()

    best: RehedgeCandidate | None = None
    best_wing: float = float("inf")
    bet_stake_sek = _bet_stake_sek(bet)

    for opp in candidates_q:
        key = brackets_key_number(bet.point, _opposite_point(bet.market, opp.point), keys)
        if key is None:
            continue

        # Size at our target wing-loss; verify the achieved wing-loss
        # is within the absolute cap.
        stake_b = middle_size(bet_stake_sek, bet.odds, opp.odds, TARGET_WING_LOSS_PCT)
        if stake_b <= 0:
            continue
        total = bet_stake_sek + stake_b
        wing_loss = total - min(bet_stake_sek * bet.odds, stake_b * opp.odds)
        wing_pct = wing_loss / total if total > 0 else float("inf")
        if wing_pct > MAX_WING_LOSS_PCT:
            continue

        if wing_pct < best_wing:
            best_wing = wing_pct
            best = RehedgeCandidate(
                bet_id=bet.id,
                case="post_placement_middle",
                hedge_provider=opp.provider_id,
                hedge_market=bet.market,
                hedge_outcome=opp_outcome,
                hedge_point=opp.point,
                hedge_odds=opp.odds,
                recommended_stake_base=round(stake_b, 2),
                base_currency="SEK",
                metadata={
                    "key_number": key,
                    "wing_loss_pct": round(wing_pct, 4),
                    "original_bet_provider": bet.provider_id,
                    "original_bet_odds": bet.odds,
                    "original_bet_point": bet.point,
                    "original_bet_stake_sek": round(bet_stake_sek, 2),
                },
            )
    return best


def scan_open_positions(db) -> list[RehedgeCandidate]:
    """Scan all open pending bets, return emit-able rehedge candidates.

    Stateless — caller owns the session and is responsible for upserting
    the results into `opportunities` (see persist_rehedge_candidates).
    """
    out: list[RehedgeCandidate] = []
    for bet in _query_open_bets(db):
        c = _classify_case1(db, bet)
        if c is not None:
            out.append(c)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py -v`
Expected: PASS (all classes, ~13 tests total)

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/rehedge_scanner.py backend/tests/analysis/test_rehedge_scanner.py
git commit -m "feat(rehedge): Case 1 classifier — post-placement NFL middle detection"
```

---

### Task 8: Same-bet dedup — pick best candidate per bet

**Files:**
- Modify: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_rehedge_scanner.py`:

```python
class TestSameBetDedup:
    def test_multiple_providers_only_best_emitted(self, db_session, future_event):
        # Add the bet (home -2.5)
        db_session.add(Bet(
            id=1, event_id=future_event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="pending",
            bet_type="value", start_time=future_event.start_time,
        ))
        # Two providers both offer the middle at different prices.
        db_session.add(Odds(
            event_id=future_event.id, provider_id="betsson",
            market="spread", outcome="away", point=3.5, odds=1.91,
            scope="ft",
        ))
        db_session.add(Odds(
            event_id=future_event.id, provider_id="betinia",
            market="spread", outcome="away", point=3.5, odds=2.10,
            scope="ft",
        ))
        db_session.flush()

        candidates = scan_open_positions(db_session)
        # Only ONE candidate per bet, chosen for lowest wing loss
        # (higher opp odds → smaller required stake_b → smaller wing loss).
        assert len(candidates) == 1
        assert candidates[0].hedge_provider == "betinia"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestSameBetDedup -v`
Expected: PASS — the Case 1 classifier already keeps only the best (lowest wing-loss) candidate per bet. If it FAILS, fix the loop in `_classify_case1` to track the best instead of returning eagerly. (Per the implementation in Task 7, it should already pass.)

- [ ] **Step 3: Commit (if test passed without change)**

```bash
git add backend/tests/analysis/test_rehedge_scanner.py
git commit -m "test(rehedge): verify dedup picks best provider per bet"
```

If the test failed and required a fix to `_classify_case1`, include the .py file in the commit.

---

### Task 9: Persistence — upsert into `opportunities` table

**Files:**
- Modify: `backend/src/analysis/rehedge_scanner.py`
- Test: `backend/tests/analysis/test_rehedge_scanner.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/analysis/test_rehedge_scanner.py`:

```python
class TestPersistence:
    def _setup_bet_and_quote(self, db_session, future_event):
        db_session.add(Bet(
            id=1, event_id=future_event.id, provider_id="unibet",
            market="spread", outcome="home", point=-2.5, odds=1.91,
            stake=100.0, currency="SEK", result="pending",
            bet_type="value", start_time=future_event.start_time,
        ))
        db_session.add(Odds(
            event_id=future_event.id, provider_id="betsson",
            market="spread", outcome="away", point=3.5, odds=1.91,
            scope="ft",
        ))
        db_session.flush()

    def test_persists_to_opportunities_table(self, db_session, future_event):
        from src.analysis.rehedge_scanner import persist_rehedge_candidates
        from src.db.models import Opportunity

        self._setup_bet_and_quote(db_session, future_event)
        candidates = scan_open_positions(db_session)
        persist_rehedge_candidates(db_session, candidates)
        db_session.commit()

        rows = db_session.query(Opportunity).filter(Opportunity.type == "rehedge").all()
        assert len(rows) == 1
        opp = rows[0]
        assert opp.event_id == future_event.id
        assert opp.provider1_id == "betsson"
        assert opp.outcome1 == "away"
        assert opp.is_active is True
        # Candidate-specific context goes in annotations JSON
        assert opp.annotations["case"] == "post_placement_middle"
        assert opp.annotations["bet_id"] == 1
        assert opp.annotations["key_number"] == 3

    def test_idempotent_upsert(self, db_session, future_event):
        # Running the scanner twice with the same market state should
        # not create duplicate opportunities rows.
        from src.analysis.rehedge_scanner import persist_rehedge_candidates
        from src.db.models import Opportunity

        self._setup_bet_and_quote(db_session, future_event)
        persist_rehedge_candidates(db_session, scan_open_positions(db_session))
        db_session.commit()
        persist_rehedge_candidates(db_session, scan_open_positions(db_session))
        db_session.commit()

        rows = db_session.query(Opportunity).filter(Opportunity.type == "rehedge").all()
        assert len(rows) == 1

    def test_deactivates_stale_candidates(self, db_session, future_event):
        # If a candidate stops emitting (e.g. opposite-side odds disappeared),
        # the existing row should be marked is_active=False.
        from src.analysis.rehedge_scanner import persist_rehedge_candidates
        from src.db.models import Opportunity

        self._setup_bet_and_quote(db_session, future_event)
        persist_rehedge_candidates(db_session, scan_open_positions(db_session))
        db_session.commit()

        # Delete the opposite-side odds row → no more candidate
        db_session.query(Odds).filter(Odds.provider_id == "betsson").delete()
        db_session.flush()

        persist_rehedge_candidates(db_session, scan_open_positions(db_session))
        db_session.commit()

        row = db_session.query(Opportunity).filter(Opportunity.type == "rehedge").one()
        assert row.is_active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestPersistence -v`
Expected: FAIL with `ImportError: cannot import name 'persist_rehedge_candidates'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/src/analysis/rehedge_scanner.py`:

```python
from src.db.models import Opportunity


def persist_rehedge_candidates(db, candidates: list[RehedgeCandidate]) -> dict:
    """Upsert candidates into `opportunities` with type='rehedge'.

    Idempotent — keyed on (event_id, market, outcome1, provider1_id, type, scope)
    per the existing `ix_opp_upsert_unique` index. Candidates not in the
    current scan are marked is_active=False (deactivated), so the UI can
    reflect a vanished hedge window in real time.

    Returns {"inserted": int, "updated": int, "deactivated": int}.
    """
    # Build a lookup of current emit set.
    current_keys = {
        (c.hedge_provider, c.hedge_market, c.hedge_outcome, c.bet_id): c
        for c in candidates
    }

    # First, deactivate any existing active rehedge rows that are no longer
    # in the current emit set. Match on (bet_id stored in annotations,
    # provider1_id, market, outcome1).
    deactivated = 0
    existing = db.query(Opportunity).filter(
        Opportunity.type == "rehedge",
        Opportunity.is_active.is_(True),
    ).all()
    for opp in existing:
        bid = (opp.annotations or {}).get("bet_id")
        key = (opp.provider1_id, opp.market, opp.outcome1, bid)
        if key not in current_keys:
            opp.is_active = False
            deactivated += 1

    inserted = 0
    updated = 0
    for c in candidates:
        # Look up by the natural key for upsert. event_id is on the bet —
        # fetch via subquery to avoid an extra round-trip.
        from src.db.models import Bet
        bet = db.query(Bet).get(c.bet_id)
        if bet is None or bet.event_id is None:
            continue

        existing_row = db.query(Opportunity).filter(
            Opportunity.type == "rehedge",
            Opportunity.event_id == bet.event_id,
            Opportunity.market == c.hedge_market,
            Opportunity.outcome1 == c.hedge_outcome,
            Opportunity.provider1_id == c.hedge_provider,
            Opportunity.scope == "ft",
        ).first()

        annotations = {
            "case": c.case,
            "bet_id": c.bet_id,
            "base_currency": c.base_currency,
            "recommended_stake_base": c.recommended_stake_base,
            **c.metadata,
        }

        if existing_row is None:
            db.add(Opportunity(
                type="rehedge",
                event_id=bet.event_id,
                market=c.hedge_market,
                scope="ft",
                provider1_id=c.hedge_provider,
                odds1=c.hedge_odds,
                outcome1=c.hedge_outcome,
                point=c.hedge_point,
                total_stake=c.recommended_stake_base,
                is_active=True,
                annotations=annotations,
            ))
            inserted += 1
        else:
            existing_row.odds1 = c.hedge_odds
            existing_row.point = c.hedge_point
            existing_row.total_stake = c.recommended_stake_base
            existing_row.is_active = True
            existing_row.annotations = annotations
            updated += 1

    return {"inserted": inserted, "updated": updated, "deactivated": deactivated}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py::TestPersistence -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full scanner test suite**

Run: `cd backend && pytest tests/analysis/test_rehedge_scanner.py tests/analysis/test_arb_math_rehedge.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/rehedge_scanner.py backend/tests/analysis/test_rehedge_scanner.py
git commit -m "feat(rehedge): persist candidates to opportunities with upsert + deactivate"
```

---

### Task 10: Scheduler tier — wire scanner into `start_all`

**Files:**
- Modify: `backend/src/pipeline/scheduler.py`
- Test: `backend/tests/pipeline/test_rehedge_tier.py` (new file)

- [ ] **Step 1: Write failing test**

Create `backend/tests/pipeline/test_rehedge_tier.py`:

```python
"""Tests for the rehedge scheduler tier."""

import asyncio

import pytest

from src.pipeline.scheduler import ExtractionScheduler


class TestRehedgeTier:
    @pytest.mark.asyncio
    async def test_start_rehedge_tier_creates_task(self):
        sched = ExtractionScheduler()
        await sched.start_rehedge_tier(interval_seconds=300)
        try:
            assert sched._rehedge_task is not None
            assert not sched._rehedge_task.done()
        finally:
            if sched._rehedge_task:
                sched._rehedge_task.cancel()
                try:
                    await sched._rehedge_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_start_twice_warns_no_double_task(self):
        sched = ExtractionScheduler()
        await sched.start_rehedge_tier(interval_seconds=300)
        first = sched._rehedge_task
        try:
            await sched.start_rehedge_tier(interval_seconds=300)
            # Second call must NOT replace the running task
            assert sched._rehedge_task is first
        finally:
            if sched._rehedge_task:
                sched._rehedge_task.cancel()
                try:
                    await sched._rehedge_task
                except asyncio.CancelledError:
                    pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/pipeline/test_rehedge_tier.py -v`
Expected: FAIL with `AttributeError: 'ExtractionScheduler' object has no attribute 'start_rehedge_tier'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/src/pipeline/scheduler.py` next to `start_settlement_tier` (~line 1071):

```python
    async def start_rehedge_tier(self, interval_seconds: int = 300):
        """Start periodic open-position rehedge scans (default 5 min).

        Scans all `bets WHERE result='pending' AND start_time > now`, looks
        for post-placement middles, upserts candidates into the
        opportunities table with type='rehedge'.

        Phase 1: emit-only. No auto-placement. See
        docs/superpowers/plans/2026-05-28-open-position-rehedge-scanner.md.
        """
        if getattr(self, "_rehedge_task", None) and not self._rehedge_task.done():
            logger.warning("[Scheduler] Rehedge tier already running")
            return

        logger.info(f"[Scheduler] Starting rehedge tier: interval={interval_seconds}s")
        self._rehedge_task = asyncio.create_task(self._rehedge_loop(interval_seconds))

    async def _rehedge_loop(self, interval_seconds: int):
        """Recurring loop for rehedge scans."""
        try:
            await asyncio.sleep(180)  # 3 min initial delay — let extraction warm up
        except asyncio.CancelledError:
            return

        while True:
            try:
                self._run_rehedge_scan()
            except asyncio.CancelledError:
                logger.info("[Scheduler:rehedge] Loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler:rehedge] Error: {e}", exc_info=True)

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    def _run_rehedge_scan(self) -> dict:
        """One scan iteration — query, classify, upsert."""
        from src.analysis.rehedge_scanner import (
            persist_rehedge_candidates,
            scan_open_positions,
        )
        from src.db.models import get_session

        session = get_session()
        try:
            candidates = scan_open_positions(session)
            stats = persist_rehedge_candidates(session, candidates)
            session.commit()

            total_changed = stats["inserted"] + stats["updated"] + stats["deactivated"]
            if total_changed > 0:
                logger.info(
                    f"[Scheduler:rehedge] +{stats['inserted']} inserted, "
                    f"{stats['updated']} updated, {stats['deactivated']} deactivated"
                )
            return stats
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

Also add the `_rehedge_task: asyncio.Task | None = None` attribute to `__init__` (search for `_settlement_task` for the pattern).

Then add a call to `start_rehedge_tier()` from `start_all()` (~line 549, right after `start_settlement_tier`):

```python
        # Open-position rehedge scanner — 5 min interval
        await self.start_rehedge_tier()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/pipeline/test_rehedge_tier.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Verify nothing else broke**

Run: `cd backend && pytest tests/pipeline/ tests/analysis/ -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/src/pipeline/scheduler.py backend/tests/pipeline/test_rehedge_tier.py
git commit -m "feat(rehedge): wire scheduler tier (5 min interval) into start_all"
```

---

### Task 11: API endpoint — surface rehedge opportunities

**Files:**
- Modify: `backend/src/api/routes/opportunities.py`
- Test: `backend/tests/api/test_rehedge_endpoint.py` (new file)

- [ ] **Step 1: Read the existing opportunities route to follow its patterns**

Run: `cd backend && grep -n "router.get\|@router\|def.*opportunit" src/api/routes/opportunities.py | head -20`

Identify the existing list-style endpoint pattern (likely `/api/opportunities/...` returning a list). Mirror its FastAPI patterns and dependency-injection style.

- [ ] **Step 2: Write failing test**

Create `backend/tests/api/test_rehedge_endpoint.py`:

```python
"""Test the /api/opportunities/rehedge endpoint."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.db.models import Bet, Event, Odds, Opportunity, Provider


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def rehedge_opp(db_session):
    """Insert a Provider / Event / Bet / Opportunity tuple for the endpoint to return."""
    for pid, cur in [("unibet", "SEK"), ("betsson", "SEK")]:
        db_session.add(Provider(id=pid, name=pid.title(), currency=cur))
    event = Event(
        id="evt-rehedge-1", sport="americanfootball_nfl",
        home_team="Pats", away_team="Jets",
        start_time=datetime.now(UTC) + timedelta(hours=12),
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(Bet(
        id=99, event_id="evt-rehedge-1", provider_id="unibet",
        market="spread", outcome="home", point=-2.5, odds=1.91,
        stake=100.0, currency="SEK", result="pending",
        bet_type="value", start_time=event.start_time,
    ))
    db_session.add(Opportunity(
        type="rehedge",
        event_id="evt-rehedge-1",
        market="spread",
        scope="ft",
        provider1_id="betsson",
        odds1=1.91,
        outcome1="away",
        point=3.5,
        total_stake=95.0,
        is_active=True,
        annotations={
            "case": "post_placement_middle",
            "bet_id": 99,
            "key_number": 3,
            "wing_loss_pct": 0.01,
            "base_currency": "SEK",
            "recommended_stake_base": 95.0,
        },
    ))
    db_session.commit()


class TestRehedgeEndpoint:
    def test_returns_active_rehedge_opportunities(self, client, rehedge_opp):
        resp = client.get("/api/opportunities/rehedge")
        assert resp.status_code == 200
        data = resp.json()
        assert "opportunities" in data
        assert len(data["opportunities"]) == 1
        opp = data["opportunities"][0]
        assert opp["case"] == "post_placement_middle"
        assert opp["original_bet_id"] == 99
        assert opp["hedge_provider"] == "betsson"
        assert opp["hedge_outcome"] == "away"
        assert opp["hedge_point"] == 3.5
        assert opp["hedge_odds"] == 1.91
        assert opp["recommended_stake_sek"] == 95.0
        assert opp["key_number"] == 3
        assert opp["event"]["home_team"] == "Pats"

    def test_excludes_inactive(self, client, rehedge_opp, db_session):
        # Mark the opportunity inactive
        db_session.query(Opportunity).filter(Opportunity.type == "rehedge").update({"is_active": False})
        db_session.commit()

        resp = client.get("/api/opportunities/rehedge")
        assert resp.status_code == 200
        assert resp.json()["opportunities"] == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_rehedge_endpoint.py -v`
Expected: FAIL — 404 on `/api/opportunities/rehedge`.

- [ ] **Step 4: Write minimal implementation**

Add to `backend/src/api/routes/opportunities.py`:

```python
@router.get("/rehedge")
def list_rehedge_opportunities(db: Session = Depends(get_db)) -> dict:
    """List active rehedge candidates emitted by the open-position scanner.

    See backend/src/analysis/rehedge_scanner.py for the source.
    """
    from src.db.models import Bet, Event, Opportunity

    opps = (
        db.query(Opportunity)
        .filter(Opportunity.type == "rehedge", Opportunity.is_active.is_(True))
        .all()
    )
    bet_ids = [
        (o.annotations or {}).get("bet_id") for o in opps
        if (o.annotations or {}).get("bet_id")
    ]
    bets_by_id = {
        b.id: b for b in db.query(Bet).filter(Bet.id.in_(bet_ids)).all()
    } if bet_ids else {}
    events_by_id = {
        e.id: e for e in db.query(Event).filter(
            Event.id.in_([o.event_id for o in opps])
        ).all()
    }

    out = []
    for o in opps:
        a = o.annotations or {}
        bid = a.get("bet_id")
        bet = bets_by_id.get(bid) if bid else None
        event = events_by_id.get(o.event_id)
        out.append({
            "opportunity_id": o.id,
            "case": a.get("case"),
            "original_bet_id": bid,
            "original_bet": {
                "provider": bet.provider_id if bet else None,
                "market": bet.market if bet else None,
                "outcome": bet.outcome if bet else None,
                "point": bet.point if bet else None,
                "odds": bet.odds if bet else None,
                "stake": bet.stake if bet else None,
                "currency": bet.currency if bet else None,
            } if bet else None,
            "hedge_provider": o.provider1_id,
            "hedge_market": o.market,
            "hedge_outcome": o.outcome1,
            "hedge_point": o.point,
            "hedge_odds": o.odds1,
            "recommended_stake_sek": a.get("recommended_stake_base"),
            "key_number": a.get("key_number"),
            "wing_loss_pct": a.get("wing_loss_pct"),
            "event": {
                "id": event.id if event else o.event_id,
                "home_team": event.home_team if event else None,
                "away_team": event.away_team if event else None,
                "start_time": event.start_time.isoformat() if event and event.start_time else None,
                "sport": event.sport if event else None,
            },
            "detected_at": o.detected_at.isoformat() if o.detected_at else None,
        })
    return {"opportunities": out}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_rehedge_endpoint.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes/opportunities.py backend/tests/api/test_rehedge_endpoint.py
git commit -m "feat(api): add /api/opportunities/rehedge endpoint"
```

---

### Task 12: Frontend — typed fetch client

**Files:**
- Create: `frontend/src/api/rehedge.ts`

- [ ] **Step 1: Create the file**

Create `frontend/src/api/rehedge.ts`:

```typescript
/** Typed client for /api/opportunities/rehedge. */

export interface RehedgeOriginalBet {
  provider: string | null;
  market: string | null;
  outcome: string | null;
  point: number | null;
  odds: number | null;
  stake: number | null;
  currency: string | null;
}

export interface RehedgeEvent {
  id: string;
  home_team: string | null;
  away_team: string | null;
  start_time: string | null;
  sport: string | null;
}

export interface RehedgeOpportunity {
  opportunity_id: number;
  case: 'post_placement_middle' | 'clv_inversion_salvage';
  original_bet_id: number;
  original_bet: RehedgeOriginalBet | null;
  hedge_provider: string;
  hedge_market: string;
  hedge_outcome: string;
  hedge_point: number | null;
  hedge_odds: number;
  recommended_stake_sek: number;
  key_number: number | null;
  wing_loss_pct: number | null;
  event: RehedgeEvent;
  detected_at: string | null;
}

export async function fetchRehedgeOpportunities(): Promise<RehedgeOpportunity[]> {
  const resp = await fetch('/api/opportunities/rehedge');
  if (!resp.ok) {
    throw new Error(`/api/opportunities/rehedge failed: ${resp.status}`);
  }
  const data: { opportunities: RehedgeOpportunity[] } = await resp.json();
  return data.opportunities;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/rehedge.ts
git commit -m "feat(frontend): add typed rehedge fetch client"
```

---

### Task 13: Frontend — `RehedgeSection` component

**Files:**
- Create: `frontend/src/pages/play/RehedgeSection.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/pages/play/RehedgeSection.tsx`:

```tsx
import { useEffect, useState } from 'react';

import { fetchRehedgeOpportunities, type RehedgeOpportunity } from '../../api/rehedge';

/**
 * Read-only display of open-position rehedge candidates.
 *
 * Phase 1: shows the candidate side-by-side with the original bet so the
 * bettor can manually place the hedge. Phase 2 (separate plan) wires
 * auto-placement via arb_runner.
 */
export function RehedgeSection() {
  const [opps, setOpps] = useState<RehedgeOpportunity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchRehedgeOpportunities();
        if (!cancelled) {
          setOpps(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 30_000); // poll every 30s
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading) return <div className="text-sm text-gray-500">Loading rehedge candidates…</div>;
  if (error) return <div className="text-sm text-red-600">Rehedge fetch failed: {error}</div>;
  if (opps.length === 0) {
    return (
      <div className="text-sm text-gray-500">
        No active rehedge candidates. The scanner runs every 5 minutes.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-700">
        Open-position rehedge ({opps.length})
      </h3>
      {opps.map((o) => (
        <RehedgeCard key={o.opportunity_id} opp={o} />
      ))}
    </div>
  );
}

function RehedgeCard({ opp }: { opp: RehedgeOpportunity }) {
  const bet = opp.original_bet;
  const event = opp.event;
  return (
    <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs">
      <div className="mb-2 font-medium">
        {event.home_team} vs {event.away_team}
        {opp.key_number != null && (
          <span className="ml-2 rounded bg-amber-200 px-1.5 py-0.5 text-amber-900">
            middle on {opp.key_number}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="font-semibold text-gray-700">You bet</div>
          {bet ? (
            <div>
              {bet.provider} {bet.outcome} {bet.point}<br />
              @ {bet.odds} for {bet.stake} {bet.currency}
            </div>
          ) : (
            <div className="text-gray-500">(original bet missing)</div>
          )}
        </div>
        <div>
          <div className="font-semibold text-gray-700">Hedge with</div>
          <div>
            {opp.hedge_provider} {opp.hedge_outcome} {opp.hedge_point}<br />
            @ {opp.hedge_odds} for {opp.recommended_stake_sek.toFixed(2)} SEK
          </div>
        </div>
      </div>
      {opp.wing_loss_pct != null && (
        <div className="mt-2 text-gray-600">
          Wing loss if no middle: {(opp.wing_loss_pct * 100).toFixed(2)}%
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/play/RehedgeSection.tsx
git commit -m "feat(frontend): RehedgeSection component for open-position hedge candidates"
```

---

### Task 14: Frontend — mount `RehedgeSection` in `PlayPage` arb sub-tab

**Files:**
- Modify: `frontend/src/pages/PlayPage.tsx`

- [ ] **Step 1: Find the arb sub-tab render block**

Run: `cd frontend && grep -n "subTab === 'arb'" src/pages/PlayPage.tsx | head -5`

Identify a stable insertion point inside the arb sub-tab JSX (above the existing arb table, after the sub-tab heading). Note the line.

- [ ] **Step 2: Insert the section**

Add to the top of `frontend/src/pages/PlayPage.tsx`:

```tsx
import { RehedgeSection } from './play/RehedgeSection';
```

Inside the arb sub-tab render block, add (replace the comment with the actual JSX context you found in Step 1):

```tsx
{subTab === 'arb' && (
  <>
    <RehedgeSection />
    {/* ... existing arb table render ... */}
  </>
)}
```

- [ ] **Step 3: Verify TypeScript compiles + lint**

Run:
```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
```
Expected: No errors.

- [ ] **Step 4: Manual smoke test**

Start the local client and load the Sports → Arbitrage sub-tab in the browser:

```bash
cd c:/Users/rasmu/betty && ./betty.bat
```

Expected: The "Open-position rehedge" section renders. If no candidates exist yet, the empty-state copy shows. No console errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/PlayPage.tsx
git commit -m "feat(frontend): mount RehedgeSection inside Sports/Arbitrage sub-tab"
```

---

### Task 15: Deploy + verify in production

**Files:** none (deploy-only)

- [ ] **Step 1: Sanity-check the full test suite locally**

Run:
```bash
cd backend && pytest tests/analysis/ tests/pipeline/ tests/api/ -v
cd frontend && npx tsc --noEmit && npm run lint
```
Expected: PASS / no errors.

- [ ] **Step 2: Push to main**

Coordinate per CLAUDE.md "Coordinate git pushes" rules:

```bash
git fetch && git log HEAD..origin/main --oneline   # see if anyone pushed since fork
git push origin main
```

If `origin` is ahead, rebase/merge first.

- [ ] **Step 3: Deploy backend (the scanner is a backend change)**

Frontend changes ship via `betty.bat` (Vite) — no deploy needed. Backend changes (rehedge_scanner.py, scheduler.py, opportunities.py) need a backend rebuild:

```bash
ssh root@148.251.40.251 "bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend"
```

- [ ] **Step 4: Verify the running container has the new code**

```bash
ssh root@148.251.40.251 "cd /opt/betty && git rev-parse HEAD"   # should match local
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health"  # note boot_id
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose logs backend --tail 50 | grep -i rehedge"
```
Expected: `[Scheduler] Starting rehedge tier: interval=300s` appears in logs.

- [ ] **Step 5: Wait one scan cycle and verify candidates appear (or don't)**

After ~10 min (3 min startup delay + 5 min interval + extraction warmup), check:

```bash
ssh root@148.251.40.251 "cd /opt/betty/backend && docker compose logs backend --tail 200 | grep -i 'Scheduler:rehedge'"
```

Expected: at least one `[Scheduler:rehedge]` log line (either "+N inserted/updated/deactivated" or silence if no candidates — both are healthy).

Query the DB via postgres MCP:

```sql
SELECT id, event_id, provider1_id, outcome1, point, odds1, annotations
FROM opportunities
WHERE type = 'rehedge' AND is_active = TRUE
ORDER BY detected_at DESC
LIMIT 10;
```

Expected: 0 or more rows (depends on current market state). The query just needs to not error.

- [ ] **Step 6: Final commit / no commit**

No code changes in this task — verification only. If logs revealed an issue, file a follow-up task; do NOT mark this plan complete until step 5 logs show the scanner running cleanly for at least one full cycle.

---

## What's NOT in this plan (intentional Phase 2 splits)

- **Auto-placement.** Phase 1 ships emit-only. Bettors place the hedge manually from the UI. Phase 2 plan wires `arb_runner.place_single_leg` with `arb_group_id` linking back to the original bet.
- **Case 3 (CLV-inversion salvage).** Has higher false-positive risk because Pinnacle fair odds bounce. Ship after Case 1 has produced 1-2 weeks of data to calibrate the inversion threshold.
- **Provider-limit awareness.** Case 3 needs a "this provider is limited on this market" lookup that doesn't exist cleanly today; extending `limit_service` is a prerequisite Phase 2 dependency.
- **Currency-drift gate during human confirmation window.** Phase 2 — once auto-placement lands, snapshot FX at scan time and abort placement if drift > 0.3%.

These belong in `docs/superpowers/plans/2026-XX-XX-rehedge-phase-2-placement.md` (to be written after Phase 1 ships and produces observation data).
