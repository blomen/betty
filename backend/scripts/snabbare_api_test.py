import asyncio, json
try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chrome")
        page = await browser.new_page()
        ws_messages, ws_sent = [], []

        def on_ws(ws):
            print(f"WS opened: {ws.url}", flush=True)
            ws.on("framereceived", lambda payload: ws_messages.append(payload))
            ws.on("framesent", lambda payload: ws_sent.append(payload))

        page.on("websocket", on_ws)

        print("Loading sportsbook...", flush=True)
        await page.goto("https://www.snabbare.com/sv/sportsbook", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(8)
        print(f"After lobby: {len(ws_messages)} recv, {len(ws_sent)} sent", flush=True)

        print("\nWS Sent Messages:")
        for i, msg in enumerate(ws_sent[:15]):
            raw = msg if isinstance(msg, bytes) else msg.encode() if isinstance(msg, str) else msg
            print(f"  [{i}]: {raw[:300]}", flush=True)

        ws_messages.clear()
        ws_sent.clear()

        # Navigate to football
        print("\nLooking for Fotboll...")
        fotboll = await page.query_selector("a[href*=\"/sportsbook/fotboll\"]")
        if fotboll:
            print("Clicking Fotboll...")
            await fotboll.click()
            await asyncio.sleep(5)
        else:
            sport_links = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="sportsbook"]')).slice(0,20).map(a=>({t:a.textContent.trim().substring(0,40),h:a.href}))""")
            print(f"Sport links: {json.dumps(sport_links, indent=2)}")

        print(f"After football: {len(ws_messages)} recv, {len(ws_sent)} sent")

        print("\nWS Sent After Football:")
        for i, msg in enumerate(ws_sent[:15]):
            raw = msg if isinstance(msg, bytes) else msg.encode() if isinstance(msg, str) else msg
            print(f"  [{i}]: {raw[:400]}", flush=True)

        def parse_ws(messages):
            events, markets, sels = [], [], []
            for msg in messages:
                raw = msg if isinstance(msg, bytes) else msg.encode()
                try:
                    idx = raw.find(b"[{")
                    if idx >= 0:
                        data = json.loads(raw[idx:])
                        for item in data:
                            p = item.get("payload", {})
                            events.extend(p.get("events", []))
                            markets.extend(p.get("markets", []))
                            sels.extend(p.get("selections", []))
                except: pass
            return events, markets, sels

        events, markets, sels = parse_ws(ws_messages)
        print(f"\nParsed: {len(events)} events, {len(markets)} markets, {len(sels)} selections")

        mt_map = {}
        for m in markets:
            mt = m.get("marketType", {})
            k = mt.get("id")
            if k not in mt_map: mt_map[k] = {"n": mt.get("originalName", mt.get("name","")), "c": 0}
            mt_map[k]["c"] += 1
        print("\nMarket Types:")
        for k, v in sorted(mt_map.items(), key=lambda x: -x[1]["c"]):
            print(f"  {k}: {v['n']} ({v['c']})")

        for e in events[:3]:
            eid = e["id"]
            em = [m for m in markets if m.get("eventId") == eid]
            es = [s for s in sels if s.get("eventId") == eid]
            print(f"\n  Event: {e.get('eventName')} (id={eid})")
            for m in em:
                mt = m.get("marketType", {})
                ms = [s for s in es if s.get("marketId") == m.get("id")]
                print(f"    Market: {mt.get('originalName')} (typeId={mt.get('id')})")
                for s in ms:
                    print(f"      {s.get('outcomeType'):8s} | {s.get('name'):30s} | odds={s.get('trueOdds')} | pts={s.get('points')}")

        # Navigate to PL
        ws_messages.clear()
        ws_sent.clear()
        print("\n\nLooking for Premier League...")
        pl = await page.query_selector("a[href*='premier-league']")
        if pl:
            print("Clicking PL...")
            await pl.click()
            await asyncio.sleep(8)
        else:
            print("No PL link found")

        print(f"After PL: {len(ws_messages)} recv, {len(ws_sent)} sent")
        print("\nWS Sent After PL:")
        for i, msg in enumerate(ws_sent[:10]):
            raw = msg if isinstance(msg, bytes) else msg.encode() if isinstance(msg, str) else msg
            print(f"  [{i}]: {raw[:400]}", flush=True)

        pev, pmk, psl = parse_ws(ws_messages)
        print(f"\nPL: {len(pev)} events, {len(pmk)} markets, {len(psl)} selections")

        pl_mt = {}
        for m in pmk:
            mt = m.get("marketType", {})
            k = mt.get("id")
            if k not in pl_mt: pl_mt[k] = {"n": mt.get("originalName",mt.get("name","")), "c": 0}
            pl_mt[k]["c"] += 1
        print("\nPL Market Types:")
        for k, v in sorted(pl_mt.items(), key=lambda x: -x[1]["c"]):
            print(f"  {k}: {v['n']} ({v['c']})")

        for e in pev[:5]:
            eid = e["id"]
            em = [m for m in pmk if m.get("eventId") == eid]
            es = [s for s in psl if s.get("eventId") == eid]
            print(f"\n  Event: {e.get('eventName')} (start={e.get('startingOn')})")
            for m in em:
                mt = m.get("marketType", {})
                ms = [s for s in es if s.get("marketId") == m.get("id")]
                print(f"    Market: {mt.get('originalName')} (typeId={mt.get('id')})")
                for s in ms:
                    print(f"      {s.get('outcomeType'):8s} | {s.get('name'):30s} | odds={s.get('trueOdds')} | pts={s.get('points')}")

        # Default markets API
        print("\n\n=== Default Markets API ===")
        for sid in [1, 2, 3, 4, 6]:
            dm = await page.evaluate(f"""async () => {{
                const r = await fetch('https://www.snabbare.com/sportsbook-api/api/default-markets?franchiseCode=SWEDEN_SNABBARE&locale=sv&sportIds={sid}');
                return await r.json();
            }}""")
            print(f"  Sport {sid}: {json.dumps(dm)}")

        await browser.close()

asyncio.run(main())