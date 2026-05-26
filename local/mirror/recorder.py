"""Network traffic recorder — captures all HTTP traffic for RL training.

Writes JSONL files (one per provider per session) with every request/response.
This is the "dashcam" — always recording, append-only, never deleted.

File structure:
  data/mirror_recordings/{provider_id}/{YYYY-MM-DD_HH-MM-SS}.jsonl

Each line is a JSON object:
  {
    "ts": "2026-03-20T01:23:45.678Z",
    "method": "GET",
    "url": "https://...",
    "status": 200,
    "request_headers": {...},
    "request_body": "...",
    "response_headers": {...},
    "response_body": "...",
    "resource_type": "xhr",
    "page_url": "https://..."
  }
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Skip recording these — static assets, tracking pixels, noise
_SKIP_EXTENSIONS = frozenset(
    {
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".map",
    }
)

_SKIP_PATTERNS = (
    "/analytics",
    "/tracking",
    "/pixel",
    "/beacon",
    "google-analytics",
    "googletagmanager",
    "facebook.com/tr",
    "hotjar",
    "clarity.ms",
    "doubleclick",
)


class NetworkRecorder:
    """Records all network traffic to JSONL files."""

    def __init__(self, provider_id: str, data_dir: Path | None = None):
        self.provider_id = provider_id
        if data_dir is None:
            try:
                from ..paths import get_data_dir

                data_dir = get_data_dir()
            except ImportError:
                import os
                from pathlib import Path

                data_dir = Path(
                    os.environ.get(
                        "BETTY_DATA_DIR",
                        str(Path(__file__).parent.parent.parent / "data"),
                    )
                )
        self._recordings_dir = data_dir / "mirror_recordings" / provider_id
        self._file = None
        self._path: Path | None = None
        self._count = 0

    def start(self):
        """Open a new recording file."""
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        self._path = self._recordings_dir / f"{ts}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8")
        self._count = 0
        logger.info(f"[recorder:{self.provider_id}] Recording to {self._path}")

    def stop(self):
        """Close the recording file."""
        if self._file:
            self._file.close()
            logger.info(
                f"[recorder:{self.provider_id}] Stopped — {self._count} entries in {self._path}"
            )
            self._file = None

    def _should_skip(self, url: str) -> bool:
        """Check if this URL should be skipped (static assets, tracking)."""
        lower = url.lower()
        # Skip by extension
        for ext in _SKIP_EXTENSIONS:
            if ext in lower.split("?")[0].split("#")[0][-10:]:
                return True
        # Skip tracking/analytics
        return any(p in lower for p in _SKIP_PATTERNS)

    async def record_response(self, response) -> None:
        """Record a response to the JSONL file."""
        if not self._file:
            return

        url = response.url
        if self._should_skip(url):
            return

        try:
            # Read response body for API calls
            response_body = None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or "text" in content_type:
                try:
                    response_body = await response.text()
                except Exception:
                    pass

            # Read request body
            request_body = None
            try:
                request_body = response.request.post_data
            except Exception:
                pass

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "method": response.request.method,
                "url": url,
                "status": response.status,
                "request_body": request_body,
                "response_body": response_body,
                "resource_type": response.request.resource_type,
            }

            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
            self._count += 1

        except Exception as e:
            logger.debug(f"[recorder:{self.provider_id}] Error recording {url}: {e}")

    def record_dom_event(self, event_type: str, data: dict) -> None:
        """Record a DOM interaction (click, input, navigation) to the JSONL file."""
        if not self._file:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "dom",
            "event": event_type,
            **data,
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()
        self._count += 1
