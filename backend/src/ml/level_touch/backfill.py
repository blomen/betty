"""Backfill pipeline: replay historical 1m candles to generate labeled training data.

This module produces LevelTouchOutcome + LevelTouchFeature rows for every
historical level touch found in the DB candle history.

Entry point: run_backfill(db_session_factory, start_date, end_date, symbol)
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

# How many candles must separate two touches of the same level before the
# second is counted (prevents double-counting the same touch event).
DEDUP_WINDOW_CANDLES: int = 30

# How many candles after a touch we observe to classify the outcome.
OBSERVATION_WINDOW_CANDLES: int = 30


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------


def detect_virtual_touches(
    candles: list[dict],
    levels: list[dict],
) -> list[dict]:
    """Walk candles chronologically and detect when price crosses a level.

    A touch is registered when:
    - from_below: prev_close < level_price <= current_high
    - from_above: prev_close > level_price >= current_low

    Deduplication: the same level can only fire once per DEDUP_WINDOW_CANDLES
    candles.  The window resets after that many candles have elapsed since the
    last touch.

    Args:
        candles: List of candle dicts with at least keys o, h, l, c.
        levels: List of level dicts with at least keys name, price, type, category.

    Returns:
        List of touch dicts, each with:
            level_name, level_type, level_category, level_price,
            approach_direction, candle_index
    """
    if not candles or not levels:
        return []

    # Track the last candle index at which each level was touched.
    # Key: level_name → candle_index of last touch (-DEDUP_WINDOW_CANDLES - 1 to force first touch)
    last_touch_index: dict[str, int] = {lv["name"]: -(DEDUP_WINDOW_CANDLES + 1) for lv in levels}

    touches: list[dict] = []

    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["c"]
        cur_high = candles[i]["h"]
        cur_low = candles[i]["l"]

        for lv in levels:
            level_price = lv["price"]
            level_name = lv["name"]

            # Dedup check
            if i - last_touch_index[level_name] <= DEDUP_WINDOW_CANDLES:
                continue

            approach_direction: str | None = None

            if prev_close < level_price <= cur_high:
                approach_direction = "from_below"
            elif prev_close > level_price >= cur_low:
                approach_direction = "from_above"

            if approach_direction is not None:
                touches.append(
                    {
                        "level_name": level_name,
                        "level_type": lv["type"],
                        "level_category": lv["category"],
                        "level_price": level_price,
                        "approach_direction": approach_direction,
                        "candle_index": i,
                    }
                )
                last_touch_index[level_name] = i

    return touches


# ---------------------------------------------------------------------------
# Per-session backfill
# ---------------------------------------------------------------------------


def backfill_session(
    session_date: str,
    candles_1m: list[dict],
    levels: list[dict],
    session_analysis: dict | None = None,
) -> list[dict]:
    """Replay a single session and return labeled training rows.

    For each detected touch (excluding the last OBSERVATION_WINDOW_CANDLES
    candles where we have no observation window), extracts features and
    classifies the outcome from the next 30 candles.

    Args:
        session_date: ISO date string e.g. "2025-01-15".
        candles_1m: List of candle dicts (with keys o, h, l, c; optionally ts,
                    volume, delta, etc.).
        levels: List of level dicts from _compute_levels_for_date.
        session_analysis: Optional dict of pre-computed session context.

    Returns:
        List of result dicts with features + outcome fields.
    """
    from ..features.level_touch_features import extract_level_touch_features
    from .compute import compute_candle_pattern_features, compute_temporal_derivatives
    from .outcomes import classify_outcome

    if not candles_1m or not levels:
        return []

    touches = detect_virtual_touches(candles_1m, levels)
    results: list[dict] = []

    for touch in touches:
        idx = touch["candle_index"]

        # Skip touches where we don't have a full observation window ahead
        if idx + OBSERVATION_WINDOW_CANDLES >= len(candles_1m):
            continue

        # --- Features: from candles UP TO and including touch candle ---
        candles_up_to_touch = candles_1m[: idx + 1]

        temporal = compute_temporal_derivatives(candles_up_to_touch)
        pattern = compute_candle_pattern_features(candles_up_to_touch)

        # Session context fields (use session_analysis when available)
        sa = session_analysis or {}
        poc = sa.get("poc")
        vwap = sa.get("vwap")
        lp = touch["level_price"]

        features = extract_level_touch_features(
            # Level metadata
            level_type=touch["level_type"],
            level_category=touch["level_category"],
            approach_direction=touch["approach_direction"],
            distance_from_poc=(lp - poc) if poc else None,
            distance_from_vwap=(lp - vwap) if vwap else None,
            # Temporal derivatives
            delta_slope_5m=temporal.get("delta_slope_5m"),
            delta_slope_10m=temporal.get("delta_slope_10m"),
            cvd_acceleration=temporal.get("cvd_acceleration"),
            volume_roc_5m=temporal.get("volume_roc_5m"),
            tick_rate_roc=temporal.get("tick_rate_roc"),
            spread_compression=temporal.get("spread_compression"),
            absorption_building=temporal.get("absorption_building"),
            imbalance_trend=temporal.get("imbalance_trend"),
            # Candle pattern
            last_3_candles_direction=pattern.get("last_3_candles_direction"),
            last_candle_is_doji=pattern.get("last_candle_is_doji"),
            consecutive_same_direction=pattern.get("consecutive_same_direction"),
            highest_volume_candle_position=pattern.get("highest_volume_candle_position"),
            range_expansion=pattern.get("range_expansion"),
        )

        # --- Outcome: from the next 30 candles ---
        obs_candles = candles_1m[idx + 1 : idx + 1 + OBSERVATION_WINDOW_CANDLES]
        highs = [c["h"] for c in obs_candles]
        lows = [c["l"] for c in obs_candles]
        outcome_data = classify_outcome(lp, touch["approach_direction"], highs, lows)

        # touch_ts: epoch timestamp of the candle if available, else candle index * 60
        touch_candle = candles_1m[idx]
        raw_ts = touch_candle.get("ts")
        if raw_ts is not None:
            if isinstance(raw_ts, datetime):
                touch_ts = raw_ts.timestamp()
            elif isinstance(raw_ts, (int, float)):
                touch_ts = float(raw_ts)
            else:
                try:
                    touch_ts = datetime.fromisoformat(str(raw_ts)).timestamp()
                except Exception:
                    touch_ts = float(idx * 60)
        else:
            touch_ts = float(idx * 60)

        results.append(
            {
                "session_date": session_date,
                "level_name": touch["level_name"],
                "level_type": touch["level_type"],
                "level_price": lp,
                "approach_direction": touch["approach_direction"],
                "candle_index": idx,
                "touch_ts": touch_ts,
                "features": features,
                "outcome": outcome_data["outcome"],
                "max_continuation_ticks": outcome_data["max_continuation_ticks"],
                "max_reversal_ticks": outcome_data["max_reversal_ticks"],
            }
        )

    return results


# ---------------------------------------------------------------------------
# Level computation helper
# ---------------------------------------------------------------------------


def _compute_levels_for_date(
    candles: list,
    repo,
    symbol: str,
    date_str: str,
) -> list[dict]:
    """Build the full level list for one session date.

    Computes:
    - Volume Profile (POC / VAH / VAL) from OHLCV candles
    - VWAP bands (approximated from OHLCV via synthetic ticks)
    - Session levels (PDH/PDL, IB high/low, Tokyo/London ranges)

    Args:
        candles: List of MarketCandle ORM objects (or dicts with o/h/l/c/v/ts).
        repo: MarketRepo instance (not used currently but available for
              fetching additional context if needed).
        symbol: Instrument symbol e.g. "NQ".
        date_str: ISO date string e.g. "2025-01-15".

    Returns:
        List of level dicts, each with: name, price, type, category.
    """
    from ...market_data.levels import (
        compute_session_levels,
        compute_volume_profile,
        compute_vwap_bands,
    )

    levels: list[dict] = []

    if not candles:
        return levels

    # Convert ORM candle objects to plain dicts for the level functions.
    def to_dict(c) -> dict:
        if isinstance(c, dict):
            return c
        return {
            "ts": c.ts,
            "open": c.o,
            "high": c.h,
            "low": c.l,
            "close": c.c,
            "volume": c.v,
            # compute_volume_profile needs price/size; we use close/volume
        }

    bars = [to_dict(c) for c in candles]

    # --- Volume Profile ---
    from ...market_data.levels import bars_to_trades

    try:
        trades = bars_to_trades(bars)
        vp = compute_volume_profile(trades)
        if vp.poc and vp.poc > 0:
            levels.append({"name": "POC", "price": vp.poc, "type": "poc", "category": "session"})
        if vp.vah and vp.vah > 0:
            levels.append({"name": "VAH", "price": vp.vah, "type": "vah", "category": "session"})
        if vp.val and vp.val > 0:
            levels.append({"name": "VAL", "price": vp.val, "type": "val", "category": "session"})
    except Exception:
        log.debug("VP computation failed for %s %s", symbol, date_str)

    # --- VWAP bands ---
    try:
        vwap_bands = compute_vwap_bands(trades)
        if vwap_bands is not None:
            levels.append({"name": "VWAP", "price": vwap_bands.vwap, "type": "vwap", "category": "band"})
            levels.append({"name": "VWAP+1SD", "price": vwap_bands.sd1_upper, "type": "vwap_1sd", "category": "band"})
            levels.append({"name": "VWAP-1SD", "price": vwap_bands.sd1_lower, "type": "vwap_1sd", "category": "band"})
            levels.append({"name": "VWAP+2SD", "price": vwap_bands.sd2_upper, "type": "vwap_2sd", "category": "band"})
            levels.append({"name": "VWAP-2SD", "price": vwap_bands.sd2_lower, "type": "vwap_2sd", "category": "band"})
    except Exception:
        log.debug("VWAP bands computation failed for %s %s", symbol, date_str)

    # --- Session levels (PDH/PDL/IB etc.) ---
    # compute_session_levels expects bars with keys: ts (datetime), high, low
    # and session_date as a datetime object.
    try:
        session_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        sl = compute_session_levels(bars, session_dt)
        if sl.pdh is not None:
            levels.append({"name": "PDH", "price": sl.pdh, "type": "pdh", "category": "prior"})
        if sl.pdl is not None:
            levels.append({"name": "PDL", "price": sl.pdl, "type": "pdl", "category": "prior"})
        if sl.ib_high is not None:
            levels.append({"name": "IB_HIGH", "price": sl.ib_high, "type": "ib_high", "category": "session"})
        if sl.ib_low is not None:
            levels.append({"name": "IB_LOW", "price": sl.ib_low, "type": "ib_low", "category": "session"})
        if sl.tokyo_high is not None:
            levels.append({"name": "TOKYO_HIGH", "price": sl.tokyo_high, "type": "tokyo", "category": "overnight"})
        if sl.tokyo_low is not None:
            levels.append({"name": "TOKYO_LOW", "price": sl.tokyo_low, "type": "tokyo", "category": "overnight"})
        if sl.london_high is not None:
            levels.append({"name": "LONDON_HIGH", "price": sl.london_high, "type": "london", "category": "overnight"})
        if sl.london_low is not None:
            levels.append({"name": "LONDON_LOW", "price": sl.london_low, "type": "london", "category": "overnight"})
        if sl.weekly_high is not None:
            levels.append({"name": "WEEKLY_HIGH", "price": sl.weekly_high, "type": "weekly", "category": "structure"})
        if sl.weekly_low is not None:
            levels.append({"name": "WEEKLY_LOW", "price": sl.weekly_low, "type": "weekly", "category": "structure"})
    except Exception:
        log.debug("Session levels computation failed for %s %s", symbol, date_str, exc_info=True)

    return levels


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_backfill(
    db_session_factory: Callable,
    start_date: str,
    end_date: str | None = None,
    symbol: str = "NQ",
) -> int:
    """Replay historical candles date-by-date and store labeled training data.

    Args:
        db_session_factory: Callable that returns a SQLAlchemy Session (context
                            manager or plain session).  Compatible with both
                            ``sessionmaker()`` and ``contextmanager`` factories.
        start_date: ISO date string "YYYY-MM-DD" (inclusive).
        end_date: ISO date string "YYYY-MM-DD" (inclusive).  Defaults to today.
        symbol: Futures symbol, e.g. "NQ".

    Returns:
        Total number of training rows written.
    """
    from ...db.models import LevelTouchFeature, LevelTouchOutcome
    from ...repositories.market_repo import MarketRepo

    if end_date is None:
        end_date = date.today().isoformat()

    current = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    total_rows = 0

    while current <= end:
        date_str = current.isoformat()

        try:
            # Fetch 1m candles for a 2-day window so that session levels can
            # see the prior day's RTH data (for PDH/PDL).
            window_start = datetime(current.year, current.month, current.day, tzinfo=timezone.utc) - timedelta(days=1)
            window_end = datetime(current.year, current.month, current.day, 23, 59, 59, tzinfo=timezone.utc)

            with _open_session(db_session_factory) as session:
                repo = MarketRepo(session)
                candles = repo.get_candles(
                    symbol=symbol,
                    interval="1m",
                    start=window_start,
                    end=window_end,
                )

            if not candles:
                log.debug("No candles for %s %s — skipping", symbol, date_str)
                current += timedelta(days=1)
                continue

            # Compute levels
            with _open_session(db_session_factory) as session:
                repo = MarketRepo(session)
                levels = _compute_levels_for_date(candles, repo, symbol, date_str)

            if not levels:
                log.debug("No levels computed for %s %s — skipping", symbol, date_str)
                current += timedelta(days=1)
                continue

            # Convert ORM candles → plain dicts for backfill_session
            candle_dicts = [
                {
                    "ts": c.ts,
                    "o": c.o,
                    "h": c.h,
                    "l": c.l,
                    "c": c.c,
                    "v": c.v,
                }
                for c in candles
            ]

            # Run the session backfill
            rows = backfill_session(date_str, candle_dicts, levels)

            if not rows:
                log.debug("No touches found for %s %s", symbol, date_str)
                current += timedelta(days=1)
                continue

            # Persist results
            with _open_session(db_session_factory) as session:
                for row in rows:
                    outcome_row = LevelTouchOutcome(
                        symbol=symbol,
                        touch_ts=row["touch_ts"],
                        level_name=row["level_name"],
                        level_type=row["level_type"],
                        level_price=row["level_price"],
                        approach_direction=row["approach_direction"],
                        session_date=row["session_date"],
                        is_backfill=1,
                        outcome=row["outcome"],
                        max_continuation_ticks=row["max_continuation_ticks"],
                        max_reversal_ticks=row["max_reversal_ticks"],
                        outcome_measured_at=time.time(),
                        prediction=None,
                        prediction_confidence=None,
                    )
                    session.add(outcome_row)
                    session.flush()

                    feature_row = LevelTouchFeature(
                        touch_outcome_id=outcome_row.id,
                        features=json.dumps(row["features"]),
                        feature_version=1,
                        created_at=time.time(),
                    )
                    session.add(feature_row)

                session.commit()

            total_rows += len(rows)
            log.info(
                "Backfilled %s %s: %d levels, %d touches written",
                symbol,
                date_str,
                len(levels),
                len(rows),
            )

        except Exception:
            log.exception("Backfill failed for %s %s — continuing", symbol, date_str)

        current += timedelta(days=1)

    log.info("Backfill complete: %d total rows written for %s", total_rows, symbol)
    return total_rows


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@contextmanager
def _open_session(factory: Callable):
    """Yield a SQLAlchemy session from either a plain sessionmaker or a
    contextmanager-style factory.  Closes the session on exit."""
    session = factory()
    # If the factory returns a context manager (e.g. contextlib-wrapped),
    # enter it; otherwise use the session directly.
    if hasattr(session, "__enter__"):
        with session as s:
            yield s
    else:
        try:
            yield session
        finally:
            session.close()
