"""RSocket frame decoding mixin for WebSocket-based providers."""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class RSocketMixin:
    """
    Mixin providing RSocket binary frame decoding for WebSocket-based providers.

    Used by ComeOn Group platforms (ComeOn, Hajper) that communicate via RSocket
    protocol over WebSocket connections.
    """

    provider_id: str  # Expected to be set by the provider class

    def _decode_rsocket_frame(self, frame_bytes: bytes) -> Optional[List[Dict]]:
        """
        Decode RSocket binary frame to extract JSON payload.

        Args:
            frame_bytes: Raw binary WebSocket frame

        Returns:
            Decoded JSON list or None if decoding fails
        """
        try:
            frame_str = frame_bytes.decode('utf-8', errors='ignore')

            # Find JSON start
            if '[{' in frame_str:
                json_start = frame_str.index('[{')
                json_str = frame_str[json_start:]
                return json.loads(json_str)

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to decode frame: {e}")

        return None

    def _setup_ws_interception(self, page) -> list:
        """
        Setup WebSocket interception and return message storage list.

        Args:
            page: Playwright page object

        Returns:
            List that will be populated with decoded WebSocket messages
        """
        messages = []

        def on_websocket(ws):
            def on_frame_received(payload):
                if isinstance(payload, bytes):
                    decoded = self._decode_rsocket_frame(payload)
                    if decoded:
                        messages.append(decoded)

            ws.on("framereceived", on_frame_received)

        page.on("websocket", on_websocket)
        return messages
