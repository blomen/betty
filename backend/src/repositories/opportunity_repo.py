"""Opportunity repository - opportunity data access."""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db.models import Event, Opportunity


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
        query = self.db.query(Opportunity, Event).join(Event, Event.id == Opportunity.event_id)

        # Base filters
        query = query.filter(Opportunity.is_active)

        now = datetime.now(UTC)
        query = query.filter((Event.start_time.is_(None)) | (Event.start_time > now))
        # Exclude live/finished events even if start_time check is borderline
        query = query.filter((Event.match_status.is_(None)) | (Event.match_status == "prematch"))

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
                (Opportunity.provider1_id.in_(provider_ids)) | (Opportunity.provider2_id.in_(provider_ids))
            )
        if market:
            query = query.filter(Opportunity.market == market)
        if sport:
            query = query.filter(Event.sport == sport)
        if min_edge is not None:
            query = query.filter(Opportunity.edge_pct >= min_edge)

        # Sort and limit
        return query.order_by(Opportunity.edge_pct.desc().nullslast()).limit(limit).all()

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
        annotations: dict | None = None,
        scope: str = "ft",
    ) -> tuple[bool, "Opportunity"]:
        """Upsert a value opportunity. Returns (is_new, opportunity).

        `scope` keys the row alongside (event, market, outcome, provider) so
        F5/1H/Q1 rows can coexist with the full-game ft row.
        """
        existing = (
            self.db.query(Opportunity)
            .filter(
                Opportunity.event_id == event_id,
                Opportunity.market == market,
                Opportunity.type == "value",
                Opportunity.outcome1 == outcome,
                Opportunity.provider1_id == provider_id,
                Opportunity.scope == scope,
            )
            .first()
        )

        now = datetime.now(UTC)

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
            existing.annotations = annotations
            flag_modified(existing, "outcomes")
            if annotations is not None:
                flag_modified(existing, "annotations")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
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
                annotations=annotations,
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()  # snapshot service needs opp_obj fields populated
            is_new = True

        # Snapshot for CLV tracking (atomic with the opp upsert above).
        from ..services.opp_snapshot_service import OppSnapshotService

        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj

    def upsert_arb(
        self,
        event_id: str,
        market: str,
        legs: list[dict],
        combined_edge_pct: float,
        guaranteed_profit_pct: float,
        point: float | None = None,
        arb_profit_pct: float | None = None,
        arb_legs: list[dict] | None = None,
        scope: str = "ft",
    ) -> tuple[bool, "Opportunity"]:
        """Upsert an arb opportunity. Returns (is_new, opportunity)."""
        # Primary leg = highest edge, secondary = second highest
        sorted_legs = sorted(legs, key=lambda x: x["edge_pct"], reverse=True)
        primary = sorted_legs[0]
        secondary = sorted_legs[1] if len(sorted_legs) > 1 else sorted_legs[0]

        existing = (
            self.db.query(Opportunity)
            .filter(
                Opportunity.event_id == event_id,
                Opportunity.market == market,
                Opportunity.type == "arb",
                Opportunity.scope == scope,
            )
            .first()
        )

        now = datetime.now(UTC)

        legs_list = [
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
        outcomes_json = {
            "legs": legs_list,
            "arb_profit_pct": arb_profit_pct,
            "arb_legs": arb_legs,
        }

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
            flag_modified(existing, "outcomes")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
                type="arb",
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
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()
            is_new = True

        from ..services.opp_snapshot_service import OppSnapshotService

        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj

    def upsert_reverse(
        self,
        event_id: str,
        market: str,
        legs: list[dict],
        combined_edge_pct: float,
        guaranteed_profit_pct: float,
        point: float | None = None,
        scope: str = "ft",
    ) -> tuple[bool, "Opportunity"]:
        """Upsert a reverse arb opportunity. Returns (is_new, opportunity)."""
        sorted_legs = sorted(legs, key=lambda x: x["edge_pct"], reverse=True)
        primary = sorted_legs[0]
        secondary = sorted_legs[1] if len(sorted_legs) > 1 else sorted_legs[0]

        existing = (
            self.db.query(Opportunity)
            .filter(
                Opportunity.event_id == event_id,
                Opportunity.market == market,
                Opportunity.type == "reverse",
                Opportunity.scope == scope,
            )
            .first()
        )

        now = datetime.now(UTC)

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
            flag_modified(existing, "outcomes")
            return False, existing
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
                scope=scope,
            )
            self.db.add(opp)
            return True, opp

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
        scope: str = "ft",
    ) -> tuple[bool, "Opportunity"]:
        """Upsert a reverse value opportunity (Pinnacle vs consensus). Returns (is_new, opportunity)."""
        existing = (
            self.db.query(Opportunity)
            .filter(
                Opportunity.event_id == event_id,
                Opportunity.market == market,
                Opportunity.type == "reverse_value",
                Opportunity.outcome1 == outcome,
                Opportunity.scope == scope,
            )
            .first()
        )

        now = datetime.now(UTC)

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
            flag_modified(existing, "outcomes")
            opp_obj = existing
            is_new = False
        else:
            opp_obj = Opportunity(
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
                scope=scope,
            )
            self.db.add(opp_obj)
            self.db.flush()
            is_new = True

        from ..services.opp_snapshot_service import OppSnapshotService

        OppSnapshotService(self.db).upsert_from_opportunity(opp_obj)

        return is_new, opp_obj

    def cleanup_stale(self, changed_event_ids: set[str] | None = None) -> dict:
        """
        Clean up stale *opportunity* rows during extraction analysis.

        Past-event / odds deletion is NOT done here — it races with concurrent
        provider extractions writing to the same odds rows, causing
        UniqueViolation / Deadlock / StaleDataError on Pinnacle's upsert and
        stalling the sharp source. The dedicated 6h cleanup tier
        (`Scheduler._run_cleanup`) handles past-event + odds deletion with a
        48h grace period instead.

        Args:
            changed_event_ids: When provided, only deactivate opportunities for
                these events (incremental mode). When None, deactivate all
                active opportunities (full mode).

        Returns cleanup stats dict.
        """
        stats = {"inactive": 0, "orphaned": 0, "past_events": 0, "past_events_deleted": 0, "deactivated": 0}
        now = datetime.now(UTC)

        # 1. Delete inactive opportunities — SA column needs ~, not Python `not`
        stats["inactive"] = self.db.query(Opportunity).filter(~Opportunity.is_active).delete()

        # 2. Delete orphaned opportunities (event doesn't exist)
        valid_event_subq = self.db.query(Event.id).subquery()
        stats["orphaned"] = (
            self.db.query(Opportunity)
            .filter(~Opportunity.event_id.in_(self.db.query(valid_event_subq)))
            .delete(synchronize_session=False)
        )

        # 3. Delete opportunities for past events — Opportunity table only, no odds/events.
        past_event_ids = [
            e.id
            for e in self.db.query(Event.id)
            .filter(
                Event.start_time < now,
                or_(Event.match_status.is_(None), ~Event.match_status.in_(["live", "finished"])),
            )
            .all()
        ]
        if past_event_ids:
            for i in range(0, len(past_event_ids), 500):
                batch = past_event_ids[i : i + 500]
                stats["past_events"] += (
                    self.db.query(Opportunity).filter(Opportunity.event_id.in_(batch)).delete(synchronize_session=False)
                )

        # 4. Deactivation — incremental vs full
        if changed_event_ids is not None:
            # Incremental: only deactivate opportunities for changed events
            stats["deactivated"] = (
                self.db.query(Opportunity)
                .filter(Opportunity.event_id.in_(changed_event_ids), Opportunity.is_active)
                .update({"is_active": False}, synchronize_session=False)
            )
        else:
            # Full: deactivate all (existing behavior)
            stats["deactivated"] = self.db.query(Opportunity).filter(Opportunity.is_active).update({"is_active": False})

        return stats

    def deactivate_all(self) -> int:
        """Deactivate all active opportunities. Returns count."""
        return self.db.query(Opportunity).filter(Opportunity.is_active).update({"is_active": False})
