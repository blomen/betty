"""Profile all critical API endpoints — run with backend already started."""

import time
import urllib.request
import json

BASE = "http://localhost:8000"

ENDPOINTS = [
    ("candles (3d 1m)",      "/api/trading/market/candles?symbol=NQ&interval=1m&days=3"),
    ("session-levels",       "/api/trading/market/session-levels?symbol=NQ&days=5"),
    ("vp session",           "/api/trading/market/volume-profile?symbol=NQ&timeframe=session"),
    ("vp weekly",            "/api/trading/market/volume-profile?symbol=NQ&timeframe=weekly"),
    ("vp monthly",           "/api/trading/market/volume-profile?symbol=NQ&timeframe=monthly"),
    ("tpo live",             "/api/trading/market/tpo/live?symbol=NQ"),
    ("opportunities value",  "/api/opportunities?type=value&active_only=true&min_value=3"),
    ("specials",             "/api/specials"),
    ("bets pending",         "/api/bets?result=pending&limit=500"),
    ("bankroll info",        "/api/bankroll"),
    ("providers",            "/api/providers"),
]


def measure(name: str, url: str) -> dict:
    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(f"{BASE}{url}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            t1 = time.perf_counter()
            return {
                "name": name,
                "status": resp.status,
                "time_ms": round((t1 - t0) * 1000),
                "size_kb": round(len(body) / 1024, 1),
            }
    except Exception as e:
        return {"name": name, "error": str(e)[:80], "time_ms": -1, "size_kb": 0}


if __name__ == "__main__":
    print("\n=== COLD RUN (no cache) ===\n")
    print(f"{'Endpoint':<25} {'Time':>8} {'Size':>8} {'Status':>6}")
    print("-" * 55)
    cold = []
    for name, url in ENDPOINTS:
        r = measure(name, url)
        cold.append(r)
        if "error" in r:
            print(f"{r['name']:<25} {'ERROR':>8} {r['size_kb']:>7.1f}K  {r.get('error', '')}")
        else:
            print(f"{r['name']:<25} {r['time_ms']:>6}ms {r['size_kb']:>7.1f}K  {r['status']}")

    print("\n=== WARM RUN (cached) ===\n")
    print(f"{'Endpoint':<25} {'Time':>8} {'Size':>8} {'Status':>6}")
    print("-" * 55)
    warm = []
    for name, url in ENDPOINTS:
        r = measure(name, url)
        warm.append(r)
        if "error" in r:
            print(f"{r['name']:<25} {'ERROR':>8} {r['size_kb']:>7.1f}K  {r.get('error', '')}")
        else:
            print(f"{r['name']:<25} {r['time_ms']:>6}ms {r['size_kb']:>7.1f}K  {r['status']}")

    print("\n=== SPEEDUP ===\n")
    for c, w in zip(cold, warm):
        if c["time_ms"] > 0 and w["time_ms"] > 0:
            speedup = c["time_ms"] / max(w["time_ms"], 1)
            print(f"{c['name']:<25} {c['time_ms']:>6}ms → {w['time_ms']:>6}ms  ({speedup:.1f}x)")

    total_cold = sum(r["time_ms"] for r in cold if r["time_ms"] > 0)
    total_warm = sum(r["time_ms"] for r in warm if r["time_ms"] > 0)
    print(f"\n{'TOTAL':<25} {total_cold:>6}ms → {total_warm:>6}ms  ({total_cold/max(total_warm,1):.1f}x)")
    print(f"\nNote: These are sequential. Browser fires 3-5 in parallel.")
    print(f"      Real page load ≈ max(parallel group) + serialization overhead")
