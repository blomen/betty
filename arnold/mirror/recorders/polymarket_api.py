"""Polymarket bet recorder via public data-api.

Hits https://data-api.polymarket.com/positions?user=<wallet>&sizeThreshold=.1
which returns user's open positions WITHOUT auth (wallet address is the key).

For each position:
1. Compute decimal odds from avgPrice using the same fee formula as the
   polymarket extractor (so stored odds are POST-fee).
2. Compute USDC stake = avgPrice × size.
3. Match the position's market title against arnold's events table to find
   event_id + map outcome to home/away.
4. POST to /api/bets with external_placement=True (skips balance check).

Replaces the DOM-scraping flow in workflows/strategies/polymarket._scrape_portfolio.
Far more reliable: JSON response is stable, no React hydration race, outcome
arrives as a team name (not "Yes"/"No").
"""

from __future__ import annotations

import logging
import re

import httpx

from .types import RecorderResult, RecoveredPosition

logger = logging.getLogger(__name__)

POLY_API = "https://data-api.polymarket.com/positions"
POLY_TRADES_API = "https://data-api.polymarket.com/trades"
POLY_GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
POLY_FEE_RATE = 0.02
DEFAULT_SIZE_THRESHOLD = 0.1
DEFAULT_LIMIT = 50
# Settlement classification thresholds on the SELL price.
# Resolved-YES auto-redeem trades sell at ~0.999; resolved-NO at ~0.001.
# In-between SELLs are manual cashouts — record actual proceeds (price × size).
REDEEM_WON_THRESHOLD = 0.95
REDEEM_LOST_THRESHOLD = 0.05


def _is_condition_id(s: str | None) -> bool:
    """Polymarket conditionIds are 0x-prefixed hex (66 chars full; older DB
    rows truncated to 60). Slugs (`athletics-vs-los-angeles-angels`) never
    start with `0x`.

    The pending-bets endpoint coalesces `bet.provider_bet_id or bet.confirmation_id`,
    and for polymarket the confirmation_id is the event_slug. Settlement via the
    /trades endpoint needs the REAL conditionId; treating a slug as one silently
    skips every manually-placed bet. Use a permissive length floor (>=30) so we
    accept both the legacy-truncated 60-char form and the current 66-char form.
    """
    if not s:
        return False
    s = s.strip()
    return s.startswith("0x") and len(s) >= 30


def _cid_key(s: str | None) -> str:
    """Normalize a conditionId for cross-source equality.

    `/trades` returns full 66-char cids; older DB rows store 60-char truncations.
    Comparing on the common 60-char prefix lets both forms match. (60-char hex
    prefix of a 256-bit hash is still ~10^60 unique — collision-free in practice.)
    """
    return (s or "").strip()[:60]


def _fee_adjusted_odds(price: float) -> float:
    """Same formula as backend.providers.polymarket._price_to_odds."""
    if price <= 0.01 or price >= 0.99:
        return 1.01
    raw = 1.0 / price
    return round(1 + (raw - 1) * (1 - POLY_FEE_RATE), 4)


async def fetch_open_positions(wallet: str) -> list[RecoveredPosition]:
    """Hit poly data-api and parse into RecoveredPosition list."""
    url = f"{POLY_API}?user={wallet}&sizeThreshold={DEFAULT_SIZE_THRESHOLD}&limit={DEFAULT_LIMIT}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.warning(f"[polymarket_api] positions fetch failed: {type(e).__name__}: {e}")
            return []

    out: list[RecoveredPosition] = []
    for p in payload or []:
        try:
            avg = float(p.get("avgPrice") or 0)
            size = float(p.get("size") or 0)
            if avg <= 0 or size <= 0:
                continue
            out.append(
                RecoveredPosition(
                    provider_id="polymarket",
                    # Full 66-char conditionId (0x + 64 hex). Older inserts truncated
                    # to 60 chars; the DB column is unbounded so the cap was arbitrary
                    # local code that broke equality lookups against /trades responses
                    # (which always return the full 66-char form).
                    provider_bet_id=(p.get("conditionId") or ""),
                    event_name=(p.get("title") or "")[:120],
                    outcome_name=p.get("outcome") or "",
                    odds=_fee_adjusted_odds(avg),
                    stake=round(avg * size, 2),
                    currency="USDC",
                    raw=p,
                )
            )
        except Exception as e:
            logger.warning(f"[polymarket_api] skipped position {p.get('title', '')[:40]}: {e}")
    return out


# ── Event matching ──
# Given a polymarket market title + outcome name, find matching arnold event_id
# and map outcome to home/away. Uses team-name fuzzy match: both teams must
# appear in the title, then outcome_name is compared to home/away to pick side.

_STOP = {"vs", "v", "the", "fc", "cf", "sc", "fk", "ec", "esports"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return {t for t in s.split() if t and len(t) >= 3 and t not in _STOP}


def _match_outcome(outcome_name: str, home: str, away: str) -> str | None:
    """Map outcome_name to 'home' or 'away' based on team-name substring."""
    on = (outcome_name or "").lower()
    h, a = (home or "").lower(), (away or "").lower()
    if not on or not h or not a:
        return None
    # Exact substring (either direction)
    if h and (h in on or on in h):
        return "home"
    if a and (a in on or on in a):
        return "away"
    # Token overlap fallback
    ton = _tokens(outcome_name)
    th, ta = _tokens(home), _tokens(away)
    if th and ton & th:
        return "home"
    if ta and ton & ta:
        return "away"
    return None


def match_event_and_outcome(
    position: RecoveredPosition,
    events: list[dict],
) -> tuple[str | None, str | None]:
    """Find best-matching event_id + outcome side for this position.

    events: list of dicts with {id, home_team, away_team}. Pre-filtered by
    caller to recent/upcoming events to keep the search space small.
    """
    title = (position.event_name or "").lower()
    if not title:
        return None, None

    best: tuple[int, str, str] | None = None
    for ev in events:
        home = (ev.get("home_team") or "").lower()
        away = (ev.get("away_team") or "").lower()
        if not home or not away:
            continue
        # Title must contain BOTH team names (anchor)
        if home not in title or away not in title:
            continue
        side = _match_outcome(position.outcome_name, home, away)
        if not side:
            continue
        score = len(home) + len(away)
        if best is None or score > best[0]:
            best = (score, ev["id"], side)

    if best:
        return best[1], best[2]
    return None, None


# ── End-to-end sync ──


async def sync(
    wallet: str,
    api_post,  # async callable(payload: dict) -> response
    fetch_events,  # async callable() -> list[{id, home_team, away_team}]
    fetch_db_pending,  # async callable() -> list[{provider_bet_id, event_id, outcome, odds, stake}]
    api_patch=None,  # async callable(bet_id: int, payload: dict) -> response — used to
    #                 backfill provider_bet_id onto rows recorded without one
) -> RecorderResult:
    """Full sync: fetch poly positions, dedup against DB, insert new ones.

    Also backfills conditionId onto existing pending rows that were recorded
    via the DOM-intercept path (which doesn't capture conditionId for poly).
    Without the backfill, settle() can never match these bets against the
    /trades endpoint and they sit pending until manual history reconcile.
    """
    result = RecorderResult(provider_id="polymarket")

    positions = await fetch_open_positions(wallet)
    result.fetched = len(positions)
    if not positions:
        return result

    events = await fetch_events() or []
    db_pending = await fetch_db_pending() or []

    # Index pending bets by normalized conditionId so a truncated 60-char DB
    # cid matches a 66-char position cid. Slugs (`athletics-vs-...`) are
    # filtered out by _is_condition_id — they must never collide.
    known_ids = {_cid_key(b.get("provider_bet_id")) for b in db_pending if _is_condition_id(b.get("provider_bet_id"))}
    known_sigs = {
        (b.get("event_id"), b.get("outcome")): b for b in db_pending if b.get("event_id") and b.get("outcome")
    }

    for pos in positions:
        # Dedup by conditionId (preferred — stable provider id)
        if pos.provider_bet_id and _cid_key(pos.provider_bet_id) in known_ids:
            result.skipped_dup += 1
            continue

        event_id, outcome = match_event_and_outcome(pos, events)
        if not event_id or not outcome:
            result.skipped_unmatched += 1
            logger.info(
                f"[polymarket_api] unmatched position: {pos.event_name[:60]} / "
                f"outcome={pos.outcome_name} — skipping insert (no event match)"
            )
            continue

        # Dedup by (event_id, outcome) — same market same side. Backfill the
        # row with the full conditionId when (a) it has no cid, OR (b) it has
        # an older truncated cid. Both block settle()'s /trades lookup.
        if (event_id, outcome) in known_sigs:
            result.skipped_dup += 1
            existing = known_sigs[(event_id, outcome)]
            existing_cid = existing.get("provider_bet_id") or ""
            needs_backfill = (
                not _is_condition_id(existing_cid)  # slug or empty
                or len(existing_cid) < 66  # truncated to 60 chars
            )
            if pos.provider_bet_id and api_patch is not None and needs_backfill:
                bet_id = existing.get("id") or existing.get("bet_id")
                if bet_id:
                    try:
                        resp = await api_patch(int(bet_id), {"provider_bet_id": pos.provider_bet_id})
                        if resp.status_code in (200, 201):
                            logger.info(
                                f"[polymarket_api] backfilled conditionId {pos.provider_bet_id[:14]}… "
                                f"onto bet {bet_id} ({pos.event_name[:40]})"
                            )
                        else:
                            logger.warning(
                                f"[polymarket_api] backfill PATCH bet {bet_id} → "
                                f"{resp.status_code}: {(resp.text or '')[:120]}"
                            )
                    except Exception as e:
                        logger.warning(f"[polymarket_api] backfill bet {bet_id} raised: {type(e).__name__}: {e}")
            continue

        payload = {
            "provider_id": "polymarket",
            "event_id": event_id or "",
            "market": "moneyline",
            "outcome": outcome or "",
            "odds": pos.odds,
            "stake": pos.stake,
            "external_placement": True,
            "boost_event": pos.event_name,
            "provider_bet_id": pos.provider_bet_id or None,
            "bet_type": "arb_counter",  # Polymarket positions in your stack are arb counters
        }

        try:
            resp = await api_post(payload)
            if resp.status_code in (200, 201):
                result.inserted += 1
            else:
                msg = f"{resp.status_code}: {(resp.text or '')[:200]}"
                result.errors.append(f"{pos.event_name[:40]}: {msg}")
                logger.warning(f"[polymarket_api] insert failed {pos.event_name[:40]}: {msg}")
        except Exception as e:
            result.errors.append(f"{pos.event_name[:40]}: {type(e).__name__}: {e}")
            logger.warning(f"[polymarket_api] insert exception {pos.event_name[:40]}: {e}")

    logger.info(f"[polymarket_api] {result.summary()}")
    return result


# ── Settlement detection via trades endpoint ───────────────────────────
# Polymarket auto-redeems winning positions when the market resolves: the
# winning side gets a SELL trade at price ≈ 0.999 (1 USDC per share). Losing
# positions don't generate a redeem trade — the LOST side stays in the
# positions list until manually claimed, but its avgPrice is now meaningless.
# So we detect:
#   - WON: SELL trade at price ≥ 0.95 on a conditionId matching a DB pending
#   - LOST: DB pending with conditionId where the OPPOSITE outcome won (mirror
#     trade exists for sister token at high price). Approximated by: DB pending
#     conditionId where the user's recorded position is no longer in open
#     positions AND no redeem trade exists → assume LOST.


async def fetch_recent_trades(wallet: str, limit: int = 500) -> list[dict]:
    """Fetch recent trades for the wallet. Returns raw dicts.

    Includes BUY (placements) and SELL (manual exits + auto-redemptions).
    The auto-redeem SELL fires within ~minutes of UMA finalizing the market.
    """
    url = f"{POLY_TRADES_API}?user={wallet}&limit={limit}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.warning(f"[polymarket_api] trades fetch failed: {type(e).__name__}: {e}")
            return []
    return payload or []


async def fetch_market_resolution(condition_id: str) -> dict | None:
    """Fetch market record from gamma-api by conditionId.

    Returns the raw market dict (or None on miss). A resolved market has
    `closed: true` plus `outcomes` / `outcomePrices` filled with the final
    "1"/"0" payouts per side. Used as the authoritative LOST signal for bets
    whose losing token never auto-redeems (the common case on polymarket).
    """
    if not condition_id:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            r = await client.get(POLY_GAMMA_MARKETS, params={"condition_ids": condition_id})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning(
                f"[polymarket_api] gamma market fetch failed for {condition_id[:14]}…: {type(e).__name__}: {e}"
            )
            return None
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def _parse_json_list(field) -> list:
    """gamma-api returns outcomes/outcomePrices as JSON strings — parse safely."""
    import json as _json

    if isinstance(field, list):
        return field
    if isinstance(field, str):
        try:
            v = _json.loads(field)
            return v if isinstance(v, list) else []
        except Exception:
            return []
    return []


def _resolve_my_outcome_index(
    outcomes: list, bet_outcome: str, home_team: str | None, away_team: str | None
) -> int | None:
    """Map bet.outcome ('home'/'away') to an index in market.outcomes.

    Reuses _match_outcome's name-substring logic on each outcome label, picking
    the index whose label matches our side. Returns None if no clean match.
    """
    if not outcomes or not bet_outcome:
        return None
    home = (home_team or "").lower()
    away = (away_team or "").lower()
    bet_outcome_l = bet_outcome.lower()
    for i, name in enumerate(outcomes):
        side = _match_outcome(str(name or ""), home, away)
        if side and side == bet_outcome_l:
            return i
    return None


async def settle(
    wallet: str,
    api_settle,  # async callable(bet_id: int, result: str, payout: float) -> response
    fetch_db_pending,  # async callable() -> list[{id, provider_bet_id, event_id, outcome, odds, stake}]
) -> dict:
    """Settle DB pending polymarket bets using API evidence only.

    Two sources of truth, in order:
      1. /trades — SELL at ≥0.95 = auto-redeem WON; ≤0.05 = LOST; mid-price
         in between = manual cashout, payout = price × size.
      2. gamma-api /markets — for bets whose losing token never auto-redeems
         (the common case on polymarket), query market resolution. closed=true
         with our side's outcomePrice = 0 → confirmed LOST.

    NO fall-through inference: a missing position + missing trade + market
    not yet resolved leaves the bet pending. False LOST classifications
    corrupt analytics worse than slow settlement.

    Returns {won, lost, errors} count summary.
    """
    out = {"won": 0, "lost": 0, "skipped": 0, "errors": []}

    pending = await fetch_db_pending() or []
    if not pending:
        return out

    trades = await fetch_recent_trades(wallet)
    positions = await fetch_open_positions(wallet)

    # Index by normalized 60-char cid prefix so a truncated bet.cid (older
    # DB row) matches a full 66-char trade/position cid.
    trades_by_cid: dict[str, list[dict]] = {}
    for t in trades:
        k = _cid_key(t.get("conditionId"))
        if not k:
            continue
        trades_by_cid.setdefault(k, []).append(t)

    # Index open positions by normalized conditionId AND by lowercased title.
    # The title index lets us recover a conditionId for bets recorded via DOM
    # intercept (no provider_bet_id captured at placement time). Without this,
    # every such bet stays "pending" forever even though /trades has its resolution.
    open_cids: set[str] = set()
    cid_by_title: dict[str, str] = {}
    for pos in positions:
        full_cid = (pos.provider_bet_id or "").strip()
        k = _cid_key(full_cid)
        if k:
            open_cids.add(k)
            title = (pos.event_name or "").lower().strip()
            if title:
                cid_by_title[title] = full_cid  # store FULL cid for backfill

    # Won/redeemed positions drop out of the open-positions list (size→0 after
    # SELL at ~0.999), so a bet that already resolved would have no title→cid
    # entry from positions alone. Trades survive — index by their title fields
    # too so the recovery path also catches recently-settled bets.
    for t in trades:
        full_tcid = (t.get("conditionId") or "").strip()
        if not full_tcid:
            continue
        for key in ("title", "eventTitle", "marketTitle", "slug"):
            ttitle = (t.get(key) or "").lower().strip()
            if ttitle and ttitle not in cid_by_title:
                cid_by_title[ttitle] = full_tcid

    def _recover_cid_from_title(bet: dict) -> str | None:
        """Find a position whose title matches the bet's event_name or home/away."""
        bet_event = (bet.get("event_name") or "").lower().strip()
        if bet_event and bet_event in cid_by_title:
            return cid_by_title[bet_event]
        home = (bet.get("home_team") or "").lower().strip()
        away = (bet.get("away_team") or "").lower().strip()
        # Substring match: position title contains both team names.
        if home and away:
            for title, c in cid_by_title.items():
                if home in title and away in title:
                    return c
        # Last-ditch: bet_event substring matches a position title.
        if bet_event and len(bet_event) >= 6:
            for title, c in cid_by_title.items():
                if bet_event in title or title in bet_event:
                    return c
        return None

    for bet in pending:
        bet_id = bet.get("id")
        raw_cid = (bet.get("provider_bet_id") or "").strip()
        # Coalesced confirmation_id (event_slug) shows up here as a non-cid
        # string — never let it through; recover from open positions if possible.
        cid = raw_cid if _is_condition_id(raw_cid) else (_recover_cid_from_title(bet) or "")
        stake = float(bet.get("stake") or 0)
        odds = float(bet.get("odds") or 0)
        if not bet_id or not cid:
            out["skipped"] += 1
            continue

        # Compute our position's expected share count: stake / avg_price
        # avg_price = 1/odds (in fee-net world this maps to ~price)
        # Polymarket native: shares = stake_usdc / avg_price_cents_decimal
        avg_price = round(1.0 / odds, 4) if odds > 0 else 0
        shares = round(stake / avg_price, 2) if avg_price > 0 else 0
        cid_trades = trades_by_cid.get(_cid_key(cid)) or []

        # SELL trades on our cid. Each user has at most one position per market,
        # so any SELL on our cid is OUR exit (auto-redeem at ~$1, manual cashout
        # mid-price, or worthless-resolution dump).
        sells = [t for t in cid_trades if t.get("side") == "SELL"]
        won_trade = next((t for t in sells if float(t.get("price") or 0) >= REDEEM_WON_THRESHOLD), None)
        lost_trade = next((t for t in sells if float(t.get("price") or 0) <= REDEEM_LOST_THRESHOLD), None)
        cashout_trade = next(
            (t for t in sells if REDEEM_LOST_THRESHOLD < float(t.get("price") or 0) < REDEEM_WON_THRESHOLD),
            None,
        )

        result = None
        payout = 0.0
        if won_trade:
            # Gross USDC received = size × price (price ≈ 0.999 for auto-redeem)
            result = "won"
            payout = round(float(won_trade.get("size") or 0) * float(won_trade.get("price") or 1.0), 2)
        elif lost_trade:
            result = "lost"
            payout = round(float(lost_trade.get("size") or 0) * float(lost_trade.get("price") or 0), 2)
        elif cashout_trade:
            # Manual mid-market exit — record actual proceeds. Classify
            # by P/L vs stake (won = recovered more than we put in).
            proceeds = round(
                float(cashout_trade.get("size") or 0) * float(cashout_trade.get("price") or 0),
                2,
            )
            result = "won" if proceeds > stake else "lost"
            payout = proceeds
        else:
            # No SELL trade. Don't infer LOST from a missing position — that's
            # the bug that mis-settled bets 561/562/563 (auto-redeem fired
            # between our /trades and /positions snapshots). Instead, ask
            # gamma-api whether the market is resolved.
            mkt = await fetch_market_resolution(cid)
            if mkt and (mkt.get("closed") or mkt.get("resolved")):
                outcomes = _parse_json_list(mkt.get("outcomes"))
                prices = _parse_json_list(mkt.get("outcomePrices"))
                if outcomes and prices and len(outcomes) == len(prices):
                    my_idx = _resolve_my_outcome_index(
                        outcomes,
                        str(bet.get("outcome") or ""),
                        bet.get("home_team"),
                        bet.get("away_team"),
                    )
                    if my_idx is not None:
                        try:
                            my_price = float(prices[my_idx])
                        except (ValueError, TypeError):
                            my_price = -1.0
                        if my_price <= 0.05:
                            result = "lost"
                            payout = 0.0
                        elif my_price >= 0.95:
                            # We won but redeem trade not propagated yet.
                            # Record at face value (shares × $1) — next poll's
                            # trade-based path will correct if needed.
                            result = "won"
                            payout = round(shares * 1.0, 2)
                        # Mid-price resolved (rare — e.g. partial refund) → leave
                        # pending; manual review needed.

        if result is None:
            # Still pending — position still open + no redeem trade
            continue

        try:
            resp = await api_settle(bet_id, result, payout)
            if resp.status_code in (200, 201):
                if result == "won":
                    out["won"] += 1
                else:
                    out["lost"] += 1
                logger.info(f"[polymarket_api] settled bet {bet_id} cid={cid[:12]}… → {result} payout=${payout:.2f}")
            else:
                msg = f"{resp.status_code}: {(resp.text or '')[:200]}"
                out["errors"].append(f"bet {bet_id}: {msg}")
                logger.warning(f"[polymarket_api] settle failed bet {bet_id}: {msg}")
        except Exception as e:
            out["errors"].append(f"bet {bet_id}: {type(e).__name__}: {e}")
            logger.warning(f"[polymarket_api] settle exception bet {bet_id}: {e}")

    return out
