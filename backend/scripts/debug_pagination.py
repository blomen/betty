
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("debug_pagination")

def print_flush(msg):
    print(msg)
    sys.stdout.flush()

async def main():
    transport = BrowserTransport(headless=True)
    # Instantiate
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        # Navigate to a league likely to have successful pagination (Premier League or similar)
        # We need to find a league ID first.
        # ID 10204 (CL) or 10188 (EPL) - let's try to search for EPL
        
        # Or just use the scraping logic to pick the biggest league
        leagues = await retriever._fetch_api("/v2/leagues", params={
             "filter.sportId": 1, 
             "page": 1, 
             "pageSize": 50
        })
        
        target = None
        if leagues and isinstance(leagues, dict) and 'leagues' in leagues:
             # Sort by eventCount
             sorted_leagues = sorted(leagues['leagues'], key=lambda x: x.get('eventCount', 0), reverse=True)
             target = sorted_leagues[0]
        
        if not target:
            print_flush("No leagues found")
            return

        print_flush(f"Targeting league: {target.get('name')} (Events: {target.get('eventCount')})")
        lid = target.get('id')
        url = f"{retriever.site_url}/sv/sportsbook/leagues/{lid}"
        
        print_flush(f"Navigating to {url}")
        await transport.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await transport.page.wait_for_timeout(5000) # Open wait
        
        # Debug "Show More"
        print_flush("Checking for 'Show more' button...")
        
        # Dump buttons
        buttons = await transport.page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('button')).map(b => ({
                    text: b.innerText,
                    classes: b.className,
                    visible: b.offsetParent !== null
                }));
            }
        """)
        
        print_flush(f"Found {len(buttons)} buttons. potentially relevant:")
        for b in buttons:
            txt = b['text'].lower()
            if 'visa' in txt or 'show' in txt or 'more' in txt:
                print_flush(f"  MATCH: {b}")
                
        # Try the Click logic from the class
        print_flush("Attempting click logic...")
        res = await transport.page.evaluate("""
            async () => {
                const xpath = "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'visa mer') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'visa fler')]";
                const matchingElement = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                
                if (matchingElement) {
                    matchingElement.click();
                    return "Clicked";
                }
                return "Not Found";
            }
        """)
        print_flush(f"Click Result: {res}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
