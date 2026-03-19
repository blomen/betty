# RL Level Precomputation — Two-Pass Session Summary Store

**Date:** 2026-03-20
**Status:** Approved
**Depends on:** [RL Trading Agent Design](2026-03-19-rl-trading-agent-design.md)

## Problem

8 of 26 `LevelType` enum values in `rl/config.py` are never populated during replay — the DQN agent has dead one-hot neurons that will never fire. The replay engine only builds levels from intra-session data (VWAP, VP, session levels, SMC structures) and simple PDH/PDL chaining. It cannot compute levels that require cross-session context.

### Missing Levels

| LevelType | Why it's missing |
|---|---|
| `NAKED_POC` | Requires tracking which prior session POCs have NOT been revisited |
| `POC_DAILY` | Needs yesterday's session POC (separate from developing `POC_SESSION`) |
| `POC_WEEKLY` | Needs composite VP across 5 sessions |
| `POC_MONTHLY` | Needs composite VP across 20 sessions |
| `POC_MACRO` | Needs composite VP across all available sessions |
| `GLOBEX_HL` | ETH high/low (18:00 ET prev → 09:30 ET current) not computed |
| `OVERNIGHT_HL` | Same as Globex for NQ futures |
| `SINGLE_PRINT` | Excluded as "too noisy" — needs filtering to significant zones |

### Impact

The agent allocates 26 one-hot dimensions for level identity. With 6 types never firing, those dimensions are wasted — the agent cannot learn anything about these level types. More importantly, naked POCs and multi-timeframe POCs are among the strongest levels in volume profile analysis (per `docs/strategy/05-key-levels.md`).

## Solution: Two-Pass Architecture

### Pass 1: `rl precompute` — Build Session Summaries

New CLI command that sweeps all Parquet tick files chronologically. For each trading session, computes and stores a lightweight summary.

#### SessionSummary Dataclass

```python
@dataclass
class SessionSummary:
    date: str                                  # "2025-09-15"
    poc: float                                 # Session POC (from full-session VP)
    vah: float                                 # Session VAH
    val: float                                 # Session VAL
    histogram: dict[str, int]                  # Volume histogram {price_str: volume}
    rth_high: float | None                     # RTH high (09:30-16:00 ET)
    rth_low: float | None                      # RTH low
    eth_high: float | None                     # ETH high (18:00 prev → 09:30 current)
    eth_low: float | None                      # ETH low
    single_print_zones: list[tuple[float, float]]  # Filtered SP zones (3+ consecutive)
```

**Histogram key format:** Price floats serialized as canonical strings using `f"{price:.2f}"` format for JSON compatibility (e.g., `"20105.00": 4523, "20105.25": 2341`). This avoids floating-point artifacts (e.g., `20105.250000000004`) creating duplicate keys after deserialization. On load, keys are parsed back to floats and re-snapped to the tick grid.

#### Session Boundaries

NQ futures sessions on CME:

- **ETH (Electronic Trading Hours):** 18:00 ET (previous calendar day) → 17:00 ET (current day), with a halt 17:00-18:00 ET
- **RTH (Regular Trading Hours):** 09:30-16:00 ET
- **Globex/Overnight:** 18:00 ET (prev) → 09:30 ET (current) — everything before RTH

A "session date" is the calendar date of the RTH portion. ETH ticks from 18:00 the night before belong to the next day's session.

#### Single Print Zone Filtering

The current VP detects individual single-print ticks (volume < 5% of POC volume). A typical session has 200+ individual single prints — too noisy to use as levels.

Filter to **significant zones**: 3+ consecutive tick-grid prices that are all single prints. These represent genuine low-volume gaps that act as support/resistance.

```python
def filter_single_print_zones(
    single_prints: list[tuple[float, float]],
    tick_size: float = 0.25,
    min_consecutive: int = 3,
) -> list[tuple[float, float]]:
    """Group consecutive single-print prices into zones.

    Returns (zone_low, zone_high) tuples for zones spanning
    min_consecutive or more tick levels.
    """
```

Expected output: 0-5 significant zones per session instead of 200+ individual ticks.

#### Storage

File: `data/rl/session_summaries.json`

```json
{
  "2025-09-15": {
    "poc": 20105.25,
    "vah": 20125.50,
    "val": 20085.00,
    "histogram": {"20080.0": 1234, "20080.25": 2345, ...},
    "rth_high": 20130.00,
    "rth_low": 20075.50,
    "eth_high": 20135.25,
    "eth_low": 20070.00,
    "single_print_zones": [[20090.25, 20091.00], [20115.50, 20116.25]]
  }
}
```

Size estimate: ~1-2 KB per session (histogram is the bulk — ~400 price levels × ~15 chars each). ~250 KB for a full year. Trivial.

#### Precompute Process

1. Load all Parquet tick files in chronological order
2. Group ticks by session date (ETH boundary: 18:00 ET)
3. For each session:
   - Run ticks through `IncrementalVolumeProfile` → POC, VAH, VAL, histogram, single prints
   - Compute RTH high/low from ticks in 09:30-16:00 ET
   - Compute ETH high/low from ticks in 18:00 ET (prev) → 09:30 ET
   - Filter single prints to significant zones
   - Store as `SessionSummary`
4. Write all summaries to JSON

**Idempotent:** If `session_summaries.json` already exists, only compute summaries for dates not yet present. This makes incremental updates fast after fetching new tick data.

### Pass 2: `rl replay` — Enhanced with Precomputed Levels

The existing `replay` command loads the session summary store before replaying. Before each session, it computes the cross-session levels and injects them into the replay engine.

#### What Gets Injected Per Session

| Level | Source | How |
|---|---|---|
| **Naked POCs** | All prior session POCs not yet revisited | New `find_naked_pocs()` in `session_store.py` — checks prior POCs against subsequent sessions' RTH ranges |
| **POC Daily** | Previous session's POC | Direct lookup from summaries |
| **POC Weekly** | Composite VP from last 5 sessions | Merge histograms, recompute POC |
| **POC Monthly** | Composite VP from last 20 sessions | Merge histograms, recompute POC |
| **POC Macro** | Composite VP from all prior sessions | Merge all histograms, recompute POC |
| **Globex HL** | Current session's ETH high/low | Direct from current session summary |
| **Overnight HL** | Same as Globex for NQ | Alias of Globex HL |
| **Single Prints** | Current session's filtered SP zones | Midpoint of each zone as level price |

#### Composite VP via Histogram Merging

The key insight: multi-session volume profiles don't need raw ticks. You merge the per-session volume histograms (add bucket volumes together), then compute POC/VAH/VAL from the merged histogram.

```python
def composite_histogram(
    summaries: list[SessionSummary],
) -> dict[float, int]:
    """Merge volume histograms from multiple sessions."""
    merged: dict[float, int] = {}
    for s in summaries:
        for price_str, vol in s.histogram.items():
            price = float(price_str)
            merged[price] = merged.get(price, 0) + vol
    return merged
```

Then feed the merged histogram into the same POC/VAH/VAL expansion logic that `IncrementalVolumeProfile.get()` uses.

#### Naked POC Tracking

A POC is "naked" if no subsequent session's price range (RTH high to RTH low) has touched it. The existing `detect_naked_pocs()` in `levels.py` does exactly this — it takes a list of `{date, poc}` and a list of bars, and returns the untouched ones.

For the precompute approach, we adapt slightly: instead of passing bars, we check each subsequent session's RTH range:

```python
def find_naked_pocs(
    summaries: dict[str, SessionSummary],
    current_date: str,
    max_lookback_sessions: int = 20,
) -> list[dict]:
    """Find prior session POCs not yet revisited by subsequent RTH ranges."""
    sorted_dates = sorted(d for d in summaries if d < current_date)
    recent = sorted_dates[-max_lookback_sessions:]

    naked = []
    for session_date in recent:
        poc = summaries[session_date].poc
        # Check all sessions AFTER this one, up to current_date
        touched = False
        for later_date in sorted_dates:
            if later_date <= session_date:
                continue
            s = summaries[later_date]
            if s.rth_low is not None and s.rth_high is not None:
                if s.rth_low <= poc <= s.rth_high:
                    touched = True
                    break
        if not touched:
            naked.append({"date": session_date, "price": poc})

    return naked
```

Cap at 20 sessions lookback — POCs older than a month are less relevant and would clutter the active levels list.

Note: `levels.py` has an existing `detect_naked_pocs()` function with a different API (takes bar lists instead of session ranges). The new `find_naked_pocs()` is a purpose-built function for the precompute pipeline — it does not wrap the existing one.

### Weekend / Holiday ETH Tick Grouping

NQ futures halt 17:00-18:00 ET daily, and are closed Saturday 17:00 ET → Sunday 18:00 ET. Friday evening ETH ticks (18:00 ET Friday) belong to Monday's session, not Saturday.

The precompute pass must assign ETH ticks to the next RTH date, not the calendar date of the tick timestamp. Logic:
- Group ticks by ETH session: 18:00 ET → next day 17:00 ET
- The "session date" = the calendar date of the RTH portion (09:30-16:00 ET) within that window
- Friday 18:00 ET through Monday 09:30 ET all belong to Monday's session
- Holiday handling: if no RTH ticks exist for a calendar date, that date has no session (skip it)

The existing `rl replay` command groups by UTC calendar date (`df["_date"]`), which accidentally works for weekdays but would misgroup Friday evening ETH ticks (they'd land on Saturday in UTC). The precompute pass uses explicit ETH boundaries instead.

### Globex HL and Look-Ahead Bias

The precompute pass knows the full session's ETH range before replay starts. This is technically look-ahead during the ETH portion of the session. However:
- The replay engine's level touch detection runs on all ticks including ETH
- Globex HL levels should only become active after RTH opens (09:30 ET), since that's when traders reference the overnight range
- Implementation: the replay engine should gate Globex/Overnight HL levels behind a RTH-started flag (similar to how VWAP resets at RTH open via `_rth_vwap_started`)

### Changes to ReplayEngine

#### New Parameter: `precomputed_levels`

`replay_session()` gains an optional `precomputed_levels` dict:

```python
def replay_session(
    self,
    ticks: list[Any],
    session_date: datetime,
    prior_session_levels: dict | None = None,
    precomputed_levels: dict | None = None,  # NEW
) -> list[Episode]:
```

The dict schema:

```python
{
    "naked_pocs": [{"date": "2025-09-10", "price": 20105.25}, ...],
    "poc_daily": 20105.25,
    "poc_weekly": 20098.50,
    "poc_monthly": 20110.00,
    "poc_macro": 20050.75,
    "globex_high": 20135.25,
    "globex_low": 20070.00,
    "overnight_high": 20135.25,  # Alias of globex for NQ
    "overnight_low": 20070.00,
    "single_print_zones": [(20090.25, 20091.00), (20115.50, 20116.25)],
}
```

#### Changes to `_rebuild_active_levels()`

Add a new section after the existing swing points block:

```python
# --- Precomputed cross-session levels ---
if self._precomputed:
    for naked in self._precomputed.get("naked_pocs", []):
        levels.append(("naked_poc", LevelType.NAKED_POC, naked["price"]))

    _add_optional(levels, "poc_daily", LevelType.POC_DAILY, self._precomputed.get("poc_daily"))
    _add_optional(levels, "poc_weekly", LevelType.POC_WEEKLY, self._precomputed.get("poc_weekly"))
    _add_optional(levels, "poc_monthly", LevelType.POC_MONTHLY, self._precomputed.get("poc_monthly"))
    _add_optional(levels, "poc_macro", LevelType.POC_MACRO, self._precomputed.get("poc_macro"))

    _add_optional(levels, "globex_high", LevelType.GLOBEX_HL, self._precomputed.get("globex_high"))
    _add_optional(levels, "globex_low", LevelType.GLOBEX_HL, self._precomputed.get("globex_low"))
    _add_optional(levels, "overnight_high", LevelType.OVERNIGHT_HL, self._precomputed.get("overnight_high"))
    _add_optional(levels, "overnight_low", LevelType.OVERNIGHT_HL, self._precomputed.get("overnight_low"))

    for sp_low, sp_high in self._precomputed.get("single_print_zones", []):
        mid = (sp_low + sp_high) / 2.0
        levels.append(("single_print", LevelType.SINGLE_PRINT, mid))
```

#### No State Reset Issues

Most precomputed levels are **static for the entire session** — composite POCs and ETH ranges don't change as ticks arrive. They're loaded once in `_reset()` from the `precomputed_levels` param.

**Exception: Naked POCs can be invalidated.** When the current session's price range sweeps through a naked POC, it should be removed from the active levels list. The engine tracks this in `_on_bar_close()`: after each 1m bar, check if any naked POC falls within the session's developing high/low range and remove it from the precomputed set. This prevents the agent from seeing a "naked" POC that was already filled earlier in the session.

### CLI Commands

#### New: `rl precompute`

```bash
python -m src.app rl precompute [--all] [--month YYYY-MM]
```

- Reads Parquet tick files from `data/rl/ticks/`
- Writes `data/rl/session_summaries.json`
- Incremental: skips dates already in the summary file
- Must run before `rl replay`

#### Enhanced: `rl replay`

```bash
python -m src.app rl replay [--all] [--month YYYY-MM]
```

- Loads `session_summaries.json` at startup
- For each session, computes precomputed_levels from summaries
- Passes them to `engine.replay_session(..., precomputed_levels=...)`
- If no summaries file found, warns and continues without precomputed levels (backward-compatible)

### Updated Pipeline

```
rl fetch          → Parquet tick files (data/rl/ticks/)
rl precompute     → Session summaries (data/rl/session_summaries.json)  ← NEW
rl replay         → Episodes (.npy files in data/rl/episodes/)
rl train          → Model checkpoint (data/rl/models/dqn_v1.pt)
rl eval           → Evaluation report
```

## Files

| File | Change |
|---|---|
| `backend/src/rl/data/session_store.py` | **NEW** — `SessionSummary`, `build_session_summary()`, `load_summaries()`, `save_summaries()`, `composite_histogram()`, `find_naked_pocs()`, `filter_single_print_zones()`, `compute_precomputed_levels()` |
| `backend/src/rl/cli.py` | Add `precompute` command; enhance `replay` to load summaries and pass precomputed levels |
| `backend/src/rl/data/replay_engine.py` | Add `precomputed_levels` param to `replay_session()`; populate 6 missing level types in `_rebuild_active_levels()` |

### What Does NOT Change

- **`rl/config.py`** — All 26 `LevelType` values already exist. No additions needed.
- **`rl/features/observation.py`** — `OBSERVATION_DIM` stays at 107. The one-hot encoding already has 26 slots.
- **`market_data/levels.py`** — No changes. `detect_naked_pocs()` already exists (though not currently called). Composite VP uses the same POC/VAH/VAL math.
- **Training pipeline** — `rl train` and `rl eval` are unchanged. They consume `.npy` episode files which now simply contain more diverse level types.

## Bug Fix: backfill.py Line 287

While investigating, we found a bug in `backend/src/ml/level_touch/backfill.py` line 287:

```python
vwap_bands = compute_vwap_bands(trades)  # 'trades' is undefined here
```

The variable `trades` was created inside `compute_volume_profile(bars_to_trades(bars))` but never captured. Fix:

```python
trades = bars_to_trades(bars)
vp = compute_volume_profile(trades)
vwap_bands = compute_vwap_bands(trades)
```

This is in the old bar-based ML backfill pipeline (separate from the RL system), but should be fixed to avoid confusion.

## Expected Impact

### Episode Distribution

Before: 20 level types can fire (of 26 declared).
After: All 26 level types can fire.

New level touches per session (estimated):
- Naked POCs: 2-5 (depends on how many are active)
- POC Daily: 1-3 (yesterday's POC is heavily tested)
- POC Weekly/Monthly/Macro: 0-2 each (less frequently touched)
- Globex HL: 1-2 (overnight range tested at RTH open)
- Overnight HL: 1-2 (alias, same as Globex for NQ)
- Single Print zones: 0-3 (filtered to significant zones only)

Estimated increase: +5-15 episodes per session, or ~10-20% more training data.

### Agent Learning

The DQN can now learn:
- Whether naked POCs are genuine magnets (the strategy claims they are)
- Whether composite POCs (weekly/monthly) provide stronger reactions than session POC
- Whether Globex HL provides edge at RTH open (gap logic, Larry Williams "Oops")
- Whether single print zones actually act as S/R
- Cross-level confluence patterns (e.g., naked POC near weekly POC = strong level)

## Known Limitations

- **Globex HL = Overnight HL for NQ.** These are the same session. The distinction exists for instruments that have separate overnight sessions (e.g., equity ETFs). For NQ Phase 1, both map to the same ETH range. If we add more instruments later, they'd differentiate.
- **Composite VP equal-weights sessions.** A session with 2M volume counts the same as one with 500K. Could volume-weight later, but equal-weight is standard for composite profiles (matches TPO logic where each time period = 1 unit).
- **Macro POC drifts with dataset size.** As more months are fetched, the macro composite changes. This is expected — it represents the "big picture" fair value.
- **Single print zone threshold (3 consecutive) is a guess.** May need tuning after seeing distributions. Too strict = too few zones. Too loose = noise.
