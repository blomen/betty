"""OppSnapshotService — persist scanner-detected opportunities and backfill CLV.

Sister to BetService.snapshot_closing_odds() (backend/src/services/bet_service.py:568).
Same closing-time definition: latest odds row at-or-before event.start_time.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Event, Opportunity, OppSnapshot


class OppSnapshotService:
    """Persist opp detections and backfill closing-line value."""

    def __init__(self, db: Session):
        self.db = db

    def upsert_from_opportunity(self, opp: Opportunity) -> OppSnapshot:
        """
        Insert a snapshot on first sighting, or bump last_detected_at +
        detection_count on re-detection. Detection-time fields are frozen
        on first sighting and never overwritten.

        Returns the OppSnapshot row (newly inserted or updated in-place).
        """
        now = datetime.now(UTC)

        existing = (
            self.db.query(OppSnapshot)
            .filter(
                OppSnapshot.event_id == opp.event_id,
                OppSnapshot.market == opp.market,
                OppSnapshot.outcome1 == opp.outcome1,
                OppSnapshot.provider1_id == opp.provider1_id,
                OppSnapshot.type == opp.type,
                OppSnapshot.scope == opp.scope,
            )
            .first()
        )

        if existing is not None:
            existing.last_detected_at = now
            existing.detection_count = (existing.detection_count or 0) + 1
            return existing

        # First sighting — freeze detection-time state.
        # Compute time-to-start if we know event start_time.
        ttk = None
        event = self.db.query(Event).filter(Event.id == opp.event_id).first()
        if event is not None and event.start_time is not None:
            start = event.start_time
            # SQLite strips tzinfo on round-trip; coerce both sides to UTC-aware.
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            ttk = (start - now).total_seconds() / 60.0

        # Leg 1 fair-odds benchmark:
        # - value: odds2 is Pinnacle fair (devigged at detect)
        # - arb: leg-1 fair is leg-1's own fair_odds carried in outcomes JSON;
        #        opp.odds2 is the secondary leg's odds, so fair_odds1 here is a
        #        best-effort proxy rather than the true devigged fair for leg 1
        # - reverse_value: leg-1 is Pinnacle raw, opp.odds2 is consensus fair
        fair_odds1 = opp.odds2 if opp.odds2 and opp.odds2 > 1.0 else None

        # Leg 2 is arb-only per the design spec.
        is_arb = opp.type == "arb"
        provider2_id = opp.provider2_id if is_arb else None
        outcome2 = opp.outcome2 if is_arb else None
        odds2_at_detection = opp.odds2 if is_arb else None

        snap = OppSnapshot(
            event_id=opp.event_id,
            type=opp.type,
            market=opp.market,
            outcome1=opp.outcome1,
            point=opp.point,
            scope=opp.scope,
            provider1_id=opp.provider1_id,
            odds1_at_detection=opp.odds1,
            fair_odds1_at_detection=fair_odds1,
            edge_pct_at_detection=opp.edge_pct,
            provider2_id=provider2_id,
            outcome2=outcome2,
            odds2_at_detection=odds2_at_detection,
            first_detected_at=now,
            last_detected_at=now,
            detection_count=1,
            time_to_start_minutes_at_detection=ttk,
        )
        self.db.add(snap)
        self.db.flush()  # populate PK so caller has it
        return snap
