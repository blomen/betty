"""TopstepX broker adapter — dynamic stop management with model signals.

Instead of flattening on every new signal, manages the position:
- Same direction signal at new zone → trail stop to previous zone (let winners ride)
- Opposite direction signal → exit and flip
- SKIP → hold current position

This implements the hybrid design: GBT decides at each level whether to
hold, tighten, or exit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from src.rl.confidence import size_multiplier as _size_multiplier

log = logging.getLogger(__name__)

# RECKLESS_LEARNING_MODE (env, default 1 = on): we have only 16 trades total,
# all from 04-23/04-24 — the model can't learn its own outcomes if the live
# gate filters everything out. Loosened thresholds let weak signals through
# so the trainer accumulates labelled (obs, action, realized_pnl_r) tuples.
# Risk caps (daily loss / trailing DD / size) stay intact — they bound the
# downside, not the take rate. Set RECKLESS_LEARNING_MODE=0 to retighten.
import os as _os

_RECKLESS = _os.environ.get("RECKLESS_LEARNING_MODE", "1") != "0"

# 2026-05-05: full-reckless tuning. Learning data shows the model has
# only ~113 correlated samples since 05-01. The conservative gates were
# silently filtering out the very signals the trainer needs to learn from.
# Loosened to maximize labeled (obs, action, realized_pnl_r) tuples per day:
# - MIN_TRADE_INTERVAL_S 10→3: zone-touch density is sub-second; the 10s
#   floor was rejecting back-to-back signals at the same level
# - ZONE_COOLDOWN_S 30→5: same reasoning — 30s blocks ~30 signals per zone
#   when price oscillates; 5s still prevents same-tick re-entries
# - MIN_CONFIDENCE stays at 0.05 (already at floor)
# Risk caps (daily_loss, trailing_dd) untouched — those are account survival.
MIN_TRADE_INTERVAL_S = 3.0 if _RECKLESS else 30.0
# In reckless paper-trading mode this MUST match level_monitor's broker
# conf floor (0.05) — otherwise this filter silently rejects pivot
# signals the upstream gate already approved. Today's 12:31 pivot
# fired 4 signals with OF=0.40 but conf=0.078, all silently dropped.
MIN_CONFIDENCE = 0.05 if _RECKLESS else 0.30
ZONE_COOLDOWN_S = 5.0 if _RECKLESS else 120.0  # don't re-enter same zone within N seconds
DEFAULT_STOP_TICKS = 25  # sensible default if model returns None
MIN_STOP_TICKS = 15  # minimum stop distance (prevent too-tight stops)
# 2026-05-05: raised from 40→80. Backtest emits stop_ticks up to 50
# unclamped; live was clipping every output >40 to a 10pt stop. On
# 05-04 the avg 1m bar was 12-26pt, so 10pt stops sat inside noise →
# 30/109 stopouts, -$3,140. Backtest 67% WR / live 35% WR mismatch
# was largely this clip. 80 ticks (20pt) gives the model headroom
# without exposing >$400/contract on NQ ($20/pt × 20pt = $400).
MAX_STOP_TICKS = 80  # maximum stop distance

# NQ tick value: $5 per tick (0.25 point), $20 per point
_NQ_TICK_VALUE = 5.0
_NQ_POINT_VALUE = 20.0

# Confidence-scaled sizing: BASE_SIZE × size_multiplier(confidence)
BASE_SIZE = 1  # base contracts; multiplied by tier from src.rl.confidence
# Legacy risk-pct constants kept for reference; no longer used in live path
# Historical risk-pct sizing (1.5% base, 2% high-conf) replaced 2026-05-09 by
# size_multiplier-driven sizing. See src/rl/confidence.py:size_multiplier.


def _round_tick(price: float) -> float:
    """Round price to NQ tick increment (0.25 points)."""
    return round(price * 4) / 4


# Optional persistence sink — set by stocks_runtime.bootstrap_stocks() to a
# callable that ships the trade dict to the production DB. We keep this as a
# module-level hook so _log_broker_trade stays a free function and the adapter
# class doesn't need to know about transport.
_persist_callback = None


def set_persist_callback(cb) -> None:
    """Register a callable that persists each closed round-trip somewhere durable.

    The callable receives the same kwargs dict that gets logged. Called once
    per closed trade. Exceptions are swallowed (we don't want a transient DB
    write failure to mask the trade outcome in logs).
    """
    global _persist_callback
    _persist_callback = cb


def _log_broker_trade(**kwargs) -> None:
    """Log completed trade with full context and persist to dashboard."""
    result = "WIN" if kwargs.get("pnl_dollars", 0) > 0 else "LOSS"
    # exit_reason precedence:
    #   1. STOP when was_stop=true (broker stop-order filled)
    #   2. caller-provided flatten_reason (early_exit_lock, flip,
    #      manual, eod_flatten, etc.) — captured by adapter at flatten()
    #   3. SIGNAL fallback for unknown non-stop exits (orphan recovery)
    #   trail_count > 0 → annotate the existing reason
    flatten_reason = kwargs.get("flatten_reason")
    if kwargs.get("was_stop"):
        exit_reason = "STOP"
    elif flatten_reason:
        exit_reason = flatten_reason.upper()
    else:
        exit_reason = "SIGNAL"
    if kwargs.get("trail_count", 0) > 0 and exit_reason in ("STOP", "SIGNAL"):
        exit_reason = f"{exit_reason}/TRAIL{kwargs['trail_count']}"
    # Stamp the canonical exit_reason into kwargs so the persist callback
    # writes it to broker_trades.exit_reason (downstream chart label).
    kwargs["exit_reason"] = exit_reason

    log.info(
        "=== TRADE %s === %s %s %dx | entry=%.2f exit=%.2f stop=%.2f | "
        "PnL=$%.2f (%.2fR) | exit=%s trails=%d | "
        "signal=%s conf=%.3f cont_p=%.3f rev_p=%.3f zone=%.2f | "
        "stop_ticks=%s session_pnl=$%.2f",
        result,
        kwargs.get("symbol"),
        kwargs.get("side"),
        kwargs.get("size", 1),
        kwargs.get("entry_price", 0),
        kwargs.get("exit_price", 0),
        kwargs.get("stop_price", 0),
        kwargs.get("pnl_dollars", 0),
        kwargs.get("pnl_r", 0),
        exit_reason,
        kwargs.get("trail_count", 0),
        kwargs.get("signal_action", "?"),
        kwargs.get("signal_confidence", 0),
        kwargs.get("signal_cont_p", 0),
        kwargs.get("signal_rev_p", 0),
        kwargs.get("signal_zone", 0),
        kwargs.get("stop_ticks", "?"),
        kwargs.get("session_pnl", 0),
    )
    # Why we took it — surface the 1-line summary alongside the trade row so
    # `docker logs` is enough to scan the day's reasoning without joining DB.
    reasoning = kwargs.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("summary"):
        log.info("    why: %s", reasoning["summary"])
    # Add session_pnl to kwargs for dashboard
    from . import dashboard

    dashboard.record_trade(kwargs)

    if _persist_callback is not None:
        try:
            _persist_callback(kwargs)
        except Exception:
            log.exception("BrokerTrade persist callback failed (trade still in logs)")


# File-based trade-context persistence so reasoning + signal_* fields
# survive container restarts. The bug it fixes:
#   - Old container places entry → stop placed → fill pending
#   - Container restarts (deploy, watchdog, OOM)
#   - New container's _pending_trade dict is empty
#   - Stream fill arrives with the close → orphan close → no reasoning
# Now the dict is mirrored to disk on entry; on startup we read it; on
# exit we clear. Any orphan close that lands while the file exists can
# still pull full context (reasoning, signal_*, conviction).
import json as _json
import os as _os_

_PENDING_TRADE_PATH = _os_.environ.get("BROKER_PENDING_TRADE_PATH", "/app/data/rl/pending_trade.json")


def _save_pending_trade_to_disk(p: dict | None) -> None:
    try:
        _os_.makedirs(_os_.path.dirname(_PENDING_TRADE_PATH), exist_ok=True)
        if p is None:
            try:
                _os_.remove(_PENDING_TRADE_PATH)
            except FileNotFoundError:
                pass
            return
        # Convert datetime to ISO so json can serialize. Reasoning is already
        # a plain dict so it round-trips cleanly.
        serializable = {}
        for k, v in p.items():
            if isinstance(v, datetime):
                serializable[k] = v.isoformat()
            else:
                serializable[k] = v
        with open(_PENDING_TRADE_PATH, "w") as f:
            _json.dump(serializable, f)
    except Exception:
        log.warning("pending_trade disk save failed", exc_info=True)


def _load_pending_trade_from_disk() -> dict | None:
    try:
        with open(_PENDING_TRADE_PATH) as f:
            p = _json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        log.warning("pending_trade disk load failed", exc_info=True)
        return None
    # Restore datetimes
    for k in ("ts", "entry_submit_ts", "entry_fill_ts", "closed_at"):
        v = p.get(k)
        if isinstance(v, str):
            try:
                p[k] = datetime.fromisoformat(v)
            except Exception:
                pass
    return p


class TopstepXBrokerAdapter:
    """Risk-enforced order execution with dynamic stop management."""

    def __init__(self, client, config) -> None:
        from ..broker.position_tracker import PositionTracker

        self.client = client
        self.config = config
        self.tracker = PositionTracker()
        self._halted = False
        self._halt_reason = ""
        # Recover any in-flight trade from disk so a restart between
        # entry-fill and exit-fill doesn't drop reasoning + signal context.
        self._pending_trade: dict | None = _load_pending_trade_from_disk()
        if self._pending_trade:
            log.warning(
                "Recovered _pending_trade from disk on startup: side=%s entry=%.2f stop=%.2f trigger=%s",
                self._pending_trade.get("side"),
                self._pending_trade.get("entry_price", 0) or 0,
                self._pending_trade.get("stop_price", 0) or 0,
                self._pending_trade.get("signal_trigger", ""),
            )
        self._zone_last_entry: dict[float, float] = {}  # zone_price → last_entry_ts
        self._trail_count = 0  # how many times we've trailed the stop
        # 2026-05-06: per-orderId fill classification cache. TopstepX splits
        # any market order with size > 1 into multiple size-1 GatewayUserTrade
        # fills, all carrying the same orderId. Without this cache the second
        # leg of a size-2 entry was reclassified as "exit" (because tracker
        # entry_price is no longer 0), which a) silently flattened the
        # tracker, b) caused arnold to drop the real close fills as
        # "arrived while flat", c) the next genuine entry became an orphan
        # that the reconcile loop had to liquidate (yesterday +$1,775,
        # today +$100/-$275). Map: orderId -> "entry" | "exit" | "stop".
        # First fill with a given orderId classifies the event; subsequent
        # fills with the same orderId update size in place via on_add or
        # are dropped idempotently (close already happened).
        self._fill_orderid_class: dict[int, str] = {}
        # Flatten reason captured at the moment flatten() is called so the
        # exit fill that lands a moment later (or the recovery path that
        # reconstructs the trade) can attribute the close. Cleared after
        # persist so the next stop-hit (was_stop path) doesn't inherit a
        # stale reason. None → was_stop=true is the only known label.
        self._last_flatten_reason: str | None = None
        # 2026-05-07: serialize on_signal handling. Two opposite-side signals
        # (enter_long + enter_short) can fire on the same tick when zones
        # cluster on both sides; without this lock, both pass the
        # `is_flat and _pending_trade is None` guard and both call
        # _execute_entry → both place market+stop orders → stop A fires
        # before entry A confirms → "out-of-order exit fill" → tracker stuck
        # in side=X / entry=0.00 corruption. Lock blocks the second signal
        # until the first either commits _pending_trade or rejects.
        self._signal_lock: asyncio.Lock | None = None

    def _reset_tracker_for_rollback(self) -> None:
        """Reset tracker to flat state on entry-flow rollback (stop-placement
        or stop-verify failure after we've pre-populated tracker.side from the
        successful entry order). Mirrors the tail of close_position so on_stream_fill
        cleanly drops the upcoming close fill via the "while flat" guard.
        """
        self.tracker.side = None
        self.tracker.entry_price = 0.0
        self.tracker.stop_price = 0.0
        self.tracker.size = 0
        self.tracker.entry_order_id = None
        self.tracker.stop_order_id = None
        self.tracker.peak_R = 0.0
        self.tracker.locked_half_R = False
        self.tracker.locked_BE = False
        self._set_pending_trade(None)

    def update_mark_and_check_be_lock(self, price: float) -> None:
        """Per-tick: update peak_R AND fire BE-lock at +1.5R if not yet locked.

        Called from BOTH paths:
          - FastAPI level_monitor._check_positions (autonomous broker tick)
          - trading_service tick handler (subprocess broker tick)

        Without this method living on the adapter, BE-lock only ran in the
        FastAPI process — but trading_service's tracker is a different
        instance, so its peak_R stayed at 0 and BE-lock never fired on the
        actual production trades. This makes BE-lock work on whichever
        process owns the live position.
        """
        # 2026-05-06 DIAGNOSTIC: peak_R was stuck at 0.0 on a +$425 winner
        # despite price moving 2.5R above entry for 17 minutes. Log gates
        # to pinpoint where the chain breaks. Throttled to avoid flooding.
        prev_peak_R = float(getattr(self.tracker, "peak_R", 0.0) or 0.0)

        # 2026-05-08 watchdog: detect the dropped-fill bug signature
        # (side set but entry_price=0). The pre-populate fix in on_signal
        # prevents the original race, but defense-in-depth — if we ever
        # land back in this state for >10s, force a reconcile from broker
        # truth so trail logic can resume mid-trade rather than the trade
        # running stop-only with no BE-lock or cont-trail.
        if not self.tracker.is_flat and self.tracker.entry_price <= 0:
            if getattr(self, "_corruption_first_seen", None) is None:
                self._corruption_first_seen = time.time()
            elif time.time() - self._corruption_first_seen > 10:
                log.error(
                    "TRACKER CORRUPTION: side=%s entry_price=0 for >10s — forcing reconcile from broker",
                    self.tracker.side,
                )
                # Cooldown: skip re-trigger for 60s so the async reconcile
                # has time to land before we evaluate again.
                self._corruption_first_seen = time.time() + 60
                try:
                    import asyncio as _arec

                    from .tracker_reconciler import reconcile_tracker_from_broker

                    contract_id = getattr(self.config, "contract_id", None)
                    _arec.create_task(reconcile_tracker_from_broker(self, self.client, contract_id))
                except Exception:
                    log.exception("TRACKER CORRUPTION: failed to schedule reconcile")
        elif hasattr(self, "_corruption_first_seen"):
            self._corruption_first_seen = None

        if self.tracker.is_flat or self.tracker.entry_price <= 0:
            if not hasattr(self, "_last_mark_skip_log_ts"):
                self._last_mark_skip_log_ts = 0.0
            if time.time() - self._last_mark_skip_log_ts > 5.0:
                log.warning(
                    "update_mark SKIP: is_flat=%s entry_price=%.2f side=%s — peak_R won't update",
                    self.tracker.is_flat,
                    self.tracker.entry_price,
                    self.tracker.side,
                )
                self._last_mark_skip_log_ts = time.time()
            return
        new_r = self.tracker.update_mark(price)
        if new_r > prev_peak_R + 0.1 or (new_r >= 1.5 and prev_peak_R < 1.5):
            log.info(
                "update_mark: price=%.2f entry=%.2f stop=%.2f side=%s prev_peak=%.2f new_r=%.2f new_peak=%.2f",
                price,
                self.tracker.entry_price,
                self.tracker.stop_price,
                self.tracker.side,
                prev_peak_R,
                new_r,
                self.tracker.peak_R,
            )

        # +1.5R BE-lock: lock a small profit (entry ± 2 ticks = $10) so the
        # trade can never give back below break-even. Single-shot via
        # locked_BE flag on the tracker.
        # 2026-05-08: lowered from 2R to 1.5R based on 472-trade analysis —
        # only 3 trades (0.6%) ever crossed 2R while 19 (4.0%) crossed 1.5R,
        # and 84% of trades that hit 1.5R reversed before reaching 2R. With
        # BE-lock at 2R the feature was effectively dead; at 1.5R it triggers
        # ~6× more often, locking small profits on the trades that peak in
        # the 1.5–2R zone before reverting.
        BE_LOCK_R = 1.5
        if (
            getattr(self.tracker, "locked_BE", False)
            or self.tracker.peak_R < BE_LOCK_R
            or self.tracker.entry_price <= 0
        ):
            return

        BE_BUFFER_TICKS = 2
        buffer_pts = BE_BUFFER_TICKS * 0.25
        if self.tracker.side == "long":
            target_stop = self.tracker.entry_price + buffer_pts
        else:
            target_stop = self.tracker.entry_price - buffer_pts
        target_stop = _round_tick(target_stop)
        self.tracker.locked_BE = True
        # Synchronously reflect the BE-lock in the tracker AND _pending_trade
        # BEFORE awaiting the async modify_stop. Without this, a trade that
        # closes via reversal-signals or EE_LOCK between this tick and the
        # broker acknowledging the modify_stop call ends up persisting with
        # the original loss-side stop_price (the persist callback reads
        # _pending_trade at flatten-time). All of today's >2R trades closed
        # this way — pnl_r 3.83 (#427), 1.69 (#432) etc. all show stop_price
        # = entry-1R despite peak_R clearly clearing 2.0. Local mutation is
        # safe because the only consumer of stop_price downstream is the
        # broker_trades persist (chart label) and the position_watcher
        # broadcast (chart visualization) — neither sends new orders. The
        # async modify_stop still runs and updates the BROKER's stop order,
        # so a real stop hit would land at BE+; if the broker rejects, our
        # local view is optimistic but the next reconcile catches the drift.
        #
        # Relax-guard: if cont-trail already moved the stop tighter (long:
        # higher / short: lower), don't relax it locally. Mirror the same
        # rule modify_stop enforces server-side.
        cur_stop = self.tracker.stop_price
        is_long_side = self.tracker.side == "long"
        would_relax = (cur_stop > 0) and (
            (is_long_side and target_stop < cur_stop) or (not is_long_side and target_stop > cur_stop)
        )
        if not would_relax:
            self.tracker.stop_price = target_stop
            if self._pending_trade is not None:
                self._pending_trade["stop_price"] = target_stop
                self._set_pending_trade(self._pending_trade)
        log.info(
            "BE-lock at peak_R=%.2f: stop → %.2f (entry %s %d ticks, side=%s)",
            self.tracker.peak_R,
            target_stop,
            "+" if self.tracker.side == "long" else "-",
            BE_BUFFER_TICKS,
            self.tracker.side,
        )
        try:
            import asyncio as _abe

            _abe.create_task(self.modify_stop(target_stop))
        except Exception:
            log.warning("BE-lock modify_stop failed", exc_info=True)

    def _set_pending_trade(self, value: dict | None) -> None:
        """In-memory + disk update with tracker snapshot for restart recovery."""
        if value is not None:
            value = dict(value)  # don't mutate caller's dict
            value["tracker_snapshot"] = self.tracker.to_snapshot()
        self._pending_trade = value
        _save_pending_trade_to_disk(value)

    async def on_signal(self, signal: dict) -> dict | None:
        """Handle signal with dynamic position management.

        - Flat + signal → enter position
        - In position + same direction → trail stop to this zone (let it ride)
        - In position + opposite direction → exit and flip
        """
        action = signal.get("action", "")
        if action.lower() in ("skip", "hold", ""):
            return None

        # Lazy-init the asyncio.Lock so it binds to the running event loop
        # (the adapter is constructed in module init, before the loop exists).
        if self._signal_lock is None:
            self._signal_lock = asyncio.Lock()
        async with self._signal_lock:
            return await self._on_signal_locked(signal)

    async def _on_signal_locked(self, signal: dict) -> dict | None:
        action = signal.get("action", "")

        if self._halted:
            log.warning("Signal rejected — halted: %s", self._halt_reason)
            return {"rejected": True, "reason": self._halt_reason}

        # Stale-signal veto: if the signal was emitted more than
        # STALE_SIGNAL_MAX_AGE_S seconds ago, drop it. Today's audit showed
        # signals reaching the broker 2-200s after emission via the SignalR
        # relay path. NQ moves enough in even 2 seconds to invalidate the
        # zone-touch premise → adverse slip → stop hit at -1R within minutes.
        # Better to skip than to enter on a phantom price.
        STALE_SIGNAL_MAX_AGE_S = 1.5
        sig_ts = float(signal.get("ts", 0) or 0)
        if sig_ts > 0:
            age = time.time() - sig_ts
            if age > STALE_SIGNAL_MAX_AGE_S:
                log.info(
                    "Signal rejected — stale (age=%.2fs > %.1fs)",
                    age,
                    STALE_SIGNAL_MAX_AGE_S,
                )
                return {"rejected": True, "reason": "stale_signal", "age_s": age}

        # Confidence filter
        confidence = float(signal.get("confidence", 0) or 0)
        if confidence < MIN_CONFIDENCE:
            return None  # silent skip for low confidence

        rejection = self._check_risk()
        if rejection:
            return rejection

        is_long_signal = "long" in action.lower()
        signal_side = "long" if is_long_signal else "short"
        price = float(signal.get("price", 0) or 0)
        zone_price = float(signal.get("zone", 0) or 0)

        # --- FLAT: enter new position ---
        if self.tracker.is_flat:
            # 2026-05-06: pending-trade guard. Bug A: rapid trades fired
            # within the broker round-trip flush window corrupted tracker
            # price state — DB rows had entry/exit shifted from broker truth
            # because the new entry overwrote tracker fields before the
            # previous trade's persist callback finished reading them.
            # tracker.is_flat goes True the moment exit_price is set in
            # on_exit, but _pending_trade is only cleared after the persist
            # row is written. Refuse new entry while _pending_trade is
            # still populated — that means a close-flush is in flight.
            if self._pending_trade is not None:
                log.warning(
                    "Signal rejected — prior trade still flushing (_pending_trade not yet cleared, side=%s entry=%.2f)",
                    self._pending_trade.get("side"),
                    self._pending_trade.get("entry_price", 0) or 0,
                )
                return {"rejected": True, "reason": "prior_trade_flushing"}

            # Zone cooldown only applies to new entries
            if zone_price > 0:
                last_entry = self._zone_last_entry.get(zone_price, 0)
                if time.time() - last_entry < ZONE_COOLDOWN_S:
                    log.info("Signal rejected — zone %.2f cooldown", zone_price)
                    return {"rejected": True, "reason": "zone_cooldown"}

            result = await self._execute_entry(signal)
            if result and not result.get("rejected") and zone_price > 0:
                self._zone_last_entry[zone_price] = time.time()
                self._trail_count = 0
            return result

        # --- IN POSITION: same direction → trail / hold ---
        # Audit found 3/3 trailed trades closed losing (-$420). The original
        # logic tightened the stop on EVERY same-side zone touch, which on a
        # winning trend baked in early exits — counter-trend wicks would
        # take us out before the move continued.
        # New rule:
        #   - Trade UNDERWATER (peak_R <= 0): allow one defensive trail to
        #     entry+0.5R (the legacy behavior — fades a counter-zone).
        #   - Trade PROFITABLE (peak_R > 0): don't tighten. Either hold or
        #     pyramid, but never bake in a tighter stop on a winner just
        #     because price rotated to a same-direction zone.
        if signal_side == self.tracker.side:
            if self.tracker.entry_price == 0.0:
                log.info("Signal skipped — awaiting entry fill confirmation")
                return None

            entry = self.tracker.entry_price
            stop_dist = abs(entry - self.tracker.stop_price) if self.tracker.stop_price else DEFAULT_STOP_TICKS * 0.25
            new_stop = _round_tick(zone_price if zone_price > 0 else price)
            peak_R = float(self.tracker.peak_R or 0.0)

            # Profitable trade — no tighten. The pyramid / reversal-exit /
            # early-exit framework upstream will handle add/exit decisions.
            if peak_R > 0.0:
                log.info(
                    "Same-side signal at %.2f on profitable trade (peak_R=%.2f) — holding (no tighten)",
                    price,
                    peak_R,
                )
                return None

            # Trade still underwater — apply the legacy first-trail defense.
            if self.tracker.side == "long" and new_stop <= entry:
                if self._trail_count == 0:
                    new_stop = _round_tick(entry + stop_dist * 0.5)
                    log.info(
                        "Defensive trail at %.2f (peak_R=%.2f<=0) — stop → %.2f (trail #%d)",
                        price,
                        peak_R,
                        new_stop,
                        self._trail_count + 1,
                    )
                else:
                    log.info("Same-side signal at %.2f — already trailed once, holding", price)
                    return None
            elif self.tracker.side == "short" and new_stop >= entry:
                if self._trail_count == 0:
                    new_stop = _round_tick(entry - stop_dist * 0.5)
                    log.info(
                        "Defensive trail at %.2f (peak_R=%.2f<=0) — stop → %.2f (trail #%d)",
                        price,
                        peak_R,
                        new_stop,
                        self._trail_count + 1,
                    )
                else:
                    log.info("Same-side signal at %.2f — already trailed once, holding", price)
                    return None
            else:
                # zone is now beyond entry on the favorable side — let the
                # pyramid framework decide; defensive trail-tighten doesn't
                # apply here.
                log.info(
                    "Same-side signal at %.2f past entry on favorable side — holding for pyramid framework",
                    price,
                )
                return None

            self._trail_count += 1
            if self._pending_trade:
                self._pending_trade["trail_count"] = self._trail_count
            return await self.modify_stop(new_stop)

        # --- IN POSITION: opposite direction → exit and flip ---
        if self.tracker.entry_price == 0.0:
            log.info("REV signal — awaiting entry fill confirmation before flipping")
            return None

        log.info(
            "REV signal at %.2f (conf=%.3f) — exiting %s and flipping to %s",
            price,
            confidence,
            self.tracker.side,
            signal_side,
        )
        await self.flatten("flip_on_reversal")
        self._trail_count = 0

        # Now enter the opposite direction
        if zone_price > 0:
            self._zone_last_entry[zone_price] = time.time()
        return await self._execute_entry(signal)

    async def flatten(self, reason: str = "manual") -> dict:
        """Cancel stop order and liquidate position. Always reconciles with
        broker truth at the end so a stuck local tracker (broker is flat
        but we think we're not) self-heals and the trade is recorded.
        """
        # Capture the reason BEFORE we touch the broker so the exit-fill
        # handler (or recovery) can read it and attribute the close. The
        # next stop-hit path overwrites was_stop logic; this only matters
        # for non-stop exits.
        self._last_flatten_reason = reason
        if self.tracker.stop_order_id:
            try:
                await self.client.cancel_order(self.tracker.stop_order_id)
            except Exception:
                log.warning("Failed to cancel stop order %s", self.tracker.stop_order_id)

        if not self.tracker.is_flat:
            try:
                await self.client.liquidate_position()
            except Exception:
                log.exception("Failed to liquidate position")
            if self.tracker.entry_price == 0.0:
                self.tracker.on_exit(0.0)
                self._set_pending_trade(None)

        recovery = await self._recover_via_broker_truth(reason)

        log.info("Flatten requested (%s): session=$%.2f", reason, self.tracker.session_pnl)
        return {
            "action": "flatten",
            "reason": reason,
            "session_pnl": self.tracker.session_pnl,
            "recovery": recovery,
        }

    async def _recover_via_broker_truth(self, reason: str) -> dict | None:
        """Backstop for stuck-tracker bug: if broker shows no position but
        the local tracker thinks it has one, query Trade/search for the
        actual closing fills, compute realized PnL, and write the
        broker_trades row that on_stream_fill never wrote.

        Returns an audit dict on successful recovery, None when nothing
        was reconciled (already in sync) or the recovery itself failed.
        """

        try:
            positions = await self.client.search_open_positions()
        except Exception:
            log.warning("recovery: Position/searchOpen failed; skipping", exc_info=True)
            return None

        contract_id = getattr(self.config, "contract_id", None)
        broker_size = sum(
            int(p.get("size") or 0) for p in positions if not contract_id or p.get("contractId") == contract_id
        )
        if broker_size > 0:
            return None

        if self.tracker.is_flat and not self._pending_trade:
            return None

        entry_px = self.tracker.entry_price
        pt = self._pending_trade or _load_pending_trade_from_disk() or {}
        if not entry_px:
            entry_px = pt.get("entry_price") or 0.0

        if not entry_px:
            log.warning(
                "recovery (%s): broker is flat and tracker has no confirmed entry — "
                "clearing tracker state without writing broker_trades row",
                reason,
            )
            self.tracker.on_exit(0.0)
            self._set_pending_trade(None)
            return {"reconciled": True, "wrote_trade_row": False, "reason": "no_entry_fill"}

        side = pt.get("side") or self.tracker.side
        if not side:
            log.warning("recovery (%s): missing side; aborting", reason)
            return None

        entry_ts = pt.get("entry_fill_ts") or pt.get("ts")
        if isinstance(entry_ts, str):
            try:
                entry_ts = datetime.fromisoformat(entry_ts)
            except Exception:
                entry_ts = None
        if not isinstance(entry_ts, datetime):
            entry_ts = datetime.fromtimestamp(time.time() - 30 * 60, tz=timezone.utc)
        if entry_ts.tzinfo is None:
            entry_ts = entry_ts.replace(tzinfo=timezone.utc)
        start_ts = datetime.fromtimestamp(entry_ts.timestamp() - 5, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_ts = datetime.fromtimestamp(time.time() + 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        account_id = getattr(self.client, "_account_id", None)
        try:
            resp = await self.client._post(
                "/api/Trade/search",
                {
                    "accountId": account_id,
                    "startTimestamp": start_ts,
                    "endTimestamp": end_ts,
                },
            )
        except Exception:
            log.exception("recovery (%s): Trade/search failed", reason)
            return None

        trades = resp.get("trades") if isinstance(resp, dict) else None
        if not trades:
            # 2026-05-07: stuck-tracker rescue. Without this branch the
            # reconcile loop loops forever — every 60s it sees broker=0
            # local=1, halts, calls flatten → here Trade/search comes back
            # empty, recovery aborts, tracker still says short@28912 →
            # next reconcile, repeat. Today's incident: stuck at peak_R=130
            # (BE-locked stop made risk_unit ~0) for 8+ minutes, all
            # signals rejected due to halt.
            #
            # Broker is flat (already verified above) but Trade/search has
            # nothing — we missed the close fill and cannot reconstruct.
            # Reset tracker to flat WITHOUT calling on_exit (which would
            # compute huge phantom P&L from entry_price - 0). Lost trade
            # row is unrecoverable; staying halted is strictly worse.
            log.warning(
                "recovery (%s): no trade records returned from Trade/search — "
                "force-clearing tracker (side=%s entry=%.2f); lost trade row unrecoverable",
                reason,
                self.tracker.side,
                self.tracker.entry_price or 0.0,
            )
            self.tracker.side = None
            self.tracker.entry_price = 0.0
            self.tracker.stop_price = 0.0
            self.tracker.size = 0
            self.tracker.entry_order_id = None
            self.tracker.stop_order_id = None
            self.tracker.peak_R = 0.0
            self.tracker.locked_half_R = False
            self.tracker.locked_BE = False
            self._set_pending_trade(None)
            return {"reconciled": True, "wrote_trade_row": False, "reason": "no_trade_records"}

        entry_order_id = self.tracker.entry_order_id or pt.get("entry_order_id")
        exit_side = 1 if side == "long" else 0
        closing_fills: list[dict] = []
        gross_pnl = 0.0
        for t in trades:
            if contract_id and t.get("contractId") != contract_id:
                continue
            if t.get("voided"):
                continue
            if entry_order_id and t.get("orderId") == entry_order_id:
                continue
            if t.get("side") != exit_side:
                continue
            pnl_field = t.get("profitAndLoss")
            if pnl_field is None:
                continue
            closing_fills.append(t)
            try:
                gross_pnl += float(pnl_field)
            except (TypeError, ValueError):
                pass

        if not closing_fills:
            log.warning(
                "recovery (%s): broker flat but no closing fills found in Trade/search; "
                "clearing tracker without writing broker_trades row",
                reason,
            )
            self.tracker.on_exit(0.0)
            self._set_pending_trade(None)
            return {"reconciled": True, "wrote_trade_row": False, "reason": "no_closing_fills"}

        size_filled = sum(int(t.get("size") or 0) for t in closing_fills)
        weighted_px = (
            sum(float(t.get("price") or 0) * int(t.get("size") or 0) for t in closing_fills) / size_filled
            if size_filled
            else float(closing_fills[-1].get("price") or 0)
        )
        last_close_ts = max(
            (t.get("creationTimestamp") for t in closing_fills if t.get("creationTimestamp")),
            default=None,
        )
        closed_at = datetime.now(timezone.utc)
        if last_close_ts:
            try:
                closed_at = datetime.fromisoformat(str(last_close_ts).replace("Z", "+00:00"))
            except Exception:
                pass

        size = pt.get("size") or max(self.tracker.size or size_filled or 1, 1)
        direction = 1.0 if side == "long" else -1.0
        _MIN_RISK_PTS = MIN_STOP_TICKS * 0.25
        initial_stop_ticks = pt.get("stop_ticks") or 0
        if initial_stop_ticks > 0:
            raw_risk = initial_stop_ticks * 0.25
        else:
            stale_stop = pt.get("stop_price", 0) or self.tracker.stop_price or 0
            raw_risk = abs(entry_px - stale_stop) if stale_stop else DEFAULT_STOP_TICKS * 0.25
        risk_pts = max(raw_risk, _MIN_RISK_PTS)
        # Canonical recompute: when stop_ticks is the authoritative R
        # basis (set at signal time, immutable through trail walks),
        # derive stop_price and tp_price from it so the row's columns
        # stay internally consistent. Without this, recovery wrote
        # stale stop_price values from pt/tracker that didn't match
        # stop_ticks — producing rows like trade 522 where stop_ticks=27
        # but stop_price implied 166 ticks, making the chart widget
        # display a 41.5pt stop and 0.51 R:R for a trade that was
        # actually intended as a clean 6.75pt 1R stop with 4.7R realized.
        if initial_stop_ticks > 0:
            intended_pts = initial_stop_ticks * 0.25
            stop_price = _round_tick(entry_px - intended_pts if side == "long" else entry_px + intended_pts)
            recomputed_tp = _round_tick(entry_px + 2 * intended_pts if side == "long" else entry_px - 2 * intended_pts)
            pt["tp_price"] = recomputed_tp
        else:
            stop_price = pt.get("stop_price", 0) or self.tracker.stop_price or 0

        # Broker's gross_pnl is the source of truth — it accounts for partial
        # fills, multi-leg closes, and any size-mismatch quirks the tracker
        # missed. When it's available, derive pnl_pts (and the displayed
        # exit_price) BACKWARDS from it so the row reflects real economics
        # instead of a phantom break-even. Without this, reversal_signals
        # closes that happen near the entry price produced rows like
        # entry==exit but pnl_dollars=$150 and pnl_r=0 — the trainer would
        # learn "this short had 0R reward" which is flat-out wrong.
        if abs(gross_pnl) > 1e-6:
            pnl_dollars = round(gross_pnl, 2)
            pnl_pts = gross_pnl / (_NQ_POINT_VALUE * max(size, 1))
            # Reconstruct exit_price so the DB row's prices reflect actual
            # economics. Round to NQ tick grid.
            weighted_px = round((entry_px + direction * pnl_pts) * 4) / 4
            pnl_pts = direction * (weighted_px - entry_px)
        else:
            pnl_pts = direction * (weighted_px - entry_px)
            pnl_dollars = round(pnl_pts * _NQ_POINT_VALUE * size, 2)
        pnl_r = round(pnl_pts / risk_pts, 3)

        try:
            self.tracker.on_exit(weighted_px)
        except Exception:
            log.exception("recovery (%s): tracker.on_exit raised; continuing to record", reason)

        reasoning = pt.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning = dict(reasoning)
            reasoning.setdefault("recovered_via_broker_truth", True)
            reasoning.setdefault("recovery_reason", reason)
        else:
            reasoning = {"recovered_via_broker_truth": True, "recovery_reason": reason}

        now_utc = datetime.now(timezone.utc)
        _log_broker_trade(
            session_pnl=round(self.tracker.session_pnl, 2),
            # Prefer entry_fill_ts (actual broker fill) over pt["ts"]
            # (signal/submit moment). entry_ts (Trade/search authoritative
            # ts) wins when present, otherwise pt["entry_fill_ts"] from
            # the on_stream_fill capture, then fall back to submit ts.
            ts=entry_ts or pt.get("entry_fill_ts") or pt.get("ts") or now_utc,
            session_date=pt.get("session_date") or (entry_ts or now_utc).strftime("%Y-%m-%d"),
            symbol=pt.get("symbol") or "NQ",
            side=side,
            size=size,
            entry_price=entry_px,
            stop_price=pt.get("original_stop_price") or stop_price,
            final_stop_price=pt.get("stop_price") or self.tracker.stop_price or stop_price,
            tp_price=pt.get("tp_price"),
            exit_price=weighted_px,
            pnl_dollars=pnl_dollars,
            pnl_r=pnl_r,
            fill_latency_ms=None,
            slippage_ticks=None,
            was_stop=False,
            trail_count=pt.get("trail_count", 0),
            stop_ticks=pt.get("stop_ticks"),
            signal_action=pt.get("signal_action"),
            signal_confidence=pt.get("signal_confidence"),
            signal_zone=pt.get("signal_zone"),
            signal_trigger=pt.get("signal_trigger") or "recovered",
            signal_cont_p=pt.get("signal_cont_p"),
            signal_rev_p=pt.get("signal_rev_p"),
            orderflow_score=pt.get("orderflow_score"),
            reasoning=reasoning,
            closed_at=closed_at,
            topstepx_account_id=account_id,
            entry_order_id=pt.get("entry_order_id") or self.tracker.entry_order_id,
            exit_order_id=pt.get("exit_order_id"),
        )
        self._set_pending_trade(None)

        log.warning(
            "recovery (%s): broker_trades row WRITTEN from Trade/search — "
            "side=%s entry=%.2f exit=%.2f pnl=$%.2f pnl_r=%.3f closing_fills=%d",
            reason,
            side,
            entry_px,
            weighted_px,
            pnl_dollars,
            pnl_r,
            len(closing_fills),
        )
        return {
            "reconciled": True,
            "wrote_trade_row": True,
            "side": side,
            "entry_price": entry_px,
            "exit_price": weighted_px,
            "pnl_dollars": pnl_dollars,
            "pnl_r": pnl_r,
            "closing_fills": len(closing_fills),
        }

    async def add_to_position(self, add_contracts: int, price: float) -> dict | None:
        """Pyramid add — submit a market order in the same direction as the
        existing position and update the tracker. Stop stays where it is
        (risk unit unchanged; the add just compounds into the winner).
        """
        if self.tracker.is_flat or add_contracts <= 0:
            return None
        if self.tracker.size + add_contracts > self.config.max_position:
            log.info(
                "Pyramid add clipped: size=%d + add=%d > max=%d",
                self.tracker.size,
                add_contracts,
                self.config.max_position,
            )
            add_contracts = max(0, self.config.max_position - self.tracker.size)
            if add_contracts == 0:
                return {"rejected": True, "reason": "pyramid_at_cap"}

        order_action = "Buy" if self.tracker.side == "long" else "Sell"
        try:
            result = await self.client.place_market_order(order_action, add_contracts)
        except Exception:
            log.exception("Pyramid add order failed")
            return {"rejected": True, "reason": "order_failed"}

        if isinstance(result, dict) and not result.get("success", True):
            err = result.get("errorMessage", "order_rejected")
            log.warning("Pyramid add rejected: %s", err)
            return {"rejected": True, "reason": err}

        self.tracker.on_add(price=price, add_size=add_contracts)

        # Widen the stop order to cover the new total size so a hit closes
        # the whole position, not just the original contracts.
        if self.tracker.stop_order_id and self.tracker.stop_price > 0:
            try:
                await self.client.modify_order(self.tracker.stop_order_id, size=self.tracker.size)
            except Exception:
                log.warning("Failed to resize stop after pyramid add", exc_info=True)

        return {
            "action": "pyramid_add",
            "add_contracts": add_contracts,
            "total_size": self.tracker.size,
            "avg_entry": self.tracker.entry_price,
        }

    async def modify_stop(self, new_stop_price: float) -> dict | None:
        """Move existing stop order to new price.

        Defense-in-depth: a stop must never relax. Long stops only move up,
        short stops only move down. A misordered trail call that tried to
        widen risk would otherwise quietly increase exposure.
        """
        # 2026-05-05: orphan-position guard. Trade 377's stop filled at
        # 11:46:38 → tracker went flat → 3s later signal 2058 routed through
        # trail logic and called modify_stop with stop_order_id cleared,
        # which placed a brand-new BUY-STOP @ 27945. That zombie stop
        # triggered 47s later and opened an untracked LONG that arnold never
        # noticed (broker had to be flattened manually for +$715). Same bug
        # class as the trade 124 phantom-fill. If we're flat, we have
        # nothing to protect — refuse to place or modify a stop.
        if self.tracker.is_flat:
            log.warning(
                "modify_stop called while flat (new=%.2f) — refusing to place orphan stop",
                new_stop_price,
            )
            return {"action": "reject", "reason": "flat", "stop_price": 0.0}
        new_stop_price = _round_tick(new_stop_price)
        side = self.tracker.side
        cur_stop = self.tracker.stop_price
        # Diagnostic: track every stop-mutation attempt so we can post-mortem
        # mystery stop_price values in broker_trades (trade #85 had stop end
        # up at entry-2 ticks for a long with no log explaining how).
        log.info(
            "modify_stop call: side=%s cur=%.2f new=%.2f entry=%.2f peak_R=%.2f locked_BE=%s",
            side or "?",
            cur_stop,
            new_stop_price,
            self.tracker.entry_price,
            self.tracker.peak_R,
            getattr(self.tracker, "locked_BE", False),
        )
        if side == "long" and cur_stop > 0 and new_stop_price < cur_stop:
            log.warning("Refusing to relax long stop: %.2f → %.2f", cur_stop, new_stop_price)
            return {"action": "reject", "reason": "stop_relaxed", "stop_price": cur_stop}
        if side == "short" and cur_stop > 0 and new_stop_price > cur_stop:
            log.warning("Refusing to relax short stop: %.2f → %.2f", cur_stop, new_stop_price)
            return {"action": "reject", "reason": "stop_relaxed", "stop_price": cur_stop}
        if not self.tracker.stop_order_id:
            # No existing stop — place a new one
            try:
                stop_action = "Sell" if self.tracker.side == "long" else "Buy"
                result = await self.client.place_stop_order(stop_action, self.tracker.size or 1, new_stop_price)
                self.tracker.stop_order_id = result.get("orderId") if isinstance(result, dict) else None
                self.tracker.stop_price = new_stop_price
                # Mirror the new stop into _pending_trade so broker_trades.stop_price
                # reflects the actually-placed stop, not the stale entry-time one.
                # Trade #88 hit a 27062.50 stop but broker_trades showed 27062.50
                # because _pending_trade was never updated when the on-fill
                # re-anchor moved it to a tighter price.
                if self._pending_trade is not None:
                    self._pending_trade["stop_price"] = new_stop_price
                    self._set_pending_trade(self._pending_trade)
                log.info("New stop placed at %.2f", new_stop_price)
                return {"action": "new_stop", "stop_price": new_stop_price}
            except Exception:
                log.exception("Failed to place stop order")
                return None
        try:
            await self.client.modify_order(self.tracker.stop_order_id, stop_price=new_stop_price)
            self.tracker.stop_price = new_stop_price
            if self._pending_trade is not None:
                self._pending_trade["stop_price"] = new_stop_price
                self._set_pending_trade(self._pending_trade)
            log.info("Stop moved to %.2f", new_stop_price)
            return {"action": "modify_stop", "stop_price": new_stop_price}
        except Exception:
            log.exception("Failed to modify stop")
            return None

    def on_stream_fill(self, fill: dict) -> None:
        """Update tracker from real TopstepX fill (GatewayUserTrade).

        Correlates by orderId when present (entry_order_id vs stop_order_id) so that
        out-of-order entry/exit fills can't get swapped. Falls back to the
        entry_price==0.0 sentinel only when orderId is missing.
        """
        data = fill.get("data", fill)
        price = float(data.get("price", 0))
        if price == 0:
            return
        # TopstepX has used both camelCase and snake_case in the past — accept either.
        order_id = data.get("orderId") or data.get("order_id") or data.get("OrderId")
        # Broker-authoritative fill timestamp. Prefer this over
        # datetime.now() since the stream message can lag the actual
        # match by 100ms-seconds; using the broker's own ts keeps the
        # widget anchored to the bar where the fill really happened.
        _broker_fill_ts: datetime | None = None
        for key in ("creationTimestamp", "creation_timestamp", "CreationTimestamp", "timestamp"):
            v = data.get(key)
            if not v:
                continue
            try:
                _broker_fill_ts = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                if _broker_fill_ts.tzinfo is None:
                    _broker_fill_ts = _broker_fill_ts.replace(tzinfo=timezone.utc)
                break
            except Exception:
                continue
        log.info(
            "Fill processing: price=%.2f side=%s size=%s order_id=%s",
            price,
            data.get("side"),
            data.get("size"),
            order_id,
        )

        # Split-fill aggregation: if we've already classified this orderId,
        # this is a sibling leg (TopstepX split a size>1 order into multiple
        # size-1 fill events). Don't re-run the entry/exit decision tree —
        # that's what flipped tracker state and produced today's orphans.
        size = int(data.get("size") or 1)
        prior = self._fill_orderid_class.get(order_id) if order_id is not None else None
        if prior == "entry":
            # Sibling entry fill — grow the position via on_add, which handles
            # the volume-weighted average entry price correctly.
            if not self.tracker.is_flat:
                old_size = self.tracker.size
                self.tracker.on_add(price=price, add_size=size)
                log.info(
                    "Split-fill: order_id=%s same-orderId entry leg, size %d → %d (avg entry %.2f)",
                    order_id,
                    old_size,
                    self.tracker.size,
                    self.tracker.entry_price,
                )
                if self._pending_trade is not None:
                    self._pending_trade["size"] = self.tracker.size
                    self._pending_trade["entry_price"] = self.tracker.entry_price
                    self._set_pending_trade(self._pending_trade)
            else:
                log.warning(
                    "Split-fill: order_id=%s entry sibling but tracker is flat — dropping (race)",
                    order_id,
                )
            return
        if prior in ("exit", "stop"):
            # Sibling close fill — position already going flat from leg 1.
            # Drop idempotently; tracker state is already correct.
            log.info(
                "Split-fill: order_id=%s same-orderId %s sibling — already processed, dropping",
                order_id,
                prior,
            )
            return

        if self.tracker.is_flat:
            log.warning("Stream fill (%.2f, order_id=%s) arrived while flat — dropping", price, order_id)
            return

        # Decide entry vs exit. Prefer orderId match; fall back to sentinel.
        if order_id is not None and self.tracker.entry_order_id is not None:
            is_entry = order_id == self.tracker.entry_order_id
            is_stop = order_id == self.tracker.stop_order_id
            # An unknown orderId is most often arnold's OWN close/flatten
            # order (different orderId from the tracked entry+stop). Logging
            # the mismatch for visibility, but falling through to sentinel
            # detection so legitimate exits aren't classified as foreign.
            # Trade 124's fake -$85 / today's halt cycle motivated this:
            # the size_mismatch reconcile loop + _recover_via_broker_truth
            # in flatten() catch genuine desyncs without false positives.
            if not is_entry and not is_stop:
                log.warning(
                    "Unknown-orderId fill (order_id=%s, our_entry=%s our_stop=%s, "
                    "price=%.2f) — treating as exit; reconcile will halt if size diverges",
                    order_id,
                    self.tracker.entry_order_id,
                    self.tracker.stop_order_id,
                    price,
                )
                # Sentinel fallback: assume exit if entry already confirmed
                is_entry = self.tracker.entry_price == 0.0
                is_stop = not is_entry and self.tracker.stop_price > 0 and abs(price - self.tracker.stop_price) < 1.0
        else:
            is_entry = self.tracker.entry_price == 0.0
            is_stop = not is_entry and self.tracker.stop_price > 0 and abs(price - self.tracker.stop_price) < 1.0

        # 2026-05-05: was_stop price-fallback. Audit of 247 trades since 05-01
        # showed 30 stops with `exit_price == stop_price` exactly but
        # `was_stop=false`. Root cause: orderId-based detection misses when
        # modify_stop replaces the stop order (new orderId) and the new id
        # isn't tracked, OR when the stop fill arrives with stop_order_id
        # cleared by a prior race. The price-fallback only fires in the
        # unknown-orderId branch above, so a known-but-stale id missed it.
        # Now: regardless of orderId path, if this is an exit AND the price
        # is within 1 tick of the live stop_price AND the fill direction
        # would actually close the position, force was_stop=true. Same-tick
        # tolerance covers stop slippage; direction check (long stop = sell,
        # short stop = buy) prevents pyramid-add fills from being labeled
        # stops by accident.
        if not is_entry and not is_stop and self.tracker.stop_price > 0:
            stop_tol = abs(price - self.tracker.stop_price)
            fill_side = data.get("side")  # 0=BUY, 1=SELL per TopstepX
            tracker_side = self.tracker.side  # "long" / "short"
            close_dir_matches = (tracker_side == "long" and fill_side == 1) or (
                tracker_side == "short" and fill_side == 0
            )
            if stop_tol <= 0.25 and close_dir_matches:
                log.info(
                    "was_stop price-fallback: exit %.2f within 0.25t of stop %.2f "
                    "(close_dir=%s) — forcing was_stop=True",
                    price,
                    self.tracker.stop_price,
                    close_dir_matches,
                )
                is_stop = True

        # Stamp the classification for this orderId so subsequent split-fill
        # legs (same orderId) hit the aggregation branch above instead of
        # being reclassified as the opposite event.
        if order_id is not None:
            if is_entry:
                self._fill_orderid_class[order_id] = "entry"
            elif is_stop:
                self._fill_orderid_class[order_id] = "stop"
            else:
                self._fill_orderid_class[order_id] = "exit"

        if is_entry:
            # Idempotent: a duplicate entry fill with the same orderId must not double-set.
            if self.tracker.entry_price == 0.0:
                self.tracker.entry_price = price
                if self._pending_trade:
                    self._pending_trade["entry_price"] = price
                    # Prefer the broker's creationTimestamp from the stream
                    # frame; falls back to local arrival time when the
                    # broker didn't include one.
                    self._pending_trade["entry_fill_ts"] = _broker_fill_ts or datetime.now(timezone.utc)
                    self._set_pending_trade(self._pending_trade)
                log.info("Stream fill (entry confirmed): %.2f order_id=%s", price, order_id)

                # Adverse-slip kill switch: if the fill came in much worse
                # than the signal price, the trade is already deep in the
                # red before the stop has a chance — almost certain to hit
                # the stop within minutes. Don't sit in a -3.75pt-already-down
                # position; flatten immediately. Today's data: 6 of 9 stops
                # had adverse slip ≥17 ticks, all closed at near -1R.
                ADVERSE_SLIP_KILL_TICKS = 15
                if self._pending_trade:
                    sig_price = self._pending_trade.get("signal_price")
                    side = self._pending_trade.get("side") or self.tracker.side
                    if sig_price and side:
                        # Direction +1 = adverse for the trade
                        direction = 1.0 if side == "long" else -1.0
                        adverse_ticks = direction * (price - sig_price) / 0.25
                        if adverse_ticks > ADVERSE_SLIP_KILL_TICKS:
                            log.warning(
                                "Adverse-slip KILL: filled %.2f vs signal %.2f (%.1f ticks adverse > %d) — flattening",
                                price,
                                sig_price,
                                adverse_ticks,
                                ADVERSE_SLIP_KILL_TICKS,
                            )
                            try:
                                import asyncio as _aks

                                _aks.create_task(self.flatten("adverse_slip_kill"))
                            except Exception:
                                log.warning("adverse-slip flatten task failed", exc_info=True)
                            return

                # Re-anchor stop to ACTUAL fill price. The stop was originally
                # computed from the signal-time price, but slippage can move
                # the entry by 6-8pt during high-volatility moments (trades
                # 81/82 had 24-33 ticks adverse slip). Without re-anchoring,
                # the stop ends up 2-3x further than intended → the trade
                # risks far more than stop_ticks dollars but reads as -1R
                # in the data, hiding the cost from the trainer.
                log.info(
                    "Re-anchor check: pending=%s intended_ticks=%s cur_stop=%.2f",
                    bool(self._pending_trade),
                    self._pending_trade.get("stop_ticks") if self._pending_trade else None,
                    self.tracker.stop_price,
                )
                if self._pending_trade:
                    intended_ticks = self._pending_trade.get("stop_ticks")
                    cur_stop = self.tracker.stop_price
                    if intended_ticks and cur_stop > 0:
                        intended_pts = float(intended_ticks) * 0.25
                        if self.tracker.side == "long":
                            target_stop = _round_tick(price - intended_pts)
                        else:
                            target_stop = _round_tick(price + intended_pts)
                        # Only move if the difference materially changes risk
                        # (>= 2 ticks). Don't relax — modify_stop enforces
                        # only-tighten direction. So if slip went IN our favor
                        # (better fill), stop will move closer; if slip went
                        # AGAINST us, modify_stop will refuse to widen and
                        # we'll log a warning so the cost is visible.
                        if abs(target_stop - cur_stop) >= 0.5:
                            log.info(
                                "Re-anchoring stop after fill: cur=%.2f → target=%.2f "
                                "(slip=%.2f pts, intended=%d ticks from fill %.2f)",
                                cur_stop,
                                target_stop,
                                price - (self._pending_trade.get("signal_price") or price),
                                int(intended_ticks),
                                price,
                            )
                            try:
                                # Schedule the stop modify; can't await here
                                # (on_stream_fill is sync). modify_stop is
                                # idempotent + has only-tighten guards.
                                import asyncio as _a

                                _a.create_task(self.modify_stop(target_stop))
                            except Exception:
                                log.warning("stop re-anchor on fill failed", exc_info=True)
            else:
                log.debug("Duplicate entry fill ignored: %.2f order_id=%s", price, order_id)
            return

        # Exit path — but if the entry fill hasn't reconciled yet, we can't compute PnL.
        entry_px = self.tracker.entry_price
        if entry_px == 0.0:
            log.error(
                "Out-of-order exit fill (%.2f, order_id=%s) before entry confirmation — "
                "skipping; tracker left open until entry fill arrives",
                price,
                order_id,
            )
            return

        self.tracker.on_exit(exit_price=price, was_stop=is_stop)
        log.info(
            "Stream fill (exit): %.2f stop=%s order_id=%s trails=%d session_pnl=$%.2f",
            price,
            is_stop,
            order_id,
            self._trail_count,
            self.tracker.session_pnl,
        )

        # Capture the closing leg's TopstepX orderId on the pending dict
        # before it gets cleared in _log_broker_trade. Used downstream to
        # join the broker_trades row to the exact /api/Trade/search fill.
        if self._pending_trade is not None and order_id is not None:
            self._pending_trade["exit_order_id"] = order_id
            self._set_pending_trade(self._pending_trade)

        if entry_px:
            # Normal close: full _pending_trade context available.
            # Orphan close: position survived a process restart so we have
            # no signal context — but check disk for a saved context first
            # (saved at entry time) so reasoning + signal_* still land in
            # broker_trades. If both in-memory and disk are empty, we still
            # write a partial row so the realized outcome (entry/exit/pnl)
            # reaches the trainer.
            pt = self._pending_trade
            if not pt:
                pt = _load_pending_trade_from_disk()
                if pt:
                    log.info(
                        "Orphan exit recovered context from disk: side=%s entry=%.2f trigger=%s",
                        pt.get("side"),
                        pt.get("entry_price", 0) or 0,
                        pt.get("signal_trigger", ""),
                    )
            pt = pt or {}
            # Broker's authoritative exit-fill timestamp from the stream
            # frame (creationTimestamp). Falls back to local arrival time
            # — same pattern as entry_fill_ts. closed_at = now_utc anchors
            # the widget's right edge, so the broker timestamp is what
            # makes the closed widget land on the actual exit candle.
            now_utc = _broker_fill_ts or datetime.now(timezone.utc)
            side = pt.get("side") or self.tracker.side or ("long" if price > entry_px else "short")
            size = pt.get("size") or max(self.tracker.size or 1, 1)
            direction = 1.0 if side == "long" else -1.0
            pnl_pts = direction * (price - entry_px)
            pnl_dollars = pnl_pts * _NQ_POINT_VALUE * size

            # 2026-05-07: trust broker's profitAndLoss when present. Bug
            # surfaced today: trade #440 had DB entry/exit swapped from
            # tracker corruption, causing the price-derived pnl to come out
            # +$80 when broker reality was -$80 (sign inverted). Since the
            # exit fill carries the broker's authoritative pnl, use it to
            # cross-check + override price-derived math when they disagree.
            broker_pnl = data.get("profitAndLoss")
            if broker_pnl is not None:
                try:
                    broker_pnl = float(broker_pnl)
                    # Allow $1 tolerance for rounding; flag anything bigger as
                    # a corruption-event so the trainer doesn't see fake R.
                    if abs(broker_pnl - pnl_dollars) > 1.0:
                        log.warning(
                            "pnl mismatch: tracker computed $%.2f but broker reports $%.2f "
                            "(side=%s tracker_entry=%.2f close=%.2f size=%d). "
                            "Back-deriving entry_px from broker truth.",
                            pnl_dollars,
                            broker_pnl,
                            side,
                            entry_px,
                            price,
                            size,
                        )
                        pnl_dollars = broker_pnl
                        # The CLOSE price (`price` arg) is authoritative — it's
                        # from the broker's stream frame. The stale value is
                        # `entry_px` from the in-memory tracker (can desync over
                        # long-lived positions or SSE reconnects). Mutate
                        # entry_px, NOT price — the previous logic back-derived
                        # exit_price into a phantom value that no candle ever
                        # traded at (trades 530 + 534 on 2026-05-08).
                        pnl_pts = broker_pnl / (_NQ_POINT_VALUE * max(size, 1))
                        entry_px = round((price - direction * pnl_pts) * 4) / 4
                except (TypeError, ValueError):
                    pass
            stop_price = pt.get("stop_price", 0) or self.tracker.stop_price or 0
            # R is realized-pnl-vs-INITIAL-risk, never against the trailed stop.
            # When a stop trails close before TP, entry−current_stop shrinks
            # and a small dollar win turns into +3-5R while losses still hit
            # at the original distance — net result was +R but −equity in
            # the stats UI. Prefer stop_ticks (captured at entry, never modified
            # by trailing); fall back to entry-vs-current-stop for orphan
            # trades that lost _pending_trade context.
            _MIN_RISK_PTS = MIN_STOP_TICKS * 0.25
            initial_stop_ticks = pt.get("stop_ticks") or 0
            if initial_stop_ticks > 0:
                raw_risk = initial_stop_ticks * 0.25
            elif stop_price:
                raw_risk = abs(entry_px - stop_price)
            else:
                raw_risk = DEFAULT_STOP_TICKS * 0.25
            risk_pts = max(raw_risk, _MIN_RISK_PTS)
            pnl_r = pnl_pts / risk_pts
            if raw_risk < _MIN_RISK_PTS:
                log.warning(
                    "pnl_r risk-floor applied: raw_risk=%.2fpt < %.2fpt; pnl_r capped to %.3f (was %.3f)",
                    raw_risk,
                    _MIN_RISK_PTS,
                    pnl_r,
                    pnl_pts / max(raw_risk, 0.25),
                )

            # Slippage = adverse fill vs. intended signal price, in NQ ticks.
            # Positive = paid worse than signal (long filled higher / short filled lower).
            signal_price = pt.get("signal_price")
            slippage_ticks = None
            if signal_price:
                slippage_ticks = round(direction * (entry_px - signal_price) / 0.25, 2)

            # Latency = signal-dispatch → entry-fill (ms). End-to-end including order ack.
            submit_ts = pt.get("entry_submit_ts")
            fill_ts = pt.get("entry_fill_ts")
            fill_latency_ms = None
            if submit_ts and fill_ts:
                fill_latency_ms = round((fill_ts - submit_ts).total_seconds() * 1000.0, 1)

            if not self._pending_trade:
                log.warning(
                    "Orphan exit: entry_px=%.2f exit=%.2f pnl=$%.2f pnl_r=%.3f side=%s "
                    "(no _pending_trade; persisting partial row)",
                    entry_px,
                    price,
                    pnl_dollars,
                    pnl_r,
                    side,
                )

            # Carry the live TopstepX account id through to the persist layer
            # so it can resolve the owning sports profile via
            # profiles.topstepx_account_id (forward-compat for multi-profile).
            tsx_account_id = getattr(self.client, "_account_id", None)

            # ts column = actual ENTRY FILL time. pt["ts"] is the order-submit
            # moment which can lead the fill by minutes for limit / stop-
            # limit entries — the chart widget anchors to ts, so using
            # submit time made widgets visually appear 5-10 min before the
            # candle where the trade actually filled. entry_fill_ts is set
            # in on_stream_fill at the broker confirmation moment.
            _log_broker_trade(
                session_pnl=round(self.tracker.session_pnl, 2),
                ts=pt.get("entry_fill_ts") or pt.get("ts") or now_utc,
                session_date=pt.get("session_date") or now_utc.strftime("%Y-%m-%d"),
                symbol=pt.get("symbol") or "NQ",
                side=side,
                size=size,
                entry_price=entry_px,
                stop_price=pt.get("original_stop_price") or stop_price,
                final_stop_price=pt.get("stop_price") or self.tracker.stop_price or stop_price,
                tp_price=pt.get("tp_price"),
                exit_price=price,
                pnl_dollars=round(pnl_dollars, 2),
                pnl_r=round(pnl_r, 3),
                fill_latency_ms=fill_latency_ms,
                slippage_ticks=slippage_ticks,
                was_stop=is_stop,
                trail_count=pt.get("trail_count", 0),
                stop_ticks=pt.get("stop_ticks"),
                signal_action=pt.get("signal_action"),
                signal_confidence=pt.get("signal_confidence"),
                signal_zone=pt.get("signal_zone"),
                signal_trigger=pt.get("signal_trigger") or ("orphan" if not self._pending_trade else None),
                signal_cont_p=pt.get("signal_cont_p"),
                signal_rev_p=pt.get("signal_rev_p"),
                orderflow_score=pt.get("orderflow_score"),
                reasoning=pt.get("reasoning"),
                closed_at=now_utc,
                topstepx_account_id=tsx_account_id,
                flatten_reason=self._last_flatten_reason,
                entry_order_id=pt.get("entry_order_id"),
                exit_order_id=pt.get("exit_order_id") or order_id,
            )
            self._set_pending_trade(None)
            # Reason is consumed by exit_reason — drop it so the next stop-
            # hit doesn't inherit a stale label.
            self._last_flatten_reason = None

    def reset_session(self) -> None:
        """Daily midnight reset."""
        self._halted = False
        self._halt_reason = ""
        self._zone_last_entry.clear()
        self._trail_count = 0
        self.tracker.reset_session()
        log.info("Session reset")

    def _check_risk(self) -> dict | None:
        """Run risk checks.

        In reckless paper mode (RECKLESS_LEARNING_MODE=1, the default on the
        practice account), all dollar/streak-based halts are bypassed: the
        whole point of paper is maximum data velocity, and the user has
        explicitly accepted unbounded losses to learn. Bug-catching halts
        (orphan_position, size_mismatch, account_violation, reconcile_failed)
        are NOT touched here — those fire in other code paths and remain
        active in every mode. Strict-mode gates fire normally.
        """
        if not _RECKLESS:
            if self.tracker.exceeds_daily_loss(self.config.max_daily_loss):
                self._halt(f"daily loss limit ${self.config.max_daily_loss}")
                return {"rejected": True, "reason": self._halt_reason}

            if self.tracker.exceeds_trailing_dd(self.config.max_trailing_dd):
                self._halt(f"trailing DD limit ${self.config.max_trailing_dd}")
                return {"rejected": True, "reason": self._halt_reason}

            if self.tracker.consecutive_stops >= 3:
                self._halt(f"{self.tracker.consecutive_stops} consecutive stops")
                return {"rejected": True, "reason": self._halt_reason}

        if time.time() - self.tracker.last_trade_ts < MIN_TRADE_INTERVAL_S:
            return {"rejected": True, "reason": "min_interval"}

        return None

    async def _execute_entry(self, signal: dict) -> dict:
        """Place a bracketed market+stop entry with risk-based sizing.

        Single atomic /api/Order/place call attaches the stop-loss bracket
        anchored to the entry FILL price (broker computes stop = fill ± ticks),
        so signal→fill slippage no longer distorts the R-ratio. TP is not
        placed as an order — kept as a reference value on _pending_trade only.
        """
        action = signal["action"]
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        price = float(signal.get("price", 0) or 0)
        stop_price = float(signal.get("stop_price", 0) or 0)
        confidence = float(signal.get("confidence", 0) or 0)

        # Validate/adjust stop distance
        stop_dist_ticks = abs(stop_price - price) / 0.25 if stop_price > 0 else DEFAULT_STOP_TICKS
        stop_dist_ticks = int(max(MIN_STOP_TICKS, min(MAX_STOP_TICKS, stop_dist_ticks)))
        offset = stop_dist_ticks * 0.25
        stop_price = _round_tick(price - offset if is_long else price + offset)

        # Confidence-scaled sizing: BASE_SIZE × size_multiplier(confidence).
        # size_multiplier tiers (from src.rl.confidence):
        #   conf >= 0.85 → 1.5x → 2 contracts
        #   0.70-0.85   → 1.0x → 1 contract
        #   0.50-0.70   → 0.6x → 1 contract (floor)
        #   0.30-0.50   → 0.3x → 1 contract (floor)
        #   <0.30       → 0.5x reckless / 0.0 strict
        # Replaces the risk-pct / drawdown-budget / SizeModel path:
        # signal.get("size") carried the size_model_v5.joblib multiplier; that
        # model is now bypassed here. size_model_v5.joblib stays in the pool
        # unused for future reference.

        size_mult = _size_multiplier(confidence)
        if size_mult <= 0.0:
            # Strict mode: below-threshold entry rejected outright.
            log.info(
                "size_multiplier skip: conf=%.3f → mult=%.2f; rejecting entry",
                confidence,
                size_mult,
            )
            return {"rejected": True, "reason": "size_multiplier_skip"}
        # Reckless paper mode: hard-cap at 1 contract regardless of
        # confidence. We only care about R-ratio for learning; absolute
        # dollar risk should be minimized so a long losing streak doesn't
        # eat into the practice account faster than the trainer can absorb
        # the signal. Strict mode keeps the original conf-scaled sizing.
        if _RECKLESS:
            size = 1
        else:
            size = max(1, min(round(BASE_SIZE * size_mult), self.config.max_position))

        log.info(
            "Sizing: conf=%.3f → size_mult=%.2f → %d contracts (BASE_SIZE=%d, max_pos=%d)",
            confidence,
            size_mult,
            size,
            BASE_SIZE,
            self.config.max_position,
        )

        if not self.tracker.is_flat:
            await self.flatten("flip")

        # Stamp the actual order-submit moment so fill_latency_ms measures
        # submit→fill, not "_pending_trade init → fill arrival" (which would
        # include stop placement + retries + tracker init and bloats the
        # number — was 67.7s on trade #54).
        entry_submit_ts = datetime.now(timezone.utc)
        log.info(
            "=== ENTRY === %s size=%d stop=%.2f (%d ticks) conf=%.3f cont_p=%.3f rev_p=%.3f zone=%.2f",
            action,
            size,
            stop_price,
            stop_dist_ticks,
            confidence,
            float(signal.get("cont_p", 0) or 0),
            float(signal.get("rev_p", 0) or 0),
            float(signal.get("zone", 0) or 0),
        )

        # 2026-05-08: pre-claim tracker state BEFORE submitting the entry order.
        # The dropped-fill bug recurs because TopstepX fills can arrive before
        # `await place_market_order` even returns — i.e. during the await. If
        # tracker.is_flat is still True at that moment, on_stream_fill's "while
        # flat" guard drops the fill, and entry_price stays at 0 forever.
        # Setting `side` here makes is_flat=False BEFORE the await, so any fill
        # that arrives during the await is correctly classified as our entry.
        # entry_price stays at 0 until the fill writes the real price. If the
        # order is rejected we _reset_tracker_for_rollback().
        side = "long" if is_long else "short"
        self.tracker.side = side
        self.tracker.size = size
        self.tracker.stop_price = stop_price
        log.info("Position opening: %s %d stop=%.2f (waiting for entry fill)", side, size, stop_price)

        # Network flakiness to api.topstepx.com surfaces as ConnectTimeout.
        # Retry once before failing so a single dropped connection doesn't
        # cost an entire setup. Two attempts is the cap — beyond that we
        # genuinely cannot place and should bail out so the caller doesn't
        # think the order is in flight.
        result = None
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                # Bracketed market order: the broker attaches the stop-loss
                # atomically and anchors it to the ENTRY FILL PRICE — slippage
                # between signal-time and fill is fully absorbed. Replaces
                # the old place_market + separate place_stop + widen-on-error
                # + verify-live dance (~135 lines), all of which existed only
                # to paper over the signal/fill price gap.
                result = await self.client.place_market_order_with_stop_bracket(order_action, size, stop_dist_ticks)
                break
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "place_market_order_with_stop_bracket attempt %d/2 failed: %s — retrying",
                    attempt,
                    type(exc).__name__,
                )
        if result is None:
            log.error("Market order failed after 2 attempts: %s", last_exc)
            self._reset_tracker_for_rollback()
            return {"rejected": True, "reason": "order_failed"}

        if isinstance(result, dict) and not result.get("success", True):
            err = result.get("errorMessage", "order_rejected")
            err_code = result.get("errorCode")
            log.warning("Market order rejected (errorCode=%s): %s", err_code, err)
            # 2026-05-07: don't halt on session/instrument-level rejections.
            # TopstepX errorCode 2 covers many things including transient
            # "instrument is not in an active trading status" messages
            # emitted between sessions / during settlement windows. Halting
            # on that froze the broker after every regular-session close
            # at 21:00 UTC — when the next session reopened, signals fired
            # but everything was rejected with `halted: account permanent
            # violation`. Only halt when the message actually indicates an
            # account-level issue (drawdown breach, daily loss limit hit,
            # position cap, account locked) — those are sticky conditions
            # that won't clear without intervention.
            err_lower = str(err).lower()
            session_signals = (
                "not in an active trading status",
                "instrument not tradeable",
                "market closed",
                "outside trading hours",
                "trading is currently unavailable",
            )
            account_signals = (
                "permanent violation",
                "daily loss",
                "maximum loss",
                "trailing drawdown",
                "max position",
                "account locked",
                "account has been",
                "violated",
            )
            is_session_error = any(s in err_lower for s in session_signals)
            is_account_error = any(s in err_lower for s in account_signals)
            if is_account_error and not is_session_error:
                self._halt(f"account violation: {err}")
            elif err_code == 2 and not is_session_error and not is_account_error:
                # Unknown code-2 message that doesn't match either bucket —
                # log loudly so we can refine the lists, but don't halt.
                log.warning(
                    "Order rejected with errorCode=2 but message doesn't match "
                    "session/account patterns; not halting: %s",
                    err,
                )
            self._reset_tracker_for_rollback()
            return {"rejected": True, "reason": err}

        entry_order_id = result.get("orderId") if isinstance(result, dict) else None
        # tracker.side / size / stop_price were already set BEFORE the order
        # submission to avoid the dropped-fill race. Just sync entry_order_id.
        self.tracker.entry_order_id = entry_order_id

        # Bracket-stop discovery: TopstepX attaches the stop atomically when
        # place_market_order_with_stop_bracket succeeds — the parent order's
        # orderId is the entry; the bracket stop gets its own orderId visible
        # via /api/Order/searchOpen. Poll briefly (with retry) to find it.
        # The bracket is anchored to fill price server-side, so we don't need
        # to recompute or widen on our end — the broker handles slippage.
        stop_side_int = 1 if is_long else 0  # stop side = opposite of entry
        stop_order_id = None
        for attempt in (1, 2, 3, 4, 5):
            try:
                open_orders = await self.client._post("/api/Order/searchOpen", {"accountId": self.client._account_id})
                orders = open_orders.get("orders") or []
                bracket_stops = [
                    o
                    for o in orders
                    if o.get("contractId") == self.config.contract_id
                    and int(o.get("type") or 0) == 4  # STOP_MARKET
                    and int(o.get("side") or -1) == stop_side_int
                ]
                if bracket_stops:
                    # Newest = highest orderId (broker assigns monotonically).
                    newest = max(bracket_stops, key=lambda o: int(o.get("id") or 0))
                    candidate_id = int(newest.get("id"))
                    broker_stop_price_raw = newest.get("stopPrice")
                    # Brackets are anchored at fill time — pre-fill the order
                    # may exist but stopPrice can be 0 or None. Only accept
                    # the discovery once the broker has actually computed
                    # the fill-anchored stopPrice. Keep polling otherwise.
                    try:
                        broker_stop_price = float(broker_stop_price_raw) if broker_stop_price_raw is not None else 0.0
                    except (TypeError, ValueError):
                        broker_stop_price = 0.0
                    if broker_stop_price > 0:
                        stop_order_id = candidate_id
                        stop_price = broker_stop_price
                        self.tracker.stop_price = stop_price
                        log.info(
                            "Bracket stop confirmed: orderId=%d stopPrice=%.2f (attempt %d)",
                            stop_order_id,
                            stop_price,
                            attempt,
                        )
                        break
                    log.debug(
                        "Bracket stop seen (orderId=%d) but stopPrice not yet anchored — retrying",
                        candidate_id,
                    )
            except Exception:
                log.warning("Bracket-stop discovery attempt %d/5 raised", attempt, exc_info=True)
            await asyncio.sleep(0.2)

        if stop_order_id is None:
            # Bracket attachment is supposed to be atomic — if we can't find
            # the stop after 1s of polling, something's wrong (broker bug or
            # the bracket payload was silently dropped). Liquidate to bound
            # risk: a naked position with the wrong slippage profile is
            # exactly what we were trying to avoid.
            log.error("Bracket stop not found after 5 attempts — flattening entry to avoid naked position")
            self._reset_tracker_for_rollback()
            try:
                await self.client.liquidate_position()
            except Exception:
                log.exception("Emergency liquidate after missing bracket also failed — POSITION MAY BE OPEN")
            self._halt("bracket_stop_missing")
            return {"rejected": True, "reason": "bracket_stop_missing"}

        # tracker.side / size / entry_order_id were set above pre-submit.
        # stop_order_id is now the broker's bracket-anchored stop order id.
        self.tracker.stop_order_id = stop_order_id

        now = datetime.now(timezone.utc)
        # TP = 2R from entry, anchored to the broker's bracket stop (which
        # the broker positioned from the actual fill). With stop = entry ± 1R,
        # TP at entry ± 2R = stop ± 3R. This way the chart's 2R band stays
        # honest even after slippage shifts the entry away from signal price.
        # (No TP order is placed; tp_price is reference-only for the widget
        # + reasoning blobs — see "No TP bracket (stop only)" decision.)
        tp_price = _round_tick(stop_price + offset * 3 if is_long else stop_price - offset * 3)
        self._pending_trade = {
            "ts": now,
            "session_date": now.strftime("%Y-%m-%d"),
            "symbol": "NQ",
            "side": side,
            "size": size,
            "stop_price": stop_price,
            # Captured once at entry, never mutated by modify_stop.
            # The widget reads this to draw the planned-1R band so R:R
            # stays correct after BE-lock / cont-trail walks shift stop_price.
            "original_stop_price": stop_price,
            "tp_price": tp_price,
            "stop_ticks": stop_dist_ticks,
            "signal_price": price,
            "entry_submit_ts": entry_submit_ts,
            "entry_fill_ts": None,
            # TopstepX broker order ids — persisted into broker_trades
            # at close time so backfill/realignment can join unambiguously
            # against /api/Trade/search (single source of truth, no
            # price-cluster ambiguity).
            "entry_order_id": entry_order_id,
            "exit_order_id": None,  # set in on_stream_fill exit path
            "signal_action": action,
            "signal_confidence": float(signal.get("confidence", 0) or 0),
            "signal_zone": float(signal.get("zone", signal.get("zone_price", 0)) or 0),
            "signal_trigger": str(signal.get("trigger", "")),
            "signal_cont_p": float(signal.get("cont_p", 0) or 0),
            "signal_rev_p": float(signal.get("rev_p", 0) or 0),
            "orderflow_score": float(signal.get("orderflow_score", 0) or 0),
            "reasoning": signal.get("reasoning") if isinstance(signal.get("reasoning"), dict) else None,
            "trail_count": 0,
            "current_zone_R": 0.0,  # last zone advance level (in R units)
        }
        # Mirror to disk so a container restart between this point and the
        # close fill doesn't strip reasoning + signal context (orphan loss).
        # _set_pending_trade also embeds a tracker snapshot for Layer 2 recovery.
        self._set_pending_trade(self._pending_trade)

        return {
            "action": action,
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "stop_order_id": stop_order_id,
        }

    async def _trail_stop(self, signal: dict) -> dict | None:
        """Move stop to new price from signal."""
        new_stop = signal.get("stop_price", 0)
        if new_stop and new_stop > 0:
            return await self.modify_stop(new_stop)
        return None

    def halt(self, reason: str) -> None:
        """Halt trading for the rest of the session."""
        self._halt(reason)

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        log.warning("HALTED: %s (session_pnl=$%.2f)", reason, self.tracker.session_pnl)
