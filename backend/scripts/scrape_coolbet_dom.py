"""Quick test: scrape coolbet directly from DOM instead of API."""
import asyncio
from camoufox.async_api import AsyncCamoufox

JS_SCRAPE = """() => {
    const events = [];
    // Coolbet renders match cards with team names, odds buttons
    // Find all visible match/event containers
    const matchEls = document.querySelectorAll(
        '[data-test*="match"], [class*="match-row"], [class*="event-row"], ' +
        '[class*="fo-match"], [class*="SportMatch"], [class*="MatchCard"]'
    );

    if (matchEls.length > 0) {
        matchEls.forEach(el => {
            const text = el.innerText.trim().substring(0, 200);
            const odds = text.match(/\\d+\\.\\d{2}/g) || [];
            if (odds.length >= 2) {
                events.push({text: text.substring(0, 120), odds: odds.slice(0, 6)});
            }
        });
        return {method: 'match-elements', count: matchEls.length, events: events.slice(0, 10)};
    }

    // Fallback: parse entire page text
    const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(l => l.length > 5);
    const parsed = [];
    for (const line of lines) {
        const odds = line.match(/\\d+\\.\\d{2}/g);
        if (odds && odds.length >= 2 && odds.length <= 8) {
            parsed.push({text: line.substring(0, 120), odds: odds});
        }
    }
    return {method: 'text-parse', total_lines: lines.length, events: parsed.slice(0, 10)};
}"""

async def main():
    proxy = {"server": "socks5://155.4.244.202:1080"}
    browser = await AsyncCamoufox(headless=True, geoip=False, proxy=proxy).__aenter__()
    page = await browser.new_page()

    # Load football page
    await page.goto("https://www.coolbet.com/sv/odds/fotboll", wait_until="load", timeout=60000)
    await asyncio.sleep(8)

    result = await page.evaluate(JS_SCRAPE)
    print(f"Method: {result['method']}")
    print(f"Events found: {len(result.get('events', []))}")
    for e in result.get("events", []):
        print(f"  {e['text']}")
        print(f"    odds: {e['odds']}")

    await browser.close()

asyncio.run(main())
