import asyncio
import json
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        page = await browser.new_page()

        api_responses = []
        api_urls_with_filters = []

        async def capture_response(response):
            url = response.url
            if (
                ("api-web.tipwin" in url)
                and response.status == 200
                and "offer/data" in url
            ):
                try:
                    data = await response.json()
                    if isinstance(data, dict):
                        meta = {
                            "url": url,
                            "keys": list(data.keys()),
                            "totalNumberOfItems": data.get("totalNumberOfItems"),
                            "pageSize": data.get("pageSize"),
                            "pageNumber": data.get("pageNumber"),
                            "items_count": len(data.get("items", [])),
                            "offer_count": len(data.get("offer", [])),
                        }

                        lookup = data.get("lookup", {})
                        if lookup:
                            meta["lookup_keys"] = list(lookup.keys())
                            sports = lookup.get("sports", {})
                            meta["sports_count"] = len(sports)
                            if sports:
                                for k, v in list(sports.items())[:5]:
                                    meta.setdefault("sports_sample", []).append(
                                        {
                                            "id": k,
                                            "name": v.get("name", ""),
                                            "abrv": v.get("abrv", ""),
                                        }
                                    )

                        if data.get("items"):
                            sport_counts = {}
                            for cat in data["items"]:
                                sport_id = cat.get("sportId", "unknown")
                                events_count = 0
                                for tg in cat.get("items", []):
                                    events_count += len(tg.get("events", []))
                                sport_counts[sport_id] = (
                                    sport_counts.get(sport_id, 0) + events_count
                                )
                            meta["events_per_sport"] = sport_counts

                        api_responses.append(meta)

                        if "filter=" in url:
                            filter_part = url.split("filter=")[1].split("&")[0]
                            api_urls_with_filters.append(
                                {
                                    "url_base": url.split("?")[0],
                                    "filter": filter_part[:100],
                                    "full_params": (
                                        url.split("?")[1] if "?" in url else ""
                                    ),
                                }
                            )
                except Exception as e:
                    print(f"Error parsing response: {e}", flush=True)

        page.on("response", capture_response)

        print("Loading tipwin.se...", flush=True)
        await page.goto("https://www.tipwin.se", wait_until="load", timeout=30000)
        for text in ["Acceptera", "Accept"]:
            try:
                await page.click(f'button:has-text("{text}")', timeout=3000)
                print("Clicked cookie consent", flush=True)
                break
            except Exception:
                continue
        await asyncio.sleep(3)

        print(f"Initial load responses: {len(api_responses)}", flush=True)
        for r in api_responses:
            print(
                f'  keys={r["keys"]}, total={r["totalNumberOfItems"]}, pageSize={r["pageSize"]}, page={r["pageNumber"]}, items={r["items_count"]}, offer={r["offer_count"]}',
                flush=True,
            )

        api_responses.clear()
        api_urls_with_filters.clear()

        print("\nNavigating to /sv/sports/full/...", flush=True)
        await page.goto(
            "https://www.tipwin.se/sv/sports/full/", wait_until="load", timeout=60000
        )
        await asyncio.sleep(5)

        print(f"\nFull page responses: {len(api_responses)}", flush=True)
        for r in api_responses:
            print(json.dumps(r, indent=2, default=str), flush=True)

        print("\nAPI URL patterns:", flush=True)
        for u in api_urls_with_filters:
            print(f'  base: {u["url_base"]}', flush=True)
            print(f'  filter: {u["filter"]}...', flush=True)
            params = u["full_params"]
            print(f"  full params (first 300): {params[:300]}", flush=True)

        print("\n\n=== Clicking to page 2 ===", flush=True)
        api_responses.clear()
        api_urls_with_filters.clear()

        next_clicked = await page.evaluate(
            """() => {
            const tabs = document.querySelector('.pagination__tabs');
            if (!tabs) return 'no tabs';
            const buttons = tabs.querySelectorAll('button');
            const texts = Array.from(buttons).map(b => b.textContent.trim());
            for (const btn of buttons) {
                if (btn.textContent.trim() === '2') {
                    btn.click();
                    return 'clicked page 2';
                }
            }
            return 'buttons: ' + texts.join(', ');
        }"""
        )
        print(f"Page 2 click result: {next_clicked}", flush=True)
        await asyncio.sleep(3)

        print(f"\nPage 2 responses: {len(api_responses)}", flush=True)
        for r in api_responses:
            print(
                f'  page={r["pageNumber"]}, items={r["items_count"]}, total={r["totalNumberOfItems"]}',
                flush=True,
            )

        if api_urls_with_filters:
            p2_filter = api_urls_with_filters[0]["filter"]
            print(f"\nPage 2 filter: {p2_filter}...", flush=True)
            print(
                f'Full params: {api_urls_with_filters[0]["full_params"][:500]}',
                flush=True,
            )

        print("\n\n=== Checking if we can manipulate page via URL ===", flush=True)

        current_url = page.url
        print(f"Current browser URL: {current_url}", flush=True)

        api_responses.clear()
        print("\nTrying /sv/sports/full/soccer/...", flush=True)
        await page.goto(
            "https://www.tipwin.se/sv/sports/full/soccer/",
            wait_until="load",
            timeout=30000,
        )
        await asyncio.sleep(4)

        print(f"Soccer page responses: {len(api_responses)}", flush=True)
        for r in api_responses:
            print(
                f'  page={r["pageNumber"]}, items={r["items_count"]}, total={r["totalNumberOfItems"]}, events_per_sport={r.get("events_per_sport", {})}',
                flush=True,
            )

        if api_responses:
            sport_events = api_responses[0].get("events_per_sport", {})
            print(f"\n  Events per sport on soccer page: {sport_events}", flush=True)

        await browser.close()


asyncio.run(main())
