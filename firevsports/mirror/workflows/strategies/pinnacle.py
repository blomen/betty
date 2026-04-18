"""Pinnacle strategy — full API-based autonomous betting.

Overrides GenericWorkflow methods with Pinnacle-specific API logic:
  - scan(): read-only preview of account state (balance, pending, settled, DB diff)
  - settle_all(): scrape pending bets → record missing → auto-settle → sync balance
  - sync_history(): DOM scrape + API fallback for settled bets
  - place_bet(): market fetch → slippage check → API placement
  - check_live_price(): fetch markets → compute edge vs fair odds
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from . import Strategy
from ..base import HistoryEntry, PlacementResult

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _api_base(intel: dict | None) -> str:
    return (intel or {}).get("api_base", "https://api.arcadia.pinnacle.se/0.1")


def _designation_map(intel: dict | None) -> dict:
    return (intel or {}).get("markets", {}).get("designation_map", {
        "home": "home", "away": "away", "draw": "draw",
        "over": "over", "under": "under",
    })


def _market_key_map(intel: dict | None) -> dict:
    return (intel or {}).get("markets", {}).get("key_map", {
        "moneyline": "s;0;m", "1x2": "s;0;m",
        "spread": "s;0;s", "total": "s;0;ou",
    })


def _date_range(days: int = 30) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    end = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return start, end


def _parse_api_bet(b: dict) -> dict:
    """Parse a single bet object from Pinnacle bets API response."""
    sels = b.get("selections", [])
    sel = sels[0] if sels else {}
    matchup = sel.get("matchup", {})
    parts = matchup.get("participants", [])
    home = next((p["name"] for p in parts if p.get("alignment") == "home"), "")
    away = next((p["name"] for p in parts if p.get("alignment") == "away"), "")
    return {
        "pin_id": b.get("id"),
        "event": f"{home} vs {away}" if home else "?",
        "home": home,
        "away": away,
        "designation": sel.get("designation", ""),
        "market_type": sel.get("market", {}).get("type", ""),
        "matchup_id": matchup.get("id") or sel.get("matchupId"),
        "odds": round(float(b.get("price", 0)), 3),
        "stake": round(float(b.get("stake", b.get("riskAmount", 0))), 2),
        "outcome": b.get("outcome", "none"),
        "placed_at": b.get("createdAt", ""),
        "settled_at": b.get("settledAt", ""),
    }


def _bets_list(data: Any) -> list:
    """Extract bets list from API response (handles list or dict with 'bets' key)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("bets", [])
    return []


def _american_to_decimal(price: float) -> float:
    if price > 0:
        return 1 + price / 100
    return 1 + 100 / abs(price)


async def _evaluate_api(page: "Page", url: str) -> Any:
    try:
        return await page.evaluate(
            f"""async () => {{
                const resp = await fetch("{url}", {{credentials: "include"}});
                if (!resp.ok) return {{ __error: resp.status }};
                return await resp.json();
            }}"""
        )
    except Exception as e:
        logger.warning(f"[pinnacle] API fetch failed: {url} — {e}")
        return None


# ------------------------------------------------------------------
# Login — API ping: /wallet/balance returns amount iff authenticated.
# ------------------------------------------------------------------

async def _check_login(page: "Page", intel: dict | None) -> bool:
    import asyncio

    api = _api_base(intel)
    # Give the page a moment to settle after nav (cookies may not be attached yet)
    await asyncio.sleep(1)
    for attempt in range(3):
        result = await _evaluate_api(page, f"{api}/wallet/balance")
        if result and "__error" not in (result if isinstance(result, dict) else {}):
            if "amount" in result:
                return True
        if attempt < 2:
            await asyncio.sleep(1.5)
    return False


# ------------------------------------------------------------------
# Balance — /wallet/balance
# ------------------------------------------------------------------

async def _sync_balance(page: "Page", intel: dict | None) -> float:
    api = _api_base(intel)
    result = await _evaluate_api(page, f"{api}/wallet/balance")
    if result and isinstance(result, dict) and "amount" in result:
        try:
            return float(result["amount"])
        except (TypeError, ValueError):
            pass
    return -1.0


# ------------------------------------------------------------------
# Scan — read-only preview
# ------------------------------------------------------------------

async def _scan(page: "Page", intel: dict | None) -> dict:
    """Fetch balance, pending bets, settled bets from Pinnacle API. Read-only."""
    try:
        from ....db.models import Bet, Event, get_session
        from ....repositories.profile_repo import ProfileRepo
    except ImportError:
        logger.warning("[pinnacle] DB models not available — scan DB diff disabled")
        Bet = Event = get_session = ProfileRepo = None

    api = _api_base(intel)
    start, end = _date_range()

    # Evaluate API helper (inherits page cookies)
    async def fetch_api(url: str) -> Any:
        try:
            return await page.evaluate(f"""
                async () => {{
                    const resp = await fetch("{url}", {{credentials: "include"}});
                    if (!resp.ok) return {{ __error: resp.status }};
                    return await resp.json();
                }}
            """)
        except Exception as e:
            logger.warning(f"[pinnacle] API fetch failed: {url} — {e}")
            return None

    # Balance
    bal_data = await fetch_api(f"{api}/wallet/balance")
    balance = float(bal_data["amount"]) if bal_data and "amount" in bal_data else -1
    currency = bal_data.get("currency", "?") if bal_data else "?"

    # API pending bets
    unsettled = await fetch_api(f"{api}/bets?status=unsettled&startDate={start}&endDate={end}")
    api_pending = [_parse_api_bet(b) for b in _bets_list(unsettled)
                   if float(b.get("price", 0)) > 0]

    # API settled bets (last 30 days)
    settled = await fetch_api(f"{api}/bets?status=settled&startDate={start}&endDate={end}")
    api_settled = []
    for b in _bets_list(settled):
        p = _parse_api_bet(b)
        if p["outcome"] == "none":
            continue
        payout = p["stake"] * p["odds"] if p["outcome"] == "win" else (0 if p["outcome"] == "loss" else p["stake"])
        p["payout"] = round(payout, 2)
        p["pl"] = round(payout - p["stake"], 2)
        api_settled.append(p)

    # DB pending bets for comparison
    db_pending = []
    try:
        if get_session is None or ProfileRepo is None:
            raise ImportError("DB not available")
        db = get_session()
        profile = ProfileRepo(db).get_active()
        if profile:
            rows = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(Bet.profile_id == profile.id, Bet.provider_id == "pinnacle", Bet.result == "pending")
                .all()
            )
            for bet, event in rows:
                h = (event.display_home or event.home_team or "") if event else ""
                a = (event.display_away or event.away_team or "") if event else ""
                db_pending.append({
                    "bet_id": bet.id, "event": f"{h} vs {a}" if h else bet.event_id,
                    "market": bet.market, "outcome": bet.outcome,
                    "odds": bet.odds, "stake": bet.stake,
                    "placed_at": bet.placed_at.isoformat() if bet.placed_at else "",
                })
        db.close()
    except Exception as e:
        logger.warning(f"[pinnacle] scan DB error: {e}")

    # Unmatched: API bets not in DB
    unmatched = []
    for ap in api_pending:
        if not any(abs(d["odds"] - ap["odds"]) < 0.01 and abs(d["stake"] - ap["stake"]) < 0.01 for d in db_pending):
            unmatched.append(ap)

    # Settleable: settled API bets matching a DB pending bet
    settleable = []
    for s in api_settled:
        for d in db_pending:
            if abs(d["odds"] - s["odds"]) < 0.01 and abs(d["stake"] - s["stake"]) < 0.01:
                settleable.append({**s, "db_bet_id": d["bet_id"], "db_event": d["event"]})
                break

    return {
        "balance": {"amount": balance, "currency": currency},
        "api_pending": api_pending,
        "api_settled_recent": api_settled[:20],
        "db_pending": db_pending,
        "unmatched_in_api": unmatched,
        "settleable": settleable,
        "summary": {
            "api_pending_count": len(api_pending),
            "api_settled_count": len(api_settled),
            "db_pending_count": len(db_pending),
            "unmatched_count": len(unmatched),
            "settleable_count": len(settleable),
        },
    }


# ------------------------------------------------------------------
# Settle all — scrape pending + auto-settle + sync balance
# ------------------------------------------------------------------

async def _settle_all(page: "Page", intel: dict | None) -> dict:
    """Full Pinnacle settlement via API.

    1. Fetch unsettled bets → record any missing in DB
    2. Fetch settled bets → match against pending DB bets → auto-settle
    3. Sync balance
    """
    try:
        from ....db.models import Bet, Event, get_session
        from ....repositories.profile_repo import ProfileRepo
        from ....services.bet_service import BetService
    except ImportError:
        logger.warning("[pinnacle] DB models not available — settle_all requires backend")
        return {"error": "backend DB not available"}

    api = _api_base(intel)
    start, end = _date_range()

    async def fetch_api(url: str) -> Any:
        try:
            return await page.evaluate(f"""
                async () => {{
                    const resp = await fetch("{url}", {{credentials: "include"}});
                    if (!resp.ok) return {{ __error: resp.status }};
                    return await resp.json();
                }}
            """)
        except Exception as e:
            logger.warning(f"[pinnacle] API fetch failed: {url} — {e}")
            return None

    # Step 1: Scrape pending bets
    unsettled = await fetch_api(f"{api}/bets?status=unsettled&startDate={start}&endDate={end}")
    recorded_new = 0

    db = get_session()
    settled_entries = []
    try:
        profile = ProfileRepo(db).get_active()
        if not profile:
            return {"error": "no active profile"}

        svc = BetService(db)

        for b in _bets_list(unsettled):
            p = _parse_api_bet(b)
            if p["stake"] <= 0 or p["odds"] <= 0:
                continue

            # Skip if already in DB
            existing = (
                db.query(Bet)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "pinnacle",
                    Bet.result == "pending",
                    Bet.odds == p["odds"],
                    Bet.stake == p["stake"],
                )
                .first()
            )
            if existing:
                continue

            # Try to find matching event
            event_id = None
            if p["home"] and p["away"]:
                from sqlalchemy import or_
                event = (
                    db.query(Event)
                    .filter(
                        or_(Event.home_team.ilike(f"%{p['home']}%"), Event.display_home.ilike(f"%{p['home']}%")),
                        or_(Event.away_team.ilike(f"%{p['away']}%"), Event.display_away.ilike(f"%{p['away']}%")),
                    )
                    .first()
                )
                if event:
                    event_id = event.id

            svc.create_bet(
                event_id=event_id, provider_id="pinnacle",
                market=p["market_type"], outcome=p["designation"],
                odds=p["odds"], stake=p["stake"], bet_type="mirror",
            )
            recorded_new += 1
            logger.info(f"[pinnacle] Recorded missing bet: {p['event']} {p['designation']} @ {p['odds']} stake={p['stake']}")
        db.commit()

        # Step 2: Fetch settled bets → auto-settle
        settled_data = await fetch_api(f"{api}/bets?status=settled&startDate={start}&endDate={end}")

        pending = (
            db.query(Bet, Event)
            .join(Event, Bet.event_id == Event.id, isouter=True)
            .filter(Bet.profile_id == profile.id, Bet.provider_id == "pinnacle", Bet.result == "pending")
            .all()
        )

        for b in _bets_list(settled_data):
            p = _parse_api_bet(b)
            if p["outcome"] == "none" or p["stake"] <= 0:
                continue

            if p["outcome"] == "win":
                status, payout = "won", p["stake"] * p["odds"]
            elif p["outcome"] == "loss":
                status, payout = "lost", 0.0
            else:
                status, payout = "void", p["stake"]

            # Match by odds + stake
            matched_bet = None
            matched_event = None
            for bet, event in pending:
                if abs(bet.odds - p["odds"]) < 0.01 and abs(bet.stake - p["stake"]) < 0.01:
                    matched_bet, matched_event = bet, event
                    break

            if not matched_bet:
                continue

            event_name = ""
            if matched_event:
                h = matched_event.display_home or matched_event.home_team or ""
                a = matched_event.display_away or matched_event.away_team or ""
                event_name = f"{h} vs {a}" if h and a else h or a

            svc.settle_bet(matched_bet.id, status, round(payout, 2))
            settled_entries.append({
                "bet_id": matched_bet.id, "event": event_name,
                "market": matched_bet.market, "outcome": matched_bet.outcome,
                "odds": matched_bet.odds, "stake": matched_bet.stake,
                "result": status, "payout": round(payout, 2),
                "pl": round(payout - matched_bet.stake, 2),
            })
            pending = [(b, e) for b, e in pending if b.id != matched_bet.id]

            logger.info(f"[pinnacle] Settled bet #{matched_bet.id} {event_name} → {status} (payout={payout:.2f})")

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"[pinnacle] settle_all failed: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        db.close()

    # Step 3: Sync balance
    bal = await fetch_api(f"{api}/wallet/balance")
    new_balance = float(bal["amount"]) if bal and "amount" in bal else -1

    total_staked = sum(s["stake"] for s in settled_entries)
    total_payout = sum(s["payout"] for s in settled_entries)
    wins = [s for s in settled_entries if s["result"] == "won"]
    losses = [s for s in settled_entries if s["result"] == "lost"]

    return {
        "recorded_new": recorded_new,
        "settled": len(settled_entries),
        "settlements": settled_entries,
        "summary": {
            "wins": len(wins), "losses": len(losses),
            "total_staked": round(total_staked, 2),
            "total_payout": round(total_payout, 2),
            "net_pl": round(total_payout - total_staked, 2),
        },
        "new_balance": new_balance,
    }


# ------------------------------------------------------------------
# Sync history — DOM scrape + API fallback
# ------------------------------------------------------------------

async def _sync_history(page: "Page", intel: dict | None) -> list[HistoryEntry]:
    """Scrape settled bets from Pinnacle. DOM first, API fallback."""
    api = _api_base(intel)
    entries: list[HistoryEntry] = []

    # DOM scrape (works when on history page)
    try:
        raw = await page.evaluate("() => document.body.innerText")
    except Exception:
        raw = ""

    if raw:
        flat = raw.replace('\n', ' ').replace('\r', '')
        cards = re.split(r'(?=(?:Settled|R.ttat):\s)', flat)
        for card in cards:
            if 'Stake:' not in card and 'Insats:' not in card:
                continue
            odds_m = re.search(r'@\s*([\d.]+)', card)
            stake_m = re.search(r'(?:Stake|Insats):\s*([\d.,]+)', card)
            if not odds_m or not stake_m:
                continue
            odds = float(odds_m.group(1))
            stake = float(stake_m.group(1).replace(',', '.'))
            upper = card.upper()
            if 'RLUST' in upper or 'LOSS' in upper:
                status = "lost"
            elif 'VOID' in upper or 'CANCEL' in upper or 'OGILTIG' in upper:
                status = "void"
            elif 'SETTLED' in upper or 'TTAT' in upper:
                status = "won"
            else:
                continue
            payout = 0.0 if status == "lost" else (stake * odds if status == "won" else stake)
            bet_id_m = re.search(r'#(\d+)', card)
            event_m = re.search(r'(\w[\w\s.]+?)\s+vs\s+(\w[\w\s.]+?)(?:\s+[A-Z]|\s+@|\s+Bet)', card)
            if stake > 0 and odds > 0:
                entries.append(HistoryEntry(
                    provider_bet_id=bet_id_m.group(1) if bet_id_m else "",
                    event_name=f"{event_m.group(1).strip()} vs {event_m.group(2).strip()}" if event_m else "",
                    market="", outcome="", odds=odds, stake=stake, status=status, payout=payout,
                ))

    logger.info(f"[pinnacle] DOM scrape: {len(entries)} bet(s)")

    # API fallback
    if not entries:
        start, end = _date_range()
        try:
            data = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch("{api}/bets?status=settled&startDate={start}&endDate={end}", {{credentials: "include"}});
                    if (!resp.ok) return {{ __error: resp.status }};
                    return await resp.json();
                }}
            """)
        except Exception:
            data = None

        for b in _bets_list(data):
            p = _parse_api_bet(b)
            if p["outcome"] == "none":
                continue
            if p["outcome"] == "win":
                st, pay = "won", p["stake"] * p["odds"]
            elif p["outcome"] == "loss":
                st, pay = "lost", 0
            else:
                st, pay = "void", p["stake"]
            entries.append(HistoryEntry(
                provider_bet_id=str(p["pin_id"] or ""),
                event_name=p["event"], market=p["market_type"],
                outcome=p["designation"], odds=p["odds"], stake=p["stake"],
                status=st, payout=pay,
            ))

    return entries


# ------------------------------------------------------------------
# Place bet — full API automation
# ------------------------------------------------------------------

async def _place_bet(page: "Page", bet, stake: float, intel: dict | None) -> PlacementResult:
    """Place bet via Pinnacle API: fetch markets → slippage check → place."""
    api = _api_base(intel)
    matchup_id = getattr(bet, "matchup_id", None)
    if not matchup_id:
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason="no_matchup_id")

    outcome = getattr(bet, "outcome", "")
    market = getattr(bet, "market", "")
    point = getattr(bet, "point", None)
    designation = _designation_map(intel).get(outcome)
    if not designation:
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"unknown_outcome:{outcome}")

    # Fetch markets
    try:
        markets = await page.evaluate(f"""
            async () => {{
                const resp = await fetch("{api}/matchups/{matchup_id}/markets/straight", {{credentials: "include"}});
                if (!resp.ok) return {{ __error: resp.status }};
                return await resp.json();
            }}
        """)
    except Exception:
        markets = None

    if not markets or "__error" in (markets if isinstance(markets, dict) else {}):
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason="markets_fetch_failed")

    # Find matching market
    target = _find_market(markets, market, point, intel)
    if not target:
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"market_not_found:{market}")
    if target.get("status") != "open":
        return PlacementResult(status="skipped", bet_id=bet.bet_id, reason=f"market_closed:{target.get('status')}")

    # Find price for our designation
    price_entry = next((p for p in target.get("prices", []) if p.get("designation") == designation), None)
    if not price_entry:
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"designation_not_found:{designation}")

    decimal_odds = _american_to_decimal(price_entry["price"])

    # Slippage check
    threshold = (intel or {}).get("placement", {}).get("slippage_threshold", 0.05)
    if bet.odds > 0 and decimal_odds < bet.odds * (1 - threshold):
        return PlacementResult(
            status="skipped", bet_id=bet.bet_id, actual_odds=round(decimal_odds, 3),
            reason=f"slippage:{decimal_odds:.2f}_vs_{bet.odds:.2f}",
        )

    # Place
    body = {
        "oddsFormat": "decimal",
        "requestId": str(uuid.uuid4()),
        "acceptBetterPrices": True,
        "acceptBetterPrice": True,
        "class": "Straight",
        "selections": [{
            "marketId": target["version"],
            "matchupId": int(matchup_id),
            "marketKey": target["key"],
            "designation": designation,
            "price": round(decimal_odds, 2),
        }],
        "stake": round(stake, 2),
        "originTag": "ps:bsd",
    }

    try:
        body_json = json.dumps(body)
        result = await page.evaluate("""
            async ([url, bodyStr]) => {
                const resp = await fetch(url, {
                    method: "POST", credentials: "include",
                    headers: {"Content-Type": "application/json"},
                    body: bodyStr,
                });
                const data = await resp.json();
                if (!resp.ok) return { __error: resp.status, ...data };
                return data;
            }
        """, [f"{api}/bets/straight", body_json])
    except Exception as e:
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"api_call_failed:{e}")

    if not result or "__error" in result:
        detail = (result or {}).get("detail", (result or {}).get("title", str((result or {}).get("__error", ""))))
        return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"api_error:{detail}")

    confirmed_price = float(result.get("price", decimal_odds))
    confirmed_stake = float(result.get("stake", stake))

    logger.info(
        f"[pinnacle] PLACED bet {result.get('id')}: "
        f"{getattr(bet, 'display_home', '')} vs {getattr(bet, 'display_away', '')} "
        f"{market} {outcome} @ {confirmed_price} stake={confirmed_stake}"
    )
    return PlacementResult(
        status="placed", bet_id=bet.bet_id,
        actual_odds=confirmed_price, actual_stake=confirmed_stake,
        raw_response=result,
    )


# ------------------------------------------------------------------
# Live price
# ------------------------------------------------------------------

async def _check_live_price(page: "Page", bet, intel: dict | None) -> float | None:
    try:
        from ....analysis.value import compute_edge
    except ImportError:
        logger.warning("[pinnacle] analysis.value not available — live price check disabled")
        return None

    api = _api_base(intel)
    matchup_id = getattr(bet, "matchup_id", None)
    fair_odds = getattr(bet, "fair_odds", None)
    if not matchup_id or not fair_odds:
        return None

    try:
        markets = await page.evaluate(f"""
            async () => {{
                const resp = await fetch("{api}/matchups/{matchup_id}/markets/straight", {{credentials: "include"}});
                if (!resp.ok) return null;
                return await resp.json();
            }}
        """)
    except Exception:
        return None

    if not markets or not isinstance(markets, list):
        return None

    market = getattr(bet, "market", "")
    point = getattr(bet, "point", None)
    target = _find_market(markets, market, point, intel)
    if not target:
        return None

    outcome = getattr(bet, "outcome", "")
    designation = _designation_map(intel).get(outcome)
    price_entry = next((p for p in target.get("prices", []) if p.get("designation") == designation), None)
    if not price_entry:
        return None

    decimal_odds = _american_to_decimal(price_entry["price"])
    edge = compute_edge("pinnacle", decimal_odds, fair_odds)
    logger.info(f"[pinnacle] Live: {outcome} @ {decimal_odds:.2f} (fair {fair_odds:.2f}) edge={edge:.1f}%")
    return edge


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _find_market(markets: list[dict], market_type: str, point: float | None, intel: dict | None) -> dict | None:
    key_map = _market_key_map(intel)
    key_prefix = key_map.get(market_type)
    if not key_prefix:
        return None

    for m in markets:
        if m.get("isAlternate"):
            continue
        mk = m.get("key", "")
        if market_type in ("moneyline", "1x2"):
            if mk == key_prefix:
                return m
        elif market_type == "spread":
            if mk.startswith("s;0;s;") and not m.get("isAlternate"):
                if point is not None:
                    try:
                        if abs(float(mk.split(";")[-1]) - point) < 0.01:
                            return m
                    except ValueError:
                        pass
                else:
                    return m
        elif market_type == "total":
            if mk.startswith("s;0;ou;") and not m.get("isAlternate"):
                if point is not None:
                    try:
                        if abs(float(mk.split(";")[-1]) - point) < 0.01:
                            return m
                    except ValueError:
                        pass
                else:
                    return m
    return None


# ------------------------------------------------------------------
# Strategy export
# ------------------------------------------------------------------

async def _navigate_to_event(page: "Page", bet, intel: dict | None) -> bool:
    """Navigate to Pinnacle event page by matchup ID."""
    matchup_id = getattr(bet, "matchup_id", None)
    if not matchup_id:
        return False

    url = f"https://www.pinnacle.se/sv/matchup/{matchup_id}"
    current = page.url or ""
    if str(matchup_id) in current:
        return True  # Already on this event

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        logger.info(f"[pinnacle] Navigated to matchup {matchup_id}")
        return True
    except Exception as e:
        logger.warning(f"[pinnacle] Navigate failed: {e}")
        return False


strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    place_bet=_place_bet,
    check_live_price=_check_live_price,
    navigate_to_event=_navigate_to_event,
)
