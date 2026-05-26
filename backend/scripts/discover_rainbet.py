"""One-shot discovery harness for rainbet.com.

Runs patchright + Chromium against rainbet's sportsbook, clears Cloudflare
Turnstile, navigates each supported sport, captures HTTP responses (with
bodies) and WebSocket frames, and writes everything to /tmp/rainbet_discovery/.

Run: python scripts/discover_rainbet.py
Inside container: docker compose exec -T backend python scripts/discover_rainbet.py

Produces:
  /tmp/rainbet_discovery/capture.har        — full HAR
  /tmp/rainbet_discovery/responses.jsonl    — *.sptpub.com response bodies
  /tmp/rainbet_discovery/ws_frames.jsonl    — sptpub WS frames (text + bin hex)
  /tmp/rainbet_discovery/summary.json       — host counts, timings, sport map
"""

import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from patchright.async_api import async_playwright

OUT = Path("/tmp/rainbet_discovery")
OUT.mkdir(parents=True, exist_ok=True)

SPORTS_TO_PROBE = [
    "soccer",
    "football",
    "basketball",
    "tennis",
    "ice-hockey",
    "american-football",
    "baseball",
    "mma",
    "boxing",
    "esports",
    "esports/counter-strike",
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
SITE_URL = "https://rainbet.com/sportsbook"


def proxy_dict():
    pu = os.environ.get("PROXY_URL")
    if not pu:
        return None
    p = urlparse(pu)
    out = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        out["username"] = p.username
        out["password"] = p.password or ""
    return out


def is_betby_host(url: str) -> bool:
    h = urlparse(url).hostname or ""
    return any(d in h for d in ("sptpub.com", "invisiblesport"))


async def clear_turnstile(page, timeout_s=60.0):
    end = time.time() + timeout_s
    while time.time() < end:
        # Stop if a Betby host has started talking
        cookies = await page.context.cookies()
        if any(c["name"] == "cf_clearance" for c in cookies):
            try:
                ts_iframe = await page.query_selector(
                    "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"
                )
                if ts_iframe is None:
                    return True
            except Exception:
                pass
        # Click at validated coord (per CF-Clearance-Scraper)
        try:
            await page.mouse.click(210, 290)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
    return False


async def run():
    proxy = proxy_dict()
    print(f"proxy: {proxy}")

    summary = {
        "started_at": time.time(),
        "proxy": proxy,
        "site_url": SITE_URL,
        "host_request_counts": {},
        "ws_opens": [],
        "sport_results": {},
    }
    response_log = []
    ws_log = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-http2", "--disable-quic"],
            proxy=proxy,
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
            record_har_path=str(OUT / "capture.har"),
            record_har_content="embed",
        )
        context.set_default_timeout(60_000)
        page = await context.new_page()

        host_counts = {}

        def on_request(req):
            h = urlparse(req.url).hostname or "?"
            host_counts[h] = host_counts.get(h, 0) + 1

        async def on_response(resp):
            if not is_betby_host(resp.url):
                return
            try:
                body = await resp.body()
                txt = body.decode("utf-8", errors="replace") if body else ""
            except Exception as e:
                txt = f"<err:{e}>"
            response_log.append(
                {
                    "ts": round(time.time() - summary["started_at"], 2),
                    "url": resp.url,
                    "status": resp.status,
                    "content_type": resp.headers.get("content-type", ""),
                    "body_size": len(body) if body else 0,
                    "body_sample": txt[:8000],
                }
            )

        def on_ws(ws):
            host = urlparse(ws.url).hostname or "?"
            summary["ws_opens"].append({"url": ws.url, "host": host})
            if "sptpub.com" not in (host or ""):
                return

            def push(direction, payload):
                if isinstance(payload, bytes):
                    ws_log.append(
                        {
                            "direction": direction,
                            "host": host,
                            "url": ws.url,
                            "kind": "bin",
                            "len": len(payload),
                            "data": payload[:2000].hex(),
                            "ts": round(time.time() - summary["started_at"], 2),
                        }
                    )
                else:
                    ws_log.append(
                        {
                            "direction": direction,
                            "host": host,
                            "url": ws.url,
                            "kind": "txt",
                            "len": len(payload),
                            "data": payload[:4000],
                            "ts": round(time.time() - summary["started_at"], 2),
                        }
                    )

            ws.on("framereceived", lambda p: push("recv", p))
            ws.on("framesent", lambda p: push("send", p))

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        page.on("websocket", on_ws)

        print(f"goto {SITE_URL}")
        await page.goto(SITE_URL, wait_until="domcontentloaded")

        print("clearing Turnstile…")
        cleared = await clear_turnstile(page)
        print(f"  cleared={cleared}")

        # Wait for sptpub activity to settle
        await page.wait_for_timeout(20_000)

        # Per-sport probe
        for slug in SPORTS_TO_PROBE:
            url = f"{SITE_URL}/{slug}"
            t0 = time.time()
            entry = {"slug": slug, "url": url}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(15_000)
                entry["ok"] = True
                entry["title"] = await page.title()
                entry["body_sample"] = await page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 600) : ''"
                )
            except Exception as e:
                entry["ok"] = False
                entry["error"] = str(e)[:300]
            entry["duration_s"] = round(time.time() - t0, 1)
            summary["sport_results"][slug] = entry

        await context.close()
        await browser.close()

    summary["host_request_counts"] = dict(sorted(host_counts.items(), key=lambda x: -x[1]))
    summary["finished_at"] = time.time()
    summary["duration_s"] = round(summary["finished_at"] - summary["started_at"], 1)

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    with open(OUT / "responses.jsonl", "w", encoding="utf-8") as f:
        for r in response_log:
            f.write(json.dumps(r, default=str) + "\n")
    with open(OUT / "ws_frames.jsonl", "w", encoding="utf-8") as f:
        for w in ws_log:
            f.write(json.dumps(w, default=str) + "\n")

    print(f"\n=== DISCOVERY DONE ({summary['duration_s']}s) ===")
    print(f"  responses captured: {len(response_log)}")
    print(f"  ws frames captured: {len(ws_log)}")
    print("  hosts hit:")
    for h, n in summary["host_request_counts"].items():
        if "sptpub.com" in h or "invisiblesport" in h:
            print(f"    {n:5d}  {h}")
    print("  sport probe results:")
    for slug, r in summary["sport_results"].items():
        print(f"    {'OK' if r['ok'] else 'ERR'}  {slug}  {r.get('title', '')}")
    print(f"\n  output dir: {OUT}")


if __name__ == "__main__":
    asyncio.run(run())
