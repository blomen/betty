"""PendingLoop — periodic settlement sync across all providers with pending bets."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

_AUTH_HEADER = "X-Nginx-Authenticated"
_AUTH_VALUE = "arnoldsports"
_POLL_INTERVAL = 60  # seconds
_CONFIRM_TIMEOUT = 300  # seconds
_ODDS_TOL = 0.10  # 10% tolerance
_STAKE_TOL = 0.30  # 30% tolerance


# ---------------------------------------------------------------------------
# Module-level detection helper
# ---------------------------------------------------------------------------


def _token_overlap(a: str, b: str) -> float:
    """Word-level overlap ratio between two strings."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


_NAME_ODDS_TOL = 0.05  # 5% tolerance when event name matches


def _detect_settlements(db_pending: list[dict], history: list[dict]) -> list[dict]:
    """Three-tier matching: exact provider_bet_id → event name+odds → fuzzy odds+stake.

    Returns a list of settlement dicts: {bet_id, result, payout, match_method}.
    """
    settlements: list[dict] = []
    used_history: set[int] = set()  # indices of matched history entries

    # Build a set of open bet signatures to avoid false positives:
    # if a bet with the same odds+stake is still open in history, don't settle it
    # via fuzzy matching against a different settled bet.
    _open_sigs: set[tuple[float, float]] = set()
    for entry in history:
        h_status = (entry.get("status") or "").lower()
        if h_status in ("pending", "open", ""):
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            if h_odds > 0:
                _open_sigs.add((round(h_odds, 2), round(h_stake, 2)))

    for bet in db_pending:
        bet_id = bet.get("bet_id") or bet.get("id")
        bet_provider_id = str(bet.get("provider_bet_id") or "")
        # Build event name from home_team/away_team if event_name not set
        bet_event = (bet.get("event_name") or "").lower().strip()
        if not bet_event:
            home = bet.get("home_team") or ""
            away = bet.get("away_team") or ""
            if home and away:
                bet_event = f"{home} v {away}".lower().strip()
        bet_odds = float(bet.get("odds", 0) or 0)
        bet_stake = float(bet.get("stake", 0) or 0)

        matched = None
        method = None

        for idx, entry in enumerate(history):
            if idx in used_history:
                continue
            h_status = (entry.get("status") or "").lower()
            if h_status in ("pending", "open", ""):
                continue

            # Tier 1: exact provider_bet_id match
            h_pid = str(entry.get("provider_bet_id") or "")
            if bet_provider_id and h_pid and bet_provider_id == h_pid:
                matched = (idx, entry)
                method = "id"
                break

            # Tier 2: event name match (+ odds if available)
            h_event = (entry.get("event_name") or "").lower().strip()
            # Normalize "vs." → "v" for comparison
            h_norm = h_event.replace(" vs. ", " v ").replace(" vs ", " v ")
            bet_norm = bet_event.replace(" vs. ", " v ").replace(" vs ", " v ")
            if (
                bet_norm
                and h_norm
                and (
                    bet_norm in h_norm
                    or h_norm in bet_norm
                    or _token_overlap(bet_norm, h_norm) >= 0.5
                )
            ):
                h_odds = float(entry.get("odds", 0) or 0)
                # If both have odds, check they're close; if history has no odds (lost bet), accept name match alone
                if (
                    h_odds > 0
                    and bet_odds > 0
                    and abs(h_odds - bet_odds) / bet_odds > _NAME_ODDS_TOL
                ):
                    continue  # odds don't match, try next
                # Also check stake is in the right ballpark (within 50%) to avoid false matches
                h_stake = float(entry.get("stake", 0) or 0)
                if (
                    h_stake > 0
                    and bet_stake > 0
                    and abs(h_stake - bet_stake) / bet_stake > 0.5
                ):
                    continue
                matched = (idx, entry)
                method = "name"
                break

            # Tier 3: fuzzy odds+stake fallback
            h_odds = float(entry.get("odds", 0) or 0)
            h_stake = float(entry.get("stake", 0) or 0)
            if (
                bet_odds > 0
                and h_odds > 0
                and abs(h_odds - bet_odds) / bet_odds <= _ODDS_TOL
                and bet_stake > 0
                and h_stake > 0
                and abs(h_stake - bet_stake) / bet_stake <= _STAKE_TOL
            ):
                matched = (idx, entry)
                method = "fuzzy"
                break

        if matched:
            idx, entry = matched
            # Guard: if matched via fuzzy/name (not exact ID) and a bet with
            # the same odds is still open, this is likely a false positive —
            # the DB bet is the open one, not the settled one.
            if (
                method != "id"
                and (round(bet_odds, 2), round(bet_stake, 2)) in _open_sigs
            ):
                logger.info(
                    f"[settle] Skipping false positive: bet {bet_id} ({bet_odds}@{bet_stake}) "
                    f"matched settled entry via {method} but an open bet with same odds exists"
                )
                continue
            used_history.add(idx)
            settlements.append(
                {
                    "bet_id": bet_id,
                    "result": (entry.get("status") or "").lower(),
                    "payout": entry.get("payout"),
                    "match_method": method,
                    "provider_bet_id": str(entry.get("provider_bet_id") or ""),
                }
            )

    return settlements


# ---------------------------------------------------------------------------
# PendingLoop
# ---------------------------------------------------------------------------


class PendingLoop:
    """Periodically fetches pending bets and syncs settlement status per provider."""

    def __init__(
        self,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
    ):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._task: asyncio.Task | None = None
        self._running = False
        self._confirm_events: dict[str, asyncio.Event] = {}
        self._status: dict[str, dict] = {}  # pid -> {last_sync, pending, settlements}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="pending_loop")
        logger.info("[PendingLoop] started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[PendingLoop] stopped")

    def confirm(self, provider_id: str) -> None:
        """Signal that the user has confirmed settlements for a provider."""
        ev = self._confirm_events.get(provider_id)
        if ev:
            ev.set()
            logger.info(f"[PendingLoop] confirm received for {provider_id}")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "providers": dict(self._status),
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self._sync_all()
            except Exception:
                logger.exception("[PendingLoop] error in _sync_all")
            await asyncio.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Sync all providers
    # ------------------------------------------------------------------

    async def _sync_all(self) -> None:
        # Short-circuit when the browser isn't up: every provider's sync_provider
        # would just log "browser not running" and return. No work to do here,
        # no tunnel traffic to generate, and no log spam every minute.
        if not (self._browser.running and self._browser.context):
            return

        # Refresh balance for every provider with an open tab, regardless of
        # pending bets. Without this, balance only writes to DB when the SPA
        # itself fetches /balance and the browser interceptor catches it —
        # so providers like kalshi go stale once the user navigates away
        # from portfolio (the SPA stops re-fetching balance on a markets page).
        await self._refresh_balances()

        pending_by_provider = await self._fetch_pending()

        # Also sync every provider with an open tab — even if DB shows 0
        # pending. The provider may have pending bets we don't know about
        # (manually placed, or placed before the mirror existed), and
        # _record_unknown_open_bets inside _sync_provider is the only way
        # they get inserted into the DB / surfaced in the UI's PENDING
        # section. Without this, spelklubben's 7 historical pending bets
        # stay invisible forever even though the tab is open.
        sync_pids: dict[str, list[dict]] = dict(pending_by_provider)
        seen: set[str] = set()
        for page in list(self._browser.context.pages):
            try:
                url = page.url or ""
            except Exception:
                continue
            pid = self._browser._detect_provider(url)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            sync_pids.setdefault(pid, [])

        if not sync_pids:
            return

        tasks = [self._sync_provider(pid, bets) for pid, bets in sync_pids.items()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_balances(self) -> None:
        """Pull a fresh balance from each open provider tab and write it to the DB.

        Uses the same tab-state safety gate as `_sync_provider` (no event-page
        clobbering) and only acts on providers whose strategy implements a
        non-DOM-disrupting `sync_balance` (all current strategies qualify —
        request-context API or read-only DOM scrape).
        """
        from .workflows import get_workflow

        seen: set[str] = set()
        for page in list(self._browser.context.pages):
            try:
                url = page.url or ""
            except Exception:
                continue
            pid = self._browser._detect_provider(url)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            url_lower = url.lower()
            try:
                workflow = get_workflow(pid)
            except Exception:
                continue
            on_event = "/event/" in url_lower or "#/event/" in url_lower
            # Same gate as _sync_provider — never refresh while user is on an
            # event page UNLESS the workflow is API-passive (sync_balance is
            # pure-API for those — no risk of clobbering an open betslip).
            if on_event and not getattr(workflow, "sync_history_is_passive", False):
                continue
            try:
                if not await workflow.check_login(page):
                    continue
                balance = await workflow.sync_balance(page)
                if balance >= 0:
                    await self._post_balance(pid, balance)
            except Exception:
                logger.debug(f"[PendingLoop] balance refresh failed for {pid}")

    async def _fetch_pending(self) -> dict[str, list[dict]]:
        """GET /api/opportunities/play/pending-bets → {provider_id: [bet, ...]}"""
        from local.http_client import tunnel_client

        try:
            client = tunnel_client()
            resp = await client.get(
                "/api/opportunities/play/pending-bets", timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
        except (
            httpx.ReadTimeout,
            httpx.ReadError,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ) as e:
            # Tunnel/server transient — already logged elsewhere by the
            # tunnel watchdog. Single-line at debug level instead of a
            # multi-page traceback every cycle.
            logger.debug(
                f"[PendingLoop] fetch failed (tunnel/server down): {e.__class__.__name__}"
            )
            return {}
        except Exception:
            logger.exception("[PendingLoop] failed to fetch pending bets")
            return {}

        # API returns {providers: [{provider_id, bets: [...]}, ...]}
        grouped: dict[str, list[dict]] = {}
        for prov in data.get("providers", []):
            pid = prov.get("provider_id")
            bets = prov.get("bets", [])
            if pid and bets:
                grouped[pid] = bets
        return grouped

    # ------------------------------------------------------------------
    # Per-provider sync
    # ------------------------------------------------------------------

    async def _sync_provider(self, pid: str, db_bets: list[dict]) -> None:
        # _sync_all already guarantees the browser is up; no need to re-check.
        logger.info(f"[PendingLoop] syncing {pid} ({len(db_bets)} pending bets)")
        self._status.setdefault(pid, {})["pending"] = len(db_bets)

        from .workflows import get_workflow

        workflow = get_workflow(pid)
        page = await workflow.find_tab(self._browser.context)

        if page is None:
            logger.debug(
                f"[PendingLoop] no open tab for {pid}, skipping (user must open it)"
            )
            return

        # Skip if the tab is on an event page — a play runner likely has the betslip
        # prepped and waiting for confirmation. A bet history sync would clobber it.
        # Everything else is safe — landing, lobby, /portfolio, history pages
        # (/spelhistorik on Gecko V2, /history on others), search results.
        # If we're not mid-bet, the page can be navigated freely.
        current_url = (page.url or "").lower()
        has_event = "/event/" in current_url or "#/event/" in current_url
        if has_event and not getattr(workflow, "sync_history_is_passive", False):
            logger.debug(
                f"[PendingLoop] {pid} tab is on an event page ({current_url[:60]}); "
                f"skipping sync to avoid clobbering an active betslip"
            )
            return

        # 2. Check login — three-tier (same as /mirror/browser/provider/{pid})
        # because workflow.check_login fails for several providers that ship
        # an analytics shim hijacking window.fetch (Spelklubben's GTM
        # tracker.js → "TypeError: Failed to fetch" on cloud-api/wallets).
        # The page's own JS still loads successfully, the interceptor caught
        # the wallets response (browser.provider_data has logged_in=True,
        # balance set), and the DOM scrape can confirm. Trust any of those.
        logged_in = False
        try:
            if await workflow.check_login(page):
                logged_in = True
        except Exception:
            logger.debug(f"[PendingLoop] workflow.check_login raised for {pid}")
        if not logged_in:
            intercepted = self._browser.provider_data.get(pid, {}) or {}
            if intercepted.get("logged_in"):
                logged_in = True
        if not logged_in:
            try:
                dom = await self._browser.check_login_dom(pid)
                if dom.get("logged_in"):
                    logged_in = True
            except Exception:
                pass
        if not logged_in:
            logger.warning(f"[PendingLoop] not logged in on {pid}")
            self._broadcaster.publish("login_required", {"provider_id": pid})
            return

        # 3. Sync history
        try:
            raw_history = await workflow.sync_history(page)
        except Exception:
            logger.exception(f"[PendingLoop] sync_history failed for {pid}")
            return

        history = [
            {
                "odds": e.odds,
                "stake": e.stake,
                "status": e.status,
                "payout": e.payout,
                "provider_bet_id": e.provider_bet_id,
                "event_name": e.event_name,
            }
            for e in raw_history
        ]

        # 4. Reconcile DB against provider truth (autonomous — DB self-heals)
        from .reconcile import reconcile_and_publish

        n = await reconcile_and_publish(
            self._proxy_url,
            _AUTH_HEADER,
            _AUTH_VALUE,
            pid,
            db_bets,
            history,
            self._broadcaster,
            page=page,
            workflow=workflow,
        )
        self._status[pid]["last_sync"] = datetime.utcnow().isoformat()
        self._status[pid]["reconciled"] = n

        if n == 0:
            logger.info(f"[PendingLoop] no reconciliation needed for {pid}")
        else:
            logger.info(f"[PendingLoop] reconciled {n} bets for {pid}")

        # 5. Record any pending bets that exist on the provider but not in DB.
        # Without this, manually-placed bets (or bets placed before the mirror
        # came up) never surface in the UI's PENDING section because reconcile
        # only updates EXISTING DB bets. The play_loop runner has the same
        # logic but only runs during active play sessions — the user wants
        # spelklubben's 7 historical pending bets visible just from having
        # the tab open.
        await self._record_unknown_open_bets(pid, history, db_bets)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _record_settlements(self, pid: str, settlements: list[dict]) -> None:
        from local.http_client import tunnel_client

        batch = [
            {"bet_id": s["bet_id"], "result": s["result"]}
            for s in settlements
            if s.get("bet_id") and s.get("result")
        ]
        if not batch:
            logger.info(f"[PendingLoop] no valid settlements to record for {pid}")
            return
        try:
            client = tunnel_client()
            resp = await client.post(
                "/api/opportunities/play/settle-batch", json=batch, timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                f"[PendingLoop] settlements recorded for {pid}: {data.get('settled', 0)}/{data.get('total', 0)}"
            )
        except Exception:
            logger.exception(f"[PendingLoop] failed to record settlements for {pid}")

    async def _post_balance(self, pid: str, balance: float) -> None:
        from local.http_client import tunnel_client

        try:
            client = tunnel_client()
            resp = await client.post(
                f"/api/bankroll/set/{pid}", json={"balance": balance}, timeout=15.0
            )
            resp.raise_for_status()
            logger.info(f"[PendingLoop] balance posted for {pid}: {balance}")
        except Exception:
            logger.warning(f"[PendingLoop] failed to post balance for {pid}")

    # ------------------------------------------------------------------
    # Unknown-open-bet recording
    # ------------------------------------------------------------------

    async def _record_unknown_open_bets(
        self, provider_id: str, history: list[dict], db_pending: list[dict] | None
    ) -> int:
        """Insert pending bets that exist on the provider but not in the DB.

        Returns the count of newly-inserted rows so callers can broadcast an
        accurate `recorded` field on `settling_done` (was always 0 pre-fix).

        Mirrors provider_runner._record_unknown_open_bets but runs from the
        passive PendingLoop so unknown bets surface even without an active
        play session. Match key is (odds, stake) rounded — same as runner
        side to keep the two paths consistent (don't double-insert).

        FAIL-CLOSED: db_pending is None means the caller's pending-bets fetch
        failed and we have NO idea what's already in the DB. Inserting blindly
        re-records every open bet as a duplicate — abort instead. The next
        sync (with a working fetch) recovers any genuinely-missing bet.
        """
        if db_pending is None:
            logger.warning(
                f"[PendingLoop] _record_unknown_open_bets({provider_id}) aborted — "
                "db_pending unknown (fetch failed); refusing to insert (would create duplicates)"
            )
            return 0
        # Build dedup sets from existing DB rows + cluster siblings:
        # - known_pids: set of provider_bet_id strings already tracked
        # - known_sigs: count of (odds, stake) signatures in DB. We dedup
        #   against COUNTS, not presence — so the user can have 2 identical
        #   bets and we record both, but a single bet that appears 5x in
        #   paginated history (Betinia returns 5 pages, same row each time)
        #   only inserts once.
        from collections import Counter

        from .play_loop import _CLUSTER_MEMBERS, _PROVIDER_TO_CLUSTER

        known_pids: set[str] = set()
        known_sigs: Counter[tuple[float, float]] = Counter()

        def _sig(b: dict) -> tuple[float, float]:
            return (
                round(float(b.get("odds", 0) or 0), 2),
                round(float(b.get("stake", 0) or 0), 1),
            )

        for b in db_pending:
            pid_id = str(b.get("provider_bet_id") or "")
            if pid_id:
                known_pids.add(pid_id)
            known_sigs[_sig(b)] += 1

        cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
        if cluster:
            for sibling in _CLUSTER_MEMBERS.get(cluster, []):
                if sibling == provider_id:
                    continue
                sibling_bets = await self._fetch_pending_for_provider(sibling)
                # A sibling holds cluster-shared odds — its pending bets dedup
                # against ours. If we can't read a sibling's state, abort:
                # inserting could duplicate a bet the sibling already tracks.
                if sibling_bets is None:
                    logger.warning(
                        f"[PendingLoop] _record_unknown_open_bets({provider_id}) aborted — "
                        f"sibling {sibling} fetch failed; refusing to insert"
                    )
                    return 0
                for b in sibling_bets:
                    pid_id = str(b.get("provider_bet_id") or "")
                    if pid_id:
                        known_pids.add(pid_id)
                    known_sigs[_sig(b)] += 1

        # Dedup the incoming history first. Provider paginations often return
        # the same row across multiple pages; without this we'd insert N
        # duplicates per real bet (the BETINIA × 22 bug).
        seen_in_history: set[tuple[str, tuple[float, float]]] = set()

        recorded = 0
        for entry in history:
            if (entry.get("status") or "").lower() != "pending":
                continue
            pid_id = str(entry.get("provider_bet_id") or "")
            sig = _sig(entry)

            # Skip if this exact history row was already processed in this batch
            # (pagination overlap).
            history_key = (pid_id, sig)
            if history_key in seen_in_history:
                continue
            seen_in_history.add(history_key)

            # Skip if already in DB by provider_bet_id (exact match)
            if pid_id and pid_id in known_pids:
                continue

            # Skip if a DB row already matches signature AND we haven't already
            # claimed every slot for that signature.
            if known_sigs[sig] > 0:
                known_sigs[sig] -= 1
                continue

            # Try to inherit event_id/market/outcome/start_time from the
            # user's picked opp (set by /mirror/arb/navigate-opp). Without
            # this the manually-recovered bet has empty event_id (blacklist
            # can't match) and null start_time (pending row can't show
            # "starts HH:MM" or ready-to-settle pill).
            picked = (getattr(self._browser, "_user_picked_opp", {}) or {}).get(
                provider_id
            ) or {}
            picked_event_id = picked.get("event_id") or ""
            picked_market = picked.get("market") or ""
            picked_outcome = picked.get("outcome") or ""
            picked_start_time = picked.get("start_time")
            # Infer bet_type from provider role. Polymarket / Kalshi are always
            # counter legs in this stack (arb hedges against soft books); soft
            # books recovered via reactive history are tagged "mirror" — a
            # recognized type that bypasses the server-side edge gate (since
            # the user already accepted the price on the provider's site).
            # Without an explicit type these rows landed as NULL and dropped
            # out of every stats / arb-correlation view.
            inferred_bet_type = (
                "arb_counter" if provider_id in ("polymarket", "kalshi") else "mirror"
            )
            payload = {
                "event_id": picked_event_id,
                "provider_id": provider_id,
                "market": picked_market or entry.get("market", ""),
                "outcome": picked_outcome or entry.get("outcome", ""),
                "odds": entry.get("odds", 0),
                "stake": entry.get("stake", 0),
                "is_bonus": False,
                "bet_type": inferred_bet_type,
                "provider_bet_id": pid_id or None,
                # Free-text event name → boost_event field. UI uses this when
                # home_team/away_team are null (no Event row joined).
                "boost_event": entry.get("event_name") or None,
                "start_time": picked_start_time,
                # Skip balance check — the bookmaker already accepted these
                # bets. Without this flag, recording fails with 400
                # "Insufficient balance" whenever the user has drained their
                # provider balance below a placed bet's stake.
                "external_placement": True,
            }
            try:
                from local.http_client import tunnel_client

                resp = await tunnel_client().post(
                    "/api/bets", json=payload, timeout=10.0
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info(
                    f"[PendingLoop] Recorded unknown open bet for {provider_id}: "
                    f"{entry.get('event_name')} {entry.get('outcome')} "
                    f"@ {entry.get('odds')} stake={entry.get('stake')} → bet #{data.get('bet_id', '?')}"
                )
                recorded += 1
                # Track the just-inserted bet so a subsequent history page in
                # this same call doesn't re-insert it.
                if pid_id:
                    known_pids.add(pid_id)
            except Exception as exc:
                # Include the actual error + response body (if any) to surface
                # validation failures from /api/bets. Without this every
                # failure looks identical and you can't tell whether it's
                # a schema mismatch, FK violation, or tunnel hiccup.
                body_preview = ""
                if hasattr(exc, "response") and exc.response is not None:
                    try:
                        body_preview = exc.response.text[:200]
                    except Exception:
                        body_preview = ""
                logger.warning(
                    f"[PendingLoop] Failed to record unknown bet for {provider_id}: "
                    f"{entry.get('event_name')} @ {entry.get('odds')} stake={entry.get('stake')} — "
                    f"{type(exc).__name__}: {exc!s}{(' | body=' + body_preview) if body_preview else ''}"
                )

        if recorded:
            self._broadcaster.publish(
                "unknown_bets_recorded",
                {"provider_id": provider_id, "count": recorded},
            )
        return recorded

    async def _fetch_pending_for_provider(self, provider_id: str) -> list[dict] | None:
        """Lookup currently-known pending bets for one provider.

        Returns None on fetch failure (tunnel error / timeout) — distinct from
        [] which means the provider genuinely has no pending bets. Callers MUST
        fail-closed on None: _record_unknown_open_bets inserts every history
        entry it can't find in db_pending, so a silent [] on failure made it
        re-insert every open bet as a duplicate (the BETINIA ×3 dup bug,
        2026-05-12 — 4 open bets became 12 DB rows across 3 failed syncs).
        """
        from local.http_client import tunnel_client

        try:
            resp = await tunnel_client().get(
                "/api/opportunities/play/pending-bets", timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(
                f"[PendingLoop] _fetch_pending_for_provider({provider_id}) failed: {e!r}"
            )
            return None
        for prov in data.get("providers", []):
            if prov.get("provider_id") == provider_id:
                return prov.get("bets", []) or []
        return []
