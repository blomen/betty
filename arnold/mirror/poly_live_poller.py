"""Polymarket live-odds poller — closes the extraction-cycle freshness gap.

The backend polymarket extractor runs every 10 min, so cached odds drift
0-10 min behind the live CLOB book. This module polls Polymarket's public
CLOB API (no auth, no Playwright) every 30s for the top-N polymarket
value-bet candidates and broadcasts `live_price` SSE events so PlayPage —
value-bet rows AND arb-table legs — updates the displayed odds in real time.

Pricing: the outcome's executable best **ask**, keyed on `token_id`, with the
2% fee haircut — shared with `_check_live_price` via `mirror/poly_clob.py`.
An earlier version of this poller read Gamma `outcomePrices` (the mid/last
probability, not the ask), applied no fee, and matched the outcome by fuzzy
team name — so it broadcast prices ~1 cent optimistic and occasionally
swapped the two sides. token_id pins the exact market+outcome+line; no
name-matching, no mid-vs-ask gap.

Architecture:
  ┌─ 30s tick ──────────────────────────────────────────────────┐
  │ 1. GET local /api/opportunities/play/batch                  │
  │ 2. Filter polymarket candidates, sort by edge desc, top 20  │
  │ 3. For each, GET clob.polymarket.com/price by token_id      │
  │ 4. Fee-adjust the ask → live_odds                           │
  │ 5. Broadcast `live_price` SSE per candidate                 │
  └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .poly_clob import ask_to_odds, fetch_clob_ask
from .sse import mirror_broadcaster

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 30.0
TOP_N = 20


async def _poll_once(client: httpx.AsyncClient, local_url: str) -> int:
    """One poll cycle. Returns number of live_price broadcasts fired."""
    try:
        r = await client.post(f"{local_url}/api/opportunities/play/batch", json={}, timeout=15.0)
        r.raise_for_status()
        batch = (r.json() or {}).get("batch") or []
    except Exception as e:
        logger.warning(f"[poly_live_poll] batch fetch failed: {e!r}")
        return 0

    poly = [b for b in batch if b.get("provider_id") == "polymarket"]
    poly.sort(key=lambda b: -(b.get("edge_pct") or 0))
    top = poly[:TOP_N]
    if not top:
        return 0

    fired = 0
    for cand in top:
        # token_id pins the exact outcome's CLOB order book — captured at
        # extraction into provider_meta. No token → can't price live; the
        # row stays on its extraction-time odds (also CLOB + fee based).
        token_id = ((cand.get("provider_meta") or {}).get("token_id")) or ""
        if not token_id:
            continue

        ask = await fetch_clob_ask(token_id)
        if ask is None:
            continue
        live_odds = ask_to_odds(ask)

        fair_odds = cand.get("fair_odds") or 0
        live_edge = None
        if fair_odds and fair_odds > 0:
            live_edge = round((live_odds / float(fair_odds) - 1.0) * 100.0, 2)

        mirror_broadcaster.publish(
            "live_price",
            {
                "provider_id": "polymarket",
                "event_id": cand.get("event_id"),
                "market": cand.get("market"),
                "outcome": cand.get("outcome"),
                "point": cand.get("point"),
                "live_odds": live_odds,
                "live_edge": live_edge,
                "source": "clob_poll",
            },
        )
        fired += 1

    return fired


async def run_poly_live_poller(local_url: str = "http://127.0.0.1:8000") -> None:
    """Forever loop — polls the CLOB every POLL_INTERVAL_SEC, broadcasts live_price.

    Started from arnold/server.py:startup as an asyncio task. Crashes are
    logged but the loop keeps going — the worst case is a few stale rows
    until the next tick (or until the next 10-min extraction cycle).
    """
    logger.info(f"[poly_live_poll] started (interval={POLL_INTERVAL_SEC}s, top_n={TOP_N})")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                fired = await _poll_once(client, local_url)
                if fired:
                    logger.info(f"[poly_live_poll] tick fired {fired} live_price events")
            except asyncio.CancelledError:
                logger.info("[poly_live_poll] cancelled — shutting down")
                raise
            except Exception as e:
                logger.warning(f"[poly_live_poll] tick crashed: {e!r}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
