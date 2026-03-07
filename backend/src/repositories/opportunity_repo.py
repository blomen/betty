"""Opportunity repository - opportunity data access."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db.models import Event, Odds, Opportunity, Bet


class OpportunityRepo:
    """Data access for opportunities."""

    def __init__(self, db: Session):
        self.db = db

    def find_active(
        self,
        type: str | None = None,
        provider1: str | None = None,
        provider2: str | None = None,
        provider_ids: list[str] | None = None,
        market: str | None = None,
        sport: str | None = None,
        min_edge: float | None = None,
        exclude_provider1: str | None = None,
        limit: int = 2000,
    ) -> list[tuple[Opportunity, Event]]:
        """
        Find active opportunities with event data.

        Returns list of (Opportunity, Event) tuples.
        Filters out events that have already started.
        """
        query = self.db.query(Opportunity, Event).join(
            Event, Event.id == Opportunity.event_id
        )

        # Base filters
        query = query.filter(Opportunity.is_active == True)

        now = datetime.now(timezone.utc)
        query = query.filter(
            (Event.start_time.is_(None)) | (Event.start_time > now)
        )

        # Optional filters
        if type:
            query = query.filter(Opportunity.type == type)
        if provider1:
            query = query.filter(Opportunity.provider1_id == provider1)
        if provider2:
            query = query.filter(Opportunity.provider2_id == provider2)
        if exclude_provider1:
            query = query.filter(Opportunity.provider1_id != exclude_provider1)
        if provider_ids:
            query = query.filter(
                (Opportunity.provider1_id.in_(provider_ids)) |
                (Opportunity.provider2_id.in_(provider_ids))
            )
        if market:
            query = query.filter(Opportunity.market == market)
        if sport:
            query = query.filter(Event.sport == sport)
        if min_edge is not None:
            query = query.filter(Opportunity.edge_pct >= min_edge)

        # Sort and limit
        return (
            query
            .order_by(Opportunity.edge_pct.desc().nullslast())
            .limit(limit)
            .all()
        )

    def upsert_value(
        self,
        event_id: str,
        market: str,
        outcome: str,
        provider_id: str,
        provider_odds: float,
        fair_odds: float,
        edge_pct: float,
        outcomes_json: list[dict],
        point: float | None = None,
    ) -> bool:
        """Upsert a value opportunity. Returns True if new."""
        existing = self.db.query(Opportunity).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.type == "value",
            Opportunity.outcome1 == outcome,
            Opportunity.provider1_id == provider_id
        ).first()

        now = datetime.now(timezone.utc)

        if existing:
            existing.is_active = True
            existing.edge_pct = edge_pct
            existing.provider1_id = provider_id
            existing.odds1 = provider_odds
            existing.provider2_id = "pinnacle"
            existing.odds2 = fair_odds
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            return False
        else:
            opp = Opportunity(
                type="value",
                event_id=event_id,
                market=market,
                outcome1=outcome,
                edge_pct=edge_pct,
                provider1_id=provider_id,
                odds1=provider_odds,
                provider2_id="pinnacle",
                odds2=fair_odds,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
            )
            self.db.add(opp)
            return True

    def upsert_dutch(
        self,
        event_id: str,
        market: str,
        legs: list[dict],
        combined_edge_pct: float,
        guaranteed_profit_pct: float,
        point: float | None = None,
    ) -> bool:
        """Upsert a dutch opportunity. Returns True if new."""
        # Primary leg = highest edge, secondary = second highest
        sorted_legs = sorted(legs, key=lambda x: x["edge_pct"], reverse=True)
        primary = sorted_legs[0]
        secondary = sorted_legs[1] if len(sorted_legs) > 1 else sorted_legs[0]

        existing = self.db.query(Opportunity).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.type == "dutch",
        ).first()

        now = datetime.now(timezone.utc)

        outcomes_json = [
            {
                "outcome": leg["outcome"],
                "provider": leg["provider"],
                "odds": leg["odds"],
                "edge_pct": leg["edge_pct"],
                "fair_odds": leg["fair_odds"],
                "stake_pct": leg["stake_pct"],
                "is_sharp": leg.get("is_sharp", False),
            }
            for leg in sorted_legs
        ]

        if existing:
            existing.is_active = True
            existing.provider1_id = primary["provider"]
            existing.provider2_id = secondary["provider"]
            existing.odds1 = primary["odds"]
            existing.odds2 = secondary["odds"]
            existing.outcome1 = primary["outcome"]
            existing.outcome2 = secondary["outcome"]
            existing.profit_pct = guaranteed_profit_pct
            existing.edge_pct = combined_edge_pct
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            return False
        else:
            opp = Opportunity(
                type="dutch",
                event_id=event_id,
                market=market,
                outcome1=primary["outcome"],
                outcome2=secondary["outcome"],
                provider1_id=primary["provider"],
                provider2_id=secondary["provider"],
                odds1=primary["odds"],
                odds2=secondary["odds"],
                profit_pct=guaranteed_profit_pct,
                edge_pct=combined_edge_pct,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
            )
            self.db.add(opp)
            return True

    def upsert_reverse(
        self,
        event_id: str,
        market: str,
        legs: list[dict],
        combined_edge_pct: float,
        guaranteed_profit_pct: float,
        point: float | None = None,
    ) -> bool:
        """Upsert a reverse dutch opportunity. Returns True if new."""
        sorted_legs = sorted(legs, key=lambda x: x["edge_pct"], reverse=True)
        primary = sorted_legs[0]
        secondary = sorted_legs[1] if len(sorted_legs) > 1 else sorted_legs[0]

        existing = self.db.query(Opportunity).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.type == "reverse",
        ).first()

        now = datetime.now(timezone.utc)

        outcomes_json = [
            {
                "outcome": leg["outcome"],
                "provider": leg["provider"],
                "odds": leg["odds"],
                "edge_pct": leg["edge_pct"],
                "fair_odds": leg["fair_odds"],
                "stake_pct": leg["stake_pct"],
                "is_sharp": leg.get("is_sharp", False),
            }
            for leg in sorted_legs
        ]

        if existing:
            existing.is_active = True
            existing.provider1_id = primary["provider"]
            existing.provider2_id = secondary["provider"]
            existing.odds1 = primary["odds"]
            existing.odds2 = secondary["odds"]
            existing.outcome1 = primary["outcome"]
            existing.outcome2 = secondary["outcome"]
            existing.profit_pct = guaranteed_profit_pct
            existing.edge_pct = combined_edge_pct
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            return False
        else:
            opp = Opportunity(
                type="reverse",
                event_id=event_id,
                market=market,
                outcome1=primary["outcome"],
                outcome2=secondary["outcome"],
                provider1_id=primary["provider"],
                provider2_id=secondary["provider"],
                odds1=primary["odds"],
                odds2=secondary["odds"],
                profit_pct=guaranteed_profit_pct,
                edge_pct=combined_edge_pct,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
            )
            self.db.add(opp)
            return True

    def upsert_reverse_value(
        self,
        event_id: str,
        market: str,
        outcome: str,
        pinnacle_odds: float,
        consensus_fair_odds: float,
        edge_pct: float,
        outcomes_json: list[dict],
        point: float | None = None,
    ) -> bool:
        """Upsert a reverse value opportunity (Pinnacle vs consensus). Returns True if new."""
        existing = self.db.query(Opportunity).filter(
            Opportunity.event_id == event_id,
            Opportunity.market == market,
            Opportunity.type == "reverse_value",
            Opportunity.outcome1 == outcome,
        ).first()

        now = datetime.now(timezone.utc)

        if existing:
            existing.is_active = True
            existing.edge_pct = edge_pct
            existing.provider1_id = "pinnacle"
            existing.odds1 = pinnacle_odds
            existing.provider2_id = "consensus"
            existing.odds2 = consensus_fair_odds
            existing.outcomes = outcomes_json
            existing.point = point
            existing.detected_at = now
            return False
        else:
            opp = Opportunity(
                type="reverse_value",
                event_id=event_id,
                market=market,
                outcome1=outcome,
                edge_pct=edge_pct,
                provider1_id="pinnacle",
                odds1=pinnacle_odds,
                provider2_id="consensus",
                odds2=consensus_fair_odds,
                outcomes=outcomes_json,
                point=point,
                is_active=True,
                detected_at=now,
            )
            self.db.add(opp)
            return True

    def cleanup_stale(self) -> dict:
        """
        Clean up stale data from database.

        Returns cleanup stats dict.
        """
        stats = {"inactive": 0, "orphaned": 0, "past_events": 0,
                 "past_events_deleted": 0, "deactivated": 0}
        now = datetime.now(timezone.utc)

        # 1. Delete inactive opportunities
        stats["inactive"] = self.db.query(Opportunity).filter(
            Opportunity.is_active == False
        ).delete()

        # 2. Delete orphaned opportunities (event doesn't exist)
        valid_event_subq = self.db.query(Event.id).subquery()
        stats["orphaned"] = self.db.query(Opportunity).filter(
            ~Opportunity.event_id.in_(self.db.query(valid_event_subq))
        ).delete(synchronize_session=False)

        # 3. Delete opportunities for past events (but keep live/finished for settlement)
        past_event_subq = self.db.query(Event.id).filter(
            Event.start_time < now,
            or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
        ).subquery()
        stats["past_events"] = self.db.query(Opportunity).filter(
            Opportunity.event_id.in_(self.db.query(past_event_subq))
        ).delete(synchronize_session=False)

        # 4. Delete past events + their odds (cascade)
        #    Preserve events that have bets OR are live/finished (for score tracking + settlement)
        past_event_ids = [
            e.id for e in self.db.query(Event.id).filter(
                Event.start_time < now,
                or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
            ).all()
        ]
        if past_event_ids:
            # Safety: query ALL bets (not just past_event_ids) to ensure
            # we never delete an event referenced by any bet
            event_ids_with_bets = set(
                row[0] for row in self.db.query(Bet.event_id).filter(
                    Bet.event_id.isnot(None)
                ).distinct().all()
            )
            deletable_ids = [
                eid for eid in past_event_ids if eid not in event_ids_with_bets
            ]
            if deletable_ids:
                for i in range(0, len(deletable_ids), 500):
                    batch = deletable_ids[i:i + 500]
                    past_events = self.db.query(Event).filter(
                        Event.id.in_(batch)
                    ).all()
                    for event in past_events:
                        self.db.delete(event)
                        stats["past_events_deleted"] += 1

        # 5. Deactivate remaining (will be refreshed during detection)
        stats["deactivated"] = self.db.query(Opportunity).filter(
            Opportunity.is_active == True
        ).update({"is_active": False})

        return stats

    def deactivate_all(self) -> int:
        """Deactivate all active opportunities. Returns count."""
        return self.db.query(Opportunity).filter(
            Opportunity.is_active == True
        ).update({"is_active": False})
