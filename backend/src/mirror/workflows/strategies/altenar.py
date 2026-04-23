"""Altenar strategy — shadow DOM interactions for all Altenar-platform providers.

Covers: betinia, quickcasino, campobet, swiper, lodur, dbet.

Shadow DOM is forced open via addInitScript in interceptor.py.
All DOM interactions go through page.evaluate() accessing the shadow root.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..base import HistoryEntry, PlacementResult
from . import Strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Altenar market typeId → canonical market type
_MARKET_TYPE_MAP = {
    1: "1x2",
    186: "moneyline",
    219: "moneyline",
    251: "moneyline",
    406: "moneyline",
    30001: "moneyline",
    18: "total",
    189: "total",
    225: "total",
    238: "total",
    258: "total",
    412: "total",
    16: "spread",
    187: "spread",
    223: "spread",
    237: "spread",
    256: "spread",
    410: "spread",
}

_ODD_TYPE_MAP = {
    1: "home",
    2: "draw",
    3: "away",
    1714: "home",
    1715: "away",
    12: "over",
    13: "under",
}

# Cached GetEventDetails responses per event_id
_event_details_cache: dict[str, tuple[dict, float]] = {}


def cache_event_details(event_id: str, data: dict):
    """Called by service.py when GetEventDetails is intercepted."""
    _event_details_cache[event_id] = (data, time.time())


def _get_shadow_root_js() -> str:
    """JS snippet to get the Altenar shadow root. Returns null if not available."""
    return """
        const stb = document.querySelector('STB-SPORTSBOOK');
        if (!stb || !stb.firstElementChild) return null;
        return stb.firstElementChild.shadowRoot;
    """


# ---------------------------------------------------------------------------
# Strategy functions
# ---------------------------------------------------------------------------


async def _check_login(page: Page, intel: dict | None) -> bool:
    """Check login via balance API — same-origin fetch."""
    url = (intel or {}).get("balance", {}).get("api", {}).get("url", "")
    if not url:
        return True
    try:
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const r = await fetch("{url}", {{credentials: "include"}});
                    if (!r.ok) return null;
                    return await r.json();
                }} catch {{ return null; }}
            }}
        """)
        return not (result is None or "__error" in (result or {}))
    except Exception:
        return False


async def _sync_balance(page: Page, intel: dict | None) -> float:
    """Read balance from Altenar account API."""
    url = (intel or {}).get("balance", {}).get("api", {}).get("url", "")
    path = (intel or {}).get("balance", {}).get("api", {}).get("path", "result.cash.total")
    if not url:
        return -1.0
    try:
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const r = await fetch("{url}", {{credentials: "include"}});
                    if (!r.ok) return null;
                    return await r.json();
                }} catch {{ return null; }}
            }}
        """)
        if result is None:
            return -1.0
        # Navigate path
        val = result
        for key in path.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return -1.0
        return float(val)
    except (TypeError, ValueError, KeyError):
        return -1.0


async def _sync_history(page: Page, intel: dict | None) -> list[HistoryEntry]:
    """Full bet history sync: navigate to MINA SPEL, scan open + settled tabs."""
    hist = (intel or {}).get("history", {})
    nav = hist.get("navigation", {})
    history_url = nav.get("url", "")
    if not history_url:
        return []

    pid = (intel or {}).get("provider_id", "altenar")

    try:
        current = page.url or ""
        if "betHistory" not in current:
            await page.goto(history_url, wait_until="domcontentloaded", timeout=15000)

        # Step 1: ÖPPET (open) loads by default — wait for interceptor to catch API
        await asyncio.sleep(3)
        logger.info(f"[{pid}] Scanned ÖPPET (open bets)")

        # Step 2: Click RÄTTATS (settled)
        tabs = nav.get("tabs", {})
        settled_text = tabs.get("settled", {}).get("text", "rättats")
        clicked = await _click_history_tab(page, settled_text)
        if clicked:
            await asyncio.sleep(3)
            logger.info(f"[{pid}] Scanned RÄTTATS (settled bets)")

        # Step 3: Back to ÖPPET
        open_text = tabs.get("open", {}).get("text", "öppet")
        await _click_history_tab(page, open_text)
        await asyncio.sleep(1)

    except Exception as e:
        logger.warning(f"[{pid}] Failed to sync bet history: {e}")
    return []


async def _click_history_tab(page: Page, tab_name: str) -> bool:
    """Click a bet history tab by name in the Altenar shadow DOM."""
    return await page.evaluate(f"""
        () => {{
            const stb = document.querySelector('STB-SPORTSBOOK');
            if (!stb || !stb.firstElementChild) return false;
            const sr = stb.firstElementChild.shadowRoot;
            if (!sr) return false;
            const tabs = sr.querySelectorAll('button[class*="BetHistoryTab"]');
            for (const tab of tabs) {{
                if (tab.textContent.trim().toLowerCase() === '{tab_name}') {{
                    tab.click();
                    return true;
                }}
            }}
            return false;
        }}
    """)


async def _navigate_to_event(page: Page, bet: Any, intel: dict | None) -> bool:
    """Navigate via sportRoutingParams URL."""
    nav = (intel or {}).get("navigation", {})
    template = nav.get("event_url_template", "")
    minimal = nav.get("event_url_minimal", "")

    eid = getattr(bet, "altenar_event_id", None) or getattr(bet, "provider_event_id", "")
    if not eid:
        logger.warning("[altenar] No event_id for navigation")
        return False

    sid = getattr(bet, "altenar_sport_id", "")
    cid = getattr(bet, "altenar_category_id", "")
    chid = getattr(bet, "altenar_championship_id", "")

    if template and sid:
        url = (
            template.replace("{event_id}", str(eid))
            .replace("{sport_id}", str(sid))
            .replace("{category_id}", str(cid))
            .replace("{championship_id}", str(chid))
        )
    elif minimal:
        url = minimal.replace("{event_id}", str(eid))
    else:
        return False

    try:
        current = page.url or ""
        if f"eventId~{eid}" in current:
            return True
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        logger.info(f"[altenar] Navigated to event {eid}")
        return True
    except Exception as e:
        logger.warning(f"[altenar] Navigate failed: {e}")
        return False


async def _place_bet(page: Page, bet: Any, stake: float, intel: dict | None) -> PlacementResult:
    """Auto-select odds + fill stake in Altenar betslip shadow DOM."""
    target_outcome = getattr(bet, "outcome", "")
    display_home = (getattr(bet, "display_home", "") or "").lower()
    display_away = (getattr(bet, "display_away", "") or "").lower()
    target_market = getattr(bet, "market", "")
    pid = (intel or {}).get("provider_id", "altenar")
    keywords = (intel or {}).get("betslip", {}).get("outcome_keywords", {})

    # Step 0: For non-moneyline markets, click "ALLA" tab to show all markets
    if target_market in ("total", "spread", "1x2"):
        await page.evaluate("""
            () => {
                const stb = document.querySelector('STB-SPORTSBOOK');
                if (!stb || !stb.firstElementChild) return false;
                const sr = stb.firstElementChild.shadowRoot;
                if (!sr) return false;
                // Find "Alla" / "All" tab button
                const tabs = sr.querySelectorAll('button, [role="tab"], [class*="Tab"]');
                for (const tab of tabs) {
                    const t = (tab.textContent || '').trim().toLowerCase();
                    if (t === 'alla' || t === 'all') {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        await asyncio.sleep(1)

    # Step 1: Click matching odds button
    logger.info(
        f"[{pid}] Autofill: market={target_market} outcome={target_outcome} "
        f"home={display_home[:20]} away={display_away[:20]}"
    )
    clicked = await page.evaluate(
        """
        (args) => {
            const stb = document.querySelector('STB-SPORTSBOOK');
            if (!stb || !stb.firstElementChild) return { error: 'no_stb' };
            const sr = stb.firstElementChild.shadowRoot;
            if (!sr) return { error: 'no_shadow' };
            const odds = sr.querySelectorAll('div[class*="OddValue"]');
            const { outcome, market, home, away, keywords } = args;

            // Debug: collect all visible odds for logging
            const debug = [];
            for (const odd of odds) {
                const container = odd.parentElement;
                const text = (container.textContent || '').toLowerCase().substring(0, 60);
                const price = parseFloat(odd.textContent.trim());
                debug.push({ text, price });
            }

            for (const odd of odds) {
                const container = odd.parentElement;
                const text = (container.textContent || '').toLowerCase();
                const price = parseFloat(odd.textContent.trim());
                if (!price || price <= 1) continue;

                let match = false;
                if (market === 'total' || market === 'totals') {
                    const overKw = keywords.over || 'över';
                    const underKw = keywords.under || 'under';
                    if (outcome === 'over' && text.includes(overKw)) match = true;
                    if (outcome === 'under' && text.includes(underKw)) match = true;
                } else if (market === 'spread') {
                    if (outcome === 'home' && home && text.includes(home.substring(0, 6))) match = true;
                    if (outcome === 'away' && away && text.includes(away.substring(0, 6))) match = true;
                } else {
                    // moneyline / 1x2
                    if (outcome === 'home' && home && text.includes(home.substring(0, 6))) match = true;
                    if (outcome === 'away' && away && text.includes(away.substring(0, 6))) match = true;
                    if (outcome === 'draw') {
                        const drawKw = keywords.draw || 'oavgjort';
                        if (text.includes(drawKw) || text.includes('draw')) match = true;
                    }
                }

                if (match) {
                    container.click();
                    return { clicked: true, text: text.substring(0, 50), price };
                }
            }
            return { clicked: false, count: odds.length, debug: debug.slice(0, 10) };
        }
    """,
        {
            "outcome": target_outcome,
            "market": target_market,
            "home": display_home,
            "away": display_away,
            "keywords": keywords,
        },
    )

    if clicked and not clicked.get("clicked") and not clicked.get("error"):
        logger.warning(f"[{pid}] No match in {clicked.get('count', 0)} odds. Debug: {clicked.get('debug', [])}")

    if not clicked or clicked.get("error"):
        reason = clicked.get("error", "unknown") if clicked else "eval_failed"
        logger.warning(f"[{pid}] Cannot auto-select: {reason}")
        return PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=stake, reason=reason)

    if not clicked.get("clicked"):
        logger.warning(f"[{pid}] Odds not found ({clicked.get('count', 0)} on page)")
        return PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=stake, reason="odds_not_found")

    logger.info(f"[{pid}] Clicked: {clicked.get('text', '?')} @ {clicked.get('price')}")
    await asyncio.sleep(1.5)

    # Step 2: Fill stake input
    stake_str = f"{stake:.2f}"
    filled = await page.evaluate(f"""
        () => {{
            const stb = document.querySelector('STB-SPORTSBOOK');
            const sr = stb && stb.firstElementChild && stb.firstElementChild.shadowRoot;
            if (!sr) return false;
            const inputs = sr.querySelectorAll('input[type="tel"]');
            for (const input of inputs) {{
                if (input.offsetHeight > 0) {{
                    input.focus();
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(input, '');
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    setter.call(input, '{stake_str}');
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }}
            }}
            return false;
        }}
    """)

    if filled:
        logger.info(f"[{pid}] Stake set to {stake_str} — user confirms")
    else:
        logger.warning(f"[{pid}] Could not fill stake input")

    return PlacementResult(
        status="manual",
        bet_id=bet.bet_id,
        actual_stake=stake,
        reason="auto_selected_user_confirms",
    )


async def _check_live_price(page: Page, bet: Any, intel: dict | None) -> float | None:
    """Read live odds from cached GetEventDetails response."""
    from ....analysis.value import compute_edge

    eid = getattr(bet, "altenar_event_id", None)
    fair_odds = getattr(bet, "fair_odds", None)
    if not eid or not fair_odds:
        return None

    cached = _event_details_cache.get(str(eid))
    if not cached:
        await asyncio.sleep(2)
        cached = _event_details_cache.get(str(eid))
    if not cached or time.time() - cached[1] > 60:
        return None

    data = cached[0]
    target_market = getattr(bet, "market", "")
    target_outcome = getattr(bet, "outcome", "")
    target_point = getattr(bet, "point", None)

    markets = data.get("markets", [])
    odds_list = data.get("odds", [])
    odds_by_id = {o["id"]: o for o in odds_list}

    for m in markets:
        our_market = _MARKET_TYPE_MAP.get(m.get("typeId"))
        if not our_market:
            continue
        if our_market != target_market and not ({our_market, target_market} <= {"1x2", "moneyline"}):
            continue

        flat_ids = [
            oid for group in m.get("desktopOddIds", []) for oid in (group if isinstance(group, list) else [group])
        ]

        for oid in flat_ids:
            odd = odds_by_id.get(oid)
            if not odd or odd.get("oddStatus") != 0:
                continue
            our_outcome = _ODD_TYPE_MAP.get(odd.get("typeId"))
            if our_outcome != target_outcome:
                continue

            if target_market in ("spread", "total") and target_point is not None:
                import re

                match = re.search(r"[(\s]([+-]?\d+\.?\d*)\)?$", odd.get("name", "").strip())
                if match:
                    odd_point = abs(float(match.group(1)))
                    if abs(odd_point - abs(target_point)) > 0.01:
                        continue

            live_price = odd.get("price")
            if not live_price or live_price <= 1:
                continue

            pid = (intel or {}).get("provider_id", "altenar")
            edge = compute_edge(pid, live_price, fair_odds)
            logger.info(
                f"[{pid}] Live: {getattr(bet, 'display_home', '?')} vs "
                f"{getattr(bet, 'display_away', '?')} {target_outcome} @ {live_price:.2f} "
                f"(fair {fair_odds:.2f}) edge={edge:.1f}%"
            )
            return edge

    return None


# ---------------------------------------------------------------------------
# Export strategy
# ---------------------------------------------------------------------------

strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    place_bet=_place_bet,
    check_live_price=_check_live_price,
)
