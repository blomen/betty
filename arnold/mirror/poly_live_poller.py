"""Polymarket live-odds poller — closes the extraction-cycle freshness gap.

Backend polymarket extractor runs every 10 min, so cached odds drift 0-10
min behind the live CLOB book. This module polls Polymarket's public Gamma
API (no auth, no Playwright) every 30s for the top-N polymarket value-bet
candidates and broadcasts `live_price` SSE events so PlayPage updates the
displayed odds in real time. Without it, the user sees stale extraction-
time odds and clicks bets whose +EV has already evaporated.

Architecture:
  ┌─ 30s tick ──────────────────────────────────────────────────┐
  │ 1. GET local /api/opportunities/play/batch                  │
  │ 2. Filter polymarket candidates, sort by edge desc, top 20  │
  │ 3. For each, GET https://gamma-api.polymarket.com/events?   │
  │      slug=<event_slug>                                      │
  │ 4. Match outcome → extract live prob → compute live_odds    │
  │ 5. Broadcast `live_price` SSE per candidate                 │
  └─────────────────────────────────────────────────────────────┘

Pure HTTP — no Playwright, no DB write, no auth. Gamma API endpoint is
the same one mirror/strategies/polymarket.py:_check_live_price uses for
the user-clicked navigation flow; this poller extends that coverage to
unclicked top-of-table rows.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .sse import mirror_broadcaster

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 30.0
TOP_N = 20
GAMMA_BASE = "https://gamma-api.polymarket.com/events"


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
        slug = ((cand.get("provider_meta") or {}).get("event_slug")) or ""
        if not slug:
            continue
        outcome = (cand.get("outcome") or "").lower()
        market = (cand.get("market") or "").lower()
        if market not in ("moneyline", "1x2", "spread", "total"):
            continue

        try:
            r2 = await client.get(GAMMA_BASE, params={"slug": slug}, timeout=10.0)
            if r2.status_code != 200:
                continue
            data = r2.json()
        except Exception as e:
            logger.debug(f"[poly_live_poll] gamma fetch for {slug[:40]} failed: {e!r}")
            continue
        if not isinstance(data, list) or not data:
            continue

        ev = data[0]
        markets = ev.get("markets") or []
        if not markets:
            continue

        # Pick the moneyline / matching market — most polymarket events have
        # 1 main market; spread/total events have a few. We match by name
        # against the candidate's display_home/away or outcome label.
        target_name = ""
        if outcome in ("home", "1"):
            target_name = (cand.get("display_home") or "").lower()
        elif outcome in ("away", "2"):
            target_name = (cand.get("display_away") or "").lower()
        if not target_name:
            continue

        live_prob = None
        for m in markets:
            try:
                outs_raw = m.get("outcomes") or "[]"
                prices_raw = m.get("outcomePrices") or "[]"
                outs = json.loads(outs_raw) if isinstance(outs_raw, str) else outs_raw
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            except Exception:
                continue
            if not outs or len(outs) != len(prices):
                continue
            for i, name in enumerate(outs):
                nlow = (name or "").lower()
                if nlow == target_name or target_name in nlow or nlow in target_name:
                    try:
                        live_prob = float(prices[i])
                    except (ValueError, TypeError):
                        live_prob = None
                    break
            if live_prob is not None:
                break

        if live_prob is None or live_prob <= 0 or live_prob >= 1:
            continue

        live_odds = round(1.0 / live_prob, 3)
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
                "source": "gamma_poll",
            },
        )
        fired += 1

    return fired


async def run_poly_live_poller(local_url: str = "http://127.0.0.1:8000") -> None:
    """Forever loop — polls Gamma every POLL_INTERVAL_SEC, broadcasts live_price.

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
