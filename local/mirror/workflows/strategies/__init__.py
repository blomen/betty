"""Strategy overrides for GenericWorkflow.

Each provider can optionally have a strategies/{provider_id}.py file
that exports a `strategy` attribute of type Strategy. Only methods
that need custom logic should be set — the rest use intel JSON.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """Optional per-provider method overrides.

    Most fields are async callables (page, intel) -> result. Slip/placement hooks
    (read_slip_odds, update_slip_stake, parse_placement_*) have custom signatures
    — see field comments for details. None to use generic.
    """

    check_login: Callable | None = None
    sync_balance: Callable | None = None
    fetch_balance: Callable | None = (
        None  # async (page, intel) -> float | None — passive ready-state refresh
    )
    sync_history: Callable | None = None
    navigate_to_event: Callable | None = None
    prep_betslip: Callable | None = None
    place_bet: Callable | None = None
    check_live_price: Callable | None = None
    # Optional settlement extensions (Polymarket uses these for claim + redeem on-chain).
    # Provider runner delegates to the strategy when all three are present.
    scrape_portfolio: Callable | None = (
        None  # (page, intel) -> list[dict] open positions
    )
    claim_banner: Callable | None = None  # (page, intel) -> {claimed, amount}
    redeem_all: Callable | None = (
        None  # (page, intel) -> {redeemed, skipped_open, errors, total}
    )
    # Optional account-level methods referenced by GenericWorkflow.scan / .settle_all.
    # Without these fields the dataclass would AttributeError on access.
    scan: Callable | None = None  # (page, intel) -> dict read-only account preview
    settle_all: Callable | None = None  # (page, intel) -> dict full settlement run
    # Slip + placement-XHR hooks consumed by ArbRunner / SlipOddsStream / provider_runner
    # placement interceptor. Async for read/write of slip state, sync for parsing
    # placement response bodies (no I/O).
    read_slip_odds: Callable | None = None  # async (page, intel) -> float | None
    update_slip_stake: Callable | None = None  # async (page, stake, intel) -> bool
    read_outcome_odds_dom: Callable | None = (
        None  # async (page, bet) -> float | None — page-state live odds (faster than check_live_price)
    )
    parse_placement_response: Callable | None = None  # sync (body) -> str | None
    parse_placement_status: Callable | None = None  # sync (body) -> dict
    # True for strategies whose sync_history is purely page.evaluate(fetch(...))
    # — no page.goto, no DOM clicks. Safe to background-poll even while the
    # user is on an event page; the call cannot clobber an open betslip.
    # Consumed by PendingLoop to bypass its event-page skip guard.
    sync_history_is_passive: bool = False


def load_strategy(provider_id: str) -> Strategy | None:
    """Import strategies/{provider_id}.py if it exists, return .strategy attr.

    Tries both `local.mirror.workflows.strategies.<id>` (repo-root sys.path,
    e.g. pytest) and `mirror.workflows.strategies.<id>` (betty.bat launcher
    that puts `local/` on sys.path directly).
    """
    for module_path in (
        f"local.mirror.workflows.strategies.{provider_id}",
        f"mirror.workflows.strategies.{provider_id}",
    ):
        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, "strategy", None)
        except ModuleNotFoundError:
            continue
        except Exception as e:
            logger.warning(f"[generic] Failed to load strategy for {provider_id}: {e}")
            return None
    return None
