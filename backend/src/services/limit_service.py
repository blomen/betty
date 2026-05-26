"""Limit service - provider limit recording with betting snapshot."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Bet, Provider
from ..repositories import LimitRepo

logger = logging.getLogger(__name__)


class LimitService:
    """Business logic for recording and managing provider limits."""

    def __init__(self, db: Session):
        self.db = db
        self.limit_repo = LimitRepo(db)

    def _build_snapshot(self, profile_id: int, provider_id: str) -> dict:
        """Build betting stats snapshot for a profile+provider."""
        bets = (
            self.db.query(Bet)
            .filter(
                Bet.profile_id == profile_id,
                Bet.provider_id == provider_id,
            )
            .all()
        )

        if not bets:
            return {
                "total_bets": 0,
                "total_stake": 0.0,
                "total_profit": 0.0,
                "win_rate": None,
                "roi_pct": None,
                "avg_clv_pct": None,
                "avg_odds": None,
                "account_age_days": None,
                "first_bet_date": None,
                "last_bet_date": None,
                "sport_breakdown": {},
                "bet_type_breakdown": {},
                "market_breakdown": {},
                "bonus_bets": 0,
            }

        total_bets = len(bets)
        total_stake = sum(b.stake for b in bets)
        total_profit = sum(b.profit for b in bets)

        settled = [b for b in bets if b.result in ("won", "lost")]
        wins = sum(1 for b in settled if b.result == "won")
        win_rate = wins / len(settled) if settled else None

        roi_pct = (total_profit / total_stake * 100) if total_stake > 0 else None

        clv_values = [b.clv_pct for b in bets if b.clv_pct is not None]
        avg_clv_pct = sum(clv_values) / len(clv_values) if clv_values else None

        avg_odds = sum(b.odds for b in bets) / total_bets

        dates = sorted(b.placed_at for b in bets if b.placed_at)
        first_bet_date = dates[0].isoformat() if dates else None
        last_bet_date = dates[-1].isoformat() if dates else None
        account_age_days = (datetime.now(UTC) - dates[0]).days if dates else None

        sport_breakdown = {}
        for b in bets:
            if b.event and b.event.sport:
                sport_breakdown[b.event.sport] = sport_breakdown.get(b.event.sport, 0) + 1

        bet_type_breakdown = {}
        for b in bets:
            bt = b.bet_type or "unknown"
            bet_type_breakdown[bt] = bet_type_breakdown.get(bt, 0) + 1

        market_breakdown = {}
        for b in bets:
            m = b.market or "unknown"
            market_breakdown[m] = market_breakdown.get(m, 0) + 1

        bonus_bets = sum(1 for b in bets if b.is_bonus)

        return {
            "total_bets": total_bets,
            "total_stake": round(total_stake, 2),
            "total_profit": round(total_profit, 2),
            "win_rate": round(win_rate, 3) if win_rate is not None else None,
            "roi_pct": round(roi_pct, 1) if roi_pct is not None else None,
            "avg_clv_pct": round(avg_clv_pct, 2) if avg_clv_pct is not None else None,
            "avg_odds": round(avg_odds, 2),
            "account_age_days": account_age_days,
            "first_bet_date": first_bet_date,
            "last_bet_date": last_bet_date,
            "sport_breakdown": sport_breakdown,
            "bet_type_breakdown": bet_type_breakdown,
            "market_breakdown": market_breakdown,
            "bonus_bets": bonus_bets,
        }

    def record_limit(
        self,
        profile_id: int,
        provider_id: str,
        limit_type: str,
        limit_level: int,
        notes: str | None = None,
        detected_at: datetime | None = None,
    ) -> dict:
        """Record a new provider limit with auto-generated betting snapshot."""
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"success": False, "error": f"Provider {provider_id} not found"}

        existing = self.limit_repo.get_existing(profile_id, provider_id, limit_type)
        if existing:
            return {
                "success": False,
                "error": f"Limit {limit_type} already exists for {provider_id}. Update or delete it first.",
            }

        snapshot = self._build_snapshot(profile_id, provider_id)

        limit = self.limit_repo.create(
            profile_id=profile_id,
            provider_id=provider_id,
            limit_type=limit_type,
            limit_level=limit_level,
            detected_at=detected_at or datetime.now(UTC),
            notes=notes,
            betting_snapshot=snapshot,
        )
        self.db.commit()

        logger.info(
            "Recorded %s (level %d) for %s on profile %d — snapshot: %d bets, %.0f stake",
            limit_type,
            limit_level,
            provider_id,
            profile_id,
            snapshot["total_bets"],
            snapshot["total_stake"],
        )

        return {
            "success": True,
            "id": limit.id,
            "betting_snapshot": snapshot,
        }

    def ban_provider(
        self,
        profile_id: int,
        provider_id: str,
        notes: str | None = None,
    ) -> dict:
        """Ban a provider: record fully_banned limit (level 5) + disable extraction."""
        # Record the limit (reuse existing logic)
        result = self.record_limit(
            profile_id=profile_id,
            provider_id=provider_id,
            limit_type="fully_banned",
            limit_level=5,
            notes=notes,
        )
        if not result["success"]:
            return result

        # Disable extraction for this profile+provider
        from ..db.models import ProviderExtractionSetting

        existing = (
            self.db.query(ProviderExtractionSetting)
            .filter(
                ProviderExtractionSetting.profile_id == profile_id,
                ProviderExtractionSetting.provider_id == provider_id,
            )
            .first()
        )
        if existing:
            existing.enabled = False
        else:
            self.db.add(
                ProviderExtractionSetting(
                    profile_id=profile_id,
                    provider_id=provider_id,
                    enabled=False,
                )
            )
        self.db.commit()

        logger.info("Banned provider %s for profile %d — extraction disabled", provider_id, profile_id)
        return result

    def update_limit(
        self,
        limit_id: int,
        limit_level: int | None = None,
        notes: str | None = None,
    ) -> dict:
        """Update mutable fields on a limit. Snapshot is immutable."""
        limit = self.limit_repo.get_by_id(limit_id)
        if not limit:
            return {"success": False, "error": "Limit not found"}

        if limit_level is not None:
            limit.limit_level = limit_level
        if notes is not None:
            limit.notes = notes
        self.db.commit()

        return {"success": True, "id": limit.id}

    def delete_limit(self, limit_id: int) -> dict:
        """Delete a limit record."""
        limit = self.limit_repo.get_by_id(limit_id)
        if not limit:
            return {"success": False, "error": "Limit not found"}

        self.limit_repo.delete(limit)
        self.db.commit()
        return {"success": True}

    def list_limits(
        self,
        profile_id: int | None = None,
        provider_id: str | None = None,
    ) -> list[dict]:
        """List limits with provider name included."""
        limits = self.limit_repo.list_limits(profile_id, provider_id)
        return [
            {
                "id": l.id,
                "profile_id": l.profile_id,
                "provider_id": l.provider_id,
                "provider_name": l.provider.name if l.provider else l.provider_id,
                "limit_type": l.limit_type,
                "limit_level": l.limit_level,
                "detected_at": l.detected_at.isoformat() if l.detected_at else None,
                "notes": l.notes,
                "betting_snapshot": l.betting_snapshot,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in limits
        ]
