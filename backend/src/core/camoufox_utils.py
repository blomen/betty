"""Helpers for cleaning up AsyncCamoufox-spawned subprocess trees.

AsyncCamoufox launches a Playwright driver (Node.js) which spawns
camoufox-bin (Firefox patched for stealth) which in turn forks renderer
tabs. When the camoufox subprocess hangs or `__aexit__` raises mid-cleanup,
nothing kills the spawned tree — driver + camoufox-bin + tabs leak,
accumulate RAM, and eventually OOM the container.

Mirrors the pattern in `BrowserTransport`: capture the driver PID at
launch, SIGKILL the descendant tree on cleanup timeout / failure.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


def capture_camoufox_driver_pid(browser: Any) -> int | None:
    """PID of the Playwright driver subprocess underlying this Camoufox browser.

    AsyncCamoufox returns a Playwright Browser. Drilling into its internal
    transport gives the driver process — every chrome/camoufox/tab subprocess
    descends from this one PID, so killing its tree reaps everything.
    """
    try:
        impl = browser._impl_obj
        connection = impl._channel._connection
        transport = connection._transport
        proc = getattr(transport, "_proc", None)
        if proc is not None and getattr(proc, "pid", None):
            return int(proc.pid)
    except Exception as e:
        logger.debug(f"[camoufox_utils] could not capture driver pid: {e}")
    return None


def force_kill_camoufox_tree(driver_pid: int | None, label: str) -> int:
    """SIGKILL `driver_pid` and every descendant. Returns count killed."""
    if psutil is None or driver_pid is None:
        return 0
    try:
        root = psutil.Process(driver_pid)
    except psutil.NoSuchProcess:
        return 0

    targets = [root]
    try:
        targets.extend(root.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    killed = 0
    for proc in targets:
        try:
            if proc.is_running():
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.debug(f"[{label}] camoufox kill({proc.pid}) failed: {e}")

    if killed:
        logger.warning(f"[{label}] force-killed {killed} hung camoufox processes")
    return killed
