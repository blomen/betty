"""
Test script to investigate 10bet.se - Final: DOM scraping with proper cookie handling.
"""
import json
from playwright.sync_api import sync_playwright


def main():
    print("=" * 80)
    print("10BET.SE - FINAL DOM SCRAPING TEST")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="sv-SE",
        )
        page = context.new_page()

        # First load home page and handle cookie
        print("\n--- Step 1: Handle cookie consent ---", flush=True)
        page.goto("https://www.10bet.se/sports", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Handle cookie consent WITHOUT navigating away
        # The "OK" button navigates to cookie-settings. Instead, try to dismiss the overlay
        try:
            # Try clicking the overlay close button or a less destructive consent button
            dismissed = False
            for sel in [
                'button:has-text("Acceptera alla")',
                'button:has-text("Acceptera")',
                '[class*="CookiesRegulation"] button:first-of-type',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        el.click()
                        print(f"  Clicked: {sel}", flush=True)
                        dismissed = True
                        break
                except Exception:
                    pass

            if not dismissed:
                # Just set a cookie to skip the banner
                print("  Setting cookie to skip banner...", flush=True)
                context.add_cookies([{
                    "name": "cookie_consent",
                    "value": "accepted",
                    "domain": ".10bet.se",
                    "path": "/",
                }])
                page.reload(wait_until="domcontentloaded")
        except Exception as e:
            print(f"  Cookie handling: {e}", flush=True)

        page.wait_for_timeout(3000)

        # Navigate to Premier League
        print("\n--- Step 2: Navigate to Premier League ---", flush=True)
        page.goto("https://www.10bet.se/sports/football/competitions/9116/matches",
                   wait_until="domcontentloaded", timeout=30000)

        # Wait for widget to fully render
        print("  Waiting 15s for render...", flush=True)
        page.wait_for_timeout(15000)
        print(f"  URL: {page.url}", flush=True)

        # If we got redirected to cookie-settings, navigate back
        if "cookie" in page.url.lower():
            print("  Got redirected to cookie page, navigating back...", flush=True)
            page.goto("https://www.10bet.se/sports/football/competitions/9116/matches",
                       wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(15000)
            print(f"  URL now: {page.url}", flush=True)

        # Dump all ta- classes to understand the widget structure
        print("\n--- Step 3: DOM Analysis ---", flush=True)
        analysis = page.evaluate("""() => {
            const result = {
                url: window.location.href,
                ta_classes: [],
                eventListItems: [],
                marketHeaders: [],
                selectionButtons: [],
                participantNames: [],
                priceTexts: [],
                allOddsLikeText: [],
            };

            // All unique ta- classes
            const taSet = new Set();
            document.querySelectorAll('[class*="ta-"]').forEach(el => {
                Array.from(el.classList).filter(c => c.startsWith('ta-')).forEach(c => taSet.add(c));
            });
            result.ta_classes = Array.from(taSet).sort();

            // EventListItem elements (not EventRow - that doesn't exist)
            document.querySelectorAll('[class*="ta-EventListItem"]').forEach(el => {
                result.eventListItems.push({
                    classes: Array.from(el.classList).filter(c => c.startsWith('ta-')),
                    text: el.innerText.substring(0, 300),
                    childTags: Array.from(el.children).map(c => c.tagName + '.' + Array.from(c.classList).filter(cl => cl.startsWith('ta-')).join('.')),
                });
            });

            // Market headers
            document.querySelectorAll('[class*="ta-EventList-header"]').forEach(el => {
                result.marketHeaders.push(el.innerText.substring(0, 200));
            });

            // Selection buttons
            document.querySelectorAll('[class*="ta-SelectionButtonView"]').forEach((el, idx) => {
                if (idx < 30) {
                    const parent = el.closest('[class*="ta-EventListItem"]');
                    result.selectionButtons.push({
                        text: el.innerText.trim(),
                        classes: Array.from(el.classList).filter(c => c.startsWith('ta-')),
                        parentItem: parent ? 'yes' : 'no',
                    });
                }
            });

            // Participant names
            document.querySelectorAll('[class*="ta-participantName"]').forEach(el => {
                result.participantNames.push(el.textContent.trim());
            });

            // Price texts
            document.querySelectorAll('[class*="ta-price_text"]').forEach(el => {
                result.priceTexts.push(el.textContent.trim());
            });

            return result;
        }""")

        print(f"  Page URL: {analysis['url']}", flush=True)
        print(f"\n  EventListItems: {len(analysis['eventListItems'])}", flush=True)
        for i, item in enumerate(analysis['eventListItems'][:5]):
            print(f"    Item {i}: {item['classes']}", flush=True)
            print(f"      Text: {item['text']!r}", flush=True)
            print(f"      Children: {item['childTags'][:5]}", flush=True)

        print(f"\n  Market headers: {analysis['marketHeaders']}", flush=True)

        print(f"\n  Participant names ({len(analysis['participantNames'])}):", flush=True)
        for name in analysis['participantNames'][:20]:
            print(f"    {name}", flush=True)

        print(f"\n  Price texts ({len(analysis['priceTexts'])}):", flush=True)
        for price in analysis['priceTexts'][:30]:
            print(f"    {price}", flush=True)

        print(f"\n  Selection buttons ({len(analysis['selectionButtons'])}):", flush=True)
        for sel in analysis['selectionButtons'][:20]:
            print(f"    {sel['text']!r} classes={sel['classes']}", flush=True)

        # Deep dive into event list items structure
        print("\n--- Step 4: Event Item Deep Dive ---", flush=True)
        deep_dive = page.evaluate("""() => {
            const items = document.querySelectorAll('[class*="ta-EventListItem"]');
            const results = [];

            for (let i = 0; i < Math.min(items.length, 5); i++) {
                const item = items[i];
                const data = {
                    index: i,
                    fullText: item.innerText,
                    participants: [],
                    markets: [],
                    selections: [],
                    timing: '',
                    href: '',
                };

                // Get event link
                const link = item.querySelector('a[href*="/events/"]');
                if (link) data.href = link.getAttribute('href');

                // Timing
                const timing = item.querySelector('[class*="ta-EventTimingStatus"], [class*="Timing"]');
                if (timing) data.timing = timing.textContent.trim();

                // Participants
                item.querySelectorAll('[class*="ta-participantName"]').forEach(p => {
                    data.participants.push(p.textContent.trim());
                });

                // Markets (by MarketType class)
                item.querySelectorAll('[class*="ta-MarketType-"]').forEach(m => {
                    const marketType = Array.from(m.classList).find(c => c.startsWith('ta-MarketType-'));
                    const prices = [];
                    m.querySelectorAll('[class*="ta-price_text"]').forEach(p => {
                        prices.push(p.textContent.trim());
                    });
                    // Also get info text (spread/total points)
                    const infoTexts = [];
                    m.querySelectorAll('[class*="ta-infoText"]').forEach(t => {
                        infoTexts.push(t.textContent.trim());
                    });
                    data.markets.push({
                        type: marketType || '?',
                        prices: prices,
                        infoTexts: infoTexts,
                    });
                });

                // Selections
                item.querySelectorAll('[class*="ta-SelectionButtonView"]').forEach(s => {
                    data.selections.push({
                        text: s.innerText.trim(),
                        classes: Array.from(s.classList).filter(c => c.startsWith('ta-')),
                    });
                });

                results.push(data);
            }
            return results;
        }""")

        for item in deep_dive:
            print(f"\n  === Event {item['index']} ===", flush=True)
            print(f"  Timing: {item['timing']!r}", flush=True)
            print(f"  Participants: {item['participants']}", flush=True)
            print(f"  Href: {item['href']}", flush=True)
            print(f"  Markets ({len(item['markets'])}):", flush=True)
            for m in item['markets']:
                print(f"    {m['type']}: prices={m['prices']} info={m['infoTexts']}", flush=True)
            print(f"  Full text: {item['fullText']!r}", flush=True)

        # Step 5: Check multi-sport navigation
        print("\n--- Step 5: Multi-sport competition navigation ---", flush=True)
        sports_map = {
            "football": "https://www.10bet.se/sports/football/competitions",
            "ice_hockey": "https://www.10bet.se/sports/ice_hockey/competitions",
            "basketball": "https://www.10bet.se/sports/basketball/competitions",
            "tennis": "https://www.10bet.se/sports/tennis/competitions",
            "american_football": "https://www.10bet.se/sports/american_football/competitions",
            "esports": "https://www.10bet.se/sports/esports/competitions",
            "handball": "https://www.10bet.se/sports/handball/competitions",
            "martial_arts": "https://www.10bet.se/sports/martial_arts/competitions",
        }
        for sport, url in sports_map.items():
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(5000)
                comps = page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="competitions/"]');
                    return Array.from(links)
                        .map(a => ({ text: a.textContent.trim(), href: a.getAttribute('href') }))
                        .filter(l => l.href && /\\/competitions\\/\\d+/.test(l.href))
                        .filter((v, i, a) => a.findIndex(x => x.href === v.href) === i);  // dedupe
                }""")
                print(f"  {sport}: {len(comps)} competitions", flush=True)
            except Exception as e:
                print(f"  {sport}: error - {str(e)[:60]}", flush=True)

        browser.close()

    print("\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)
    print("""
FINDINGS:
1. NO REST API endpoints exist for event/odds data
   - sportswidget.10bet.se only has: /health/status, /configuration/init, /betslip/init, /authentication/logout
   - sportswidget-cdn.10bet.se serves: JS bundles, translations, config, images
   - openapi.framegas3.com only used for geo-IP detection + chat JWT via Socket.IO

2. The sportsbook is a JS SPA widget that:
   - Loads from sportswidget-cdn.10bet.se as JS bundles
   - Fetches event/odds data internally (NOT through interceptable HTTP/WS)
   - Renders everything into the DOM with `ta-*` CSS class prefixes

3. DOM SCRAPING IS THE ONLY VIABLE APPROACH:
   - ta-EventListItem = event row container
   - ta-participantName = team names
   - ta-price_text = odds values
   - ta-MarketType-MRES = match result (1X2)
   - ta-MarketType-HCTG = total goals (over/under)
   - ta-MarketType-HCMR = handicap/spread
   - ta-infoText = spread/total point values
   - ta-EventTimingStatus = match date/time
   - a[href*="/events/"] = event detail links with numeric IDs

4. Competition navigation:
   - /sports/{sport}/competitions = list of competitions with numeric IDs
   - /sports/{sport}/competitions/{id}/matches = matches for a competition

5. Cookie consent MUST be handled carefully (clicking "OK" navigates away)
""")


if __name__ == "__main__":
    main()
