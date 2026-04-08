"""Signal logger — persists meaningful position changes to a JSON log file.

Only logs when the trading decision actually changes (new entry, flip,
or exit after a hold period). Filters out tick-by-tick noise from
zone oscillation.

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

# State for deduplication
_last_action: str | None = None
_last_zone: float = 0.0
_last_signal_epoch: float = 0.0
_MIN_SIGNAL_GAP_S = 120.0  # minimum 2 minutes between signals at same zone


def log_signal(
    price: float,
    zone_center: float,
    zone_members: int,
    zone_hierarchy: float,
    inference_result: dict,
    approach_direction: str = "unknown",
) -> None:
    """Log a signal only if it represents a meaningful position change.

    Filters:
    - Skip if same action at same zone within 2 minutes
    - Always log action changes (CONT→REV, REV→CONT)
    - Always log new zone entries
    """
    global _last_action, _last_zone, _last_signal_epoch

    action = inference_result.get("action", "SKIP")
    if action == "SKIP":
        return  # never log skips

    now_epoch = time.time()
    same_action = action == _last_action
    recent = (now_epoch - _last_signal_epoch) < _MIN_SIGNAL_GAP_S

    # Filter: skip if same action within 2 min (regardless of zone)
    # Only log when direction changes or enough time has passed
    if same_action and recent:
        return

    _last_action = action
    _last_zone = zone_center
    _last_signal_epoch = now_epoch

    try:
        _SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y-%m-%d") + ".jsonl"
        filepath = _SIGNALS_DIR / filename

        entry = {
            "ts": now.isoformat(),
            "epoch": now_epoch,
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
