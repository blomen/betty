"""Workflow registry — maps provider_id to platform workflow class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_PLATFORM_MAP: dict[str, type[ProviderWorkflow]] | None = None
_WORKFLOW_CACHE: dict[str, ProviderWorkflow] = {}  # Cached instances per provider_id


def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .polymarket import PolymarketWorkflow
    from .pinnacle import PinnacleWorkflow
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .kambi import KambiWorkflow
    from .generic import GenericWorkflow
    return {
        "polymarket": PolymarketWorkflow,
        "pinnacle": PinnacleWorkflow,
        "altenar": AltenarWorkflow,
        "gecko_v2": GeckoWorkflow,
        "kambi": KambiWorkflow,
        "spectate": GenericWorkflow,
        "tenbet": GenericWorkflow,
        "snabbare": GenericWorkflow,
        "custom": GenericWorkflow,
        "betconstruct": GenericWorkflow,
        "interwetten": GenericWorkflow,
        "coolbet": GenericWorkflow,
        "tipwin": GenericWorkflow,
    }


_RETRIEVER_TO_PLATFORM = {
    "polymarket": "polymarket",
    "pinnacle": "pinnacle",
    "altenar": "altenar",
    "gecko_v2": "gecko_v2",
    "kambi": "kambi",
    "spectate": "spectate",
    "tenbet": "tenbet",
    "snabbare": "snabbare",
    "custom": "custom",
    "betconstruct": "betconstruct",
    "interwetten": "interwetten",
    "coolbet": "coolbet",
    "tipwin": "tipwin",
}


_FALLBACK_DOMAINS: dict[str, str] = {
    "polymarket": "polymarket.com",
    "pinnacle": "pinnacle.se",
    "betinia": "betinia.se",
    "quickcasino": "quickcasino.com",
    "campobet": "campobet.se",
    "comeon": "comeon.com",
    "swiper": "swiper.bet",
    "lodur": "lodurbet.com",
    "dbet": "dbet.com",
    "unibet": "unibet.se",
    "leovegas": "leovegas.se",
    "expekt": "expekt.se",
    "betmgm": "betmgm.se",
    "speedybet": "speedybet.com",
    "x3000": "x3000.se",
    "goldenbull": "goldenbull.se",
    "1x2": "1x2.se",
    "spelklubben": "spelklubben.com",
    "betsson": "betsson.se",
    "nordicbet": "nordicbet.com",
    "betsafe": "betsafe.se",
    "hajper": "hajper.com",
    "interwetten": "interwetten.se",
    "coolbet": "coolbet.com",
    "vbet": "vbet.com",
    "10bet": "10bet.com",
    "tipwin": "tipwin.se",
    "mrgreen": "mrgreen.com",
    "888sport": "888sport.com",
    "snabbare": "snabbare.com",
}


def get_workflow(provider_id: str) -> ProviderWorkflow:
    """Get the workflow instance for a provider. Cached per provider_id."""
    if provider_id in _WORKFLOW_CACHE:
        return _WORKFLOW_CACHE[provider_id]

    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = _load_platform_map()

    provider = None
    try:
        from ...config.loader import load_config
        cfg = load_config()
        provider = cfg.get_provider(provider_id)
    except ImportError:
        logger.warning(f"[workflows] config.loader not available — using platform map only")

    if provider is None:
        domain = _FALLBACK_DOMAINS.get(provider_id, "")
        if provider_id in _PLATFORM_MAP:
            instance = _PLATFORM_MAP[provider_id](provider_id=provider_id, domain=domain)
            _WORKFLOW_CACHE[provider_id] = instance
            return instance
        from .generic import GenericWorkflow
        instance = GenericWorkflow(provider_id=provider_id, domain=domain)
        _WORKFLOW_CACHE[provider_id] = instance
        return instance

    platform = _RETRIEVER_TO_PLATFORM.get(provider.retriever_type, provider.retriever_type)
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        from .generic import GenericWorkflow
        cls = GenericWorkflow

    domain = provider.domain or ""
    if not domain:
        domain = _FALLBACK_DOMAINS.get(provider_id, "")
    instance = cls(provider_id=provider_id, domain=domain)
    _WORKFLOW_CACHE[provider_id] = instance
    return instance


__all__ = [
    "ProviderWorkflow", "WorkflowMode", "PlacementResult", "HistoryEntry",
    "get_workflow",
]
