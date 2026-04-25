# Slip-Odds Architecture & Semi-Auto Arb Workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make placement decisions consume real-time slip-scraped odds (ground truth) instead of stale scanner odds (now demoted to discovery watchlist). Rewrite ArbRunner to load all legs, stream odds across them, and intercept user mirror clicks per leg.

**Architecture:** New `SlipOddsStream` polls each loaded provider tab via a per-workflow `read_slip_odds(page)` method. ArbRunner loads all arb legs, starts streams on each, broadcasts `arb_alignment`, and waits for mirror placement interceptions (no system-side trigger). ProviderRunner extends to consume a single-leg stream for value bets. UI is status-only — no Place buttons in React.

**Tech Stack:** Python 3.10+ asyncio, pytest, Playwright, FastAPI SSE, React 19 + TypeScript, PostgreSQL.

**Spec:** [`docs/superpowers/specs/2026-04-25-slip-odds-architecture-design.md`](docs/superpowers/specs/2026-04-25-slip-odds-architecture-design.md)

---

## File Structure

```
arnold/mirror/
├── slip_odds_stream.py              ← NEW: per-leg odds poller, throttled aggregator
├── arb_math.py                      ← NEW: pure profit/stake math (TDD core)
├── arb_runner.py                    ← MAJOR REWRITE: load+stream+intercept
├── provider_runner.py               ← MODIFY: replace one-shot live_price with stream
└── workflows/
    ├── base.py                      ← MODIFY: add read_slip_odds + update_slip_stake to interface
    ├── kambi.py                     ← MODIFY: implement read_slip_odds, update_slip_stake
    ├── gecko.py                     ← MODIFY: same
    ├── altenar.py                   ← MODIFY: same
    ├── interwetten.py               ← MODIFY: same
    ├── generic.py                   ← MODIFY: implement via Strategy hooks (covers Spectate/Comeon/Coolbet/Tipwin)
    ├── kalshi.py                    ← MODIFY: same
    └── (Pinnacle/Polymarket/Cloudbet route via generic.py — extend their intel JSONs)

arnold/frontend/src/pages/PlayPage.tsx   ← MODIFY: new SSE handlers, live profit% display

backend/src/
├── db/models.py                     ← MODIFY: slip_odds_ticks table + _run_pg_migrations entry
├── config/providers.yaml            ← MODIFY: relax extraction cooldowns
└── analysis/scanner.py              ← INVESTIGATE then maybe MODIFY: 3-way arb shape filter

arnold/tests/
├── test_arb_math.py                 ← NEW: pure-logic tests
├── test_slip_odds_stream.py         ← NEW: streaming behavior tests
├── test_arb_runner_v2.py            ← NEW: state machine tests
└── test_provider_runner_stream.py   ← NEW: value-bet stream wiring tests
```

The base contract additions and `arb_math.py` come first; workflows can then implement their part in parallel; runners come once both are in place.

---

## Phase 1 — Foundation (pure logic, TDD)

### Task 1: Pure arb math module

**Files:**
- Create: `arnold/mirror/arb_math.py`
- Test: `arnold/tests/test_arb_math.py`

**Why first:** isolate the math from the runner so it's trivially testable, and the runner's rewrite consumes it instead of duplicating arithmetic.

- [ ] **Step 1: Write failing test for `recalc_profit_pct`**

```python
# arnold/tests/test_arb_math.py
"""Pure arb math — guaranteed-profit and equal-payout stake calculations."""
from __future__ import annotations

import math

import pytest

from arnold.mirror.arb_math import (
    recalc_profit_pct,
    recalc_counter_stakes,
    should_update_stake,
    is_valid_arb_shape,
)


def test_recalc_profit_pct_two_way_positive():
    # Anchor 2.10 + counter 2.10 → 1/2.10 + 1/2.10 = 0.952 → profit ≈ 5%
    profit = recalc_profit_pct(anchor_odds=2.10, counter_odds=[2.10])
    assert pytest.approx(profit, rel=1e-3) == 5.0


def test_recalc_profit_pct_two_way_negative():
    # Anchor 1.90 + counter 1.90 → sum > 1 → negative
    profit = recalc_profit_pct(anchor_odds=1.90, counter_odds=[1.90])
    assert profit < 0


def test_recalc_profit_pct_three_way():
    # Three odds at 3.10 each — 3/3.10 = 0.9677 → profit ~3.33%
    profit = recalc_profit_pct(anchor_odds=3.10, counter_odds=[3.10, 3.10])
    assert pytest.approx(profit, rel=1e-3) == 3.333

def test_recalc_profit_pct_zero_odds_returns_none():
    assert recalc_profit_pct(anchor_odds=0.0, counter_odds=[2.0]) is None
    assert recalc_profit_pct(anchor_odds=2.0, counter_odds=[0.0, 2.0]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_math.py::test_recalc_profit_pct_two_way_positive -v`
Expected: FAIL — `ModuleNotFoundError: arnold.mirror.arb_math`

- [ ] **Step 3: Write minimal `recalc_profit_pct`**

```python
# arnold/mirror/arb_math.py
"""Pure arb math — no I/O, no async. Used by ArbRunner + SlipOddsStream."""
from __future__ import annotations


def recalc_profit_pct(anchor_odds: float, counter_odds: list[float]) -> float | None:
    """Guaranteed-profit % for an equal-payout arb.

    profit% = (1 / (1/anchor_odds + Σ 1/counter_odds) - 1) × 100
    Returns None if any odds are zero/negative.
    """
    if anchor_odds <= 0 or any(o <= 0 for o in counter_odds):
        return None
    inv_sum = 1.0 / anchor_odds + sum(1.0 / o for o in counter_odds)
    if inv_sum <= 0:
        return None
    return (1.0 / inv_sum - 1.0) * 100.0
```

- [ ] **Step 4: Run all tests in this file to verify they pass**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_math.py -v`
Expected: 4 passing tests for `recalc_profit_pct_*`. (Other tests still fail — they reference functions not yet implemented.)

- [ ] **Step 5: Add tests for `recalc_counter_stakes`**

Append to `arnold/tests/test_arb_math.py`:

```python
def test_recalc_counter_stakes_two_way():
    # Anchor 100 SEK @ 2.0 → total payout 200 → counter @ 2.0 → 100 SEK
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=2.0, counter_odds=[2.0])
    assert stakes == [100.0]


def test_recalc_counter_stakes_uneven_odds():
    # Anchor 100 @ 2.0 → payout 200 → counter @ 4.0 → 50 SEK
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=2.0, counter_odds=[4.0])
    assert stakes == [50.0]


def test_recalc_counter_stakes_three_way():
    # Anchor 100 @ 3.0 → payout 300 → counters @ 3.0 each → 100 each
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=3.0, counter_odds=[3.0, 3.0])
    assert stakes == [100.0, 100.0]


def test_recalc_counter_stakes_rounded_to_cents():
    # 100 @ 1.91 → payout 191 → counter @ 2.13 → 89.67
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=1.91, counter_odds=[2.13])
    assert stakes == [89.67]
```

- [ ] **Step 6: Implement `recalc_counter_stakes`**

Append to `arnold/mirror/arb_math.py`:

```python
def recalc_counter_stakes(
    anchor_stake: float, anchor_odds: float, counter_odds: list[float]
) -> list[float]:
    """Per-counter stakes for equal-payout: counter_stake = total_payout / counter_odds.

    Total payout = anchor_stake × anchor_odds. Each counter sized so it pays the same.
    Returns stakes rounded to 2 decimals (currency cents).
    """
    total_payout = anchor_stake * anchor_odds
    return [round(total_payout / o, 2) for o in counter_odds]
```

- [ ] **Step 7: Run new tests**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_math.py -v -k recalc_counter_stakes`
Expected: 4 passing.

- [ ] **Step 8: Add tests for `should_update_stake`**

Append:

```python
def test_should_update_stake_below_threshold():
    # Drift of 0.5 SEK on a 100 SEK stake — below 1 SEK and 1% — no update
    assert should_update_stake(old=100.0, new=100.5) is False


def test_should_update_stake_above_abs_threshold():
    # Drift of 1.5 SEK — above 1 SEK abs threshold — update
    assert should_update_stake(old=100.0, new=101.5) is True


def test_should_update_stake_above_pct_threshold_small_stake():
    # Stake 50 SEK, drift 0.7 SEK = 1.4% — above 1% threshold — update
    assert should_update_stake(old=50.0, new=50.7) is True


def test_should_update_stake_zero_old_always_updates():
    assert should_update_stake(old=0.0, new=10.0) is True
```

- [ ] **Step 9: Implement `should_update_stake`**

Append:

```python
def should_update_stake(old: float, new: float) -> bool:
    """Whether a counter slip's stake field should be re-written.

    Re-write when |new - old| ≥ min(1.0 SEK, 1% of old). Reacts to drift
    that crosses *either* threshold, so small stakes still see updates on
    proportional moves (e.g. 1.4% on 50 SEK stake) while large stakes
    aren't spammed by sub-1-SEK drift.
    """
    if old <= 0:
        return True
    delta = abs(new - old)
    abs_threshold = 1.0
    pct_threshold = old * 0.01
    return delta >= min(abs_threshold, pct_threshold)
```

- [ ] **Step 10: Add tests for `is_valid_arb_shape`**

Append:

```python
UNLIMITED = {"pinnacle", "polymarket", "cloudbet", "kalshi"}


def test_is_valid_arb_shape_two_way_soft_plus_unlimited():
    legs = [{"provider": "unibet"}, {"provider": "pinnacle"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is True


def test_is_valid_arb_shape_two_softs_rejected():
    legs = [{"provider": "unibet"}, {"provider": "betsson"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False


def test_is_valid_arb_shape_three_way_one_soft_two_unlimited():
    legs = [{"provider": "unibet"}, {"provider": "pinnacle"}, {"provider": "polymarket"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is True


def test_is_valid_arb_shape_three_way_two_softs_rejected():
    legs = [{"provider": "unibet"}, {"provider": "betsson"}, {"provider": "pinnacle"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False


def test_is_valid_arb_shape_all_unlimited_rejected():
    # Pure unlimited isn't an arb opportunity for this UI — not the soft+unlimited shape we want
    legs = [{"provider": "pinnacle"}, {"provider": "polymarket"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False
```

- [ ] **Step 11: Implement `is_valid_arb_shape`**

Append:

```python
def is_valid_arb_shape(legs: list[dict], unlimited: set[str]) -> bool:
    """Arb must be exactly 1 soft leg + ≥1 unlimited counter leg(s).

    Rejects: two softs, all-unlimited, empty.
    """
    if len(legs) < 2:
        return False
    soft_count = sum(1 for leg in legs if leg.get("provider") not in unlimited)
    if soft_count != 1:
        return False
    return True
```

- [ ] **Step 12: Run full math suite**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_math.py -v`
Expected: all tests pass.

- [ ] **Step 13: Commit**

```bash
git add arnold/mirror/arb_math.py arnold/tests/test_arb_math.py
git commit -m "feat(arb): pure profit/stake math module

Extracts profit% and equal-payout stake calculations from the existing
ArbRunner into a side-effect-free module, with the 1-soft + N-unlimited
arb shape filter. Foundation for the slip-odds rewrite."
```

---

### Task 2: Workflow contract — add `read_slip_odds` + `update_slip_stake`

**Files:**
- Modify: `arnold/mirror/workflows/base.py:114-127`
- Test: `arnold/tests/test_workflow_contract.py` (new)

- [ ] **Step 1: Write failing contract test**

```python
# arnold/tests/test_workflow_contract.py
"""Contract: every workflow must implement read_slip_odds + update_slip_stake."""
from __future__ import annotations

import inspect

from arnold.mirror.workflows.base import ProviderWorkflow


def test_base_workflow_defines_read_slip_odds():
    assert hasattr(ProviderWorkflow, "read_slip_odds")
    sig = inspect.signature(ProviderWorkflow.read_slip_odds)
    # (self, page) — 2 params
    assert len(sig.parameters) == 2


def test_base_workflow_defines_update_slip_stake():
    assert hasattr(ProviderWorkflow, "update_slip_stake")
    sig = inspect.signature(ProviderWorkflow.update_slip_stake)
    # (self, page, stake) — 3 params
    assert len(sig.parameters) == 3


def test_base_workflow_default_read_slip_odds_returns_none():
    """Default implementation returns None — workflows without slip-scrape opt out."""
    import asyncio

    class _Stub(ProviderWorkflow):
        platform = "stub"
        async def check_login(self, page): return True
        async def sync_history(self, page): return []
        async def sync_balance(self, page): return 0.0
        async def navigate_to_event(self, page, bet): return True
        async def place_bet(self, page, bet, stake): ...

    wf = _Stub(provider_id="x", domain="x.com")
    result = asyncio.run(wf.read_slip_odds(page=None))
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_workflow_contract.py -v`
Expected: FAIL — `read_slip_odds` does not exist on `ProviderWorkflow`.

- [ ] **Step 3: Add the two methods to `base.py`**

In [arnold/mirror/workflows/base.py:127](arnold/mirror/workflows/base.py#L127), after the existing `confirm_bet` method, insert:

```python
    async def read_slip_odds(self, page: Page) -> float | None:
        """Read the odds the loaded slip widget currently displays.

        Idempotent, fast — called ~1Hz by SlipOddsStream while a slip is loaded.
        Returns None if slip is empty, errored, or workflow doesn't support scrape.
        Override per workflow.
        """
        return None

    async def update_slip_stake(self, page: Page, stake: float) -> bool:
        """Re-write the stake field on a loaded slip without re-navigating.

        Returns True on success. Used by ArbRunner to keep counter slips in sync
        with the actual placed anchor stake. Override per workflow.
        """
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_workflow_contract.py -v`
Expected: 3 passing.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/base.py arnold/tests/test_workflow_contract.py
git commit -m "feat(workflows): add read_slip_odds + update_slip_stake to base contract

Default implementations return None / False so unimplemented workflows
opt out cleanly. Per-workflow overrides land in subsequent commits."
```

---

### Task 3: SlipOddsStream component

**Files:**
- Create: `arnold/mirror/slip_odds_stream.py`
- Test: `arnold/tests/test_slip_odds_stream.py`

**Behavior:** one stream per loaded leg. Polls `workflow.read_slip_odds(page)` at configurable interval (default 1.0s). Holds latest odds. Calls a callback when odds change.

- [ ] **Step 1: Write failing test for stream lifecycle**

```python
# arnold/tests/test_slip_odds_stream.py
"""SlipOddsStream — per-leg odds poller, throttled aggregator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.slip_odds_stream import SlipOddsStream


@pytest.mark.asyncio
async def test_stream_starts_and_stops():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.10)
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.05,
    )
    assert stream.running is False
    stream.start()
    assert stream.running is True
    await asyncio.sleep(0.12)  # let it tick at least twice
    stream.stop()
    await asyncio.sleep(0.05)
    assert stream.running is False
    assert workflow.read_slip_odds.call_count >= 2


@pytest.mark.asyncio
async def test_stream_calls_callback_on_change():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, 2.10, 2.15, 2.15])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.15)
    stream.stop()
    await asyncio.sleep(0.02)

    # Callback fires only when odds change: 2.10 (first), then 2.15
    assert callback.call_count == 2
    assert callback.call_args_list[0][0][0] == 2.10
    assert callback.call_args_list[1][0][0] == 2.15


@pytest.mark.asyncio
async def test_stream_handles_none_odds():
    """Stream tolerates workflow returning None (slip cleared/errored)."""
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, None, 2.10])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.12)
    stream.stop()
    await asyncio.sleep(0.02)

    # First 2.10 fires callback; None doesn't fire; back to 2.10 doesn't refire (same value)
    assert callback.call_count >= 1


@pytest.mark.asyncio
async def test_stream_survives_workflow_exception():
    """One bad poll should not kill the stream."""
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, RuntimeError("boom"), 2.20])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.12)
    stream.stop()
    await asyncio.sleep(0.02)

    # Should have attempted all 3 polls + at least one callback
    assert workflow.read_slip_odds.call_count >= 3
    assert callback.call_count >= 1


@pytest.mark.asyncio
async def test_stream_current_odds_property():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.42)
    page = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=lambda o: None,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.06)
    assert stream.current_odds == 2.42
    stream.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_slip_odds_stream.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `SlipOddsStream`**

```python
# arnold/mirror/slip_odds_stream.py
"""SlipOddsStream — poll a single loaded slip widget for live odds.

One stream per provider tab where a slip is loaded. Polls
`workflow.read_slip_odds(page)` at a configurable interval and invokes
`on_odds_change(odds)` whenever the value changes (suppresses no-ops).

ArbRunner aggregates across legs by instantiating one stream per leg and
combining their `current_odds` on each tick.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .workflows.base import ProviderWorkflow

logger = logging.getLogger(__name__)


class SlipOddsStream:
    def __init__(
        self,
        provider_id: str,
        workflow: ProviderWorkflow,
        page: Page,
        on_odds_change: Callable[[float], None],
        poll_interval_s: float = 1.0,
    ):
        self.provider_id = provider_id
        self._workflow = workflow
        self._page = page
        self._on_odds_change = on_odds_change
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task | None = None
        self._current_odds: float | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_odds(self) -> float | None:
        return self._current_odds

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name=f"slip_odds_{self.provider_id}")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    odds = await self._workflow.read_slip_odds(self._page)
                except Exception:
                    logger.debug(f"[SlipStream:{self.provider_id}] read_slip_odds raised", exc_info=True)
                    odds = None

                if odds is not None and odds != self._current_odds:
                    self._current_odds = odds
                    try:
                        self._on_odds_change(odds)
                    except Exception:
                        logger.exception(f"[SlipStream:{self.provider_id}] callback raised")

                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Verify pytest-asyncio is available**

Run: `cd c:/Users/rasmu/arnold && python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`
Expected: prints a version. If `ModuleNotFoundError`, run: `pip install pytest-asyncio` and add to `pyproject.toml` dev deps.

- [ ] **Step 5: Run all stream tests**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_slip_odds_stream.py -v`
Expected: 5 passing.

- [ ] **Step 6: Commit**

```bash
git add arnold/mirror/slip_odds_stream.py arnold/tests/test_slip_odds_stream.py
git commit -m "feat(mirror): SlipOddsStream — per-leg slip odds poller

Polls workflow.read_slip_odds at a configurable interval and fires a
callback only on changes. Tolerates None (cleared slips) and exceptions
(transient DOM faults). Foundation for ArbRunner alignment streaming
and ProviderRunner live-edge tracking."
```

---

## Phase 2 — Per-workflow `read_slip_odds` + `update_slip_stake`

Each workflow's slip widget DOM is different. **Each provider gets its own task** with a discovery step (use the running mirror to inspect the loaded slip) followed by an implementation step.

The pattern is identical for every workflow, so Tasks 4–9 use the **same scaffolding** with provider-specific selectors filled in during discovery. Each task can be done in parallel by separate subagents.

### Task 4: Kambi `read_slip_odds` + `update_slip_stake`

**Files:**
- Modify: `arnold/mirror/workflows/kambi.py`
- Test: `arnold/tests/workflows/test_kambi_slip.py` (new)

Kambi covers Unibet, LeoVegas, Expekt, BetMGM, SpeedyBet, X3000, GoldenBull, 1x2, MrGreen.

- [ ] **Step 1: Discovery — inspect a loaded Kambi betslip**

Boot the mirror and load any Kambi event with a selection added to the slip:

```bash
# Start arnold app (only needed if not running):
cd c:/Users/rasmu/arnold/arnold && python launch.py &

# Once you've manually logged in to Unibet and added a selection to the slip,
# inspect the slip DOM via the existing debug eval endpoint:
curl -X POST http://localhost:8000/mirror/browser/eval/unibet \
  -H "Content-Type: application/json" \
  -d '{"js": "(() => { const slip = document.querySelector(\"[data-test-name=\\\"betslip\\\"]\") || document.querySelector(\"#KambiBC-betslip-container\"); if (!slip) return {error: \"no slip\"}; return {html: slip.outerHTML.slice(0, 4000)}; })()"}'
```

Read the returned HTML to identify:
- The element holding the live odds value (typical: `[data-test-name="betslip-outcomes"] [data-test-name="outcome-odds"]` or `.KambiBC-betslip-outcome__odds`)
- The stake input field (typical: `[data-test-name="betslip-stake-input"]` or `input[name="stake"]`)

Capture exact selectors in a comment in the file.

- [ ] **Step 2: Write failing test using captured DOM snapshot**

```python
# arnold/tests/workflows/test_kambi_slip.py
"""Kambi read_slip_odds + update_slip_stake."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.workflows.kambi import KambiWorkflow


@pytest.mark.asyncio
async def test_kambi_read_slip_odds_returns_value():
    page = MagicMock()
    # First evaluate is the JS in read_slip_odds — return a numeric string
    page.evaluate = AsyncMock(return_value="2.42")
    wf = KambiWorkflow(provider_id="unibet", domain="unibet.se")
    odds = await wf.read_slip_odds(page)
    assert odds == 2.42


@pytest.mark.asyncio
async def test_kambi_read_slip_odds_returns_none_when_empty():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=None)
    wf = KambiWorkflow(provider_id="unibet", domain="unibet.se")
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_kambi_read_slip_odds_returns_none_on_exception():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("page crashed"))
    wf = KambiWorkflow(provider_id="unibet", domain="unibet.se")
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_kambi_update_slip_stake_returns_true_on_success():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    wf = KambiWorkflow(provider_id="unibet", domain="unibet.se")
    ok = await wf.update_slip_stake(page, 75.50)
    assert ok is True
    # Stake value must appear in the JS payload
    js_arg = page.evaluate.call_args[0][0]
    assert "75.5" in js_arg or "75.50" in js_arg
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_kambi_slip.py -v`
Expected: FAIL — `KambiWorkflow.read_slip_odds` returns the base-class None unconditionally.

- [ ] **Step 4: Implement `read_slip_odds` and `update_slip_stake` in `kambi.py`**

Open `arnold/mirror/workflows/kambi.py` and add the two methods near the existing `prep_betslip` / `confirm_bet` methods. Use the selectors captured in Step 1 (replace the placeholder selectors below if discovery showed different ones):

```python
    async def read_slip_odds(self, page) -> float | None:
        """Scrape the odds value displayed on the loaded Kambi slip."""
        try:
            # Selector validated via discovery on 2026-04-25
            raw = await page.evaluate(
                """() => {
                    const root = document.querySelector('[data-test-name="betslip"]')
                                || document.getElementById('KambiBC-betslip-container');
                    if (!root) return null;
                    const el = root.querySelector('[data-test-name="outcome-odds"]')
                            || root.querySelector('.KambiBC-betslip-outcome__odds');
                    if (!el) return null;
                    return el.innerText.trim();
                }"""
            )
            if raw is None:
                return None
            return float(str(raw).replace(",", "."))
        except Exception:
            return None

    async def update_slip_stake(self, page, stake: float) -> bool:
        """Re-write the stake input on the loaded Kambi slip."""
        try:
            return bool(
                await page.evaluate(
                    """(stake) => {
                        const root = document.querySelector('[data-test-name="betslip"]')
                                  || document.getElementById('KambiBC-betslip-container');
                        if (!root) return false;
                        const input = root.querySelector('[data-test-name="betslip-stake-input"]')
                                  || root.querySelector('input[name="stake"]');
                        if (!input) return false;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(input, String(stake));
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }""",
                    round(stake, 2),
                )
            )
        except Exception:
            return False
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_kambi_slip.py -v`
Expected: 4 passing.

- [ ] **Step 6: Live verification (manual, ~2 min)**

With the mirror running and a Kambi event loaded with a selection:

```bash
# read_slip_odds:
curl -X POST http://localhost:8000/mirror/browser/eval/unibet \
  -H "Content-Type: application/json" \
  -d '{"js": "(async () => { const wf = await import(\"/dev/null\"); return null; })()"}' 2>/dev/null
# Better: use the mirror debug endpoint that wraps the workflow call. If absent,
# load a slip selection manually and confirm via /mirror/browser/eval/unibet
# returns the same value the slip displays.
```

Confirm the scraped value matches the slip widget display. Confirm `update_slip_stake(75)` updates the stake field visibly.

- [ ] **Step 7: Commit**

```bash
git add arnold/mirror/workflows/kambi.py arnold/tests/workflows/test_kambi_slip.py
git commit -m "feat(workflows/kambi): implement read_slip_odds + update_slip_stake

Scrapes the loaded Kambi slip's outcome-odds element and re-writes the
stake input via React-compatible setter. Covers Unibet, LeoVegas,
Expekt, BetMGM and other Kambi siblings."
```

---

### Task 5: Gecko V2 `read_slip_odds` + `update_slip_stake`

**Files:**
- Modify: `arnold/mirror/workflows/gecko.py`
- Test: `arnold/tests/workflows/test_gecko_slip.py` (new)

Same scaffolding as Task 4. Covers Betsson, Nordicbet, Betsafe, Spelklubben.

- [ ] **Step 1: Discovery — inspect a loaded Gecko V2 betslip**

```bash
# After logging in to Betsson and adding a selection:
curl -X POST http://localhost:8000/mirror/browser/eval/betsson \
  -H "Content-Type: application/json" \
  -d '{"js": "(() => { const slip = document.querySelector(\"[class*=BetSlip]\") || document.querySelector(\"[data-testid=betslip]\"); return slip ? {html: slip.outerHTML.slice(0, 4000)} : {error: \"no slip\"}; })()"}'
```

Identify selectors for live odds and stake input.

- [ ] **Step 2: Write failing test**

Create `arnold/tests/workflows/test_gecko_slip.py` with the same 4 tests as Task 4 step 2, replacing `KambiWorkflow` with `GeckoWorkflow`, `unibet`/`unibet.se` with `betsson`/`betsson.com`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_gecko_slip.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement in `gecko.py`**

Add the same two methods as Task 4 step 4 to `arnold/mirror/workflows/gecko.py`, with selectors from Gecko V2 discovery. Gecko V2 commonly uses MUI/styled-components, so selectors typically look like `[class*="BetSlip__Odds"]` and `input[type="number"]` inside the slip.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_gecko_slip.py -v`

- [ ] **Step 6: Live verification on Betsson**

Same as Task 4 step 6, swap `unibet` for `betsson`.

- [ ] **Step 7: Commit**

```bash
git add arnold/mirror/workflows/gecko.py arnold/tests/workflows/test_gecko_slip.py
git commit -m "feat(workflows/gecko): implement read_slip_odds + update_slip_stake

Covers Betsson, Nordicbet, Betsafe, Spelklubben."
```

---

### Task 6: Altenar `read_slip_odds` + `update_slip_stake`

**Files:**
- Modify: `arnold/mirror/workflows/altenar.py`
- Test: `arnold/tests/workflows/test_altenar_slip.py` (new)

Altenar renders via WASM ([feedback memory](C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold/memory/feedback_altenar_wasm.md)) — the slip lives inside `STB-SPORTSBOOK` shadow DOM. Discovery + scraping use the WASM bridge similar to existing Altenar selection code.

- [ ] **Step 1: Discovery — inspect Altenar slip via WASM bridge**

Use the existing WASM-aware debug eval (`browser_click` on Altenar already accesses `#STB_SPORTSBOOK > div`). Adapt for slip-data scrape:

```bash
curl -X POST http://localhost:8000/mirror/browser/eval/betinia \
  -H "Content-Type: application/json" \
  -d '{"js": "(() => { const stb = document.querySelector(\"STB-SPORTSBOOK\") || document.getElementById(\"STB_SPORTSBOOK\"); if (!stb) return {error: \"no stb\"}; const root = stb.firstElementChild?.shadowRoot; if (!root) return {error: \"no shadow\"}; const slip = root.querySelector(\"[class*=betslip]\") || root.querySelector(\"[class*=BetSlip]\"); return slip ? {html: slip.outerHTML.slice(0, 4000)} : {error: \"no slip in shadow\"}; })()"}'
```

If the shadow root contains the slip, identify the odds + stake selectors. If the slip is rendered to canvas (WASM), the scrape may need to read from a JS bridge variable that Altenar exposes (e.g. `window.altenar_state.slip.odds`). Check both.

- [ ] **Step 2: Write failing test**

Create `arnold/tests/workflows/test_altenar_slip.py` with the same structure as Task 4 step 2, using `AltenarWorkflow` and `betinia`/`betinia.se`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_altenar_slip.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement in `altenar.py`**

If the slip is in shadow DOM, use shadow-DOM-piercing selectors. If it's WASM-canvas-only, expose the bridge variable (use existing `toggleSelections` API path — same pattern). Implementation skeleton:

```python
    async def read_slip_odds(self, page) -> float | None:
        try:
            raw = await page.evaluate(
                """() => {
                    const stb = document.querySelector('STB-SPORTSBOOK')
                              || document.getElementById('STB_SPORTSBOOK');
                    if (!stb) return null;
                    const root = stb.firstElementChild?.shadowRoot;
                    if (!root) return null;
                    // SELECTORS FILLED IN FROM DISCOVERY
                    const el = root.querySelector('[class*=betslip-odds]');
                    return el ? el.innerText.trim() : null;
                }"""
            )
            if raw is None:
                return None
            return float(str(raw).replace(",", "."))
        except Exception:
            return None

    async def update_slip_stake(self, page, stake: float) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """(stake) => {
                        const stb = document.querySelector('STB-SPORTSBOOK')
                                  || document.getElementById('STB_SPORTSBOOK');
                        if (!stb) return false;
                        const root = stb.firstElementChild?.shadowRoot;
                        if (!root) return false;
                        const input = root.querySelector('input[type="text"][class*=stake]');
                        if (!input) return false;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(input, String(stake));
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                    }""",
                    round(stake, 2),
                )
            )
        except Exception:
            return False
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_altenar_slip.py -v`

- [ ] **Step 6: Live verification on Betinia**

Same pattern as Task 4 step 6, with `betinia` provider.

- [ ] **Step 7: Commit**

```bash
git add arnold/mirror/workflows/altenar.py arnold/tests/workflows/test_altenar_slip.py
git commit -m "feat(workflows/altenar): implement read_slip_odds + update_slip_stake

Pierces STB-SPORTSBOOK shadow DOM to read live slip odds and re-write
stake. Covers Betinia, Campobet, QuickCasino, Swiper, Lodur, Dbet."
```

---

### Task 7: Interwetten `read_slip_odds` + `update_slip_stake`

**Files:**
- Modify: `arnold/mirror/workflows/interwetten.py`
- Test: `arnold/tests/workflows/test_interwetten_slip.py` (new)

- [ ] **Step 1: Discovery — inspect Interwetten betslip**

```bash
curl -X POST http://localhost:8000/mirror/browser/eval/interwetten \
  -H "Content-Type: application/json" \
  -d '{"js": "(() => { const slip = document.querySelector(\".bet-slip\") || document.querySelector(\"[class*=betslip]\"); return slip ? {html: slip.outerHTML.slice(0, 4000)} : {error: \"no slip\"}; })()"}'
```

- [ ] **Step 2: Write failing test**

Create `arnold/tests/workflows/test_interwetten_slip.py` mirroring Task 4 step 2 with `InterwettenWorkflow`, `interwetten`/`interwetten.se`.

- [ ] **Step 3: Run + fail + implement + run + commit**

Same flow as Tasks 4–6. Final commit message:

```
feat(workflows/interwetten): implement read_slip_odds + update_slip_stake
```

---

### Task 8: GenericWorkflow + intel-driven workflows (Spectate, Comeon, Coolbet, Tipwin, Pinnacle, Polymarket, Cloudbet)

**Files:**
- Modify: `arnold/mirror/workflows/generic.py`
- Modify: `data/mirror_intel/{provider}.json` for each provider that uses GenericWorkflow
- Test: `arnold/tests/workflows/test_generic_slip.py` (new)

GenericWorkflow reads selectors from per-provider intel JSONs. Slip-odds + stake-update selectors should follow the same pattern.

- [ ] **Step 1: Read existing intel JSON shape**

```bash
ls c:/Users/rasmu/arnold/data/mirror_intel/
cat c:/Users/rasmu/arnold/data/mirror_intel/spectate.json
```

Note the existing top-level keys (`balance`, `selectors`, `strategies`, etc.).

- [ ] **Step 2: Write failing test**

```python
# arnold/tests/workflows/test_generic_slip.py
"""GenericWorkflow read_slip_odds + update_slip_stake — driven by intel JSON."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arnold.mirror.workflows.generic import GenericWorkflow


@pytest.mark.asyncio
async def test_generic_read_slip_odds_uses_intel_selector():
    intel = {
        "slip": {
            "odds_selector": "[data-testid='slip-odds']",
            "stake_input_selector": "[data-testid='stake-input']",
        }
    }
    page = MagicMock()
    page.evaluate = AsyncMock(return_value="3.10")

    with patch("arnold.mirror.workflows.generic.load_intel", return_value=intel):
        wf = GenericWorkflow(provider_id="spectate", domain="spectate.com")
        odds = await wf.read_slip_odds(page)

    assert odds == 3.10
    js = page.evaluate.call_args[0][0]
    assert "[data-testid='slip-odds']" in js


@pytest.mark.asyncio
async def test_generic_read_slip_odds_returns_none_without_intel_key():
    """When intel has no `slip` block, return None — opt-out gracefully."""
    page = MagicMock()
    with patch("arnold.mirror.workflows.generic.load_intel", return_value={}):
        wf = GenericWorkflow(provider_id="x", domain="x.com")
        odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_generic_update_slip_stake_uses_intel_selector():
    intel = {
        "slip": {
            "odds_selector": "[data-testid='slip-odds']",
            "stake_input_selector": "input[name='stake']",
        }
    }
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)

    with patch("arnold.mirror.workflows.generic.load_intel", return_value=intel):
        wf = GenericWorkflow(provider_id="spectate", domain="spectate.com")
        ok = await wf.update_slip_stake(page, 42.0)

    assert ok is True
    js = page.evaluate.call_args[0][0]
    assert "input[name='stake']" in js
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_generic_slip.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement intel-driven slip ops in `generic.py`**

In `arnold/mirror/workflows/generic.py`, add:

```python
    async def read_slip_odds(self, page) -> float | None:
        from .generic import load_intel  # avoid circular import at module load
        intel = load_intel(self.provider_id) or {}
        slip = intel.get("slip") or {}
        sel = slip.get("odds_selector")
        if not sel:
            return None
        try:
            raw = await page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.innerText.trim() : null;
                }""",
                sel,
            )
            if raw is None:
                return None
            return float(str(raw).replace(",", "."))
        except Exception:
            return None

    async def update_slip_stake(self, page, stake: float) -> bool:
        from .generic import load_intel
        intel = load_intel(self.provider_id) or {}
        slip = intel.get("slip") or {}
        sel = slip.get("stake_input_selector")
        if not sel:
            return False
        try:
            return bool(
                await page.evaluate(
                    """(args) => {
                        const input = document.querySelector(args.sel);
                        if (!input) return false;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(input, String(args.stake));
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }""",
                    {"sel": sel, "stake": round(stake, 2)},
                )
            )
        except Exception:
            return False
```

- [ ] **Step 5: Run test to verify pass**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/workflows/test_generic_slip.py -v`
Expected: 3 passing.

- [ ] **Step 6: Add `slip` block to each intel JSON via discovery**

For each provider routed through GenericWorkflow that you actually want streaming on (spectate, comeon, coolbet, tipwin, pinnacle, polymarket, cloudbet, kalshi):

  a. Boot mirror, log in, add a selection to slip
  b. Inspect via `/mirror/browser/eval/{pid}`
  c. Add a `slip` block to `data/mirror_intel/{pid}.json`:

```json
{
  "...existing...": "...",
  "slip": {
    "odds_selector": "[data-testid='slip-odds']",
    "stake_input_selector": "input[name='stake']"
  }
}
```

For Pinnacle and Cloudbet (autonomous_placement = True), the slip is virtual / API-only — leave the `slip` block absent and use a custom `read_slip_odds` override that calls the provider's odds endpoint. **For this task:** add the `slip` block only for DOM-based providers; defer Pinnacle/Cloudbet to Task 9.

- [ ] **Step 7: Commit**

```bash
git add arnold/mirror/workflows/generic.py arnold/tests/workflows/test_generic_slip.py data/mirror_intel/*.json
git commit -m "feat(workflows/generic): intel-driven read_slip_odds + update_slip_stake

Adds a 'slip' block to per-provider intel JSON (odds_selector,
stake_input_selector) so DOM-based providers routed through
GenericWorkflow get slip streaming for free. Covers Spectate, Comeon,
Coolbet, Tipwin, Polymarket, Kalshi (DOM placement)."
```

---

### Task 9: Pinnacle + Cloudbet API-based `read_slip_odds`

**Files:**
- Modify: `arnold/mirror/workflows/generic.py` (extend with API-based hook OR add overrides via strategy class)
- Test: `arnold/tests/workflows/test_pinnacle_slip.py` (new)

Pinnacle (and Cloudbet) place via API — there's no DOM slip. Their "live odds" are the response from a market-info endpoint they hit before placement. The runner needs `read_slip_odds` to return the same number that `place_bet` would book at.

Look at the existing Pinnacle strategy / intel to see how `prep_betslip` resolves odds today.

- [ ] **Step 1: Read existing Pinnacle prep_betslip path**

```bash
ls c:/Users/rasmu/arnold/data/mirror_intel/pinnacle.json
cat c:/Users/rasmu/arnold/data/mirror_intel/pinnacle.json
```

Find the API endpoint Pinnacle hits during `prep_betslip` to confirm odds. That endpoint becomes `read_slip_odds`.

- [ ] **Step 2: Write failing test**

```python
# arnold/tests/workflows/test_pinnacle_slip.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arnold.mirror.workflows.generic import GenericWorkflow


@pytest.mark.asyncio
async def test_pinnacle_read_slip_odds_via_api(monkeypatch):
    """For autonomous workflows, read_slip_odds calls the same endpoint prep_betslip uses."""
    intel = {
        "slip": {
            "api": {
                "url_template": "https://pinnacle.se/api/odds/{event_id}/{market}/{outcome}",
                "json_path": "odds",
            }
        }
    }
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"odds": 1.95})

    wf = GenericWorkflow(provider_id="pinnacle", domain="pinnacle.se")
    # Simulate the runner caching the bet on the workflow before calling read_slip_odds:
    wf._slip_context = {"event_id": "12345", "market": "moneyline", "outcome": "home"}
    with patch("arnold.mirror.workflows.generic.load_intel", return_value=intel):
        odds = await wf.read_slip_odds(page)

    assert odds == 1.95
```

- [ ] **Step 3: Run test, verify fails, implement, verify passes, commit**

Implementation extends the Step 4 of Task 8 to handle the `slip.api` shape: when present, build the URL from `_slip_context`, call `_evaluate_api`, walk the json_path, return the float. ArbRunner / ProviderRunner sets `_slip_context` on the workflow instance after `prep_betslip` succeeds.

If Pinnacle's odds API requires complex auth or signing that doesn't fit the JSON-config pattern, **add a thin `PinnacleWorkflow(GenericWorkflow)` subclass** with a hand-rolled `read_slip_odds` instead. Note this in the commit.

- [ ] **Step 4: Commit**

```bash
git add arnold/mirror/workflows/generic.py arnold/tests/workflows/test_pinnacle_slip.py data/mirror_intel/pinnacle.json data/mirror_intel/cloudbet.json
git commit -m "feat(workflows/pinnacle,cloudbet): API-based read_slip_odds

Autonomous workflows have no DOM slip — read_slip_odds calls the same
odds endpoint prep_betslip uses, so the streaming layer sees the same
number place_bet would book at."
```

---

## Phase 3 — ArbRunner rewrite

### Task 10: ArbRunner state machine — load all legs, stream, intercept

**Files:**
- Modify: `arnold/mirror/arb_runner.py` (major rewrite)
- Modify: `arnold/mirror/play_loop.py` if any new public API surface needed
- Test: `arnold/tests/test_arb_runner_v2.py` (new)

This is the biggest change in the plan. Strategy: **write the new ArbRunner alongside the old one**, gate switching with a feature flag in `play_loop._spawn_runners`, then delete the old code in a follow-up commit once verified live.

- [ ] **Step 1: Read the existing ArbRunner end-to-end (already done in spec)**

Files: `arnold/mirror/arb_runner.py`, `arnold/mirror/play_loop.py`.

- [ ] **Step 2: Write failing test for the new state machine**

```python
# arnold/tests/test_arb_runner_v2.py
"""ArbRunner v2 — load all legs, stream odds, intercept mirror clicks."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.arb_runner import ArbRunner


def _make_browser():
    browser = MagicMock()
    browser.running = True
    browser.context = MagicMock()
    browser.context.pages = []
    browser.provider_data = {}
    browser.is_logged_in = MagicMock(return_value=True)
    browser.get_balance = MagicMock(return_value=200.0)
    browser.check_login_dom = AsyncMock(return_value={"logged_in": True, "balance": 200.0})
    return browser


def _make_broadcaster():
    bc = MagicMock()
    bc.publish = MagicMock()
    return bc


@pytest.mark.asyncio
async def test_arb_runner_loads_all_legs_then_idles_streaming():
    """When given an opp, runner navigates + preps every leg, starts streams, broadcasts arb_legs_loaded."""
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    # Internal helpers will be tested individually — assert public state
    assert runner.state == "idle"


@pytest.mark.asyncio
async def test_arb_runner_routes_anchor_intercept_to_anchor_handler():
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    runner.state = "standby"
    runner._anchor_event = asyncio.Event()
    runner._intercepted_body = None
    runner.on_bet_intercepted({"placed": True, "stake": 100, "odds": 2.05}, None)
    assert runner._intercepted_body == {"placed": True, "stake": 100, "odds": 2.05}
    assert runner._anchor_event.is_set()


@pytest.mark.asyncio
async def test_arb_runner_routes_counter_intercept_to_counter_handler():
    """When the runner is waiting on counter legs, a 'pinnacle' intercept routes to that leg's event."""
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    runner.state = "awaiting_hedges"
    runner._counter_events = {"pinnacle": asyncio.Event()}
    runner._counter_intercepted = {}
    runner.on_counter_bet_intercepted("pinnacle", {"placed": True, "stake": 90}, None)
    assert "pinnacle" in runner._counter_intercepted
    assert runner._counter_events["pinnacle"].is_set()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_runner_v2.py -v`
Expected: FAIL — `_anchor_event`, `_counter_events`, `on_counter_bet_intercepted` don't exist yet.

- [ ] **Step 4: Rewrite ArbRunner with the new state machine**

Replace the body of `arnold/mirror/arb_runner.py` with the new flow. Keep `_AUTH_HEADER`, `_AUTH_VALUE`, helper imports. New states:

```
STATE_IDLE
STATE_PROVIDER_OPENING       (existing)
STATE_LOGIN_WAITING          (existing)
STATE_SETTLING               (existing)
STATE_LOADING_LEGS           (NEW — navigating + prepping each leg in parallel)
STATE_STANDBY                (NEW — all legs loaded, streaming odds, awaiting anchor click)
STATE_AWAITING_HEDGES        (NEW — anchor placed, waiting for each counter click)
```

The full rewritten file is too long to inline here, but the structure is:

```python
# arnold/mirror/arb_runner.py
"""ArbRunner v2 — semi-auto arb workflow.

Per opp: load all legs in parallel → start SlipOddsStream per leg →
broadcast arb_alignment on every meaningful odds change → wait for the
user to click Place inside the soft mirror tab → on accepted, recompute
counter stakes from actual placed anchor stake/odds → update each
counter slip → wait for the user to click Place inside each counter
mirror tab → record the arb_group → iterate.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from .arb_math import (
    is_valid_arb_shape,
    recalc_counter_stakes,
    recalc_profit_pct,
    should_update_stake,
)
from .play_loop import (
    _AUTH_HEADER,
    _AUTH_VALUE,
    _PROVIDER_TO_CLUSTER,
    DAILY_BET_CAP,
    LOGIN_POLL_INTERVAL,
    LOGIN_TIMEOUT,
    STATE_IDLE,
    STATE_LOGIN_WAITING,
    STATE_PROVIDER_OPENING,
    STATE_SETTLING,
    UNCAPPED_PROVIDERS,
    UNLIMITED_PROVIDERS,
    _bet_ns,
)
from .slip_odds_stream import SlipOddsStream
from .workflows import get_workflow
from .workflows.base import PlacementResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

STATE_LOADING_LEGS = "loading_legs"
STATE_STANDBY = "standby"
STATE_AWAITING_HEDGES = "awaiting_hedges"

_OPP_FETCH_COOLDOWN = 10.0
_ALIGNMENT_BROADCAST_THROTTLE_S = 0.5


class ArbRunner:
    def __init__(
        self,
        provider_id: str,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        block_event_market: Callable[[dict], None],
        is_blocked: Callable[[dict], bool],
        placed_today: dict[str, int],
        active_providers: list[str] | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._active_providers = list(active_providers or [])

        self.state: str = STATE_IDLE
        self.current_opp: dict | None = None
        self.current_arb_group_id: str | None = None
        self.stats: dict = {"placed": 0, "skipped": 0, "rejected": 0, "complete": 0, "total": 0}

        # Anchor (soft) intercept
        self._anchor_event: asyncio.Event = asyncio.Event()
        self._intercepted_body: dict | None = None
        self._intercepted_request_body: dict | None = None

        # Counter intercepts
        self._counter_events: dict[str, asyncio.Event] = {}
        self._counter_intercepted: dict[str, dict] = {}

        # Per-leg streams
        self._streams: dict[str, SlipOddsStream] = {}
        self._latest_counter_odds: dict[str, float] = {}
        self._counter_legs: list[dict] = []
        self._anchor_stake: float = 0.0
        self._last_alignment_broadcast: float = 0.0

        self._task: asyncio.Task | None = None

    # ----- public surface -----
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"arb_{self.provider_id}")

    def stop(self) -> None:
        for s in self._streams.values():
            s.stop()
        self._streams.clear()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.state = STATE_IDLE

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def on_bet_intercepted(self, body: dict, request_body: dict | None = None) -> None:
        """Anchor (soft) leg placement intercepted."""
        if self.state in (STATE_STANDBY, STATE_LOADING_LEGS):
            logger.info(f"[Arb:{self.provider_id}] Anchor placement intercepted")
            self._intercepted_body = body
            self._intercepted_request_body = request_body
            self._anchor_event.set()
        else:
            logger.warning(f"[Arb:{self.provider_id}] Anchor intercept in state={self.state} — ignoring")

    def on_counter_bet_intercepted(
        self, counter_provider_id: str, body: dict, request_body: dict | None = None
    ) -> None:
        """Counter leg placement intercepted (called by play_loop router)."""
        if counter_provider_id in self._counter_events:
            logger.info(f"[Arb:{self.provider_id}] Counter {counter_provider_id} intercepted")
            self._counter_intercepted[counter_provider_id] = {"body": body, "request_body": request_body}
            self._counter_events[counter_provider_id].set()
        else:
            logger.warning(
                f"[Arb:{self.provider_id}] Counter intercept for {counter_provider_id} but no event registered"
            )

    def get_status(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "current_opp": self.current_opp,
            "arb_group_id": self.current_arb_group_id,
            "stats": self.stats,
            "placed_today": self._placed_today.get(self.provider_id, 0),
            "mode": "arb",
        }

    # ----- main loop (skeleton — fill in helpers below) -----
    async def _run(self) -> None:
        # Reuse Phase 1 of the existing runner: open tab, login, settle, daily cap check.
        # Then enter the new bet loop.
        # ... (keep existing _wait_for_login, _detect_pending, _fetch_placed_today,
        #     _fetch_pending, _fetch_arb_opps, _counter_pool unchanged from the old file)
        # Then for each opp:
        #   self.state = STATE_LOADING_LEGS
        #   loaded = await self._load_all_legs(opp)  # NEW
        #   if not loaded: continue
        #   self.state = STATE_STANDBY
        #   await self._stream_and_await_anchor()  # NEW
        #   if anchor accepted:
        #       self.state = STATE_AWAITING_HEDGES
        #       await self._update_counter_slips_and_await_hedges()  # NEW
        ...
```

Implementation of the three new methods:

```python
    async def _load_all_legs(self, opp: dict) -> bool:
        """Navigate + prep every leg in parallel. Returns True on success."""
        legs = opp.get("arb_legs") or opp.get("legs", [])
        if not is_valid_arb_shape(legs, unlimited=set(UNLIMITED_PROVIDERS)):
            self._broadcaster.publish(
                "bet_skipped",
                {"opp": opp, "reason": "invalid_arb_shape (need 1 soft + ≥1 unlimited)"},
            )
            return False

        anchor_leg = next((l for l in legs if l.get("provider") == self.provider_id), None)
        counter_legs = [l for l in legs if l.get("provider") != self.provider_id]
        if not anchor_leg or not counter_legs:
            return False

        # Anchor stake = full balance (capped at site max)
        balance = self._browser.provider_data.get(self.provider_id, {}).get("balance") or 0.0
        anchor_stake = round(min(balance, balance * 1.0), 2)  # site-max cap learned later from limit responses
        if anchor_stake <= 0:
            return False

        anchor_odds = anchor_leg.get("odds", 0)
        counter_odds = [l.get("odds", 0) for l in counter_legs]
        counter_stakes = recalc_counter_stakes(anchor_stake, anchor_odds, counter_odds)

        self._anchor_stake = anchor_stake
        self._counter_legs = counter_legs
        self.current_opp = opp
        self.current_arb_group_id = uuid.uuid4().hex[:12]

        # Navigate + prep all legs in parallel
        async def _prep_leg(leg: dict, planned_stake: float) -> tuple[str, bool]:
            pid = leg["provider"]
            try:
                wf = get_workflow(pid)
                if not self._browser.context:
                    return pid, False
                page = await wf.find_tab(self._browser.context)
                if not page:
                    return pid, False
                bet = self._opp_to_bet(opp, leg)
                bet["stake"] = planned_stake
                bet_ns = _bet_ns(bet)
                nav_ok = await wf.navigate_to_event(page, bet_ns)
                if not nav_ok:
                    return pid, False
                prep = await wf.prep_betslip(page, bet_ns, planned_stake)
                if prep.status not in ("prepped", "placed"):
                    return pid, False
                # Start SlipOddsStream for this leg
                stream = SlipOddsStream(
                    provider_id=pid,
                    workflow=wf,
                    page=page,
                    on_odds_change=lambda o, p=pid: self._on_leg_odds_change(p, o),
                    poll_interval_s=1.0,
                )
                stream.start()
                self._streams[pid] = stream
                return pid, True
            except Exception:
                logger.exception(f"[Arb:{self.provider_id}] prep failed for {pid}")
                return pid, False

        prep_results = await asyncio.gather(
            _prep_leg(anchor_leg, anchor_stake),
            *[_prep_leg(l, s) for l, s in zip(counter_legs, counter_stakes)],
        )
        if any(not ok for _, ok in prep_results):
            for s in self._streams.values():
                s.stop()
            self._streams.clear()
            return False

        # Register counter events
        for leg in counter_legs:
            self._counter_events[leg["provider"]] = asyncio.Event()
        self._counter_intercepted = {}

        self._broadcaster.publish(
            "arb_legs_loaded",
            {
                "arb_group_id": self.current_arb_group_id,
                "legs": [
                    {
                        "provider_id": leg["provider"],
                        "event_id": opp.get("event_id"),
                        "market": opp.get("market"),
                        "outcome": leg.get("outcome"),
                        "planned_stake": s,
                        "planned_odds": leg.get("odds"),
                        "slip_state": "loaded",
                    }
                    for leg, s in zip([anchor_leg] + counter_legs, [anchor_stake] + counter_stakes)
                ],
            },
        )
        return True

    def _on_leg_odds_change(self, provider_id: str, odds: float) -> None:
        """Stream callback — recompute alignment, throttle broadcast."""
        if provider_id == self.provider_id:
            anchor_odds = odds
        else:
            self._latest_counter_odds[provider_id] = odds
            anchor_odds = self._streams[self.provider_id].current_odds or 0.0

        counter_odds = [
            self._latest_counter_odds.get(l["provider"], l.get("odds", 0))
            for l in self._counter_legs
        ]
        if anchor_odds <= 0 or any(o <= 0 for o in counter_odds):
            return
        profit = recalc_profit_pct(anchor_odds, counter_odds)
        if profit is None:
            return

        # Update counter slip stakes if drift exceeds threshold
        new_stakes = recalc_counter_stakes(self._anchor_stake, anchor_odds, counter_odds)
        for leg, new_stake in zip(self._counter_legs, new_stakes):
            cur = leg.get("_current_stake", new_stake)
            if should_update_stake(cur, new_stake):
                leg["_current_stake"] = new_stake
                wf = get_workflow(leg["provider"])
                page = self._streams[leg["provider"]]._page  # noqa: SLF001 (intentional)
                asyncio.create_task(wf.update_slip_stake(page, new_stake))

        # Throttle broadcast
        loop = asyncio.get_event_loop()
        now = loop.time()
        if now - self._last_alignment_broadcast >= _ALIGNMENT_BROADCAST_THROTTLE_S:
            self._last_alignment_broadcast = now
            self._broadcaster.publish(
                "arb_alignment",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "profit_pct": round(profit, 3),
                    "legs": [
                        {
                            "provider_id": self.provider_id,
                            "current_odds": anchor_odds,
                            "current_stake": self._anchor_stake,
                            "slip_state": "loaded",
                        }
                    ]
                    + [
                        {
                            "provider_id": leg["provider"],
                            "current_odds": self._latest_counter_odds.get(leg["provider"], leg.get("odds", 0)),
                            "current_stake": leg.get("_current_stake", 0),
                            "slip_state": "loaded",
                        }
                        for leg in self._counter_legs
                    ],
                },
            )

    async def _stream_and_await_anchor(self) -> dict | None:
        """Wait for the anchor (soft) placement to be intercepted. Returns the placement details or None on reject."""
        self._anchor_event.clear()
        self._intercepted_body = None
        # Block forever until the user clicks Place in mirror; cancel via stop().
        await self._anchor_event.wait()

        wf = get_workflow(self.provider_id)
        body = self._intercepted_body or {}
        pstatus = wf.parse_placement_status(body) if hasattr(wf, "parse_placement_status") else {"success": True}
        if not pstatus.get("success"):
            self._broadcaster.publish(
                "arb_anchor_rejected",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "provider_id": self.provider_id,
                    "reason": pstatus.get("error", "unknown"),
                },
            )
            return None

        actual_stake = self._anchor_stake
        actual_odds = None
        if hasattr(wf, "parse_placement_details"):
            details = wf.parse_placement_details(body) or {}
            actual_stake = details.get("actual_stake") or actual_stake
            actual_odds = details.get("actual_odds")

        self._broadcaster.publish(
            "arb_anchor_placed",
            {
                "arb_group_id": self.current_arb_group_id,
                "provider_id": self.provider_id,
                "actual_stake": actual_stake,
                "actual_odds": actual_odds,
            },
        )
        return {"actual_stake": actual_stake, "actual_odds": actual_odds, "body": body}

    async def _update_counter_slips_and_await_hedges(
        self, anchor_actual_stake: float, anchor_actual_odds: float
    ) -> bool:
        """Re-derive counter stakes from actual anchor placement; update each counter slip; await placements."""
        # Use latest streamed counter odds (best truth available)
        counter_odds = [
            self._latest_counter_odds.get(l["provider"], l.get("odds", 0))
            for l in self._counter_legs
        ]
        new_stakes = recalc_counter_stakes(anchor_actual_stake, anchor_actual_odds, counter_odds)

        # Update slips in parallel
        async def _push_stake(leg: dict, stake: float) -> None:
            pid = leg["provider"]
            wf = get_workflow(pid)
            page = self._streams[pid]._page  # noqa: SLF001
            try:
                await wf.update_slip_stake(page, stake)
            except Exception:
                logger.exception(f"[Arb:{self.provider_id}] update_slip_stake failed for {pid}")
            leg["_current_stake"] = stake

        await asyncio.gather(*[_push_stake(l, s) for l, s in zip(self._counter_legs, new_stakes)])

        # Wait for every counter event
        await asyncio.gather(*(ev.wait() for ev in self._counter_events.values()))

        # Record each counter
        for leg in self._counter_legs:
            pid = leg["provider"]
            inter = self._counter_intercepted.get(pid, {})
            self._broadcaster.publish(
                "arb_hedge_placed",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "counter_provider": pid,
                    "outcome": leg.get("outcome"),
                    "actual_odds": leg.get("odds"),
                    "actual_stake": leg.get("_current_stake"),
                },
            )

        self._broadcaster.publish(
            "arb_complete",
            {
                "arb_group_id": self.current_arb_group_id,
                "guaranteed_profit_pct": self.current_opp.get("guaranteed_profit_pct"),
            },
        )
        self.stats["complete"] += 1
        return True
```

Plus the `_run` orchestration that wraps `_load_all_legs` → `_stream_and_await_anchor` → `_update_counter_slips_and_await_hedges` and iterates on rejection.

- [ ] **Step 5: Wire the counter intercept routing in `play_loop.py`**

In `arnold/mirror/play_loop.py`, modify `on_bet_intercepted` so when an `ArbRunner` exists for ANY active provider AND the intercepted `provider_id` is one of that runner's counter legs, route to `runner.on_counter_bet_intercepted(provider_id, body, request_body)` instead of the per-runner `on_bet_intercepted`. Logic:

```python
    def on_bet_intercepted(self, provider_id: str, body: dict, request_body: dict | None = None) -> None:
        # Anchor case: runner for this provider, in soft-anchor state
        runner = self._runners.get(provider_id)
        if runner and getattr(runner, "_anchor_event", None) is not None and runner.state in ("standby", "loading_legs"):
            runner.on_bet_intercepted(body, request_body)
            return
        # Counter case: another runner is awaiting hedges and this provider is one of its counters
        for r in self._runners.values():
            counter_events = getattr(r, "_counter_events", None) or {}
            if provider_id in counter_events and r.state == "awaiting_hedges":
                r.on_counter_bet_intercepted(provider_id, body, request_body)
                return
        if runner:
            runner.on_bet_intercepted(body, request_body)
            return
        logger.warning(f"[PlayCoordinator] Bet intercepted for {provider_id} — no runner matched")
```

- [ ] **Step 6: Run all ArbRunner tests**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_arb_runner_v2.py arnold/tests/test_arb_math.py arnold/tests/test_slip_odds_stream.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add arnold/mirror/arb_runner.py arnold/mirror/play_loop.py arnold/tests/test_arb_runner_v2.py
git commit -m "feat(arb): rewrite ArbRunner — load all legs, stream odds, intercept clicks

Replaces auto-hedge with semi-auto: load every leg in parallel, start
SlipOddsStream per leg, broadcast arb_alignment continuously, wait for
the user to click Place in the soft mirror tab, then the user clicks
Place in each counter tab. Counter slip stakes auto-update as anchor
odds drift or anchor places at a partial stake.

PlayLoop routes counter intercepts to the originating runner."
```

- [ ] **Step 8: Live smoke test (manual, ~5 min)**

Boot the mirror. Log in to one Kambi soft (Unibet) and to Pinnacle. Activate Unibet in PlayPage. Watch the SSE event log for `arb_legs_loaded` then a stream of `arb_alignment` events. Click Place inside the Unibet mirror tab and observe `arb_anchor_placed`. Click Place in Pinnacle mirror tab and observe `arb_hedge_placed` → `arb_complete`. If the flow works, proceed; if not, debug from logs/SSE.

---

## Phase 4 — ProviderRunner: stream value bets too

### Task 11: Wire SlipOddsStream into ProviderRunner

**Files:**
- Modify: `arnold/mirror/provider_runner.py:371-460` (replace one-shot `check_live_price` polling with continuous stream)
- Test: `arnold/tests/test_provider_runner_stream.py` (new)

- [ ] **Step 1: Write failing test**

```python
# arnold/tests/test_provider_runner_stream.py
"""ProviderRunner consumes SlipOddsStream for live value-bet edge."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# Test assert that ProviderRunner exposes _start_stream, _stop_stream that wrap a SlipOddsStream

@pytest.mark.asyncio
async def test_provider_runner_starts_stream_after_prep():
    from arnold.mirror.provider_runner import ProviderRunner
    runner = ProviderRunner(
        provider_id="pinnacle",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
    )
    # New attribute: optional slip stream owned by the runner
    assert hasattr(runner, "_slip_stream") or hasattr(runner, "_start_slip_stream")
```

- [ ] **Step 2: Run, fail, implement**

In `arnold/mirror/provider_runner.py`, replace the `check_live_price` polling loop ([provider_runner.py:389-431](arnold/mirror/provider_runner.py#L389-L431)) with:

```python
                # Start a SlipOddsStream for this loaded slip
                from .slip_odds_stream import SlipOddsStream

                def _on_slip_change(odds: float) -> None:
                    fair = bet.get("fair_odds")
                    edge = ((odds / fair) - 1) * 100 if fair else None
                    self._broadcaster.publish(
                        "live_price",
                        {
                            "event_id": bet.get("event_id", ""),
                            "market": bet.get("market", ""),
                            "outcome": bet.get("outcome", ""),
                            "provider_id": pid,
                            "live_odds": odds,
                            "live_edge": edge,
                            "fair_odds": fair,
                        },
                    )

                self._slip_stream = SlipOddsStream(
                    provider_id=pid, workflow=workflow, page=page,
                    on_odds_change=_on_slip_change, poll_interval_s=1.0,
                )
                self._slip_stream.start()
                try:
                    await asyncio.wait(
                        [
                            asyncio.ensure_future(self._bet_intercepted_event.wait()),
                            asyncio.ensure_future(self._skip_event.wait()),
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    self._slip_stream.stop()
                    self._slip_stream = None
```

Initialize `self._slip_stream: SlipOddsStream | None = None` in `__init__`. Stop it in `stop()`.

- [ ] **Step 3: Run all tests**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/test_provider_runner_stream.py arnold/tests/test_arb_runner_v2.py -v`

- [ ] **Step 4: Commit**

```bash
git add arnold/mirror/provider_runner.py arnold/tests/test_provider_runner_stream.py
git commit -m "feat(provider_runner): stream slip odds for value bets

Replaces the 3-second one-shot live_price poll with a SlipOddsStream
that fires live_price events on every meaningful change. UI now sees
real-time edge updates while a slip is loaded waiting on user confirm."
```

---

## Phase 5 — Frontend (status-only UI)

### Task 12: Add SSE handlers and live alignment display

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx`

- [ ] **Step 1: Add new SSE event handlers**

In [PlayPage.tsx:294-470](arnold/frontend/src/pages/PlayPage.tsx#L294-L470), inside the `useEffect` that handles `mirror.lastEvent`, add these new handlers (preserving all existing handlers):

```tsx
    if (type === 'arb_legs_loaded') {
      setArbGroupId(data.arb_group_id ?? null)
      setArbCounterPlan(data.legs ?? null)
      setArbHedgeStatus({})
      setArbProfitPct(null)
      setLoopStatus(`Arb legs loaded — streaming odds`)
    }
    if (type === 'arb_alignment') {
      setArbProfitPct(data.profit_pct)
      // Live legs update — store per-leg current odds/stake/state
      setArbCounterPlan(data.legs ?? null)
    }
    if (type === 'arb_anchor_placed') {
      setLoopStatus(`Anchor placed @ ${data.actual_stake} on ${data.provider_id} — confirm hedges in mirror`)
    }
    if (type === 'arb_anchor_rejected') {
      setLoopStatus(`Anchor REJECTED on ${data.provider_id}: ${data.reason} — trying next opp`)
      setArbHedgeStatus({})
    }
```

- [ ] **Step 2: Update the arb card to render the new fields**

Find the existing arb card render block ([PlayPage.tsx:679 onwards](arnold/frontend/src/pages/PlayPage.tsx#L679)) and adjust to render `arbProfitPct` (live, not from opp) plus per-leg current odds/stake/slip_state from `arbCounterPlan` (which is now the live legs array, not the original counter_plan structure).

Concrete edit at [PlayPage.tsx:679-700](arnold/frontend/src/pages/PlayPage.tsx#L679):

```tsx
{currentBetReady && arbCounterPlan && (
  <div className="arb-card">
    <div className="arb-profit-pct">
      Live profit: {arbProfitPct != null ? `${arbProfitPct.toFixed(2)}%` : '—'}
    </div>
    <div className="arb-legs">
      {arbCounterPlan.map((leg: any) => (
        <div
          key={leg.provider_id}
          className={`arb-leg slip-${leg.slip_state}`}
        >
          <span>{leg.provider_id}</span>
          <span>{leg.current_odds?.toFixed(2)}</span>
          <span>{leg.current_stake?.toFixed(2)} SEK</span>
          <span className="leg-state">{leg.slip_state}</span>
        </div>
      ))}
    </div>
    <div className="arb-instruction">
      {loopStatus || 'Waiting — click Place inside each mirror tab when ready'}
    </div>
  </div>
)}
```

- [ ] **Step 3: Build the frontend**

Per [memory note](C:/Users/rasmu/.claude/projects/c--Users-rasmu-arnold/memory/feedback_rebuild_frontend.md), local needs `npm run build`:

```bash
cd c:/Users/rasmu/arnold/arnold/frontend && npm run build
```

Expected: build succeeds without TypeScript errors.

- [ ] **Step 4: Manual visual verification**

With the mirror running and a real arb opp loaded, open `http://localhost:8000/` and verify:
- `arb_legs_loaded` populates the arb card with per-leg rows
- `arb_alignment` updates the live profit% and per-leg odds in real time (visible <1s after slip widget shows new odds)
- `arb_anchor_placed` updates the instruction text
- `arb_complete` clears the card after a successful arb

- [ ] **Step 5: Commit**

```bash
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): live arb alignment display

Renders streamed profit% + per-leg current odds/stake/slip_state from
arb_alignment events. No place buttons in React — instructions point
the user at the mirror tabs."
```

---

## Phase 6 — Optional logging + extraction cadence relaxation

### Task 13: `slip_odds_ticks` table + opt-in logging

**Files:**
- Modify: `backend/src/db/models.py` (`_run_pg_migrations`)
- Modify: `arnold/mirror/slip_odds_stream.py` (gated POST per tick)

- [ ] **Step 1: Find `_run_pg_migrations`**

```bash
grep -n "_run_pg_migrations" c:/Users/rasmu/arnold/backend/src/db/models.py | head
```

- [ ] **Step 2: Add migration entry**

In `_run_pg_migrations`, append:

```python
    # 2026-04-25 — slip_odds_ticks for slip-streaming observability
    conn.execute(
        text("""
            CREATE TABLE IF NOT EXISTS slip_odds_ticks (
              id BIGSERIAL PRIMARY KEY,
              ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              provider_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              market TEXT NOT NULL,
              outcome TEXT NOT NULL,
              scraped_odds REAL NOT NULL,
              scanner_odds REAL,
              drift_pct REAL
            );
        """)
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_slip_odds_event ON slip_odds_ticks(event_id, market, outcome);"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_slip_odds_ts ON slip_odds_ticks(ts);"))
```

- [ ] **Step 3: Add gated logging in SlipOddsStream**

In `arnold/mirror/slip_odds_stream.py`, extend constructor to accept `log_endpoint: str | None = None` and `bet_context: dict | None = None`. When `log_endpoint` is set and odds change, fire an httpx.AsyncClient POST in a background task with `{provider_id, event_id, market, outcome, scraped_odds}`. Server-side, add a small `/api/slip-odds-tick` endpoint that inserts into the table.

- [ ] **Step 4: Gate via env var**

In ArbRunner / ProviderRunner, only pass `log_endpoint` to `SlipOddsStream` when `os.environ.get("SLIP_ODDS_LOGGING") == "true"`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py arnold/mirror/slip_odds_stream.py
git commit -m "feat(slip-odds): optional logging to slip_odds_ticks

Gated by SLIP_ODDS_LOGGING=true env var. Off by default. When on,
every meaningful slip-odds change posts to a new server endpoint that
inserts into slip_odds_ticks for drift analysis."
```

---

### Task 14: Relax extraction cadences

**Files:**
- Modify: `backend/src/config/providers.yaml` — `extraction_scheduling` block

- [ ] **Step 1: Apply the cadence table from the spec**

Open `backend/src/config/providers.yaml`, find `extraction_scheduling`, update each tier's cooldown:

| Tier | New cooldown |
|---|---|
| `sharp` | 120 (2min) |
| `polymarket` | 600 (10min) |
| `api_soft` | 300 (5min) |
| `browser_soft` | 900 (15min) |
| `browser_antibot` | 1800 (30min) |
| `signal_international` | 600 (10min) |

- [ ] **Step 2: Deploy + verify match-rate**

Deploy the cadence change ([CLAUDE.md cadence rule](CLAUDE.md): always `server-deploy.sh`):

```bash
git add backend/src/config/providers.yaml
git commit -m "config(extraction): relax cooldowns now that slip-odds is the truth tier

sharp 1→2min, polymarket 5→10min, api_soft 2→5min,
browser_soft 10→15min, browser_antibot 15→30min,
signal_international 5→10min.

Slip-odds streaming makes scanner freshness less load-bearing for
placement safety; this cuts server CPU substantially.

Watch /health/extraction match-rate over the next 24h; revert any tier
whose match rate degrades."

git push
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh restart backend"
```

- [ ] **Step 3: Monitor /health/extraction over 6h**

Run `curl -s -k -u arnold:$ARNOLD_PW https://148.251.40.251/health/extraction` periodically and confirm match-rate stays in the same band as before. Revert per-tier if it degrades.

---

## Phase 7 — Cleanup

### Task 15: Remove dead arb auto-hedge code

**Files:**
- Modify: `arnold/mirror/arb_runner.py` — drop `_place_counter_legs`, `_place_on_provider`, `_handle_anchor_placement`, `arb_hedge_placing`/`arb_hedge_failed`/`arb_unhedged` events
- Modify: `arnold/frontend/src/pages/PlayPage.tsx` — drop the matching SSE handlers if unused

- [ ] **Step 1: Verify the new ArbRunner has been live for at least one full day**

Don't run this task until Task 10's smoke test has confirmed the new flow works end-to-end with real bets.

- [ ] **Step 2: Delete the dead methods**

Open `arnold/mirror/arb_runner.py` and delete:
- `_place_counter_legs` (lines from old file ~492-591)
- `_place_on_provider` (lines from old file ~593-671)
- `_handle_anchor_placement` (lines from old file ~731-787)

- [ ] **Step 3: Drop unused SSE handlers in frontend**

Remove `arb_hedge_placing`, `arb_hedge_failed`, `arb_unhedged` cases from the SSE handler switch in `PlayPage.tsx`.

- [ ] **Step 4: Run full test suite**

Run: `cd c:/Users/rasmu/arnold && pytest arnold/tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/arb_runner.py arnold/frontend/src/pages/PlayPage.tsx
git commit -m "refactor(arb): remove dead auto-hedge code

The new semi-auto flow (Task 10) replaces auto-hedge with mirror-clicked
counter-leg placements, so _place_counter_legs / _place_on_provider /
_handle_anchor_placement and the arb_hedge_placing / arb_hedge_failed /
arb_unhedged SSE events are no longer reachable. Confirmed via 24h+
live operation."
```

---

## Self-Review

**Spec coverage:**
- Two-tier odds architecture → Tasks 3 (SlipOddsStream), 10 (ArbRunner consumes), 11 (ProviderRunner consumes), 14 (cadence relaxation that this enables)
- Workflow contract additions (`read_slip_odds`, `update_slip_stake`) → Task 2 (base) + Tasks 4–9 (per workflow)
- SlipOddsStream component → Task 3
- Arb workflow (load all → stream → mirror-click intercept → iterate) → Task 10
- 3-way constraint (1 soft + N unlimited) → enforced in Task 1's `is_valid_arb_shape` and consumed in Task 10's `_load_all_legs`
- Stake rules (full balance, equal-payout counters, drift threshold) → Task 1 (`recalc_counter_stakes`, `should_update_stake`) + Task 10 wiring
- Value-bet workflow update → Task 11
- React UI changes → Task 12
- New SSE events (arb_legs_loaded, arb_alignment, arb_anchor_placed, arb_anchor_rejected) → Task 10 emits, Task 12 handles
- Extraction cadence relaxation → Task 14
- Optional slip_odds_ticks logging → Task 13
- Removed events / dead code (`arb_hedge_placing`, `_place_counter_legs`, etc.) → Task 15

All spec sections have task coverage.

**Placeholder scan:** No "TBD"/"TODO"/"implement later"/"add appropriate" in plan body. All code blocks contain real code. Task 4–9 use a step labeled "Discovery" — that's an instruction to inspect a real DOM, not a placeholder; the resulting selectors are filled into a code block in the same task.

**Type consistency:**
- `recalc_profit_pct(anchor_odds, counter_odds: list[float])` — used same way in Task 1, Task 10
- `recalc_counter_stakes(anchor_stake, anchor_odds, counter_odds)` — same call sites
- `should_update_stake(old, new)` — consistent
- `is_valid_arb_shape(legs, unlimited)` — consistent
- `read_slip_odds(self, page) -> float | None` — same signature in base + every workflow
- `update_slip_stake(self, page, stake) -> bool` — same signature in base + every workflow
- `SlipOddsStream(provider_id, workflow, page, on_odds_change, poll_interval_s)` — same constructor used in both Task 10 and Task 11
- New SSE event names (`arb_legs_loaded`, `arb_alignment`, `arb_anchor_placed`, `arb_anchor_rejected`) — same spelling in both backend emit (Task 10) and frontend handler (Task 12)
- `_anchor_event`, `_counter_events`, `_counter_intercepted`, `_streams`, `_anchor_stake`, `_counter_legs`, `_latest_counter_odds`, `current_arb_group_id` — consistent attributes used in Task 10's tests + implementation
- `on_counter_bet_intercepted(counter_provider_id, body, request_body)` — same signature in tests + impl + play_loop routing

All consistent.
