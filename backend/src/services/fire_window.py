"""
FireWindowService — Core state, per-bet live price checking, fire/skip/advance.

No continuous polling. Live price is checked ONCE per bet, right before placement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db.models import Odds, get_session
from ..mirror.workflows import get_workflow

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


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
    matchup_id: str | None = None  # Pinnacle event ID for URL navigation


@dataclass
class FireWindow:
    provider_queue: list[str]
    provider_bets: dict[str, list[FireWindowBet]]
    current_provider: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "ready"  # ready | active | firing | complete
    fired_results: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_window: FireWindow | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_stake(pid: str, stake: float) -> float:
    """Round stake down: $1 for Polymarket, 10 kr for others."""
    if pid == "polymarket":
        return float(int(stake))
    return float(int(stake / 10) * 10)


def _min_bet(pid: str) -> float:
    return 1.0 if pid == "polymarket" else 10.0


def _resolve_provider_meta(provider_bets: dict[str, list[FireWindowBet]]) -> None:
    """Resolve provider-specific metadata (slugs, matchup IDs) from the DB."""
    # Polymarket: event slugs + outcome display names
    if "polymarket" in provider_bets:
        poly_meta = _resolve_polymarket_meta(provider_bets["polymarket"])
        for bet in provider_bets["polymarket"]:
            meta = poly_meta.get(bet.event_id)
            if meta:
                bet.market_slug = meta["market_slug"]
                outcome_map = meta.get("poly_outcome_map", {})
                bet.poly_outcome = outcome_map.get(bet.outcome)
                if meta.get("poly_home"):
                    bet.display_home = meta["poly_home"]
                if meta.get("poly_away"):
                    bet.display_away = meta["poly_away"]

    # Pinnacle: matchup IDs for URL navigation
    if "pinnacle" in provider_bets:
        pin_event_ids = list({b.event_id for b in provider_bets["pinnacle"]})
        if pin_event_ids:
            db = get_session()
            try:
                rows = (
                    db.query(Odds)
                    .filter(
                        Odds.provider_id == "pinnacle",
                        Odds.event_id.in_(pin_event_ids),
                    )
                    .all()
                )
                matchup_map: dict[str, str] = {}
                for row in rows:
                    meta = row.provider_meta or {}
                    mid = meta.get("matchup_id")
                    if mid and row.event_id not in matchup_map:
                        matchup_map[row.event_id] = str(mid)
                for bet in provider_bets["pinnacle"]:
                    bet.matchup_id = matchup_map.get(bet.event_id)
            finally:
                db.close()


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

    # Resolve provider-specific metadata (slugs, matchup IDs)
    _resolve_provider_meta(provider_bets)

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
# Set current provider (replaces activate_provider)
# ---------------------------------------------------------------------------

def set_current_provider(provider_id: str) -> dict:
    """Set the current provider and return live state. No polling, no tabs."""
    if _window is None:
        return {"error": "no fire window open"}

    _window.current_provider = provider_id
    _window.status = "active"
    return get_live_state()


async def activate_provider_workflow(provider_id: str, mirror_service) -> dict:
    """Run the full workflow setup when a provider is activated.

    1. Reuse blank tab or find existing — never create extra tabs
    2. Check login
    3. Settle finished events (start_time passed) across ALL providers
    4. Sync bet history for this provider → settle pending bets
    5. Sync balance → update DB
    """
    result = {"provider_id": provider_id, "steps": {}}

    workflow = get_workflow(provider_id)

    # Get browser context
    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None
    if not context:
        result["steps"]["tab"] = "no_browser_context"
        return result

    # Step 1: Find existing tab or reuse the blank tab
    page = await workflow.find_tab(context)
    if not page:
        from ..config.loader import load_config
        cfg = load_config()
        if workflow.domain:
            url = f"https://www.{workflow.domain}"
        else:
            pconfig = cfg.get_provider(provider_id)
            url = pconfig.site_url or (f"https://www.{pconfig.domain}" if pconfig and pconfig.domain else None)
        if not url:
            result["steps"]["tab"] = "no_url"
            return result
        # Reuse about:blank tab instead of creating new one
        blank = next((p for p in context.pages if (p.url or "").startswith("about:")), None)
        try:
            if blank:
                await blank.goto(url, wait_until="domcontentloaded", timeout=15000)
                page = blank
                result["steps"]["tab"] = "reused_blank"
            else:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                result["steps"]["tab"] = "opened"
            logger.info(f"[FireWindow] {provider_id}: tab → {url}")
        except Exception as e:
            result["steps"]["tab"] = f"failed:{e}"
            return result
    else:
        result["steps"]["tab"] = "found"

    # Step 2: Check login
    try:
        logged_in = await workflow.check_login(page)
        result["steps"]["login"] = "ok" if logged_in else "not_logged_in"
        if not logged_in:
            return result
    except Exception as e:
        result["steps"]["login"] = f"error:{e}"
        return result

    # Step 3: Settle finished events across ALL providers
    try:
        settled_global = _settle_expired_bets()
        result["steps"]["settle_expired"] = settled_global
    except Exception as e:
        result["steps"]["settle_expired"] = f"error:{e}"

    # Step 4: Sync bet history for this provider → settle pending bets
    try:
        history = await workflow.sync_history(page)
        if history:
            settled = _settle_from_history(provider_id, history)
            result["steps"]["history"] = {"fetched": len(history), "settled": settled}
        else:
            result["steps"]["history"] = {"fetched": 0, "settled": 0}
    except Exception as e:
        result["steps"]["history"] = f"error:{e}"
        logger.warning(f"[FireWindow] {provider_id}: sync_history failed: {e}")

    # Step 5: Sync balance
    try:
        balance = await workflow.sync_balance(page)
        if balance >= 0:
            from ..repositories.profile_repo import ProfileRepo
            db = get_session()
            try:
                repo = ProfileRepo(db)
                profile = repo.get_active()
                if profile:
                    old = repo.get_balance(profile.id, provider_id)
                    repo.set_balance(profile.id, provider_id, balance)
                    db.commit()
                    result["steps"]["balance"] = {"old": round(old, 2), "new": round(balance, 2)}
                    logger.info(f"[FireWindow] {provider_id}: balance {old:.2f} → {balance:.2f}")
            finally:
                db.close()
        else:
            result["steps"]["balance"] = "unknown"
    except Exception as e:
        result["steps"]["balance"] = f"error:{e}"
        logger.warning(f"[FireWindow] {provider_id}: sync_balance failed: {e}")

    return result


def _settle_from_history(provider_id: str, history: list) -> int:
    """Match history entries against pending bets in DB and settle them."""
    from .bet_service import BetService
    settled_count = 0
    db = get_session()
    try:
        svc = BetService(db)
        for entry in history:
            if entry.status in ("won", "lost", "void", "cashout"):
                # Try to find matching pending bet
                from ..db.models import Bet
                pending = (
                    db.query(Bet)
                    .filter(
                        Bet.provider_id == provider_id,
                        Bet.result == "pending",
                        Bet.odds == entry.odds,
                        Bet.stake == entry.stake,
                    )
                    .first()
                )
                if pending:
                    pending.result = entry.status
                    if entry.payout is not None:
                        pending.payout = entry.payout
                    settled_count += 1
                    logger.info(
                        f"[FireWindow] Settled {provider_id} bet #{pending.id}: "
                        f"{entry.status} (payout={entry.payout})"
                    )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[FireWindow] settle_from_history failed: {e}")
    finally:
        db.close()
    return settled_count


def _settle_expired_bets() -> dict:
    """Check ALL pending bets across all providers where event start_time has passed.

    For events that have started, mark them as needing settlement check.
    Returns {provider_id: count} of bets flagged for settlement.
    """
    from ..db.models import Bet, Event
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    result = {}
    db = get_session()
    try:
        # Find pending bets where the event has started
        pending = (
            db.query(Bet)
            .filter(Bet.result == "pending")
            .all()
        )

        expired_by_provider: dict[str, list] = {}
        for bet in pending:
            # Check start_time on the bet itself or the linked event
            start = bet.start_time if hasattr(bet, 'start_time') and bet.start_time else None
            if not start:
                # Try to get from event table
                event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                start = event.start_time if event and hasattr(event, 'start_time') else None

            if start and start < now:
                expired_by_provider.setdefault(bet.provider_id, []).append(bet)

        for pid, bets in expired_by_provider.items():
            result[pid] = len(bets)
            logger.info(f"[FireWindow] {pid}: {len(bets)} pending bets with expired events")

        if result:
            total = sum(result.values())
            logger.info(f"[FireWindow] Total expired pending bets: {total} across {len(result)} providers")

    except Exception as e:
        logger.warning(f"[FireWindow] _settle_expired_bets failed: {e}")
    finally:
        db.close()

    return result


# ---------------------------------------------------------------------------
# Live state (simplified — DB odds + balance, no live overlay)
# ---------------------------------------------------------------------------

def get_live_state() -> dict:
    """Return current state for the active provider using DB odds."""
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
        is_active = bet.edge_pct > 0
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
# Single-bet flow: check → confirm → next
# ---------------------------------------------------------------------------

def get_next_bet() -> dict:
    """Get the next unfired bet for the current provider, sorted by edge desc.

    Returns bet details + position info, or {"done": True} if no more bets.
    """
    if _window is None:
        return {"error": "no fire window open"}
    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    bets = _window.provider_bets.get(pid, [])
    fired_ids = _window.fired_results.get(f"{pid}_bet_ids", set())

    # Also check DB for already-placed bets (survives restart)
    already_placed: set[str] = set()
    try:
        import os
        from sqlalchemy import create_engine, text
        db_url = os.environ.get("DATABASE_URL", "")
        sync_url = db_url.replace("+asyncpg", "+psycopg2")
        if sync_url:
            eng = create_engine(sync_url, pool_pre_ping=True)
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT event_id, market, outcome FROM bets "
                    "WHERE provider_id = :pid AND result = 'pending'"
                ), {"pid": pid}).fetchall()
                for row in rows:
                    already_placed.add(f"{row[0]}:{row[1]}:{row[2]}")
            eng.dispose()
        print(f"[FireWindow] Already placed: {len(already_placed)} bets for {pid}")
    except Exception as e:
        print(f"[FireWindow] DB check failed: {e}")

    # Sort by edge desc, find first unfired
    for bet in sorted(bets, key=lambda b: -b.edge_pct):
        if bet.bet_id in fired_ids:
            continue
        if bet.edge_pct <= 0:
            continue
        # Skip if already placed in DB
        bet_key = f"{bet.event_id}:{bet.market}:{bet.outcome}"
        if bet_key in already_placed:
            continue

        # Check balance — adjust stake if needed, skip if too low
        balance = 0
        try:
            from ..repositories.profile_repo import ProfileRepo
            _db = get_session()
            try:
                _repo = ProfileRepo(_db)
                _profile = _repo.get_active()
                if _profile:
                    balance = _repo.get_balance(_profile.id, pid)
            finally:
                _db.close()
        except Exception:
            pass

        if balance < _min_bet(pid):
            continue  # Balance too low — skip to next or done

        # Adjust stake to remaining balance, round down to avoid exceeding
        actual_stake = _round_stake(pid, min(bet.stake, balance))
        actual_profit = actual_stake * (bet.edge_pct / 100)

        cents = round((1 / bet.odds) * 100) if bet.odds > 1 else 0
        fair_cents = round((1 / bet.fair_odds) * 100) if bet.fair_odds > 1 else 0

        remaining = len([b for b in bets if b.bet_id not in fired_ids and b.edge_pct > 0
                         and f"{b.event_id}:{b.market}:{b.outcome}" not in already_placed])

        return {
            "bet_id": bet.bet_id,
            "provider_id": pid,
            "event_id": bet.event_id,
            "display_home": bet.display_home,
            "display_away": bet.display_away,
            "market": bet.market,
            "outcome": bet.outcome,
            "point": bet.point,
            "odds": bet.odds,
            "fair_odds": bet.fair_odds,
            "edge_pct": bet.edge_pct,
            "stake": round(actual_stake, 2),
            "expected_profit": round(actual_profit, 2),
            "tier": bet.tier,
            "market_slug": bet.market_slug,
            "poly_outcome": bet.poly_outcome,
            "original_outcome": bet.original_outcome,
            "start_time": bet.start_time,
            "cents": cents,
            "fair_cents": fair_cents,
            "remaining_bets": remaining,
            "matchup_id": bet.matchup_id,
            "done": False,
        }

    return {"done": True, "provider_id": pid}


async def check_bet(bet_id: int, mirror_service) -> dict:
    """Check live price for a specific bet. Returns price comparison."""
    if _window is None:
        return {"error": "no fire window open"}
    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, [])
    bet = next((b for b in bets if b.bet_id == bet_id), None)
    if bet is None:
        return {"error": f"bet {bet_id} not found"}

    live_edge = None
    live_cents = None

    if mirror_service is not None:
        workflow = get_workflow(pid)
        context = getattr(mirror_service, 'interceptor', None)
        context = getattr(context, 'context', None) if context else None
        if context:
            page = await workflow.find_tab(context)
            if page:
                await workflow.navigate_to_event(page, bet)
                live_edge = await workflow.check_live_price(page, bet)
                live_cents = getattr(bet, '_live_cents', None)

    db_cents = round((1 / bet.odds) * 100) if bet.odds > 1 else 0
    fair_cents = round((1 / bet.fair_odds) * 100) if bet.fair_odds > 1 else 0

    return {
        "bet_id": bet_id,
        "db_cents": db_cents,
        "live_cents": live_cents,
        "fair_cents": fair_cents,
        "db_edge": bet.edge_pct,
        "live_edge": live_edge,
        "is_positive": (live_edge or bet.edge_pct) > 0,
    }


async def place_bet(bet_id: int, mirror_service) -> dict:
    """Place a single confirmed bet, record to DB, sync balance."""
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    bets = _window.provider_bets.get(pid, [])
    bet = next((b for b in bets if b.bet_id == bet_id), None)
    if bet is None:
        return {"error": f"bet {bet_id} not found"}

    # Track fired bets
    fired_key = f"{pid}_bet_ids"
    if fired_key not in _window.fired_results:
        _window.fired_results[fired_key] = set()
    _window.fired_results[fired_key].add(bet_id)

    # Adjust stake to available balance
    balance = float('inf')
    try:
        from ..repositories.profile_repo import ProfileRepo
        _db = get_session()
        try:
            _repo = ProfileRepo(_db)
            _profile = _repo.get_active()
            if _profile:
                balance = _repo.get_balance(_profile.id, pid)
        finally:
            _db.close()
    except Exception:
        pass

    actual_stake = _round_stake(pid, min(bet.stake, balance))
    if actual_stake < _min_bet(pid):
        return {"status": "skipped", "bet_id": bet_id, "reason": "insufficient_balance"}

    label = f"*{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*"

    workflow = get_workflow(pid)
    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None
    page = await workflow.find_tab(context) if context else None

    if page is None and workflow.mode.value == "autonomous":
        print(f"  {label}FAILED no tab*")
        return {"status": "failed", "bet_id": bet_id, "reason": "no_tab"}

    if page:
        result = await workflow.place_bet(page, bet, actual_stake)
    else:
        from ..mirror.workflows.base import PlacementResult
        result = PlacementResult(status="manual", bet_id=bet_id, actual_stake=actual_stake)

    if result.status == "placed":
        _record_bet(bet, pid, result.raw_response or {}, actual_stake)
        _sync_balance_after_bet(bet, pid)
        print(f"  {label}PLACED*")
    elif result.status == "manual":
        print(f"  {label}MANUAL — place in mirror, interceptor records*")
    else:
        print(f"  {label}{result.status.upper()} {result.reason or ''}*")

    return {
        "status": result.status,
        "bet_id": bet_id,
        "provider_id": pid,
        "stake": actual_stake,
        "reason": result.reason,
        **({"actual_odds": result.actual_odds} if result.actual_odds else {}),
    }


def _record_bet(bet: FireWindowBet, provider_id: str, result: dict, actual_stake: float | None = None) -> None:
    """Record placed bet to the database."""
    from .bet_service import BetService
    stake = actual_stake if actual_stake is not None else bet.stake
    db = get_session()
    try:
        svc = BetService(db)
        resp = svc.create_bet(
            event_id=bet.event_id,
            provider_id=provider_id,
            market=bet.market,
            outcome=bet.outcome,
            odds=bet.odds,
            stake=stake,
            point=bet.point,
            fair_odds_at_placement=bet.fair_odds,
            bet_type="value",
        )
        if "error" in resp:
            logger.warning("[FireWindow] Bet recording failed: %s", resp["error"])
        else:
            logger.info("[FireWindow] Bet recorded: id=%s", resp.get("id"))
        db.commit()
    except Exception as exc:
        logger.exception("[FireWindow] Failed to record bet: %s", exc)
        db.rollback()
    finally:
        db.close()


def _sync_balance_after_bet(bet: FireWindowBet, provider_id: str) -> None:
    """Deduct stake from provider balance after placement."""
    from ..repositories.profile_repo import ProfileRepo
    db = get_session()
    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()
        if profile:
            current = repo.get_balance(profile.id, provider_id)
            new_balance = max(0, current - bet.stake)
            repo.set_balance(profile.id, provider_id, new_balance)
            db.commit()
            logger.info("[FireWindow] Balance synced: %s %.2f → %.2f", provider_id, current, new_balance)
    except Exception as exc:
        logger.exception("[FireWindow] Balance sync failed: %s", exc)
        db.rollback()
    finally:
        db.close()


def skip_bet(bet_id: int) -> dict:
    """Skip a bet without placing it."""
    if _window is None:
        return {"error": "no fire window open"}
    pid = _window.current_provider
    fired_key = f"{pid}_bet_ids"
    if fired_key not in _window.fired_results:
        _window.fired_results[fired_key] = set()
    _window.fired_results[fired_key].add(bet_id)
    return {"status": "skipped", "bet_id": bet_id}


# ---------------------------------------------------------------------------
# Fire all (legacy — kept for batch fire)
# ---------------------------------------------------------------------------

async def fire_provider(mirror_service) -> dict:
    """Fire bets for the current provider with per-bet live price checking.

    For each bet (sorted by edge desc):
    - Check balance
    - Check live price via workflow (if supported)
    - Fire only if still +EV
    - Print concise output per bet
    """
    if _window is None:
        return {"error": "no fire window open"}

    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    _window.status = "firing"

    bets = _window.provider_bets.get(pid, [])
    sorted_bets = sorted(bets, key=lambda b: -b.edge_pct)

    # Check balance
    from ..repositories.profile_repo import ProfileRepo
    db = get_session()
    try:
        profile_repo = ProfileRepo(db)
        profile = profile_repo.get_active()
        balance = profile_repo.get_balance(profile.id, pid) if profile else float("inf")
    finally:
        db.close()

    remaining = balance
    placed = []
    failed = []
    excluded = []
    skipped_balance = []

    # Get workflow + browser context once for the whole provider
    workflow = get_workflow(pid)
    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None
    page = await workflow.find_tab(context) if context else None

    for bet in sorted_bets:
        label = f"*{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*"

        # Check live price via workflow if we have a page
        edge = bet.edge_pct
        if page is not None:
            await workflow.navigate_to_event(page, bet)
            live_edge = await workflow.check_live_price(page, bet)
            if live_edge is not None:
                edge = live_edge

        # Skip if not +EV
        if edge <= 0:
            print(f"  {label}SKIP edge={edge:.1f}%*")
            excluded.append({"bet_id": bet.bet_id, "reason": "negative_edge"})
            continue

        # Round stake and check balance
        actual_stake = _round_stake(pid, min(bet.stake, remaining))
        if actual_stake < _min_bet(pid):
            print(f"  {label}SKIP balance*")
            skipped_balance.append({"bet_id": bet.bet_id, "reason": "insufficient_balance"})
            continue

        # Fire the bet
        print(f"  {label}FIRE edge={edge:.1f}%*")

        if page is not None:
            try:
                result = await workflow.place_bet(page, bet, actual_stake)
                if result.status == "placed":
                    placed.append({"bet_id": bet.bet_id, "status": "placed", "provider_id": pid, "stake": actual_stake})
                    _record_bet(bet, pid, result.raw_response or {}, actual_stake)
                    _sync_balance_after_bet(bet, pid)
                    remaining -= actual_stake
                elif result.status == "manual":
                    placed.append({"bet_id": bet.bet_id, "status": "manual", "provider_id": pid, "stake": actual_stake})
                    remaining -= actual_stake
                else:
                    failed.append({"bet_id": bet.bet_id, "reason": result.reason or result.status})
            except Exception as exc:
                logger.exception("Placement failed for bet %s", bet.bet_id)
                failed.append({"bet_id": bet.bet_id, "reason": str(exc)})
        else:
            # No browser tab — manual placement
            placed.append({"bet_id": bet.bet_id, "status": "manual", "provider_id": pid, "stake": actual_stake})
            remaining -= actual_stake

    # Cleanup (e.g. close Polymarket tabs)
    if page is not None:
        try:
            await workflow.cleanup(page)
        except Exception:
            logger.debug("Workflow cleanup failed", exc_info=True)

    fire_result = {
        "provider_id": pid,
        "placed": placed,
        "failed": failed,
        "excluded": excluded + skipped_balance,
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

    Sets status to ``ready`` or ``complete``.
    Returns the next provider_id, or None if the queue is exhausted.
    """
    if _window is None:
        return None

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
    """Tear down the fire window."""
    global _window
    _window = None


def get_window() -> FireWindow | None:
    """Return the current FireWindow singleton (or None)."""
    return _window
