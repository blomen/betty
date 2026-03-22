# Boost Pipeline Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 gaps in the boost/specials pipeline: synthesize missing original odds, add cache TTL, remove dead ML features, and wire up boost outcome resolution for ML training.

**Architecture:** Each gap is an independent change touching 1-2 files. Changes 1 and 2 modify the enrichment pipeline. Change 3 is a pure cleanup. Change 4 adds a new function + hook.

**Tech Stack:** Python, SQLAlchemy, pytest

**Spec:** `docs/superpowers/specs/2026-03-22-boost-pipeline-gaps-design.md`

---

### Task 1: Reorder `enrich_specials_with_ev()` — match events before computing edge

**Files:**
- Modify: `backend/src/analysis/ev_enrichment.py:183-200`
- Test: `backend/tests/test_ev_enrichment.py` (create)

- [ ] **Step 1: Write test for reordered execution**

Create `backend/tests/test_ev_enrichment.py`:

```python
"""Test EV enrichment for odds boosts."""
from datetime import datetime, timezone, timedelta
from src.db.models import Event


def _make_event(db_session, event_id="evt-1", home="Arsenal", away="Chelsea", hours_from_now=24):
    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    ev = Event(id=event_id, home_team=home, away_team=away, sport="football",
               start_time=start)
    db_session.add(ev)
    db_session.flush()
    return ev


def test_enrich_matches_before_edge(db_session):
    """Event matching must run BEFORE edge calculation so Pinnacle proxy can fill original_odds."""
    _make_event(db_session)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 2.50,
        "original_odds": 2.00,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    # After reorder: matched_event_id should be set AND edge_pct computed
    assert result[0].get("matched_event_id") is not None
    assert result[0].get("edge_pct") == 25.0  # (2.50/2.00 - 1) * 100
```

- [ ] **Step 2: Run test to verify it passes with current code**

Run: `cd backend && python -m pytest tests/test_ev_enrichment.py::test_enrich_matches_before_edge -v`
Expected: PASS (current code computes edge first, but result is the same since original_odds is provided)

- [ ] **Step 3: Reorder the function**

In `backend/src/analysis/ev_enrichment.py`, modify `enrich_specials_with_ev()` (lines 183-200):

```python
def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """Compute boost edge and cross-reference boost events with Events table."""
    # 1. Cross-reference with Events table FIRST (sets matched_event_id)
    matched = _match_boosts_to_events(specials, db)
    logger.info(f"Event matching: {matched}/{len(specials)} boosts matched to events")

    # 2. Boost edge: boosted_odds / original_odds - 1
    count = 0
    for s in specials:
        boosted = s.get("boosted_odds")
        original = s.get("original_odds")
        if boosted and original and original > 1.0:
            s["edge_pct"] = round((boosted / original - 1) * 100, 2)
            s["is_positive_ev"] = s["edge_pct"] > 0
            count += 1
    logger.info(f"Boost edge: {count}/{len(specials)} computed (boosted/original)")

    return specials
```

- [ ] **Step 4: Run test to verify it still passes**

Run: `cd backend && python -m pytest tests/test_ev_enrichment.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/analysis/ev_enrichment.py backend/tests/test_ev_enrichment.py
git commit -m "refactor(ev_enrichment): reorder to match events before computing edge"
```

---

### Task 2: Synthesize original odds from Pinnacle for Kambi/BetConstruct

**Files:**
- Modify: `backend/src/analysis/ev_enrichment.py`
- Test: `backend/tests/test_ev_enrichment.py`

- [ ] **Step 1: Write failing test for Pinnacle proxy**

Add to `backend/tests/test_ev_enrichment.py`:

```python
from src.db.models import Odds


def _add_pinnacle_odds(db_session, event_id="evt-1"):
    """Add Pinnacle 1x2 odds for an event."""
    from src.db.models import Provider
    db_session.add(Provider(id="pinnacle", name="Pinnacle"))
    for outcome, odds_val in [("home", 2.10), ("draw", 3.40), ("away", 3.50)]:
        db_session.add(Odds(
            event_id=event_id, provider_id="pinnacle",
            market="1x2", outcome=outcome, odds=odds_val,
        ))
    db_session.flush()


def test_pinnacle_proxy_fills_original_odds(db_session):
    """Kambi boost with no original_odds gets Pinnacle fair odds as proxy."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 3.00,
        "original_odds": None,  # Kambi — no pre-boost odds
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    s = result[0]
    assert s.get("matched_event_id") == ev.id
    # Should have synthesized original_odds from Pinnacle de-vigged home odds
    assert s.get("original_odds") is not None
    assert s["original_odds"] > 2.0  # De-vigged > raw odds
    assert s.get("edge_pct") is not None


def test_pinnacle_proxy_skips_combos(db_session):
    """Combo boosts (multi-leg) should NOT get Pinnacle proxy."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win & over 2.5 goals",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 5.00,
        "original_odds": None,
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    # Combo — original_odds should remain None
    assert result[0].get("original_odds") is None


def test_pinnacle_proxy_skips_when_already_has_odds(db_session):
    """Boosts that already have original_odds should not be overwritten."""
    ev = _make_event(db_session)
    _add_pinnacle_odds(db_session, ev.id)
    specials = [{
        "title": "Arsenal to win",
        "event": "Arsenal vs Chelsea",
        "boosted_odds": 3.00,
        "original_odds": 2.50,  # Already has original
        "sport": "football",
    }]
    from src.analysis.ev_enrichment import enrich_specials_with_ev
    result = enrich_specials_with_ev(specials, db_session)
    assert result[0]["original_odds"] == 2.50  # Unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_ev_enrichment.py::test_pinnacle_proxy_fills_original_odds -v`
Expected: FAIL — `original_odds` is still None

- [ ] **Step 3: Implement Pinnacle proxy**

Add to `backend/src/analysis/ev_enrichment.py`, after the `_match_boosts_to_events` import block, add the import and new function:

```python
from ..db.models import Event, Odds, SpecialOdds
from .devig import get_fair_odds_for_outcome
```

Then add the `_fill_pinnacle_proxy_odds` function before `enrich_specials_with_ev`:

```python
def _fill_pinnacle_proxy_odds(specials: list[dict], db: Session) -> int:
    """Synthesize original_odds from Pinnacle fair odds for boosts missing them.

    Only applies to single-leg match winner bets where the boost event
    matched a Pinnacle event and we can identify the outcome from the title.
    """
    from ..analysis.llm_enrichment import _detect_legs_from_title

    # Collect matched event IDs that need proxy odds
    needs_proxy = [
        s for s in specials
        if not s.get("original_odds")
        and s.get("matched_event_id")
        and _detect_legs_from_title(s.get("title", "")) == 1
    ]
    if not needs_proxy:
        return 0

    # Load events for team name lookup
    event_ids = list({s["matched_event_id"] for s in needs_proxy})
    events = db.query(Event).filter(Event.id.in_(event_ids)).all()
    event_map = {ev.id: ev for ev in events}

    # Load Pinnacle 1x2/moneyline odds for these events
    pinnacle_odds = (
        db.query(Odds)
        .filter(
            Odds.event_id.in_(event_ids),
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline"]),
        )
        .all()
    )
    # Group by event_id -> {outcome: odds}
    odds_by_event: dict[str, dict[str, float]] = {}
    for o in pinnacle_odds:
        odds_by_event.setdefault(o.event_id, {})[o.outcome] = o.odds

    count = 0
    for s in needs_proxy:
        eid = s["matched_event_id"]
        market_odds = odds_by_event.get(eid)
        if not market_odds or len(market_odds) < 2:
            continue

        ev = event_map.get(eid)
        if not ev or not ev.home_team or not ev.away_team:
            continue

        # Heuristic: which outcome does this boost title reference?
        title_lower = s.get("title", "").lower()
        home_lower = ev.home_team.lower()
        away_lower = ev.away_team.lower()

        outcome = None
        if home_lower in title_lower and away_lower not in title_lower:
            outcome = "home"
        elif away_lower in title_lower and home_lower not in title_lower:
            outcome = "away"
        # Skip ambiguous (both teams in title) or no team match

        if not outcome or outcome not in market_odds:
            continue

        fair_odds = get_fair_odds_for_outcome(outcome, market_odds)
        if fair_odds and fair_odds > 1.0:
            s["original_odds"] = round(fair_odds, 3)
            count += 1

    return count
```

Then update `enrich_specials_with_ev` to call it between matching and edge calc:

```python
def enrich_specials_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """Compute boost edge and cross-reference boost events with Events table."""
    # 1. Cross-reference with Events table FIRST (sets matched_event_id)
    matched = _match_boosts_to_events(specials, db)
    logger.info(f"Event matching: {matched}/{len(specials)} boosts matched to events")

    # 2. Synthesize original_odds from Pinnacle for providers that don't expose them
    proxy_count = _fill_pinnacle_proxy_odds(specials, db)
    if proxy_count:
        logger.info(f"Pinnacle proxy: {proxy_count} boosts got synthesized original_odds")

    # 3. Boost edge: boosted_odds / original_odds - 1
    count = 0
    for s in specials:
        boosted = s.get("boosted_odds")
        original = s.get("original_odds")
        if boosted and original and original > 1.0:
            s["edge_pct"] = round((boosted / original - 1) * 100, 2)
            s["is_positive_ev"] = s["edge_pct"] > 0
            count += 1
    logger.info(f"Boost edge: {count}/{len(specials)} computed (boosted/original)")

    return specials
```

- [ ] **Step 4: Fix imports**

Update the imports at the top of `ev_enrichment.py`:

```python
from ..db.models import Event, Odds, SpecialOdds
from .devig import get_fair_odds_for_outcome
```

Note: `SpecialOdds` is already imported. Just add `Odds` and the devig import.

- [ ] **Step 5: Run all tests**

Run: `cd backend && python -m pytest tests/test_ev_enrichment.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/ev_enrichment.py backend/tests/test_ev_enrichment.py
git commit -m "feat(ev_enrichment): synthesize original_odds from Pinnacle for Kambi/BetConstruct"
```

---

### Task 3: Add 48h cache TTL to LLM enrichment

**Files:**
- Modify: `backend/src/analysis/llm_enrichment.py:27-33, 66-81, 132-160`
- Test: `backend/tests/test_llm_cache_ttl.py` (create)

- [ ] **Step 1: Write failing test for cache TTL**

Create `backend/tests/test_llm_cache_ttl.py`:

```python
"""Test LLM cache TTL expiry."""
from datetime import datetime, timezone, timedelta
from src.db.models import LlmBoostCache


def _add_cache_entry(db_session, key="abc123", hours_ago=0):
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    db_session.add(LlmBoostCache(
        cache_key=key,
        title="Arsenal to win",
        boosted_odds=2.50,
        llm_title="Arsenal wins",
        llm_probability=0.45,
        llm_fair_odds=2.222,
        llm_confidence="medium",
        llm_reasoning="Strong home form",
        created_at=created.isoformat(),
        last_used_at=created.isoformat(),
    ))
    db_session.flush()


def test_fresh_cache_is_carried_forward(db_session):
    """Cache entries < 48h old should be carried forward."""
    _add_cache_entry(db_session, key="abc123", hours_ago=10)
    from src.analysis.llm_enrichment import _load_cache_from_db, _carry_forward_from_cache
    cache = _load_cache_from_db(db_session)
    specials = [{"title": "Arsenal to win", "boosted_odds": 2.50, "event": ""}]
    # Patch _cache_key to return our known key
    from src.analysis.llm_enrichment import _cache_key
    key = _cache_key("Arsenal to win", 2.50, "")
    # Re-add with correct key
    db_session.query(LlmBoostCache).delete()
    _add_cache_entry(db_session, key=key, hours_ago=10)
    cache = _load_cache_from_db(db_session)
    count, used_keys = _carry_forward_from_cache(specials, cache)
    assert count == 1
    assert specials[0]["llm_probability"] == 0.45


def test_stale_cache_is_not_carried_forward(db_session):
    """Cache entries > 48h old should NOT be carried forward."""
    from src.analysis.llm_enrichment import _cache_key, _load_cache_from_db, _carry_forward_from_cache
    key = _cache_key("Arsenal to win", 2.50, "")
    _add_cache_entry(db_session, key=key, hours_ago=72)
    cache = _load_cache_from_db(db_session)
    specials = [{"title": "Arsenal to win", "boosted_odds": 2.50, "event": ""}]
    count, used_keys = _carry_forward_from_cache(specials, cache)
    assert count == 0
    assert specials[0].get("llm_probability") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llm_cache_ttl.py::test_stale_cache_is_not_carried_forward -v`
Expected: FAIL — stale entry is still carried forward (no TTL check exists)

- [ ] **Step 3: Add TTL constant and update `_load_cache_from_db`**

In `backend/src/analysis/llm_enrichment.py`, add constant after line 32:

```python
CACHE_TTL_HOURS = 48
```

Update `_load_cache_from_db()` to also load `created_at`:

```python
def _load_cache_from_db(db: Session) -> dict[str, dict]:
    """Load ALL LLM results from the persistent llm_boost_cache table."""
    from src.db.models import LlmBoostCache
    rows = db.query(LlmBoostCache).all()
    cache = {}
    for r in rows:
        cache[r.cache_key] = {
            "llm_title": r.llm_title or "",
            "llm_probability": r.llm_probability,
            "llm_fair_odds": r.llm_fair_odds,
            "llm_reasoning": r.llm_reasoning,
            "llm_confidence": r.llm_confidence,
            "llm_event_time": getattr(r, "llm_event_time", None),
            "created_at": r.created_at,
        }
    logger.debug(f"Loaded {len(cache)} LLM results from persistent cache")
    return cache
```

- [ ] **Step 4: Add TTL check in `_carry_forward_from_cache`**

In `_carry_forward_from_cache()`, after `if prev and prev.get("llm_probability"):`, add the TTL check:

```python
def _carry_forward_from_cache(specials: list[dict], cache: dict[str, dict]) -> tuple[int, list[str]]:
    """Apply cached LLM data to matching specials. Returns (count, list of used keys)."""
    count = 0
    used_keys = []
    now = datetime.now(timezone.utc)
    for s in specials:
        key = _cache_key(s.get("title", ""), s.get("boosted_odds", 0), s.get("event", ""))
        prev = cache.get(key)
        if prev and prev.get("llm_probability"):
            # TTL check — skip stale entries so they get re-researched
            created_str = prev.get("created_at")
            if created_str:
                try:
                    created_dt = datetime.fromisoformat(created_str)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now - created_dt).total_seconds() / 3600
                    if age_hours > CACHE_TTL_HOURS:
                        continue
                except (ValueError, TypeError):
                    pass  # Can't parse — carry forward anyway

            probability = prev["llm_probability"]
            fair_odds = round(1 / probability, 3) if probability > 0 else None
            boosted_odds = s.get("boosted_odds", 0)

            s["llm_title"] = prev.get("llm_title", "")
            s["llm_probability"] = probability
            s["llm_fair_odds"] = fair_odds
            s["llm_reasoning"] = prev.get("llm_reasoning", "")
            s["llm_confidence"] = prev.get("llm_confidence", "low")
            # Recompute edge from current boosted_odds (may have changed)
            if fair_odds and fair_odds > 1.0 and boosted_odds > 1.0:
                s["llm_edge_pct"] = round((boosted_odds / fair_odds - 1) * 100, 2)
            # Apply bookmaker-anchor sanity check to cached results too
            _apply_bookmaker_anchor(s)
            # Apply LLM event_time if scraped event_time is missing
            llm_et = prev.get("llm_event_time")
            if llm_et and not s.get("event_time"):
                s["event_time"] = llm_et
            count += 1
            used_keys.append(key)
    return count, used_keys
```

- [ ] **Step 5: Run all tests**

Run: `cd backend && python -m pytest tests/test_llm_cache_ttl.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/llm_enrichment.py backend/tests/test_llm_cache_ttl.py
git commit -m "feat(llm_enrichment): add 48h cache TTL for LLM boost results"
```

---

### Task 4: Remove dead ML features (`brave_results_count`, `legs_matched_ratio`)

**Files:**
- Modify: `backend/src/ml/features/boost_features.py`
- Modify: `backend/src/ml/models/boost_calibrator.py:16-25`
- Test: `backend/tests/test_boost_features.py` (create)

- [ ] **Step 1: Write test for updated feature extraction**

Create `backend/tests/test_boost_features.py`:

```python
"""Test boost feature extraction after dead feature removal."""


def test_extract_boost_features_returns_17_features():
    from src.ml.features.boost_features import extract_boost_features
    features = extract_boost_features(
        llm_raw_probability=0.45,
        llm_confidence=1,
        boost_type="single",
        sport="football",
        league="Premier League",
        num_legs=1,
        has_pinnacle_match=True,
        pinnacle_implied_prob=0.40,
        original_odds=2.50,
        boosted_odds=3.00,
        provider="unibet",
    )
    assert len(features) == 17
    assert "brave_results_count" not in features
    assert "legs_matched_ratio" not in features
    assert "llm_raw_probability" in features
    assert features["boost_margin"] == (3.00 - 2.50) / 2.50


def test_feature_names_match_extraction():
    """FEATURE_NAMES in calibrator must match keys from extract_boost_features."""
    from src.ml.features.boost_features import extract_boost_features
    from src.ml.models.boost_calibrator import FEATURE_NAMES
    features = extract_boost_features(
        llm_raw_probability=0.5, llm_confidence=1,
        boost_type="single", sport="football", league="",
        num_legs=1, has_pinnacle_match=False, pinnacle_implied_prob=None,
        original_odds=2.0, boosted_odds=2.5, provider="test",
    )
    assert set(FEATURE_NAMES) == set(features.keys())
    assert len(FEATURE_NAMES) == 17
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_boost_features.py -v`
Expected: FAIL — feature count is 19, and dead features are present

- [ ] **Step 3: Update `boost_features.py`**

Replace the full file `backend/src/ml/features/boost_features.py`:

```python
"""Extract features for M4 LLM Boost Calibrator.

Calibrates the LLM's probability output based on historical accuracy patterns.
The LLM does all research — this model only adjusts the probability.
"""

SPORT_ENCODING = {
    "football": 0, "basketball": 1, "tennis": 2, "ice_hockey": 3,
    "american_football": 4, "baseball": 5, "mma": 6, "esports": 7,
    "handball": 8, "volleyball": 9,
}


def extract_boost_features(
    llm_raw_probability: float, llm_confidence: int,
    boost_type: str, sport: str, league: str,
    num_legs: int, has_pinnacle_match: bool,
    pinnacle_implied_prob: float | None,
    original_odds: float, boosted_odds: float,
    provider: str, hours_to_event: float = 0.0,
    llm_reasoning_length: int = 0,
    day_of_week: int = 0,
) -> dict:
    boost_margin = (boosted_odds - original_odds) / original_odds if original_odds > 0 else 0

    keyword_anytime_scorer = 1 if "anytime" in (boost_type or "").lower() else 0
    keyword_both_teams = 1 if "both teams" in (boost_type or "").lower() else 0
    keyword_over = 1 if "over" in (boost_type or "").lower() else 0

    return {
        "llm_raw_probability": llm_raw_probability,
        "llm_confidence": llm_confidence,
        "boost_type_single": 1 if boost_type == "single" else 0,
        "boost_type_combo": 1 if "combo" in boost_type or "leg" in boost_type else 0,
        "sport": SPORT_ENCODING.get(sport, len(SPORT_ENCODING)),
        "num_legs": num_legs,
        "has_pinnacle_match": int(has_pinnacle_match),
        "pinnacle_implied_prob": pinnacle_implied_prob or 0.0,
        "original_odds": original_odds,
        "boosted_odds": boosted_odds,
        "boost_margin": boost_margin,
        "hours_to_event": hours_to_event,
        "llm_reasoning_length": llm_reasoning_length,
        "keyword_anytime_scorer": keyword_anytime_scorer,
        "keyword_both_teams": keyword_both_teams,
        "keyword_over": keyword_over,
        "day_of_week": day_of_week,
    }
```

- [ ] **Step 4: Update `FEATURE_NAMES` in `boost_calibrator.py`**

In `backend/src/ml/models/boost_calibrator.py`, replace `FEATURE_NAMES` (lines 16-25):

```python
FEATURE_NAMES = [
    "llm_raw_probability", "llm_confidence",
    "boost_type_single", "boost_type_combo", "sport",
    "num_legs", "has_pinnacle_match", "pinnacle_implied_prob",
    "original_odds", "boosted_odds",
    "boost_margin", "hours_to_event", "llm_reasoning_length",
    "keyword_anytime_scorer", "keyword_both_teams", "keyword_over",
    "day_of_week",
]
```

- [ ] **Step 5: Add model version check to `predict()`**

In `backend/src/ml/models/boost_calibrator.py`, add a version check. Find the `predict` method and add a feature count check. Also update the `train` method to store the feature count:

In `train()`, update the joblib dump to include feature count:

```python
            joblib.dump({
                "isotonic_model": self.isotonic_model,
                "lgbm_model": self.lgbm_model,
                "feature_names": self.feature_names,
                "feature_count": len(self.feature_names),
                "task": "calibration",
            }, file_path)
```

Add a `load` method or update any existing model loading. Since loading happens in `predictor.py`, add a guard in `predict()`:

```python
    def predict(self, features: dict) -> float | None:
        if self.isotonic_model is None:
            return None
        # Version check: feature count must match
        if len(self.feature_names) != len(FEATURE_NAMES):
            logger.warning(
                f"Boost calibrator feature mismatch: model has {len(self.feature_names)}, "
                f"code expects {len(FEATURE_NAMES)} — discarding model"
            )
            self.isotonic_model = None
            self.lgbm_model = None
            self.feature_names = FEATURE_NAMES
            return None
        try:
            if self.lgbm_model is not None:
                X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="X does not have valid feature names")
                    proba = self.lgbm_model.predict_proba(X)
                return float(proba[0][1])
            llm_prob = features.get("llm_raw_probability", 0.5)
            calibrated = self.isotonic_model.predict([llm_prob])
            return float(calibrated[0])
        except Exception as e:
            logger.warning(f"Boost calibration failed: {e}")
            return None
```

- [ ] **Step 6: Run all tests**

Run: `cd backend && python -m pytest tests/test_boost_features.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/ml/features/boost_features.py backend/src/ml/models/boost_calibrator.py backend/tests/test_boost_features.py
git commit -m "refactor(ml): remove dead brave_results_count and legs_matched_ratio features"
```

---

### Task 5: Add boost outcome resolution for ML training

**Files:**
- Modify: `backend/src/ml/feature_store.py`
- Test: `backend/tests/test_feature_store.py` (append)

- [ ] **Step 1: Write failing test for resolve_boost_outcomes**

Add to `backend/tests/test_feature_store.py`:

```python
def test_resolve_boost_outcomes_won(db_session):
    """Settling a boost bet as 'won' should set outcome_binary=1 on matching ml_features."""
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider
    # Setup provider + profile
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    # Log boost feature
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    # Place and settle boost bet
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="won", payout=300,
    )
    db_session.add(bet)
    db_session.flush()
    # Resolve
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 1
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome == 1.0
    assert row.outcome_binary == 1
    assert row.resolved_at is not None


def test_resolve_boost_outcomes_lost(db_session):
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="lost", payout=0,
    )
    db_session.add(bet)
    db_session.flush()
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 1
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome == 0.0
    assert row.outcome_binary == 0


def test_resolve_boost_outcomes_void_deletes(db_session):
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    from src.db.models import Bet, Profile, Provider, MlFeature
    db_session.add(Provider(id="unibet", name="Unibet"))
    profile = Profile(name="test", is_active=True, bankroll=10000)
    db_session.add(profile)
    db_session.flush()
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    bet = Bet(
        profile_id=profile.id, provider_id="unibet", market="boost",
        outcome="Arsenal att vinna", odds=3.0, stake=100, bet_type="boost",
        result="void", payout=100,
    )
    db_session.add(bet)
    db_session.flush()
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 0  # void = deleted, not resolved
    assert db_session.query(MlFeature).filter_by(source_type="boost").count() == 0


def test_resolve_boost_no_settled_bet(db_session):
    """If no settled bet exists, features should remain unresolved."""
    from src.ml.feature_store import log_features, resolve_boost_outcomes
    log_features(db_session, "betting", "Arsenal att vinna", "boost", {"llm_raw_probability": 0.45})
    count = resolve_boost_outcomes(db_session, "Arsenal att vinna")
    assert count == 0
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).filter_by(source_type="boost").first()
    assert row.outcome is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_feature_store.py::test_resolve_boost_outcomes_won -v`
Expected: FAIL — `resolve_boost_outcomes` does not exist

- [ ] **Step 3: Implement `resolve_boost_outcomes`**

Add to `backend/src/ml/feature_store.py`:

```python
def resolve_boost_outcomes(session: Session, boost_title: str) -> int:
    """Resolve ML feature outcomes for a settled boost bet.

    Joins ml_features (source_type='boost', source_id=boost_title) to bets
    (bet_type='boost', outcome=boost_title) to propagate settlement results.

    Returns count of resolved feature rows.
    """
    from src.db.models import Bet

    # Find settled bet for this boost
    bet = session.query(Bet).filter(
        Bet.bet_type == "boost",
        Bet.outcome == boost_title,
        Bet.result.isnot(None),
    ).first()
    if not bet:
        return 0

    # Find all unresolved feature rows for this boost
    rows = session.query(MlFeature).filter(
        MlFeature.source_type == "boost",
        MlFeature.source_id == boost_title,
        MlFeature.outcome.is_(None),
    ).all()
    if not rows:
        return 0

    now = datetime.now(timezone.utc)

    if bet.result == "void":
        for row in rows:
            session.delete(row)
        session.flush()
        return 0

    outcome_val = 1.0 if bet.result == "won" else 0.0
    outcome_bin = 1 if bet.result == "won" else 0

    for row in rows:
        row.outcome = outcome_val
        row.outcome_binary = outcome_bin
        row.resolved_at = now

    session.flush()
    return len(rows)
```

- [ ] **Step 4: Run all tests**

Run: `cd backend && python -m pytest tests/test_feature_store.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/feature_store.py backend/tests/test_feature_store.py
git commit -m "feat(feature_store): add resolve_boost_outcomes for ML calibrator training"
```

---

### Task 6: Hook `resolve_boost_outcomes` into `settle_bet`

**Files:**
- Modify: `backend/src/services/bet_service.py:237-295`

- [ ] **Step 1: Add the hook**

In `backend/src/services/bet_service.py`, in the `settle_bet()` method, after the postmortem compute block (after line 287), add:

```python
        # Resolve ML feature outcomes for boost bets (M4 calibrator training data)
        if bet.bet_type == "boost" and bet.outcome:
            try:
                from src.ml.feature_store import resolve_boost_outcomes
                resolve_boost_outcomes(self.db, bet.outcome)
            except Exception as e:
                logger.warning(f"Boost outcome resolution failed for bet {bet_id}: {e}")
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/bet_service.py
git commit -m "feat(bet_service): hook boost outcome resolution into settle_bet"
```

---

### Task 7: Final integration check

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=30`
Expected: ALL PASS

- [ ] **Step 2: Verify imports work end-to-end**

Run: `cd backend && python -c "from src.analysis.ev_enrichment import enrich_specials_with_ev; from src.analysis.llm_enrichment import enrich_specials_with_llm; from src.ml.features.boost_features import extract_boost_features; from src.ml.feature_store import resolve_boost_outcomes; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 3: Final commit (if any fixups needed)**

```bash
git add -A && git commit -m "fix: address integration issues from boost pipeline gaps"
```
