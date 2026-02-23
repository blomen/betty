"""Trading service - business logic for the trading journal system."""

import csv
import io
import math
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..constants import TRADE_STATE_TRANSITIONS, PSYCH_GATE_THRESHOLD
from ..config.trading_loader import get_instruments, get_setups, get_routine_config
from ..repositories.trading_repo import TradingRepo


def _utcnow():
    return datetime.now(timezone.utc)


class TradingService:
    """Core trading business logic: validation, sizing, lifecycle."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = TradingRepo(db)

    # ---- Account operations ----

    def seed_accounts(self):
        """Create the 3 default accounts if none exist."""
        existing = self.repo.list_accounts()
        if existing:
            return existing
        defaults = [
            {"name": "Intraday / Scalp", "account_type": "intraday"},
            {"name": "Swing", "account_type": "swing"},
            {"name": "HODL", "account_type": "hodl"},
        ]
        accounts = []
        for d in defaults:
            accounts.append(self.repo.create_account(**d))
        self.db.commit()
        return accounts

    def update_account(self, account_id: int, data: dict) -> dict:
        acct = self.repo.get_account(account_id)
        if not acct:
            return {"error": "Account not found"}
        for k, v in data.items():
            if v is not None and hasattr(acct, k):
                setattr(acct, k, v)
        self.db.commit()
        return {"success": True, "account": self._acct_dict(acct)}

    def adjust_balance(self, account_id: int, amount: float) -> dict:
        acct = self.repo.get_account(account_id)
        if not acct:
            return {"error": "Account not found"}
        acct.balance += amount
        acct.equity += amount
        self.db.commit()
        return {"success": True, "balance": acct.balance}

    def reset_daily(self, account_id: int) -> dict:
        acct = self.repo.get_account(account_id)
        if not acct:
            return {"error": "Account not found"}
        acct.trades_today = 0
        acct.daily_pnl = 0.0
        acct.consecutive_losses = 0
        acct.is_daily_locked = False
        self.db.commit()
        return {"success": True}

    def reset_weekly(self, account_id: int) -> dict:
        acct = self.repo.get_account(account_id)
        if not acct:
            return {"error": "Account not found"}
        acct.weekly_pnl = 0.0
        acct.is_weekly_locked = False
        self.db.commit()
        return {"success": True}

    # ---- Routine operations ----

    def get_or_create_routine(self, d: str | None = None) -> dict:
        if d is None:
            d = date.today().isoformat()
        routine = self.repo.get_routine_by_date(d)
        if not routine:
            routine = self.repo.create_routine(d)
            self.db.commit()
        return self._routine_dict(routine)

    def update_routine(self, d: str, data: dict) -> dict:
        routine = self.repo.get_routine_by_date(d)
        if not routine:
            return {"error": "Routine not found"}
        for k, v in data.items():
            if v is not None and hasattr(routine, k):
                setattr(routine, k, v)
        # Auto-compute psych average
        scores = [routine.sleep_score, routine.focus_score, routine.emotional_score]
        valid = [s for s in scores if s is not None]
        routine.psych_average = sum(valid) / len(valid) if valid else None
        self.db.commit()
        return {"success": True, "routine": self._routine_dict(routine)}

    # ---- Trade validation ----

    def validate_trade(self, data: dict) -> dict:
        """Run risk policy checks. Returns {errors: [...], warnings: [...], sizing: {...}}."""
        errors = []
        warnings = []

        acct = self.repo.get_account(data["account_id"])
        if not acct:
            return {"errors": ["Account not found"], "warnings": [], "sizing": {}}

        # Hard blocks
        if not data.get("stop_price"):
            errors.append("Stop price is required")

        if acct.is_daily_locked:
            errors.append("Daily loss limit reached — account locked")
        if acct.is_weekly_locked:
            errors.append("Weekly loss limit reached — account locked")
        if acct.trades_today >= acct.max_trades_per_day:
            errors.append(f"Max trades/day ({acct.max_trades_per_day}) reached")
        if acct.consecutive_losses >= acct.stop_after_consecutive_losses:
            errors.append(f"Consecutive loss limit ({acct.stop_after_consecutive_losses}) reached")

        # Position sizing
        sizing = {}
        entry = data.get("entry_price")
        stop = data.get("stop_price")
        instrument_key = data.get("instrument", "")
        instruments = get_instruments()
        inst_cfg = instruments.get(instrument_key, {})

        if entry and stop and inst_cfg:
            tick_size = inst_cfg.get("tick_size", 0.25)
            tick_value = inst_cfg.get("tick_value", 5.0)
            risk_per_contract = abs(entry - stop) / tick_size * tick_value
            max_risk_dollars = acct.balance * (acct.risk_per_trade_pct / 100)
            contracts = max(1, math.floor(max_risk_dollars / risk_per_contract)) if risk_per_contract > 0 else 1
            risk_amount = risk_per_contract * data.get("contracts", contracts)

            # Check risk exceeds policy
            if risk_amount > max_risk_dollars * 1.5:
                errors.append(
                    f"Risk ${risk_amount:.0f} exceeds {acct.risk_per_trade_pct}% policy "
                    f"(max ${max_risk_dollars:.0f})"
                )

            # Daily DD check
            if acct.balance > 0:
                daily_dd_pct = abs(acct.daily_pnl) / acct.balance * 100 if acct.daily_pnl < 0 else 0
                if daily_dd_pct >= acct.max_daily_loss_pct:
                    errors.append(
                        f"Daily drawdown {daily_dd_pct:.1f}% >= limit {acct.max_daily_loss_pct}%"
                    )
                weekly_dd_pct = abs(acct.weekly_pnl) / acct.balance * 100 if acct.weekly_pnl < 0 else 0
                if weekly_dd_pct >= acct.max_weekly_loss_pct:
                    errors.append(
                        f"Weekly drawdown {weekly_dd_pct:.1f}% >= limit {acct.max_weekly_loss_pct}%"
                    )

            # RR ratio
            targets = data.get("targets") or []
            rr_ratio = None
            if targets and risk_per_contract > 0:
                first_target = targets[0].get("price", entry) if isinstance(targets[0], dict) else targets[0]
                reward_per_contract = abs(first_target - entry) / tick_size * tick_value
                rr_ratio = reward_per_contract / risk_per_contract if risk_per_contract else None

            sizing = {
                "suggested_contracts": contracts,
                "risk_per_contract": round(risk_per_contract, 2),
                "total_risk": round(risk_amount, 2),
                "max_risk_dollars": round(max_risk_dollars, 2),
                "rr_ratio": round(rr_ratio, 2) if rr_ratio else None,
            }

        # Routine gate — must complete daily routine before first trade
        today_str = date.today().isoformat()
        routine = self.repo.get_routine_by_date(today_str)
        if not routine or not routine.is_complete:
            errors.append("Daily routine not completed — finish the Today checklist first")
        elif routine.psych_average is not None:
            # Psych gate (soft block) — only checked if routine exists
            if routine.psych_average < PSYCH_GATE_THRESHOLD and not routine.psych_override:
                warnings.append(
                    f"Psych score {routine.psych_average:.1f} below threshold "
                    f"{PSYCH_GATE_THRESHOLD} — override required"
                )

        return {"errors": errors, "warnings": warnings, "sizing": sizing}

    # ---- Trade lifecycle ----

    def create_trade(self, data: dict) -> dict:
        validation = self.validate_trade(data)
        if data.get("dry_run"):
            return {"dry_run": True, **validation}
        if validation["errors"]:
            return {"error": "Validation failed", **validation}

        # Compute sizing fields
        entry = data.get("entry_price")
        stop = data.get("stop_price")
        instruments = get_instruments()
        inst_cfg = instruments.get(data.get("instrument", ""), {})
        risk_amount = None
        rr_ratio = None

        if entry and stop and inst_cfg:
            tick_size = inst_cfg.get("tick_size", 0.25)
            tick_value = inst_cfg.get("tick_value", 5.0)
            risk_per_contract = abs(entry - stop) / tick_size * tick_value
            risk_amount = risk_per_contract * data.get("contracts", 1)
            targets = data.get("targets") or []
            if targets:
                first_target = targets[0].get("price", entry) if isinstance(targets[0], dict) else targets[0]
                reward_per_contract = abs(first_target - entry) / tick_size * tick_value
                rr_ratio = reward_per_contract / risk_per_contract if risk_per_contract else None

        # Link to today's routine
        today_str = date.today().isoformat()
        routine = self.repo.get_routine_by_date(today_str)

        trade = self.repo.create_trade(
            account_id=data["account_id"],
            instrument=data["instrument"],
            direction=data["direction"],
            setup_type=data["setup_type"],
            entry_price=entry,
            stop_price=stop,
            targets=data.get("targets"),
            contracts=data.get("contracts", 1),
            confirmations=data.get("confirmations"),
            notes=data.get("notes"),
            risk_amount=risk_amount,
            rr_ratio=round(rr_ratio, 2) if rr_ratio else None,
            daily_routine_id=routine.id if routine else None,
        )
        self.db.flush()

        self.repo.add_event(
            trade_id=trade.id,
            event_type="transition",
            to_state="created",
            notes="Trade created",
        )
        self.db.commit()
        return {"success": True, "trade_id": trade.id, **validation}

    def transition_trade(self, trade_id: int, to_state: str, notes: str | None = None) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}

        allowed = TRADE_STATE_TRANSITIONS.get(trade.state, set())
        if to_state not in allowed:
            return {"error": f"Cannot transition from '{trade.state}' to '{to_state}'"}

        from_state = trade.state
        trade.state = to_state

        # Update state timestamps
        ts_map = {"armed": "armed_at", "triggered": "triggered_at", "open": "opened_at", "closed": "closed_at"}
        if to_state in ts_map:
            setattr(trade, ts_map[to_state], _utcnow())

        self.repo.add_event(
            trade_id=trade.id,
            event_type="transition",
            from_state=from_state,
            to_state=to_state,
            notes=notes,
        )
        self.db.commit()
        return {"success": True, "state": trade.state}

    def close_trade(self, trade_id: int, exit_price: float, commission: float | None = None, notes: str | None = None) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        if trade.state in ("closed", "reviewed"):
            return {"error": "Trade already closed"}

        instruments = get_instruments()
        inst_cfg = instruments.get(trade.instrument, {})
        tick_size = inst_cfg.get("tick_size", 0.25)
        tick_value = inst_cfg.get("tick_value", 5.0)

        # Auto-calculate commission from config if not provided
        if commission is None or commission == 0:
            cpc = inst_cfg.get("commission_per_contract", 0)
            commission = cpc * trade.contracts

        # Calculate PnL
        if trade.entry_price is not None:
            direction_mult = 1 if trade.direction == "long" else -1
            pnl_per_contract = (exit_price - trade.entry_price) * direction_mult / tick_size * tick_value
            realized_pnl = pnl_per_contract * trade.contracts - commission
        else:
            realized_pnl = -commission

        # R-multiple
        r_multiple = None
        if trade.risk_amount and trade.risk_amount > 0:
            r_multiple = realized_pnl / (trade.risk_amount / trade.contracts * trade.contracts)

        from_state = trade.state
        trade.state = "closed"
        trade.realized_pnl = round(realized_pnl, 2)
        trade.commission = commission
        trade.r_multiple = round(r_multiple, 2) if r_multiple is not None else None
        trade.closed_at = _utcnow()

        # Update account
        acct = self.repo.get_account(trade.account_id)
        if acct:
            acct.realized_pnl += realized_pnl
            acct.daily_pnl += realized_pnl
            acct.weekly_pnl += realized_pnl
            acct.balance += realized_pnl
            acct.equity = acct.balance
            acct.trades_today += 1

            if realized_pnl < 0:
                acct.consecutive_losses += 1
            else:
                acct.consecutive_losses = 0

            # Check lockouts
            if acct.balance > 0:
                if abs(acct.daily_pnl) / acct.balance * 100 >= acct.max_daily_loss_pct and acct.daily_pnl < 0:
                    acct.is_daily_locked = True
                if abs(acct.weekly_pnl) / acct.balance * 100 >= acct.max_weekly_loss_pct and acct.weekly_pnl < 0:
                    acct.is_weekly_locked = True

        self.repo.add_event(
            trade_id=trade.id,
            event_type="transition",
            from_state=from_state,
            to_state="closed",
            details={"exit_price": exit_price, "pnl": realized_pnl, "commission": commission},
            notes=notes,
        )
        self.db.commit()
        return {"success": True, "realized_pnl": realized_pnl, "r_multiple": r_multiple}

    def partial_exit(self, trade_id: int, contracts: int, exit_price: float, notes: str | None = None) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        if contracts >= trade.contracts:
            return {"error": "Cannot partial exit all contracts — use close instead"}

        instruments = get_instruments()
        inst_cfg = instruments.get(trade.instrument, {})
        tick_size = inst_cfg.get("tick_size", 0.25)
        tick_value = inst_cfg.get("tick_value", 5.0)

        direction_mult = 1 if trade.direction == "long" else -1
        pnl = (exit_price - (trade.entry_price or 0)) * direction_mult / tick_size * tick_value * contracts
        trade.contracts -= contracts
        trade.realized_pnl = round((trade.realized_pnl or 0) + pnl, 2)

        self.repo.add_event(
            trade_id=trade.id,
            event_type="partial_exit",
            details={"contracts": contracts, "exit_price": exit_price, "pnl": round(pnl, 2)},
            notes=notes,
        )
        self.db.commit()
        return {"success": True, "remaining_contracts": trade.contracts, "partial_pnl": round(pnl, 2)}

    def move_to_be(self, trade_id: int) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        trade.be_price = trade.entry_price
        trade.stop_price = trade.entry_price
        self.repo.add_event(
            trade_id=trade.id,
            event_type="move_to_be",
            details={"be_price": trade.entry_price},
        )
        self.db.commit()
        return {"success": True, "stop_price": trade.stop_price}

    def trail_stop(self, trade_id: int, new_stop: float, notes: str | None = None) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        old_stop = trade.stop_price
        trade.stop_price = new_stop
        self.repo.add_event(
            trade_id=trade.id,
            event_type="trail_stop",
            details={"old_stop": old_stop, "new_stop": new_stop},
            notes=notes,
        )
        self.db.commit()
        return {"success": True, "stop_price": new_stop}

    def add_position(self, trade_id: int, contracts: int, entry_price: float, notes: str | None = None) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        # Weighted average entry
        old_total = (trade.entry_price or 0) * trade.contracts
        new_total = entry_price * contracts
        trade.entry_price = (old_total + new_total) / (trade.contracts + contracts)
        trade.contracts += contracts
        self.repo.add_event(
            trade_id=trade.id,
            event_type="add_position",
            details={"contracts": contracts, "entry_price": entry_price, "new_avg": round(trade.entry_price, 4)},
            notes=notes,
        )
        self.db.commit()
        return {"success": True, "contracts": trade.contracts, "avg_entry": round(trade.entry_price, 4)}

    def submit_review(self, trade_id: int, data: dict) -> dict:
        trade = self.repo.get_trade(trade_id)
        if not trade:
            return {"error": "Trade not found"}
        if trade.state not in ("closed", "reviewed"):
            return {"error": "Trade must be closed before review"}

        existing = self.repo.get_review(trade_id)
        if existing:
            for k, v in data.items():
                if v is not None and hasattr(existing, k):
                    setattr(existing, k, v)
        else:
            self.repo.create_review(trade_id=trade_id, **data)

        if trade.state == "closed":
            trade.state = "reviewed"
            self.repo.add_event(
                trade_id=trade.id,
                event_type="transition",
                from_state="closed",
                to_state="reviewed",
                notes="Review submitted",
            )

        self.db.commit()
        return {"success": True}

    # ---- Daily auto-reset ----

    def auto_reset_daily(self) -> dict:
        """Reset daily counters for all accounts. Called by scheduler at market open."""
        accounts = self.repo.list_accounts()
        reset_count = 0
        for acct in accounts:
            if acct.trades_today > 0 or acct.daily_pnl != 0 or acct.is_daily_locked:
                acct.trades_today = 0
                acct.daily_pnl = 0.0
                acct.consecutive_losses = 0
                acct.is_daily_locked = False
                reset_count += 1
        self.db.commit()
        return {"reset_accounts": reset_count}

    def auto_reset_weekly(self) -> dict:
        """Reset weekly counters for all accounts. Called by scheduler on Monday."""
        accounts = self.repo.list_accounts()
        reset_count = 0
        for acct in accounts:
            if acct.weekly_pnl != 0 or acct.is_weekly_locked:
                acct.weekly_pnl = 0.0
                acct.is_weekly_locked = False
                reset_count += 1
        self.db.commit()
        return {"reset_accounts": reset_count}

    # ---- Analytics ----

    def get_analytics(self, filters: dict | None = None) -> dict:
        """Compute comprehensive trade analytics for reviewed + closed trades."""
        all_trades = self.repo.list_trades(limit=10000)
        trades = [t for t in all_trades if t.state in ("closed", "reviewed")]

        if filters:
            if filters.get("account_id"):
                trades = [t for t in trades if t.account_id == filters["account_id"]]
            if filters.get("instrument"):
                trades = [t for t in trades if t.instrument == filters["instrument"]]
            if filters.get("setup_type"):
                trades = [t for t in trades if t.setup_type == filters["setup_type"]]

        if not trades:
            return {"total": 0}

        wins = [t for t in trades if (t.realized_pnl or 0) > 0]
        losses = [t for t in trades if (t.realized_pnl or 0) < 0]
        breakevens = [t for t in trades if (t.realized_pnl or 0) == 0]

        total_pnl = sum(t.realized_pnl or 0 for t in trades)
        gross_wins = sum(t.realized_pnl or 0 for t in wins)
        gross_losses = abs(sum(t.realized_pnl or 0 for t in losses))
        avg_win = gross_wins / len(wins) if wins else 0
        avg_loss = gross_losses / len(losses) if losses else 0
        win_rate = len(wins) / len(trades) if trades else 0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # R-multiples
        r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
        avg_r = sum(r_values) / len(r_values) if r_values else 0
        max_r = max(r_values) if r_values else 0
        min_r = min(r_values) if r_values else 0

        # Streaks
        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        streak = 0
        last_dir = None
        for t in sorted(trades, key=lambda x: x.closed_at or x.created_at):
            pnl = t.realized_pnl or 0
            d = "win" if pnl > 0 else "loss" if pnl < 0 else "be"
            if d == last_dir and d != "be":
                streak += 1
            else:
                streak = 1
            last_dir = d
            if d == "win":
                max_win_streak = max(max_win_streak, streak)
            elif d == "loss":
                max_loss_streak = max(max_loss_streak, streak)
            current_streak = streak if d != "be" else current_streak

        # Largest single win/loss
        largest_win = max((t.realized_pnl or 0 for t in trades), default=0)
        largest_loss = min((t.realized_pnl or 0 for t in trades), default=0)

        # By setup breakdown
        by_setup: dict[str, dict] = {}
        for t in trades:
            s = t.setup_type
            if s not in by_setup:
                by_setup[s] = {"count": 0, "wins": 0, "total_pnl": 0, "total_r": 0, "r_count": 0}
            by_setup[s]["count"] += 1
            if (t.realized_pnl or 0) > 0:
                by_setup[s]["wins"] += 1
            by_setup[s]["total_pnl"] += t.realized_pnl or 0
            if t.r_multiple is not None:
                by_setup[s]["total_r"] += t.r_multiple
                by_setup[s]["r_count"] += 1

        setup_stats = {}
        for s, d in by_setup.items():
            setup_stats[s] = {
                "count": d["count"],
                "wins": d["wins"],
                "win_rate": d["wins"] / d["count"] if d["count"] else 0,
                "total_pnl": round(d["total_pnl"], 2),
                "avg_r": round(d["total_r"] / d["r_count"], 2) if d["r_count"] else 0,
                "expectancy": round(
                    (d["wins"] / d["count"]) * (d["total_pnl"] / d["wins"] if d["wins"] else 0)
                    - ((d["count"] - d["wins"]) / d["count"]) * (abs(d["total_pnl"] - sum(
                        t.realized_pnl or 0 for t in trades if t.setup_type == s and (t.realized_pnl or 0) > 0
                    )) / (d["count"] - d["wins"]) if (d["count"] - d["wins"]) else 0),
                    2,
                ),
            }

        # By instrument
        by_instrument: dict[str, dict] = {}
        for t in trades:
            i = t.instrument
            if i not in by_instrument:
                by_instrument[i] = {"count": 0, "wins": 0, "total_pnl": 0}
            by_instrument[i]["count"] += 1
            if (t.realized_pnl or 0) > 0:
                by_instrument[i]["wins"] += 1
            by_instrument[i]["total_pnl"] += t.realized_pnl or 0

        instrument_stats = {}
        for i, d in by_instrument.items():
            instrument_stats[i] = {
                "count": d["count"],
                "wins": d["wins"],
                "win_rate": round(d["wins"] / d["count"], 3) if d["count"] else 0,
                "total_pnl": round(d["total_pnl"], 2),
            }

        # By direction
        longs = [t for t in trades if t.direction == "long"]
        shorts = [t for t in trades if t.direction == "short"]
        direction_stats = {
            "long": {
                "count": len(longs),
                "wins": sum(1 for t in longs if (t.realized_pnl or 0) > 0),
                "total_pnl": round(sum(t.realized_pnl or 0 for t in longs), 2),
            },
            "short": {
                "count": len(shorts),
                "wins": sum(1 for t in shorts if (t.realized_pnl or 0) > 0),
                "total_pnl": round(sum(t.realized_pnl or 0 for t in shorts), 2),
            },
        }

        # Equity curve (cumulative PnL over time)
        equity_curve = []
        cum_pnl = 0
        for t in sorted(trades, key=lambda x: x.closed_at or x.created_at):
            cum_pnl += t.realized_pnl or 0
            equity_curve.append({
                "trade_id": t.id,
                "closed_at": (t.closed_at or t.created_at).isoformat() if (t.closed_at or t.created_at) else None,
                "pnl": round(t.realized_pnl or 0, 2),
                "cumulative_pnl": round(cum_pnl, 2),
            })

        # Review stats
        reviewed = [t for t in trades if t.review]
        avg_grade = sum(t.review.grade or 0 for t in reviewed) / len(reviewed) if reviewed else 0
        rules_followed_pct = sum(1 for t in reviewed if t.review.followed_rules) / len(reviewed) if reviewed else 0

        return {
            "total": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate": round(win_rate, 3),
            "total_pnl": round(total_pnl, 2),
            "gross_wins": round(gross_wins, 2),
            "gross_losses": round(gross_losses, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
            "expectancy": round(expectancy, 2),
            "avg_r": round(avg_r, 2),
            "max_r": round(max_r, 2),
            "min_r": round(min_r, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "current_streak": current_streak,
            "current_streak_direction": last_dir,
            "by_setup": setup_stats,
            "by_instrument": instrument_stats,
            "by_direction": direction_stats,
            "equity_curve": equity_curve,
            "avg_grade": round(avg_grade, 1),
            "rules_followed_pct": round(rules_followed_pct, 3),
            "total_commission": round(sum(t.commission or 0 for t in trades), 2),
        }

    # ---- CSV export ----

    def export_trades_csv(self, filters: dict | None = None) -> str:
        """Export trades as CSV string."""
        all_trades = self.repo.list_trades(limit=10000)
        trades = all_trades
        if filters:
            if filters.get("state"):
                trades = [t for t in trades if t.state == filters["state"]]
            if filters.get("account_id"):
                trades = [t for t in trades if t.account_id == filters["account_id"]]
            if filters.get("instrument"):
                trades = [t for t in trades if t.instrument == filters["instrument"]]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "account", "instrument", "direction", "setup_type",
            "entry_price", "stop_price", "exit_price", "contracts",
            "risk_amount", "rr_ratio", "r_multiple", "realized_pnl",
            "commission", "state", "notes",
            "created_at", "opened_at", "closed_at",
            "review_grade", "review_followed_rules",
        ])
        for t in sorted(trades, key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc)):
            # Extract exit price from close event
            exit_price = None
            for ev in (t.events or []):
                if ev.event_type == "transition" and ev.to_state == "closed" and ev.details:
                    exit_price = ev.details.get("exit_price")
            writer.writerow([
                t.id,
                t.account.name if t.account else t.account_id,
                t.instrument,
                t.direction,
                t.setup_type,
                t.entry_price,
                t.stop_price,
                exit_price,
                t.contracts,
                t.risk_amount,
                t.rr_ratio,
                t.r_multiple,
                t.realized_pnl,
                t.commission,
                t.state,
                t.notes or "",
                (t.created_at.isoformat() if t.created_at else ""),
                (t.opened_at.isoformat() if t.opened_at else ""),
                (t.closed_at.isoformat() if t.closed_at else ""),
                t.review.grade if t.review else "",
                t.review.followed_rules if t.review else "",
            ])
        return output.getvalue()

    # ---- Serializers ----

    def _acct_dict(self, a) -> dict:
        return {
            "id": a.id,
            "name": a.name,
            "account_type": a.account_type,
            "balance": a.balance,
            "equity": a.equity,
            "realized_pnl": a.realized_pnl,
            "daily_pnl": a.daily_pnl,
            "weekly_pnl": a.weekly_pnl,
            "risk_per_trade_pct": a.risk_per_trade_pct,
            "max_daily_loss_pct": a.max_daily_loss_pct,
            "max_weekly_loss_pct": a.max_weekly_loss_pct,
            "max_trades_per_day": a.max_trades_per_day,
            "stop_after_consecutive_losses": a.stop_after_consecutive_losses,
            "trades_today": a.trades_today,
            "consecutive_losses": a.consecutive_losses,
            "is_daily_locked": a.is_daily_locked,
            "is_weekly_locked": a.is_weekly_locked,
        }

    def _routine_dict(self, r) -> dict:
        return {
            "id": r.id,
            "date": r.date,
            "macro_notes": r.macro_notes,
            "overnight_high": r.overnight_high,
            "overnight_low": r.overnight_low,
            "key_levels": r.key_levels,
            "prev_value_area": r.prev_value_area,
            "bias_text": r.bias_text,
            "bias_direction": r.bias_direction,
            "bias_confidence": r.bias_confidence,
            "sleep_score": r.sleep_score,
            "focus_score": r.focus_score,
            "emotional_score": r.emotional_score,
            "psych_average": r.psych_average,
            "psych_override": r.psych_override,
            "checklist_completion": r.checklist_completion,
            "is_complete": r.is_complete,
        }

    def trade_dict(self, t) -> dict:
        return {
            "id": t.id,
            "account_id": t.account_id,
            "account_name": t.account.name if t.account else None,
            "daily_routine_id": t.daily_routine_id,
            "instrument": t.instrument,
            "direction": t.direction,
            "setup_type": t.setup_type,
            "entry_price": t.entry_price,
            "stop_price": t.stop_price,
            "be_price": t.be_price,
            "targets": t.targets,
            "contracts": t.contracts,
            "risk_amount": t.risk_amount,
            "rr_ratio": t.rr_ratio,
            "r_multiple": t.r_multiple,
            "confirmations": t.confirmations,
            "state": t.state,
            "realized_pnl": t.realized_pnl,
            "commission": t.commission,
            "notes": t.notes,
            "armed_at": t.armed_at.isoformat() if t.armed_at else None,
            "triggered_at": t.triggered_at.isoformat() if t.triggered_at else None,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "from_state": e.from_state,
                    "to_state": e.to_state,
                    "details": e.details,
                    "notes": e.notes,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in (t.events or [])
            ],
            "review": {
                "id": t.review.id,
                "thesis_recap": t.review.thesis_recap,
                "followed_rules": t.review.followed_rules,
                "what_to_improve": t.review.what_to_improve,
                "grade": t.review.grade,
            } if t.review else None,
        }
