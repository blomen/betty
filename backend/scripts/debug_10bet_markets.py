import asyncio
try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright


JS_FIND_COMP = '''() => {
    const links = document.querySelectorAll('a[href*="competitions/"]');
    for (const a of links) {
        const match = a.getAttribute('href').match(/\/competitions\/(\d+)/);
        if (match) return { id: match[1], text: a.textContent.trim().substring(0, 60) };
    }
    return null;
}'''

JS_EXTRACT_EVENTS = '''() => {
    const items = document.querySelectorAll('[class*="ta-EventListItem"]');
    const results = [];
    for (let i = 0; i < Math.min(items.length, 3); i++) {
        const item = items[i];
        const participants = Array.from(item.querySelectorAll('[class*="ta-participantName"]')).map(p => p.textContent.trim());
        const markets = [];
        item.querySelectorAll('[class*="ta-MarketType-"]').forEach(m => {
            const cls = Array.from(m.classList).find(c => c.startsWith('ta-MarketType-'));
            const marketType = cls ? cls.replace('ta-MarketType-', '') : 'unknown';
            const prices = Array.from(m.querySelectorAll('[class*="ta-price_text"]')).map(p => p.textContent.trim());
            const infoTexts = Array.from(m.querySelectorAll('[class*="ta-infoText"]')).map(t => t.textContent.trim());
            const allText = m.textContent.trim();
            markets.push({
                type: marketType,
                prices: prices,
                infoTexts: infoTexts,
                allText: allText.substring(0, 200),
                priceCount: prices.length,
                infoCount: infoTexts.length
            });
        });
        results.push({
            participants: participants,
            participantCount: participants.length,
            marketCount: markets.length,
            markets: markets
        });
    }
    return results;
}'''


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel='chrome')
        page = await browser.new_page()

        await page.goto('https://www.10bet.se/sports', wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)

        # Cookies
        cookie_sels = [
            'button:has-text("Tillat alla")',
            'button:has-text("Acceptera alla")',
            'button:has-text("Acceptera")',
        ]
        for sel in cookie_sels:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    print(f'Clicked: {sel}', flush=True)
                    break
            except Exception:
                continue
        await asyncio.sleep(1)

        # Football competitions
        await page.goto('https://www.10bet.se/sports/football/competitions', wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)

        first_comp = await page.evaluate(JS_FIND_COMP)
        print(f'First competition: {first_comp}', flush=True)

        if first_comp:
            url = f'https://www.10bet.se/sports/football/competitions/{first_comp["id"]}/matches'
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            try:
                await page.wait_for_selector('[class*="ta-EventListItem"]', timeout=10000)
            except Exception:
                print('No events found', flush=True)
            await asyncio.sleep(2)

            data = await page.evaluate(JS_EXTRACT_EVENTS)

            print('Football events (first 3):', flush=True)
            for i, ev in enumerate(data):
                parts = ' vs '.join(ev['participants'][:2])
                print(f'  Event {i+1}: {parts} ({ev["participantCount"]} participants, {ev["marketCount"]} markets)', flush=True)
                for m in ev['markets']:
                    print(f'    Market: {m["type"]}', flush=True)
                    print(f'      Prices ({m["priceCount"]}): {m["prices"]}', flush=True)
                    print(f'      InfoTexts ({m["infoCount"]}): {m["infoTexts"]}', flush=True)
                    spread_total_types = ('HCTG', 'TPOT', 'OUTG', 'HCOT', 'HCMR', 'FHOT', 'TGHC')
                    if not m['infoTexts'] and m['type'] in spread_total_types:
                        print(f'      !!! Missing infoText for spread/total market', flush=True)
                        print(f'      AllText: {m["allText"]}', flush=True)

        # Basketball
        await page.goto('https://www.10bet.se/sports/basketball/competitions', wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)

        bball_comp = await page.evaluate(JS_FIND_COMP)

        if bball_comp:
            print(f'Basketball: {bball_comp["text"]} (ID: {bball_comp["id"]})', flush=True)
            url = f'https://www.10bet.se/sports/basketball/competitions/{bball_comp["id"]}/matches'
            await page.goto(url, wait_until='domcontentloaded', timeout=20000)
            try:
                await page.wait_for_selector('[class*="ta-EventListItem"]', timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)

            bdata = await page.evaluate(JS_EXTRACT_EVENTS)
            for i, ev in enumerate(bdata):
                parts = ' vs '.join(ev['participants'][:2])
                print(f'  Event {i+1}: {parts}', flush=True)
                for m in ev['markets']:
                    print(f'    {m["type"]}: prices={m["prices"]} info={m["infoTexts"]}', flush=True)
                    if not m['infoTexts'] and m['type'] in ('HCOT', 'TPOT'):
                        print(f'      AllText: {m["allText"]}', flush=True)

        await browser.close()


asyncio.run(main())