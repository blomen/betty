"""Profile repository - balance, bonus, and profile data access."""

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..db.models import (
    BONUS_MIN_ODDS,
    Profile,
    ProfileProviderBalance,
    ProfileProviderBonus,
)

BONUS_WAGERING_DAYS = 60  # Days to complete wagering before bonus expires

# TTL cache for total bankroll (avoids re-querying all balances + exchange rates per request)
_bankroll_cache: dict[int, tuple[float, float]] = {}  # profile_id -> (expires_at, value)
_BANKROLL_CACHE_TTL = 30.0  # seconds


class ProfileRepo:
    """Data access for profiles, balances, and bonus tracking."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Profile ----

    def get_active(self) -> Profile:
        """Get the currently active profile, creating default if none exists."""
        profile = self.db.query(Profile).filter(Profile.is_active).first()
        if not profile:
            profile = self.db.query(Profile).first()
            if profile:
                profile.is_active = True
                self.db.commit()
            else:
                profile = Profile(name="default", is_active=True)
                self.db.add(profile)
                self.db.commit()
        return profile

    # ---- Balance ----

    def get_balance(self, profile_id: int, provider_id: str) -> float:
        """Get balance for a specific profile and provider."""
        record = (
            self.db.query(ProfileProviderBalance)
            .filter(ProfileProviderBalance.profile_id == profile_id, ProfileProviderBalance.provider_id == provider_id)
            .first()
        )
        return record.balance if record else 0.0

    def set_balance(self, profile_id: int, provider_id: str, balance: float) -> None:
        """Set balance for a specific profile and provider."""
        record = (
            self.db.query(ProfileProviderBalance)
            .filter(ProfileProviderBalance.profile_id == profile_id, ProfileProviderBalance.provider_id == provider_id)
            .first()
        )

        if record:
            record.balance = balance
            record.updated_at = datetime.now(timezone.utc)
        else:
            record = ProfileProviderBalance(profile_id=profile_id, provider_id=provider_id, balance=balance)
            self.db.add(record)

    def adjust_balance(self, profile_id: int, provider_id: str, amount: float) -> float:
        """Adjust balance for a specific profile and provider. Returns new balance."""
        record = (
            self.db.query(ProfileProviderBalance)
            .filter(ProfileProviderBalance.profile_id == profile_id, ProfileProviderBalance.provider_id == provider_id)
            .first()
        )

        if record:
            record.balance += amount
            record.updated_at = datetime.now(timezone.utc)
            return record.balance
        else:
            record = ProfileProviderBalance(profile_id=profile_id, provider_id=provider_id, balance=amount)
            self.db.add(record)
            return amount

    def get_total_bankroll(self, profile_id: int) -> float:
        """Get total bankroll for a profile in SEK (converts non-SEK balances).

        Cached for 30s to avoid re-querying all balances + exchange rates on
        every opportunity listing request.
        """
        now = time.time()
        cached = _bankroll_cache.get(profile_id)
        if cached and now < cached[0]:
            return cached[1]

        from ..config import get_exchange_rate

        records = self.db.query(ProfileProviderBalance).filter(ProfileProviderBalance.profile_id == profile_id).all()
        total = sum(r.balance * get_exchange_rate(r.provider_id) for r in records)
        _bankroll_cache[profile_id] = (now + _BANKROLL_CACHE_TTL, total)
        return total

    def get_all_balances(self, profile_id: int) -> dict[str, float]:
        """Return dict of provider_id -> balance for all providers with balance > 0."""
        records = (
            self.db.query(ProfileProviderBalance)
            .filter(
                ProfileProviderBalance.profile_id == profile_id,
                ProfileProviderBalance.balance > 0,
            )
            .all()
        )
        return {r.provider_id: r.balance for r in records}

    def get_all_registered_providers(self, profile_id: int) -> set[str]:
        """Return set of all provider_ids registered in the profile (including balance=0)."""
        records = (
            self.db.query(ProfileProviderBalance.provider_id)
            .filter(
                ProfileProviderBalance.profile_id == profile_id,
            )
            .all()
        )
        return {r[0] for r in records}

    def get_provider_balance(self, profile_id: int, provider_id: str) -> float:
        """Get balance for a single provider. Alias for get_balance()."""
        return self.get_balance(profile_id, provider_id)

    def get_avg_daily_wager(self, profile_id: int, lookback_days: int = 14) -> dict:
        """
        Average total stake per day over the lookback window.
        Returns {"avg_daily_wager": float, "has_history": bool, "days_with_bets": int}.
        """
        from sqlalchemy import func

        from ..db.models import Bet

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        rows = (
            self.db.query(
                func.sum(Bet.stake).label("total_stake"),
                func.count(func.distinct(func.date(Bet.placed_at))).label("days_with_bets"),
            )
            .filter(
                Bet.profile_id == profile_id,
                Bet.placed_at >= cutoff,
                Bet.stake > 0,
            )
            .first()
        )

        total_stake = rows.total_stake or 0.0
        days_with_bets = rows.days_with_bets or 0

        return {
            "avg_daily_wager": round(total_stake / lookback_days, 2) if lookback_days > 0 else 0.0,
            "has_history": days_with_bets >= 1,
            "days_with_bets": days_with_bets,
        }

    def copy_balances(self, from_profile_id: int, to_profile_id: int) -> int:
        """Copy all balances from one profile to another. Returns count copied."""
        source_balances = (
            self.db.query(ProfileProviderBalance).filter(ProfileProviderBalance.profile_id == from_profile_id).all()
        )

        count = 0
        for source in source_balances:
            existing = (
                self.db.query(ProfileProviderBalance)
                .filter(
                    ProfileProviderBalance.profile_id == to_profile_id,
                    ProfileProviderBalance.provider_id == source.provider_id,
                )
                .first()
            )

            if not existing:
                new_balance = ProfileProviderBalance(
                    profile_id=to_profile_id, provider_id=source.provider_id, balance=source.balance
                )
                self.db.add(new_balance)
                count += 1

        return count

    # ---- Bonus ----

    def get_bonus_status(self, profile_id: int, provider_id: str) -> dict:
        """Get bonus status and wagering progress for a provider."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        if not record:
            return {
                "status": "available",
                "bonus_type": None,
                "bonus_amount": 0.0,
                "wagering_requirement": 0.0,
                "wagered_amount": 0.0,
                "min_odds": 0.0,
                "progress_pct": 100.0,
                "is_cleared": True,
                "claimed_at": None,
                "expires_at": None,
                "days_remaining": None,
            }

        # Auto-expire: if wagering deadline has passed, mark as completed
        active_statuses = ("in_progress", "trigger_needed")
        _expires = record.expires_at
        if _expires and _expires.tzinfo is None:
            _expires = _expires.replace(tzinfo=timezone.utc)
        if record.bonus_status in active_statuses and _expires and datetime.now(timezone.utc) > _expires:
            record.bonus_status = "completed"
            record.updated_at = datetime.now(timezone.utc)

        is_cleared = record.bonus_status in ("completed", "available", "claimed") or (
            record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement
        )

        progress_pct = 0.0
        if record.wagering_requirement > 0:
            progress_pct = min(100.0, (record.wagered_amount or 0.0) / record.wagering_requirement * 100)

        days_remaining = None
        if record.expires_at and record.bonus_status in active_statuses:
            exp = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=timezone.utc)
            delta = exp - datetime.now(timezone.utc)
            days_remaining = max(0, delta.days)

        return {
            "status": record.bonus_status,
            "bonus_type": record.bonus_type,
            "bonus_amount": record.bonus_amount,
            "wagering_requirement": record.wagering_requirement,
            "wagered_amount": record.wagered_amount,
            "min_odds": record.min_odds if record.min_odds else BONUS_MIN_ODDS,
            "progress_pct": progress_pct,
            "is_cleared": is_cleared,
            "trigger_mode": record.trigger_mode or "cumulative",
            "claimed_at": record.claimed_at.isoformat() if record.claimed_at else None,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "days_remaining": days_remaining,
        }

    def get_bonus_statuses_batch(self, profile_id: int, provider_ids: list[str]) -> dict[str, dict]:
        """Batch-fetch bonus statuses for multiple providers in one query."""
        records = (
            self.db.query(ProfileProviderBonus)
            .filter(
                ProfileProviderBonus.profile_id == profile_id,
                ProfileProviderBonus.provider_id.in_(provider_ids),
            )
            .all()
        )

        record_map = {r.provider_id: r for r in records}
        default = {
            "status": "available",
            "bonus_type": None,
            "bonus_amount": 0.0,
            "wagering_requirement": 0.0,
            "wagered_amount": 0.0,
            "min_odds": 0.0,
            "progress_pct": 100.0,
            "is_cleared": True,
            "claimed_at": None,
            "expires_at": None,
            "days_remaining": None,
        }
        now = datetime.now(timezone.utc)
        active_statuses = ("in_progress", "trigger_needed")
        result = {}

        for pid in provider_ids:
            record = record_map.get(pid)
            if not record:
                result[pid] = dict(default)
                continue

            _expires = record.expires_at
            if _expires and _expires.tzinfo is None:
                _expires = _expires.replace(tzinfo=timezone.utc)
            if record.bonus_status in active_statuses and _expires and now > _expires:
                record.bonus_status = "completed"
                record.updated_at = now

            is_cleared = record.bonus_status in ("completed", "available", "claimed") or (
                record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement
            )
            progress_pct = 0.0
            if record.wagering_requirement > 0:
                progress_pct = min(100.0, (record.wagered_amount or 0.0) / record.wagering_requirement * 100)
            days_remaining = None
            if record.expires_at and record.bonus_status in active_statuses:
                exp = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=timezone.utc)
                days_remaining = max(0, (exp - now).days)

            result[pid] = {
                "status": record.bonus_status,
                "bonus_type": record.bonus_type,
                "bonus_amount": record.bonus_amount,
                "wagering_requirement": record.wagering_requirement,
                "wagered_amount": record.wagered_amount,
                "min_odds": record.min_odds if record.min_odds else BONUS_MIN_ODDS,
                "progress_pct": progress_pct,
                "is_cleared": is_cleared,
                "trigger_mode": record.trigger_mode or "cumulative",
                "claimed_at": record.claimed_at.isoformat() if record.claimed_at else None,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                "days_remaining": days_remaining,
            }

        return result

    def record_wagering(self, profile_id: int, provider_id: str, stake: float, odds: float) -> dict:
        """Record a bet toward wagering requirement."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        if not record or record.bonus_status not in ("in_progress", "trigger_needed"):
            return self.get_bonus_status(profile_id, provider_id)

        # Check if bonus has expired
        _rec_expires = record.expires_at
        if _rec_expires and _rec_expires.tzinfo is None:
            _rec_expires = _rec_expires.replace(tzinfo=timezone.utc)
        if _rec_expires and datetime.now(timezone.utc) > _rec_expires:
            record.bonus_status = "completed"
            record.updated_at = datetime.now(timezone.utc)
            return self.get_bonus_status(profile_id, provider_id)

        provider_min_odds = record.min_odds if record.min_odds else BONUS_MIN_ODDS
        if odds < provider_min_odds:
            return self.get_bonus_status(profile_id, provider_id)

        record.wagered_amount = (record.wagered_amount or 0.0) + stake
        record.updated_at = datetime.now(timezone.utc)

        if record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement:
            if record.bonus_status == "trigger_needed":
                if record.bonus_type == "bonusdeposit":
                    # Two-phase bonusdeposit: trigger met → unlock bonus money
                    # Add bonus to balance and start main wagering phase
                    self.adjust_balance(profile_id, provider_id, record.bonus_amount)
                    # wagering_multiplier is defined as "× bonus amount"
                    # e.g. 12 means bonus×12 (equivalent to (dep+bonus)×6 when dep=bonus)
                    wager_req = record.bonus_amount * record.wagering_multiplier
                    if wager_req <= 0:
                        # Wager-first model: no wager phase, bonus is cash
                        record.bonus_status = "completed"
                        record.wagering_requirement = 0.0
                        record.wagered_amount = 0.0
                    else:
                        record.bonus_status = "in_progress"
                        record.wagering_requirement = wager_req
                        record.wagered_amount = 0.0
                        record.min_odds = record.main_min_odds or BONUS_MIN_ODDS
                else:
                    # Freebet trigger — don't auto-advance, user must activate
                    pass
            else:
                # Wager complete — withdrawal restriction lifted
                # For bonusdeposit: bonus was already credited at trigger completion (trigger_needed → in_progress)
                # so no additional balance adjustment needed here.
                record.bonus_status = "completed"

        return self.get_bonus_status(profile_id, provider_id)

    def start_bonus_wagering(
        self,
        profile_id: int,
        provider_id: str,
        bonus_amount: float,
        wagering_multiplier: float = 10.0,
        min_odds: float = 1.80,
        deadline_days: int | None = None,
    ) -> dict:
        """Start tracking bonus wagering for a provider."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        wagering_requirement = bonus_amount * wagering_multiplier
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=deadline_days or BONUS_WAGERING_DAYS)

        if record:
            record.bonus_status = "in_progress"
            record.bonus_type = "bonusdeposit"
            record.bonus_amount = bonus_amount
            record.wagering_multiplier = wagering_multiplier
            record.wagering_requirement = wagering_requirement
            record.wagered_amount = 0.0
            record.min_odds = min_odds
            record.claimed_at = now
            record.expires_at = expires
            record.updated_at = now
        else:
            record = ProfileProviderBonus(
                profile_id=profile_id,
                provider_id=provider_id,
                bonus_status="in_progress",
                bonus_type="bonusdeposit",
                bonus_amount=bonus_amount,
                wagering_multiplier=wagering_multiplier,
                wagering_requirement=wagering_requirement,
                wagered_amount=0.0,
                min_odds=min_odds,
                claimed_at=now,
                expires_at=expires,
            )
            self.db.add(record)

        return self.get_bonus_status(profile_id, provider_id)

    def start_bonus_trigger(
        self,
        profile_id: int,
        provider_id: str,
        bonus_amount: float,
        trigger_wagering: float,
        trigger_min_odds: float = 1.50,
        main_wagering_multiplier: float = 12.0,
        main_min_odds: float = 1.80,
        deadline_days: int | None = None,
        deposit_amount: float | None = None,
        trigger_mode: str = "cumulative",
    ) -> dict:
        """Start two-phase bonus: trigger first, then main wagering.

        Phase 1 (trigger_needed): wager deposit×multiplier at trigger_odds to unlock bonus.
        Phase 2 (in_progress): bonus added to balance, wager (deposit+bonus)×multiplier at main_min_odds.
        """
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=deadline_days or BONUS_WAGERING_DAYS)

        kwargs = dict(
            bonus_status="trigger_needed",
            bonus_type="bonusdeposit",
            bonus_amount=bonus_amount,
            wagering_multiplier=main_wagering_multiplier,
            wagering_requirement=trigger_wagering,  # Phase 1: deposit × trigger_multiplier
            wagered_amount=0.0,
            min_odds=trigger_min_odds,  # Phase 1: trigger odds
            main_min_odds=main_min_odds,  # Saved for phase 2
            deposit_amount=deposit_amount,  # Original deposit for phase 2 calc
            trigger_mode=trigger_mode,
            claimed_at=now,
            expires_at=expires,
            updated_at=now,
        )

        if record:
            for k, v in kwargs.items():
                setattr(record, k, v)
        else:
            record = ProfileProviderBonus(
                profile_id=profile_id,
                provider_id=provider_id,
                **kwargs,
            )
            self.db.add(record)

        return self.get_bonus_status(profile_id, provider_id)

    def claim_bonus(self, profile_id: int, provider_id: str) -> dict:
        """Mark a bonus as already claimed (used on another account)."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        now = datetime.now(timezone.utc)
        if record:
            record.bonus_status = "claimed"
            record.claimed_at = now
            record.expires_at = None
            record.updated_at = now
        else:
            record = ProfileProviderBonus(
                profile_id=profile_id,
                provider_id=provider_id,
                bonus_status="claimed",
                claimed_at=now,
            )
            self.db.add(record)

        return self.get_bonus_status(profile_id, provider_id)

    def unclaim_bonus(self, profile_id: int, provider_id: str) -> dict:
        """Reset a claimed bonus back to available."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        if record:
            record.bonus_status = "available"
            record.bonus_type = None
            record.claimed_at = None
            record.expires_at = None
            record.bonus_amount = 0.0
            record.wagering_requirement = 0.0
            record.wagered_amount = 0.0
            record.updated_at = datetime.now(timezone.utc)

        return self.get_bonus_status(profile_id, provider_id)

    def start_freebet_tracking(
        self,
        profile_id: int,
        provider_id: str,
        bonus_amount: float,
        min_odds: float = 1.80,
        trigger_wagering: float | None = None,
        deadline_days: int | None = None,
        trigger_mode: str = "single",
    ) -> dict:
        """Start freebet tracking — user needs to wager trigger amount to unlock."""
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        wagering_req = trigger_wagering or bonus_amount
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=deadline_days or BONUS_WAGERING_DAYS)

        kwargs = dict(
            bonus_status="trigger_needed",
            bonus_type="freebet",
            bonus_amount=bonus_amount,
            wagering_multiplier=1.0,
            wagering_requirement=wagering_req,
            wagered_amount=0.0,
            min_odds=min_odds,
            trigger_mode=trigger_mode,
            claimed_at=now,
            expires_at=expires,
            updated_at=now,
        )

        if record:
            for k, v in kwargs.items():
                setattr(record, k, v)
        else:
            record = ProfileProviderBonus(
                profile_id=profile_id,
                provider_id=provider_id,
                **kwargs,
            )
            self.db.add(record)

        return self.get_bonus_status(profile_id, provider_id)

    def advance_freebet_status(self, profile_id: int, provider_id: str, new_status: str) -> dict:
        """Advance freebet status through its phases."""
        valid_transitions = {
            "trigger_needed": "freebet_available",
            "freebet_available": "completed",
        }

        record = (
            self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == profile_id, ProfileProviderBonus.provider_id == provider_id)
            .first()
        )

        if not record:
            return self.get_bonus_status(profile_id, provider_id)

        expected = valid_transitions.get(record.bonus_status)
        if expected != new_status:
            return self.get_bonus_status(profile_id, provider_id)

        record.bonus_status = new_status
        record.updated_at = datetime.now(timezone.utc)

        return self.get_bonus_status(profile_id, provider_id)

    def get_wagering_prognosis(self, profile_id: int, provider_id: str) -> dict | None:
        """Calculate estimated time to complete wagering based on recent bet pace.

        Returns both per-provider stats and total (all-provider) stats for context.
        """
        record = (
            self.db.query(ProfileProviderBonus)
            .filter(
                ProfileProviderBonus.profile_id == profile_id,
                ProfileProviderBonus.provider_id == provider_id,
            )
            .first()
        )

        if not record or record.bonus_status not in ("in_progress", "trigger_needed"):
            return None

        remaining = max(0.0, (record.wagering_requirement or 0.0) - (record.wagered_amount or 0.0))
        if remaining == 0:
            return None

        from ..db.models import Bet

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        min_odds = record.min_odds or BONUS_MIN_ODDS

        # --- Required pace from deadline ---
        days_remaining = 0.0
        required_weekly_wagering = 0.0
        if record.expires_at:
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            days_remaining = max(0, (expires_at - datetime.now(timezone.utc)).total_seconds() / 86400)
            weeks_remaining = days_remaining / 7
            required_weekly_wagering = remaining / weeks_remaining if weeks_remaining > 0 else remaining

        # --- Aggregate per-provider qualifying + total in two single-row SQL queries
        # instead of materializing both bet lists in Python. The function only
        # needs (count, sum(stake), min(placed_at)) per group — `func.*` does
        # exactly that and avoids two full table scans.
        from sqlalchemy import func

        def _agg_stats(filters: list, *, total_remaining: float):
            """Run ONE aggregate query and return (bets_per_week, avg_stake, weekly_wagering, est_weeks)."""
            row = (
                self.db.query(
                    func.count(Bet.id),
                    func.sum(Bet.stake),
                    func.min(Bet.placed_at),
                )
                .filter(*filters)
                .one()
            )
            count, stake_sum, earliest = row
            if not count:
                return 0.0, 0.0, 0.0, None
            if earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
            days_span = max(1, (datetime.now(timezone.utc) - earliest).days)
            bets_per_week = count / (days_span / 7) if days_span > 0 else 0
            avg_stake = float(stake_sum) / count
            weekly_wagering = bets_per_week * avg_stake
            est_weeks = total_remaining / weekly_wagering if weekly_wagering > 0 else None
            return bets_per_week, avg_stake, weekly_wagering, est_weeks

        # Per-provider qualifying bets
        bets_per_week, avg_stake, weekly_wagering, est_weeks = _agg_stats(
            [
                Bet.profile_id == profile_id,
                Bet.provider_id == provider_id,
                Bet.placed_at >= cutoff,
                Bet.odds >= min_odds,
            ],
            total_remaining=remaining,
        )

        # Total across ALL providers (overall betting pace + bankroll context)
        total_bets_per_week, total_avg_stake, total_weekly_wagering, _ = _agg_stats(
            [
                Bet.profile_id == profile_id,
                Bet.placed_at >= cutoff,
            ],
            total_remaining=remaining,
        )

        bankroll = self.get_total_bankroll(profile_id)

        # Cap actual pace to bankroll (can't wager more than you have)
        effective_weekly_wagering = min(total_weekly_wagering, bankroll) if bankroll > 0 else total_weekly_wagering

        return {
            "remaining": round(remaining, 0),
            "bets_per_week": round(bets_per_week, 1),
            "avg_stake": round(avg_stake, 0),
            "est_weeks": round(est_weeks, 1) if est_weeks else None,
            "weekly_wagering": round(weekly_wagering, 0),
            # Total context
            "total_bets_per_week": round(total_bets_per_week, 1),
            "total_avg_stake": round(total_avg_stake, 0),
            "total_weekly_wagering": round(effective_weekly_wagering, 0),
            "bankroll": round(bankroll, 0),
            # Required pace from deadline
            "required_weekly_wagering": round(required_weekly_wagering, 0),
        }
