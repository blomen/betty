# Scanner Trust Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close five trust gaps in the opportunity scanner + batch builder so the UI cannot surface phantom edges that the user would place real money against. Builds on yesterday's period-scope fix with the same pattern: refuse misaligned comparisons rather than patch symptoms.

**Architecture:** Five independent fixes, each in the layer that matches its bug class. (1) Currency-aware stake annotation on BatchBet for cross-currency clarity. (2) Enhanced home/away inversion detector with new `Event.home_away_validated` column. (3) Spread implied-probability disagreement gate in scanner.group_odds. (4) Upper-bound edge sanity gate in batch_builder. (5) Stale opportunity cleanup hooked into analyzer.

**Tech Stack:** Python 3.10+, SQLAlchemy ORM, PostgreSQL 16, pytest. Tests run with `c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest backend/tests/`. Deploys via `bash /opt/arnold/scripts/server-deploy.sh rebuild backend`.

**Spec:** [docs/superpowers/specs/2026-05-26-scanner-trust-gates-design.md](../specs/2026-05-26-scanner-trust-gates-design.md)

---

## File Structure

```
backend/src/
├── analysis/scanner.py             MODIFY: spread disagreement gate (Task 3) + currency annotation on ArbOpportunity legs (Task 1)
├── pipeline/storage.py             MODIFY: detect_and_fix_inversion enhanced (Task 2) + home_away_validated flag set
├── db/models.py                    MODIFY: Event.home_away_validated column + migration (Task 2)
├── services/batch_builder.py       MODIFY: BatchBet.stake_currency/stake_native fields (Task 1) + upper-bound edge gate (Task 4)
├── services/opportunity_service.py MODIFY: call stale cleanup before scan (Task 5)
├── analysis/analyzer.py            MODIFY: emit cleanup metrics in run summary (Task 5)
└── api/__init__.py                 MODIFY: /health/extraction reports 4 new counters (Tasks 1-5)

backend/tests/
├── analysis/test_spread_disagreement_gate.py   NEW (Task 3)
├── analysis/test_arb_currency_annotation.py    NEW (Task 1)
├── pipeline/test_inversion_enhanced.py         NEW (Task 2)
├── services/test_batch_phantom_gate.py         NEW (Task 4)
└── test_opportunity_cleanup.py                 NEW (Task 5)
```

Each fix is independently committable. Recommended commit order matches task order — earlier fixes don't depend on later ones.

---

## Task 1: Currency annotation on arb legs + batch stake

**Files:**
- Modify: `backend/src/services/batch_builder.py` (`BatchBet` dataclass around line 56; `_build_value_bet` around line 462; `_build_arb_bet` if exists)
- Modify: `backend/src/analysis/scanner.py` (arb leg construction around line 893)
- Test: `backend/tests/analysis/test_arb_currency_annotation.py` (NEW)

**Why:** The arb math `1/sum(1/odds)` is currency-independent — decimal odds ratios cancel. But `BatchBet.stake` is a single float with no currency annotation. For cross-currency arbs (cloudbet USDC vs pinnacle SEK), the user needs to know each leg's stake in the provider's native currency, not just the SEK equivalent. Without explicit `currency` + `stake_native` fields, the frontend/placement layer can silently place the wrong amount on the wrong leg, destroying the arb.

- [ ] **Step 1: Write failing test**

Create `backend/tests/analysis/test_arb_currency_annotation.py`:

```python
"""Arb legs and BatchBets carry currency + native-stake annotations so
cross-currency arbs (cloudbet USDC vs pinnacle SEK) can be placed correctly."""
from __future__ import annotations

from dataclasses import fields

from src.services.batch_builder import BatchBet


def test_batchbet_has_currency_fields():
    """BatchBet must expose stake_currency + stake_native alongside stake (SEK)."""
    field_names = {f.name for f in fields(BatchBet)}
    assert "stake_currency" in field_names, "BatchBet missing stake_currency field"
    assert "stake_native" in field_names, "BatchBet missing stake_native field"


def test_arb_leg_has_currency_field():
    """Each leg dict in ArbOpportunity.legs must include 'currency'."""
    # Smoke-test the dict shape via a real Pinnacle/Cloudbet arb fixture
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from src.analysis.scanner import OpportunityScanner

    scanner = OpportunityScanner(session=None)
    odds_pinnacle = SimpleNamespace(
        provider_id="pinnacle", market="moneyline", outcome="home",
        odds=2.56, point=None, scope="ft",
        updated_at=datetime.now(timezone.utc), bid=None, ask=None,
    )
    odds_cloudbet = SimpleNamespace(
        provider_id="cloudbet", market="moneyline", outcome="away",
        odds=1.75, point=None, scope="ft",
        updated_at=datetime.now(timezone.utc), bid=None, ask=None,
    )
    event = SimpleNamespace(
        id="evt:test", sport="basketball",
        home_team="A", away_team="B",
        league="Test", start_time=None,
        odds=[odds_pinnacle, odds_cloudbet],
        home_away_validated=True,
    )
    arbs = scanner.scan_arb(events=[event])
    assert arbs, "expected at least one arb in fixture"
    for arb in arbs:
        for leg in arb.legs:
            assert "currency" in leg, f"arb leg missing currency: {leg}"
            assert leg["currency"] in ("SEK", "USDC", "USD", "GBP"), \
                f"unexpected currency: {leg['currency']}"
```

> **Note on `home_away_validated=True`:** Task 2 adds this attribute to `Event`. For this test it's set on the SimpleNamespace fixture; in the real DB it's a column. If running this test BEFORE Task 2 is merged, `getattr(event, "home_away_validated", True)` in scanner will return True (default safe) — adapt the scanner read to use getattr to keep test self-contained.

- [ ] **Step 2: Verify test fails**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/test_arb_currency_annotation.py -v`
Expected: FAIL on `assert "stake_currency" in field_names` and on the arb leg `currency` check.

- [ ] **Step 3: Add currency fields to BatchBet**

Edit `backend/src/services/batch_builder.py`. Locate the `BatchBet` dataclass (around line 55). Find the `stake: float` field (line 74). Add immediately AFTER `expected_profit: float`:

```python
    # Currency annotation — SEK is the bankroll-base and Kelly works in SEK,
    # but cross-currency arbs (cloudbet=USDC, kalshi=USD, polymarket=USDC,
    # smarkets=GBP) require the user to place stake_native at the provider,
    # not stake. Frontend MUST present stake_native + stake_currency.
    stake_currency: str = "SEK"
    stake_native: float = 0.0
```

- [ ] **Step 4: Populate `stake_currency` + `stake_native` in `_build_value_bet`**

In the same file, locate `_build_value_bet` (around line 462). Find the `return BatchBet(...)` call (around line 586). Just before the return, add the conversion using the already-imported `get_exchange_rate`:

```python
        # Convert SEK Kelly stake to provider's native currency. For SEK
        # providers exchange_rate is 1.0 and stake_native == stake.
        from ..config import get_provider_currency
        stake_currency = get_provider_currency(provider_id)
        exchange_rate = get_exchange_rate(provider_id)  # SEK per native unit
        stake_native = round(stake / exchange_rate, 2) if exchange_rate > 0 else stake
```

Then in the `return BatchBet(...)` call, add `stake_currency=stake_currency, stake_native=stake_native,` alongside the existing `stake=stake,` field.

- [ ] **Step 5: Populate fields in any other `return BatchBet(...)` call**

Same file. `grep -n "return BatchBet(" backend/src/services/batch_builder.py` lists all sites. For each one not already covered, repeat the conversion + pass the two new fields. The second known site is around line 735 in a `_relocate_bet` or similar — apply identically.

- [ ] **Step 6: Add `currency` to each leg dict in ArbOpportunity**

Edit `backend/src/analysis/scanner.py`. Locate `_find_arb_in_market` (around line 684). Find the legs construction around line 893 (`legs.append({...})`). Add `"currency": get_provider_currency(data["provider"])` to the dict. Need to import `get_provider_currency` at the top of `scanner.py`:

```python
from ..config import get_provider_currency
```

(Adapt to the existing import group from `..config`.)

The leg dict construction becomes:

```python
            legs.append(
                {
                    "outcome": out,
                    "provider": data["provider"],
                    "odds": data["odds"],
                    "edge_pct": data["edge_pct"],
                    "fair_odds": data["fair_odds"],
                    "stake_pct": stake_pct,
                    "is_sharp": data["is_sharp"],
                    "point": point_by_outcome.get(out),
                    "currency": get_provider_currency(data["provider"]),  # NEW
                }
            )
```

Also do the same for the arb_legs construction further down (around line 942).

- [ ] **Step 7: Run tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/test_arb_currency_annotation.py -v`
Expected: PASS.

- [ ] **Step 8: Run broader existing tests for regression**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/ tests/services/ -v --tb=short`
Expected: All previously-passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add backend/src/services/batch_builder.py backend/src/analysis/scanner.py backend/tests/analysis/test_arb_currency_annotation.py
git commit -m "feat(scanner): annotate arb legs + BatchBet with currency + native stake"
```

---

## Task 2: Enhanced home/away inversion detector + `home_away_validated` flag

**Files:**
- Modify: `backend/src/db/models.py` (`Event` class around line 91; `_run_pg_migrations` around line 1680)
- Modify: `backend/src/pipeline/storage.py` (`detect_and_fix_inversion` line 138)
- Modify: `backend/src/analysis/scanner.py` (`group_odds` filter — skip events where `home_away_validated=False`)
- Test: `backend/tests/pipeline/test_inversion_enhanced.py` (NEW)

**Why:** Current detector only fires when Pinnacle shows a clear favorite (odds ratio > 1.5). Near-coinflip matches like SSG v Samsung (Pinnacle ratio 1.06) slip through and produce phantom spread edges because the soft book's home/away is inverted relative to Pinnacle. Lower the threshold + add a devig-agreement signal + verify-after-swap + drop the soft odds entirely if disagreement persists. Scanner refuses to use unvalidated events.

- [ ] **Step 1: Write failing test**

Create `backend/tests/pipeline/test_inversion_enhanced.py`:

```python
"""Enhanced inversion detection catches near-coinflip inversions and drops
unresolvable mismatches."""
from __future__ import annotations

import pytest


def _odds_pair(provider, home_odds, away_odds, sport="basketball"):
    """Helper to build a synthetic odds pair as it would arrive."""
    return {
        "provider": provider,
        "home_odds": home_odds,
        "away_odds": away_odds,
        "sport": sport,
    }


def test_inversion_caught_at_low_ratio():
    """Pinnacle home@2.0/away@1.85 (ratio 1.08) + soft home@1.85/away@2.0 (inverted)
    should be caught and swapped even though ratio < 1.5."""
    from src.pipeline.storage import _is_inversion_detected
    sharp_home, sharp_away = 2.0, 1.85
    soft_home, soft_away = 1.85, 2.00
    assert _is_inversion_detected(sharp_home, sharp_away, soft_home, soft_away), \
        "inversion at ratio 1.08 must be detected"


def test_no_inversion_when_books_agree():
    """Both books favor the same side — no inversion."""
    from src.pipeline.storage import _is_inversion_detected
    assert not _is_inversion_detected(2.0, 1.85, 2.10, 1.80)


def test_devig_disagreement_triggers_inversion():
    """Even when raw ratio is near 1.0, devig probability disagreement of >25pp
    on home identifies an inversion."""
    from src.pipeline.storage import _is_inversion_detected
    # Sharp: home 2.23, away 2.10 → devig P(home) ≈ 48.5%
    # Soft (inverted): home 2.10, away 2.23 → soft P(home) ≈ 51.5%
    # Difference is only 3pp here, so this should NOT trigger.
    # Now invert further: Sharp home 1.50, away 2.50 → P(home) ≈ 62.5%
    # Soft home 2.50, away 1.50 → soft P(home) ≈ 37.5%
    # Difference 25pp+ — triggers
    assert _is_inversion_detected(1.50, 2.50, 2.50, 1.50)


def test_post_swap_verification_drops_if_still_off():
    """If swap doesn't reconcile (e.g. genuine event mismatch), the soft odds
    should be marked as dropped and event.home_away_validated stays False."""
    from src.pipeline.storage import _validate_post_swap
    # Even after swap, sharp 2.0/1.85 vs soft 1.20/4.50 still disagrees by >15pp
    assert not _validate_post_swap(2.0, 1.85, 4.50, 1.20)


def test_event_marked_validated_when_clean():
    """An event with no inversion + sharp/soft devig agreement should be
    marked home_away_validated=True."""
    from src.pipeline.storage import _validate_post_swap
    assert _validate_post_swap(2.0, 1.85, 2.10, 1.80)
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/pipeline/test_inversion_enhanced.py -v`
Expected: FAIL — `_is_inversion_detected` and `_validate_post_swap` not defined.

- [ ] **Step 3: Add `Event.home_away_validated` column**

Edit `backend/src/db/models.py`. Locate the `Event` class (around line 91). Find a stable insertion point in the column block (after `away_team` or `sport`) and add:

```python
    # Enhanced inversion detection (2026-05-26): set True only when storage
    # has verified the soft book's home/away assignment agrees with Pinnacle
    # (after swap if needed). False means the scanner must skip this event's
    # soft odds because we don't trust the side mapping. Defaults to True so
    # historical events without the check still surface.
    home_away_validated = Column(Boolean, nullable=False, server_default=text("true"), default=True)
```

Make sure `Boolean` is in the imports at the top of `models.py`; `text` from sqlalchemy is needed for `server_default=text("true")` — if not imported, add `from sqlalchemy import text` near the other sqlalchemy imports.

- [ ] **Step 4: Add migration for the new column**

In the same file, locate `_run_pg_migrations` (around line 1680). In the `additions` list, append:

```python
        # 2026-05-26 — enhanced inversion detector flag on events. Default true
        # so historical events without verification stay visible until the next
        # extraction cycle revalidates them.
        ("events", "home_away_validated", "BOOLEAN NOT NULL DEFAULT TRUE"),
```

- [ ] **Step 5: Implement detector helpers + update `detect_and_fix_inversion`**

Edit `backend/src/pipeline/storage.py`. Add new module-level helpers near `detect_and_fix_inversion` (around line 138). Insert BEFORE the existing function:

```python
# Enhanced inversion thresholds (2026-05-26)
INVERSION_RATIO_THRESHOLD = 1.10  # was 1.50 — catches near-coinflip inversions
INVERSION_DEVIG_DISAGREEMENT_PP = 0.25  # 25pp probability disagreement on home outcome
POST_SWAP_DISAGREEMENT_PP = 0.15  # 15pp threshold after attempting swap


def _devig_prob_home(home_odds: float, away_odds: float) -> float:
    """Return de-vigged P(home) for a 2-way market, or 0.5 if odds invalid."""
    if home_odds <= 1 or away_odds <= 1:
        return 0.5
    p_home_raw = 1.0 / home_odds
    p_away_raw = 1.0 / away_odds
    total = p_home_raw + p_away_raw
    if total <= 0:
        return 0.5
    return p_home_raw / total


def _is_inversion_detected(
    sharp_home: float, sharp_away: float,
    soft_home: float, soft_away: float,
) -> bool:
    """Detect home/away inversion using two signals:

    1. Raw odds ratio: if either book has favorite/dog with ratio > 1.10
       and they disagree on which side is favored, that's an inversion.
    2. Devig probability: if devigged P(home) differs by > 25pp between
       books, that's an inversion regardless of raw ratio.
    """
    if any(o <= 1 for o in (sharp_home, sharp_away, soft_home, soft_away)):
        return False

    # Signal 1: ratio + favorite-side disagreement
    sharp_ratio = max(sharp_home, sharp_away) / min(sharp_home, sharp_away)
    soft_ratio = max(soft_home, soft_away) / min(soft_home, soft_away)
    sharp_home_favored = sharp_home < sharp_away
    soft_home_favored = soft_home < soft_away
    if sharp_ratio > INVERSION_RATIO_THRESHOLD and sharp_home_favored != soft_home_favored:
        return True

    # Signal 2: devig probability disagreement
    sharp_p_home = _devig_prob_home(sharp_home, sharp_away)
    soft_p_home = _devig_prob_home(soft_home, soft_away)
    if abs(sharp_p_home - soft_p_home) > INVERSION_DEVIG_DISAGREEMENT_PP:
        return True

    return False


def _validate_post_swap(
    sharp_home: float, sharp_away: float,
    soft_home: float, soft_away: float,
) -> bool:
    """After swap (or with no swap needed), confirm the books agree within
    POST_SWAP_DISAGREEMENT_PP. Returns True if validated."""
    if any(o <= 1 for o in (sharp_home, sharp_away, soft_home, soft_away)):
        return False
    sharp_p = _devig_prob_home(sharp_home, sharp_away)
    soft_p = _devig_prob_home(soft_home, soft_away)
    return abs(sharp_p - soft_p) <= POST_SWAP_DISAGREEMENT_PP
```

Then update `detect_and_fix_inversion` to use these helpers. Read the existing body via `Read backend/src/pipeline/storage.py offset=138 limit=80` first, then replace the existing detection logic (the part comparing `home_odds`/`away_odds` to sharp) with a call to `_is_inversion_detected`. After deciding swap-needed and applying it, call `_validate_post_swap` on the post-swap odds. Return values:
- `(swap_needed: bool, validated: bool)` — both flags. Callers update `Event.home_away_validated = validated`.

> **Compatibility note:** the existing `detect_and_fix_inversion` returns a single bool. Update both the function signature AND all callers (`grep -n "detect_and_fix_inversion" backend/src/` to find them). Callers should pass the `validated` flag through to the Event update path.

- [ ] **Step 6: Update scanner to skip unvalidated events**

Edit `backend/src/analysis/scanner.py`. Locate `group_odds` (around line 1110). At the top of the method body (before `if exclude_providers is None`), add:

```python
        # 2026-05-26: skip events where the home/away inversion check did
        # not resolve cleanly. Defaults to True for historical rows that
        # haven't been revalidated yet.
        if not getattr(event, "home_away_validated", True):
            logger.debug(
                "home_away_unvalidated: drop %s (sport=%s)",
                event.id, getattr(event, "sport", None),
            )
            return {}
```

- [ ] **Step 7: Run inversion tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/pipeline/test_inversion_enhanced.py -v`
Expected: PASS.

- [ ] **Step 8: Run broader existing tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/pipeline/ tests/analysis/ -v --tb=short`
Expected: previously-passing tests still pass. If existing tests expected `detect_and_fix_inversion` to return a bool (instead of tuple), update them.

- [ ] **Step 9: Commit**

```bash
git add backend/src/db/models.py backend/src/pipeline/storage.py backend/src/analysis/scanner.py backend/tests/pipeline/test_inversion_enhanced.py
git commit -m "feat(inversion): enhanced detector + Event.home_away_validated flag"
```

---

## Task 3: Spread implied-probability disagreement gate

**Files:**
- Modify: `backend/src/analysis/scanner.py` (`find_value_in_market` around line 1278; `_find_arb_in_market` around line 684)
- Test: `backend/tests/analysis/test_spread_disagreement_gate.py` (NEW)

**Why:** Quarter-handicap convention mismatches (Unibet `home @ +0.5 @ 1.91` is the +0/+0.5 quarter; Pinnacle `home @ +0.5 @ 1.24` is the full +0.5 line) bucket different bets at `spread_0.5`. The symptom: devigged probabilities for the same nominal outcome disagree by 24+pp. Use the symptom as the refusal trigger — no per-provider convention research needed.

- [ ] **Step 1: Write failing test**

Create `backend/tests/analysis/test_spread_disagreement_gate.py`:

```python
"""Scanner refuses to emit value bets for spread buckets where soft and sharp
devigged probabilities disagree by >30pp on the same outcome."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.analysis.scanner import OpportunityScanner


def _odds(provider, market, outcome, value, point=None, scope="ft"):
    return SimpleNamespace(
        provider_id=provider, market=market, outcome=outcome, odds=value,
        point=point, scope=scope,
        updated_at=datetime.now(timezone.utc), bid=None, ask=None,
    )


def _event(sport, odds_list, **kwargs):
    base = dict(
        id="evt:t1", sport=sport,
        home_team="A", away_team="B",
        league="Test", start_time=None,
        home_away_validated=True,
        odds=odds_list,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_spread_disagreement_drops_phantom_bucket():
    """Unibet home@+0.5@1.91 (P~52%) vs Pinnacle devig home@+0.5 (P~76%) →
    refuse the bucket entirely."""
    scanner = OpportunityScanner(session=None)
    ev = _event("football", [
        # Pinnacle: home @ +0.5 @ 1.24, away @ -0.5 @ 3.96 (complement)
        _odds("pinnacle", "spread", "home", 1.24, 0.5, "ft"),
        _odds("pinnacle", "spread", "away", 3.96, -0.5, "ft"),
        # Unibet: home @ +0.5 @ 1.91 (different bet via quarter handicap)
        _odds("unibet", "spread", "home", 1.91, 0.5, "ft"),
    ])
    values = scanner.scan_value(events=[ev])
    spread_values = [v for v in values if v.market == "spread"]
    assert not spread_values, \
        f"expected zero value bets from disagreement bucket, got {spread_values}"


def test_spread_small_disagreement_emits_normally():
    """5pp disagreement is within tolerance — value bet emitted."""
    scanner = OpportunityScanner(session=None)
    ev = _event("football", [
        # Pinnacle: home@+0.5@1.45 → P(home)≈69%
        _odds("pinnacle", "spread", "home", 1.45, 0.5, "ft"),
        _odds("pinnacle", "spread", "away", 2.80, -0.5, "ft"),
        # Unibet: home@+0.5@1.50 → P(home)≈67% (2pp lower — well within tolerance)
        _odds("unibet", "spread", "home", 1.50, 0.5, "ft"),
    ])
    values = scanner.scan_value(events=[ev])
    spread_values = [v for v in values if v.market == "spread"]
    assert spread_values, "small disagreement should still emit"


def test_total_market_not_affected_by_spread_gate():
    """The gate applies only to spread markets, not total."""
    scanner = OpportunityScanner(session=None)
    ev = _event("football", [
        _odds("pinnacle", "total", "over", 1.85, 2.5, "ft"),
        _odds("pinnacle", "total", "under", 2.00, 2.5, "ft"),
        _odds("unibet", "total", "over", 2.00, 2.5, "ft"),
    ])
    values = scanner.scan_value(events=[ev])
    total_values = [v for v in values if v.market == "total"]
    assert total_values, "totals must still emit"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/test_spread_disagreement_gate.py -v`
Expected: FAIL on `test_spread_disagreement_drops_phantom_bucket` (Unibet's bucket emits a 45% value bet today).

- [ ] **Step 3: Add the gate to scanner**

Edit `backend/src/analysis/scanner.py`. Add a module constant near the existing MAX_* constants (around line 41):

```python
# 2026-05-26: spread quarter-handicap convention mismatches surface as
# devigged-probability disagreement of >30pp on the same nominal outcome.
# Refuse value bets for soft providers in such buckets — they're pricing a
# different bet than Pinnacle, not offering value.
SPREAD_DISAGREEMENT_MAX_PP = 0.30
```

Add a helper method on `OpportunityScanner`:

```python
    def _drop_spread_disagreement_providers(
        self,
        market: str,
        odds_by_outcome: dict[str, list[dict]],
        pinnacle_market: dict[str, float],
    ) -> None:
        """For each outcome in a spread market, drop soft providers whose
        devigged probability disagrees with Pinnacle's by >SPREAD_DISAGREEMENT_MAX_PP.
        Mutates odds_by_outcome in place. Non-spread markets are no-ops."""
        if not market.startswith("spread"):
            return

        # Pinnacle devig per outcome
        total_pinnacle_inv = sum(1.0 / o for o in pinnacle_market.values() if o > 1)
        if total_pinnacle_inv <= 0:
            return
        pinnacle_devig = {
            out: (1.0 / odds) / total_pinnacle_inv
            for out, odds in pinnacle_market.items() if odds > 1
        }

        # For each soft provider, compute their devig per outcome and compare
        soft_devig: dict[str, dict[str, float]] = {}
        for outcome, providers in odds_by_outcome.items():
            for po in providers:
                if po["provider"] == "pinnacle" or po["provider"] in SIGNAL_ONLY_PROVIDERS:
                    continue
                soft_devig.setdefault(po["provider"], {})[outcome] = 1.0 / po["odds"]

        # Normalize each soft provider's devig (sum to 1)
        for prov, devig in soft_devig.items():
            total = sum(devig.values())
            if total <= 0:
                continue
            for outcome in list(devig.keys()):
                devig[outcome] = devig[outcome] / total

        # Drop providers whose devig differs from Pinnacle by > threshold on ANY outcome
        dropped = set()
        for prov, devig in soft_devig.items():
            for outcome, soft_p in devig.items():
                pinnacle_p = pinnacle_devig.get(outcome)
                if pinnacle_p is None:
                    continue
                if abs(soft_p - pinnacle_p) > SPREAD_DISAGREEMENT_MAX_PP:
                    dropped.add(prov)
                    logger.debug(
                        "spread_disagreement: drop %s from %s (outcome=%s, soft_p=%.2f, sharp_p=%.2f)",
                        prov, market, outcome, soft_p, pinnacle_p,
                    )
                    break

        # Mutate odds_by_outcome to remove dropped providers
        for outcome in list(odds_by_outcome.keys()):
            odds_by_outcome[outcome] = [
                po for po in odds_by_outcome[outcome] if po["provider"] not in dropped
            ]
            if not odds_by_outcome[outcome]:
                del odds_by_outcome[outcome]
```

- [ ] **Step 4: Wire the gate into find_value_in_market + _find_arb_in_market**

In the same file, locate `find_value_in_market` (around line 1278). After `pinnacle_market = self._build_pinnacle_market(odds_by_outcome)` and `self._enrich_spread_complement(...)`, add:

```python
        # 2026-05-26: spread disagreement gate
        self._drop_spread_disagreement_providers(market, odds_by_outcome, pinnacle_market)
```

Do the same in `_find_arb_in_market` (around line 684) after the equivalent pinnacle_market + enrich calls.

- [ ] **Step 5: Run the spread disagreement tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/test_spread_disagreement_gate.py -v`
Expected: PASS.

- [ ] **Step 6: Run broader scanner tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/analysis/ -v --tb=short`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/src/analysis/scanner.py backend/tests/analysis/test_spread_disagreement_gate.py
git commit -m "feat(scanner): spread devig-disagreement gate (>30pp drops bucket)"
```

---

## Task 4: Upper-bound edge sanity gate in batch_builder

**Files:**
- Modify: `backend/src/services/batch_builder.py` (`_build_value_bet` around line 462)
- Test: `backend/tests/services/test_batch_phantom_gate.py` (NEW)

**Why:** Backstop. Even after Tasks 1–3 + 5, a new bug class could surface a phantom 45% edge. This gate ensures the user CANNOT place a bet on a value > 10% or arb > 5% — those are virtually always bugs, not real edges. Logs dropped opps for visibility.

- [ ] **Step 1: Write failing test**

Create `backend/tests/services/test_batch_phantom_gate.py`:

```python
"""Batch builder refuses to surface value bets at edge > MAX_BATCH_VALUE_EDGE_PCT
or arbs at profit > MAX_BATCH_ARB_PROFIT_PCT — these are virtually always
phantom edges, not real value."""
from __future__ import annotations

import pytest


def test_constants_exposed():
    """Module-level constants must exist so production logging refers to them."""
    from src.services.batch_builder import MAX_BATCH_VALUE_EDGE_PCT, MAX_BATCH_ARB_PROFIT_PCT
    assert MAX_BATCH_VALUE_EDGE_PCT > 0
    assert MAX_BATCH_ARB_PROFIT_PCT > 0


def test_value_bet_above_cap_returns_none():
    """An opportunity with edge_pct > MAX_BATCH_VALUE_EDGE_PCT must be skipped."""
    from src.services.batch_builder import MAX_BATCH_VALUE_EDGE_PCT, _is_phantom_value_bet

    # Below cap → not phantom
    assert not _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT - 0.1)
    # At cap → not phantom (boundary inclusive)
    assert not _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT)
    # Above cap → phantom
    assert _is_phantom_value_bet(edge_pct=MAX_BATCH_VALUE_EDGE_PCT + 0.01)
    assert _is_phantom_value_bet(edge_pct=45.0)


def test_arb_above_cap_returns_none():
    """An arb with profit_pct > MAX_BATCH_ARB_PROFIT_PCT must be skipped."""
    from src.services.batch_builder import MAX_BATCH_ARB_PROFIT_PCT, _is_phantom_arb

    assert not _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT - 0.1)
    assert not _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT)
    assert _is_phantom_arb(profit_pct=MAX_BATCH_ARB_PROFIT_PCT + 0.01)
    assert _is_phantom_arb(profit_pct=8.0)
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/services/test_batch_phantom_gate.py -v`
Expected: FAIL — neither constant nor helpers exist.

- [ ] **Step 3: Add module constants + helpers**

Edit `backend/src/services/batch_builder.py`. Locate `MAX_TTK_HOURS = 168.0` (around line 52). Add immediately AFTER:

```python
# 2026-05-26: upper-bound sanity gates. Even after scope/inversion/spread
# disagreement fixes, anything above these is virtually always a bug —
# currency mismatch in stake sizing, novel scope/handicap bug, fuzzy-match
# false positive, etc. Refuse to surface and log so we can monitor.
MAX_BATCH_VALUE_EDGE_PCT = 10.0
MAX_BATCH_ARB_PROFIT_PCT = 5.0


def _is_phantom_value_bet(edge_pct: float) -> bool:
    """Return True if a value bet's edge is above the sanity cap."""
    return edge_pct > MAX_BATCH_VALUE_EDGE_PCT


def _is_phantom_arb(profit_pct: float) -> bool:
    """Return True if an arb's guaranteed profit is above the sanity cap."""
    return profit_pct > MAX_BATCH_ARB_PROFIT_PCT
```

- [ ] **Step 4: Wire the gate into `_build_value_bet`**

In the same file, locate `_build_value_bet` (around line 462). Find the per-provider min-edge check (around line 501-503):

```python
        if (opp.edge_pct or 0.0) < prov_min_edge_pct:
            return None
```

Add the upper-bound check immediately AFTER:

```python
        # 2026-05-26: upper-bound sanity gate
        if _is_phantom_value_bet(opp.edge_pct or 0.0):
            logger.warning(
                "[suspect_phantom] dropping value bet edge=%.2f%% > cap=%.2f%% "
                "(event=%s market=%s provider=%s)",
                opp.edge_pct, MAX_BATCH_VALUE_EDGE_PCT,
                opp.event_id, opp.market, opp.provider1_id,
            )
            return None
```

If there is a separate arb-building function (search via `grep -n "def _build_arb\|profit_pct" backend/src/services/batch_builder.py`), add the parallel arb check there:

```python
        if _is_phantom_arb(opp.profit_pct or 0.0):
            logger.warning(
                "[suspect_phantom] dropping arb profit=%.2f%% > cap=%.2f%% "
                "(event=%s market=%s)",
                opp.profit_pct, MAX_BATCH_ARB_PROFIT_PCT,
                opp.event_id, opp.market,
            )
            return None
```

If arbs are constructed inline within `build()` rather than via a helper, add the check at the equivalent point.

- [ ] **Step 5: Run the phantom gate tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/services/test_batch_phantom_gate.py -v`
Expected: PASS.

- [ ] **Step 6: Run broader batch tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/services/ -v --tb=short`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/batch_builder.py backend/tests/services/test_batch_phantom_gate.py
git commit -m "feat(batch): upper-bound sanity gates (10% value, 5% arb)"
```

---

## Task 5: Stale opportunity cleanup

**Files:**
- Modify: `backend/src/services/opportunity_service.py` OR `backend/src/analysis/analyzer.py` (location of scan entry point)
- Test: `backend/tests/test_opportunity_cleanup.py` (NEW)

**Why:** Off-season opportunities (NFL during summer) carry `is_active=true` for weeks because no fresh scan touches them. Cron-style cleanup based on event start_time + odds age.

- [ ] **Step 1: Locate the scan entry point**

Run: `grep -n "def scan\|def run_scan\|def analyze" backend/src/services/opportunity_service.py backend/src/analysis/analyzer.py 2>/dev/null | head -10`

Identify the function that runs after each extraction cycle.

- [ ] **Step 2: Write failing test**

Create `backend/tests/test_opportunity_cleanup.py`:

```python
"""Stale opportunity cleanup: expire opps for events past start time + 1h,
or where the underlying odds are >4h old."""
from __future__ import annotations

import pytest

# The cleanup is implemented as a function that takes a session and returns
# a count of rows expired. Test it in isolation.


def test_cleanup_function_exists():
    from src.services.opportunity_service import cleanup_stale_opportunities  # noqa
    assert callable(cleanup_stale_opportunities)


def test_cleanup_expires_post_start_event(monkeypatch):
    """Opportunities for events with start_time < NOW() - 1h should be
    set inactive."""
    # Real DB tests live in integration suite — this confirms the SQL shape
    # by inspecting the function source for the expected predicate.
    import inspect
    from src.services import opportunity_service
    src = inspect.getsource(opportunity_service.cleanup_stale_opportunities)
    assert "start_time" in src, "cleanup must filter on event start_time"
    assert "is_active" in src, "cleanup must set is_active=false"


def test_cleanup_expires_stale_odds(monkeypatch):
    import inspect
    from src.services import opportunity_service
    src = inspect.getsource(opportunity_service.cleanup_stale_opportunities)
    assert "updated_at" in src, "cleanup must reference odds.updated_at"
```

> **Note:** This is a smoke-test approach. A real integration test would seed a Postgres test DB and verify rows. The plan defers full DB testing to the prod smoke step.

- [ ] **Step 3: Verify test fails**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/test_opportunity_cleanup.py -v`
Expected: FAIL — `cleanup_stale_opportunities` not defined.

- [ ] **Step 4: Add the cleanup function**

Edit `backend/src/services/opportunity_service.py`. Add a new function (place near the top of the module, after imports):

```python
def cleanup_stale_opportunities(session) -> dict:
    """Expire opportunities whose underlying data is stale.

    Two rules:
      1. Hard expire: event start_time has passed by > 1h
      2. Soft expire: the opp's provider1 odds row hasn't been updated in > 4h
         (e.g. off-season events whose providers don't ship fresh data)

    Returns {'expired_post_start': N, 'expired_stale_odds': M} for /health/extraction.
    """
    from sqlalchemy import text
    expired_post_start = session.execute(text("""
        UPDATE opportunities SET is_active = false
        WHERE is_active = true
          AND event_id IN (
            SELECT id FROM events WHERE start_time < NOW() - INTERVAL '1 hour'
          )
    """)).rowcount or 0

    expired_stale_odds = session.execute(text("""
        UPDATE opportunities SET is_active = false
        WHERE is_active = true
          AND id IN (
            SELECT op.id FROM opportunities op
            JOIN odds o
              ON o.event_id = op.event_id
              AND o.provider_id = op.provider1_id
            WHERE op.is_active = true
              AND o.updated_at < NOW() - INTERVAL '4 hours'
          )
    """)).rowcount or 0

    session.commit()
    return {
        "expired_post_start": int(expired_post_start),
        "expired_stale_odds": int(expired_stale_odds),
    }
```

- [ ] **Step 5: Hook cleanup into the scan flow**

In the same file (or `analyzer.py` — whichever runs after each extraction cycle, per Step 1), add a call to `cleanup_stale_opportunities(session)` BEFORE the new scan inserts. Log the returned counts at INFO level:

```python
        cleanup_counts = cleanup_stale_opportunities(session)
        logger.info(
            "[opp_cleanup] expired %d post-start, %d stale-odds",
            cleanup_counts["expired_post_start"],
            cleanup_counts["expired_stale_odds"],
        )
```

- [ ] **Step 6: Run cleanup tests**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/test_opportunity_cleanup.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/opportunity_service.py backend/tests/test_opportunity_cleanup.py
git commit -m "feat(opps): periodic cleanup of stale opportunities"
```

---

## Task 6: Health endpoint surfaces all new counters

**Files:**
- Modify: `backend/src/api/__init__.py` (extend `/health/extraction` response with 4 new counters)

**Why:** Operational visibility for the four new gates from Tasks 2-5. Yesterday's `unscannable_markets` set the pattern; extend it.

- [ ] **Step 1: Locate the /health/extraction handler**

Run: `grep -nA 5 "def health_extraction\|/health/extraction" backend/src/api/__init__.py | head -30`

- [ ] **Step 2: Add new counters to the response**

Edit `backend/src/api/__init__.py`. Locate the `return {...}` of `health_extraction()` (around line 530). Extend with counts from the new gates:

```python
            from sqlalchemy import text
            # Snapshots for trust-gate visibility (2026-05-26)
            n_phantom_value = db.execute(text("""
                SELECT COUNT(*) FROM opportunities
                WHERE is_active=true AND type='value' AND edge_pct > 10
            """)).scalar() or 0
            n_phantom_arb = db.execute(text("""
                SELECT COUNT(*) FROM opportunities
                WHERE is_active=true AND type='arb' AND profit_pct > 5
            """)).scalar() or 0
            n_unvalidated_events = db.execute(text("""
                SELECT COUNT(*) FROM events
                WHERE home_away_validated = false
                  AND start_time > NOW() AND start_time < NOW() + INTERVAL '24 hours'
            """)).scalar() or 0
            n_active_total = db.execute(text("""
                SELECT COUNT(*) FROM opportunities WHERE is_active=true
            """)).scalar() or 0

            response["phantom_value_count"] = int(n_phantom_value)
            response["phantom_arb_count"] = int(n_phantom_arb)
            response["unvalidated_events_24h"] = int(n_unvalidated_events)
            response["active_opportunities_total"] = int(n_active_total)
            response["trust_gates_status"] = (
                "WARNING" if (n_phantom_value + n_phantom_arb) > 5 else "OK"
            )
```

> If the existing handler builds `response` differently, adapt. The four counters and the status are the load-bearing additions.

- [ ] **Step 3: Smoke check the endpoint locally** (skip if no local backend)

If running a local backend, hit `/health/extraction` and verify the new keys are present. Otherwise defer to post-deploy verification.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat(health): report 4 trust-gate counters on /health/extraction"
```

---

## Task 7: Pre-deploy validation + deploy

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m pytest tests/ --tb=line -q`
Expected: All previously-passing tests still pass. The Task 7 baseline from yesterday was 40 pre-existing failures unrelated to scope; same baseline expected here. New scope/trust-gate tests pass.

- [ ] **Step 2: Lint check**

Run: `cd backend && c:/Users/rasmu/arnold/.venv/Scripts/python.exe -m ruff check src/`
Expected: No errors.

- [ ] **Step 3: Confirm no deploy in flight**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status && pgrep -fa 'server-deploy.sh' | grep -v $$"`
Expected: "No active deploy" and no other `server-deploy.sh` processes.

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(scanner): trust gates — currency / inversion / spread / phantom / cleanup" --body "$(cat <<'EOF'
## Summary
Five trust gates closing the gaps audited 2026-05-26:

1. Currency annotation on arb legs + BatchBet for cross-currency clarity
2. Enhanced home/away inversion detector (lower threshold + devig agreement + post-swap verification + home_away_validated event flag)
3. Spread implied-probability disagreement gate (>30pp drops bucket)
4. Upper-bound edge sanity gate in batch_builder (10% value cap, 5% arb cap)
5. Stale opportunity cleanup (post-start + stale-odds-4h)

Plus /health/extraction extended with 4 new trust-gate counters.

## Why
Audit found 427 active "value bets" at edge ≥15% (almost all phantom), 64 cross-currency arbs without conversion clarity, and at least one home/away inverted spread (SSG v Samsung KBO) that the existing inversion detector missed because Pinnacle's ratio was 1.06 (below the old 1.5 threshold).

## Test plan
- [ ] All new tests pass
- [ ] Pre-existing test failure count unchanged
- [ ] Post-deploy: phantom_value_count drops to ~0
- [ ] Post-deploy: phantom_arb_count drops to ~0
- [ ] Post-deploy: SSG v Samsung KBO produces zero spread opportunities
- [ ] Post-deploy: /health/extraction returns all 4 new counters

## Refs
- Spec: docs/superpowers/specs/2026-05-26-scanner-trust-gates-design.md
EOF
)"
```

- [ ] **Step 5: Deploy**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh pull && bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`
Expected: Deploy completes, backend healthy within 2 min.

- [ ] **Step 6: Verify migration ran**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d arnold -c \"SELECT column_name FROM information_schema.columns WHERE table_name='events' AND column_name='home_away_validated';\""`
Expected: Returns the column name.

- [ ] **Step 7: Verify phantom counts drop**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d arnold -c \"SELECT type, COUNT(*) FILTER (WHERE edge_pct > 10 OR profit_pct > 5) AS phantoms, COUNT(*) AS total FROM opportunities WHERE is_active=true GROUP BY type;\""`
Expected: phantoms column should be near 0 (allowing for in-flight scans not yet rerun). Total dropped meaningfully vs the pre-deploy baseline (427 spread value bets at ≥15% should be 0 within minutes).

- [ ] **Step 8: Verify SSG v Samsung specifically**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml exec -T postgres psql -U arnold -d arnold -c \"SELECT COUNT(*) AS ssg_samsung_spread_opps FROM opportunities op JOIN events e ON e.id=op.event_id WHERE (e.home_team ILIKE '%ssg%' OR e.away_team ILIKE '%samsung%') AND op.market='spread' AND op.is_active=true;\""`
Expected: 0 (the audit-trigger opportunity is gone).

- [ ] **Step 9: Verify health endpoint**

Run: `ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/extraction" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{k}: {d.get(k)}') for k in ('phantom_value_count','phantom_arb_count','unvalidated_events_24h','active_opportunities_total','trust_gates_status','unscannable_markets')]"`
Expected: All keys present. `trust_gates_status` = OK or WARNING with reason.

- [ ] **Step 10: Tail logs for unexpected drops**

Run: `ssh root@148.251.40.251 "docker compose -f /opt/arnold/docker-compose.yml logs backend --tail=200 | grep -E 'suspect_phantom|spread_disagreement|home_away_unvalidated' | head -30"`
Expected: some `spread_disagreement` drops on football events (expected), some `home_away_unvalidated` early on as new events scanned, near-zero `suspect_phantom` (those should be caught by the upstream gates).

---

## Self-Review

**Spec coverage:**
- ✅ Fix 1 (currency-aware): Task 1 (narrowed from "math" to "annotation/sizing" per deeper investigation)
- ✅ Fix 2 (inversion detector): Task 2
- ✅ Fix 3 (spread disagreement gate): Task 3
- ✅ Fix 4 (upper-bound edge gate): Task 4
- ✅ Fix 5 (stale cleanup): Task 5
- ✅ Health counters: Task 6
- ✅ Deploy: Task 7

**Placeholder scan:** No "TBD" or open-ended steps. A few "adapt to actual code shape" notes (Task 4 arb path, Task 5 entry point location, Task 6 response building) — these are necessary because the file structure may vary; the helpers and tests are fully specified.

**Type consistency:** `BatchBet.stake_currency: str`, `BatchBet.stake_native: float` (Task 1); `Event.home_away_validated: Boolean` (Task 2); `_is_phantom_value_bet(edge_pct: float) -> bool` (Task 4); `cleanup_stale_opportunities(session) -> dict` (Task 5). All consistent across tasks that reference them.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-scanner-trust-gates.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks
2. **Inline Execution** — execute in this session with batched checkpoints

Which approach?
