"""Settlement service — scan pending bets, propose resolved events for confirmation.

Provides API for the settle step:
1. scan_settlements() — find pending bets with resolved events, return proposals
2. confirm_settlement(bet_id, result) — settle a single bet after user confirmation
"""

import logging
from datetime import UTC, datetime

from ..db.models import Bet, get_session

logger = logging.getLogger(__name__)


async def scan_settlements() -> list[dict]:
    """Find pending bets where the event has resolved.

    Returns list of settlement proposals grouped by provider, each with:
    - bet details (id, event, market, outcome, odds, stake)
    - proposed result (won/lost/void)
    - proposed payout
    """
    db = get_session()
    try:
        # Bet.start_time column is `DateTime` (naive). Compare against a naive
        # UTC `now` so the filter works identically on SQLite and Postgres
        # without relying on driver-level tz coercion. Pending the schema-wide
        # migration to timezone-aware columns.
        now = datetime.now(UTC).replace(tzinfo=None)
        pending = (
            db.query(Bet)
            .filter(
                Bet.result == "pending",
                Bet.start_time < now,
            )
            .all()
        )

        if not pending:
            return []

        # Group by provider
        by_provider: dict[str, list] = {}
        for bet in pending:
            by_provider.setdefault(bet.provider_id, []).append(bet)

        proposals = []

        # Polymarket
        if "polymarket" in by_provider:
            poly_proposals = await _scan_polymarket(by_provider["polymarket"])
            proposals.extend(poly_proposals)

        # Future: other providers added here

        return proposals
    finally:
        db.close()


async def _scan_polymarket(bets: list) -> list[dict]:
    """Check Polymarket resolution for pending bets."""
    from ..core.transport import Transport
    from ..providers.polymarket import PolymarketProvider

    # Build slug → bet mapping
    slug_bets: dict[str, list] = {}
    for bet in bets:
        slug = bet.confirmation_id
        if slug:
            slug_bets.setdefault(slug, []).append(bet)

    if not slug_bets:
        return []

    # Fetch resolved events
    transport = Transport()
    provider = PolymarketProvider(transport=transport)
    try:
        resolved = await provider.fetch_resolved(limit=1000)
    except Exception:
        logger.exception("[Settle] Failed to fetch Polymarket resolved events")
        return []

    resolution_map: dict[str, dict] = {}
    for r in resolved:
        slug = r.get("slug", "")
        if slug:
            resolution_map[slug] = r

    proposals = []
    for slug, bet_list in slug_bets.items():
        res = resolution_map.get(slug)
        if not res:
            continue

        winner_team = res.get("winner_team")
        resolved_markets = res.get("resolved_markets") or {}
        home_team = res.get("home_team", "")
        away_team = res.get("away_team", "")

        for bet in bet_list:
            result = _determine_bet_result(
                bet,
                winner_team,
                resolved_markets,
                home_team,
                away_team,
            )
            if result is None:
                continue

            if result == "won":
                payout = round(bet.stake * bet.odds, 2)
            elif result == "void":
                payout = bet.stake
            else:
                payout = 0.0

            proposals.append(
                {
                    "bet_id": bet.id,
                    "provider_id": bet.provider_id,
                    "event_id": bet.event_id,
                    "market": bet.market,
                    "outcome": bet.outcome,
                    "point": bet.point,
                    "odds": bet.odds,
                    "stake": bet.stake,
                    "currency": bet.currency or "USDC",
                    "proposed_result": result,
                    "proposed_payout": payout,
                    "home_team": home_team,
                    "away_team": away_team,
                    "winner": winner_team,
                    "score": f"{res.get('home_score', '?')}-{res.get('away_score', '?')}",
                }
            )

    return proposals


def confirm_settlement(bet_id: int, result: str) -> dict:
    """Settle a single bet after user confirmation."""
    from ..repositories.profile_repo import ProfileRepo
    from .bet_service import BetService

    db = get_session()
    try:
        bet = db.query(Bet).filter(Bet.id == bet_id).first()
        if not bet:
            return {"error": f"Bet {bet_id} not found"}
        if bet.result != "pending":
            return {"error": f"Bet {bet_id} already settled: {bet.result}"}

        if result == "won":
            payout = round(bet.stake * bet.odds, 2)
        elif result == "void":
            payout = bet.stake
        else:
            payout = 0.0

        svc = BetService(db)
        resp = svc.settle_bet(bet_id, result, payout)
        if "error" in resp:
            return resp

        # Credit balance on win/void
        if payout > 0 and bet.profile_id:
            repo = ProfileRepo(db)
            current = repo.get_balance(bet.profile_id, bet.provider_id)
            repo.set_balance(bet.profile_id, bet.provider_id, current + payout)

        db.commit()
        logger.info("[Settle] Confirmed bet %d: %s → %s (payout %.2f)", bet_id, bet.event_id, result, payout)
        return {"status": "settled", "bet_id": bet_id, "result": result, "payout": payout}
    except Exception as exc:
        db.rollback()
        logger.exception("[Settle] Failed to settle bet %d", bet_id)
        return {"error": str(exc)}
    finally:
        db.close()


def _determine_bet_result(
    bet,
    winner_team: str | None,
    resolved_markets: dict,
    home_team: str,
    away_team: str,
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
