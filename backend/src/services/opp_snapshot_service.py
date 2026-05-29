"""OppSnapshotService — persist scanner-detected opportunities and backfill CLV.

Sister to BetService.snapshot_closing_odds() (backend/src/services/bet_service.py:568).
Same closing-time definition: latest odds row at-or-before event.start_time.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..analysis.sharp_blend import blended_fair_from_rows
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

        # Multi-book sharp blend (shadow). Computed from the same current Odds
        # rows the scanner saw this cycle — detection-time by construction.
        blend = blended_fair_from_rows(
            outcome=opp.outcome1,
            rows=self._blend_member_rows(opp, opp.market, opp.point, opp.scope),
            sport=self._event_sport(opp.event_id),
        )
        blended_fair1 = blend.fair_odds if blend else None
        blend_n_sources = blend.n_sources if blend else None
        blend_sources = blend.sources if blend else None

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
            blended_fair1_at_detection=blended_fair1,
            blend_n_sources_at_detection=blend_n_sources,
            blend_sources=blend_sources,
        )
        self.db.add(snap)
        self.db.flush()  # populate PK so caller has it
        return snap

    def compute_closing_clv(self, batch_size: int = 500) -> dict:
        """
        For every opp_snapshots row where clv_computed_at IS NULL and the
        event has started, backfill provider/pinnacle closing odds + CLV +
        (for arbs) closing prob sum. Mark clv_computed_at = now() even if
        no closing data was available, to avoid reprocessing.

        Mirrors BetService.snapshot_closing_odds() (bet_service.py:568).

        Returns: {"processed": int, "updated": int}
                 - processed: rows where we ran the backfill (incl. data-less)
                 - updated:   rows where we wrote at least one CLV value
        """
        now = datetime.now(UTC)

        rows = (
            self.db.query(OppSnapshot)
            .join(Event, Event.id == OppSnapshot.event_id)
            .filter(
                OppSnapshot.clv_computed_at.is_(None),
                Event.start_time.isnot(None),
                Event.start_time <= now,
            )
            .limit(batch_size)
            .all()
        )

        processed = 0
        updated = 0

        for snap in rows:
            processed += 1
            did_update = False
            event = self.db.query(Event).filter(Event.id == snap.event_id).first()
            start_time = event.start_time if event else None
            sport = event.sport if event else None  # reused by the blend block below
            # SQLite strips tzinfo on round-trip; coerce to UTC-aware so
            # datetime arithmetic with tz-aware Odds.updated_at succeeds.
            if start_time is not None and start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)

            # ---- Leg 1: same-provider closing odds ----
            p1_odds = self._latest_odds(
                event_id=snap.event_id,
                provider_id=snap.provider1_id,
                market=snap.market,
                outcome=snap.outcome1,
                point=snap.point,
                scope=snap.scope,
            )
            if p1_odds is not None and p1_odds.odds > 1.0:
                snap.provider1_closing_odds = p1_odds.odds
                if start_time and p1_odds.updated_at:
                    upd = p1_odds.updated_at
                    if upd.tzinfo is None:
                        upd = upd.replace(tzinfo=UTC)
                    snap.provider1_closing_age_minutes = (start_time - upd).total_seconds() / 60.0
                snap.provider_clv_pct = round((snap.odds1_at_detection / p1_odds.odds - 1) * 100, 2)
                did_update = True

            # ---- Pinnacle closing fair odds (devigged) ----
            pinnacle_fair, pinnacle_age = self._pinnacle_closing_fair(
                event_id=snap.event_id,
                market=snap.market,
                outcome=snap.outcome1,
                point=snap.point,
                scope=snap.scope,
                start_time=start_time,
            )
            if pinnacle_fair is not None:
                snap.pinnacle_closing_fair = pinnacle_fair
                snap.pinnacle_closing_age_minutes = pinnacle_age
                snap.pinnacle_clv_pct = round((snap.odds1_at_detection / pinnacle_fair - 1) * 100, 2)
                did_update = True

            # ---- Arb leg 2 + closing prob sum ----
            if snap.type == "arb" and snap.provider2_id and snap.outcome2:
                p2_odds = self._latest_odds(
                    event_id=snap.event_id,
                    provider_id=snap.provider2_id,
                    market=snap.market,
                    outcome=snap.outcome2,
                    point=snap.point,
                    scope=snap.scope,
                )
                if p2_odds is not None and p2_odds.odds > 1.0:
                    snap.provider2_closing_odds = p2_odds.odds
                    if start_time and p2_odds.updated_at:
                        upd2 = p2_odds.updated_at
                        if upd2.tzinfo is None:
                            upd2 = upd2.replace(tzinfo=UTC)
                        snap.provider2_closing_age_minutes = (start_time - upd2).total_seconds() / 60.0
                    did_update = True

                if snap.provider1_closing_odds and snap.provider2_closing_odds:
                    prob_sum = 1.0 / snap.provider1_closing_odds + 1.0 / snap.provider2_closing_odds
                    snap.closing_prob_sum = prob_sum
                    snap.was_arb_at_close = prob_sum < 1.0

            # ---- Blended sharp closing fair (shadow) ----
            blend = blended_fair_from_rows(
                outcome=snap.outcome1,
                rows=self._blend_member_rows(snap, snap.market, snap.point, snap.scope),
                sport=sport,
            )
            if blend is not None and blend.fair_odds > 1.0:
                snap.blended_closing_fair = blend.fair_odds
                snap.blended_clv_pct = round((snap.odds1_at_detection / blend.fair_odds - 1) * 100, 2)
                did_update = True

            # Always mark done — even when no closing data was available —
            # so the row isn't reprocessed every cycle.
            snap.clv_computed_at = now
            if did_update:
                updated += 1

        self.db.flush()  # push all mutations to DB (caller owns the transaction)
        return {"processed": processed, "updated": updated}

    def _latest_odds(
        self,
        event_id: str,
        provider_id: str,
        market: str,
        outcome: str,
        point: float | None,
        scope: str,
    ):
        """Latest odds row for the given key. Returns Odds or None."""
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.provider_id == provider_id,
            Odds.market == market,
            Odds.outcome == outcome,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        return q.order_by(Odds.updated_at.desc().nullslast()).first()

    def _pinnacle_closing_fair(
        self,
        event_id: str,
        market: str,
        outcome: str,
        point: float | None,
        scope: str,
        start_time,
    ):
        """Pinnacle's odds devigged against sibling outcomes for the same
        (market, point, scope). Returns (fair_odds, age_minutes) or (None, None).
        """
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.provider_id == "pinnacle",
            Odds.market == market,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        siblings = q.all()
        if not siblings:
            return None, None

        # Devig: prob_i = (1/odds_i) / sum(1/odds_j) → fair_i = 1/prob_i
        inv_sum = sum(1.0 / o.odds for o in siblings if o.odds > 1.0)
        if inv_sum <= 0:
            return None, None

        target = next((o for o in siblings if o.outcome == outcome), None)
        if target is None or target.odds <= 1.0:
            return None, None

        fair = 1.0 / ((1.0 / target.odds) / inv_sum)
        age = None
        if start_time and target.updated_at:
            upd = target.updated_at
            if upd.tzinfo is None:
                upd = upd.replace(tzinfo=UTC)
            age = (start_time - upd).total_seconds() / 60.0
        return fair, age

    def _blend_member_rows(self, snap_or_opp, market, point, scope):
        """All Odds rows (any outcome) for the event/market/point/scope across
        every provider — sharp_blend filters to members itself. Returns list[Odds]."""
        from ..db.models import Odds

        q = self.db.query(Odds).filter(
            Odds.event_id == snap_or_opp.event_id,
            Odds.market == market,
            Odds.scope == scope,
        )
        if market in ("spread", "total") and point is not None:
            q = q.filter(Odds.point == point)
        return q.all()

    def _event_sport(self, event_id: str) -> str | None:
        ev = self.db.query(Event).filter(Event.id == event_id).first()
        return ev.sport if ev else None
