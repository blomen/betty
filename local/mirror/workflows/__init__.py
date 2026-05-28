"""Workflow registry — maps provider_id to platform workflow class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import (
    HistoryEntry,
    PlacementResult,
    PositionEntry,
    ProviderWorkflow,
    WorkflowMode,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_PLATFORM_MAP: dict[str, type[ProviderWorkflow]] | None = None
_WORKFLOW_CACHE: dict[str, ProviderWorkflow] = {}  # Cached instances per provider_id


def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    # Polymarket + Pinnacle + Kalshi migrated to data/mirror_intel/ + strategies/
    # and are routed via GenericWorkflow in get_workflow() ahead of this map.
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .generic import GenericWorkflow
    from .kambi import KambiWorkflow

    return {
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

# Fallback: provider_id → platform (when config.loader is unavailable in arnoldsports)
_PROVIDER_TO_PLATFORM: dict[str, str] = {
    # Altenar
    "betinia": "altenar",
    "campobet": "altenar",
    "quickcasino": "altenar",
    "swiper": "altenar",
    "lodur": "altenar",
    "dbet": "altenar",
    # Gecko V2
    "betsson": "gecko_v2",
    "nordicbet": "gecko_v2",
    "betsafe": "gecko_v2",
    "spelklubben": "gecko_v2",
    # Kambi
    "unibet": "kambi",
    "leovegas": "kambi",
    "expekt": "kambi",
    "888sport": "kambi",
    "speedybet": "kambi",
    "x3000": "kambi",
    "goldenbull": "kambi",
    "1x2": "kambi",
    "betmgm": "kambi",
    "mrgreen": "kambi",
}


_FALLBACK_DOMAINS: dict[str, str] = {
    "polymarket": "polymarket.com",
    "pinnacle": "pinnacle.se",
    "betinia": "betinia.se",
    "quickcasino": "quickcasino.se",
    "campobet": "campobet.se",
    "comeon": "comeon.com",
    "swiper": "swiper.se",
    "lodur": "lodur.se",
    "dbet": "dbet.com",
    "unibet": "unibet.se",
    "leovegas": "leovegas.com",
    "expekt": "expekt.se",
    "betmgm": "betmgm.se",
    "speedybet": "speedybet.com",
    "x3000": "x3000.se",
    "goldenbull": "goldenbull.se",
    "1x2": "1x2.se",
    "spelklubben": "spelklubben.se",
    "betsson": "betsson.com",
    "nordicbet": "nordicbet.com",
    "betsafe": "betsafe.com",
    "hajper": "hajper.com",
    "coolbet": "coolbet.com",
    "vbet": "vbet.com",
    "10bet": "10bet.com",
    "tipwin": "tipwin.se",
    "mrgreen": "mrgreen.com",
    "888sport": "888sport.com",
    "snabbare": "snabbare.com",
    "kalshi": "kalshi.com",
    "cloudbet": "cloudbet.com",
}


def get_workflow(provider_id: str) -> ProviderWorkflow:
    """Get the workflow instance for a provider. Cached per provider_id.

    If an intel JSON exists at data/mirror_intel/{provider_id}.json, route to
    GenericWorkflow — lets us migrate providers off dedicated classes one by one.
    """
    if provider_id in _WORKFLOW_CACHE:
        return _WORKFLOW_CACHE[provider_id]

    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = _load_platform_map()

    from .generic import GenericWorkflow, load_intel

    # Providers with an explicit dedicated class in _PROVIDER_TO_PLATFORM take
    # precedence over the intel-JSON → GenericWorkflow shortcut. Lets us
    # register a purpose-built workflow even when a mirror_intel JSON also
    # exists for that provider.
    _explicit_platform = _PROVIDER_TO_PLATFORM.get(provider_id)
    if _explicit_platform and _explicit_platform in _PLATFORM_MAP:
        domain = ""
        try:
            from ...config.loader import load_config

            p = load_config().get_provider(provider_id)
            if p and p.domain:
                domain = p.domain
        except ImportError:
            pass
        if not domain:
            domain = _FALLBACK_DOMAINS.get(provider_id, "")
        instance = _PLATFORM_MAP[_explicit_platform](
            provider_id=provider_id, domain=domain
        )
        _WORKFLOW_CACHE[provider_id] = instance
        logger.info(
            f"[workflows] {provider_id} → {type(instance).__name__} (explicit platform map)"
        )
        return instance

    if load_intel(provider_id) is not None:
        domain = _FALLBACK_DOMAINS.get(provider_id, "")
        if not domain:
            try:
                from ...config.loader import load_config

                p = load_config().get_provider(provider_id)
                if p and p.domain:
                    domain = p.domain
            except ImportError:
                pass
        instance = GenericWorkflow(provider_id=provider_id, domain=domain)
        _WORKFLOW_CACHE[provider_id] = instance
        logger.info(f"[workflows] {provider_id} → GenericWorkflow (intel JSON present)")
        return instance

    provider = None
    try:
        from ...config.loader import load_config

        cfg = load_config()
        provider = cfg.get_provider(provider_id)
    except ImportError:
        logger.warning(
            "[workflows] config.loader not available — using platform map only"
        )

    if provider is None:
        domain = _FALLBACK_DOMAINS.get(provider_id, "")
        # Check platform map directly (platform names like "altenar", "kambi")
        if provider_id in _PLATFORM_MAP:
            instance = _PLATFORM_MAP[provider_id](
                provider_id=provider_id, domain=domain
            )
            _WORKFLOW_CACHE[provider_id] = instance
            return instance
        # Resolve provider → platform (e.g. "betinia" → "altenar")
        platform = _PROVIDER_TO_PLATFORM.get(provider_id)
        if platform and platform in _PLATFORM_MAP:
            instance = _PLATFORM_MAP[platform](provider_id=provider_id, domain=domain)
            _WORKFLOW_CACHE[provider_id] = instance
            return instance
        from .generic import GenericWorkflow

        instance = GenericWorkflow(provider_id=provider_id, domain=domain)
        _WORKFLOW_CACHE[provider_id] = instance
        return instance

    platform = _RETRIEVER_TO_PLATFORM.get(
        provider.retriever_type, provider.retriever_type
    )
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
    "ProviderWorkflow",
    "WorkflowMode",
    "PlacementResult",
    "HistoryEntry",
    "PositionEntry",
    "get_workflow",
]
