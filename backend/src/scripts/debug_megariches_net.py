import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # Use a specific user agent to look real
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        print("Monitoring all JSON responses...")
        
        # Capture any JSON response
        page.on("response", lambda response: print(f"<< {response.status} {response.url}") if "json" in response.headers.get("content-type", "") else None)

        try:
            url = "https://www.fastbet.com/sv/sports"
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print(f"Page Title: {await page.title()}")
            
            # Wait for dynamic content
            await page.wait_for_timeout(10000)
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
