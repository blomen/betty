
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discover_sports")

async def main():
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    try:
        await retriever._ensure_init()
        
        logger.info("Scraping sports from DOM...")
        
        # Wait for left menu or sport list
        # Selectors based on common Kambi/SBTech/MTS layouts or specific Snabbare classes
        # Snabbare uses SBTech/Kambi mixed or custom? It says "Sportradar MTS" in snabbare.py docstring.
        # Let's inspect the page content broadly.
        
        data = await transport.page.evaluate("""
            () => {
                const sports = [];
                // Try to find any list items that look like sports
                // Usually in a sidebar
                const links = Array.from(document.querySelectorAll('a[href*="/sportsbook/leagues/"], a[href*="/sportsbook/sports/"]'));
                
                links.forEach(l => {
                    const href = l.getAttribute('href');
                    const text = l.innerText.trim();
                    sports.push({text, href});
                });
                
                // Also check for specific sport menu items if they have data attributes
                const menuItems = Array.from(document.querySelectorAll('[data-at="sports-menu-item"]'));
                menuItems.forEach(i => {
                    sports.push({
                        text: i.innerText,
                        id: i.getAttribute('data-id') || 'unknown',
                        href: 'unknown'
                    });
                });
                
                return sports;
            }
        """)
        
        print(f"Found {len(data)} potential sport links/items")
        for item in data:
            print(item)
            
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        # Proper cleanup
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
