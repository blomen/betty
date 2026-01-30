"""
Provider Pool Manager

Type-aware concurrency control for provider extraction.
Groups providers by shared resources (API backend, browser) to prevent
rate limits and resource contention while maximizing throughput.
"""

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from ..config.loader import OrchestratorConfig, ProviderConfig, ProviderGroupConfig

logger = logging.getLogger(__name__)


class ProviderPoolManager:
    """
    Type-aware concurrency control for provider extraction.

    Groups providers by their retriever_type and applies per-group
    concurrency limits to prevent rate limiting on shared backends
    (e.g., Kambi API) and browser resource contention.

    Usage:
        pool = ProviderPoolManager(config, provider_configs)

        # Get optimal provider order for type mixing
        ordered = pool.get_interleaved_order(providers)

        # Acquire slots before extraction
        async with pool.acquire(provider_id):
            await extract_provider(provider_id)
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        provider_configs: Dict[str, ProviderConfig]
    ):
        """
        Initialize pool manager.

        Args:
            config: Orchestrator configuration with provider_groups
            provider_configs: Dictionary of provider ID -> ProviderConfig
        """
        self._config = config
        self._group_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._browser_semaphore = asyncio.Semaphore(config.max_browser_instances)
        self._provider_to_group: Dict[str, ProviderGroupConfig] = {}
        self._groups: Dict[str, ProviderGroupConfig] = {}

        # Build group lookup
        for group in config.provider_groups:
            self._groups[group.name] = group
            self._group_semaphores[group.name] = asyncio.Semaphore(group.max_concurrent)

        # Map providers to groups based on retriever_type
        for pid, pconfig in provider_configs.items():
            retriever_type = pconfig.retriever_type
            for group in config.provider_groups:
                if retriever_type in group.retriever_types:
                    self._provider_to_group[pid] = group
                    logger.debug(f"Mapped provider {pid} ({retriever_type}) to group {group.name}")
                    break

        # Log configuration summary
        grouped_count = len(self._provider_to_group)
        total_count = len(provider_configs)
        logger.info(
            f"[PoolManager] Initialized with {len(config.provider_groups)} groups, "
            f"{grouped_count}/{total_count} providers mapped, "
            f"max_browser_instances={config.max_browser_instances}"
        )

    def get_group(self, provider_id: str) -> Optional[ProviderGroupConfig]:
        """Get the group configuration for a provider."""
        return self._provider_to_group.get(provider_id)

    def get_health_check_delay(self, provider_id: str) -> float:
        """
        Get health check delay for a provider (in seconds).

        Args:
            provider_id: Provider identifier

        Returns:
            Delay in seconds (0 if no delay configured)
        """
        group = self._provider_to_group.get(provider_id)
        if group and group.health_check_delay_ms > 0:
            return group.health_check_delay_ms / 1000.0
        return 0.0

    @asynccontextmanager
    async def acquire(self, provider_id: str):
        """
        Acquire resource slots for provider extraction.

        Respects both group-level concurrency limits and global browser limits.
        Providers not in any group run without restrictions (except global limits).

        Args:
            provider_id: Provider identifier

        Yields:
            None (context manager for slot acquisition)
        """
        group = self._provider_to_group.get(provider_id)

        if group:
            group_semaphore = self._group_semaphores[group.name]

            # Acquire group semaphore first
            async with group_semaphore:
                # If group uses browser, also acquire browser semaphore
                if group.shared_resource == "browser":
                    async with self._browser_semaphore:
                        logger.debug(
                            f"[{provider_id}] Acquired group={group.name} + browser slot"
                        )
                        yield
                        # Apply post-extraction delay before releasing slot
                        if group.post_extraction_delay_ms > 0:
                            delay_sec = group.post_extraction_delay_ms / 1000.0
                            logger.debug(f"[{provider_id}] Post-extraction delay: {delay_sec}s")
                            await asyncio.sleep(delay_sec)
                        logger.debug(
                            f"[{provider_id}] Released group={group.name} + browser slot"
                        )
                else:
                    logger.debug(f"[{provider_id}] Acquired group={group.name} slot")
                    yield
                    # Apply post-extraction delay before releasing slot
                    if group.post_extraction_delay_ms > 0:
                        delay_sec = group.post_extraction_delay_ms / 1000.0
                        logger.debug(f"[{provider_id}] Post-extraction delay: {delay_sec}s")
                        await asyncio.sleep(delay_sec)
                    logger.debug(f"[{provider_id}] Released group={group.name} slot")
        else:
            # No group restriction - run freely
            logger.debug(f"[{provider_id}] No group restriction, running freely")
            yield

    def get_interleaved_order(self, providers: List[str]) -> List[str]:
        """
        Order providers to maximize type mixing for optimal concurrency.

        Round-robin across groups ensures different backend types
        start together, maximizing parallel execution without hitting
        per-group limits early.

        Args:
            providers: List of provider IDs

        Returns:
            Reordered provider list for optimal type mixing

        Example:
            Input:  [kambi1, kambi2, kambi3, gecko1, gecko2, pinnacle]
            Output: [kambi1, gecko1, pinnacle, kambi2, gecko2, kambi3]
        """
        # Group providers by their group name
        by_group: Dict[str, List[str]] = defaultdict(list)

        for pid in providers:
            group = self._provider_to_group.get(pid)
            group_name = group.name if group else "ungrouped"
            by_group[group_name].append(pid)

        # Log grouping for debugging
        for group_name, pids in by_group.items():
            logger.debug(f"[PoolManager] Group {group_name}: {pids}")

        # Round-robin across groups
        result = []
        group_names = list(by_group.keys())

        while any(by_group.values()):
            for group_name in group_names:
                if by_group[group_name]:
                    result.append(by_group[group_name].pop(0))

        logger.info(f"[PoolManager] Interleaved {len(providers)} providers across {len(group_names)} groups")

        return result

    def get_stats(self) -> Dict:
        """
        Get current pool statistics.

        Returns:
            Dictionary with pool state information
        """
        stats = {
            "groups": {},
            "browser_available": self._browser_semaphore._value,
            "browser_max": self._config.max_browser_instances,
        }

        for group_name, semaphore in self._group_semaphores.items():
            group = self._groups[group_name]
            stats["groups"][group_name] = {
                "available": semaphore._value,
                "max": group.max_concurrent,
                "shared_resource": group.shared_resource,
            }

        return stats
