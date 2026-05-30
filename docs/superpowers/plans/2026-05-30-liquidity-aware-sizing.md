# Liquidity-Aware Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap prediction-market value-bet stakes by available order-book depth (`depth_usd`), so a Kelly stake never exceeds a configurable fraction (default 50%) of the visible ask-side depth — protecting Polymarket/Kalshi fills from slippage/partial fills.

**Architecture:** A pure helper `liquidity_capped_stake()` in `stake_calculator.py` (keeping the MC-tuned `calculate_stake` untouched), gated by a new per-provider `liquidity_fraction`. The scanner threads `depth_usd` into the value path and applies the cap (plus a min-stake re-check) after Kelly sizing. Server-side only, no migration.

**Tech Stack:** Python 3.12 / SQLAlchemy / pytest. SQLite in tests.

**Spec:** `docs/superpowers/specs/2026-05-30-liquidity-aware-sizing-design.md`

**Scope:** Prediction-market CLOB books only (polymarket, kalshi — providers with `depth_usd`). Pinnacle/cloudbet are NOT gated. Frontend rendering of the `was_liquidity_capped` flag is a deferred follow-up — this plan only puts the flag on `ValueBet` (available to the API payload/logs).

**Deploy note:** Touches `backend/` (`bankroll/stake_calculator.py`, `analysis/scanner.py`, `analysis/value.py`) → backend rebuild. Behavior change confined to prediction-market value-bet sizing (only ever lowers a stake). No migration. Deploy once at the end.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/src/bankroll/stake_calculator.py` | `liquidity_fraction` field on `ProviderStakeProfile` + `liquidity_capped_stake()` pure helper | Modify |
| `backend/src/analysis/value.py` | `depth_usd` + `was_liquidity_capped` + `liquidity_cap_reason` fields on `ValueBet` | Modify |
| `backend/src/analysis/scanner.py` | Thread `depth_usd` into po-dicts + value loop; apply cap + min re-check in `scan_value_with_stakes` | Modify |
| `backend/tests/bankroll/test_liquidity_cap.py` | Unit tests for the pure helper | Create |
| `backend/tests/analysis/test_liquidity_cap_integration.py` | Integration test for the scanner path | Create |

---

## Task 1: Liquidity cap — config field + pure helper

**Files:**
- Modify: `backend/src/bankroll/stake_calculator.py` (`ProviderStakeProfile` dataclass ~line 71-82; `PROVIDER_STAKE_PROFILES` ~line 106-119; append helper after `provider_min_edge_pct`)
- Test: `backend/tests/bankroll/test_liquidity_cap.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/bankroll/test_liquidity_cap.py`:

```python
"""Tests for the prediction-market liquidity stake cap."""

from src.bankroll.stake_calculator import liquidity_capped_stake


def test_gated_provider_over_cap_is_capped():
    # polymarket fraction 0.5, depth $400, rate 10.5 -> cap = 0.5*400*10.5 = 2100 SEK.
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="polymarket", depth_usd=400.0, exchange_rate_sek=10.5
    )
    assert capped == 2100.0
    assert was_capped is True
    assert reason is not None and "liquidity" in reason


def test_gated_provider_under_cap_unchanged():
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=1000.0, provider_id="polymarket", depth_usd=400.0, exchange_rate_sek=10.5
    )
    assert capped == 1000.0
    assert was_capped is False
    assert reason is None


def test_kalshi_is_gated():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="kalshi", depth_usd=100.0, exchange_rate_sek=10.5
    )
    # cap = 0.5*100*10.5 = 525
    assert capped == 525.0
    assert was_capped is True


def test_ungated_provider_never_capped():
    # pinnacle has liquidity_fraction None -> no cap even with depth present.
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="pinnacle", depth_usd=10.0, exchange_rate_sek=1.0
    )
    assert capped == 5000.0
    assert was_capped is False
    assert reason is None


def test_cloudbet_ungated():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="cloudbet", depth_usd=10.0, exchange_rate_sek=10.5
    )
    assert capped == 5000.0
    assert was_capped is False


def test_null_depth_no_cap():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="polymarket", depth_usd=None, exchange_rate_sek=10.5
    )
    assert capped == 5000.0
    assert was_capped is False


def test_zero_or_negative_depth_no_cap():
    for bad in (0.0, -5.0):
        capped, was_capped, _ = liquidity_capped_stake(
            stake_sek=5000.0, provider_id="polymarket", depth_usd=bad, exchange_rate_sek=10.5
        )
        assert capped == 5000.0
        assert was_capped is False


def test_unknown_provider_no_cap():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="betsson", depth_usd=10.0, exchange_rate_sek=1.0
    )
    assert capped == 5000.0
    assert was_capped is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/bankroll/test_liquidity_cap.py -v`
Expected: FAIL with `ImportError: cannot import name 'liquidity_capped_stake'`.

- [ ] **Step 3: Add the config field**

In `backend/src/bankroll/stake_calculator.py`, add a field to the `ProviderStakeProfile` dataclass (after the `min_edge_pct: float = 1.0` line):

```python
    # Fraction of visible ask-side depth (depth_usd) a single value-bet stake
    # may consume on a CLOB prediction market. None = provider not liquidity-
    # gated (pinnacle uses max_stake separately; cloudbet has no order book).
    liquidity_fraction: float | None = None
```

Then set it on the two CLOB providers in `PROVIDER_STAKE_PROFILES` — update the `polymarket` and `kalshi` entries to include `liquidity_fraction=0.5`:

```python
    "polymarket": ProviderStakeProfile(
        fee_rate=0.0, min_stake_native=1.0, currency="USDC", min_edge_pct=5.0, liquidity_fraction=0.5
    ),
    "kalshi": ProviderStakeProfile(
        fee_rate=0.0, min_stake_native=1.0, currency="USD", min_edge_pct=3.0, liquidity_fraction=0.5
    ),
```

(Leave `pinnacle` and `cloudbet` entries as-is — they default to `liquidity_fraction=None`.)

- [ ] **Step 4: Implement the helper**

Append to `backend/src/bankroll/stake_calculator.py` (after `provider_min_edge_pct`, ~line 154):

```python
def liquidity_capped_stake(
    stake_sek: float,
    provider_id: str,
    depth_usd: float | None,
    exchange_rate_sek: float,
) -> tuple[float, bool, str | None]:
    """Cap a value-bet stake by available CLOB order-book depth.

    Only applies to liquidity-gated providers (those with a `liquidity_fraction`
    in PROVIDER_STAKE_PROFILES — polymarket, kalshi). For ungated providers, or
    when depth is unknown/non-positive, the stake is returned unchanged.

    Args:
        stake_sek: Kelly stake in SEK (Betty's base currency).
        provider_id: the bet provider.
        depth_usd: ask-side order-book depth in USD (Odds.depth_usd), or None.
        exchange_rate_sek: SEK per 1 native unit (≈10.5 for USDC/USD books).

    Returns:
        (capped_stake_sek, was_capped, reason). Currency note: depth_usd is USD;
        USDC≈USD for depth purposes, so cap_sek = fraction × depth_usd × rate.
    """
    profile = PROVIDER_STAKE_PROFILES.get(provider_id)
    fraction = profile.liquidity_fraction if profile else None
    if fraction is None or depth_usd is None or depth_usd <= 0:
        return stake_sek, False, None

    cap_sek = fraction * depth_usd * (exchange_rate_sek or 1.0)
    if stake_sek <= cap_sek:
        return stake_sek, False, None
    return cap_sek, True, f"liquidity cap: {fraction:.0%} of ${depth_usd:.0f} depth"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/bankroll/test_liquidity_cap.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/bankroll/stake_calculator.py backend/tests/bankroll/test_liquidity_cap.py
git commit -m "feat(bankroll): liquidity_capped_stake helper + per-provider liquidity_fraction"
```

---

## Task 2: Thread depth into the value path + apply the cap

**Files:**
- Modify: `backend/src/analysis/value.py` (`ValueBet` dataclass — add 3 fields after `consensus_lean`, ~line 84)
- Modify: `backend/src/analysis/scanner.py` (po-dict builder ~line 1391-1401; value loop ~line 1834-1840; `scan_value_with_stakes` ~line 289-339; imports ~line 19-20)
- Test: `backend/tests/analysis/test_liquidity_cap_integration.py` (create)

- [ ] **Step 1: Add fields to `ValueBet`**

In `backend/src/analysis/value.py`, add after the `consensus_lean: dict | None = None` field (~line 84, before the `@property expected_value`):

```python
    # Order-book depth (Odds.depth_usd, USD) for the bet provider — CLOB books
    # only (polymarket/kalshi); None elsewhere. Drives the liquidity stake cap.
    depth_usd: float | None = None
    # Set when the recommended stake was reduced to fit available depth.
    was_liquidity_capped: bool = False
    liquidity_cap_reason: str | None = None
```

- [ ] **Step 2: Write the failing integration test**

Create `backend/tests/analysis/test_liquidity_cap_integration.py`:

```python
"""Integration: liquidity cap applied in the value-bet stake path."""

from src.analysis.value import ValueBet
from src.bankroll.stake_calculator import liquidity_capped_stake, provider_min_stake_sek


def test_value_bet_carries_depth_and_cap_fields():
    # The dataclass must carry the new fields with safe defaults.
    vb = ValueBet(
        event_id="e", market="moneyline", outcome="home", provider="polymarket",
        provider_odds=2.0, fair_odds=1.9, fair_probability=0.53, edge_pct=5.0,
    )
    assert vb.depth_usd is None
    assert vb.was_liquidity_capped is False
    assert vb.liquidity_cap_reason is None
    vb.depth_usd = 400.0
    assert vb.depth_usd == 400.0


def test_cap_then_min_floor_skips_subminimum_stake():
    # Mirrors the scanner logic: cap a stake, then re-check the provider floor.
    # polymarket cap with tiny depth $2 -> cap = 0.5*2*10.5 = 10.5 SEK.
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=500.0, provider_id="polymarket", depth_usd=2.0, exchange_rate_sek=10.5
    )
    assert was_capped is True
    assert capped == 10.5
    # provider_min_stake_sek(polymarket, rate=10.5, fallback=25) = 1.0 native * 10.5 = 10.5
    floor = provider_min_stake_sek("polymarket", 10.5, 25.0)
    # capped (10.5) is NOT below floor (10.5) here; make a stricter depth to force skip.
    capped2, _, _ = liquidity_capped_stake(
        stake_sek=500.0, provider_id="polymarket", depth_usd=1.0, exchange_rate_sek=10.5
    )
    assert capped2 == 5.25  # 0.5*1*10.5
    assert capped2 < floor  # below the $1 native floor -> scanner will skip
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/analysis/test_liquidity_cap_integration.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword` is NOT expected (we don't pass new kwargs); it fails on `AttributeError: 'ValueBet' object has no attribute 'depth_usd'` until Step 1 is done. (If Step 1 already applied, the first test passes; the second test only uses the Task-1 helper and should pass once Task 1 is merged. Run after Step 4 to confirm the scanner wiring.)

- [ ] **Step 4: Thread `depth_usd` through the scanner**

(a) In `backend/src/analysis/scanner.py`, add `depth_usd` to the grouped po-dict builder. Change the dict appended at ~line 1391-1401 to include depth:

```python
            grouped[market_key][outcome].append(
                {
                    "provider": odds.provider_id,
                    "odds": odds.odds,
                    "point": odds.point,
                    "updated_at": odds.updated_at,
                    "bid": odds.bid,
                    "ask": odds.ask,
                    "max_stake": odds.max_stake,
                    "depth_usd": odds.depth_usd,
                }
            )
```

(b) In the value loop, after the block that sets `vb.prob_sum` / `vb.pinnacle_overround` / `vb.odds_snapshot` (~line 1835-1840), add:

```python
                    vb.depth_usd = po.get("depth_usd")
```

(c) Add `get_exchange_rate` to the config import at the top of `scanner.py` (currently `from ..config import get_provider_currency` on line 20):

```python
from ..config import get_exchange_rate, get_provider_currency
```

(d) Add the stake-calculator helper imports. Find the existing import `from ..bankroll.stake_calculator import StakeCalculator` (line 19) and extend it:

```python
from ..bankroll.stake_calculator import (
    StakeCalculator,
    liquidity_capped_stake,
    provider_min_stake_sek,
)
```

- [ ] **Step 5: Apply the cap in `scan_value_with_stakes`**

In `backend/src/analysis/scanner.py`, inside `scan_value_with_stakes`, immediately AFTER the `result = stake_calculator.calculate(...)` call (~line 291-297) and BEFORE the key-number annotation, add:

```python
            # Liquidity cap (prediction markets): never stake more than a
            # fraction of visible CLOB depth. Only lowers a stake. Applied
            # after Kelly so the MC-tuned sizing stays untouched.
            final_stake = result.stake
            was_liq_capped = False
            liq_reason = None
            liq_skip_reason = result.skip_reason
            if final_stake > 0:
                final_stake, was_liq_capped, liq_reason = liquidity_capped_stake(
                    final_stake, vb.provider, vb.depth_usd, get_exchange_rate(vb.provider)
                )
                if was_liq_capped:
                    floor_sek = provider_min_stake_sek(
                        vb.provider, get_exchange_rate(vb.provider), stake_calculator.min_stake
                    )
                    if final_stake < floor_sek:
                        final_stake = 0.0
                        liq_skip_reason = (
                            f"liquidity-capped stake below min ({floor_sek:.0f} kr): {liq_reason}"
                        )
```

Then update the `enriched = ValueBet(...)` constructor (~line 327-351) to use the capped values: change `recommended_stake=result.stake if result.stake > 0 else None` to use `final_stake`, change `skip_reason=result.skip_reason` to `skip_reason=liq_skip_reason`, and add the three new fields. The relevant lines become:

```python
                recommended_stake=final_stake if final_stake > 0 else None,
                kelly_fraction=result.kelly_fraction,
                is_high_confidence=is_high_confidence,
                skip_reason=liq_skip_reason,
```

and add (alongside the other keyword args, e.g. after `consensus_lean=...`):

```python
                depth_usd=vb.depth_usd,
                was_liquidity_capped=was_liq_capped,
                liquidity_cap_reason=liq_reason,
```

- [ ] **Step 6: Run the integration test + full value/bankroll suites**

Run: `cd backend && python -m pytest tests/analysis/test_liquidity_cap_integration.py tests/bankroll/test_liquidity_cap.py -v`
Expected: PASS.

Regression: `cd backend && python -m pytest tests/analysis/ tests/bankroll/ -q`
Expected: no NEW failures vs baseline. (Note: the repo has ~24 PRE-EXISTING failures on `main` unrelated to this work — e.g. `test_health_detector`, `test_allocator_envelope`, `test_kalshi_parser`, a `test_stake_calculator` SEK test. Confirm your change adds no failures beyond those: compare the failing-test set to a clean checkout if unsure. `test_liquidity_cap*` must pass.)

- [ ] **Step 7: Commit**

```bash
git add backend/src/analysis/value.py backend/src/analysis/scanner.py backend/tests/analysis/test_liquidity_cap_integration.py
git commit -m "feat(scanner): cap prediction-market value-bet stakes by order-book depth"
```

---

## Task 3: Deploy + verify

**Files:** none (operational).

- [ ] **Step 1: Confirm clean state + merge to main** (only when the user approves)

Work is on a feature branch; finish via PR (CI runs ruff + frontend-typecheck). NOTE: `backend-tests` CI is currently RED on `main` from a pre-existing unrelated collection error (`tests/mirror/test_sharp_refresh_dispatch.py` imports `local`); your PR doesn't change that. Verify your feature tests pass locally before merge.

- [ ] **Step 2: Deploy backend**

```bash
ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh' || echo free"   # ensure no deploy running
ssh root@148.251.40.251 "bash /opt/betty/backend/scripts/server-deploy.sh rebuild backend"
```
Expected: deploy completes, `/health` responds.

- [ ] **Step 3: Verify the cap is live**

After a scan cycle, confirm prediction-market value bets show capped stakes when depth is thin (check logs / the value-bet API payload for `was_liquidity_capped`). No migration to verify (no schema change).

---

## Self-Review

**Spec coverage:**
- Component 1 (`liquidity_capped_stake` pure helper) → Task 1 ✓
- Component 2 (config `liquidity_fraction` on poly/kalshi) → Task 1 Step 3 ✓
- Component 3 (integration: thread depth, apply cap, re-check min, surface flag) → Task 2 ✓
- Currency isolation (depth_usd USD × exchange_rate_sek) → Task 1 helper ✓
- Edge cases (ungated/null/≤0 depth → no cap; capped-below-min → skip) → Task 1 tests + Task 2 Step 5 ✓
- Out of scope (pinnacle/cloudbet ungated; frontend UI deferred) → respected ✓

**Placeholder scan:** No TBD/TODO. The regression step references pre-existing failures explicitly (named) rather than vaguely.

**Type consistency:** `liquidity_capped_stake` returns `(float, bool, str|None)` — used identically in Task 1 tests and Task 2 Step 5. `ValueBet` new fields (`depth_usd`, `was_liquidity_capped`, `liquidity_cap_reason`) defined in Task 2 Step 1 and set in Step 5. `provider_min_stake_sek(provider_id, exchange_rate, fallback)` and `stake_calculator.min_stake` match the existing signatures in `stake_calculator.py`.

**Verification done during exploration:** po-dict builder carries `bid`/`ask` but not `depth_usd` (Step 4a adds it); `Odds.depth_usd` column exists; `scan_value_with_stakes` builds `enriched` from `result.stake`; `StakeCalculator` exposes `.min_stake`.
