"""AltenarWorkflow — navigation-only stub for Altenar-platform soft books.

Covers: campobet, quickcasino, betinia, swiper, lodur, dbet.

Soft providers are fully manual since the DOM/API automation rewrite.
This workflow exists only to:
  1. Tell the browser which tab to open (home_url)
  2. Navigate that tab to a specific event when the user clicks an arb row
     (navigate_to_event)

Everything else — login detection, balance sync, history sync, bet placement
— is done manually via PlayPage inline controls (place / settle / adjust
odds / adjust balance). The corresponding workflow methods exist only as
no-op satisfiers of the abstract base, returning sentinel values that tell
callers "nothing to do here".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import (
    HistoryEntry,
    PlacementResult,
    ProviderWorkflow,
    WorkflowMode,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


def _g(obj, key, default=None):
    """Get attribute from object or dict — handles both play loop dicts and BetProxy objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


logger = logging.getLogger(__name__)

# Altenar API sport_id → canonical sport string. Used by navigate_to_event's
# sport-consistency check to avoid following a cross-sport false-positive
# event match into the wrong page.
_SPORT_ID_TO_SPORT: dict[int, str] = {
    40: "volleyball",
    66: "football",
    67: "basketball",
    68: "tennis",
    69: "volleyball",
    70: "ice_hockey",
    71: "boxing",
    73: "handball",
    74: "cricket",
    75: "american_football",
    76: "baseball",
    77: "table_tennis",
    84: "mma",
    101: "rugby",
    102: "rugby",
    145: "esports",
}


class AltenarWorkflow(ProviderWorkflow):
    platform = "altenar"

    def __init__(
        self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED
    ):
        super().__init__(provider_id, domain, mode)

    @property
    def home_url(self) -> str:
        # Land on /sv/sport so the user's first impression matches what the
        # bookmaker normally shows (sportsbook lobby, not casino lobby).
        return f"https://{self.domain}/sv/sport"

    # ------------------------------------------------------------------
    # No-op stubs for abstract methods. Soft providers are fully manual;
    # the user manages login/balance/history/placements via PlayPage UI.
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        return False

    async def sync_balance(self, page: Page) -> float:
        return -1

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        return []

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        return PlacementResult(
            status="manual",
            bet_id=_g(bet, "bet_id", 0) or 0,
            reason="soft_provider_manual_only",
        )

    # ------------------------------------------------------------------
    # Navigation — sportRoutingParams URL pattern. Only auto-action we keep
    # for soft providers; everything else is the user clicking on the
    # bookmaker site.
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        eid = _g(bet, "altenar_event_id", None)
        if not eid:
            meta = _g(bet, "provider_meta") or {}
            eid = meta.get("event_id")
        if not eid:
            logger.warning(f"[{self.provider_id}] No altenar_event_id for navigation")
            return False

        meta = _g(bet, "provider_meta") or {}
        sid = _g(bet, "altenar_sport_id", "") or meta.get("sport_id", "")
        cid = _g(bet, "altenar_category_id", "") or meta.get("category_id", "")
        chid = _g(bet, "altenar_championship_id", "") or meta.get("championship_id", "")

        # Sport consistency check — guard against a false-positive event_id
        # that points into a different sport than the bet says (cross-sport
        # fuzzy-match artifact). Cheap inline check, no DOM.
        if sid:
            try:
                inferred_sport = _SPORT_ID_TO_SPORT.get(int(sid))
                bet_sport = _g(bet, "sport", "")
                if inferred_sport and bet_sport and inferred_sport != bet_sport:
                    logger.warning(
                        f"[{self.provider_id}] Sport mismatch — skipping nav: "
                        f"sport_id={sid} ({inferred_sport}) vs bet sport={bet_sport} eid={eid}"
                    )
                    return False
            except (ValueError, TypeError):
                pass

        params = f"page~event__eventId~{eid}"
        if sid:
            params = (
                f"page~event__sportId~{sid}__categoryIds~{cid}"
                f"__championshipIds~{chid}__eventId~{eid}"
            )
        url = f"https://{self.domain}/sv/sport?sportRoutingParams={params}"

        try:
            current = page.url or ""
            if f"eventId~{eid}" in current:
                return True
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            logger.info(f"[{self.provider_id}] Navigated to event {eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Navigate failed: {e}")
            return False
