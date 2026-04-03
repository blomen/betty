"""Cluster-based setup labeling for soft setups (rotation, excess test, balance break).

Runs HDBSCAN on narrative features of episodes that didn't match any rule-based setup.
Clusters are then mapped to setup types based on their centroid characteristics.
"""
from __future__ import annotations

import logging

import numpy as np

from .setup_types import SetupType

log = logging.getLogger(__name__)

_POC_ZONE_TYPES = {"daily_poc", "weekly_poc", "monthly_poc", "tpoc"}
_EXCESS_ZONE_TYPES = {"naked_poc"}


def cluster_and_label(
    observations: np.ndarray,
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
    min_cluster_size: int = 200,
) -> np.ndarray:
    """Cluster unlabeled episodes and assign soft setup labels."""
    try:
        from hdbscan import HDBSCAN
    except ImportError:
        log.warning("hdbscan not installed — falling back to heuristic labeling")
        return _heuristic_label(zone_types_list, rewards_cont, rewards_rev,
                                price_vs_value, balance_widths)

    n = len(observations)
    clusterer = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=50)
    cluster_ids = clusterer.fit_predict(observations)

    labels = np.full(n, SetupType.UNKNOWN.value, dtype=object)

    for cid in set(cluster_ids):
        if cid == -1:
            continue
        mask = cluster_ids == cid
        labels[mask] = _classify_cluster(
            zone_types_list=[zone_types_list[i] for i in np.where(mask)[0]],
            rewards_cont=rewards_cont[mask],
            rewards_rev=rewards_rev[mask],
            price_vs_value=price_vs_value[mask],
            balance_widths=balance_widths[mask],
        )

    log.info("Clustered %d episodes: %d clusters, %d noise",
             n, len(set(cluster_ids) - {-1}), (cluster_ids == -1).sum())
    return labels


def _classify_cluster(
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
) -> str:
    """Map a cluster to a setup type based on centroid characteristics."""
    n = len(rewards_cont)

    poc_count = sum(1 for zt in zone_types_list if set(zt) & _POC_ZONE_TYPES)
    poc_ratio = poc_count / max(n, 1)

    excess_count = sum(1 for zt in zone_types_list if set(zt) & _EXCESS_ZONE_TYPES)
    excess_ratio = excess_count / max(n, 1)

    avg_pvv = np.abs(price_vs_value).mean()
    rev_better = (rewards_rev > rewards_cont).mean()

    avg_balance = balance_widths.mean()
    cont_better = (rewards_cont > rewards_rev).mean()

    if poc_ratio > 0.3 and avg_pvv > 0.5 and rev_better > 0.55:
        return SetupType.ROTATION_TO_POC.value
    if excess_ratio > 0.2:
        return SetupType.EXCESS_TEST.value
    if avg_balance > 0.4 and cont_better > 0.55:
        return SetupType.BALANCE_BREAK.value

    return SetupType.UNKNOWN.value


def _heuristic_label(
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
) -> np.ndarray:
    """Simple heuristic fallback when HDBSCAN is not available."""
    n = len(rewards_cont)
    labels = np.full(n, SetupType.UNKNOWN.value, dtype=object)

    for i in range(n):
        zt = set(zone_types_list[i])
        if zt & _POC_ZONE_TYPES and abs(price_vs_value[i]) > 0.5:
            labels[i] = SetupType.ROTATION_TO_POC.value
        elif zt & _EXCESS_ZONE_TYPES:
            labels[i] = SetupType.EXCESS_TEST.value
        elif balance_widths[i] > 0.4 and rewards_cont[i] > rewards_rev[i]:
            labels[i] = SetupType.BALANCE_BREAK.value

    return labels
