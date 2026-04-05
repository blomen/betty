"""Auto-settlement service — scan pending bets and settle resolved events.

Runs as a background task. For each provider:
- Polymarket: fetch resolved events via Gamma API, match against pending bets
- Other providers: TODO — wire up per provider as we go
"""

import asyncio
import logging
from datetime import datetime, timezone

from ..db.models import Bet, get_session
from ..config import get_exchange_rate

logger = logging.getLogger(__name__)

SETTLE_INTERVAL_S = 300  # Check every 5 minutes


async def auto_settle_loop():
    """Background loop: check and settle resolved bets."""
    while True:
        try:
            settled = await settle_pending_bets()
            if settled:
                logger.info("[AutoSettle] Settled %d bets", settled)
        except Exception:
            logger.exception("[AutoSettle] Error in settle loop")
        await asyncio.sleep(SETTLE_INTERVAL_S)


async def settle_pending_bets() -> int:
    """Check all pending bets and settle any that have resolved."""
    db = get_session()
    try:
        # Get all pending bets where event has started
        now = datetime.now(timezone.utc)
        pending = (
            db.query(Bet)
            .filter(
                Bet.result == "pending",
                Bet.start_time < now,
            )
            .all()
        )

        if not pending:
            return 0

        # Group by provider
        by_provider: dict[str, list] = {}
        for bet in pending:
            by_provider.setdefault(bet.provider_id, []).append(bet)

        total_settled = 0

        # Polymarket settlement
        if "polymarket" in by_provider:
            count = await _settle_polymarket(by_provider["polymarket"], db)
            total_settled += count

        # Future: other providers
        # if "pinnacle" in by_provider:
        #     count = await _settle_pinnacle(by_provider["pinnacle"], db)

        return total_settled
    finally:
        db.close()


async def _settle_polymarket(bets: list, db) -> int:
    """Settle Polymarket bets by checking resolution via Gamma API."""
    from ..providers.polymarket import PolymarketProvider
    from ..core.transport import Transport
    from .bet_service import BetService
    from ..repositories.profile_repo import ProfileRepo

    # Build slug → bet mapping from confirmation_id (which stores the event slug)
    slug_bets: dict[str, list] = {}
    for bet in bets:
        slug = bet.confirmation_id  # Event slug stored at placement
        if slug:
            slug_bets.setdefault(slug, []).append(bet)

    if not slug_bets:
        return 0

    # Fetch resolved events from Polymarket
    transport = Transport()
    provider = PolymarketProvider(transport=transport)
    try:
        resolved = await provider.fetch_resolved(limit=1000)
    except Exception:
        logger.exception("[AutoSettle] Failed to fetch Polymarket resolved events")
        return 0

    # Build slug → resolution mapping
    resolution_map: dict[str, dict] = {}
    for r in resolved:
        slug = r.get("slug", "")
        if slug:
            resolution_map[slug] = r

    settled_count = 0
    bet_service = BetService(db)
    profile_repo = ProfileRepo(db)

    for slug, bet_list in slug_bets.items():
        res = resolution_map.get(slug)
        if not res:
            continue  # Not resolved yet

        winner_team = res.get("winner_team")
        resolved_markets = res.get("resolved_markets") or {}
        home_team = res.get("home_team", "")
        away_team = res.get("away_team", "")

        for bet in bet_list:
            result = _determine_bet_result(
                bet, winner_team, resolved_markets, home_team, away_team,
            )
            if result is None:
                continue  # Can't determine — skip

            # Calculate payout
            if result == "won":
                payout = round(bet.stake * bet.odds, 2)
            elif result == "void":
                payout = bet.stake
            else:
                payout = 0.0

            # Settle via BetService (handles CLV, wagering, bonus)
            resp = bet_service.settle_bet(bet.id, result, payout)
            if "error" in resp:
                logger.warning("[AutoSettle] Failed to settle bet %d: %s", bet.id, resp["error"])
                continue

            # Auto-credit balance on win/void
            if payout > 0 and bet.profile_id:
                current = profile_repo.get_balance(bet.profile_id, "polymarket")
                profile_repo.set_balance(bet.profile_id, "polymarket", current + payout)

            settled_count += 1
            logger.info(
                "[AutoSettle] Settled bet %d: %s %s — %s (payout %.2f USDC)",
                bet.id, bet.event_id, bet.outcome, result, payout,
            )

    db.commit()
    return settled_count


def _determine_bet_result(
    bet, winner_team: str | None, resolved_markets: dict,
    home_team: str, away_team: str,
) -> str | None:
    """Determine if a bet won, lost, or voided based on resolution data.

    Returns 'won', 'lost', 'void', or None if can't determine.
    """
    market = bet.market
    outcome = bet.outcome
    point = bet.point

    if market in ("moneyline", "1x2"):
        if not winner_team:
            return None
        # Match winner_team to home/away
        from ..matching import normalize_outcome
        winner_side = normalize_outcome(winner_team, home_team, away_team)
        if winner_side == outcome:
            return "won"
        elif winner_side in ("home", "away"):
            return "lost"
        return None

    elif market == "spread":
        if point is None:
            return None
        pt_str = str(int(point)) if point == int(point) else str(point)
        key = f"spread_{pt_str}"
        winner_side = resolved_markets.get(key)
        if winner_side is None:
            return None
        return "won" if winner_side == outcome else "lost"

    elif market == "total":
        if point is None:
            return None
        pt_str = str(int(point)) if point == int(point) else str(point)
        key = f"total_{pt_str}"
        winner_outcome = resolved_markets.get(key)
        if winner_outcome is None:
            return None
        return "won" if winner_outcome == outcome else "lost"

    return None
