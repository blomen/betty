
import asyncio
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.core.transport import BrowserTransport

async def main():
    transport = BrowserTransport(headless=True)
    try:
        await transport._ensure_browser()
        print("Navigating...")
        await transport.page.goto("https://www.snabbare.com/sv/odds", wait_until="networkidle")
        print("Getting content...")
        content = await transport.page.content()
        with open("snabbare_home.html", "w", encoding="utf-8") as f:
            f.write(content)
        print("Done.")
    finally:
        await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
