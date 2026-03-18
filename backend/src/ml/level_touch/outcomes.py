"""Level touch outcome classifier and async outcome tracker."""

import json
import time
import logging
import asyncio
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TICK_SIZE = 0.25
STRONG_THRESHOLD = 20   # ticks
WEAK_THRESHOLD = 8      # ticks
OBSERVATION_WINDOW_SEC = 30 * 60  # 30 minutes

OUTCOMES = ["strong_reversal", "weak_reversal", "chop", "weak_continuation", "strong_continuation"]
OUTCOME_TO_INDEX = {o: i for i, o in enumerate(OUTCOMES)}

# RTH end: 15:30 ET in seconds since midnight (15*3600 + 30*60)
_RTH_END_SEC = 15 * 3600 + 30 * 60


# ---------------------------------------------------------------------------
# Task 4: Pure outcome classifier
# ---------------------------------------------------------------------------

def classify_outcome(
    level_price: float,
    approach_direction: str,
    candle_highs: list[float],
    candle_lows: list[float],
) -> dict:
    """Classify the outcome of a level touch using post-touch candle data.

    Args:
        level_price: Price of the level being touched.
        approach_direction: "from_below" or "from_above".
        candle_highs: List of candle high prices in the observation window.
        candle_lows: List of candle low prices in the observation window.

    Returns:
        Dict with keys: outcome (str | None), max_continuation_ticks (float),
        max_reversal_ticks (float).
    """
    if not candle_highs or not candle_lows:
        return {"outcome": None, "max_continuation_ticks": 0.0, "max_reversal_ticks": 0.0}

    max_high = max(candle_highs)
    min_low = min(candle_lows)

    if approach_direction == "from_below":
        # Price came from below and touched the level.
        # Continuation = price continued up above level.
        # Reversal = price fell back down below level.
        raw_continuation = (max_high - level_price) / TICK_SIZE
        raw_reversal = (level_price - min_low) / TICK_SIZE
    else:
        # approach_direction == "from_above"
        # Price came from above and touched the level.
        # Continuation = price continued down below level.
        # Reversal = price bounced up above level.
        raw_continuation = (level_price - min_low) / TICK_SIZE
        raw_reversal = (max_high - level_price) / TICK_SIZE

    max_continuation_ticks = max(raw_continuation, 0.0)
    max_reversal_ticks = max(raw_reversal, 0.0)

    # Classify: dominant direction wins; threshold determines class strength.
    if max_continuation_ticks >= max_reversal_ticks:
        dominant_ticks = max_continuation_ticks
        if dominant_ticks >= STRONG_THRESHOLD:
            outcome = "strong_continuation"
        elif dominant_ticks >= WEAK_THRESHOLD:
            outcome = "weak_continuation"
        else:
            outcome = "chop"
    else:
        dominant_ticks = max_reversal_ticks
        if dominant_ticks >= STRONG_THRESHOLD:
            outcome = "strong_reversal"
        elif dominant_ticks >= WEAK_THRESHOLD:
            outcome = "weak_reversal"
        else:
            outcome = "chop"

    return {
        "outcome": outcome,
        "max_continuation_ticks": max_continuation_ticks,
        "max_reversal_ticks": max_reversal_ticks,
    }


# ---------------------------------------------------------------------------
# Task 5: Async outcome tracker
# ---------------------------------------------------------------------------

class OutcomeTracker:
    """Tracks level touches and schedules delayed outcome measurement.

    Usage (live mode):
        tracker = OutcomeTracker()
        tracker.set_context(loop=asyncio.get_event_loop(), db_session_factory=SessionLocal)
        tracker.register_touch(...)

    In test / backfill mode, set_context is never called — RTH checks and
    DB writes are skipped automatically.
    """

    def __init__(self):
        self._pending: dict[str, dict] = {}
        self._loop = None
        self._db_session_factory = None

    def set_context(self, loop, db_session_factory):
        """Attach an asyncio event loop and DB session factory for live operation."""
        self._loop = loop
        self._db_session_factory = db_session_factory

    def register_touch(
        self,
        symbol: str,
        level_name: str,
        level_type: str,
        level_price: float,
        approach_direction: str,
        touch_ts: float,
        session_date: str,
        features: dict,
        prediction: str | None = None,
        prediction_confidence: float | None = None,
    ) -> None:
        """Register a level touch and schedule outcome measurement.

        RTH boundary: when running live (self._loop is set), touches after
        15:30 ET are ignored because there won't be a full 30-minute window
        within the regular session.

        Dedup: if the same level_name was already registered within
        OBSERVATION_WINDOW_SEC, the new touch is ignored.
        """
        # RTH boundary check — only in live mode (loop is set)
        if self._loop is not None:
            try:
                touch_dt = datetime.fromtimestamp(touch_ts, tz=timezone.utc)
                # Convert UTC to ET (UTC-5 standard / UTC-4 daylight).
                # Use a simple heuristic: check wall-clock seconds since midnight ET.
                # We import zoneinfo lazily to avoid hard dependency in tests.
                try:
                    from zoneinfo import ZoneInfo
                    et_dt = touch_dt.astimezone(ZoneInfo("America/New_York"))
                    seconds_since_midnight = (
                        et_dt.hour * 3600 + et_dt.minute * 60 + et_dt.second
                    )
                    if seconds_since_midnight > _RTH_END_SEC:
                        log.debug(
                            "Skipping touch for %s %s: after RTH end (15:30 ET)",
                            symbol, level_name,
                        )
                        return
                except Exception:
                    pass  # If timezone check fails, proceed anyway
            except Exception:
                pass

        # Dedup: same level within OBSERVATION_WINDOW_SEC
        key = level_name
        if key in self._pending:
            if touch_ts - self._pending[key]["touch_ts"] < OBSERVATION_WINDOW_SEC:
                log.debug(
                    "Deduping touch for %s %s (%.0f s since last)",
                    symbol, level_name, touch_ts - self._pending[key]["touch_ts"],
                )
                return

        touch_data = {
            "symbol": symbol,
            "level_name": level_name,
            "level_type": level_type,
            "level_price": level_price,
            "approach_direction": approach_direction,
            "touch_ts": touch_ts,
            "session_date": session_date,
            "features": features,
            "prediction": prediction,
            "prediction_confidence": prediction_confidence,
            "db_id": None,  # filled after _write_touch_to_db
        }
        self._pending[key] = touch_data

        log.info(
            "Registered touch: %s %s @ %.2f (%s)",
            symbol, level_name, level_price, approach_direction,
        )

        # Write initial DB row (no outcome yet) if session factory available
        if self._db_session_factory is not None:
            self._write_touch_to_db(touch_data)

        # Schedule delayed outcome measurement if event loop available
        if self._loop is not None:
            self._loop.call_later(
                OBSERVATION_WINDOW_SEC,
                lambda td=touch_data: asyncio.run_coroutine_threadsafe(
                    self._measure_outcome(td), self._loop
                ),
            )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _write_touch_to_db(self, touch_data: dict) -> None:
        """Write LevelTouchOutcome (without outcome yet) and LevelTouchFeature rows."""
        from ..db.models import LevelTouchOutcome, LevelTouchFeature

        try:
            with self._db_session_factory() as session:
                outcome_row = LevelTouchOutcome(
                    symbol=touch_data["symbol"],
                    touch_ts=touch_data["touch_ts"],
                    level_name=touch_data["level_name"],
                    level_type=touch_data["level_type"],
                    level_price=touch_data["level_price"],
                    approach_direction=touch_data["approach_direction"],
                    session_date=touch_data["session_date"],
                    is_backfill=0,
                    prediction=touch_data.get("prediction"),
                    prediction_confidence=touch_data.get("prediction_confidence"),
                    outcome=None,
                    max_continuation_ticks=None,
                    max_reversal_ticks=None,
                    outcome_measured_at=None,
                )
                session.add(outcome_row)
                session.flush()

                feature_row = LevelTouchFeature(
                    touch_outcome_id=outcome_row.id,
                    features=json.dumps(touch_data["features"]),
                    feature_version=1,
                    created_at=time.time(),
                )
                session.add(feature_row)
                session.commit()

                # Store the DB id so _measure_outcome can update the right row
                touch_data["db_id"] = outcome_row.id
                log.debug(
                    "Wrote LevelTouchOutcome id=%d for %s %s",
                    outcome_row.id, touch_data["symbol"], touch_data["level_name"],
                )
        except Exception:
            log.exception(
                "Failed to write touch to DB for %s %s",
                touch_data["symbol"], touch_data["level_name"],
            )

    async def _measure_outcome(self, touch_data: dict) -> None:
        """Called 30 min after touch: fetch candles, classify, update DB row."""
        from datetime import datetime, timezone

        symbol = touch_data["symbol"]
        level_name = touch_data["level_name"]
        touch_ts = touch_data["touch_ts"]
        level_price = touch_data["level_price"]
        approach_direction = touch_data["approach_direction"]

        log.info(
            "Measuring outcome for %s %s (touch_ts=%.0f)",
            symbol, level_name, touch_ts,
        )

        # Remove from pending regardless of success
        self._pending.pop(level_name, None)

        if self._db_session_factory is None:
            return

        try:
            from ..repositories.market_repo import MarketRepo

            start_dt = datetime.fromtimestamp(touch_ts, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(
                touch_ts + OBSERVATION_WINDOW_SEC, tz=timezone.utc
            )

            with self._db_session_factory() as session:
                repo = MarketRepo(session)
                candles = repo.get_candles(
                    symbol=symbol,
                    interval="1m",
                    start=start_dt,
                    end=end_dt,
                )

                highs = [c.h for c in candles]
                lows = [c.l for c in candles]

            result = classify_outcome(level_price, approach_direction, highs, lows)

            if touch_data.get("db_id") is None:
                log.warning(
                    "No db_id for %s %s — cannot update outcome row",
                    symbol, level_name,
                )
                return

            with self._db_session_factory() as session:
                from ..db.models import LevelTouchOutcome

                row = session.get(LevelTouchOutcome, touch_data["db_id"])
                if row is None:
                    log.warning(
                        "LevelTouchOutcome id=%d not found in DB",
                        touch_data["db_id"],
                    )
                    return

                row.outcome = result["outcome"]
                row.max_continuation_ticks = result["max_continuation_ticks"]
                row.max_reversal_ticks = result["max_reversal_ticks"]
                row.outcome_measured_at = time.time()
                session.commit()

                log.info(
                    "Outcome updated: %s %s → %s (cont=%.1f rev=%.1f)",
                    symbol, level_name, result["outcome"],
                    result["max_continuation_ticks"],
                    result["max_reversal_ticks"],
                )

        except Exception:
            log.exception(
                "Failed to measure outcome for %s %s", symbol, level_name
            )
