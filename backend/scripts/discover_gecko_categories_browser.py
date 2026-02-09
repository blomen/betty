"""
Discover OBG/Gecko V2 sport category IDs using browser session.

1. Navigate to betsson.com to establish session
2. Capture API headers from browser requests
3. Call category-by-slug and events-table/v2 APIs
"""
import asyncio
import json
from playwright.async_api import async_playwright


SPORT_SLUGS = {
    "football": "fotboll",
    "basketball": "basket",
    "tennis": "tennis",
    "ice_hockey": "ishockey",
    "american_football": "amerikansk-fotboll",
    "baseball": "baseboll",
    "mma": "mma",
    "esports": "esports",
    "rugby": "rugby",
    "cricket": "cricket",
    "boxing": "boxning",
    "handball": "handboll",
    "volleyball": "volleyboll",
    "darts": "dart",
    "curling": "curling",
    "golf": "golf",
    "table_tennis": "bordtennis",
}

KNOWN_CATEGORIES = {
    "football": 1,
    "basketball": 3,
    "tennis": 4,
    "ice_hockey": 5,
    "rugby": 6,
    "handball": 7,
    "american_football": 10,
    "baseball": 12,
    "mma": 14,
    "esports": 20,
    "boxing": 16,
    "cricket": 15,
}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        page = await context.new_page()

        # Capture API headers
        captured_headers = {}
        api_base = None

        async def capture_route(route, request):
            nonlocal api_base
            url = request.url
            if '/api/sb/' in url and not captured_headers:
                captured_headers.update(dict(request.headers))
                idx = url.find('/api/sb/')
                api_base = url[:idx]
                print(f"[CAPTURED] API base: {api_base}")
            await route.continue_()

        await page.route('**/api/sb/**', capture_route)

        # Navigate to Betsson
        print("Navigating to betsson.com...")
        await page.goto("https://www.betsson.com/sv/odds", wait_until="load", timeout=60000)

        # Handle cookie consent
        try:
            await page.click('button:has-text("Acceptera")', timeout=5000)
            print("Cookie consent accepted")
        except Exception:
            pass

        await asyncio.sleep(5)
        await page.unroute('**/api/sb/**')

        if not captured_headers or not api_base:
            print("ERROR: Could not capture API headers!")
            await browser.close()
            return

        # Extract needed headers
        headers = {}
        for k, v in captured_headers.items():
            kl = k.lower()
            if kl.startswith(('x-sb-', 'x-obg-')) or kl in (
                'brandid', 'sessiontoken', 'marketcode', 'correlationid'
            ):
                headers[k] = v
        headers['accept'] = 'application/json'
        headers['content-type'] = 'application/json'

        print(f"\nCaptured {len(headers)} API headers")

        # Now test category IDs
        print("\n=== Category discovery via slug lookup ===\n")

        for sport, slug in sorted(SPORT_SLUGS.items()):
            url = f"{api_base}/api/sb/v1/widgets/category-by-slug/sv/{slug}"
            try:
                resp = await context.request.get(url, headers=headers)
                if resp.ok:
                    data = (await resp.json()).get("data", {})
                    cat_id = data.get("id", "?")
                    name = data.get("name", "?")
                    event_count = data.get("eventCount", 0)
                    known = KNOWN_CATEGORIES.get(sport, "NOT SET")
                    match_str = "✓" if cat_id == known else f"MISMATCH (was {known})" if known != "NOT SET" else "NEW"
                    print(f"  {sport:25s} slug={slug:20s} -> id={cat_id:>4} name={name:20s} events={event_count:>5} [{match_str}]")
                else:
                    print(f"  {sport:25s} slug={slug:20s} -> HTTP {resp.status}")
            except Exception as e:
                print(f"  {sport:25s} slug={slug:20s} -> Error: {e}")

            await asyncio.sleep(0.5)

        # Now verify with events-table API
        print("\n\n=== Verifying event counts per category ===\n")

        for sport, slug in sorted(SPORT_SLUGS.items()):
            cat_id = KNOWN_CATEGORIES.get(sport)
            if cat_id is None:
                continue

            url = (f"{api_base}/api/sb/v1/widgets/events-table/v2"
                   f"?categoryIds={cat_id}&phase=4&pageNumber=1"
                   f"&marketTemplateIds=MW3W,MW2W,MTG2W,TGOU,M3WHCP,M2WHCP")
            try:
                resp = await context.request.get(url, headers=headers)
                if resp.ok:
                    data = (await resp.json()).get("data", {})
                    total = data.get("totalItemCount", 0)
                    pages = data.get("totalPages", 0)
                    events = data.get("events", [])
                    print(f"  {sport:25s} cat={cat_id:>3}: {total:>5} events, {pages} pages, {len(events)} on page 1")
                    # Show first 3 events
                    for ev in events[:3]:
                        parts = ev.get("participants", [])
                        if len(parts) >= 2:
                            h = parts[0].get("label", "?")
                            a = parts[1].get("label", "?")
                            print(f"    {h} vs {a}")
                else:
                    print(f"  {sport:25s} cat={cat_id:>3}: HTTP {resp.status}")
            except Exception as e:
                print(f"  {sport:25s} cat={cat_id:>3}: Error: {e}")

            await asyncio.sleep(0.5)

        # Also scan IDs 1-50 to find any unknown categories
        print("\n\n=== Scanning all category IDs 1-50 ===\n")

        for cat_id in range(1, 51):
            url = (f"{api_base}/api/sb/v1/widgets/events-table/v2"
                   f"?categoryIds={cat_id}&phase=4&pageNumber=1"
                   f"&marketTemplateIds=MW3W,MW2W")
            try:
                resp = await context.request.get(url, headers=headers)
                if resp.ok:
                    data = (await resp.json()).get("data", {})
                    total = data.get("totalItemCount", 0)
                    if total > 0:
                        events = data.get("events", [])
                        sport_name = events[0].get("categoryName", "?") if events else "?"
                        known_sport = next((s for s, c in KNOWN_CATEGORIES.items() if c == cat_id), None)
                        status = f"(known: {known_sport})" if known_sport else "NEW - NOT IN MAP"
                        print(f"  ID {cat_id:>3}: {total:>5} events - {sport_name:20s} {status}")
            except Exception:
                pass

            await asyncio.sleep(0.3)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
