"""SessionState — per-session memory + circuit breakers + per-zone cooldown.

The Apr-8 disaster (-324 R on a 5%-win-rate day, 91.5% take rate) revealed
that the model has no concept of "this session is going badly" or "we already
traded this zone two minutes ago". The trained heads see each touch
independently; without a stateful layer wrapping them, the system happily
takes 450 trades a day even when it's getting steamrolled.

This module sits inside LiveInferenceV5 (or any live caller) and answers two
questions before each new entry:

  1. Is this session in a hostile state? (circuit breaker)
  2. Did we just trade this zone? (per-zone cooldown)

If either fires, the caller skips the entry. The state is purely live — no
training needed, no schema bump.

API:
    state = SessionState()
    if state.should_skip(zone_key, now_ts) is None:
        # take trade
        result_R = ...
        state.record_trade(zone_key, now_ts, realized_R=result_R)
    else:
        # skipped — log reason

The thresholds below are conservative defaults — tune per how aggressive
you want the gate.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# --- Circuit-breaker thresholds ----------------------------------------------
# How many trades back to look at when computing the rolling win rate.
ROLLING_WINDOW: int = 10
# Pause new entries when the rolling window's win rate drops below this.
# Apr-8 saw a sustained 5% win rate; 15% is a clear "regime hostile" signal.
MIN_ROLLING_WIN_RATE: float = 0.15
# Pause when this many trades in a row are losers — catches "every level fails"
# regimes faster than the rolling window.
MAX_CONSECUTIVE_LOSSES: int = 5
# Pause when session R drops this far below its peak (in R units, post-fees).
# Apr-8 hit -324 R; 200 R is enough headroom for normal grinding losses but
# tight enough that the next disaster gets caught after one bad day, not two.
MAX_SESSION_DRAWDOWN_R: float = 200.0
# How many wins it takes to "uncircuit" — one bounce shouldn't re-enable us
# if we just had a 50%-stop-rate day; require 2 winners.
WINS_TO_RESUME: int = 2

# --- Per-zone cooldown -------------------------------------------------------
# Minimum seconds between two entries on the same zone. 5-minute cooldown
# stops the ~600 touches/day from generating ~600 trades on the same handful
# of repeated zone retests. Real traders don't enter the same level twice in
# 30 seconds — neither should we.
MIN_ZONE_COOLDOWN_SECONDS: int = 300


@dataclass
class SkipReason:
    """Why a trade entry was skipped by the gate."""

    code: str  # short tag: "circuit_consec", "circuit_winrate", "circuit_dd", "cooldown"
    detail: str  # human-readable detail


@dataclass
class SessionState:
    """Live session memory + entry gate.

    Tracks recent trade outcomes, consecutive losses, equity peak/drawdown,
    and per-zone last-trade timestamps. Exposes `should_skip(zone, now)`
    that returns a SkipReason or None.

    All thresholds live as module-level constants so they can be tuned
    without code changes (or overridden per-instance via attributes).
    """

    rolling_window: int = ROLLING_WINDOW
    min_rolling_win_rate: float = MIN_ROLLING_WIN_RATE
    max_consecutive_losses: int = MAX_CONSECUTIVE_LOSSES
    max_session_drawdown_r: float = MAX_SESSION_DRAWDOWN_R
    wins_to_resume: int = WINS_TO_RESUME
    min_zone_cooldown_seconds: int = MIN_ZONE_COOLDOWN_SECONDS

    # Internal rolling state (populated by record_trade)
    _recent_outcomes: deque = field(default_factory=lambda: deque(maxlen=ROLLING_WINDOW))
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    session_R: float = 0.0
    peak_session_R: float = 0.0
    trades_taken: int = 0
    trades_skipped_circuit: int = 0
    trades_skipped_cooldown: int = 0

    # Circuit-breaker latch — once tripped, requires WINS_TO_RESUME wins
    _circuit_active: bool = False
    _circuit_reason: str = ""

    # Per-zone cooldown: zone_key (rounded price) → last entry epoch seconds
    _zone_last_entry_ts: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    @property
    def rolling_win_rate(self) -> float:
        """Win rate over the last `rolling_window` trades."""
        if not self._recent_outcomes:
            return 0.5  # neutral if no data
        wins = sum(1 for r in self._recent_outcomes if r > 0)
        return wins / len(self._recent_outcomes)

    @property
    def session_drawdown_r(self) -> float:
        """How far below peak we are, in R. Negative number, 0 if at peak."""
        return self.session_R - self.peak_session_R

    @property
    def circuit_active(self) -> bool:
        return self._circuit_active

    @property
    def circuit_reason(self) -> str:
        return self._circuit_reason

    # ------------------------------------------------------------------
    # Gate
    # ------------------------------------------------------------------

    def should_skip(self, zone_key: float, now_ts: float) -> SkipReason | None:
        """Return SkipReason if entry should be blocked, else None.

        Caller passes the zone identifier (we round to the nearest tick) and
        the current epoch seconds. No state mutation — call record_trade()
        afterwards to log the actual entry.
        """
        # Per-zone cooldown — fastest check
        zk = round(zone_key * 4) / 4
        last_ts = self._zone_last_entry_ts.get(zk)
        if last_ts is not None:
            elapsed = now_ts - last_ts
            if elapsed < self.min_zone_cooldown_seconds:
                self.trades_skipped_cooldown += 1
                return SkipReason(
                    code="cooldown",
                    detail=f"zone {zk} traded {elapsed:.0f}s ago (cooldown {self.min_zone_cooldown_seconds}s)",
                )

        # Circuit breaker — pre-emptive pause based on session state
        if self._circuit_active:
            self.trades_skipped_circuit += 1
            return SkipReason(
                code="circuit_active",
                detail=f"latched: {self._circuit_reason} (need {self.wins_to_resume - self.consecutive_wins} more wins to resume)",
            )

        # Hard rules — trip the circuit if any fire
        if self.consecutive_losses >= self.max_consecutive_losses:
            self._trip_circuit(f"{self.consecutive_losses} consecutive losses")
            self.trades_skipped_circuit += 1
            return SkipReason(code="circuit_consec", detail=self._circuit_reason)

        if len(self._recent_outcomes) >= self.rolling_window and self.rolling_win_rate < self.min_rolling_win_rate:
            self._trip_circuit(
                f"rolling-{self.rolling_window} win rate {self.rolling_win_rate:.1%} < {self.min_rolling_win_rate:.1%}"
            )
            self.trades_skipped_circuit += 1
            return SkipReason(code="circuit_winrate", detail=self._circuit_reason)

        if self.session_drawdown_r <= -self.max_session_drawdown_r:
            self._trip_circuit(f"session drawdown {self.session_drawdown_r:.1f}R below peak {self.peak_session_R:.1f}R")
            self.trades_skipped_circuit += 1
            return SkipReason(code="circuit_dd", detail=self._circuit_reason)

        return None

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def record_trade(self, zone_key: float, now_ts: float, realized_R: float) -> None:
        """Log an executed trade and update circuit state.

        Caller invokes this AFTER the trade closes with its realized R
        (post-fees, post-size scaling — whatever P&L number is meaningful
        to your downstream sizing).
        """
        zk = round(zone_key * 4) / 4
        self._zone_last_entry_ts[zk] = now_ts
        self._recent_outcomes.append(realized_R)
        self.session_R += realized_R
        self.peak_session_R = max(self.peak_session_R, self.session_R)
        self.trades_taken += 1

        if realized_R > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            # Resume from circuit if we've stacked enough wins
            if self._circuit_active and self.consecutive_wins >= self.wins_to_resume:
                self._reset_circuit()
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def reset_for_new_session(self) -> None:
        """Call at session boundary. Clears all rolling state.

        Per-zone cooldown clears too — a new session starts fresh.
        """
        self._recent_outcomes.clear()
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.session_R = 0.0
        self.peak_session_R = 0.0
        self.trades_taken = 0
        self.trades_skipped_circuit = 0
        self.trades_skipped_cooldown = 0
        self._circuit_active = False
        self._circuit_reason = ""
        self._zone_last_entry_ts.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trip_circuit(self, reason: str) -> None:
        self._circuit_active = True
        self._circuit_reason = reason
        # Reset consecutive_wins so we need fresh wins to resume
        self.consecutive_wins = 0

    def _reset_circuit(self) -> None:
        self._circuit_active = False
        self._circuit_reason = ""

    # ------------------------------------------------------------------
    # Debug snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Read-only summary for inclusion in inference payloads / logs."""
        return {
            "trades_taken": self.trades_taken,
            "trades_skipped_circuit": self.trades_skipped_circuit,
            "trades_skipped_cooldown": self.trades_skipped_cooldown,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "session_R": round(self.session_R, 2),
            "peak_session_R": round(self.peak_session_R, 2),
            "session_drawdown_R": round(self.session_drawdown_r, 2),
            "circuit_active": self._circuit_active,
            "circuit_reason": self._circuit_reason,
            "zones_in_cooldown": sum(
                1
                for ts in self._zone_last_entry_ts.values()
                if True  # snapshot just count
            ),
        }
