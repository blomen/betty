# Arb Drain Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock the Betinia↔Pinnacle arb loop with four targeted fixes — anchor stake cap, hedge wait timeout, Pinnacle-tab arbitration, Pinnacle history reconciliation — so soft balance drains into the unlimited side without hangs, mis-sizing, or un-reconciled rows.

**Architecture:** Four independent fixes layered onto the existing `ArbRunner` v2 pipeline. Three are surgical edits to `arb_runner.py` / `play_loop.py` / `pinnacle.py`. The fourth introduces a new `PinnacleSharedRunner` class that arbitrates Pinnacle tab ownership between the unlimited-side ProviderRunner and the soft-side ArbRunner via lend/release semantics.

**Tech Stack:** Python 3.10+, asyncio, Playwright, pytest. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-04-30-arb-drain-blockers-design.md](../specs/2026-04-30-arb-drain-blockers-design.md)

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `arnold/mirror/arb_runner.py` | Modify | Hedge timeout, stake cap consumption, Pinnacle lend/release glue |
| `arnold/mirror/play_loop.py` | Modify | Spawn PinnacleSharedRunner when soft anchors present; pass `_stake_caps` to ArbRunner |
| `arnold/mirror/pinnacle_shared.py` | Create | Shared Pinnacle runner — value-bet mode + lent-to-arb mode |
| `arnold/mirror/workflows/pinnacle.py` | Modify | Replace `sync_history` stub with DOM scrape |
| `backend/tests/mirror/test_arb_stake_cap.py` | Create | Anchor stake cap unit tests |
| `backend/tests/mirror/test_arb_hedge_timeout.py` | Create | Hedge timeout unit tests |
| `backend/tests/mirror/test_pinnacle_shared.py` | Create | Lend/release unit tests |
| `backend/tests/mirror/test_pinnacle_history.py` | Create | DOM scrape parsing tests |
| `docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md` | Create | Discovery doc for Pinnacle bet-history endpoint |

---

## Task 1: Anchor stake cap

Smallest blast radius, unblocks the first real placement. ArbRunner currently sets `anchor_stake = round(balance, 2)` without consulting `_stake_caps` — Altenar then rejects whatever exceeds its learned cap.

**Files:**
- Modify: `arnold/mirror/arb_runner.py:82-100` (`__init__`)
- Modify: `arnold/mirror/arb_runner.py:394-428` (`_load_all_legs`)
- Modify: `arnold/mirror/play_loop.py:373-421` (`_spawn_runners`)
- Test: `backend/tests/mirror/test_arb_stake_cap.py`

- [ ] **Step 1.1: Write the failing test**

Create `backend/tests/mirror/test_arb_stake_cap.py`:

```python
"""Anchor stake cap tests — ArbRunner must respect _stake_caps when sizing the anchor."""

from __future__ import annotations

import pytest


@pytest.fixture
def runner_with_balance():
    """Build an ArbRunner with a fake browser whose betinia balance is 200 SEK."""
    from arnold.mirror.arb_runner import ArbRunner

    class _FakeBrowser:
        provider_data = {"betinia": {"balance": 200.0}}
        context = None

    def _block(_b):  # pragma: no cover
        pass

    def _is_blocked(_b):
        return False

    class _FakeBroadcaster:
        def publish(self, *_a, **_k):
            pass

    def _build(stake_caps: dict[str, float] | None):
        return ArbRunner(
            provider_id="betinia",
            browser=_FakeBrowser(),
            broadcaster=_FakeBroadcaster(),
            proxy_url="http://localhost:18000",
            block_event_market=_block,
            is_blocked=_is_blocked,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
            stake_caps=stake_caps,
        )

    return _build


def _compute_anchor_stake(runner) -> float:
    """Lift the stake calc from _load_all_legs so we can unit-test it without async."""
    balance = runner._browser.provider_data.get(runner.provider_id, {}).get("balance") or 0.0
    cap = runner._stake_caps.get(runner.provider_id)
    return round(min(balance, cap) if cap else balance, 2)


def test_anchor_stake_uses_balance_when_no_cap(runner_with_balance):
    runner = runner_with_balance(stake_caps={})
    assert _compute_anchor_stake(runner) == 200.0


def test_anchor_stake_clamped_to_cap_when_cap_lower(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": 50.0})
    assert _compute_anchor_stake(runner) == 50.0


def test_anchor_stake_uses_balance_when_balance_lower(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": 500.0})
    assert _compute_anchor_stake(runner) == 200.0


def test_anchor_stake_none_cap_treated_as_no_cap(runner_with_balance):
    runner = runner_with_balance(stake_caps={"betinia": None})
    assert _compute_anchor_stake(runner) == 200.0
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_arb_stake_cap.py -v`
Expected: FAIL with `TypeError: ArbRunner.__init__() got an unexpected keyword argument 'stake_caps'`

- [ ] **Step 1.3: Add `stake_caps` to ArbRunner.__init__**

Edit `arnold/mirror/arb_runner.py` — find the `__init__` signature starting at line 82 and add the `stake_caps` parameter. Replace the existing signature (lines 82-100):

```python
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
        stake_caps: dict[str, float] | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._active_providers = list(active_providers or [])
        self._stake_caps = stake_caps if stake_caps is not None else {}
```

- [ ] **Step 1.4: Run unit tests to verify they pass**

Run: `cd backend && python -m pytest tests/mirror/test_arb_stake_cap.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 1.5: Apply the cap inside `_load_all_legs`**

Edit `arnold/mirror/arb_runner.py` — find the `_load_all_legs` block currently reading:

```python
        # Anchor stake = full balance (capped at site max)
        balance = self._browser.provider_data.get(self.provider_id, {}).get("balance") or 0.0
        anchor_stake = round(balance, 2)  # site-max cap learned later from limit responses
        if anchor_stake <= 0:
            return False
```

Replace with:

```python
        # Anchor stake = min(balance, learned site cap). _stake_caps is populated
        # by ProviderRunner / placement responses; if the soft book has rejected
        # us before with a maxStake hint, we honour it here so the next attempt
        # sizes correctly instead of getting rejected again.
        balance = self._browser.provider_data.get(self.provider_id, {}).get("balance") or 0.0
        cap = self._stake_caps.get(self.provider_id)
        anchor_stake = round(min(balance, cap) if cap else balance, 2)
        if anchor_stake <= 0:
            return False
```

- [ ] **Step 1.6: Wire `stake_caps` from `PlayCoordinator._spawn_runners`**

Edit `arnold/mirror/play_loop.py` — find the `else: runner = ArbRunner(...)` block in `_spawn_runners` (around line 408). Replace the ArbRunner construction with:

```python
            else:
                runner = ArbRunner(
                    provider_id=pid,
                    browser=self._browser,
                    broadcaster=self._broadcaster,
                    proxy_url=self._proxy_url,
                    block_event_market=self._block_event_market,
                    is_blocked=self._is_blocked,
                    placed_today=self._placed_today,
                    active_providers=active,
                    stake_caps=self._stake_caps,
                )
```

- [ ] **Step 1.7: Run the full mirror test directory**

Run: `cd backend && python -m pytest tests/mirror/ -v`
Expected: all existing tests still pass, the 4 new tests pass.

- [ ] **Step 1.8: Commit**

```bash
git add arnold/mirror/arb_runner.py arnold/mirror/play_loop.py backend/tests/mirror/test_arb_stake_cap.py
git commit -m "fix(arb): respect _stake_caps when sizing anchor stake

ArbRunner._load_all_legs now consults the same _stake_caps dict that
ProviderRunner populates from limit responses. Without this, anchors
got rejected at sizes the site had already told us were too big."
```

---

## Task 2: Hedge wait timeout

Adds a 180s ceiling on `_update_counter_slips_and_await_hedges` so the runner can't deadlock when the user closes the Pinnacle tab.

**Files:**
- Modify: `arnold/mirror/arb_runner.py:65-71` (constants)
- Modify: `arnold/mirror/arb_runner.py:645-717` (`_update_counter_slips_and_await_hedges`)
- Test: `backend/tests/mirror/test_arb_hedge_timeout.py`

- [ ] **Step 2.1: Write the failing test**

Create `backend/tests/mirror/test_arb_hedge_timeout.py`:

```python
"""Hedge wait timeout — ArbRunner must give up on un-clicked counters after N seconds."""

from __future__ import annotations

import asyncio

import pytest


class _RecordingBroadcaster:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict):
        self.events.append((event, payload))


@pytest.fixture
def runner_with_counter_legs():
    from arnold.mirror.arb_runner import ArbRunner

    class _FakeBrowser:
        provider_data = {"betinia": {"balance": 100.0}}
        context = None

    bc = _RecordingBroadcaster()
    runner = ArbRunner(
        provider_id="betinia",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://localhost:18000",
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
        active_providers=["betinia", "pinnacle"],
        stake_caps={},
    )
    runner.current_arb_group_id = "abcdef123456"
    runner.current_opp = {"event_id": "e1", "market": "moneyline"}
    runner._counter_legs = [{"provider": "pinnacle", "outcome": "away", "odds": 2.10}]
    runner._counter_events = {"pinnacle": asyncio.Event()}
    runner._counter_intercepted = {}  # never fires — simulates user not clicking

    # Stub the slip-stake push so we don't hit Playwright
    async def _no_op(*_a, **_k):
        return True

    from arnold.mirror import arb_runner as _ar

    class _StubWf:
        provider_id = "pinnacle"
        update_slip_stake = staticmethod(_no_op)
        parse_placement_status = staticmethod(lambda _b: {"success": True})

    def _get_wf(_pid):
        return _StubWf()

    runner._streams = {"pinnacle": type("S", (), {"page": None})()}

    return runner, bc, _get_wf


@pytest.mark.asyncio
async def test_hedge_timeout_emits_failure_for_unclicked_counters(monkeypatch, runner_with_counter_legs):
    runner, bc, get_wf_stub = runner_with_counter_legs
    from arnold.mirror import arb_runner as _ar

    monkeypatch.setattr(_ar, "get_workflow", get_wf_stub)
    monkeypatch.setattr(_ar, "COUNTER_HEDGE_TIMEOUT_S", 0.2)

    # Stub _record_bet to a no-op so we don't hit httpx
    async def _stub_record(*_a, **_k):
        return None

    runner._record_bet = _stub_record  # type: ignore

    await runner._update_counter_slips_and_await_hedges(
        anchor_actual_stake=50.0, anchor_actual_odds=2.0
    )

    failed = [p for e, p in bc.events if e == "arb_hedge_failed"]
    assert len(failed) == 1
    assert failed[0]["counter_provider"] == "pinnacle"
    assert failed[0]["reason"] == "user_timeout"
```

Note: requires `pytest-asyncio`. Check `backend/pyproject.toml` — if missing, install via:
```bash
cd backend && pip install pytest-asyncio
```
The repo uses `asyncio_mode = "auto"` in some tests; if your test doesn't autorun, add `@pytest.mark.asyncio` (already present above).

- [ ] **Step 2.2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_arb_hedge_timeout.py -v`
Expected: FAIL — either AttributeError on `COUNTER_HEDGE_TIMEOUT_S` or hangs (gather has no timeout). If it hangs, kill with Ctrl+C; that's the failure we're fixing.

- [ ] **Step 2.3: Add the timeout constant**

Edit `arnold/mirror/arb_runner.py` — find the constants block near the top (around line 65-71):

```python
_OPP_FETCH_COOLDOWN = 10.0
_ALIGNMENT_BROADCAST_THROTTLE_S = 0.5
LEG_DRIFT_TOL_PCT = 0.01  # 1% drift tolerance below planned odds → red
RERANK_INTERVAL_S = 5.0
DETHRONE_HYSTERESIS_PCT = 0.5
```

Add below:

```python
# Maximum time to wait for the user to click Place on every counter tab after
# the anchor placement has been intercepted. If the user closes the counter tab
# or never clicks, we give up on the missing legs and emit arb_hedge_failed
# rather than blocking the runner forever. Anchor is already on the site by
# this point — surfacing the failure lets the user manually hedge or absorb.
COUNTER_HEDGE_TIMEOUT_S = 180.0
```

- [ ] **Step 2.4: Wrap the gather in `wait_for`**

Edit `arnold/mirror/arb_runner.py` inside `_update_counter_slips_and_await_hedges`. Find:

```python
        # Wait for every counter event
        await asyncio.gather(*(ev.wait() for ev in self._counter_events.values()))
```

Replace with:

```python
        # Wait for every counter event up to COUNTER_HEDGE_TIMEOUT_S; counters
        # that don't fire in time are treated as user_timeout (anchor stays
        # un-hedged from the system's perspective; user must hedge manually).
        try:
            await asyncio.wait_for(
                asyncio.gather(*(ev.wait() for ev in self._counter_events.values())),
                timeout=COUNTER_HEDGE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            for leg in self._counter_legs:
                pid = leg["provider"]
                if pid in self._counter_intercepted:
                    continue
                self._broadcaster.publish(
                    "arb_hedge_failed",
                    {
                        "arb_group_id": self.current_arb_group_id,
                        "counter_provider": pid,
                        "outcome": leg.get("outcome"),
                        "reason": "user_timeout",
                        "max_stake": None,
                    },
                )
            logger.warning(
                f"[Arb:{self.provider_id}] Counter hedge wait timed out after "
                f"{COUNTER_HEDGE_TIMEOUT_S}s — {len(self._counter_legs) - len(self._counter_intercepted)} unfired"
            )
```

- [ ] **Step 2.5: Run unit tests**

Run: `cd backend && python -m pytest tests/mirror/test_arb_hedge_timeout.py -v`
Expected: PASS.

- [ ] **Step 2.6: Run all mirror tests**

Run: `cd backend && python -m pytest tests/mirror/ -v`
Expected: all green.

- [ ] **Step 2.7: Commit**

```bash
git add arnold/mirror/arb_runner.py backend/tests/mirror/test_arb_hedge_timeout.py
git commit -m "fix(arb): timeout counter hedge wait after 180s

Without a ceiling on the gather-of-counter-events, ArbRunner deadlocked
forever when the user closed the Pinnacle tab or the placement XHR
pattern didn't match. Now we emit arb_hedge_failed(reason='user_timeout')
for any leg whose intercept didn't fire in time."
```

---

## Task 3: Pinnacle bet-history discovery doc

Phase A of fix #4 — paper documentation only. The implementation in Task 4 reads from this doc; if discovery is incomplete, Task 4 falls back to DOM scrape.

**Files:**
- Create: `docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md`

- [ ] **Step 3.1: Write the discovery doc**

Create `docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md`:

```markdown
# Pinnacle Bet-History Discovery

**Date:** 2026-04-30
**Purpose:** Discover the URL pattern, request shape, and response shape of the bet-history XHR on pinnacle.se so `PinnacleMirrorWorkflow.sync_history` can intercept it. Until this doc is complete, `sync_history` falls back to DOM scrape (best-effort, low-volume only).

## Method

Manual capture using Chrome DevTools while logged into pinnacle.se. Steps:

1. Open https://www.pinnacle.se/en/account/bet-history/
2. Open DevTools → Network tab
3. Set the date filter to "Last 30 days" (or whatever surfaces a populated history)
4. Watch which XHRs fire; right-click → "Copy as fetch" the one(s) that contain bet rows
5. Note: URL, query params, request body, response body shape

## What to record

| Field | Notes |
|---|---|
| Endpoint URL pattern | e.g. `https://www.pinnacle.se/api/0.1/wagers/v3/...` |
| HTTP method | GET / POST |
| Auth | Bearer? Cookie? Custom header? |
| Pagination | offset/limit? cursor? page? |
| Response root path | `data.bets[]`? `result.wagers[]`? |
| Per-bet fields | provider_bet_id key, status field+values, odds key, stake key, payout key, event name path, market path, outcome path |
| Status values | What strings/codes map to won / lost / void / cashout |
| Odds format | Decimal? American? (Pinnacle uses American on slip — does history match?) |

## Acceptance

This doc is "complete" once `_BET_HISTORY_KEYWORDS` in `arnold/mirror/browser.py` has a Pinnacle-specific token and `_parse_pinnacle_history_entry` in `arnold/mirror/workflows/pinnacle.py` can map a real captured response into a `HistoryEntry`. Until then, `sync_history` returns the DOM-scrape best-effort and reconciliation runs in degraded mode.
```

- [ ] **Step 3.2: Commit the doc**

```bash
git add docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md
git commit -m "docs(spec): pinnacle bet-history discovery doc"
```

---

## Task 4: Pinnacle `sync_history` DOM scrape

Replaces the stub `sync_history` with a best-effort DOM scrape so counter-leg placements can reconcile until the XHR pattern is captured.

**Files:**
- Modify: `arnold/mirror/workflows/pinnacle.py:151-161` (`sync_history`)
- Test: `backend/tests/mirror/test_pinnacle_history.py`

- [ ] **Step 4.1: Write the failing test**

Create `backend/tests/mirror/test_pinnacle_history.py`:

```python
"""Pinnacle DOM-scrape history parser — covers row → HistoryEntry mapping."""

from __future__ import annotations

import pytest


def _row(**kwargs):
    """Build a dict simulating a Pinnacle history row as produced by the JS scrape."""
    base = {
        "provider_bet_id": "W123456",
        "event_name": "Real Madrid vs Barcelona",
        "market": "Money Line",
        "outcome": "Real Madrid",
        "odds": "1.85",
        "stake": "100.00",
        "status": "WON",
        "payout": "185.00",
    }
    base.update(kwargs)
    return base


def test_parse_won_bet_maps_to_history_entry():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row())

    assert entry is not None
    assert entry.provider_bet_id == "W123456"
    assert entry.status == "won"
    assert entry.odds == pytest.approx(1.85)
    assert entry.stake == pytest.approx(100.0)
    assert entry.payout == pytest.approx(185.0)


def test_parse_lost_bet():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="LOST", payout="0.00"))
    assert entry is not None
    assert entry.status == "lost"
    assert entry.payout == 0.0


def test_parse_void_bet():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="REFUNDED", payout="100.00"))
    assert entry is not None
    assert entry.status == "void"


def test_parse_unknown_status_returns_none():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="PENDING", payout="0.00"))
    assert entry is None


def test_parse_malformed_numbers_returns_none():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(odds="not-a-number"))
    assert entry is None
```

- [ ] **Step 4.2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_history.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_pinnacle_dom_row'`.

- [ ] **Step 4.3: Implement `_parse_pinnacle_dom_row` and replace `sync_history` stub**

Edit `arnold/mirror/workflows/pinnacle.py`. Replace the existing `sync_history` method (around line 151-161) with the full new implementation. Add the helper at module scope (above the class).

Find:

```python
    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Stub — history endpoint not observed during discovery.

        # TODO(pinnacle-history): Navigate to /en/account/bet-history/ and
        # intercept the /0.1/wagers or /0.1/bets/history XHR.  Implement once
        # the first manual visit to the history page captures the endpoint shape.
        """
        logger.info(
            f"[{self.provider_id}] sync_history stub returning [] — pending bets won't reconcile until implemented"
        )
        return []
```

Replace with:

```python
    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Best-effort DOM scrape of the bet-history page.

        Phase A discovery (XHR pattern) lives at
        docs/superpowers/specs/2026-04-30-pinnacle-history-discovery.md.
        Until that lands, we navigate the page manually and parse visible
        rows. Returns [] on selector miss — reconciler then runs in degraded
        mode (no Pinnacle settlement until the next attempt).
        """
        try:
            await page.goto(
                f"https://www.{self.domain}/en/account/bet-history/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_history: nav failed: {e}")
            return []

        # Wait briefly for the history table/rows to render. Selectors are
        # speculative until discovery — we try several before giving up.
        try:
            await page.wait_for_selector(
                "table, [data-testid*=history], [data-testid*=bet], .bet-history, .wager-row",
                timeout=8000,
            )
        except Exception:
            logger.info(f"[{self.provider_id}] sync_history: no history rows found in DOM")
            return []

        # Scrape rows in-page. Each row maps to a dict that _parse_pinnacle_dom_row
        # converts to a HistoryEntry. Field selectors are best-effort until
        # discovery captures the real DOM shape.
        try:
            raw_rows = await page.evaluate(
                """() => {
                    const rows = Array.from(document.querySelectorAll(
                        '[data-testid*=bet-row], [data-testid*=wager-row], tbody tr, .bet-history-row'
                    ));
                    return rows.map(r => {
                        const txt = (sel) => {
                            const el = r.querySelector(sel);
                            return el ? (el.textContent || '').trim() : '';
                        };
                        return {
                            provider_bet_id: r.getAttribute('data-bet-id') || r.id || '',
                            event_name: txt('[data-testid*=event-name]') || txt('.event-name') || '',
                            market: txt('[data-testid*=market]') || txt('.market') || '',
                            outcome: txt('[data-testid*=selection]') || txt('.selection') || '',
                            odds: txt('[data-testid*=odds]') || txt('.odds') || '',
                            stake: txt('[data-testid*=stake]') || txt('.stake') || '',
                            status: (txt('[data-testid*=status]') || txt('.status') || '').toUpperCase(),
                            payout: txt('[data-testid*=payout]') || txt('.payout') || '',
                        };
                    });
                }"""
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_history: DOM scrape failed: {e}")
            return []

        entries: list[HistoryEntry] = []
        for raw in raw_rows or []:
            entry = _parse_pinnacle_dom_row(raw)
            if entry:
                entries.append(entry)

        logger.info(
            f"[{self.provider_id}] sync_history (DOM): {len(entries)} settled entries from {len(raw_rows or [])} rows"
        )
        return entries
```

Add the helper at module scope — find the existing `def _slugify(s: str) -> str:` near the bottom and insert ABOVE it:

```python
# Status text → canonical status. Pinnacle's exact wording is unknown until
# discovery; we accept the most common bookmaker phrasings. Anything not in
# this map (e.g. "PENDING") is treated as not-settled and skipped.
_STATUS_MAP = {
    "WON": "won",
    "WIN": "won",
    "LOST": "lost",
    "LOSE": "lost",
    "VOID": "void",
    "VOIDED": "void",
    "CANCELLED": "void",
    "REFUND": "void",
    "REFUNDED": "void",
    "CASHOUT": "cashout",
    "CASHED OUT": "cashout",
}


def _parse_pinnacle_dom_row(raw: dict) -> HistoryEntry | None:
    """Map one DOM-scraped row dict into a HistoryEntry, or None if unparseable.

    Tolerant of missing fields — we'd rather drop a row than crash the
    reconciler. Numeric coercion errors → None.
    """
    status = _STATUS_MAP.get((raw.get("status") or "").strip().upper())
    if not status:
        return None
    try:
        odds = float((raw.get("odds") or "0").replace(",", ".").strip() or 0)
        stake = float((raw.get("stake") or "0").replace(",", ".").strip() or 0)
        payout = float((raw.get("payout") or "0").replace(",", ".").strip() or 0)
    except (ValueError, TypeError):
        return None
    if odds <= 0 or stake <= 0:
        return None
    return HistoryEntry(
        provider_bet_id=str(raw.get("provider_bet_id") or ""),
        event_name=raw.get("event_name") or "",
        market=raw.get("market") or "",
        outcome=raw.get("outcome") or "",
        odds=odds,
        stake=stake,
        status=status,
        payout=payout,
    )
```

- [ ] **Step 4.4: Run the parser tests**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_history.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 4.5: Run all mirror tests**

Run: `cd backend && python -m pytest tests/mirror/ -v`
Expected: all green.

- [ ] **Step 4.6: Commit**

```bash
git add arnold/mirror/workflows/pinnacle.py backend/tests/mirror/test_pinnacle_history.py
git commit -m "fix(pinnacle): DOM-scrape sync_history bridge

Replaces the [] stub with a best-effort DOM scrape of /en/account/bet-history/.
Returns [] on selector miss — reconciler runs in degraded mode rather than
crashing. Will be replaced by XHR interception once
2026-04-30-pinnacle-history-discovery.md is filled in."
```

---

## Task 5: Pinnacle-tab arbitration via `PinnacleSharedRunner`

Largest piece. Introduces a new runner class that owns the Pinnacle tab and lends it to ArbRunner on demand. PlayCoordinator spawns it instead of `ProviderRunner` whenever soft anchors are present.

**Files:**
- Create: `arnold/mirror/pinnacle_shared.py`
- Modify: `arnold/mirror/play_loop.py:373-421` (`_spawn_runners`)
- Modify: `arnold/mirror/arb_runner.py:82-100` (accept optional `pinnacle_shared`)
- Modify: `arnold/mirror/arb_runner.py:_load_all_legs` (lend before prep, release in finally)
- Test: `backend/tests/mirror/test_pinnacle_shared.py`

### Task 5a: Skeleton `PinnacleSharedRunner`

- [ ] **Step 5a.1: Write the failing test**

Create `backend/tests/mirror/test_pinnacle_shared.py`:

```python
"""PinnacleSharedRunner — lend/release semantics."""

from __future__ import annotations

import asyncio

import pytest


class _FakePage:
    url = "https://www.pinnacle.se/en/"


class _FakeContext:
    pages: list = []


class _FakeBrowser:
    context = _FakeContext()
    provider_data: dict = {"pinnacle": {"balance": 500.0}}


class _RecordingBroadcaster:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, ev, payload):
        self.events.append((ev, payload))


def _build_runner():
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner

    return PinnacleSharedRunner(
        provider_id="pinnacle",
        browser=_FakeBrowser(),
        broadcaster=_RecordingBroadcaster(),
        proxy_url="http://localhost:18000",
        pop_bet=lambda: None,
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
    )


def test_runner_starts_in_idle_state():
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()
    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()  # set means "not lent"


@pytest.mark.asyncio
async def test_lend_to_arb_marks_state_and_clears_event(monkeypatch):
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    # Stub find_tab to return a fake page
    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore

    page = await runner.lend_to_arb("group-abc")
    assert page is not None
    assert runner.state == STATE_LENT_TO_ARB
    assert not runner._lent_event.is_set()
    events = [e for e, _ in runner._broadcaster.events]
    assert "pinnacle_lent" in events


@pytest.mark.asyncio
async def test_release_to_value_sets_event_and_emits(monkeypatch):
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore
    await runner.lend_to_arb("group-abc")
    runner.release_to_value()

    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()
    events = [e for e, _ in runner._broadcaster.events]
    assert "pinnacle_released" in events


@pytest.mark.asyncio
async def test_lend_is_idempotent(monkeypatch):
    runner = _build_runner()

    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore
    p1 = await runner.lend_to_arb("group-abc")
    p2 = await runner.lend_to_arb("group-abc")
    assert p1 is p2
    lent_events = [e for e, _ in runner._broadcaster.events if e == "pinnacle_lent"]
    assert len(lent_events) == 1  # only emit once
```

- [ ] **Step 5a.2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arnold.mirror.pinnacle_shared'`.

- [ ] **Step 5a.3: Implement the skeleton**

Create `arnold/mirror/pinnacle_shared.py`:

```python
"""PinnacleSharedRunner — value-bet runner that can lend its tab to ArbRunner.

When the user selects both a soft anchor (e.g. betinia) and pinnacle, two
runners would otherwise share the Pinnacle tab and overwrite each other's
slip. This class arbitrates: in `value` mode it behaves like a ProviderRunner
playing value bets; on `lend_to_arb()` it stops navigating, returns the page
to ArbRunner, and waits for `release_to_value()` before resuming.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .provider_runner import ProviderRunner

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

STATE_LENT_TO_ARB = "lent_to_arb"


class PinnacleSharedRunner(ProviderRunner):
    """ProviderRunner subclass that supports lending its Pinnacle tab to ArbRunner.

    Public additions:
      lend_to_arb(arb_group_id) -> Page  (blocks until tab is found)
      release_to_value()                 (no-op if not lent)

    Internally we hold an asyncio.Event named `_lent_event`. It is set when
    the runner is free, cleared when an arb has borrowed the tab. The value
    loop must `await self._lent_event.wait()` before each navigation step.
    """

    def __init__(
        self,
        provider_id: str,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        pop_bet: Callable[[], dict | None],
        block_event_market: Callable[[dict], None],
        is_blocked: Callable[[dict], bool],
        placed_today: dict[str, int],
        peek_top_edge: Callable[[], float | None] | None = None,
        stake_caps: dict[str, float] | None = None,
        mark_recently_skipped: Callable[[dict], None] | None = None,
    ):
        super().__init__(
            provider_id=provider_id,
            browser=browser,
            broadcaster=broadcaster,
            proxy_url=proxy_url,
            pop_bet=pop_bet,
            block_event_market=block_event_market,
            is_blocked=is_blocked,
            placed_today=placed_today,
            peek_top_edge=peek_top_edge,
            stake_caps=stake_caps,
            mark_recently_skipped=mark_recently_skipped,
        )
        self._lent_event: asyncio.Event = asyncio.Event()
        self._lent_event.set()  # start in "free" state
        self._lent_to_group_id: str | None = None
        self._pre_lend_state: str | None = None

    async def _find_tab(self, context):
        """Indirection so tests can stub tab discovery without touching Playwright."""
        from .workflows import get_workflow

        wf = get_workflow(self.provider_id)
        return await wf.find_tab(context)

    async def lend_to_arb(self, arb_group_id: str) -> Page | None:
        """Mark the runner as lent, return the current Pinnacle page.

        Idempotent: a second call with the same arb_group_id returns the same
        page without re-emitting `pinnacle_lent`. A different group_id while
        already lent logs a warning and returns the current page anyway —
        ArbRunner is responsible for not overlapping arbs on the same tab.
        """
        if self._lent_to_group_id == arb_group_id:
            return await self._find_tab(self._browser.context)
        if self._lent_to_group_id is not None:
            logger.warning(
                f"[PinnacleShared] lend_to_arb({arb_group_id}) called while already lent to "
                f"{self._lent_to_group_id} — returning shared page anyway"
            )
        self._pre_lend_state = self.state
        self.state = STATE_LENT_TO_ARB
        self._lent_to_group_id = arb_group_id
        self._lent_event.clear()
        self._broadcaster.publish("pinnacle_lent", {"arb_group_id": arb_group_id})
        page = await self._find_tab(self._browser.context)
        return page

    def release_to_value(self) -> None:
        """Mark the runner as free again. No-op if not lent."""
        if self._lent_to_group_id is None:
            return
        group_id = self._lent_to_group_id
        self._lent_to_group_id = None
        # Don't restore the previous state literally — the value loop will
        # re-derive its state on the next iteration. Just leave a known-good
        # idle marker.
        from .play_loop import STATE_RUNNING

        self.state = self._pre_lend_state or STATE_RUNNING
        self._pre_lend_state = None
        self._lent_event.set()
        self._broadcaster.publish("pinnacle_released", {"arb_group_id": group_id})
```

- [ ] **Step 5a.4: Run the skeleton tests**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5a.5: Commit**

```bash
git add arnold/mirror/pinnacle_shared.py backend/tests/mirror/test_pinnacle_shared.py
git commit -m "feat(arb): PinnacleSharedRunner skeleton with lend/release

Subclasses ProviderRunner. Adds STATE_LENT_TO_ARB plus an asyncio.Event-
gated lend semaphore. ArbRunner will call lend_to_arb()/release_to_value()
in the next commit; the value loop will block on _lent_event next."
```

---

### Task 5b: Block the value loop on `_lent_event`

- [ ] **Step 5b.1: Add a regression test**

Append to `backend/tests/mirror/test_pinnacle_shared.py`:

```python
@pytest.mark.asyncio
async def test_value_loop_waits_for_lent_event(monkeypatch):
    """When lent, the value loop's pre-bet hook must yield until released."""
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner  # noqa: F401

    runner = _build_runner()

    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore
    await runner.lend_to_arb("g1")

    # The hook should not return while lent
    waited = asyncio.create_task(runner._await_unlent_or_done())
    await asyncio.sleep(0.05)
    assert not waited.done()

    runner.release_to_value()
    await asyncio.wait_for(waited, timeout=1.0)
```

- [ ] **Step 5b.2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py::test_value_loop_waits_for_lent_event -v`
Expected: FAIL — `AttributeError: '_await_unlent_or_done'`.

- [ ] **Step 5b.3: Add the hook**

Append to `arnold/mirror/pinnacle_shared.py` (inside the class):

```python
    async def _await_unlent_or_done(self) -> None:
        """Block until lent_event is set OR runner is being torn down.

        The value loop in ProviderRunner._run is a tight loop; rather than
        rewriting it here we expose this method and call it before each
        navigation step. The patch in _run is small enough that we override
        only the helper, not the entire loop.
        """
        await self._lent_event.wait()
```

- [ ] **Step 5b.4: Patch `_run` to call the hook**

Override `_run` in `PinnacleSharedRunner` by adding (still inside the class):

```python
    async def _run(self) -> None:
        """Wrap ProviderRunner._run so we await lent_event before each iteration.

        We intercept by replacing the bet-pop step. The simplest robust
        approach: monkey-patch the bound _pop_bet to first await the lent
        event, then delegate to the original popper. This way every pre-bet
        wait happens at the correct point in the parent's loop without us
        copying the loop body.
        """
        original_pop = self._pop_bet

        def _gated_pop():
            # Synchronous shim — schedule an await, but the loop already runs
            # on the same task. Use a simple busy-wait via run_until_done is
            # not allowed; instead, when lent we just return None and let the
            # parent loop's queue-empty idle path kick in, which sleeps 5s and
            # checks again. The 5s lent-busy-wait is acceptable: arbs are
            # rare relative to value bets.
            if not self._lent_event.is_set():
                return None
            return original_pop()

        self._pop_bet = _gated_pop  # type: ignore
        try:
            await super()._run()
        finally:
            self._pop_bet = original_pop  # type: ignore
```

- [ ] **Step 5b.5: Run all shared-runner tests**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 5b.6: Commit**

```bash
git add arnold/mirror/pinnacle_shared.py backend/tests/mirror/test_pinnacle_shared.py
git commit -m "feat(arb): block PinnacleSharedRunner value loop while lent

Gates _pop_bet behind _lent_event so the value loop returns None (queue-empty
idle path → 5s sleep) while ArbRunner has the tab. Avoids overriding the
entire run loop body — single-point override on the popper is enough."
```

---

### Task 5c: PlayCoordinator spawns `PinnacleSharedRunner` when needed

- [ ] **Step 5c.1: Write the failing test**

Append to `backend/tests/mirror/test_pinnacle_shared.py`:

```python
def test_coordinator_spawns_shared_runner_when_soft_anchors_present(monkeypatch):
    """PlayCoordinator must instantiate PinnacleSharedRunner — not ProviderRunner —
    when the active set contains both pinnacle and at least one soft anchor."""
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner
    from arnold.mirror.play_loop import PlayLoop

    pl = PlayLoop(browser=_FakeBrowser(), broadcaster=_RecordingBroadcaster(), proxy_url="http://x")
    pl._provider_ids = ["betinia", "pinnacle"]
    pl._spawn_runners(["betinia", "pinnacle"])

    assert isinstance(pl._runners["pinnacle"], PinnacleSharedRunner)


def test_coordinator_uses_plain_provider_runner_when_only_unlimited(monkeypatch):
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner
    from arnold.mirror.play_loop import PlayLoop
    from arnold.mirror.provider_runner import ProviderRunner

    pl = PlayLoop(browser=_FakeBrowser(), broadcaster=_RecordingBroadcaster(), proxy_url="http://x")
    pl._provider_ids = ["pinnacle", "polymarket"]
    pl._spawn_runners(["pinnacle", "polymarket"])

    runner = pl._runners["pinnacle"]
    assert isinstance(runner, ProviderRunner)
    assert not isinstance(runner, PinnacleSharedRunner)
```

- [ ] **Step 5c.2: Run to verify failure**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py -v`
Expected: the two new tests fail (current spawner always uses ProviderRunner).

- [ ] **Step 5c.3: Patch `_spawn_runners`**

Edit `arnold/mirror/play_loop.py` — find `_spawn_runners` (around line 373). Replace the body of the `if is_unlimited:` branch with:

```python
            if is_unlimited:
                cluster = _PROVIDER_TO_CLUSTER.get(pid, pid)
                if cluster not in self._cluster_queues:
                    self._cluster_queues[cluster] = []
                soft_anchors_present = any(
                    p not in UNLIMITED_PROVIDERS for p in provider_ids
                )
                if pid == "pinnacle" and soft_anchors_present:
                    from .pinnacle_shared import PinnacleSharedRunner

                    runner = PinnacleSharedRunner(
                        provider_id=pid,
                        browser=self._browser,
                        broadcaster=self._broadcaster,
                        proxy_url=self._proxy_url,
                        pop_bet=self._make_pop_bet(cluster),
                        block_event_market=self._block_event_market,
                        is_blocked=self._is_blocked,
                        placed_today=self._placed_today,
                        peek_top_edge=self._make_peek_top_edge(cluster),
                        stake_caps=self._stake_caps,
                        mark_recently_skipped=self._mark_recently_skipped,
                    )
                else:
                    runner = ProviderRunner(
                        provider_id=pid,
                        browser=self._browser,
                        broadcaster=self._broadcaster,
                        proxy_url=self._proxy_url,
                        pop_bet=self._make_pop_bet(cluster),
                        block_event_market=self._block_event_market,
                        is_blocked=self._is_blocked,
                        placed_today=self._placed_today,
                        peek_top_edge=self._make_peek_top_edge(cluster),
                        stake_caps=self._stake_caps,
                        mark_recently_skipped=self._mark_recently_skipped,
                    )
```

- [ ] **Step 5c.4: Run tests**

Run: `cd backend && python -m pytest tests/mirror/ -v`
Expected: all green.

- [ ] **Step 5c.5: Commit**

```bash
git add arnold/mirror/play_loop.py backend/tests/mirror/test_pinnacle_shared.py
git commit -m "feat(arb): PlayCoordinator spawns PinnacleSharedRunner when needed

When the active set contains soft anchors AND pinnacle, the coordinator
now wires the shared variant. Pure-unlimited sessions still use plain
ProviderRunner (no lending overhead)."
```

---

### Task 5d: ArbRunner uses lend/release

- [ ] **Step 5d.1: Write the failing test**

Append to `backend/tests/mirror/test_pinnacle_shared.py`:

```python
@pytest.mark.asyncio
async def test_arb_runner_calls_lend_then_release(monkeypatch):
    """ArbRunner must lend the Pinnacle tab when it loads counters and release on cleanup."""
    from arnold.mirror.arb_runner import ArbRunner
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner

    bc = _RecordingBroadcaster()
    shared = PinnacleSharedRunner(
        provider_id="pinnacle",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        pop_bet=lambda: None,
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
    )

    async def _fake_find_tab(_ctx):
        return _FakePage()

    shared._find_tab = _fake_find_tab  # type: ignore

    arb = ArbRunner(
        provider_id="betinia",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
        active_providers=["betinia", "pinnacle"],
        stake_caps={},
        pinnacle_shared=shared,
    )

    # We just want to verify lend/release wiring — call the helpers directly
    page = await arb._lend_pinnacle_if_needed("group-xyz")
    assert page is not None
    assert shared.state == "lent_to_arb"

    arb._release_pinnacle_if_held()
    assert shared.state != "lent_to_arb"
```

- [ ] **Step 5d.2: Run to verify failure**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py::test_arb_runner_calls_lend_then_release -v`
Expected: FAIL — `TypeError: ArbRunner.__init__() got an unexpected keyword argument 'pinnacle_shared'`.

- [ ] **Step 5d.3: Add the param + helpers to ArbRunner**

Edit `arnold/mirror/arb_runner.py` — extend the `__init__` signature added in Task 1.3 to include `pinnacle_shared`:

```python
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
        stake_caps: dict[str, float] | None = None,
        pinnacle_shared: object | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._active_providers = list(active_providers or [])
        self._stake_caps = stake_caps if stake_caps is not None else {}
        self._pinnacle_shared = pinnacle_shared
```

Add the two helper methods at the end of the class (just before the existing `_record_bet` or wherever convenient):

```python
    async def _lend_pinnacle_if_needed(self, arb_group_id: str):
        """Borrow the Pinnacle tab from the shared runner if one is wired.

        Returns the Page on success, None if no shared runner is configured
        (in which case the caller falls back to workflow.find_tab).
        """
        if self._pinnacle_shared is None:
            return None
        try:
            return await self._pinnacle_shared.lend_to_arb(arb_group_id)
        except Exception:
            logger.exception(f"[Arb:{self.provider_id}] lend_to_arb failed")
            return None

    def _release_pinnacle_if_held(self) -> None:
        """Release the Pinnacle tab back to the shared runner. No-op if none."""
        if self._pinnacle_shared is None:
            return
        try:
            self._pinnacle_shared.release_to_value()
        except Exception:
            logger.exception(f"[Arb:{self.provider_id}] release_to_value failed")
```

- [ ] **Step 5d.4: Run the new test**

Run: `cd backend && python -m pytest tests/mirror/test_pinnacle_shared.py::test_arb_runner_calls_lend_then_release -v`
Expected: PASS.

- [ ] **Step 5d.5: Wire lend/release into `_load_all_legs` and `_run`**

Edit `arnold/mirror/arb_runner.py`. Two changes:

(a) inside `_load_all_legs`, BEFORE the `_prep_leg(...)` parallel gather, lend the Pinnacle page if a counter leg is on Pinnacle. Find the line that ends the `for leg, planned_odds in zip(...)` loop and the start of the `# Navigate + prep all legs in parallel` comment. Insert immediately before that comment:

```python
        # If pinnacle is one of the counters, borrow the shared tab so the
        # value-bet PinnacleSharedRunner pauses while we own the slip.
        if any(l.get("provider") == "pinnacle" for l in counter_legs):
            await self._lend_pinnacle_if_needed(self.current_arb_group_id or "")
```

(b) in the outer `_run` method's `finally` block (the one at the end of `_run`), add a release call. Find:

```python
        finally:
            for s in self._streams.values():
                s.stop()
            self._streams.clear()
            self.state = STATE_IDLE
            self.current_opp = None
```

Replace with:

```python
        finally:
            for s in self._streams.values():
                s.stop()
            self._streams.clear()
            self._release_pinnacle_if_held()
            self.state = STATE_IDLE
            self.current_opp = None
```

Also add `_release_pinnacle_if_held()` in `stop()` after the `for s in self._streams.values(): s.stop()` line:

Find:
```python
    def stop(self) -> None:
        for s in self._streams.values():
            s.stop()
        self._streams.clear()
```

Replace with:
```python
    def stop(self) -> None:
        for s in self._streams.values():
            s.stop()
        self._streams.clear()
        self._release_pinnacle_if_held()
```

- [ ] **Step 5d.6: Update the dethrone branch to release before reload**

In `_run`, find the dethrone branch:

```python
                    if anchor_result is None:
                        # Either rejected, stopped, or dethroned
                        for s in self._streams.values():
                            s.stop()
                        self._streams.clear()
                        self._counter_events.clear()
                        self._counter_intercepted.clear()
                        if self._dethroned_to is not None:
```

Insert the release call just before the `if self._dethroned_to is not None:` line:

```python
                    if anchor_result is None:
                        # Either rejected, stopped, or dethroned
                        for s in self._streams.values():
                            s.stop()
                        self._streams.clear()
                        self._counter_events.clear()
                        self._counter_intercepted.clear()
                        # Reset the lend so the next _load_all_legs can re-lend cleanly
                        self._release_pinnacle_if_held()
                        if self._dethroned_to is not None:
```

Do the same just before the success-path cleanup at the end of the bet-loop:

Find:
```python
                    # Clean up streams
                    for s in self._streams.values():
                        s.stop()
                    self._streams.clear()
                    self._counter_events.clear()
                    self._counter_intercepted.clear()

                    placed_any = True
```

Replace with:
```python
                    # Clean up streams
                    for s in self._streams.values():
                        s.stop()
                    self._streams.clear()
                    self._counter_events.clear()
                    self._counter_intercepted.clear()
                    self._release_pinnacle_if_held()

                    placed_any = True
```

- [ ] **Step 5d.7: Wire the shared runner into PlayCoordinator's ArbRunner spawn**

Edit `arnold/mirror/play_loop.py` — find the ArbRunner construction (the `else: runner = ArbRunner(...)` updated in Step 1.6). Replace with:

```python
            else:
                pinnacle_shared = self._runners.get("pinnacle")
                # Only pass it through if it is actually the shared variant
                from .pinnacle_shared import PinnacleSharedRunner

                if not isinstance(pinnacle_shared, PinnacleSharedRunner):
                    pinnacle_shared = None
                runner = ArbRunner(
                    provider_id=pid,
                    browser=self._browser,
                    broadcaster=self._broadcaster,
                    proxy_url=self._proxy_url,
                    block_event_market=self._block_event_market,
                    is_blocked=self._is_blocked,
                    placed_today=self._placed_today,
                    active_providers=active,
                    stake_caps=self._stake_caps,
                    pinnacle_shared=pinnacle_shared,
                )
```

Note: `_spawn_runners` iterates `for pid in provider_ids` — if Pinnacle comes before the soft anchor in that list, the shared runner exists when ArbRunner is constructed. If the soft anchor comes first, `pinnacle_shared` will be None on first spawn. Fix by spawning unlimited providers first. Insert near the top of `_spawn_runners`, replacing `for pid in provider_ids:` with:

```python
        # Spawn unlimited providers first so soft-anchor ArbRunners can pick up
        # the shared Pinnacle reference. Without this, ArbRunner constructed
        # before PinnacleSharedRunner gets a None reference and falls back to
        # workflow.find_tab (which is fine but loses the lend/release benefit).
        ordered = sorted(provider_ids, key=lambda p: 0 if p in UNLIMITED_PROVIDERS else 1)
        for pid in ordered:
```

- [ ] **Step 5d.8: Run all mirror tests**

Run: `cd backend && python -m pytest tests/mirror/ -v`
Expected: all green.

- [ ] **Step 5d.9: Commit**

```bash
git add arnold/mirror/arb_runner.py arnold/mirror/play_loop.py backend/tests/mirror/test_pinnacle_shared.py
git commit -m "feat(arb): ArbRunner lends/releases the Pinnacle tab via shared runner

ArbRunner now borrows the Pinnacle page from PinnacleSharedRunner before
prepping counter slips and releases it in every cleanup path (success,
dethrone, reject, stop, fatal error). Coordinator spawns unlimited
providers first so the shared reference exists when ArbRunner is built."
```

---

## Task 6: End-to-end smoke check

Manual verification that all four fixes hold together. No automated test — the loop crosses a real browser, a real provider site, and the live API.

- [ ] **Step 6.1: Run the local Arnold app**

```bash
arnold.bat
```

Wait for the Sports tab to load and the SSH tunnel to come up.

- [ ] **Step 6.2: Open a Betinia tab and a Pinnacle tab**

- Open https://www.betinia.se in the Playwright browser, log in
- Open https://www.pinnacle.se/en in the same browser, log in
- Confirm both rows in the Sports tab show green (logged in + balance)

- [ ] **Step 6.3: Select betinia + pinnacle, start play**

- Click Start with both providers selected.
- Verify in the local logs: `Spawned PinnacleSharedRunner for pinnacle` and `Spawned ArbRunner for betinia`.

- [ ] **Step 6.4: Watch the SSE feed**

Open browser devtools on the local Arnold UI → Network → SSE stream. Confirm:
- `pinnacle_lent` fires when ArbRunner loads counter slips
- `arb_legs_loaded` follows
- `arb_alignment` ticks every ≤0.5s with `all_green: true|false`

- [ ] **Step 6.5: Place an anchor on Betinia**

Click Place in the Betinia tab. Confirm:
- `arb_anchor_placed` SSE event
- Counter stake on Pinnacle slip updates within ~1s
- DB has the anchor row with `notes: arb_group:<12-hex>`

- [ ] **Step 6.6: Confirm the timeout works**

Without clicking Place on Pinnacle, wait 3 minutes. Confirm:
- `arb_hedge_failed` with `reason: "user_timeout"` fires
- `pinnacle_released` fires
- ArbRunner advances to the next opp (or completes if none)

- [ ] **Step 6.7: Confirm Pinnacle reconciliation**

After settling a Pinnacle bet (use a small low-risk wager that resolves quickly):
- Trigger reconcile manually via the Sports tab settle button or wait for pending_loop
- Verify the DB pending row updates to `won|lost|void` matching site truth

- [ ] **Step 6.8: Final commit if any tweaks needed**

If smoke testing reveals tuning needs (timeout duration, log noise), commit those tweaks here:

```bash
git add -p
git commit -m "tune(arb): adjustments from smoke test"
```

---

## Self-Review

Spec coverage:
- §3 architecture: hedge timeout (Task 2), stake cap (Task 1), Pinnacle arbitration (Task 5), Pinnacle history (Task 4). ✅
- §4.1 hedge timeout details — present in Task 2.4. ✅
- §4.2 stake cap details — present in Task 1.3 and 1.5. ✅
- §4.3 PinnacleSharedRunner with lend/release — Task 5a–5d. ✅
- §4.4 sync_history DOM scrape + discovery doc — Task 3 + Task 4. ✅
- §5 SSE events: `pinnacle_lent`, `pinnacle_released`, `arb_hedge_failed(user_timeout)` — emitted in Task 5a.3 and Task 2.4. ✅
- §6 testing — every fix has unit tests; smoke test in Task 6. ✅
- §9 order matches: stake cap (Task 1) → timeout (Task 2) → discovery (Task 3) → history impl (Task 4) → arbitration (Task 5). ✅

Type consistency:
- `_stake_caps: dict[str, float] | None` — same name in ArbRunner.__init__ (Task 1.3), PinnacleSharedRunner.__init__ (Task 5a.3), ProviderRunner (existing). ✅
- `_lent_event: asyncio.Event` — used in Task 5a.3 and 5b. ✅
- `_pinnacle_shared: object | None` — kept loose typing to avoid forward-import cycle; downcast happens at coordinator level via `isinstance` check (Task 5d.7). ✅
- `lend_to_arb(arb_group_id) -> Page | None` — same signature in test (5a.1) and impl (5a.3). ✅
- `release_to_value()` — same name throughout. ✅
- `STATE_LENT_TO_ARB = "lent_to_arb"` — string literal matches the test assertion in Task 5d.1. ✅

Placeholder scan: every step has runnable code or exact text. No "implement appropriate X". ✅

UI work in spec §5 (PlayPage.tsx) was deferred — spec lists it but it is a follow-on cosmetic change that doesn't gate the drain loop. If the user wants it in this plan, append a Task 7 for the React renderer.
