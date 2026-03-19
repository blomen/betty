"""MirrorService — orchestrates bet interception, parsing, storage, and notification."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..db.models import get_session, BetTrace, Bet
from ..services.bet_service import BetService
from ..matching.normalizer import normalize_team_name
from .interceptor import BetInterceptor
from .parsers.gecko import GeckoBetParser

logger = logging.getLogger(__name__)


class MirrorService:
    """Coordinates BetInterceptor + parsing + BetService + Broadcaster."""

    def __init__(self, provider_id: str, broadcaster=None, discovery: bool = False):
        self.provider_id = provider_id
        self.broadcaster = broadcaster
        self.parser = GeckoBetParser()
        self.interceptor = BetInterceptor(
            provider_id=provider_id,
            on_bet_response=self._handle_bet_response,
            discovery=discovery,
        )

    async def start(self, site_url: str | None = None):
        """Start the mirror browser."""
        url = site_url or "https://www.spelklubben.se/sv/odds"
        await self.interceptor.start(site_url=url)

    async def stop(self):
        """Stop the mirror browser."""
        await self.interceptor.stop()

    def get_status(self) -> dict[str, Any]:
        """Get current mirror status."""
        return self.interceptor.get_status()

    async def _handle_bet_response(self, url: str, request_body: str | None, response_body: str):
        """Process an intercepted bet placement response."""
        try:
            body = json.loads(response_body)
        except json.JSONDecodeError:
            logger.warning(f"[mirror:{self.provider_id}] Invalid JSON response from {url}")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "failed")
            return

        # Check for rejection
        if self.parser.is_rejection(body):
            logger.info(f"[mirror:{self.provider_id}] Bet rejected")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "rejected")
            self._notify("bet_rejected", {"provider": self.provider_id, "reason": "Bet rejected by bookmaker"})
            return

        # Parse confirmed bet
        parsed = self.parser.parse(body)
        if parsed is None:
            logger.warning(f"[mirror:{self.provider_id}] Could not parse bet response")
            await asyncio.to_thread(self._store_trace_sync, url, request_body, response_body, "failed")
            return

        # Create bet and store trace in a sync thread
        result = await asyncio.to_thread(
            self._process_bet_sync, url, request_body, response_body, parsed
        )

        # Notify frontend (back on event loop thread)
        self._notify("bet_mirrored", result)

    def _process_bet_sync(
        self, url: str, request_body: str | None, response_body: str, parsed: dict
    ) -> dict[str, Any]:
        """Synchronous: create bet + store trace (runs in thread via asyncio.to_thread)."""
        db = get_session()
        try:
            confirmation_id = parsed["confirmation_id"]

            # Dedup: check if this bet was already logged
            existing = db.query(Bet).filter(
                Bet.confirmation_id == confirmation_id
            ).first()
            if existing:
                logger.info(f"[mirror:{self.provider_id}] Bet {confirmation_id} already logged (dedup)")
                return {
                    "status": "duplicate",
                    "confirmation_id": confirmation_id,
                    "provider": self.provider_id,
                }

            # Match event in DB
            event_id = self._match_event(db, parsed)

            # Create bet via BetService
            bet_service = BetService(db)
            bet_result = bet_service.create_bet(
                event_id=event_id,
                provider_id=self.provider_id,
                market=parsed.get("market"),
                outcome=parsed.get("outcome"),
                odds=parsed["odds"],
                stake=parsed["stake"],
                point=parsed.get("point"),
                bet_type="mirror",
            )

            # Update confirmation_id on the created bet
            if "error" not in bet_result:
                bet_obj = db.get(Bet, bet_result["bet_id"])
                if bet_obj:
                    bet_obj.confirmation_id = confirmation_id

            db.commit()

            bet_id = bet_result.get("bet_id")
            parse_status = "ok" if event_id else "unmatched"
            if "error" in bet_result:
                parse_status = "failed"

            # Store trace
            self._store_trace(
                db=db,
                url=url,
                request_body=request_body,
                response_body=response_body,
                parse_status=parse_status,
                provider_bet_id=confirmation_id,
                bet_id=bet_id,
            )
            db.commit()

            event_display = parsed.get("event_name", "Unknown event")
            return {
                "status": "ok" if "error" not in bet_result else "error",
                "confirmation_id": confirmation_id,
                "provider": self.provider_id,
                "event": event_display,
                "market": parsed.get("market"),
                "outcome": parsed.get("outcome"),
                "odds": parsed["odds"],
                "stake": parsed["stake"],
                "matched": event_id is not None,
                "error": bet_result.get("error"),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror:{self.provider_id}] Error processing bet: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "provider": self.provider_id}
        finally:
            db.close()

    def _store_trace_sync(
        self, url: str, request_body: str | None, response_body: str, parse_status: str
    ):
        """Store trace in a new DB session (for rejected/failed bets)."""
        db = get_session()
        try:
            self._store_trace(db, url, request_body, response_body, parse_status)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror:{self.provider_id}] Error storing trace: {e}")
        finally:
            db.close()

    def _store_trace(
        self,
        db,
        url: str,
        request_body: str | None,
        response_body: str,
        parse_status: str,
        provider_bet_id: str | None = None,
        bet_id: int | None = None,
    ) -> BetTrace:
        """Insert a BetTrace record."""
        trace = BetTrace(
            timestamp=datetime.now(timezone.utc),
            provider_id=self.provider_id,
            request_url=url,
            request_body=request_body,
            response_body=response_body,
            bet_id=bet_id,
            provider_bet_id=provider_bet_id,
            parse_status=parse_status,
        )
        db.add(trace)
        return trace

    def _match_event(self, db, parsed: dict) -> str | None:
        """Try to match intercepted bet to an internal Event."""
        from ..db.models import Event
        from rapidfuzz import fuzz
        from datetime import timedelta

        home = parsed.get("home_team")
        away = parsed.get("away_team")
        if not home or not away:
            return None

        # Query events starting within next 7 days to keep candidate set small
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        events = db.query(Event).filter(
            Event.home_team.isnot(None),
            Event.away_team.isnot(None),
            Event.start_time >= now - timedelta(hours=3),
            Event.start_time <= cutoff,
        ).all()

        best_match = None
        best_score = 0.0

        for event in events:
            home_score = fuzz.ratio(home, event.home_team or "")
            away_score = fuzz.ratio(away, event.away_team or "")
            combined = (home_score + away_score) / 2

            if combined > best_score:
                best_score = combined
                best_match = event

        if best_match and best_score >= 75:
            logger.info(
                f"[mirror:{self.provider_id}] Matched to event {best_match.id} "
                f"(score={best_score:.0f})"
            )
            return best_match.id

        logger.warning(f"[mirror:{self.provider_id}] No match for {home} vs {away} (best={best_score:.0f})")
        return None

    def _notify(self, event_type: str, data: dict):
        """Publish SSE event if broadcaster is available."""
        if self.broadcaster:
            self.broadcaster.publish(event_type, data)
