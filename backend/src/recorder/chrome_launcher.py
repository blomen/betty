"""
Chrome Launcher — Manages a dedicated Chrome instance with CDP enabled.

Launched on app startup so the recorder can connect immediately when the user
clicks "Go" to place a bet. Uses a dedicated profile directory so sessions
and cookies persist across app restarts without interfering with the user's
main Chrome.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CDP_PORT = 9222

# Known Chrome paths on Windows
_CHROME_PATHS_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class ChromeLauncher:
    """Manages a dedicated Chrome instance with --remote-debugging-port."""

    def __init__(self, port: int = CDP_PORT):
        self._port = port
        self._process: subprocess.Popen | None = None

    @property
    def cdp_url(self) -> str:
        return f"http://localhost:{self._port}"

    @property
    def is_running(self) -> bool:
        """Check if our managed Chrome process is still alive."""
        return self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Launch Chrome with CDP. Returns True if CDP is available."""
        # 1. Check if CDP is already responding (e.g. user started Chrome manually)
        if await self._is_cdp_available():
            logger.info(f"CDP already available on port {self._port}")
            return True

        # 2. Find Chrome executable
        chrome_path = self._find_chrome()
        if not chrome_path:
            logger.warning("Chrome not found — recorder will be unavailable")
            return False

        # 3. Launch with dedicated profile
        profile_dir = Path.home() / ".bankrollbbq" / "chrome-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        args = [
            chrome_path,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        logger.info(f"Launching Chrome: {chrome_path} (CDP port {self._port})")
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            logger.error(f"Failed to launch Chrome: {e}")
            return False

        # 4. Wait for CDP to become available (up to 10 seconds)
        for _ in range(20):
            await asyncio.sleep(0.5)
            if self._process.poll() is not None:
                logger.error("Chrome process exited immediately")
                self._process = None
                return False
            if await self._is_cdp_available():
                logger.info(f"Chrome started with CDP on port {self._port}")
                return True

        logger.error("Chrome started but CDP not responding within 10s")
        return False

    # ------------------------------------------------------------------
    # Navigation — open a URL in the CDP Chrome
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> dict | None:
        """Open a URL in the CDP Chrome. Returns tab info or None on failure."""
        if not await self._is_cdp_available():
            return None

        loop = asyncio.get_event_loop()

        def _open_tab():
            try:
                req = urllib.request.Request(
                    f"http://localhost:{self._port}/json/new?{url}",
                    method="PUT",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                logger.warning(f"Failed to open tab in CDP Chrome: {e}")
                return None

        return await loop.run_in_executor(None, _open_tab)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def stop(self):
        """Terminate the managed Chrome process (if we launched it)."""
        if self._process is not None:
            logger.info("Stopping managed Chrome instance")
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _is_cdp_available(self) -> bool:
        """Check if CDP is responding on our port."""
        loop = asyncio.get_event_loop()

        def _check():
            try:
                with urllib.request.urlopen(
                    f"http://localhost:{self._port}/json/version", timeout=2
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

        return await loop.run_in_executor(None, _check)

    @staticmethod
    def _find_chrome() -> str | None:
        """Locate the Chrome executable."""
        # shutil.which covers PATH and common aliases
        for name in ("chrome", "google-chrome", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                return found

        # Windows-specific known paths
        for path in _CHROME_PATHS_WIN:
            if Path(path).exists():
                return path

        return None


# Singleton
_launcher: ChromeLauncher | None = None


def get_chrome_launcher() -> ChromeLauncher:
    global _launcher
    if _launcher is None:
        _launcher = ChromeLauncher()
    return _launcher
