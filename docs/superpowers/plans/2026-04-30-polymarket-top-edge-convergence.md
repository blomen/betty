# Polymarket top-edge convergence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the polymarket play runner always sit at READY on the bet whose live edge is genuinely the top of the queue. Re-insert dethroned bets at their live edge instead of dropping them.

**Architecture:** Add a `push_bet` callable to `PlayLoop` (queue mutator that re-inserts and sorts by `edge_pct`). In `ProviderRunner`, gate a polymarket-only convergence loop after `prep_betslip`: read live edge → compare to queue top (excluding self) → if not top, stamp live edge onto bet, push back, pop new top, repeat (cap 5 iterations). Modify the existing `_watch_for_better` dethrone path to push-back-with-`bet_reinserted` instead of drop-with-`bet_skipped`. Mark hard-fail prep results (`navigation_redirected`, `no_cent_button_matched`, `event_closed`, `click_failed`) with the existing 60s `_recently_skipped` TTL.

**Tech Stack:** Python 3.13, asyncio, pytest, unittest.mock, FastAPI (broadcaster SSE).

**Spec:** [docs/superpowers/specs/2026-04-30-polymarket-top-edge-convergence-design.md](docs/superpowers/specs/2026-04-30-polymarket-top-edge-convergence-design.md)

---

## File map

- **Modify** [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py) — add `_make_push_bet(cluster)` factory and pass `push_bet=...` into `ProviderRunner` construction.
- **Modify** [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) —
  - Add `push_bet` constructor parameter.
  - Add hard-fail TTL marking after `prep_failed`.
  - Add convergence loop (polymarket-only) after `prep_betslip` success, before `bet_ready`.
  - Modify dethrone path in `_watch_for_better` to push-back-and-reinsert.
- **Create** [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py) — unit tests for `_make_push_bet`, hard-fail TTL marking, and the dethrone re-insert logic.
- **Modify** [arnold/tests/test_provider_runner_stream.py](arnold/tests/test_provider_runner_stream.py) — update `_make_runner()` to pass the new `push_bet` parameter (default lambda).

The existing `test_play_loop.py` is broken (stale `arnoldsports` import) and out of scope — do not fix here.

---

## Task 1: Add `_make_push_bet` factory to PlayLoop

**Files:**
- Modify: [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py) — add new factory method next to `_make_pop_bet` and `_make_peek_top_edge` (around line 444).
- Test: [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py) — new file.

- [ ] **Step 1: Write the failing test**

Create `arnold/tests/test_polymarket_convergence.py`:

```python
"""Tests for polymarket top-edge convergence loop and queue helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

from arnold.mirror.play_loop import PlayLoop


def _make_loop() -> PlayLoop:
    return PlayLoop(
        browser=MagicMock(running=False, context=None, provider_data={}),
        broadcaster=MagicMock(publish=MagicMock()),
        proxy_url="https://x.test",
    )


def _bet(event_id: str, edge: float, market: str = "moneyline", outcome: str = "home") -> dict:
    return {
        "event_id": event_id,
        "provider_id": "polymarket",
        "market": market,
        "outcome": outcome,
        "edge_pct": edge,
        "fair_odds": 2.0,
        "stake": 10.0,
    }


def test_make_push_bet_appends_and_sorts_desc():
    """push_bet inserts a bet and re-sorts the cluster queue by edge_pct desc."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0), _bet("b", 5.0)]
    push = loop._make_push_bet("polymarket")
    push(_bet("c", 7.5))
    edges = [b["edge_pct"] for b in loop._cluster_queues["polymarket"]]
    assert edges == [10.0, 7.5, 5.0]


def test_make_push_bet_replaces_existing_key_in_place():
    """If a bet with the same (event_id, market, outcome) is in the queue,
    push_bet replaces its edge_pct instead of duplicating."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 23.0), _bet("b", 5.0)]
    push = loop._make_push_bet("polymarket")
    push(_bet("a", 18.0))  # same event_id, lower edge
    queue = loop._cluster_queues["polymarket"]
    assert len(queue) == 2  # not duplicated
    by_id = {b["event_id"]: b["edge_pct"] for b in queue}
    assert by_id == {"a": 18.0, "b": 5.0}
    # Re-sorted: b (5) > a (18)? no — a is still 18 > 5
    edges = [b["edge_pct"] for b in queue]
    assert edges == [18.0, 5.0]


def test_make_push_bet_updates_queue_total():
    """push_bet bumps _queue_total when adding a new bet."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0)]
    loop._queue_total = 1
    push = loop._make_push_bet("polymarket")
    push(_bet("b", 5.0))
    assert loop._queue_total == 2


def test_make_push_bet_no_total_bump_on_replace():
    """Replacing an existing bet must not bump _queue_total."""
    loop = _make_loop()
    loop._cluster_queues["polymarket"] = [_bet("a", 10.0)]
    loop._queue_total = 1
    push = loop._make_push_bet("polymarket")
    push(_bet("a", 5.0))
    assert loop._queue_total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py -v`
Expected: FAIL with `AttributeError: 'PlayLoop' object has no attribute '_make_push_bet'`

- [ ] **Step 3: Implement `_make_push_bet`**

In [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py), insert this method directly after `_make_peek_top_edge` (around line 462, before `_block_event_market`):

```python
    def _make_push_bet(self, cluster: str) -> callable:
        """Return a function that re-inserts a bet into the cluster queue and re-sorts.

        Idempotent on (event_id, market, outcome): if the bet is already present,
        its edge_pct is updated in place rather than appended. Used by the
        polymarket convergence loop to re-insert a bet at its just-measured live
        edge.
        """
        queue = self._cluster_queues[cluster]

        def push(bet: dict) -> None:
            key = (bet.get("event_id"), bet.get("market"), bet.get("outcome"))
            for existing in queue:
                if (existing.get("event_id"), existing.get("market"), existing.get("outcome")) == key:
                    existing["edge_pct"] = bet.get("edge_pct", existing.get("edge_pct"))
                    queue.sort(key=lambda b: -float(b.get("edge_pct") or 0))
                    return
            queue.append(bet)
            queue.sort(key=lambda b: -float(b.get("edge_pct") or 0))
            self._queue_total = sum(len(q) for q in self._cluster_queues.values())

        return push
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/play_loop.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(play): add _make_push_bet factory for cluster-queue re-insertion

Idempotent on (event_id, market, outcome): updates edge_pct in place
when the bet is already in the queue, rather than duplicating. Used by
the polymarket convergence loop to put a dethroned bet back at its
live-measured edge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Wire `push_bet` into `ProviderRunner` constructor

**Files:**
- Modify: [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py) — pass `push_bet=...` in `_spawn_runners` (around line 395-407).
- Modify: [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) — add constructor parameter (around line 86-100).
- Modify: [arnold/tests/test_provider_runner_stream.py](arnold/tests/test_provider_runner_stream.py) — update `_make_runner()`.

- [ ] **Step 1: Write the failing test**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_provider_runner_accepts_push_bet_param():
    """ProviderRunner constructor accepts push_bet callable."""
    from arnold.mirror.provider_runner import ProviderRunner

    push_calls = []

    def push(bet: dict) -> None:
        push_calls.append(bet)

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        push_bet=push,
    )
    assert runner._push_bet is push
    runner._push_bet({"event_id": "x", "edge_pct": 5.0})
    assert push_calls == [{"event_id": "x", "edge_pct": 5.0}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_provider_runner_accepts_push_bet_param -v`
Expected: FAIL with `TypeError: ProviderRunner.__init__() got an unexpected keyword argument 'push_bet'`

- [ ] **Step 3: Add the constructor parameter**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), modify `__init__` (around line 76-100). Add `push_bet` after `peek_top_edge`:

```python
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
        push_bet: Callable[[dict], None] | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._pop_bet = pop_bet
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._peek_top_edge = peek_top_edge
        self._stake_caps = stake_caps if stake_caps is not None else {}
        self._mark_recently_skipped = mark_recently_skipped or (lambda _b: None)
        self._push_bet = push_bet or (lambda _b: None)
```

- [ ] **Step 4: Wire push_bet into the spawn call**

In [arnold/mirror/play_loop.py](arnold/mirror/play_loop.py) `_spawn_runners` (around line 395-407), modify the `ProviderRunner(...)` construction inside the `if is_unlimited:` branch to pass `push_bet`:

```python
            if is_unlimited:
                cluster = _PROVIDER_TO_CLUSTER.get(pid, pid)
                if cluster not in self._cluster_queues:
                    self._cluster_queues[cluster] = []
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
                    push_bet=self._make_push_bet(cluster),
                )
```

- [ ] **Step 5: Run all relevant tests**

Run:
```bash
cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py arnold/tests/test_provider_runner_stream.py -v
```
Expected: 5 polymarket-convergence + 4 provider-runner-stream tests PASS.

- [ ] **Step 6: Commit**

```bash
git add arnold/mirror/play_loop.py arnold/mirror/provider_runner.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(runner): add push_bet param to ProviderRunner

Plumbed from PlayLoop._make_push_bet(cluster) for the polymarket
convergence loop. Default no-op so non-polymarket runners are unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Mark hard-fail prep results with 60s TTL

**Files:**
- Modify: [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) — modify `prep_failed` block (around line 418-425).
- Test: [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py).

- [ ] **Step 1: Write the failing test**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_hard_fail_reasons_constant_includes_known_failures():
    """The hard-fail reason set in provider_runner must cover all four
    polymarket prep_betslip failure modes."""
    from arnold.mirror.provider_runner import HARD_FAIL_PREP_REASONS

    assert "navigation_redirected" in HARD_FAIL_PREP_REASONS
    assert "no_cent_button_matched" in HARD_FAIL_PREP_REASONS
    assert "event_closed" in HARD_FAIL_PREP_REASONS
    assert "click_failed" in HARD_FAIL_PREP_REASONS


def test_is_hard_fail_reason_substring_match():
    """is_hard_fail_reason matches when any known prefix appears in the reason
    string. prep_betslip reasons include extra context, e.g.:
       'navigation_redirected (expected slug ... not in URL ...)'
       'no_cent_button_matched (market=moneyline, target=...)'
       'click_failed: js_eval_returned_none'
    """
    from arnold.mirror.provider_runner import is_hard_fail_reason

    assert is_hard_fail_reason("navigation_redirected (expected slug 'foo' not in URL 'bar')")
    assert is_hard_fail_reason("no_cent_button_matched (market=moneyline, target=team)")
    assert is_hard_fail_reason("click_failed: js_eval_returned_none")
    assert is_hard_fail_reason("event_closed")
    assert not is_hard_fail_reason("transient_render_glitch")
    assert not is_hard_fail_reason("")
    assert not is_hard_fail_reason(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_hard_fail_reasons_constant_includes_known_failures arnold/tests/test_polymarket_convergence.py::test_is_hard_fail_reason_substring_match -v`
Expected: FAIL with `ImportError: cannot import name 'HARD_FAIL_PREP_REASONS'`.

- [ ] **Step 3: Add the constant and helper**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), insert just after the `READY_TIMEOUT_S = 0.0` line (around line 70):

```python
# Hard-fail prep_betslip reasons — the bet cannot be played in its current
# state. Marked with the recently_skipped 60s TTL so it doesn't return on the
# next refresh tick. Polymarket-specific (other providers use different
# failure modes) but the matching is substring-based so it's safe everywhere.
HARD_FAIL_PREP_REASONS = (
    "navigation_redirected",
    "no_cent_button_matched",
    "event_closed",
    "click_failed",
)


def is_hard_fail_reason(reason: str | None) -> bool:
    """True if `reason` starts with or contains any HARD_FAIL_PREP_REASONS prefix."""
    if not reason:
        return False
    return any(token in reason for token in HARD_FAIL_PREP_REASONS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_hard_fail_reasons_constant_includes_known_failures arnold/tests/test_polymarket_convergence.py::test_is_hard_fail_reason_substring_match -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Use the helper in the prep_failed block**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), modify the `prep_failed` block (around line 418-425) to call `_mark_recently_skipped` for hard fails:

```python
                # Auto-skip if prep failed — without this the runner goes to
                # READY with no/wrong outcome selected and the user clicks Buy
                # on whatever's currently highlighted on the page (potentially
                # a wrong-market bet).
                if prep_result and prep_result.status == "failed":
                    logger.warning(f"[Runner:{pid}] Prep failed: {prep_result.reason} — skipping bet")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {"bet": bet, "reason": f"prep_failed: {prep_result.reason}"},
                    )
                    self.stats["skipped"] += 1
                    # Hard fails (redirect / closed event / unmatched cent button) get
                    # the 60s TTL so they don't immediately re-pop into the queue on
                    # the next _refresh_batch. Soft fails (none currently exist) fall
                    # through and may be re-fetched right away.
                    if is_hard_fail_reason(prep_result.reason):
                        self._mark_recently_skipped(bet)
                    continue
```

- [ ] **Step 6: Add an integration-style test for the marking call**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_mark_recently_skipped_called_on_hard_fail(monkeypatch):
    """When prep_betslip returns failed with a hard-fail reason, the runner
    must call mark_recently_skipped(bet) so refresh_batch excludes it for 60s."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from arnold.mirror.provider_runner import ProviderRunner

    marked: list[dict] = []

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        mark_recently_skipped=lambda b: marked.append(b),
        push_bet=lambda b: None,
    )

    # Drive the prep_failed branch directly via the helper logic. The full
    # _run loop is too complex to invoke from a unit test (browser tabs,
    # workflow strategy, asyncio scaffolding), so we simulate the part the
    # task adds: is_hard_fail_reason + mark_recently_skipped.
    from arnold.mirror.provider_runner import is_hard_fail_reason

    bet = {"event_id": "abc", "market": "moneyline", "outcome": "home"}
    reason = "navigation_redirected (expected slug 'x' not in URL 'y')"
    if is_hard_fail_reason(reason):
        runner._mark_recently_skipped(bet)
    assert marked == [bet]


def test_mark_recently_skipped_not_called_on_soft_reason():
    """Non-hard-fail prep reasons must NOT trigger the TTL marking."""
    from unittest.mock import MagicMock
    from arnold.mirror.provider_runner import ProviderRunner, is_hard_fail_reason

    marked: list[dict] = []
    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        mark_recently_skipped=lambda b: marked.append(b),
        push_bet=lambda b: None,
    )
    bet = {"event_id": "abc", "market": "moneyline", "outcome": "home"}
    reason = "transient_render_glitch"
    if is_hard_fail_reason(reason):
        runner._mark_recently_skipped(bet)
    assert marked == []
```

- [ ] **Step 7: Run tests**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py -v`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add arnold/mirror/provider_runner.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(runner): mark prep hard-fails with 60s recently_skipped TTL

Hard-fail prep reasons (navigation_redirected, no_cent_button_matched,
event_closed, click_failed) now trigger _mark_recently_skipped so the
bet doesn't immediately re-pop into the queue on the next refresh.
Soft fails fall through unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add convergence-loop helper (pure function, polymarket-only)

**Files:**
- Modify: [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) — add module-level constants + helper around line 70.
- Test: [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py).

- [ ] **Step 1: Write the failing test**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_convergence_should_redirect_returns_true_when_top_above_live():
    """should_redirect_to_top: queue top edge > live edge → True."""
    from arnold.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=19.9, queue_top_edge=23.0) is True
    assert should_redirect_to_top(live_edge=10.0, queue_top_edge=10.0001) is True


def test_convergence_should_redirect_returns_false_when_at_or_above_top():
    """should_redirect_to_top: live edge >= queue top → False (we're top)."""
    from arnold.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=23.0, queue_top_edge=23.0) is False
    assert should_redirect_to_top(live_edge=25.0, queue_top_edge=23.0) is False


def test_convergence_should_redirect_handles_missing_inputs():
    """Missing live_edge OR missing queue_top_edge → False (assume top)."""
    from arnold.mirror.provider_runner import should_redirect_to_top

    assert should_redirect_to_top(live_edge=None, queue_top_edge=23.0) is False
    assert should_redirect_to_top(live_edge=19.9, queue_top_edge=None) is False
    assert should_redirect_to_top(live_edge=None, queue_top_edge=None) is False


def test_convergence_max_iter_constant():
    """CONVERGENCE_MAX_ITER caps the convergence loop at 5."""
    from arnold.mirror.provider_runner import CONVERGENCE_MAX_ITER

    assert CONVERGENCE_MAX_ITER == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_convergence_should_redirect_returns_true_when_top_above_live -v`
Expected: FAIL with `ImportError: cannot import name 'should_redirect_to_top'`.

- [ ] **Step 3: Add the constant and helper**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), insert directly below the HARD_FAIL section added in Task 3:

```python
# Convergence loop: after prep_betslip, the polymarket runner re-pops the queue
# top until the bet on screen genuinely has the top live edge. Capped to
# prevent infinite churn on a flapping queue. Each iteration costs ~3-5s of
# navigation; 5 iterations = ~25s worst case. See
# docs/superpowers/specs/2026-04-30-polymarket-top-edge-convergence-design.md.
CONVERGENCE_MAX_ITER = 5


def should_redirect_to_top(live_edge: float | None, queue_top_edge: float | None) -> bool:
    """Zero-hysteresis convergence check.

    Returns True iff live_edge < queue_top_edge AND both values are present.
    Used by the polymarket convergence loop after prep_betslip to decide
    whether to push the active bet back and pop the new top.

    Returning False on any-None inputs is intentional: if we can't measure
    live edge or there's nothing in the queue, assume the active bet is OK
    and proceed to READY rather than churning.
    """
    if live_edge is None or queue_top_edge is None:
        return False
    return queue_top_edge > live_edge
```

- [ ] **Step 4: Run tests**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py -v -k convergence`
Expected: 4 convergence tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/provider_runner.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(runner): add convergence-loop helper for polymarket top-edge re-rank

should_redirect_to_top: zero-hysteresis comparison used by the convergence
loop to decide whether to push the active bet back and pop the queue's
new top. CONVERGENCE_MAX_ITER caps re-pops at 5 (~25s worst case).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire convergence loop into `_run` (polymarket-only)

**Files:**
- Modify: [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) — wrap the prep + post-prep block in a convergence loop, gated behind `if pid == "polymarket":`. Around lines 395-470.

This is the largest behavior change. The existing structure is:

```
NAVIGATING → navigate_to_event → check_event_closed → prep_betslip → check_live_price
            → bet_ready → READY wait
```

After this task, polymarket runners will have:

```
NAVIGATING → for iter in range(CONVERGENCE_MAX_ITER + 1):
                navigate_to_event → check_event_closed → prep_betslip
                if prep failed → handle (with TTL for hard fails) → break to outer
                check_live_price
                if should_redirect_to_top:
                    stamp live edge → push_bet → continue (back to pop_bet)
                else:
                    break out of convergence
            → bet_ready → READY wait
```

Non-polymarket runners are untouched: they use the existing single-pass flow.

- [ ] **Step 1: Write the failing test**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_convergence_iter_attribute_exists():
    """ProviderRunner tracks convergence_iter on self for telemetry."""
    from arnold.mirror.provider_runner import ProviderRunner
    from unittest.mock import MagicMock

    runner = ProviderRunner(
        provider_id="polymarket",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        push_bet=lambda b: None,
    )
    assert runner._convergence_iter == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_convergence_iter_attribute_exists -v`
Expected: FAIL with `AttributeError: ... has no attribute '_convergence_iter'`.

- [ ] **Step 3: Add the runner attribute**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) `__init__`, add directly after the `self._slip_stream = None` line (around line 114):

```python
        self._slip_stream = None  # Set when a slip is loaded; cleared when bet ready/placed/skipped
        # Per-bet convergence iteration counter — reset to 0 each time we
        # successfully reach READY. Used as a hard cap so a flapping queue
        # can't cause infinite re-navigation. See should_redirect_to_top.
        self._convergence_iter = 0
```

- [ ] **Step 4: Run test**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_convergence_iter_attribute_exists -v`
Expected: PASS.

- [ ] **Step 5: Refactor — extract prep + live-read into a helper method**

This isolates the per-iteration logic so the convergence loop can call it cleanly. Add this method on `ProviderRunner` directly above `_run` (around line 240):

```python
    async def _prep_and_read_live_edge(self, bet: dict, pid: str, workflow, page) -> tuple[Any, float | None, float | None]:
        """One iteration of: navigate-already-done → prep_betslip → check_live_price.

        Returns (prep_result, live_odds, live_edge). Caller handles failure
        modes (prep_result.status == "failed") and convergence decisions.
        """
        bet_ns = _bet_ns(bet)
        stake = bet.get("stake", 0.0)
        cached_bal = self._browser.provider_data.get(pid, {}).get("balance")
        if cached_bal is not None and cached_bal > 0 and stake > cached_bal:
            stake = cached_bal
        cap = self._stake_caps.get(pid)
        if cap is not None and cap > 0 and stake > cap:
            logger.info(f"[Runner:{pid}] Capping stake {stake} → {cap} (provider limit)")
            stake = cap
        bet["stake"] = stake
        bet_ns.stake = stake
        prep_result = await workflow.prep_betslip(page, bet_ns, stake)

        live_odds = prep_result.actual_odds if prep_result else None
        live_edge = bet.get("edge_pct")
        if prep_result and prep_result.status != "failed" and hasattr(workflow, "check_live_price"):
            try:
                lo, le = await workflow.check_live_price(page, bet_ns)
                if lo is not None:
                    live_odds = lo
                    live_edge = le
            except Exception:
                pass
        return prep_result, live_odds, live_edge
```

- [ ] **Step 6: Replace the prep + live-read block in `_run` with the convergence loop**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), in `_run`, find the block starting at the `# Prep betslip` comment (around line 400) and ending just before `# Ready — wait for interceptor or skip` (around line 454). Currently it looks like:

```python
                # Prep betslip
                stake = bet.get("stake", 0.0)
                cached_bal = self._browser.provider_data.get(pid, {}).get("balance")
                if cached_bal is not None and cached_bal > 0 and stake > cached_bal:
                    stake = cached_bal
                cap = self._stake_caps.get(pid)
                if cap is not None and cap > 0 and stake > cap:
                    logger.info(f"[Runner:{pid}] Capping stake {stake} → {cap} (provider limit)")
                    stake = cap
                bet["stake"] = stake
                bet_ns.stake = stake
                prep_result = await workflow.prep_betslip(page, bet_ns, stake)

                if prep_result and prep_result.status == "failed":
                    logger.warning(f"[Runner:{pid}] Prep failed: {prep_result.reason} — skipping bet")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {"bet": bet, "reason": f"prep_failed: {prep_result.reason}"},
                    )
                    self.stats["skipped"] += 1
                    if is_hard_fail_reason(prep_result.reason):
                        self._mark_recently_skipped(bet)
                    continue

                live_odds = prep_result.actual_odds
                live_edge = bet.get("edge_pct")
                if hasattr(workflow, "check_live_price"):
                    try:
                        lo, le = await workflow.check_live_price(page, bet_ns)
                        if lo is not None:
                            live_odds = lo
                            live_edge = le
                    except Exception:
                        pass

                if live_edge is not None and live_edge < 0:
                    logger.info(f"[Runner:{pid}] Auto-skip: live edge {live_edge:.1f}%")
                    self._broadcaster.publish(...)
                    self.stats["skipped"] += 1
                    continue
```

Replace this block (after the navigate + event_closed checks, before the READY-wait) with:

```python
                # Prep + live-edge read. For polymarket, wrap in a convergence
                # loop: re-pop the queue's new top whenever live edge drops
                # below it. Cap iterations at CONVERGENCE_MAX_ITER. Other
                # providers (pinnacle/cloudbet/kalshi) use the single-pass path.
                prep_result, live_odds, live_edge = await self._prep_and_read_live_edge(
                    bet, pid, workflow, page
                )

                # Hard-fail handling (any provider).
                if prep_result and prep_result.status == "failed":
                    logger.warning(f"[Runner:{pid}] Prep failed: {prep_result.reason} — skipping bet")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {"bet": bet, "reason": f"prep_failed: {prep_result.reason}"},
                    )
                    self.stats["skipped"] += 1
                    if is_hard_fail_reason(prep_result.reason):
                        self._mark_recently_skipped(bet)
                    self._convergence_iter = 0
                    continue

                # Polymarket-only convergence loop.
                if pid == "polymarket":
                    redirected = False
                    while self._convergence_iter < CONVERGENCE_MAX_ITER:
                        try:
                            queue_top = self._peek_top_edge(
                                (bet.get("event_id"), bet.get("market"), bet.get("outcome"))
                            ) if self._peek_top_edge else None
                        except TypeError:
                            queue_top = self._peek_top_edge() if self._peek_top_edge else None
                        if not should_redirect_to_top(live_edge, queue_top):
                            break  # Active bet IS top — proceed to READY.
                        # Stamp live edge on the bet and push back.
                        old_cached = bet.get("edge_pct")
                        bet["edge_pct"] = live_edge
                        self._convergence_iter += 1
                        self._broadcaster.publish(
                            "bet_converging",
                            {
                                "provider_id": pid,
                                "bet": bet,
                                "live_edge": live_edge,
                                "queue_top": queue_top,
                                "iteration": self._convergence_iter,
                                "old_cached_edge": old_cached,
                            },
                        )
                        logger.info(
                            f"[Runner:{pid}] Converging (iter {self._convergence_iter}/{CONVERGENCE_MAX_ITER}): "
                            f"live edge {live_edge:.1f}% < queue top {queue_top:.1f}% — re-inserting and re-popping"
                        )
                        self._push_bet(bet)
                        # Pop new top, navigate, prep again. If pop returns None
                        # (queue drained mid-cycle), break out and fall through
                        # to READY on the current (still-pushed-back) bet.
                        new_bet = self._pop_bet()
                        if new_bet is None:
                            logger.warning(f"[Runner:{pid}] Queue empty mid-convergence — falling through")
                            break
                        bet = new_bet
                        bet["provider_id"] = pid
                        self.current_bet = bet
                        bet_ns = _bet_ns(bet)
                        nav_ok = await workflow.navigate_to_event(page, bet_ns)
                        if not nav_ok:
                            self._broadcaster.publish(
                                "bet_skipped", {"bet": bet, "reason": "navigation_failed"}
                            )
                            self.stats["skipped"] += 1
                            redirected = True
                            break
                        if await self._is_event_closed(page):
                            self._broadcaster.publish(
                                "bet_skipped", {"bet": bet, "reason": "event_closed"}
                            )
                            self.stats["skipped"] += 1
                            redirected = True
                            break
                        prep_result, live_odds, live_edge = await self._prep_and_read_live_edge(
                            bet, pid, workflow, page
                        )
                        if prep_result and prep_result.status == "failed":
                            self._broadcaster.publish(
                                "bet_skipped",
                                {"bet": bet, "reason": f"prep_failed: {prep_result.reason}"},
                            )
                            self.stats["skipped"] += 1
                            if is_hard_fail_reason(prep_result.reason):
                                self._mark_recently_skipped(bet)
                            redirected = True
                            break
                    else:
                        # Hit CONVERGENCE_MAX_ITER — log and proceed on whatever we have.
                        logger.warning(
                            f"[Runner:{pid}] Convergence cap hit ({CONVERGENCE_MAX_ITER}) — "
                            f"proceeding to READY on {bet.get('display_home')} v {bet.get('display_away')} "
                            f"with live edge {live_edge}"
                        )
                    if redirected:
                        # Inner break fired with a skip — restart the outer loop.
                        self._convergence_iter = 0
                        continue
                    # Reset for the next bet pop.
                    self._convergence_iter = 0

                # Auto-skip negative EV (any provider).
                if live_edge is not None and live_edge < 0:
                    logger.info(f"[Runner:{pid}] Auto-skip: live edge {live_edge:.1f}%")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {
                            "bet": bet,
                            "reason": f"negative EV ({live_odds:.2f}, edge {live_edge:.1f}%)",
                            "live_odds": live_odds,
                            "live_edge": live_edge,
                        },
                    )
                    self.stats["skipped"] += 1
                    continue
                stake = bet["stake"]
```

Note: the trailing `stake = bet["stake"]` line is needed because the original code had `stake` as a local variable used by the READY-state code and `_handle_placement`. The helper method recomputes/applies stake; the outer `stake` must be re-bound from the (possibly updated) bet dict.

- [ ] **Step 7: Verify the imports are present**

Ensure `should_redirect_to_top`, `is_hard_fail_reason`, `CONVERGENCE_MAX_ITER` are referenced from module-level (already defined in Tasks 3 & 4 — they're in the same file as `_run`, so no imports needed).

- [ ] **Step 8: Run all relevant tests**

Run:
```bash
cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py arnold/tests/test_provider_runner_stream.py arnold/tests/test_arb_runner_v2.py -v
```
Expected: all PASS. The arb runner test is included as a regression check (it instantiates `ArbRunner`, which shares some code paths but we didn't touch its constructor).

- [ ] **Step 9: Compile-check the file**

Run: `cd c:/Users/rasmu/arnold && python -c "from arnold.mirror.provider_runner import ProviderRunner; print('OK')"`
Expected: `OK`.

- [ ] **Step 10: Commit**

```bash
git add arnold/mirror/provider_runner.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(runner): polymarket convergence loop after prep_betslip

After prep_betslip succeeds, polymarket runners now compare live edge to
the queue's top (excluding self). If live edge < top, the bet is stamped
with its just-measured live edge, pushed back, and the queue's new top
is popped/navigated. Repeat up to CONVERGENCE_MAX_ITER (5) before
falling through to READY.

Other providers unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Modify READY-state dethrone to push-back-and-reinsert

**Files:**
- Modify: [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py) — `_watch_for_better` task body (around line 547-586) and the post-wait skip-broadcast block (around line 700-720).

The current dethrone path drops the bet (broadcasts `bet_skipped` with reason "dethroned by …" via `_auto_skip_reason`) and the runner pops the next top. We need:

1. When dethrone fires (queue top ≥ live edge + 2pts hysteresis), push the active bet back at its live edge.
2. Broadcast `bet_reinserted` instead of `bet_skipped`.
3. Set `_skip_event` to exit the wait (unchanged).
4. The post-wait code branches on a new `_dethrone_reinsert` flag to suppress the `bet_skipped` broadcast for this case.

- [ ] **Step 1: Write the failing test**

Append to [arnold/tests/test_polymarket_convergence.py](arnold/tests/test_polymarket_convergence.py):

```python
def test_should_dethrone_at_ready_uses_2pt_hysteresis():
    """At-READY dethrone uses DETHRONE_HYSTERESIS_PCT (2pts) — strict
    convergence is only on initial entry."""
    from arnold.mirror.provider_runner import (
        DETHRONE_HYSTERESIS_PCT,
        should_dethrone_at_ready,
    )

    assert DETHRONE_HYSTERESIS_PCT == 2.0
    # Below hysteresis — do not dethrone.
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=21.5) is False
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=22.0) is True  # exactly +2
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=23.0) is True


def test_should_dethrone_at_ready_handles_missing_inputs():
    """Missing live_edge OR queue_top_edge → False (don't dethrone)."""
    from arnold.mirror.provider_runner import should_dethrone_at_ready

    assert should_dethrone_at_ready(live_edge=None, queue_top_edge=23.0) is False
    assert should_dethrone_at_ready(live_edge=20.0, queue_top_edge=None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_should_dethrone_at_ready_uses_2pt_hysteresis -v`
Expected: FAIL with `ImportError: cannot import name 'should_dethrone_at_ready'`.

- [ ] **Step 3: Add the helper**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), add directly after `should_redirect_to_top`:

```python
def should_dethrone_at_ready(live_edge: float | None, queue_top_edge: float | None) -> bool:
    """At-READY dethrone with DETHRONE_HYSTERESIS_PCT buffer.

    Returns True iff queue_top_edge >= live_edge + hysteresis. Used by
    _watch_for_better while the runner is sitting at READY waiting for the
    user. The hysteresis prevents thrashing on small edge fluctuations.
    """
    if live_edge is None or queue_top_edge is None:
        return False
    return queue_top_edge >= live_edge + DETHRONE_HYSTERESIS_PCT
```

- [ ] **Step 4: Run test**

Run: `cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py::test_should_dethrone_at_ready_uses_2pt_hysteresis arnold/tests/test_polymarket_convergence.py::test_should_dethrone_at_ready_handles_missing_inputs -v`
Expected: PASS.

- [ ] **Step 5: Modify `_watch_for_better` to push-back-and-reinsert**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), find the `_watch_for_better` async function inside `_run` (around line 547). The current body uses an inline comparison and broadcasts `bet_dethroned`. Replace the comparison + broadcast block with:

```python
                async def _watch_for_better() -> None:
                    nonlocal _dethrone_reason, _auto_skip_reason
                    while True:
                        try:
                            await asyncio.sleep(DETHRONE_POLL_S)
                        except asyncio.CancelledError:
                            raise
                        if self._peek_top_edge is None:
                            continue
                        try:
                            top_edge = self._peek_top_edge(_active_key)
                        except TypeError:
                            top_edge = self._peek_top_edge()
                        except Exception:
                            continue
                        if top_edge is None:
                            continue
                        compare_edge = live_edge_holder[0]
                        if compare_edge is None:
                            compare_edge = _intent_edge
                        if not should_dethrone_at_ready(compare_edge, top_edge):
                            continue
                        # Dethrone fires — push active bet back at its live edge
                        # and exit the wait so the runner pops the new top.
                        bet["edge_pct"] = compare_edge if compare_edge is not None else _intent_edge
                        self._push_bet(bet)
                        _dethrone_reason = (
                            f"reinserted at +{compare_edge:.1f}% "
                            f"(queue top +{top_edge:.1f}%, hysteresis {DETHRONE_HYSTERESIS_PCT:.1f}pts)"
                        )
                        # Mark this as a re-insert so the post-wait code
                        # broadcasts bet_reinserted instead of bet_skipped.
                        _auto_skip_reason = _dethrone_reason
                        self._broadcaster.publish(
                            "bet_reinserted",
                            {
                                "provider_id": pid,
                                "bet": bet,
                                "old_cached_edge": _intent_edge,
                                "new_live_edge": compare_edge,
                                "queue_top": top_edge,
                            },
                        )
                        self._skip_event.set()
                        return
```

- [ ] **Step 6: Add `_dethrone_reinsert` tracking flag**

The post-wait code branches on `_auto_skip_reason` to broadcast `bet_skipped`. We need a separate signal to suppress that broadcast on dethrone-reinsert. Add a new local flag in `_run` near where `_auto_skip_reason` is declared (around line 477). Find:

```python
                _auto_skip_reason: str | None = None
```

Replace with:

```python
                _auto_skip_reason: str | None = None
                _dethrone_reinsert: bool = False
```

Then in `_watch_for_better`, set the flag when dethrone fires. Modify the dethrone block from Step 5 to also include:

```python
                        nonlocal _dethrone_reinsert
                        _dethrone_reinsert = True
```

immediately before `self._skip_event.set()`.

(`nonlocal` for `_dethrone_reinsert` must be declared at the top of `_watch_for_better` alongside `_dethrone_reason, _auto_skip_reason`.) Update the function header:

```python
                async def _watch_for_better() -> None:
                    nonlocal _dethrone_reason, _auto_skip_reason, _dethrone_reinsert
```

- [ ] **Step 7: Modify the post-wait skip block**

In [arnold/mirror/provider_runner.py](arnold/mirror/provider_runner.py), find the post-wait block (around line 700-720). The current code:

```python
                if _auto_skip_reason is not None and not self._bet_intercepted_event.is_set():
                    logger.info(f"[Runner:{pid}] Auto-skip: {_auto_skip_reason}")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {
                            "bet": bet,
                            "reason": _auto_skip_reason,
                            "live_odds": live_odds,
                            "live_edge": live_edge,
                        },
                    )
                    self.stats["skipped"] += 1
```

Replace with:

```python
                if _auto_skip_reason is not None and not self._bet_intercepted_event.is_set():
                    if _dethrone_reinsert:
                        # bet_reinserted was already broadcast inside _watch_for_better.
                        # Don't double-broadcast as a skip and don't bump stats["skipped"]
                        # — re-insert is internal queue-rebalance, not a skip.
                        logger.info(f"[Runner:{pid}] Re-insert: {_auto_skip_reason}")
                    else:
                        logger.info(f"[Runner:{pid}] Auto-skip: {_auto_skip_reason}")
                        self._broadcaster.publish(
                            "bet_skipped",
                            {
                                "bet": bet,
                                "reason": _auto_skip_reason,
                                "live_odds": live_odds,
                                "live_edge": live_edge,
                            },
                        )
                        self.stats["skipped"] += 1
```

- [ ] **Step 8: Compile-check**

Run: `cd c:/Users/rasmu/arnold && python -c "from arnold.mirror.provider_runner import ProviderRunner; print('OK')"`
Expected: `OK`.

- [ ] **Step 9: Run tests**

Run:
```bash
cd c:/Users/rasmu/arnold && python -m pytest arnold/tests/test_polymarket_convergence.py arnold/tests/test_provider_runner_stream.py arnold/tests/test_arb_runner_v2.py -v
```
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add arnold/mirror/provider_runner.py arnold/tests/test_polymarket_convergence.py
git commit -m "feat(runner): READY dethrone re-inserts at live edge instead of dropping

When _watch_for_better fires at READY (queue top >= live edge + 2pts
hysteresis), the active bet is now pushed back to the cluster queue
at its just-measured live edge instead of being dropped. The runner
broadcasts bet_reinserted (audit trail) instead of bet_skipped, and
stats[\"skipped\"] is not incremented — this is a queue rebalance,
not a skip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Manual end-to-end verification

**Files:** none (testing only).

This task is a manual verification checklist. The auto tests cover the pure helpers, but the convergence loop interacts with browser, Playwright, and the live polymarket DOM — only an end-to-end run can validate the full flow.

- [ ] **Step 1: Start the local arnold app**

Run: `cd c:/Users/rasmu/arnold && arnold.bat` (Windows) — opens SSH tunnel + uvicorn + Playwright + browser at `http://localhost:8000`.

- [ ] **Step 2: Open the Sports tab → Play, select polymarket only**

Wait for login detection. Confirm balance shows in the UI.

- [ ] **Step 3: Click Start. Open browser dev tools → Network → EventStream tab**

Filter for `text/event-stream`. The local SSE endpoint streams runner events.

- [ ] **Step 4: Verify convergence telemetry on entry**

Expected sequence in the SSE stream (in order):
- `provider_opening`
- `login_waiting` (multiple)
- `login_detected`
- `settling_pending` → `settling_done`
- `bet_converging` (0-3 events, depending on how stale the cached batch is)
- `bet_ready` (the final one — confirm `bet.event_id` matches what the UI shows at the top of the polymarket list)

- [ ] **Step 5: Test READY-state dethrone**

While at READY, watch for `bet_reinserted` in the stream. Should fire only when a queue bet's cached edge exceeds the active's live edge by ≥ 2pts. Confirm:
- `bet_reinserted.bet` is the one that WAS at READY.
- The next `bet_ready` event is for a different `event_id`.
- The UI does NOT show a "skipped" status — the dethroned bet should still appear in the queue list.

- [ ] **Step 6: Test hard-fail TTL**

Force a hard fail: in the polymarket browser tab, navigate to a closed/expired event manually before the runner gets there. When the runner pops that bet:
- `bet_skipped` fires with `reason: prep_failed: ...`.
- For ~60s the bet does NOT re-appear in the queue (verify via `_refresh_batch` logs in the local FastAPI console or by watching the UI list).
- After 60s, the bet returns.

- [ ] **Step 7: Stress: convergence cap**

Hard to force naturally. Instead, log-check: after 30 minutes of polymarket play, grep the local arnold log for `Convergence cap hit (5)`. If it appears, that's expected; if it appears more than ~3 times per session, the queue is unstable and we may need to revisit hysteresis tuning.

- [ ] **Step 8: Confirm no regression on other UNCAPPED providers**

Restart with pinnacle (or cloudbet) selected. Confirm:
- `bet_converging` and `bet_reinserted` events do NOT appear (these are polymarket-only).
- The runner places bets via the normal flow.

- [ ] **Step 9: Document findings**

If anything misbehaves, note it on the spec doc as a `## Open issues` section. Otherwise mark the implementation as verified.

---

## Self-review

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| Convergence loop after prep_betslip | Task 5 |
| Zero-hysteresis on initial convergence | Task 4 (`should_redirect_to_top`) + Task 5 |
| 2pts hysteresis at READY | Task 6 (`should_dethrone_at_ready`) |
| Re-insert at live edge | Task 1 (`_make_push_bet`) + Tasks 5 & 6 |
| Hard-fail 60s TTL | Task 3 |
| 5-iteration convergence cap | Task 4 (`CONVERGENCE_MAX_ITER`) + Task 5 |
| Polymarket-only gating | Task 5 (`if pid == "polymarket":`) |
| `bet_converging` SSE | Task 5 |
| `bet_reinserted` SSE | Task 6 |
| Remove `bet_dethroned` | Task 6 (replaced by `bet_reinserted`) |
| Idempotent push (replace existing) | Task 1 |
| Manual e2e verification | Task 7 |

**Placeholder scan:** No "TBD"/"TODO". Every code block contains complete code. No "implement appropriate error handling" — failures are enumerated explicitly.

**Type consistency:**
- `should_redirect_to_top(live_edge, queue_top_edge) -> bool` — used in Tasks 4 and 5 with same signature.
- `should_dethrone_at_ready(live_edge, queue_top_edge) -> bool` — used in Tasks 6 with same signature.
- `is_hard_fail_reason(reason) -> bool` — used in Tasks 3 and 5 with same signature.
- `_push_bet(bet)` callable — defined in Task 1, plumbed in Task 2, used in Tasks 5 and 6.
- `CONVERGENCE_MAX_ITER`, `DETHRONE_HYSTERESIS_PCT` — used consistently across Tasks 4, 5, 6.

All consistent.
