# Pinnacle workflow migration — dedicated class → strategies pattern (mirror style)

**Date:** 2026-04-30
**Author:** Rasmus + Claude
**Status:** Design (pending implementation plan)

## Goal

Migrate Pinnacle off the dedicated `PinnacleMirrorWorkflow` class and onto the same `GenericWorkflow + intel JSON + strategies/<provider>.py` pattern Polymarket uses. Behavior model: **B2 — mirror placement (user confirms CONFIRM on pinnacle.se), API-based settlement.**

## Why

The migration is half-done. `data/mirror_intel/pinnacle.json` exists. `arnold/mirror/workflows/strategies/pinnacle.py` is ~840 lines of working code (login + balance + history + scan + settle_all + check_live_price + navigate_to_event + place_bet) with recent commits proving live placement (`ace8f748` "auth works, bet placed live"). But:

1. `arnold/mirror/workflows/__init__.py` still routes `pinnacle → PinnacleMirrorWorkflow` via `_PROVIDER_TO_PLATFORM`, so the strategy file is dormant.
2. `_scan` and `_settle_all` are defined but missing from the `Strategy(...)` export.
3. Under B2 the strategy needs a new `_prep_betslip` (DOM-click, ported from the dedicated class) and the API-based `_place_bet` should be removed.
4. `read_slip_odds`, `update_slip_stake`, `parse_placement_status`, `parse_placement_response` are called by `arb_runner.py`, `slip_odds_stream.py`, and `provider_runner.py`. The dedicated class overrides all four. `GenericWorkflow` doesn't, so today the migration would silently break Pinnacle's role as anchor in arb runs.

## Decisions (captured in brainstorm)

- **B (mirror style, not autonomous API placement).** User clicks CONFIRM on pinnacle.se; we intercept the placement XHR.
- **B2 (keep API for settlement).** `sync_history` keeps DOM-first + API-fallback. `_scan` and `_settle_all` stay (API-based).
- **(c) Test disposition.** Delete the dedicated `pinnacle.py` and most of `test_pinnacle_slip.py`. Port the four placement-parser tests to a new `test_pinnacle_strategy.py` targeting the strategy module.
- **Intel JSON change:** drop `"autonomous_placement": true`. Keep `markets.designation_map`, `markets.key_map`, `navigation`. No `login` block — strategy `_check_login` overrides intel.

## Architecture

```
                     ┌─────────────────────────────────┐
                     │  arnold/mirror/workflows/        │
                     │     __init__.py                  │
                     │  get_workflow("pinnacle")        │
                     └──────┬──────────────────────────┘
                            │  intel JSON exists, no
                            │  explicit platform map
                            ▼
                     ┌─────────────────────────────────┐
                     │  GenericWorkflow                 │
                     │   + data/mirror_intel/           │
                     │       pinnacle.json              │
                     │   + strategies/pinnacle.py       │
                     │     (loaded by load_strategy)    │
                     └──────┬──────────────────────────┘
                            │
              ┌─────────────┼─────────────┬───────────────┐
              ▼             ▼             ▼               ▼
        check_login   sync_balance   sync_history    prep_betslip
        sync_history  scan         settle_all       check_live_price
        navigate_to_event                           read_slip_odds
                                                    update_slip_stake
                                                    parse_placement_*
```

`place_bet` is intentionally NOT a strategy field. `GenericWorkflow.place_bet` falls back to a "manual" PlacementResult, and `provider_runner.py:990` only calls it when `autonomous_placement` is true (which it isn't anymore). The placement XHR interceptor in `browser.py` catches the user's CONFIRM click and routes the body through `parse_placement_status` / `parse_placement_response`.

## Files

### Modified

**`arnold/mirror/workflows/__init__.py`**
- Remove `from .pinnacle import PinnacleMirrorWorkflow`.
- Remove `"pinnacle_mirror": PinnacleMirrorWorkflow` from `_PLATFORM_MAP`.
- Remove `"pinnacle": "pinnacle_mirror"` from `_PROVIDER_TO_PLATFORM`.
- Update header comment to drop the PinnacleMirrorWorkflow reference.

**`arnold/mirror/workflows/strategies/__init__.py`** — extend the `Strategy` dataclass:
```python
read_slip_odds: Callable | None = None
update_slip_stake: Callable | None = None
parse_placement_response: Callable | None = None
parse_placement_status: Callable | None = None
```

**`arnold/mirror/workflows/generic.py`** — add four delegating methods:
```python
async def read_slip_odds(self, page):
    if self.strategy and self.strategy.read_slip_odds:
        return await self.strategy.read_slip_odds(page, self.intel)
    return await super().read_slip_odds(page)

async def update_slip_stake(self, page, stake):
    if self.strategy and self.strategy.update_slip_stake:
        return await self.strategy.update_slip_stake(page, stake, self.intel)
    return await super().update_slip_stake(page, stake)

def parse_placement_response(self, body):
    if self.strategy and self.strategy.parse_placement_response:
        return self.strategy.parse_placement_response(body)
    return super().parse_placement_response(body)

def parse_placement_status(self, body):
    if self.strategy and self.strategy.parse_placement_status:
        return self.strategy.parse_placement_status(body)
    return super().parse_placement_status(body)
```

`parse_placement_*` were `@staticmethod` on `ProviderWorkflow`. They become instance methods on `GenericWorkflow`. Existing `Class.parse_placement_status(body)` call sites on dedicated subclasses (e.g. `altenar.py:535`) keep working.

**`arnold/mirror/workflows/strategies/pinnacle.py`** — keep almost everything; modify the `Strategy(...)` export and add new helpers:

*Keep as-is:*
- `_check_login`, `_sync_balance` (localStorage-based)
- `_sync_history` (DOM-first, API-fallback)
- `_check_live_price`, `_navigate_to_event`
- `_scan`, `_settle_all`
- All API helpers (`_evaluate_api`, `_post_api`, `_build_headers`, `_PINNACLE_HEADERS`, `_api_base`, `_designation_map`, `_market_key_map`, `_find_market`, `_american_to_decimal`, `_parse_api_bet`, `_bets_list`, `_date_range`)

*Remove:*
- `_place_bet` (~90 lines). Without `autonomous_placement: true`, never invoked.

*Add (port from dedicated `pinnacle.py`):*
- `_MARKET_LABEL_MAP`, `_OUTCOME_POSITION` constants.
- `_click_market_btn(page, market, outcome)` — internal helper.
- `_prep_betslip(page, bet, stake, intel)` — click outcome → poll for `localStorage["Main:Betslip"].Selections.length > 0` (5s) → call `_update_slip_stake`. Returns `PlacementResult(status="prepped" | "failed", reason=...)`.
- `_read_slip_odds(page, intel)` — read `localStorage["Main:Betslip"].Selections[0].price` (American), convert via `_american_to_decimal`.
- `_update_slip_stake(page, stake, intel)` — React hidden-setter on `input[placeholder="Stake"]`. Returns bool.
- `parse_placement_status(body)` — module-level function (was `@staticmethod`). Looks for `wagerNumber`/`betId` → success; `limits[].type == "maxRiskStake"` → max_stake.
- `parse_placement_response(body)` — module-level function. Returns `wagerNumber` or `betId` as string.

*Final `Strategy(...)` export:*
```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scan=_scan,
    settle_all=_settle_all,
    read_slip_odds=_read_slip_odds,
    update_slip_stake=_update_slip_stake,
    parse_placement_response=parse_placement_response,
    parse_placement_status=parse_placement_status,
)
```

**`data/mirror_intel/pinnacle.json`** — remove `"autonomous_placement": true`. Keep markets + navigation as-is.

### Deleted

- `arnold/mirror/workflows/pinnacle.py` (521-line dedicated class).
- `arnold/tests/workflows/test_pinnacle_slip.py` — entire file removed. The DOM-click / stake-input tests (~18 of 22) target dedicated-class internals and are not worth porting. The 4 placement-parser tests are re-implemented (with adjusted imports) in the new file below.

### Added

- `arnold/tests/workflows/test_pinnacle_strategy.py` — ported placement-parser tests:
  - `test_parse_placement_status_success` (`wagerNumber` present)
  - `test_parse_placement_status_failure` (error key present, no wagerNumber)
  - `test_parse_placement_status_max_stake` (`limits[].type == "maxRiskStake"`)
  - `test_parse_placement_response` (returns `wagerNumber` / `betId` / `None`)

  Imports module-level functions:
  ```python
  from arnold.mirror.workflows.strategies.pinnacle import (
      parse_placement_status,
      parse_placement_response,
  )
  ```

## Risks & mitigations

1. **Login signal divergence.** Strategy uses `localStorage['Main:User'].loggedIn + token`. Dedicated class used DOM text scrape for `LOG IN`/`DEPONERA`. If Pinnacle ever clears localStorage on logout but DOM stays stale, strategy says "logged in" while user isn't. *Mitigation:* `_sync_balance` returns `-1.0` if `Main:User.balance` isn't a number, so a stale-localStorage-but-logged-out state drops balance to -1. Already guarded.

2. **Arb anchor stake re-writes.** Section 3 plumbing is new code. If wiring is wrong, `arb_runner.py:522, 659` silently no-op. *Mitigation:* smoke-test one low-stake arb run before trusting Pinnacle as anchor.

3. **Placement XHR interception path.** `provider_runner.py:230` and `arb_runner.py:615, 676` call `workflow.parse_placement_status(body)`. After migration this routes through GenericWorkflow's instance method → strategy's module-level function. *Mitigation:* the placement URL pattern (registered in `browser.py` for Pinnacle) must still be active. Confirm during live test.

4. **Stale slip selection.** `Main:Betslip` may already have a stale selection from a prior bet. Pinnacle's React replaces (not appends) for single-selection mode, but this needs live verification. *Mitigation:* add a TODO in `_prep_betslip` to clear `localStorage["Main:Betslip"]` if the live test reveals stacking behavior.

## Verification

1. `pytest arnold/tests/workflows/test_pinnacle_strategy.py` — 4 ported tests pass.
2. `pytest arnold/tests/test_arb_runner_green_gate.py` — Pinnacle anchor mock survives the GenericWorkflow indirection.
3. Smoke: `python -c "from arnold.mirror.workflows import get_workflow; w = get_workflow('pinnacle'); print(type(w).__name__, w.strategy is not None)"` — expect `GenericWorkflow True`.
4. Manual live (local arnold): open Pinnacle in mirror, log in, place one low-stake value bet, click CONFIRM on pinnacle.se, verify bet recorded to DB.
5. Manual live: trigger settle_all from UI, verify recent settled bets reconcile.
6. Manual live: one arb run with Pinnacle anchor, verify `update_slip_stake` re-writes when prices move (log line `[Arb:pinnacle]`).

## Out of scope

- API placement (B2 explicitly chose mirror).
- Improving Pinnacle DOM history scrape (good enough as fallback).
- Discovery JSON automation for new providers.
- Polymarket strategy changes — Section 3 plumbing additions are inert for Polymarket since its strategy doesn't set the four new fields.
