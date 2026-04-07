"""Signal logger — persists every specialist decision to a JSON log file.

Each signal is a line of JSON with timestamp, action, confidence,
zone info, and the full specialist output. Designed for post-session
review: "did the model call the right action at each level?"

Log file: data/rl/signals/YYYY-MM-DD.jsonl (one file per session date)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SIGNALS_DIR = Path("data/rl/signals")


def log_signal(
    price: float,
    zone_center: float,
    zone_members: int,
    zone_hierarchy: float,
    inference_result: dict,
    approach_direction: str = "unknown",
) -> None:
    """Append a signal entry to today's log file.

    Called from level_monitor on every zone touch that produces an inference.
    """
    try:
        _SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y-%m-%d") + ".jsonl"
        filepath = _SIGNALS_DIR / filename

        entry = {
            "ts": now.isoformat(),
            "epoch": time.time(),
            "price": round(price, 2),
            "zone_center": round(zone_center, 2),
            "zone_members": zone_members,
            "zone_hierarchy": round(zone_hierarchy, 3),
            "approach": approach_direction,
            **{k: round(v, 4) if isinstance(v, float) else v
               for k, v in inference_result.items()},
        }

        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")

    except Exception:
        log.debug("Signal log failed", exc_info=True)
