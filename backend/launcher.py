"""
Firev Desktop Application Launcher

Starts FastAPI server in a background thread and opens a native
Windows window via pywebview. The React frontend is served as
static files from the same FastAPI instance.

Usage:
  python launcher.py          (dev mode)
  Firev.exe                   (bundled mode — PyInstaller)
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing

# Fix Windows console streams for PyInstaller GUI mode (console=False).
# When there is no console, Python sets sys.stdout/stderr to None, which
# crashes libraries that call sys.stderr.isatty() (e.g. uvicorn logging).
import io

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_free_port(start: int = 8000, end: int = 8100) -> int:
    """Find an available port in the given range.

    Uses connect() to check if something is already listening, then bind()
    to verify the port is truly available.  This avoids the race where
    bind() succeeds on a TIME_WAIT socket but uvicorn then fails.
    """
    for port in range(start, end):
        # First check: is anything already listening?
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                # Something is listening → skip
                continue
        # Second check: can we actually bind?
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free ports in range {start}-{end}")


def start_server(port: int):
    """Run uvicorn in a background thread."""
    logger = logging.getLogger("launcher.server")
    try:
        # Ensure ProactorEventLoop on Windows — required for browser-based
        # extraction (patchright needs asyncio.create_subprocess_exec).
        # Without this, some Windows configurations fall back to
        # SelectorEventLoop which raises NotImplementedError on subprocess.
        import asyncio
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        import uvicorn
        logger.info("Importing FastAPI app...")
        from src.api import app
        logger.info("FastAPI app imported OK, starting uvicorn on port %d...", port)

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            timeout_keep_alive=120,  # SSE streams need long keep-alive (default 5s drops connections)
        )
        server = uvicorn.Server(config)
        server.run()
        logger.info("Uvicorn server stopped")
    except OSError as e:
        if "address already in use" in str(e).lower() or "10048" in str(e):
            logger.error("Port %d is already in use — is another Firev instance running?", port)
        else:
            logger.exception("Server thread crashed (OSError)")
    except Exception:
        logger.exception("Server thread crashed")


def wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """Poll the health endpoint until the server is ready.

    Args:
        port: Port to check.
        timeout: Maximum seconds to wait (default 60s to handle cold starts).
    """
    import urllib.request
    import urllib.error

    logger = logging.getLogger("launcher.wait")
    url = f"http://127.0.0.1:{port}/health"
    start = time.time()
    deadline = start + timeout
    logged_5s = logged_15s = False

    while time.time() < deadline:
        elapsed = time.time() - start
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    logger.info("Health check passed after %.1fs", elapsed)
                    return True
        except (urllib.error.URLError, OSError):
            pass

        if elapsed > 5 and not logged_5s:
            logger.info("Still waiting for server (%.0fs)...", elapsed)
            logged_5s = True
        if elapsed > 15 and not logged_15s:
            logger.warning("Server slow to start (%.0fs) — may be a cold start", elapsed)
            logged_15s = True

        time.sleep(0.25)

    logger.error("Server did not respond within %.0fs", timeout)
    return False


def main():
    """Entry point for the desktop app."""
    from src.paths import is_bundled, get_logs_dir

    # Set up logging to file so we can debug bundled crashes
    log_file = get_logs_dir() / "launcher.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("launcher")

    try:
        _run(logger, is_bundled())
    except Exception:
        logger.exception("Fatal error in launcher")
        # Show error dialog so user sees something
        try:
            import traceback
            import ctypes
            msg = traceback.format_exc()
            ctypes.windll.user32.MessageBoxW(
                0, f"Firev failed to start:\n\n{msg}", "Firev Error", 0x10
            )
        except Exception:
            pass
        sys.exit(1)


def _find_icon() -> str | None:
    """Locate the app icon (.ico) for pywebview window."""
    from src.paths import is_bundled, get_bundle_dir
    import os

    candidates = []
    if is_bundled():
        candidates.append(os.path.join(str(get_bundle_dir()), 'frontend', 'dist', 'firev.ico'))
    # Dev mode
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'public', 'firev.ico'))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'public', 'firev.ico'))

    for path in candidates:
        resolved = os.path.normpath(path)
        if os.path.isfile(resolved):
            return resolved
    return None


def _set_window_icon(icon_path: str, logger: logging.Logger):
    """Set the window icon using Win32 API after pywebview creates the window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # Give the app its own taskbar identity so Windows uses our icon
        # instead of grouping under the Python interpreter icon.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Firev.Firev.1"
        )

        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010

        # Load both small (16x16 for title bar) and large (32x32 for taskbar/alt-tab)
        hicon_big = user32.LoadImageW(
            0, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE
        )
        hicon_small = user32.LoadImageW(
            0, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )

        if not hicon_big and not hicon_small:
            logger.warning("Failed to load icon from %s", icon_path)
            return

        # Find the pywebview window by title
        hwnd = user32.FindWindowW(None, "Firev")
        if not hwnd:
            logger.warning("Could not find Firev window to set icon")
            return

        WM_SETICON = 0x0080
        ICON_BIG = 1
        ICON_SMALL = 0

        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)

        logger.info("Window icon set successfully")
    except Exception:
        logger.exception("Failed to set window icon")


def _kill_orphan_servers(logger: logging.Logger):
    """Kill orphan backend processes listening on ports 8000-8100.

    Uses netstat to find PIDs bound to our port range, then kills them.
    Surgical — only targets LISTENING processes on specific ports, so RL
    training, VSCode, and other node/python processes are untouched.
    """
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        pids_to_kill = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            state = parts[3]
            pid = parts[4]
            # Check if listening on a port in our range
            if state != "LISTENING" or not pid.isdigit():
                continue
            try:
                addr_port = int(local_addr.rsplit(":", 1)[-1])
            except ValueError:
                continue
            if 8000 <= addr_port <= 8100:
                pid_int = int(pid)
                if pid_int > 0 and pid_int != os.getpid():
                    pids_to_kill.add(pid_int)

        for pid in pids_to_kill:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                logger.info("Killed orphan process PID %d", pid)
            except Exception:
                pass
    except Exception:
        logger.debug("Orphan cleanup skipped", exc_info=True)


def _run(logger: logging.Logger, bundled: bool):
    """Core launcher logic, separated for clean error handling."""
    # Set AppUserModelID early so Windows taskbar uses our icon from the start
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Firev.Firev.1"
            )
        except Exception:
            pass

    # Kill any orphan servers from previous sessions before finding a port
    _kill_orphan_servers(logger)

    # First-run setup (directories, config, DB, Playwright check)
    from src.first_run import run_first_time_setup

    logger.info("Running first-time setup...")
    run_first_time_setup()

    # Find available port
    port = find_free_port()
    logger.info("Starting server on port %d...", port)

    # Start FastAPI in background thread
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()

    # Wait for server readiness
    if not wait_for_server(port):
        logger.error("Server failed to start — check logs/launcher.log for details")
        sys.exit(1)

    logger.info("Server ready at http://127.0.0.1:%d", port)

    # Open native window (must be on main thread for Windows)
    try:
        import webview

        icon_path = _find_icon()
        if icon_path:
            logger.info("Using app icon: %s", icon_path)

        logger.info("Opening pywebview window...")
        webview.create_window(
            title="Firev",
            url=f"http://127.0.0.1:{port}",
            width=1400,
            height=900,
            min_size=(1000, 600),
            resizable=True,
        )

        def _on_shown():
            """Set icon once the window is visible."""
            if icon_path:
                import time
                time.sleep(0.5)  # Brief delay for window to fully initialize
                _set_window_icon(icon_path, logger)

        # Blocks until window is closed.
        # private_mode=False enables caching/cookies between sessions.
        webview.start(
            func=_on_shown,
            private_mode=False,
            debug=False,
        )
    except ImportError:
        # pywebview not installed — fall back to browser
        logger.warning("pywebview not installed, opening in default browser")
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{port}")
        logger.info("Press Ctrl+C to stop the server")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass

    logger.info("Window closed, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    main()
