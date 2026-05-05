"""TopstepX broker adapter — dynamic stop management with model signals.

Instead of flattening on every new signal, manages the position:
- Same direction signal at new zone → trail stop to previous zone (let winners ride)
- Opposite direction signal → exit and flip
- SKIP → hold current position

This implements the hybrid design: GBT decides at each level whether to
hold, tighten, or exit.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# RECKLESS_LEARNING_MODE (env, default 1 = on): we have only 16 trades total,
# all from 04-23/04-24 — the model can't learn its own outcomes if the live
# gate filters everything out. Loosened thresholds let weak signals through
# so the trainer accumulates labelled (obs, action, realized_pnl_r) tuples.
# Risk caps (daily loss / trailing DD / size) stay intact — they bound the
# downside, not the take rate. Set RECKLESS_LEARNING_MODE=0 to retighten.
import os as _os

_RECKLESS = _os.environ.get("RECKLESS_LEARNING_MODE", "1") != "0"

MIN_TRADE_INTERVAL_S = 10.0 if _RECKLESS else 30.0
# In reckless paper-trading mode this MUST match level_monitor's broker
# conf floor (0.05) — otherwise this filter silently rejects pivot
# signals the upstream gate already approved. Today's 12:31 pivot
# fired 4 signals with OF=0.40 but conf=0.078, all silently dropped.
MIN_CONFIDENCE = 0.05 if _RECKLESS else 0.30
ZONE_COOLDOWN_S = 30.0 if _RECKLESS else 120.0  # don't re-enter same zone within N seconds
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

# Risk-based sizing: risk 1-2% of max drawdown per trade
RISK_PCT_BASE = 0.015  # 1.5% of drawdown for normal signals (conf 0.30-0.70)
RISK_PCT_HIGH = 0.02  # 2% for high confidence (conf > 0.70)


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
    exit_reason = "STOP" if kwargs.get("was_stop") else "SIGNAL"
    if kwargs.get("trail_count", 0) > 0:
        exit_reason = f"TRAILED({kwargs['trail_count']})"

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

    def update_mark_and_check_be_lock(self, price: float) -> None:
        """Per-tick: update peak_R AND fire BE-lock at +2R if not yet locked.

        Called from BOTH paths:
          - FastAPI level_monitor._check_positions (autonomous broker tick)
          - trading_service tick handler (subprocess broker tick)

        Without this method living on the adapter, BE-lock only ran in the
        FastAPI process — but trading_service's tracker is a different
        instance, so its peak_R stayed at 0 and BE-lock never fired on the
        actual production trades. This makes BE-lock work on whichever
        process owns the live position.
        """
        if self.tracker.is_flat or self.tracker.entry_price <= 0:
            return
        self.tracker.update_mark(price)

        # +2R BE-lock: lock a small profit (entry ± 2 ticks = $10) so the
        # trade can never give back below break-even. Single-shot via
        # locked_BE flag on the tracker.
        if getattr(self.tracker, "locked_BE", False) or self.tracker.peak_R < 2.0 or self.tracker.entry_price <= 0:
            return

        BE_BUFFER_TICKS = 2
        buffer_pts = BE_BUFFER_TICKS * 0.25
        if self.tracker.side == "long":
            target_stop = self.tracker.entry_price + buffer_pts
        else:
            target_stop = self.tracker.entry_price - buffer_pts
        target_stop = _round_tick(target_stop)
        self.tracker.locked_BE = True
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
            log.warning("recovery (%s): no trade records returned from Trade/search", reason)
            return None

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
        stop_price = pt.get("stop_price", 0) or self.tracker.stop_price or 0
        _MIN_RISK_PTS = MIN_STOP_TICKS * 0.25
        initial_stop_ticks = pt.get("stop_ticks") or 0
        if initial_stop_ticks > 0:
            raw_risk = initial_stop_ticks * 0.25
        elif stop_price:
            raw_risk = abs(entry_px - stop_price)
        else:
            raw_risk = DEFAULT_STOP_TICKS * 0.25
        risk_pts = max(raw_risk, _MIN_RISK_PTS)

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
            ts=pt.get("ts") or entry_ts or now_utc,
            session_date=pt.get("session_date") or (entry_ts or now_utc).strftime("%Y-%m-%d"),
            symbol=pt.get("symbol") or "NQ",
            side=side,
            size=size,
            entry_price=entry_px,
            stop_price=stop_price,
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
        log.info(
            "Fill processing: price=%.2f side=%s size=%s order_id=%s",
            price,
            data.get("side"),
            data.get("size"),
            order_id,
        )

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

        if is_entry:
            # Idempotent: a duplicate entry fill with the same orderId must not double-set.
            if self.tracker.entry_price == 0.0:
                self.tracker.entry_price = price
                if self._pending_trade:
                    self._pending_trade["entry_price"] = price
                    self._pending_trade["entry_fill_ts"] = datetime.now(timezone.utc)
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
            now_utc = datetime.now(timezone.utc)
            side = pt.get("side") or self.tracker.side or ("long" if price > entry_px else "short")
            size = pt.get("size") or max(self.tracker.size or 1, 1)
            direction = 1.0 if side == "long" else -1.0
            pnl_pts = direction * (price - entry_px)
            pnl_dollars = pnl_pts * _NQ_POINT_VALUE * size
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

            _log_broker_trade(
                session_pnl=round(self.tracker.session_pnl, 2),
                ts=pt.get("ts") or now_utc,
                session_date=pt.get("session_date") or now_utc.strftime("%Y-%m-%d"),
                symbol=pt.get("symbol") or "NQ",
                side=side,
                size=size,
                entry_price=entry_px,
                stop_price=stop_price,
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
            )
            self._set_pending_trade(None)

    def reset_session(self) -> None:
        """Daily midnight reset."""
        self._halted = False
        self._halt_reason = ""
        self._zone_last_entry.clear()
        self._trail_count = 0
        self.tracker.reset_session()
        log.info("Session reset")

    def _check_risk(self) -> dict | None:
        """Run risk checks."""
        if self.tracker.exceeds_daily_loss(self.config.max_daily_loss):
            self._halt(f"daily loss limit ${self.config.max_daily_loss}")
            return {"rejected": True, "reason": self._halt_reason}

        if self.tracker.exceeds_trailing_dd(self.config.max_trailing_dd):
            self._halt(f"trailing DD limit ${self.config.max_trailing_dd}")
            return {"rejected": True, "reason": self._halt_reason}

        if self.tracker.consecutive_stops >= 3:
            self._halt("3 consecutive stops")
            return {"rejected": True, "reason": self._halt_reason}

        if time.time() - self.tracker.last_trade_ts < MIN_TRADE_INTERVAL_S:
            return {"rejected": True, "reason": "min_interval"}

        return None

    async def _execute_entry(self, signal: dict) -> dict:
        """Place market + stop orders with risk-based sizing."""
        action = signal["action"]
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        price = float(signal.get("price", 0) or 0)
        stop_price = float(signal.get("stop_price", 0) or 0)
        confidence = float(signal.get("confidence", 0) or 0)

        # Validate/adjust stop distance
        stop_dist_ticks = abs(stop_price - price) / 0.25 if stop_price > 0 else DEFAULT_STOP_TICKS
        stop_dist_ticks = int(max(MIN_STOP_TICKS, min(MAX_STOP_TICKS, stop_dist_ticks)))
        offset = stop_dist_ticks * 0.25
        stop_price = _round_tick(price - offset if is_long else price + offset)

        # Risk-based sizing — base contracts derived from drawdown budget +
        # stop distance, then scaled by the trained SizeModel multiplier from
        # the signal (composite of DQN observation + narrative — tiers 0.0,
        # 0.3, 0.6, 1.0, 1.5 per rl/agent/size_model.py:SIZE_TIERS). Without
        # the multiplier, sizing was a binary 1.5%/2% confidence flip with
        # the SizeModel output dropped on the floor.
        risk_pct = RISK_PCT_HIGH if confidence > 0.70 else RISK_PCT_BASE
        risk_dollars = self.config.max_trailing_dd * risk_pct
        risk_per_contract = stop_dist_ticks * _NQ_TICK_VALUE
        base_size = max(
            1,
            min(
                int(risk_dollars / risk_per_contract),
                self.config.max_position,
            ),
        )

        # SizeModel multiplier — defaults to 1.0 if absent (legacy signal
        # source) so existing callers behave identically. Tier 0 (0.0x) means
        # the model voted skip; honor it by rejecting the entry.
        size_mult = float(signal.get("size", 1.0) or 1.0)
        scaled = base_size * size_mult
        if size_mult <= 0.0:
            log.info(
                "SizeModel skip: base=%d × mult=%.2f → 0 contracts; rejecting entry",
                base_size,
                size_mult,
            )
            return {"rejected": True, "reason": "size_model_skip"}
        # Round half-up so 0.3-0.49 rounds to 0 (then floors to 1), 0.5+ to 1,
        # 1.5 with base=1 to 2. Clamp to [1, max_position].
        size = max(1, min(int(scaled + 0.5), self.config.max_position))

        log.info(
            "Sizing: risk=$%.0f (%.1f%% of $%.0f DD), stop=%d ticks ($%.0f/contract) → base=%d × size_mult=%.2f → %d contracts",
            risk_dollars,
            risk_pct * 100,
            self.config.max_trailing_dd,
            stop_dist_ticks,
            risk_per_contract,
            base_size,
            size_mult,
            size,
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

        # Network flakiness to api.topstepx.com surfaces as ConnectTimeout.
        # Retry once before failing so a single dropped connection doesn't
        # cost an entire setup. Two attempts is the cap — beyond that we
        # genuinely cannot place and should bail out so the caller doesn't
        # think the order is in flight.
        result = None
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                result = await self.client.place_market_order(order_action, size)
                break
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "place_market_order attempt %d/2 failed: %s — retrying",
                    attempt,
                    type(exc).__name__,
                )
        if result is None:
            log.error("Market order failed after 2 attempts: %s", last_exc)
            return {"rejected": True, "reason": "order_failed"}

        if isinstance(result, dict) and not result.get("success", True):
            err = result.get("errorMessage", "order_rejected")
            err_code = result.get("errorCode")
            log.warning("Market order rejected (errorCode=%s): %s", err_code, err)
            if err_code == 2 or "permanent violation" in str(err).lower():
                self._halt(f"account permanent violation: {err}")
            return {"rejected": True, "reason": err}

        entry_order_id = result.get("orderId") if isinstance(result, dict) else None

        stop_order_id = None
        if stop_price > 0:
            current_stop = stop_price
            for attempt in (1, 2, 3):
                try:
                    stop_result = await self.client.place_stop_order(stop_action, size, current_stop)
                except Exception:
                    log.exception("Stop placement raised (attempt %d/3)", attempt)
                    stop_result = None
                if isinstance(stop_result, dict):
                    if stop_result.get("success", True):
                        stop_order_id = stop_result.get("orderId")
                        break
                    err_msg = str(stop_result.get("errorMessage", ""))
                    log.warning(
                        "Stop placement rejected (attempt %d/3): %s",
                        attempt,
                        err_msg,
                    )
                    # "Order price is outside allowed range" means the market
                    # has moved through our pre-computed stop level between
                    # signal time and order placement. Recompute the stop from
                    # the actual fill price (entry_price as set by stream fill)
                    # with an extra 2-tick safety buffer + widen by 4 ticks
                    # each retry. This adapts to fast-moving NQ where the
                    # signal-time stop is stale by the time the order lands.
                    if (
                        "allowed range" in err_msg.lower()
                        or "best ask" in err_msg.lower()
                        or "best bid" in err_msg.lower()
                    ):
                        live_entry = self.tracker.entry_price or price
                        # Widen by 4 ticks per retry. is_long: stop below; short: stop above.
                        widen_ticks = 4 * attempt
                        widen_offset = (stop_dist_ticks + widen_ticks) * 0.25
                        if is_long:
                            current_stop = _round_tick(live_entry - widen_offset)
                        else:
                            current_stop = _round_tick(live_entry + widen_offset)
                        log.info(
                            "Stop recalc from live entry %.2f: new_stop=%.2f (was %.2f, +%d ticks)",
                            live_entry,
                            current_stop,
                            stop_price,
                            widen_ticks,
                        )
                        # Reflect updated stop in pending trade record so
                        # broker_trades persists the actual placed stop, not
                        # the stale signal-time one.
                        stop_price = current_stop

            if stop_order_id is None:
                # We have a filled (or about-to-fill) market order but no stop. Sitting
                # naked is worse than reverting — liquidate immediately to bound risk.
                log.error(
                    "Stop placement failed twice — flattening entry to avoid unhedged position",
                )
                try:
                    await self.client.liquidate_position()
                except Exception:
                    log.exception("Emergency liquidate after failed stop also failed — POSITION MAY BE OPEN")
                self._halt("stop_placement_failed")
                return {"rejected": True, "reason": "stop_placement_failed"}

            # Verify the stop is actually live on the broker. TopstepX
            # sometimes accepts the place_stop_order call (returns success +
            # orderId) but the order doesn't end up in the book — trade 128
            # (2026-04-30 15:50, +$890) entered with a "successful" stop
            # response but Order/searchOpen later showed zero open orders,
            # leaving the position naked. Confirm before tracking it.
            try:
                open_orders = await self.client._post("/api/Order/searchOpen", {"accountId": self.client._account_id})
                live_ids = {int(o.get("id")) for o in (open_orders.get("orders") or []) if o.get("id")}
                if int(stop_order_id) not in live_ids:
                    log.error(
                        "Stop verify FAILED: orderId %s not in Order/searchOpen (%d open) — "
                        "flattening to avoid naked position",
                        stop_order_id,
                        len(live_ids),
                    )
                    try:
                        await self.client.liquidate_position()
                    except Exception:
                        log.exception("Emergency liquidate after stop-verify failure also failed")
                    self._halt("stop_verify_failed")
                    return {"rejected": True, "reason": "stop_verify_failed"}
                log.info("Stop verified live: orderId=%s @ %.2f", stop_order_id, current_stop)
            except Exception:
                # Verification call itself failed — don't block trade on a
                # transient REST error. The reconcile loop will catch any
                # genuine naked-position state within 60s.
                log.warning("Stop verification REST call failed; continuing", exc_info=True)

        side = "long" if is_long else "short"
        self.tracker.on_fill(side, price=0.0, size=size, stop_price=stop_price)
        self.tracker.entry_order_id = entry_order_id
        self.tracker.stop_order_id = stop_order_id

        now = datetime.now(timezone.utc)
        # TP = 2R from entry
        tp_price = _round_tick(price + offset * 2 if is_long else price - offset * 2)
        self._pending_trade = {
            "ts": now,
            "session_date": now.strftime("%Y-%m-%d"),
            "symbol": "NQ",
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "tp_price": tp_price,
            "stop_ticks": stop_dist_ticks,
            "signal_price": price,
            "entry_submit_ts": entry_submit_ts,
            "entry_fill_ts": None,
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
