"""
Validate that ComeOn's SPA hydrates on concurrent Camoufox pages.

RESULT (2026-03-20): FAILED — SPA does NOT hydrate on concurrent pages.
Tested with 15s and 30s timeouts, staggered navigation. Page 0 always times out
waiting for [data-at="game-card"]. The existing comment in comeon_multileague.py
(lines 351-352) is confirmed correct. Concurrent page approach abandoned.

Run manually (requires Camoufox + network):
    cd backend && python -m pytest tests/test_comeon_concurrent.py -v -s
"""
import asyncio
import pytest

# SKIPPED — validation FAILED 2026-03-20, concurrent pages don't work
pytestmark = pytest.mark.skipif(
    True,
    reason="Validation FAILED 2026-03-20: ComeOn SPA doesn't hydrate on concurrent pages"
)


@pytest.mark.asyncio
async def test_concurrent_pages_hydrate():
    """Open 2 ComeOn league pages concurrently and verify both render game cards."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        pytest.skip("camoufox not installed")

    LEAGUE_URLS = [
        "https://www.comeon.com/sv/sportsbook/sport/1-fotboll/leagues/1-england-premier-league",
        "https://www.comeon.com/sv/sportsbook/sport/1-fotboll/leagues/3-england-championship",
    ]

    async with AsyncCamoufox(headless=True, geoip=True, humanize=0.2, os="windows") as browser:
        # Warm up — pass Cloudflare
        page0 = await browser.new_page()
        await page0.goto("https://www.comeon.com/sv", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        try:
            btn = await page0.query_selector('#onetrust-accept-btn-handler')
            if btn:
                await btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass
        await page0.close()

        pages = []
        for url in LEAGUE_URLS:
            p = await browser.new_page()
            pages.append(p)
            await asyncio.sleep(1.0)

        async def navigate_and_check(page, url):
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            await page.wait_for_selector('[data-at="game-card"]', timeout=30000)
            count = await page.evaluate(
                '() => document.querySelectorAll(\'[data-at="game-card"]\').length'
            )
            return count

        task0 = asyncio.create_task(navigate_and_check(pages[0], LEAGUE_URLS[0]))
        await asyncio.sleep(2)
        task1 = asyncio.create_task(navigate_and_check(pages[1], LEAGUE_URLS[1]))
        results = await asyncio.gather(task0, task1, return_exceptions=True)

        for p in pages:
            await p.close()

    for i, result in enumerate(results):
        assert not isinstance(result, Exception), (
            f"Page {i} failed: {result}. "
            f"SPA does NOT hydrate on concurrent pages — abandon concurrent approach."
        )
        assert result > 0, f"Page {i} rendered 0 game cards"

    print(f"VALIDATION PASSED: Page 0 = {results[0]} cards, Page 1 = {results[1]} cards")
