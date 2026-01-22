
import asyncio
import logging
import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

async def main():
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        # Get top league
        leagues = await retriever._fetch_api("/v2/leagues", params={
             "filter.sportId": 1, 
             "page": 1, 
             "pageSize": 50
        })
        
        target = None
        if leagues and isinstance(leagues, dict) and 'leagues' in leagues:
             sorted_leagues = sorted(leagues['leagues'], key=lambda x: x.get('eventCount', 0), reverse=True)
             target = sorted_leagues[0]
        
        if not target:
             print("No leagues found via API. Trying fallback URL.")
             # Fallback to a known huge league (Premier League or CL)
             # Snabbare URLs: /sv/sportsbook/leagues/{id}
             # IDs might need to be discovered, but let's try a common one or Search?
             # Let's try navigating to "Football" main page and clicking "Premier League" if possible?
             # Or just /sv/sportsbook which usually has top leagues.
             url = f"{retriever.site_url}/sv/sportsbook/leagues/10188" # EPL often has this ID in Kambi, maybe different here.
             # Actually safer to go to /sv/sportsbook/sports/1 (Football)
             url = f"{retriever.site_url}/sv/sportsbook/sports/1"

        else:
            lid = target.get('id')
            url = f"{retriever.site_url}/sv/sportsbook/leagues/{lid}"
            
        print(f"Navigating to {url}")
        await transport.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await transport.page.wait_for_timeout(5000)
        
        # Dump buttons
        buttons = await transport.page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('button')).map(b => ({
                    text: b.innerText,
                    classes: b.className,
                    outerHTML: b.outerHTML
                }));
            }
        """)
        
        with open("buttons_dump.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {url}\n")
            f.write(f"Buttons found: {len(buttons)}\n")
            for b in buttons:
                if 'visa' in b['text'].lower() or 'show' in b['text'].lower():
                    f.write(f"MATCH: {json.dumps(b)}\n")
                elif 'ladda' in b['text'].lower():
                    f.write(f"MATCH_LADDA: {json.dumps(b)}\n")
                    
    except Exception as e:
        with open("buttons_dump.txt", "w") as f: f.write(f"Error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
