import asyncio
import aiohttp
import json
import os

async def check_provider(session, domain):
    # Spectate usually uses spectate-web.<domain>/spectate
    # We can check the digest endpoint or just the root/health
    base = f"https://spectate-web.{domain}/spectate"
    url = f"{base}/eventsrequest/getEventsDigest/football"
    
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                print(f"[FOUND] {domain} matches Spectate pattern! ({resp.status})")
                return domain
            elif resp.status in [403, 404]:
                print(f"[POSSIBLE] {domain} returned {resp.status} for {url}")
                return domain
            else:
                print(f"[NO] {domain} returned {resp.status}")
    except Exception as e:
        print(f"[ERR] {domain}: {e}")
    return None

async def main():
    # Load providers
    with open('backend/src/config/providers.json', 'r') as f:
        providers = json.load(f)
        
    print(f"Scanning {len(providers)} providers for Spectate backend...")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for p in providers:
            domain = p # providers.json is a list of strings
            tasks.append(check_provider(session, domain))
            
        results = await asyncio.gather(*tasks)
        
    confirmed = [r for r in results if r]
    print(f"\nConfimed Spectate Providers: {confirmed}")

if __name__ == "__main__":
    asyncio.run(main())
