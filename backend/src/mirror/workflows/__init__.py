"""Workflow registry — maps provider_id to platform workflow class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_PLATFORM_MAP: dict[str, type[ProviderWorkflow]] | None = None


def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .polymarket import PolymarketWorkflow
    from .pinnacle import PinnacleWorkflow
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .kambi import KambiWorkflow
    from .manual import ManualWorkflow
    return {
        "polymarket": PolymarketWorkflow,
        "pinnacle": PinnacleWorkflow,
        "altenar": AltenarWorkflow,
        "gecko_v2": GeckoWorkflow,
        "kambi": KambiWorkflow,
        "spectate": ManualWorkflow,
        "tenbet": ManualWorkflow,
        "snabbare": ManualWorkflow,
        "custom": ManualWorkflow,
        "betconstruct": ManualWorkflow,
        "interwetten": ManualWorkflow,
        "coolbet": ManualWorkflow,
        "tipwin": ManualWorkflow,
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


def get_workflow(provider_id: str) -> ProviderWorkflow:
    """Get the workflow instance for a provider."""
    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = _load_platform_map()

    from ...config.loader import load_config
    cfg = load_config()
    provider = cfg.get_provider(provider_id)

    if provider is None:
        if provider_id in _PLATFORM_MAP:
            domain = {"polymarket": "polymarket.com", "pinnacle": "pinnacle.com"}.get(provider_id, "")
            return _PLATFORM_MAP[provider_id](provider_id=provider_id, domain=domain)
        from .manual import ManualWorkflow
        return ManualWorkflow(provider_id=provider_id, domain="")

    platform = _RETRIEVER_TO_PLATFORM.get(provider.retriever_type, provider.retriever_type)
    cls = _PLATFORM_MAP.get(platform)
    if cls is None:
        from .manual import ManualWorkflow
        cls = ManualWorkflow

    domain = provider.domain or ""
    return cls(provider_id=provider_id, domain=domain)


__all__ = [
    "ProviderWorkflow", "WorkflowMode", "PlacementResult", "HistoryEntry",
    "get_workflow",
]
