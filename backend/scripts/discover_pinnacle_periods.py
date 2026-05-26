"""Discover Pinnacle's period codes per sport.

The current extractor (backend/src/providers/pinnacle.py:_parse_markets)
only handles three period buckets: 0 (full game / OT-included), 6 (ice
hockey regulation), and 1-5 (esports map markets). All other period
values are silently skipped — which is fine for the current 1x2 /
spread / total scope, but means we never extract first-half (NFL/NBA/
soccer 1H), F5 (MLB first 5 innings), or quarter / period markets.

This script hits Pinnacle's unauthenticated guest API and prints, per
sport, every distinct (period, market_type) pair we see across active
leagues. The output answers:

  "Which period codes does each sport actually ship, and what market
   types live at each one?"

Once we know the mapping, we can extend `_parse_markets` to emit the
appropriate scope tag (e.g. "f5" for baseball period 6, "1h" for
football period 1) and the scanner will pick those up automatically.

Usage (from repo root, with .venv activated):
    python backend/scripts/discover_pinnacle_periods.py

Optional: pass a comma-separated sport id list to override the default.

No SSH, no docker — runs on your machine. Hits the same public endpoint
the retriever uses.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

BASE = "https://guest.api.arcadia.pinnacle.com/0.1"

# Sport IDs Pinnacle uses internally. These are documented in
# backend/src/config/providers.yaml under each sport's pinnacle_sport_id.
DEFAULT_SPORTS: dict[str, int] = {
    "baseball_mlb": 3,
    "americanfootball_nfl": 15,
    "basketball_nba": 4,
    "icehockey_nhl": 19,
    "soccer_epl": 29,
}


def _get(url: str, timeout: int = 30) -> object:
    """Fetch JSON from Pinnacle's guest API. Honors PROXY_URL like the
    retriever does (Pinnacle blocks datacenter IPs for non-residential
    egress)."""
    req = urllib.request.Request(url, headers={"User-Agent": "arnold-discovery/1.0"})
    proxy = os.environ.get("PROXY_URL", "").strip()
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        if proxy
        else urllib.request.build_opener()
    )
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def discover_sport(sport_key: str, sport_id: int, max_leagues: int = 3, max_matchups: int = 5) -> dict:
    """Sample a few leagues for one sport and aggregate the period × type
    matrix. Capped low so the whole script runs in seconds."""
    summary: dict = {
        "sport": sport_key,
        "sport_id": sport_id,
        "period_type_counts": defaultdict(lambda: defaultdict(int)),
        "samples": [],
        "errors": [],
    }
    try:
        leagues = _get(f"{BASE}/sports/{sport_id}/leagues?all=false")
    except urllib.error.URLError as exc:
        summary["errors"].append(f"leagues fetch failed: {exc}")
        return summary

    if not isinstance(leagues, list):
        summary["errors"].append(f"unexpected leagues response: {type(leagues).__name__}")
        return summary

    active = [l for l in leagues if isinstance(l, dict) and l.get("matchupCount", 0) > 0]
    sampled = active[:max_leagues]
    if not sampled:
        summary["errors"].append("no active leagues with matchups")
        return summary

    for league in sampled:
        league_id = league.get("id")
        if not league_id:
            continue
        try:
            markets = _get(f"{BASE}/leagues/{league_id}/markets/straight")
        except urllib.error.URLError as exc:
            summary["errors"].append(f"league {league_id} markets failed: {exc}")
            continue
        if not isinstance(markets, list):
            continue

        # Tally (period, type) across this league's markets.
        local_periods = defaultdict(set)
        sampled_market: dict | None = None
        for m in markets[:200]:  # cap per league
            if not isinstance(m, dict):
                continue
            if m.get("status") != "open":
                continue
            period = m.get("period", 0)
            mtype = m.get("type") or "unknown"
            summary["period_type_counts"][period][mtype] += 1
            local_periods[period].add(mtype)
            # Grab one full sample market for each non-zero period for shape inspection.
            if period != 0 and sampled_market is None:
                sampled_market = {
                    "matchupId": m.get("matchupId"),
                    "period": period,
                    "type": mtype,
                    "isAlternate": m.get("isAlternate"),
                    "key": m.get("key"),
                    "price_count": len(m.get("prices", [])),
                    "first_price": (m.get("prices") or [{}])[0],
                }

        summary["samples"].append(
            {
                "league_id": league_id,
                "league_name": league.get("name"),
                "matchup_count": league.get("matchupCount"),
                "periods_seen": sorted(local_periods.keys()),
                "sampled_non_zero_market": sampled_market,
            }
        )

    return summary


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        # Allow `python ... baseball_mlb,americanfootball_nfl`
        keys = [k.strip() for k in argv[1].split(",") if k.strip()]
        sports = {k: DEFAULT_SPORTS[k] for k in keys if k in DEFAULT_SPORTS}
        missing = [k for k in keys if k not in DEFAULT_SPORTS]
        if missing:
            print(f"Unknown sports: {missing}. Known: {list(DEFAULT_SPORTS)}", file=sys.stderr)
            return 2
    else:
        sports = DEFAULT_SPORTS

    overall = []
    for sport_key, sport_id in sports.items():
        print(f"[discover] {sport_key} (id={sport_id})...", file=sys.stderr)
        result = discover_sport(sport_key, sport_id)
        # Convert defaultdicts to plain dicts for JSON serialisation.
        result["period_type_counts"] = {p: dict(types) for p, types in result["period_type_counts"].items()}
        overall.append(result)

    print(json.dumps(overall, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
