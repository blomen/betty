"""
FireWindowService — Core state, live polling, fire/skip/advance.

Manages the "fire window" between batch building (which identifies +EV bets)
and actual bet placement. Lets the user monitor live prices against sharp fair
odds and manually confirm firing provider by provider.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..analysis.value import compute_edge
from ..db.models import Odds, get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LiveSnapshot:
    bet_id: int
    live_odds: float | None = None
    fair_odds: float | None = None
    live_edge: float | None = None
    original_edge: float = 0.0
    delta: float = 0.0
    category: str = "pending"  # improved | stable | degraded | negative | pending
    last_updated: datetime | None = None


@dataclass
class FireWindowBet:
    bet_id: int
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None
    odds: float
    fair_odds: float
    edge_pct: float
    stake: float
    expected_profit: float
    display_home: str
    display_away: str
    sport: str
    tier: str
    start_time: str | None = None
    market_slug: str | None = None
    poly_outcome: str | None = None
    original_outcome: str | None = None


@dataclass
class FireWindow:
    provider_queue: list[str]
    provider_bets: dict[str, list[FireWindowBet]]
    current_provider: str | None = None
    live_snapshots: dict[int, LiveSnapshot] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "ready"  # ready | active | firing | complete
    _poll_task: Optional[asyncio.Task] = field(default=None, repr=False)
    fired_results: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_window: FireWindow | None = None

POLL_INTERVAL_S = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_window(
    batch: list[dict],
    provider_order: list[str] | None = None,
) -> dict:
    """Create the FireWindow singleton from a list of BatchBet dicts.

    Groups bets by provider, resolves Polymarket metadata from the DB,
    and orders providers: polymarket first, pinnacle second, then soft
    providers sorted by total EV descending.

    Returns the queue response dict.
    """
    global _window

    # Group bets by provider
    provider_bets: dict[str, list[FireWindowBet]] = {}
    for b in batch:
        pid = b.get("provider_id") or b.get("provider", "")
        bet = FireWindowBet(
            bet_id=b.get("bet_id", id(b)),
            provider_id=pid,
            event_id=b.get("event_id", ""),
            market=b.get("market", ""),
            outcome=b.get("outcome", ""),
            point=b.get("point"),
            odds=b.get("odds", 0.0),
            fair_odds=b.get("fair_odds", 0.0),
            edge_pct=b.get("edge_pct", 0.0),
            stake=b.get("stake", 0.0),
            expected_profit=b.get("expected_profit", 0.0),
            display_home=b.get("display_home", ""),
            display_away=b.get("display_away", ""),
            sport=b.get("sport", ""),
            tier=b.get("tier", "soft"),
            start_time=b.get("start_time"),
            original_outcome=b.get("original_outcome"),
        )
        provider_bets.setdefault(pid, []).append(bet)

    # Resolve Polymarket metadata from DB
    if "polymarket" in provider_bets:
        poly_meta = _resolve_polymarket_meta(provider_bets["polymarket"])
        for bet in provider_bets["polymarket"]:
            meta = poly_meta.get(bet.event_id)
            if meta:
                bet.market_slug = meta["market_slug"]
                outcome_map = meta.get("poly_outcome_map", {})
                bet.poly_outcome = outcome_map.get(bet.outcome)
                # Use Polymarket's full team names for display
                if meta.get("poly_home"):
                    bet.display_home = meta["poly_home"]
                if meta.get("poly_away"):
                    bet.display_away = meta["poly_away"]

    # Build provider order
    if provider_order is None:
        provider_order = _default_provider_order(provider_bets)

    _window = FireWindow(
        provider_queue=provider_order,
        provider_bets=provider_bets,
    )

    return _build_queue_response()


def _resolve_polymarket_meta(bets: list[FireWindowBet]) -> dict:
    """Query DB for Polymarket odds rows to get event_slug and poly display names.

    Returns ``{event_id: {market_slug: str, poly_outcome_map: {outcome: display_name}}}``.
    """
    event_ids = list({b.event_id for b in bets})
    if not event_ids:
        return {}

    result: dict[str, dict] = {}
    db = get_session()
    try:
        rows = (
            db.query(Odds)
            .filter(
                Odds.provider_id == "polymarket",
                Odds.event_id.in_(event_ids),
            )
            .all()
        )
        for row in rows:
            meta = row.provider_meta or {}
            eid = row.event_id
            if eid not in result:
                slug = meta.get("event_slug", "")
                result[eid] = {
                    "market_slug": slug,
                    "poly_outcome_map": {},
                    "poly_home": meta.get("poly_home", ""),
                    "poly_away": meta.get("poly_away", ""),
                }
            # Map canonical outcome -> Polymarket display name
            poly_home = meta.get("poly_home")
            poly_away = meta.get("poly_away")
            if row.outcome == "home" and poly_home:
                result[eid]["poly_outcome_map"]["home"] = poly_home
            elif row.outcome == "away" and poly_away:
                result[eid]["poly_outcome_map"]["away"] = poly_away
            elif row.outcome == "draw":
                result[eid]["poly_outcome_map"]["draw"] = "Draw"
    finally:
        db.close()

    return result


def _default_provider_order(provider_bets: dict[str, list[FireWindowBet]]) -> list[str]:
    """polymarket first, pinnacle second, then soft by total EV desc."""
    priority = []
    soft = []
    for pid, bets in provider_bets.items():
        if pid == "polymarket":
            priority.insert(0, pid)
        elif pid == "pinnacle":
            priority.append(pid)
        else:
            total_ev = sum(b.expected_profit for b in bets)
            soft.append((pid, total_ev))
    soft.sort(key=lambda x: x[1], reverse=True)
    return priority + [p for p, _ in soft]


def _build_queue_response() -> dict:
    """Build the queue overview dict for API responses."""
    if _window is None:
        return {"status": "no_window"}

    queue = []
    for pid in _window.provider_queue:
        bets = _window.provider_bets.get(pid, [])
        fired = pid in _window.fired_results
        tier = bets[0].tier if bets else "soft"
        queue.append({
            "provider_id": pid,
            "bet_count": len(bets),
            "total_stake": round(sum(b.stake for b in bets), 2),
            "total_ev": round(sum(b.expected_profit for b in bets), 2),
            "tier": tier,
            "fired": fired,
        })

    return {
        "status": _window.status,
        "current_provider": _window.current_provider,
        "queue": queue,
    }


# ---------------------------------------------------------------------------
# Activate / polling
# ---------------------------------------------------------------------------

async def activate_provider(provider_id: str, mirror_service) -> dict:
    """Activate a provider for live price monitoring.

    Stops any previous poll task, initialises LiveSnapshots for every bet
    belonging to *provider_id*, and for Polymarket opens browser tabs
    before starting the background poll loop.
    """
    if _window is None:
        return {"error": "no fire window open"}

    # Stop existing poll
    _cancel_poll()

    _window.current_provider = provider_id
    _window.status = "active"
    _window.live_snapshots.clear()

    bets = _window.provider_bets.get(provider_id, [])
    for bet in bets:
        # Mark bets with invalid odds (< 1.0) as negative immediately
        if bet.odds < 1.0:
            _window.live_snapshots[bet.bet_id] = LiveSnapshot(
                bet_id=bet.bet_id,
                fair_odds=bet.fair_odds,
                original_edge=bet.edge_pct,
                live_odds=bet.odds,
                live_edge=-99.0,
                category="negative",
            )
        else:
            _window.live_snapshots[bet.bet_id] = LiveSnapshot(
                bet_id=bet.bet_id,
                fair_odds=bet.fair_odds,
                original_edge=bet.edge_pct,
            )

    # Polymarket: open tabs then start polling
    if provider_id == "polymarket" and mirror_service is not None:
        tab_bets = [
            {"market_slug": b.market_slug, "poly_outcome": b.poly_outcome, "bet_id": b.bet_id}
            for b in bets if b.market_slug
        ]
        try:
            await mirror_service._ensure_poly_tabs(tab_bets)
        except Exception:
            logger.exception("Failed to open Polymarket tabs")

    # Start background poll
    _window._poll_task = asyncio.create_task(
        _poll_loop(provider_id, mirror_service)
    )

    return get_live_state()


async def _poll_loop(provider_id: str, mirror_service) -> None:
    """Background loop: update live prices every POLL_INTERVAL_S seconds."""
    try:
        while True:
            await _update_live_prices(provider_id, mirror_service)
            await asyncio.sleep(POLL_INTERVAL_S)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Poll loop crashed for %s", provider_id)


async def _update_live_prices(provider_id: str, mirror_service) -> None:
    """Fetch latest prices and update LiveSnapshots."""
    if _window is None:
        return

    bets = _window.provider_bets.get(provider_id, [])
    now = datetime.now(timezone.utc)

    if provider_id == "polymarket" and mirror_service is not None:
        for bet in bets:
            snap = _window.live_snapshots.get(bet.bet_id)
            if snap is None or not bet.market_slug:
                continue

            page = getattr(mirror_service, "_poly_tabs", {}).get(bet.market_slug)
            if page is None:
                continue

            try:
                buttons = await mirror_service._read_btn_prices(page)
                matched = mirror_service._find_btn_for_market(
                    buttons, bet.outcome, bet.market,
                )
                if matched:
                    price = matched.get("price")
                    logger.debug(
                        "[FireWindow] bet %s (%s/%s): matched=%s, all_sections=%s",
                        bet.bet_id, bet.market, bet.outcome,
                        {k: v for k, v in matched.items()},
                        {btn.get("section", ""): btn.get("text", "")[:30] for btn in buttons[:8]},
                    )
                    if price and 0 < price < 1:
                        # Polymarket prices are probabilities (0-1); convert to decimal odds
                        live_odds = round(1 / price, 4)
                        snap.live_odds = live_odds
                        snap.fair_odds = bet.fair_odds
                        if live_odds:
                            snap.live_edge = compute_edge(provider_id, live_odds, bet.fair_odds)
                            snap.delta = (snap.live_edge or 0) - snap.original_edge
                            snap.category = _categorise(snap.live_edge, snap.delta)
                        snap.last_updated = now
            except Exception:
                logger.debug("Price read failed for bet %s", bet.bet_id, exc_info=True)
    else:
        # Non-Polymarket providers: no live polling implemented yet.
        # Snapshots remain in "pending" state with original edge.
        for bet in bets:
            snap = _window.live_snapshots.get(bet.bet_id)
            if snap:
                snap.live_odds = bet.odds
                snap.live_edge = bet.edge_pct
                snap.delta = 0.0
                snap.category = "stable"
                snap.last_updated = now


def _categorise(live_edge: float | None, delta: float) -> str:
    if live_edge is None:
        return "pending"
    if live_edge <= 0:
        return "negative"
    if delta > 1:
        return "improved"
    if delta < -1:
        return "degraded"
    return "stable"


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------

def get_live_state() -> dict:
    """Return current live state for the active provider."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, []) if pid else []
    tier = bets[0].tier if bets else "soft"

    # Position in queue
    try:
        position = _window.provider_queue.index(pid) + 1 if pid else 0
    except ValueError:
        position = 0

    bet_dicts = []
    active_count = 0
    excluded_count = 0
    total_stake = 0.0
    total_ev = 0.0

    for bet in bets:
        snap = _window.live_snapshots.get(bet.bet_id)
        live_edge = snap.live_edge if snap else None
        category = snap.category if snap else "pending"

        is_active = (live_edge or 0) > 0 if live_edge is not None else True
        if is_active:
            active_count += 1
            total_stake += bet.stake
            total_ev += bet.expected_profit
        else:
            excluded_count += 1

        bet_dicts.append({
            "bet_id": bet.bet_id,
            "provider_id": bet.provider_id,
            "event_id": bet.event_id,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "odds": bet.odds,
            "fair_odds": bet.fair_odds,
            "edge_pct": bet.edge_pct,
            "stake": bet.stake,
            "expected_profit": bet.expected_profit,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "sport": bet.sport,
            "tier": bet.tier,
            "market_slug": bet.market_slug,
            "poly_outcome": bet.poly_outcome,
            "original_outcome": bet.original_outcome,
            "start_time": bet.start_time,
            # Live data
            "live_odds": snap.live_odds if snap else None,
            "live_price_cents": round(100 / snap.live_odds, 1) if snap and snap.live_odds and snap.live_odds > 0 else None,
            "live_edge": snap.live_edge if snap else None,
            "delta": snap.delta if snap else 0.0,
            "category": category,
            "last_updated": snap.last_updated.isoformat() if snap and snap.last_updated else None,
        })

    # Fetch current balance for this provider
    balance = None
    try:
        from ..repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            profile_repo = ProfileRepo(db)
            profile = profile_repo.get_active()
            if profile:
                balance = profile_repo.get_balance(profile.id, pid)
        finally:
            db.close()
    except Exception:
        pass

    return {
        "provider_id": pid,
        "tier": tier,
        "position": position,
        "total_providers": len(_window.provider_queue),
        "status": _window.status,
        "bets": bet_dicts,
        "balance": round(balance, 2) if balance is not None else None,
        "summary": {
            "total_bets": len(bets),
            "active_bets": active_count,
            "excluded_bets": excluded_count,
            "total_stake": round(total_stake, 2),
            "total_ev": round(total_ev, 2),
        },
    }


# ---------------------------------------------------------------------------
# Fire / skip / advance
# ---------------------------------------------------------------------------

async def fire_provider(mirror_service) -> dict:
    """Fire all positive-edge bets for the current provider.

    Does a final price update, splits bets into to_fire (edge > 0) and
    excluded, executes placement, then advances the queue.
    """
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    _window.status = "firing"
    _cancel_poll()

    # Final price snapshot
    await _update_live_prices(pid, mirror_service)

    bets = _window.provider_bets.get(pid, [])
    to_fire = []
    excluded = []
    skipped_balance = []

    # Filter by edge first
    positive_edge = []
    for bet in bets:
        snap = _window.live_snapshots.get(bet.bet_id)
        edge = snap.live_edge if snap else bet.edge_pct
        if edge is not None and edge > 0:
            positive_edge.append((bet, edge))
        else:
            excluded.append(bet)

    # Sort by edge descending — fire best bets first within balance
    positive_edge.sort(key=lambda x: -x[1])

    # Check balance — fire as many as the provider balance allows
    from ..repositories.profile_repo import ProfileRepo
    db = get_session()
    try:
        profile_repo = ProfileRepo(db)
        profile = profile_repo.get_active()
        balance = profile_repo.get_balance(profile.id, pid) if profile else float("inf")
    finally:
        db.close()

    remaining = balance
    for bet, edge in positive_edge:
        if remaining >= bet.stake:
            to_fire.append(bet)
            remaining -= bet.stake
        else:
            skipped_balance.append(bet)

    placed = []
    failed = []

    if pid == "polymarket" and mirror_service is not None:
        poly_tabs = getattr(mirror_service, "_poly_tabs", {})
        for bet in to_fire:
            page = poly_tabs.get(bet.market_slug)
            if page is None:
                failed.append({"bet_id": bet.bet_id, "reason": "no_tab"})
                continue
            try:
                snap = _window.live_snapshots.get(bet.bet_id)
                expected_price = 1 / snap.live_odds if snap and snap.live_odds and snap.live_odds > 0 else 1 / bet.odds
                result = await mirror_service._place_single_polymarket_bet(
                    page=page,
                    bet_id=bet.bet_id,
                    slug=bet.market_slug,
                    outcome=bet.poly_outcome or bet.outcome,
                    amount=bet.stake,
                    expected_price=expected_price,
                    max_slippage=3.0,
                    original_outcome=bet.original_outcome,
                    market_type=bet.market,
                )
                if result.get("status") == "placed":
                    placed.append(result)
                else:
                    failed.append(result)
            except Exception as exc:
                logger.exception("Placement failed for bet %s", bet.bet_id)
                failed.append({"bet_id": bet.bet_id, "reason": str(exc)})

        # Close Polymarket tabs
        try:
            await mirror_service.close_poly_tabs()
        except Exception:
            logger.debug("Failed to close poly tabs", exc_info=True)
    else:
        # Non-Polymarket providers: no automated placement yet
        for bet in to_fire:
            placed.append({
                "bet_id": bet.bet_id,
                "status": "manual",
                "provider_id": pid,
                "stake": bet.stake,
            })

    fire_result = {
        "provider_id": pid,
        "placed": placed,
        "failed": failed,
        "excluded": (
            [{"bet_id": b.bet_id, "reason": "negative_edge"} for b in excluded]
            + [{"bet_id": b.bet_id, "reason": "insufficient_balance"} for b in skipped_balance]
        ),
        "summary": {
            "total": len(bets),
            "fired": len(placed),
            "failed": len(failed),
            "excluded": len(excluded) + len(skipped_balance),
        },
    }

    _window.fired_results[pid] = fire_result

    next_pid = _advance_queue()
    fire_result["next_provider"] = next_pid

    return fire_result


def skip_provider() -> dict:
    """Skip the current provider without firing. Advances the queue."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    _cancel_poll()

    bets = _window.provider_bets.get(pid, [])
    _window.fired_results[pid] = {
        "provider_id": pid,
        "status": "skipped",
        "skipped_bets": len(bets),
        "skipped_stake": round(sum(b.stake for b in bets), 2),
    }

    next_pid = _advance_queue()
    return {
        "provider_id": pid,
        "status": "skipped",
        "next_provider": next_pid,
    }


def _advance_queue() -> str | None:
    """Move to the next unfired provider in the queue.

    Clears live snapshots and sets status to ``ready`` or ``complete``.
    Returns the next provider_id, or None if the queue is exhausted.
    """
    if _window is None:
        return None

    _window.live_snapshots.clear()
    _window.current_provider = None

    for pid in _window.provider_queue:
        if pid not in _window.fired_results:
            _window.current_provider = pid
            _window.status = "ready"
            return pid

    _window.status = "complete"
    return None


# ---------------------------------------------------------------------------
# Summary / lifecycle
# ---------------------------------------------------------------------------

def get_fired_summary() -> dict:
    """Return summary of all providers' fire results."""
    if _window is None:
        return {"error": "no fire window open"}

    providers = []
    total_fired = 0
    total_failed = 0
    total_skipped = 0
    total_excluded = 0

    for pid in _window.provider_queue:
        result = _window.fired_results.get(pid)
        if result is None:
            providers.append({"provider_id": pid, "status": "pending"})
            continue

        if result.get("status") == "skipped":
            total_skipped += result.get("skipped_bets", 0)
            providers.append(result)
        else:
            summary = result.get("summary", {})
            total_fired += summary.get("fired", 0)
            total_failed += summary.get("failed", 0)
            total_excluded += summary.get("excluded", 0)
            providers.append({
                "provider_id": pid,
                "status": "fired",
                **summary,
            })

    return {
        "status": _window.status,
        "providers": providers,
        "totals": {
            "fired": total_fired,
            "failed": total_failed,
            "skipped": total_skipped,
            "excluded": total_excluded,
        },
    }


def close_window() -> None:
    """Tear down the fire window, cancelling any active poll task."""
    global _window
    _cancel_poll()
    _window = None


def get_window() -> FireWindow | None:
    """Return the current FireWindow singleton (or None)."""
    return _window


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cancel_poll() -> None:
    """Cancel the background poll task if running."""
    if _window and _window._poll_task and not _window._poll_task.done():
        _window._poll_task.cancel()
        _window._poll_task = None
