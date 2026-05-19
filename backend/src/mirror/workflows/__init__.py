"""Workflow registry — maps provider_id to platform workflow class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, PositionEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_PLATFORM_MAP: dict[str, type[ProviderWorkflow]] | None = None
_WORKFLOW_CACHE: dict[str, ProviderWorkflow] = {}  # Cached instances per provider_id


def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .generic import GenericWorkflow
    from .kambi import KambiWorkflow
    from .pinnacle import PinnacleWorkflow
    from .polymarket import PolymarketWorkflow

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
    "coolbet": "coolbet",
    "tipwin": "tipwin",
    "kalshi": "kalshi",
}


def get_workflow(provider_id: str) -> ProviderWorkflow:
    """Get the workflow instance for a provider. Cached per provider_id."""
    if provider_id in _WORKFLOW_CACHE:
        return _WORKFLOW_CACHE[provider_id]

    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = _load_platform_map()

    from ...config.loader import load_config

    cfg = load_config()
    provider = cfg.get_provider(provider_id)

    if provider is None:
        if provider_id in _PLATFORM_MAP:
            domain = {"polymarket": "polymarket.com", "pinnacle": "pinnacle.se", "kalshi": "kalshi.com"}.get(
                provider_id, ""
            )
            instance = _PLATFORM_MAP[provider_id](provider_id=provider_id, domain=domain)
            _WORKFLOW_CACHE[provider_id] = instance
            return instance
        from .generic import GenericWorkflow

        instance = GenericWorkflow(provider_id=provider_id, domain="")
        _WORKFLOW_CACHE[provider_id] = instance
        return instance

    platform = _RETRIEVER_TO_PLATFORM.get(provider.retriever_type, provider.retriever_type)
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        from .generic import GenericWorkflow

        cls = GenericWorkflow

    domain = provider.domain or ""
    # Fallback domains for providers without explicit domain in config
    if not domain:
        domain = {"polymarket": "polymarket.com", "pinnacle": "pinnacle.se", "kalshi": "kalshi.com"}.get(
            provider_id, ""
        )
    instance = cls(provider_id=provider_id, domain=domain)
    _WORKFLOW_CACHE[provider_id] = instance
    return instance


__all__ = [
    "ProviderWorkflow",
    "WorkflowMode",
    "PlacementResult",
    "HistoryEntry",
    "PositionEntry",
    "get_workflow",
]
