"""Profile repository - balance, bonus, and profile data access."""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..db.models import (
    Profile, ProfileProviderBalance, ProfileProviderBonus, BONUS_MIN_ODDS,
)

BONUS_WAGERING_DAYS = 60  # Days to complete wagering before bonus expires


class ProfileRepo:
    """Data access for profiles, balances, and bonus tracking."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Profile ----

    def get_active(self) -> Profile:
        """Get the currently active profile, creating default if none exists."""
        profile = self.db.query(Profile).filter(Profile.is_active == True).first()
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
        record = self.db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == profile_id,
            ProfileProviderBalance.provider_id == provider_id
        ).first()
        return record.balance if record else 0.0

    def set_balance(self, profile_id: int, provider_id: str, balance: float) -> None:
        """Set balance for a specific profile and provider."""
        record = self.db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == profile_id,
            ProfileProviderBalance.provider_id == provider_id
        ).first()

        if record:
            record.balance = balance
            record.updated_at = datetime.utcnow()
        else:
            record = ProfileProviderBalance(
                profile_id=profile_id,
                provider_id=provider_id,
                balance=balance
            )
            self.db.add(record)

    def adjust_balance(self, profile_id: int, provider_id: str, amount: float) -> float:
        """Adjust balance for a specific profile and provider. Returns new balance."""
        record = self.db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == profile_id,
            ProfileProviderBalance.provider_id == provider_id
        ).first()

        if record:
            record.balance += amount
            record.updated_at = datetime.utcnow()
            return record.balance
        else:
            record = ProfileProviderBalance(
                profile_id=profile_id,
                provider_id=provider_id,
                balance=amount
            )
            self.db.add(record)
            return amount

    def get_total_bankroll(self, profile_id: int) -> float:
        """Get total bankroll for a profile (sum of all provider balances)."""
        records = self.db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == profile_id
        ).all()
        return sum(r.balance for r in records)

    def copy_balances(self, from_profile_id: int, to_profile_id: int) -> int:
        """Copy all balances from one profile to another. Returns count copied."""
        source_balances = self.db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == from_profile_id
        ).all()

        count = 0
        for source in source_balances:
            existing = self.db.query(ProfileProviderBalance).filter(
                ProfileProviderBalance.profile_id == to_profile_id,
                ProfileProviderBalance.provider_id == source.provider_id
            ).first()

            if not existing:
                new_balance = ProfileProviderBalance(
                    profile_id=to_profile_id,
                    provider_id=source.provider_id,
                    balance=source.balance
                )
                self.db.add(new_balance)
                count += 1

        return count

    # ---- Bonus ----

    def get_bonus_status(self, profile_id: int, provider_id: str) -> dict:
        """Get bonus status and wagering progress for a provider."""
        record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        if not record:
            return {
                "status": "available",
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
        if (record.bonus_status == "in_progress" and record.expires_at
                and datetime.utcnow() > record.expires_at):
            record.bonus_status = "completed"
            record.updated_at = datetime.utcnow()

        is_cleared = (
            record.bonus_status in ("completed", "available", "claimed") or
            (record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement)
        )

        progress_pct = 0.0
        if record.wagering_requirement > 0:
            progress_pct = min(100.0, record.wagered_amount / record.wagering_requirement * 100)

        days_remaining = None
        if record.expires_at and record.bonus_status == "in_progress":
            delta = record.expires_at - datetime.utcnow()
            days_remaining = max(0, delta.days)

        return {
            "status": record.bonus_status,
            "bonus_amount": record.bonus_amount,
            "wagering_requirement": record.wagering_requirement,
            "wagered_amount": record.wagered_amount,
            "min_odds": record.min_odds if record.min_odds else BONUS_MIN_ODDS,
            "progress_pct": progress_pct,
            "is_cleared": is_cleared,
            "claimed_at": record.claimed_at.isoformat() if record.claimed_at else None,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "days_remaining": days_remaining,
        }

    def record_wagering(self, profile_id: int, provider_id: str, stake: float, odds: float) -> dict:
        """Record a bet toward wagering requirement."""
        record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        if not record or record.bonus_status != "in_progress":
            return self.get_bonus_status(profile_id, provider_id)

        # Check if bonus has expired
        if record.expires_at and datetime.utcnow() > record.expires_at:
            record.bonus_status = "completed"
            record.updated_at = datetime.utcnow()
            return self.get_bonus_status(profile_id, provider_id)

        provider_min_odds = record.min_odds if record.min_odds else BONUS_MIN_ODDS
        if odds < provider_min_odds:
            return self.get_bonus_status(profile_id, provider_id)

        record.wagered_amount = (record.wagered_amount or 0.0) + stake
        record.updated_at = datetime.utcnow()

        if record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement:
            record.bonus_status = "completed"

        return self.get_bonus_status(profile_id, provider_id)

    def start_bonus_wagering(
        self,
        profile_id: int,
        provider_id: str,
        bonus_amount: float,
        wagering_multiplier: float = 10.0,
        min_odds: float = 1.80,
    ) -> dict:
        """Start tracking bonus wagering for a provider."""
        record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        wagering_requirement = bonus_amount * wagering_multiplier
        now = datetime.utcnow()
        expires = now + timedelta(days=BONUS_WAGERING_DAYS)

        if record:
            record.bonus_status = "in_progress"
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

    def claim_bonus(self, profile_id: int, provider_id: str) -> dict:
        """Mark a bonus as already claimed (used on another account)."""
        record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        now = datetime.utcnow()
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
        record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        if record:
            record.bonus_status = "available"
            record.claimed_at = None
            record.expires_at = None
            record.bonus_amount = 0.0
            record.wagering_requirement = 0.0
            record.wagered_amount = 0.0
            record.updated_at = datetime.utcnow()

        return self.get_bonus_status(profile_id, provider_id)
