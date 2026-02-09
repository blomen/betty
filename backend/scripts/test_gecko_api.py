"""Investigate Betsson (Gecko V2) API endpoints to find main odds data."""
import asyncio
import logging
import sys
import json

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stderr)

from src.core.transport import BrowserTransport


async def test():
    transport = BrowserTransport(headless=False, channel='chrome')
    await transport._ensure_browser()
    page = transport.page

    api_calls = []
    pending = []

    async def process(response):
        try:
            url = response.url
            ct = response.headers.get('content-type', '')
            if 'json' in ct or 'javascript' in ct:
                try:
                    data = await response.json()
                except Exception:
                    return

                # Summary of what's in this response
                summary = {}
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, list):
                            summary[k] = f"list[{len(v)}]"
                        elif isinstance(v, dict):
                            summary[k] = f"dict[{len(v)}]"
                        else:
                            summary[k] = type(v).__name__

                    # Check nested 'data' key
                    if 'data' in data and isinstance(data['data'], dict):
                        for k, v in data['data'].items():
                            if isinstance(v, list):
                                summary[f'data.{k}'] = f"list[{len(v)}]"

                api_calls.append({
                    'url': url.split('?')[0],
                    'params': url.split('?')[1][:200] if '?' in url else '',
                    'status': response.status,
                    'summary': summary,
                    'raw_keys': list(data.keys()) if isinstance(data, dict) else 'non-dict',
                    'data': data,
                })
        except Exception as e:
            pass

    def intercept(response):
        url = response.url
        if response.status == 200 and '/api/' in url:
            pending.append(asyncio.create_task(process(response)))

    page.on('response', intercept)

    # Navigate to Betsson football page
    print('=== Loading betsson.com/sv/odds/fotboll ===', flush=True)
    await page.goto('https://www.betsson.com/sv/odds/fotboll', wait_until='load', timeout=60000)

    # Handle cookie consent
    for sel in ['button:has-text("Acceptera")', 'button:has-text("Accept")', '#accept-cookies']:
        try:
            await page.click(sel, timeout=3000)
            print(f'Clicked cookie consent: {sel}', flush=True)
            break
        except Exception:
            continue

    await asyncio.sleep(10)  # Wait for all API calls

    # Scroll to trigger more loading
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    await asyncio.sleep(3)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(3)

    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    page.remove_listener('response', intercept)

    print(f'\n=== Captured {len(api_calls)} API responses ===\n', flush=True)

    for i, call in enumerate(api_calls):
        url_short = call['url'].replace('https://www.betsson.com', '')
        print(f'[{i+1}] {url_short}', flush=True)
        print(f'    Status: {call["status"]}', flush=True)

        # Show summary of interesting fields
        summary = call['summary']
        interesting = {k: v for k, v in summary.items()
                      if 'list' in str(v) and int(str(v).split('[')[1].rstrip(']')) > 0}
        if interesting:
            print(f'    Data: {interesting}', flush=True)

        # Show params (shortened)
        if call['params']:
            print(f'    Params: {call["params"][:150]}', flush=True)
        print(flush=True)

    # Look specifically for responses with events/markets
    print('=== Responses with events/markets ===', flush=True)
    for i, call in enumerate(api_calls):
        data = call['data']
        has_events = False
        event_count = 0
        market_count = 0

        if isinstance(data, dict):
            # Check top level
            for key in ['events', 'matches', 'fixtures', 'items', 'results']:
                if key in data and isinstance(data[key], list):
                    has_events = True
                    event_count = len(data[key])

            # Check nested data
            nested = data.get('data', {})
            if isinstance(nested, dict):
                for key in ['events', 'matches', 'fixtures', 'items', 'results', 'markets', 'marketSelections']:
                    if key in nested and isinstance(nested[key], list) and len(nested[key]) > 0:
                        has_events = True
                        if 'event' in key or 'match' in key or 'fixture' in key:
                            event_count = len(nested[key])
                        if 'market' in key:
                            market_count += len(nested[key])

        if has_events:
            url_short = call['url'].replace('https://www.betsson.com', '')
            print(f'  [{i+1}] {url_short}', flush=True)
            print(f'      events={event_count} markets={market_count}', flush=True)
            # Show first event structure
            nested = data.get('data', data)
            for key in ['events', 'matches', 'fixtures']:
                items = nested.get(key, [])
                if items and len(items) > 0:
                    print(f'      First {key} keys: {list(items[0].keys())[:15]}', flush=True)
                    # Show participant info
                    p = items[0].get('participants', [])
                    if p:
                        print(f'      Participants: {[pp.get("label") for pp in p[:2]]}', flush=True)
                    break

    await transport.close()


if __name__ == '__main__':
    asyncio.run(test())
