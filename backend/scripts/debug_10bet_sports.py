import asyncio
from playwright.async_api import async_playwright

SPORTS = {
    'football': 'football',
    'basketball': 'basketball',
    'tennis': 'tennis',
    'ice_hockey': 'ice_hockey',
    'handball': 'handball',
    'esports': 'esports',
    'american_football': 'american_football',
    'baseball': 'baseball',
    'mma': 'martial_arts',
}

JS_FIND_COMPS = """() => {
    const links = document.querySelectorAll('a[href*="competitions/"]');
    return Array.from(links)
        .map(a => ({
            text: a.textContent.trim().substring(0, 60),
            href: a.getAttribute('href')
        }))
        .filter(l => l.href && /\/competitions\/\d+/.test(l.href))
        .filter((v, i, a) => a.findIndex(x => x.href === v.href) === i);
}"""

JS_PAGE_PREVIEW = """() => {
    const main = document.querySelector('main, .main-content, [role="main"]');
    return main ? main.innerHTML.substring(0, 500) : document.body.innerHTML.substring(0, 500);
}"""

JS_SPORT_LINKS = """() => {
    const links = document.querySelectorAll('a[href*="/sports/"]');
    return Array.from(links).slice(0, 10).map(a => ({
        text: a.textContent.trim().substring(0, 40),
        href: a.getAttribute('href')
    }));
}"""

JS_FIRST_COMP = r"""() => {
    const links = document.querySelectorAll('a[href*="competitions/"]');
    for (const a of links) {
        const match = a.getAttribute('href').match(/\/competitions\/(\d+)/);
        if (match) return { id: match[1], text: a.textContent.trim().substring(0, 60) };
    }
    return null;
}"""

JS_EVENT_INFO = """() => {
    const items = document.querySelectorAll('[class*="ta-EventListItem"]');
    const sample = items[0];
    if (!sample) return { count: 0, html: 'no events found' };

    const participants = Array.from(sample.querySelectorAll('[class*="ta-participantName"]')).map(p => p.textContent.trim());
    const prices = Array.from(sample.querySelectorAll('[class*="ta-price_text"]')).map(p => p.textContent.trim());
    const markets = Array.from(sample.querySelectorAll('[class*="ta-MarketType-"]')).map(m => {
        const cls = Array.from(m.classList).find(c => c.startsWith('ta-MarketType-'));
        return cls ? cls.replace('ta-MarketType-', '') : 'unknown';
    });
    const timing = sample.querySelector('[class*="ta-EventTimingStatus"], [class*="Timing"]');

    return {
        count: items.length,
        participants,
        prices,
        markets,
        timing: timing ? timing.textContent.trim() : '',
        html: sample.innerHTML.substring(0, 500)
    };
}"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel='chrome')
        page = await browser.new_page()

        print('Loading 10bet.se...', flush=True)
        await page.goto('https://www.10bet.se/sports', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)

        # Handle cookie consent
        for sel in ['button:has-text("Acceptera alla")', 'button:has-text("Acceptera")']:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    print('Clicked cookie consent', flush=True)
                    await asyncio.sleep(1)
                    break
            except:
                continue

        # Test each sport
        for sport_name, sport_slug in SPORTS.items():
            url = f'https://www.10bet.se/sports/{sport_slug}/competitions'
            print(f'\n=== {sport_name} ({url}) ===', flush=True)

            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                await asyncio.sleep(3)

                current = page.url
                if current != url:
                    print(f'  Redirected to: {current}', flush=True)

                comps = await page.evaluate(JS_FIND_COMPS)

                print(f'  Found {len(comps)} competitions', flush=True)
                for c in comps[:5]:
                    print(f'    {c["text"]} -> {c["href"]}', flush=True)
                if len(comps) > 5:
                    print(f'    ... and {len(comps) - 5} more', flush=True)

                if not comps:
                    sidebar = await page.evaluate(JS_PAGE_PREVIEW)
                    print(f'  Page content preview: {sidebar[:200]}', flush=True)

                    title = await page.title()
                    print(f'  Page title: {title}', flush=True)

                    all_links = await page.evaluate(JS_SPORT_LINKS)
                    if all_links:
                        print(f'  Sport links found:', flush=True)
                        for l in all_links:
                            print(f'    {l["text"]} -> {l["href"]}', flush=True)

            except Exception as e:
                print(f'  ERROR: {e}', flush=True)

        # Check first football competition matches page
        print('\n\n=== Sample football competition ===', flush=True)
        await page.goto('https://www.10bet.se/sports/football/competitions', wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(3)

        first_comp = await page.evaluate(JS_FIRST_COMP)

        if first_comp:
            print(f'Loading first competition: {first_comp["text"]} (ID: {first_comp["id"]})', flush=True)
            await page.goto(f'https://www.10bet.se/sports/football/competitions/{first_comp["id"]}/matches', wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(3)

            try:
                await page.wait_for_selector('[class*="ta-EventListItem"]', timeout=10000)
            except:
                pass

            event_info = await page.evaluate(JS_EVENT_INFO)

            print(f'Events found: {event_info["count"]}', flush=True)
            print(f'Sample event:', flush=True)
            print(f'  Participants: {event_info.get("participants", [])}', flush=True)
            print(f'  Prices: {event_info.get("prices", [])}', flush=True)
            print(f'  Markets: {event_info.get("markets", [])}', flush=True)
            print(f'  Timing: {event_info.get("timing", "")}', flush=True)

        await browser.close()

asyncio.run(main())
