
import asyncio
from playwright.async_api import async_playwright
import os

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        url = "https://www.snabbare.com/sv/sportsbook/sport/1-fotboll/leagues/134-premier-league"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(5000)
        
        content = await page.content()
        log_file = os.path.join(os.path.dirname(__file__), "../../debug/snabbare_source.html")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(content)
            
        print(f"Saved HTML to {log_file}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
