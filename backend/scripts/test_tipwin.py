"""Test script for Tipwin pagination analysis."""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stderr)

from src.core.transport import BrowserTransport


async def test():
    transport = BrowserTransport(headless=True)
    await transport._ensure_browser()
    page = transport.page

    api_data = []
    pending = []

    async def process(response):
        try:
            data = await response.json()
            if isinstance(data, dict):
                has_offer = 'offer' in data and isinstance(data.get('offer'), list) and len(data['offer']) > 0
                has_items = 'items' in data and isinstance(data.get('items'), list) and len(data['items']) > 0
                if has_offer or has_items:
                    total = data.get('totalNumberOfItems', '?')
                    pn = data.get('pageNumber', '?')
                    ps = data.get('pageSize', '?')
                    key = 'offer' if has_offer else 'items'
                    count = len(data.get(key, []))
                    print(f'  DATA[{key}]: page={pn} size={ps} total={total} count={count}', flush=True)
                    api_data.append(data)
        except Exception:
            pass

    def intercept(response):
        url = response.url
        if ('api-web.tipwin' in url or 'api-web-rest.tipwin' in url) and response.status == 200 and 'offer' in url:
            pending.append(asyncio.create_task(process(response)))

    page.on('response', intercept)

    # Setup
    await page.goto('https://www.tipwin.se', wait_until='load', timeout=30000)
    try:
        await page.click('button:has-text("Acceptera")', timeout=5000)
    except Exception:
        pass
    await asyncio.sleep(2)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    api_data.clear()
    pending.clear()

    # Navigate to full sports listing
    print('=== Navigating to /sv/sports/full/ ===', flush=True)
    await page.goto('https://www.tipwin.se/sv/sports/full/', wait_until='load', timeout=60000)
    await asyncio.sleep(8)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    print(f'Initial: {len(api_data)} responses', flush=True)

    # Find and analyze pagination
    pagination = await page.evaluate("""() => {
        const pTabs = document.querySelector('.pagination__tabs');
        if (!pTabs) return {found: false, html: ''};
        const children = pTabs.children;
        return {
            found: true,
            childCount: children.length,
            html: pTabs.innerHTML.substring(0, 500),
            items: Array.from(children).map(c => ({
                text: c.textContent.trim(),
                tag: c.tagName,
                cls: c.className.substring(0, 60)
            }))
        };
    }""")

    print(f'Pagination found: {pagination.get("found")}', flush=True)
    if pagination.get('found'):
        print(f'Children: {pagination.get("childCount")}', flush=True)
        for item in pagination.get('items', []):
            print(f'  {item}', flush=True)

    # Try clicking pagination numbers
    for page_num in range(2, 8):
        try:
            prev_count = len(api_data)
            # Try clicking by text content
            selector = f'.pagination__tabs >> text="{page_num}"'
            await page.click(selector, timeout=3000)
            await asyncio.sleep(5)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            new = len(api_data) - prev_count
            print(f'Page {page_num}: {new} new responses (total {len(api_data)})', flush=True)
        except Exception as e:
            print(f'Page {page_num}: failed - {str(e)[:80]}', flush=True)
            break

    # Also try the sport-menu approach - expand soccer in sidebar
    print('\n=== Checking sidebar sport tree ===', flush=True)
    sidebar_html = await page.evaluate("""() => {
        const aside = document.querySelector('.l--aside--left');
        if (!aside) return 'no sidebar';
        return aside.innerHTML.substring(0, 1000);
    }""")
    print(f'Sidebar HTML: {sidebar_html[:300]}', flush=True)

    # Count total events from all responses
    total_events = 0
    for d in api_data:
        if 'offer' in d and isinstance(d['offer'], list):
            total_events += len(d['offer'])
        if 'items' in d and isinstance(d['items'], list):
            total_events += len(d['items'])

    print(f'\nTotal events from API: {total_events}', flush=True)
    print(f'Total API responses: {len(api_data)}', flush=True)

    page.remove_listener('response', intercept)
    await transport.close()


if __name__ == '__main__':
    asyncio.run(test())
