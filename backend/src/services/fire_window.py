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
    available_providers: list[str] | None = None  # Cluster: all providers with this bet


# Altenar providers share identical odds — treat as one cluster
ALTENAR_CLUSTER = frozenset({"betinia", "campobet", "quickcasino", "swiper", "lodur", "dbet"})
ALTENAR_CLUSTER_ID = "altenar"


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


def _merge_altenar_cluster(provider_bets: dict[str, list[FireWindowBet]]) -> None:
    """Merge all Altenar providers into one cluster with deduplicated bets.

    Since all Altenar providers share identical odds, we keep one copy per
    event+market+outcome (highest edge) and track which providers have it.
    """
    altenar_pids = [pid for pid in list(provider_bets.keys()) if pid in ALTENAR_CLUSTER]
    if len(altenar_pids) < 2:
        # Single or no Altenar provider — no merging needed
        if len(altenar_pids) == 1:
            pid = altenar_pids[0]
            for bet in provider_bets[pid]:
                bet.available_providers = [pid]
        return

    # Collect all bets, deduplicate by event+market+outcome+point
    merged: dict[str, FireWindowBet] = {}  # key → best bet
    for pid in altenar_pids:
        for bet in provider_bets[pid]:
            point_key = f"{bet.point}" if bet.point is not None else ""
            key = f"{bet.event_id}:{bet.market}:{bet.outcome}:{point_key}"
            if key not in merged:
                bet.available_providers = [pid]
                bet.provider_id = ALTENAR_CLUSTER_ID
                merged[key] = bet
            else:
                existing = merged[key]
                if pid not in existing.available_providers:
                    existing.available_providers.append(pid)
                # Keep the one with higher edge (should be identical, but just in case)
                if bet.edge_pct > existing.edge_pct:
                    bet.available_providers = existing.available_providers
                    bet.provider_id = ALTENAR_CLUSTER_ID
                    merged[key] = bet

    # Remove individual Altenar providers, add cluster
    for pid in altenar_pids:
        del provider_bets[pid]
    provider_bets[ALTENAR_CLUSTER_ID] = list(merged.values())
    logger.info(
        f"[FireWindow] Merged {len(altenar_pids)} Altenar providers → "
        f"{len(merged)} unique bets (providers: {', '.join(altenar_pids)})"
    )


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
            stake=(rounded_stake := _round_stake(pid, b.get("stake", 0.0))),
            expected_profit=round(rounded_stake * b.get("edge_pct", 0.0) / 100, 2),
            display_home=b.get("display_home", ""),
            display_away=b.get("display_away", ""),
            sport=b.get("sport", ""),
            tier=b.get("tier", "soft"),
            start_time=b.get("start_time"),
            original_outcome=b.get("original_outcome"),
        )
        provider_bets.setdefault(pid, []).append(bet)

    # Merge Altenar cluster: deduplicate bets, track available providers
    _merge_altenar_cluster(provider_bets)

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


_tabs_opened = False  # Guard against double-call from React strict mode


async def open_needed_tabs(mirror_service) -> dict:
    """Open tabs for providers that have balance OR unsettled expired bets.

    This runs once when the fire window opens. Providers get a tab if:
    - They're in the fire window queue AND have balance >= min_bet, OR
    - They have pending bets where start_time has passed (need settlement)
    """
    global _tabs_opened
    if _tabs_opened:
        return {"opened": [], "settle_needed": [], "count": 0, "skipped": "already_opened"}
    from ..db.models import Bet, Event
    from ..repositories.profile_repo import ProfileRepo
    from ..config.loader import load_config

    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None
    if not context:
        return {"error": "no_browser_context"}

    cfg = load_config()
    now = datetime.now(timezone.utc)

    # Get balances
    db = get_session()
    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()
        balances = repo.get_all_balances(profile.id) if profile else {}

        # Find providers with expired pending bets
        pending = db.query(Bet).filter(
            Bet.profile_id == profile.id, Bet.result == "pending"
        ).all() if profile else []

        providers_needing_settle: set[str] = set()
        for bet in pending:
            start = getattr(bet, 'start_time', None)
            if not start:
                event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                start = getattr(event, 'start_time', None) if event else None
            if start:
                # Normalize timezone — DB may store naive datetimes
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if start < now:
                    providers_needing_settle.add(bet.provider_id)
    finally:
        db.close()

    # Providers with ANY pending bets (need settlement check regardless of start_time)
    providers_with_pending = {bet.provider_id for bet in pending}

    # Determine which providers need a tab:
    # 1. Has balance + in queue (can place bets)
    # 2. Has expired pending bets (need settlement)
    # 3. Has ANY pending bets (need settlement check — FIRST PRIORITY)
    queue_pids = set(_window.provider_queue) if _window else set()
    # Expand cluster ID to individual members for tab opening
    expanded_queue = set()
    for qpid in queue_pids:
        if qpid == ALTENAR_CLUSTER_ID:
            expanded_queue.update(ALTENAR_CLUSTER)
        else:
            expanded_queue.add(qpid)
    providers_with_balance = {pid for pid, bal in balances.items() if bal >= 10 and pid in expanded_queue}
    all_needing_tab = providers_with_balance | providers_needing_settle | providers_with_pending

    logger.info(
        f"[FireWindow] Tabs needed: {len(all_needing_tab)} "
        f"(balance: {providers_with_balance}, settle: {providers_needing_settle})"
    )

    # Open tabs — reuse blank first, then open new
    opened = []
    settle_needed = []
    for pid in all_needing_tab:
        workflow = get_workflow(pid)
        # Already has a tab?
        existing = await workflow.find_tab(context)
        if existing:
            opened.append(pid)
            if pid in providers_needing_settle:
                settle_needed.append(pid)
            continue

        # Build URL
        if workflow.domain:
            url = f"https://www.{workflow.domain}"
        else:
            pconfig = cfg.get_provider(pid)
            url = pconfig.site_url or (f"https://www.{pconfig.domain}" if pconfig and pconfig.domain else None)
        if not url:
            continue

        # Reuse blank tab for first provider, new tabs for rest
        blank = next((p for p in context.pages if (p.url or "").startswith("about:")), None)
        try:
            if blank:
                await blank.goto(url, wait_until="domcontentloaded", timeout=15000)
                opened.append(pid)
            else:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                opened.append(pid)
            if pid in providers_needing_settle:
                settle_needed.append(pid)
            logger.info(f"[FireWindow] Opened tab: {pid} → {url}")
        except Exception as e:
            logger.warning(f"[FireWindow] Failed to open tab for {pid}: {e}")

    _tabs_opened = True
    return {
        "opened": opened,
        "settle_needed": settle_needed,
        "count": len(opened),
    }


async def activate_provider_workflow(provider_id: str, mirror_service) -> dict:
    """Run workflow setup when a provider is activated.

    Tab was already opened by open_needed_tabs. This just:
    1. Finds the existing tab
    2. Settles expired bets across ALL providers
    3. Reads balance from DB

    For Altenar cluster: syncs history + balance for all member providers with tabs.
    """
    result = {"provider_id": provider_id, "steps": {}}

    context = getattr(mirror_service, 'interceptor', None) if mirror_service else None
    context = getattr(context, 'context', None) if context else None

    # Settle expired bets across ALL providers
    try:
        settled_global = _settle_expired_bets()
        result["steps"]["settle_expired"] = settled_global
    except Exception as e:
        result["steps"]["settle_expired"] = f"error:{e}"

    # Altenar cluster: sync history+balance for all members with open tabs
    if provider_id == ALTENAR_CLUSTER_ID:
        if context:
            synced = {}
            for cpid in ALTENAR_CLUSTER:
                wf = get_workflow(cpid)
                page = await wf.find_tab(context)
                if not page:
                    continue
                try:
                    await wf.sync_history(page)
                    synced[cpid] = "scanned"
                except Exception as e:
                    synced[cpid] = f"error:{e}"
            result["steps"]["cluster_sync"] = synced
        # Read cluster balances
        try:
            from ..repositories.profile_repo import ProfileRepo
            db = get_session()
            try:
                repo = ProfileRepo(db)
                profile = repo.get_active()
                if profile:
                    cluster_bal = {}
                    for cpid in ALTENAR_CLUSTER:
                        bal = repo.get_balance(profile.id, cpid)
                        if bal > 0:
                            cluster_bal[cpid] = round(bal, 2)
                    result["steps"]["balance"] = round(sum(cluster_bal.values()), 2)
                    result["steps"]["cluster_balances"] = cluster_bal
            finally:
                db.close()
        except Exception as e:
            result["steps"]["balance"] = f"error:{e}"
        return result

    workflow = get_workflow(provider_id)

    # Find existing tab (opened by open_needed_tabs)
    if context:
        page = await workflow.find_tab(context)
        result["steps"]["tab"] = "found" if page else "not_found"
    else:
        page = None
        result["steps"]["tab"] = "no_browser_context"

    # Provider-specific settlement
    if context and page:
        if provider_id == "polymarket":
            try:
                from ..mirror.workflows.polymarket import PolymarketWorkflow
                poly_wf = workflow if isinstance(workflow, PolymarketWorkflow) else PolymarketWorkflow(
                    provider_id="polymarket", domain="polymarket.com",
                )
                settle_result = await poly_wf.settle_all(page)
                result["steps"]["settle"] = {
                    "settled": settle_result.get("settled", 0),
                    "claimed": settle_result.get("claim", {}).get("claimed", False),
                    "redeemed": settle_result.get("redeem", {}).get("redeemed", 0),
                    "net_pl": settle_result.get("summary", {}).get("net_pl", 0),
                }
                result["steps"]["balance"] = settle_result.get("new_balance", -1)
                try:
                    history_entries = await poly_wf.sync_history(page)
                    result["steps"]["history_sync"] = {
                        "settled_from_history": len(history_entries),
                    }
                except Exception as he:
                    logger.warning(f"[FireWindow] Polymarket sync_history failed: {he}")
                    result["steps"]["history_sync"] = f"error:{he}"
            except Exception as e:
                logger.warning(f"[FireWindow] Polymarket settle_all failed: {e}", exc_info=True)
                result["steps"]["settle"] = f"error:{e}"
        elif provider_id == "pinnacle":
            logger.info(f"[FireWindow] >>> Pinnacle settle: page={'found' if page else 'NONE'}, workflow={type(workflow).__name__}, has_strategy={hasattr(workflow, 'strategy') and workflow.strategy is not None}")
            try:
                settle_result = await workflow.settle_all(page)
                logger.info(f"[FireWindow] >>> Pinnacle settle_all done: {settle_result}")
                result["steps"]["settle"] = {
                    "recorded_new": settle_result.get("recorded_new", 0),
                    "settled": settle_result.get("settled", 0),
                    "net_pl": settle_result.get("summary", {}).get("net_pl", 0),
                }
                if settle_result.get("new_balance", -1) >= 0:
                    result["steps"]["balance"] = settle_result["new_balance"]
            except Exception as e:
                logger.warning(f"[FireWindow] >>> Pinnacle settle_all FAILED: {e}", exc_info=True)
                result["steps"]["settle"] = f"error:{e}"
        else:
            try:
                await workflow.sync_history(page)
                result["steps"]["settle_history"] = "scanned"
            except Exception as e:
                result["steps"]["settle_history"] = f"error:{e}"

    # Read balance from DB (interceptor syncs it on page load)
    try:
        from ..repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            repo = ProfileRepo(db)
            profile = repo.get_active()
            if profile:
                balance = repo.get_balance(profile.id, provider_id)
                result["steps"]["balance"] = round(balance, 2)
        finally:
            db.close()
    except Exception as e:
        result["steps"]["balance"] = f"error:{e}"

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
    """Void pending bets where event start_time has passed.

    These are ghost bets — recorded in DB but never actually placed,
    or already settled outside our system. Safe to void since the event
    has already started/ended.

    Returns {provider_id: {"expired": N, "voided": N}}.
    """
    from ..db.models import Bet, Event
    from .bet_service import BetService
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    result = {}
    db = get_session()
    try:
        svc = BetService(db)

        pending = (
            db.query(Bet)
            .filter(Bet.result == "pending")
            .all()
        )

        expired_by_provider: dict[str, list] = {}
        for bet in pending:
            start = bet.start_time if hasattr(bet, 'start_time') and bet.start_time else None
            if not start:
                event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                start = event.start_time if event and hasattr(event, 'start_time') else None

            if start:
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if start < now:
                    expired_by_provider.setdefault(bet.provider_id, []).append(bet)

        voided_ids: set[int] = set()
        for pid, bets in expired_by_provider.items():
            voided = 0
            for bet in bets:
                try:
                    svc.settle_bet(bet.id, "void", 0.0)
                    voided += 1
                    voided_ids.add(bet.id)
                    logger.info(
                        f"[FireWindow] Voided expired ghost bet #{bet.id} "
                        f"{pid} (stake={bet.stake})"
                    )
                except Exception as e:
                    logger.warning(f"[FireWindow] Failed to void bet #{bet.id}: {e}")
            result[pid] = {"expired": len(bets), "voided": voided}

        db.commit()

        # Remove voided bets from in-memory fire window
        if voided_ids and _window is not None:
            for pid in list(_window.provider_bets.keys()):
                before = len(_window.provider_bets[pid])
                _window.provider_bets[pid] = [
                    b for b in _window.provider_bets[pid]
                    if b.bet_id not in voided_ids
                ]
                removed = before - len(_window.provider_bets[pid])
                if removed:
                    logger.info(f"[FireWindow] Removed {removed} voided bets from {pid} queue")

    except Exception as e:
        db.rollback()
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
            "available_providers": bet.available_providers,
        })

    # Fetch current balance — for clusters, sum all member balances
    is_cluster = pid == ALTENAR_CLUSTER_ID
    balance = None
    cluster_balances = None
    try:
        from ..repositories.profile_repo import ProfileRepo
        db = get_session()
        try:
            profile_repo = ProfileRepo(db)
            profile = profile_repo.get_active()
            if profile:
                if is_cluster:
                    cluster_balances = {}
                    for cpid in ALTENAR_CLUSTER:
                        bal = profile_repo.get_balance(profile.id, cpid)
                        if bal > 0:
                            cluster_balances[cpid] = round(bal, 2)
                    balance = sum(cluster_balances.values())
                else:
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
        **({"cluster_balances": cluster_balances} if cluster_balances else {}),
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

def _get_cluster_balances(cluster_providers: list[str]) -> dict[str, float]:
    """Get balances for all providers in a cluster. Returns {pid: balance}."""
    from ..repositories.profile_repo import ProfileRepo
    balances = {}
    db = get_session()
    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()
        if profile:
            for cpid in cluster_providers:
                balances[cpid] = repo.get_balance(profile.id, cpid)
    finally:
        db.close()
    return balances


def _get_already_placed(provider_ids: list[str]) -> set[str]:
    """Get dedup keys for already-placed pending bets.

    Returns both event:market:outcome keys AND slug: keys so that
    imported positions (event_id=None) match fire window bets by slug.
    """
    from ..db.models import Bet
    already_placed: set[str] = set()
    db = get_session()
    try:
        rows = db.query(Bet.event_id, Bet.market, Bet.outcome, Bet.confirmation_id).filter(
            Bet.provider_id.in_(provider_ids),
            Bet.result == "pending",
        ).all()
        for row in rows:
            already_placed.add(f"{row[0]}:{row[1]}:{row[2]}")
            if row[3]:
                already_placed.add(f"slug:{row[3]}")
    finally:
        db.close()
    return already_placed


def _pick_best_provider(available: list[str], balances: dict[str, float], stake: float) -> tuple[str, float] | None:
    """Pick the provider with highest balance that can cover the stake."""
    candidates = [(p, balances.get(p, 0)) for p in available if balances.get(p, 0) >= _min_bet(p)]
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[1])
    return candidates[0]


def get_next_bet() -> dict:
    """Get the next unfired bet for the current provider, sorted by edge desc.

    For cluster providers (Altenar), picks the provider with highest balance.
    Returns bet details + position info, or {"done": True} if no more bets.
    """
    if _window is None:
        return {"error": "no fire window open"}
    pid = _window.current_provider
    if pid is None:
        return {"error": "no active provider"}

    bets = _window.provider_bets.get(pid, [])
    fired_ids = _window.fired_results.get(f"{pid}_bet_ids", set())

    is_cluster = pid == ALTENAR_CLUSTER_ID
    # For clusters, check already-placed across all member providers
    if is_cluster:
        cluster_providers = list(ALTENAR_CLUSTER)
    else:
        cluster_providers = [pid]

    try:
        already_placed = _get_already_placed(cluster_providers)
        print(f"[FireWindow] Already placed: {len(already_placed)} bets for {pid}")
    except Exception as e:
        print(f"[FireWindow] DB check failed: {e}")
        already_placed = set()

    # For clusters, get all balances upfront
    if is_cluster:
        try:
            balances = _get_cluster_balances(cluster_providers)
        except Exception:
            balances = {}
    else:
        balances = {}

    # Sort by edge desc, find first unfired
    for bet in sorted(bets, key=lambda b: -b.edge_pct):
        if bet.bet_id in fired_ids:
            continue
        if bet.edge_pct <= 0:
            continue
        bet_key = f"{bet.event_id}:{bet.market}:{bet.outcome}"
        if bet_key in already_placed:
            continue
        # Also check by slug (imported positions have event_id=None)
        if bet.market_slug and f"slug:{bet.market_slug}" in already_placed:
            continue

        # Pick provider and balance
        if is_cluster and bet.available_providers:
            pick = _pick_best_provider(bet.available_providers, balances, bet.stake)
            if not pick:
                continue
            actual_pid, balance = pick
        else:
            actual_pid = pid
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

        if balance < _min_bet(actual_pid):
            continue

        actual_stake = _round_stake(actual_pid, min(bet.stake, balance))
        actual_profit = actual_stake * (bet.edge_pct / 100)

        cents = round((1 / bet.odds) * 100) if bet.odds > 1 else 0
        fair_cents = round((1 / bet.fair_odds) * 100) if bet.fair_odds > 1 else 0

        remaining = len([b for b in bets if b.bet_id not in fired_ids and b.edge_pct > 0
                         and f"{b.event_id}:{b.market}:{b.outcome}" not in already_placed])

        return {
            "bet_id": bet.bet_id,
            "provider_id": actual_pid,
            "cluster_id": pid if is_cluster else None,
            "available_providers": bet.available_providers,
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
        # For clusters, use any member provider's workflow (same odds engine)
        check_pid = pid
        if pid == ALTENAR_CLUSTER_ID and bet.available_providers:
            check_pid = bet.available_providers[0]
        workflow = get_workflow(check_pid)
        context = getattr(mirror_service, 'interceptor', None)
        context = getattr(context, 'context', None) if context else None
        if context:
            page = await workflow.find_tab(context)
            if page:
                await workflow.navigate_to_event(page, bet)
                live_edge = await workflow.check_live_price(page, bet)
                # Get live price from prepare result if available
                prep = getattr(workflow, '_last_prepare', None)
                if prep and prep.get("live_price"):
                    live_cents = round(prep["live_price"] * 100)

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


async def place_bet(bet_id: int, mirror_service, target_provider: str | None = None) -> dict:
    """Place a single confirmed bet, record to DB, sync balance.

    For cluster bets, target_provider specifies which provider to use.
    Falls back to picking the provider with highest balance.
    """
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

    # Resolve actual provider for clusters
    is_cluster = pid == ALTENAR_CLUSTER_ID
    if is_cluster:
        if target_provider and target_provider in (bet.available_providers or []):
            actual_pid = target_provider
        else:
            balances = _get_cluster_balances(bet.available_providers or list(ALTENAR_CLUSTER))
            pick = _pick_best_provider(bet.available_providers or [], balances, bet.stake)
            if not pick:
                return {"status": "skipped", "bet_id": bet_id, "reason": "no_cluster_balance"}
            actual_pid = pick[0]
    else:
        actual_pid = pid

    # Adjust stake to available balance
    balance = float('inf')
    try:
        from ..repositories.profile_repo import ProfileRepo
        _db = get_session()
        try:
            _repo = ProfileRepo(_db)
            _profile = _repo.get_active()
            if _profile:
                balance = _repo.get_balance(_profile.id, actual_pid)
        finally:
            _db.close()
    except Exception:
        pass

    actual_stake = _round_stake(actual_pid, min(bet.stake, balance))
    if actual_stake < _min_bet(actual_pid):
        return {"status": "skipped", "bet_id": bet_id, "reason": "insufficient_balance"}

    label = f"*{bet.display_home} vs {bet.display_away}*{bet.market}*{bet.outcome}*"

    workflow = get_workflow(actual_pid)
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

    if result.status in ("placed", "manual"):
        _record_bet(bet, actual_pid, result.raw_response or {}, actual_stake)
        _sync_balance_after_bet(bet, actual_pid)
        print(f"  {label}{result.status.upper()} @ {actual_pid}*")
    else:
        print(f"  {label}{result.status.upper()} {result.reason or ''}*")

    return {
        "status": result.status,
        "bet_id": bet_id,
        "provider_id": actual_pid,
        "cluster_id": pid if is_cluster else None,
        "stake": actual_stake,
        "reason": result.reason,
        **({"actual_odds": result.actual_odds} if result.actual_odds else {}),
    }


def _record_bet(bet: FireWindowBet, provider_id: str, result: dict, actual_stake: float | None = None) -> None:
    """Record placed bet to the database."""
    from .bet_service import BetService
    from ..db.models import Bet
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
            start_time_str=bet.start_time,
        )
        if "error" in resp:
            logger.warning("[FireWindow] Bet recording failed: %s", resp["error"])
        else:
            bet_id = resp.get("id")
            logger.info("[FireWindow] Bet recorded: id=%s", bet_id)
            # Save market_slug as confirmation_id for Polymarket settlement
            if bet.market_slug and bet_id:
                db_bet = db.query(Bet).filter(Bet.id == bet_id).first()
                if db_bet:
                    db_bet.confirmation_id = bet.market_slug
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
    global _window, _tabs_opened
    _window = None
    _tabs_opened = False


def get_window() -> FireWindow | None:
    """Return the current FireWindow singleton (or None)."""
    return _window


def _match_polymarket_position(bet, event, positions: list[dict]) -> dict | None:
    """Match a pending Polymarket bet against scraped portfolio positions.

    Uses team names from the event to find the matching position.
    """
    if not event:
        return None

    # Build search keywords from event
    keywords = []
    if event.home_team:
        # Use first significant word (>3 chars) from team name
        for word in event.home_team.split():
            if len(word) > 3:
                keywords.append(word.lower())
                break
    if event.away_team:
        for word in event.away_team.split():
            if len(word) > 3:
                keywords.append(word.lower())
                break

    if not keywords:
        return None

    # Deduplicate positions (scraper returns each position twice — collapsed + expanded)
    seen_markets = set()
    for pos in positions:
        full_text = (pos.get("full_text") or "").lower()
        market = pos.get("market", "")

        # Skip duplicates
        dedup_key = f"{market}:{pos.get('has_redeem')}:{pos.get('has_sell')}"
        if dedup_key in seen_markets:
            continue
        seen_markets.add(dedup_key)

        # Check if all keywords appear in the position text
        if all(kw in full_text for kw in keywords):
            return pos

    return None


# ---------------------------------------------------------------------------
# Settlement — check and apply across all providers
# ---------------------------------------------------------------------------

# Staged settlements awaiting user confirmation
_pending_settlements: list[dict] = []


async def check_settlements(mirror_service) -> dict:
    """Check ALL providers for pending bets that need settlement.

    Returns a breakdown with each bet's outcome and P&L for user review.
    """
    global _pending_settlements
    from ..db.models import Bet, Event
    from ..repositories.profile_repo import ProfileRepo

    db = get_session()
    staged: list[dict] = []
    now = datetime.now(timezone.utc)

    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()
        if not profile:
            return {"error": "no active profile"}

        pending = (
            db.query(Bet)
            .filter(Bet.profile_id == profile.id, Bet.result == "pending")
            .all()
        )

        if not pending:
            return {"settlements": [], "summary": {"total": 0}}

        # Group by provider
        by_provider: dict[str, list] = {}
        for bet in pending:
            by_provider.setdefault(bet.provider_id, []).append(bet)

        # Check Polymarket via DOM portfolio scrape
        if "polymarket" in by_provider and mirror_service:
            try:
                from ..mirror.workflows.polymarket import PolymarketWorkflow
                poly_wf = PolymarketWorkflow(provider_id="polymarket", domain="polymarket.com")
                context = getattr(mirror_service, 'interceptor', None)
                context = getattr(context, 'context', None) if context else None
                if context:
                    poly_page = await poly_wf.find_tab(context)
                    if poly_page:
                        positions = await poly_wf.scrape_portfolio(poly_page)
                        # Match positions against pending bets
                        for bet in by_provider["polymarket"]:
                            event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                            event_name = f"{event.home_team} vs {event.away_team}" if event and event.home_team else bet.event_id or ""
                            # Find matching position by checking if event keywords appear in full_text
                            matched_pos = _match_polymarket_position(bet, event, positions)
                            if matched_pos:
                                status = matched_pos["status"]
                                if "Won" in matched_pos.get("full_text", "") or "WON" in matched_pos.get("full_text", ""):
                                    status = "won"
                                elif "Lost" in matched_pos.get("full_text", "") or "LOST" in matched_pos.get("full_text", ""):
                                    status = "lost"
                                if status in ("won", "lost"):
                                    vals = matched_pos.get("values", [])
                                    payout = vals[0] if status == "won" and vals else 0
                                    staged.append({
                                        "bet_id": bet.id,
                                        "provider": "polymarket",
                                        "event": event_name,
                                        "market": bet.market,
                                        "outcome": bet.outcome,
                                        "odds": bet.odds,
                                        "stake": bet.stake,
                                        "result": status,
                                        "payout": round(payout, 2),
                                        "pl": round(payout - bet.stake, 2),
                                        "has_redeem": matched_pos.get("has_redeem", False),
                                    })
            except Exception as e:
                logger.warning(f"[settle] Polymarket settlement check failed: {e}", exc_info=True)

        # Check Pinnacle via bet history API
        if "pinnacle" in by_provider and mirror_service:
            try:
                workflow = get_workflow("pinnacle")
                context = getattr(mirror_service, 'interceptor', None)
                context = getattr(context, 'context', None) if context else None
                if context:
                    page = await workflow.find_tab(context)
                    if page:
                        history = await workflow.sync_history(page)
                        for entry in history:
                            if entry.status in ("won", "lost", "void"):
                                # Find matching pending bet
                                for bet in by_provider.get("pinnacle", []):
                                    if bet.odds == entry.odds and bet.stake == entry.stake and bet.result == "pending":
                                        payout = entry.payout or 0
                                        event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                                        event_name = f"{event.home_team} vs {event.away_team}" if event else bet.event_id
                                        staged.append({
                                            "bet_id": bet.id,
                                            "provider": "pinnacle",
                                            "event": event_name,
                                            "market": bet.market,
                                            "outcome": bet.outcome,
                                            "odds": bet.odds,
                                            "stake": bet.stake,
                                            "result": entry.status,
                                            "payout": round(payout, 2),
                                            "pl": round(payout - bet.stake, 2),
                                        })
                                        break
            except Exception as e:
                logger.warning(f"[settle] Pinnacle settlement check failed: {e}")

        # For other providers: flag expired events (start_time passed) as needing manual check
        for pid, bets in by_provider.items():
            if pid in ("polymarket", "pinnacle"):
                continue  # Already handled above
            for bet in bets:
                start = getattr(bet, 'start_time', None)
                if not start:
                    event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                    start = getattr(event, 'start_time', None) if event else None
                if start:
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                if start and start < now:
                    event = db.query(Event).filter(Event.id == bet.event_id).first() if bet.event_id else None
                    event_name = f"{event.home_team} vs {event.away_team}" if event and event.home_team else bet.event_id
                    staged.append({
                        "bet_id": bet.id,
                        "provider": pid,
                        "event": event_name,
                        "market": bet.market,
                        "outcome": bet.outcome,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": "expired",  # Needs manual check
                        "payout": None,
                        "pl": None,
                    })

    except Exception as e:
        logger.error(f"[settle] check_settlements failed: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        db.close()

    _pending_settlements = staged

    # Build summary
    wins = [s for s in staged if s["result"] == "won"]
    losses = [s for s in staged if s["result"] == "lost"]
    expired = [s for s in staged if s["result"] == "expired"]
    total_staked = sum(s["stake"] for s in staged if s["result"] in ("won", "lost"))
    total_payout = sum(s.get("payout") or 0 for s in staged if s["result"] in ("won", "lost"))

    return {
        "settlements": staged,
        "summary": {
            "total": len(staged),
            "wins": len(wins),
            "losses": len(losses),
            "expired_needs_check": len(expired),
            "total_staked": round(total_staked, 2),
            "total_payout": round(total_payout, 2),
            "net_pl": round(total_payout - total_staked, 2),
        },
    }


def apply_settlements() -> dict:
    """Apply staged settlements — update bet results in DB and sync balances."""
    global _pending_settlements

    if not _pending_settlements:
        return {"error": "no pending settlements"}

    from ..db.models import Bet
    from ..repositories.profile_repo import ProfileRepo

    db = get_session()
    applied = 0
    try:
        repo = ProfileRepo(db)
        profile = repo.get_active()

        for s in _pending_settlements:
            if s["result"] in ("won", "lost", "void"):
                bet = db.query(Bet).filter(Bet.id == s["bet_id"]).first()
                if bet and bet.result == "pending":
                    bet.result = s["result"]
                    if s.get("payout") is not None:
                        bet.payout = s["payout"]
                    applied += 1
                    logger.info(f"[settle] Applied: #{s['bet_id']} {s['provider']} → {s['result']} (P&L: {s.get('pl')})")

                    # Sync balance: add payout back
                    if s["result"] == "won" and s.get("payout") and profile:
                        cur = repo.get_balance(profile.id, s["provider"])
                        repo.set_balance(profile.id, s["provider"], cur + s["payout"])

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[settle] apply_settlements failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()

    result = {"applied": applied, "total": len(_pending_settlements)}
    _pending_settlements = []
    return result
