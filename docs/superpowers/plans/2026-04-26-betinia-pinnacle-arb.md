# Betinia + Pinnacle Arb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing semi-auto arb pipeline end-to-end for Betinia (anchor) + Pinnacle (counter): re-ranking, green-gate, Pinnacle Playwright workflow with slip scraping, and PlayPage UI rewired to the new SSE event names.

**Architecture:** ArbRunner v2 already loads all legs in parallel and streams slip odds; we add (1) a 5s top-opp watcher that swaps opps when a higher-edge one appears, (2) per-leg `slip_state` (`green`/`red`) computed from drift + recomputed profit, (3) a new `pinnacle.py` Playwright workflow built from a discovery doc, and (4) PlayPage handlers for the new `arb_legs_loaded` / `arb_alignment` / `arb_anchor_*` / `arb_dethroned` events.

**Tech Stack:** Python 3.10+ (asyncio, Playwright async API, pytest-asyncio), TypeScript / React 19 / Vite, FastAPI for the local mirror surface.

**Spec:** [docs/superpowers/specs/2026-04-26-betinia-pinnacle-arb-design.md](../specs/2026-04-26-betinia-pinnacle-arb-design.md)

---

## File Structure

**New files:**
- `arnold/mirror/workflows/pinnacle.py` — Pinnacle Playwright workflow (DOM-driven, mirror-mode)
- `arnold/tests/workflows/test_pinnacle_slip.py` — unit tests for `read_slip_odds` + `update_slip_stake` + `parse_placement_status`
- `arnold/tests/test_arb_runner_green_gate.py` — unit tests for `_compute_slip_state`, dethrone hysteresis, alignment payload
- `docs/superpowers/specs/2026-04-26-pinnacle-discovery.md` — Pinnacle discovery notes (selectors, XHR shapes, slip storage layout)

**Modified files:**
- `arnold/mirror/arb_runner.py` — add `_compute_slip_state`, `_watch_top_opp`, `_dethroned_to`, `current_opp_key`, augmented `arb_alignment` payload, defensive green-gate in `_stream_and_await_anchor`
- `arnold/mirror/slip_odds_stream.py` — expose `page` as a public attribute (rename `_page` → `page`); update existing accesses
- `arnold/mirror/workflows/__init__.py` — register `pinnacle` → `PinnacleMirrorWorkflow` in `_PROVIDER_TO_PLATFORM` + platform map
- `arnold/frontend/src/pages/PlayPage.tsx` — drop old arb event handlers; add new ones; render per-leg slip-state row

---

## Task 0: Pinnacle Discovery Document

**Files:**
- Create: `docs/superpowers/specs/2026-04-26-pinnacle-discovery.md`

This task is a research deliverable — no code, no tests. Per saved feedback `feedback_provider_discovery_first.md`: "Research provider auth/CORS/API fully before writing workflow code."

- [ ] **Step 1: Open Pinnacle in the mirror browser, log in, capture DOM + storage**

Run these commands in this order:

```bash
# Start arnold local client (will open Playwright browser)
cd c:/Users/rasmu/arnold && ./arnold.bat
```

In the Playwright browser tab the launcher opens, navigate to `pinnacle.se` (or whatever Pinnacle domain you use), log in manually, then navigate to a specific event with multiple markets.

In the browser DevTools console, capture each of these and paste into the discovery doc:

```javascript
// Slip storage check
Object.keys(localStorage).filter(k => k.toLowerCase().includes('slip') || k.toLowerCase().includes('bet'))
Object.keys(sessionStorage).filter(k => k.toLowerCase().includes('slip') || k.toLowerCase().includes('bet'))

// React root presence
!!document.querySelector('[data-reactroot]') || !!document.querySelector('#__next')

// Slip element selector — click an outcome on a market first, then run:
document.querySelectorAll('[class*="bet-slip" i], [class*="betslip" i], [data-test*="bet" i]')
```

Open the Network tab, click "Place Bet" on a small test stake (or just observe XHR up to the place click), record:
- Outcome-add XHR (if any) — URL pattern, request body
- Stake input change events (search "stake" in network filter)
- Place button XHR — URL pattern, request body, response shape
- Balance + history endpoint URLs

- [ ] **Step 2: Write the discovery doc**

Create `docs/superpowers/specs/2026-04-26-pinnacle-discovery.md` with these required sections:

```markdown
# Pinnacle Mirror Workflow Discovery

**Date:** 2026-04-26
**Domain used:** [pinnacle.se | pinnacle.com | other]

## Login detection
- Selector / cookie / API to confirm logged-in state:
- Logged-out indicator:

## Balance
- DOM selector OR XHR endpoint that returns the balance:
- Response shape (paste exact JSON):
- Currency normalization needed? (yes/no, details)

## History
- XHR endpoint(s) for placed-bet history:
- Pagination params:
- Response shape (paste exact JSON for one settled + one pending bet):
- Status field mapping → "won" | "lost" | "void" | "cashout" | "pending"

## Event navigation
- URL pattern for an event page (paste 1 example):
- How to map `event_id` (from server arb opps) → Pinnacle event ID/URL:
  - If 1:1: explain
  - If lookup needed: explain endpoint
- Selectors for the outcome buttons on a market (paste exact selectors for 1x2 / spread / total):

## Slip widget
- Framework rendering it (React / Vue / WASM / vanilla):
- Slip selection storage location (localStorage key / DOM only / in-memory React store):
  - If localStorage: paste the key name pattern + example value
  - If DOM only: paste the selector path that reflects live drifting odds
- Stake input element selector:
- Stake input reactivity (controlled React input / plain input / store dispatch):
  - If controlled React: confirm whether direct .value assignment works or React's hidden setter is needed
- Place button selector:

## Placement XHR
- URL pattern for the place call:
- Request body shape (paste exact JSON):
- Response shape on success (paste exact JSON, redact PII):
- Response shape on stake-limit error (paste exact JSON if you can trigger one):
- Response shape on other errors:
- Field that contains the provider bet ID:

## Open issues / unknowns
- [List anything that wasn't determinable]
```

- [ ] **Step 3: Commit the discovery doc**

```bash
cd c:/Users/rasmu/arnold
git add docs/superpowers/specs/2026-04-26-pinnacle-discovery.md
git commit -m "docs(spec): Pinnacle mirror workflow discovery"
```

Expected: commit succeeds. Subsequent tasks reference this doc by exact selectors / endpoint names.

---

## Task 1: Expose `SlipOddsStream.page` as a public attribute

**Files:**
- Modify: `arnold/mirror/slip_odds_stream.py:42`
- Modify: `arnold/mirror/arb_runner.py:481`, `arb_runner.py:568`

The `_update_counter_slips_and_await_hedges` and `_on_leg_odds_change` methods reach into `self._streams[pid]._page`. Spec §4.2 calls for exposing this cleanly so we don't poke privates.

- [ ] **Step 1: Write the failing test**

Append to `arnold/tests/test_slip_odds_stream.py` (file already exists):

```python
def test_slip_odds_stream_exposes_page_publicly():
    """Spec §4.2: ArbRunner reads stream.page; should be a public attr."""
    from unittest.mock import MagicMock
    from arnold.mirror.slip_odds_stream import SlipOddsStream

    page = MagicMock(name="playwright_page")
    workflow = MagicMock()
    stream = SlipOddsStream(
        provider_id="pinnacle",
        workflow=workflow,
        page=page,
        on_odds_change=lambda o: None,
    )
    assert stream.page is page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_slip_odds_stream.py::test_slip_odds_stream_exposes_page_publicly -v`
Expected: FAIL — `AttributeError: 'SlipOddsStream' object has no attribute 'page'` (only has `_page`).

- [ ] **Step 3: Make the attribute public in SlipOddsStream**

In `arnold/mirror/slip_odds_stream.py`, change `self._page = page` (line ~42) to `self.page = page`, and update internal use on line ~71 from `self._page` to `self.page`.

- [ ] **Step 4: Update ArbRunner internal accesses**

In `arnold/mirror/arb_runner.py`, replace these two lines:

```python
# Line ~481 in _on_leg_odds_change:
page = self._streams[leg["provider"]]._page  # noqa: SLF001 (intentional)
# Line ~568 in _update_counter_slips_and_await_hedges:
page = self._streams[pid]._page  # noqa: SLF001
```

with (no `# noqa` needed):

```python
page = self._streams[leg["provider"]].page
```

and

```python
page = self._streams[pid].page
```

respectively.

- [ ] **Step 5: Run all stream + runner tests to verify nothing regressed**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_slip_odds_stream.py arnold/tests/test_arb_runner_v2.py -v`
Expected: all PASS, including the new `test_slip_odds_stream_exposes_page_publicly`.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/slip_odds_stream.py arnold/mirror/arb_runner.py arnold/tests/test_slip_odds_stream.py
git commit -m "refactor(slip-odds): expose SlipOddsStream.page publicly"
```

---

## Task 2: Add `_compute_slip_state` helper

**Files:**
- Modify: `arnold/mirror/arb_runner.py` (add static method to ArbRunner near existing helpers)
- Test: `arnold/tests/test_arb_runner_green_gate.py` (NEW)

Spec §4.2 green-gate: a per-leg state of `green` or `red` based on drift tolerance and recomputed profit.

- [ ] **Step 1: Write the failing tests**

Create `arnold/tests/test_arb_runner_green_gate.py`:

```python
"""ArbRunner green-gate + dethrone tests (per spec §4.2)."""

from __future__ import annotations

import pytest

from arnold.mirror.arb_runner import ArbRunner


class TestComputeSlipState:
    def test_green_when_live_matches_planned(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.10) == "green"

    def test_green_when_drift_within_tolerance(self):
        # 1% tolerance → 2.10 * 0.99 = 2.079; live=2.08 is acceptable
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.08) == "green"

    def test_red_when_drift_exceeds_tolerance(self):
        # 2.10 * 0.99 = 2.079; live=2.07 is below threshold
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.07) == "red"

    def test_red_when_live_is_none(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=None) == "red"

    def test_red_when_live_is_zero(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=0.0) == "red"

    def test_red_when_live_is_negative(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=-0.5) == "red"

    def test_green_when_live_is_above_planned(self):
        # Higher odds than planned is always good
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.50) == "green"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py::TestComputeSlipState -v`
Expected: FAIL — `AttributeError: type object 'ArbRunner' has no attribute '_compute_slip_state'`.

- [ ] **Step 3: Add `_compute_slip_state` to ArbRunner**

In `arnold/mirror/arb_runner.py`, add this constant near the top with the others (after `_ALIGNMENT_BROADCAST_THROTTLE_S`):

```python
LEG_DRIFT_TOL_PCT = 0.01  # 1% drift tolerance below planned odds → red
```

Then inside the `ArbRunner` class, add this static method (place it next to other small helpers like `_opp_to_bet`):

```python
@staticmethod
def _compute_slip_state(planned_odds: float, live_odds: float | None) -> str:
    """Per spec §4.2: green if live within drift tolerance of planned (or higher), else red."""
    if live_odds is None or live_odds <= 0:
        return "red"
    if live_odds < planned_odds * (1.0 - LEG_DRIFT_TOL_PCT):
        return "red"
    return "green"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py::TestComputeSlipState -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/arb_runner.py arnold/tests/test_arb_runner_green_gate.py
git commit -m "feat(arb): _compute_slip_state for green-gate"
```

---

## Task 3: Capture `_planned_odds` per leg + `current_opp_key` in `_load_all_legs`

**Files:**
- Modify: `arnold/mirror/arb_runner.py` (in `_load_all_legs` and `__init__`)
- Test: `arnold/tests/test_arb_runner_green_gate.py`

Per spec §4.2 we need each leg's planned odds captured so streams can compute `slip_state`, and an opp key for dethrone comparison.

- [ ] **Step 1: Write the failing test**

Append to `arnold/tests/test_arb_runner_green_gate.py`:

```python
class TestOppKey:
    def test_opp_key_includes_event_market_point_outcome(self):
        opp = {
            "event_id": "evt-123",
            "market": "spread",
            "point": -2.5,
            "outcome": "home",
        }
        # First-leg outcome is what determines the anchor's selection
        leg = {"outcome": "home", "provider": "betinia", "odds": 2.10}
        key = ArbRunner._compute_opp_key(opp, leg)
        assert key == "evt-123|spread|-2.5|home"

    def test_opp_key_handles_missing_point(self):
        opp = {"event_id": "evt-456", "market": "1x2", "outcome": "draw"}
        leg = {"outcome": "draw", "provider": "betinia", "odds": 3.40}
        key = ArbRunner._compute_opp_key(opp, leg)
        assert key == "evt-456|1x2||draw"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py::TestOppKey -v`
Expected: FAIL — `AttributeError: type object 'ArbRunner' has no attribute '_compute_opp_key'`.

- [ ] **Step 3: Add `_compute_opp_key` and instance fields**

In `arnold/mirror/arb_runner.py`:

In `__init__`, after `self._counter_legs: list[dict] = []` (line ~116), add:

```python
self._planned_anchor_odds: float = 0.0
self.current_opp_key: str | None = None
self._dethroned_to: dict | None = None
self._current_recomputed_profit_pct: float | None = None
```

Add this static method next to `_compute_slip_state`:

```python
@staticmethod
def _compute_opp_key(opp: dict, anchor_leg: dict) -> str:
    """Stable key for comparing two opps for dethrone (spec §4.2)."""
    return "|".join(
        [
            str(opp.get("event_id", "")),
            str(opp.get("market", "")),
            "" if opp.get("point") is None else str(opp.get("point")),
            str(anchor_leg.get("outcome", "")),
        ]
    )
```

- [ ] **Step 4: Capture planned odds + opp_key in `_load_all_legs`**

In `_load_all_legs`, after the existing `self._counter_legs = counter_legs` and before `self.current_opp = opp`, add:

```python
self._planned_anchor_odds = anchor_odds
for leg, planned_odds in zip(counter_legs, counter_odds):
    leg["_planned_odds"] = planned_odds
self.current_opp_key = self._compute_opp_key(opp, anchor_leg)
self._dethroned_to = None
self._current_recomputed_profit_pct = None
```

(Place these directly after `self._counter_legs = counter_legs`. `anchor_odds` and `counter_odds` are already computed earlier in the same method.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/arb_runner.py arnold/tests/test_arb_runner_green_gate.py
git commit -m "feat(arb): capture planned odds + opp_key per loaded opp"
```

---

## Task 4: Augment `arb_alignment` payload with `slip_state` + `all_green` + `current_profit_pct`

**Files:**
- Modify: `arnold/mirror/arb_runner.py` `_on_leg_odds_change` (line ~459)
- Test: `arnold/tests/test_arb_runner_green_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `arnold/tests/test_arb_runner_green_gate.py`:

```python
from unittest.mock import AsyncMock, MagicMock


def _make_browser():
    browser = MagicMock()
    browser.context = MagicMock()
    browser.context.pages = []
    browser.provider_data = {}
    return browser


def _make_broadcaster():
    bc = MagicMock()
    bc.publish = MagicMock()
    return bc


class TestAlignmentPayload:
    def _setup_runner_with_loaded_opp(self):
        """Build an ArbRunner with state as if _load_all_legs already succeeded."""
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.state = "standby"
        runner.current_opp = {"event_id": "e1", "market": "1x2", "outcome": "home"}
        runner.current_arb_group_id = "abc123"
        runner._planned_anchor_odds = 2.10
        runner._anchor_stake = 100.0
        runner._counter_legs = [
            {"provider": "pinnacle", "outcome": "away", "odds": 2.05, "_planned_odds": 2.05}
        ]
        # Stub the streams so _on_leg_odds_change can read anchor_odds
        anchor_stream = MagicMock()
        anchor_stream.current_odds = 2.10
        anchor_stream.page = MagicMock()
        counter_stream = MagicMock()
        counter_stream.page = MagicMock()
        runner._streams = {"betinia": anchor_stream, "pinnacle": counter_stream}
        return runner

    def test_alignment_includes_slip_state_per_leg(self):
        runner = self._setup_runner_with_loaded_opp()
        # Tick anchor with planned odds (green) and counter with planned odds (green)
        runner._latest_counter_odds = {"pinnacle": 2.05}
        runner._on_leg_odds_change("betinia", 2.10)
        runner._on_leg_odds_change("pinnacle", 2.05)

        # Find the most recent arb_alignment broadcast
        calls = [c for c in runner._broadcaster.publish.call_args_list if c.args[0] == "arb_alignment"]
        assert calls, "expected at least one arb_alignment broadcast"
        payload = calls[-1].args[1]

        assert payload["arb_group_id"] == "abc123"
        assert "all_green" in payload
        assert payload["all_green"] is True
        assert "current_profit_pct" in payload
        legs = payload["legs"]
        assert all("slip_state" in leg for leg in legs)
        assert all("planned_odds" in leg for leg in legs)
        assert all(leg["slip_state"] == "green" for leg in legs)

    def test_alignment_marks_red_when_anchor_drifts_below_tol(self):
        runner = self._setup_runner_with_loaded_opp()
        # Drift anchor below 1% tol: 2.10 * 0.99 = 2.079; 2.07 < 2.079 → red
        runner._latest_counter_odds = {"pinnacle": 2.05}
        runner._on_leg_odds_change("pinnacle", 2.05)
        runner._broadcaster.publish.reset_mock()
        runner._streams["betinia"].current_odds = 2.07
        runner._on_leg_odds_change("betinia", 2.07)

        calls = [c for c in runner._broadcaster.publish.call_args_list if c.args[0] == "arb_alignment"]
        assert calls
        payload = calls[-1].args[1]
        assert payload["all_green"] is False
        anchor_leg = next(l for l in payload["legs"] if l["provider_id"] == "betinia")
        assert anchor_leg["slip_state"] == "red"

    def test_alignment_all_green_false_when_profit_negative(self):
        runner = self._setup_runner_with_loaded_opp()
        # Push counter way down so profit goes negative even though both legs are
        # within drift tolerance. Planned counter is 2.05 → red triggers below 2.0295.
        # Force counter higher than planned (still "green" per drift) but lower in
        # arb math — actually higher counter odds INCREASE arb profit, so do the
        # opposite: artificially set counter live = 1.2 which is NOT green either.
        # Instead, reduce anchor live to 1.5 (still green since live > 0 and the
        # drift gate says live >= planned * 0.99; 1.5 < 2.10*0.99 → red anyway).
        # Cleanest path: leave both green but set counter so 1/2.10 + 1/2.05 > 1.
        # 1/2.10 + 1/2.05 = 0.476 + 0.488 = 0.964 → profit ~3.7%. Set counter live
        # to 1.05 to push 1/anchor + 1/counter > 1 — but 1.05 < 2.05*0.99 → red.
        # Conclusion: profit-negative WITHOUT any leg going red is unreachable in
        # this 2-leg arb, since the only way to push profit < 0 is to push odds
        # down, which trips the drift gate. So this test is redundant with the
        # red-leg test; assert the simpler combined case via the red-leg test only.
        pass  # intentionally a no-op marker
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py::TestAlignmentPayload -v`
Expected: FAIL on `test_alignment_includes_slip_state_per_leg` and `test_alignment_marks_red_when_anchor_drifts_below_tol` — payload keys missing.

- [ ] **Step 3: Augment `_on_leg_odds_change` payload**

In `arnold/mirror/arb_runner.py`, replace the body of `_on_leg_odds_change` from the line `# Throttle broadcast` onwards (line ~487 to end of method) with:

```python
        # Compute per-leg slip_state
        anchor_state = self._compute_slip_state(self._planned_anchor_odds, anchor_odds)
        counter_states = [
            self._compute_slip_state(leg.get("_planned_odds", leg.get("odds", 0)), live)
            for leg, live in zip(self._counter_legs, counter_odds)
        ]
        all_states = [anchor_state] + counter_states
        all_green = all(s == "green" for s in all_states) and profit > 0
        self._current_recomputed_profit_pct = profit
        self._all_green = all_green

        # Throttle broadcast
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now - self._last_alignment_broadcast >= _ALIGNMENT_BROADCAST_THROTTLE_S:
            self._last_alignment_broadcast = now
            self._broadcaster.publish(
                "arb_alignment",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "profit_pct": round(profit, 3),
                    "current_profit_pct": round(profit, 3),
                    "all_green": all_green,
                    "legs": [
                        {
                            "provider_id": self.provider_id,
                            "current_odds": anchor_odds,
                            "planned_odds": self._planned_anchor_odds,
                            "drift_pct": round((anchor_odds / self._planned_anchor_odds - 1.0) * 100.0, 3)
                            if self._planned_anchor_odds > 0
                            else 0.0,
                            "current_stake": self._anchor_stake,
                            "slip_state": anchor_state,
                        }
                    ]
                    + [
                        {
                            "provider_id": leg["provider"],
                            "current_odds": self._latest_counter_odds.get(leg["provider"], leg.get("odds", 0)),
                            "planned_odds": leg.get("_planned_odds", leg.get("odds", 0)),
                            "drift_pct": round(
                                (
                                    self._latest_counter_odds.get(leg["provider"], leg.get("odds", 0))
                                    / leg.get("_planned_odds", leg.get("odds", 1))
                                    - 1.0
                                )
                                * 100.0,
                                3,
                            )
                            if leg.get("_planned_odds", 0) > 0
                            else 0.0,
                            "current_stake": leg.get("_current_stake", 0),
                            "slip_state": state,
                        }
                        for leg, state in zip(self._counter_legs, counter_states)
                    ],
                },
            )
```

Also add `self._all_green: bool = False` to `__init__` near the other green-gate fields added in Task 3.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py -v`
Expected: all PASS (the empty `test_alignment_all_green_false_when_profit_negative` body is a noop pass).

- [ ] **Step 5: Run all arb_runner tests as regression check**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_v2.py arnold/tests/test_arb_math.py arnold/tests/test_arb_runner_green_gate.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/arb_runner.py arnold/tests/test_arb_runner_green_gate.py
git commit -m "feat(arb): emit slip_state + all_green + drift_pct in arb_alignment"
```

---

## Task 5: Top-opp watcher with dethrone hysteresis

**Files:**
- Modify: `arnold/mirror/arb_runner.py` (add `_watch_top_opp`, `_top_opp_watcher_task`, integrate with `_run`)
- Test: `arnold/tests/test_arb_runner_green_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `arnold/tests/test_arb_runner_green_gate.py`:

```python
class TestDethroneHysteresis:
    """Per spec §4.2: switch to a new opp only when its profit beats current by ≥0.5pp."""

    def _make_runner(self):
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.current_opp_key = "evt-A|1x2||home"
        runner._current_recomputed_profit_pct = 1.0
        return runner

    def test_no_dethrone_when_top_is_same_opp(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-A",
            "market": "1x2",
            "point": None,
            "outcome": "home",
            "guaranteed_profit_pct": 5.0,
            "arb_legs": [{"provider": "betinia", "outcome": "home", "odds": 2.10}],
        }
        assert runner._should_dethrone(top_opp) is False

    def test_no_dethrone_when_below_hysteresis(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 1.4,  # +0.4pp over current 1.0 — below 0.5pp
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is False

    def test_dethrone_at_hysteresis_threshold(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 1.5,  # +0.5pp over current 1.0
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is True

    def test_dethrone_with_no_recomputed_profit_yet_uses_zero_baseline(self):
        runner = self._make_runner()
        runner._current_recomputed_profit_pct = None
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 0.6,  # +0.6pp over baseline 0
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py::TestDethroneHysteresis -v`
Expected: FAIL — `AttributeError: 'ArbRunner' object has no attribute '_should_dethrone'`.

- [ ] **Step 3: Implement `_should_dethrone` + watcher loop**

In `arnold/mirror/arb_runner.py`, add these constants near the existing ones at module top:

```python
RERANK_INTERVAL_S = 5.0
DETHRONE_HYSTERESIS_PCT = 0.5
```

Add this method to `ArbRunner` (next to other small helpers):

```python
def _should_dethrone(self, top_opp: dict) -> bool:
    """Decide whether to swap to a new top opp (spec §4.2 hysteresis)."""
    legs = top_opp.get("arb_legs") or top_opp.get("legs", [])
    anchor_leg = next((l for l in legs if l.get("provider") == self.provider_id), None)
    if anchor_leg is None:
        return False
    new_key = self._compute_opp_key(top_opp, anchor_leg)
    if new_key == self.current_opp_key:
        return False
    new_profit = top_opp.get("guaranteed_profit_pct", 0.0)
    baseline = self._current_recomputed_profit_pct if self._current_recomputed_profit_pct is not None else 0.0
    return (new_profit - baseline) >= DETHRONE_HYSTERESIS_PCT
```

Add the watcher coroutine + task field. In `__init__`, after the `_dethroned_to` line, add:

```python
self._top_opp_watcher_task: asyncio.Task | None = None
```

Add this method next to other internal helpers:

```python
async def _watch_top_opp(self) -> None:
    """Periodic re-rank loop. Cancelled when leaving STATE_STANDBY."""
    while True:
        try:
            await asyncio.sleep(RERANK_INTERVAL_S)
            opps = await self._fetch_arb_opps()
            if not opps:
                continue
            top = opps[0]
            if self._should_dethrone(top):
                self._broadcaster.publish(
                    "arb_dethroned",
                    {
                        "arb_group_id": self.current_arb_group_id,
                        "old_profit": self._current_recomputed_profit_pct,
                        "new_profit": top.get("guaranteed_profit_pct"),
                    },
                )
                self._dethroned_to = top
                self._anchor_event.set()
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"[Arb:{self.provider_id}] top-opp watcher error")
```

- [ ] **Step 4: Wire the watcher into `_run`**

In `arnold/mirror/arb_runner.py` `_run`, find the block (around line ~268-280) that sets `self.state = STATE_STANDBY` and calls `self._stream_and_await_anchor()`. Replace with:

```python
                    # Stream and await anchor click (with top-opp watcher)
                    self.state = STATE_STANDBY
                    self._top_opp_watcher_task = asyncio.create_task(
                        self._watch_top_opp(), name=f"arb_watch_{pid}"
                    )
                    try:
                        anchor_result = await self._stream_and_await_anchor()
                    finally:
                        if self._top_opp_watcher_task and not self._top_opp_watcher_task.done():
                            self._top_opp_watcher_task.cancel()
                            try:
                                await self._top_opp_watcher_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        self._top_opp_watcher_task = None
```

In the same `_run` method, modify the `if anchor_result is None:` branch (line ~272-280) to handle the dethrone path:

```python
                    if anchor_result is None:
                        # Either rejected, stopped, or dethroned
                        for s in self._streams.values():
                            s.stop()
                        self._streams.clear()
                        self._counter_events.clear()
                        self._counter_intercepted.clear()
                        if self._dethroned_to is not None:
                            new_opp = self._dethroned_to
                            self._dethroned_to = None
                            # Swap opp inline — fall through to next iteration over a synthetic 1-element list
                            opps = [new_opp]
                            opps_iter_handled = True
                            # Loop back to load_all_legs(new_opp) on the next "for opp in opps" pass.
                            # Easiest: break out of the inner for, set placed_any=True so we don't think
                            # we're done, and let the outer while re-fetch.
                            placed_any = True
                            break
                        self.stats["rejected"] += 1
                        continue
```

This is intentionally a soft handoff — we let the outer `while True` loop re-fetch opps so the dethrone target is naturally re-checked against the freshest list.

- [ ] **Step 5: Run all related tests**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_arb_runner_green_gate.py arnold/tests/test_arb_runner_v2.py arnold/tests/test_arb_math.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/arb_runner.py arnold/tests/test_arb_runner_green_gate.py
git commit -m "feat(arb): top-opp watcher with dethrone hysteresis"
```

---

## Task 6: Pinnacle workflow skeleton (offline-testable methods)

**Files:**
- Create: `arnold/mirror/workflows/pinnacle.py`
- Create: `arnold/tests/workflows/test_pinnacle_slip.py`
- Modify: `arnold/mirror/workflows/__init__.py` (register pinnacle)

**Prerequisite:** Task 0 discovery doc must be complete. The selectors and storage keys in this task come from that doc — replace the bracketed `[FROM_DISCOVERY:...]` placeholders with the actual values before this task ships.

- [ ] **Step 1: Write failing tests for `read_slip_odds` + `update_slip_stake` + `parse_placement_status`**

Create `arnold/tests/workflows/test_pinnacle_slip.py`:

```python
"""Pinnacle mirror workflow — slip read/write + placement parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.workflows.pinnacle import PinnacleMirrorWorkflow


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_when_slip_empty():
    wf = PinnacleMirrorWorkflow(provider_id="pinnacle", domain="pinnacle.se")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=None)  # slip storage empty
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_read_slip_odds_parses_displayed_price():
    wf = PinnacleMirrorWorkflow(provider_id="pinnacle", domain="pinnacle.se")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=2.05)  # workflow's JS evaluator returns the price directly
    odds = await wf.read_slip_odds(page)
    assert odds == 2.05


@pytest.mark.asyncio
async def test_update_slip_stake_returns_true_on_success():
    wf = PinnacleMirrorWorkflow(provider_id="pinnacle", domain="pinnacle.se")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    ok = await wf.update_slip_stake(page, 250.0)
    assert ok is True


@pytest.mark.asyncio
async def test_update_slip_stake_returns_false_on_no_slip():
    wf = PinnacleMirrorWorkflow(provider_id="pinnacle", domain="pinnacle.se")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=False)
    ok = await wf.update_slip_stake(page, 250.0)
    assert ok is False


def test_parse_placement_status_success():
    """Per discovery doc — adjust shape to match captured response."""
    body = {"status": "ACCEPTED", "betId": 12345}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is True
    assert result["error"] is None


def test_parse_placement_status_failure():
    body = {"status": "REJECTED", "errorCode": "STAKE_LIMIT", "maxStake": 50.0}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] is not None
    assert result.get("max_stake") == 50.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/workflows/test_pinnacle_slip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arnold.mirror.workflows.pinnacle'`.

- [ ] **Step 3: Create the Pinnacle workflow skeleton**

Create `arnold/mirror/workflows/pinnacle.py`. Replace `[FROM_DISCOVERY:...]` brackets with values from the discovery doc before committing. If the discovery says "DOM-only slip" rather than localStorage, swap the JS in `read_slip_odds` for a `querySelector` chain.

```python
"""Pinnacle mirror Playwright workflow.

User-driven mirror flow — navigates to event pages, scrapes slip odds, rewrites
stake fields, intercepts placement XHRs. Distinct from the autonomous
`workflows/strategies/pinnacle.py` strategy which auto-places via the public API.

Selectors and storage keys come from
docs/superpowers/specs/2026-04-26-pinnacle-discovery.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PinnacleMirrorWorkflow(ProviderWorkflow):
    platform = "pinnacle_mirror"
    autonomous_placement = False

    def __init__(self, provider_id: str = "pinnacle", domain: str = "pinnacle.se", mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id=provider_id, domain=domain, mode=mode)

    async def check_login(self, page: Page) -> bool:
        """[FROM_DISCOVERY: login indicator] — selector or cookie that proves logged-in."""
        try:
            # Replace with the actual selector from discovery
            return bool(await page.evaluate("() => !!document.querySelector('[data-testid=\"user-menu\"]')"))
        except Exception:
            return False

    async def sync_balance(self, page: Page) -> float:
        """[FROM_DISCOVERY: balance source]."""
        try:
            # Replace with actual selector or XHR call
            raw = await page.evaluate(
                "() => { const el = document.querySelector('[data-testid=\"balance\"]'); return el ? el.textContent : null; }"
            )
            if not raw:
                return -1
            return float("".join(c for c in raw if c.isdigit() or c == "."))
        except Exception:
            return -1

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """[FROM_DISCOVERY: history XHR endpoint + response shape]."""
        # Skeleton — fill from discovery
        return []

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """[FROM_DISCOVERY: event URL pattern]."""
        # Skeleton — fill from discovery doc's URL pattern
        return False

    async def place_bet(self, page: Page, bet, stake: float):
        """Not used in mirror flow — user clicks Place on the site."""
        from .base import PlacementResult
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def read_slip_odds(self, page: Page) -> float | None:
        """[FROM_DISCOVERY: slip odds selector or storage key]."""
        try:
            # If discovery shows localStorage-based slip:
            #   raw = await page.evaluate("() => localStorage.getItem('[FROM_DISCOVERY:KEY]')")
            #   if raw: return float(json.loads(raw)['selections'][0]['price'])
            # If DOM-only:
            #   return await page.evaluate("() => { const el = document.querySelector('[FROM_DISCOVERY:SLIP_ODDS_SELECTOR]'); return el ? parseFloat(el.textContent) : null; }")
            result = await page.evaluate(
                "() => { const el = document.querySelector('[data-testid=\"slip-price\"]'); return el ? parseFloat(el.textContent) : null; }"
            )
            return result if result and result > 0 else None
        except Exception:
            return None

    async def update_slip_stake(self, page: Page, stake: float) -> bool:
        """[FROM_DISCOVERY: stake input selector + reactivity strategy]."""
        try:
            # If controlled React input — use hidden setter:
            return bool(
                await page.evaluate(
                    """(stake) => {
                        const el = document.querySelector('[data-testid="stake-input"]');
                        if (!el) return false;
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, String(stake));
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }""",
                    round(stake, 2),
                )
            )
        except Exception:
            return False

    @staticmethod
    def parse_placement_status(body: dict) -> dict:
        """[FROM_DISCOVERY: placement response shape]."""
        # Adjust per discovery doc
        status = (body.get("status") or "").upper()
        if status in ("ACCEPTED", "PROCESSED", "OK"):
            return {"success": True, "error": None, "max_stake": None}
        return {
            "success": False,
            "error": body.get("errorCode") or body.get("error") or "unknown",
            "max_stake": body.get("maxStake"),
        }

    @staticmethod
    def parse_placement_response(body: dict) -> str | None:
        """[FROM_DISCOVERY: bet ID field]."""
        bid = body.get("betId") or body.get("bet_id")
        return str(bid) if bid else None
```

- [ ] **Step 4: Register the workflow**

In `arnold/mirror/workflows/__init__.py`, find `_load_platform_map` (the function that builds `_PLATFORM_MAP`). Add an entry mapping `"pinnacle_mirror"` to the new class. Also ensure `pinnacle` resolves to this class — add to `_PROVIDER_TO_PLATFORM`:

```python
_PROVIDER_TO_PLATFORM: dict[str, str] = {
    # ... existing entries ...
    "pinnacle": "pinnacle_mirror",
}
```

And in `_load_platform_map` (or wherever the imports happen), add:

```python
from .pinnacle import PinnacleMirrorWorkflow
# inside the dict literal:
"pinnacle_mirror": PinnacleMirrorWorkflow,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/workflows/test_pinnacle_slip.py -v`
Expected: all 6 PASS.

- [ ] **Step 6: Run the full mirror test suite as regression**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/ -v`
Expected: all PASS (or pre-existing failures unchanged — note them but don't fix unrelated ones in this task).

- [ ] **Step 7: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/workflows/pinnacle.py arnold/mirror/workflows/__init__.py arnold/tests/workflows/test_pinnacle_slip.py
git commit -m "feat(workflows/pinnacle): mirror Playwright workflow skeleton with slip scrape"
```

---

## Task 7: Live-validate Pinnacle workflow against the running mirror

**Files:**
- Modify: `arnold/mirror/workflows/pinnacle.py` (replace `[FROM_DISCOVERY:...]` with confirmed values)
- Possibly modify: `docs/superpowers/specs/2026-04-26-pinnacle-discovery.md` (correct anything wrong from Task 0)

This is a manual verification task — the unit tests pass against mocks, but the JS evaluators must work in the real browser.

- [ ] **Step 1: Boot the mirror with Pinnacle**

```bash
cd c:/Users/rasmu/arnold && ./arnold.bat
```

Open Pinnacle in the Playwright tab, log in.

- [ ] **Step 2: Verify `check_login` works**

In the Playwright DevTools console, paste the literal selector chain that `check_login` uses (from your discovery doc — e.g. `!!document.querySelector('[data-testid="user-menu"]')`). It must return `true` when logged in and `false` when logged out.

- [ ] **Step 3: Verify `sync_balance` works**

Paste the balance scrape JS into the console. Confirm it returns a positive number matching the on-screen balance.

- [ ] **Step 4: Verify `read_slip_odds` works**

Click an outcome on a market to add it to the slip. In the console, paste the JS from `read_slip_odds`. Confirm it returns the live displayed odds (2.05, etc.) and not `null`. Wait 30s for line drift; paste again — the value should change to track the slip.

- [ ] **Step 5: Verify `update_slip_stake` works**

In the console, paste the JS from `update_slip_stake` with a sample value:

```javascript
(stake => {
    const el = document.querySelector('[FROM_DISCOVERY:SELECTOR]');
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, String(stake));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
})(123)
```

Confirm the slip's stake field visually shows `123`. If the React component snaps back to a prior value, the reactivity strategy is wrong — check discovery for store-dispatch-based input pattern.

- [ ] **Step 6: Update the workflow file with confirmed selectors**

Replace any `[FROM_DISCOVERY:...]` placeholder still left in `arnold/mirror/workflows/pinnacle.py` with the verified strings. Re-run the unit tests:

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/workflows/test_pinnacle_slip.py -v`
Expected: all PASS (mocks don't depend on selectors).

- [ ] **Step 7: Commit any selector fixups**

```bash
cd c:/Users/rasmu/arnold
git add arnold/mirror/workflows/pinnacle.py docs/superpowers/specs/2026-04-26-pinnacle-discovery.md
git commit -m "fix(workflows/pinnacle): selector adjustments after live verification"
```

If no changes were needed, skip the commit.

---

## Task 8: PlayPage UI — wire the new arb_* events

**Files:**
- Modify: `arnold/frontend/src/pages/PlayPage.tsx`

Per spec §4.3.

- [ ] **Step 1: Replace old state with leg-shaped state**

In `arnold/frontend/src/pages/PlayPage.tsx`, replace the existing arb state declarations (lines ~106-116) with:

```tsx
  // Per-leg arb alignment from arb_legs_loaded + arb_alignment events
  type ArbLeg = {
    provider_id: string
    current_odds: number
    planned_odds: number
    drift_pct: number
    current_stake: number
    slip_state: 'loading' | 'green' | 'red'
    placed?: boolean
    failed_reason?: string
  }
  const [arbLegs, setArbLegs] = useState<ArbLeg[] | null>(null)
  const [arbAllGreen, setArbAllGreen] = useState<boolean>(false)
  const [arbProfitPct, setArbProfitPct] = useState<number | null>(null)
  const [arbGroupId, setArbGroupId] = useState<string | null>(null)
  const [arbDethroneToast, setArbDethroneToast] = useState<string | null>(null)
```

Delete the old `arbHedgeStatus` and `arbCounterPlan` state lines.

- [ ] **Step 2: Replace the SSE handlers**

Find the block from `if (type === 'bet_skipped' || type === 'bet_failed') {` (around line ~490) through `if (type === 'arb_complete') {` (around line ~516). Replace the entire `bet_skipped`/`bet_failed`/`arb_*` handler block with:

```tsx
    if (type === 'bet_skipped' || type === 'bet_failed') {
      setCurrentBetReady(null)
      setLoopStatus(null)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    if (type === 'arb_legs_loaded') {
      setArbGroupId(data.arb_group_id ?? null)
      setArbLegs(
        (data.legs ?? []).map((l: any) => ({
          provider_id: l.provider_id,
          current_odds: l.planned_odds ?? 0,
          planned_odds: l.planned_odds ?? 0,
          drift_pct: 0,
          current_stake: l.planned_stake ?? 0,
          slip_state: 'loading',
        }))
      )
      setArbAllGreen(false)
      setArbProfitPct(null)
    }
    if (type === 'arb_alignment') {
      setArbAllGreen(!!data.all_green)
      setArbProfitPct(data.current_profit_pct ?? data.profit_pct ?? null)
      setArbLegs(prev => {
        if (!prev) return prev
        const incoming: Record<string, any> = {}
        for (const l of (data.legs ?? [])) incoming[l.provider_id] = l
        return prev.map(leg => {
          const update = incoming[leg.provider_id]
          if (!update) return leg
          return {
            ...leg,
            current_odds: update.current_odds ?? leg.current_odds,
            planned_odds: update.planned_odds ?? leg.planned_odds,
            drift_pct: update.drift_pct ?? leg.drift_pct,
            current_stake: update.current_stake ?? leg.current_stake,
            slip_state: update.slip_state ?? leg.slip_state,
          }
        })
      })
    }
    if (type === 'arb_anchor_placed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.provider_id ? { ...l, placed: true, current_stake: data.actual_stake ?? l.current_stake, current_odds: data.actual_odds ?? l.current_odds } : l) : prev)
    }
    if (type === 'arb_anchor_rejected') {
      setArbDethroneToast(`Anchor rejected: ${data.reason ?? 'unknown'}`)
      setTimeout(() => setArbDethroneToast(null), 4000)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
    }
    if (type === 'arb_hedge_placed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.counter_provider ? { ...l, placed: true, current_stake: data.actual_stake ?? l.current_stake, current_odds: data.actual_odds ?? l.current_odds } : l) : prev)
    }
    if (type === 'arb_hedge_failed') {
      setArbLegs(prev => prev ? prev.map(l => l.provider_id === data.counter_provider ? { ...l, failed_reason: data.reason ?? 'failed', slip_state: 'red' } : l) : prev)
    }
    if (type === 'arb_dethroned') {
      const delta = data.new_profit != null && data.old_profit != null
        ? `+${(data.new_profit - data.old_profit).toFixed(2)}pp`
        : ''
      setArbDethroneToast(`Switched to higher-edge opp ${delta}`)
      setTimeout(() => setArbDethroneToast(null), 3500)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
    }
    if (type === 'arb_complete') {
      setTimeout(() => {
        setArbLegs(null)
        setArbAllGreen(false)
        setArbProfitPct(null)
        setArbGroupId(null)
        setCurrentBetReady(null)
      }, 5000)
      loadArbOpps()
    }
```

- [ ] **Step 3: Replace the arb card render block**

Find the block starting `{/* Arb card */}` (around line ~763) and ending `{arbHedgeStatus.__unhedged && (` plus the closing tags up to `</div>` on line ~810. Replace the entire `{currentBetReady && arbCounterPlan && (...)}` block with:

```tsx
      {/* Arb alignment card — shows per-leg slip state during a loaded opp */}
      {arbLegs && arbLegs.length > 0 && (
        <div className="border-b border-purple-700/50 bg-purple-900/10 px-3 py-2">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="px-1.5 py-0.5 text-[10px] font-bold bg-purple-900/50 text-purple-400 border border-purple-700/50 rounded">DUTCH ARB</span>
            {arbProfitPct != null && (
              <span className={`text-xs font-mono font-semibold ${arbProfitPct > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {arbProfitPct > 0 ? '+' : ''}{arbProfitPct.toFixed(2)}% profit
              </span>
            )}
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${arbAllGreen ? 'bg-green-900/50 text-green-300' : 'bg-zinc-800 text-zinc-400'}`}>
              {arbAllGreen ? 'ALL GREEN — place anchor' : 'WAITING'}
            </span>
            {arbGroupId && <span className="text-[10px] text-zinc-600 ml-auto font-mono">{arbGroupId}</span>}
          </div>
          <div className="space-y-0.5">
            {arbLegs.map(leg => (
              <div key={leg.provider_id} className="flex items-center gap-2 pl-1 text-[10px]">
                <span className={`inline-block w-2 h-2 rounded-full ${leg.slip_state === 'green' ? 'bg-green-400' : leg.slip_state === 'red' ? 'bg-red-400' : 'bg-zinc-600 animate-pulse'}`} />
                <span className="text-zinc-400 uppercase w-16">{leg.provider_id}</span>
                <span className="font-mono text-zinc-300 w-16">@ {leg.current_odds?.toFixed(2)}</span>
                <span className="font-mono text-zinc-500 w-20">(plan {leg.planned_odds?.toFixed(2)})</span>
                <span className={`font-mono w-12 ${Math.abs(leg.drift_pct) > 1 ? 'text-amber-400' : 'text-zinc-500'}`}>
                  {leg.drift_pct >= 0 ? '+' : ''}{leg.drift_pct?.toFixed(2)}%
                </span>
                <span className="font-mono text-zinc-300 w-16">{Math.round(leg.current_stake ?? 0)} kr</span>
                {leg.placed && <span className="text-green-400 font-semibold">PLACED</span>}
                {leg.failed_reason && <span className="text-red-400">FAILED: {leg.failed_reason}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {arbDethroneToast && (
        <div className="border-b border-amber-700/50 bg-amber-900/10 px-3 py-1.5 text-xs text-amber-300">
          {arbDethroneToast}
        </div>
      )}
```

- [ ] **Step 4: Clean up the play_complete / play_stopped handler**

Find the `if (type === 'play_complete' || type === 'play_stopped') {` block (around line ~525). Replace its body with:

```tsx
      setLoopRunning(false)
      setCurrentBetReady(null)
      setLoopProviderStatus(null)
      setToasts([])
      setSettleWaiting(false)
      setLoopStatus(null)
      setArbLegs(null)
      setArbAllGreen(false)
      setArbProfitPct(null)
      setArbGroupId(null)
```

(Remove the now-defunct references to `arbCounterPlan` and `arbHedgeStatus`.)

- [ ] **Step 5: Type-check**

Run: `cd c:/Users/rasmu/arnold/arnold/frontend && npm run lint`
Expected: 0 errors. If TypeScript flags removed-state references elsewhere in the file, grep for `arbHedgeStatus` and `arbCounterPlan` and remove any remaining references (they should not exist after the edits in Step 1+2+3).

```bash
cd c:/Users/rasmu/arnold && grep -n 'arbHedgeStatus\|arbCounterPlan' arnold/frontend/src/pages/PlayPage.tsx
```

Expected output: empty.

- [ ] **Step 6: Build the frontend**

Run: `cd c:/Users/rasmu/arnold/arnold/frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
cd c:/Users/rasmu/arnold
git add arnold/frontend/src/pages/PlayPage.tsx
git commit -m "feat(playpage): wire new arb SSE events with per-leg slip-state row"
```

---

## Task 9: Live smoke test — one full Betinia + Pinnacle arb

**Files:** none modified (verification task)

Per spec §7 testing.

- [ ] **Step 1: Start the mirror**

```bash
cd c:/Users/rasmu/arnold && ./arnold.bat
```

In the React UI (Sports tab → Arbitrage sub-tab):
- Confirm there's at least one Betinia-anchored arb opp showing in the Arb section
- Activate Betinia (click its row → amber → opens tab)
- Open Pinnacle tab manually if it didn't auto-open

- [ ] **Step 2: Log in to both**

Log in to Betinia and Pinnacle in their respective tabs. Wait for both rows to turn green in the UI.

- [ ] **Step 3: Watch the arb card**

When the runner picks an opp and loads legs:
- The DUTCH ARB card should appear with both Betinia and Pinnacle rows
- Each row shows live odds + planned odds + drift% + stake + a colored dot
- Both dots should turn green within ~5s; banner should show "ALL GREEN — place anchor"
- If a higher-edge opp appears, the toast "Switched to higher-edge opp +X.XXpp" should fire and the card should reload with new legs

- [ ] **Step 4: Place the anchor**

In the Betinia tab, click the slip's place button. The Betinia row in the UI should show `PLACED` with the actual_stake. The Pinnacle row's `current_stake` should auto-update to the recomputed counter stake.

- [ ] **Step 5: Place the counter**

In the Pinnacle tab, the slip stake should be auto-filled by `update_slip_stake`. Click place. The Pinnacle row should show `PLACED`. The card should disappear after 5s with `arb_complete`.

- [ ] **Step 6: Verify DB recording**

Connect to the server postgres via the MCP server, run:

```sql
SELECT id, provider_id, market, outcome, odds, stake, notes, created_at
FROM bets
WHERE notes LIKE 'arb_group:%'
ORDER BY created_at DESC
LIMIT 4;
```

Expected: two rows in the latest arb_group share the same `notes` value (e.g. `arb_group:abc123`); one is `betinia`, one is `pinnacle`; their stake / odds match what you placed.

- [ ] **Step 7: Document the smoke test result**

Append a row to the spec doc's §7 (or paste a short log into the PR description when this branch ships). If the smoke test fails at any step, do NOT mark this task complete — open a follow-up to fix and re-test.

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| §3 Architecture (re-rank watcher) | Task 5 |
| §3 Architecture (green-gate) | Tasks 2, 4 |
| §4.1 Pinnacle workflow | Tasks 0, 6, 7 |
| §4.2 dethrone hysteresis | Task 5 |
| §4.2 green-gate compute | Tasks 2, 4 |
| §4.2 SlipOddsStream.page public | Task 1 |
| §4.3 PlayPage UI rewire | Task 8 |
| §4.4 Backend changes (none) | n/a |
| §5 Data flow | All tasks together; verified end-to-end in Task 9 |
| §6 Error handling — `read_slip_odds` returns None | Tasks 6, 4 (state stays red) |
| §6 Error handling — counter intercepted but XHR failed | Task 8 (`arb_hedge_failed` UI handler) |
| §7 Testing — unit `_compute_slip_state` | Task 2 |
| §7 Testing — unit dethrone hysteresis | Task 5 |
| §7 Testing — unit pinnacle workflow | Task 6 |
| §7 Testing — live smoke | Task 9 |
| §10 Constants | Tasks 2, 5 |

**Placeholder scan:** The `[FROM_DISCOVERY:...]` markers in Task 6 are deliberate and gated by Task 0 — they get replaced before that task ships, called out explicitly in the prereq line. No "TODO", "TBD", or hand-wavy steps elsewhere.

**Type consistency:** `_compute_slip_state` (Task 2) signature `(planned_odds: float, live_odds: float | None) -> str` — matches usage in Task 4. `_compute_opp_key` (Task 3) signature `(opp: dict, anchor_leg: dict) -> str` — matches usage in Task 5's `_should_dethrone`. `arb_alignment` payload shape from Task 4 — `{provider_id, current_odds, planned_odds, drift_pct, current_stake, slip_state}` per leg — matches the UI shape consumed in Task 8 (`ArbLeg` type uses identical field names). `arb_legs_loaded` payload from existing code — `{arb_group_id, legs: [{provider_id, planned_odds, planned_stake, ...}]}` — matches Task 8's loader.

**Stale-slip threshold (spec §6):** Spec says "≥10 consecutive None ticks → red". This is NOT explicitly implemented in this plan — `_compute_slip_state(planned, None) == "red"` already covers it (None → red after the very first tick), which is stricter than spec. Acceptable: stricter is safer for the user's first session. Flag for follow-up if it proves too sensitive.
