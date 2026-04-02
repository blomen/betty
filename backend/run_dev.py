"""
Dev server launcher.

Usage:
    python run_dev.py          # default: no reload, ProactorEventLoop (mirror works)
    python run_dev.py --reload # with hot reload (mirror disabled on Windows)
"""

import sys
import asyncio
import uvicorn

if __name__ == "__main__":
    use_reload = "--reload" in sys.argv

    # Windows: ProactorEventLoop supports subprocesses (Playwright mirror).
    # uvicorn's reloader spawns a child with SelectorEventLoop — no fix for that.
    # Without reload, we control the loop directly.
    if sys.platform == "win32" and not use_reload:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(
        "src.api:app",
        host="127.0.0.1",
        port=8000,
        timeout_keep_alive=120,
        reload=use_reload,
    )
