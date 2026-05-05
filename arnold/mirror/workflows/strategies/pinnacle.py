"""Pinnacle strategy — full API-based autonomous betting.

Overrides GenericWorkflow methods with Pinnacle-specific API logic:
  - scan(): read-only preview of account state (balance, pending, settled, DB diff)
  - settle_all(): scrape pending bets → record missing → auto-settle → sync balance
  - sync_history(): DOM scrape + API fallback for settled bets
  - place_bet(): market fetch → slippage check → API placement
  - check_live_price(): fetch markets → compute edge vs fair odds
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..base import HistoryEntry, PlacementResult
from . import Strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _api_base(intel: dict | None) -> str:
    return (intel or {}).get("api_base", "https://api.arcadia.pinnacle.se/0.1")


def _designation_map(intel: dict | None) -> dict:
    return (
        (intel or {})
        .get("markets", {})
        .get(
            "designation_map",
            {
                "home": "home",
                "away": "away",
                "draw": "draw",
                "over": "over",
                "under": "under",
            },
        )
    )


def _market_key_map(intel: dict | None) -> dict:
    return (
        (intel or {})
        .get("markets", {})
        .get(
            "key_map",
            {
                "moneyline": "s;0;m",
                "1x2": "s;0;m",
                "spread": "s;0;s",
                "total": "s;0;ou",
            },
        )
    )


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


# ------------------------------------------------------------------
# API helpers — Playwright request context bypasses browser CORS.
# ------------------------------------------------------------------

_PINNACLE_HEADERS = {
    "Origin": "https://www.pinnacle.se",
    "Referer": "https://www.pinnacle.se/",
    "Accept": "application/json",
    # Public web-SDK key — constant across sessions (captured from intercepted XHR).
    "X-Api-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R",
}

# ------------------------------------------------------------------
# DOM-click constants — Pinnacle event page market-btn layout.
# ------------------------------------------------------------------

# Market label text (lower-cased) → canonical market type.
_MARKET_LABEL_MAP = {
    "money line": "moneyline",
    "moneyline": "moneyline",
    "1x2": "1x2",
    "spread": "spread",
    "handicap": "spread",
    "total": "total",
    "total points": "total",
    "over/under": "total",
}

# Visual button order within a market section → outcome.
# Pinnacle renders home → (draw) → away. Totals: over → under.
_OUTCOME_POSITION: dict[str, dict[str, int]] = {
    "1x2": {"home": 0, "draw": 1, "away": 2},
    "moneyline": {"home": 0, "away": 1},
    "spread": {"home": 0, "away": 1},
    "total": {"over": 0, "under": 1},
}


async def _build_headers(page: Page) -> dict:
    """Harvest X-Device-UUID + X-Session from localStorage['Main:User']. Pinnacle sets
    these at login; every authenticated XHR needs them alongside cookies and the
    static X-Api-Key."""
    headers = dict(_PINNACLE_HEADERS)
    try:
        main_user = await page.evaluate(
            r"""() => { try { const r = localStorage.getItem('Main:User'); return r ? JSON.parse(r) : null; } catch { return null; } }"""
        )
        if isinstance(main_user, dict):
            uuid_val = main_user.get("uuid") or main_user.get("deviceId")
            token = main_user.get("token")
            if uuid_val:
                headers["X-Device-UUID"] = str(uuid_val)
            if token:
                headers["X-Session"] = str(token)
    except Exception as e:
        logger.warning(f"[pinnacle] harvest Main:User failed: {e}")
    try:
        cookies = await page.context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "pinnacle" in c.get("domain", ""))
        if cookie_str:
            headers["Cookie"] = cookie_str
    except Exception:
        pass
    return headers


async def _evaluate_api(page: Page, url: str) -> Any:
    try:
        headers = await _build_headers(page)
        resp = await page.context.request.get(url, headers=headers)
        if resp.status < 200 or resp.status >= 400:
            return {"__error": resp.status}
        return await resp.json()
    except Exception as e:
        logger.warning(f"[pinnacle] API fetch failed: {url} — {e}")
        return None


async def _post_api(page: Page, url: str, body: dict) -> dict | None:
    try:
        headers = await _build_headers(page)
        headers["Content-Type"] = "application/json"
        resp = await page.context.request.post(url, data=body, headers=headers)
        data = None
        try:
            data = await resp.json()
        except Exception:
            data = {}
        if resp.status < 200 or resp.status >= 400:
            return {"__error": resp.status, **(data or {})}
        return data
    except Exception as e:
        logger.warning(f"[pinnacle] API POST failed: {url} — {e}")
        return None


# ------------------------------------------------------------------
# Login + balance — DOM scrape (Pinnacle's /wallet/balance requires a JWT
# fingerprint that Playwright's request context can't replicate).
# ------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """True when Pinnacle session is live.

    Two-signal check — accept either:
      1. localStorage['Main:User'].loggedIn === true && token (primary, survives
         UI language switch).
      2. DOM text shows DEPONERA/DEPOSIT + SEK balance + no LOG IN button
         (fallback — covers the brief window after navigation when localStorage
         hasn't repopulated yet, and matchup pages where Main:User is cleared).
    """
    import asyncio

    await asyncio.sleep(1)
    for _ in range(3):
        try:
            result = await page.evaluate(
                r"""() => {
                    // Signal 1: localStorage Main:User
                    try {
                        const raw = localStorage.getItem('Main:User');
                        if (raw) {
                            const u = JSON.parse(raw);
                            if (u && u.loggedIn === true && u.token) return {logged_in: true, via: 'storage'};
                        }
                    } catch {}
                    // Signal 2: DOM text — DEPONERA + SEK balance + no LOG IN.
                    const text = document.body.innerText || '';
                    const hasLogin = /\bLOG IN\b/i.test(text) || /\bLOGGA IN\b/i.test(text);
                    const hasBalance = /SEK\s*[\d,.]+/i.test(text) || /[\d,.]+\s*KR/i.test(text);
                    const hasDeposit = /\bDEPONERA\b/i.test(text) || /\bDEPOSIT\b/i.test(text);
                    if (hasBalance && hasDeposit && !hasLogin) return {logged_in: true, via: 'dom'};
                    return {logged_in: false};
                }"""
            )
            if isinstance(result, dict) and result.get("logged_in"):
                return True
        except Exception:
            pass
        await asyncio.sleep(1.5)
    return False


async def _sync_balance(page: Page, intel: dict | None) -> float:
    """Read balance from localStorage['Main:User'].balance — set by the site at login
    and refreshed with every /wallet/balance XHR. Survives UI language switch."""
    try:
        amount = await page.evaluate(
            r"""() => {
                try {
                    const raw = localStorage.getItem('Main:User');
                    if (raw) {
                        const u = JSON.parse(raw);
                        if (u && typeof u.balance === 'number') return u.balance;
                        if (u && u.balance && typeof u.balance.amount === 'number') return u.balance.amount;
                    }
                } catch {}
                return null;
            }"""
        )
        return float(amount) if amount is not None else -1.0
    except Exception as e:
        logger.warning(f"[pinnacle] sync_balance via localStorage failed: {e}")
        return -1.0


# ------------------------------------------------------------------
# Scan — read-only preview
# ------------------------------------------------------------------


async def _scan(page: Page, intel: dict | None) -> dict:
    """Fetch balance, pending bets, settled bets from Pinnacle API. Read-only."""
    try:
        from ....db.models import Bet, Event, get_session
        from ....repositories.profile_repo import ProfileRepo
    except ImportError:
        logger.warning("[pinnacle] DB models not available — scan DB diff disabled")
        Bet = Event = get_session = ProfileRepo = None

    api = _api_base(intel)
    start, end = _date_range()

    # Authenticated request via Playwright request context — sends X-Api-Key,
    # X-Device-UUID, X-Session, and Cookie. Matches the auth surface used by
    # _check_live_price / _sync_history's API fallback so all Pinnacle calls
    # behave identically (was: in-page fetch with cookies-only).
    async def fetch_api(url: str) -> Any:
        return await _evaluate_api(page, url)

    # Balance
    bal_data = await fetch_api(f"{api}/wallet/balance")
    balance = float(bal_data["amount"]) if bal_data and "amount" in bal_data else -1
    currency = bal_data.get("currency", "?") if bal_data else "?"

    # API pending bets
    unsettled = await fetch_api(f"{api}/bets?status=unsettled&startDate={start}&endDate={end}")
    api_pending = [_parse_api_bet(b) for b in _bets_list(unsettled) if float(b.get("price", 0)) > 0]

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
                db_pending.append(
                    {
                        "bet_id": bet.id,
                        "event": f"{h} vs {a}" if h else bet.event_id,
                        "market": bet.market,
                        "outcome": bet.outcome,
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "placed_at": bet.placed_at.isoformat() if bet.placed_at else "",
                    }
                )
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


async def _settle_all(page: Page, intel: dict | None) -> dict:
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
        return await _evaluate_api(page, url)

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
                event_id=event_id,
                provider_id="pinnacle",
                market=p["market_type"],
                outcome=p["designation"],
                odds=p["odds"],
                stake=p["stake"],
                bet_type="mirror",
            )
            recorded_new += 1
            logger.info(
                f"[pinnacle] Recorded missing bet: {p['event']} {p['designation']} @ {p['odds']} stake={p['stake']}"
            )
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
            settled_entries.append(
                {
                    "bet_id": matched_bet.id,
                    "event": event_name,
                    "market": matched_bet.market,
                    "outcome": matched_bet.outcome,
                    "odds": matched_bet.odds,
                    "stake": matched_bet.stake,
                    "result": status,
                    "payout": round(payout, 2),
                    "pl": round(payout - matched_bet.stake, 2),
                }
            )
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
            "wins": len(wins),
            "losses": len(losses),
            "total_staked": round(total_staked, 2),
            "total_payout": round(total_payout, 2),
            "net_pl": round(total_payout - total_staked, 2),
        },
        "new_balance": new_balance,
    }


# ------------------------------------------------------------------
# Sync history — DOM scrape + API fallback
# ------------------------------------------------------------------


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Scrape settled bets from Pinnacle. DOM first, API fallback."""
    api = _api_base(intel)
    entries: list[HistoryEntry] = []

    # DOM scrape (works when on history page)
    try:
        raw = await page.evaluate("() => document.body.innerText")
    except Exception:
        raw = ""

    if raw:
        flat = raw.replace("\n", " ").replace("\r", "")
        cards = re.split(r"(?=(?:Settled|R.ttat):\s)", flat)
        for card in cards:
            if "Stake:" not in card and "Insats:" not in card:
                continue
            odds_m = re.search(r"@\s*([\d.]+)", card)
            stake_m = re.search(r"(?:Stake|Insats):\s*([\d.,]+)", card)
            if not odds_m or not stake_m:
                continue
            odds = float(odds_m.group(1))
            stake = float(stake_m.group(1).replace(",", "."))
            upper = card.upper()
            if "RLUST" in upper or "LOSS" in upper:
                status = "lost"
            elif "VOID" in upper or "CANCEL" in upper or "OGILTIG" in upper:
                status = "void"
            elif "SETTLED" in upper or "TTAT" in upper:
                status = "won"
            else:
                continue
            payout = 0.0 if status == "lost" else (stake * odds if status == "won" else stake)
            bet_id_m = re.search(r"#(\d+)", card)
            event_m = re.search(r"(\w[\w\s.]+?)\s+vs\s+(\w[\w\s.]+?)(?:\s+[A-Z]|\s+@|\s+Bet)", card)
            if stake > 0 and odds > 0:
                entries.append(
                    HistoryEntry(
                        provider_bet_id=bet_id_m.group(1) if bet_id_m else "",
                        event_name=f"{event_m.group(1).strip()} vs {event_m.group(2).strip()}" if event_m else "",
                        market="",
                        outcome="",
                        odds=odds,
                        stake=stake,
                        status=status,
                        payout=payout,
                    )
                )

    logger.info(f"[pinnacle] DOM scrape: {len(entries)} bet(s)")

    # API fallback
    if not entries:
        start, end = _date_range()
        data = await _evaluate_api(page, f"{api}/bets?status=settled&startDate={start}&endDate={end}")
        if isinstance(data, dict) and "__error" in data:
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
            entries.append(
                HistoryEntry(
                    provider_bet_id=str(p["pin_id"] or ""),
                    event_name=p["event"],
                    market=p["market_type"],
                    outcome=p["designation"],
                    odds=p["odds"],
                    stake=p["stake"],
                    status=st,
                    payout=pay,
                )
            )

    return entries


# ------------------------------------------------------------------
# Live price
# ------------------------------------------------------------------


async def _check_live_price(page: Page, bet, intel: dict | None) -> float | None:
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

    markets = await _evaluate_api(page, f"{api}/matchups/{matchup_id}/markets/straight")
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


async def _navigate_to_event(page: Page, bet, intel: dict | None) -> bool:
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


# ------------------------------------------------------------------
# Placement-XHR parsers — called by browser placement interceptor when
# the user clicks CONFIRM on pinnacle.se and the placement XHR returns.
# Pure functions — no Page, no intel.
# ------------------------------------------------------------------


def parse_placement_status(body: dict) -> dict:
    """Infer success/failure from Pinnacle placement XHR response.

    Returns dict with success: bool, error: str | None, max_stake: float | None.
    Success path: response carries wagerNumber or betId.
    Failure path: extract max_stake from top-level keys or limits[].type=='maxRiskStake'.
    """
    if body.get("wagerNumber") or body.get("betId"):
        return {"success": True, "error": None, "max_stake": None}
    max_stake = body.get("maxStake") or body.get("max_stake") or body.get("maximumStake")
    if max_stake is None:
        for limit in body.get("limits") or []:
            if limit.get("type") == "maxRiskStake":
                max_stake = limit.get("amount")
                break
    return {
        "success": False,
        "error": body.get("error") or body.get("errorCode") or "unknown",
        "max_stake": max_stake,
    }


def parse_placement_response(body: dict) -> str | None:
    """Extract provider_bet_id from Pinnacle placement response.

    Tries wagerNumber first (inferred primary), then betId.
    """
    bid = body.get("wagerNumber") or body.get("betId")
    return str(bid) if bid else None


# ------------------------------------------------------------------
# Slip helpers — read odds + update stake without re-navigating.
# Called by SlipOddsStream and ArbRunner.
# ------------------------------------------------------------------


async def _read_slip_odds(page: Page, intel: dict | None) -> float | None:
    """Read American price from localStorage['Main:Betslip'].Selections[0],
    convert to decimal. Returns None when slip empty or storage missing.

    Polled ~1Hz by SlipOddsStream while a counter slip is loaded — must be
    fast and exception-safe.
    """
    try:
        price = await page.evaluate(
            r"""() => {
                const raw = localStorage.getItem("Main:Betslip");
                if (!raw) return null;
                try {
                    const data = JSON.parse(raw);
                    const sels = data?.Selections ?? [];
                    if (sels.length === 0) return null;
                    return sels[0].price;
                } catch { return null; }
            }"""
        )
        if price is None:
            return None
        return _american_to_decimal(float(price))
    except Exception:
        return None


async def _update_slip_stake(page: Page, stake: float, intel: dict | None) -> bool:
    """Write stake to Pinnacle's React-controlled input via the hidden-setter
    pattern. Used by ArbRunner to keep counter slips in sync with anchor
    placements. Returns True iff the React onChange handler fired.
    """
    try:
        result = await page.evaluate(
            """((stake) => {
                const el = document.querySelector('input[placeholder="Stake"]');
                if (!el) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, String(stake));
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            })""",
            stake,
        )
        return bool(result)
    except Exception:
        return False


# ------------------------------------------------------------------
# Betslip prep — DOM click to select outcome, then fill stake.
# ------------------------------------------------------------------


async def _click_market_btn(page: Page, market: str, outcome: str) -> bool:
    """Click the button.market-btn matching market + outcome.

    Strategy: scan button.market-btn elements, group by parent market section
    label (e.g. "Money Line"), pick by visual position (home=0, draw=1, away=2
    for 1x2; over=0, under=1 for totals). Returns True iff a click was dispatched.
    """
    try:
        canon_market = _MARKET_LABEL_MAP.get(market, market)
        position_map = _OUTCOME_POSITION.get(canon_market) or _OUTCOME_POSITION.get("moneyline", {})
        target_pos = position_map.get(outcome)
        if target_pos is None:
            logger.warning(f"[pinnacle] _click_market_btn: unknown outcome {outcome!r} for market {canon_market!r}")
            return False

        # Wait for the markets section to mount — Pinnacle's matchup page lazy-renders
        # button.market-btn ~1-3s after domcontentloaded. Without this, the JS lookup
        # races the React mount and returns -1 (allBtns empty).
        try:
            await page.wait_for_selector("button.market-btn", timeout=10000, state="attached")
        except Exception as e:
            logger.warning(
                f"[pinnacle] _click_market_btn: market-btn never appeared ({e}) — login wall or class rename?"
            )
            return False

        js = """
        (([market, outcome, pos]) => {
            const allBtns = Array.from(document.querySelectorAll('button.market-btn'));
            if (!allBtns.length) return -1;

            const groups = [];
            let currentGroup = null;
            let currentHeader = null;

            for (const btn of allBtns) {
                let el = btn.parentElement;
                let foundHeader = null;
                for (let i = 0; i < 10 && el; i++) {
                    const t = el.textContent || "";
                    const lower = t.toLowerCase();
                    if (lower.includes("money line") || lower.includes("1x2") ||
                        lower.includes("spread") || lower.includes("handicap") ||
                        lower.includes("total") || lower.includes("over/under")) {
                        foundHeader = t.toLowerCase();
                        break;
                    }
                    el = el.parentElement;
                }
                if (foundHeader !== currentHeader) {
                    currentGroup = { header: foundHeader, btns: [] };
                    groups.push(currentGroup);
                    currentHeader = foundHeader;
                }
                if (currentGroup) {
                    currentGroup.btns.push(btn);
                }
            }

            const marketLower = market.toLowerCase();
            let targetGroup = null;
            for (const g of groups) {
                const h = g.header || "";
                if (h.includes(marketLower) ||
                    (marketLower === "moneyline" && h.includes("money line")) ||
                    (marketLower === "1x2" && h.includes("1x2")) ||
                    (marketLower === "spread" && (h.includes("spread") || h.includes("handicap"))) ||
                    (marketLower === "total" && (h.includes("total") || h.includes("over")))) {
                    targetGroup = g;
                    break;
                }
            }

            if (!targetGroup) return -2;
            if (pos >= targetGroup.btns.length) return -3;
            return allBtns.indexOf(targetGroup.btns[pos]);
        })
        """

        idx = await page.evaluate(js, [market, outcome, target_pos])
        if idx is None or idx < 0:
            logger.warning(
                f"[pinnacle] _click_market_btn: btn lookup returned {idx} "
                f"(market={market!r} outcome={outcome!r} pos={target_pos})"
            )
            return False

        await page.evaluate(f"() => document.querySelectorAll('button.market-btn')[{idx}].click()")
        logger.info(f"[pinnacle] Clicked market-btn[{idx}] for {market}/{outcome}")
        return True
    except Exception as e:
        logger.warning(f"[pinnacle] _click_market_btn failed: {e}")
        return False


async def _prep_betslip(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    """Click the correct outcome → wait for slip → write stake.

    Steps:
      1. Resolve market + outcome from bet (dict or attr).
      2. Call _click_market_btn.
      3. Poll localStorage["Main:Betslip"].Selections.length > 0 (5s, 250ms).
      4. Call _update_slip_stake.

    Returns PlacementResult(prepped) on success, (failed, reason) on either gate.
    """
    import asyncio

    def _g(obj, k, default=None):
        if isinstance(obj, dict):
            return obj.get(k, default)
        return getattr(obj, k, default)

    market = (_g(bet, "market") or "moneyline").lower()
    outcome = (_g(bet, "outcome") or "home").lower()
    bet_id = _g(bet, "bet_id", 0) or 0

    clicked = await _click_market_btn(page, market, outcome)
    if not clicked:
        logger.warning(f"[pinnacle] prep_betslip: outcome click failed market={market!r} outcome={outcome!r}")
        return PlacementResult(status="failed", bet_id=bet_id, reason="outcome_btn_not_found")

    slip_populated = False
    for _ in range(20):
        try:
            count = await page.evaluate(
                """() => {
                    const raw = localStorage.getItem("Main:Betslip");
                    if (!raw) return 0;
                    try {
                        const d = JSON.parse(raw);
                        return (d?.Selections ?? []).length;
                    } catch { return 0; }
                }"""
            )
            if count and int(count) > 0:
                slip_populated = True
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    if not slip_populated:
        logger.warning("[pinnacle] prep_betslip: slip not populated within 5s")
        return PlacementResult(status="failed", bet_id=bet_id, reason="slip_not_populated")

    await _update_slip_stake(page, stake, intel)
    return PlacementResult(status="prepped", bet_id=bet_id)


async def _fetch_balance(page: Page, intel: dict | None) -> float | None:
    """Lightweight balance refresh used by the ready-state passive sync loop.

    Same localStorage read as _sync_balance — the source of truth Pinnacle
    keeps fresh on every authenticated XHR. Returns None on failure so the
    runner just skips this tick instead of broadcasting a -1 sentinel.
    """
    val = await _sync_balance(page, intel)
    return val if val >= 0 else None


strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scan=_scan,
    settle_all=_settle_all,
    read_slip_odds=_read_slip_odds,
    update_slip_stake=_update_slip_stake,
    parse_placement_response=parse_placement_response,
    parse_placement_status=parse_placement_status,
)
# place_bet intentionally omitted: GenericWorkflow.place_bet falls back to
# manual mode without autonomous_placement, and provider_runner only invokes
# place_bet when workflow.autonomous_placement is True.
