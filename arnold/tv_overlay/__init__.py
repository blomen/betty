"""TV overlay — broadcasts zone/position state to a Tampermonkey userscript."""

from arnold.tv_overlay.status import get_status, snapshot

__all__ = ["get_status", "snapshot"]
