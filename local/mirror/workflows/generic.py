"""GenericWorkflow — data-driven workflow for any provider using intel JSON + strategy overrides."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

logger = logging.getLogger(__name__)


def _default_intel_dir() -> Path:
    try:
        from ...paths import get_data_dir

        d = get_data_dir() / "mirror_intel"
    except ImportError:
        import os

        d = (
            Path(
                os.environ.get("BETTY_DATA_DIR")
                or os.environ.get(
                    "ARNOLD_DATA_DIR",
                    str(Path(__file__).parent.parent.parent.parent / "data"),
                )
            )
            / "mirror_intel"
        )
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_intel(provider_id: str, intel_dir: Path | None = None) -> dict | None:
    """Load intel JSON for a provider. Returns None if not found."""
    d = intel_dir or _default_intel_dir()
    path = d / f"{provider_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[generic] Failed to load intel for {provider_id}: {e}")
        return None


def save_intel(provider_id: str, intel: dict, intel_dir: Path | None = None) -> Path:
    """Save intel JSON for a provider. Returns path written."""
    d = intel_dir or _default_intel_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{provider_id}.json"
    path.write_text(json.dumps(intel, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[generic] Saved intel for {provider_id} → {path}")
    return path


def _extract_path(data: Any, path: str) -> Any:
    """Extract a value from nested dict using dot-separated path.

    Example: _extract_path({"data": {"balance": 123}}, "data.balance") -> 123
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


class GenericWorkflow(ProviderWorkflow):
    """Data-driven workflow that reads intel JSON + optional strategy overrides."""

    platform = "generic"

    def __init__(
        self,
        provider_id: str,
        domain: str,
        mode: WorkflowMode = WorkflowMode.GUIDED,
        intel_dir: Path | None = None,
    ):
        super().__init__(provider_id, domain, mode)
        self.intel = load_intel(provider_id, intel_dir)
        from .strategies import load_strategy

        self.strategy = load_strategy(provider_id)
        # Intel JSON may declare this provider as autonomous (API-based place_bet
        # called on user confirm instead of waiting for a placement interception).
        self.autonomous_placement = bool(
            (self.intel or {}).get("autonomous_placement", False)
        )
        # fetch_balance is OPTIONAL on the workflow surface — provider_runner /
        # arb_runner gate the ready-state passive refresh on
        # hasattr(workflow, "fetch_balance"), so we only expose it when the
        # strategy actually defines it. Keeps the no-op fallback off for
        # workflows that don't want a 60s background refresh.
        if self.strategy and self.strategy.fetch_balance:
            self.fetch_balance = self._fetch_balance  # type: ignore[method-assign]

    @property
    def home_url(self) -> str:
        """Intel JSON `home_url` overrides the default `https://{domain}`.

        Generic providers like Kalshi land on /portfolio (not /) so balance +
        positions calls fire on tab open without an extra navigation.
        """
        url = (self.intel or {}).get("home_url")
        if isinstance(url, str) and url.startswith("http"):
            return url
        return super().home_url

    async def _fetch_balance(self, page: Page) -> float | None:
        if self.strategy and self.strategy.fetch_balance:
            return await self.strategy.fetch_balance(page, self.intel)
        return None

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        if self.strategy and self.strategy.check_login:
            return await self.strategy.check_login(page, self.intel)

        if not self.intel or not self.intel.get("login"):
            # No intel — try balance check as fallback
            try:
                bal = await self.sync_balance(page)
                return bal > 0
            except Exception:
                return False

        login = self.intel["login"]
        if login["method"] == "balance_api":
            bal = await self.sync_balance(page)
            return bal > 0

        if login["method"] == "dom":
            indicator = login.get("indicator", {})
            selector = indicator.get("selector")
            if selector:
                el = await page.query_selector(selector)
                return el is not None

        return True

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def sync_balance(self, page: Page) -> float:
        if self.strategy and self.strategy.sync_balance:
            return await self.strategy.sync_balance(page, self.intel)

        if not self.intel or not self.intel.get("balance"):
            return -1.0

        bal = self.intel["balance"]

        if bal["method"] == "api" and bal.get("api"):
            api = bal["api"]
            data = await self._evaluate_api(page, api["url"])
            if data is None or "__error" in (data or {}):
                return -1.0
            val = _extract_path(data, api["path"])
            try:
                return float(val) * api.get("multiplier", 1.0)
            except (TypeError, ValueError):
                logger.warning(f"[{self.provider_id}] Cannot parse balance: {val}")
                return -1.0

        if bal["method"] == "dom" and bal.get("dom"):
            dom = bal["dom"]
            el = await page.query_selector(dom["selector"])
            if not el:
                return -1.0
            text = await el.text_content()
            if not text:
                return -1.0
            pattern = dom.get("regex", r"[\d.,]+")
            match = re.search(pattern, text)
            if not match:
                return -1.0
            try:
                cleaned = match.group().replace(",", "").replace(" ", "")
                return float(cleaned) * dom.get("multiplier", 1.0)
            except ValueError:
                return -1.0

        return -1.0

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        if self.strategy and self.strategy.sync_history:
            return await self.strategy.sync_history(page, self.intel)

        if not self.intel or not self.intel.get("history"):
            return []

        hist = self.intel["history"]

        nav = self.intel.get("navigation", {})
        history_path = (nav or {}).get("history_path") or hist.get("url")
        if history_path:
            current = page.url or ""
            if history_path not in current:
                # Passive: don't auto-navigate to the history page. The mirror
                # only navigates automatically for arb event clicks; everything
                # else (balance, history, settle) is read off whatever page the
                # user has open. When they land on history themselves, the next
                # 60s PendingLoop tick scrapes + records.
                logger.debug(
                    f"[{self.provider_id}] sync_history: tab on {current[:60]} (history_path={history_path}) — skipping"
                )
                return []

        if hist["method"] == "api" and hist.get("api"):
            return await self._sync_history_api(page, hist["api"])

        if hist["method"] == "dom" and hist.get("dom"):
            return await self._sync_history_dom(page, hist["dom"])

        return []

    async def _sync_history_api(self, page: Page, api_cfg: dict) -> list[HistoryEntry]:
        endpoint = api_cfg.get("endpoint", "")
        data = await self._evaluate_api(page, endpoint)
        if not data or "__error" in (data or {}):
            return []

        mapping = api_cfg.get("mapping", {})
        status_map = mapping.get("status_map", {})

        bets_data = data
        if isinstance(data, dict):
            for key in ("bets", "items", "results", "data", "coupons"):
                if key in data:
                    bets_data = data[key]
                    break

        if not isinstance(bets_data, list):
            return []

        entries = []
        for bet in bets_data:
            try:
                raw_status = str(
                    _extract_path(bet, mapping.get("status", "status")) or ""
                )
                status = status_map.get(raw_status, raw_status)
                payout_val = _extract_path(bet, mapping.get("payout", "payout"))
                entries.append(
                    HistoryEntry(
                        provider_bet_id=str(
                            _extract_path(bet, mapping.get("bet_id", "id")) or ""
                        ),
                        event_name=str(
                            _extract_path(bet, mapping.get("event_name", "event")) or ""
                        ),
                        market="",
                        outcome="",
                        odds=float(
                            _extract_path(bet, mapping.get("odds", "odds")) or 0
                        ),
                        stake=float(
                            _extract_path(bet, mapping.get("stake", "stake")) or 0
                        ),
                        status=status,
                        payout=float(payout_val) if payout_val else None,
                    )
                )
            except (TypeError, ValueError, KeyError) as e:
                logger.debug(
                    f"[{self.provider_id}] Skip unparseable history entry: {e}"
                )
        return entries

    async def _sync_history_dom(self, page: Page, dom_cfg: dict) -> list[HistoryEntry]:
        container_sel = dom_cfg.get("container", "body")
        row_sel = dom_cfg.get("row_selector", "")
        fields = dom_cfg.get("fields", {})

        if not row_sel:
            return []

        rows = await page.query_selector_all(f"{container_sel} {row_sel}")
        entries = []
        for row in rows:
            try:
                entry = HistoryEntry(
                    provider_bet_id="",
                    event_name=await self._extract_dom_field(
                        row, fields.get("event_name", {})
                    ),
                    market="",
                    outcome="",
                    odds=float(
                        await self._extract_dom_field(row, fields.get("odds", {})) or 0
                    ),
                    stake=float(
                        await self._extract_dom_field(row, fields.get("stake", {})) or 0
                    ),
                    status=await self._extract_dom_status(
                        row, fields.get("status", {})
                    ),
                    payout=float(
                        await self._extract_dom_field(row, fields.get("payout", {}))
                        or 0
                    )
                    or None,
                )
                if entry.odds > 0 and entry.stake > 0:
                    entries.append(entry)
            except (TypeError, ValueError) as e:
                logger.debug(f"[{self.provider_id}] Skip unparseable DOM row: {e}")
        return entries

    async def _extract_dom_field(self, row, field_cfg: dict) -> str:
        if not field_cfg:
            return ""
        selector = field_cfg.get("selector", "")
        if not selector:
            return ""
        el = await row.query_selector(selector)
        if not el:
            return ""
        text = (await el.text_content() or "").strip()
        pattern = field_cfg.get("regex")
        if pattern:
            match = re.search(pattern, text)
            return match.group() if match else ""
        return text

    async def _extract_dom_status(self, row, field_cfg: dict) -> str:
        raw = await self._extract_dom_field(row, field_cfg)
        text_map = field_cfg.get("text_map", {})
        return text_map.get(raw, raw.lower())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        if self.strategy and self.strategy.navigate_to_event:
            return await self.strategy.navigate_to_event(page, bet, self.intel)

        if not self.intel or not self.intel.get("navigation"):
            logger.info(
                f"[{self.provider_id}] No navigation intel — user navigates manually"
            )
            return True

        nav = self.intel["navigation"]
        template = nav.get("event_url_template")
        if not template:
            return True

        def _g(attr: str) -> str:
            if isinstance(bet, dict):
                val = bet.get(attr)
                if val is None:
                    val = (bet.get("provider_meta") or {}).get(attr, "")
            else:
                val = getattr(bet, attr, None)
                if val is None:
                    meta = getattr(bet, "provider_meta", None) or {}
                    if isinstance(meta, dict):
                        val = meta.get(attr, "")
            return str(val or "")

        url = template
        for key in (
            "event_id",
            "provider_event_id",
            "matchup_id",
            "event_slug",
            "market_slug",
            "slug",
        ):
            url = url.replace(f"{{{key}}}", _g(key))
        if not url.startswith("http"):
            url = f"https://{self.domain}{url}"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            logger.info(f"[{self.provider_id}] Navigated to {url}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Navigate failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Betslip prep (outcome click + stake fill) — strategy override only
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        if self.strategy and self.strategy.prep_betslip:
            return await self.strategy.prep_betslip(page, bet, stake, self.intel)
        return PlacementResult(status="no_prep", bet_id=0, reason="not_implemented")

    # ------------------------------------------------------------------
    # Placement — always guided
    # ------------------------------------------------------------------

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        if self.strategy and self.strategy.place_bet:
            return await self.strategy.place_bet(page, bet, stake, self.intel)

        if not self.intel or not self.intel.get("betslip"):
            return PlacementResult(
                status="manual",
                bet_id=bet.bet_id,
                actual_stake=stake,
                reason="no_betslip_intel",
            )

        bs = self.intel["betslip"]

        stake_sel = bs.get("stake_input", "")
        if stake_sel:
            try:
                input_el = await page.query_selector(stake_sel)
                if input_el:
                    await input_el.fill("")
                    await input_el.fill(f"{stake:.2f}")
                    logger.info(f"[{self.provider_id}] Stake filled: {stake:.2f}")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Cannot fill stake: {e}")

        confirm_sel = bs.get("confirm_button", "")
        if confirm_sel:
            try:
                await page.evaluate(f"""
                    () => {{
                        const btn = document.querySelector('{confirm_sel}');
                        if (btn) {{
                            btn.style.outline = '3px solid #ff6600';
                            btn.style.outlineOffset = '2px';
                        }}
                    }}
                """)
            except Exception:
                pass

        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="generic_guided_user_confirms",
        )

    # ------------------------------------------------------------------
    # Live price (optional)
    # ------------------------------------------------------------------

    async def check_live_price(
        self, page: Page, bet
    ) -> tuple[float | None, float | None]:
        if self.strategy and self.strategy.check_live_price:
            result = await self.strategy.check_live_price(page, bet, self.intel)
            # Strategies may return (odds, edge) tuple or bare edge float — normalise
            if isinstance(result, tuple):
                return result
            return None, result
        return None, None

    # ------------------------------------------------------------------
    # Slip read/write — consumed by ArbRunner + SlipOddsStream
    # ------------------------------------------------------------------

    async def read_slip_odds(self, page: Page) -> float | None:
        if self.strategy and self.strategy.read_slip_odds:
            return await self.strategy.read_slip_odds(page, self.intel)
        return await super().read_slip_odds(page)

    async def read_outcome_odds_dom(self, page: Page, bet) -> float | None:
        """Provider-specific live-odds reader. Used by /mirror/arb/navigate-opp's
        poll task as the preferred drift signal — faster than check_live_price
        and more accurate than read_slip_odds (which can lock at click time)."""
        if self.strategy and self.strategy.read_outcome_odds_dom:
            return await self.strategy.read_outcome_odds_dom(page, bet)
        return None

    async def update_slip_stake(self, page: Page, stake: float) -> bool:
        if self.strategy and self.strategy.update_slip_stake:
            return await self.strategy.update_slip_stake(page, stake, self.intel)
        return await super().update_slip_stake(page, stake)

    # ------------------------------------------------------------------
    # Placement response parsing — called by browser placement interceptor
    # ------------------------------------------------------------------

    def parse_placement_response(self, body: dict) -> str | None:
        if self.strategy and self.strategy.parse_placement_response:
            return self.strategy.parse_placement_response(body)
        return super().parse_placement_response(body)

    def parse_placement_status(self, body: dict) -> dict:
        if self.strategy and self.strategy.parse_placement_status:
            return self.strategy.parse_placement_status(body)
        return super().parse_placement_status(body)

    # ------------------------------------------------------------------
    # Scan — read-only account state preview
    # ------------------------------------------------------------------

    async def scan(self, page: Page) -> dict:
        """Read-only preview: balance, pending bets, settled bets, DB diff."""
        if self.strategy and self.strategy.scan:
            return await self.strategy.scan(page, self.intel)
        return {"error": f"No scan implementation for {self.provider_id}"}

    # ------------------------------------------------------------------
    # Settle all — scrape pending + auto-settle + sync balance
    # ------------------------------------------------------------------

    async def settle_all(self, page: Page) -> dict:
        """Full settlement: record missing bets, auto-settle, sync balance."""
        if self.strategy and self.strategy.settle_all:
            return await self.strategy.settle_all(page, self.intel)

        # Fallback: use sync_history + _settle_from_history
        history = await self.sync_history(page)
        if not history:
            return {"settled": 0, "note": "no history entries found"}

        try:
            from ...services.fire_window import _settle_from_history

            count = _settle_from_history(self.provider_id, history)
        except ImportError:
            logger.warning(
                f"[{self.provider_id}] _settle_from_history not available (backend not installed)"
            )
            count = 0
        balance = await self.sync_balance(page)
        return {"settled": count, "new_balance": balance}

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    async def auto_discover(self, page: Page) -> bool:
        """Run discovery if no intel exists. Called on first provider detection."""
        if self.intel is not None:
            return True

        from .discovery import discover

        try:
            self.intel = await discover(page, self.provider_id)
            logger.info(
                f"[{self.provider_id}] Auto-discovery complete: {self.intel.get('capabilities', {})}"
            )
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Auto-discovery failed: {e}")
            return False
