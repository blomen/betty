"""
Discover Kambi brand slugs from sportsbook websites.

Uses Playwright to visit sportsbook sites and intercept requests to kambicdn.com
to extract the brand slug used in the Kambi API URL path.

Usage:
    python scripts/discover_kambi_brands.py [--url URL] [--all]

Examples:
    # Test a single URL
    python scripts/discover_kambi_brands.py --url https://www.paf.se/sport

    # Scan all candidate sites
    python scripts/discover_kambi_brands.py --all

    # Verify a discovered slug works
    python scripts/discover_kambi_brands.py --verify slugname
"""

import argparse
import asyncio
import sys
from typing import Optional

import aiohttp


# Candidate sportsbook URLs to scan
# Format: (name, sportsbook_url)
CANDIDATES = [
    ("Paf", "https://www.paf.se/sport"),
    ("ATG", "https://www.atg.se/sport"),
    ("Maria Casino", "https://www.mariacasino.se/sport"),
    ("iGame", "https://www.igame.com/sport"),
    ("32Red", "https://www.32red.com/sport"),
    ("Storspelare", "https://www.storspelare.com/sport"),
    ("Casumo", "https://www.casumo.se/sport"),
    ("Rizk", "https://www.rizk.com/sv/sport"),
    ("Guts", "https://www.guts.com/sport"),
    ("Napoleon", "https://www.napoleongames.be/sport"),
    ("Bwin", "https://sports.bwin.se"),
    ("ComeOn SE", "https://www.comeon.com/sv/sportsbook"),
    ("Kindred Brands", "https://www.mariacasino.se/odds"),
]


async def discover_slug_playwright(name: str, url: str, timeout: int = 30) -> Optional[str]:
    """
    Visit a sportsbook URL and intercept kambicdn.com requests to find the brand slug.

    The Kambi API URL format is:
        https://eu1.offering-api.kambicdn.com/offering/v2018/{brand_slug}/...

    Returns the brand slug or None.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install")
        sys.exit(1)

    found_slug = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        def handle_request(request):
            nonlocal found_slug
            url = request.url
            if "kambicdn.com" in url and "/offering/" in url:
                # Extract slug from URL: .../offering/v2018/{slug}/...
                parts = url.split("/offering/")
                if len(parts) > 1:
                    after = parts[1]  # e.g., "v2018/ubse/listView/..."
                    segments = after.split("/")
                    if len(segments) >= 2:
                        found_slug = segments[1]

        page.on("request", handle_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # Wait for API calls to happen
            await page.wait_for_timeout(5000)

            # Try clicking on a sport link if available to trigger more API calls
            if not found_slug:
                for selector in ["a[href*='sport']", "[data-sport]", ".sport-link"]:
                    try:
                        el = page.locator(selector).first
                        if await el.is_visible(timeout=2000):
                            await el.click()
                            await page.wait_for_timeout(3000)
                            if found_slug:
                                break
                    except Exception:
                        continue

        except Exception as e:
            print(f"  [{name}] Navigation error: {e}")
        finally:
            await browser.close()

    return found_slug


async def verify_slug(slug: str) -> bool:
    """Verify a Kambi slug works by fetching the group listing."""
    url = f"https://eu1.offering-api.kambicdn.com/offering/v2018/{slug}/group.json"
    params = {"market": "SE", "lang": "sv_SE"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    groups = data.get("group", {}).get("groups", [])
                    print(f"  Slug '{slug}' is VALID - {len(groups)} sport groups")
                    return True
                else:
                    print(f"  Slug '{slug}' returned HTTP {resp.status}")
                    return False
    except Exception as e:
        print(f"  Slug '{slug}' verification failed: {e}")
        return False


async def discover_slug_altenar(name: str, url: str, timeout: int = 30) -> Optional[str]:
    """
    Visit a sportsbook URL and intercept biahosted.com requests to find the Altenar integration ID.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        sys.exit(1)

    found_integration = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        def handle_request(request):
            nonlocal found_integration
            req_url = request.url
            if "biahosted.com" in req_url and "integration=" in req_url:
                # Extract integration param
                for part in req_url.split("&"):
                    if part.startswith("integration=") or "integration=" in part:
                        val = part.split("integration=")[-1].split("&")[0]
                        found_integration = val

        page.on("request", handle_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  [{name}] Navigation error: {e}")
        finally:
            await browser.close()

    return found_integration


async def main():
    parser = argparse.ArgumentParser(description="Discover Kambi brand slugs")
    parser.add_argument("--url", help="Single URL to test")
    parser.add_argument("--name", help="Name for single URL test", default="Test")
    parser.add_argument("--all", action="store_true", help="Scan all candidates")
    parser.add_argument("--verify", help="Verify a slug works")
    parser.add_argument("--altenar-url", help="Check a URL for Altenar integration ID")
    args = parser.parse_args()

    if args.verify:
        await verify_slug(args.verify)
        return

    if args.url:
        print(f"Scanning {args.name}: {args.url}")
        slug = await discover_slug_playwright(args.name, args.url)
        if slug:
            print(f"  Found Kambi slug: {slug}")
            await verify_slug(slug)
        else:
            print("  No Kambi slug found")

            # Try Altenar
            integration = await discover_slug_altenar(args.name, args.url)
            if integration:
                print(f"  Found Altenar integration: {integration}")
            else:
                print("  No Altenar integration found either")
        return

    if args.altenar_url:
        print(f"Scanning for Altenar: {args.altenar_url}")
        integration = await discover_slug_altenar("Test", args.altenar_url)
        if integration:
            print(f"  Found Altenar integration: {integration}")
        else:
            print("  No Altenar integration found")
        return

    if args.all:
        print("Scanning all candidate sportsbooks...\n")
        results = []
        for name, url in CANDIDATES:
            print(f"[{name}] {url}")
            slug = await discover_slug_playwright(name, url)
            if slug:
                print(f"  Found Kambi slug: {slug}")
                valid = await verify_slug(slug)
                results.append((name, url, "kambi", slug, valid))
            else:
                print("  No Kambi slug found, trying Altenar...")
                integration = await discover_slug_altenar(name, url)
                if integration:
                    print(f"  Found Altenar integration: {integration}")
                    results.append((name, url, "altenar", integration, True))
                else:
                    print("  No platform found")
                    results.append((name, url, None, None, False))
            print()

        # Summary
        print("\n" + "=" * 70)
        print("DISCOVERY RESULTS")
        print("=" * 70)
        for name, url, platform, slug, valid in results:
            status = "VALID" if valid else "INVALID" if slug else "NOT FOUND"
            platform_str = f"{platform}:{slug}" if slug else "-"
            print(f"  {name:20s} | {platform_str:30s} | {status}")
        return

    parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
