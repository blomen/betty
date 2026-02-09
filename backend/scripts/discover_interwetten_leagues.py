"""
Discover Interwetten league IDs by scraping their sportsbook navigation.
"""
import asyncio
from playwright.async_api import async_playwright


JS_EXTRACT = """() => {
    const groups = {};
    const cats = document.querySelectorAll('.s-navigation-category');

    for (const cat of cats) {
        const sportLabel = cat.querySelector('.s-navigation-category-title, strong, h3, h4');
        let sportName = 'unknown';

        const sportAttr = cat.getAttribute('data-sport') || cat.getAttribute('data-category');
        if (sportAttr) {
            sportName = sportAttr;
        } else if (sportLabel) {
            sportName = sportLabel.textContent.trim();
        }

        const links = cat.querySelectorAll('a[href*="/l/"]');
        if (links.length > 0) {
            if (!groups[sportName]) groups[sportName] = [];
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const parts = href.split('/l/');
                if (parts.length > 1) {
                    const rest = parts[1].replace(/^\\/+|\\/+$/g, '');
                    const slashIdx = rest.indexOf('/');
                    const idStr = slashIdx > 0 ? rest.substring(0, slashIdx) : rest;
                    const slug = rest;
                    groups[sportName].push({
                        id: parseInt(idStr),
                        text: link.textContent.trim().substring(0, 60),
                        slug: slug
                    });
                }
            }
        }
    }
    return groups;
}"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        page = await browser.new_page()

        print("Navigating to Interwetten...")
        await page.goto(
            "https://www.interwetten.se/en/sportsbook",
            wait_until="load",
            timeout=60000,
        )

        # Handle cookie consent
        for text in ["Accept", "Acceptera", "OK"]:
            try:
                await page.click(f'button:has-text("{text}")', timeout=3000)
                print(f"Cookie consent: clicked '{text}'")
                break
            except Exception:
                continue

        await asyncio.sleep(3)

        # Extract sport-league navigation
        data = await page.evaluate(JS_EXTRACT)

        for sport, leagues in sorted(data.items()):
            print(f"\n=== {sport} ({len(leagues)} leagues) ===")
            for league in leagues:
                print(
                    f"  ({league['id']}, \"{league['slug']}\"),  # {league['text']}"
                )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
