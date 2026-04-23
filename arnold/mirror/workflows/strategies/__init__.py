"""Strategy overrides for GenericWorkflow.

Each provider can optionally have a strategies/{provider_id}.py file
that exports a `strategy` attribute of type Strategy. Only methods
that need custom logic should be set — the rest use intel JSON.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """Optional per-provider method overrides.

    Each field is an async callable(page, intel) -> result, or None to use generic.
    """
    check_login: Callable | None = None
    sync_balance: Callable | None = None
    sync_history: Callable | None = None
    navigate_to_event: Callable | None = None
    prep_betslip: Callable | None = None
    place_bet: Callable | None = None
    check_live_price: Callable | None = None
    # Optional settlement extensions (Polymarket uses these for claim + redeem on-chain).
    # Provider runner delegates to the strategy when all three are present.
    scrape_portfolio: Callable | None = None  # (page, intel) -> list[dict] open positions
    claim_banner: Callable | None = None      # (page, intel) -> {claimed, amount}
    redeem_all: Callable | None = None        # (page, intel) -> {redeemed, skipped_open, errors, total}


def load_strategy(provider_id: str) -> Strategy | None:
    """Import strategies/{provider_id}.py if it exists, return .strategy attr."""
    try:
        mod = importlib.import_module(f"mirror.workflows.strategies.{provider_id}")
        return getattr(mod, "strategy", None)
    except ModuleNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"[generic] Failed to load strategy for {provider_id}: {e}")
        return None
