#!/usr/bin/env python3
"""Scan provider frontpages for odds boost content using Playwright."""

import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SITES = {
    # ComeOn group - known boost pages
    "comeon": "https://www.comeon.com/sv/sportsbook/sport/37-odds-boosts",
    "hajper": "https://www.hajper.com/sv/sportsbook/sport/37-odds-boosts",
    "lyllo": "https://www.lyllocasino.com/sv/sportsbook/sport/37-odds-boosts",
    # Spectate
    "mrgreen": "https://www.mrgreen.se/sport/odds-boost/",
    "888sport": "https://www.888sport.se/sport/",
    # Gecko V2 / OBG
    "bethard": "https://www.bethard.com/sv/sports/oddsboost",
    "spelklubben": "https://www.spelklubben.se/sv/betting/oddsboost",
    # Standalone
    "coolbet": "https://www.coolbet.com/sv/oddsboost",
    "vbet": "https://www.vbet.se",
    "tipwin": "https://www.tipwin.se",
    "snabbare": "https://www.snabbare.com/sv",
    "10bet": "https://www.10bet.se",
    # Kambi
    "leovegas": "https://www.leovegas.se/sv-se/betting",
    "betmgm": "https://www.betmgm.se/sv/sport",
    "speedybet": "https://www.speedybet.com/sv/betting",
    "x3000": "https://www.x3000.se/betting",
    "goldenbull": "https://www.goldenbull.se/betting",
    "1x2": "https://www.1x2.se/betting",
}

BOOST_KW = [
    "odds boost",
    "oddsboost",
    "odds-boost",
    "förhöjda odds",
    "förhöjda",
    "guldboost",
    "super boost",
    "superboost",
    "ökade odds",
    "enhanced",
    "boosted",
    "priceboost",
    "dagens spel",
]

API_KW = ["boost", "bonus", "featured", "special", "enhanced", "highlight", "promo", "priceboost", "globalbonuses"]


async def scan_site(context, name, url):
    """Scan a single site for boost content."""
    result = {
        "name": name,
        "url": url,
        "boost_apis": [],
        "ws_boost": [],
        "keywords_html": [],
        "keywords_visible": [],
        "all_api_count": 0,
        "error": None,
    }

    page = await context.new_page()
    boost_apis = []
    all_api_count = 0
    ws_boost = []

    async def on_response(response):
        nonlocal all_api_count
        u = response.url.lower()
        if "/api/" in u or ".json" in u or "/v1/" in u or "/v2/" in u or "playground" in u or "spectate" in u:
            all_api_count += 1
        if any(kw in u for kw in API_KW):
            entry = {"url": response.url, "status": response.status}
            try:
                body = await response.text()
                entry["size"] = len(body)
                entry["preview"] = body[:300]
            except Exception:
                pass
            boost_apis.append(entry)

    page.on("response", on_response)

    def on_ws(ws):
        def on_frame(data):
            if isinstance(data, str) and any(kw in data.lower() for kw in ["boost", "enhanced"]):
                ws_boost.append(data[:200])

        ws.on("framereceived", lambda d: on_frame(d))

    page.on("websocket", on_ws)

    try:
        await page.goto(url, wait_until="load", timeout=20000)
        await asyncio.sleep(3)

        # Cookie consent
        for sel in [
            "#onetrust-accept-btn-handler",
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            'button:has-text("Godkänn")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        await asyncio.sleep(4)

        # Scroll
        for i in range(4):
            await page.evaluate(f"window.scrollTo(0, {(i + 1) * 800})")
            await asyncio.sleep(0.8)

        # Check HTML
        content = (await page.content()).lower()
        result["keywords_html"] = [kw for kw in BOOST_KW if kw in content]

        # Check visible text
        try:
            body_text = await page.inner_text("body")
            body_lower = body_text.lower()
            for kw in BOOST_KW:
                if kw in body_lower:
                    idx = body_lower.find(kw)
                    snippet = body_text[max(0, idx - 15) : idx + 60].strip()
                    result["keywords_visible"].append(f'{kw}: "{snippet}"')
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)[:120]
    finally:
        await page.close()

    result["boost_apis"] = boost_apis
    result["ws_boost"] = ws_boost
    result["all_api_count"] = all_api_count
    return result


async def main():
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        from playwright.async_api import async_playwright

    # Select which sites to scan from CLI args
    if len(sys.argv) > 1:
        names = sys.argv[1:]
        sites = {k: v for k, v in SITES.items() if k in names}
    else:
        sites = SITES

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="sv-SE",
        )

        for name, url in sites.items():
            print(f"\n{'=' * 60}")
            print(f"  {name.upper()} — {url}")
            print(f"{'=' * 60}")

            r = await scan_site(context, name, url)

            if r["error"]:
                print(f"  ERROR: {r['error']}")

            if r["keywords_html"]:
                print(f"  HTML keywords: {r['keywords_html']}")
            else:
                print("  HTML keywords: NONE")

            if r["keywords_visible"]:
                print("  Visible text:")
                for v in r["keywords_visible"][:5]:
                    print(f"    {v}")

            if r["boost_apis"]:
                print(f"  Boost API calls ({len(r['boost_apis'])}):")
                for api in r["boost_apis"][:6]:
                    print(f"    [{api['status']}] {api['url'][:120]}")
                    if "preview" in api and api.get("size", 0) > 20:
                        print(f"         size={api['size']}  {api['preview'][:200]}")
            else:
                print("  Boost API calls: NONE")

            if r["ws_boost"]:
                print(f"  WS boost frames ({len(r['ws_boost'])}):")
                for msg in r["ws_boost"][:3]:
                    print(f"    {msg[:150]}")

            print(f"  Total API calls intercepted: {r['all_api_count']}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
