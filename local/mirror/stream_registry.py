"""Stream registry — global lookup for active ProviderDataStream instances."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_stream import ProviderDataStream

_streams: dict[str, ProviderDataStream] = {}


def register(provider_id: str, stream: ProviderDataStream) -> None:
    _streams[provider_id] = stream


def unregister(provider_id: str) -> None:
    _streams.pop(provider_id, None)


def get(provider_id: str) -> ProviderDataStream | None:
    return _streams.get(provider_id)


def get_all() -> dict[str, ProviderDataStream]:
    return dict(_streams)
